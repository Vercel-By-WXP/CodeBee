# -*- coding: utf-8 -*-
"""历史统计：从过往运行记录提取每个智能体在每种任务类型上的胜率，反哺路由。"""
from __future__ import annotations

from . import store


def agent_stats(limit=200):
    """返回 {agent_id: {task_type: {"runs": n, "wins": w}}}。"""
    stats = {}

    def bump(aid, ttype, win):
        if not aid:
            return
        s = stats.setdefault(aid, {}).setdefault(ttype, {"runs": 0, "wins": 0})
        s["runs"] += 1
        if win:
            s["wins"] += 1

    for run in store.list_runs(limit):
        if run.get("kind") != "orchestration" or run.get("status") != "done":
            continue
        verdict = run.get("verdict") or {}
        task = store.get_task(run.get("task_id")) if run.get("task_id") else None
        ttype = (task or {}).get("type") or verdict.get("type")
        if not ttype:
            continue
        win = bool(verdict.get("pass") or verdict.get("publishable"))
        for s in run.get("steps") or []:
            if s.get("role") in ("implement", "draft"):
                bump(s.get("agent"), ttype, win)
    return stats
