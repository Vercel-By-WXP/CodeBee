# -*- coding: utf-8 -*-
"""Bounded, run-scoped storage for large tool results omitted from model context."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from . import paths

_DIR = paths.DATA_DIR / "tool_outputs"
_REF_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_RUN_BYTES = 32 * 1024 * 1024
MAX_READ_CHARS = 20000


def _run_dir(run_id):
    return _DIR / hashlib.sha256(str(run_id or "").encode("utf-8")).hexdigest()


def save(run_id, call_id, content):
    """Store one output and return an opaque reference, or empty string on limits/errors."""
    if not str(run_id or "").strip():
        return ""
    raw = str(content or "").encode("utf-8", errors="replace")
    if len(raw) > MAX_RESULT_BYTES:
        return ""
    run_dir = _run_dir(run_id)
    ref = hashlib.sha256((str(call_id or "") + "\0").encode("utf-8") + raw).hexdigest()[:32]
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(mode=0o700, exist_ok=True)
        if run_dir.is_symlink() or (hasattr(run_dir, "is_junction") and run_dir.is_junction()):
            return ""
        target = run_dir / (ref + ".txt")
        if target.exists():
            return ref if target.is_file() and not target.is_symlink() else ""
        total = sum(p.stat().st_size for p in run_dir.glob("*.txt") if p.is_file())
        if total + len(raw) > MAX_RUN_BYTES:
            return ""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(target), flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return ""
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(raw)
                handle.flush()
        finally:
            os.close(fd)
        return ref
    except (OSError, ValueError):
        return ""


def read(run_id, ref, *, offset=0, limit=MAX_READ_CHARS):
    """Read a bounded slice, scoped to a single run and an opaque content reference."""
    if not str(run_id or "").strip() or not _REF_RE.fullmatch(str(ref or "")):
        return {"ok": False, "error": "工具结果引用无效"}
    run_dir = _run_dir(run_id)
    if run_dir.is_symlink() or (hasattr(run_dir, "is_junction") and run_dir.is_junction()):
        return {"ok": False, "error": "工具结果存储路径不安全"}
    target = run_dir / (str(ref) + ".txt")
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        return {"ok": False, "error": "工具结果存储路径不安全"}
    try:
        offset = max(0, int(offset or 0))
        limit = max(1, min(MAX_READ_CHARS, int(limit or MAX_READ_CHARS)))
        with target.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(limit * 4 + 4)
            text = raw.decode("utf-8", errors="replace")[:limit]
            end = handle.tell()
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
        return {"ok": True, "text": text, "offset": offset,
                "next_offset": end if end < size else None, "size_bytes": size}
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": "找不到此运行中的工具结果引用"}
