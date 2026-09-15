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
    """返回 (总分, 理由字符串)。配额惩罚：catalog 里配了
    quota_tokens_per_hour 的智能体，本小时用量越接近配额分越低
    （封顶 -45，足以盖过历史加分），满额后仅在没有其他选择时才会被选中。"""
    stats = stats or {}
    base = CAPABILITY.get(agent.get("kind"), 60)
    hb = _history_bonus(stats, agent.get("id"), ttype)
    total = base + hb
    hs = (stats.get(agent.get("id")) or {}).get(ttype)
    htxt = ("，历史 %d/%d 胜（+%s）" % (hs["wins"], hs["runs"], hb)) if hs else "，无历史记录"
    quota_txt = ""
    quota = int(agent.get("quota_tokens_per_hour") or 0)
    if quota > 0:
        try:
            from . import usage
            used = usage.agent_tokens_recent(agent.get("id"), hours=1)
        except Exception:
            used = 0
        if used > 0:
            ratio = min(1.0, used / float(quota))
            penalty = round(-45.0 * ratio, 1)
            if penalty:
                total += penalty
                quota_txt = "，本小时 %d/%d tokens（%s）" % (used, quota, penalty)
    return total, "能力基线 %d%s%s，总分 %s" % (base, htxt, quota_txt, round(total, 1))


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
    """评审者：跨厂商是硬规则（Codeband 的对抗式配对）——同族评审有同款盲区，
    评审者必须来自与实现者不同的 kind；跨族池为空才回退同厂商并如实备注，
    绝不把回退伪装成跨厂商。"""
    stats = stats or history.agent_stats()
    impl_kind = impl.get("kind")
    real = [a for a in agents if a.get("mode") == "real" and a["id"] != impl["id"]]
    cross = [a for a in real if a.get("kind") != impl_kind]
    if cross:
        best = max(cross, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, "跨厂商评审（%s ≠ %s）：%s" % (best.get("kind"), impl_kind, reason)
    if real:
        best = max(real, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, ("（无跨厂商智能体可用，回退同厂商 %s 评审——建议启用其他厂商的 CLI）"
                      % impl_kind) + reason
    mb = next((a for a in agents if a["id"] == "mock-b"), None)
    if mb and impl.get("mode") != "mock":
        return mb, "（无第二个真实智能体，用 mock 评审）"
    return impl, "（自评：仅有实现者一个智能体可用）"


def pick_critics(agents, ttype, stats=None, impl=None):
    """小说评审组：全部真实智能体（排除作者本人——自评是最典型的同族盲区）；
    面板与作者同厂商时在路由依据里明示，供用户决定是否补充其他厂商。
    没有任何真实智能体时用内置 mock。"""
    stats = stats or history.agent_stats()
    real = [a for a in agents if a.get("mode") == "real" and (not impl or a["id"] != impl["id"])]
    if real:
        note = "全部真实智能体参与（按历史表现自动选择）"
        if impl and impl.get("mode") == "real" \
                and all(a.get("kind") == impl.get("kind") for a in real):
            note += "；评审组与作者同为 %s，建议启用其他厂商 CLI 交叉评审" % impl.get("kind")
        return real, note
    mocks = [a for a in agents if a.get("mode") == "mock"] or agents[:2]
    return mocks, "（无真实智能体，用内置 mock 演示）"
