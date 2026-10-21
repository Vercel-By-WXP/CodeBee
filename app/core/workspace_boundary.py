# -*- coding: utf-8 -*-
"""Small, deterministic filesystem boundary for model-facing file actions."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List


class WorkspaceBoundaryError(ValueError):
    pass


class WorkspaceBoundary:
    def __init__(self, root, max_write_bytes=16 * 1024 * 1024,
                 max_read_bytes=16 * 1024 * 1024, format_locks=None,
                 internal_dir=".codebee"):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceBoundaryError("工作目录不可用")
        self.max_write_bytes = max(1, int(max_write_bytes))
        self.max_read_bytes = max(1, int(max_read_bytes))
        self.internal_dir = str(internal_dir or ".codebee").strip("/\\")
        self.format_locks: List[Dict[str, Any]] = [
            item for item in (format_locks or ()) if isinstance(item, dict)
        ]

    def resolve(self, relative):
        raw = str(relative or "").strip().replace("\\", "/")
        if not raw or "\x00" in raw or raw.startswith("/"):
            raise WorkspaceBoundaryError("路径必须是非空相对路径")
        if len(raw) >= 2 and raw[1] == ":":
            raise WorkspaceBoundaryError("路径必须是相对路径")
        parts = raw.split("/")
        if any(part in ("", ".", "..") and part == ".." for part in parts):
            raise WorkspaceBoundaryError("路径不得包含 ..")
        target = (self.root / Path(*parts)).resolve()
        if target != self.root and self.root not in target.parents:
            raise WorkspaceBoundaryError("路径越出工作目录")
        return target

    def _check_lock(self, target, channel):
        rel = target.relative_to(self.root).as_posix()
        if rel == self.internal_dir or rel.startswith(self.internal_dir + "/"):
            raise WorkspaceBoundaryError("内部目录为只读")
        ext = target.suffix.lower()
        for lock in self.format_locks:
            extensions = {str(x).lower() for x in (lock.get("extensions") or [])}
            if ext not in extensions:
                continue
            if str(channel or "") != str(lock.get("channel") or ""):
                raise WorkspaceBoundaryError(
                    "%s 由 %s 通道管理：%s" % (
                        ext, lock.get("channel") or "指定通道", lock.get("reason") or ""))

    def read(self, relative, max_bytes=None):
        target = self.resolve(relative)
        limit = self.max_read_bytes if max_bytes is None else max(1, int(max_bytes))
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise WorkspaceBoundaryError("读取失败：%s" % exc)
        if size > limit:
            raise WorkspaceBoundaryError("读取超过 %d 字节上限" % limit)
        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkspaceBoundaryError("读取失败：%s" % exc)

    def write(self, relative, content, channel=None, max_bytes=None):
        target = self.resolve(relative)
        self._check_lock(target, channel)
        text = str(content if content is not None else "")
        limit = self.max_write_bytes if max_bytes is None else max(1, int(max_bytes))
        if len(text.encode("utf-8")) > limit:
            raise WorkspaceBoundaryError("写入超过 %d 字节上限" % limit)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".%s-" % target.name, suffix=".tmp",
                                          dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, str(target))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def exists(self, relative):
        try:
            return self.resolve(relative).exists()
        except WorkspaceBoundaryError:
            return False
