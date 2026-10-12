# -*- coding: utf-8 -*-
"""Append-only external human-review requests with idempotent transitions."""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "review-requests.jsonl"
_STATUSES = {"requested", "assigned", "approved", "changes_requested", "dismissed", "expired"}
_TRANSITIONS = {
    "assign": ("requested", "assigned"),
    "approve": ("assigned", "approved"),
    "changes_requested": ("assigned", "changes_requested"),
    "dismiss": (("requested", "assigned", "changes_requested"), "dismissed"),
    "expire": (("requested", "assigned", "changes_requested"), "expired"),
}


def _read():
    latest = {}
    try:
        lines = Path(_FILE).read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return latest
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("id"):
            latest[row["id"]] = row
    return latest


def _append(row):
    path = Path(_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def route_channel(paths_changed, rules=None, default_channel=""):
    """Select the channel with the most matching changed files."""
    paths_changed = [str(p or "") for p in (paths_changed or []) if str(p or "")]
    rules = [r for r in (rules or []) if isinstance(r, dict)]
    best, best_count = "", 0
    import fnmatch
    for rule in rules:
        channel = str(rule.get("channel") or "").strip()
        patterns = rule.get("paths") if isinstance(rule.get("paths"), list) else []
        count = sum(1 for path in paths_changed if any(fnmatch.fnmatchcase(path, str(p)) for p in patterns))
        if channel and count > best_count:
            best, best_count = channel, count
    return best or str(default_channel or "")[:200]


def create(run_id, *, summary="", paths=None, channel="", now=None):
    stamp = float(now if now is not None else time.time())
    row = {"id": "review-" + secrets.token_hex(10), "run_id": str(run_id or "")[:100],
           "summary": str(summary or "")[:1000],
           "paths": [str(p)[:500] for p in (paths or [])][:200],
           "channel": str(channel or "")[:200], "status": "requested",
           "version": 1, "created_at": stamp, "updated_at": stamp}
    with _LOCK:
        _append(row)
    return row


def get(request_id):
    with _LOCK:
        return _read().get(str(request_id or ""))


def transition(request_id, action, *, reviewer="", reason="", now=None):
    with _LOCK:
        row = _read().get(str(request_id or ""))
        if not row:
            raise KeyError("评审请求不存在")
        action = str(action or "").strip().lower()
        if action not in _TRANSITIONS:
            raise ValueError("不支持的评审状态操作")
        allowed, target = _TRANSITIONS[action]
        if row.get("status") == target:
            return row
        if row.get("status") not in allowed:
            raise ValueError("当前评审状态不允许此操作")
        stamp = float(now if now is not None else time.time())
        updated = dict(row, status=target, version=int(row.get("version") or 1) + 1,
                       updated_at=stamp)
        if reviewer:
            updated["reviewer"] = str(reviewer)[:200]
        if reason:
            updated["reason"] = str(reason)[:1000]
        _append(updated)
        return updated
