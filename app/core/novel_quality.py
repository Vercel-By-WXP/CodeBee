# -*- coding: utf-8 -*-
"""Deterministic quality checks for signing-oriented Chinese web fiction.

The LLM remains responsible for literary judgment.  This module supplies
stable rubric defaults and small text signals so reviewers can cite evidence
for the common rejection reasons instead of relying on vague impressions.
"""
from __future__ import annotations

import re
import math
import unicodedata
from difflib import SequenceMatcher
from .novel_quality_common import (  # noqa: F401 - 旧调用面再导出
    OPENING_DIM, SIGNING_RUBRIC, _FEEDBACK_ID_INLINE_LIMIT,
    _positive_chapter_number,
)
from .novel_quality_feedback import (  # noqa: F401 - 旧调用面再导出
    _CHAPTER_PLACEHOLDER_PHRASES, _DIALOGUE_RE, _DUP_MAX_NOTES, _DUP_MIN_DIALOGUE_CHARS, _DUP_MIN_PARA_CHARS, _DUP_MIN_SENT_CHARS, _EMOTION_RE, _METAPHOR_RE, _METRIC_BEAT_RE, _NEAR_METAPHOR_CANDIDATES, _NEAR_METAPHOR_POSTINGS_PER_GRAM, _NEAR_METAPHOR_RATIO, _NEAR_REPEAT_CANDIDATES, _NEAR_REPEAT_GRAMS, _NEAR_REPEAT_POSTINGS_PER_GRAM, _NEAR_REPEAT_RATIO, _NEAR_REPEAT_SENTENCE_MAX, _NEAR_REPEAT_SENTENCE_MIN, _NUMERIC_BEAT_RE, _OPENING_CAUSAL_CONTINUATION_RE, _OPENING_CHOICE_RE, _OPENING_CONSEQUENCE_RE, _OPENING_TRIGGER_RE, _REPEAT_CLAUSE_MAX, _REPEAT_CLAUSE_MIN, _REPEAT_MAX_NOTES, _REPEAT_OPENING_MIN, _REPEAT_SENTENCE_MIN, _SENTENCE_RE, _STOCK_PHRASES,
    _chapter_text_map, _feedback_analysis_is_substantive,
    _feedback_has_unresolved_conclusion, _is_periodic_text, feedback_check_errors,
    feedback_check_issues, feedback_record_key, merge_feedback_checks,
    review_evidence_errors,
)

from .novel_quality_style import (  # noqa: F401 - 旧调用面再导出
    _quote_style_counts, _style_snippet, style_consistency_notes,
)

SIGNING_CHECKPOINT_WORDS = 20000   # 项目检查点默认值；不是通用平台规则，调用方应核对当前后台

_NON_CAUSAL_REASONS = {
    "保持", "未变化", "无", "剧情需要", "为了推进剧情", "推动剧情",
    "设定如此", "作者安排", "方便剧情", "情节需要",
    "未说明", "随后", "之后", "接着", "突然", "剧情发展", "自然发展",
    "afterward", "afterwards", "later", "later on", "then", "suddenly",
    "same", "unchanged", "no change", "none",
}
_GENERIC_CAUSAL_FILLERS = (
    "因为某种原因", "由于某种原因", "不知为何", "不明原因", "莫名其妙",
    "情况有变", "情况突然变化", "发生变故", "出了意外", "发生意外",
    "不知怎么回事", "不清楚原因", "事情变了", "局势变化",
    "for some reason", "for unknown reasons", "for reasons unknown",
    "somehow", "circumstances changed", "things changed", "something happened",
    "an accident happened", "an unexplained change",
    "the plot required it", "the story required it", "for plot purposes",
    "for the sake of the plot", "plot convenience", "natural progression",
    "author's choice", "the author decided",
)


def _has_causal_reason(reason):
    """Reject empty/timing-only explanations; a cause needs concrete content."""
    normalized = re.sub(r"[\s\u3000，,。；;：:！？!?]", "", str(reason or "")).casefold()
    non_causal = {
        re.sub(r"\s+", "", phrase).casefold() for phrase in _NON_CAUSAL_REASONS
    }
    if not normalized or normalized in non_causal:
        return False
    vague_phrases = _GENERIC_CAUSAL_FILLERS + (
        "为了推进剧情", "剧情需要", "剧情发展", "作者安排", "方便剧情", "自然发展",
    )
    return not any(re.sub(r"\s+", "", token).casefold() in normalized
                   for token in vague_phrases)


def _opening_source_compact(text):
    """Return scan characters with their original manuscript offsets.

    Markdown headings are excluded from the scan, but the offsets continue to
    refer to the unmodified source so diagnostics can locate the actual text.
    """
    chars, offsets = [], []
    source_offset = 0
    for line in str(text or "").splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if not re.match(r"^\s{0,3}#{1,6}\s", content):
            for index, char in enumerate(line):
                if not char.isspace():
                    chars.append(char)
                    offsets.append(source_offset + index)
        source_offset += len(line)
    return "".join(chars), offsets


