# -*- coding: utf-8 -*-
"""Expiring model/provider evaluation conclusions.

An evaluation is evidence captured at a point in time, not a permanent
capability claim.  Results are kept as a small, content-free local ledger so
the UI or a router can distinguish a fresh ``passed`` result from an expired
one.  A provider health failure invalidates matching conclusions immediately;
the health probe can show recovery, but only a fresh evaluation restores
``passed``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

DEFAULT_TTL_SECONDS = 24 * 60 * 60
_LOCK = threading.RLock()


def _path() -> Path:
    from . import paths
    return Path(paths.DATA_DIR) / "evaluations.json"


def ttl_seconds() -> float:
    try:
        return max(60.0, float(os.environ.get(
            "TUTTI_EVALUATION_TTL_S", DEFAULT_TTL_SECONDS)))
    except (TypeError, ValueError):
        return float(DEFAULT_TTL_SECONDS)


def _read() -> dict:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(value: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Evaluation is telemetry.  A read-only data directory must not turn
        # a successful model call into a task failure.
        pass


def key(kind: str, provider_id: str, model: str = "") -> str:
    return "%s|%s|%s" % (str(kind or "provider"),
                          str(provider_id or ""), str(model or ""))


def decorate(result: dict, *, now=None) -> dict:
    """Attach an evaluated-at timestamp and expiry to an API response."""
    out = dict(result or {})
    now = time.time() if now is None else float(now)
    out["evaluated_at"] = now
    out["expires_at"] = now + ttl_seconds()
    out["fresh"] = True
    return out


def record(kind: str, provider_id: str, model: str, result: dict,
           *, protocol: str = "", requested_model: str = "",
           served_model: str = "") -> dict:
    """Persist safe metadata for one completed evaluation and return it."""
    out = decorate(result)
    entry = {
        "kind": str(kind or "provider"),
        "provider_id": str(provider_id or ""),
        "model": str(model or ""),
        "status": str(out.get("status") or "failed"),
        "ok": bool(out.get("ok")),
        "evaluated_at": out["evaluated_at"],
        "expires_at": out["expires_at"],
        "protocol": str(protocol or ""),
        "requested_model": str(requested_model or model or ""),
        "served_model": str(served_model or ""),
    }
    with _LOCK:
        data = _read()
        data.setdefault("entries", {})[key(kind, provider_id, model)] = entry
        _write(data)
    return out


def invalidate_provider(provider_id: str, *, reason: str = "health_failure") -> None:
    """Invalidate all conclusions for one provider after a health event."""
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        return
    with _LOCK:
        data = _read()
        data.setdefault("invalidated", {})[provider_id] = {
            "at": time.time(), "reason": str(reason or "health_failure")[:80]}
        _write(data)


def get(kind: str, provider_id: str, model: str = "", *, now=None):
    """Read an evaluation, returning a stale marker after TTL/health expiry."""
    now = time.time() if now is None else float(now)
    with _LOCK:
        data = _read()
    entry = (data.get("entries") or {}).get(key(kind, provider_id, model))
    if not isinstance(entry, dict):
        return None
    out = dict(entry)
    invalid = (data.get("invalidated") or {}).get(str(provider_id or "")) or {}
    invalid_at = float(invalid.get("at") or 0)
    expires_at = float(out.get("expires_at") or 0)
    stale_reason = ""
    if invalid_at and invalid_at >= float(out.get("evaluated_at") or 0):
        stale_reason = str(invalid.get("reason") or "health_failure")
    elif expires_at <= now:
        stale_reason = "ttl_expired"
    if stale_reason:
        out["fresh"] = False
        out["status"] = "stale"
        out["stale_reason"] = stale_reason
    else:
        out["fresh"] = True
    return out


def reset_for_tests() -> None:
    with _LOCK:
        try:
            _path().unlink()
        except OSError:
            pass
