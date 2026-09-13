# -*- coding: utf-8 -*-
"""规划器：把用户目标自动拆解为有序子任务。

优先用「编排中枢」直连 API 的编排者模型（统一规划/管理）；未配置或调用
失败时回落到最强可用 CLI 智能体；再失败退化为单步模板——计划永远可执行，
不阻塞任务。review 类引擎可让编排者产出写作大纲，拼进起草提示词。
"""
from __future__ import annotations

import json

from . import modelhub, runner, usage

MAX_SUBTASKS = 4


def _log_usage(source, role, task, res, agent=None, tool="", model="", provider=""):
    """规划链路的调用入台账（编排者直连 / CLI 规划）；失败不影响规划本身。"""
    try:
        usage.record(source=source, task_id=(task or {}).get("id", ""),
                     task_type=(task or {}).get("type", ""), role=role,
                     agent=(agent or {}).get("id", "") or "orchestrator",
                     agent_label=(agent or {}).get("label", "") or "编排者",
                     tool=tool or (agent or {}).get("kind", "") or "orchestrator",
                     model=model or ((res or {}).get("model") or ""),
                     provider=provider, ok=bool((res or {}).get("ok")),
                     duration_s=float(((res or {}).get("raw") or {}).get("duration") or 0.0),
                     cost_usd=float((res or {}).get("cost_usd") or 0.0),
                     usage=(res or {}).get("usage"))
    except Exception:
        pass

CODE_PLAN_PROMPT = """你是技术负责人。请把下面的开发目标拆解为 __N__ 个以内、按顺序执行的子任务，
并判定任务难度。只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"difficulty": "easy 或 hard", "subtasks": [{"title": "简短标题", "detail": "具体要做什么，给执行工程师的直接指令"}]}
难度判定：常规增删改查/小函数/格式调整 = easy；跨模块改动/架构调整/复杂算法/安全相关 = hard。
子任务粒度要可独立验证；最后一个子任务必须包含整体联调/收尾。

## 开发目标
__GOAL__

## 背景与上下文
__CONTEXT__

## 验收命令（最终必须通过）
__VERIFY__"""

REVIEW_OUTLINE_PROMPT = """你是内容主编。请为下面的创作任务拟一份写作大纲（要点列表，3-8 条），
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"outline": ["要点1", "要点2", ...]}

## 创作任务
__GOAL__

## 背景与上下文
__CONTEXT__"""

SERIAL_OUTLINE_PROMPT = """你是网文主编，熟悉签约平台（番茄/七猫/起点）的过稿标准。
请为下面的小说目标设计一份连载大纲：共 __N__ 章，每章约 __W__ 字。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"book_title": "书名", "chapters": [{"title": "章节标题", "beats": "本章剧情要点（50-120字：事件/冲突/推进）", "hook": "章末钩子（一句话）"}]}
硬性要求：
- 第 1-3 章是黄金三章：第 1 章开篇即冲突+人设立住，第 3 章末留大钩子；
- 每章有明确冲突与剧情推进，禁止水字数的日常流水账；
- 结局必须闭环（完本感），主角有成长弧光；
- 题材健康，无违规内容，符合平台签约调性。

## 小说目标
__GOAL__

## 背景与上下文
__CONTEXT__"""


