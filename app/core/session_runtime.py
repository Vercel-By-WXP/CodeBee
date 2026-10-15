# -*- coding: utf-8 -*-
"""Durable session admission and workspace fencing.

The runtime is intentionally small and stdlib-only.  Session metadata is the
authoritative lifecycle record; the existing :mod:`session_log` remains the
append-only transcript used by the pipeline.  A workspace lease prevents stale
browser/mobile callers from mutating a newer owner after reconnect or restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path


class SessionBusyError(RuntimeError):
    pass


class SessionNotFoundError(KeyError):
    pass


def workspace_identity(workspace) -> str:
    path = str(Path(workspace).expanduser().resolve())
    return "ws-" + hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()[:24]


class WorkspaceLease:
    _lock = threading.RLock()

    def __init__(self, path, *, owner, epoch, expires_at, root=None):
        self.path = Path(path)
        self.owner = str(owner)
        self.epoch = int(epoch)
        self.expires_at = float(expires_at)
        self.root = Path(root) if root else self.path.parent

    @classmethod
    def _file(cls, workspace, root=None):
        base = Path(root or Path(workspace).expanduser().resolve().parent / ".codebee-leases")
        base.mkdir(parents=True, exist_ok=True)
        return base / (workspace_identity(workspace) + ".json")

    @classmethod
    def acquire(cls, workspace, owner, *, ttl=90.0, root=None):
        workspace = Path(workspace).expanduser().resolve()
        file = cls._file(workspace, root)
        now = time.time()
        with cls._lock:
            current = {}
            try:
                current = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                pass
            if current and float(current.get("expires_at") or 0) > now and str(current.get("owner")) != str(owner):
                raise RuntimeError("workspace lease held by another owner")
            epoch = int(current.get("epoch") or 0) + 1
            expires = now + max(0.01, float(ttl))
            payload = {"workspace": str(workspace), "identity": workspace_identity(workspace),
                       "owner": str(owner), "epoch": epoch, "expires_at": expires,
                       "updated_at": now}
            tmp = file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(file)
            return cls(file, owner=owner, epoch=epoch, expires_at=expires, root=file.parent)

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def valid(self):
        row = self._read()
        return bool(row and str(row.get("owner")) == self.owner
                    and int(row.get("epoch") or -1) == self.epoch
                    and float(row.get("expires_at") or 0) > time.time())

    def renew(self, ttl=90.0):
        with self._lock:
            if not self.valid():
                return False
            row = self._read()
            row["expires_at"] = time.time() + max(0.01, float(ttl))
            row["updated_at"] = time.time()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            self.expires_at = row["expires_at"]
            return True

    def release(self):
        with self._lock:
            if not self.valid():
                return False
            row = self._read()
            row["expires_at"] = 0
            row["released_at"] = time.time()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            return True


class SessionRuntime:
    def __init__(self, root):
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.registry = self.sessions_dir / "sessions.json"
        self._lock = threading.RLock()
        self._sessions = self._load()
        self._leases = {}

    def _load(self):
        try:
            data = json.loads(self.registry.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self):
        tmp = self.registry.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._sessions, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.registry)

    def _row(self, sid):
        row = self._sessions.get(str(sid))
        if not row:
            raise SessionNotFoundError(sid)
        return row

    def create(self, *, workspace, owner, session_id=None, route=None):
        with self._lock:
            sid = str(session_id or ("ses-" + secrets.token_hex(10)))
            if sid in self._sessions:
                raise ValueError("session already exists")
            ws = str(Path(workspace).expanduser().resolve())
            row = {"id": sid, "workspace": ws, "workspace_identity": workspace_identity(ws),
                   "owner": str(owner), "owner_epoch": 0, "status": "idle", "turn": 0,
                   "inflight": False, "inflight_count": 0, "route": {}, "created_at": time.time(),
                   "updated_at": time.time()}
            self._sessions[sid] = row
            self._save()
            return dict(row)

    def get(self, sid):
        with self._lock:
            return dict(self._row(sid))

    def list(self):
        with self._lock:
            return [dict(x) for x in self._sessions.values()]

    def lease(self, sid, owner=None):
        with self._lock:
            row = self._row(sid)
            lease = self._leases.get(sid)
            if lease is None or (owner and lease.owner != owner):
                lease = WorkspaceLease.acquire(row["workspace"], owner or row["owner"], root=self.root / "leases")
                self._leases[sid] = lease
                row["owner"] = lease.owner
                row["owner_epoch"] = lease.epoch
                row["updated_at"] = time.time()
                self._save()
            return lease

    def resume(self, sid, *, owner):
        with self._lock:
            row = self._row(sid)
            lease = WorkspaceLease.acquire(row["workspace"], owner, root=self.root / "leases")
            self._leases[sid] = lease
            row.update({"owner": str(owner), "owner_epoch": lease.epoch,
                        "status": "idle", "inflight": False, "updated_at": time.time()})
            self._save()
            return dict(row)

    def admit_turn(self, sid, content, *, route=None, allow_parallel=False):
        with self._lock:
            row = self._row(sid)
            lease = self.lease(sid)
            if not lease.valid():
                raise RuntimeError("workspace lease is stale")
            if row.get("status") == "closed":
                raise RuntimeError("session is closed")
            if row.get("inflight") and not allow_parallel:
                raise SessionBusyError(sid)
            row["turn"] = int(row.get("turn") or 0) + 1
            row["inflight"] = True
            row["inflight_count"] = int(row.get("inflight_count") or 0) + 1
            row["status"] = "running"
            row["route"] = dict(route or {})
            row["last_prompt"] = str(content or "")[:200]
            row["updated_at"] = time.time()
            self._save()
            return {"session_id": sid, "turn": row["turn"], "route": dict(row["route"]),
                    "owner_epoch": row["owner_epoch"]}

    def finish_turn(self, sid, result=None):
        with self._lock:
            row = self._row(sid)
            row["inflight_count"] = max(0, int(row.get("inflight_count") or 1) - 1)
            row["inflight"] = bool(row["inflight_count"])
            row["status"] = "running" if row["inflight"] else "idle"
            row["last_result"] = str(result or "")[:500]
            row["updated_at"] = time.time()
            self._save()
            return dict(row)

    def drain_turns(self, sid, result=None):
        """Set a run-bound session back to idle after an abnormal exit.

        A worker can die between admission and step finalization.  Keeping the
        inflight counter in that case makes the next retry look permanently
        busy.  The operation is scoped to the session owner and is idempotent;
        it never closes the durable session or releases its workspace lease.
        """
        with self._lock:
            row = self._row(sid)
            row["inflight_count"] = 0
            row["inflight"] = False
            row["status"] = "idle" if row.get("status") != "closed" else "closed"
            if result:
                row["last_result"] = str(result)[:500]
            row["updated_at"] = time.time()
            self._save()
            return dict(row)

    def close(self, sid):
        with self._lock:
            row = self._row(sid)
            row.update({"status": "closed", "inflight": False, "updated_at": time.time()})
            lease = self._leases.pop(sid, None)
            if lease:
                lease.release()
            self._save()
            return dict(row)


_DEFAULT = None
_DEFAULT_ROOT = None


def default_runtime():
    """Return the process-local runtime bound to the current data directory."""
    global _DEFAULT, _DEFAULT_ROOT
    try:
        from . import paths
        root = Path(paths.DATA_DIR)
    except Exception:
        root = Path.home() / ".codebee"
    if _DEFAULT is None or _DEFAULT_ROOT != root:
        _DEFAULT = SessionRuntime(root)
        _DEFAULT_ROOT = root
    return _DEFAULT
