# -*- coding: utf-8 -*-
"""规划器：把用户目标自动拆解为有序子任务。

代码任务：由最强可用智能体产出 JSON 计划（≤4 个子任务），解析失败或
手动模式退化为单步模板——计划永远可执行，不阻塞任务。
小说任务：模板计划（大纲→起草→评审→修订→终稿），作者/评审组由路由决定。
"""
from __future__ import annotations

import json

from . import runner

MAX_SUBTASKS = 4

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


def make_code_plan(task, planner_agent, workdir, ev=None, resume=None):
    """代码任务计划：LLM 拆解 → 失败退化为单步模板。"""
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
    steps = _norm_subtasks(runner.extract_json(res.get("text") or ""))
    if steps:
        diff = None
        try:
            raw = runner.extract_json(res.get("text") or "") or {}
            d = str(raw.get("difficulty") or "").lower()
            diff = d if d in ("easy", "hard") else None
        except Exception:
            diff = None
        return {"source": "llm(%s)" % planner_agent["id"], "steps": steps,
                "difficulty": diff}
    return _fallback_code_plan(task, "LLM 计划解析失败，退化为单步模板")


def _fallback_code_plan(task, note):
    return {"source": "template", "note": note,
            "steps": [{"title": "实现任务", "detail": task["goal"]}]}


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
