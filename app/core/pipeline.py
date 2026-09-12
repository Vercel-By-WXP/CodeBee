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
import time

from . import catalog, history, jobs, manager, modelhub, mocks, planner, registry, router, runner, store

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
    """校验任务上的 resume 声明；返回 {agent, session, note} 或 None。"""
    r = task.get("resume") or {}
    if not (r.get("agent") and r.get("session")):
        return None
    a = _pick(agents, r.get("agent"))
    if a is None or a.get("mode") != "real":
        return None
    return {"agent": a, "session": r["session"],
            "note": "沿用已有会话 %s…（保留其上下文继续工作）" % str(r["session"])[:8]}


def _run_step(run_id, role, agent, prompt, workdir, readonly, ev, timeout=runner.DEFAULT_TIMEOUT, note="", resume=None):
    """执行一个智能体步骤并记录。返回 runner 统一结果。"""
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
        res = runner.run_agent(agent, prompt, workdir=workdir, readonly=readonly,
                               timeout=timeout, cancel_event=ev, log_path=str(log_abs),
                               resume=resume)
    _check_cancel(ev)
    store.finish_step(run_id, step["n"],
                      "done" if res["ok"] else "failed",
                      summary=((res.get("text") or res.get("error") or "")[:200]),
                      exit_code=res.get("raw", {}).get("exit_code"),
                      cost_usd=res.get("cost_usd", 0.0),
                      tokens=res.get("tokens", 0),
                      duration_s=time.time() - start)
    return res


# ---------------------------------------------------------------- 提示词

CODE_IMPL_PROMPT = """你是一名高级工程师，请在当前工作目录中直接完成以下任务（直接修改/创建文件）。

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

CODE_FIX_PROMPT = """你是一名高级工程师。你之前的实现没有通过验收，请在当前工作目录中直接修复（直接修改/创建文件）。

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
    try:
        r = runner.run_process(argv=["git", "diff", "HEAD"], cwd=workdir, timeout=60)
        if r["ok"]:
            return r["stdout"][:30000]
        r2 = runner.run_process(argv=["git", "diff"], cwd=workdir, timeout=60)
        return r2["stdout"][:30000] if r2["ok"] else ""
    except Exception:
        return ""


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
    store.finish_step(run_id, step["n"], "done" if ok else "failed",
                      summary=("验证通过" if ok else "验证失败（exit %s）" % r["exit_code"]),
                      exit_code=r["exit_code"], duration_s=time.time() - start)
    _check_cancel(ev)
    return ok, True


def _run_review(run_id, task, workdir, reviewer, ev):
    diff = _git_diff(workdir)
    prompt = (CODE_REVIEW_PROMPT
              .replace("__GOAL__", task["goal"])
              .replace("__VERIFY__", task.get("verify_command") or "（未配置）")
              .replace("__DIFF__", diff or "（无法获取 git diff，请综合任务目标谨慎评审）"))
    res = _run_step(run_id, "review", reviewer, prompt, workdir, readonly=True, ev=ev)
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
        plan_step, plan_log = store.add_step(run_id, "plan", impl["id"], impl.get("label"),
                                             note=route.get("implementer", ""))
        plan = planner.make_code_plan(task, modelhub.bind_agent(impl, difficulty), workdir, ev,
                                      resume=resume_ctx["session"] if resume_ctx else None)
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
        impl_b = modelhub.bind_agent(impl_agent, difficulty)
        for i, sub in enumerate(subtasks):
            prompt = (CODE_IMPL_PROMPT
                      .replace("__GOAL__", task["goal"])
                      .replace("__SUBTASK__",
                               sub["detail"] if sub["detail"] else sub["title"])
                      .replace("__CONTEXT__", task.get("context") or "（无）")
                      .replace("__VERIFY_HINT__", _verify_hint(task)))
            role = "implement" if len(subtasks) == 1 else "implement-%d/%d" % (i + 1, len(subtasks))
            res = _run_step(run_id, role, impl_b, prompt, workdir,
                            readonly=False, ev=ev, note=prefix_note if i == 0 else "",
                            resume=use_resume)
            if impl_agent.get("mode") == "mock" and res["ok"]:
                try:
                    mock_path = os.path.abspath(os.path.join(workdir, "mock-impl.txt"))
                    if _inside(workdir, mock_path):
                        with open(mock_path, "a", encoding="utf-8") as f:
                            f.write("%s mock 实现：%s / %s\n" % (_now(), task["title"], sub["title"]))
                except Exception:
                    pass
            if not res["ok"]:
                store.update_run(run_id, status="failed",
                                 error="实现步骤失败: %s" % res.get("error"), ended_at=_now())
                return False
        return True

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
                            resume=resume_ctx["session"] if resume_ctx else None)
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

