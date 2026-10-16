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


def request(run_id, point, *, payload=None, timeout_s=0, now=None):
    point = str(point or "").strip().lower()
    if point not in {"decompose", "review", "repair"}:
        raise ValueError("介入点必须是 decompose、review 或 repair")
    stamp = float(time.time() if now is None else now)
    row = {"id": "hitl-" + secrets.token_hex(8), "run_id": str(run_id)[:120],
           "point": point, "status": "waiting", "payload": payload or {},
           "created_at": stamp, "expires_at": stamp + max(0, float(timeout_s)),
           "updated_at": stamp}
    with _LOCK:
        _append(row)
    return row


def list_pending(run_id=""):
    with _LOCK:
        rows = list(_rows().values())
        now = time.time()
        expired = []
        for row in rows:
            if row.get("status") == "waiting" and row.get("expires_at", 0) and row["expires_at"] <= now:
                updated = dict(row, status="expired", updated_at=now, note="介入超时")
                _append(updated)
                expired.append(updated)
        if expired:
            expired_map = {x["id"]: x for x in expired}
            rows = [expired_map.get(x.get("id"), x) for x in rows]
    return [x for x in rows if x.get("status") == "waiting" and (not run_id or x.get("run_id") == run_id)]


def decide(request_id, action, *, actor="user", note=""):
    action = str(action or "").lower()
    target = {"approve": "approved", "reject": "rejected", "continue": "approved"}.get(action)
    if not target:
        raise ValueError("action 必须是 approve、reject 或 continue")
    with _LOCK:
        row = _rows().get(str(request_id))
        if not row:
            raise KeyError("介入请求不存在")
        if row.get("status") == "waiting" and row.get("expires_at", 0) and row["expires_at"] <= time.time():
            updated = dict(row, status="expired", updated_at=time.time(), note="介入超时")
            _append(updated)
            return updated
        if row.get("status") != "waiting":
            return row
        updated = dict(row, status=target, actor=str(actor or "user")[:120],
                       note=str(note or "")[:1000], updated_at=time.time())
        _append(updated)
        return updated
