# -*- coding: utf-8 -*-
"""编排流水线 v2：智能模式（规划→路由→执行→验证→评审→自动修复→换将）+ 手动模式。

安全约束：稿件文件名在流水线内再次消毒（basename + 去分隔符），且每次
open 前都用 commonpath 校验路径必须落在任务工作目录内，防止目录穿越。

智能模式（mode=auto）：
  1. 规划器把目标拆成有序子任务（LLM 计划，失败退化为单步模板）；
  2. 路由器按能力基线 × 历史胜率选实现者/评审者（跨厂商评审约束）；
  3. 验证/评审不通过 → 自动把问题清单发回实现者修复（至多 router.MAX_REPAIR_ROUNDS 轮）；
  4. 仍不通过 → 自动换将重实现一次；
  5. 全程记录"为什么选它"与每轮修复结果。
手动模式（mode=manual）：用户显式指定实现者/评审组，行为同 v1。
"""
from __future__ import annotations

import os
import re
import threading
import time

from . import aiflavor, catalog, history, jobs, manager, modelhub, mocks, planner, registry, router, runner, skills, store, usage
from . import builtin_agent
from . import diagnostics
from . import paths as paths_mod
from . import session_log as session_log_mod
from . import step_runner as step_runner_mod
from .repeat_guard import guard as repeat_guard

DEFAULT_RUBRIC = ["情节", "人物", "文笔", "节奏", "吸引力"]


class Cancelled(Exception):
    pass


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _check_cancel(ev):
    if ev is not None and ev.is_set():
        raise Cancelled()


def _inside(dirpath, target):
    try:
        return os.path.commonpath(
            [os.path.abspath(dirpath), os.path.abspath(target)]) == os.path.abspath(dirpath)
    except ValueError:
        return False


def _task_images(task, workdir):
    """任务的图片附件绝对路径（仅 codex 原生 -i 用）。无附件/异常返回空列表。"""
    try:
        from . import attachments as att_mod
        return att_mod.image_paths(task, workdir)
    except Exception:
        return []


def _ms_name(raw):
    name = re.sub(r"[\\/\x00]+", "_", str(raw or "")).strip()
    name = re.sub(r"\.{2,}", "_", name).lstrip(".")
    return os.path.basename(name) or "manuscript.md"


def _ms_io(workdir, raw_name, mode):
    """打开稿件文件；open 紧邻边界校验，路径越界直接拒绝。"""
    name = _ms_name(raw_name)
    p = os.path.abspath(os.path.join(workdir, name))
    if not _inside(workdir, p):
        raise ValueError("稿件路径越界，已拒绝: %r" % raw_name)
    return open(p, mode, encoding="utf-8", errors="replace")


def _agents():
    return registry.effective_agents(catalog.load(), manager.detect_all())


# 本轮运行的智能体池：execute_run 入口快照，_run_step 死链补位时扫描。
# 直接调 _run_step 的场景（单测/内部工具）池为空 → 补位不触发，闸门语义不变。
_CURRENT_AGENTS: list = []


def _dead_binding_substitute(dead_id, resume=None):
    """死链补位：原定 CLI 配了链但解析为空（供应商停删/无密钥/**协议不匹配**——
    如 chat-only 供应商挂在只讲 responses 的 codex 链上）时，从同池找一个
    「配置过且链还活着」的真实 CLI 顶上——用户配的其它供应商继续干活，
    而不是整步判死。全池无活链才维持失败：死链闸门「绝不静默偷跑本机默认」
    的语义不变，补位用的仍是用户显式配好的链。

    resume 会话钉在原 CLI 上（会话跟人走），有 resume 时补位无意义，直接不找。
    返回 bind_agent 之后的替代者，或 None。"""
    if resume:
        return None
    from . import modelhub as _mh
    for a in _CURRENT_AGENTS or []:
        if a.get("id") == dead_id or a.get("mode") != "real":
            continue
        try:
            cand = _mh.bind_agent(a, "default")
        except Exception:
            continue
        if cand.get("binding_configured") and (cand.get("call_chain") or cand.get("env")):
            return cand
    return None


def _pick(agents, agent_id):
    for a in agents:
        if a["id"] == agent_id:
            return a
    return None


def _real(agents):
    return [a for a in agents if a.get("mode") == "real"]


def _pick_implementer(agents, wanted):
    real = _real(agents)
    if wanted:
        a = _pick(agents, wanted)
        if a:
            return a, ""
    if real:
        return real[0], ""
    return (agents[0], "") if agents else (None, "")


def _pick_critics_manual(agents, task):
    wanted = task.get("critics") or []
    if wanted:
        picked = [a for a in agents if a["id"] in wanted]
        if picked:
            return picked
    real = _real(agents)
    if real:
        return real
    return [a for a in agents if a.get("mode") == "mock"] or agents[:2]


def _valid_resume(task, agents):
    """校验任务上的 resume 声明；返回 {agent, session, project, note} 或 None。"""
    r = task.get("resume") or {}
    if not (r.get("agent") and r.get("session")):
        return None
    a = _pick(agents, r.get("agent"))
    if a is None or a.get("mode") != "real":
        return None
    return {"agent": a, "session": r["session"],
            "project": (r.get("project") or "").strip(),
            "note": "沿用已有会话 %s…（保留其上下文继续工作）" % str(r["session"])[:8]}


def _resume_workdir(resume_ctx, fallback):
    """续会话的工作目录：CLI 必须在会话所属项目目录下启动才能定位到会话
    （opencode/qwen 实测按 cwd 查找，否则报找不到或直接挂起）。
    目录已不存在时退回任务工作目录。"""
    if not resume_ctx:
        return fallback
    proj = resume_ctx.get("project") or ""
    if proj and os.path.isdir(proj):
        return proj
    return fallback


def _compaction_enabled():
    """Phase 2 灰度开关：环境变量 TUTTI_COMPACTION=1 启用上下文压缩（默认关）。"""
    return os.environ.get("TUTTI_COMPACTION") == "1"


_sessions_cache = {}
_sessions_lock = threading.Lock()


def _get_session(run_id):
    """每 run 一个 surface 会话日志（data/runs/<id>/session.jsonl）。"""
    with _sessions_lock:
        s = _sessions_cache.get(run_id)
        if s is None:
            sdir = paths_mod.RUNS_DIR / run_id
            sdir.mkdir(parents=True, exist_ok=True)
            s = session_log_mod.Session(run_id, store_path=str(sdir / "session.jsonl"))
            _sessions_cache[run_id] = s
        return s


def _make_llm_caller(agent, workdir):
    """压缩摘要用 LLM：直接复用当前 step 的 agent（同 CLI 同模型）。"""
    def caller(messages):
        prompt = "\n\n".join(m.get("content", "") for m in messages)
        res = runner.run_agent(agent, prompt, workdir=workdir, readonly=True,
                               timeout=300)
        return res.get("text") or ""
    return caller


def _resume_sid(agent, sid):
    """§07 T1.1：该 agent 是否可用会话 id 续会话；不可用返回 None（退回全新调用）。

    codex/claude/opencode/qwen 原生支持 resume；generic 需 catalog 配了
    resume_argv_template；mock/其余一律 None。避免 run_agent 对 generic 的
    「未配置会话恢复」硬失败把修订流程打断。
    """
    sid = (sid or "").strip()
    if not sid or agent.get("mode") == "mock":
        return None
    kind = agent.get("kind", "generic")
    if kind in ("codex", "claude", "opencode", "qwen"):
        return sid
    if kind == "generic" and agent.get("resume_argv_template"):
        return sid
    return None


def _is_review_role(role):
    """评审类步骤：用户指令在此类步骤注入时升级为「评分依据」，不再是普通纠偏。"""
    r = str(role or "")
    return "critique" in r or r == "review"


def _drain_directives(run_id, workdir, role=None, step_n=None):
    """取出运行中积压的用户指令（store.drain_messages），拼成注入块 + 收集图片附件。

    无头 CLI 没有交互 stdin，插不进正在跑的进程——指令在下一个步骤开始前
    生效（轮间干预），所以 drain 放在 _run_step 的真实调用分支。
    role/step_n 仅作送达回执（consumed_by）；评审类步骤额外追加「评分依据」
    框架文案，把用户意见变成评审判定的正式输入（插话进评审门）。
    返回 (注入文本块 或 "", 图片绝对路径列表)；消费即标记，不会重复注入。
    """
    try:
        msgs = store.drain_messages(run_id, consumed_by={"step": step_n, "role": role})
    except Exception:
        return "", []
    if not msgs:
        return "", []
    lines = ["## 用户实时指令（运行中追加，针对当前进展的纠偏，优先级高于原始要求）"]
    if _is_review_role(role):
        lines.append("本步为评审步骤：请把上述用户意见作为评分依据之一，"
                     "在相应维度的分数与 issues 中明确体现（引用用户原话）。")
    imgs = []
    for m in msgs:
        stamp = m.get("created_at") or ""
        sender = m.get("sender") or "用户"
        text = (m.get("text") or "").strip()
        lines.append("- [%s %s] %s" % (stamp, sender, text) if text
                     else "- [%s %s]（附件指令，见下方文件）" % (stamp, sender))
        for rel in (m.get("attachments") or []):
            rel = str(rel)
            low = rel.lower()
            if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                ap = os.path.join(workdir or "", rel) if workdir else rel
                if workdir and os.path.isfile(ap):
                    imgs.append(ap)
                    lines.append("  · 图片附件：%s（请查看图片内容）" % rel)
                else:
                    lines.append("  · 图片附件：%s" % rel)
            else:
                lines.append("  · 文件附件：%s（位于工作目录，可直接读取）" % rel)
    return "\n".join(lines), imgs


def _steered_task(run_id, task):
    """规划/大纲步骤的输入任务副本：未消费的用户指令合入 context（peek 不消费）。

    编排者决策（code plan / 连载大纲）不走 _run_step，消息只在 context 里可见；
    peek 语义保证后续真实步骤仍会 drain 注入——规划者和执行者都看到，双保险。
    """
    try:
        msgs = store.peek_messages(run_id)
    except Exception:
        return task
    if not msgs:
        return task
    lines = ["## 用户实时指令（运行中追加，规划时必须纳入考量）"]
    for m in msgs:
        text = (m.get("text") or "").strip()
        if text:
            lines.append("- [%s %s] %s" % (m.get("created_at") or "",
                                           m.get("sender") or "用户", text))
        for rel in (m.get("attachments") or []):
            lines.append("  · 附件：%s（位于工作目录，可直接读取）" % rel)
    t2 = dict(task)
    t2["context"] = ((task.get("context") or "") + "\n\n" + "\n".join(lines)).strip()
    return t2


def _wait_gate(run_id, ev):
    """暂停闸门：run.paused 标志位挂在下一个步骤开始前，放行或取消才继续。

    轮询 1s（本地内存读，开销可忽略）；取消事件优先——用户点「取消运行」
    不必先解除暂停。终止态（服务重启恢复/外部取消）同样放行，防卡死。"""
    while True:
        run = store.get_run(run_id) or {}
        if not run.get("paused") or run.get("status") not in ("queued", "running"):
            return
        if ev is not None and ev.is_set():
            return
        time.sleep(1.0)


def _binding_dead_msg(agent):
    """死链失败文案：委托 modelhub 单一真源（告警 sync 共用同一文案）。"""
    try:
        from . import modelhub
        return modelhub.binding_dead_msg(agent.get("id") or "")
    except Exception:
        return ("绑定链全部失效，本步判失败、不回落 CLI 本机默认——"
                "请在「CLI 绑定」页为该 CLI 绑定已启用的供应商")


def _run_step(run_id, role, agent, prompt, workdir, readonly, ev, timeout=runner.DEFAULT_TIMEOUT, note="", resume=None, images=None, require_tools=False):
    """执行一个智能体步骤并记录。返回 runner 统一结果。"""
    _wait_gate(run_id, ev)
    # 绑定解析为空分两种（2026-09-18 起 区分对待）：
    # · 从没配过链（binding_configured=False）：回落 CLI 本机默认照跑——
    #   用户根本没在 CodeBee 里配供应商，谈不到「烧自己配的配额」；判失败
    #   反而把用本地登录的普通用户全挡在门外（0.1.6 真实装机误伤案例）。
    # · 配过链但全死（binding_configured=True）：先看同池有没有「配置过且链还
    #   活着」的 CLI 可补位（评审补位的全步骤版——用户的其它供应商继续干活，
    #   典型场景：chat-only 供应商挂在只讲 responses 的 codex 链上必然解析为
    #   空，2026-09-18 重写任务 3/3 续跑全灭案）；全池无活链才判失败不静默
    #   降级——宁可失败不偷跑本机默认。只记在步骤备注/错误里，不产生健康胶囊。
    dead_binding = (agent.get("mode") == "real"
                    and agent.get("binding_configured")
                    and not (agent.get("call_chain") or agent.get("env")))
    if dead_binding:
        sub = _dead_binding_substitute(agent.get("id"), resume=resume)
        if sub is not None:
            note = ((note + "；") if note else "") + (
                "⚠ 原定 %s 绑定链全部失效，已补位 %s"
                % (agent.get("label") or agent["id"],
                   sub.get("label") or sub["id"]))
            agent = sub
            dead_binding = False
    dead_msg = _binding_dead_msg(agent) if dead_binding else ""
    if dead_binding:
        note = ((note + "；") if note else "") + "⚠ " + dead_msg
    step, log_abs = store.add_step(run_id, role, agent["id"],
                                   agent.get("label", agent["id"]), note=note)
    start = time.time()
    if dead_binding:
        from .error_codes import ErrorCode
        res = {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
               "tokens": 0, "usage": None, "error": dead_msg,
               "error_code": ErrorCode.ENV_BLOCK,
               "raw": {"exit_code": None}, "kind": agent.get("kind", "generic"),
               "model": agent.get("model")}
        _finish_step_result(run_id, step, res, role, agent, start)
        return res
    if agent.get("mode") == "mock":
        time.sleep(0.3)
        res = {"ok": True, "text": "[mock] %s" % prompt[:80], "json": None,
               "cost_usd": 0.0, "tokens": 0, "error": "", "raw": {"exit_code": 0}}
        if log_abs:
            try:
                log_abs.write_text("[mock 智能体] 跳过真实调用\n", encoding="utf-8")
            except Exception:
                pass
    else:
        # 5C：重复调用守门——指纹取原始 prompt（提醒注入 spawn 副本，不污染计数链）
        guard = repeat_guard.check(run_id, role, prompt)
        if guard["should_stop"]:
            from .error_codes import ErrorCode
            res = {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                   "tokens": 0, "usage": None, "error": guard["reminder"],
                   "error_code": ErrorCode.ENV_BLOCK,
                   "raw": {"exit_code": None}, "kind": agent.get("kind", "generic"),
                   "model": agent.get("model")}
            _finish_step_result(run_id, step, res, role, agent, start)
            return res
        # 运行中指挥：drain 用户追加的指令/附件，注入本步（守门拦截时不 drain，
        # 消息留给下一个真实步骤，不空耗）；role/step_n 作送达回执
        directive_block, directive_imgs = _drain_directives(run_id, workdir,
                                                            role=role, step_n=step["n"])
        if directive_imgs:
            images = list(images or []) + directive_imgs
        if directive_block:
            prompt = directive_block + "\n\n---\n\n" + prompt
        effective_prompt = (guard["reminder"] + "\n\n---\n\n" + prompt) if guard["reminder"] else prompt
        res = _spawn_step(session_run_id=run_id, role=role, agent=agent,
                          prompt=effective_prompt, workdir=workdir, readonly=readonly,
                          ev=ev, timeout=timeout, resume=resume, step=step,
                          log_abs=log_abs, images=images, require_tools=require_tools)
    # 先收尾再查取消：取消时进程已被 run_process 杀停，若先抛 Cancelled，
    # 步骤记录会永远停在「运行中」变僵尸（与 _run_verify 的顺序对齐）
    _finish_step_result(run_id, step, res, role, agent, start)
    _check_cancel(ev)
    return res


