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

from . import atomic, paths

MOCK_AGENTS = [
    {"id": "mock-a", "label": "演示智能体 A（mock）", "kind": "mock", "mode": "mock", "command": ""},
    {"id": "mock-b", "label": "演示智能体 B（mock）", "kind": "mock", "mode": "mock", "command": ""},
]

_LOCK = threading.RLock()


def capability_manifest(entry):
    """Return one explicit, UI-safe capability contract for a backend.

    The manifest is descriptive only: it never grants a backend extra access.
    Execution remains governed by the task contract, sandbox policy and command
    authorization gates.
    """
    entry = entry or {}
    orch = entry.get("orch") or {}
    kind = str(orch.get("kind") or "")
    transport = "acp" if kind == "acp" or str(entry.get("id") or "").startswith("acp-") else "cli"
    resume = bool(orch.get("resume_argv_template"))
    if kind == "mock":
        transport = "mock"
        resume = False
    platforms = entry.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        platforms = ["windows", "linux", "macos"]
    platforms = [str(x) for x in platforms if str(x) in ("windows", "linux", "macos")]
    if not platforms:
        platforms = ["windows", "linux", "macos"]
    declared = entry.get("capabilities") if isinstance(entry.get("capabilities"), dict) else {}
    return {
        "id": str(entry.get("id") or ""),
        "transport": transport,
        "resume": resume,
        "sandbox": str(declared.get("sandbox") or "task-policy"),
        "approval": str(declared.get("approval") or "task-contract"),
        "mcp": bool(declared.get("mcp", kind in ("acp", "mcp"))),
        "platforms": platforms,
        "model_tiers": [str(x) for x in (declared.get("model_tiers") or
                                           ["budget", "standard", "premium"])[:8]],
    }


def load_enabled():
    try:
        return json.loads(paths.ENABLED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_enabled(state):
    with _LOCK:
        atomic.write_json(paths.ENABLED_FILE, state, mode=0o600)


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
        # 整包透传 orch：per-agent 运行时开关（timeout_ms 5E、stall_timeout_s
        # 看门狗等）都在 runner 侧读取——此前只挑字段，catalog 上配的
        # timeout_ms/stall 根本流不到 runner（2026-09-17 连载 c35 实测）
        "orch": orch,
    }


def resolve_orch_enabled(entry, det, pref, *, seed_detected=True):
    """统一的「该 CLI 是否进自动路由」判定，effective_agents 与 catalog_view 共用。

    优先级：
      1. 显式编排偏好（pref 含 ``enabled``）——用户关掉的绝不偷偷启用；
      2. 否则「探测到即启用」种子（无显式偏好且已被探测到安装 → 默认开）；
      3. 否则回退 ``default_enabled``（catalog 里的硬开，如 codex/claude）。

    第 2 条是精简化要的核心：装了但没手动开 ≠ 不该用。旧逻辑只认
    ``default_enabled``，导致用户机器上装了 Copilot / opencode / qwen 却默认不进
    自动路由，得去「智能体目录」手动开。种子让「探测到就可用」成为默认，
    同时显式关闭始终优先（决策记录 #13：用户自己的选择不可逆）。
    """
    if not (entry.get("orch") or {}).get("kind"):
        return False
    if not (det or {}).get("installed"):
        return False
    pref = pref or {}
    if "enabled" in pref:
        return bool(pref["enabled"])
    if seed_detected:
        return True
    return bool(entry.get("default_enabled", False))


def effective_agents(catalog_entries, detected, *, seed_detected=True):
    """生成当前可参与编排的智能体列表（真实已装+启用，外加内置 mock）。

    seed_detected=True（默认）：没有显式编排偏好的已探测 CLI 默认启用
    （「探测到即启用」种子）。显式关闭仍尊重；default_enabled 作兜底硬开。
    """
    enabled = load_enabled()
    out = []
    for entry in catalog_entries:
        det = (detected or {}).get(entry.get("id")) or {}
        pref = enabled.get(entry.get("id")) or {}
        if not resolve_orch_enabled(entry, det, pref, seed_detected=seed_detected):
            continue
        out.append(_build_agent(entry))
    try:
        from . import backend_profiles
        out.extend(backend_profiles.runtime_agents())
    except Exception:
        # Broken local profile storage must not disable unrelated installed agents.
        pass
    out.extend([dict(m) for m in MOCK_AGENTS])
    return out


def installed_agent(entry_id, catalog_entries, detected):
    """按 id 构建「已安装」的智能体，无视编排启用开关。

    续会话是用户对该 CLI 的显式指定，不依赖其是否参与自动路由；
    未安装 / 无编排配置 / id 不存在时返回 None。
    """
    if str(entry_id or "").startswith("acp-"):
        try:
            from . import backend_profiles
            return next((agent for agent in backend_profiles.runtime_agents()
                         if agent.get("id") == entry_id), None)
        except Exception:
            return None
    for entry in catalog_entries:
        if entry.get("id") != entry_id:
            continue
        if not (entry.get("orch") or {}).get("kind"):
            return None
        if not ((detected or {}).get(entry_id) or {}).get("installed"):
            return None
        return _build_agent(entry)
    return None
