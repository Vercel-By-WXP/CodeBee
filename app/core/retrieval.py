"""Dependency-free retrieval index with traceable keyword scoring.

This is intentionally a RAG-lite layer: SQLite/vector backends can be added
later without changing the API contract. Every hit carries its source and the
terms that caused the match.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "retrieval_index.json"
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_MAX_FILES = 500
_MAX_BYTES = 20 * 1024 * 1024
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}


def _terms(text):
    return [x.lower() for x in _WORD_RE.findall(str(text or ""))]


def _load():
    try:
        value = json.loads(_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"documents": {}}
    except Exception:
        return {"version": 1, "documents": {}}


def _save(value):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_FILE)


def upsert(source, text, metadata=None, doc_id=""):
    source = str(source or "").strip()[:500]
    text = str(text or "").strip()
    if not source or not text:
        raise ValueError("source 和 text 不能为空")
    root_hint = ""
    if isinstance(metadata, dict):
        root_hint = str(metadata.get("root") or "")[:500]
    did = str(doc_id or hashlib.sha256((root_hint + "\0" + source).encode("utf-8")).hexdigest()[:20])[:100]
    meta = metadata if isinstance(metadata, dict) else {}
    meta = {str(k)[:80]: str(v)[:1000] for k, v in list(meta.items())[:30]}
    row = {"id": did, "source": source, "text": text[:200000],
           "terms": sorted(set(_terms(text))),
           "metadata": meta}
    with _LOCK:
        data = _load()
        data.setdefault("version", 1)
        documents = data.setdefault("documents", {})
        # Explicit IDs are client supplied; avoid silently replacing an
        # unrelated document when two sources reuse the same short ID.
        if did in documents and documents[did].get("source") != source:
            suffix = hashlib.sha256((source + "\0" + text).encode("utf-8")).hexdigest()[:12]
            did = (did[:87] + "-" + suffix)[:100]
            row["id"] = did
        documents[did] = row
        _save(data)
    return row


def index_directory(root, extensions=(".md", ".txt", ".json"), limit=500,
                    allowed_roots=None, max_bytes=_MAX_BYTES):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("目录不存在")
    if allowed_roots is not None:
        roots = [Path(p).expanduser().resolve() for p in allowed_roots if p]
        if not any(root == p or p in root.parents for p in roots):
            raise ValueError("只能索引任务工作目录范围内的文件")
    try:
        file_limit = max(1, min(_MAX_FILES, int(limit)))
    except (TypeError, ValueError):
        raise ValueError("limit 必须是整数")
    try:
        byte_limit = max(1, min(_MAX_BYTES, int(max_bytes)))
    except (TypeError, ValueError):
        raise ValueError("max_bytes 必须是整数")
    count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if count >= file_limit or total_bytes >= byte_limit:
            break
        # Never follow a symlink while indexing user-controlled trees.
        if path.is_symlink() or any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix.lower() in extensions:
            try:
                resolved = path.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                size = path.stat().st_size
                if size > byte_limit - total_bytes:
                    continue
                upsert(str(path.relative_to(root)), path.read_text(encoding="utf-8", errors="replace"),
                       {"root": str(root)})
                count += 1
                total_bytes += size
            except OSError:
                pass
    return {"indexed": count, "bytes": total_bytes,
            "total": len(_load().get("documents") or {})}


def search(query, limit=10):
    try:
        n = max(1, min(50, int(limit or 10)))
    except (TypeError, ValueError):
        raise ValueError("limit 必须是整数")
    terms = set(_terms(str(query or "")[:500]))
    if not terms:
        return []
    with _LOCK:
        docs = list((_load().get("documents") or {}).values())
    rows = []
    for doc in docs:
        matched = sorted(terms.intersection(doc.get("terms") or []))
        if not matched:
            continue
        score = len(matched) / max(1, len(terms))
        rows.append({"id": doc.get("id"), "source": doc.get("source"),
                     "score": round(score, 4), "matched_terms": matched,
                     "snippet": str(doc.get("text") or "")[:600],
                     "metadata": doc.get("metadata") or {}})
    rows.sort(key=lambda x: (-x["score"], x["source"] or ""))
    return rows[:n]
