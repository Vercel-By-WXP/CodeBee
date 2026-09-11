# -*- coding: utf-8 -*-
"""模型接入层：供应商注册表 + CCSwitch 导入 + 按 CLI 绑定 + 难度路由。

data/models.json 结构：
{
  "providers": [{"id","name","protocol":"anthropic|openai","base_url","api_key",
                 "model","model_easy","model_hard","source"}],
  "bindings": {"claude-code": {"provider_id","model","difficulty_routing"},
               "codex-cli": {...}}
}

密钥只落本地盘（与 CCSwitch 同等信任域）；API 一律脱敏返回。
按运行注入，不改写任何 CLI 的全局配置文件：
  claude : env ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL
  codex  : env ORCH_API_KEY + -c model_provider/model_providers.orch.* 覆盖
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "models.json"


def _load():
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": [], "bindings": {}}


def _save(data):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def providers():
    with _LOCK:
        return _load().get("providers", [])


def bindings():
    with _LOCK:
        return _load().get("bindings", {})


def _mask(key):
    if not key:
        return ""
    return key[:8] + "..." + key[-4:] if len(key) > 16 else key[:4] + "..."


def provider_view():
    """脱敏后的供应商列表（给 UI/API）。"""
    return [{k: (_mask(v) if k == "api_key" else v) for k, v in p.items()}
            for p in providers()]


def upsert_provider(entry):
    """新增/更新供应商。api_key 为空串表示沿用旧值。返回错误或 None。"""
    with _LOCK:
        data = _load()
        plist = data.setdefault("providers", [])
        pid = (entry.get("id") or "").strip()
        name = (entry.get("name") or "").strip()
        base_url = (entry.get("base_url") or "").strip().rstrip("/")
        if not name:
            return "名称不能为空"
        if not base_url.startswith(("http://", "https://")):
            return "base_url 必须是 http/https"
        proto = entry.get("protocol") or "anthropic"
        if proto not in ("anthropic", "openai"):
            return "protocol 只能是 anthropic 或 openai"
        target = None
        if pid:
            target = next((p for p in plist if p["id"] == pid), None)
        if target is None:
            target = {"id": "prov-%d" % (len(plist) + 1)}
            plist.append(target)
        target.update({
            "name": name, "protocol": proto, "base_url": base_url,
            "model": (entry.get("model") or "").strip(),
            "model_easy": (entry.get("model_easy") or "").strip(),
            "model_hard": (entry.get("model_hard") or "").strip(),
            "source": entry.get("source") or target.get("source") or "manual",
        })
        key = (entry.get("api_key") or "").strip()
        if key or "api_key" not in target:
            target["api_key"] = key
        _save(data)
        return None


def delete_provider(pid):
    with _LOCK:
        data = _load()
        data["providers"] = [p for p in data.get("providers", []) if p["id"] != pid]
        for k, b in (data.get("bindings") or {}).items():
            if b.get("provider_id") == pid:
                b["provider_id"] = ""
        _save(data)


def set_binding(agent_id, provider_id=None, model=None, difficulty_routing=None):
    with _LOCK:
        data = _load()
        b = data.setdefault("bindings", {}).setdefault(agent_id, {})
        if provider_id is not None:
            b["provider_id"] = provider_id if provider_id in [p["id"] for p in data.get("providers", [])] else ""
        if model is not None:
            b["model"] = (model or "").strip()
        if difficulty_routing is not None:
            b["difficulty_routing"] = bool(difficulty_routing)
        _save(data)
        return b


# ---------------------------------------------------------------- CCSwitch 导入

CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")


def _parse_codex_config(text):
    """从 codex 的 config.toml 文本中提取 model / base_url / wire_api。"""
    model = base_url = wire = None
    m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text or "")
    if m:
        model = m.group(1)
    m = re.search(r'(?m)^\s*base_url\s*=\s*"([^"]+)"', text or "")
    if m:
        base_url = m.group(1)
    m = re.search(r'(?m)^\s*wire_api\s*=\s*"([^"]+)"', text or "")
    if m:
        wire = m.group(1)
    return model, base_url, wire


def import_ccswitch():
    """从 CCSwitch 的 SQLite 导入 claude/codex 供应商。返回 (导入数, 说明)。"""
    if not os.path.isfile(CCSWITCH_DB):
        return 0, "未找到 CCSwitch 数据库 %s" % CCSWITCH_DB
    with _LOCK:
        data = _load()
        plist = data.setdefault("providers", [])
        known = {p.get("source_id"): p for p in plist if p.get("source") == "ccswitch"}
        imported, skipped = 0, 0
        con = sqlite3.connect("file:%s?mode=ro" % CCSWITCH_DB.replace("\\", "/"), uri=True)
        try:
            cur = con.cursor()
            cur.execute("SELECT id, app_type, name, settings_config FROM providers "
                        "WHERE app_type IN ('claude','codex')")
            rows = cur.fetchall()
        finally:
            con.close()
        for pid, app, name, cfg in rows:
            try:
                sc = json.loads(cfg)
            except Exception:
                skipped += 1
                continue
            prov = None
            if app == "claude":
                env = sc.get("env") or {}
                prov = {
                    "protocol": "anthropic",
                    "base_url": (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/"),
                    "api_key": env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or "",
                    "model": (env.get("ANTHROPIC_MODEL")
                              or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or ""),
                }
            elif app == "codex":
                key = ((sc.get("auth") or {}).get("OPENAI_API_KEY")) or ""
                model, base_url, wire = _parse_codex_config(sc.get("config") or "")
                if not base_url:
                    skipped += 1
                    continue
                prov = {"protocol": "openai", "base_url": base_url.rstrip("/"),
                        "api_key": key, "model": model,
                        "wire_api": wire or "responses"}
            if not prov or not prov.get("base_url"):
                skipped += 1
                continue
            prov.update({"name": ("[CC] " if app == "codex" else "") + (name or pid),
                         "source": "ccswitch", "source_id": "%s:%s" % (app, pid)})
            old = known.get(prov["source_id"])
            if old:
                # 重导入：继承 id（绑定引用不断链），保留用户设置的难度映射
                prov["id"] = old.get("id") or ("prov-%d" % (len(plist) + 1))
                prov["model_easy"] = old.get("model_easy", "")
                prov["model_hard"] = old.get("model_hard", "")
                plist[plist.index(old)] = prov
            else:
                prov["id"] = "prov-%d" % (len(plist) + 1)
                plist.append(prov)
            imported += 1
        _save(data)
        return imported, "导入 %d 个，跳过 %d 个（缺地址或格式不识别）" % (imported, skipped)


# ---------------------------------------------------------------- 运行时解析

def bind_agent(agent, difficulty="default"):
    """按绑定生成应用了供应商/模型覆盖的 agent 副本；无绑定时原样返回。"""
    r = resolve_binding(agent.get("id"), difficulty) or resolve_binding(agent.get("kind"), difficulty)
    if not r:
        return agent
    a = dict(agent)
    merged = dict(agent.get("env") or {})
    merged.update(r.get("env") or {})
    a["env"] = merged
    if r.get("model"):
        a["model"] = r["model"]
    if r.get("codex_provider"):
        a["codex_provider"] = r["codex_provider"]
    return a


def resolve_binding(agent_kind_or_id, difficulty="default"):
    """返回 {env:{}, model:..., codex_provider:...} 或 None。

    difficulty: easy | hard | default
    """
    b = bindings().get(agent_kind_or_id) or bindings().get(
        "codex-cli" if agent_kind_or_id == "codex" else "claude-code") or {}
    pid = b.get("provider_id")
    if not pid:
        return None
    prov = next((p for p in providers() if p.get("id") == pid), None)
    if not prov or not prov.get("api_key"):
        return None
    model = b.get("model") or prov.get("model") or ""
    if b.get("difficulty_routing"):
        tier = "easy" if difficulty == "easy" else "hard" if difficulty == "hard" else None
        if tier:
            model = prov.get("model_" + tier) or model
    out = {"model": model, "env": {}, "provider": prov}
    if prov["protocol"] == "anthropic":
        out["env"] = {"ANTHROPIC_BASE_URL": prov["base_url"],
                      "ANTHROPIC_AUTH_TOKEN": prov["api_key"]}
        if model:
            out["env"]["ANTHROPIC_MODEL"] = model
    else:
        out["env"] = {"ORCH_API_KEY": prov["api_key"]}
        out["codex_provider"] = {
            "name": "orch", "base_url": prov["base_url"],
            "env_key": "ORCH_API_KEY", "wire_api": prov.get("wire_api", "responses")}
    return out


def classify_difficulty(goal, verify_command):
    """难度启发式（规划器 LLM 判定优先，这里只做退化）。"""
    text = goal or ""
    hard_words = ("重构", "架构", "迁移", "安全", "性能", "并发", "分布式", "设计")
    if verify_command and (len(text) > 120 or any(w in text for w in hard_words)):
        return "hard"
    if len(text) <= 60 and not any(w in text for w in hard_words):
        return "easy"
    return "hard"