def _opening_source_end(offsets, count, source_length):
    """Map a compact-text character limit to an exclusive source offset."""
    return offsets[count - 1] + 1 if len(offsets) >= count else source_length


def _continuity_quote_key(value):
    """Normalize manuscript quotes and supplied evidence identically.

    Reviewer quotes often differ only by Chinese book quotes, em dashes,
    ellipses, compatibility-width punctuation, or mixed punctuation copied
    from a markdown renderer. Apply canonical Unicode normalization, then
    remove common prose/Markdown punctuation and whitespace while preserving
    symbols and name punctuation such as ``+1``, ``A-B``, ``A/B`` and ``甲·乙``.
    This remains an anchored containment test rather than fuzzy paraphrase.
    """
    value = unicodedata.normalize("NFC", str(value or ""))
    semantic_punctuation = {"-", "/", "／", "·", "‐", "‑"}
    return "".join(
        char for char in value
        if not char.isspace() and char not in ("\u200b", "\ufeff")
        and (not unicodedata.category(char).startswith("P")
             or char in semantic_punctuation)
    )


def _continuity_states_compatible(left, right):
    """Treat an unopposed textual refinement as the same continuity state.

    Reviewers often describe a short state as "在客栈" and another as
    "在客栈等人". Exact-string comparison called those a plot jump even though
    the second only adds detail. Containment is accepted only when neither
    state carries an explicit negation, so "活着" and "并非活着" remain
    conflicting. This narrow rule avoids pretending to solve general semantics.
    """
    a = _continuity_quote_key(left).casefold()
    b = _continuity_quote_key(right).casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        negation = re.compile(
            r"不|未|无|没|非|\b(?:not|no|never|without|cannot|can't|"
            r"isn't|aren't|wasn't|weren't)\b", re.I)
        return not (negation.search(str(left).casefold())
                    or negation.search(str(right).casefold()))
    return False


def opening_signal_notes(text, trigger_chars=600, payoff_chars=1200):
    """Return conservative, manuscript-anchored opening risk signals.

    These checks explain a weak opening for revision; they do not assert a
    platform rule or require a particular genre device.  A trigger is any
    concrete event/action marker, while an immediate payoff is a character
    choice followed by a visible consequence.  The caller may combine these
    observations with literary review scores.
    """
    source = str(text or "")
    body = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", source, flags=re.M)
    body = body.strip()
    if not body:
        return []
    compact, source_offsets = _opening_source_compact(source)
    try:
        trigger_chars = max(120, int(trigger_chars or 600))
    except (TypeError, ValueError, OverflowError):
        trigger_chars = 600
    try:
        payoff_chars = max(trigger_chars, int(payoff_chars or 1200))
    except (TypeError, ValueError, OverflowError):
        payoff_chars = max(trigger_chars, 1200)
    trigger_window = compact[:trigger_chars]
    payoff_window = compact[:payoff_chars]
    quote = body[:100].strip()
    notes = []
    choice_matches = list(_OPENING_CHOICE_RE.finditer(payoff_window))
    consequence_matches = list(_OPENING_CONSEQUENCE_RE.finditer(payoff_window))
    has_trigger = bool(_OPENING_TRIGGER_RE.search(trigger_window)
                       or _OPENING_CHOICE_RE.search(trigger_window))
    has_choice = bool(choice_matches)
    # A consequence hundreds of characters later may belong to a different
    # beat. Require it to follow a choice closely, with at most one sentence
    # boundary, and inspect every choice so an early idle verb cannot mask a
    # later meaningful decision. This stays advisory because lexical causality
    # cannot be proven by a deterministic text check.
    def follows_choice(choice, consequence):
        if consequence.start() < choice.end() or consequence.start() - choice.end() > 180:
            return False
        gap = payoff_window[choice.end():consequence.start()]
        boundaries = list(re.finditer(r"[。！？!?；;\n]", gap))
        if len(boundaries) > 1:
            return False
        if boundaries:
            # A consequence in the next sentence is only attributable to the
            # choice when the prose explicitly carries the causal transition.
            continuation = gap[boundaries[0].end():]
            if not _OPENING_CAUSAL_CONTINUATION_RE.match(continuation):
                return False
        return True

    has_consequence = any(follows_choice(choice, consequence)
                          for choice in choice_matches
                          for consequence in consequence_matches)
    if len(compact) >= trigger_chars and not has_trigger:
        notes.append({
            "dim": OPENING_DIM, "severity": "minor", "chapter": 1,
            "note": "开篇切入偏慢：前 %d 字未出现可定位的具体事件或行动，故事承诺尚未落地——"
                    "请把正在发生的异常、冲突或任务前置，并保留人物视角"
                    % trigger_chars,
            "quote": quote,
            "evidence_type": "absence_window",
            "scan_window": {"start_char": 0,
                            "end_char": _opening_source_end(
                                source_offsets, trigger_chars, len(source))},
            "advisory": True,
            "kind": "开篇缺少具体触发事件",
        })
    if len(compact) >= payoff_chars and not (has_choice and has_consequence):
        missing = []
        if not has_choice:
            missing.append("人物选择")
        if not has_consequence:
            missing.append("可见后果")
        notes.append({
            "dim": OPENING_DIM, "severity": "minor", "chapter": 1,
            "note": "开篇即时兑现不足：前 %d 字缺少%s——"
                    "让主角做出有代价的选择，并让局面产生可见变化"
                    % (payoff_chars, "与".join(missing)),
            "quote": (body[:160] if body else quote),
            "evidence_type": "absence_window",
            "scan_window": {"start_char": 0,
                            "end_char": _opening_source_end(
                                source_offsets, payoff_chars, len(source))},
            "advisory": True,
            "kind": "开篇缺少人物选择或即时后果",
        })
    return notes


