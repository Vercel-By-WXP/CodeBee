# -*- coding: utf-8 -*-
"""Append-only execution checkpoints with conservative replay semantics."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path

from . import paths
from .redact import scrub_text

_DIR = paths.DATA_DIR / "checkpoints"
_LOCK = threading.RLock()
_TERMINAL = {"done", "failed", "cancelled", "timeout", "unknown"}


def _file():
    return _DIR / ("checkpoints-%s.jsonl" % time.strftime("%Y%m"))


def _hash(value):
    if isinstance(value, (dict, list, tuple)):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    else:
        raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _append(row):
    _DIR.mkdir(parents=True, exist_ok=True)
    with _file().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def start(run_id, step, role, prompt="", *, input_data=None, attempt=1,
          idempotency_key=""):
    """Record a step start without persisting prompt contents."""
    now = time.time()
    try:
        attempt = max(1, min(100, int(attempt)))
    except (TypeError, ValueError):
        attempt = 1
    row = {"event_id": "cp-" + secrets.token_hex(10), "event": "start",
           "run_id": str(run_id or "")[:80], "step": int(step),
           "role": str(role or "")[:80], "status": "running",
           "attempt": attempt, "idempotency_key": str(idempotency_key or "")[:160],
           "prompt_sha256": _hash(prompt), "input_sha256": _hash(input_data),
           "created_at": now}
    with _LOCK:
        try:
            _append(row)
        except OSError:
            pass
    return row


def finish(run_id, step, status, *, error="", output=None, metadata=None):
    status = str(status or "unknown").strip().lower()
    if status not in _TERMINAL:
        status = "unknown"
    row = {"event_id": "cp-" + secrets.token_hex(10), "event": "finish",
           "run_id": str(run_id or "")[:80], "step": int(step),
           "status": status, "error": scrub_text(error, limit=500),
           "output_sha256": _hash(output), "metadata": {
               str(k)[:40]: v for k, v in (metadata or {}).items()
               if isinstance(v, (str, int, float, bool))
           }, "created_at": time.time()}
    with _LOCK:
        try:
            _append(row)
        except OSError:
            pass
    return row


def _read(run_id=""):
    rows = []
    try:
        files = sorted(_DIR.glob("checkpoints-*.jsonl"), reverse=True)
    except OSError:
        files = []
    for path in files[:24]:
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            for line in path.read_text(encoding="utf-8").splitlines()[-50000:]:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict) and (not run_id or row.get("run_id") == run_id):
                    rows.append(row)
        except OSError:
            continue
    return rows


def replay_preview(run_id):
    """Return latest checkpoint per step and whether automatic replay is safe."""
    with _LOCK:
        rows = _read(str(run_id or "")[:80])
    latest = {}
    starts = {}
    finishes = {}
    for row in rows:
        try:
            key = int(row.get("step"))
        except (TypeError, ValueError):
            continue
        if key < 0:
            continue
        latest[key] = dict(latest.get(key) or {}, **row)
        if row.get("event") == "start":
            starts[key] = row
        elif row.get("event") == "finish":
            finishes[key] = row
    steps = []
    for key in sorted(latest):
        item = dict(latest[key])
        if key in starts:
            item.setdefault("prompt_sha256", starts[key].get("prompt_sha256"))
            item.setdefault("attempt", starts[key].get("attempt", 1))
        steps.append(item)
    unknown = next((x for x in steps if x.get("status") in ("unknown", "running")
                    or x.get("event") != "finish"
                    or x.get("step") not in finishes
                    or x.get("step") not in starts), None)
    if not steps:
        unknown = {"reason": "checkpoint_not_found"}
    return {"run_id": str(run_id or ""), "steps": steps,
            "replayable": unknown is None,
            "blocked_reason": ("checkpoint_not_found" if not steps else
                               "requires_reconciliation" if unknown else "")}


def replay(run_id):
    """Alias used by integrations; intentionally returns a preview only."""
    return replay_preview(run_id)
