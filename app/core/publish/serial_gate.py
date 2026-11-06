# -*- coding: utf-8 -*-
"""任务级串行闸门：不让多本书同时抢同一个见底配额池。

背景
----
``quota_ledger.py`` 做的是 **per-key 四维计量 + 派单前节流**（RPM/RPD/TPM/TPD），
管的是「这一单发给注定被 429 的通道」；它管不了「四本书同时开跑、互相把对方
推成失败」——那是**任务级并发**问题。交接单第 1 节与 11-05 记录里，这一条
一直只靠「先串行、别四本齐跑」的口头纪律撑着。

本模块把这条纪律变成可判、可测、可回退的纯函数。

边界（很重要，别误读成「已经上线」）
------------------------------------
- **默认关闭**：``CODEBEE_SERIAL_GATE`` 未开时 ``plan_dispatch`` 原样放行，
  与现状逐字等价，不改变任何调度行为。
- 纯函数：不写盘、不联网、不启停 run；调用方决定拿结果做什么。
- **本模块尚未接入调度消费点**。接入是下一步（见交接单第 3 节），在那之前
  它只是一个可验收的判据，不是线上闸门。宣称「已串行化」是假的。

dev / prod
----------
- dev：直接喂 fixture 跑守测，不需要起服务、不烧额度；
- prod：建议先只开「只读观察」——把 ``plan_dispatch`` 的 defer 结果打日志，
  观察一个完整周期再接调度；``max_active`` 从 1 起，别一次放四本。
"""
from __future__ import annotations

import os

ENV_ENABLED = "CODEBEE_SERIAL_GATE"
ENV_MAX_ACTIVE = "CODEBEE_SERIAL_MAX_ACTIVE"
ACTIVE_STATUSES = frozenset(("queued", "running"))
_TRUE = frozenset(("1", "true", "yes", "on"))


def policy_from_env(environ=None):
    """读策略。缺值/坏值一律回落到「关闭 + max_active=1」，绝不因配置错就乱放。"""
    env = os.environ if environ is None else environ
    raw = str(env.get(ENV_ENABLED, "0") or "0").strip().lower()
    enabled = raw in _TRUE
    try:
        max_active = int(str(env.get(ENV_MAX_ACTIVE, "1") or "1").strip())
    except (TypeError, ValueError):
        max_active = 1
    max_active = max(1, min(max_active, 16))
    return {"enabled": enabled, "max_active": max_active}


def _active_task_ids(active_runs):
    """只数真正占池子的 queued/running run 所属的 task。"""
    out = set()
    for run in active_runs or []:
        if not isinstance(run, dict):
            continue
        if str(run.get("status") or "") not in ACTIVE_STATUSES:
            continue
        tid = str(run.get("task_id") or "")
        if tid:
            out.add(tid)
    return out


def _defer(item, reason):
    return {"task_id": str(item.get("task_id") or "") if isinstance(item, dict) else "",
            "reason": reason}


def plan_dispatch(candidates, active_runs, policy=None, quota_ok=True):
    """决定哪些候选任务现在可以起跑。

    candidates / active_runs 都是 dict 列表；candidate 至少带 ``task_id``，
    可选 ``created_at``（排序用，越小越先）。返回
    ``{"allow", "defer", "enabled", "max_active", "active_tasks", "reasons"}``。
    坏输入 fail-closed：全量 defer，不抛异常、不静默放行。
    """
    pol = policy if isinstance(policy, dict) else policy_from_env()
    enabled = bool(pol.get("enabled"))
    max_active = int(pol.get("max_active") or 1)

    if not isinstance(candidates, (list, tuple)) or not isinstance(active_runs, (list, tuple)):
        return {"allow": [], "defer": [], "enabled": enabled,
                "max_active": max_active, "active_tasks": [],
                "reasons": ["bad_input: containers"]}
    if any(not isinstance(c, dict) or not str(c.get("task_id") or "")
           for c in candidates):
        return {"allow": [], "defer": [_defer(c, "bad_input: candidate") for c in candidates],
                "enabled": enabled, "max_active": max_active,
                "active_tasks": [], "reasons": ["bad_input: candidate"]}

    active_tasks = _active_task_ids(active_runs)
    reasons = []

    if not enabled:
        # 关 = 与现状逐字等价；连 quota_ok 都不看，避免「默认关却偷偷拦」。
        return {"allow": list(candidates), "defer": [], "enabled": False,
                "max_active": max_active, "active_tasks": sorted(active_tasks),
                "reasons": []}

    if not quota_ok:
        reasons.append("quota_not_restored")
        return {"allow": [],
                "defer": [_defer(c, "quota_not_restored") for c in candidates],
                "enabled": True, "max_active": max_active,
                "active_tasks": sorted(active_tasks), "reasons": reasons}

    ordered = sorted(candidates, key=lambda c: (str(c.get("created_at") or ""),
                                                str(c.get("task_id") or "")))
    allow, defer = [], []
    occupied = len(active_tasks)
    # active_tasks 是「放行前的占用事实」，只增不改；seen 另记本轮已放行，
    # 防止同一 task 在候选里出现两次时占掉两个槽位。
    seen = set(active_tasks)
    for cand in ordered:
        tid = str(cand.get("task_id") or "")
        if tid in active_tasks:
            defer.append(_defer(cand, "task_already_active"))
            continue
        if tid in seen:
            defer.append(_defer(cand, "duplicate_candidate"))
            continue
        if occupied >= max_active:
            defer.append(_defer(cand, "pool_full"))
            continue
        allow.append(cand)
        seen.add(tid)
        occupied += 1
    if defer:
        reasons.append("deferred %d task(s)" % len(defer))
    return {"allow": allow, "defer": defer, "enabled": True,
            "max_active": max_active, "active_tasks": sorted(active_tasks),
            "reasons": reasons}


def human_report(plan):
    """给人看的结论：必须能一眼看出「关着」还是「真拦了」。"""
    if not plan.get("enabled"):
        return "串行闸门：关闭（与现状一致，未拦截任何任务）；接调度前它只是判据"
    lines = ["串行闸门：开启，max_active=%d，已占槽位 task %d 个"
             % (plan.get("max_active", 1), len(plan.get("active_tasks") or []))]
    lines.append("放行 %d 本 / 延迟 %d 本" % (len(plan.get("allow") or []),
                                            len(plan.get("defer") or [])))
    for row in plan.get("defer") or []:
        lines.append("  [延迟] %s：%s" % (row.get("task_id"), row.get("reason")))
    return "\n".join(lines)