def _prose_sentences(text):
    """Return normalized prose sentences, excluding Markdown headings."""
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", str(text or ""), flags=re.M)
    out = []
    for sentence in _SENTENCE_RE.findall(text):
        normalized = re.sub(r"[\s\u3000]+", "", sentence)
        normalized = normalized.strip("。！？!?；;，,、:：\"'“”‘’「」『』")
        # Synthetic fixtures and intentional sound effects can be long while
        # carrying one repeated character; they are not a reusable sentence
        # template and would create noisy release blockers.
        if len(normalized) >= _REPEAT_SENTENCE_MIN and len(set(normalized)) >= 8:
            out.append(normalized)
    return out


def _prose_clauses(text):
    """Return substantive comma/semicolon clauses for cross-chapter checks."""
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", str(text or ""), flags=re.M)
    out = []
    for clause in re.split(r"[，,。！？!?；;：:\n]+", text):
        normalized = re.sub(r"[\s\u3000]+", "", clause)
        normalized = normalized.strip("\"'“”‘’「」『』（）()[]【】")
        if (_REPEAT_CLAUSE_MIN <= len(normalized) <= _REPEAT_CLAUSE_MAX
                and len(set(normalized)) >= 6):
            out.append(normalized)
    return out


def _chapter_list_text(chapters):
    return "、".join("第 %d 章" % int(chapter) for chapter in chapters)


def _stock_phrase_notes(texts):
    """Summarize repeated stock actions/rhetoric across chapter texts."""
    notes = []
    for phrase in _STOCK_PHRASES:
        hits = []
        total = 0
        for chapter, text in sorted(texts.items()):
            count = str(text).count(phrase)
            if count:
                hits.append(chapter)
                total += count
        if len(hits) >= 3 or total >= 4:
            notes.append({
                "dim": "文风统一", "severity": "major" if len(hits) >= 3 else "minor",
                "chapter": None,
                "note": "惯用修辞/动作重复：‘%s’在%s共出现 %d 次——"
                        "请改用人物专属反应、环境细节或具体选择承载情绪"
                        % (phrase, _chapter_list_text(hits), total),
                "quote": phrase,
                "kind": "惯用修辞重复",
            })
    return notes