def _run_builtin_step(run_id, role, bi, prompt, workdir, ev, note="", images=None,
                      followups=False):
    """内置智能体步骤：直连模型 API + 工具循环（builtin_agent），不经 CLI 进程。

    与 _run_step 对齐的三件事：暂停/取消闸门、运行中指令 drain 注入、重复调用
    守门；结果同样经 _finish_step_result 落步骤（output=干净回答）并入用量台账。
    日志只有「迭代/工具」摘要行——对话视图吃 output，日志抽屉看工具轨迹。
    followups=True 时从回答末尾解析「建议追问」块（直连对话专用协议）：
    剥离出结构化列表落步骤记录，正文保持干净。"""
    _wait_gate(run_id, ev)
    step, log_abs = store.add_step(run_id, role, "builtin", "CodeBee", note=note)
    start = time.time()
    agent_pseudo = {"id": "builtin", "label": "CodeBee", "kind": "builtin", "mode": "real",
                    "provider": {"id": bi.get("provider_id") or "",
                                 "name": bi.get("provider_name") or ""}}
    guard = repeat_guard.check(run_id, role, prompt)
    if guard["should_stop"]:
        from .error_codes import ErrorCode
        res = {"ok": False, "text": "", "usage": None, "cost_usd": 0.0, "tokens": 0,
               "error": guard["reminder"], "error_code": ErrorCode.ENV_BLOCK,
               "raw": {"exit_code": None}, "model": bi.get("model")}
        _finish_step_result(run_id, step, res, role, agent_pseudo, start)
        return res
    # 运行中指挥：drain 用户追加的指令/附件，注入本轮（与 _run_step 同语义）
    directive_block, directive_imgs = _drain_directives(run_id, workdir, role=role, step_n=step["n"])
    if directive_imgs:
        images = list(images or []) + directive_imgs
    if directive_block:
        prompt = directive_block + "\n\n---\n\n" + prompt
    if guard["reminder"]:
        prompt = guard["reminder"] + "\n\n---\n\n" + prompt
    lines = ["CodeBee（%s · %s）" % (bi.get("provider_name"), bi.get("model"))]

    def _log(line):
        lines.append(str(line))

    res = builtin_agent.run(bi, prompt, workdir, cancel_event=ev, log=_log, images=images)
    if followups and res.get("ok"):
        clean, fups = _parse_followups(res.get("text") or "")
        if fups:
            res["text"] = clean
            res["followups"] = fups
    if log_abs:
        try:
            log_abs.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass
    usage = res.get("usage") or {}
    res.setdefault("raw", {})
    res["raw"]["exit_code"] = 0 if res.get("ok") else 1
    res["raw"]["duration"] = time.time() - start
    res["tokens"] = int(usage.get("total") or 0)
    res.setdefault("cost_usd", 0.0)
    # 先收尾再查取消：与 _run_step 同序，防步骤记录停在「运行中」变僵尸
    _finish_step_result(run_id, step, res, role, agent_pseudo, start)
    _check_cancel(ev)
    return res


def _budget_max_tokens():
    """§07 T2.1：单次 run 的 token 预算上限；0/未配置 = 不限。

    环境变量 TUTTI_BUDGET_MAX_TOKENS 优先（运维场景：不动配置文件直接钳住失控 run）。
    """
    try:
        env_val = os.environ.get("TUTTI_BUDGET_MAX_TOKENS")
        if env_val:
            return max(0, int(env_val))
    except Exception:
        pass
    try:
        from .settings_schema import get as ss_get, register_default_namespaces
        register_default_namespaces()
        return int(ss_get("budget", "max_tokens_per_run") or 0)
    except Exception:
        return 0


def _spawn_step(session_run_id, role, agent, prompt, workdir, readonly, ev,
                timeout, resume, step, log_abs, images=None, require_tools=False):
    """真实 CLI 调用：压缩灰度路径或原路径。"""
    # T2.1 预算闸：已用 token 达到单次 run 上限 → 阻断后续真实调用（ENV_BLOCK）。
    # 只拦「下一步」，允许越过线的当前步完成；auto 续跑可在用户调高预算后接手。
    cap = _budget_max_tokens()
    if cap > 0:
        try:
            from .token_meter import token_meter
            used = token_meter.used(session_run_id)
        except Exception:
            used = 0
        if used >= cap:
            from .error_codes import ErrorCode
            log_path_warn = "已超出单次运行 token 预算：%d/%d（可在设置 budget.max_tokens_per_run 调整），停止后续步骤" % (used, cap)
            return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                    "tokens": 0, "usage": None, "error": log_path_warn,
                    "error_code": ErrorCode.ENV_BLOCK, "sid": "",
                    "raw": {"exit_code": None}, "kind": agent.get("kind", "generic"),
                    "model": agent.get("model")}
    if _compaction_enabled() and not resume:
        # Phase 2（1D）：撑爆 → 压缩 → 守门重试；同时把 usage 累进 token_meter（1C）
        session = _get_session(session_run_id)
        llm_caller = _make_llm_caller(agent, workdir)
        call_kwargs = dict(workdir=workdir, readonly=readonly,
                           timeout=timeout, cancel_event=ev, log_path=str(log_abs),
                           images=images, require_tools=require_tools)

        def _call(p, **kw):
            # 模型可见即已记录（§1A 不变量）：入参/出参先落 session 日志
            session.append("user_message", {"content": p, "role": role},
                           turn_id=str(step["n"]))
            r = runner.run_agent(agent, p, **{**call_kwargs, **kw})
            session.append("assistant_message",
                           {"content": (r.get("text") or r.get("error") or ""),
                            "ok": r.get("ok"), "model": r.get("model")},
                           turn_id=str(step["n"]))
            try:
                from .token_meter import token_meter
                token_meter.accumulate(session_run_id, r.get("usage"),
                                       model=r.get("model") or "")
            except Exception:
                pass
            return r

        res, _retried = step_runner_mod.execute_step(
            session, _call, prompt, model=agent.get("model") or "",
            llm_caller=llm_caller)
    else:
        res = runner.run_agent(agent, prompt, workdir=workdir, readonly=readonly,
                               timeout=timeout, cancel_event=ev, log_path=str(log_abs),
                               resume=resume, images=images, require_tools=require_tools)
    return res


def _finish_step_result(run_id, step, res, role, agent, start):
    """step 收尾：记录 + 用量 + 运行时断言（5F）。"""
    # 被取消杀停的步骤如实记「已取消」；超时被杀记「超时」——都只是步骤
    # 显示层的细分，run 级仍是 failed，善后路径（重试/自动续跑）不变
    raw = res.get("raw") or {}
    if raw.get("cancelled"):
        status = "cancelled"
    elif raw.get("timed_out"):
        status = "timeout"
    else:
        status = "done" if res["ok"] else "failed"
    store.finish_step(run_id, step["n"],
                      status,
                      summary=((res.get("text") or res.get("error") or "")[:600]),
                      exit_code=res.get("raw", {}).get("exit_code"),
                      cost_usd=res.get("cost_usd", 0.0),
                      tokens=res.get("tokens", 0),
                      duration_s=time.time() - start,
                      model=res.get("model"),
                      # 智能体的最终回答（runner 已从 JSONL 事件流里抽出 agent_message）。
                      # 对话视图直读这个；日志文件是全量事件流，塞进气泡就成了「看日志」。
                      output=(res.get("text") or ""),
                      followups=res.get("followups"))
    # 错误台账：失败/超时各记一条结构化记录（遥测与诊断包的数据源）。
    # 用户主动取消不入账——那不是产品问题；detail 只存脱敏后的失败摘录。
    if status in ("failed", "timeout"):
        try:
            from . import errorlog
            prov = agent.get("provider") or {}
            errorlog.record(
                category="step", reason=str(res.get("error_code") or "UNKNOWN"),
                detail=(res.get("error") or ""),
                provider=(prov.get("name") if isinstance(prov, dict) else "") or "",
                model=res.get("model") or "", tool=agent.get("kind", ""),
                role=role, run_id=run_id, task_id=(store.get_run(run_id) or {}).get("task_id") or "",
                step=step["n"], exit_code=res.get("raw", {}).get("exit_code"))
        except Exception:
            pass
    if agent.get("mode") != "mock":
        _record_usage(run_id, role, agent, res, source="pipeline", step=step["n"])
    # 5F：step 级运行时断言（只告警不阻断）
    try:
        diagnostics.invariants.run_for("step", {
            "run_id": run_id, "role": role, "ok": res.get("ok"),
            "error_code": res.get("error_code") or "",
            "text_len": len(res.get("text") or ""),
        })
    except Exception:
        pass


def _record_usage(run_id, role, agent, res, source="pipeline", step=0):
    """把一次真实智能体调用记入用量台账；取不到的上下文留空，绝不抛错。

    step 必须与 store.add_step 的步骤号一致：启动时的历史回填按
    (run_id, role, step) 去重，缺了会把同一步骤重复入账。
    """
    try:
        run = store.get_run(run_id) or {}
        task = store.get_task(run.get("task_id")) if run.get("task_id") else None
        usage.record(
            source=source, run_id=run_id, step=step,
            task_id=run.get("task_id") or "",
            task_type=(task or {}).get("type", ""),
            role=role, agent=agent.get("id", ""),
            agent_label=agent.get("label", ""),
            tool=agent.get("kind", ""),
            model=res.get("model") or "",
            ok=bool(res.get("ok")),
            duration_s=float(res.get("raw", {}).get("duration") or 0.0),
            cost_usd=float(res.get("cost_usd") or 0.0),
            usage=res.get("usage"))
        # 告警模块：CLI 调用成功/失败上报（provider 名与 usage 台账一致）
        from . import health
        prov = agent.get("provider") or {}
        prov_name = (prov.get("name") if isinstance(prov, dict) else "") or ""
        if prov_name:
            if res.get("ok"):
                health.report_success(prov_name)
            else:
                health.report_failure(prov_name, res.get("error") or "",
                                      model=res.get("model") or "",
                                      provider_id=prov.get("id") or "")
    except Exception:
        pass


# ---------------------------------------------------------------- 提示词

CODE_IMPL_PROMPT = """你是一名高级工程师，请在当前工作目录中直接完成以下任务（直接修改/创建文件）。创建/写入的文件一律以 UTF-8 编码保存（PowerShell 显式加 -Encoding UTF8，禁止依赖系统默认编码）。

## 总体目标
__GOAL__

## 本步指令
__SUBTASK__

## 背景与上下文
__CONTEXT__

## 要求
- 修改要最小化、聚焦任务目标；不要无关重构。
- 完成后只用 3-5 句话总结你改了什么、为什么。
__VERIFY_HINT__"""

CODE_FIX_PROMPT = """你是一名高级工程师。你之前的实现没有通过验收，请在当前工作目录中直接修复（直接修改/创建文件）。创建/写入的文件一律以 UTF-8 编码保存（PowerShell 显式加 -Encoding UTF8，禁止依赖系统默认编码）。

## 总体目标
__GOAL__

## 未通过的原因
__ISSUES__

## 要求
- 只针对上述问题修复；不要无关重构。
- 完成后用 2-3 句话说明改了什么。
__VERIFY_HINT__"""

CODE_REVIEW_PROMPT = """你是代码评审员（不要修改任何文件）。请只基于下方提供的任务与变更内容进行评审，
输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{
  "pass": true/false,
  "scores": {"正确性": 1-10, "可维护性": 1-10, "安全": 1-10},
  "issues": [{"severity": "blocker|major|minor", "title": "...", "detail": "..."}],
  "summary": "一句话结论"
}

## 任务目标
__GOAL__

## 验收命令
__VERIFY__

## 变更内容（git diff，若为空表示无法获取）
__DIFF__
"""


def _git_diff(workdir):
    """评审用的变更集：git diff HEAD 之外还拼上未跟踪新文件——
    新文件不进 git diff，但恰是智能体产物的大头（新章节/新模块），
    缺了评审官等于半盲评。只读，不动 index。"""
    from . import gitmod
    return gitmod.collect_changes(workdir)["diff"]


def _verify_hint(task):
    if task.get("verify_command"):
        return "- 完成后请自查：`%s` 应当通过。" % task["verify_command"]
    return ""


def _run_verify(run_id, task, workdir, ev):
    """确定性验证。返回 (verify_pass, ran)。"""
    if not task.get("verify_command"):
        return True, False
    step, log_abs = store.add_step(run_id, "verify", "builtin", "内置验证器")
    start = time.time()
    r = runner.run_process(shell_cmd=task["verify_command"], cwd=workdir,
                           timeout=600, cancel_event=ev, log_path=str(log_abs))
    ok = r["ok"]
    # 与 _finish_step_result 同一套显示层细分：超时/取消杀停不再冒充「失败」
    if r.get("cancelled"):
        v_status, v_sum = "cancelled", "验证被取消终止"
    elif r.get("timed_out"):
        v_status, v_sum = "timeout", "验证超时被终止"
    else:
        v_status = "done" if ok else "failed"
        v_sum = "验证通过" if ok else "验证失败（exit %s）" % r["exit_code"]
    store.finish_step(run_id, step["n"], v_status,
                      summary=v_sum,
                      exit_code=r["exit_code"], duration_s=time.time() - start)
    _check_cancel(ev)
    return ok, True


def _run_review(run_id, task, workdir, reviewer, ev):
    diff = _git_diff(workdir)
    prompt = (CODE_REVIEW_PROMPT
              .replace("__GOAL__", task["goal"])
              .replace("__VERIFY__", task.get("verify_command") or "（未配置）")
              .replace("__DIFF__", diff or "（无法获取 git diff，请综合任务目标谨慎评审）"))
    res = _run_step(run_id, "review", reviewer, prompt, workdir, readonly=True, ev=ev,
                    images=_task_images(task, workdir))
    if reviewer.get("mode") == "mock":
        return mocks.review(task, True)
    parsed = runner.extract_json(res.get("text") or "")
    if not isinstance(parsed, dict):
        return {"pass": False, "scores": {}, "issues": [],
                "summary": "评审输出无法解析为 JSON：%s" % (res.get("text") or "")[:200]}
    return parsed


def _format_issues(review_json, verify_pass, verify_failed_note):
    lines = []
    if not verify_pass and verify_failed_note:
        lines.append("- 验证命令未通过（%s）" % verify_failed_note)
    for it in (review_json.get("issues") or [])[:10]:
        lines.append("- [%s] %s：%s" % (it.get("severity", "?"),
                                        it.get("title", ""), str(it.get("detail", ""))[:300]))
    return "\n".join(lines) or "（评审未给出具体问题，请自查实现质量与验收命令）"


# ---------------------------------------------------------------- 代码流水线

