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

import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.request

from . import paths


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁用重定向：SSRF 防护的一部分。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "models.json"


def _load():
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": [], "bindings": {}}


# ---------------------------------------------------------------- 模型列表拉取

_LITE = ("mini", "flash", "lite", "nano", "small", "tiny", "8b", "7b", "4b")
_HEAVY = ("opus", "pro", "max", "ultra", "plus", "heavy", "codex")


def _auto_priority(name):
    """按模型名启发式估强弱：分越高越强（优先级越靠前）。仅用于新模型的初始排序。"""
    n = (name or "").lower()
    score = 50.0
    if any(k in n for k in _LITE):
        score -= 30
    if any(k in n for k in _HEAVY):
        score += 15
    vers = re.findall(r"(\d+)\.(\d+)", n)
    if vers:
        score += min(20.0, float(vers[0][0]) * 4 + float(vers[0][1]))
    else:
        m = re.search(r"(\d+)", n)
        if m:
            score += min(12.0, float(m.group(1)) * 2)
    for i, fam in enumerate(("gpt-5", "claude", "gemini", "glm-5", "deepseek", "qwen", "kimi", "grok")):
        if fam in n:
            score += 10 - i
            break
    return score


def _auth_header_variants(key, protocol):
    h1 = {"Authorization": "Bearer " + key, "User-Agent": "tutti-orchestrator/1.0"}
    if protocol == "anthropic":
        h2 = {"x-api-key": key, "anthropic-version": "2023-06-01",
              "User-Agent": "tutti-orchestrator/1.0"}
        return [h1, h2]
    return [h1]


def _validate_host(url, allow_private):
    """SSRF 防护：校验协议与解析后 IP；私网/环回仅在 allow_private 时放行。"""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        return None, "协议必须是 http/https"
    host = p.hostname
    if not host:
        return None, "缺少主机名"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return None, "域名解析失败: %r" % e
    for i in infos:
        a = ipaddress.ip_address(i[4][0])
        if not allow_private and (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved):
            return None, ("拒绝访问私网/环回地址 %s。内网自建网关属预期场景：该供应商"
                          "导入/新增内网 IP 时会自动开启 allow_private。" % a)
    return host, ""