def _norm_chapters(data, n):
    """规范化连载大纲输出；不合规返回 None。"""
    if not isinstance(data, dict):
        return None
    chs = data.get("chapters")
    if not isinstance(chs, list) or not chs:
        return None
    out = []
    for c in chs[:n]:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()
        beats = str(c.get("beats") or "").strip()
        if not title:
            continue
        out.append({"title": title[:60], "beats": beats[:500],
                    "hook": str(c.get("hook") or "").strip()[:200]})
    if len(out) < max(2, n // 2):   # 至少给出半数章的大纲，否则视为失败
        return None
    while len(out) < n:             # 缺的章补模板位
        out.append({"title": "第 %d 章" % (len(out) + 1), "beats": "按全书目标推进剧情",
                    "hook": ""})
    return {"book_title": str(data.get("book_title") or "").strip()[:40], "chapters": out[:n]}


def make_serial_outline(task, author_agent=None, workdir=None, ev=None):
    """连载大纲：编排者 API 优先 → 作者 CLI → 模板。返回 {book_title, chapters:[{title,beats,hook}]}。"""
    serial = task.get("serial") or {}
    n = int(serial.get("chapters") or 8)
    wpc = int(serial.get("words_per_chapter") or 2500)
    prompt = (SERIAL_OUTLINE_PROMPT.replace("__N__", str(n)).replace("__W__", str(wpc))
              .replace("__GOAL__", task["goal"])
              .replace("__CONTEXT__", task.get("context") or "（无）"))

    orch = _orchestrator()
    if orch:
        prov, model = orch
        res = modelhub.chat(prov["id"], model, prompt)
        _log_usage("outline", "outline", task, res, model=model,
                   provider=prov.get("name", prov.get("id", "")))
        data = runner.extract_json(res.get("text") or "") if res["ok"] else None
        outline = _norm_chapters(data, n)
        if outline:
            outline["source"] = "编排者(%s · %s)" % (prov.get("name", prov["id"]), model)
            return outline

    if author_agent and author_agent.get("mode") == "real":
        res = runner.run_agent(modelhub.bind_agent(author_agent), prompt,
                               workdir=workdir or task.get("workdir"), readonly=True,
                               timeout=300, cancel_event=ev)
        _log_usage("outline", "outline", task, res, agent=author_agent)
        outline = _norm_chapters(runner.extract_json(res.get("text") or ""), n)
        if outline:
            outline["source"] = "llm(%s)" % author_agent["id"]
            return outline
    elif author_agent and author_agent.get("mode") == "mock":
        # mock：确定性模板大纲
        pass

    chapters = [{"title": "第 %d 章" % i, "beats": "按全书目标推进剧情，保持冲突与钩子",
                 "hook": ""} for i in range(1, n + 1)]
    return {"book_title": "", "chapters": chapters, "source": "template"}


def _norm_subtasks(data):
    """规范化 LLM 计划输出；不合规返回 None。"""
    if not isinstance(data, dict):
        return None
    subs = data.get("subtasks")
    if not isinstance(subs, list) or not subs:
        return None
    steps = []
    for s in subs[:MAX_SUBTASKS]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        detail = str(s.get("detail") or "").strip()
        if not title:
            continue
        steps.append({"title": title[:60], "detail": detail[:1500]})
    return steps or None


def _plan_difficulty(data):
    d = str((data or {}).get("difficulty") or "").lower()
    return d if d in ("easy", "hard") else None


def _orchestrator():
    """编排者可用时返回 (provider, model)，否则 None。"""
    try:
        return modelhub.resolve_orchestrator()
    except Exception:
        return None


def make_code_plan(task, planner_agent, workdir, ev=None, resume=None):
    """代码任务计划：编排者 API 优先 → CLI 智能体 → 单步模板。"""
    orch = _orchestrator()
    if orch:
        plan = _orch_code_plan(task, orch[0], orch[1])
        if plan:
            return plan
    if planner_agent is None:
        return _fallback_code_plan(task, "（无可用智能体）")
    if planner_agent.get("mode") == "mock":
        return _fallback_code_plan(task, "mock 模式：单步模板")
    prompt = (CODE_PLAN_PROMPT.replace("__N__", str(MAX_SUBTASKS))
              .replace("__GOAL__", task["goal"])
              .replace("__CONTEXT__", task.get("context") or "（无）")
              .replace("__VERIFY__", task.get("verify_command") or "（未配置）"))
    res = runner.run_agent(planner_agent, prompt, workdir=workdir, readonly=True,
                           timeout=300, cancel_event=ev, resume=resume)
    _log_usage("plan", "plan", task, res, agent=planner_agent)
    data = runner.extract_json(res.get("text") or "")
    steps = _norm_subtasks(data)
    if steps:
        return {"source": "llm(%s)" % planner_agent["id"], "steps": steps,
                "difficulty": _plan_difficulty(data)}
    return _fallback_code_plan(task, "LLM 计划解析失败，退化为单步模板")


def _fallback_code_plan(task, note):
    return {"source": "template", "note": note,
            "steps": [{"title": "实现任务", "detail": task["goal"]}]}


def _orch_code_plan(task, prov, model):
    res = modelhub.chat(prov["id"], model,
                        (CODE_PLAN_PROMPT.replace("__N__", str(MAX_SUBTASKS))
                         .replace("__GOAL__", task["goal"])
                         .replace("__CONTEXT__", task.get("context") or "（无）")
                         .replace("__VERIFY__", task.get("verify_command") or "（未配置）")))
    _log_usage("plan", "plan", task, res, model=model,
               provider=prov.get("name", prov.get("id", "")))
    if not res["ok"]:
        return None
    data = runner.extract_json(res.get("text") or "")
    steps = _norm_subtasks(data)
    if not steps:
        return None
    return {"source": "编排者(%s · %s)" % (prov.get("name", prov["id"]), model),
            "steps": steps, "difficulty": _plan_difficulty(data)}


def make_review_outline(task):
    """review 类任务：编排者产出写作大纲（失败返回 None，起草退回无大纲）。"""
    orch = _orchestrator()
    if not orch:
        return None
    prov, model = orch
    res = modelhub.chat(prov["id"], model,
                        (REVIEW_OUTLINE_PROMPT
                         .replace("__GOAL__", task["goal"])
                         .replace("__CONTEXT__", task.get("context") or "（无）")))
    _log_usage("outline", "outline", task, res, model=model,
               provider=prov.get("name", prov.get("id", "")))
    if not res["ok"]:
        return None
    data = runner.extract_json(res.get("text") or "")
    outline = data.get("outline") if isinstance(data, dict) else None
    if not isinstance(outline, list):
        return None
    items = [str(x).strip()[:120] for x in outline if str(x).strip()][:8]
    return {"source": "编排者(%s · %s)" % (prov.get("name", prov["id"]), model),
            "items": items} if items else None


def make_novel_plan(task, author, critics):
    dims = "、".join(task.get("rubric") or ["情节", "人物", "文笔", "节奏", "吸引力"])
    return {
        "source": "template",
        "steps": [
            {"title": "起草", "detail": "作者 %s 按目标撰写稿件" % author.get("label", author["id"])},
            {"title": "多维度评审", "detail": "评审组 %s 按 %s 打分，每维度阈值 %.1f"
                % ("、".join(a.get("label", a["id"]) for a in critics), dims, task.get("threshold", 7.0))},
            {"title": "修订循环", "detail": "任一维度低于阈值 → 汇总 major 意见回炉，至多 %d 轮"
                % task.get("rounds", 2)},
            {"title": "发布门禁", "detail": "所有维度达标才标记可发布，输出评审报告"},
        ],
    }
