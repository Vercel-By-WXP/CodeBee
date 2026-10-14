# -*- coding: utf-8 -*-
"""Turn user wording into a small, auditable execution contract.

The contract is deliberately deterministic.  It does not reinterpret the
request with another model; it preserves the user's own sentences and makes
explicit constraints visible to every later agent.
"""
from __future__ import annotations

import re


VERSION = 1
MAX_ITEMS = 12
MAX_ITEM_CHARS = 360
MAX_RAW_CHARS = 1200

_SPLIT_RE = re.compile(
    r"(?<=[。！？!?；;，,\n])\s*|\s*(?<=\.)\s+(?=[A-Za-z0-9])")
_CLAUSE_JOIN_RE = re.compile(r"\s+(?:but|and|however)\s+|但是|但|并且|而且|同时", re.I)
_NEGATIVE_RE = re.compile(
    r"(?:不要|别|禁止|不得|不能|不可|切勿|避免|仅限|只能|must not|do not|don't|never|without)",
    re.I,
)
_POSITIVE_RE = re.compile(
    r"(?:必须|需要|请|要求|目标是|希望|要做到|应当|务必|修复|实现|完成|增加|补充|补上|补|添加|修改|保留|保持|must|should|implement|create|update|fix|deliver)",
    re.I,
)
_DELIVERABLE_RE = re.compile(
    r"(?:输出|产出|生成|保存|修改|创建|实现|返回|交付|报告|文件|落地|write|create|update|deliver)",
    re.I,
)


def _clean(value, cap=MAX_ITEM_CHARS):
    return " ".join(str(value or "").split())[:cap].strip()


def _sentences(text):
    raw = str(text or "").replace("\r", "\n")
    sentences = []
    for sentence in _SPLIT_RE.split(raw):
        sentences.extend(_CLAUSE_JOIN_RE.split(sentence))
    return [_clean(part) for part in sentences if _clean(part)]


def _unique(items):
    out = []
    for item in items:
        value = _clean(item)
        if value and value not in out:
            out.append(value)
        if len(out) >= MAX_ITEMS:
            break
    return out


def build(goal, context="", acceptance_criteria=None):
    """Build a bounded contract without changing the user's wording."""
    goal = _clean(goal, 2000)
    context = trusted_context(context)
    source = "\n".join(x for x in (goal, context) if x)
    sentences = _sentences(source)
    must_not, must, deliverables = [], [], []
    for sentence in sentences:
        if _NEGATIVE_RE.search(sentence):
            must_not.append(sentence)
        if _POSITIVE_RE.search(sentence) and not _NEGATIVE_RE.search(sentence):
            must.append(sentence)
        if _DELIVERABLE_RE.search(sentence):
            deliverables.append(sentence)
    criteria = _unique(acceptance_criteria or [])
    if criteria:
        must.extend(criteria)
    if not must and goal and not must_not:
        must.append(goal)
    return {
        "version": VERSION,
        "primary_goal": goal[:MAX_RAW_CHARS],
        "must": _unique(must),
        "must_not": _unique(must_not),
        "deliverables": _unique(deliverables),
        "raw_excerpt": source[:MAX_RAW_CHARS],
    }


def trusted_context(context):
    """Drop inline attachment material before extracting user requirements.

    ``attachments.context_block`` marks previews as untrusted.  Everything
    before that marker is the user's task context; the material after it is
    reference data and must never become a high-priority instruction.
    """
    text = str(context or "")
    marker = "<!-- codebee-attachments:start -->"
    return text.split(marker, 1)[0].rstrip() if marker in text else text


def normalize(value, *, goal="", context="", acceptance_criteria=None):
    """Normalize persisted or user-provided contracts and fill missing data."""
    if not isinstance(value, dict):
        return build(goal, context, acceptance_criteria)
    fallback = build(goal, trusted_context(context), acceptance_criteria)
    out = {"version": VERSION}
    for key in ("primary_goal", "raw_excerpt"):
        out[key] = _clean(value.get(key) or fallback[key], MAX_RAW_CHARS)
    for key in ("must", "must_not", "deliverables"):
        raw = value.get(key)
        out[key] = _unique(raw if isinstance(raw, list) else fallback[key])
    return out


def render(contract):
    """Render the contract as a high-priority prompt block."""
    c = normalize(contract)
    lines = [
        "## 用户意图契约（优先级高于默认做法，必须逐项核对）",
        "主目标：%s" % (c.get("primary_goal") or "（未提供）"),
    ]
    if c.get("must"):
        lines.append("必须完成：\n" + "\n".join("- " + x for x in c["must"]))
    if c.get("must_not"):
        lines.append("明确禁止/避免：\n" + "\n".join("- " + x for x in c["must_not"]))
    if c.get("deliverables"):
        lines.append("交付物线索：\n" + "\n".join("- " + x for x in c["deliverables"]))
    lines.append("执行前确认你理解了以上目标；完成前逐项核对，未满足的项目不得声称已完成。")
    return "\n\n".join(lines)


def ensure_task(task):
    """Return a task copy with a normalized contract and prompt block."""
    value = dict(task or {})
    contract = normalize(value.get("intent_contract"), goal=value.get("goal") or "",
                         context=trusted_context(value.get("context") or ""),
                         acceptance_criteria=value.get("acceptance_criteria") or [])
    value["intent_contract"] = contract
    return value, render(contract)
