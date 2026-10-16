"""Human-in-the-loop approval records for configurable pipeline checkpoints."""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from . import paths

_FILE = paths.DATA_DIR / "interventions.jsonl"
_LOCK = threading.RLock()
_CONDITION = threading.Condition(_LOCK)
_TIMEOUT_ACTIONS = {"continue", "approve", "abort", "reject"}


def _rows():
    try:
        lines = Path(_FILE).read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return {}
    out = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            out[row["id"]] = row
    return out


def _append(row):
    Path(_FILE).parent.mkdir(parents=True, exist_ok=True)
    with Path(_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def _timeout_target(row):
    """Map a timeout policy to the decision consumed by the pipeline."""
    action = str(row.get("timeout_action") or "continue").strip().lower()
    return "rejected" if action in {"abort", "reject"} else "approved"


def _expire(row, now=None):
    """Append one timeout transition and return its durable representation."""
    if row.get("status") != "waiting":
        return row
    now = float(time.time() if now is None else now)
    target = _timeout_target(row)
    updated = dict(row, status="expired", resolution=target,
                   updated_at=now, note="介入超时")
    _append(updated)
    _CONDITION.notify_all()
    return updated


def request(run_id, point, *, payload=None, timeout_s=0, timeout_action="continue",
            now=None):
    point = str(point or "").strip().lower()
    if point not in {"decompose", "review", "repair"}:
        raise ValueError("介入点必须是 decompose、review 或 repair")
    try:
        timeout = max(0.0, float(timeout_s))
    except (TypeError, ValueError):
        timeout = 0.0
    timeout_action = str(timeout_action or "continue").strip().lower()
    if timeout_action not in _TIMEOUT_ACTIONS:
        raise ValueError("timeout_action 必须是 continue、approve、abort 或 reject")
    stamp = float(time.time() if now is None else now)
    row = {"id": "hitl-" + secrets.token_hex(8), "run_id": str(run_id)[:120],
           "point": point, "status": "waiting", "payload": payload or {},
           "timeout_action": timeout_action,
           # timeout_s=0 means no automatic timeout, matching the API default.
           "created_at": stamp, "expires_at": stamp + timeout if timeout else 0,
           "updated_at": stamp}
    with _CONDITION:
        _append(row)
        _CONDITION.notify_all()
    return row


def list_pending(run_id=""):
    with _CONDITION:
        rows = list(_rows().values())
        now = time.time()
        for index, row in enumerate(rows):
            if row.get("status") == "waiting" and row.get("expires_at", 0) and row["expires_at"] <= now:
                rows[index] = _expire(row, now)
    return [x for x in rows if x.get("status") == "waiting" and (not run_id or x.get("run_id") == run_id)]


def decide(request_id, action, *, actor="user", note=""):
    action = str(action or "").lower()
    target = {"approve": "approved", "reject": "rejected", "continue": "approved"}.get(action)
    if not target:
        raise ValueError("action 必须是 approve、reject 或 continue")
    with _CONDITION:
        row = _rows().get(str(request_id))
        if not row:
            raise KeyError("介入请求不存在")
        if row.get("status") == "waiting" and row.get("expires_at", 0) and row["expires_at"] <= time.time():
            return _expire(row)
        if row.get("status") != "waiting":
            return row
        updated = dict(row, status=target, actor=str(actor or "user")[:120],
                       note=str(note or "")[:1000], updated_at=time.time())
        _append(updated)
        _CONDITION.notify_all()
        return updated


def wait_for_decision(request_id, timeout=None):
    """Wait for an approval/rejection and return its pipeline decision.

    Expired requests return the configured timeout resolution (``approved``
    for continue/approve, ``rejected`` for abort/reject).  ``timeout`` is an
    optional caller-side bound; reaching it leaves the request waiting and
    returns ``"waiting"`` rather than silently changing persisted state.
    """
    request_id = str(request_id or "")
    deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
    with _CONDITION:
        while True:
            row = _rows().get(request_id)
            if not row:
                raise KeyError("介入请求不存在")
            status = row.get("status")
            if status == "waiting":
                now = time.time()
                expires = row.get("expires_at") or 0
                if expires and expires <= now:
                    row = _expire(row, now)
                    return row.get("resolution") or _timeout_target(row)
                wait_for = None
                if expires:
                    wait_for = max(0.0, float(expires) - now)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return "waiting"
                    wait_for = remaining if wait_for is None else min(wait_for, remaining)
                # Poll briefly as well as waiting on the in-process condition:
                # an approval may be written by another service process.
                wait_for = 0.5 if wait_for is None else min(wait_for, 0.5)
                _CONDITION.wait(wait_for)
                continue
            if status == "expired":
                return row.get("resolution") or _timeout_target(row)
            return status
