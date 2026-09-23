# -*- coding: utf-8 -*-
"""多线剧情推演（inkos 借鉴 2026-09-23）：写章前一次调用生成 2-3 条分支
节拍计划，自带推荐择优，选中分支注入本章 BEATS；全部分支追加审计文件
.codebee/branch-plans.md 供回看。计划级赛马——比 prose 级 Best-of-N 省
一个数量级的 token。"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

BRANCH_PLAN_PROMPT = """你是网文剧情策划。基于全书设定与上一章结尾，为下一章生成 __N__ 条**彼此方向不同**的剧情分支计划。

要求：
- 每条分支给出：本章节拍（50-120 字：事件/冲突/推进）+ 章末钩子（一句话）+ 一句取舍理由（为什么选/不选它）
- 分支之间要有真实的方向差异（不同冲突来源/不同人物决定/不同信息揭示），不是同一件事换措辞
- 你是策划也是评委：综合大纲意图、前情、人物动机，推荐其中最有力的一条

只输出一个 ```json 代码块：
{"branches": [{"beats": "…", "hook": "…", "why": "…"}, …], "pick": 0}
pick = 推荐分支的下标（0 起）。

## 全书目标
__GOAL__

## 大纲上下文（本章前后）
__OUTLINE__

## 上一章结尾
__PREV__
"""


def plan_branches(run_id, task, i, goal, outline_txt, prev, n, *, log_path=None):
    """生成并择优一条分支计划。返回 (beats, hook) 或 None（失败/未启用走原大纲）。

    编排者一次调用产 N 条+自荐 pick；失败静默回落原大纲节拍（推演是增强
    不是闸门）。全部分支连同 pick 追加 .codebee/branch-plans.md 审计。"""
    if n < 2:
        return None
    from . import modelhub, runner
    if n < 2:
        return None
    orch = modelhub.resolve_orchestrator()
    if not orch:
        return None
    prov, model = orch
    prompt = (BRANCH_PLAN_PROMPT
              .replace("__N__", str(n))
              .replace("__GOAL__", goal[:600])
              .replace("__OUTLINE__", outline_txt[:3000])
              .replace("__PREV__", (prev or "（开篇）")[-1200:]))
    res = modelhub.chat(prov["id"], model, prompt, max_tokens=4000, timeout=300)
    if not res.get("ok"):
        return None
    data = runner.extract_json(res.get("text") or "")
    branches = (data or {}).get("branches") if isinstance(data, dict) else None
    if not isinstance(branches, list):
        return None
    usable = [b for b in branches
              if isinstance(b, dict) and str(b.get("beats") or "").strip()]
    if len(usable) < 2:
        return None
    try:
        pick = int(data.get("pick") or 0)
    except (TypeError, ValueError):
        pick = 0
    pick = max(0, min(pick, len(usable) - 1))
    chosen = usable[pick]
    try:
        from . import store as _store
        wd = _store.run_workdir(run_id) or ""
    except Exception:
        wd = ""
    _append_audit(wd, i, usable, pick)
    return (str(chosen["beats"]).strip()[:300],
            str(chosen.get("hook") or "").strip()[:80] or None)


def _append_audit(workdir, chapter, branches, pick):
    """全部分支追加 .codebee/branch-plans.md（失败静默——审计是增强）。"""
    if not workdir:
        return
    try:
        p = os.path.abspath(os.path.join(str(workdir), ".codebee",
                                         "branch-plans.md"))
        root = os.path.abspath(str(workdir))
        if root not in p.split(os.sep)[:-len([".codebee", "branch-plans.md"])]:
            if not p.startswith(root + os.sep):
                return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write("# 多线剧情推演（每章分支计划与择优，供回看）\n\n")
            f.write("## 第 %d 章\n" % chapter)
            for k, b in enumerate(branches):
                mark = "✅ " if k == pick else ""
                f.write("- %s%s（钩子：%s）——%s\n" % (
                    mark, str(b.get("beats") or "")[:160],
                    str(b.get("hook") or "")[:60],
                    str(b.get("why") or "")[:120]))
            f.write("\n")
    except Exception:
        pass
