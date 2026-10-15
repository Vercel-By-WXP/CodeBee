# -*- coding: utf-8 -*-
"""Run acceptance matrix and evidence normalization.

The matrix keeps correctness, performance, boundary behavior, exception
handling, readability and security as separate conclusions.  Missing evidence
is reported as ``not_evaluated``/``unknown`` rather than being inferred from a
successful process exit.
"""
from __future__ import annotations

import math
import time
from datetime import datetime

CASES = ("correctness", "performance", "boundary", "exception",
         "readability", "security")


def _case(name, status, evidence=None, reason=""):
    return {"case": name, "status": status,
            "evidence": [evidence] if isinstance(evidence, dict) else [],
            "reason": str(reason or "")[:300]}


def _quality_verdict(run):
    verdict = run.get("verdict") or {}
    for key in ("pass", "publishable", "verify_pass"):
        if isinstance(verdict.get(key), bool):
            return verdict.get(key), key
    return None, ""


_PERFORMANCE_THRESHOLDS = (
    ("max_duration_s", "duration_s", "max"),
    ("max_cost_usd", "cost_usd", "max"),
    ("max_first_token_ms", "first_token_ms", "max"),
    ("min_tokens_per_sec", "tokens_per_sec", "min"),
)


def _number(value):
    """Return a finite float, or ``None`` for missing/invalid evidence."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 and math.isfinite(value) else None


def normalize_thresholds(value):
    """Validate public task parameters before any task mutation."""
    allowed = {key for key, _metric, _direction in _PERFORMANCE_THRESHOLDS}
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        raise ValueError("acceptance_thresholds 必须是仅含已知性能阈值的对象")
    normalized = {}
    for key, raw in value.items():
        number = _number(raw)
        if number is None:
            raise ValueError("acceptance_thresholds.%s 必须是有限非负数" % key)
        normalized[key] = number
    return normalized


def _performance_thresholds(run, task):
    """Prefer the run's frozen configuration, including an empty snapshot."""
    for owner in (run, task):
        if not isinstance(owner, dict):
            continue
        if "acceptance_thresholds" in owner:
            return owner["acceptance_thresholds"]
    return {}


def _performance_case(run, task):
    measured = {}
    metrics = tuple(dict.fromkeys(
        [metric for _threshold, metric, _direction in _PERFORMANCE_THRESHOLDS]
        + ["tokens", "input", "output", "cached", "reasoning"]))
    for metric in metrics:
        value = _number(run.get(metric))
        # Store initializes cost/tokens to zero even without usage evidence.
        if value is not None and (value > 0 or metric not in
                                  ("cost_usd", "tokens", "input", "output", "cached", "reasoning")):
            measured[metric] = value
    if "duration_s" not in measured:
        try:
            elapsed = (datetime.fromisoformat(run["ended_at"]) -
                       datetime.fromisoformat(run["started_at"])).total_seconds()
            if elapsed >= 0:
                measured["duration_s"] = elapsed
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    evidence = {"source": "run.metrics", "measured": measured,
                "thresholds": {}, "checks": []}
    try:
        thresholds = normalize_thresholds(_performance_thresholds(run, task))
    except ValueError as exc:
        return _case("performance", "not_evaluated", evidence=evidence, reason=str(exc))
    evidence["thresholds"] = thresholds
    for threshold_key, metric, direction in _PERFORMANCE_THRESHOLDS:
        if threshold_key not in thresholds:
            continue
        limit = thresholds[threshold_key]
        actual = measured.get(metric)
        passed = None
        if actual is not None:
            passed = actual <= limit if direction == "max" else actual >= limit
        evidence["checks"].append({"metric": metric, "actual": actual,
                                    "threshold": limit, "passed": passed,
                                    "direction": direction})
    if not evidence["checks"]:
        return _case("performance", "not_evaluated", evidence=evidence,
                     reason="已记录性能数据，但未声明性能阈值")
    if any(item["passed"] is False for item in evidence["checks"]):
        return _case("performance", "failed", evidence=evidence, reason="性能实测值未满足声明阈值")
    missing = [item["metric"] for item in evidence["checks"] if item["passed"] is None]
    if missing:
        return _case("performance", "not_evaluated", evidence=evidence,
                     reason="性能阈值已声明，但缺少实测值：%s" % ", ".join(missing))
    return _case("performance", "passed", evidence=evidence)


def evaluate_run(run, task=None):
    """Build a deterministic acceptance result from persisted run evidence."""
    run = run or {}
    task = task or {}
    status = str(run.get("status") or "")
    verdict = run.get("verdict") or {}
    out = []

    correct, source = _quality_verdict(run)
    if status in ("failed", "timeout", "cancelled"):
        out.append(_case("correctness", "unknown",
                         {"source": "run.status", "value": status},
                         "任务终态未形成可判定的质量结论"))
    elif correct is not None:
        out.append(_case("correctness", "passed" if correct else "failed",
                         {"source": "run.verdict", "field": source,
                          "value": bool(correct)}))
    else:
        out.append(_case("correctness", "not_evaluated", reason="缺少验证或质量门证据"))

    out.append(_performance_case(run, task))

    boundary = verdict.get("boundary_checks")
    if isinstance(boundary, list) and boundary and all(
            isinstance(x, dict) and isinstance(x.get("passed"), bool) for x in boundary):
        failed = [x for x in boundary if x.get("passed") is False]
        out.append(_case("boundary", "failed" if failed else "passed",
                         {"source": "run.verdict.boundary_checks", "count": len(boundary),
                          "failed": len(failed)}))
    else:
        out.append(_case("boundary", "not_evaluated", reason="当前任务未声明边界检查证据"))

    exception = verdict.get("exception")
    if isinstance(exception, bool):
        out.append(_case("exception", "passed" if exception else "failed",
                         {"source": "run.verdict.exception", "value": exception}))
    elif status in ("failed", "timeout"):
        out.append(_case("exception", "failed" if status in ("failed", "timeout") else "unknown",
                         {"source": "run.error", "status": status,
                          "error": str(run.get("error") or "")[:300]}))
    elif status == "cancelled":
        out.append(_case("exception", "unknown",
                         {"source": "run.status", "value": status}))
    else:
        out.append(_case("exception", "not_evaluated", reason="缺少独立异常处理证据"))

    for name, key in (("readability", "readability"), ("security", "security")):
        value = verdict.get(key)
        if isinstance(value, bool):
            out.append(_case(name, "passed" if value else "failed",
                             {"source": "run.verdict.%s" % key, "value": value}))
        else:
            out.append(_case(name, "not_evaluated", reason="未提供独立 %s 证据" % name))

    # 总体通过要求六个维度都有明确通过证据；缺证据不能被两个局部绿灯掩盖。
    return {"version": 1, "evaluated_at": time.time(),
            "passed": bool(out and all(x["status"] == "passed" for x in out)),
            "cases": out}