def _fetch_models_http(base_url, api_key, protocol, allow_private=False):
    """GET {base}/models 拉取模型列表。返回 (names, err)。

    仅访问用户自己配置的供应商地址；协议白名单 + 解析 IP 边界校验 + 禁用重定向。
    """
    import urllib.parse
    base = (base_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None, "base_url 必须是 http/https"
    urls = [base + "/models"] if base.endswith("/v1") else [base + "/v1/models", base + "/models"]
    last_err = ""
    opener = urllib.request.build_opener(_NoRedirect)
    for url in urls:
        host_info = _validate_host(url, allow_private)
        if host_info is None:
            last_err = host_info[1]
            continue
        for headers in _auth_header_variants(api_key, protocol):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with opener.open(req, timeout=15) as resp:
                    raw = resp.read(2 * 1024 * 1024)  # 响应上限 2MB
                data = json.loads(raw.decode("utf-8", "replace"))
                items = data.get("data") if isinstance(data, dict) else data
                names = []
                for it in items or []:
                    if isinstance(it, str):
                        names.append(it)
                    elif isinstance(it, dict):
                        names.append(it.get("id") or it.get("name") or it.get("model"))
                names = [n for n in names if n]
                if names:
                    return names, ""
                last_err = url + " 返回 200 但未解析到模型"
            except Exception as e:
                last_err = "%s → %r" % (url, e)
    return None, last_err


def refresh_models(provider_id):
    """拉取单个供应商的可用模型列表（保留既有启停与手动优先级）。返回 (数量, 错误)。"""
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
    if not prov:
        return 0, "供应商不存在"
    if not prov.get("api_key"):
        return 0, "该供应商未配置密钥"
    names, err = _fetch_models_http(prov.get("base_url"), prov["api_key"],
                                    prov.get("protocol"), bool(prov.get("allow_private")))
    if names is None:
        return 0, err
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return 0, "供应商不存在"
        old = {m.get("name"): m for m in prov.get("models") or []}
        next_prio = max([m.get("priority", 0) for m in old.values()] or [0])
        existing, fresh = [], []
        for n in names:
            o = old.get(n)
            if o:
                existing.append({"name": n, "enabled": bool(o.get("enabled", True)),
                                 "priority": o.get("priority", 0)})
            else:
                fresh.append({"name": n, "enabled": True, "priority": 0,
                              "auto": _auto_priority(n)})
        # 新模型按自动强弱估分整体排在既有手动排序之后（不推翻手动顺序）
        fresh.sort(key=lambda m: -m.pop("auto"))
        allm = existing + fresh
        allm.sort(key=lambda m: m.get("priority", 999))
        for i, m in enumerate(allm):
            m["priority"] = i + 1
        prov["models"] = allm
        prov["models_fetched_at"] = time.strftime("%Y-%m-%d %H:%M")
        _save(data)
        return len(allm), ""


def refresh_all_async():
    """后台逐个刷新全部供应商的模型列表（导入后自动触发）。返回供应商数。"""
    ids = [p.get("id") for p in providers() if p.get("api_key")]

    def _worker():
        for pid in ids:
            try:
                refresh_models(pid)
            except Exception:
                pass

    threading.Thread(target=_worker, name="model-refresh", daemon=True).start()
    return len(ids)


def model_op(provider_id, name, op):
    """模型启停与优先级调序。op: enable | disable | up | down。"""
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return "供应商不存在"
        models = prov.get("models") or []
        target = next((m for m in models if m.get("name") == name), None)
        if not target:
            return "模型不存在"
        if op in ("enable", "disable"):
            target["enabled"] = (op == "enable")
        elif op in ("up", "down"):
            ordered = sorted(models, key=lambda m: m.get("priority", 999))
            idx = ordered.index(target)
            j = idx - 1 if op == "up" else idx + 1
            if 0 <= j < len(ordered):
                ordered[idx], ordered[j] = ordered[j], ordered[idx]
            for i, m in enumerate(ordered):
                m["priority"] = i + 1
        else:
            return "未知操作 " + op
        _save(data)
        return None


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


def _is_private_host(url):
    """主机是私网/环回 IP 字面量或 localhost 时返回 True（自动放行内网网关）。"""
    import urllib.parse
    host = urllib.parse.urlsplit(url or "").hostname or ""
    try:
        a = ipaddress.ip_address(host)
        return a.is_private or a.is_loopback
    except ValueError:
        return host == "localhost"


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
        if _is_private_host(target["base_url"]):
            target["allow_private"] = True
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
        # 顺带导入模型价格表（供画廊展示，价格来自 CCSwitch 本地库）
        try:
            con2 = sqlite3.connect("file:%s?mode=ro" % CCSWITCH_DB.replace("\\", "/"), uri=True)
            cur2 = con2.cursor()
            cur2.execute("SELECT model_id, input_cost_per_million, output_cost_per_million "
                         "FROM model_pricing")
            pricing = {}
            for mid, cin, cout in cur2.fetchall():
                try:
                    pricing[mid] = {"in": float(cin), "out": float(cout)}
                except Exception:
                    pass
            data["pricing"] = pricing
            con2.close()
        except Exception:
            pass
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
            if _is_private_host(prov["base_url"]):
                prov["allow_private"] = True
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

def _enabled_models(prov):
    ms = [m for m in (prov.get("models") or []) if m.get("enabled", True)]
    return sorted(ms, key=lambda m: m.get("priority", 999))


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
    if r.get("model_fallbacks"):
        a["model_fallbacks"] = r["model_fallbacks"]
    if r.get("codex_provider"):
        a["codex_provider"] = r["codex_provider"]
    return a


def resolve_binding(agent_kind_or_id, difficulty="default"):
    """返回 {env:{}, model:..., model_fallbacks:[...], codex_provider:...} 或 None。

    difficulty: easy | hard | default。
    模型解析优先级：绑定显式模型 > 难度映射字段(model_easy/hard) > 供应商默认
    模型 > 启用模型的优先级列表（困难→第 1 最强，简单→末位最省）。
    """
    b = bindings().get(agent_kind_or_id) or bindings().get(
        "codex-cli" if agent_kind_or_id == "codex" else "claude-code") or {}
    pid = b.get("provider_id")
    if not pid:
        return None
    prov = next((p for p in providers() if p.get("id") == pid), None)
    if not prov or not prov.get("api_key"):
        return None
    names = [m["name"] for m in _enabled_models(prov)]
    routing = bool(b.get("difficulty_routing"))
    tier = difficulty if difficulty in ("easy", "hard") else None

    model = b.get("model") or ""
    if not model and routing and tier:
        model = prov.get("model_" + tier) or ""
    if not model:
        model = prov.get("model") or ""
    if not model and names:
        model = names[0] if (not routing or difficulty != "easy") else names[-1]
    fallbacks = [n for n in names if n != model][:3]
    out = {"model": model, "env": {}, "provider": prov, "model_fallbacks": fallbacks}
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


def models_view():
    """扁平化模型目录（画廊展示用）：跨供应商的模型卡片列表 + 价格。"""
    pricing = _load().get("pricing") or {}
    rows = []
    for p in providers():
        base = {"provider_id": p.get("id"), "provider_name": p.get("name"),
                "protocol": p.get("protocol"),
                "fetched_at": p.get("models_fetched_at") or "",
                "fetch_status": "ok" if p.get("models") is not None else "未获取"}
        ms = p.get("models")
        if ms is None:
            rows.append(dict(base, name="", enabled=False, priority=0))
            continue
        for m in sorted(ms, key=lambda x: x.get("priority", 999)):
            pr = pricing.get(m["name"]) or {}
            rows.append(dict(base, name=m["name"], enabled=bool(m.get("enabled", True)),
                             priority=m.get("priority", 0),
                             price_in=pr.get("in"), price_out=pr.get("out")))
    return rows


def classify_difficulty(goal, verify_command):
    """难度启发式（规划器 LLM 判定优先，这里只做退化）。"""
    text = goal or ""
    hard_words = ("重构", "架构", "迁移", "安全", "性能", "并发", "分布式", "设计")
    if verify_command and (len(text) > 120 or any(w in text for w in hard_words)):
        return "hard"
    if len(text) <= 60 and not any(w in text for w in hard_words):
        return "easy"
    return "hard"
