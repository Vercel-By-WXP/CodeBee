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

from . import aiflavor, attachments, branching, catalog, chaptersafe, dispatch_log, history, hooks, jobs, knowledge, manager, modelhub, mocks, paihang, planner, registry, router, runner, skills, store, task_compile, usage, volumes
from . import builtin_agent
from . import diagnostics
from . import paths as paths_mod
from . import session_log as session_log_mod
from . import step_runner as step_runner_mod
from .repeat_guard import guard as repeat_guard

DEFAULT_RUBRIC = ["情节", "人物", "文笔", "节奏", "吸引力"]


class Cancelled(Exception):
    pass


class TaskTimeout(Exception):
    pass


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _check_cancel(ev):
    if ev is not None and ev.is_set():
        raise Cancelled()


def _run_deadline(run_id):
    """Convert the persisted wall-clock cutoff into a monotonic deadline."""
    run = store.get_run(run_id) or {}
    try:
        deadline_at = float(run.get("deadline_at"))
    except (TypeError, ValueError):
        return None
    return time.monotonic() + (deadline_at - time.time())


def _ensure_budget(run_id):
    deadline = _run_deadline(run_id)
    if deadline is not None and time.monotonic() >= deadline:
        store.update_run(run_id, expected_status="running", status="timeout",
                         ended_at=_now(), error="任务总时限已到")
        raise TaskTimeout()
    return deadline


def _step_timeout(run_id, requested, deadline=None):
    value = max(0.0, float(requested or runner.DEFAULT_TIMEOUT))
    deadline = deadline if deadline is not None else _run_deadline(run_id)
    if deadline is not None:
        value = min(value, max(0.0, deadline - time.monotonic()))
    return value, deadline


def _planner_call(fn, *args, deadline=None, **kwargs):
    """Allow older injected planner doubles to omit the new deadline kwarg."""
    if deadline is None:
        return fn(*args, **kwargs)
    try:
        return fn(*args, deadline=deadline, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument 'deadline'" not in str(exc):
            raise
        return fn(*args, **kwargs)


def _inside(dirpath, target):
    try:
        return os.path.commonpath(
            [os.path.abspath(dirpath), os.path.abspath(target)]) == os.path.abspath(dirpath)
    except ValueError:
        return False


def _task_images(task, workdir):
    """任务的图片附件绝对路径（仅 codex 原生 -i 用）。无附件/异常返回空列表。"""
    return attachments.image_paths(task, workdir)


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
# 本次 run 的任务宪章注入块（execute_run 起跑时从 .codebee/constitution.md 读入；
# 步骤函数在各提示词组装点引用，空串=未配置零噪音）
_RUN_CONSTITUTION = ""


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
    """Phase 2 灰度开关：环境变量 TUTTI_COMPACTION=1 或设置页
    settings_v2 orchestrator.compaction.enabled 任一开启即生效（默认关）。"""
    if os.environ.get("TUTTI_COMPACTION") == "1":
        return True
    try:
        from .settings_schema import get as ss_get, register_default_namespaces
        register_default_namespaces()
        return bool(ss_get("orchestrator", "compaction.enabled"))
    except Exception:
        return False


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


def _make_llm_caller(agent, workdir, deadline=None):
    """压缩摘要用 LLM：直接复用当前 step 的 agent（同 CLI 同模型）。"""
    def caller(messages):
        prompt = "\n\n".join(m.get("content", "") for m in messages)
        res = runner.run_agent(agent, prompt, workdir=workdir, readonly=True,
                               timeout=300, deadline=deadline)
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
    msg_lines, imgs = attachments.directive_lines(msgs, workdir)
    lines.extend(msg_lines)
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
        # ev.wait 睡等：取消置位即刻醒来，不必耗满 1s 轮询间隔
        (ev.wait(1.0) if ev is not None else time.sleep(1.0))


def _binding_dead_msg(agent):
    """死链失败文案：委托 modelhub 单一真源（告警 sync 共用同一文案）。"""
    try:
        from . import modelhub
        return modelhub.binding_dead_msg(agent.get("id") or "")
    except Exception:
        return ("绑定链全部失效，本步判失败、不回落 CLI 本机默认——"
                "请在「模型调度（可选）」页为该 CLI 指定已启用的供应商")


def _run_step(run_id, role, agent, prompt, workdir, readonly, ev, timeout=runner.DEFAULT_TIMEOUT, note="", resume=None, images=None, require_tools=False):
    """执行一个智能体步骤并记录。返回 runner 统一结果。"""
    _wait_gate(run_id, ev)
    deadline = _ensure_budget(run_id)
    timeout, deadline = _step_timeout(run_id, timeout, deadline)
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
                                   agent.get("label", agent["id"]), note=note,
                                   model=agent.get("model") or "")
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
                   "raw": {"exit_code": None, "repeat_stop": True},
                   "kind": agent.get("kind", "generic"),
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
                          log_abs=log_abs, images=images, require_tools=require_tools,
                          deadline=deadline)
    # 先收尾再查取消：取消时进程已被 run_process 杀停，若先抛 Cancelled，
    # 步骤记录会永远停在「运行中」变僵尸（与 _run_verify 的顺序对齐）
    _finish_step_result(run_id, step, res, role, agent, start)
    if (res.get("raw") or {}).get("deadline_exceeded"):
        store.update_run(run_id, expected_status="running", status="timeout",
                         ended_at=_now(), error="任务总时限已到")
        raise TaskTimeout()
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
    deadline = _ensure_budget(run_id)
    step, log_abs = store.add_step(run_id, role, "builtin", "CodeBee", note=note,
                                   model=bi.get("model") or "")
    start = time.time()
    agent_pseudo = {"id": "builtin", "label": "CodeBee", "kind": "builtin", "mode": "real",
                    "provider": {"id": bi.get("provider_id") or "",
                                 "name": bi.get("provider_name") or ""}}
    guard = repeat_guard.check(run_id, role, prompt)
    if guard["should_stop"]:
        from .error_codes import ErrorCode
        res = {"ok": False, "text": "", "usage": None, "cost_usd": 0.0, "tokens": 0,
               "error": guard["reminder"], "error_code": ErrorCode.ENV_BLOCK,
               "raw": {"exit_code": None, "repeat_stop": True},
               "model": bi.get("model")}
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

    # 思考过程可视化（2026-09-22 用户诉求）：模型的思维链/正文/工具活动边收边
    # 落进步骤记录（store.stream_step 内部节流），对话时间线 2s 轮询即能读到
    # 「它正在想什么」——此前整轮只有三点打字动画。
    live = {"thinking": "", "text": ""}

    def _on_reason(chunk):
        live["thinking"] += chunk or ""
        store.stream_step(run_id, step["n"], thinking=live["thinking"])

    def _on_stream(acc):
        live["text"] = acc or ""
        store.stream_step(run_id, step["n"], text=live["text"])

    def _on_activity(line):
        store.stream_step(run_id, step["n"], activity=str(line or "")[:200])

    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
    res = builtin_agent.run(bi, prompt, workdir, timeout=remaining or 180,
                            deadline=deadline, cancel_event=ev, log=_log, images=images,
                            on_reason=_on_reason, on_stream=_on_stream,
                            on_activity=_on_activity)
    if followups and res.get("ok"):
        clean, fups = _parse_followups(res.get("text") or "")
        if fups:
            res["text"] = clean
            res["followups"] = fups
    if log_abs:
        try:
            # 思考过程同时进步骤日志（日志抽屉与对话气泡同源，便于事后复盘）
            think = (res.get("reasoning") or "").strip()
            if think:
                lines.append("[思考过程]\n" + think)
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
    if (res.get("raw") or {}).get("deadline_exceeded"):
        store.update_run(run_id, expected_status="running", status="timeout",
                         ended_at=_now(), error="任务总时限已到")
        raise TaskTimeout()
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
                timeout, resume, step, log_abs, images=None, require_tools=False,
                deadline=None):
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
    usage_recorded = False
    if _compaction_enabled() and not resume:
        # Phase 2（1D）：撑爆 → 压缩 → 守门重试；同时把 usage 累进 token_meter（1C）
        session = _get_session(session_run_id)
        llm_caller = _make_llm_caller(agent, workdir, deadline=deadline)
        call_kwargs = dict(workdir=workdir, readonly=readonly,
                           timeout=timeout, cancel_event=ev, log_path=str(log_abs),
                           images=images, require_tools=require_tools, deadline=deadline)

        def _call(p, **kw):
            nonlocal usage_recorded
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
                usage_recorded = True
            except Exception:
                pass
            return r

        res, _retried = step_runner_mod.execute_step(
            session, _call, prompt, model=agent.get("model") or "",
            llm_caller=llm_caller)
    else:
        res = runner.run_agent(agent, prompt, workdir=workdir, readonly=readonly,
                               timeout=timeout, cancel_event=ev, log_path=str(log_abs),
                               resume=resume, images=images, require_tools=require_tools,
                               deadline=deadline)
    # 默认关闭压缩和 resume 都走直通分支，也必须把真实 usage 送进预算表；否则
    # 下一步永远看到 used=0，max_tokens_per_run 只是一个无效设置。
    if not usage_recorded:
        try:
            from .token_meter import token_meter
            token_meter.accumulate(session_run_id, res.get("usage"),
                                   model=res.get("model") or "")
        except Exception:
            pass
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
                      followups=res.get("followups"),
                      # 思考过程（内置智能体流式抓取）：落进步骤记录，对话气泡折叠展示
                      thinking=res.get("reasoning") or None)
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
        provider = res.get("provider") or agent.get("provider") or {}
        provider_id = provider.get("id") if isinstance(provider, dict) else ""
        provider_name = provider.get("name") if isinstance(provider, dict) else ""
        usage.record(
            source=source, run_id=run_id, step=step,
            task_id=run.get("task_id") or "",
            task_type=(task or {}).get("type", ""),
            role=role, agent=agent.get("id", ""),
            agent_label=agent.get("label", ""),
            tool=agent.get("kind", ""),
            model=res.get("model") or "",
            provider=res.get("provider_id") or provider_id or provider_name or "",
            ok=bool(res.get("ok")),
            duration_s=float(res.get("raw", {}).get("duration") or 0.0),
            cost_usd=float(res.get("cost_usd") or 0.0),
            usage=res.get("usage"))
        # 告警模块：CLI 调用成功/失败上报（provider 名与 usage 台账一致）
        from . import health
        prov = res.get("provider") or agent.get("provider") or {}
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
__FILES__

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

## 评审要求（findings 锚定证据）
每个 issue 的 detail 必须给出「文件名:行号」（从 diff 的 hunk 头 @@ -a,b +c,d @@ 与上下文推算），
并引用该处一行关键代码作为依据——没有证据定位的问题不要报，宁可少报不报猜测。
（借鉴 pr-af：findings grounded in code evidence 是评审可信度的根。）

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


def _files_hint(sub):
    """计划锚定文件清单（OpenSpec explore 借鉴）：计划里带了 files 就明示改动面，
    让实现者知道该动哪些文件、不必全盘摸索。无 files 时返回空串（旧计划兼容）。"""
    files = (sub or {}).get("files")
    if not isinstance(files, list) or not files:
        return ""
    return "\n- 本步涉及文件（计划已锚定，先读再改）：" + "、".join(
        "`%s`" % str(f) for f in files[:10])


def _run_verify(run_id, task, workdir, ev):
    """确定性验证。返回 (verify_pass, ran)。"""
    deadline = _ensure_budget(run_id)
    if not task.get("verify_command"):
        return True, False
    step, log_abs = store.add_step(run_id, "verify", "builtin", "内置验证器")
    start = time.time()
    timeout, deadline = _step_timeout(run_id, 600, deadline)
    r = runner.run_process(shell_cmd=task["verify_command"], cwd=workdir,
                           timeout=timeout, deadline=deadline, cancel_event=ev,
                           log_path=str(log_abs))
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
    if r.get("deadline_exceeded"):
        store.update_run(run_id, expected_status="running", status="timeout",
                         ended_at=_now(), error="任务总时限已到")
        raise TaskTimeout()
    _check_cancel(ev)
    return ok, True


# 评审器鉴权/致命故障签名：命中即「评审未执行」，不得伪装成质量未通过
_AUTH_RE = re.compile(
    r"Invalid API Key|invalid api key|API [Kk]ey 不可用|API key not valid|"
    r"unauthorized|Unauthorized|401 Unauthorized|鉴权失败|认证失败", re.I)


def _run_review(run_id, task, workdir, reviewer, ev):
    diff = _git_diff(workdir)
    prompt = (CODE_REVIEW_PROMPT
              .replace("__GOAL__", task["goal"])
              .replace("__VERIFY__", task.get("verify_command") or "（未配置）")
              .replace("__DIFF__", diff or "（无法获取 git diff，请综合任务目标谨慎评审）"))
    if task.get("context"):
        prompt += "\n\n## 原始背景与附件要求\n" + task["context"]
    res = _run_step(run_id, "review", reviewer, prompt, workdir, readonly=True, ev=ev,
                    images=_task_images(task, workdir))
    if reviewer.get("mode") == "mock":
        return mocks.review(task, True)
    # 评审器自身故障 ≠ 评审未通过：2026-09-23 禅道双单实案——评审器 Key 失效
    # （mimo 退出码 0 但输出 Invalid API Key），被解析成「评审未通过」整晚空转
    # 修复轮。鉴权/崩溃签名命中时返回 reviewer_error，调用方以明确错误收口。
    _head = (res.get("text") or "")[:2000]
    _auth_hit = bool(_AUTH_RE.search(_head)) or bool((res.get("raw") or {}).get("auth_error"))
    if not res.get("ok") or _auth_hit:
        _why = (res.get("error") or "").strip() or _head[:200]
        return {"pass": False, "scores": {}, "issues": [], "reviewer_error": True,
                "summary": "评审器执行失败（评审未执行）：%s" % _why[:280]}
    parsed = runner.extract_json(res.get("text") or "")
    if isinstance(parsed, dict):
        return parsed
    # 评审 JSON 解析失败 → 第 5 道网（借鉴连载评审的 extract_scores_from_text）：
    # 模型偶尔在 JSON 前后加说明文字或格式不合法，但分数仍散落在文本中。
    # 此前直接判「无法解析」→ pass=False 空分 → 白白触发修复轮（修复者
    # 只拿到一条报错文本，无从修起）。先尝试从原文提取分数；至少拿到分
    # 数就能正确判定过/不过，避免把「格式错」当「质量差」误触发修复。
    scores = runner.extract_scores_from_text(res.get("text") or "")
    if scores:
        # 有分无 issue：不触发修复循环（没有可修的具体问题），只记录
        passed = all(float(v) >= 7.0 for v in scores.values())
        return {"pass": passed, "scores": scores, "issues": [],
                "summary": "评审 JSON 解析失败，分数从原文提取（issues 不可用）"}
    return {"pass": False, "scores": {}, "issues": [],
            "summary": "评审输出无法解析为 JSON：%s" % (res.get("text") or "")[:200]}


