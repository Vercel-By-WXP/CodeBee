# -*- coding: utf-8 -*-
"""历史统计：从过往运行记录提取每个智能体在每种任务类型上的胜率，反哺路由。"""
from __future__ import annotations

import time

from . import store

# 连败统计窗：只数近窗内的失败步，出窗自然回落——冷却必须随时间自愈，不引入
# 额外计时状态（2026-10-22 codex 实案：当日 4 连败仍凭高能力基线占先手，每轮
# 起跑白烧一次确认失败才让位）。
STREAK_WINDOW_S = 45 * 60


def agent_stats(limit=200):
    """返回 {agent_id: {task_type: {"runs": n, "wins": w, "streak": k}}}。

    streak＝起草/实现位「最近的连续失败步数」：failed/timeout 记一败，done 即断，
    cancelled/running 不计不断；只累计近 STREAK_WINDOW_S 内的失败。评审位成败
    不参与——评审挂了不该没收起草位的先手，一次评审成功也不该把起草连败洗白。
    供路由连败冷却（router.STREAK_BENCH_MIN）与粘性抑制使用。"""
    stats = {}
    now = time.time()
    broken = set()

    def bump(aid, ttype, win):
        if not aid:
            return
        s = stats.setdefault(aid, {}).setdefault(ttype, {"runs": 0, "wins": 0})
        s["runs"] += 1
        if win:
            s["wins"] += 1

    def mark_streak(aid, ttype, status, ended_epoch):
        if not aid or (aid, ttype) in broken:
            return
        if status == "done":
            broken.add((aid, ttype))
            return
        if status not in ("failed", "timeout"):
            return
        try:
            age = now - float(ended_epoch)
        except (TypeError, ValueError):
            return
        if age < 0 or age > STREAK_WINDOW_S:
            return
        s = stats.setdefault(aid, {}).setdefault(ttype, {"runs": 0, "wins": 0})
        s["streak"] = s.get("streak", 0) + 1

    # 列表里移除的记录仍是执行史：路由胜率不能因用户清列表而失忆
    for run in store.list_runs(limit, include_hidden=True):
        if run.get("kind") != "orchestration":
            continue
        # 失败运行也要进历史（记为全负）：以前只数 done——常挂的 CLI 永远
        # 「无历史记录」满血参选，2026-09-17 opencode 当日秒挂 3 次仍被换将
        # 选中。cancelled 不算（用户取消非智能体之过）。
        if run.get("status") not in ("done", "failed"):
            continue
        verdict = run.get("verdict") or {}
        task = store.get_task(run.get("task_id")) if run.get("task_id") else None
        ttype = (task or {}).get("type") or verdict.get("type")
        if not ttype:
            continue
        win = run.get("status") == "done" and bool(
            verdict.get("pass") or verdict.get("publishable"))
        for s in run.get("steps") or []:
            if s.get("role") in ("implement", "draft"):
                bump(s.get("agent"), ttype, win)
        # list_runs 新记录在前、步内时间正序：倒序扫才是「从最新往回数」的连败串
        for s in reversed(run.get("steps") or []):
            if s.get("role") in ("implement", "draft"):
                mark_streak(s.get("agent"), ttype, s.get("status"),
                            s.get("ended_at_epoch") or s.get("started_at_epoch"))
    return stats