NOVEL_DRAFT_PROMPT = """你是一名专业作者。请在当前工作目录中撰写/修订稿件文件：`__FILE__`（直接写入该文件）。

## 写作任务
__GOAL__

## 背景与上下文
__CONTEXT__

## 要求
- 只修改 `__FILE__` 这一个文件；保持 Markdown 结构。
- 完成后用 3 句话说明本轮写了什么。"""

NOVEL_REVISE_PROMPT = """你是一名专业作者。请根据下方汇总评审意见修订稿件文件：`__FILE__`（直接写入该文件）。

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


def _ensure_critique_placeholders(tpl):
    """自定义评审模板缺占位符时补上，避免稿件内容/维度定义丢失导致盲评。"""
    if "__MANUSCRIPT__" not in tpl:
        tpl += "\n\n## 待评审稿件\n---\n__MANUSCRIPT__\n---"
    if "__DIMKEYS__" not in tpl:
        tpl = ("请按维度打分（1-10 分）。\n\n" + tpl)
    return tpl


# ---------------------------------------------------------------- 连载引擎（长篇小说：逐章打磨）

SERIAL_CHAPTER_PROMPT = """你是一名网文作者。请撰写本书第 __I__ 章，把本章正文写入文件 `__FILE__`（直接写入该文件，只写本章）。

## 全书目标
__GOAL__

## 全书大纲（本章 = 大纲第 __I__ 章）
__OUTLINE__

## 前情提要（此前各章结尾摘录，衔接用）
__PREV__

## 本章要求
- 章节标题：__TITLE__
- 剧情要点：__BEATS__
- 章末钩子：__HOOK__
- 正文约 __WORDS__ 字，中文，直接开写正文（可含本章标题行），不要写任何与正文无关的说明。"""

SERIAL_REVISE_PROMPT = """你是一名网文作者。第 __I__ 章没有通过评审，请修订文件 `__FILE__`（直接改写该文件）。

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


def _chapter_io(workdir, i, mode):
    """打开第 i 章文件；open 紧邻边界校验，路径越界直接拒绝（形态同 _ms_io）。"""
    p = os.path.abspath(os.path.join(workdir, "chapter-%02d.md" % i))
    if not _inside(workdir, p):
        raise ValueError("章节路径越界: chapter-%02d.md" % i)
    return open(p, mode, encoding="utf-8", errors="replace")


def _read_chapter(workdir, i):
    try:
        with _chapter_io(workdir, i, "r") as f:
            return f.read()
    except Exception:
        return ""


def _wc(text):
    """近似字数（去空白后的字符数，中文场景够用）。"""
    return len(re.sub(r"\s", "", text or ""))


def _write_chapter(workdir, i, text):
    with _chapter_io(workdir, i, "w") as f:
        f.write(text)