def _format_issues(review_json, verify_pass, verify_failed_note):
    lines = []
    if not verify_pass and verify_failed_note:
        lines.append("- 验证命令未通过（%s）" % verify_failed_note)
    for it in (review_json.get("issues") or [])[:10]:
        lines.append("- [%s] %s：%s" % (it.get("severity", "?"),
                                        it.get("title", ""), str(it.get("detail", ""))[:300]))
    return "\n".join(lines) or "（评审未给出具体问题，请自查实现质量与验收命令）"


# ---------------------------------------------------------------- 代码流水线

def _shortstat_files(diffstat_line):
    """` 3 files changed, 10 insertions(+)` → 文件数（解析失败给大数排最后）。"""
    import re as _re
    m = _re.search(r"(\d+) files? changed", diffstat_line or "")
    return int(m.group(1)) if m else 9999


def _code_bestof(run, task, impl, difficulty, ev):
    """代码任务 Best-of-N（借鉴 orca 并行 worktree 择优）。启用条件：best_of≥2、
    无续会话、非手动指定实现者、工作目录是 git 仓库。

    按基线（git_rev 或 HEAD）建 N 个临时 worktree → 各路并行跑实现子任务链 →
    各跑验证命令 → 择优（验证通过 > 改动文件更少）→ 胜者 diff 应用回主工作区。
    任一环节失败一律返回 False，由调用方回落原单路实现（绝不因赛马挡任务）。"""
    from . import gitmod
    run_id = run["id"]
    n = max(2, min(3, int(task.get("best_of") or 1)))
    wd = task["workdir"]
    if not gitmod._git(wd, "rev-parse", "--is-inside-work-tree")["ok"]:
        return False
    base = task.get("git_rev") or ""
    if base and not gitmod.valid_rev(base):
        return False
    base_rev = (gitmod._git(wd, "rev-parse", base)["stdout"].strip() if base
                else gitmod._git(wd, "rev-parse", "HEAD")["stdout"].strip())
    if not base_rev:
        return False
    wts = []
    results = {}

    def _cleanup():
        for k, br, wt_path in wts:
            gitmod._git(wd, "worktree", "remove", "--force", wt_path, timeout=60)
            gitmod._git(wd, "branch", "-D", br, timeout=60)

    try:
        import tempfile
        race_root = os.path.join(tempfile.gettempdir(), "codebee-race", run_id)
        os.makedirs(race_root, exist_ok=True)
        for k in range(n):
            br = "codebee-race-%s-%d" % (run_id, k)
            wt_path = os.path.join(race_root, "v%d" % k)
            r = gitmod._git(wd, "worktree", "add", "--detach", wt_path, base_rev, timeout=120)
            if not r["ok"]:
                raise RuntimeError(r["stderr"] or "worktree add 失败")
            wts.append((k, br, wt_path))

        subtasks = [{"title": task.get("title") or task.get("goal") or "实现",
                     "detail": task.get("goal") or ""}]

        def _race_one(k, wt_path):
            agt_b = modelhub.bind_agent(impl, difficulty)
            res = None
            for i, sub in enumerate(subtasks):
                prompt = (_RUN_CONSTITUTION + CODE_IMPL_PROMPT
                          .replace("__GOAL__", task["goal"])
                          .replace("__SUBTASK__", sub["detail"] if sub["detail"] else sub["title"])
                          .replace("__FILES__", _files_hint(sub))
                          .replace("__CONTEXT__", task.get("context") or "（无）")
                          .replace("__VERIFY_HINT__", _verify_hint(task)))
                res = _run_step(run_id, "race%d-impl" % k, agt_b, prompt, wt_path,
                                readonly=False, ev=ev,
                                note="赛马路 %d/%d（worktree 隔离）" % (k + 1, n),
                                images=_task_images(task, wd), require_tools=True)
                if not res["ok"]:
                    break
            v_pass = bool(res and res["ok"])
            if v_pass and task.get("verify_command"):
                r = runner.run_process(shell_cmd=task["verify_command"], cwd=wt_path,
                                       timeout=600, cancel_event=ev)
                v_pass = bool(r["ok"])
            diffstat = (gitmod._git(wt_path, "diff", "--shortstat", base_rev)["stdout"] or "").strip()
            results[k] = {"ok": bool(res and res["ok"]), "verify": v_pass,
                          "wt": wt_path, "diffstat": diffstat}

        threads = []
        for k, br, wt_path in wts:
            th = threading.Thread(target=_race_one, args=(k, wt_path),
                                  name="codebestof-%s-%d" % (run_id, k), daemon=True)
            threads.append(th)
            th.start()
        for th in threads:
            th.join(3600)
        _check_cancel(ev)

        usable = [r for k, r in sorted(results.items()) if r["ok"] and r["verify"]]
        if not usable:
            return False   # 全败：回落单路实现（保持原有换将/报错行为）
        usable.sort(key=lambda r: _shortstat_files(r["diffstat"]))
        winner = usable[0]
        diff = gitmod._git(winner["wt"], "diff", base_rev, timeout=120)
        if diff["ok"] and diff["stdout"].strip():
            apply_r = runner.run_process(
                argv=["git", "apply", "--whitespace=nowarn"],
                cwd=wd, timeout=60, stdin_text=diff["stdout"])
            if not apply_r["ok"]:
                return False
        store.update_run(run_id, bestof={
            "kind": "code-worktree", "candidates": len(results),
            "winner_files": _shortstat_files(winner["diffstat"]),
            "diffstat": winner["diffstat"],
        })
        return True
    except Exception:
        log.warning("code bestof aborted run=%s", run_id, exc_info=True)
        return False
    finally:
        try:
            _cleanup()
        except Exception:
            pass


def _record_actual_route(run_id, task, agents, stats, implementer,
                         implementers=(), reviewer=None, critics=(), implement_reason="",
                         review_reason="", direct=False):
    """把引擎实际选中的执行者/评审者回写到可解释路由计划。"""
    spec = task.get("_compiled_spec") or task_compile.compile_task(task)
    impl_plan = router.route_plan(
        agents, "implement", spec, stats, selected=implementer,
        participants=implementers,
        selection_reason=implement_reason)
    if direct:
        review_plan = {"role": "review", "selected": "", "participants": [],
                       "selection_reason": "", "candidates": [], "fallback": []}
    else:
        review_group = list(critics or ())
        actual_reviewer = reviewer or (review_group[0] if review_group else None)
        review_plan = router.route_plan(
            agents, "review", spec, stats, selected=actual_reviewer,
            participants=review_group, selection_reason=review_reason)
    store.update_run(run_id, route_plan={
        "task": spec, "implement": impl_plan, "review": review_plan,
    })
    task_id = task.get("id") or (store.get_run(run_id) or {}).get("task_id") or ""
    difficulty = (task.get("difficulty") or "auto")
    for plan in (impl_plan, review_plan):
        dispatch_log.record_event(
            run_id=run_id, task_id=task_id, task_type=spec.get("type") or "",
            difficulty=difficulty, role=plan.get("role") or "",
            phase="selected", selected=plan.get("selected") or "",
            participants=plan.get("participants") or (),
            candidates=plan.get("candidates") or (),
            fallback=plan.get("fallback") or (),
            selection_reason=plan.get("selection_reason") or "")


def _record_dispatch_completed(run_id, task, result, verify_pass=None, review_pass=None):
    """记录运行终态，供调度回放与线上指标复盘使用。"""
    try:
        spec = task.get("_compiled_spec") or task_compile.compile_task(task)
        dispatch_log.record_event(
            run_id=run_id,
            task_id=task.get("id") or (store.get_run(run_id) or {}).get("task_id") or "",
            task_type=spec.get("type") or "", difficulty=task.get("difficulty") or "auto",
            role="", phase="completed", result=result,
            verify_pass=verify_pass, review_pass=review_pass)
    except Exception:
        pass


