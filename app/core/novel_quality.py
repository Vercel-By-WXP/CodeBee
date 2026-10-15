# -*- coding: utf-8 -*-
"""Deterministic quality checks for signing-oriented Chinese web fiction.

The LLM remains responsible for literary judgment.  This module supplies
stable rubric defaults and small text signals so reviewers can cite evidence
for the common rejection reasons instead of relying on vague impressions.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


SIGNING_RUBRIC = [
    "开篇吸引力",
    "情节推进",
    "文风统一",
    "情感细腻度",
    "节奏控制",
]

OPENING_DIM = "开篇吸引力"
SIGNING_CHECKPOINT_WORDS = 20000   # 项目检查点默认值；不是通用平台规则，调用方应核对当前后台


def feedback_check_errors(review: Any, chapters: Mapping[int, str], records: list[dict]) -> list[str]:
    """Require an explicit, manuscript-anchored response to each reused feedback case.

    This closes the gap between injecting a rejection record and actually testing
    the proposed fix. ``not_applicable`` is allowed only with a real quote and
    explanation; ``not_addressed`` is valid evidence of a defect, handled as a
    blocking issue by the caller so that revision can still run.
    """
    if not records:
        return []
    rows = review.get("feedback_check") if isinstance(review, dict) else None
    by_id = {str(x.get("id")): x for x in rows if isinstance(x, dict)} if isinstance(rows, list) else {}
    errors = []
    for record in records:
        rid = str(record.get("id") or "")
        if isinstance(rows, list) and sum(isinstance(x, dict) and str(x.get("id")) == rid for x in rows) > 1:
            errors.append("拒稿复盘 %s 出现重复处理结论" % rid)
        row = by_id.get(rid)
        if not row:
            errors.append("拒稿复盘 %s 缺少逐条处理结论" % rid)
            continue
        status = str(row.get("status") or "").strip()
        if status not in ("addressed", "not_addressed", "not_applicable"):
            errors.append("拒稿复盘 %s 的 status 无效" % rid)
        try:
            chapter = int(row.get("chapter"))
        except (TypeError, ValueError, OverflowError):
            chapter = -1
        text = chapters.get(chapter, "")
        text = "\n".join(line for line in text.splitlines()
                         if not re.match(r"^\s{0,3}#{1,6}\s", line))
        quote = str(row.get("quote") or "").strip()
        analysis = str(row.get("analysis") or "").strip()
        if len(re.sub(r"\s+", "", quote)) < 8 or quote not in text or not analysis:
            errors.append("拒稿复盘 %s 缺少可定位正文引文与处理分析" % rid)
    return errors


def feedback_check_issues(review: Any, records: list[dict]) -> list[dict]:
    """Translate validated unresolved checks into normal, anchored repair work."""
    wanted = {str(r.get("id")) for r in records}
    out = []
    for row in (review.get("feedback_check") or []) if isinstance(review, dict) else ():
        if not isinstance(row, dict) or str(row.get("id")) not in wanted:
            continue
        if str(row.get("status") or "").strip() != "not_addressed":
            continue
        try:
            chapter = int(row.get("chapter"))
        except (TypeError, ValueError, OverflowError):
            chapter = None
        quote = str(row.get("quote") or "").strip()
        analysis = str(row.get("analysis") or "").strip()
        if chapter is None or not quote or not analysis:
            continue  # feedback_check_errors owns malformed-row diagnostics
        out.append({"dim": "拒稿复盘", "severity": "major", "chapter": chapter,
                    "quote": quote,
                    "note": "[%s] 原问题仍未处理：%s" % (row["id"], analysis)})
    return out


def review_evidence_errors(review: Any, chapters: Mapping[int, str]) -> list[str]:
    """Require a real chapter quote and an explanation for each scored dimension.

    This validates provenance only, never the literary truth of the judgement.
    ``chapters`` must contain exactly the excerpts sent to the reviewer.
    """
    review = review if isinstance(review, dict) else {}
    rows = review.get("evidence")
    rows = rows if isinstance(rows, list) else []
    supported = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("dim") not in SIGNING_RUBRIC:
            continue
        try:
            text = chapters.get(int(row.get("chapter")), "")
        except (TypeError, ValueError, OverflowError):
            continue
        text = "\n".join(line for line in text.splitlines()
                         if not re.match(r"^\s{0,3}#{1,6}\s", line))
        quote = str(row.get("quote") or "").strip()
        if (len(re.sub(r"\s+", "", quote)) >= 8 and quote in text
                and str(row.get("analysis") or "").strip()):
            supported.add(row["dim"])
    return ["%s缺少可定位正文引文与分析" % dim
            for dim in SIGNING_RUBRIC if dim not in supported]

_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_DIALOGUE_RE = re.compile(r"[“‘\"「『](.*?)[”’\"」』]", re.S)
_EMOTION_RE = re.compile(r"哭|泪|笑|怒|怕|颤|抖|咬|攥|握紧|心跳|呼吸|喉咙|指甲|沉默")

# 逐字重复检测阈值（2026-10-11 连载实案校准）：整段 ≥40 字重复两遍是 113/118 章
# 实际事故形态；完整对白复现（≥6 字）同理——有意复诵必有动作/归属铺垫，评审员
# 对 113 章「这杆子歪着」的判词就是「若有意让角色复诵咀嚼，缺少铺垫」。叙述性
# 短句复沓（「早上探，往里。」5 字）是白描节奏手段，落在阈值之下不误伤。
_DUP_MIN_PARA_CHARS = 40
_DUP_MIN_SENT_CHARS = 12
_DUP_MIN_DIALOGUE_CHARS = 6
_DUP_MAX_NOTES = 4


def duplicate_notes(text):
    """章内逐字重复检测（确定性，成稿事故级）。

    抓三类：整段逐字重复、整句（≥12 字去空白）逐字重复、完整对白（≥6 字去空白）
    逐字重复。返回结构化证据列表 [{kind, count, quote}]，供落盘验收打回重起草与
    评审提示词引用；短句复沓等有意的节奏手段不在阈值之内，不构成证据。
    """
    text = str(text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    notes = []
    # ① 整段逐字重复（去空白比对；间隔段落/连排都算）
    para_counts = {}
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"\s+", "", p)
        if len(p) >= _DUP_MIN_PARA_CHARS:
            para_counts[p] = para_counts.get(p, 0) + 1
    dup_paras = [p for p, c in sorted(para_counts.items(), key=lambda kv: -kv[1])
                 if c >= 2]
    for p in dup_paras[:2]:
        notes.append({"kind": "整段重复", "count": para_counts[p], "quote": p[:40]})
    # ② 整句逐字重复（已被整段重复覆盖的句子不再单列，避免一条事故报一串）
    sent_counts = {}
    for s in _SENTENCE_RE.findall(text):
        s = re.sub(r"\s+", "", s)
        if len(s) >= _DUP_MIN_SENT_CHARS:
            sent_counts[s] = sent_counts.get(s, 0) + 1
    for s, c in sorted(sent_counts.items(), key=lambda kv: -kv[1]):
        if c < 2 or len(notes) >= _DUP_MAX_NOTES:
            continue
        if any(s in p for p in dup_paras):
            continue
        notes.append({"kind": "整句重复", "count": c, "quote": s[:40]})
    # ③ 完整对白逐字重复（阈值更低：成稿事故最常以「对白整句连排两遍」出现）
    seen_dialogue = {n["quote"] for n in notes}
    dial_counts = {}
    for d in _DIALOGUE_RE.findall(text):
        d = re.sub(r"\s+", "", d)
        if len(d) >= _DUP_MIN_DIALOGUE_CHARS:
            dial_counts[d] = dial_counts.get(d, 0) + 1
    for d, c in sorted(dial_counts.items(), key=lambda kv: -kv[1]):
        if c < 2 or len(notes) >= _DUP_MAX_NOTES:
            continue
        if any(d in q for q in seen_dialogue):
            continue
        notes.append({"kind": "对白重复", "count": c, "quote": d[:40]})
    return notes


def format_duplicate_notes(notes):
    """Render duplicate_notes evidence as human-readable lines."""
    lines = []
    for n in notes if isinstance(notes, list) else []:
        try:
            lines.append("%s %d 处：「%s」" % (n.get("kind", "重复"),
                                              int(n.get("count") or 0),
                                              str(n.get("quote") or "")[:40]))
        except (TypeError, ValueError):
            continue
    return lines


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
    pairs = list(chapters.items()) if isinstance(chapters, dict) else list(chapters or [])
    raw = {int(c): str(t or "") for c, t in pairs if str(t or "").strip()}
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


def rubric_for(task):
    """Return the configured rubric, or the signing rubric for serial novels."""
    task = task if isinstance(task, dict) else {}
    raw = task.get("rubric")
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        values = [str(x).strip() for x in raw if str(x).strip()]
        if values:
            return values[:8]
    if str(task.get("type") or "").lower() == "serial_novel":
        return list(SIGNING_RUBRIC)
    return []


def opening_requirements(chapter, target_words=None):
    """Return concise, chapter-aware acceptance requirements for prompts."""
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        chapter = 1
    if chapter == 1:
        return ("首章观察点（非硬门槛）：记录读者何时能辨认视角人物、故事承诺与正在发生的事；"
                "核心卖点是否通过具体场景兑现；铺垫是否符合题材且有阅读价值；结尾是否形成自然的继续阅读理由。"
                "根据文本效果和作者目标评估，不因未在固定字数内出现冲突/卖点/钩子而自动判失败。")
    return ("本章观察点：核对与前文的承接、局面/人物/信息/情绪的变化及本章叙事功能；"
            "结尾是否提供符合本章节奏的收束或继续阅读理由。不强制每章反转或悬念断章。")


def signal_summary(text, chapter=None):
    """Extract explainable text signals; no claim is made that they are scores."""
    text = str(text or "").replace("\r\n", "\n").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    sentence_lengths = [len(re.sub(r"\s+", "", s)) for s in sentences]
    dialogue_chars = sum(len(x) for x in _DIALOGUE_RE.findall(text))
    emotion_hits = len(_EMOTION_RE.findall(text))
    chars = len(re.sub(r"\s+", "", text))
    avg = round(sum(sentence_lengths) / len(sentence_lengths), 1) if sentence_lengths else 0.0
    long_sentences = sum(1 for n in sentence_lengths if n >= 55)
    hints = []
    if avg >= 35 or long_sentences:
        hints.append("存在偏长句，复查长定语、重复说明与动作拆分")
    if len(paragraphs) >= 4 and chars / len(paragraphs) > 220:
        hints.append("段落偏长，复查手机阅读下的节奏与信息密度")
    # 逐字重复（整段/整句/完整对白）是成稿事故而非文风选择，直接点名证据；
    # 起草验收闸（pipeline._chapter_state）用同一判定打回，评审面据此复核
    for line in format_duplicate_notes(duplicate_notes(text)):
        hints.append("逐字重复（成稿事故，须删并保留信息量最高的一处）：" + line)
    # Dialogue/emotion-action counts are descriptive signals only. Their absence
    # is not a quality defect: narrative voice, genre, and scene purpose vary.
    return {
        "chapter": chapter,
        "chars": chars,
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "avg_sentence_chars": avg,
        "long_sentences": long_sentences,
        "dialogue_chars": dialogue_chars,
        "emotion_action_hits": emotion_hits,
        "hints": hints[:6],
    }


def format_signal_summary(summary):
    """Render signal_summary output as a compact prompt block."""
    if not isinstance(summary, dict):
        return ""
    parts = [
        "文本信号（仅作证据，不替代文学判断）：约 %s 字；%s 段；%s 句；平均句长 %s 字；长句 %s；对白 %s 字；情绪动作线索 %s 处。"
        % (summary.get("chars", 0), summary.get("paragraphs", 0),
           summary.get("sentences", 0), summary.get("avg_sentence_chars", 0),
           summary.get("long_sentences", 0), summary.get("dialogue_chars", 0),
           summary.get("emotion_action_hits", 0))]
    hints = summary.get("hints") or []
    if hints:
        parts.append("确定性复查提示：" + "；".join(str(x) for x in hints))
    return "\n".join(parts)


def style_anchor_block(anchor):
    """Render the book-level style anchor as a prompt block; empty when absent."""
    anchor = str(anchor or "").strip()
    if not anchor:
        return ""
    return ("## 全书文风锚（大纲裁定，所有章节起草/修订/评审必须遵守；"
            "与其冲突处以本锚为准）\n%s\n" % anchor[:400])


def reuse_guard_block(samples):
    """Render prior chapters' opening/closing excerpts as a negative-example block.

    samples: [{"chapter": int, "head": str, "tail": str}] — 起草面据此避开同款
    开笔句式与同型收束（跨章复用红线：同一动作句式至多两次，第二次必须换载体
    或升级信息量）。无样例（首批第 1 章等）返回空串，不占提示词字节。
    """
    rows = []
    for s in samples if isinstance(samples, list) else []:
        if not isinstance(s, dict):
            continue
        try:
            ch = int(s.get("chapter"))
        except (TypeError, ValueError):
            continue
        head = str(s.get("head") or "").strip()
        tail = str(s.get("tail") or "").strip()
        if head:
            rows.append("- 第 %d 章开笔：「%s」" % (ch, head[:60]))
        if tail:
            rows.append("- 第 %d 章收束：「…%s」" % (ch, tail[-70:]))
    if not rows:
        return ""
    return ("## 跨章复用红线（起草前必读）\n"
            "此前各章已用的开笔/收束形态如下——本章不得再以同款句式开笔或同型"
            "句式收束；同一动作句式全书至多两次，第二次必须换载体或升级信息量，"
            "连读时章尾节奏不得趋同：\n" + "\n".join(rows) + "\n")


def fingerprint(summary):
    """Normalize a signal_summary into per-1000-char style metrics."""
    if not isinstance(summary, dict):
        return {}
    chars = max(1, int(summary.get("chars") or 0))
    return {
        "avg_sentence_chars": float(summary.get("avg_sentence_chars") or 0.0),
        "dialogue_per_k": round(1000.0 * float(summary.get("dialogue_chars") or 0) / chars, 1),
        "emotion_per_k": round(1000.0 * float(summary.get("emotion_action_hits") or 0) / chars, 1),
    }


def _median(values):
    xs = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else round((xs[mid - 1] + xs[mid]) / 2.0, 1)


def style_deviation_notes(baselines, summary, min_baseline=2):
    """Cross-chapter style drift warnings from deterministic fingerprints.

    baselines: signal_summary dicts of the book's previous chapters.  Needs at
    least ``min_baseline`` chapters before speaking (开篇几章没有稳定基线)；
    all notes are evidence hints for reviewers/revisers, never scores.
    """
    notes = []
    base = [fingerprint(b) for b in (baselines or []) if isinstance(b, dict)]
    base = [b for b in base if b]
    if len(base) < min_baseline:
        return notes
    cur = fingerprint(summary)
    if not cur:
        return notes
    avg_base = _median([b["avg_sentence_chars"] for b in base])
    if avg_base >= 12 and cur["avg_sentence_chars"] > avg_base * 1.45:
        notes.append("本章平均句长 %s 字，此前各章中位 %s 字——句式明显变长，"
                     "复查长定语、说明性插叙与节奏拖沓"
                     % (cur["avg_sentence_chars"], avg_base))
    elif avg_base >= 12 and cur["avg_sentence_chars"] < avg_base * 0.62:
        notes.append("本章平均句长 %s 字，此前各章中位 %s 字——句式明显变碎，"
                     "复查是否为凑字数拆散动作与因果"
                     % (cur["avg_sentence_chars"], avg_base))
    dial_base = _median([b["dialogue_per_k"] for b in base])
    if dial_base >= 40 and cur["dialogue_per_k"] < dial_base * 0.45:
        notes.append("对白占比骤降（本章 %s/千字，此前中位 %s/千字）——"
                     "复查是否换成了大段叙述，文风与推进方式是否漂移"
                     % (cur["dialogue_per_k"], dial_base))
    emo_base = _median([b["emotion_per_k"] for b in base])
    if emo_base >= 1.5 and cur["emotion_per_k"] < emo_base * 0.4:
        notes.append("情绪动作密度骤降（本章 %s/千字，此前中位 %s/千字）——"
                     "复查复杂情感是否退回「很伤心/很愤怒」式概括"
                     % (cur["emotion_per_k"], emo_base))
    return notes


def opening_below(means, threshold):
    """True when the signing opening dimension is scored below the line."""
    try:
        value = float((means or {}).get(OPENING_DIM))
    except (TypeError, ValueError):
        return False
    try:
        line = float(threshold)
    except (TypeError, ValueError):
        return False
    return value < line
