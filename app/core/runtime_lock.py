"""One service per data directory, shared by CLI and desktop launchers."""
from __future__ import annotations

import atexit
import os
from pathlib import Path
from typing import IO, Optional


class ServiceAlreadyRunning(RuntimeError):
    """Another process owns this data directory."""


class ServiceLease:
    """OS-backed lock; a crashed process releases it automatically."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._file: Optional[IO[bytes]] = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        # Never unlink this file: replacing its inode defeats POSIX file locks.
        handle = (self.directory / "service.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ServiceAlreadyRunning(
                "CodeBee 已在使用此数据目录，请先退出已有服务：%s" % self.directory
            ) from exc
        self._file = handle

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is not None:
            # Closing the handle releases both flock and Windows byte locks.
            handle.close()
        key = str(self.directory.resolve())
        if _LEASES.get(key) is self:
            _LEASES.pop(key, None)

    def __enter__(self) -> "ServiceLease":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


_LEASES = {}


def acquire_service(directory: Path) -> ServiceLease:
    """Keep a service lease until process exit; repeated startup calls reuse it."""
    key = str(Path(directory).resolve())
    lease = _LEASES.get(key)
    if lease is None or lease._file is None:
        lease = ServiceLease(Path(key))
        lease.acquire()
        _LEASES[key] = lease
        atexit.register(lease.release)
    return lease