def _retry_content_draft_with_cli(run_id, task, agents, impl, difficulty, mode,
                                  stats, prompt, workdir, step_wd, ev, resume_ctx,
                                  draft_note, draft_res):
    """Auto content tasks may try another configured CLI after a draft failure.

    Model/provider failover has already been exhausted inside run_agent. Keep
    manual execution, user cancellation and resumed sessions pinned to the
    user's explicit choice.
    """
    raw = draft_res.get("raw") or {}
    if (mode == "manual" or impl.get("mode") != "real" or resume_ctx
            or raw.get("cancelled") or draft_res.get("error_code") == "CANCELLED"):
        return draft_res, impl, draft_note

    tried = {impl.get("id")}
    failures = ["%s：%s" % (impl.get("id") or "CLI",
                            (draft_res.get("error") or "起草失败")[:180])]
    dead_upstreams = []
    first_upstreams = router.agent_upstreams(impl.get("id"))
    if first_upstreams:
        dead_upstreams.append(first_upstreams)
    current = impl
    last_impl = impl
    last_result = draft_res
    last_note = draft_note

    while True:
        _check_cancel(ev)
        candidate, reason = router.pick_switch_candidate(
            agents, "implement", task.get("type") or "novel", stats,
            exclude=tried, dead_upstreams=dead_upstreams)
        if candidate is None or candidate.get("id") in tried:
            break
        tried.add(candidate.get("id"))
        if candidate.get("mode") != "real":
            continue

        why = (last_result.get("error") or "起草失败")[:180]
        switch_note = ("起草自动换 CLI：%s → %s（%s）；前序失败：%s" % (
            current.get("id") or "CLI", candidate.get("id") or "CLI",
            reason or "自动路由", why))
        attempt_note = (draft_note + "；" if draft_note else "") + switch_note
        bound = modelhub.bind_agent(candidate, difficulty)
        last_result = _run_step(
            run_id, "draft", bound, prompt, step_wd, readonly=False, ev=ev,
            note=attempt_note, images=_task_images(task, workdir))
        last_impl = candidate
        last_note = attempt_note
        if last_result.get("ok"):
            return last_result, candidate, attempt_note

        failures.append("%s：%s" % (candidate.get("id") or "CLI",
                                    (last_result.get("error") or "起草失败")[:180]))
        if (last_result.get("raw") or {}).get("cancelled"):
            break
        ups = router.agent_upstreams(candidate.get("id"))
        if ups:
            dead_upstreams.append(ups)
        current = candidate

    if last_impl is not impl:
        last_result = dict(last_result)
        last_result["error"] = "CLI 自动切换均失败：%s" % "；".join(failures[-4:])
    return last_result, last_impl, last_note


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
    if mode == "fast":
        difficulty, explicit = "easy", True
    elif mode == "expert":
        difficulty, explicit = "hard", True
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
        store.update_run(run_id, expected_status="running", status="failed",
                         error="没有可用智能体", ended_at=_now())
        return

    # 项目记忆既给规划器，也给快速路径的实现者；不能因省掉独立规划而漏掉。
    user_context_len = len(task.get("context") or "")
    project_memory = _read_project_memory(workdir)
    if project_memory:
        task = dict(task, context=(task.get("context") or "") + "\n\n" + project_memory)

    # ---- 规划。低风险短任务直接把目标作为单步计划，省掉一次独立模型调用；
    # 附件或较长的用户背景仍先规划，避免快速路径漏读约束。
    fast_path = (mode == "fast" or
                 (mode == "auto" and difficulty == "easy"
                  and not task.get("attachments")
                  and user_context_len < 600))
    planned = False
    if mode in ("auto", "expert") and not fast_path:
        _wait_gate(run_id, ev)
        plan_step, plan_log = store.add_step(run_id, "plan", impl["id"], impl.get("label"),
                                             note=route.get("implementer", ""))
        plan = _planner_call(
            planner.make_code_plan, _steered_task(run_id, task),
            modelhub.bind_agent(impl, difficulty),
            _resume_workdir(resume_ctx, workdir), ev,
            resume=resume_ctx["session"] if resume_ctx else None,
            log_path=str(plan_log) if plan_log else None,
            deadline=_run_deadline(run_id))
        _ensure_budget(run_id)
        # 规划器判定优先于启发式（仅当用户未显式指定难度）
        if not explicit and plan.get("difficulty") in ("easy", "hard"):
            difficulty = plan["difficulty"]
        store.finish_step(run_id, plan_step["n"], "done",
                          summary="计划来源 %s（难度 %s）：%s" % (
                              plan["source"], difficulty,
                              "；".join(s["title"] for s in plan["steps"])[:160]),
                          duration_s=0.1 if impl.get("mode") == "mock" else None)
        planned = True
    elif fast_path:
        plan = {
            "source": "dynamic-fast-path",
            "difficulty": "easy",
            "steps": [{"title": "直接实现并自检", "detail": task["goal"]}],
            "workflow": {
                "review_required": not bool(task.get("verify_command")),
                "max_repair_rounds": 1,
                "allow_switch": False,
            },
        }
    else:
        plan = {"source": "manual", "steps": [{"title": "实现任务", "detail": task["goal"]}]}
    workflow = task_compile.code_workflow(
        task, difficulty, plan=plan, mode=mode, planned=planned)
    store.update_run(run_id, plan=plan, difficulty=difficulty, workflow=workflow)
    subtasks = plan["steps"]
    # 计划落盘（planning-with-files）：磁盘上的计划与 spec/evidence 同居任务档案
    _write_task_plan(task, workdir, plan)

    # ---- 评审者（有会话延续时评审者仍用新鲜上下文，避免偏见）
    if not workflow["review_required"]:
        reviewer = None
        route["reviewer"] = workflow["reason"]
    elif mode in ("auto", "expert"):
        reviewer, route["reviewer"] = router.pick_reviewer(agents, impl, "code", stats)
    else:
        reviewer, route["reviewer"] = _pick_reviewer_legacy(agents, impl)
    _record_actual_route(run_id, task, agents, stats, impl, reviewer=reviewer,
                         implement_reason=route.get("implementer", ""),
                         review_reason=route.get("reviewer", ""),
                         direct=not workflow["review_required"])
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
                        mh_data = modelhub._load()
                        agt_b = capability.cascade_reorder(
                            agt_b, capability.make_tier_lookup(modelhub.providers()),
                            providers=modelhub.providers(),
                            pricing=mh_data.get("pricing") or {},
                            difficulty=difficulty, task_type=task.get("type") or "code",
                            role="implement")
                except Exception:
                    pass
            for i, sub in enumerate(subtasks):
                # 子任务进度注入（planning-with-files 的每轮注入计划头）：多子任务时
                # 让实现者知道全局位置与已完成项，防止长链目标漂移；单子任务零噪音
                prog = ""
                if len(subtasks) > 1:
                    done_titles = "、".join(
                        (s.get("title") or "")[:40] for s in subtasks[:i]) or "（无）"
                    prog = ("\n\n## 计划进度（第 %d/%d 项）\n已完成：%s。当前接着做下面这一项，"
                            "不要重做已完成的。" % (i + 1, len(subtasks), done_titles))
                prompt = (_RUN_CONSTITUTION + CODE_IMPL_PROMPT
                          .replace("__GOAL__", task["goal"])
                          .replace("__SUBTASK__",
                                   sub["detail"] if sub["detail"] else sub["title"])
                          .replace("__FILES__", _files_hint(sub))
                          .replace("__CONTEXT__", task.get("context") or "（无）")
                          .replace("__VERIFY_HINT__", _verify_hint(task))) + prog
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
        if runner._auth_error(res.get("error") or ""):
            auth_err = ("实现步骤失败（上游拒绝认证；已停止自动换 CLI，避免隐式切换供应商）。"
                        "请检查当前绑定的 Key、账号权限和端点；需要认证降级时，"
                        "请在该 CLI 的绑定链中显式配置不同凭据。错误：%s"
                        % (res.get("error") or "")[:500])
            store.update_run(run_id, expected_status="running", status="failed",
                             error=auth_err, ended_at=_now())
            return False
        # 单路实现失败：Best-of-N 赛马兜底（借鉴 orca worktree 择优）——多路并行
        # 各自 worktree 隔离实现+验证，胜者 diff 回主工作区；失败回落走换将
        if (mode == "auto" and impl_agent.get("mode") == "real"
                and max(1, min(3, int(task.get("best_of") or 1))) >= 2
                and resume_ctx is None
                and _code_bestof(run, task, impl_agent, difficulty, ev)):
            return True
        # 实现步失败不立刻判死：2026-09-16 实测配额烧干时 5 连跑全在同一条 CLI 上
        # 失败收场，而健康的 opencode 一直在旁观望——跨 CLI 换将重试
        # （mode=manual 尊重用户指定，不换）。2026-09-22 续修：配额/限流类死亡
        # 是确定性秒死，允许继续走查候选名单（同上游让位异上游）；其余死因
        # （超时等）仍只换一次，防着在坏候选上再烧一整个超时。
        if mode == "auto" and impl_agent.get("mode") == "real":
            tried = {impl_agent["id"], "mock-a", "mock-b"}
            notes = []
            dead_ups = []  # 配额死亡候选的上游集合：换将优先异上游
            prev_id = impl_agent["id"]
            other = None
            while True:
                other, other_reason = router.pick_switch_candidate(
                    agents, "implement", "code", stats,
                    exclude=tried, dead_upstreams=dead_ups)
                if other is None or other.get("mode") != "real":
                    other = None
                    break
                note = "实现步失败自动换将 %s → %s：%s。失败原因：%s" % (
                    prev_id, other["id"], other_reason,
                    (res.get("error") or "")[:200])
                ok2, res = _run_one(other)
                if ok2:
                    route_note = "；".join(notes + [note])
                    store.update_run(run_id, error="", route_note=route_note)
                    _record_actual_route(
                        run_id, task, agents, stats, other,
                        implementers=[other], reviewer=reviewer,
                        implement_reason=route_note,
                        review_reason=route.get("reviewer", ""),
                        direct=not workflow["review_required"])
                    return True
                notes.append(note)
                tried.add(other["id"])
                prev_id = other["id"]
                if runner._quota_error(res.get("error") or ""):
                    ups = router.agent_upstreams(other["id"])
                    if ups:
                        dead_ups.append(ups)
                else:
                    break  # 非配额死因：只换一次就收手
            tail_err = (res.get("error") or "")[:200]
            if notes:
                res_err = "；".join(notes) + "；换将后仍失败：%s" % tail_err
            elif other is None:
                res_err = "实现步骤失败（无其他真实 CLI 可换将）: %s" % tail_err
            else:
                res_err = "实现步骤失败: %s" % tail_err
        else:
            res_err = "实现步骤失败: %s" % res.get("error")
        store.update_run(run_id, expected_status="running", status="failed",
                         error=res_err, ended_at=_now())
        return False

    def review_and_score():
        verify_pass, verify_ran = _run_verify(run_id, task, workdir, ev)
        if not workflow["review_required"]:
            return {
                "pass": verify_pass,
                "scores": {},
                "issues": ([] if verify_pass else [{
                    "severity": "high", "title": "验证命令未通过",
                    "detail": "动态短链省略模型评审，先修复确定性验证失败。",
                }]),
                "summary": "低风险短链：确定性验证%s，模型评审已省略。" %
                           ("通过" if verify_pass else "未通过"),
            }, verify_pass, verify_ran
        # gate 验证（借鉴 pi-subagents 的 gate:"npm test"）：verify 失败时先跳过
        # 模型评审直接进修复轮——评审此时只能复述「验证没过」，白烧一次调用。
        # 连续失败 >=2 轮后恢复评审参与诊断（模型能看出编译错误之外的病灶）。
        if not verify_pass and verify_ran:
            verify_fail_streak[0] += 1
            if verify_fail_streak[0] < 2:
                return {"pass": False, "scores": {},
                        "issues": [{"severity": "high", "title": "验证命令未通过",
                                    "detail": "验证命令 `%s` 未通过：先修复使验证转绿，无需等评审。"
                                              % task.get("verify_command")}],
                        "summary": "gate：验证未通过，跳过模型评审直接修复"}, verify_pass, verify_ran
        else:
            verify_fail_streak[0] = 0
        # 先让确定性验证落盘，再执行模型评审；终态判断会同时使用两份证据。
        review_json = _run_review(run_id, task, workdir, modelhub.bind_agent(reviewer, difficulty), ev)
        return review_json, verify_pass, verify_ran

    verify_fail_streak = [0]   # gate：verify 连败计数（>=2 轮恢复评审参与诊断）

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
            prompt = attachments.append_task_context(prompt, task)
            res = _run_step(run_id, "fix-r%d" % round_no, modelhub.bind_agent(impl, difficulty),
                            prompt, workdir, readonly=False, ev=ev,
                            note="自动修复第 %d 轮" % round_no,
                            resume=resume_ctx["session"] if resume_ctx else impl_sid[0],
                            require_tools=True)
            if (res.get("raw") or {}).get("timed_out"):
                # 自动修复没有换将/重试兜底；超时后继续验收会把未完成的修复
                # 当作正常轮次，让 run 继续显示 running，最终还可能误标 done。
                # 步骤本身仍保留 timeout 细节，这里将 run/task 收口为终态 timeout。
                error = (res.get("error") or "修复步骤超时")[:400]
                store.update_run(run_id, expected_status="running", status="timeout",
                                 ended_at=_now(), error="代码修复步骤超时：%s" % error)
                return
            if res["ok"]:
                _record_actual_route(
                    run_id, task, agents, stats, impl,
                    implementers=[impl], reviewer=reviewer,
                    implement_reason="修复轮由 %s 完成" %
                    (impl.get("label") or impl.get("id")),
                    review_reason=route.get("reviewer", ""),
                    direct=not workflow["review_required"])
            if impl.get("mode") == "mock" and res["ok"]:
                pass  # mock 不产生真实变更
        review_json, verify_pass, verify_ran = review_and_score()
        if review_json.get("reviewer_error"):
            # 评审器故障（Key 失效/崩溃）：没有可修的质量问题，修复轮与换将
            # 都无意义（换将也走同一个失效网关）——以明确错误收口，原因可见。
            _why = (review_json.get("summary") or "评审器执行失败")[:300]
            store.update_run(run_id, expected_status="running", status="failed",
                             ended_at=_now(), error="评审器故障（评审未执行）：%s" % _why)
            return
        passed = verify_pass and bool(review_json.get("pass"))
        repairs.append({"round": round_no, "kind": "switch" if switched else (
            "initial" if round_no == 0 else "repair"),
            "verify_pass": verify_pass, "review_pass": bool(review_json.get("pass")),
            "passed": passed})
        if passed:
            break
        if round_no < workflow["max_repair_rounds"] and not switched:
            round_no += 1
            continue
        if mode == "auto" and workflow["allow_switch"] and not switched:
            ex = (impl["id"],)
            if impl.get("mode") == "real":
                ex += ("mock-a", "mock-b")  # 真实实现者失败时不降级到 mock
            other, other_reason = router.pick(agents, "implement", "code", stats, exclude=ex)
            if other is not None:
                note = "换将 %s → %s：%d 轮实现/修复后仍未通过。%s" % (
                    impl["id"], other["id"], round_no + 1, other_reason)
                store.update_run(run_id, error="")
                impl = other
                _record_actual_route(
                    run_id, task, agents, stats, impl,
                    implementers=[impl], reviewer=reviewer,
                    implement_reason=note,
                    review_reason=route.get("reviewer", ""),
                    direct=not workflow["review_required"])
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
        "review_pass": (bool(review_json.get("pass"))
                        if workflow["review_required"] else None),
        "review_skipped": not workflow["review_required"],
        "scores": scores, "overall_score": overall_score,
        "issues": review_json.get("issues") or [],
        "reviewer": reviewer["id"] if reviewer else "",
        "route": route, "repairs": repairs, "switched": switched,
        "workflow": workflow,
    }

    lines = [
        "# 代码任务报告：%s" % task["title"], "",
        "- 结论：**%s**" % ("✅ 通过" if overall_pass else "❌ 未通过"),
        "- 编排模式：%s　实现者：%s　评审者：%s" % (
            {"auto": "自动", "fast": "快速", "expert": "专家",
             "manual": "手动"}.get(mode, mode), impl.get("label"),
            reviewer.get("label") if reviewer else "按验证结果省略"),
        "- 动态步骤：规划 %s；实现 %d 步；验证 %s；评审 %d 人；最多修复 %d 轮；换将 %s" % (
            "独立执行" if workflow["planning"] == "llm" else "并入实现",
            workflow["implementation_steps"], "有" if workflow["verification"] else "无",
            workflow["reviewers"], workflow["max_repair_rounds"],
            "允许" if workflow["allow_switch"] else "关闭"),
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
    _write_task_evidence(run_id, task, workdir, _evidence_lines_from_run(run_id, task))
    # 项目记忆沉淀（借鉴 agentmemory 持久记忆）：代码任务成功后提取架构事实
    # （改动文件/验收结果/修复轮数），追加到 .codebee/project-memory.md——
    # 同目录后续 code 任务规划前自动注入，让编排者「知道这个代码库的脾气」
    try:
        _diff = _git_diff(workdir)
        _files_touched = sorted(set(re.findall(
            r"(?:^|\n)diff --git a/(\S+) b/(\S+)", _diff or "")))
        _touched_str = "、".join(sorted(set(b for _, b in _files_touched)))[:500] if _files_touched else ""
        _mem_lines = ["改动文件：%s" % (_touched_str or "（无 diff）"),
                      "验收：%s" % ("通过" if verify_pass else "未通过"),
                      "修复轮数：%d" % (len(repairs) - 1)]
        _write_project_memory(task, workdir, _mem_lines)
    except Exception:
        pass
    store.update_run(run_id, expected_status="running", status="done", verdict=verdict,
                     summary="代码任务%s（验证%s / 评审%s%s）" % (
                         "通过" if overall_pass else "未通过",
                         "通过" if verify_pass else "未通过",
                         (("通过" if review_json.get("pass") else "未通过")
                          if workflow["review_required"] else "按策略省略"),
                         "，%d 轮修复" % (len(repairs) - 1) if len(repairs) > 1 else ""),
                     ended_at=_now())


# ---------------------------------------------------------------- direct 引擎（直连单 CLI：无拆解/评审，对话式续轮）

DIRECT_PROMPT = """你是 CodeBee 的执行智能体，直接完成用户交代的任务。用户的目标、背景与工作目录内的附件就是全部输入：不拆解、不评审、不换人，直接动手。

## 任务
__GOAL__

## 背景与上下文
__CONTEXT__

## 要求
- 背景与上下文里若有「附件材料」，先逐个读取再处理——附件是必要输入，不读附件就作答视为未完成。
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
- 背景与上下文里若有「附件材料」，先逐个读取再处理——附件是必要输入，不读附件就作答视为未完成。
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
    impl = None
    direct_provider = (task.get("direct_provider_id") or "").strip()
    direct_model = (task.get("direct_model") or "").strip()
    direct_agent = (task.get("direct_agent") or "").strip()
    direct_difficulty = ("hard" if task.get("thinking") == "high" else
                         "easy" if task.get("thinking") == "low" else "default")
    if resume_ctx is None and direct_agent:
        # 手动指定执行 CLI（2026-09-23）：用户点名压过一切自动（模型绑定/
        # 自动推荐/路由）。目标未启用编排时按续会话语义注入（installed_agent
        # 无视启停开关）——显式指定不该被「参与编排」开关否决。
        impl = _pick(agents, direct_agent)
        if impl is None:
            extra = registry.installed_agent(direct_agent, catalog.load(),
                                             manager.detect_all())
            if extra:
                agents.append(extra)
                impl = extra
        if impl is None:
            store.update_run(run_id, expected_status="running", status="failed",
                             error="指定的执行 CLI「%s」不可用（未安装或未检测到），"
                                   "请在对话条改回自动推荐，或到目录页先安装" % direct_agent,
                             ended_at=_now())
            return
        route["implementer"] = "%s（手动指定 CLI）" % (impl.get("label") or impl.get("id"))
    elif resume_ctx is None and direct_provider:
        bi = builtin_agent.resolve(direct_provider, direct_model, direct_difficulty)
        if bi is None:
            store.update_run(run_id, expected_status="running", status="failed",
                             error="指定的对话厂商或模型不可用，请改用自动推荐或检查配置",
                             ended_at=_now())
            return
    elif resume_ctx is None and not (mode == "manual" and task.get("implementer")):
        try:
            bi = builtin_agent.resolve(difficulty=("hard" if task.get("thinking") == "high"
                                                   else "easy" if task.get("thinking") == "low"
                                                   else "default"))
        except Exception:
            bi = None
    if bi is not None:
        bi["reasoning_effort"] = {"low": "low", "standard": "medium", "high": "high"}.get(
            task.get("thinking"), "medium")
        impl = None
        route["implementer"] = "CodeBee（%s · %s）" % (bi["provider_name"], bi["model"])
    elif resume_ctx is not None:
        impl = resume_ctx["agent"]
        route["implementer"] = resume_ctx["note"]
    elif impl is not None:
        pass          # 手动指定 CLI 已就位（route 亦已写）
    elif mode == "manual":
        impl, _ = _pick_implementer(agents, task.get("implementer"))
    else:
        impl, route["implementer"] = router.pick(agents, "implement", task["type"], stats)
    if impl is None and bi is None:
        store.update_run(run_id, expected_status="running", status="failed",
                         error="没有可用智能体", ended_at=_now())
        return
    actual_impl = impl or {
        "id": "builtin:%s:%s" % (bi.get("provider_id") or "provider", bi.get("model") or "model"),
        "label": "CodeBee · %s" % (bi.get("model") or bi.get("provider_name") or "内置模型"),
        "kind": "builtin",
    }
    _record_actual_route(run_id, task, agents, stats, actual_impl,
                         implement_reason=route.get("implementer", ""), direct=True)
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
            prompt = ""
            if task.get("type") == "rank_scan" and bi is not None:
                # 扫榜选材（借鉴 oh-story 扫榜）：抓七猫排行榜公开数据注入，
                # AI 做选题洞察；抓取失败回落普通直连提示词
                prompt = paihang.rank_scan_prompt(task.get("goal") or "") or ""
            if not prompt:
                if bi is not None:
                    prompt = (BUILTIN_DIRECT_PROMPT
                              .replace("__GOAL__", task["goal"])
                              .replace("__CONTEXT__", task.get("context") or "（无）")
                              .replace("__FOLLOWUPS__", FOLLOWUPS_PROTOCOL))
                else:
                    prompt = (DIRECT_PROMPT
                              .replace("__GOAL__", task["goal"])
                              .replace("__CONTEXT__", task.get("context") or "（无）"))
            if task.get("attachments") and "codebee-attachments:start" not in prompt:
                prompt += "\n\n## 用户背景与附件\n" + (task.get("context") or "")
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
        # 钩子注入（message_submit / task_start 通道）：一次性消费拼提示词头
        hook_head = ""
        try:
            hook_head = store.pop_run_inject(run_id)
        except Exception:
            pass
        if hook_head:
            prompt = ("## 项目钩子注入\n" + hook_head + "\n\n" + prompt)
        if bi is not None:
            res = _run_builtin_step(run_id, "direct" if first else "chat", bi, prompt,
                                    step_wd, ev=ev, note=note, images=images,
                                    followups=True)
        else:
            res = _run_step(run_id, "direct" if first else "chat", impl, prompt, step_wd,
                            readonly=False, ev=ev, note=note,
                            resume=sid or None, images=images)
        if not res["ok"]:
            store.update_run(run_id, expected_status="running", status="failed",
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
    _write_task_evidence(run_id, task, workdir, _evidence_lines_from_run(run_id, task))
    store.update_run(run_id, expected_status="running", status="done", verdict=verdict,
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

NOVEL_DRAFT_PROMPT = """你是__ROLE__。请在当前工作目录中撰写/修订稿件文件：`__FILE__`（直接写入该文件）。文件必须以 UTF-8 编码保存（PowerShell 写文件显式加 -Encoding UTF8，禁止依赖默认编码）。

## 写作任务
__GOAL__

## 背景与上下文
__CONTEXT__

## 评审维度（写前自查）
__RUBRIC__
（按这些维度组织内容重点——评审会按它们打分）

## 要求
- 只修改 `__FILE__` 这一个文件；保持 Markdown 结构。
- 完成后用 3 句话说明本轮写了什么。"""

# review 引擎共用执行骨架，但交付物不能只靠 rubric 猜格式。每个内置类型给出
# 最小成品契约，起草和修订都注入；自定义流程继续使用通用回退，避免强加结构。
CONTENT_DELIVERY_CONTRACTS = {
    "novel": ("小说作者", [
        "遵守用户给定的题材、篇幅、视角和风格；未给出的核心设定不要擅自扩张。",
        "用场景、行动和对话推进冲突，人物动机与前后因果保持一致。",
    ]),
    "article": ("平台内容主编", [
        "标题、开头钩子、正文层级和结尾行动建议要适配目标平台与读者。",
        "事实、数据和引语不得编造；缺少来源时明确标注待核实。",
    ]),
    "video_script": ("短视频编导", [
        "按镜头或时间段写清画面、口播、字幕/音效与预计时长，前 3 秒给出钩子。",
        "每个画面都应可实际拍摄或制作，结尾给出自然的互动或转化动作。",
    ]),
    "doc": ("技术文档编辑", [
        "先明确读者、目的和前置条件，再按可执行步骤组织正文。",
        "命令、参数、示例与限制必须一致；无法确认的内容明确标注。",
        # sepia 分场合规则（工单/文档体裁）：标题=结果、验收可测试、链接不重复
        "标题写结果或结论（「如何迁移 X」优于「X 说明」），正文链接原文不整段复述。",
        "涉及需求或变更时给出可测试的验收标准（能被逐条勾选判定通过/不通过）。",
    ]),
    "translation": ("专业译者与审校", [
        "忠实保留原文含义、语气、数字、专名、占位符、链接和 Markdown 结构，不增译或漏译。",
        "术语译法全文一致；歧义或无法确认的专名保留原文并加简短译注。",
    ]),
    "research": ("研究分析师", [
        "围绕决策问题组织证据、对比、结论与可执行建议，避免资料堆砌。",
        "结论必须能回溯到来源；证据不足处明确写出不确定性和验证办法。",
    ]),
    "speech": ("演讲撰稿人", [
        "按场合、听众和时长控制篇幅，使用适合现场说出的短句与自然转场。",
        "开场建立关系，主体围绕一个核心信息展开，结尾给出清晰收束或号召。",
    ]),
    "weekly_report": ("业务汇报顾问", [
        "按成果与影响、关键数据、问题阻塞、下步行动（负责人/时间）组织内容。",
        "只使用用户提供或可核验的数据；缺失数字保留待补项，不虚构业绩。",
        # sepia 分场合规则（postmortem 体裁）：先给结论；对机制严格不指名甩锅
        "第一段先给本期最重要的结论或结果，再展开支撑细节，不按时间流水铺陈。",
        "问题与阻塞直说机制原因，不带情绪也不指名甩锅；行动项必须落到负责人与时间。",
    ]),
    "email": ("商务沟通顾问", [
        "包含明确主题、称呼、来意、必要背景、请求/下一步和得体落款。",
        "语气匹配双方关系；日期、承诺、附件与联系人不得凭空补造。",
        # sepia 分场合规则（PR 回复体裁）：先答再铺陈；篇幅与利害成正比
        "第一句/第一段先给结论或答复（对方要做什么、答应还是不答应），再给必要背景。",
        "请求具体到动作与截止时间；篇幅与事情轻重成正比，删掉礼节性空话与自我表扬。",
    ]),
    "tech_proposal": ("解决方案架构师", [
        "覆盖现状与目标、约束、候选方案对比、推荐架构、实施阶段、风险与回滚、验收指标。",
        "区分已知事实、假设和待验证项；成本收益给出计算口径而非虚构数字。",
        # sepia 分场合规则（技术文章体裁）：从问题开场/真实死胡同/明确观点/带条件数字
        "从要解决的问题开场（不是从背景科普铺陈），让读者第一段就知道为什么非做不可。",
        "候选对比里至少保留一个真实分析过又被否决的方向，写清否决理由，不搞陪衬方案。",
        "必须有明确表态的推荐意见和取舍逻辑；关键数字一律带适用条件与计算口径。",
    ]),
    "resume": ("招聘与简历顾问", [
        "围绕目标岗位提炼真实经历，用行动、结果和技能关键词表达岗位匹配度。",
        "不得虚构经历、公司、学历、指标或技术栈；缺少量化数据时保留待补提示。",
    ]),
    "bid_doc": ("投标经理", [
        # BidCraft 标书匠灵感（2026-09-22）：逐条对齐招标文件实质性要求与评分标准
        "逐条对齐招标文件的实质性要求和评分标准，每个评分点都有对应的应答段落。",
        "资质、业绩、人员证书只引用真实材料；缺失项明确标注「待补」并说明需要哪类材料。",
        # sepia 分场合规则（投标文体）：先结论后展开；废标项零容忍
        "应答开门见山给结论（满足/优于/偏离），再给支撑细节，不写套话式开头。",
        "全文自查废标风险项（密封/签章/格式/有效期），任何疑似偏离必须显式列出。",
    ]),
}


def _content_role(task):
    """返回内置类型的专业角色；自定义 review 流程使用中性角色。"""
    spec = CONTENT_DELIVERY_CONTRACTS.get(str(task.get("type") or ""))
    return spec[0] if spec else "内容交付专家"


def _content_contract(task):
    """把类型成品约束渲染为稳定提示块；无内置契约时不额外注入。"""
    spec = CONTENT_DELIVERY_CONTRACTS.get(str(task.get("type") or ""))
    if not spec:
        return ""
    return "\n\n## 本类型交付约束\n" + "\n".join("- " + item for item in spec[1])

# 调研报告类稿件的追加要求（借鉴 gpt-researcher 迭代深研）：有网络/读文件工具时
# 多源交叉验证，单源结论降权——调研的可信度来自证据链而非文采
RESEARCH_APPENDIX = """

## 调研要求（证据链）
- 有联网/检索工具就先搜集资料再写：同一关键结论至少两个独立来源交叉验证，
  单源信息要标注「仅单一来源」。
- 引用来源在文中用行内链接或脚注标明（域名即可，不编造 URL）。
- 区分「事实」与「观点」：数据/时间/版本号给来源，预测/评价标明是分析。
- 结构硬性要求：报告第一段必须是「**核心结论**」三行以内的要点摘要（结论先行），
  之后才展开分层论证 → 风险与局限（说明哪些结论证据不足）。"""

NOVEL_REVISE_PROMPT = """你是__ROLE__。请根据下方汇总评审意见修订稿件文件：`__FILE__`（直接写入该文件）。文件必须以 UTF-8 编码保存（PowerShell 写文件显式加 -Encoding UTF8，禁止依赖默认编码）。

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
MODULES_FILE = "plot-modules.md"
_MODULES_MAX_CHARS = 12000

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
            "与其冲突处以圣经为准）\n\n"
            "**连续性锁**（借鉴 drama-skills）：人物外貌/性格口头禅/物品/能力等需要跨章一致"
            "的设定，写作时原样沿用圣经中的表述（可整句贴入正文），不要同义改写——每章用词"
            "一致读者才不会出戏。\n\n" + txt)


