"""过签体检：把判据结论从「按发布按钮那一刻」提前到「稿子写完那一刻」。

为什么单独一层（2026-11-04，P2-A）
--------------------------------
P0-A 的实测结论第三条：闸门只在投递口生效，挡不住作者把稿子拷到平台网页手动投。
作者写完五章时最需要知道三件事——哪一章没给读者东西、哪一章字数不够、哪条题材
承诺**根本没有机器替他在查**；等到点发布才被告知，人已经在平台页面上了。

这一层**只组装，不判断**：判据在 `appeal`，锚点在 `revise`。多出来的只有体检报告
才需要的三样东西：逐章一行能看懂的结论、一句人话总结、以及「哪些承诺没人替你查」。

三条边界（与 P0-B / P1 保持同一口径，别在这里偷偷放宽）
----------------------------------------------------
① 不适用的任务返回 ``verdict = unknown`` 加原因，**绝不出 blocker**——体检卡在
   文档、短篇、空目录上说什么都算误报。
② 报告里**不带正文**：只带段号/句号与一行的证据摘录，正文一旦顺着 API 出去，
   就成了第二个「把用户稿子到处传」的口子。
③ 不落盘、不缓存：改完稿子再拉一次，结论跟着变（与 `revise.block_for` 同）。
"""

import time

from . import appeal

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_SHORT = "short"
STATUSES = (STATUS_OK, STATUS_EMPTY, STATUS_SHORT)

VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_BLOCKED = "blocked"
VERDICT_UNKNOWN = "unknown"

# 不适用原因 → 人话。界面上直接显示这行，别让用户去猜 `no_workdir` 是什么。
REASON_TEXT = {
    "not_serial": "这个任务不是长篇 / 连载，不套题材承诺",
    "no_workdir": "任务还没有工作目录",
    "no_text": "目录里没有第 1 章起的稿子（也没有能切出「第 1 章」的合稿）",
    "no_first_chapter": "缺第 1 章起的稿子，判不了签约",
    "unknown_genre": "认不出题材，不硬套别的题材的承诺",
}

# 一句短结论的上限。右栏只有 188 宽，超过就会被截成「第 1、2 章没给读…」。
HEADLINE_MAX = 26


def _label_list(gains):
    labels = []
    for gain in gains:
        label = str((gain or {}).get("label") or "").strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _first_gain(gains):
    """第一条证据的坐标：弹层里显示「第 3 章 P48 S2 才拿到第一笔」。"""
    for gain in gains:
        paragraph = (gain or {}).get("paragraph")
        if paragraph:
            return {"paragraph": paragraph,
                    "sentence": (gain or {}).get("sentence") or 1,
                    "text": str((gain or {}).get("text") or "")[:40]}
    return {}


def _chapter_rows(result):
    rows = []
    for chapter in result.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        gains = list(chapter.get("gains") or [])
        notes = [str(w) for w in (chapter.get("warnings") or []) if str(w).strip()]
        if not gains:
            status = STATUS_EMPTY
        elif notes:
            # 有获得但仍被点名（典型是字数不达标）：短板而非硬伤，界面上分开说。
            status = STATUS_SHORT
        else:
            status = STATUS_OK
        rows.append({
            "chapter": chapter.get("chapter"),
            "title": str(chapter.get("title") or ""),
            "words": chapter.get("words") or 0,
            "gains": len(gains),
            "gain_labels": _label_list(gains),
            "first_gain": _first_gain(gains),
            "gated": bool(chapter.get("gated")),
            "blocked": bool(chapter.get("blockers")),
            "status": status,
            "note": "；".join(notes),
        })
    return rows


