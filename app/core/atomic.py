"""Crash-safe, dependency-free file persistence helpers.

All durable JSON/markdown state should go through this module.  A unique
temporary file in the destination directory prevents concurrent writers from
sharing ``*.tmp`` and ``os.replace`` makes readers observe either the old or
the complete new document.  ``fsync`` is best effort on filesystems where the
directory handle is not syncable (notably Windows).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def _sync_directory(directory: Path) -> None:
    """Durably publish a rename where the platform exposes directory fsync."""
    try:
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        fd = os.open(str(directory), flags)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def write_bytes(path, data, *, mode=None, durable=True) -> Path:
    """Atomically replace *path* with bytes and return the destination path.

    ``mode`` is applied before publication, which avoids a short window where
    a newly created credentials file has the process umask's default mode.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(data)
    fd = -1
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".%s." % target.name,
                                         suffix=".tmp", dir=str(target.parent))
        if mode is not None:
            try:
                os.chmod(tmp_name, mode)
            except OSError:
                pass
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(payload)
            fh.flush()
            if durable:
                os.fsync(fh.fileno())
        # Windows refuses to replace a file while a reader still has it open.
        # Readers are deliberately lock-free, so retry the short sharing
        # violation window instead of exposing a partial in-place write or
        # failing a perfectly valid concurrent read.
        delay = 0.002
        for attempt in range(100):
            try:
                os.replace(tmp_name, target)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 99:
                    raise
                time.sleep(delay)
                delay = min(delay * 1.25, 0.020)
        tmp_name = ""
        if durable:
            _sync_directory(target.parent)
        return target
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def write_text(path, text, *, mode=None, durable=True, encoding="utf-8") -> Path:
    return write_bytes(Path(path), str(text).encode(encoding), mode=mode,
                       durable=durable)


def create_bytes(path, data, *, mode=None, durable=True) -> Path:
    """Atomically create *path* without replacing an existing file.

    A fully written temporary inode is published with a hard-link operation,
    which is atomic and preserves the ``O_EXCL`` first-writer-wins contract.
    ``FileExistsError`` is intentionally propagated to the caller.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(data)
    fd = -1
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".%s." % target.name,
                                         suffix=".tmp", dir=str(target.parent))
        if mode is not None:
            try:
                os.chmod(tmp_name, mode)
            except OSError:
                pass
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(payload)
            fh.flush()
            if durable:
                os.fsync(fh.fileno())
        # link() fails with FileExistsError rather than replacing a rival.
        # Drop our temporary directory entry after publication.  The target
        # keeps its own hard link to the fully written inode; retaining the
        # source would leak one hidden ``.*.tmp`` file for every first-write.
        os.link(tmp_name, str(target))
        os.unlink(tmp_name)
        tmp_name = ""
        if durable:
            _sync_directory(target.parent)
        return target
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def create_text(path, text, *, mode=None, durable=True, encoding="utf-8") -> Path:
    return create_bytes(Path(path), str(text).encode(encoding), mode=mode,
                        durable=durable)


def write_json(path, value, *, mode=None, durable=True, indent=2) -> Path:
    return write_text(path, json.dumps(value, ensure_ascii=False, indent=indent),
                      mode=mode, durable=durable)
