# -*- coding: utf-8 -*-
"""编排注册表：catalog（装了什么）× 用户偏好（启用谁、用什么模型）→ 可编排智能体。

data/orchestration.json 结构：
  {"codex-cli": {"enabled": true, "model": "gpt-5.5"}, ...}
"""
from __future__ import annotations

import json
import threading

from . import paths

MOCK_AGENTS = [
    {"id": "mock-a", "label": "演示智能体 A（mock）", "kind": "mock", "mode": "mock", "command": ""},
    {"id": "mock-b", "label": "演示智能体 B（mock）", "kind": "mock", "mode": "mock", "command": ""},
]

_LOCK = threading.RLock()


def load_enabled():
    try:
        return json.loads(paths.ENABLED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_enabled(state):
    with _LOCK:
        paths.ensure_dirs()
        paths.ENABLED_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def set_preference(agent_id, enabled=None, model=None):
    with _LOCK:
        state = load_enabled()
        pref = state.get(agent_id) or {}
        if enabled is not None:
            pref["enabled"] = bool(enabled)
        if model is not None:
            pref["model"] = (model or "").strip() or None
        state[agent_id] = pref
        save_enabled(state)
        return pref


def effective_agents(catalog_entries, detected):
    """生成当前可参与编排的智能体列表（真实已装+启用，外加内置 mock）。"""
    enabled = load_enabled()
    out = []
    for entry in catalog_entries:
        orch = entry.get("orch")
        if not orch or not orch.get("kind"):
            continue
        det = (detected or {}).get(entry.get("id")) or {}
        if not det.get("installed"):
            continue
        pref = enabled.get(entry.get("id")) or {}
        if not pref.get("enabled", entry.get("default_enabled", False)):
            continue
        out.append({
            "id": entry["id"],
            "label": entry.get("name", entry["id"]),
            "kind": orch.get("kind", "generic"),
            "command": orch.get("command") or (entry.get("detect") or {}).get("cli") or entry["id"],
            "mode": "real",
            "model": pref.get("model") or None,
            "env": orch.get("env") or {},
            "argv_template": orch.get("argv_template"),
        })
    out.extend([dict(m) for m in MOCK_AGENTS])
    return out
