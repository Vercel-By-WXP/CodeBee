# -*- coding: utf-8 -*-
"""编排注册表：catalog（装了什么）× 用户偏好（启用谁）→ 可编排智能体。

data/orchestration.json 结构：
  {"codex-cli": {"enabled": true}, ...}

运行时用哪个模型（含主模型/降级备选链、供应商注入、难度路由）统一在
「CLI 绑定」页配置，存 modelhub 的 data/models.json bindings。
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


def set_preference(agent_id, enabled=None, model=None, models=None):
    """目前只管「参与编排」开关；model/models 是旧参数，静默忽略
    （模型链已并入 modelhub bindings，保留签名兼容旧调用方）。"""
    with _LOCK:
        state = load_enabled()
        pref = state.get(agent_id) or {}
        pref.pop("model", None)
        pref.pop("models", None)   # 顺手清掉历史遗留字段
        if enabled is not None:
            pref["enabled"] = bool(enabled)
        state[agent_id] = pref
        save_enabled(state)
        return pref


def _build_agent(entry):
    """catalog 条目 → 运行时智能体字典（resume_argv_template 透传给 runner）。"""
    orch = entry.get("orch") or {}
    return {
        "id": entry["id"],
        "label": entry.get("name", entry["id"]),
        "kind": orch.get("kind", "generic"),
        "command": orch.get("command") or (entry.get("detect") or {}).get("cli") or entry["id"],
        "mode": "real",
        "env": orch.get("env") or {},
        "argv_template": orch.get("argv_template"),
        "resume_argv_template": orch.get("resume_argv_template"),
        # 小时级 token 配额（可选，0/缺省=不限）：路由时对本小时用量超标的
        # 智能体降权（munder-difflin 式配额感知），订阅型 CLI 不至于被单任务打爆
        "quota_tokens_per_hour": int(orch.get("quota_tokens_per_hour") or 0),
    }


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
        out.append(_build_agent(entry))
    out.extend([dict(m) for m in MOCK_AGENTS])
    return out


def installed_agent(entry_id, catalog_entries, detected):
    """按 id 构建「已安装」的智能体，无视编排启用开关。

    续会话是用户对该 CLI 的显式指定，不依赖其是否参与自动路由；
    未安装 / 无编排配置 / id 不存在时返回 None。
    """
    for entry in catalog_entries:
        if entry.get("id") != entry_id:
            continue
        if not (entry.get("orch") or {}).get("kind"):
            return None
        if not ((detected or {}).get(entry_id) or {}).get("installed"):
            return None
        return _build_agent(entry)
    return None