def _plot_modules(workdir):
    """剧情模块库（oh-story 拆文沉淀式）：工作目录里的 plot-modules.md
    （可复用的桥段/冲突/爽点/名场面素材模块），作者手工维护，每章起草与
    评审前自动注入。不存在/为空返回 ""——约定式功能，零配置零噪音。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), MODULES_FILE))
    if not _inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        txt = _read_text_any_enc(p)[:_MODULES_MAX_CHARS].strip()
    except OSError:
        return ""
    if not txt:
        return ""
    return ("## 剧情模块库（plot-modules.md：可复用的桥段/冲突/爽点素材模块，"
            "鼓励化用，不要照抄原句）\n\n" + txt)


def _book_volume_plan(task, outline, upto):
    """本书的卷规划表（卷边界+卷名+卷弧光）。实现见 planner.book_volume_plan
    ——放在 planner 是因为它要沿 serial.continues 链读历史 run 的卷名，属
    「大纲/命名」职责；此处保留薄封装便于连载流程阅读。"""
    return planner.book_volume_plan(task, outline, upto)


LEDGER_FILE = os.path.join(".codebee", "resource-ledger.md")
_LEDGER_MAX_CHARS = 6000   # 账本注入上限：太老的状态让评审官收敛 recent 优先


def _parse_tagged_lines(text, tag):
    """从模型回复提取 <tag>...</tag> 块的非空行列表（缺失/空块返回 []）。"""
    m = re.search(r"<%s>([\s\S]*?)</%s>" % (tag, tag), text or "")
    if not m:
        return []
    return [ln.strip(" -*") for ln in m.group(1).splitlines() if ln.strip(" -*")]


def _append_ledger(workdir, chapter, lines):
    """资源账本追加：本章评审提炼的道具/伤情/承诺/伏笔增量，供下章起草注入。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), LEDGER_FILE))
    if not _inside(workdir, p):
        raise ValueError("ledger 路径越界")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write("# 资源账本（每章评审自动追加：道具/伤情/承诺/伏笔的现状，"
                    "写新章前必须核对，防止跨章穿帮）\n\n")
        f.write("## 第 %d 章\n%s\n" % (chapter, "\n".join("- " + x for x in lines)))


def _read_ledger(workdir):
    """读取资源账本注入文本（截至上一章）。不存在返回「（暂无记录）」。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), LEDGER_FILE))
    if not _inside(workdir, p) or not os.path.isfile(p):
        return "（暂无记录，本章建立的新道具/伤情/承诺/伏笔会被账本自动登记）"
    try:
        txt = _read_text_any_enc(p)[-_LEDGER_MAX_CHARS:].strip()
    except OSError:
        return "（暂无记录）"
    return txt or "（暂无记录）"


def _shrink_context_block(sk_block, bible, budget=12000):
    """分层上下文降级（长提示词在容量受限通道上会挂起/秒拒，2026-09-17 讯飞实测）。

    四层优先级：故事圣经（最高，设定冲突以它为准）> 剧情模块库 > 经验库 > 大纲/前情
    （后两者在提示词正文里，永不动）。超预算时按优先级保序截断：
    - 圣经截断保整段（按二级标题边界，无边界才硬截）
    - 模块库截断保整模块（按「## 」标题边界）
    - 经验库直接硬截（条目本身短，损失最小）
    返回 (新 sk_block, 降级说明)。无降级返回原样。"""
    total = len(sk_block or "") + len(bible or "")
    if total <= budget:
        return sk_block, bible, ""
    notes = []
    # 1) 先压经验库到 4K（条目短、损失最小）
    if len(sk_block or "") > 4000:
        sk_block = sk_block[:4000] + "\n\n（经验库已因上下文容量限制精简）"
        notes.append("经验库→4K")
        if len(sk_block) + len(bible or "") <= budget:
            return sk_block, bible, "；".join(notes)
    # 2) 模块库按模块边界截断
    if bible and "## 剧情模块库" in bible:
        head, sep, mods = bible.partition("## 剧情模块库")
        mods = sep + mods
        keep = mods[:6000]
        cut = keep.rfind("\n## ")
        if cut > 200:
            keep = keep[:cut]
        bible = head + keep + "\n\n（模块库已因上下文容量限制精简）"
        notes.append("模块库→边界截断")
        if len(sk_block) + len(bible) <= budget:
            return sk_block, bible, "；".join(notes)
    # 3) 圣经按二级标题边界截断到预算
    if bible:
        budget_left = max(2000, budget - len(sk_block or ""))
        keep = bible[:budget_left]
        cut = keep.rfind("\n## ")
        if cut > 500:
            keep = keep[:cut]
        bible = keep + "\n\n（圣经已因上下文容量限制精简）"
        notes.append("圣经→边界截断")
    return sk_block, bible, "；".join(notes)


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
__VOLUME__
- 撰写本书第 __I__ 章，把本章正文写入文件 `__FILE__`（直接写入该文件，只写本章）。文件必须以 UTF-8 编码保存：PowerShell 一律显式加 `-Encoding UTF8`（如 `Set-Content -Path __FILE__ -Encoding UTF8`），禁止依赖系统默认编码，否则中文会乱码。
- 章节标题：__TITLE__
- 剧情要点：__BEATS__
- 章末钩子：__HOOK__
- 正文约 __WORDS__ 字，中文，直接开写正文（可含本章标题行）。

## 前情提要（此前各章结尾摘录，衔接用）
__PREV__

## 资源账本（道具/伤情/承诺/伏笔的现状登记，写本章前必须核对）
__LEDGER__

- 写完文件后，最终回复只输出一行：`第 __I__ 章完成（约 __WORDS__ 字）`——不要在回复里复述或解释正文。"""

