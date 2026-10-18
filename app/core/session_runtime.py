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
        self._heartbeat_lock = threading.RLock()
        self._heartbeat_stop = None
        self._heartbeat_thread = None

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

    def start_heartbeat(self, ttl=90.0, interval=None):
        """Keep an active run's lease alive until it finishes.

        Lease files are intentionally short lived so abandoned workers can be
        reclaimed.  Long running workers therefore need a process-local
        heartbeat; callers should still call :meth:`stop_heartbeat` during
        normal cleanup.
        """
        ttl = max(0.1, float(ttl))
        interval = (max(0.1, min(ttl / 3.0, 30.0))
                    if interval is None else max(0.1, float(interval)))
        with self._heartbeat_lock:
            if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
                return False
            stop = threading.Event()
            self._heartbeat_stop = stop

            def loop():
                while not stop.wait(interval):
                    try:
                        if not self.renew(ttl):
                            break
                    except (OSError, ValueError, TypeError, RuntimeError):
                        # Stop cleanly if the lease file is temporarily
                        # unreadable; a later admission can acquire a fresh one.
                        break

            thread = threading.Thread(target=loop, name="workspace-lease-heartbeat",
                                      daemon=True)
            self._heartbeat_thread = thread
            thread.start()
            return True

    def heartbeat_running(self):
        with self._heartbeat_lock:
            return bool(self._heartbeat_thread and self._heartbeat_thread.is_alive())

    def stop_heartbeat(self):
        with self._heartbeat_lock:
            stop = self._heartbeat_stop
            thread = self._heartbeat_thread
            self._heartbeat_stop = None
            self._heartbeat_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def release(self):
        self.stop_heartbeat()
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
        self.cleanup_expired_leases()

    def cleanup_expired_leases(self):
        """Remove lease records that can no longer fence a workspace."""
        lease_dir = self.root / "leases"
        if not lease_dir.exists():
            return 0
        now = time.time()
        removed = 0
        # Acquire/renew use the same class lock.  Holding it across the
        # read-and-unlink pair prevents cleanup from deleting a lease that was
        # atomically replaced after cleanup read an expired snapshot.
        with WorkspaceLease._lock:
            for path in lease_dir.glob("*.json"):
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if float(row.get("expires_at") or 0) > now:
                        continue
                    path.unlink()
                    removed += 1
                except (OSError, ValueError, TypeError):
                    continue
        return removed

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
                   "inflight": False, "inflight_count": 0, "active_turns": [],
                   "route": {}, "created_at": time.time(), "updated_at": time.time()}
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
            if (lease is None or not lease.valid()
                    or (owner and lease.owner != owner)):
                if lease is not None:
                    lease.stop_heartbeat()
                lease = WorkspaceLease.acquire(row["workspace"], owner or row["owner"], root=self.root / "leases")
                self._leases[sid] = lease
                row["owner"] = lease.owner
                row["owner_epoch"] = lease.epoch
                # Replacing a lease fences admissions from the previous owner.
                row["active_turns"] = []
                row["inflight_count"] = 0
                row["inflight"] = False
                if row.get("status") != "closed":
                    row["status"] = "idle"
                row["updated_at"] = time.time()
                self._save()
            return lease

    def resume(self, sid, *, owner):
        with self._lock:
            row = self._row(sid)
            old = self._leases.pop(sid, None)
            if old is not None:
                old.release()
            lease = WorkspaceLease.acquire(row["workspace"], owner, root=self.root / "leases")
            self._leases[sid] = lease
            row.update({"owner": str(owner), "owner_epoch": lease.epoch,
                        "status": "idle", "inflight": False,
                        "inflight_count": 0, "active_turns": [],
                        "updated_at": time.time()})
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
            token = secrets.token_urlsafe(18)
            active_turns = list(row.get("active_turns") or [])
            active_turns.append(token)
            row["active_turns"] = active_turns
            row["inflight"] = True
            row["inflight_count"] = int(row.get("inflight_count") or 0) + 1
            row["status"] = "running"
            row["route"] = dict(route or {})
            row["last_prompt"] = str(content or "")[:200]
            row["updated_at"] = time.time()
            self._save()
            lease.start_heartbeat()
            return {"session_id": sid, "turn": row["turn"], "route": dict(row["route"]),
                    "owner_epoch": row["owner_epoch"], "token": token}

    @staticmethod
    def _consume_admission(row, admission):
        """Consume one admission token, rejecting stale worker callbacks."""
        active = list(row.get("active_turns") or [])
        if admission:
            token = admission.get("token") if isinstance(admission, dict) else None
            epoch = admission.get("owner_epoch") if isinstance(admission, dict) else None
            try:
                epoch_mismatch = (epoch is not None
                                  and int(epoch) != int(row.get("owner_epoch") or -1))
            except (TypeError, ValueError):
                epoch_mismatch = True
            if epoch_mismatch or not token or token not in active:
                return False
            active.remove(token)
        elif active:
            # Backward compatibility for embedded callers. Pipeline paths pass
            # an admission token and therefore cannot consume a stale turn.
            active.pop()
        elif int(row.get("inflight_count") or 0) <= 0:
            return False
        row["active_turns"] = active
        return True

    def finish_turn(self, sid, result=None, *, admission=None):
        with self._lock:
            row = self._row(sid)
            if row.get("status") == "closed":
                return dict(row)
            if not self._consume_admission(row, admission):
                return dict(row)
            row["inflight_count"] = max(0, int(row.get("inflight_count") or 1) - 1)
            row["inflight"] = bool(row["inflight_count"])
            row["status"] = "running" if row["inflight"] else "idle"
            row["last_result"] = str(result or "")[:500]
            row["updated_at"] = time.time()
            self._save()
            if not row["inflight"]:
                lease = self._leases.pop(sid, None)
                if lease is not None:
                    lease.release()
            return dict(row)

    def drain_turns(self, sid, result=None, *, admissions=None, owner=None,
                    owner_epoch=None):
        """Set a run-bound session back to idle after an abnormal exit.

        A worker can die between admission and step finalization.  Keeping the
        inflight counter in that case makes the next retry look permanently
        busy.  The operation is scoped to the session owner and admission
        tokens and is idempotent.  Stale workers return without touching the
        current owner's turn or lease.
        """
        with self._lock:
            row = self._row(sid)
            if owner is not None and str(row.get("owner")) != str(owner):
                return dict(row)
            if owner_epoch is not None:
                try:
                    if int(row.get("owner_epoch") or -1) != int(owner_epoch):
                        return dict(row)
                except (TypeError, ValueError):
                    return dict(row)
            tokens = list(admissions or [])
            scoped = bool(tokens)
            if tokens:
                valid = False
                for admission in tokens:
                    if self._consume_admission(row, admission):
                        valid = True
                if not valid:
                    return dict(row)
            elif row.get("active_turns"):
                # Preserve the embedded/runtime API's legacy unscoped drain.
                # Pipeline finally blocks always provide owner fencing, so a
                # stale worker cannot use this compatibility path.
                if owner is not None or owner_epoch is not None:
                    return dict(row)
                self._consume_admission(row, {"token": row["active_turns"][-1],
                                              "owner_epoch": row.get("owner_epoch")})
                # Legacy callers historically drained every outstanding turn.
                row["active_turns"] = []
            if scoped:
                # Admissions can race with the caller's snapshot. Preserve
                # tokens admitted after that snapshot and derive the durable
                # count from the remaining token set.
                row["inflight_count"] = len(row.get("active_turns") or [])
                row["inflight"] = bool(row["inflight_count"])
            else:
                row["inflight_count"] = 0
                row["inflight"] = False
            if row.get("status") != "closed":
                row["status"] = "running" if row["inflight"] else "idle"
            if result:
                row["last_result"] = str(result)[:500]
            row["updated_at"] = time.time()
            self._save()
            if not row["inflight"]:
                lease = self._leases.pop(sid, None)
                if lease is not None:
                    lease.release()
            return dict(row)

    def close(self, sid):
        with self._lock:
            row = self._row(sid)
            row.update({"status": "closed", "inflight": False,
                        "inflight_count": 0, "active_turns": [],
                        "updated_at": time.time()})
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
