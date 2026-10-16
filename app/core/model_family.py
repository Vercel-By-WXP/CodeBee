"""Model name normalization and family tracking helpers."""
from __future__ import annotations

import os
import re

FAMILY_PATTERNS = (
    ("OpenAI", re.compile(r"^(?:gpt-|o[1-9]|chatgpt|text-embedding)", re.I)),
    ("Anthropic", re.compile(r"^claude[-_ ]", re.I)),
    ("Alibaba", re.compile(r"^(?:qwen|通义|qwq)", re.I)),
    ("Google", re.compile(r"^(?:gemini|gemma)[-_]", re.I)),
    ("DeepSeek", re.compile(r"^deepseek[-_ ]", re.I)),
    ("Mistral", re.compile(r"^mistral[-_ ]", re.I)),
    ("智谱", re.compile(r"^(?:glm-|chatglm)", re.I)),
)


def family(model, default="unknown"):
    name = str(model or "").strip()
    for label, pattern in FAMILY_PATTERNS:
        if pattern.search(name):
            return label
    return default


def resolve_model(agent=None, result=None, environ=None):
    """Best-effort actual model lookup, preferring call result over config."""
    result = result if isinstance(result, dict) else {}
    agent = agent if isinstance(agent, dict) else {}
    environ = environ or os.environ
    model = result.get("model") or result.get("model_name") or agent.get("model")
    if not model:
        key = str(agent.get("id") or "").upper().replace("-", "_") + "_MODEL"
        model = environ.get(key) or environ.get("CODEBEE_MODEL")
    model = str(model or "").strip()
    return {"model": model, "family": family(model)}


def annotate(record, *, agent=None, result=None):
    out = dict(record or {})
    info = resolve_model(agent, result)
    if info["model"]:
        out["model"] = info["model"]
    out["model_family"] = info["family"]
    return out