SERIAL_REVISE_PROMPT = """你是一名网文作者。第 __I__ 章没有通过评审，请修订文件 `__FILE__`（直接改写该文件）。文件必须以 UTF-8 编码保存（PowerShell 显式加 -Encoding UTF8，禁止依赖默认编码）。

## 全书目标
__GOAL__

## 本卷上下文
__VOLUME__
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
    if not prose:
        # 第四道网（BAML 借鉴）：维度名没命中时按「X：N 分」模式泛化抓取——
        # 自定义 rubric 改了维度措辞而模型用了自己的说法时仍能救回
        prose = runner.extract_scores_from_text(text)
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
    # 安全落盘委托（inkos 安全章节工作区借鉴）：tmp+原子改名，写一半
    # 崩溃不留半章冒充成稿；路径守卫在 chaptersafe（resolve+parents）
    chaptersafe.atomic_write_chapter(workdir, i, text)


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
    actual_implementers = [impl]
    actual_critics = list(critics)
    current_impl = [impl]
    current_impl_reason = [route.get("author", "")]
    current_review_reason = [route.get("critics", "")]

    def refresh_actual_route(primary_impl=None, implement_reason=None,
                             review_reason=None):
        if primary_impl is not None:
            current_impl[0] = primary_impl
        if implement_reason is not None:
            current_impl_reason[0] = implement_reason
        if review_reason is not None:
            current_review_reason[0] = review_reason
        _record_actual_route(
            run_id, task, agents, stats, current_impl[0],
            implementers=actual_implementers, critics=actual_critics,
            implement_reason=current_impl_reason[0],
            review_reason=current_review_reason[0])

    def remember_agent(bucket, agent):
        if agent and not any(x.get("id") == agent.get("id") for x in bucket):
            bucket.append(agent)

    refresh_actual_route(impl)
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
    # 剧情模块库：plot-modules.md（拆文沉淀的可复用素材模块）并入同一注入块，
    # 同样要求 run 内字节稳定；无模块库时零噪音
    mods = _plot_modules(workdir)
    if mods:
        bible = (bible + "\n\n" + mods) if bible else mods
    # 任务宪章（spec-kit constitution）：作者定的质量原则拼在注入块最前——
    # 优先级最高的约束放最前面，写作者先读原则再读设定
    if _RUN_CONSTITUTION:
        bible = _RUN_CONSTITUTION + (bible or "")
    if task.get("context"):
        bible = task["context"] + ("\n\n" + bible if bible else "")


    def crit_prompt_for(text, note="", event_check=""):
        tpl = _ensure_critique_placeholders(
            _tpl(task, "critique_prompt", NOVEL_CRITIQUE_PROMPT))
        if note:
            tpl = tpl.replace("你是严格的评审",
                              "你是严格的评审（背景：%s，请结合全书目标评审本章节）" % note, 1)
        if event_check:
            # 逐项目标审稿（借鉴 AI-Novel-Writer v1.1）：本章大纲要点逐项核对
            tpl = tpl.replace("## 待评审稿件", "%s\n\n## 待评审稿件" % event_check, 1)
        # stable_order：评审分轮次调用，hits 中途变化会打碎前缀缓存（§07 T1.2'）
        sk, _ = skills.block_for(task, stable_order=True, run_id=run_id)
        if sk:
            tpl = tpl.replace("## 待评审稿件", "%s\n\n## 待评审稿件" % sk, 1)
        if bible:
            tpl = tpl.replace("## 待评审稿件", "%s\n\n## 待评审稿件" % bible, 1)
        kb = knowledge.block_for(task)
        if kb:
            tpl = tpl.replace("## 待评审稿件", "%s\n\n## 待评审稿件" % kb, 1)
        # 确定性检测（AI 味/叙事架构/节奏）此前只挂在单稿件评审上，连载逐章
        # 从未拿到——逐章节奏与钩子恰恰最需要这条参考线（命中才追加，不扣分）
        tpl = aiflavor.inject_into_prompt(tpl, text, task.get("type"))
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
            store.update_run(run_id, expected_status="running", status="failed",
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
        outline = _planner_call(
            planner.make_serial_outline, _steered_task(run_id, task), impl, workdir, ev,
            log_path=str(outline_log) if outline_log else None,
            deadline=_run_deadline(run_id))
        _ensure_budget(run_id)
        if outline.get("degraded") and impl.get("mode") != "mock":
            # 兜底模板只有章号没有情节，据此写出的两万字等于废稿——
            # 中止并交给自动续跑等编排者恢复后重试，而不是空转烧配额。
            store.finish_step(run_id, outline_step["n"], "failed",
                              summary="大纲降级：%s" % (outline.get("degraded_reason") or "编排者不可用"),
                              duration_s=None)
            store.update_run(run_id, expected_status="running", status="failed",
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

    # 分卷规划表（本书级、由章号确定性推导）：覆盖到本批末章即可。写成
    # 全书顶层字段，UI/报告/发布页直接读，不必重算。
    vol_plan = _book_volume_plan(task, outline, end)
    if vol_plan:
        store.update_run(run_id, volumes=vol_plan)

    def vol_block_for(chapter):
        """单章的卷上下文：写作时知道自己身处哪一卷、卷内第几章、是否卷末。
        不分卷返回 ""（提示词零噪音）。"""
        ent = volumes.find(vol_plan, chapter) if vol_plan else None
        if not ent:
            return ""
        pos, total = volumes.position(vol_plan, chapter)
        title = ("《%s》" % ent["title"]) if ent.get("title") else "（本卷待命名）"
        lines = ["- 本卷：第 %d 卷 %s" % (ent["vol"], title)]
        if total:
            lines.append("- 本卷位置：第 %d 章 / 共 %d 章" % (pos, total))
        else:
            lines.append("- 本卷位置：第 %d 章" % pos)
        if ent.get("arc"):
            lines.append("- 本卷弧光（本卷写作须服务这条主线）：%s" % ent["arc"])
        if total and pos == total:
            lines.append("- **本章是卷末章**：必须收束本卷主线冲突，写出本卷最高潮，"
                         "并在结尾留下牵出下一卷的大钩子（卷末是最重要的读者留存点）。")
        elif total and pos >= max(1, total - 1):
            lines.append("- 本章临近卷末：开始向本卷高潮收拢，不要再铺新的支线。")
        return "\n".join(lines) + "\n"

    chapter_scores = []          # [{chapter,title,means,passed,rounds,words}]
    issues_all = []

    # ---- 2) 逐章
    for k in range(1, n + 1):
        i = start + k - 1        # 全书章号：文件名/步骤角色/评分记录都按全书编号
        ch = outline["chapters"][k - 1]
        ch_file = "chapter-%02d.md" % i
        # 分卷上下文：评审时也带着「这是第几卷第几章、是不是卷末」——
        # 卷末章要按卷弧度收束，不符的章节不该靠全书评分才发现。
        _vpos, _vtotal = volumes.position(vol_plan, i) if vol_plan else (0, 0)
        vol_review_block = ""
        if vol_plan and _vpos:
            _vent = volumes.find(vol_plan, i)
            _vt = ("《%s》" % _vent["title"]) if (_vent and _vent.get("title")) else ""
            vol_review_block = (
                "本卷上下文：第 %d 卷%s，本章是卷内第 %d%s 章。%s\n"
                % (_vent["vol"], _vt, _vpos,
                   ("/%d" % _vtotal) if _vtotal else "",
                   "**卷末章**：须核对本卷主线是否收束、高潮是否到位、卷末钩子是否立住。"
                   if (_vtotal and _vpos == _vtotal)
                   else "核对本章是否服务于本卷主线弧光。"))
        # 逐项目标审稿（借鉴 AI-Novel-Writer）：本章大纲要点随评审下发，评审
        # 以 <event_check> 回逐项判定；资源账本（借鉴角色资源账本）以 <ledger>
        # 回本章道具/伤情/承诺/伏笔增量，评审达标后追加账本文件供下章注入。
        event_block = (
            "## 本章大纲核对（逐项目标审稿）\n"
            "本章按大纲应完成：\n- 剧情要点：%s\n- 章末钩子：%s\n"
            "评审时逐项判定「已完成 / 未完成 / 待核实」，判定必须引用正文证据"
            "（原文短句或位置），写在回复末尾的 <event_check> 块内（每项一行）。"
            "「铺垫了但没发生」不算已完成；未完成的项必须反映到对应维度评分。\n"
            "另在 <event_check> 块之后输出 <ledger> 块（没有新变化就整个省略）："
            "逐行列出本章新出现或状态变化的 道具/伤情/承诺/伏笔，格式："
            "类型|名称|现状（一句话）。\n%s\n"
            % (ch.get("beats") or "按大纲推进", ch.get("hook") or "留下悬念",
               vol_review_block))
        ev_check_lines = [[]]     # 每章重置：第一份非空评审的逐项判定
        ledger_lines = []         # 本章全部评审的账本增量并集
        ledger_txt = _read_ledger(workdir)   # 截至上一章的资源账本（起草注入）
        prev = ""
        draft_sid = ""  # §07 T1.1：本轮 draft/复用章的会话 id（revise 复用；reuse 时为空）
        if i > 1:
            tails = []
            for j in range(max(1, i - 2), i):
                t = _read_chapter(workdir, j)
                if t:
                    tails.append("（第 %d 章结尾）…%s" % (j, t[-260:].strip()))
            prev = "\n".join(tails) or "（无）"
            # findings 中期记忆（借鉴 agentmemory 持久记忆）：此前各章的关键事实
            # 追加在 .codebee/findings.md，注入时只取前一章之前的记录（当章发现
            # 会在当章评审后追加进来）。300+ 章长篇的前情只看近 2 章不够，
            # findings 填补中期记忆空洞。失败静默。
            try:
                fd_p = os.path.join(workdir, ".codebee", "findings.md")
                if os.path.isfile(fd_p):
                    fd_txt = _read_text_any_enc(fd_p)
                    if fd_txt:
                        # 截到 3000 字防无限膨胀；只在尾部追加时自动增长，注入头固定
                        prev += "\n\n## 此前章节发现摘要\n" + fd_txt[:3000]
            except Exception:
                pass

        # 评审-修订（每章至多 1 轮修订）
        rounds_used = 1
        means = {}

        # 逐项目标审稿 + 资源账本的每章聚合桶（run_critique 内解析填充；
        # event_check 取第一份非空，ledger 全评审增量求并）
        ev_check_lines = [[]]
        ledger_lines = []

        def run_critique(text, rnd, note_extra="", critic_sids=None, event_check=""):
            """一轮多维评审：返回 (cj_by_agent, scored)。变体赛马与主循环共用。
            event_check：本章大纲核对块（逐项目标审稿），随评审下发并回收标记块。

            多评审并发跑（2026-09-22）：评审占运行总时长的六成以上，而各评审之间
            互不依赖（同一份稿件、各自视角），串行等待纯属浪费——每位评审的墙上
            时间直接叠加。与「同章多稿赛马」「best-of 候选」同一套线程范式（那两处
            早已多线程调用 _run_step）。共享结构的写入全部收敛到 join 之后按
            critics 原顺序合并，保证与串行版逐字节同结果（ev_check 取第一份非空、
            issues/ledger 的先后顺序都不变），只在耗时上取并行收益。"""
            cj_map, sids = {}, dict(critic_sids or {})
            scored = 0   # 真正给出分数的评审数；失败/不可解析不得当成 0 分计入
            role = "critique-c%d" % i
            results = {}
            # 绑定解析提前到主线程：bind_agent 会把链首同步进 CLI 自家配置（写盘），
            # 多线程同时调它有写盘竞争。解析结果随线程参数传入，线程内只做调用。
            bound = [modelhub.bind_agent(a, difficulty) for a in critics]

            def _critique_one(idx, agent):
                """单评审执行体（线程内只做调用与解析，不改共享结构）。

                异常不吞：记进 results 由主线程按原顺序重抛——串行版的
                Cancelled/管道异常语义（上抛打断本 run）必须原样保留。"""
                try:
                    _critique_one_inner(idx, agent)
                except BaseException as exc:      # noqa: BLE001（含 Cancelled）
                    results[idx] = {"error": exc}

            def _critique_one_inner(idx, agent):
                if agent.get("mode") == "mock":
                    step, log_abs = store.add_step(run_id, role, agent["id"], agent.get("label"))
                    time.sleep(0.15)
                    cj = mocks.critique(agent["id"], rnd, dims, threshold_ch)
                    store.finish_step(run_id, step["n"], "done",
                                      summary="均分 %.1f：%s" % (
                                          sum(cj["scores"].values()) / max(1, len(dims)),
                                          cj["summary"]),
                                      duration_s=0.15)
                    results[idx] = {"agent": agent, "cj": cj, "evl": [],
                                    "ledger": [], "sid": None}
                    return
                lens = _critic_lens(critics, agent)
                res = _run_step(run_id, role, bound[idx],
                                crit_prompt_for(
                                    text,
                                    note=("小说第 %d 章" % i) + (
                                        "｜你的专属评审视角：%s（其他评审会覆盖其余视角，"
                                        "请深挖你的镜头，但所有维度仍需打分）" % lens)
                                    if lens else "",
                                    event_check=event_check) + note_extra,
                                workdir, readonly=True, ev=ev,
                                resume=sids.get(agent["id"]))
                results[idx] = {
                    "agent": agent, "cj": _critique_json(res, dims),
                    # 逐项目标审稿 + 资源账本：标记块先各存各的，join 后按序合并
                    "evl": _parse_tagged_lines(res.get("text"), "event_check"),
                    "ledger": _parse_tagged_lines(res.get("text"), "ledger"),
                    # §07 T1.1：记录该评审的会话 id（第 2 轮复用）
                    "sid": _resume_sid(agent, res.get("sid")),
                }

            threads = []
            for idx, agent in enumerate(critics):
                th = threading.Thread(target=_critique_one, args=(idx, agent),
                                      name="crit-%s-c%d-%d" % (run_id, i, idx), daemon=True)
                threads.append(th)
                th.start()
            for th in threads:
                th.join(3000)
            _check_cancel(ev)

            # join 后按原顺序合并（确定性：与串行版结果一致）
            for idx in range(len(critics)):
                r = results.get(idx)
                if r is None:
                    continue     # 该评审线程超时未回，按「没出分」处理
                if "error" in r:
                    raise r["error"]     # 还原串行版的异常上抛语义
                agent, cj = r["agent"], r["cj"]
                if cj.get("scores"):
                    scored += 1
                if r["evl"] and not ev_check_lines[0]:
                    ev_check_lines[0] = r["evl"]
                ledger_lines.extend(r["ledger"])
                if r["sid"]:
                    sids[agent["id"]] = r["sid"]
                cj_map[agent["id"]] = cj
                issues_all.extend({"chapter": i, **it} for it in (cj.get("issues") or [])[:6])

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
                                        note="小说第 %d 章" % i,
                                        event_check=event_check) + note_extra,
                                    workdir, readonly=True, ev=ev)
                    cj = runner.extract_json(res.get("text") or "")
                    if isinstance(cj, dict) and isinstance(cj.get("scores"), dict) \
                            and cj.get("scores"):
                        cj_map[spare["id"]] = cj
                        scored += 1
                        remember_agent(actual_critics, spare)
                        refresh_actual_route(
                            review_reason="章节评审补位：%s" %
                            (spare.get("label") or spare.get("id")))
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
        race_losers = []   # 赛马败稿文本（收卷前留存）：首轮修订时提炼败者精华

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
            sk_block, _ = skills.block_for(task, stable_order=True, run_id=run_id)
            if bible:
                sk_block = (sk_block + "\n\n" + bible) if sk_block else bible
            kb_block = knowledge.block_for(task)
            if kb_block:
                sk_block = (sk_block + "\n\n" + kb_block) if sk_block else kb_block
            scope = ("本章 = 大纲第 %d 章" % i) if start == 1 else (
                "本批为第 %d–%d 章，下列按全书章号列出各章要点" % (start, end))

            # 多线剧情推演（inkos 借鉴 2026-09-23）：serial.branches>=2 时
            # 写章前一次编排者调用生成 N 条分支节拍、自荐择优，选中分支替换
            # 本章 BEATS/HOOK（计划级赛马，省 prose 级开销）；失败静默走原大纲。
            branch_beats = branch_hook = None
            try:
                n_branch = max(1, min(3, int(serial.get("branches") or 1)))
                if n_branch >= 2:
                    got = branching.plan_branches(
                        run_id, task, i, task["goal"], outline_txt, prev, n_branch)
                    if got:
                        branch_beats, branch_hook = got
            except Exception:
                branch_beats = None

            def _draft_prompt(vfile):
                return (SERIAL_CHAPTER_PROMPT
                        .replace("__SKILLS__", sk_block)
                        .replace("__SCOPE__", scope)
                        .replace("__VOLUME__", vol_block_for(i))
                        .replace("__I__", str(i)).replace("__FILE__", vfile)
                        .replace("__GOAL__", task["goal"])
                        .replace("__OUTLINE__", outline_txt)
                        .replace("__PREV__", prev)
                        .replace("__LEDGER__", ledger_txt)
                        .replace("__TITLE__", ch["title"])
                        .replace("__BEATS__", branch_beats or ch["beats"] or "按大纲推进")
                        .replace("__HOOK__", branch_hook or ch.get("hook") or "留下悬念")
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
                chapter_impl = impl
                chapter_reason = route.get("author", "")
                last_attempt_impl = impl
                last_attempt_reason = chapter_reason
                prev_prompt = None      # 上一次实际下发的提示词
                prev_timed_out = False  # 上一次是否「超时/停滞」收场
                for draft_attempt in range(3):
                    if draft_attempt:
                        # 30s / 60s 退避；ev.wait 睡等可被取消即刻唤醒
                        if ev is not None:
                            if ev.wait(30 * draft_attempt):
                                break
                        else:
                            time.sleep(30 * draft_attempt)
                    if draft_attempt and len(prompt) > 12000 and sk_block and sk_block in prompt:
                        # 长提示词在容量受限通道（讯飞托管 35B 等）上会挂起/秒拒
                        # ——分层降级：经验库→4K、模块库按模块边界、圣经按二级标题
                        # 边界，保大纲/前情/本章要点（2026-09-17 七猫实测：全量
                        # 30KB 对讯飞必挂）
                        sk2, bible2, _note = _shrink_context_block(sk_block, bible, budget=12000)
                        use_prompt = prompt.replace(sk_block, sk2).replace(bible, bible2)
                    # 超时重试必须有新变量才值得做（2026-09-22）：提示词与上次逐字节
                    # 相同、上次又是超时/停滞收场时，重试只是把同一个超时再烧一遍
                    # （2400s × N）。此时直接跳出交替换将——换 CLI/模型才是新机会。
                    # 注意保留「缩上下文重试」：提示词真的变小了（缩块生效）就照试，
                    # 那是针对「提示词过大挂起」的有效降级。
                    if prev_timed_out and use_prompt == prev_prompt:
                        break
                    res = _run_step(run_id, "draft-c%d" % i, modelhub.bind_agent(impl, difficulty), use_prompt,
                                    step_wd, readonly=False, ev=ev, timeout=2400,
                                    resume=resume_ctx["session"] if resume_ctx else None,
                                    images=_task_images(task, workdir),
                                    note=("起草重试 %d/2（网关限流退避）" % draft_attempt) if draft_attempt else "")
                    prev_prompt = use_prompt
                    prev_timed_out = bool((res.get("raw") or {}).get("timed_out"))
                    if (res.get("raw") or {}).get("repeat_stop"):
                        break   # 重复守卫强制停止：同输入再试仍是死路，终态跳出
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
                        last_attempt_impl = other
                        last_attempt_reason = "章节起草换将：%s" % (
                            other_reason or other.get("label") or other.get("id"))
                        res = _run_step(run_id, "draft-c%d" % i,
                                        modelhub.bind_agent(other, difficulty), use_prompt,
                                        step_wd, readonly=False, ev=ev, timeout=2400,
                                        images=_task_images(task, workdir),
                                        note="起草换将 %s → %s：%s" % (
                                            impl["id"], other["id"],
                                            (other_reason or "")[:90]))
                        if (res.get("raw") or {}).get("repeat_stop"):
                            break   # 重复守卫判死：换将同 role 计数链必拦，终态跳出
                        good, txt = _chapter_state()
                        if not good and res["ok"] and _wc(res.get("text") or "") >= int(wpc * 0.6):
                            try:
                                _write_chapter(workdir, i, res["text"])
                                good, txt = _chapter_state()
                            except OSError:
                                pass
                        if good:
                            draft_sid = ""   # 换将作者无本任会话，revise 另起
                            chapter_impl = last_attempt_impl
                            chapter_reason = last_attempt_reason
                if not good:
                    time.sleep(3)   # 落盘竞态宽限：CLI 崩溃退出前写的文件可能晚于
                    good, txt = _chapter_state()   # 退出检查零点几秒才可见（c34 实测）
                    if good:
                        chapter_impl = last_attempt_impl
                        chapter_reason = last_attempt_reason
                if not good:
                    store.update_run(run_id, expected_status="running", status="failed",
                                     error="第 %d 章起草失败: %s" % (i, (res or {}).get("error")), ended_at=_now())
                    return
                remember_agent(actual_implementers, chapter_impl)
                refresh_actual_route(chapter_impl, implement_reason=chapter_reason)
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
                        # ev.wait 睡等可被取消即刻唤醒
                        if ev is not None:
                            if ev.wait(60):
                                break
                        else:
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
                    store.update_run(run_id, expected_status="running", status="failed",
                                     error="第 %d 章赛马全部变体起草失败" % i, ended_at=_now())
                    return
                scored_variants.sort(key=lambda v: (-v["avg"], v["variant"]))
                win = scored_variants[0]
                win_agent = next((a for a in pool if a.get("id") == win["agent"]), None)
                if win_agent is not None:
                    remember_agent(actual_implementers, win_agent)
                    refresh_actual_route(
                        win_agent,
                        implement_reason="同章多稿赛马胜出：%s" %
                        (win_agent.get("label") or win_agent.get("id")))
                # 收敛：赢家转正，败稿删除；胜者评审结果直接作为第 1 轮（不重评）
                if win["file"] != ch_file:
                    try:
                        os.replace(os.path.join(workdir, win["file"]),
                                   os.path.join(workdir, ch_file))
                    except OSError as e:
                        store.update_run(run_id, expected_status="running", status="failed",
                                         error="第 %d 章赛马收卷失败: %r" % (i, e), ended_at=_now())
                        return
                for v in scored_variants[1:]:
                    # 败者精华：删除前留存文本，供首轮修订提炼（懒调用）
                    try:
                        race_losers.append({
                            "variant": v["variant"],
                            "text": (_read_text_any_enc(os.path.join(workdir, v["file"])) or "")[:8000]})
                    except OSError:
                        pass
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
            _vent = volumes.find(vol_plan, i) if vol_plan else None
            if _vent:
                cs["vol"] = _vent["vol"]
                if _vent.get("title"):
                    cs["vol_title"] = _vent["title"]
            chapter_scores.append(cs)
            store.update_run(run_id, chapter_scores=chapter_scores)
            continue

        for rnd in (1, 2):
            text = _read_chapter(workdir, i)
            if rnd == 1 and race_cj is not None:
                cj_by_agent, scored = race_cj, race_scored
            else:
                cj_by_agent, scored, sids_now = run_critique(
                    text, rnd, critic_sids=critic_sids, event_check=event_block)
                critic_sids.update(sids_now)
            if not scored:
                # 「评不上」≠「评了 0 分」：全部评审失败时中止本轮，
                # 让自动续跑换个时机重试，而不是以 0 分误判章稿质量。
                store.update_run(run_id, expected_status="running", status="failed",
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
            if rnd == 1 and race_losers and impl.get("mode") == "real":
                # 赛马败者精华回收（连载版）：一次选择器调用提炼落选稿优点，
                # 与评审意见一并喂给首轮修订；失败静默不影响修订
                try:
                    cands = "### 胜者稿（已选定）\n\n%s" % _read_chapter(workdir, i)[:8000]
                    for li, lo in enumerate(race_losers, 1):
                        cands += "\n\n### 落选稿 %d\n\n%s" % (li, lo["text"])
                    sel_prompt = (BESTOF_SELECTOR_PROMPT
                                  .replace("__GOAL__", task["goal"])
                                  .replace("__CANDIDATES__", cands))
                    sel = _run_step(run_id, "race-select-c%d" % i,
                                    modelhub.bind_agent(critics[0] if critics else impl, difficulty),
                                    sel_prompt, step_wd, readonly=True, ev=ev,
                                    note="赛马败者精华提炼")
                    _selcj = runner.extract_json(sel.get("text") or "")
                    _imp = str((_selcj or {}).get("improvements") or "").strip()
                    if _imp:
                        crit_lines.append("- [赛马精华] 终审从落选候选稿提炼出值得吸收的优点：%s"
                                          % _imp[:400])
                except Exception:
                    pass
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
                          .replace("__VOLUME__", vol_block_for(i))
                          .replace("__CRITIQUE__", "\n".join(crit_lines))
                          .replace("__WORDS__", str(wpc)))
                prompt = attachments.append_task_context(prompt, task)
                _run_step(run_id, "revise-c%d" % i, modelhub.bind_agent(impl, difficulty), prompt,
                          step_wd, readonly=False, ev=ev, timeout=2400,
                          resume=resume_ctx["session"] if resume_ctx else draft_sid)
            remember_agent(actual_implementers, impl)
            refresh_actual_route(
                impl, implement_reason="章节修订：%s" %
                (impl.get("label") or impl.get("id")))
            # 正史重写后旧推演标过期（inkos 借鉴）：修订改写了章稿，本章的
            # 分支计划不再反映当前正史——审计标记防误读。失败静默。
            try:
                branching.mark_stale(workdir, i)
            except Exception:
                pass
            _check_cancel(ev)
        cs_new = {"chapter": i, "title": ch["title"], "means": means,
                  "passed": bool(means) and all(v >= threshold_ch for v in means.values()),
                  "rounds": rounds_used,
                  "words": _wc(_read_chapter(workdir, i))}
        _vent = volumes.find(vol_plan, i) if vol_plan else None
        if _vent:
            cs_new["vol"] = _vent["vol"]                     # 章节卡按卷分组用
            if _vent.get("title"):
                cs_new["vol_title"] = _vent["title"]
        if ev_check_lines[0]:
            cs_new["event_check"] = ev_check_lines[0][:8]   # 逐项目标审稿结果
        chapter_scores.append(cs_new)
        # 每章即时持久化：长篇中断/超时后可断点续跑，不丢已完成章的分数
        store.update_run(run_id, chapter_scores=chapter_scores)
        # 资源账本（借鉴角色资源账本）：评审提出的道具/伤情/承诺/伏笔增量
        # 追加到 .codebee/resource-ledger.md，下一章起草时注入，防跨章穿帮。
        # 失败静默——账本是增强不是硬依赖。
        if ledger_lines:
            try:
                _append_ledger(workdir, i, ledger_lines)
            except Exception:
                pass
        # findings 沉淀（借鉴 agentmemory 持久记忆）：章节标题+要点追加到
        # .codebee/findings.md——后续章节起草时随圣经/模块库注入，弥补
        # 前情提要只看近 2 章结尾的中期记忆空洞。失败静默。
        try:
            fd_p = os.path.join(workdir, ".codebee", "findings.md")
            os.makedirs(os.path.dirname(fd_p), exist_ok=True)
            header_needed = not os.path.isfile(fd_p)
            with open(fd_p, "a", encoding="utf-8") as f:
                if header_needed:
                    f.write("# 章节发现（每章评审达标后自动追加，供后续章节参考）\n\n")
                f.write("- 第%d章《%s》：%s（%d 字，%s）\n" % (
                    i, ch["title"],
                    ch.get("beats") or "按大纲推进", chapter_scores[-1]["words"],
                    "%.1f 分" % means.get("情节", 0.0) if means else "无评分"))
        except Exception:
            pass

    # ---- 3) 全局一致性评审（覆盖 1..end 全书：续写批次必须连同旧章一起查一致性）
    full_text = "\n\n".join(_read_chapter(workdir, i) for i in range(1, end + 1))
    global_issues = []

    def run_global_round(agent_list):
        """一轮全局评审：返回 (出分评审数, 按维累计分)。失败/不可解析不得当成低分计入。

        多评审并发（2026-09-22，同章级评审并发）：各自读同一份全书文本、互不依赖，
        串行只是把等待时间叠起来。共享结构仍在 join 后按原顺序合并，结果与串行一致。"""
        gmeans_acc, scored = {}, 0
        results = {}
        # 绑定解析提前到主线程（线程内不做会写盘的 bind_agent）
        bound = [modelhub.bind_agent(a, difficulty) for a in agent_list]

        def _global_one(idx, agent):
            """异常不吞：记进 results 由主线程按原顺序重抛（保留串行语义）。"""
            try:
                _global_one_inner(idx, agent)
            except BaseException as exc:      # noqa: BLE001（含 Cancelled）
                results[idx] = {"error": exc}

        def _global_one_inner(idx, agent):
            if agent.get("mode") == "mock":
                step, _ = store.add_step(run_id, "global-critique", agent["id"],
                                         agent.get("label"))
                time.sleep(0.15)
                gj = {"scores": {d: 8.0 for d in dims},
                      "issues": [], "summary": "（mock）全书结构完整，达到可签约水平"}
                store.finish_step(run_id, step["n"], "done", summary="均分 8.0：（mock）全书达标",
                                  duration_s=0.15)
                results[idx] = gj
                return
            gtpl = SERIAL_GLOBAL_PROMPT
            if bible:
                gtpl = gtpl.replace("## 全书目标", bible + "\n\n## 全书目标", 1)
            res = _run_step(run_id, "global-critique", bound[idx],
                            (gtpl.replace("__DIMKEYS__", dimkey)
                             .replace("__GOAL__", task["goal"])
                             .replace("__MANUSCRIPT__", full_text[:60000])),
                            workdir, readonly=True, ev=ev, timeout=2400)
            results[idx] = _critique_json(res, dims)

        threads = []
        for idx, agent in enumerate(agent_list):
            th = threading.Thread(target=_global_one, args=(idx, agent),
                                  name="gcrit-%s-%d" % (run_id, idx), daemon=True)
            threads.append(th)
            th.start()
        for th in threads:
            th.join(3000)
        _check_cancel(ev)

        for idx in range(len(agent_list)):
            gj = results.get(idx)
            if gj is None:
                continue
            if "error" in gj:
                raise gj["error"]     # 还原串行版的异常上抛语义
            if gj.get("scores"):
                scored += 1
            global_issues.extend({"chapter": "全书", **it} for it in (gj.get("issues") or [])[:8])
            for d in dims:
                v = gj.get("scores", {}).get(d)
                if v is not None:
                    gmeans_acc.setdefault(d, []).append(float(v))
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
                remember_agent(actual_critics, spare)
                refresh_actual_route(
                    review_reason="全局评审补位：%s" %
                    (spare.get("label") or spare.get("id")))
                break
        if not gscored:
            store.update_run(run_id, expected_status="running", status="failed",
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
                          .replace("__VOLUME__", vol_block_for(i))
                          .replace("__CRITIQUE__", crit)
                          .replace("__WORDS__", str(wpc)))
                prompt = attachments.append_task_context(prompt, task)
                res = _run_step(run_id, "polish-c%d" % i, modelhub.bind_agent(impl, difficulty),
                                prompt, workdir, readonly=False, ev=ev, timeout=2400,
                                resume=resume_ctx["session"] if resume_ctx else None)
                if not res["ok"]:
                    continue
            remember_agent(actual_implementers, impl)
            refresh_actual_route(
                impl, implement_reason="全局打磨：%s" %
                (impl.get("label") or impl.get("id")))
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
        if not fixed:
            # 最弱章重改全部失败（供应商拥堵/流断等）→ 章稿没有任何变化，
            # 继续跑全书重评只会白烧评审链，组长步骤一挂「工作中」就是几十
            # 分钟（2026-09-19 实案：polish-c18/c11 双败后仍进全书重评，
            # 单个评审 35 分钟，用户侧只见打磨 2/3 久卡不动）。直接收尾。
            store.finish_step(run_id, pstep["n"], "failed",
                              summary="重改未成功（%s 全部失败），本轮打磨中止；"
                                      "已落盘章稿不受影响"
                                      % "、".join("第 %d 章" % c["chapter"] for c in weak))
            break
        # 重评全书一致性（同一评审闭包；本轮全挂则保留上一轮结论——评审链挂了
        # 不代表书变差，不能拿「无法评审」覆盖真实分数）
        try:
            full_text = "\n\n".join(_read_chapter(workdir, i2) for i2 in range(1, end + 1))
            gscored2, gmeans_acc2 = run_global_round(critics)
        except BaseException:
            store.finish_step(run_id, pstep["n"], "failed",
                              summary="打磨后全书重评异常中止，已落盘章稿不受影响")
            raise
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
    # 分卷标题：卷首章之前插「第 X 卷 《卷名》」分隔（全书 1..end 都插，续写
    # 批次合并时上一批的卷标题也一并补上，不会只在首批出现）
    vol_heads = {}
    if vol_plan:
        for _ent in vol_plan:
            if _ent["first"] <= end:
                vol_heads[_ent["first"]] = _ent
    for i in range(1, end + 1):     # 合并全书：续写时包含上一批已写好的章
        head = vol_heads.get(i)
        if head:
            vol_title = ("第 %d 卷 《%s》" % (head["vol"], head["title"])) \
                if head.get("title") else ("第 %d 卷" % head["vol"])
            parts.append("## %s" % vol_title)
            parts.append("")
        parts.append(_read_chapter(workdir, i).strip())
        parts.append("")
    with _ms_io(workdir, ms_name, "w") as f:
        f.write("\n".join(parts))
    total_words = _wc("\n".join(parts))
    store.finish_step(run_id, step["n"], "done",
                      summary="已合并 %d 章为 %s（约 %d 字%s）" % (
                          n, ms_name, total_words,
                          "，分 %d 卷" % len(vol_heads) if vol_heads else ""),
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
    if vol_plan:
        verdict["volumes"] = vol_plan
    scope_txt = ("续写第 %d–%d 章，衔接前文 %d 章" % (start, end, start - 1)) if start > 1 \
        else ("共 %d 章" % n)
    lines = ["# 连载小说评审报告：%s" % task["title"], "",
             "- 书名：%s（%s / 约 %d 字，合并为 `%s`）" % (
                 book_title, scope_txt, total_words, ms_name),
             "- 分卷：%s" % ("；".join(
                 ("第 %d 卷《%s》第 %d–%d 章" % (v["vol"], v.get("title") or "未命名",
                                                v["first"],
                                                v["last"] if v["last"] is not None else end))
                 for v in vol_heads.values()) if vol_heads
                 else ("未分卷（可设置「每卷章数」，或在目标里写明卷结构）")),
             "- 结论：**%s**（各章门禁 %s / 全局评审 %s）" % (
                 "✅ 达到发布标准" if publishable else "❌ 未达标",
                 "通过" if chapters_pass else "未通过",
                 "通过" if global_pass else "未通过"),
             "- 编排模式：%s　作者：%s　评审组：%s" % (
                 {"auto": "自动", "fast": "快速", "expert": "专家",
                  "manual": "手动"}.get(mode, mode),
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
    _write_task_evidence(run_id, task, workdir, _evidence_lines_from_run(run_id, task))
    store.update_run(run_id, expected_status="running", status="done", verdict=verdict,
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
    if mode == "fast":
        difficulty = "easy"
    elif mode == "expert":
        difficulty = "hard"
    workflow = task_compile.content_workflow(task, difficulty, mode=mode)

    # ---- 路由
    if resume_ctx is not None:
        impl = resume_ctx["agent"]
        route["author"] = resume_ctx["note"]
    elif mode == "manual":
        impl, _ = _pick_implementer(agents, task.get("implementer"))
        critics = _pick_critics_manual(agents, task)
    else:
        task_type = task.get("type") or "novel"
        impl, route["author"] = router.pick(agents, "implement", task_type, stats)
        critics, route["critics"] = router.pick_critics(agents, task_type, stats, impl=impl)
    if impl is None:
        store.update_run(run_id, expected_status="running", status="failed",
                         error="没有可用智能体", ended_at=_now())
        return
    if resume_ctx is not None:
        if mode != "manual":
            critics, route["critics"] = router.pick_critics(
                agents, task.get("type") or "novel", stats, impl=impl)
        else:
            critics = _pick_critics_manual(agents, task)

    critic_pool = list(critics)
    if mode != "manual":
        critics = critic_pool[:workflow["reviewers"]]

    _record_actual_route(run_id, task, agents, stats, impl, critics=critics,
                         implement_reason=route.get("author", ""),
                         review_reason=route.get("critics", ""))

    # ---- 规划（小说为模板计划）
    _wait_gate(run_id, ev)
    plan = planner.make_novel_plan(_steered_task(run_id, task), impl, critics)
    store.update_run(run_id, plan=plan, route=route, difficulty=difficulty,
                     workflow=workflow)

    ms_path = os.path.join(workdir, ms_name)

    def write_ms(text):
        with _ms_io(workdir, ms_name, "w") as f:
            f.write(text)

    def read_ms():
        try:
            return _read_text_any_enc(ms_path)
        except Exception:
            return ""

    draft_note = route.get("author", "") if mode != "manual" else ""

    # 编排者大纲：只对真实执行有意义；失败静默退回无大纲（喂入带指令的任务副本）
    outline = (_planner_call(planner.make_review_outline,
                             _steered_task(run_id, task),
                             deadline=_run_deadline(run_id))
               if workflow["outline"] and impl.get("mode") != "mock" else None)
    _ensure_budget(run_id)
    if outline:
        workflow = task_compile.content_workflow(
            task, difficulty, plan=outline, mode=mode)
        if mode != "manual":
            critics = critic_pool[:workflow["reviewers"]]
        store.update_run(run_id, outline=outline, workflow=workflow)
        _record_actual_route(run_id, task, agents, stats, impl, critics=critics,
                             implement_reason=route.get("author", ""),
                             review_reason=route.get("critics", ""))
    rounds = workflow["review_rounds"]

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
        is_research = task.get("type") == "research"

        def _draft_prompt_for(vfile):
            p = (_tpl(task, "draft_prompt", NOVEL_DRAFT_PROMPT).replace("__FILE__", vfile)
                 .replace("__ROLE__", _content_role(task))
                 .replace("__GOAL__", task["goal"])
                 .replace("__CONTEXT__", task.get("context") or "（无）")
                 .replace("__RUBRIC__", "、".join(dims) if dims else "（按流程默认维度）"))
            if outline:
                p += "\n\n## 编排者大纲（按要点组织稿件）\n" + \
                     "\n".join("- " + i for i in outline["items"])
            if is_research:
                # 调研报告追加证据链要求（gpt-researcher 借鉴）
                p += RESEARCH_APPENDIX
            if task.get("type") == "weekly_report":
                # 禅道本周素材注入（Weekly Report Generator 借鉴：从工作系统
                # 取数入素材）。尽力而为：未配置/不可达静默为空，绝不阻塞起草。
                try:
                    from . import zentao as _zt
                    brief = _zt.weekly_brief()
                except Exception:
                    brief = ""
                if brief:
                    p += ("\n\n## 禅道本周工作素材（如实取材，缺失数字留待补，"
                          "勿虚构业绩）\n" + brief)
            p += _content_contract(task)
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
            draft_res, actual_impl, fallback_note = _retry_content_draft_with_cli(
                run_id, task, agents, impl, difficulty, mode, stats,
                _draft_prompt_for(ms_name), workdir, step_wd, ev, resume_ctx,
                draft_note, draft_res)
            if actual_impl.get("id") != impl.get("id"):
                impl = actual_impl
                draft_note = fallback_note
                route["author"] = fallback_note
                if mode != "manual":
                    critic_pool, route["critics"] = router.pick_critics(
                        agents, task.get("type") or "novel", stats, impl=impl)
                    critics = critic_pool[:workflow["reviewers"]]
                plan = planner.make_novel_plan(_steered_task(run_id, task), impl, critics)
                store.update_run(run_id, route=route, plan=plan)
                _record_actual_route(
                    run_id, task, agents, stats, impl, critics=critics,
                    implement_reason=route.get("author", ""),
                    review_reason=route.get("critics", ""))
            if draft_res.get("raw", {}).get("cancelled"):
                _check_cancel(ev)
        if not draft_res["ok"]:
            store.update_run(run_id, expected_status="running", status="failed",
                             error="起草失败: %s" % draft_res.get("error"),
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
        if task.get("context"):
            crit_prompt += "\n\n## 原始任务背景与附件参考\n" + task["context"]
        # AI 味确定性检测（借鉴 oh-story 去AI味）：客观参考线随评审下发，
        # 命中才追加——评审官结合上下文判断是否真问题，脚本不直接扣分
        crit_prompt = aiflavor.inject_into_prompt(crit_prompt, manuscript, task.get("type"))
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
                      .replace("__ROLE__", _content_role(task))
                      .replace("__GOAL__", task["goal"])
                      .replace("__CRITIQUE__", "\n".join(crit_lines)))
            prompt += _content_contract(task)
            prompt = attachments.append_task_context(prompt, task)
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
        "workflow": workflow,
    }

    # 3) 报告
    lines = ["# 评审报告：%s" % task["title"], "",
             "- 任务类型：%s" % task["type"],
             "- 结论：**%s**（综合 %.1f / 阈值 %.1f，%d 轮评审）"
             % ("✅ 达到发布标准" if publishable else "❌ 未达标，建议再修",
                overall, threshold, verdict["rounds_used"]),
             "- 编排模式：%s　起草/修订：%s　评审组：%s" % (
                 {"auto": "自动", "fast": "快速", "expert": "专家",
                  "manual": "手动"}.get(mode, mode),
                 impl.get("label"), "、".join(a.get("label") for a in critics)),
             "- 动态步骤：大纲 %s；评审 %d 人；最多 %d 轮（%s）" % (
                 "启用" if workflow["outline"] else "省略",
                 len(critics), rounds, workflow["reason"])]
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
    _write_task_evidence(run_id, task, workdir, _evidence_lines_from_run(run_id, task))
    store.update_run(run_id, expected_status="running", status="done", verdict=verdict,
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
            store.update_run(run_id, expected_status="running", status="done", ended_at=_now(),
                             verdict={"qa": True,
                                      "answered_by": agent.get("id")})
            return
        errors.append("%s：%s" % (agent.get("id"),
                                  (res.get("error") or "无输出")[:120]))
    store.update_run(run_id, expected_status="running", status="failed", ended_at=_now(),
                     error="答疑失败（执行/评审链不可用）——" + "；".join(errors[-3:]))


def _read_constitution(workdir):
    """任务宪章（借鉴 spec-kit constitution）：工作目录 .codebee/constitution.md
    （作者手工维护的质量原则——代码规范/文风/测试要求）。存在且非空时返回注入块，
    每次 run 的所有智能体提示词都会带上；否则返回 ""（零配置零噪音）。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), ".codebee", "constitution.md"))
    if not _inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        txt = _read_text_any_enc(p)[:6000].strip()
    except OSError:
        return ""
    if not txt:
        return ""
    return ("## 项目宪章（constitution.md：本项目一切产出的质量原则，优先级最高，"
            "与其他要求冲突时以宪章为准）\n\n" + txt + "\n\n")


