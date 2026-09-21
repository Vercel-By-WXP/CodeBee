# -*- coding: utf-8 -*-
"""自由文本脱敏基础函数；不依赖业务模块，供各类本地台账复用。"""
from __future__ import annotations

import re


_KEY_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[key]"),
    (re.compile(r"\b(?:Bearer|bearer)\s+\S+"), "Bearer [key]"),
    (re.compile(r"(?i)\b((?:api[_-]?|access[_-]?|secret[_-]?|auth[_-]?)(?:key|token|secret))"
                r"""["']?\s*[:=，,]\s*["']?[A-Za-z0-9._~+/=-]{8,}"""), r"\1[key]"),
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "[token]"),
)

_PATH_PATTERNS = (
    (re.compile(r"(?i)\b[A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*"),
     lambda m: "[path]" + m.group(0).split("\\")[-1]),
    (re.compile(r"(?i)\b(?:\\\\[^\\\s]+\\[^\s]+)"), "[path]"),
    (re.compile(r"(?:/Users/|/home/|~)[^\s\"':]+"),
     lambda m: "[path]" + m.group(0).rsplit("/", 1)[-1]),
)


def scrub_text(text, limit=600):
    """剥离常见密钥、令牌和绝对路径，再按字符上限截断。"""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    out = text
    for pattern, replacement in _KEY_PATTERNS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _PATH_PATTERNS:
        try:
            out = pattern.sub(replacement, out)
        except Exception:
            pass
    out = out.strip()
    return out[:limit] + "…" if len(out) > limit else out