def _run_code(run, task, agents, ev, stats, mode):
    run_id = run["id"]
    workdir = task["workdir"]
    repairs = []       # 每轮 {round, kind, verify_pass, review_pass, issues}
    impl_sid = [""]    # §07 T1.1：最后一次实现的 CLI 会话 id（fix 轮复用）
    route = {}
    switched = False
    resume_ctx = _valid_resume(task, agents)

    # ---- 难度（影响模型选择）：用户指定 > 启发式 > 规划器判定
    difficulty = task.get("difficulty") or "auto"
    explicit = difficulty in ("easy", "hard")
    if not explicit:
        difficulty = modelhub.classify_difficulty(task["goal"], task.get("verify_command"))

    # ---- 路由
    if resume_ctx is not None:
        impl = resume_ctx["agent"]
        route["implementer"] = resume_ctx["note"]
    elif mode == "manual":
        impl, _ = _pick_implementer(agents, task.get("implementer"))
    else:
        impl, route["implementer"] = router.pick(agents, "implement", "code", stats)
    if impl is None:
        store.update_run(run_id, status="failed", error="没有可用智能体", ended_at=_now())
        return

    # ---- 规划
    if mode == "auto":
        _wait_gate(run_id, ev)
        plan_step, plan_log = store.add_step(run_id, "plan", impl["id"], impl.get("label"),
                                             note=route.get("implementer", ""))
        plan = planner.make_code_plan(_steered_task(run_id, task),
                                      modelhub.bind_agent(impl, difficulty),
                                      _resume_workdir(resume_ctx, workdir), ev,
                                      resume=resume_ctx["session"] if resume_ctx else None,
                                      log_path=str(plan_log) if plan_log else None)
        # 规划器判定优先于启发式（仅当用户未显式指定难度）
        if not explicit and plan.get("difficulty") in ("easy", "hard"):
            difficulty = plan["difficulty"]
        store.finish_step(run_id, plan_step["n"], "done",
                          summary="计划来源 %s（难度 %s）：%s" % (
                              plan["source"], difficulty,
                              "；".join(s["title"] for s in plan["steps"])[:160]),
                          duration_s=0.1 if impl.get("mode") == "mock" else None)
    else:
        plan = {"source": "manual", "steps": [{"title": "实现任务", "detail": task["goal"]}]}
    store.update_run(run_id, plan=plan, difficulty=difficulty)
    subtasks = plan["steps"]

    # ---- 评审者（有会话延续时评审者仍用新鲜上下文，避免偏见）
    if mode == "auto":
        reviewer, route["reviewer"] = router.pick_reviewer(agents, impl, "code", stats)
    else:
        reviewer, route["reviewer"] = _pick_reviewer_legacy(agents, impl)
    store.update_run(run_id, route=route)

    def implement_all(impl_agent, prefix_note):
        # 会话延续只对原实现者有效；换将后新智能体没有该会话，必须丢弃
        use_resume = (resume_ctx["session"]
                      if (resume_ctx and impl_agent["id"] == resume_ctx["agent"]["id"]) else None)
        # 续会话时 CLI 要在会话所属项目目录下启动，否则定位不到会话
        step_wd = _resume_workdir(resume_ctx, workdir) if use_resume else workdir

        def _run_one(agt):
            """用指定智能体跑全部子任务；返回 (ok, 最后一次 res)。"""
            agt_b = modelhub.bind_agent(agt, difficulty)
            att_imgs = _task_images(task, workdir)  # 图片附件供 codex 原生 -i 直读
            # §07 T3.1 FrugalGPT 级联（默认关）：easy 任务把链按 tier 升序重排，
            # 便宜模型先跑；质量闸门不过走既有 repair/换将轮，等效"贵模型兜底"。
            if difficulty == "easy":
                try:
                    from .settings_schema import get as ss_get, register_default_namespaces
                    register_default_namespaces()
                    if ss_get("cascade", "enabled"):
                        from . import capability
                        agt_b = capability.cascade_reorder(
                            agt_b, capability.make_tier_lookup(modelhub.providers()))
                except Exception:
                    pass
            for i, sub in enumerate(subtasks):
                prompt = (CODE_IMPL_PROMPT
                          .replace("__GOAL__", task["goal"])
                          .replace("__SUBTASK__",
                                   sub["detail"] if sub["detail"] else sub["title"])
                          .replace("__CONTEXT__", task.get("context") or "（无）")
                          .replace("__VERIFY_HINT__", _verify_hint(task)))
                role = "implement" if len(subtasks) == 1 else "implement-%d/%d" % (i + 1, len(subtasks))
                res = _run_step(run_id, role, agt_b, prompt, step_wd,
                                readonly=False, ev=ev,
                                note=prefix_note if i == 0 else "",
                                resume=use_resume, images=att_imgs,
                                require_tools=True)
                # §07 T1.1：记录最后一次实现的会话 id，fix 轮复用（会话内前缀走缓存读计价）
                new_sid = _resume_sid(agt_b, res.get("sid"))
                if new_sid:
                    impl_sid[0] = new_sid
                if agt.get("mode") == "mock" and res["ok"]:
                    try:
                        mock_path = os.path.abspath(os.path.join(workdir, "mock-impl.txt"))
                        if _inside(workdir, mock_path):
                            with open(mock_path, "a", encoding="utf-8") as f:
                                f.write("%s mock 实现：%s / %s\n" % (_now(), task["title"], sub["title"]))
                    except Exception:
                        pass
                if not res["ok"]:
                    return False, res
            return True, res

        ok, res = _run_one(impl_agent)
        if ok:
            return True
        # 实现步失败不立刻判死：2026-09-16 实测配额烧干时 5 连跑全在同一条 CLI 上
        # 失败收场，而健康的 opencode 一直在旁观望——跨 CLI 换将重试一次
        # （mode=manual 尊重用户指定，不换）。
        if mode == "auto" and impl_agent.get("mode") == "real":
            ex = {impl_agent["id"], "mock-a", "mock-b"}
            other, other_reason = router.pick(agents, "implement", "code", stats,
                                              exclude=ex)
            if other is not None and other.get("mode") == "real":
                note = "实现步失败自动换将 %s → %s：%s。失败原因：%s" % (
                    impl_agent["id"], other["id"], other_reason,
                    (res.get("error") or "")[:200])
                ok2, res = _run_one(other)
                if ok2:
                    store.update_run(run_id, error="", route_note=note)
                    return True
                res_err = "%s；换将后仍失败：%s" % (note, (res.get("error") or "")[:200])
            else:
                res_err = "实现步骤失败（无其他真实 CLI 可换将）: %s" % res.get("error")
        else:
            res_err = "实现步骤失败: %s" % res.get("error")
        store.update_run(run_id, status="failed", error=res_err, ended_at=_now())
        return False

    def review_and_score():
        review_json = _run_review(run_id, task, workdir, modelhub.bind_agent(reviewer, difficulty), ev)
        verify_pass, verify_ran = _run_verify(run_id, task, workdir, ev)
        return review_json, verify_pass, verify_ran

    attempt_note = route.get("implementer", "") if mode == "auto" else ""
    round_no = 0
    review_json, verify_pass, verify_ran = None, True, False
    while True:
        if round_no == 0:
            if not implement_all(impl, attempt_note):
                return
        elif switched:
            if not implement_all(impl, "换将后全量重实现"):
                return
        else:
            issues_txt = _format_issues(review_json, verify_pass, task.get("verify_command"))
            prompt = (CODE_FIX_PROMPT
                      .replace("__GOAL__", task["goal"])
                      .replace("__ISSUES__", issues_txt)
                      .replace("__VERIFY_HINT__", _verify_hint(task)))
            res = _run_step(run_id, "fix-r%d" % round_no, modelhub.bind_agent(impl, difficulty),
                            prompt, workdir, readonly=False, ev=ev,
                            note="自动修复第 %d 轮" % round_no,
                            resume=resume_ctx["session"] if resume_ctx else impl_sid[0],
                            require_tools=True)
            if impl.get("mode") == "mock" and res["ok"]:
                pass  # mock 不产生真实变更
        review_json, verify_pass, verify_ran = review_and_score()
        passed = verify_pass and bool(review_json.get("pass"))
        repairs.append({"round": round_no, "kind": "switch" if switched else (
            "initial" if round_no == 0 else "repair"),
            "verify_pass": verify_pass, "review_pass": bool(review_json.get("pass")),
            "passed": passed})
        if passed:
            break
        if round_no < router.MAX_REPAIR_ROUNDS and not switched:
            round_no += 1
            continue
        if mode == "auto" and not switched:
            ex = (impl["id"],)
            if impl.get("mode") == "real":
                ex += ("mock-a", "mock-b")  # 真实实现者失败时不降级到 mock
            other, other_reason = router.pick(agents, "implement", "code", stats, exclude=ex)
            if other is not None:
                note = "换将 %s → %s：%d 轮实现/修复后仍未通过。%s" % (
                    impl["id"], other["id"], round_no + 1, other_reason)
                store.update_run(run_id, error="")
                impl = other
                switched = True
                round_no += 1
                continue
        break

    overall_pass = verify_pass and bool(review_json.get("pass"))
    scores = review_json.get("scores") or {}
    overall_score = round(sum(scores.values()) / len(scores), 1) if scores else None
    verdict = {
        "type": "code", "engine": "code", "pass": overall_pass, "mode": mode,
        "verify_ran": verify_ran, "verify_pass": verify_pass,
        "review_pass": bool(review_json.get("pass")),
        "scores": scores, "overall_score": overall_score,
        "issues": review_json.get("issues") or [],
        "reviewer": reviewer["id"],
        "route": route, "repairs": repairs, "switched": switched,
    }

    lines = [
        "# 代码任务报告：%s" % task["title"], "",
        "- 结论：**%s**" % ("✅ 通过" if overall_pass else "❌ 未通过"),
        "- 编排模式：%s　实现者：%s　评审者：%s" % (
            "智能" if mode == "auto" else "手动", impl.get("label"), reviewer.get("label")),
        "- 验证命令：%s → %s" % (task.get("verify_command") or "（未配置）",
                                 "通过" if verify_pass else "未通过"),
        "- 综合评分：%s　修复/换将：%s" % (
            overall_score if overall_score is not None else "-",
            ("%d 轮修复" % (len(repairs) - 1)) if len(repairs) > 1 else "无") +
            ("　（已换将重实现）" if switched else ""),
    ]
    if route:
        lines.append("")
        lines.append("## 路由依据")
        lines.extend("- %s：%s" % (k, v) for k, v in route.items() if v)
    lines += ["", "## 评审问题", ""]
    if review_json.get("issues"):
        for it in review_json["issues"]:
            lines.append("- **[%s]** %s：%s" % (it.get("severity", "?"),
                                                it.get("title", ""), it.get("detail", "")))
    else:
        lines.append("（无）")
    lines += ["", "## 评审总评", "", review_json.get("summary", ""), ""]
    store.write_report(run_id, "\n".join(lines))
    store.update_run(run_id, status="done", verdict=verdict,
                     summary="代码任务%s（验证%s / 评审%s%s）" % (
                         "通过" if overall_pass else "未通过",
                         "通过" if verify_pass else "未通过",
                         "通过" if review_json.get("pass") else "未通过",
                         "，%d 轮修复" % (len(repairs) - 1) if len(repairs) > 1 else ""),
                     ended_at=_now())


# ---------------------------------------------------------------- direct 引擎（直连单 CLI：无拆解/评审，对话式续轮）

DIRECT_PROMPT = """你是 CodeBee 的执行智能体，直接完成用户交代的任务。用户的目标、背景与工作目录内的附件就是全部输入：不拆解、不评审、不换人，直接动手。

## 任务
__GOAL__

## 背景与上下文
__CONTEXT__

## 要求
- 能改直接改、能写直接写（限本工作目录内），产出文件一律 UTF-8 编码（PowerShell 写文件显式 -Encoding UTF8）。
- 正文直接交代结果与答案：不要写「本轮做了什么」这类开场总结，也不要「回复：」这类引导词。
- 回复的最后一行单独输出一行交代结果：
DIRECT_DONE: <一句话结果摘要（含产出文件）>
这一行之后不要再输出任何内容。"""

DIRECT_FOLLOWUP_PROMPT = """你在与用户的持续对话中。用户针对已有成果发来了新消息（见下方「用户实时指令」注入块），请接着处理。

## 原始任务
__GOAL__

## 要求
- 优先回应用户新消息（继续做/改/答疑均可），仍限本工作目录内。
- 正文开门见山直接回答：不要写「本轮做了什么」这类开场总结，也不要「回复：」这类引导词。
- 回复的最后一行单独输出：
DIRECT_DONE: <一句话结果摘要>
这一行之后不要再输出任何内容。"""

DIRECT_MAX_TURNS = 200   # 对话续轮上限（每轮都要用户主动发消息才触发，防意外打满）

# 内置智能体版：人格与工具说明在 builtin_agent._SYSTEM_PROMPT，这里只给任务输入；
# 不要求 DIRECT_DONE 协议尾行——builtin 的最终回答本身就是干净文本
BUILTIN_DIRECT_PROMPT = """## 任务
__GOAL__

## 背景与上下文
__CONTEXT__

## 要求
- 能改直接改、能写直接写（用工具，限本工作目录内），产出文件一律 UTF-8 编码。
- 完成后直接给用户结论与答案，需要时顺带交代产出/修改了哪些文件；不要写「本轮做了什么」这类开场白。
__FOLLOWUPS__"""

BUILTIN_FOLLOWUP_PROMPT = """## 原始任务
__GOAL__

## 上一轮输出（结尾）
__PREV__

## 要求
- 优先回应用户的新消息（继续做/改/答疑均可），仍限本工作目录内，工具可用。
- 直接给答案/结果，需要时再带一句改动说明；不要写「本轮做了什么」这类开场白，也不要「回复：」这类引导词。
__FOLLOWUPS__"""

# 追问建议协议（借鉴 freebuff 的 suggest_followups）：模型在正文后自带最多 3 条
# 建议追问，后端解析成结构化字段、从正文剥离，前端渲染成可点芯片。省一次额外
# 请求；模型不配合时自然没有芯片，无需兜底。
FOLLOWUPS_PROTOCOL = """
## 回复末尾协议
正文全部写完之后，另起一段输出最多 3 条「建议追问」（用户最可能接着问的方向），格式严格如下；没有合适的方向就整个省略，绝不要输出空的或凑数的：
<followups>
10 字内的短标签 | 用户点击后会原样发送的完整消息（第一人称，一句话）
</followups>"""


def _parse_followups(text):
    """从回答正文里解析并剥离 <followups> 块。返回 (干净正文, [建议列表])。

    每行「标签 | 消息」（兼容全角｜），最多取 3 条，两端空白与空行忽略；
    没有块或解析不出任何合法行时原样返回。"""
    import re as _re
    if not text or "<followups>" not in text:
        return text, []
    m = _re.search(r"<followups>\s*([\s\S]*?)</followups>", text)
    if not m:
        return text, []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        parts = _re.split(r"[｜|]", line, 1)
        if len(parts) != 2:
            continue
        label = parts[0].strip()
        msg = parts[1].strip()
        if not label or not msg:
            continue
        out.append({"label": label[:24], "prompt": msg[:200]})
        if len(out) >= 3:
            break
    clean = (text[:m.start()] + text[m.end():]).strip()
    return clean, out


def _pending_messages(run_id):
    """该 run 信箱里未消费消息列表（读不到时当空，绝不因信箱异常打断执行）。"""
    try:
        return store.peek_messages(run_id)
    except Exception:
        return []


def _direct_prev_run(task_id, exclude_run_id):
    """同一任务下最近一次已结束的 direct run（追话时继承会话 id 与工作目录）。"""
    try:
        runs = store.list_runs(limit=200)
    except Exception:
        return None
    cands = [r for r in runs
             if r.get("task_id") == task_id and r.get("id") != exclude_run_id
             and r.get("status") in ("done", "failed", "cancelled")]
    if not cands:
        return None
    return max(cands, key=lambda r: r.get("id") or "")


def _direct_last_text(run):
    """上一轮 direct run 的最后一条步骤输出（取步骤记录里的 summary）。"""
    steps = run.get("steps") or []
    for s in reversed(steps):
        txt = (s.get("summary") or "").strip()
        if txt:
            return txt
    return ""


