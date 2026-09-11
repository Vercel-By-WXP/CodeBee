# -*- coding: utf-8 -*-
"""智能路由：能力基线 × 历史胜率 × 角色约束 → 选智能体，并给出可解释的理由。"""
from __future__ import annotations

from . import history

# 各类智能体的能力基线（0-100）。真实 CLI 里官方双雄最高。
CAPABILITY = {
    "codex": 84, "claude": 84, "opencode": 74, "qwen": 72,
    "aider": 70, "openclaw": 60, "generic": 55, "mock": 10,
}

MAX_REPAIR_ROUNDS = 2  # 自动修复循环上限


def _history_bonus(stats, agent_id, ttype):
    s = (stats.get(agent_id) or {}).get(ttype) or {}
    if not s.get("runs"):
        return 0.0
    win_rate = s["wins"] / s["runs"]
    return round(18.0 * win_rate + min(6.0, s["runs"]), 1)  # 胜率为主，经验为辅


def score(agent, role, ttype, stats=None):
    """返回 (总分, 理由字符串)。"""
    stats = stats or {}
    base = CAPABILITY.get(agent.get("kind"), 60)
    hb = _history_bonus(stats, agent.get("id"), ttype)
    total = base + hb
    hs = (stats.get(agent.get("id")) or {}).get(ttype)
    htxt = ("，历史 %d/%d 胜（+%s）" % (hs["wins"], hs["runs"], hb)) if hs else "，无历史记录"
    return total, "能力基线 %d%s，总分 %s" % (base, htxt, round(total, 1))


def pick(agents, role, ttype, stats=None, exclude=()):
    """按分选出最优智能体。返回 (agent, 理由) 或 (None, "")。"""
    stats = stats or history.agent_stats()
    best, best_reason = None, ""
    for a in agents:
        if a["id"] in exclude:
            continue
        total, reason = score(a, role, ttype, stats)
        if best is None or total > best[0]:
            best, best_reason = (total, a), reason
    if best is None:
        return None, ""
    return best[1], best_reason


def pick_reviewer(agents, impl, ttype, stats=None):
    """评审者：跨厂商优先（与实现者不同），真实智能体中选分最高者。"""
    stats = stats or history.agent_stats()
    real = [a for a in agents if a.get("mode") == "real" and a["id"] != impl["id"]]
    if real:
        best = max(real, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, "跨厂商评审：" + reason
    mb = next((a for a in agents if a["id"] == "mock-b"), None)
    if mb and impl.get("mode") != "mock":
        return mb, "（无第二个真实智能体，用 mock 评审）"
    return impl, "（自评：仅有实现者一个智能体可用）"


def pick_critics(agents, ttype, stats=None):
    """小说评审组：全部真实智能体；没有任何真实智能体时用内置 mock。"""
    stats = stats or history.agent_stats()
    real = [a for a in agents if a.get("mode") == "real"]
    if real:
        return real, "全部真实智能体参与（按历史表现自动选择）"
    mocks = [a for a in agents if a.get("mode") == "mock"] or agents[:2]
    return mocks, "（无真实智能体，用内置 mock 演示）"
