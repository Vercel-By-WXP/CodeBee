# -*- coding: utf-8 -*-
"""发布成功率：把「最后一公里」的可靠性变成一个能看的数。

产品级指标是 **≥95%**。没有这个数，发布链的可靠性只能靠记忆判断——而这个
项目近 30 天有 43 次修复集中在发布链，记忆显然不够用。

口径（刻意选窄）：

- 只统计**内容动作**：建书、发章、整篇短故事。``connect`` / ``probe`` /
  ``auto_fire`` 是连接探测和定时触发，不是一次发布，混进来会稀释真实失败率；
- **草稿不算上线**：``*_draft`` 只落到平台草稿箱，作者还要再点一次提交。
  草稿单独统计，不混入成功率，否则「全部发草稿失败」会显示成发布正常；
- 只按台账记录算，**不推断**：没有记录的批次就是没有数据，不补、不估。

台账本身是 append-only 的 jsonl，记账失败全吞（见 ledger.record），所以这里
的数字只可能偏少不会偏多——宁可低估样本量，也不要虚报成功率。
"""
from __future__ import annotations

from . import ledger

# 真正「上台面」的动作：作者点一次，平台侧就多一章/一本。
_LIVE_ACTIONS = ("upload_chapter", "upload_chapter_scheduled", "publish_short_story",
                 "create_book")
# 只到草稿箱的动作：单独统计，不进成功率。
_DRAFT_ACTIONS = ("upload_chapter_draft", "upload_short_story_draft")

GOAL_RATE = 0.95
# 告警的最小失败数：1 次失败可能是单次抖动（平台瞬断），不满 2 次不打扰人。
ALERT_MIN_FAILURES = 2


def _alert_from(live, days, target=GOAL_RATE):
    """成功率告警判定（注意力面板第 7 来源的判定核心）。

    三条同时满足才告警：有样本、失败数达下限、rate < 目标线。
    没样本（rate None）绝不告警——「还没有发布记录」不是事故。
    返回 None 或描述 dict（带最近失败摘要，供横幅一句话说清事由）。
    """
    rate = live.get("rate")
    if rate is None:
        return None
    failed = int(live.get("failed") or 0)
    if failed < ALERT_MIN_FAILURES:
        return None
    if rate >= target:
        return None
    return {
        "rate": rate, "goal": target, "days": int(days),
        "attempts": int(live.get("attempts") or 0), "failed": failed,
    }


def alert(days=7, target=GOAL_RATE):
    """独立判定入口（给注意力面板用，不需要整份 summary）。"""
    return _alert_from(summary(days=days)["live"], days, target)


def _bucket():
    return {"attempts": 0, "ok": 0, "failed": 0, "rate": None}


def _finish(bucket):
    attempts = bucket["attempts"]
    bucket["rate"] = round(bucket["ok"] / attempts, 4) if attempts else None
    return bucket


def summary(days=7, task_id=""):
    """近 N 天的发布成功率。task_id 非空时只算这本书。

    返回 ``{days, goal, live:{...}, draft:{...}, platforms:{pid:{live,draft}},
    failures:[...], alert}``。``rate`` 在样本为 0 时是 ``None``——界面显示
    「还没有发布记录」，不要显示 0%（那看起来像全线失败）。``alert`` 只在
    全局视图（task_id 为空）时计算：失败数达下限且 rate 跌破目标线才非 None。
    """
    try:
        days = max(1, min(365, int(days)))
    except (TypeError, ValueError):
        days = 7
    live, draft = _bucket(), _bucket()
    platforms, failures = {}, []
    for rec in ledger._iter_records(days=days):
        if task_id and str(rec.get("task_id") or "") != str(task_id):
            continue
        action = str(rec.get("action") or "")
        if action in _LIVE_ACTIONS:
            bucket, kind = live, "live"
        elif action in _DRAFT_ACTIONS:
            bucket, kind = draft, "draft"
        else:
            continue
        bucket["attempts"] += 1
        ok = bool(rec.get("ok"))
        bucket["ok" if ok else "failed"] += 1
        pid = str(rec.get("platform") or "") or "unknown"
        slot = platforms.setdefault(pid, {"live": _bucket(), "draft": _bucket()})[kind]
        slot["attempts"] += 1
        slot["ok" if ok else "failed"] += 1
        if not ok:
            failures.append({
                "ts": str(rec.get("ts") or ""),
                "platform": pid,
                "action": action,
                "chapter_no": rec.get("chapter_no") or 0,
                "title": str(rec.get("title") or ""),
                "error": str(rec.get("error") or ""),
            })
    for pid, pair in platforms.items():
        _finish(pair["live"])
        _finish(pair["draft"])
    failures.sort(key=lambda f: f["ts"], reverse=True)
    out = {
        "days": days,
        "goal": GOAL_RATE,
        "live": _finish(live),
        "draft": _finish(draft),
        "platforms": platforms,
        "failures": failures[:5],
    }
    # 告警只挂在**全局**视图上（task_id 过滤视图的告警语义不明，不掺和）——
    # GET /api/publish/stats 默认就是全局，前端/巡检拿到即用。
    out["alert"] = _alert_from(out["live"], days) if not task_id else None
    return out