def _run_serial_review(run, task, agents, ev, stats, mode, critics, impl, route, resume_ctx, difficulty):
    """连载流水线：大纲 → 逐章起草/评审/修订 → 全局一致性评审 → 合并成书。"""
    import json as _json
    run_id = run["id"]
    workdir = task["workdir"]
    serial = task.get("serial") or {}
    n = int(serial.get("chapters") or 8)
    wpc = int(serial.get("words_per_chapter") or 2500)
    dims = task.get("rubric") or DEFAULT_RUBRIC
    threshold = task.get("threshold", 7.0)
    threshold_ch = threshold - 0.5 if threshold >= 7.5 else threshold   # 单章阈值略放宽 0.5 分
    dimkey = ", ".join('"%s": 0' % d for d in dims)

    def crit_prompt_for(text, note=""):
        tpl = _ensure_critique_placeholders(
            _tpl(task, "critique_prompt", NOVEL_CRITIQUE_PROMPT))
        if note:
            tpl = tpl.replace("你是严格的评审",
                              "你是严格的评审（背景：%s，请结合全书目标评审本章节）" % note, 1)
        return tpl.replace("__DIMKEYS__", dimkey).replace(
            "__MANUSCRIPT__", text or "（稿件为空！）")

    # ---- 1) 大纲
    outline_step, outline_log = store.add_step(run_id, "outline", impl["id"], impl.get("label"),
                                               note=route.get("author", ""))
    outline = planner.make_serial_outline(task, impl, workdir, ev)
    try:
        outline_log.write_text(_json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    store.finish_step(run_id, outline_step["n"], "done",
                      summary="大纲来源 %s：%s（共 %d 章）" % (
                          outline.get("source", "?"),
                          outline.get("book_title") or task["title"], n),
                      duration_s=0.1 if impl.get("mode") == "mock" else None)
    store.update_run(run_id, outline=outline)
    _check_cancel(ev)
    outline_txt = "\n".join(
        "第 %d 章《%s》：%s%s" % (i + 1, c["title"], c["beats"],
                                ("（章末钩子：%s）" % c["hook"]) if c.get("hook") else "")
        for i, c in enumerate(outline["chapters"]))

    chapter_scores = []          # [{chapter,title,means,passed,rounds,words}]
    issues_all = []

    # ---- 2) 逐章
    for i in range(1, n + 1):
        ch = outline["chapters"][i - 1]
        ch_file = "chapter-%02d.md" % i
        prev = ""
        if i > 1:
            tails = []
            for j in range(max(1, i - 2), i):
                t = _read_chapter(workdir, j)
                if t:
                    tails.append("（第 %d 章结尾）…%s" % (j, t[-260:].strip()))
            prev = "\n".join(tails) or "（无）"

        # 起草
        if impl.get("mode") == "mock":
            step, log_abs = store.add_step(run_id, "draft-c%d" % i, impl["id"], impl.get("label"))
            _write_chapter(workdir, i, mocks.draft_manuscript(
                {"title": ch["title"], "goal": task["goal"]}, 1))
            time.sleep(0.2)
            store.finish_step(run_id, step["n"], "done", summary="（mock）第 %d 章草稿落盘" % i,
                              duration_s=0.2)
        else:
            prompt = (SERIAL_CHAPTER_PROMPT
                      .replace("__I__", str(i)).replace("__FILE__", ch_file)
                      .replace("__GOAL__", task["goal"])
                      .replace("__OUTLINE__", outline_txt)
                      .replace("__PREV__", prev)
                      .replace("__TITLE__", ch["title"])
                      .replace("__BEATS__", ch["beats"] or "按大纲推进")
                      .replace("__HOOK__", ch.get("hook") or "留下悬念")
                      .replace("__WORDS__", str(wpc)))
            res = _run_step(run_id, "draft-c%d" % i, modelhub.bind_agent(impl, difficulty), prompt,
                            workdir, readonly=False, ev=ev,
                            resume=resume_ctx["session"] if resume_ctx else None)
            if not res["ok"]:
                store.update_run(run_id, status="failed",
                                 error="第 %d 章起草失败: %s" % (i, res.get("error")), ended_at=_now())
                return

        # 评审-修订（每章至多 1 轮修订）
        rounds_used = 1
        means = {}
        for rnd in (1, 2):
            text = _read_chapter(workdir, i)
            for agent in critics:
                role = "critique-c%d" % i
                if agent.get("mode") == "mock":
                    step, log_abs = store.add_step(run_id, role, agent["id"], agent.get("label"))
                    time.sleep(0.15)
                    cj = mocks.critique(agent["id"], rnd, dims, threshold_ch)
                    store.finish_step(run_id, step["n"], "done",
                                      summary="均分 %.1f：%s" % (
                                          sum(cj["scores"].values()) / max(1, len(dims)),
                                          cj["summary"]),
                                      duration_s=0.15)
                else:
                    res = _run_step(run_id, role, modelhub.bind_agent(agent, difficulty),
                                    crit_prompt_for(text, note="小说第 %d 章" % i),
                                    workdir, readonly=True, ev=ev)
                    cj = runner.extract_json(res.get("text") or "")
                    if not isinstance(cj, dict) or not isinstance(cj.get("scores"), dict):
                        cj = {"scores": {}, "issues": [],
                              "summary": "评审输出无法解析：%s" % (res.get("text") or "")[:150]}
                issues_all.extend({"chapter": i, **it} for it in (cj.get("issues") or [])[:6])
                _check_cancel(ev)
            vals = {}
            for d in dims:
                xs = []
                for a in critics:
                    sc = _last_critique_scores(run_id, role_prefix="critique-c%d" % i, agent_id=a["id"])
                    if sc and d in sc:
                        xs.append(float(sc[d]))
                vals[d] = round(sum(xs) / len(xs), 1) if xs else 0.0
            means = vals
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
                _write_chapter(workdir, i, mocks.draft_manuscript(
                    {"title": ch["title"], "goal": task["goal"]}, 2))
            else:
                prompt = (SERIAL_REVISE_PROMPT
                          .replace("__I__", str(i)).replace("__FILE__", ch_file)
                          .replace("__GOAL__", task["goal"])
                          .replace("__CRITIQUE__", "\n".join(crit_lines))
                          .replace("__WORDS__", str(wpc)))
                _run_step(run_id, "revise-c%d" % i, modelhub.bind_agent(impl, difficulty), prompt,
                          workdir, readonly=False, ev=ev,
                          resume=resume_ctx["session"] if resume_ctx else None)
            _check_cancel(ev)
        chapter_scores.append({"chapter": i, "title": ch["title"], "means": means,
                               "passed": bool(means) and all(v >= threshold_ch for v in means.values()),
                               "rounds": rounds_used,
                               "words": _wc(_read_chapter(workdir, i))})

    # ---- 3) 全局一致性评审
    full_text = "\n\n".join(_read_chapter(workdir, i) for i in range(1, n + 1))
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
            res = _run_step(run_id, role, modelhub.bind_agent(agent, difficulty),
                            (SERIAL_GLOBAL_PROMPT.replace("__DIMKEYS__", dimkey)
                             .replace("__GOAL__", task["goal"])
                             .replace("__MANUSCRIPT__", full_text[:60000])),
                            workdir, readonly=True, ev=ev)
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
    global_pass = bool(global_means) and all(v >= threshold for v in global_means.values())

    # ---- 4) 合并成书
    step, _ = store.add_step(run_id, "merge", "builtin", "内置合成器")
    ms_name = _ms_name(task.get("manuscript"))
    book_title = outline.get("book_title") or task["title"]
    parts = ["# %s" % book_title, ""]
    for i in range(1, n + 1):
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
        "threshold": threshold, "chapters_used": n, "total_words": total_words,
        "chapter_scores": chapter_scores, "global_scores": global_means,
        "global_pass": global_pass, "route": route,
    }
    lines = ["# 连载小说评审报告：%s" % task["title"], "",
             "- 书名：%s（%d 章 / 约 %d 字，合并为 `%s`）" % (book_title, n, total_words, ms_name),
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
                     summary="连载任务%s（%d 章约 %d 字，综合 %.1f）" % (
                         "达标" if publishable else "未达标", n, total_words, overall),
                     ended_at=_now())


