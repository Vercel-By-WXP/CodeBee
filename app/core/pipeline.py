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

from . import catalog, history, jobs, manager, modelhub, mocks, planner, registry, router, runner, skills, store, usage
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


def _run_step(run_id, role, agent, prompt, workdir, readonly, ev, timeout=runner.DEFAULT_TIMEOUT, note="", resume=None, images=None, require_tools=False):
    """执行一个智能体步骤并记录。返回 runner 统一结果。"""
    _wait_gate(run_id, ev)
    # 绑定解析为空 → CLI 将回落本机默认配置（用户配置的模型/供应商全部不生效）。
    # 2026-09-16 实测：这种状态下烧干配额的本机默认供应商被静默使用，用户以为
    # 在用自己配的模型。首次出现时在步骤备注里醒目标出。
    if agent.get("mode") == "real" and not (agent.get("call_chain") or agent.get("env")):
        note = ((note + "；") if note else "") + \
               "⚠ 未解析到绑定链，本步回落 CLI 本机默认配置（请在模型接入页检查该 CLI 的供应商绑定）"
    step, log_abs = store.add_step(run_id, role, agent["id"],
                                   agent.get("label", agent["id"]), note=note)
    start = time.time()
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


def _run_builtin_step(run_id, role, bi, prompt, workdir, ev, note="", images=None):
    """内置智能体步骤：直连模型 API + 工具循环（builtin_agent），不经 CLI 进程。

    与 _run_step 对齐的三件事：暂停/取消闸门、运行中指令 drain 注入、重复调用
    守门；结果同样经 _finish_step_result 落步骤（output=干净回答）并入用量台账。
    日志只有「迭代/工具」摘要行——对话视图吃 output，日志抽屉看工具轨迹。"""
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
                      output=(res.get("text") or ""))
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
- 回复的最后一行单独输出一行交代结果：
DIRECT_DONE: <一句话说明本轮做了什么、产出了哪些文件>
这一行之后不要再输出任何内容。"""

DIRECT_FOLLOWUP_PROMPT = """你在与用户的持续对话中。用户针对已有成果发来了新消息（见下方「用户实时指令」注入块），请接着处理。

## 原始任务
__GOAL__

## 要求
- 优先回应用户新消息（继续做/改/答疑均可），仍限本工作目录内。
- 回复的最后一行单独输出：
DIRECT_DONE: <一句话说明本轮做了什么>
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
- 完成后直接给用户一段简短说明：做了什么、产出/修改了哪些文件。"""

BUILTIN_FOLLOWUP_PROMPT = """## 原始任务
__GOAL__

## 上一轮输出（结尾）
__PREV__