def chapter_craft_notes(text, chapter=None):
    """Find chapter-local template signals that cross-chapter checks miss.

    These are manuscript editing warnings, not a detector for authorship or a
    prediction of a platform decision.  A single simile or number is normal;
    only repeated clusters are reported.  The thresholds target the observed
    failure mode where a chapter stacks several metric ladders and generic
    emotional placeholders around otherwise plausible plot beats.
    """
    source = str(text or "").replace("\r\n", "\n").strip()
    if not source:
        return []
    number_beats = []
    for paragraph in re.split(r"\n\s*\n", source):
        compact = re.sub(r"[\s\u3000。！？!?；;:：\"'“”‘’「」『』]", "", paragraph)
        if _NUMERIC_BEAT_RE.fullmatch(compact):
            number_beats.append(compact)
    metric_beats = []
    for sentence in _SENTENCE_RE.findall(source):
        sentence = re.sub(r"\s+", "", sentence)
        if _METRIC_BEAT_RE.search(sentence):
            metric_beats.append(sentence)
    number_beat_count = len(number_beats) + len(metric_beats)
    notes = []
    if number_beat_count >= 6:
        quote = (metric_beats[0] if metric_beats else "、".join(number_beats[:3]))[:120]
        notes.append({
            "dim": "文风统一", "severity": "major",
            "chapter": chapter, "quote": quote,
            "note": "本章独立数字/数量报数段过密（共 %d 段），并多次用递增数字代替场景变化——"
                    "保留真正改变人物处境的数据，其余改为具体反应、阻力或选择"
                    % number_beat_count,
            "kind": "章内数字报数模板重复",
        })

    metaphor_hits = [re.sub(r"[\s\u3000]+", "", item).strip("\"'“”‘’「」『』")
                     for item in _METAPHOR_RE.findall(source)]
    metaphor_hits = [item for item in metaphor_hits if len(item) >= 4]
    if len(metaphor_hits) >= 6:
        notes.append({
            "dim": "文风统一", "severity": "minor", "chapter": chapter,
            "quote": metaphor_hits[0][:80],
            "note": "本章泛化比喻密度偏高（‘像/仿佛/如同’共 %d 处）——"
                    "删去能由动作或物件直接呈现的比喻，保留真正承担人物视角的意象"
                    % len(metaphor_hits),
            "kind": "章内泛化比喻密集",
        })

    placeholder_hits = []
    for phrase in _CHAPTER_PLACEHOLDER_PHRASES:
        count = source.count(phrase)
        if count:
            placeholder_hits.extend([phrase] * count)
    distinct_placeholders = {item for item in placeholder_hits}
    if len(placeholder_hits) >= 8 and len(distinct_placeholders) >= 2:
        notes.append({
            "dim": "情感细腻度", "severity": "major", "chapter": chapter,
            "quote": next(iter(distinct_placeholders)),
            "note": "本章用通用停顿/沉默/轻声动作承载情绪过多（共 %d 处、%d 类）——"
                    "改为人物当下的判断、身体反应和具体选择，避免同一情绪占位"
                    % (len(placeholder_hits), len(distinct_placeholders)),
            "kind": "章内情绪占位动作重复",
        })

    separators = len(re.findall(r"^\s*---+\s*$", source, flags=re.M))
    if separators >= 4 and len(re.sub(r"\s+", "", source)) < 12000:
        notes.append({
            "dim": "节奏控制", "severity": "minor", "chapter": chapter,
            "quote": source[:80],
            "note": "本章在较短篇幅内切换 %d 个场景段落，容易把多个高潮和钩子并排堆放——"
                    "保留一个主冲突的阶段结果，其余信息延后或改为下一章行动"
                    % (separators + 1),
            "kind": "章内桥段堆叠",
        })
    return notes


def _metaphor_hits(texts):
    """Collect normalized simile fragments and the chapters containing them."""
    hits = {}
    for chapter, text in sorted(texts.items()):
        for match in sorted(set(_METAPHOR_RE.findall(str(text)))):
            phrase = re.sub(r"[\s\u3000]+", "", match).strip("\"'“”‘’「」『』")
            if len(phrase) >= 8 and len(set(phrase)) >= 5:
                chapters = hits.setdefault(phrase, [])
                if chapter not in chapters:
                    chapters.append(chapter)
    return hits


