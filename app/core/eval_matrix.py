# -*- coding: utf-8 -*-
"""Small declarative prompt/model evaluation matrix."""
from __future__ import annotations

import hashlib
import json
import math
import time


def normalize_manifest(value):
    if not isinstance(value, dict):
        raise ValueError("manifest 必须是对象")
    mid = str(value.get("id") or "matrix-%d" % int(time.time()))[:80]
    candidates = [str(x).strip()[:120] for x in value.get("candidates") or [] if str(x).strip()][:32]
    prompts = [str(x).strip()[:120] for x in value.get("prompts") or [] if str(x).strip()][:32]
    cases = []
    for index, item in enumerate(value.get("cases") or []):
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            continue
        cases.append({"id": str(item.get("id") or "case-%d" % (index + 1))[:80],
                      "prompt": str(item.get("prompt") or "")[:4000],
                      "expected": str(item.get("expected") or "")[:1000],
                      "tags": [str(x)[:40] for x in item.get("tags") or []][:8]})
    if prompts and not cases:
        cases = [{"id": "prompt-%d" % (i + 1), "prompt": p, "expected": "", "tags": []}
                 for i, p in enumerate(prompts)]
    if not candidates or not cases:
        raise ValueError("manifest 至少需要 candidates 和 cases")
    try:
        threshold = max(0.0, min(10.0, float(value.get("regression_threshold", 0.5))))
    except (TypeError, ValueError):
        threshold = 0.5
    normalized = {"id": mid, "candidates": candidates, "cases": cases,
                  "regression_threshold": threshold,
                  "seed": str(value.get("seed") or "")[:100],
                  "provider": str(value.get("provider") or "")[:120]}
    normalized["manifest_sha256"] = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return normalized


def _score(value):
    if isinstance(value, dict):
        value = value.get("score")
    try:
        value = float(value)
        return max(0.0, min(10.0, value)) if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def evaluate(manifest, results, baseline=None):
    manifest = normalize_manifest(manifest)
    results = results if isinstance(results, dict) else {}
    baseline = baseline if isinstance(baseline, dict) else {}
    matrix, regressions, totals = [], [], {}
    for candidate in manifest["candidates"]:
        scores = []
        for case in manifest["cases"]:
            candidate_results = results.get(candidate)
            candidate_baseline = baseline.get(candidate)
            value = (candidate_results.get(case["id"]) if isinstance(candidate_results, dict) else None)
            old_value = (candidate_baseline.get(case["id"]) if isinstance(candidate_baseline, dict) else None)
            score = _score(value)
            old = _score(old_value)
            row = {"candidate": candidate, "case": case["id"], "score": score,
                   "baseline": old, "delta": round(score - old, 4) if score is not None and old is not None else None}
            matrix.append(row)
            if row["delta"] is not None and row["delta"] < -manifest["regression_threshold"]:
                regressions.append(row)
            if score is not None:
                scores.append(score)
        totals[candidate] = round(sum(scores) / len(scores), 4) if scores else None
    ranked = sorted(((score, candidate) for candidate, score in totals.items() if score is not None),
                    reverse=True)
    return {"manifest": manifest, "matrix": matrix, "averages": totals,
            "best_candidate": ranked[0][1] if ranked else "",
            "regressions": regressions,
            "passed": not regressions}