def _unjudged(result):
    """没有机器判据的承诺：列出来，让作者知道这几条得自己看。

    与 `appeal._promise_table` 里的 `judged=False` 是同一件事的另一面——那边标的是
    「这条没判据」，这里给的是能直接上屏的字符串。
    """
    out = []
    for promise in result.get("promises") or []:
        if not isinstance(promise, dict) or promise.get("judged"):
            continue
        text = str(promise.get("text") or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _numbers(rows, status):
    return [str(row["chapter"]) for row in rows if row["status"] == status]


def _reason_text(reason, result=None):
    """不适用原因的人话。

    `no_first_chapter` 要带上**实际的起始章号**：「目录里是第 21 章起的稿子」
    比「缺第 1 章」有用得多——作者一看就知道前二十章被归档到别处了，而不是
    以为稿子丢了（2026-11-04：一本 200 章的书被判成「还没有章节稿」）。
    """
    if reason == "no_first_chapter":
        start = int((result or {}).get("file_start") or 0)
        count = int((result or {}).get("file_count") or 0)
        if start > 1 and count:
            return "目录里是第 %d 章起的 %d 章稿，缺第 1 章" % (start, count)
    return REASON_TEXT.get(reason) or "这本还判不了"


def headline(verdict, rows, reason_text=""):
    """一句人话，≤ HEADLINE_MAX 字。"""
    if verdict == VERDICT_UNKNOWN:
        text = str(reason_text or "") or "这本还判不了"
    elif verdict == VERDICT_BLOCKED:
        # 只说**真被拦**的章。门禁外那章虽也空窗，但那条只记短板不拦，
        # 混进「没给读者东西」会让作者以为它跟第 1 章一样要命。
        hard = [str(r["chapter"]) for r in rows if r.get("blocked")]
        empty = hard or _numbers(rows, STATUS_EMPTY)
        text = ("第 %s 章没给读者东西" % "、".join(empty)) if empty else "有章没兑现获得"
    elif verdict == VERDICT_WARN:
        empty = _numbers(rows, STATUS_EMPTY)
        short = _numbers(rows, STATUS_SHORT)
        parts = []
        if empty:
            parts.append("%s 章单章空窗" % "、".join(empty))
        if short:
            parts.append("%s 章字数偏少" % "、".join(short))
        text = "；".join(parts) or "有短板但不拦"
    else:
        text = "前 %d 章都兑现了" % len(rows) if rows else "还没有可体检的章节"
    return text[:HEADLINE_MAX]


def report(task, now=None):
    """体检报告。不适用时也返回完整结构（``verdict=unknown`` 带原因），不抛。"""
    result = appeal.check_task(task, use_model=False)
    stamp = float(now if now is not None else time.time())
    if not result.get("enabled"):
        reason = str(result.get("reason") or "")
        reason_text = _reason_text(reason, result)
        return {
            "enabled": False,
            "reason": reason,
            "reason_text": reason_text,
            "genre": "",
            "genre_name": "",
            "verdict": VERDICT_UNKNOWN,
            "headline": headline(VERDICT_UNKNOWN, [], reason_text),
            "chapters": [],
            "blockers": [],
            "warnings": [],
            "unjudged": [],
            "sources": [str(item) for item in (result.get("sources") or []) if str(item)],
            "gate": appeal.DEFAULT_CHAPTERS,
            "scan": appeal.DEFAULT_SCAN,
            "min_words": appeal.DEFAULT_MIN_WORDS,
            "checked_at": stamp,
        }
    profile = appeal.profile_for(result.get("genre")) or {}
    rows = _chapter_rows(result)
    blockers = [str(b) for b in (result.get("blockers") or []) if str(b).strip()]
    warnings = [str(w) for w in (result.get("warnings") or []) if str(w).strip()]
    if blockers:
        verdict = VERDICT_BLOCKED
    elif warnings:
        verdict = VERDICT_WARN
    else:
        verdict = VERDICT_PASS
    return {
        "enabled": True,
        "reason": "",
        "reason_text": "",
        "genre": str(result.get("genre") or ""),
        "genre_name": str(result.get("genre_name") or ""),
        "verdict": verdict,
        "headline": headline(verdict, rows),
        "chapters": rows,
        "blockers": blockers,
        "warnings": warnings,
        "unjudged": _unjudged(result),
        "sources": [str(item) for item in (result.get("sources") or []) if str(item)],
        "gate": appeal._positive_int(profile.get("chapters"), appeal.DEFAULT_CHAPTERS),
        "scan": appeal._positive_int(profile.get("scan_chapters"), appeal.DEFAULT_SCAN),
        "min_words": appeal._positive_int(profile.get("min_words"),
                                          appeal.DEFAULT_MIN_WORDS),
        "checked_at": stamp,
    }