## 要求
- 优先回应用户的新消息（继续做/改/答疑均可），仍限本工作目录内，工具可用。
- 回复直接说清本轮做了什么、答案是什么。"""


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
                          .replace("__CONTEXT__", task.get("context") or "（无）"))
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
                          .replace("__PREV__", (last_text or "（无）")[-3000:]))
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
                                    step_wd, ev=ev, note=note, images=images)
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
                    cj = runner.extract_json(res.get("text") or "")
                    if not isinstance(cj, dict) or not isinstance(cj.get("scores"), dict) \
                            or not cj.get("scores"):
                        cj = {"scores": {}, "issues": [],
                              "summary": "评审输出无法解析：%s" % (res.get("text")
                                                          or res.get("error") or "")[:150]}
                    else:
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
                res = _run_step(run_id, "draft-c%d" % i, modelhub.bind_agent(impl, difficulty), prompt,
                                step_wd, readonly=False, ev=ev, timeout=2400,
                                resume=resume_ctx["session"] if resume_ctx else None,
                                images=_task_images(task, workdir))
                # §07 T1.1：draft 会话 id 供本轮 revise 复用（同会话内前缀走缓存读计价）
                draft_sid = _resume_sid(impl, res.get("sid")) or ""
                if not res["ok"]:
                    # 成品是文件不是退出码：CLI 超时但章稿已完整落盘（终章长文实测
                    # 反复出现——文件写完、收尾声明没等到）就送评审门把关，别整章作废
                    txt = _read_chapter(workdir, i)
                    if not (txt and _wc(txt) >= int(wpc * 0.6)):
                        store.update_run(run_id, status="failed",
                                         error="第 %d 章起草失败: %s" % (i, res.get("error")), ended_at=_now())
                        return
                    live = (store.get_run(run_id).get("steps") or [])
                    if live:
                        store.finish_step(run_id, live[-1]["n"], "done",
                                          summary="起草调用超时，但章稿已完整落盘（约 %d 字）——交评审门判质量"
                                                  % _wc(txt))
            else:
                # ---- 同章多稿赛马（dev-3.0）：n 个作者并行起草 → 逐变体评审 →
                # 均分最高者为正稿。变体写隔离文件 chapter-XX-vK.md，赢家改名、
                # 败稿删除；变体 0 = 本任作者（revise 会话沿用），其余取跨族优先的
                # 其他真实智能体，不足时同作者开新会话凑数。
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
    global_means, global_issues = {}, []
    for agent in critics:
        role = "global-critique"
        if agent.get("mode") == "mock":
            step, _ = store.add_step(run_id, role, agent["id"], agent.get("label"))
            time.sleep(0.15)
            gj = {"scores": {d: 8.0 for d in dims},
                  "issues": [], "summary": "（mock）全书结构完整，达到可签约水平"}
            store.finish_step(run_id, step["n"], "done", summary="均分 8.0：（mock）全书达标",
                              duration_s=0.15)
        else:
            gtpl = SERIAL_GLOBAL_PROMPT
            if bible:
                gtpl = gtpl.replace("## 全书目标", bible + "\n\n## 全书目标", 1)
            res = _run_step(run_id, role, modelhub.bind_agent(agent, difficulty),
                            (gtpl.replace("__DIMKEYS__", dimkey)
                             .replace("__GOAL__", task["goal"])
                             .replace("__MANUSCRIPT__", full_text[:60000])),
                            workdir, readonly=True, ev=ev, timeout=2400)
            gj = runner.extract_json(res.get("text") or "")
            if not isinstance(gj, dict) or not isinstance(gj.get("scores"), dict):
                gj = {"scores": {}, "issues": [], "summary": "全局评审输出无法解析"}
        global_issues.extend({"chapter": "全书", **it} for it in (gj.get("issues") or [])[:8])
        for d in dims:
            v = gj.get("scores", {}).get(d)
            if v is not None:
                global_means.setdefault(d, []).append(float(v))
        _check_cancel(ev)
    global_means = {d: round(sum(xs) / len(xs), 1) for d, xs in global_means.items()}
    global_pass = _all_ge(global_means, threshold)

    # ---- 3.5) 自驱打磨：全局评审不过 → 自动重改最弱章并重评（至多 2 轮，无需人工）
    polish_rounds = 0
    while (not global_pass) and polish_rounds < 2 and chapter_scores:
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
            vals = {}
            for d in dims:
                xs = [float(cj["scores"].get(d, 0)) for cj in cj_by_agent.values()
                      if isinstance(cj.get("scores"), dict) and d in cj["scores"]]
                vals[d] = round(sum(xs) / len(xs), 1) if xs else 0.0
            for c2 in chapter_scores:
                if c2["chapter"] == i:
                    c2["means"] = vals
                    c2["passed"] = bool(vals) and all(v >= threshold_ch for v in vals.values())
                    c2["rounds"] = int(c2.get("rounds") or 1) + 1
                    c2["polished"] = True
                    c2["words"] = _wc(_read_chapter(workdir, i))
                    fixed.append(i)
            store.update_run(run_id, chapter_scores=chapter_scores)
            _check_cancel(ev)
        # 重评全书一致性
        full_text = "\n\n".join(_read_chapter(workdir, i2) for i2 in range(1, end + 1))
        gmeans, gissues = {}, []
        for agent in critics:
            if agent.get("mode") == "mock":
                gj2 = {"scores": {d: 8.0 for d in dims}, "issues": [],
                       "summary": "（mock）打磨后全书达标"}
            else:
                res3 = _run_step(run_id, "global-critique", modelhub.bind_agent(agent, difficulty),
                                 (SERIAL_GLOBAL_PROMPT.replace("__DIMKEYS__", dimkey)
                                  .replace("__GOAL__", task["goal"])
                                  .replace("__MANUSCRIPT__", full_text[:60000])),
                                 workdir, readonly=True, ev=ev, timeout=2400)
                gj2 = runner.extract_json(res3.get("text") or "")
                if not isinstance(gj2, dict) or not isinstance(gj2.get("scores"), dict):
                    gj2 = {"scores": {}, "issues": [], "summary": "全局评审输出无法解析"}
            global_issues.extend({"chapter": "全书", **it} for it in (gj2.get("issues") or [])[:8])
            for d in dims:
                v = gj2.get("scores", {}).get(d)
                if v is not None:
                    gmeans.setdefault(d, []).append(float(v))
            _check_cancel(ev)
        global_means = {d: round(sum(xs) / len(xs), 1) for d, xs in gmeans.items()}
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
        prompt = (_tpl(task, "draft_prompt", NOVEL_DRAFT_PROMPT).replace("__FILE__", ms_name)
                  .replace("__GOAL__", task["goal"])
                  .replace("__CONTEXT__", task.get("context") or "（无）"))
        if outline:
            prompt += "\n\n## 编排者大纲（按要点组织稿件）\n" + \
                      "\n".join("- " + i for i in outline["items"])
        draft_res = _run_step(run_id, "draft", modelhub.bind_agent(impl, difficulty), prompt,
                              step_wd, readonly=False, ev=ev, note=draft_note,
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
    agents = _agents()
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