def _run_direct(run, task, agents, ev, stats, mode):
    """直连引擎：目标+附件直接交给一个执行者，跑完即止。

    执行者优先级：内置智能体（直连模型 API + 工具循环，无 CLI 进程）→ CLI 智能体。
    任务显式声明 CLI 会话续接（resume）或手动指定了执行者时尊重选择走 CLI；
    无可用供应商时回退 CLI。无规划/评审/验证/换将——快档位。对话式续轮：
    运行中信箱来消息 → drain 注入下一步；步骤结束后信箱还有未消费消息就
    再续一轮；信箱空了收工为 done。运行结束后再来消息走 retry_task
    （消息自动继承到新 run）。
    """
    run_id = run["id"]
    workdir = task["workdir"]
    route = {}
    resume_ctx = _valid_resume(task, agents)
    bi = None
    if resume_ctx is None and not (mode == "manual" and task.get("implementer")):
        try:
            bi = builtin_agent.resolve()
        except Exception:
            bi = None
    if bi is not None:
        impl = None
        route["implementer"] = "CodeBee（%s · %s）" % (bi["provider_name"], bi["model"])
    elif resume_ctx is not None:
        impl = resume_ctx["agent"]
        route["implementer"] = resume_ctx["note"]
    elif mode == "manual":
        impl, _ = _pick_implementer(agents, task.get("implementer"))
    else:
        impl, route["implementer"] = router.pick(agents, "implement", task["type"], stats)
    if impl is None and bi is None:
        store.update_run(run_id, status="failed", error="没有可用智能体", ended_at=_now())
        return
    difficulty = task.get("difficulty") or "default"
    step_wd = _resume_workdir(resume_ctx, workdir) if resume_ctx else workdir
    store.update_run(run_id, route=route, difficulty=difficulty)

    sid = (resume_ctx["session"] if resume_ctx else "") or ""
    last_text = ""
    # 追话起跑（/api/runs/<id>/chat → retry_task）：信箱已有未消费消息 = 这是对话
    # 的下一轮而非首轮。CLI 继承上一轮的会话 id（真的「接着上次聊」），内置智能体
    # 靠「上一轮输出（结尾）」块带上下文；同时把首步切成续轮档。
    try:
        pending0 = store.peek_messages(run_id)
    except Exception:
        pending0 = []
    if pending0:
        prev = _direct_prev_run(task["id"], run_id)
        if prev:
            ps = (prev.get("direct_session") or {})
            if impl is not None and ps.get("agent") == impl["id"] and ps.get("session"):
                sid = sid or ps["session"]
            if ps.get("workdir"):
                step_wd = ps["workdir"]
            last_text = _direct_last_text(prev)
    first = not pending0
    turns = 0
    while True:
        _wait_gate(run_id, ev)
        if first:
            if bi is not None:
                prompt = (BUILTIN_DIRECT_PROMPT
                          .replace("__GOAL__", task["goal"])
                          .replace("__CONTEXT__", task.get("context") or "（无）")
                          .replace("__FOLLOWUPS__", FOLLOWUPS_PROTOCOL))
            else:
                prompt = (DIRECT_PROMPT
                          .replace("__GOAL__", task["goal"])
                          .replace("__CONTEXT__", task.get("context") or "（无）"))
            note = route.get("implementer", "")
            images = _task_images(task, workdir)
        else:
            if bi is not None:
                prompt = (BUILTIN_FOLLOWUP_PROMPT
                          .replace("__GOAL__", task["goal"])
                          .replace("__PREV__", (last_text or "（无）")[-3000:])
                          .replace("__FOLLOWUPS__", FOLLOWUPS_PROTOCOL))
            else:
                prompt = DIRECT_FOLLOWUP_PROMPT.replace("__GOAL__", task["goal"])
                if not sid and last_text:
                    # 无会话续接能力的 CLI（如 dsh 一次性任务）：把上一轮输出尾部带进上下文
                    prompt += "\n\n## 上一轮输出（结尾）\n" + last_text[-3000:]
            note = "对话续轮"
            images = None
        # 续轮判据：只有「本步执行期间新到」的消息才再开一轮。
        # 不能只看「信箱非空」——真实步骤的 drain 在 _run_step/_run_builtin_step
        # 内部发生，起跑前就积压的消息会被本步吃掉（peek 归零）；而 mock/不走
        # drain 的路径消息永远不消费，只看非空会空转到轮数上限。
        # 比较步骤前后的未消费数即可区分。
        before_n = len(_pending_messages(run_id))
        if bi is not None:
            res = _run_builtin_step(run_id, "direct" if first else "chat", bi, prompt,
                                    step_wd, ev=ev, note=note, images=images,
                                    followups=True)
        else:
            res = _run_step(run_id, "direct" if first else "chat", impl, prompt, step_wd,
                            readonly=False, ev=ev, note=note,
                            resume=sid or None, images=images)
        if not res["ok"]:
            store.update_run(run_id, status="failed",
                             error="执行失败: %s" % res.get("error"), ended_at=_now())
            return
        turns += 1
        last_text = (res.get("text") or "").strip()
        if impl is not None:
            new_sid = _resume_sid(impl, res.get("sid"))
            if new_sid:
                sid = new_sid
        try:
            store.update_run(run_id, direct_session={
                "agent": "builtin" if bi is not None else impl["id"],
                "session": sid, "workdir": step_wd})
        except Exception:
            pass
        if turns >= DIRECT_MAX_TURNS:
            break
        if len(_pending_messages(run_id)) <= before_n:
            break           # 本步期间没有新消息：对话告一段落
        first = False

    verdict = {"type": task["type"], "engine": "direct", "pass": True, "mode": mode,
               "direct": True, "turns": turns,
               "impl": "builtin" if bi is not None else impl["id"], "route": route}
    impl_label = ("CodeBee（%s · %s）" % (bi["provider_name"], bi["model"])
                  if bi is not None else impl.get("label"))
    report = ["# 直连任务：%s" % task["title"], "",
              "- 执行者：%s（%d 轮对话）" % (impl_label, turns), ""]
    if last_text:
        report += ["## 最近一轮输出", "", last_text[-5000:], ""]
    store.write_report(run_id, "\n".join(report))
    store.update_run(run_id, status="done", verdict=verdict,
                     summary="直连完成（%d 轮）：%s" % (turns, last_text[:160]),
                     ended_at=_now())


def _pick_reviewer_legacy(agents, impl):
    """手动模式评审者选择（v1 逻辑：跨厂商 > mock-b > 自评）。"""
    real = _real(agents)
    for a in real:
        if a["id"] != impl["id"]:
            return a, ""
    mb = _pick(agents, "mock-b")
    if mb and impl.get("mode") != "mock":
        return mb, "（无第二个真实智能体，用 mock 评审）"
    return impl, "（自评：仅有实现者一个智能体可用）"


# ---------------------------------------------------------------- review 引擎（小说/文档/翻译/调研…通用）

NOVEL_DRAFT_PROMPT = """你是一名专业作者。请在当前工作目录中撰写/修订稿件文件：`__FILE__`（直接写入该文件）。文件必须以 UTF-8 编码保存（PowerShell 写文件显式加 -Encoding UTF8，禁止依赖默认编码）。

## 写作任务
__GOAL__

## 背景与上下文
__CONTEXT__

## 要求
- 只修改 `__FILE__` 这一个文件；保持 Markdown 结构。
- 完成后用 3 句话说明本轮写了什么。"""

NOVEL_REVISE_PROMPT = """你是一名专业作者。请根据下方汇总评审意见修订稿件文件：`__FILE__`（直接写入该文件）。文件必须以 UTF-8 编码保存（PowerShell 写文件显式加 -Encoding UTF8，禁止依赖默认编码）。

## 原始写作任务
__GOAL__

## 评审汇总（各维度均分与主要问题）
__CRITIQUE__

## 要求
- 针对性改进所有 major 问题；保持既定风格与设定。
- 完成后用 3 句话说明本轮改了什么。"""

NOVEL_CRITIQUE_PROMPT = """你是严格的评审（不要使用任何工具、不要修改文件，只依据下方稿件内容评审）。
请输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{
  "scores": {"__DIMKEYS__"},
  "issues": [{"dim": "维度名", "severity": "major|minor", "note": "具体问题"}],
  "summary": "一句话总评"
}
每个维度打 1-10 分（可为小数），宁严勿宽。

## 待评审稿件
---
__MANUSCRIPT__
---"""


def _tpl(task, key, default):
    """自定义流程的提示词覆盖：任务上带模板则用之，否则用内置默认。"""
    t = (task.get(key) or "").strip()
    return t if t else default


# ---------------------------------------------------------------- 故事圣经与评审视角

BIBLE_FILE = "story-bible.md"
_BIBLE_MAX_CHARS = 20000

# 评审视角播种（dev-3.0 式 bug hunters）：N 个评审各领一个深挖镜头，
# 避免全员盯着同一处。按评审序号取模分配——同一评审每轮同一镜头，
# 提示词前缀字节稳定，不碎前缀缓存。
CRITIC_LENSES = (
    "情节逻辑与因果链（事件是否成立、动机是否充分、有没有逻辑硬伤）",
    "人物一致性与弧光（言行是否符合人设、成长是否有迹可循）",
    "文笔与节奏（语言质量、场景切换、详略与爽点铺排）",
    "设定与伏笔台账（世界观自洽、伏笔是否按故事圣经埋设与回收）",
)


def _story_bible(workdir):
    """故事圣经（NovelClaw 式结构化记忆）：工作目录里的 story-bible.md
    （人物卡/世界观/伏笔台账），作者手工维护，每章起草与评审前自动注入。
    不存在/为空返回 ""——约定式功能，零配置时不产生任何提示词噪音。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), BIBLE_FILE))
    if not _inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        txt = _read_text_any_enc(p)[:_BIBLE_MAX_CHARS].strip()
    except OSError:
        return ""
    if not txt:
        return ""
    return ("## 故事圣经（story-bible.md：人物/世界观/伏笔台账，本书一切写作与评审以此为准，"
            "与其冲突处以圣经为准）\n\n" + txt)


def _critic_lens(critics, agent):
    """该评审的专属视角；单评审/手动指定时不播种（无从轮换，也别稀释注意力）。"""
    try:
        idx = list(critics).index(agent)
    except ValueError:
        return ""
    if len(critics) < 2:
        return ""
    return CRITIC_LENSES[idx % len(CRITIC_LENSES)]


def _ensure_critique_placeholders(tpl):
    """自定义评审模板缺占位符时补上，避免稿件内容/维度定义丢失导致盲评。"""
    if "__MANUSCRIPT__" not in tpl:
        tpl += "\n\n## 待评审稿件\n---\n__MANUSCRIPT__\n---"
    if "__DIMKEYS__" not in tpl:
        tpl = ("请按维度打分（1-10 分）。\n\n" + tpl)
    return tpl


# ---------------------------------------------------------------- 连载引擎（长篇小说：逐章打磨）

SERIAL_CHAPTER_PROMPT = """你是一名网文作者（写作规范见下方经验库）。本书信息如下，请先完整读完再执行末尾的「本章任务」。

__SKILLS__

## 全书目标
__GOAL__

## 全书大纲（__SCOPE__）
__OUTLINE__

---
## 本章任务（执行这一条即可）
- 撰写本书第 __I__ 章，把本章正文写入文件 `__FILE__`（直接写入该文件，只写本章）。文件必须以 UTF-8 编码保存：PowerShell 一律显式加 `-Encoding UTF8`（如 `Set-Content -Path __FILE__ -Encoding UTF8`），禁止依赖系统默认编码，否则中文会乱码。
- 章节标题：__TITLE__
- 剧情要点：__BEATS__
- 章末钩子：__HOOK__
- 正文约 __WORDS__ 字，中文，直接开写正文（可含本章标题行）。

## 前情提要（此前各章结尾摘录，衔接用）
__PREV__

- 写完文件后，最终回复只输出一行：`第 __I__ 章完成（约 __WORDS__ 字）`——不要在回复里复述或解释正文。"""

SERIAL_REVISE_PROMPT = """你是一名网文作者。第 __I__ 章没有通过评审，请修订文件 `__FILE__`（直接改写该文件）。文件必须以 UTF-8 编码保存（PowerShell 显式加 -Encoding UTF8，禁止依赖默认编码）。

## 全书目标
__GOAL__

## 本章评审意见
__CRITIQUE__

## 要求
- 针对性解决所有 major 问题，保持与前后的剧情衔接；字数仍约 __WORDS__ 字。"""

SERIAL_GLOBAL_PROMPT = """你是网文主编（不要修改任何文件）。全书各章已完稿，请从**全书整体**视角评审。
请输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{
  "scores": {"__DIMKEYS__"},
  "issues": [{"dim": "维度名", "severity": "major|minor", "note": "具体问题（指明哪一章）"}],
  "summary": "一句话总评：是否达到可签约水平"
}
每个维度打 1-10 分，宁严勿宽。重点关注：主线一致性、人物弧光、节奏、爽点密度、完本感。

## 全书目标
__GOAL__

