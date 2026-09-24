# -*- coding: utf-8 -*-
"""Run/step/event tracing primitives.

The shape intentionally follows OpenTelemetry/OpenInference's trace -> span
relationship without adding a third-party dependency.  It is a read model of
the existing run and dispatch ledgers: no prompt, stdout, stderr, credentials,
or artifact contents are copied into the trace.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from .redact import scrub_text


SCHEMA_VERSION = 1


def _stable(prefix: str, *parts: object, length: int = 32) -> str:
    raw = prefix + ":" + ":".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def trace_id(run_id: object) -> str:
    """Return a deterministic 128-bit trace id for a run."""
    return _stable("codebee.trace", run_id, length=32)


def root_span_id(run_id: object) -> str:
    return _stable("codebee.root", run_id, length=16)


def span_id(run_id: object, step_no: object) -> str:
    return _stable("codebee.step", run_id, step_no, length=16)


def event_id(run_id: object, event: dict) -> str:
    """Stable event id used for deduplication during replay."""
    return _stable(
        "codebee.event",
        run_id,
        event.get("ts"),
        event.get("phase"),
        event.get("role"),
        event.get("selected"),
        event.get("result"),
        length=24,
    )


def _epoch(value):
    if value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _duration(step: dict):
    try:
        value = float(step.get("duration_s"))
        return round(max(0.0, value), 3)
    except (TypeError, ValueError):
        started = _epoch(step.get("started_at_epoch"))
        ended = _epoch(step.get("ended_at_epoch"))
        if started is not None and ended is not None:
            return round(max(0.0, ended - started), 3)
    return None


def _safe_event(event: dict, run_id: str) -> dict:
    """Keep only routing metadata from a dispatch event."""
    allowed = (
        "ts", "task_id", "task_type", "difficulty", "role", "phase",
        "selected", "participants", "candidates", "fallback",
        "selection_reason", "result", "verify_pass", "review_pass",
        "trace_id", "span_id", "parent_event_id", "event_id",
    )
    item = {key: event[key] for key in allowed if key in event}
    item.setdefault("trace_id", trace_id(run_id))
    item.setdefault("event_id", event_id(run_id, item))
    return _redact(item)


def _redact(value, limit=600):
    """Redact strings even when a legacy event predates dispatch_log scrub."""
    if isinstance(value, dict):
        return {key: _redact(item, 240) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, 240) for item in value]
    if isinstance(value, str):
        return scrub_text(value, limit=limit)
    return value


def build_trace(run: dict, events=()) -> dict:
    """Build a stable, redacted trace view from an existing run dictionary."""
    run_id = str(run.get("id") or "")
    tid = str(run.get("trace_id") or trace_id(run_id))
    rid = str(run.get("root_span_id") or root_span_id(run_id))
    spans = []
    for step in run.get("steps") or ():
        if not isinstance(step, dict):
            continue
        try:
            number = int(step.get("n"))
        except (TypeError, ValueError):
            continue
        span = {
            "trace_id": tid,
            "span_id": str(step.get("span_id") or span_id(run_id, number)),
            "parent_span_id": str(step.get("parent_span_id") or rid),
            "name": "agent.%s" % str(step.get("role") or "step")[:80],
            "step": number,
            "status": str(step.get("status") or "unknown"),
            "agent": str(step.get("agent") or "")[:80],
            "model": str(step.get("model") or "")[:120],
            "provider": str(step.get("provider") or "")[:120],
            "started_at": step.get("started_at_epoch"),
            "ended_at": step.get("ended_at_epoch"),
            "duration_s": _duration(step),
            "tokens": step.get("tokens", 0),
            "cost_usd": step.get("cost_usd", 0.0),
            "exit_code": step.get("exit_code"),
        }
        spans.append(span)
    safe_events = [_safe_event(item, run_id) for item in (events or ())
                   if isinstance(item, dict)]
    return {
        "version": SCHEMA_VERSION,
        "trace_id": tid,
        "root_span_id": rid,
        "run_id": run_id,
        "status": str(run.get("status") or "unknown"),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "spans": spans,
        "events": safe_events,
    }
