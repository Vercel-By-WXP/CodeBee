# -*- coding: utf-8 -*-
"""Project-scoped collaboration roles for local/relay clients."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "project-permissions.json"
ROLES = ("owner", "editor", "viewer")


def _load():
    try:
        value = json.loads(_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(value):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = _FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(_FILE)


def list_for(project_id):
    with _LOCK:
        return dict((_load().get(str(project_id)) or {}))


def role(project_id, actor):
    actor = str(actor or "local").strip()[:120] or "local"
    current = list_for(project_id)
    return current.get(actor) or ("owner" if not current else "viewer")


def can(project_id, actor, required="viewer"):
    order = {"viewer": 0, "editor": 1, "owner": 2}
    return order.get(role(project_id, actor), -1) >= order.get(required, 0)


def set_role(project_id, actor, value):
    project_id = str(project_id or "").strip()
    actor = str(actor or "").strip()[:120]
    value = str(value or "").strip().lower()
    if not project_id or not actor or value not in ROLES:
        raise ValueError("项目、成员和角色必须有效")
    with _LOCK:
        data = _load()
        members = data.setdefault(project_id, {})
        if value == "owner" and any(v == "owner" for k, v in members.items() if k != actor):
            raise ValueError("一个项目只能保留一个显式 owner")
        members[actor] = value
        _save(data)
        return dict(members)
