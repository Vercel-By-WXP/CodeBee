# -*- coding: utf-8 -*-
"""Source hashing, chunking and quality reporting for the local knowledge index."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from . import paths, retrieval

_REPORT_FILE = paths.DATA_DIR / "knowledge_pipeline.json"
_LOCK = threading.RLock()


def chunk_text(text, chunk_size=1200, overlap=120):
    text = str(text or "")
    try:
        size = max(100, min(10000, int(chunk_size)))
        overlap = max(0, min(size // 2, int(overlap)))
    except (TypeError, ValueError):
        size, overlap = 1200, 120
    if not text:
        return []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        part = text[start:end].strip()
        if part:
            out.append(part)
        if end >= len(text):
            break
        start = end - overlap
    return out


def _load():
    try:
        data = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def ingest(source, text, metadata=None, *, chunk_size=1200, overlap=120):
    source = str(source or "").strip()[:500]
    text = str(text or "")
    if not source or not text.strip():
        raise ValueError("source 和 text 不能为空")
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    ingest_id = "ingest-" + hashlib.sha256((source + "\0" + source_hash).encode("utf-8")).hexdigest()[:20]
    chunks = chunk_text(text, chunk_size, overlap)
    meta = {str(k)[:60]: str(v)[:500] for k, v in (metadata or {}).items()
            if isinstance(k, (str, int)) and isinstance(v, (str, int, float, bool))}
    indexed = []
    for index, part in enumerate(chunks):
        indexed.append(retrieval.upsert(
            "%s#%d" % (source, index + 1), part,
            dict(meta, source=source, source_sha256=source_hash,
                 ingest_id=ingest_id, chunk_index=index, chunk_count=len(chunks)),
            doc_id="%s-%04d" % (ingest_id, index)))
    report = {"ingest_id": ingest_id, "source": source, "source_sha256": source_hash,
              "chunks": len(chunks), "indexed_chunks": len(indexed),
              "chars": len(text), "created_at": time.time(),
              "quality": {"non_empty_ratio": round(sum(bool(x.strip()) for x in chunks) /
                                                        float(max(1, len(chunks))), 4),
                          "unique_ratio": round(len(set(chunks)) / float(max(1, len(chunks))), 4),
                          "avg_chunk_chars": round(sum(map(len, chunks)) / float(max(1, len(chunks))), 2)}}
    with _LOCK:
        data = _load()
        data[ingest_id] = report
        _REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _REPORT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_REPORT_FILE)
    return report


def quality_report(ingest_id):
    with _LOCK:
        return _load().get(str(ingest_id or ""), {"ingest_id": str(ingest_id or ""),
                                                    "indexed_chunks": 0, "quality": {}})
