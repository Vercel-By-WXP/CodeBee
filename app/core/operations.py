# -*- coding: utf-8 -*-
"""Append-only external operation ledger.

External writes cannot promise exactly-once delivery.  Each write therefore
has a stable operation id and an explicit state: pending, confirmed, failed or
unknown.  Unknown means the caller must reconcile with the remote system before
retrying; it is never treated as success.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.error
from pathlib import Path

from . import paths

LOCK = threading.RLock()
STATUSES = ("pending", "confirmed", "failed", "unknown")
PENDING_TTL_SECONDS = 15 * 60


def _file(day=None) -> Path:
    day = day or time.strftime("%Y-%m-%d")
    return paths.DATA_DIR / "operations" / ("operations-%s.jsonl" % day[:7].replace("-", ""))


def _read_all():
    rows = []
    root = paths.DATA_DIR / "operations"
    try:
        files = sorted(root.glob("operations-*.jsonl"))
    except OSError:
        files = []
    for fp in files:
        try:
            for line in fp.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("operation_id"):
                    rows.append(row)
        except OSError:
            continue
    return rows


def _write_all(rows):
    """Test/support migration helper; production writes use append-only _append."""
    root = paths.DATA_DIR / "operations"
    root.mkdir(parents=True, exist_ok=True)
    fp = _file()
    fp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                  encoding="utf-8")


def _append(row):
    try:
        fp = _file(row.get("submitted_at") and
                   time.strftime("%Y-%m-%d", time.localtime(float(row["submitted_at"]))))
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
    except Exception:
        # Observability must not turn a remote write into a local failure.
        pass


def _request_hash(request) -> str:
    raw = json.dumps(request or {}, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _operation_id(target: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return "op-%s-%s-%s" % (stamp, secrets.token_hex(4),
                             hashlib.sha256(str(target).encode()).hexdigest()[:6])


def _safe_meta(metadata):
    if not isinstance(metadata, dict):
        return {}
    out = {}
    for key, value in metadata.items():
        name = str(key)[:40]
        if any(token in name.lower() for token in ("key", "token", "password", "secret")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = str(value)[:300] if isinstance(value, str) else value
    return out


def begin(target, request=None, *, task_id="", run_id="", metadata=None):
    operation_id = _operation_id(target)
    now = time.time()
    row = {
        "operation_id": operation_id,
        "target": str(target or "")[:160],
        "request_hash": _request_hash(request),
        "submitted_at": now,
        "status": "pending",
        "remote_receipt": "",
        "error": "",
        "last_reconciled_at": None,
        "task_id": str(task_id or "")[:80],
        "run_id": str(run_id or "")[:80],
        "metadata": _safe_meta(metadata),
    }
    with LOCK:
        _append(row)
    return operation_id


def update(operation_id, status, *, remote_receipt="", error="",
           metadata=None, reconciled=False):
    operation_id = str(operation_id or "").strip()
    status = str(status or "").strip().lower()
    if not operation_id or status not in STATUSES:
        return False
    with LOCK:
        # Read and transition-check under the same lock as append.  Two
        # concurrent callbacks must not race a pending operation into
        # conflicting terminal states.
        current = get(operation_id)
        if current is None:
            return False
        current_status = str(current.get("status") or "pending")
        # External effects are monotonic: a confirmed write must never be
        # downgraded by cleanup/error handling.  Unknown/failed can only be
        # promoted by an explicit remote reconciliation.
        if current_status == "confirmed" and status != "confirmed":
            return False
        if current_status in ("unknown", "failed") and status == "confirmed" and not reconciled:
            return False
        if current_status in ("unknown", "failed") and status != current_status and not reconciled:
            return False
        if status == "confirmed" and not remote_receipt:
            remote_receipt = current.get("remote_receipt") or ""
        now = time.time()
        row = {
            "operation_id": operation_id,
            "status": status,
            "remote_receipt": str(remote_receipt or "")[:300],
            "error": str(error or "")[:500],
            "last_reconciled_at": now if reconciled else None,
            "updated_at": now,
        }
        if metadata:
            row["metadata"] = _safe_meta(metadata)
        _append(row)
    return True


def confirm(operation_id, remote_receipt="", *, metadata=None):
    return update(operation_id, "confirmed", remote_receipt=remote_receipt,
                  metadata=metadata)


def fail(operation_id, error="", *, metadata=None):
    return update(operation_id, "failed", error=error, metadata=metadata)


def mark_unknown(operation_id, error="", *, metadata=None):
    return update(operation_id, "unknown", error=error, metadata=metadata)


def reconcile(operation_id, status, remote_receipt="", error="", metadata=None):
    """Record a remote lookup result for an unknown operation."""
    return update(operation_id, status, remote_receipt=remote_receipt, error=error,
                  metadata=metadata, reconciled=True)


def get(operation_id):
    operation_id = str(operation_id or "")
    latest = None
    with LOCK:
        for row in _read_all():
            if row.get("operation_id") == operation_id:
                latest = dict(latest or {}, **row)
    return latest


def recent(*, target="", status="", limit=100):
    latest = {}
    with LOCK:
        for row in _read_all():
            if target and str(row.get("target") or "") != str(target):
                continue
            latest[row["operation_id"]] = dict(latest.get(row["operation_id"]) or {}, **row)
    rows = list(latest.values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: float(r.get("updated_at") or r.get("submitted_at") or 0), reverse=True)
    return rows[:max(1, min(1000, int(limit or 100)))]


def recover_pending(*, now=None, ttl_seconds=PENDING_TTL_SECONDS):
    """Turn stale pending writes into unknown so restart never replays blindly."""
    now = time.time() if now is None else float(now)
    changed = 0
    for row in recent(status="pending", limit=1000):
        submitted = float(row.get("submitted_at") or 0)
        if submitted and now - submitted > max(1, float(ttl_seconds)):
            if mark_unknown(row["operation_id"], "进程重启后未收到远端回执，请先对账"):
                changed += 1
    return changed


def status_for_exception(error) -> str:
    text = str(error or "").lower()
    if isinstance(error, (TimeoutError, ConnectionError, urllib.error.URLError)) or any(x in text for x in
            ("timeout", "timed out", "超时", "connection", "connect", "connection reset",
             "断开", "断连", "连不上", "网络", "network", "unreachable", "disconnect")):
        return "unknown"
    return "failed"


def finish_exception(operation_id, error):
    status = status_for_exception(error)
    if status == "unknown":
        return mark_unknown(operation_id, str(error))
    return fail(operation_id, str(error))