def _write_project_memory(task, workdir, lines):
    """项目记忆持久化（借鉴 agentmemory）：代码任务成功后把架构事实追加到
    .codebee/project-memory.md——同目录后续 code 任务规划前自动注入，
    让编排者「知道这个代码库的脾气」而非每次从零摸索。失败静默。"""
    if not lines:
        return ""
    try:
        pm = os.path.join(workdir, ".codebee", "project-memory.md")
        os.makedirs(os.path.dirname(pm), exist_ok=True)
        header_needed = not os.path.isfile(pm)
        with open(pm, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("# 项目记忆（每次代码任务完成后自动追加，供后续任务参考）\n\n")
            f.write("### %s · %s\n" % (task.get("title") or "", _now()))
            for ln in lines:
                f.write("- %s\n" % str(ln)[:300])
            f.write("\n")
        return pm
    except Exception:
        return ""


def _read_project_memory(workdir, cap=4000):
    """读取项目记忆供规划提示词注入。超出上限截断到最新条目。"""
    p = os.path.join(workdir or "", ".codebee", "project-memory.md")
    if not _inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        txt = _read_text_any_enc(p)[:cap].strip()
    except OSError:
        return ""
    if not txt:
        return ""
    return ("## 项目记忆（此前代码任务在此工作目录留下的架构事实，"
            "规划时优先参考）\n\n" + txt + "\n\n")


def _write_task_spec(task, workdir):
    """任务规格落盘 .codebee/spec.md（借鉴 agent-orchestrator 的 .spec/PROMPT.md 与
    planning-with-files 的文件化计划）：任务定义随工作目录留存、随任务分支版本化，
    追话/复盘/续跑时可见原始意图。失败静默返回空串——规格文件永远不能挡住任务执行。"""
    try:
        from pathlib import Path as _P
        root = _P(workdir).resolve()
        target = (root / ".codebee" / "spec.md").resolve()
        if root not in target.parents:   # 守卫：spec 必须落在工作目录内（../、symlink 出逃弃写）
            return ""
        os.makedirs(str(target.parent), exist_ok=True)
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
        if not str(target).startswith(str(root) + os.sep):   # sink 侧复检：路径必须仍在工作目录内
            return ""
        with open(str(target), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return str(target)
    except Exception:
        return ""


def _write_task_evidence(run_id, task, workdir, summary_lines):
    """验证证据持久化（借鉴 gsd-pi 的 validation evidence）：run 收尾把确定性
    证据追加进 .codebee/evidence.md——验证命令结果、评审分数、AI 味检测等。
    与 spec.md（任务意图）呼应成「任务档案」；失败静默，绝不挡收尾。"""
    if not summary_lines:
        return ""
    try:
        spec_dir = os.path.join(workdir, ".codebee")
        os.makedirs(spec_dir, exist_ok=True)
        path = os.path.join(spec_dir, "evidence.md")
        header_needed = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("# 验证证据（每次运行追加一节）\n\n")
            f.write("## %s · run %s\n\n" % (_now(), run_id))
            for ln in summary_lines:
                f.write("- %s\n" % str(ln)[:300])
            f.write("\n")
        _archive_stamp(run_id, task, workdir, summary_lines)
        return path
    except Exception:
        return ""


def _archive_stamp(run_id, task, workdir, summary_lines):
    """归档戳（借鉴 OpenSpec archive）：evidence 落盘后向 spec.md 尾部追加
    「已完成」快照行——spec 从「意图」升格为「意图+交付记录」的活档案。
    失败静默。"""
    try:
        spec = os.path.join(workdir, ".codebee", "spec.md")
        if not os.path.isfile(spec):
            return
        with open(spec, "a", encoding="utf-8") as f:
            f.write("\n---\n**✅ 已交付** · %s · run %s\n%s\n" % (
                _now(), run_id,
                "\n".join("- %s" % str(ln)[:160] for ln in summary_lines[:5])))
    except Exception:
        return


def _evidence_lines_from_run(run_id, task):
    """从 run 步骤与 verdict 提取证据行（确定性事实，不抄模型输出）。"""
    run = store.get_run(run_id) or {}
    lines = []
    for s in run.get("steps") or []:
        role = str(s.get("role") or "")
        if role == "verify":
            lines.append("验证命令 `%s` → %s%s" % (
                task.get("verify_command") or "", s.get("status"),
                "（%s）" % s.get("summary") if s.get("summary") else ""))
    verdict = run.get("verdict") or {}
    if verdict:
        means = verdict.get("scores") or {}
        if means:
            lines.append("评审均分：%s（阈值 %s，%s）" % (
                "、".join("%s %.1f" % (d, v) for d, v in means.items()),
                verdict.get("threshold"),
                "达标" if verdict.get("publishable") else "未达标"))
        if verdict.get("overall") is not None:
            lines.append("综合分 %.1f / %d 轮" % (verdict.get("overall") or 0.0,
                                                  verdict.get("rounds_used") or 0))
        bestof = run.get("bestof")
        if bestof:
            lines.append("赛马：%s（胜者 %s）" % (
                bestof.get("kind") or "内容候选",
                bestof.get("pick", bestof.get("winner_files", "?"))))
    return lines


def _write_task_plan(task, workdir, plan):
    """计划落盘 .codebee/task_plan.md（planning-with-files 精髓：计划活在磁盘上，
    /clear、压缩、崩溃、续跑都不丢）。与 spec.md/evidence.md 同居任务档案；
    失败静默——计划文件永远不能挡住任务执行。"""
    try:
        from pathlib import Path as _P
        steps = (plan or {}).get("steps") or []
        if not steps:
            return ""   # 无步骤不产空计划文件
        root = _P(workdir).resolve()
        target = (root / ".codebee" / "task_plan.md").resolve()
        if root not in target.parents:
            return ""
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# 任务计划", "", "来源：%s" % (plan or {}).get("source", "?"), ""]
        for i, s in enumerate((plan or {}).get("steps") or [], 1):
            lines.append("%d. %s" % (i, str(s.get("detail") or s.get("title") or "")[:200]))
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(target)
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
    # 生产 enqueue 已完成 queued→running 认领；测试/兼容调用也可能直接从
    # queued 进入。按初读状态做 CAS 起跑确认：取消若恰好落在读取之后，当前
    # 状态已是 cancelled，写入会失败并立即退出，不产生 Git/文件副作用。
    initial_status = run.get("status")
    if initial_status not in ("queued", "running"):
        return
    if store.update_run(run_id, expected_status=initial_status, status="running",
                        started_at=_now()) is None:
        return
    if task is None:
        store.update_run(run_id, expected_status="running", status="failed",
                         error="找不到任务 %s" % run.get("task_id"), ended_at=_now())
        return
    try:
        _ensure_budget(run_id)
    except TaskTimeout:
        return
    # 统一任务编译：旧字段继续供各引擎读取，规格作为运行级诊断与调度输入落盘。
    task_spec = task_compile.compile_task(task)
    store.update_run(run_id, task_spec=task_spec,
                     task_spec_summary=task_compile.summary(task_spec),
                     difficulty=task_spec["difficulty"])
    task = dict(task)
    task = attachments.refresh_task(task, task.get("workdir") or "")
    task["_compiled_spec"] = task_spec
    # 运行内统一使用编译后的难度；store 中历史任务常带 difficulty=auto，
    # 不能让这个兼容值覆盖 easy/default/hard 的模型调度决策。
    task["difficulty"] = task_spec["difficulty"]
    # 同理，历史任务可能保存非法/过期 engine；执行以编译后的流程引擎为准。
    task["engine"] = task_spec["engine"]
    # 同工作目录并行检测：任务计划(.codebee/task_plan.md)、项目记忆与任务分支
    # 检出都是工作目录级共享状态，两任务同目录并行会互相覆盖/干扰（2026-09-23
    # 禅道双单实案：A 的评审 diff 混进 B 的任务计划）。只留警告不阻断——
    # 确有把握互不冲突的用户可以忽略。
    try:
        _wd = (task.get("workdir") or "").rstrip("/\\")
        _others = [t for t in store.list_tasks(200)
                   if t.get("id") != task.get("id")
                   and (t.get("workdir") or "").rstrip("/\\") == _wd
                   and t.get("status") in ("running", "queued") and _wd]
        if _others:
            _names = "、".join((t.get("title") or t.get("id") or "")[:24]
                               for t in _others[:3])
            store.update_run(run_id, warnings=[
                "工作目录与运行中任务并行：%s——任务计划/项目记忆/分支检出会互相干扰，建议错开或使用独立工作目录" % _names])
    except Exception:
        pass
    # 代码版本检出：任务指定了基线版本时，先检出任务分支 tutti/<task-id> 再跑流水线。
    # 显式意图不容静默降级——仓库缺失/脏工作区/引用不存在一律中止运行并报错，
    # 绝不带着用户未提交改动切分支、也不悄悄退回当前 HEAD。
    git_ctx = None
    if task.get("git_rev"):
        from . import gitmod
        ok, err, gitinfo = gitmod.prepare_checkout(
            task["workdir"], task["git_rev"], task["id"])
        if not ok:
            store.update_run(run_id, expected_status="running", status="failed",
                             error="代码版本检出失败：%s" % err, ended_at=_now())
            return
        git_ctx = gitinfo
        store.update_run(run_id, git=gitinfo)
        # 任务分支裁决状态：新一轮 run 产生新分支内容，重置回「待裁决」
        store.set_task_git_state(task["id"], "isolated")
    # 任务规格文件化（借鉴 planning-with-files/agent-orchestrator）：任何任务都在
    # 工作目录留一份 .codebee/spec.md——原始意图可见、随任务分支版本化
    _write_task_spec(task, task["workdir"])
    # 任务宪章（借鉴 spec-kit constitution）：工作目录 .codebee/constitution.md
    # 是作者定下的质量原则（代码规范/文风/测试要求），每次 run 注入所有智能体
    # 提示词——代码/文章/翻译全类型通用，一次定义持续生效。缺省零噪音。
    global _RUN_CONSTITUTION
    _RUN_CONSTITUTION = _read_constitution(task["workdir"])
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
    # 运行级任务画像：所有后续 bind_agent 调用共享同一预置类型，
    # 模型级联因此覆盖 direct/code/review/serial/translation 等全部引擎。
    for _agent in agents:
        if isinstance(_agent, dict):
            _agent["_dispatch_task_type"] = task.get("type") or "direct"
            _agent["_thinking"] = task.get("thinking") or "auto"
    store.update_run(run_id, route_plan={
        "task": task_spec,
        "implement": router.route_plan(agents, "implement", task_spec, stats),
        "review": router.route_plan(agents, "review", task_spec, stats),
    })
    mode = task.get("mode") or ("manual" if task.get("implementer") else "auto")
    store.update_run(run_id, mode=mode)
    # 任务生命周期钩子（task_start，2026-09-22）：stdout 注入文本并入任务上下文
    # ——context 在全引擎提示词都有 __CONTEXT__ 占位，一处并入全覆盖
    try:
        hook_txt = hooks.run_event("task_start",
                                   ctx={"title": task.get("title") or "",
                                        "type": task.get("type") or "",
                                        "mode": mode, "goal": task.get("goal") or ""},
                                   task_id=task.get("id") or "", run_id=run_id)
        if hook_txt:
            task["context"] = ((task.get("context") or "") + "\n\n" +
                               t("## 项目钩子注入（task_start）\n") + hook_txt).strip()
            store.update_run(run_id, hook_inject=hook_txt[:400])
    except Exception:
        pass   # 外部钩子故障绝不挡主流程
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
                store.update_run(run_id, expected_status="running", status="failed",
                                 error="没有可用智能体", ended_at=_now())
                return
            if resume_ctx is not None:
                if mode == "auto":
                    critics, route["critics"] = router.pick_critics(
                        agents, task["type"], stats, impl=impl)
                else:
                    critics = _pick_critics_manual(agents, task)
            if task.get("serial"):
                _run_serial_review(run, task, agents, ev, stats, mode,
                                   critics, impl, route, resume_ctx, difficulty)
            else:
                _run_content_review(run, task, agents, ev, stats, mode)
    except TaskTimeout:
        store.update_run(run_id, expected_status="running",
                         status="timeout", ended_at=_now(), error="任务总时限已到")
    except Cancelled:
        store.update_run(run_id, expected_status="running",
                         status="cancelled", ended_at=_now())
    except Exception as e:
        import traceback
        store.update_run(run_id, expected_status="running", status="failed",
                         error=repr(e)[:500], ended_at=_now())
        try:
            err_path = store.run_dir(run_id) / "error.log"
            if _inside(str(store.run_dir(run_id).parent), str(err_path)):
                err_path.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
    finally:
        # 全类型统一写调度终态与质量反馈。调用成功只代表传输可靠；真正用于
        # 在线推荐的成功率以 verify/review/publishable 等验收结果为准。
        try:
            final_run = store.get_run(run_id) or {}
            final_status = final_run.get("status") or ""
            if final_status in ("done", "failed"):
                final_verdict = final_run.get("verdict") or {}
                if "pass" in final_verdict:
                    quality_ok = bool(final_verdict.get("pass"))
                elif "publishable" in final_verdict:
                    quality_ok = bool(final_verdict.get("publishable"))
                else:
                    quality_ok = final_status == "done"
                _record_dispatch_completed(
                    run_id, task, "passed" if quality_ok else "failed",
                    verify_pass=final_verdict.get("verify_pass"),
                    review_pass=final_verdict.get(
                        "review_pass", final_verdict.get("publishable")))
                final_agent = (((final_run.get("route_plan") or {})
                                .get("implement") or {}).get("selected") or "")
                if final_agent.startswith("builtin:"):
                    final_agent = "builtin"
                usage.record_quality_for_run(run_id, quality_ok, agent=final_agent)
            elif final_status == "cancelled":
                _record_dispatch_completed(run_id, task, "cancelled")
        except Exception:
            pass
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
        # 知识库闭环：从产出材料提炼可复用知识条目（草稿态，人工转正后参与注入）
        try:
            knowledge.learn_async(run_id)
        except Exception:
            pass