def _near_duplicate_sentence_groups(texts):
    """Group long cross-chapter sentences that reuse nearly the same frame."""
    sentences = []
    for chapter, text in sorted(texts.items()):
        for sentence in sorted(set(_prose_sentences(text))):
            if _NEAR_REPEAT_SENTENCE_MIN <= len(sentence) <= _NEAR_REPEAT_SENTENCE_MAX:
                sentences.append((chapter, sentence))
    if len(sentences) < 2:
        return []

    parents = list(range(len(sentences)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    postings = {}
    for index, (_chapter, sentence) in enumerate(sentences):
        grams = set(sentence[offset:offset + 3]
                    for offset in range(len(sentence) - 2))
        overlaps = {}
        # Rare shingles carry more discriminating information.  Ignore very
        # common shingles and cap each posting scan so long manuscripts retain
        # bounded candidate-generation work per sentence.
        informative = sorted(
            ((len(postings.get(gram, ())), gram) for gram in grams
             if postings.get(gram)))
        informative = [(count, gram) for count, gram in informative
                       if count <= _NEAR_REPEAT_POSTINGS_PER_GRAM]
        for _count, gram in informative[:_NEAR_REPEAT_GRAMS]:
            for previous in postings[gram][-_NEAR_REPEAT_POSTINGS_PER_GRAM:]:
                if sentences[previous][0] == _chapter:
                    continue
                overlaps[previous] = overlaps.get(previous, 0) + 1
        min_overlap = min(_NEAR_REPEAT_GRAMS, max(5, int(len(grams) * 0.18)))
        candidates = sorted(
            ((count, previous) for previous, count in overlaps.items()
             if count >= min_overlap), reverse=True)[:_NEAR_REPEAT_CANDIDATES]
        for _count, previous in candidates:
            ratio = SequenceMatcher(None, sentence, sentences[previous][1],
                                    autojunk=False).ratio()
            if ratio >= _NEAR_REPEAT_RATIO:
                union(index, previous)
        for gram in grams:
            postings.setdefault(gram, []).append(index)

    groups = {}
    for index, row in enumerate(sentences):
        groups.setdefault(find(index), []).append(row)
    result = []
    for rows in groups.values():
        chapters = sorted({chapter for chapter, _sentence in rows})
        variants = list(dict.fromkeys(sentence for _chapter, sentence in rows))
        if len(chapters) >= 2 and len(variants) >= 2:
            chapter_quotes = {}
            for chapter, sentence in sorted(rows):
                chapter_quotes.setdefault(chapter, sentence[:80])
            result.append((chapters, variants, chapter_quotes))
    return sorted(result, key=lambda group: (-len(group[0]), group[0], group[1][0]))


def _near_duplicate_metaphor_groups(metaphors):
    """Group lightly varied simile images without comparing every pair.

    Exact phrase reuse is reported separately. This catches near copies such
    as changing one modifier in an otherwise repeated image, using rare
    three-character shingles to keep candidate work bounded on long books.
    """
    rows = [(phrase, chapters) for phrase, chapters in sorted(metaphors.items())
            if chapters and len(phrase) >= 8]
    if len(rows) < 2:
        return []
    parents = list(range(len(rows)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    postings = {}
    for index, (phrase, _chapters) in enumerate(rows):
        grams = set(phrase[offset:offset + 3] for offset in range(len(phrase) - 2))
        informative = sorted((len(postings.get(gram, ())), gram)
                             for gram in grams if postings.get(gram))
        candidates = {}
        for _count, gram in informative[:24]:
            for previous in postings[gram][- _NEAR_METAPHOR_POSTINGS_PER_GRAM:]:
                candidates[previous] = candidates.get(previous, 0) + 1
        likely = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[
            :_NEAR_METAPHOR_CANDIDATES]
        for previous, overlap in likely:
            minimum = min(4, max(2, int(min(len(phrase), len(rows[previous][0])) * 0.12)))
            if overlap < minimum:
                continue
            if SequenceMatcher(None, phrase, rows[previous][0], autojunk=False).ratio() \
                    >= _NEAR_METAPHOR_RATIO:
                left, right = find(index), find(previous)
                if left != right:
                    parents[right] = left
        for gram in grams:
            postings.setdefault(gram, []).append(index)

    groups = {}
    for index, (phrase, chapters) in enumerate(rows):
        groups.setdefault(find(index), []).extend((phrase, chapter) for chapter in chapters)
    results = []
    for group in groups.values():
        chapter_numbers = sorted({chapter for _phrase, chapter in group})
        variants = list(dict.fromkeys(phrase for phrase, _chapter in group))
        if len(chapter_numbers) >= 2 and len(variants) >= 2:
            chapter_quotes = {}
            for phrase, chapter in sorted(group, key=lambda row: (row[1], row[0])):
                chapter_quotes.setdefault(str(chapter), phrase)
            results.append((chapter_numbers, variants, chapter_quotes))
    return sorted(results, key=lambda group: (-len(group[0]), group[0], group[1][0]))


def repetition_notes(chapters):
    """Find book-level sentence/template repetition with manuscript evidence.

    ``chapters`` accepts ``{chapter_no: text}`` or an iterable of pairs.  The
    result uses the normal issue shape so callers can feed it into the same
    repair and publish gates as model findings.  Two chapters produce a
    review warning; three or more chapters produce a major issue.  Very short
    phrases are intentionally ignored because names, titles, and genre terms
    are expected to recur.
    """
    if isinstance(chapters, dict):
        pairs = list(chapters.items())
    else:
        try:
            pairs = list(chapters or [])
        except TypeError:
            pairs = []
    texts = {}
    for pair in pairs:
        try:
            chapter, text = pair
        except (TypeError, ValueError):
            continue
        number = _positive_chapter_number(chapter)
        if number is None:
            continue
        if not isinstance(text, str):
            continue
        body = text.strip()
        if body:
            if number in texts:
                texts[number] = texts[number] + "\n" + body
            else:
                texts[number] = body
    if len(texts) < 2:
        return []

    stock_notes = _stock_phrase_notes(texts)
    metaphor_hits = _metaphor_hits(texts)
    near_metaphor_groups = _near_duplicate_metaphor_groups(metaphor_hits)
    near_group_phrases = {
        phrase for _chapters, variants, _chapter_quotes in near_metaphor_groups
        for phrase in variants
    }
    near_metaphor_notes = []
    for chapters_seen, variants, chapter_quotes in near_metaphor_groups:
        near_metaphor_notes.append({
            "dim": "文风统一",
            "severity": "major" if len(chapters_seen) >= 3 else "minor",
            "chapter": None,
            "note": "跨章近似比喻重复：%s反复使用相近意象，虽替换少量修饰词仍沿用同一比喻骨架——"
                    "请确认是否为有意母题，否则改换意象来源或感官通道"
                    % _chapter_list_text(chapters_seen),
            "quote": variants[0],
            "chapter_quotes": chapter_quotes,
            "kind": "跨章近似比喻重复",
        })
    reserved_notes = stock_notes[:1] + near_metaphor_notes[:1]

    def finish_notes(current):
        # Reserve report slots for repeated stock phrases and near-reused
        # imagery. Otherwise many exact sentence findings can fill the cap
        # before these more specific style patterns reach the output. Major
        # findings always outrank minor warnings, regardless of which detector
        # produced them. Deduplicate reserved notes that were also collected by
        # the normal scan.
        combined = []
        seen = set()
        for note in current + reserved_notes:
            key = (note.get("kind"), note.get("chapter"), note.get("quote"))
            if key in seen:
                continue
            seen.add(key)
            combined.append(note)
        combined.sort(key=lambda note: note.get("severity") != "major")
        return combined[:_REPEAT_MAX_NOTES]

    notes = []
    sentence_hits = {}
    sentence_samples = {}
    sentence_chapter_samples = {}
    for chapter, text in sorted(texts.items()):
        for sentence in sorted(set(_prose_sentences(text))):
            key = _continuity_quote_key(sentence)
            if not key:
                continue
            chapters = sentence_hits.setdefault(key, [])
            if chapter not in chapters:
                chapters.append(chapter)
            sentence_samples.setdefault(key, sentence)
            sentence_chapter_samples.setdefault(key, {})[str(chapter)] = sentence
    for sentence_key, chapters_seen in sorted(
            sentence_hits.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(chapters_seen) < 2:
            continue
        severity = "major" if len(chapters_seen) >= 3 else "minor"
        sentence = sentence_samples[sentence_key]
        notes.append({
            "dim": "文风统一", "severity": severity,
            "chapter": None,
            "note": "跨章整句复用：%s出现同一完整句式（共 %d 章）——"
                    "请确认是否为必要的固定表达，否则改换叙述角度、动作载体和信息量"
                    % (_chapter_list_text(chapters_seen), len(chapters_seen)),
            "quote": sentence[:80],
            "chapter_quotes": {
                str(chapter): sentence_chapter_samples[sentence_key][str(chapter)][:80]
                for chapter in chapters_seen
            },
            "kind": "跨章整句重复",
        })
    for chapters_seen, variants, chapter_quotes in _near_duplicate_sentence_groups(texts):
        severity = "major" if len(chapters_seen) >= 3 else "minor"
        notes.append({
            "dim": "文风统一", "severity": severity, "chapter": None,
            "note": "跨章近似句式重复：%s多次沿用相同长句骨架——"
                    "请改变叙述主语、动作顺序和信息落点"
                    % _chapter_list_text(chapters_seen),
            "quote": variants[0][:80],
            "chapter_quotes": {str(chapter): quote
                               for chapter, quote in chapter_quotes.items()},
            "kind": "跨章近似句式重复",
        })
    # Repeated clauses catch a more subtle template pattern: each full sentence
    # differs, while the same substantial action or reaction is reused inside
    # it.  Short stock phrases remain the responsibility of the dedicated list
    # below, so a recurring name or beat-length fragment cannot block a book.
    clause_hits = {}
    for chapter, text in sorted(texts.items()):
        for clause in sorted(set(_prose_clauses(text))):
            clause_hits.setdefault(clause, []).append(chapter)
    full_sentence_clauses = set()
    for sentence_key, chapters_seen in sentence_hits.items():
        if len(chapters_seen) >= 2:
            full_sentence_clauses.update(_prose_clauses(sentence_samples[sentence_key]))
    for clause, chapters_seen in sorted(
            clause_hits.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(chapters_seen) < 2:
            continue
        if clause in full_sentence_clauses:
            continue
        chapter_quotes = {str(chapter): clause for chapter in chapters_seen}
        notes.append({
            "dim": "文风统一",
            "severity": "major" if len(chapters_seen) >= 3 else "minor",
            "chapter": None,
            "note": "跨章分句复用：%s重复使用同一较长动作/描写分句（共 %d 章）——"
                    "保留情节信息，改换动作载体、观察角度或句子节奏"
                    % (_chapter_list_text(chapters_seen), len(chapters_seen)),
            "quote": clause,
            "chapter_quotes": chapter_quotes,
            "kind": "跨章分句重复",
        })
    repeated_full_metaphors = {
        phrase for phrase, chapters_seen in metaphor_hits.items()
        if any(phrase in sentence_samples[sentence_key]
               for sentence_key, sentence_chapters in sentence_hits.items()
               if len(sentence_chapters) >= 2)
    }
    for phrase, chapters_seen in sorted(
            metaphor_hits.items(), key=lambda item: (-len(item[1]), item[0])):
        if (len(chapters_seen) < 2 or phrase in repeated_full_metaphors
                or phrase in near_group_phrases):
            continue
        notes.append({
            "dim": "文风统一",
            "severity": "major" if len(chapters_seen) >= 3 else "minor",
            "chapter": None,
            "note": "跨章比喻意象重复：%s反复使用相同的比喻片段（共 %d 章）——"
                    "改换意象来源、感官通道或叙述视角，避免只替换主语"
                    % (_chapter_list_text(chapters_seen), len(chapters_seen)),
            "quote": phrase,
            "chapter_quotes": {str(chapter): phrase for chapter in chapters_seen},
            "kind": "跨章比喻重复",
        })
    for near_metaphor_note in near_metaphor_notes:
        notes.append(near_metaphor_note)

    # Compare the first and last sentence of each chapter.  Matching a long
    # edge is a useful proxy for repeated chapter openings/closings while
    # avoiding false positives from recurring character names alone.
    for edge_name, index in (("开笔", 0), ("收束", -1)):
        edge_hits = {}
        for chapter, text in sorted(texts.items()):
            sentences = _prose_sentences(text)
            if not sentences:
                continue
            edge = sentences[index]
            key = edge[:_REPEAT_OPENING_MIN] if index == 0 else edge[-_REPEAT_OPENING_MIN:]
            if len(key) >= _REPEAT_OPENING_MIN:
                edge_hits.setdefault(key, []).append((chapter, edge))
        for key, rows in sorted(edge_hits.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(rows) < 2:
                continue
            chapters_seen = [row[0] for row in rows]
            notes.append({
                # Edge fragments are weak evidence by themselves: genre prose
                # often reuses a short scene-setting lead-in.  Keep this as a
                # review warning; full/near-full sentence matches own blocking.
                "dim": "文风统一", "severity": "minor", "chapter": None,
                "note": "跨章%s句式同款：%s共享相同的 %d 字起止结构——"
                        "必须改变切入视角、动作顺序或信息落点"
                        % (edge_name, _chapter_list_text(chapters_seen), len(key)),
                "quote": rows[0][1][:80] if index == 0 else rows[0][1][-80:],
                "chapter_quotes": {
                    str(chapter): (edge[:80] if index == 0 else edge[-80:])
                    for chapter, edge in rows
                },
                "kind": "跨章%s句式重复" % edge_name,
            })
    # Stock rhetoric was computed before the capped scans so it could retain a
    # slot when exact/near-repeat findings saturate the report.
    for stock_note in stock_notes:
        notes.append(stock_note)
    return finish_notes(notes)


def parse_continuity_lines(lines, chapter=None):
    """Parse reviewer ``<continuity>`` rows into a validated ledger shape.

    Required format is ``实体|属性|本章状态|变化原因|正文证据``.  Bad rows are
    ignored instead of becoming false facts; the reviewer still has to cite
    the actual manuscript in its normal ``issues`` output.
    """
    result = []
    for raw in lines if isinstance(lines, list) else []:
        parts = [str(x).strip() for x in str(raw or "").split("|")]
        if len(parts) < 5:
            continue
        entity, attr, state, reason, evidence = parts[:5]
        if not entity or not attr or not state or not evidence:
            continue
        result.append({
            "chapter": chapter,
            "entity": entity[:60], "attribute": attr[:80],
            "state": state[:160], "reason": reason[:160],
            "evidence": evidence[:240],
        })
    return result


def validate_continuity_rows(rows, manuscript):
    """Keep only continuity rows whose evidence is an anchored manuscript quote.

    Reviewer ledgers are state carried into future prompts, so an invented or
    paraphrased evidence string must not be allowed to become a fact.  Whitespace
    differences are ignored, while headings and short filler quotes remain
    invalid.
    """
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", str(manuscript or ""), flags=re.M)
    text = _continuity_quote_key(text)
    valid = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        evidence = _continuity_quote_key(row.get("evidence"))
        if len(evidence) < 8 or evidence not in text:
            continue
        reason = str(row.get("reason") or "").strip()
        if _has_causal_reason(reason):
            anchored_reason = _continuity_quote_key(reason)
            if len(anchored_reason) < 8 or anchored_reason not in text:
                continue
        valid.append(row)
    return valid


def continuity_conflict_notes(rows):
    """Turn contradictory reviewer ledger rows into anchored major issues.

    The latest review round for a chapter is expected to replace an earlier
    round.  Within that round, two reviewers disagreeing about the same
    entity/attribute is a conflict; across chapters, a changed state must carry
    an explicit transition reason.  This is a deterministic guardrail around
    semantic model judgement, not a claim that arbitrary prose can be fully
    understood by regex.
    """
    valid = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)
             and row.get("entity") and row.get("attribute") and row.get("state")]
    if not valid:
        return []
    # Keep only the latest submitted row set per chapter.  Rows from the first
    # failed review round must not fight with the final repaired chapter.
    by_chapter = {}
    for row in valid:
        chapter = _positive_chapter_number(row.get("chapter"))
        if chapter is None:
            continue
        by_chapter.setdefault(chapter, []).append(row)
    notes = []
    history = {}
    for chapter in sorted(by_chapter):
        current = by_chapter[chapter]
        grouped = {}
        for row in current:
            key = (_continuity_quote_key(row["entity"]),
                   _continuity_quote_key(row["attribute"]))
            grouped.setdefault(key, []).append(row)
        for (entity_key, attr_key), group in grouped.items():
            entity = str(group[0]["entity"]).strip()
            attr = str(group[0]["attribute"]).strip()
            state_by_key = {}
            for row in group:
                state_by_key.setdefault(_continuity_quote_key(row["state"]),
                                        str(row["state"]).strip())
            states = list(state_by_key.values())
            mutually_compatible = all(
                _continuity_states_compatible(left, right)
                for index, left in enumerate(states)
                for right in states[index + 1:])
            if len(states) > 1 and not mutually_compatible:
                notes.append({
                    "dim": "情节逻辑", "severity": "major", "chapter": chapter,
                    "note": "连续性账本冲突：第 %d 章对‘%s·%s’给出互斥状态：%s——"
                            "先统一人物/设定事实，再继续写作"
                            % (chapter, entity, attr, "、".join(states[:4])),
                    "quote": next((row["evidence"] for row in group if row.get("evidence")), ""),
                    "kind": "连续性账本冲突",
                })
                continue
            # Preserve the most informative compatible description for the
            # cross-chapter baseline without treating added detail as a change.
            state = max(states, key=lambda value: len(_continuity_quote_key(value)))
            state_key = _continuity_quote_key(state)
            previous = history.get((entity_key, attr_key))
            if previous and not _continuity_states_compatible(previous["state"], state):
                reasons = [str(row.get("reason") or "").strip() for row in group]
                has_causal_bridge = any(_has_causal_reason(reason) for reason in reasons)
                if not has_causal_bridge:
                    notes.append({
                        "dim": "情节逻辑", "severity": "major", "chapter": chapter,
                        "note": "人物/设定状态无因果变化：‘%s·%s’从第 %d 章的‘%s’变为‘%s’，"
                                "但没有说明触发事件或代价"
                                % (entity, attr, previous["chapter"], previous["state"], state),
                        "quote": group[0].get("evidence") or "",
                        "kind": "连续性状态跳变",
                    })
            history[(entity_key, attr_key)] = {
                "chapter": chapter, "state": state, "state_key": state_key,
            }
    return notes


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
    chapter = _positive_chapter_number(chapter) or 1
    if chapter == 1:
        return ("首章观察点（非硬门槛）：记录读者何时能辨认视角人物、故事承诺与正在发生的事；"
                "核心卖点是否通过具体场景兑现：区分只提到能力/身份/目标，与它实际改变行动并带来阶段结果；"
                "检查读者是否得到与题材相称的首个回报或新看点，可为爽感、信息发现、悬念推进、关系变化、惊险、幽默或氛围，"
                "不要求固定爽点或打脸；同时判断这个回报是否具体、有力度且具辨识度，不能把有事件/有结果本身等同于足够好看；"
                "铺垫是否符合题材且有阅读价值；结尾是否形成自然的继续阅读理由。"
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
    for item in chapter_craft_notes(text, chapter=chapter):
        hints.append("章内复查提示：" + str(item.get("note") or ""))
    # 逐字重复（整段/整句/完整对白）是成稿事故而非文风选择，直接点名证据；
    # 起草验收闸（pipeline._chapter_state）用同一判定打回，评审面据此复核
    for line in format_duplicate_notes(duplicate_notes(text)):
        hints.append("逐字重复（成稿事故，须删并保留信息量最高的一处）：" + line)
    opening_hints = []
    if chapter == 1:
        opening_hints = [
            "开篇观察（非硬门槛，需结合文学判断）：" + str(item.get("note") or "")
            for item in opening_signal_notes(text)
        ]
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
        # Opening observations must survive a saturated generic signal list;
        # otherwise the exact rejection pattern can be detected but omitted
        # from the revision prompt. Reserve space while preserving the cap.
        "hints": (hints[:max(0, 6 - len(opening_hints))] + opening_hints)[:6],
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
        ch = _positive_chapter_number(s.get("chapter"))
        if ch is None:
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
    try:
        chars = max(1, int(summary.get("chars") or 0))
    except (TypeError, ValueError, OverflowError):
        chars = 1
    def number(value):
        try:
            result = float(value or 0.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return result if math.isfinite(result) else 0.0
    return {
        "avg_sentence_chars": number(summary.get("avg_sentence_chars")),
        "dialogue_per_k": round(1000.0 * number(summary.get("dialogue_chars")) / chars, 1),
        "emotion_per_k": round(1000.0 * number(summary.get("emotion_action_hits")) / chars, 1),
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
    if not isinstance(baselines, (list, tuple)):
        return notes
    if isinstance(min_baseline, bool) or not isinstance(min_baseline, int) or min_baseline < 0:
        return notes
    base = [fingerprint(b) for b in baselines if isinstance(b, dict)]
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
    if not isinstance(means, dict):
        return False
    try:
        value = float(means.get(OPENING_DIM))
    except (TypeError, ValueError, OverflowError):
        return False
    try:
        line = float(threshold)
    except (TypeError, ValueError, OverflowError):
        return False
    return value < line
