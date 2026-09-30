"""Small local agent presence registry for the operator dashboard."""
from __future__ import annotations

import json
import threading
import time

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "agent_presence.json"
_STALE_S = 180


def _load():
    try:
        value = json.loads(_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save(value):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def heartbeat(agent_id, label="", capabilities=None, task_id="", status="idle", metadata=None):
    aid = str(agent_id or "").strip()[:100]
    if not aid:
        raise ValueError("agent_id 必填")
    if capabilities is not None and not isinstance(capabilities, list):
        raise ValueError("capabilities 必须是列表")
    if status is not None and not isinstance(status, str):
        raise ValueError("status 必须是文本")
    now = time.time()
    with _LOCK:
        data = _load()
        safe_meta = metadata if isinstance(metadata, dict) else {}
        # Presence is a hot, user-controlled endpoint; keep the durable
        # registry bounded even when an agent sends oversized diagnostics.
        safe_meta = {str(k)[:80]: str(v)[:500] for k, v in list(safe_meta.items())[:30]}
        data[aid] = {"agent_id": aid, "label": str(label or aid)[:120],
                     "capabilities": [str(x)[:80] for x in (capabilities or [])][:30],
                     "task_id": str(task_id or "")[:100], "status": str(status or "idle")[:30],
                     "metadata": safe_meta,
                     "last_seen": now}
        _save(data)
        return data[aid]


def list_agents():
    now = time.time()
    with _LOCK:
        rows = []
        for row in _load().values():
            item = dict(row)
            try:
                last_seen = float(item.get("last_seen") or 0)
            except (TypeError, ValueError):
                last_seen = 0.0
            age = max(0, now - last_seen)
            item["age_s"] = int(age)
            item["online"] = age <= _STALE_S
            rows.append(item)
        return sorted(rows, key=lambda x: (not x["online"], x.get("label") or ""))


def remove(agent_id):
    with _LOCK:
        data = _load()
        existed = str(agent_id or "") in data
        data.pop(str(agent_id or ""), None)
        _save(data)
        return existed
