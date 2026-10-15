# -*- coding: utf-8 -*-
"""Small, inspectable sharing packages for project collaboration.

Packages contain metadata and governed content only.  They never include
provider credentials, run logs, browser profiles or arbitrary workspace files.
The compact code format is transport friendly for the phone relay and has a
checksum so pasted/truncated packages fail closed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time

from . import experts, knowledge, project_space, project_memory

SCHEMA = 1
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _clean(value, limit=500):
    return str(value or "").strip()[:limit]


def _package(kind, payload):
    if kind not in ("project", "expert", "knowledge"):
        raise ValueError("不支持的分享包类型")
    body = {"app": "codebee", "schema": SCHEMA, "kind": kind,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "payload": payload}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body["sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return body


def encode(package):
    raw = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode(code):
    if not isinstance(code, str) or len(code) > 2_000_000:
        raise ValueError("分享码为空或过大")
    try:
        raw = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)).decode("utf-8")
        item = json.loads(raw)
    except Exception as exc:
        raise ValueError("分享码无法解析") from exc
    if not isinstance(item, dict) or item.get("app") != "codebee" or int(item.get("schema") or 0) != SCHEMA:
        raise ValueError("分享包版本或来源不匹配")
    supplied = item.get("sha256")
    unsigned = dict(item)
    unsigned.pop("sha256", None)
    raw_unsigned = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not isinstance(supplied, str) or not hashlib.sha256(raw_unsigned.encode("utf-8")).hexdigest() == supplied:
        raise ValueError("分享包完整性校验失败")
    return item


def export_expert(expert_id):
    expert = experts.get_expert(expert_id)
    if not expert:
        raise ValueError("专家模板不存在")
    package = _package("expert", {"expert": experts.snapshot(expert)})
    return {"kind": "expert", "name": expert.get("name"), "package": package,
            "code": encode(package)}


def export_knowledge(*, scope="", limit=200):
    try:
        limit = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        limit = 200
    entries = knowledge.list_entries(scope=scope or None)[:limit]
    package = _package("knowledge", {"scope": _clean(scope, 80), "entries": entries})
    return {"kind": "knowledge", "count": len(entries), "package": package,
            "code": encode(package)}


def export_project(project_id, *, include_archived=False):
    project = project_space.get(project_id, include_archived=include_archived)
    if not project:
        raise ValueError("项目不存在")
    # Runs are useful for context, but package size and privacy stay bounded.
    payload = {"project": {"id": project["id"], "name": project["name"],
                            "workdir": project["workdir"]},
               "tasks": project["tasks"][:100], "automations": project["automations"][:100],
               "memory": [e for e in project["memory"] if e.get("status") in ("approved", "pending")][:200],
               "experts": [t.get("expert_snapshot") for t in project["tasks"]
                           if isinstance(t.get("expert_snapshot"), dict)][:50]}
    package = _package("project", payload)
    return {"kind": "project", "name": project["name"], "package": package,
            "code": encode(package)}


def import_package(code, *, apply=False):
    package = decode(code)
    kind = package["kind"]
    payload = package.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("分享包内容无效")
    if not apply:
        return {"kind": kind, "preview": payload, "applied": False}
    if kind == "expert":
        expert = payload.get("expert")
        if not isinstance(expert, dict):
            raise ValueError("专家包缺少模板")
        saved = experts.import_expert(expert)
        return {"kind": kind, "expert": saved, "applied": True}
    if kind == "knowledge":
        rows = payload.get("entries") or []
        imported = 0
        for row in rows[:500]:
            if not isinstance(row, dict):
                continue
            if knowledge.upsert_entry(row.get("scope") or "*", row.get("title"), row.get("body"),
                                      tags=row.get("tags"), source="shared-package",
                                      as_of=row.get("as_of"), status="approved",
                                      confidence=row.get("confidence")):
                imported += 1
        return {"kind": kind, "imported": imported, "applied": True}
    if kind == "project":
        imported = 0
        for row in (payload.get("memory") or [])[:200]:
            if not isinstance(row, dict):
                continue
            # Project packages do not write arbitrary files.  Rehydrate facts
            # into the global governed knowledge store for explicit reuse.
            if knowledge.upsert_entry("*", row.get("title"), row.get("content"),
                                      tags=["shared-project"], source="shared-project",
                                      status="approved"):
                imported += 1
        return {"kind": kind, "imported_memory": imported, "applied": True}
    raise ValueError("不支持的分享包类型")
