# -*- coding: utf-8 -*-
"""Discover repository-local AGENTS.md guidance for an execution context.

The format is intentionally compatible with the common AGENTS.md convention,
but the loader stays small and dependency-free.  Guidance is treated as input
data: it is bounded, scrubbed before prompt injection, and content-addressed so
the run can explain exactly which context was used.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .redact import scrub_text

MAX_FILES = 8
MAX_FILE_CHARS = 8000
MAX_PROMPT_CHARS = 24000


def _roots(workdir, boundary=None):
    try:
        current = Path(workdir).expanduser().resolve()
    except (TypeError, OSError, ValueError):
        return []
    if not current.is_dir():
        return []
    # Trust instructions only through the nearest repository root. Outside a
    # repository, the selected workdir itself is the boundary; never walk into
    # an unrelated parent directory (or drive root).
    try:
        if boundary:
            stop = Path(boundary).expanduser().resolve()
        else:
            stop = current
            for candidate in (current, *current.parents):
                if (candidate / ".git").exists():
                    stop = candidate
                    break
    except (TypeError, OSError, ValueError):
        stop = current
    if current != stop and stop not in current.parents:
        stop = current
    roots = []
    while True:
        roots.append(current)
        if current == stop:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return roots


def _read(path):
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        raw = path.read_bytes()[:MAX_FILE_CHARS * 4]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", "replace")
        return scrub_text(text, limit=MAX_FILE_CHARS)
    except OSError:
        return ""


def discover(workdir, *, boundary=None, max_files=MAX_FILES, max_chars=MAX_PROMPT_CHARS):
    """Return nearest-first AGENTS.md guidance and stable metadata."""
    try:
        file_limit = max(1, min(MAX_FILES, int(max_files)))
        char_limit = max(1000, min(MAX_PROMPT_CHARS, int(max_chars)))
    except (TypeError, ValueError):
        file_limit, char_limit = MAX_FILES, MAX_PROMPT_CHARS
    records = []
    chunks = []
    used = 0
    for root in _roots(workdir, boundary=boundary):
        if len(records) >= file_limit:
            break
        path = root / "AGENTS.md"
        text = _read(path)
        if not text:
            continue
        remaining = char_limit - used
        if remaining <= 0:
            break
        text = text[:remaining]
        rel = str(path)
        records.append({
            "path": rel,
            "scope": str(root),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chars": len(text),
        })
        chunks.append("## AGENTS.md（作用域：%s）\n%s" % (root, text))
        used += len(text)
    prompt = "\n\n".join(chunks)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return {"prompt": prompt, "files": records, "content_sha256": digest,
            "file_count": len(records), "chars": len(prompt)}


def inject(task, workdir=None):
    """Append discovered guidance to a task copy and return metadata."""
    value = dict(task or {})
    result = discover(workdir or value.get("workdir"))
    if result["prompt"]:
        existing = str(value.get("context") or "").strip()
        value["context"] = (existing + "\n\n" if existing else "") + result["prompt"]
    value["agent_context"] = {"content_sha256": result["content_sha256"],
                               "files": result["files"],
                               "file_count": result["file_count"],
                               "chars": result["chars"]}
    return value, result