## 全文
---
__MANUSCRIPT__
---"""


def _read_text_any_enc(p):
    """工作区文件读文本：委托 runner.read_text_any_enc（UTF-8 → GBK → replace）。
    CLI 子代理在中文 Windows 上可能把文件落成 GBK，统一走这条纪律，消费侧
    （前情提要/评审/合并稿）不再因编码混编出 U+FFFD。"""
    return runner.read_text_any_enc(p)


def _critique_json(res, dims):
    """评审输出解析三道网，返回统一形状 {scores, issues, summary}：
    ① extract_json（严格 JSON → 围栏 → 宽松修复内嵌引号 → 花括号扫描）
    ② as_scores（兜底扫描掉进内层时，返回值本身就是 维度→分 本体，包回）
    ③ scores_from_prose（agentic CLI 把 JSON 写进文件、stdout 只留中文总结）
    任何一道出分即算有效评审——「无法解析」绝不能把正常出分的评审吞掉
    （2026-09-18 七猫案：kimi 内嵌引号病连烧三轮自动续跑全判评审全挂）。"""
    text = res.get("text") or ""
    gj = runner.as_scores(runner.extract_json(text))
    if isinstance(gj, dict) and isinstance(gj.get("scores"), dict) and gj.get("scores"):
        return gj
    prose = runner.scores_from_prose(text, dims)
    if prose:
        return {"scores": prose, "issues": [], "summary": text[:400]}
    return {"scores": {}, "issues": [],
            "summary": "评审输出无法解析：%s" % (text or res.get("error") or "")[:150]}


def _chapter_io(workdir, i, mode):
    """打开第 i 章文件；open 紧邻边界校验，路径越界直接拒绝（形态同 _ms_io）。
    读模式兼容 GBK 落盘的章稿（见 _read_text_any_enc）。"""
    p = os.path.abspath(os.path.join(workdir, "chapter-%02d.md" % i))
    if not _inside(workdir, p):
        raise ValueError("章节路径越界: chapter-%02d.md" % i)
    if mode == "r":
        return _read_text_any_enc(p)
    return open(p, mode, encoding="utf-8", errors="replace")


def _read_chapter(workdir, i):
    try:
        return _chapter_io(workdir, i, "r")
    except Exception:
        return ""


def _read_variant(workdir, i, k):
    """赛马变体稿 chapter-XX-vK.md；不存在/读失败返回空串。"""
    p = os.path.abspath(os.path.join(str(workdir), "chapter-%02d-v%d.md" % (i, k)))
    if not _inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        return _read_text_any_enc(p)
    except OSError:
        return ""


def _wc(text):
    """近似字数（去空白后的字符数，中文场景够用）。"""
    return len(re.sub(r"\s", "", text or ""))


def _write_chapter(workdir, i, text):
    with _chapter_io(workdir, i, "w") as f:
        f.write(text)


def _all_ge(scores, threshold):
    """scores 全部 ≥ threshold（分数缺失视为不达标，不炸比较）。"""
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    thr = num(threshold)
    if thr is None:
        return False
    vals = [num(v) for v in (scores or {}).values()]
    return bool(vals) and all(v is not None and v >= thr for v in vals)


def _weakest_chapters(chapter_scores, global_means, threshold, limit=2):
    """定位最该重改的章：维度不达标者优先，其次全书短板对应维度最低者。

    分数缺失/非法（None、非数字）按"未知"处理：不参与比较，也不让排序崩溃。
    """
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    thr = num(threshold) or 7.0
    short = [d for d, v in (global_means or {}).items()
             if (num(v) if num(v) is not None else thr) < thr]

    def score(c):
        m = {d: num(v) for d, v in (c.get("means") or {}).items()}
        bad = [v for d, v in m.items() if d in short and v is not None]
        vals = [v for v in m.values() if v is not None]
        return (0 if not c.get("passed") else 1,
                -sum(1 for v in vals if v < thr),
                (sum(bad) / len(bad)) if bad else 99.0,
                (sum(vals) / len(vals)) if vals else 99.0)

    cand = [c for c in chapter_scores if not c.get("passed") or short]
    cand.sort(key=score)
    return cand[:limit]


def _run_serial_review(run, task, agents, ev, stats, mode, critics, impl, route, resume_ctx, difficulty):
    """连载流水线：大纲 → 逐章起草/评审/修订 → 全局一致性评审 → 合并成书。"""
    import json as _json
    run_id = run["id"]
    workdir = task["workdir"]
    # 续会话步骤的 CLI 启动目录（稿件读写仍用 workdir）
    step_wd = _resume_workdir(resume_ctx, workdir) if resume_ctx else workdir
    serial = task.get("serial") or {}
    n = int(serial.get("chapters") or 8)
    wpc = int(serial.get("words_per_chapter") or 2500)
    dims = task.get("rubric") or DEFAULT_RUBRIC
    threshold = task.get("threshold", 7.0)
    threshold_ch = threshold - 0.5 if threshold >= 7.5 else threshold   # 单章阈值略放宽 0.5 分
    dimkey = ", ".join('"%s": 0' % d for d in dims)
    # 续写批次的全书起始章号（=1 为全新连载；>1 时章节文件/步骤/评分都用全书章号，
    # 与上一批任务在同一工作目录无缝衔接）
    start = int(serial.get("start_chapter") or 1)
    # 故事圣经：工作目录里的 story-bible.md，整个 run 内字节稳定（前缀缓存友好）
    bible = _story_bible(workdir)


    def crit_prompt_for(text, note=""):
        tpl = _ensure_critique_placeholders(
            _tpl(task, "critique_prompt", NOVEL_CRITIQUE_PROMPT))
        if note:
            tpl = tpl.replace("你是严格的评审",
                              "你是严格的评审（背景：%s，请结合全书目标评审本章节）" % note, 1)
        # stable_order：评审分轮次调用，hits 中途变化会打碎前缀缓存（§07 T1.2'）
        sk, _ = skills.block_for(task, stable_order=True)
        if sk:
            tpl = tpl.replace("## 待评审稿件", "%s\n\n## 待评审稿件" % sk, 1)
        if bible:
            tpl = tpl.replace("## 待评审稿件", "%s\n\n## 待评审稿件" % bible, 1)
        return tpl.replace("__DIMKEYS__", dimkey).replace(
            "__MANUSCRIPT__", text or "（稿件为空！）")

    # ---- 1) 大纲（断点续跑时直接继承上一遍，保证全书结构一致）
    inherit = run.get("inherit") or {}
    done_set = set(inherit.get("done_chapters") or [])
    inh_scores = {c.get("chapter"): c for c in (inherit.get("chapter_scores") or [])}
    if inherit.get("outline"):
        outline = inherit["outline"]
        # 历史遗留：降级/模板大纲被继承时，真实任务宁可中止重生成，也不按空模板写全书
        if (outline.get("degraded") or outline.get("source") == "template") \
                and impl.get("mode") != "mock":
            store.update_run(run_id, status="failed",
                             error="继承的大纲为降级模板（无真实情节），已中止以重新生成大纲",
                             ended_at=_now())
            return
        n = len(outline.get("chapters") or []) or n
        outline_step, _ = store.add_step(run_id, "outline", impl["id"], impl.get("label"),
                                         note="断点续跑")
        store.finish_step(run_id, outline_step["n"], "done",
                          summary="继承上一遍大纲（共 %d 章），已完成 %d 章将被复用"
                                  % (n, len(done_set & set(range(start, start + n)))),
                          duration_s=0.1)
    else:
        _wait_gate(run_id, ev)
        outline_step, outline_log = store.add_step(run_id, "outline", impl["id"], impl.get("label"),
                                                   note=route.get("author", ""))
        outline = planner.make_serial_outline(_steered_task(run_id, task), impl, workdir, ev,
                                              log_path=str(outline_log) if outline_log else None)
        if outline.get("degraded") and impl.get("mode") != "mock":
            # 兜底模板只有章号没有情节，据此写出的两万字等于废稿——
            # 中止并交给自动续跑等编排者恢复后重试，而不是空转烧配额。
            store.finish_step(run_id, outline_step["n"], "failed",
                              summary="大纲降级：%s" % (outline.get("degraded_reason") or "编排者不可用"),
                              duration_s=None)
            store.update_run(run_id, status="failed",
                             error="%s，已中止以免按空模板写全书"
                                   % (outline.get("degraded_reason") or "编排者不可用"),
                             ended_at=_now())
            return
        try:
            with open(outline_log, "a", encoding="utf-8") as f:
                f.write("\n===== 最终大纲 =====\n")
                f.write(_json.dumps(outline, ensure_ascii=False, indent=2))
        except Exception:
            pass
        store.finish_step(run_id, outline_step["n"], "done",
                          summary="大纲来源 %s：%s（共 %d 章）" % (
                              outline.get("source", "?"),
                              outline.get("book_title") or task["title"], n),
                          duration_s=0.1 if impl.get("mode") == "mock" else None)
    store.update_run(run_id, outline=outline)
    _check_cancel(ev)
    end = start + n - 1   # 本批最后一章的全书章号（全局评审与合并成书覆盖 1..end）
    outline_txt = "\n".join(
        "第 %d 章《%s》：%s%s" % (start + k, c["title"], c["beats"],
                                ("（章末钩子：%s）" % c.get("hook")) if c.get("hook") else "")
        for k, c in enumerate(outline["chapters"]))

    chapter_scores = []          # [{chapter,title,means,passed,rounds,words}]
    issues_all = []

    # ---- 2) 逐章
    for k in range(1, n + 1):
        i = start + k - 1        # 全书章号：文件名/步骤角色/评分记录都按全书编号
        ch = outline["chapters"][k - 1]
        ch_file = "chapter-%02d.md" % i
        prev = ""
        draft_sid = ""  # §07 T1.1：本轮 draft/复用章的会话 id（revise 复用；reuse 时为空）
        if i > 1:
            tails = []
            for j in range(max(1, i - 2), i):
                t = _read_chapter(workdir, j)
                if t:
                    tails.append("（第 %d 章结尾）…%s" % (j, t[-260:].strip()))
            prev = "\n".join(tails) or "（无）"

        # 评审-修订（每章至多 1 轮修订）
        rounds_used = 1
        means = {}

        def run_critique(text, rnd, note_extra="", critic_sids=None):
            """一轮多维评审：返回 (cj_by_agent, scored)。变体赛马与主循环共用。"""
            cj_map, sids = {}, dict(critic_sids or {})
            scored = 0   # 真正给出分数的评审数；失败/不可解析不得当成 0 分计入
            for agent in critics:
                role = "critique-c%d" % i
                if agent.get("mode") == "mock":
                    step, log_abs = store.add_step(run_id, role, agent["id"], agent.get("label"))
                    time.sleep(0.15)
                    cj = mocks.critique(agent["id"], rnd, dims, threshold_ch)
                    scored += 1
                    store.finish_step(run_id, step["n"], "done",
                                      summary="均分 %.1f：%s" % (
                                          sum(cj["scores"].values()) / max(1, len(dims)),
                                          cj["summary"]),
                                      duration_s=0.15)
                else:
                    lens = _critic_lens(critics, agent)
                    res = _run_step(run_id, role, modelhub.bind_agent(agent, difficulty),
                                    crit_prompt_for(
                                        text,
                                        note=("小说第 %d 章" % i) + (
                                            "｜你的专属评审视角：%s（其他评审会覆盖其余视角，"
                                            "请深挖你的镜头，但所有维度仍需打分）" % lens)
                                        if lens else "") + note_extra,
                                    workdir, readonly=True, ev=ev,
                                    resume=sids.get(agent["id"]))
                    cj = _critique_json(res, dims)
                    if cj.get("scores"):
                        scored += 1
                    # §07 T1.1：记录该评审的会话 id（第 2 轮复用）
                    csid = _resume_sid(agent, res.get("sid"))
                    if csid:
                        sids[agent["id"]] = csid
                cj_map[agent["id"]] = cj
                issues_all.extend({"chapter": i, **it} for it in (cj.get("issues") or [])[:6])
                _check_cancel(ev)

            # 评审者级 fallback：名单内评审全挂（网关抖动/CLI 故障）时，
            # 从其它已启用真实智能体补位至多 2 个（排除 mock 与已试过的），
            # 只要有一个出分就不触发「评审全败中止」。
            if not scored and impl.get("mode") != "mock":
                tried = {a.get("id") for a in critics}
                pool = [a for a in (agents or [])
                        if a.get("mode") == "real" and a.get("id") not in tried]
                for spare in pool[:2]:
                    res = _run_step(run_id, role, modelhub.bind_agent(spare, difficulty),
                                    crit_prompt_for(
                                        text,
                                        note="小说第 %d 章" % i) + note_extra,
                                    workdir, readonly=True, ev=ev)
                    cj = runner.extract_json(res.get("text") or "")
                    if isinstance(cj, dict) and isinstance(cj.get("scores"), dict) \
                            and cj.get("scores"):
                        cj_map[spare["id"]] = cj
                        scored += 1
                        issues_all.extend({"chapter": i, **it}
                                          for it in (cj.get("issues") or [])[:6])
                        break
                    _check_cancel(ev)
            return cj_map, scored, sids

        def means_of(cj_map):
            vals = {}
            for d in dims:
                xs = [float(cj["scores"].get(d, 0)) for cj in cj_map.values()
                      if isinstance(cj.get("scores"), dict) and d in cj["scores"]]
                vals[d] = round(sum(xs) / len(xs), 1) if xs else 0.0
            return vals

        critic_sids = {}   # §07 T1.1：每评审的会话 id（第 2 轮复用，前缀走缓存读）
        race_cj = None     # 变体赛马已评审胜者：直接作为第 1 轮结果，不重评
        race_scored = 0

        reuse = i in done_set and os.path.exists(os.path.join(workdir, ch_file))
        if reuse:
            # 坏稿防线：进程中途死掉会留下半成品文件（实测 4 字节的 chapter-12），
            # 按「文件存在」复用会让评审给垃圾稿打低分、修订陷入循环。
            # 字数低于 max(200, 30% 目标字数) → 视为未完成，整章重写。
            words_now = _wc(_read_chapter(workdir, i))
            if words_now < max(200, int(wpc * 0.3)):
                reuse = False
        if reuse:
            # 断点续跑：上一遍已写好的章直接复用（不重写；分数沿用既有记录或重评）
            step, _log = store.add_step(run_id, "draft-c%d" % i, impl["id"], impl.get("label"),
                                        note="断点续跑")
            store.finish_step(run_id, step["n"], "done",
                              summary="（断点续跑）复用上一遍成稿 %s（约 %d 字）"
                                      % (ch_file, _wc(_read_chapter(workdir, i))),
                              duration_s=0.1)
        elif impl.get("mode") == "mock":
            step, log_abs = store.add_step(run_id, "draft-c%d" % i, impl["id"], impl.get("label"))
            _write_chapter(workdir, i, mocks.draft_manuscript(
                {"title": ch["title"], "goal": task["goal"]}, 1))
            time.sleep(0.2)
            store.finish_step(run_id, step["n"], "done", summary="（mock）第 %d 章草稿落盘" % i,
                              duration_s=0.2)
        else:
            # stable_order：同一任务 8 个章节的技能块必须字节级一致（§07 T1.2' 前缀缓存）
            sk_block, _ = skills.block_for(task, stable_order=True)
            if bible:
                sk_block = (sk_block + "\n\n" + bible) if sk_block else bible
            scope = ("本章 = 大纲第 %d 章" % i) if start == 1 else (
                "本批为第 %d–%d 章，下列按全书章号列出各章要点" % (start, end))

            def _draft_prompt(vfile):
                return (SERIAL_CHAPTER_PROMPT
                        .replace("__SKILLS__", sk_block)
                        .replace("__SCOPE__", scope)
                        .replace("__I__", str(i)).replace("__FILE__", vfile)
                        .replace("__GOAL__", task["goal"])
                        .replace("__OUTLINE__", outline_txt)
                        .replace("__PREV__", prev)
                        .replace("__TITLE__", ch["title"])
                        .replace("__BEATS__", ch["beats"] or "按大纲推进")
                        .replace("__HOOK__", ch.get("hook") or "留下悬念")
                        .replace("__WORDS__", str(wpc)))

            n_variants = max(1, min(3, int(serial.get("variants") or 1)))
            race = (n_variants >= 2 and not _compaction_enabled()
                    and not resume_ctx)   # 续会话语义只认 impl 一人，赛马退场
            if not race:
                prompt = _draft_prompt(ch_file)
                # 网关突发限流（Not Allowed / UnknownError）秒~分钟级自愈：章稿
                # 失败先退避重试同作者（保住连载风格一致），重试穷尽才判死整 run
                # ——2026-09-17 七猫连载实测：多 run 并行推进时后位章节必撞限流，
                # 一章失败即整 run 作废，太贵。
                def _chapter_state():
                    """草稿验收：成品是章稿文件本身，不是退出码——文件够长才算好。"""
                    txt = _read_chapter(workdir, i)
                    return (bool(txt) and _wc(txt) >= int(wpc * 0.6)), (txt or "")

                res = None
                good = False
                txt = ""
                use_prompt = prompt
                for draft_attempt in range(3):
                    if draft_attempt:
                        if ev is not None and ev.is_set():
                            break
                        time.sleep(30 * draft_attempt)   # 30s / 60s 退避
                    if draft_attempt and len(prompt) > 12000 and sk_block and sk_block in prompt:
                        # 长提示词在容量受限通道（讯飞托管 35B 等）上会挂起/秒拒
                        # ——降级重试：经验库块截到 4K 字，保留大纲/前情/本章要点
                        # （2026-09-17 七猫实测：全量 30KB 对讯飞必挂）
                        use_prompt = prompt.replace(
                            sk_block, sk_block[:4000] + "\n\n（经验库已因通道容量限制精简）")
                    res = _run_step(run_id, "draft-c%d" % i, modelhub.bind_agent(impl, difficulty), use_prompt,
                                    step_wd, readonly=False, ev=ev, timeout=2400,
                                    resume=resume_ctx["session"] if resume_ctx else None,
                                    images=_task_images(task, workdir),
                                    note=("起草重试 %d/2（网关限流退避）" % draft_attempt) if draft_attempt else "")
                    good, txt = _chapter_state()
                    if good:
                        break
                    if res["ok"] and _wc(res.get("text") or "") >= int(wpc * 0.6):
                        # 回复正文就是完整章稿（kimi 35B 实测：正文当消息回而不
                        # 落盘）→ 代为落盘救回成品
                        try:
                            _write_chapter(workdir, i, res["text"])
                            good, txt = _chapter_state()
                        except OSError:
                            pass
                        if good:
                            break
                    if ev is not None and ev.is_set():
                        break
                # 同作者重试穷尽 → 起草换将：按路由分序逐个试备选（最多 2 个，
                # 只试一个会让第二名没机会——2026-09-17 c35 实测 opencode 顶在
                # 前面，能干活的 kimi 永远轮不上）。连载不断档优先，风格差异交
                # 评审门与后续 revise 拉回。
                if not good:
                    tried = {impl["id"], "mock-a", "mock-b"}
                    for _alt in range(2):
                        if good or (ev is not None and ev.is_set()):
                            break
                        other, other_reason = router.pick(
                            agents, "implement", task.get("type") or "serial", None,
                            exclude=tried)
                        if not (other and other.get("mode") == "real"):
                            break
                        tried.add(other["id"])
                        res = _run_step(run_id, "draft-c%d" % i,
                                        modelhub.bind_agent(other, difficulty), use_prompt,
                                        step_wd, readonly=False, ev=ev, timeout=2400,
                                        images=_task_images(task, workdir),
                                        note="起草换将 %s → %s：%s" % (
                                            impl["id"], other["id"],
                                            (other_reason or "")[:90]))
                        good, txt = _chapter_state()
                        if not good and res["ok"] and _wc(res.get("text") or "") >= int(wpc * 0.6):
                            try:
                                _write_chapter(workdir, i, res["text"])
                                good, txt = _chapter_state()
                            except OSError:
                                pass
                        if good:
                            draft_sid = ""   # 换将作者无本任会话，revise 另起
                if not good:
                    time.sleep(3)   # 落盘竞态宽限：CLI 崩溃退出前写的文件可能晚于
                    good, txt = _chapter_state()   # 退出检查零点几秒才可见（c34 实测）
                if not good:
                    store.update_run(run_id, status="failed",
                                     error="第 %d 章起草失败: %s" % (i, (res or {}).get("error")), ended_at=_now())
                    return
                if not res["ok"]:
                    # 成品是文件不是退出码：CLI 超时但章稿已完整落盘（终章长文实测
                    # 反复出现——文件写完、收尾声明没等到）就送评审门把关，别整章作废
                    live = (store.get_run(run_id).get("steps") or [])
                    if live:
                        store.finish_step(run_id, live[-1]["n"], "done",
                                          summary="起草调用超时，但章稿已完整落盘（约 %d 字）——交评审门判质量"
                                                  % _wc(txt))
                # §07 T1.1：draft 会话 id 供本轮 revise 复用（同会话内前缀走缓存读计价）
                draft_sid = (_resume_sid(impl, res.get("sid")) or "") if res["ok"] else draft_sid
            else:
                # ---- 同章多稿赛马（dev-3.0）：n 个作者并行起草 → 逐变体评审 →
                # 均分最高者为正稿。变体写隔离文件 chapter-XX-vK.md，赢家改名、
                # 败稿删除；变体 0 = 本任作者（revise 会话沿用），其余取跨族优先的
                # 其他真实智能体，不足时同作者开新会话凑数。
                scored_variants = []
                for race_round in range(2):
                    # 全变体失败（网关突发限流）→ 60s 退避重赛一轮，别一章判死
                    if race_round:
                        if ev is not None and ev.is_set():
                            break
                        time.sleep(60)
                        for kk in range(n_variants):
                            # 清上一轮残稿：防陈旧半成品被本轮评分误认成新成品
                            try:
                                os.remove(os.path.join(workdir, "chapter-%02d-v%d.md" % (i, kk)))
                            except OSError:
                                pass
                    pool = [impl]
                    others = [a for a in agents if a.get("mode") == "real" and a["id"] != impl["id"]]
                    others.sort(key=lambda a: 0 if a.get("kind") != impl.get("kind") else 1)
                    pool += others[:n_variants - 1]
                    while len(pool) < n_variants:
                        pool.append(impl)   # 不够就同作者再开一路（新会话天然出不同稿）
                    results = {}

                    def _draft_one(kk, agent):
                        vfile = "chapter-%02d-v%d.md" % (i, kk)
                        r = _run_step(run_id, "draft-c%d-v%d" % (i, kk),
                                      modelhub.bind_agent(agent, difficulty),
                                      _draft_prompt(vfile), step_wd, readonly=False, ev=ev,
                                      timeout=2400,
                                      # 赛马只在全新起草时启用（无续会话），每路都是新会话
                                      images=_task_images(task, workdir),
                                      note="赛马变体 %d/%d（%s）" % (kk + 1, len(pool), agent.get("id")))
                        results[kk] = (vfile, agent, r)

                    threads = []
                    for kk, agent in enumerate(pool):
                        th = threading.Thread(target=_draft_one, args=(kk, agent),
                                              name="race-%s-c%d-v%d" % (run_id, i, kk), daemon=True)
                        threads.append(th)
                        th.start()
                    for th in threads:
                        th.join(3000)
                    _check_cancel(ev)

                    scored_variants = []
                    for kk in range(len(pool)):
                        vfile, agent, r = results.get(kk, (None, None, None))
                        if vfile is None:
                            continue
                        txt = _read_variant(workdir, i, kk)
                        ok_text = txt and _wc(txt) >= int(wpc * 0.6)
                        if r is not None and not r["ok"] and not ok_text:
                            continue   # 这一路彻底失败（无成品也不够长）
                        if not ok_text:
                            continue
                        cj_map, sc, sids2 = run_critique(
                            txt, 1, note_extra="（本稿为同章赛马变体 %d/%d，只评这一份）" % (kk + 1, len(pool)))
                        m = means_of(cj_map)
                        avg = round(sum(m.values()) / max(1, len(m)), 2) if m else 0.0
                        scored_variants.append({"variant": kk, "agent": agent.get("id"),
                                                "file": vfile, "means": m, "avg": avg,
                                                "cj": cj_map, "scored": sc, "sids": sids2})
                    if scored_variants:
                        break
                if not scored_variants:
                    store.update_run(run_id, status="failed",
                                     error="第 %d 章赛马全部变体起草失败" % i, ended_at=_now())
                    return
                scored_variants.sort(key=lambda v: (-v["avg"], v["variant"]))
                win = scored_variants[0]
                # 收敛：赢家转正，败稿删除；胜者评审结果直接作为第 1 轮（不重评）
                if win["file"] != ch_file:
                    try:
                        os.replace(os.path.join(workdir, win["file"]),
                                   os.path.join(workdir, ch_file))
                    except OSError as e:
                        store.update_run(run_id, status="failed",
                                         error="第 %d 章赛马收卷失败: %r" % (i, e), ended_at=_now())
                        return
                for v in scored_variants[1:]:
                    try:
                        os.remove(os.path.join(workdir, v["file"]))
                    except OSError:
                        pass
                race_log = [{"variant": v["variant"], "agent": v["agent"], "avg": v["avg"],
                             "chosen": v is win, "reviewed": v["scored"] > 0}
                            for v in scored_variants]
                latest_run = store.get_run(run_id) or {}
                all_variants = dict(latest_run.get("variants") or {})
                all_variants[str(i)] = race_log
                store.update_run(run_id, variants=all_variants)
                if win["scored"] > 0:
                    race_cj, race_scored = win["cj"], win["scored"]
                    critic_sids.update(win["sids"])
                # revise 会话沿用变体 0（本任作者）的会话；那一路失败则留空
                r0 = (results.get(0) or (None, None, None))[2] or {}
                draft_sid = _resume_sid(impl, r0.get("sid")) or ""

        # 复用章且上一遍已有评审分数 → 直接沿用，不再重评
        if reuse and inh_scores.get(i, {}).get("means"):
            cs = dict(inh_scores[i])
            cs.setdefault("chapter", i)
            cs["reused"] = True
            chapter_scores.append(cs)
            store.update_run(run_id, chapter_scores=chapter_scores)
            continue

        for rnd in (1, 2):
            text = _read_chapter(workdir, i)
            if rnd == 1 and race_cj is not None:
                cj_by_agent, scored = race_cj, race_scored
            else:
                cj_by_agent, scored, sids_now = run_critique(text, rnd, critic_sids=critic_sids)
                critic_sids.update(sids_now)
            if not scored:
                # 「评不上」≠「评了 0 分」：全部评审失败时中止本轮，
                # 让自动续跑换个时机重试，而不是以 0 分误判章稿质量。
                store.update_run(run_id, status="failed",
                                 error="第 %d 章评审全部失败（评审模型不可用或输出不可解析），"
                                       "已中止以免以 0 分误判质量" % i, ended_at=_now())
                return
            means = means_of(cj_by_agent)
            passed = bool(means) and all(v >= threshold_ch for v in means.values())
            if passed or rnd == 2:
                break
            # 第 1 轮不达标 → 修订该章后重评审
            rounds_used = 2
            majors = [x for x in issues_all if x.get("chapter") == i
                      and x.get("severity") == "major"][:8]
            crit_lines = ["- %s：%.1f（章阈值 %.1f）" % (d, means[d], threshold_ch) for d in dims]
            crit_lines += ["- [%s] %s" % (x.get("dim", "?"), str(x.get("note", ""))[:140])
                           for x in majors]
            if impl.get("mode") == "mock":
                step, log_abs = store.add_step(run_id, "revise-c%d" % i, impl["id"],
                                               impl.get("label"))
                _write_chapter(workdir, i, mocks.draft_manuscript(
                    {"title": ch["title"], "goal": task["goal"]}, 2))
                time.sleep(0.15)
                store.finish_step(run_id, step["n"], "done",
                                  summary="（mock）已按评审意见修订第 %d 章" % i, duration_s=0.15)
            else:
                prompt = (SERIAL_REVISE_PROMPT
                          .replace("__I__", str(i)).replace("__FILE__", ch_file)
                          .replace("__GOAL__", task["goal"])
                          .replace("__CRITIQUE__", "\n".join(crit_lines))
                          .replace("__WORDS__", str(wpc)))
                _run_step(run_id, "revise-c%d" % i, modelhub.bind_agent(impl, difficulty), prompt,
                          step_wd, readonly=False, ev=ev, timeout=2400,
                          resume=resume_ctx["session"] if resume_ctx else draft_sid)
            _check_cancel(ev)
        chapter_scores.append({"chapter": i, "title": ch["title"], "means": means,
                               "passed": bool(means) and all(v >= threshold_ch for v in means.values()),
                               "rounds": rounds_used,
                               "words": _wc(_read_chapter(workdir, i))})
        # 每章即时持久化：长篇中断/超时后可断点续跑，不丢已完成章的分数
        store.update_run(run_id, chapter_scores=chapter_scores)

    # ---- 3) 全局一致性评审（覆盖 1..end 全书：续写批次必须连同旧章一起查一致性）
    full_text = "\n\n".join(_read_chapter(workdir, i) for i in range(1, end + 1))
    global_issues = []

    def run_global_round(agent_list):
        """一轮全局评审：返回 (出分评审数, 按维累计分)。失败/不可解析不得当成低分计入。"""
        gmeans_acc, scored = {}, 0
        for agent in agent_list:
            if agent.get("mode") == "mock":
                step, _ = store.add_step(run_id, "global-critique", agent["id"],
                                         agent.get("label"))
                time.sleep(0.15)
                gj = {"scores": {d: 8.0 for d in dims},
                      "issues": [], "summary": "（mock）全书结构完整，达到可签约水平"}
                store.finish_step(run_id, step["n"], "done", summary="均分 8.0：（mock）全书达标",
                                  duration_s=0.15)
                scored += 1
            else:
                gtpl = SERIAL_GLOBAL_PROMPT
                if bible:
                    gtpl = gtpl.replace("## 全书目标", bible + "\n\n## 全书目标", 1)
                res = _run_step(run_id, "global-critique", modelhub.bind_agent(agent, difficulty),
                                (gtpl.replace("__DIMKEYS__", dimkey)
                                 .replace("__GOAL__", task["goal"])
                                 .replace("__MANUSCRIPT__", full_text[:60000])),
                                workdir, readonly=True, ev=ev, timeout=2400)
                gj = _critique_json(res, dims)
                if gj.get("scores"):
                    scored += 1
            global_issues.extend({"chapter": "全书", **it} for it in (gj.get("issues") or [])[:8])
            for d in dims:
                v = gj.get("scores", {}).get(d)
                if v is not None:
                    gmeans_acc.setdefault(d, []).append(float(v))
            _check_cancel(ev)
        return scored, gmeans_acc

    gscored, gmeans_acc = run_global_round(critics)
    # 「评不上」≠「评了低分」：全局评审全挂时先从其它真实智能体补位（对齐章级
    # 评审者级 fallback）；补位后仍零分则判 run 失败——「无法评审」绝不能当成
    # 「全局评审未通过」去盖「未达标」章（2026-09-18 假未达标案：codex 绑定链
    # 全失效 + kimi 命令行超长，global_scores 为空被 _all_ge 判成不通过）。
    # mock 评审总出分，不会误触；判失败不设 impl mock 例外（对齐章级中止）。
    if not gscored:
        tried = {a.get("id") for a in critics}
        for spare in [a for a in (agents or [])
                      if a.get("mode") == "real" and a.get("id") not in tried][:2]:
            sc, acc = run_global_round([spare])
            for d, xs in acc.items():
                gmeans_acc.setdefault(d, []).extend(xs)
            gscored += sc
            if gscored:
                break
        if not gscored:
            store.update_run(run_id, status="failed",
                             error="全局一致性评审全部失败（评审模型不可用或输出不可解析），"
                                   "已中止以免把「无法评审」误判为「未达标」。"
                                   "各章稿件已全部落盘，修复评审链后续跑可直接收尾",
                             ended_at=_now())
            return
    global_means = {d: round(sum(xs) / len(xs), 1) for d, xs in gmeans_acc.items()}
    global_pass = _all_ge(global_means, threshold)

    # ---- 3.5) 自驱打磨：全局评审不过 → 自动重改最弱章并重评（至多 2 轮，无需人工）
    polish_rounds = 0
    while (not global_pass) and polish_rounds < 2 and chapter_scores and global_means:
        polish_rounds += 1
        weak = _weakest_chapters(chapter_scores, global_means, threshold, limit=2)
        if not weak:
            break
        note = "自动打磨第 %d 轮：全局评审未过，重改最弱章 %s" % (
            polish_rounds, "、".join("第 %d 章" % c["chapter"] for c in weak))
        pstep, _ = store.add_step(run_id, "polish-r%d" % polish_rounds, impl["id"],
                                  impl.get("label"), note=note)
        fixed = []
        for c in weak:
            i = c["chapter"]
            ch = outline["chapters"][i - start]   # outline 是本批的：按批内下标取，i 是全书章号
            dims_txt = "；".join(
                "%s %.1f" % (d, (c.get("means") or {}).get(d, 0.0))
                for d in dims if float((c.get("means") or {}).get(d, 0.0)) < threshold)
            gj = "；".join("%s %.1f" % (d, s2) for d, s2 in global_means.items()
                           if float(s2) < threshold)
            crit = ("- 本章维度不达标：%s（阈值 %.1f）\n- 全书一致性评审指出的短板：%s"
                    "\n- 重改要求：优先修全书节奏/衔接问题（章间过渡、信息倾泻、主角主动性），"
                    "再补本章短板；不得改动既有剧情主线的关键事实。"
                    % (dims_txt or "（无）", threshold, gj or "（无）"))
            if impl.get("mode") == "mock":
                _write_chapter(workdir, i, mocks.draft_manuscript(
                    {"title": ch["title"], "goal": task["goal"]}, 3))
            else:
                prompt = (SERIAL_REVISE_PROMPT
                          .replace("__I__", str(i)).replace("__FILE__", "chapter-%02d.md" % i)
                          .replace("__GOAL__", task["goal"])
                          .replace("__CRITIQUE__", crit)
                          .replace("__WORDS__", str(wpc)))
                res = _run_step(run_id, "polish-c%d" % i, modelhub.bind_agent(impl, difficulty),
                                prompt, workdir, readonly=False, ev=ev, timeout=2400,
                                resume=resume_ctx["session"] if resume_ctx else None)
                if not res["ok"]:
                    continue
            # 重评该章
            cj_by_agent = {}
            for agent in critics:
                if agent.get("mode") == "mock":
                    cj = mocks.critique(agent["id"], 3, dims, threshold_ch)
                else:
                    res2 = _run_step(run_id, "critique-c%d" % i, modelhub.bind_agent(agent, difficulty),
                                     crit_prompt_for(_read_chapter(workdir, i), note="小说第 %d 章（打磨后）" % i),
                                     workdir, readonly=True, ev=ev)
                    cj = runner.extract_json(res2.get("text") or "")
                    if not isinstance(cj, dict) or not isinstance(cj.get("scores"), dict):
                        cj = {"scores": {}, "issues": [], "summary": "评审输出无法解析"}
                cj_by_agent[agent["id"]] = cj
                _check_cancel(ev)
            # 重评全挂（本轮所有评审都解析不出分数）→ 保留该章原分与达标态：
            # 拿「无法重评」覆盖真实分数，会把打磨前的好章误标 0 分、误判不达标
            repolished = any(isinstance(c.get("scores"), dict) and c["scores"]
                             for c in cj_by_agent.values())
            vals = {}
            for d in dims:
                xs = [float(cj["scores"].get(d, 0)) for cj in cj_by_agent.values()
                      if isinstance(cj.get("scores"), dict) and d in cj["scores"]]
                vals[d] = round(sum(xs) / len(xs), 1) if xs else 0.0
            for c2 in chapter_scores:
                if c2["chapter"] == i:
                    if repolished:
                        c2["means"] = vals
                        c2["passed"] = bool(vals) and all(v >= threshold_ch for v in vals.values())
                        c2["rounds"] = int(c2.get("rounds") or 1) + 1
                        c2["polished"] = True
                    c2["words"] = _wc(_read_chapter(workdir, i))
                    fixed.append(i)
            store.update_run(run_id, chapter_scores=chapter_scores)
            _check_cancel(ev)
        # 重评全书一致性（同一评审闭包；本轮全挂则保留上一轮结论——评审链挂了
        # 不代表书变差，不能拿「无法评审」覆盖真实分数）
        full_text = "\n\n".join(_read_chapter(workdir, i2) for i2 in range(1, end + 1))
        gscored2, gmeans_acc2 = run_global_round(critics)
        if gscored2:
            global_means = {d: round(sum(xs) / len(xs), 1) for d, xs in gmeans_acc2.items()}
            global_pass = _all_ge(global_means, threshold)
        store.finish_step(run_id, pstep["n"], "done" if global_pass else "failed",
                          summary="重改 %s；打磨后全局 %s（%s）" % (
                              "、".join("第 %d 章" % x for x in fixed),
                              "、".join("%s %.1f" % (d, global_means.get(d, 0.0)) for d in dims),
                              "通过" if global_pass else "仍未通过"),
                          duration_s=1.0)

    # ---- 4) 合并成书
    step, _ = store.add_step(run_id, "merge", "builtin", "内置合成器")
    ms_name = _ms_name(task.get("manuscript"))
    book_title = outline.get("book_title") or task["title"]
    parts = ["# %s" % book_title, ""]
    for i in range(1, end + 1):     # 合并全书：续写时包含上一批已写好的章
        parts.append(_read_chapter(workdir, i).strip())
        parts.append("")
    with _ms_io(workdir, ms_name, "w") as f:
        f.write("\n".join(parts))
    total_words = _wc("\n".join(parts))
    store.finish_step(run_id, step["n"], "done",
                      summary="已合并 %d 章为 %s（约 %d 字）" % (n, ms_name, total_words),
                      duration_s=0.1)

    chapters_pass = all(c["passed"] for c in chapter_scores)
    publishable = bool(chapters_pass and global_pass)
    overall = round(sum(sum(c["means"].values()) / max(1, len(c["means"]))
                        for c in chapter_scores) / max(1, len(chapter_scores)), 1)
    verdict = {
        "type": task["type"], "engine": "review", "serial": True,
        "publishable": publishable, "overall": overall, "mode": mode,
        "threshold": threshold, "chapters_used": n,
        "start_chapter": start, "end_chapter": end, "total_words": total_words,
        "chapter_scores": chapter_scores, "global_scores": global_means,
        "global_pass": global_pass, "route": route,
    }
    scope_txt = ("续写第 %d–%d 章，衔接前文 %d 章" % (start, end, start - 1)) if start > 1 \
        else ("共 %d 章" % n)
    lines = ["# 连载小说评审报告：%s" % task["title"], "",
             "- 书名：%s（%s / 约 %d 字，合并为 `%s`）" % (
                 book_title, scope_txt, total_words, ms_name),
             "- 结论：**%s**（各章门禁 %s / 全局评审 %s）" % (
                 "✅ 达到发布标准" if publishable else "❌ 未达标",
                 "通过" if chapters_pass else "未通过",
                 "通过" if global_pass else "未通过"),
             "- 编排模式：%s　作者：%s　评审组：%s" % (
                 "智能" if mode == "auto" else "手动",
                 impl.get("label"), "、".join(a.get("label") for a in critics)),
             "", "## 各章得分（章阈值 %.1f）" % threshold_ch, "",
             "| 章 | 标题 | " + " | ".join(dims) + " | 均分 | 达标 | 轮次 | 字数 |",
             "|" + "---|" * (len(dims) + 6)]
    for c in chapter_scores:
        mean = round(sum(c["means"].values()) / max(1, len(c["means"])), 1) if c["means"] else 0
        lines.append(("| %d | %s | " % (c["chapter"], c["title"]))
                     + " | ".join("%.1f" % c["means"].get(d, 0.0) for d in dims)
                     + " | %.1f | %s | %d | %d |" % (mean, "✓" if c["passed"] else "✗",
                                                     c["rounds"], c["words"]))
    lines += ["", "## 全局评审（阈值 %.1f）" % threshold, ""]
    lines += ["- %s：%.1f" % (d, global_means.get(d, 0.0)) for d in dims]
    lines += ["", "## 主要问题", ""]
    majors = [x for x in issues_all + global_issues if x.get("severity") == "major"][:12]
    if majors:
        lines.extend("- [第%s章][%s] %s" % (str(x.get("chapter", "?")), x.get("dim", "?"),
                                            str(x.get("note", ""))[:150]) for x in majors)
    else:
        lines.append("（无 major 问题）")
    store.write_report(run_id, "\n".join(lines))
    store.update_run(run_id, status="done", verdict=verdict,
                     summary="连载任务%s（%s，约 %d 字，综合 %.1f）" % (
                         "达标" if publishable else "未达标", scope_txt,
                         total_words, overall),
                     ended_at=_now())


# ---- Best-of-N 赛马起草（非连载单稿；借鉴 freebuff editor-multi-prompt + best-of-n-selector）----
# 连载路径的同章赛马是另一套（_run_serial_review 的 n_variants 分支，逐变体全维度
# 评分），互不影响。赛马与压缩会话互斥（同连载赛马的守卫）：并行 _run_step 会在
# 同一 run 的会话事件流里交错，压缩面选择区域可能被搅乱。

# 每路候选的写法策略（刻意制造多样性，不是同提示词跑 N 遍）；0 号不带策略=现状等价
BESTOF_STRATEGIES = (
    "",
    "\n\n## 写法策略\n本稿以「场景与画面感」优先：多用具体可感的细节、动作与环境推进，少直接概括。",
    "\n\n## 写法策略\n本稿以「对话与冲突」驱动：让人物在对话与碰撞中推进情节，节奏明快、信息密度高。",
)

BESTOF_SELECTOR_PROMPT = """你是终审编辑。同一个写作任务并行产生了多份候选稿，请对比选出最好的一份。