def _last_critique_scores(run_id, role_prefix, agent_id):
    """取该 run 中指定角色+智能体最近一次 critique 步骤的评分（从摘要里拿不到，直接读不到 JSON，
    这里退化为读 run.steps 中该角色最后一次的 summary 前缀均分不可行——改为由调用方维护。
    保留接口以兼容历史调用。"""
    return None


def _run_content_review(run, task, agents, ev, stats, mode):
    run_id = run["id"]
    workdir = task["workdir"]
    ms_name = _ms_name(task.get("manuscript"))
    dims = task.get("rubric") or DEFAULT_RUBRIC
    threshold = task.get("threshold", 7.0)
    rounds = task.get("rounds", 2)
    route = {}
    resume_ctx = _valid_resume(task, agents)
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
        critics, route["critics"] = router.pick_critics(agents, "novel", stats)
    if impl is None:
        store.update_run(run_id, status="failed", error="没有可用智能体", ended_at=_now())
        return
    if resume_ctx is not None and mode == "auto":
        critics, route["critics"] = router.pick_critics(agents, "novel", stats)

    # ---- 规划（小说为模板计划）
    plan = planner.make_novel_plan(task, impl, critics)
    store.update_run(run_id, plan=plan, route=route, difficulty=difficulty)

    ms_path = os.path.join(workdir, ms_name)

    def write_ms(text):
        with _ms_io(workdir, ms_name, "w") as f:
            f.write(text)

    def read_ms():
        try:
            with _ms_io(workdir, ms_name, "r") as f:
                return f.read()
        except Exception:
            return ""

    draft_note = route.get("author", "") if mode == "auto" else ""

    # 编排者大纲：只对真实执行有意义；失败静默退回无大纲
    outline = planner.make_review_outline(task) if impl.get("mode") != "mock" else None
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
                              workdir, readonly=False, ev=ev, note=draft_note,
                              resume=resume_ctx["session"] if resume_ctx else None)
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
    task = store.get_task(run.get("task_id"))
    store.update_run(run_id, status="running", started_at=_now())
    if task is None:
        store.update_run(run_id, status="failed", error="找不到任务 %s" % run.get("task_id"),
                         ended_at=_now())
        return
    agents = _agents()
    stats = history.agent_stats()
    mode = task.get("mode") or ("manual" if task.get("implementer") else "auto")
    store.update_run(run_id, mode=mode)
    # engine 决定流水线：code=实现/验证/评审/修复；review=起草/多维评审/修订/门禁
    engine = task.get("engine") or ("code" if task["type"] == "code" else "review")
    try:
        if engine == "code":
            _run_code(run, task, agents, ev, stats, mode)
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
