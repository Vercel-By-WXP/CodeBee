# -*- coding: utf-8 -*-
"""Run acceptance matrix and evidence normalization.

The matrix keeps correctness, performance, boundary behavior, exception
handling, readability and security as separate conclusions.  Missing evidence
is reported as ``not_evaluated``/``unknown`` rather than being inferred from a
successful process exit.
"""
from __future__ import annotations

import time

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

    duration = run.get("duration_s")
    if duration is None and run.get("started_at") and run.get("ended_at"):
        duration = "recorded"
    has_duration = isinstance(duration, (int, float)) and duration >= 0
    has_usage = any(isinstance(run.get(key), (int, float)) and run.get(key) > 0
                    for key in ("tokens", "cost_usd"))
    if has_duration or has_usage:
        out.append(_case("performance", "passed", {
            "source": "run.metrics", "duration_s": duration,
            "tokens": run.get("tokens", 0), "cost_usd": run.get("cost_usd", 0),
        }, "仅表示性能数据已记录，不代表达到业务阈值"))
    else:
        out.append(_case("performance", "not_evaluated", reason="缺少耗时或用量证据"))

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
