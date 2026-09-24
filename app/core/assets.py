# -*- coding: utf-8 -*-
"""Content-addressed asset index.

The index owns references and revisions, not file contents.  Files stay in the
task workspace; projects/runs can safely point to ``asset_id + revision_id``
without making a second copy of a document.
"""
from __future__ import annotations

import json
import hashlib
import mimetypes
import threading
import time
from pathlib import Path

from . import paths

LOCK = threading.RLock()


def _path():
    return paths.DATA_DIR / "assets.json"


def _load():
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"assets": {}}
    except (OSError, ValueError):
        return {"assets": {}}


def _save(data):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def register(workdir, relative_path, *, owner_type="", owner_id="", name="", mime=""):
    """Index a workspace file and return its reference, or ``None`` if absent."""
    root = Path(str(workdir or "")).resolve()
    rel = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("../") or "/../" in rel:
        return None
    fp = (root / rel).resolve()
    try:
        if root not in fp.parents or not fp.is_file():
            return None
        digest_builder = hashlib.sha256()
        size = 0
        with fp.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest_builder.update(chunk)
                size += len(chunk)
        digest = digest_builder.hexdigest()
        asset_id = "asset-" + digest[:16]
        revision_id = "assetrev-" + digest[:16]
        ref = {"asset_id": asset_id, "revision_id": revision_id,
               "content_sha256": digest, "path": rel,
               "name": str(name or fp.name)[:120],
               "mime": str(mime or mimetypes.guess_type(fp.name)[0]
                           or "application/octet-stream")[:120],
               "size": size, "updated_at": time.time()}
        with LOCK:
            data = _load()
            assets = data.setdefault("assets", {})
            row = assets.setdefault(asset_id, ref)
            # The content hash identifies one immutable revision.  Keep the
            # first source path/name as canonical metadata so registering the
            # same bytes from another workspace cannot rewrite an existing
            # reference's path semantics.
            row.update({k: ref[k] for k in
                        ("revision_id", "content_sha256", "size", "updated_at")})
            for key in ("path", "name", "mime"):
                if not row.get(key):
                    row[key] = ref[key]
            owners = row.setdefault("owners", [])
            owner = {"type": str(owner_type or "")[:40], "id": str(owner_id or "")[:100]}
            if owner["id"] and owner not in owners:
                owners.append(owner)
                row["owners"] = owners[-50:]
            _save(data)
            return dict(row)
    except (OSError, ValueError):
        return None


def get(asset_id, revision_id=""):
    with LOCK:
        row = (_load().get("assets") or {}).get(str(asset_id or ""))
    if not isinstance(row, dict):
        return None
    if revision_id and str(row.get("revision_id") or "") != str(revision_id):
        return None
    return dict(row)


def list_assets(*, owner_type="", owner_id="", limit=200):
    with LOCK:
        rows = list((_load().get("assets") or {}).values())
    if owner_type or owner_id:
        rows = [row for row in rows if any(
            (not owner_type or owner_type == item.get("type")) and
            (not owner_id or owner_id == item.get("id"))
            for item in (row.get("owners") or []))]
    rows.sort(key=lambda row: float(row.get("updated_at") or 0), reverse=True)
    return [dict(row) for row in rows[:max(1, min(1000, int(limit or 200)))]]
