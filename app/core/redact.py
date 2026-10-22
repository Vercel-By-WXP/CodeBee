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


# ---------------------------------------------------------------- 工具输出遮罩
# scrub_text 面向日志（会截断并抹掉路径）；工具输出要保留代码/路径可读性，
# 只遮罩「凭据形态的值」，让模型知道这里有一个密钥但看不到明文。

_PLACEHOLDER_HINTS = (
    "your_", "your-", "example", "placeholder", "changeme", "redacted",
    "xxxx", "****", "dummy", "sample",
)
_ASSIGN_NAME = (
    r"[A-Za-z0-9_.-]*(?:"
    r"api[_-]?key|apikey|access[_-]?key|secret[_-]?key|auth[_-]?key|"
    r"api[_-]?token|access[_-]?token|auth[_-]?token|secret[_-]?token|"
    r"client[_-]?secret|private[_-]?key|passw(?:or)?d|pwd|credentials?"
    r")[A-Za-z0-9_.-]*"
)
_RULES = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
                r"-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
     0, "[private key masked]", True),
    (re.compile(r"(?i)(?P<key>%s)[\"']?\s*[:=]\s*(?P<quote>[\"'])"
                r"(?P<value>[^\"'\r\n]{6,300})(?P=quote)" % _ASSIGN_NAME),
     "value", "[key]", False),
    (re.compile(r"(?i)(?P<key>%s)[\"']?\s*[:=]\s*(?P<value>[^\s\"',;]{6,})"
                % _ASSIGN_NAME),
     "value", "[key]", False),
    (re.compile(r"(?i)\bBearer\s+(?P<value>[A-Za-z0-9._~+/=-]{8,})"),
     "value", "[key]", False),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), 0, "sk-[key]", False),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
                r"[A-Za-z0-9_-]{8,}"), 0, "[jwt]", False),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
     0, "[key]", False),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0, "[key]", False),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0, "[key]", False),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), 0, "[key]", False),
    (re.compile(r"(?i)\b(?P<scheme>[a-z][a-z0-9+.-]*://)"
                r"(?P<user>[^/\s:@]{1,64}):(?P<value>[^/\s@]{6,})@"),
     "value", "[key]", False),
)


def _looks_secret(value):
    v = str(value or "").strip()
    if len(v) < 6:
        return False
    low = v.lower()
    if any(ch in v for ch in "${}%\r\n()"):
        return False
    if any(tok in low for tok in _PLACEHOLDER_HINTS):
        return False
    if low.startswith(("self.", "os.", "process.", "env.", "getenv",
                       "settings.", "config.", "request.")):
        return False
    return low not in ("none", "null", "true", "false", "undefined",
                       "password", "token", "secret")


def _secret_spans(text):
    """[(start, end, replacement, raw_value)]；重叠时保留更长的命中。"""
    spans = []
    for rule, group, replacement, multiline in _RULES:
        for match in rule.finditer(text):
            start, end = match.span(group)
            value = match.group(group)
            ok = len(value) >= 32 if multiline else _looks_secret(value)
            if ok:
                spans.append((start, end, replacement, value))
    spans.sort(key=lambda row: (row[0], -(row[1] - row[0])))
    out, last_end = [], -1
    for span in spans:
        if span[0] < last_end:
            continue
        out.append(span)
        last_end = span[1]
    return out


def find_secrets(text, limit=64, cap=200000):
    """凭据形态的值列表（去重、有界）；供运行期 taint 记账使用。"""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    values, seen = [], set()
    for _start, _end, _repl, value in _secret_spans(text[:cap]):
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def mask_secrets(text):
    """把凭据明文替换为占位符，保留键名、引号与代码结构。"""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    spans = _secret_spans(text)
    if not spans:
        return text
    out, pos = [], 0
    for start, end, replacement, _value in spans:
        out.append(text[pos:start])
        out.append(replacement)
        pos = end
    out.append(text[pos:])
    return "".join(out)
