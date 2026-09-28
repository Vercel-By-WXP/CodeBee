# -*- coding: utf-8 -*-
"""Deterministic quality checks for signing-oriented Chinese web fiction.

The LLM remains responsible for literary judgment.  This module supplies
stable rubric defaults and small text signals so reviewers can cite evidence
for the common rejection reasons instead of relying on vague impressions.
"""
from __future__ import annotations

import re


SIGNING_RUBRIC = [
    "开篇吸引力",
    "情节推进",
    "文风统一",
    "情感细腻度",
    "节奏控制",
]

OPENING_DIM = "开篇吸引力"
SIGNING_CHECKPOINT_WORDS = 20000   # 平台口径：前 2 万字用于签约评估

_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_DIALOGUE_RE = re.compile(r"[“‘\"「『](.*?)[”’\"」』]", re.S)
_EMOTION_RE = re.compile(r"哭|泪|笑|怒|怕|颤|抖|咬|攥|握紧|心跳|呼吸|喉咙|指甲|沉默")


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
        try:
            short = int(target_words or 0) < 1200
        except (TypeError, ValueError):
            short = False
        core = ("前 1200 字" if short else "前 2000 字")
        return ("黄金一章门禁：前 100 字让读者知道主角是谁；前 200 字出现异常、欲望或冲突；"
                "前 600 字把困境具体化；%s 展示核心卖点；结尾必须留下可兑现的钩子。" % core
                + "禁止用世界观、环境或人物关系长铺垫开场。")
    return "本章门禁：开头尽快承接上一章并制造新变化；结尾必须留下下一步行动或可兑现的章末钩子。"


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
    if chapter == 1 and chars and chars < 600:
        hints.append("首章正文不足 600 字，无法证明前 600 字已进入具体困境")
    if avg >= 35 or long_sentences:
        hints.append("存在偏长句，复查长定语、重复说明与动作拆分")
    if len(paragraphs) >= 4 and chars / len(paragraphs) > 220:
        hints.append("段落偏长，复查手机阅读下的节奏与信息密度")
    if text and dialogue_chars == 0:
        hints.append("缺少对白证据，复查是否用场景/行动推进冲突")
    if text and emotion_hits == 0:
        hints.append("缺少可见的情绪动作或生理反应，复查复杂情感是否写得过于概括")
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