## 任务目标
__GOAL__

## 对比要点（按重要性）
贴合任务要求与评审维度 > 结构与可读性 > 语言质量 > 不跑题、不注水

__CANDIDATES__

## 输出
只输出一个 JSON 对象，不要输出任何其他内容：
{"pick": <选中候选的编号（从 0 开始的整数）>, "reason": "<一句话理由>", "improvements": "<落选稿里值得吸收进选中稿的具体优点，多条用分号隔开；没有就给空字符串>"}
"""


def _variant_name(ms_name, k):
    """候选稿文件名：manuscript.md → manuscript.v0.md（无扩展名则尾部追加）。"""
    m = re.search(r"(\.[^./\\]+)$", ms_name)
    return (ms_name[:m.start()] + ".v%d" % k + m.group(1)) if m else (ms_name + ".v%d" % k)


def _bestof_draft(run_id, task, impl, prompt_fn, ms_name, workdir, step_wd, ev,
                  resume_ctx, difficulty, best_of, sel_agent, write_ms, note=""):
    """非连载评审流的 Best-of-N 赛马起草。

    N 路并行起草到各自变体文件（每路带不同写法策略）→ 终审选择器单次调用对比
    择优并回收落选稿精华 → 胜者写回正式稿名。某路失败只弃那路；全败返回其中
    一路的原始结果（保持原报错行为）；选择器失败/不可解析回落 0 号候选（等价
    单稿行为）。选择结果与败者精华记入 run.bestof，变体文件保留供用户比对。"""
    n = max(2, min(3, int(best_of)))
    results = {}

    def _one(kk):
        vfile = _variant_name(ms_name, kk)
        p = prompt_fn(vfile) + BESTOF_STRATEGIES[kk % len(BESTOF_STRATEGIES)]
        r = _run_step(run_id, "draft-v%d" % kk,
                      modelhub.bind_agent(impl, difficulty), p, step_wd,
                      readonly=False, ev=ev,
                      # 只有 0 号候选继承续会话（N 路共用同一 CLI 会话会互相践踏）
                      resume=(resume_ctx["session"] if (resume_ctx and kk == 0) else None),
                      images=_task_images(task, workdir),
                      note=(note + " · " if note else "") + "候选 %d/%d" % (kk + 1, n))
        txt = ""
        try:
            txt = _read_text_any_enc(os.path.join(workdir, vfile))
        except Exception:
            txt = ""
        results[kk] = (vfile, r, (txt or "").strip())

    threads = []
    for kk in range(n):
        th = threading.Thread(target=_one, args=(kk,),
                              name="bestof-%s-v%d" % (run_id, kk), daemon=True)
        threads.append(th)
        th.start()
    for th in threads:
        th.join(3000)
    _check_cancel(ev)

    candidates = []
    for kk in range(n):
        vfile, r, txt = results.get(kk, (None, None, ""))
        if r is not None and r.get("ok") and txt:
            candidates.append({"k": kk, "file": vfile, "res": r, "text": txt})
    if not candidates:
        r = (results.get(0) or results.get(n - 1) or (None, None, ""))[1]
        return (r or {"ok": False, "error": "全部候选起草失败"}), None

    # 终审选择器：单次调用对比全部候选（结构化输出 + 败者精华回收）
    cand_blocks = "\n\n".join(
        "### 候选 %d\n\n%s" % (c["k"], c["text"]) for c in candidates)
    sel_prompt = (BESTOF_SELECTOR_PROMPT
                  .replace("__GOAL__", task["goal"])
                  .replace("__CANDIDATES__", cand_blocks))
    pick, reason, improvements = candidates[0]["k"], "", ""
    sel_res = _run_step(run_id, "bestof-select",
                        modelhub.bind_agent(sel_agent, difficulty),
                        sel_prompt, workdir, readonly=True, ev=ev, note="候选择优")
    cj = runner.extract_json(sel_res.get("text") or "") if sel_res.get("ok") else None
    if isinstance(cj, dict):
        try:
            pk = int(cj.get("pick"))
        except Exception:
            pk = -1
        if any(c["k"] == pk for c in candidates):
            pick = pk
            reason = str(cj.get("reason") or "")[:300]
            improvements = str(cj.get("improvements") or "")[:600]
    winner = next(c for c in candidates if c["k"] == pick)
    winner["reason"] = reason
    winner["improvements"] = improvements
    write_ms(winner["text"])
    store.update_run(run_id, bestof={
        "pick": winner["k"], "file": winner["file"],
        "candidates": [c["k"] for c in candidates],
        "reason": reason, "improvements": improvements,
        "selector": (sel_agent.get("id") or "") if isinstance(sel_agent, dict) else "",
    })
    return winner["res"], winner


def _run_content_review(run, task, agents, ev, stats, mode):
    run_id = run["id"]
    workdir = task["workdir"]
    ms_name = _ms_name(task.get("manuscript"))
    dims = task.get("rubric") or DEFAULT_RUBRIC
    threshold = task.get("threshold", 7.0)
    rounds = task.get("rounds", 2)
    route = {}
    resume_ctx = _valid_resume(task, agents)
    # 续会话步骤的 CLI 启动目录（稿件读写仍用 workdir）
    step_wd = _resume_workdir(resume_ctx, workdir) if resume_ctx else workdir
    difficulty = task.get("difficulty") or (
        "hard" if threshold >= 8.5 else "easy" if threshold <= 6 else "default")

    # ---- 路由
    if resume_ctx is not None:
        impl = resume_ctx["agent"]
        route["author"] = resume_ctx["note"]
    elif mode == "manual":
        impl, _ = _pick_implementer(agents, task.get("implementer"))
        critics = _pick_critics_manual(agents, task)
    else:
        impl, route["author"] = router.pick(agents, "implement", "novel", stats)
        critics, route["critics"] = router.pick_critics(agents, "novel", stats, impl=impl)
    if impl is None:
        store.update_run(run_id, status="failed", error="没有可用智能体", ended_at=_now())
        return
    if resume_ctx is not None and mode == "auto":
        critics, route["critics"] = router.pick_critics(agents, "novel", stats, impl=impl)

    # ---- 规划（小说为模板计划）
    _wait_gate(run_id, ev)
    plan = planner.make_novel_plan(_steered_task(run_id, task), impl, critics)
    store.update_run(run_id, plan=plan, route=route, difficulty=difficulty)

    ms_path = os.path.join(workdir, ms_name)

    def write_ms(text):
        with _ms_io(workdir, ms_name, "w") as f:
            f.write(text)

    def read_ms():
        try:
            return _read_text_any_enc(ms_path)
        except Exception:
            return ""

    draft_note = route.get("author", "") if mode == "auto" else ""

    # 编排者大纲：只对真实执行有意义；失败静默退回无大纲（喂入带指令的任务副本）
    outline = (planner.make_review_outline(_steered_task(run_id, task))
               if impl.get("mode") != "mock" else None)
    if outline:
        store.update_run(run_id, outline=outline)

    # 1) 起草
    bestof_improvements = ""   # 赛马败者精华（真实路径由选择器填充；mock 路径恒空）
    if impl.get("mode") == "mock":
        step, log_abs = store.add_step(run_id, "draft", impl["id"], impl.get("label"), note=draft_note)
        write_ms(mocks.draft_manuscript(task, 1))
        time.sleep(0.2)
        store.finish_step(run_id, step["n"], "done", summary="（mock）草稿已写入 %s" % ms_name,
                          duration_s=0.2)
        try:
            log_abs.write_text("[mock] 已写入草稿\n", encoding="utf-8")
        except Exception:
            pass
    else:
        def _draft_prompt_for(vfile):
            p = (_tpl(task, "draft_prompt", NOVEL_DRAFT_PROMPT).replace("__FILE__", vfile)
                 .replace("__GOAL__", task["goal"])
                 .replace("__CONTEXT__", task.get("context") or "（无）"))
            if outline:
                p += "\n\n## 编排者大纲（按要点组织稿件）\n" + \
                     "\n".join("- " + i for i in outline["items"])
            return p

        best_of = max(1, min(3, int(task.get("best_of") or 1)))
        if best_of >= 2 and not _compaction_enabled():
            try:
                sel_agent = critics[0]
            except Exception:
                sel_agent = impl
            draft_res, _bw = _bestof_draft(
                run_id, task, impl, _draft_prompt_for, ms_name, workdir, step_wd,
                ev, resume_ctx, difficulty, best_of, sel_agent, write_ms,
                note=draft_note)
            bestof_improvements = (_bw or {}).get("improvements") or ""
        else:
            draft_res = _run_step(run_id, "draft", modelhub.bind_agent(impl, difficulty),
                                  _draft_prompt_for(ms_name), step_wd, readonly=False,
                                  ev=ev, note=draft_note,
                                  resume=resume_ctx["session"] if resume_ctx else None,
                                  images=_task_images(task, workdir))
        if not draft_res["ok"]:
            store.update_run(run_id, status="failed", error="起草失败: %s" % draft_res.get("error"),
                             ended_at=_now())
            return

    # 2) 评审-修订循环
    history_rounds = []
    publishable = False
    for r in range(1, rounds + 1):
        manuscript = read_ms()
        per_agent, issues_all = {}, []
        dimkey = ", ".join('"%s": 0' % d for d in dims)
        crit_prompt = (_ensure_critique_placeholders(
            _tpl(task, "critique_prompt", NOVEL_CRITIQUE_PROMPT))
            .replace("__DIMKEYS__", dimkey)
            .replace("__MANUSCRIPT__", manuscript or "（稿件为空！）"))
        # AI 味确定性检测（借鉴 oh-story 去AI味）：客观参考线随评审下发，
        # 命中才追加——评审官结合上下文判断是否真问题，脚本不直接扣分
        _aiflavor_line = aiflavor.report_line(manuscript)
        if _aiflavor_line:
            crit_prompt += "\n\n## 确定性检测结果（供评审参考）\n" + _aiflavor_line
        for agent in critics:
            role = "critique-r%d" % r
            if agent.get("mode") == "mock":
                step, log_abs = store.add_step(run_id, role, agent["id"], agent.get("label"))
                time.sleep(0.15)
                cj = mocks.critique(agent["id"], r, dims, threshold)
                store.finish_step(run_id, step["n"], "done",
                                  summary="均分 %.1f：%s" % (
                                      sum(cj["scores"].values()) / max(1, len(dims)), cj["summary"]),
                                  duration_s=0.15)
                try:
                    log_abs.write_text("[mock] %s\n" % str(cj), encoding="utf-8")
                except Exception:
                    pass
            else:
                res = _run_step(run_id, role, modelhub.bind_agent(agent, difficulty),
                                crit_prompt, workdir, readonly=True, ev=ev)
                cj = runner.extract_json(res.get("text") or "")
                if not isinstance(cj, dict) or not isinstance(cj.get("scores"), dict):
                    cj = {"scores": {}, "issues": [],
                          "summary": "评审输出无法解析：%s" % (res.get("text") or "")[:150]}
            per_agent[agent["id"]] = cj
            issues_all.extend(cj.get("issues") or [])
            _check_cancel(ev)

        means = {}
        for d in dims:
            vals = [float(cj["scores"].get(d, 0)) for cj in per_agent.values()
                    if isinstance(cj.get("scores"), dict) and d in cj["scores"]]
            means[d] = round(sum(vals) / len(vals), 1) if vals else 0.0
        publishable = bool(means) and all(v >= threshold for v in means.values())
        history_rounds.append({"round": r, "means": means,
                               "per_agent": {k: v.get("scores", {}) for k, v in per_agent.items()},
                               "issues": issues_all, "passed": publishable})
        if publishable or r == rounds:
            break

        # 修订
        crit_lines = ["- %s：%.1f（阈值 %.1f）" % (d, means[d], threshold) for d in dims]
        majors = [i for i in issues_all if i.get("severity") == "major"][:8]
        for i in majors:
            crit_lines.append("- [%s] %s" % (i.get("dim", "?"), str(i.get("note", ""))[:120]))
        if r == 1 and bestof_improvements:
            # 败者精华回收（freebuff suggestedImprovements 的落地）：终审从落选
            # 候选稿提炼的优点，首轮修订时与评审意见一并喂给作者
            crit_lines.append("- [赛马精华] 终审择优时从落选候选稿提炼出值得吸收的优点：%s"
                              % bestof_improvements[:400])
        if impl.get("mode") == "mock":
            step, log_abs = store.add_step(run_id, "revise-r%d" % r, impl["id"], impl.get("label"))
            write_ms(mocks.draft_manuscript(task, r + 1))
            time.sleep(0.2)
            store.finish_step(run_id, step["n"], "done", summary="（mock）已按评审意见修订",
                              duration_s=0.2)
            try:
                log_abs.write_text("[mock] 已修订\n", encoding="utf-8")
            except Exception:
                pass
        else:
            prompt = (NOVEL_REVISE_PROMPT.replace("__FILE__", ms_name)
                      .replace("__GOAL__", task["goal"])
                      .replace("__CRITIQUE__", "\n".join(crit_lines)))
            _run_step(run_id, "revise-r%d" % r, modelhub.bind_agent(impl, difficulty), prompt,
                      workdir, readonly=False, ev=ev,
                      resume=resume_ctx["session"] if resume_ctx else None)
        _check_cancel(ev)

    final_means = history_rounds[-1]["means"] if history_rounds else {}
    overall = round(sum(final_means.values()) / len(final_means), 1) if final_means else 0.0
    verdict = {
        "type": task["type"], "engine": "review", "publishable": publishable,
        "overall": overall, "mode": mode,
        "threshold": threshold, "rounds_used": history_rounds[-1]["round"] if history_rounds else 0,
        "scores": final_means, "history": history_rounds, "route": route,
    }

    # 3) 报告
    lines = ["# 评审报告：%s" % task["title"], "",
             "- 任务类型：%s" % task["type"],
             "- 结论：**%s**（综合 %.1f / 阈值 %.1f，%d 轮评审）"
             % ("✅ 达到发布标准" if publishable else "❌ 未达标，建议再修",
                overall, threshold, verdict["rounds_used"]),
             "- 编排模式：%s　起草/修订：%s　评审组：%s" % (
                 "智能" if mode == "auto" else "手动",
                 impl.get("label"), "、".join(a.get("label") for a in critics))]
    if route:
        lines.append("")
        lines.append("## 路由依据")
        lines.extend("- %s：%s" % (k, v) for k, v in route.items() if v)
    lines += ["", "## 各轮维度均分", "",
              "| 轮次 | " + " | ".join(dims) + " | 达标 |",
              "|" + "---|" * (len(dims) + 2)]
    for h in history_rounds:
        lines.append("| %d | " % h["round"]
                     + " | ".join("%.1f" % h["means"].get(d, 0.0) for d in dims)
                     + " | %s |" % ("✓" if h["passed"] else "✗"))
    lines += ["", "## 末轮主要问题", ""]
    last_issues = (history_rounds[-1].get("issues") if history_rounds else []) or []
    majors = [i for i in last_issues if i.get("severity") == "major"]
    minors = [i for i in last_issues if i.get("severity") != "major"]
    if majors:
        lines.append("**major**")
        lines.extend("- [%s] %s" % (i.get("dim", "?"), str(i.get("note", ""))[:150]) for i in majors)
    if minors:
        lines.append("**minor**")
        lines.extend("- [%s] %s" % (i.get("dim", "?"), str(i.get("note", ""))[:150]) for i in minors)
    if not majors and not minors:
        lines.append("（无）")
    lines += ["", "## 稿件位置", "", "`%s`" % ms_path, ""]
    store.write_report(run_id, "\n".join(lines))
    store.update_run(run_id, status="done", verdict=verdict,
                     summary="评审任务%s（综合 %.1f）" % ("达标" if publishable else "未达标", overall),
                     ended_at=_now())


# ---------------------------------------------------------------- 入口

SERIAL_QA_PROMPT = """你是网文《__TITLE__》的责任编辑（不要修改任何文件）。读者（作者本人）就这本书提了一个问题，请直接回答：

