"""Bounded, keyless diagnostics for best-effort lifecycle hooks."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .redact import scrub_text

_LOCK = threading.RLock()
_CREDENTIAL = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|passwd|secret|token)"
    r"[\"']?\s*(?:[:=]\s*|\s+)[\"']?[^\s,;\"']+")


def safe_error(value):
    text = str(value or "")
    for name, secret in os.environ.items():
        if secret and len(secret) >= 4 and re.search(r"(?i)key|token|secret|password|credential", name):
            text = text.replace(secret, "[redacted]")
    text = _CREDENTIAL.sub(lambda m: m.group(1) + "=[redacted]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", text)
    return scrub_text(text, limit=600)


def _path(data_dir):
    return Path(data_dir) / "hook-diagnostics.json"


def _load(data_dir):
    try:
        path = _path(data_dir)
        if path.stat().st_size > 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record(data_dir, hook, payload, *, status, exit_code=None, duration_ms=0, error=""):
    # Never persist command, stdin, stdout, environment or task context.
    row = {"hook_id": safe_error(hook.get("id") or "test")[:80],
           "event": str(hook.get("event") or "")[:40], "at": time.time(),
           "run_id": safe_error(payload.get("run_id"))[:100],
           "task_id": safe_error(payload.get("task_id"))[:100],
           "status": status, "exit_code": exit_code,
           "duration_ms": round(max(0, duration_ms), 2), "error": safe_error(error)}
    with _LOCK:
        data = _load(data_dir)
        recent = [row] + list(data.get("recent") or [])
        errors = list(data.get("last_errors") or [])
        if status != "succeeded":
            errors = [row] + [x for x in errors if
                (x.get("hook_id"), x.get("event")) != (row["hook_id"], row["event"])]
        data = {"version": 1, "recent": recent[:100], "last_errors": errors[:50]}
        path = _path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return row


def view(data_dir):
    with _LOCK:
        data = _load(data_dir)
        return {"version": 1, "recent": list(data.get("recent") or [])[:100],
                "last_errors": list(data.get("last_errors") or [])[:50]}
