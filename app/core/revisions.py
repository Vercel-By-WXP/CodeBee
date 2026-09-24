# -*- coding: utf-8 -*-
"""Small, content-addressed revision helpers.

The execution layer stores revision identifiers and digests instead of copying
skill/knowledge bodies into every run.  A digest is deliberately based on the
semantic payload supplied by the caller, so formatting-only metadata changes do
not silently change the meaning of an existing run.
"""
from __future__ import annotations

import hashlib
import json
import time


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def content_sha256(value) -> str:
    if not isinstance(value, str):
        value = canonical_json(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def revision_id(prefix: str, digest: str) -> str:
    return "%s-%s" % (str(prefix or "rev"), str(digest or "")[:16])


def make_revision(prefix: str, content, *, source="", created_at=None) -> dict:
    digest = content_sha256(content)
    return {
        "revision_id": revision_id(prefix, digest),
        "content_sha256": digest,
        "source": str(source or "")[:160],
        "created_at": created_at or time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def revision_record(prefix: str, content, *, source="", created_at=None,
                    extra=None) -> dict:
    out = make_revision(prefix, content, source=source, created_at=created_at)
    if isinstance(extra, dict):
        out.update(extra)
    return out


def stable_digest(items) -> str:
    """Digest a list/dict of revision metadata without embedding content."""
    return content_sha256(items)
