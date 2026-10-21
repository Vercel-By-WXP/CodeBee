# -*- coding: utf-8 -*-
"""引号制式与 BOM 等确定性文风体例检查。

从 novel_quality 拆出（单文件行数治理），主模块原样再导出。
"""
from __future__ import annotations

from .novel_quality_common import _positive_chapter_number


_STYLE_NAMES = {"「」": "直角引号「」", "“”": "弯引号“”", '"': "ASCII 直引号"}
_STYLE_OPEN_CLOSE = {"「」": ("「", "」"), "“”": ("“", "”"), '"': ('"', '"')}
_STYLE_MIN_QUOTES = 3     # 单章某制式 ≥3 对才认定「该章用了这种制式」


def _quote_style_counts(text):
    """按制式统计引号对数：直角「」、弯引号“”、ASCII 直引号 "。"""
    t = str(text or "")
    return {"「」": t.count("「"),
            "“”": min(t.count("\u201c"), t.count("\u201d")),
            '"': t.count('"') // 2}


def _style_snippet(text, marker, closer):
    """取制式标记处的一段原文做证据（≤30 字），找不到标记回退章首。"""
    t = str(text or "")
    try:
        i = t.index(marker)
        j = t.index(closer, i) + 1 if closer in t[i + 1:i + 60] else i + 20
        return t[i:min(j, i + 30)].replace("\n", " ")
    except ValueError:
        return t[:30].replace("\n", " ")


def style_consistency_notes(chapters):
    """跨章/章内体例一致性检测（确定性，签约红线级；2026-10-15 七猫双拒实案）。

    引号制式混用和 BOM 是可定位的内部交付问题，按项目体例要求出 major。
    这不能证明它们是编辑所说「风格上不够统一」的原因；语域、视角距离、
    人物声音仍需文学评审独立判断。
    chapters 接受 {章号: 正文} 或 [(章号, 正文), ...]。三类红线：①引号制式
    跨章混用（多数章制式为基准，偏离章在 note 点名）；②章内两种制式并存；
    ③UTF-8 BOM 残留。返回 issue 列表 [{dim, severity, chapter, note, quote}]——
    跨章账 chapter=None + note 点名病灶章（修复链按字段∪文案选章/撤账）。
    """
    if isinstance(chapters, dict):
        pairs = list(chapters.items())
    else:
        try:
            pairs = list(chapters or [])
        except TypeError:
            pairs = []
    raw = {}
    for pair in pairs:
        try:
            chapter, text = pair
        except (TypeError, ValueError):
            continue
        chapter = _positive_chapter_number(chapter)
        if chapter is not None and isinstance(text, str) and text.strip():
            raw[chapter] = text
    if not raw:
        return []
    texts = {c: t.replace("\r\n", "\n").strip() for c, t in raw.items()}
    notes = []
    dominant = {}
    for c, t in texts.items():
        counts = _quote_style_counts(t)
        best = max(counts, key=lambda k: counts[k])
        if counts[best] >= _STYLE_MIN_QUOTES:
            dominant[c] = best
    votes = {}
    for style in dominant.values():
        votes[style] = votes.get(style, 0) + 1
    if votes:
        base = max(votes, key=lambda k: votes[k])
        stray = [c for c, s in sorted(dominant.items()) if s != base]
        if stray and votes[base] >= 2:
            notes.append({
                "dim": "文风统一", "severity": "major", "chapter": None,
                "note": "引号制式跨章混用：第 %s 章用%s、其余章以%s为主——签约稿须"
                        "按项目交付要求统一体例；此项不代表文学文风或平台判断"
                        % ("、".join(str(c) for c in stray),
                           _STYLE_NAMES[dominant[stray[0]]], _STYLE_NAMES[base]),
                "quote": _style_snippet(texts[stray[0]],
                                        *_STYLE_OPEN_CLOSE[dominant[stray[0]]])})
    for c, t in sorted(texts.items()):
        counts = _quote_style_counts(t)
        used = sorted(k for k, v in counts.items() if v >= _STYLE_MIN_QUOTES)
        if len(used) >= 2:
            notes.append({
                "dim": "文风统一", "severity": "major", "chapter": c,
                "note": "第 %d 章引号制式章内混用（%s并存）——同章两制是编辑肉眼"
                        "可辨的体例伤，统一为本稿主流制式后才能提交"
                        % (c, "、".join(_STYLE_NAMES[k] for k in used)),
                "quote": _style_snippet(t, *_STYLE_OPEN_CLOSE[used[0]])})
    for c, t in raw.items():
        if t.startswith("\ufeff"):
            notes.append({
                "dim": "文风统一", "severity": "major", "chapter": c,
                "note": "第 %d 章文件带 UTF-8 BOM 残留——发布管线原样搬运时首行"
                        "会多出不可见字符，交稿前须清除" % c,
                "quote": t.lstrip("\ufeff")[:30].replace("\n", " ")})
    return notes