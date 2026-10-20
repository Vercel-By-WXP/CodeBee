"""Configurable review dimensions and quality fallback decisions.

The module is intentionally dependency-free and stores only review policy, not
credentials or manuscript content.  Existing flows may continue to pass a
plain list of rubric names; this module adds weights and policy metadata.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import atomic, paths

DEFAULT_DIMENSIONS = [
    {"id": "correctness", "name": "正确性", "weight": 1.0},
    {"id": "maintainability", "name": "可维护性", "weight": 1.0},
    {"id": "security", "name": "安全性", "weight": 1.0},
    {"id": "performance", "name": "性能", "weight": 1.0},
    {"id": "readability", "name": "可读性", "weight": 1.0},
]
_FILE = paths.DATA_DIR / "review-policy.json"
_LOCK = threading.RLock()


def _clean_dimensions(value):
    if isinstance(value, str):
        value = [x.strip() for x in value.replace("，", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        return [dict(x) for x in DEFAULT_DIMENSIONS]
    out = []
    seen = set()
    for item in value[:12]:
        if isinstance(item, str):
            item = {"id": item, "name": item, "weight": 1.0}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()[:80]
        ident = str(item.get("id") or name).strip().lower().replace(" ", "-")[:60]
        if not name or not ident or ident in seen:
            continue
        try:
            weight = max(0.01, min(100.0, float(item.get("weight", 1.0))))
        except (TypeError, ValueError):
            weight = 1.0
        out.append({"id": ident, "name": name, "weight": round(weight, 4)})
        seen.add(ident)
    return out or [dict(x) for x in DEFAULT_DIMENSIONS]


def default():
    return {"dimensions": [dict(x) for x in DEFAULT_DIMENSIONS],
            "min_score": 7.0, "min_model_family": "", "min_confidence": 0.0,
            "preferred_reviewer": ""}


def load():
    with _LOCK:
        try:
            raw = json.loads(Path(_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
    out = default()
    if isinstance(raw, dict):
        out.update({k: raw[k] for k in out if k in raw})
    out["dimensions"] = _clean_dimensions(out.get("dimensions"))
    try:
        out["min_score"] = max(1.0, min(10.0, float(out["min_score"])))
    except (TypeError, ValueError):
        out["min_score"] = 7.0
    try:
        out["min_confidence"] = max(0.0, min(1.0, float(out["min_confidence"])))
    except (TypeError, ValueError):
        out["min_confidence"] = 0.0
    out["min_model_family"] = str(out.get("min_model_family") or "")[:80]
    out["preferred_reviewer"] = str(out.get("preferred_reviewer") or "")[:120]
    return out


def save(policy):
    if not isinstance(policy, dict):
        raise ValueError("评审策略必须是对象")
    out = load()
    if "dimensions" in policy:
        dims = _clean_dimensions(policy.get("dimensions"))
        if not dims:
            raise ValueError("至少需要一个评审维度")
        out["dimensions"] = dims
    for key in ("min_score", "min_confidence"):
        if key in policy:
            try:
                value = float(policy[key])
            except (TypeError, ValueError):
                raise ValueError("%s 必须是数字" % key)
            out[key] = max(0.0, min(10.0 if key == "min_score" else 1.0, value))
    for key in ("min_model_family", "preferred_reviewer"):
        if key in policy:
            out[key] = str(policy[key] or "").strip()[:120]
    with _LOCK:
        atomic.write_json(_FILE, out)
    return out


def weighted_score(scores, dimensions=None):
    dims = _clean_dimensions(dimensions) if dimensions is not None else load()["dimensions"]
    total = weight = 0.0
    for dim in dims:
        try:
            score = float((scores or {}).get(dim["id"], (scores or {}).get(dim["name"])))
        except (TypeError, ValueError):
            continue
        if 1 <= score <= 10:
            total += score * dim["weight"]
            weight += dim["weight"]
    return round(total / weight, 3) if weight else None


def build_prompt(dimensions=None):
    dims = _clean_dimensions(dimensions) if dimensions is not None else load()["dimensions"]
    return "\n".join("- %s（权重 %.2f）：请给出 1-10 分及行级证据。" %
                     (d["name"], d["weight"]) for d in dims)


def assess(verdict, policy=None):
    """Mark a review as qualified or degraded according to policy."""
    policy = policy or load()
    verdict = dict(verdict or {})
    score = verdict.get("overall")
    if score is None:
        score = weighted_score(verdict.get("scores"), policy.get("dimensions"))
    reasons = []
    try:
        if score is None or float(score) < float(policy.get("min_score", 7.0)):
            reasons.append("评分低于最低门槛")
    except (TypeError, ValueError):
        reasons.append("评分不可用")
    family = str(verdict.get("reviewer_model_family") or verdict.get("model_family") or "")
    minimum = str(policy.get("min_model_family") or "")
    if minimum and family and family != minimum:
        reasons.append("评审模型族不满足最低要求")
    if minimum and not family:
        reasons.append("缺少评审模型族")
    try:
        confidence = float(verdict.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < float(policy.get("min_confidence", 0.0)):
        reasons.append("评审可信度低于最低门槛")
    verdict["overall"] = score
    verdict["review_quality"] = "qualified" if not reasons else "degraded"
    verdict["review_degraded"] = bool(reasons)
    verdict["review_degradation_reasons"] = reasons
    return verdict