__QUESTION__

回答要求：
- 只回答问题，不要重写章节、不要改动任何文件。
- 先给结论，再给依据；涉及章节时点明具体文件（如 chapter-09.md）。
- 问题若暴露了稿件自身毛病（缺章、编号错位、前后矛盾），说清楚坏在哪个文件、该怎么修。

各章成稿都在当前工作目录（chapter-XX.md），合并稿 manuscript.md 可能只含部分章节，可按需查阅。"""


def _run_serial_qa(run, task, agents, ev):
    """连载答疑轮：追话提问不再整本重跑——单步只读问答，答案落步骤 output，
    对话页时间线（消息气泡 + 步骤正文）直接可读（2026-09-18「为啥没有第九章」
    案：一句追问被当成完整编排指令，重评 8 章还烧出连环自动续跑）。"""
    run_id = run["id"]
    workdir = run.get("workdir") or task.get("workdir") or ""
    question = (run.get("qa_text") or "").strip() or "（见下方读者追问）"
    prompt = (SERIAL_QA_PROMPT
              .replace("__TITLE__", (task.get("title") or "本书").lstrip("# ").strip())
              .replace("__QUESTION__", question))
    # 回答者顺序：评审组（专职读稿）→ 实现者 → 其余已启用真实智能体；死链自动落到下一个
    critic_ids = task.get("critics") or []
    impl_id = task.get("implementer") or ""
    ordered = [a for a in agents if a.get("id") in critic_ids]
    ordered += [a for a in agents
                if a.get("id") == impl_id and a.get("id") not in critic_ids]
    ordered += [a for a in agents
                if a.get("mode") == "real"
                and a.get("id") not in critic_ids and a.get("id") != impl_id]
    ordered = [a for a in ordered if a.get("mode") != "mock"] or ordered[:1]
    errors = []
    for agent in ordered[:3]:
        _check_cancel(ev)
        res = _run_step(run_id, "qa", modelhub.bind_agent(agent, "default"),
                        prompt, workdir, readonly=True, ev=ev, timeout=1200)
        if res.get("ok") and (res.get("text") or "").strip():
            store.update_run(run_id, status="done", ended_at=_now(),
                             verdict={"qa": True,
                                      "answered_by": agent.get("id")})
            return
        errors.append("%s：%s" % (agent.get("id"),
                                  (res.get("error") or "无输出")[:120]))
    store.update_run(run_id, status="failed", ended_at=_now(),
                     error="答疑失败（执行/评审链不可用）——" + "；".join(errors[-3:]))


def _write_task_spec(task, workdir):
    """任务规格落盘 .codebee/spec.md（借鉴 agent-orchestrator 的 .spec/PROMPT.md 与
    planning-with-files 的文件化计划）：任务定义随工作目录留存、随任务分支版本化，
    追话/复盘/续跑时可见原始意图。失败静默返回空串——规格文件永远不能挡住任务执行。"""
    try:
        spec_dir = os.path.join(workdir, ".codebee")
        os.makedirs(spec_dir, exist_ok=True)
        lines = [
            "# 任务规格", "",
            "- 标题：%s" % (task.get("title") or ""),
            "- 类型：%s" % (task.get("type") or ""),
            "- 创建：%s" % (task.get("created_at") or ""),
            "- 目标：%s" % str(task.get("goal") or "").replace("\n", " "),
        ]
        if task.get("context"):
            lines.append("- 背景：%s" % str(task["context"]).replace("\n", " "))
        if task.get("difficulty"):
            lines.append("- 难度：%s" % task["difficulty"])
        if task.get("mode"):
            lines.append("- 路由模式：%s" % task["mode"])
        label = {"rounds": "评审轮数", "threshold": "发布阈值", "best_of": "赛马候选数"}
        for k in ("rounds", "threshold", "best_of"):
            if task.get(k) is not None:
                lines.append("- %s：%s" % (label[k], task[k]))
        if task.get("rubric"):
            lines.append("- 评审维度：%s" % "、".join(task["rubric"]))
        if task.get("serial"):
            s = task["serial"]
            lines.append("- 连载：%s 章 × %s 字（赛马变体 %s）"
                         % (s.get("chapters"), s.get("words_per_chapter"), s.get("variants", 1)))
        if task.get("verify_command"):
            lines.append("- 验证命令：`%s`" % task["verify_command"])
        path = os.path.join(spec_dir, "spec.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception:
        return ""


def execute_run(run_id):
    run = store.get_run(run_id)
    if not run:
        return
    ev = jobs.cancel_event_for(run_id)
    if run.get("cancelled_by_user"):
        # 排队期间被取消：事件可能已置位，也可能只在 run 上留了标记——
        # 双保险兜底，任务标记已落，失败收尾照常走（不会被自动续跑复活）。
        ev.set()
        store.update_run(run_id, status="cancelled", ended_at=_now(),
                         error="排队期间被取消")
        return
    task = store.get_task(run.get("task_id"))
    store.update_run(run_id, status="running", started_at=_now())
    if task is None:
        store.update_run(run_id, status="failed", error="找不到任务 %s" % run.get("task_id"),
                         ended_at=_now())
        return
    # 代码版本检出：任务指定了基线版本时，先检出任务分支 tutti/<task-id> 再跑流水线。
    # 显式意图不容静默降级——仓库缺失/脏工作区/引用不存在一律中止运行并报错，
    # 绝不带着用户未提交改动切分支、也不悄悄退回当前 HEAD。
    git_ctx = None
    if task.get("git_rev"):
        from . import gitmod
        ok, err, gitinfo = gitmod.prepare_checkout(
            task["workdir"], task["git_rev"], task["id"])
        if not ok:
            store.update_run(run_id, status="failed", error="代码版本检出失败：%s" % err,
                             ended_at=_now())
            return
        git_ctx = gitinfo
        store.update_run(run_id, git=gitinfo)
        # 任务分支裁决状态：新一轮 run 产生新分支内容，重置回「待裁决」
        store.set_task_git_state(task["id"], "isolated")
    # 任务规格文件化（借鉴 planning-with-files/agent-orchestrator）：任何任务都在
    # 工作目录留一份 .codebee/spec.md——原始意图可见、随任务分支版本化
    _write_task_spec(task, task["workdir"])
    agents = _agents()
    global _CURRENT_AGENTS
    _CURRENT_AGENTS = agents
    # 续会话是对该 CLI 的显式指定：目标未启用编排时也注入本次运行（不影响路由池）
    want = ((task.get("resume") or {}).get("agent") or "").strip()
    if want and _pick(agents, want) is None:
        extra = registry.installed_agent(want, catalog.load(), manager.detect_all())
        if extra:
            agents.append(extra)
    stats = history.agent_stats()
    mode = task.get("mode") or ("manual" if task.get("implementer") else "auto")
    store.update_run(run_id, mode=mode)
    # engine 决定流水线：code=实现/验证/评审/修复；review=起草/多维评审/修订/门禁；
    # direct=单 CLI 直达（无拆解/评审，信箱续轮即对话）
    engine = task.get("engine") or ("code" if task["type"] == "code" else "review")
    try:
        # 连载答疑轮：op=qa 不走编排流水线，单步只读回答后即收尾；
        # 放进 try——Cancelled 与主流程同口径收口为 cancelled
        if run.get("op") == "qa":
            _run_serial_qa(run, task, agents, ev)
            return
        if engine == "code":
            _run_code(run, task, agents, ev, stats, mode)
        elif engine == "direct":
            _run_direct(run, task, agents, ev, stats, mode)
        else:
            route = {}
            resume_ctx = _valid_resume(task, agents)
            difficulty = task.get("difficulty") or (
                "hard" if (task.get("threshold") or 7.0) >= 8.5 else
                "easy" if (task.get("threshold") or 7.0) <= 6 else "default")
            # 路由（与单稿件评审一致的规则）
            if resume_ctx is not None:
                impl = resume_ctx["agent"]
                route["author"] = resume_ctx["note"]
            elif mode == "manual":
                impl, _ = _pick_implementer(agents, task.get("implementer"))
                critics = _pick_critics_manual(agents, task)
            else:
                impl, route["author"] = router.pick(agents, "implement", task["type"], stats)
                critics, route["critics"] = router.pick_critics(agents, task["type"], stats, impl=impl)
            if impl is None:
                store.update_run(run_id, status="failed", error="没有可用智能体", ended_at=_now())
                return
            if resume_ctx is not None and mode == "auto":
                critics, route["critics"] = router.pick_critics(agents, task["type"], stats, impl=impl)
            if task.get("serial"):
                _run_serial_review(run, task, agents, ev, stats, mode,
                                   critics, impl, route, resume_ctx, difficulty)
            else:
                _run_content_review(run, task, agents, ev, stats, mode)
    except Cancelled:
        store.update_run(run_id, status="cancelled", ended_at=_now())
    except Exception as e:
        import traceback
        store.update_run(run_id, status="failed", error=repr(e)[:500],
                         ended_at=_now())
        try:
            err_path = store.run_dir(run_id) / "error.log"
            if _inside(str(store.run_dir(run_id).parent), str(err_path)):
                err_path.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
    finally:
        # 任务分支收尾（git_rev 隔离链的第二半）：先只读快照本 run 的全部变更
        # 落 run 记录供人审，再把产物提交到 tutti/<task-id> 并切回原分支。
        # 放 finally：done/failed/cancelled/异常一律保存现场；收尾自身绝不抛错，
        # 问题记入 run.git.restore_error，不覆盖 run 的最终结论。
        if git_ctx is not None:
            from . import gitmod
            try:
                store.update_run(run_id, changes=gitmod.collect_changes(task["workdir"]))
                fin = gitmod.finalize_run(
                    task["workdir"], git_ctx,
                    "tutti %s: %s（%s）" % (run_id, task.get("title") or task["goal"][:40],
                                            (store.get_run(run_id) or {}).get("status") or "?"))
                store.update_run(run_id, git={**git_ctx, **fin})
            except Exception as e:
                try:
                    store.update_run(run_id, git={**git_ctx,
                                                  "restore_error": repr(e)[:200]})
                except Exception:
                    pass
        # 自学习闭环：运行结束自动把本次评审暴露的问题沉淀为可复用教训（异步，不阻塞）
        try:
            if store.get_run(run_id):
                skills.learn_async(run_id)
        except Exception:
            pass
