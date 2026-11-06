# -*- coding: utf-8 -*-
"""端到端交付指标：一本新书「从建单到过签」无需人工救火的完成率。

为什么要另起一份指标
--------------------
``publish/stats.py`` 统计的是**发布动作成功率**（≥95%）。它看不见下面这些事，
而这些事恰恰决定产品是不是真的能自己跑完一本书：

- 质量闸拦下后被人工强制放行（``force_publish``）；
- 重启/超时中断后人工点「继续任务」（``manual_retry``）；
- 人工暂停再恢复、人工改稿后重提。

只盯发布成功率时，一本被人工救了四次的书仍然显示 100%，指标很好看，
产品其实没跑通。本模块把「人救火」计进分母，给出一个更诚实的数。

口径（刻意保守，宁可 unknown 也不假绿）
----------------------------------------
1. 一本 ``task`` 算一次旅程；从 ``task_created`` 到终态只算一次。
2. **只有「异常被人工接管」才算救火**。正常交互不计：看改稿单、点发布、
   按 ``outcome.py`` 的刻意设计由作者登记审核/签约结论（``outcome_recorded``）
   都是产品内设计好的动作，算成救火会惩罚正确设计、把指标变成摆设。
3. 未到终态 = ``incomplete``（在跑），**不进分母**；字段缺失或出现不认识的
   事件 = ``unknown``，**也不进分母**，单列报数。绝不把「没记」当成「没救火」。
4. 零推断：不猜事件、不补样本、不联网、不写盘。

dev / prod
----------
纯函数，dev 直接喂 fixture 即可；prod 由只读报告脚本消费，不改任何调度或写路径。
本模块**默认不接入任何线上行为**，消费点是 ``scripts/journey_report.py``。
"""
from __future__ import annotations

from collections import OrderedDict

# 异常被人工接管的四类事件。新增类型必须同步改守测，避免悄悄扩大口径。
RESCUE_KINDS = frozenset((
    "force_publish",         # 质量闸被人工压过
    "manual_retry",          # 中断/失败后人工续跑
    "manual_pause_resume",   # 人工暂停再恢复
    "manual_revise",         # 人工改稿后重新提交
))
START_KINDS = frozenset(("task_created",))
TERMINAL_KINDS = frozenset(("accepted", "rejected", "cancelled", "failed"))
# 正常链路事件：明确列出，防止把「作者正常登记结论」误算成救火。
NEUTRAL_KINDS = frozenset((
    "draft_done", "review_done", "published", "outcome_recorded",
))
VALID_KINDS = RESCUE_KINDS | START_KINDS | TERMINAL_KINDS | NEUTRAL_KINDS

STATUSES = ("clean", "rescued", "incomplete", "unknown")


def _unknown(reason):
    """判不了一律 unknown，不上绿标，也不进分母。"""
    return {"status": "unknown", "reasons": [reason], "judged": False}


def classify_journey(events):
    """判一本 task 的旅程。events 为按时间排列的事件 dict 列表，每条至少带 ``kind``。

    返回 ``{"status", "reasons", "judged"}``；``judged=True`` 才进完成率分母。
    """
    if not isinstance(events, (list, tuple)) or not events:
        return _unknown("empty_events")
    kinds = []
    for ev in events:
        if not isinstance(ev, dict):
            return _unknown("bad_event")
        kind = ev.get("kind")
        if not isinstance(kind, str) or kind not in VALID_KINDS:
            return _unknown("unknown_kind")
        kinds.append(kind)
    if not any(k in START_KINDS for k in kinds):
        return _unknown("missing_task_created")
    if not any(k in TERMINAL_KINDS for k in kinds):
        # 在跑的书不算失败也不算成功，单列；绝不按「没救火」记成 clean。
        return {"status": "incomplete", "reasons": [], "judged": False}
    rescues = sorted(set(k for k in kinds if k in RESCUE_KINDS))
    return {
        "status": "rescued" if rescues else "clean",
        "reasons": rescues,
        "judged": True,
    }


def group_by_task(events):
    """把扁平事件流按 task_id 分组；坏事件单独成组，保证能被判成 unknown。"""
    groups = OrderedDict()
    for idx, ev in enumerate(events or []):
        if isinstance(ev, dict):
            tid = str(ev.get("task_id") or "")
            key = tid if tid else "__no_task_%d" % idx
        else:
            key = "__bad_%d" % idx
        groups.setdefault(key, []).append(ev)
    return groups


def assess(journeys):
    """汇总一次旅程清单。只接受 classify_journey 结果；坏输入按 unknown 计。"""
    counts = dict((s, 0) for s in STATUSES)
    rescue_reasons = {}
    for item in journeys or []:
        st = item.get("status") if isinstance(item, dict) else None
        if st not in counts:
            st = "unknown"
        counts[st] += 1
        if st == "rescued" and isinstance(item, dict):
            for r in item.get("reasons") or []:
                rescue_reasons[r] = rescue_reasons.get(r, 0) + 1
    judged = counts["clean"] + counts["rescued"]
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "judged": judged,
        "clean_rate": (round(counts["clean"] / judged, 4)
                       if judged else None),
        "rescue_reasons": rescue_reasons,
    }


def from_events(events):
    """便捷入口：扁平事件流 → 汇总结果。"""
    groups = group_by_task(events)
    return assess([classify_journey(v) for v in groups.values()])


def human_report(summary):
    """给人看的一行行结论。必须报「真被判过的样本数」（决策 18）。"""
    c = summary["counts"]
    rate = summary["clean_rate"]
    lines = [
        "端到端交付指标：一本新书从建单到过签，无需人工救火的完成率",
        "真被判过的样本数 %d/%d（终态且事件完整）；在跑 %d、判不了 %d 单列不进分母"
        % (summary["judged"], summary["total"], c["incomplete"], c["unknown"]),
        "无需人工救火 %d 本 / 人工救火 %d 本" % (c["clean"], c["rescued"]),
        "完成率：%s" % ("%.1f%% (%d/%d)" % (rate * 100, c["clean"], summary["judged"])
                       if rate is not None else "unknown（无终态完整样本，不上绿标）"),
    ]
    if summary["rescue_reasons"]:
        lines.append("救火原因：" + "；".join(
            "%s %d 次" % (k, v) for k, v in sorted(summary["rescue_reasons"].items())))
    if c["unknown"]:
        lines.append("提醒：%d 本判不了（事件缺失或不认识），别把它当成「没救火」" % c["unknown"])
    return "\n".join(lines)
