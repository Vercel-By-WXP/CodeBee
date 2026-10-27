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


def _bucket():
    return {"attempts": 0, "ok": 0, "failed": 0, "rate": None}


def _finish(bucket):
    attempts = bucket["attempts"]
    bucket["rate"] = round(bucket["ok"] / attempts, 4) if attempts else None
    return bucket


def summary(days=7, task_id=""):
    """近 N 天的发布成功率。task_id 非空时只算这本书。

    返回 ``{days, goal, live:{...}, draft:{...}, platforms:{pid:{live,draft}},
    failures:[...]}``。``rate`` 在样本为 0 时是 ``None``——界面显示「还没有
    发布记录」，不要显示 0%（那看起来像全线失败）。
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
    return {
        "days": days,
        "goal": GOAL_RATE,
        "live": _finish(live),
        "draft": _finish(draft),
        "platforms": platforms,
        "failures": failures[:5],
    }
