# -*- coding: utf-8 -*-
"""本机工具配置的多源探测与导入。

从 modelhub 拆出（单文件行数治理）；路径常量与底层读写辅助仍在 modelhub，
经 _mh() 解析，保持对 modelhub 属性的打桩语义；旧调用面由 modelhub 再导出。
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time


def _mh():
    from . import modelhub
    return modelhub


def _prov(name, protocol, base_url, api_key, source, source_id, model="",
          model_easy="", model_hard="", wire_api=None, models=None):
    """归一化一个导入条目；地址非法时返回 None。"""
    base_url = _mh()._strip_endpoint(base_url)
    if not base_url.startswith(("http://", "https://")):
        return None
    proto = protocol if protocol in _mh()._PROTOCOLS else "openai"
    out = {
        "name": (name or "").strip() or source_id,
        "protocol": proto,
        "base_url": base_url,
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
        "model_easy": (model_easy or "").strip(),
        "model_hard": (model_hard or "").strip(),
        "source": source,
        "source_id": source_id,
    }
    if wire_api:
        out["wire_api"] = wire_api
    if _mh()._is_private_host(base_url):
        out["allow_private"] = True
    names = [n for n in (models or []) if n]
    if names:
        out["models"] = [{"name": n, "enabled": True, "priority": i + 1}
                         for i, n in enumerate(dict.fromkeys(names))]
        out["models_fetched_at"] = "配置导入"
    return out


def _merge_provider(data, prov):
    """按 (source, source_id) 幂等合并：重导入继承 id 并保留用户改动。

    保留项：用户填过的默认/难度模型、启停状态、已拉取的模型列表（含删除墓碑）。
    返回 added | updated | duplicate。
    """
    plist = data.setdefault("providers", [])
    sid = prov.get("source_id") or ""
    old = None
    if sid:
        old = next((p for p in plist if p.get("source") == prov.get("source")
                    and p.get("source_id") == sid), None)
    if old is None:
        # 跨来源去重：同一个网关常被多个工具分别记录（如 CCSwitch / Claude Code / ZCode），
        # 导入全部时按「地址 + 密钥」收敛成一条，避免同一供应商出现多份。
        same = next((p for p in plist if p.get("base_url") == prov.get("base_url")), None)
        # Native CLI sessions and ordinary API-key gateways may share the same
        # public endpoint, but they are different credential domains and must
        # remain independently selectable.
        if (same is not None and
                (same.get("credential_mode") == "cli" or
                 prov.get("credential_mode") == "cli")):
            same = None
        if same is not None:
            newkey = prov.get("api_key") or ""
            have = same.get("api_key") or ""
            if newkey and not have:
                same["api_key"] = newkey      # 从别的工具补齐缺失的密钥
                return "updated"
            if not (newkey and have and newkey != have):
                return "duplicate"            # 密钥相同，或新条目未带密钥
            # 同地址但密钥不同 = 同一网关的两个账号，继续按新增处理
    if old is not None:
        new = dict(old)
        new.update(prov)
        for f in ("model", "model_easy", "model_hard"):
            if (old.get(f) or "").strip():
                new[f] = old[f]              # 用户填过的字段不被配置覆盖
        if old.get("models") is not None:
            new["models"] = old["models"]    # 已有列表优先（含启停 / 排序 / 墓碑）
            new["models_fetched_at"] = old.get("models_fetched_at") or new.get("models_fetched_at")
        for f in ("allow_private", "wire_api"):
            if f in old:
                new[f] = old[f]
        if not old.get("enabled", True):
            new["enabled"] = False
        for i, p in enumerate(plist):
            if p is old:
                plist[i] = new
                break
        return "updated"
    prov = dict(prov)
    prov["id"] = _mh()._next_pid(plist)
    plist.append(prov)
    return "added"


# ---------------------------------------------------------------- 各来源采集器

def _cc_providers(app, pid, name, sc):
    """CCSwitch 单条 provider 记录 → 归一化条目（按 app_type 解析各自配置形状）。

    source_id 沿用历史格式 `<app>:<pid>`：老用户已有的供应商重导入是「更新」而非重复，
    也因此不会丢掉已拉取的模型列表与难度映射。
    """
    sid = "%s:%s" % (app, pid)
    if app in ("claude", "claude-desktop"):
        env = sc.get("env") or {}
        p = _prov(name, "anthropic", env.get("ANTHROPIC_BASE_URL"),
                  env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY"),
                  "ccswitch", sid,
                  model=env.get("ANTHROPIC_MODEL") or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or "",
                  model_easy=env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "",
                  model_hard=env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "")
        return [p] if p else []
    if app == "codex":
        key = ((sc.get("auth") or {}).get("OPENAI_API_KEY")) or ""
        text = sc.get("config") or ""
        model = _mh()._toml_quoted(text, "model") or ""
        secs = [(s, kv) for s, kv in _mh()._toml_parse(text)[1].items()
                if s.startswith("model_providers.")]
        out = []
        for i, (sec, kv) in enumerate(secs):
            ident = sec.split(".", 1)[1]
            # 首个区块用历史 id（更新老数据）；多区块时其余追加标识区分
            extra = (kv.get("name") or ident) if i else ""
            p = _prov(("%s · %s" % (name, extra)) if extra else name,
                      "openai", kv.get("base_url"),
                      kv.get("experimental_bearer_token") or key,
                      "ccswitch", sid if i == 0 else sid + ":" + ident, model=model,
                      wire_api=kv.get("wire_api") or "responses")
            if p:
                out.append(p)
        if not out:  # 兼容旧格式：base_url 直接写在顶层
            p = _prov(name, "openai", _mh()._toml_quoted(text, "base_url"), key, "ccswitch", sid,
                      model=model, wire_api=_mh()._toml_quoted(text, "wire_api") or "responses")
            if p:
                out.append(p)
        return out
    if app == "gemini":
        env = sc.get("env") or {}
        p = _prov(name, "google", env.get("GOOGLE_GEMINI_BASE_URL") or env.get("GEMINI_BASE_URL"),
                  env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY"), "ccswitch", sid,
                  model=env.get("GEMINI_MODEL") or "")
        return [p] if p else []
    if app == "openclaw":
        api = str(sc.get("api") or "").lower()
        proto = "anthropic" if "anthropic" in api else "openai"
        names = [m.get("id") or m.get("name")
                 for m in (sc.get("models") or []) if isinstance(m, dict)]
        p = _prov(name, proto, sc.get("baseUrl"), sc.get("apiKey"), "ccswitch", sid, models=names)
        return [p] if p else []
    return []


def _src_ccswitch():
    out = {"providers": [], "pricing": {}, "note": "", "found": os.path.isfile(_mh().CCSWITCH_DB)}
    if not out["found"]:
        return out
    try:
        con = _mh()._sqlite_ro(_mh().CCSWITCH_DB)
        try:
            cur = con.cursor()
            cur.execute("SELECT id, app_type, name, settings_config FROM providers")
            rows = cur.fetchall()
        finally:
            con.close()
    except Exception as e:
        out["error"] = "读取数据库失败：%r" % (e,)
        return out
    skipped = []
    for pid, app, name, cfg in rows:
        try:
            sc = json.loads(cfg)
        except Exception:
            skipped.append("%s/%s" % (app, name))
            continue
        got = [p for p in _cc_providers(app, pid, name, sc or {}) if p]
        if got:
            out["providers"].extend(got)
        else:
            skipped.append("%s/%s" % (app, name))
    try:
        con = _mh()._sqlite_ro(_mh().CCSWITCH_DB)
        try:
            cur = con.cursor()
            cur.execute("SELECT model_id, input_cost_per_million, output_cost_per_million "
                        "FROM model_pricing")
            for mid, cin, cout in cur.fetchall():
                try:
                    out["pricing"][mid] = {"in": float(cin), "out": float(cout)}
                except Exception:
                    pass
        finally:
            con.close()
    except Exception:
        pass
    if skipped:
        out["note"] = "跳过 {0} 条（缺地址或格式不识别）：{1}".format(
            len(skipped), "、".join(sorted(set(skipped))[:4]))
        out["note_args"] = [len(skipped), "、".join(sorted(set(skipped))[:4])]
    return out


def _src_claude():
    out = {"providers": [], "note": "", "found": os.path.isfile(_mh().CLAUDE_SETTINGS)}
    global_cfg = _mh()._read_json(_mh().CLAUDE_GLOBAL)
    out["found"] = out["found"] or os.path.isfile(_mh().CLAUDE_GLOBAL)
    if not out["found"]:
        return out
    env = (_mh()._read_json(_mh().CLAUDE_SETTINGS) or {}).get("env") or {}
    p = _prov("Claude Code", "anthropic", env.get("ANTHROPIC_BASE_URL"),
              env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY"),
              "claude", "claude:settings",
              model=env.get("ANTHROPIC_MODEL") or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or "",
              model_easy=env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "",
              model_hard=env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "")
    if p:
        out["providers"].append(p)
    elif env.get("ANTHROPIC_BASE_URL"):
        out["note"] = "settings.json 里的 ANTHROPIC_BASE_URL 不是 http/https"
    else:
        out["note"] = "settings.json 的 env 未配置 ANTHROPIC_BASE_URL"
    # Claude Code subscription/browser login is owned by Claude Code.  The
    # account marker is kept in ~/.claude.json while bearer tokens are stored
    # by the CLI's secure credential store; never convert it to an API key.
    account = global_cfg.get("oauthAccount") if isinstance(global_cfg, dict) else None
    if isinstance(account, dict) and any(str(account.get(k) or "").strip()
                                         for k in ("accountUuid", "organizationUuid", "emailAddress")):
        native = _prov("Claude Code 原生 OAuth", "anthropic", "https://api.anthropic.com", "",
                       "claude", "claude:oauth", model="sonnet",
                       models=["sonnet", "opus", "haiku"])
        if native:
            native["credential_mode"] = "cli"
            native["native_cli"] = "claude-code"
            native["native_models"] = True
            out["providers"].append(native)
    return out


def _src_codex():
    cfg = os.path.join(_mh().CODEX_DIR, "config.toml")
    auth_path = os.path.join(_mh().CODEX_DIR, "auth.json")
    cfg_exists = os.path.isfile(cfg)
    out = {"providers": [], "note": "", "found": os.path.isfile(cfg) or os.path.isfile(auth_path)}
    if not out["found"]:
        return out
    text = ""
    if cfg_exists:
        try:
            with open(cfg, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except Exception as e:
            out["error"] = "读取 config.toml 失败：%r" % (e,)
            return out
    auth = _mh()._read_json(auth_path) or {}
    key = auth.get("OPENAI_API_KEY") or ""
    chatgpt_oauth = (str(auth.get("auth_mode") or "").lower() in ("chatgpt", "chatgpt_auth_tokens")
                     or (isinstance(auth, dict) and any(auth.get(k) for k in
                                                        ("access_token", "refresh_token", "id_token"))))
    model = _mh()._toml_quoted(text, "model") or ""
    active = _mh()._toml_quoted(text, "model_provider") or ""
    top, sections = _mh()._toml_parse(text)
    for sec, kv in sections.items():
        if not sec.startswith("model_providers."):
            continue
        ident = sec.split(".", 1)[1]
        p = _prov(kv.get("name") or ("Codex " + ident), "openai", kv.get("base_url"),
                  kv.get("experimental_bearer_token") or key, "codex", "codex:" + ident,
                  model=(model if ident == active else ""),
                  wire_api=kv.get("wire_api") or "responses")
        if p:
            out["providers"].append(p)
    if not out["providers"] and cfg_exists:
        p = _prov("Codex CLI", "openai", top.get("base_url") or _mh()._toml_quoted(text, "base_url"),
                  key, "codex", "codex:default", model=model,
                  wire_api=_mh()._toml_quoted(text, "wire_api") or "responses")
        if p:
            out["providers"].append(p)
    if chatgpt_oauth:
        native_model = model or "gpt-5.5"
        native = _prov("Codex CLI 原生 ChatGPT OAuth", "openai", "https://chatgpt.com", "",
                       "codex", "codex:oauth", model=native_model,
                       models=list(dict.fromkeys([native_model, "gpt-5.5", "gpt-5.4"])))
        if native:
            native["credential_mode"] = "cli"
            native["native_cli"] = "codex-cli"
            native["native_models"] = True
            out["providers"].append(native)
    if not out["providers"] and cfg_exists:
        out["note"] = ("config.toml 未配置第三方 model_providers"
                       + ("" if key else "（且 auth.json 无 OPENAI_API_KEY）"))
    return out


def _src_zcode():
    out = {"providers": [], "note": "", "found": os.path.isfile(_mh().ZCODE_CONFIG)}
    if not out["found"]:
        return out
    d = _mh()._read_json(_mh().ZCODE_CONFIG) or {}
    default_model = str(d.get("model") or "")
    tail = default_model.split("/", 1)[1] if "/" in default_model else default_model
    bad = []
    nokey = []
    for pid, p in (d.get("provider") or {}).items():
        if not isinstance(p, dict):
            continue
        o = p.get("options") or {}
        kind = str(p.get("kind") or "").lower()
        proto = "anthropic" if "anthropic" in kind else "openai"
        names = list((p.get("models") or {}).keys())
        pr = _prov(p.get("name") or pid, proto, o.get("baseURL"), o.get("apiKey"),
                   "zcode", "zcode:%s" % pid,
                   model=(tail if tail in names else ""), models=names)
        if pr:
            if not pr.get("api_key"):
                nokey.append(str(p.get("name") or pid))
            out["providers"].append(pr)
        else:
            bad.append(str(p.get("name") or pid))
    notes = []
    if bad:
        notes.append("跳过 {0} 个（缺合法 baseURL）：{1}".format(
            len(bad), "、".join(bad[:4])))
    if nokey:
        # ZCode 的 builtin Plan 条目（zcode.z.ai/api/v1/zcode-plan 等）密钥不入
        # config.json（实测 credentials.json 里的 OAuth token 也不能直接当 API
        # key 用）——静默导入空密钥条目只会让用户在拉列表/适配测试时一头雾水
        notes.append("{0} 个未带出密钥——请在「模型接入」页手填：{1}".format(
            len(nokey), "、".join(nokey[:4])))
    if notes:
        out["note"] = "；".join(notes)
    return out


def _src_qwen():
    out = {"providers": [], "note": "",
           "found": (os.path.isfile(_mh().QWEN_SETTINGS) or
                     os.path.isfile(_mh().QWEN_OAUTH))}
    if not out["found"]:
        return out
    d = _mh()._read_json(_mh().QWEN_SETTINGS) or {}
    env = d.get("env") or {}
    cur = (d.get("model") or {}).get("name") if isinstance(d.get("model"), dict) else ""
    for key, items in (d.get("modelProviders") or {}).items():
        kl = str(key).lower()
        proto = "openai" if "openai" in kl else ("anthropic" if "anthropic" in kl else "openai")
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            base = it.get("baseUrl") or it.get("base_url")
            apikey = (it.get("apiKey") or env.get(it.get("envKey") or it.get("apiKeyEnv") or "")
                      or "")
            mid = it.get("id") or it.get("name") or key
            pr = _prov(it.get("name") or mid, proto, base, apikey, "qwen",
                       "qwen:%s:%s" % (key, mid),
                       model=(mid if mid and mid == cur else ""), models=[mid])
            if pr:
                out["providers"].append(pr)
    # Qwen Code keeps its device-flow OAuth session separate from settings and
    # refreshes it itself.  Never copy access/refresh tokens into api_key:
    # qwen-oauth is a CLI-owned credential domain, and its free tier was
    # discontinued on 2026-04-15 (the account now uses Coding Plan/paid quota).
    oauth = _mh()._read_json(_mh().QWEN_OAUTH)
    if _oauth_credentials_present(oauth):
        native = _prov("Qwen Code 原生 OAuth", "openai", "https://chat.qwen.ai", "",
                       "qwen", "qwen:oauth", model="coder-model",
                       models=["coder-model"])
        if native:
            native["credential_mode"] = "cli"
            native["native_cli"] = "qwencode"
            native["native_models"] = True
            out["providers"].append(native)
            out["note"] = "已发现 Qwen Code 原生 OAuth（免费额度已停用，按账号订阅/配额计费）"
    if not out["providers"]:
        out["note"] = "settings.json 的 modelProviders 为空"
    return out


def _src_gemini():
    envp = os.path.join(_mh().GEMINI_DIR, ".env")
    setp = os.path.join(_mh().GEMINI_DIR, "settings.json")
    out = {"providers": [], "note": "",
           "found": (os.path.isfile(envp) or os.path.isfile(setp)
                     or os.path.isfile(_mh().GEMINI_OAUTH))}
    if not out["found"]:
        return out
    env = _mh()._read_env_file(envp)
    key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or ""
    base = env.get("GOOGLE_GEMINI_BASE_URL") or env.get("GEMINI_BASE_URL") or ""
    if not base and key:
        base = "https://generativelanguage.googleapis.com"
    p = _prov("Gemini CLI", "google", base, key, "gemini", "gemini:env",
              model=env.get("GEMINI_MODEL") or "")
    if p:
        out["providers"].append(p)
    elif key:
        out["note"] = "未在 .env 找到 GEMINI_API_KEY / GOOGLE_GEMINI_BASE_URL"
    oauth = _mh()._read_json(_mh().GEMINI_OAUTH)
    if _oauth_credentials_present(oauth):
        names = ["auto", "flash", "pro"]
        cfg = _mh()._read_json(setp) or {}
        configured = cfg.get("model") if isinstance(cfg, dict) else ""
        if isinstance(configured, dict):
            configured = configured.get("name") or configured.get("id") or ""
        if configured:
            names.insert(0, str(configured))
        native = _prov("Gemini CLI 原生 OAuth", "openai",
                       "https://generativelanguage.googleapis.com", "",
                       "gemini", "gemini:oauth", model=str(configured or "auto"),
                       models=names)
        if native:
            native["credential_mode"] = "cli"
            native["native_cli"] = "gemini-cli"
            native["native_models"] = True
            out["providers"].append(native)
    if not out["providers"] and not out["note"]:
        out["note"] = "未找到 Gemini API Key 或有效 OAuth 凭据"
    return out


def _oauth_credentials_present(value):
    """Best-effort check for CLI-managed OAuth credentials.

    Refresh tokens remain useful after an access token expires, so expiry is
    deliberately not enforced here; the owning CLI refreshes the session.
    """
    if isinstance(value, dict):
        keys = {str(k).lower() for k in value}
        if keys.intersection({"access_token", "accesstoken", "refresh_token",
                              "refreshtoken", "token"}):
            return any(str(v or "").strip() for k, v in value.items()
                       if str(k).lower() in {"access_token", "accesstoken",
                                             "refresh_token", "refreshtoken",
                                             "token"})
        return any(_oauth_credentials_present(v) for v in value.values())
    if isinstance(value, list):
        return any(_oauth_credentials_present(v) for v in value if isinstance(v, (dict, list)))
    return False


def _kimi_native_models():
    """Read managed:kimi-code aliases from the user's Kimi config."""
    try:
        with open(_mh().KIMI_CONFIG, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError:
        return [], ""
    top, sections = _mh()._toml_parse(text)
    names = []
    for sec, vals in sections.items():
        if not sec.lower().startswith("models.") or not isinstance(vals, dict):
            continue
        provider = str(vals.get("provider") or "").lower()
        if provider != "managed:kimi-code" and "managed:kimi-code" not in provider:
            continue
        alias = sec.split(".", 1)[1].strip().strip('"').strip("'")
        if alias:
            names.append(alias)
    default = str(top.get("default_model") or top.get("defaultModel") or "").strip()
    if default and default in names:
        names.remove(default)
        names.insert(0, default)
    return list(dict.fromkeys(names)), default


def _src_kimi():
    cred_files = []
    if os.path.isdir(_mh().KIMI_CREDENTIALS):
        try:
            cred_files = [os.path.join(_mh().KIMI_CREDENTIALS, n)
                          for n in os.listdir(_mh().KIMI_CREDENTIALS)
                          if n.lower().endswith(".json")]
        except OSError:
            cred_files = []
    valid = any(_oauth_credentials_present(_mh()._read_json(p)) for p in cred_files)
    names, default = _kimi_native_models()
    out = {"providers": [], "note": "",
           "found": bool(cred_files or os.path.isfile(_mh().KIMI_CONFIG))}
    if valid:
        native = _prov("Kimi Code 原生 OAuth", "openai", "https://kimi.com", "",
                       "kimi", "kimi:oauth", model=default or (names[0] if names else ""),
                       models=names or ([default] if default else ["default"]))
        if native:
            native["credential_mode"] = "cli"
            native["native_cli"] = "kimi-code"
            native["native_models"] = True
            out["providers"].append(native)
    elif out["found"]:
        out["note"] = "未找到 Kimi Code 有效 OAuth 凭据或 managed:kimi-code 模型"
    return out


def _src_grok():
    """Discover Grok Build's browser OAuth session.

    Grok keeps the token in its own auth.json and applies the account's
    server-side tier/limits.  We therefore expose only the CLI's built-in Grok
    model names and never turn this into an API-key gateway.  The account tier
    is not guessed locally: a paid Grok login is not mislabeled as free.
    """
    out = {"providers": [], "note": "", "found": os.path.isfile(_mh().GROK_AUTH)
           or os.path.isfile(_mh().GROK_CONFIG)}
    auth = _mh()._read_json(_mh().GROK_AUTH)
    if not _grok_credentials_present(auth):
        if out["found"]:
            out["note"] = "未找到 Grok Build 有效 OAuth 登录态"
        return out
    default = ""
    top, sections = {}, {}
    try:
        with open(_mh().GROK_CONFIG, "r", encoding="utf-8-sig") as fh:
            top, sections = _mh()._toml_parse(fh.read())
        models = sections.get("models") or {}
        default = str(models.get("default") or top.get("model") or "").strip()
    except OSError:
        pass
    # Keep the built-in first-party names as a conservative fallback, but
    # prefer xAI model ids explicitly present in the user's config.  Custom
    # providers in the same file are intentionally ignored.
    names = []
    if default.startswith("grok-"):
        names.append(default)
    try:
        for sec, vals in sections.items():
            if not sec.lower().startswith("model.") or not isinstance(vals, dict):
                continue
            model_id = str(vals.get("model") or vals.get("id") or "").strip()
            if model_id.startswith("grok-"):
                names.append(model_id)
    except Exception:
        pass
    names.extend(("grok-4.6", "grok-4.5"))
    names = list(dict.fromkeys(names))
    native = _prov("Grok Build 原生 OAuth", "openai",
                   "https://grok.com", "", "grok", "grok:oauth",
                   model=names[0], models=names)
    if native:
        native["credential_mode"] = "cli"
        native["native_cli"] = "grok-build"
        native["native_models"] = True
        out["providers"].append(native)
    return out


def _src_mimo():
    """Discover MiMo Code's first-party zero-config channel."""
    out = {"providers": [], "note": "",
           "found": os.path.isfile(_mh().MIMO_CONFIG) or bool(shutil.which("mimo"))}
    if not out["found"]:
        return out
    names = ["mimo/mimo-auto"]
    try:
        with open(_mh().MIMO_CONFIG, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
        m = re.search(r'"model"\s*:\s*"([^"]+)"', text)
        configured = (m.group(1) if m else "").strip()
        if configured and configured not in names and configured.lower().startswith("mimo/"):
            names.insert(0, configured)
    except (OSError, UnicodeError):
        pass
    native = _prov("MiMo Code 原生免费通道", "openai", "https://mimo.xiaomi.com", "",
                   "mimo", "mimo:native-auto", model=names[0], models=names)
    if native:
        native["credential_mode"] = "cli"
        native["native_cli"] = "mimo-code"
        native["native_models"] = True
        native["free_tier"] = "limited"
        out["providers"].append(native)
    return out


_PI_NATIVE_DEFAULTS = {
    "anthropic": "claude-opus-4-8",
    "openai-codex": "gpt-6.1-sol",
    "github-copilot": "gpt-5.4",
    "openrouter": "moonshotai/kimi-k2.6",
}


def _pi_native_models(provider):
    """Best-effort discovery of Pi's built-in models for an OAuth provider.

    Pi's model catalog is CLI-owned and may be refreshed independently, so the
    command is authoritative when available.  The packaged default model is a
    conservative fallback for offline startup and tests.
    """
    provider = str(provider or "").strip().lower()
    names = []
    listed = False
    if shutil.which("pi"):
        try:
            p = subprocess.run(["pi", "--list-models", provider],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=20, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # A successful command is authoritative, including an explicit
            # "No models available" response.  Do not expose a packaged model
            # that this installed Pi cannot actually select.
            listed = p.returncode == 0
            for raw in (p.stdout or "").splitlines():
                line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw).strip()
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower() == provider:
                    model = parts[1].split(":", 1)[0].strip()
                    if model and model.lower() not in {"model", "provider"}:
                        names.append("%s/%s" % (provider, model))
        except (OSError, subprocess.SubprocessError):
            pass
    if listed:
        return list(dict.fromkeys(names))
    fallback = _PI_NATIVE_DEFAULTS.get(provider)
    if fallback:
        names.append("%s/%s" % (provider, fallback))
    return list(dict.fromkeys(names))


def _pi_oauth_usable(cred):
    """Match Pi's auth-storage schema and allow refreshable expiry."""
    if not isinstance(cred, dict):
        return False
    raw_access = cred.get("access") or cred.get("access_token")
    raw_refresh = cred.get("refresh") or cred.get("refresh_token")
    access = raw_access.strip() if isinstance(raw_access, str) else ""
    refresh = raw_refresh.strip() if isinstance(raw_refresh, str) else ""
    if not access or not refresh:
        return False
    expiry = (cred.get("expires") or cred.get("expires_at") or
              cred.get("expiresAt") or cred.get("expiry") or
              cred.get("expiry_date"))
    if expiry in (None, "") or isinstance(expiry, bool) or not isinstance(expiry, (int, float, str)):
        return False
    if isinstance(expiry, str):
        return False
    try:
        value = float(expiry)
        if not math.isfinite(value):
            return False
        if value > 100000000000:  # milliseconds since epoch
            value /= 1000.0
        expired = value <= time.time()
    except (TypeError, ValueError, OverflowError):
        return False
    return not expired or bool(refresh)


def _src_pi():
    """Discover Pi's stored OAuth sessions without importing API-key entries."""
    out = {"providers": [], "note": "", "found": os.path.isfile(_mh().PI_AUTH)}
    auth = _mh()._read_json(_mh().PI_AUTH)
    if not isinstance(auth, dict):
        if out["found"]:
            out["note"] = "Pi auth.json 不是可识别的 OAuth 凭据"
        return out
    for provider, cred in auth.items():
        if not isinstance(cred, dict) or str(cred.get("type") or "").lower() != "oauth":
            continue
        if not _pi_oauth_usable(cred):
            continue
        names = _pi_native_models(provider)
        if not names:
            continue
        proto = "anthropic" if str(provider).lower() == "anthropic" else "openai"
        native = _prov("Pi 原生 OAuth · %s" % provider, proto, "https://pi.dev", "",
                       "pi", "pi:oauth:%s" % provider, model=names[0], models=names)
        if native:
            native["credential_mode"] = "cli"
            native["native_cli"] = "pi"
            native["native_provider"] = str(provider)
            native["native_models"] = True
            out["providers"].append(native)
    if not out["providers"] and out["found"] and not out["note"]:
        out["note"] = "Pi auth.json 没有可用的内置 OAuth provider"
    return out


def _grok_credentials_present(value):
    """Recognize Grok's documented auth.json token shapes.

    Recent Grok builds store the browser token as
    ``{"https://accounts.x.ai/sign-in": {"key": "..."}}`` rather than an
    ``access_token`` field.  Keep this check Grok-specific so a generic
    arbitrary ``key`` in another CLI's config cannot look like OAuth.
    """
    if _oauth_credentials_present(value):
        return True
    if not isinstance(value, dict):
        return False
    for issuer, payload in value.items():
        if (str(issuer).lower().startswith(("https://accounts.x.ai/", "https://auth.x.ai/"))
                and isinstance(payload, dict)
                and str(payload.get("key") or "").strip()):
            return True
    return False


def _kimi_credential_paths():
    try:
        return [os.path.join(_mh().KIMI_CREDENTIALS, n)
                for n in os.listdir(_mh().KIMI_CREDENTIALS)
                if n.lower().endswith(".json")]
    except OSError:
        return []


def _src_opencode():
    out = {"providers": [], "note": "",
           "found": (os.path.isfile(_mh().OPENCODE_CONFIG) or os.path.isfile(_mh().OPENCODE_AUTH)
                     or bool(shutil.which("opencode")))}
    if not out["found"]:
        return out
    d = _mh()._read_json(_mh().OPENCODE_CONFIG) or {}
    auth = _mh()._read_json(_mh().OPENCODE_AUTH) or {}
    for pid, p in (d.get("provider") or {}).items():
        if not isinstance(p, dict):
            continue
        o = p.get("options") or {}
        key = o.get("apiKey") or ""
        if not key:
            a = auth.get(pid)
            key = (a.get("key") or a.get("apiKey") or "") if isinstance(a, dict) else (a or "")
        proto = "anthropic" if "anthropic" in str(p.get("npm") or "").lower() else "openai"
        names = list((p.get("models") or {}).keys())
        pr = _prov(p.get("name") or pid, proto, o.get("baseURL"), key,
                   "opencode", "opencode:%s" % pid, models=names)
        if pr:
            out["providers"].append(pr)
    # OpenCode also exposes a first-party pool of free models through its own
    # CLI.  These models use the CLI's local auth/session, so they must remain
    # a credential-free provider in CodeBee rather than being treated as an
    # ordinary OpenAI gateway.
    native = _opencode_native_models()
    if native:
        pr = _prov("OpenCode 原生免费模型", "openai", "https://opencode.ai", "",
                   "opencode", "opencode:native-free", models=native)
        if pr:
            pr["credential_mode"] = "cli"
            pr["native_cli"] = "opencode"
            pr["native_models"] = True
            out["providers"].append(pr)
    if not out["providers"]:
        out["note"] = "opencode.json 的 provider 为空"
    return out


def _opencode_native_models():
    """Return OpenCode's currently advertised free first-party models.

    ``opencode models`` is the authoritative list and works across OpenCode
    releases.  Keep discovery best-effort: a missing/old CLI must not make the
    normal provider import fail or block the settings page.
    """
    from . import runner as _runner
    # 服务进程的 PATH 是启动快照：opencode 装在 nvm 符号链接目录时快照里常没
    # 这条，which 落空但 CLI 明明在盘上（2026-10-16 实案，免费池因此整体
    # 探测不到）——与 runner/manager 同款落点探测兜底；裸名 spawn 起不动
    # .cmd 垫片（CreateProcess 只补 .exe），必须 resolve_command 给全路径。
    exe = shutil.which("opencode") or _runner._fallback_probe_cli("opencode")
    if not exe:
        return []
    try:
        p = subprocess.run(_runner.resolve_command("opencode") + ["models"],
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=20,
                           check=False, creationflags=getattr(subprocess,
                           "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in (p.stdout or "").splitlines():
        line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
        m = re.match(r"^(opencode/[^\s]+)$", line)
        if m and re.search(r"(?:free|contributor)", m.group(1), re.I):
            names.append(m.group(1))
    # Prefer DeepSeek in the default ordering while retaining every free model.
    return sorted(dict.fromkeys(names), key=lambda n: (0 if "deepseek" in n.lower() else 1, n))


def _src_continue():
    yp = os.path.join(_mh().CONTINUE_DIR, "config.yaml")
    jp = os.path.join(_mh().CONTINUE_DIR, "config.json")
    out = {"providers": [], "note": "",
           "found": os.path.isfile(yp) or os.path.isfile(jp)}
    if not out["found"]:
        return out
    models = []
    d = _mh()._read_json(jp)
    if isinstance(d, dict) and isinstance(d.get("models"), list):
        models = d["models"]
    elif os.path.isfile(yp):
        models = _mh()._yaml_models(yp)
    for m in models:
        if not isinstance(m, dict):
            continue
        base = m.get("apiBase") or m.get("api_base") or m.get("baseUrl")
        proto = "anthropic" if "anthropic" in str(m.get("provider") or "").lower() else "openai"
        name = m.get("name") or m.get("model") or "Continue"
        mid = m.get("model") or ""
        pr = _prov(name, proto, base, m.get("apiKey") or m.get("api_key"), "continue",
                   "continue:%s" % name, model=mid, models=[mid] if mid else None)
        if pr:
            out["providers"].append(pr)
    if not out["providers"]:
        out["note"] = "配置里没有带 apiBase 的模型条目"
    return out


def _src_cursor():
    out = {"providers": [], "note": "", "found": os.path.isfile(_mh().CURSOR_DB)}
    if not out["found"]:
        return out
    rows = _mh()._vsc_rows(_mh().CURSOR_DB)
    blob = next((v for k, v in rows.items() if "persistentStorage.applicationUser" in k), "")

    def field(name):
        m = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(name), blob)
        return m.group(1) if m else ""

    openai_key = rows.get("cursorAuth/openAIKey") or ""
    claude_key = rows.get("cursorAuth/claudeKey") or ""
    if openai_key and field("openAIBaseUrl"):
        p = _prov("Cursor · OpenAI", "openai", field("openAIBaseUrl"), openai_key,
                  "cursor", "cursor:openai")
        if p:
            out["providers"].append(p)
    if claude_key and field("claudeBaseUrl"):
        p = _prov("Cursor · Claude", "anthropic", field("claudeBaseUrl"), claude_key,
                  "cursor", "cursor:claude")
        if p:
            out["providers"].append(p)
    if not out["providers"]:
        out["note"] = "Cursor 未自定义 API Key / Base URL（官方订阅无本地凭证可导入）"
    return out


def _trae_models(d):
    """Trae 的 model_list 可能是 [{...}] 或 {agent: [{...}]}。"""
    if isinstance(d, list):
        for it in d:
            if isinstance(it, dict):
                yield it
    elif isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        yield it


def _src_trae():
    out = {"providers": [], "note": "", "found": False}
    seen = set()  # 同一模型会在多个 key / 多个库中重复出现，按归一化后的条目去重
    for db in _mh().TRAE_DBS:
        if not os.path.isfile(db):
            continue
        out["found"] = True
        for k, s in _mh()._vsc_rows(db).items():
            if ("model_list" not in k and "modelList" not in k) or "base_url" not in s:
                continue
            try:
                d = json.loads(s)
            except Exception:
                continue
            for it in _trae_models(d):
                base, ak = it.get("base_url"), it.get("ak")
                if not base or not ak:
                    continue
                name = it.get("display_name") or it.get("name") or "Trae"
                proto = "anthropic" if "anthropic" in str(base).lower() else "openai"
                p = _prov(name, proto, base, ak, "trae",
                          "trae:%s:%s" % (name, _mh()._strip_endpoint(base)))
                if p and p["source_id"] not in seen:
                    seen.add(p["source_id"])
                    out["providers"].append(p)
    if not out["providers"]:
        out["note"] = ("Trae 未添加自定义模型（预置模型的 AK / BaseURL 为空，无凭证可导入）"
                       if out["found"] else "")
    return out


def _dotenv_get(path, key):
    """极简 dotenv：取 KEY=VALUE 行的值（dsh 的 ~/.dsh/.env 是 credentials-local 存储）。"""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _src_dsh():
    """DeepSeek Harness（dsh）：~/.dsh/settings.yaml 的 llm-deepseek 端点与模型。

    dsh 的约定是密钥不进 settings.yaml——走 ~/.dsh/.env（credentials-local 存储）
    或进程 env 的 DEEPSEEK_API_KEY；都没有时仅登记供应商，导入后补填密钥。
    """
    out = {"providers": [], "note": "", "found": os.path.isfile(_mh().DSH_SETTINGS)}
    if not out["found"]:
        return out
    d = {}
    try:
        import yaml
        with open(_mh().DSH_SETTINGS, "r", encoding="utf-8-sig") as f:
            d = yaml.safe_load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    llm = d.get("llm-deepseek")
    if not isinstance(llm, dict):
        llm = {}
    adm = d.get("agent-default-model")
    if not isinstance(adm, dict):
        adm = {}
    base = str(llm.get("baseURL") or "")
    names = [str(m["id"]) for m in (llm.get("models") or [])
             if isinstance(m, dict) and m.get("id")]
    if not base:
        out["note"] = "settings.yaml 未配置 llm-deepseek.baseURL"
        return out
    key = (_dotenv_get(os.path.join(_mh().DSH_DIR, ".env"), "DEEPSEEK_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY") or "")
    p = _prov("DeepSeek Harness", "openai", base, key, "dsh", "dsh:llm-deepseek",
              model=str(adm.get("model") or "") or (names[0] if names else ""))
    if p:
        p["models"] = [{"name": n, "enabled": True, "priority": i + 1}
                       for i, n in enumerate(dict.fromkeys(names))]
        out["providers"].append(p)
        if not key:
            out["note"] = ("未找到 DEEPSEEK_API_KEY（dsh 把密钥放在 ~/.dsh/.env 或环境变量），"
                           "导入后请在编辑里补填")
    return out


# (来源 id, 显示名, 说明, 采集器, 配置文件路径)
_SOURCES = [
    ("ccswitch", "CCSwitch", "本地库（Claude / Claude Desktop / Codex / Gemini / OpenClaw）",
     _src_ccswitch, lambda: [_mh().CCSWITCH_DB]),
    ("claude", "Claude Code", "~/.claude/settings.json / ~/.claude.json（含原生 OAuth）",
     _src_claude, lambda: [_mh().CLAUDE_SETTINGS, _mh().CLAUDE_GLOBAL]),
    ("codex", "Codex CLI", "~/.codex/config.toml + auth.json（含 ChatGPT OAuth）",
     _src_codex, lambda: [os.path.join(_mh().CODEX_DIR, "config.toml"),
                          os.path.join(_mh().CODEX_DIR, "auth.json")]),
    ("zcode", "ZCode", "~/.zcode/v2/config.json 的 provider 表",
     _src_zcode, lambda: [_mh().ZCODE_CONFIG]),
     ("qwen", "Qwen Code", "~/.qwen/settings.json / oauth_creds.json（含原生 OAuth）",
      _src_qwen, lambda: [_mh().QWEN_SETTINGS, _mh().QWEN_OAUTH]),
    ("gemini", "Gemini CLI", "~/.gemini 的 .env / settings.json",
     _src_gemini, lambda: [os.path.join(_mh().GEMINI_DIR, ".env"),
                           os.path.join(_mh().GEMINI_DIR, "settings.json"), _mh().GEMINI_OAUTH]),
    ("opencode", "OpenCode", "配置 provider + opencode models 原生免费模型",
     _src_opencode, lambda: [_mh().OPENCODE_CONFIG, _mh().OPENCODE_AUTH]),
    ("kimi", "Kimi Code", "~/.kimi-code 原生 OAuth + managed:kimi-code 模型",
     _src_kimi, lambda: [_mh().KIMI_CONFIG] + _kimi_credential_paths()),
    ("grok", "Grok Build", "~/.grok/auth.json 原生 OAuth（账户配额由 Grok 管理）",
     _src_grok, lambda: [_mh().GROK_AUTH, _mh().GROK_CONFIG]),
    ("mimo", "MiMo Code", "MiMo Auto 原生免费通道（限时，凭据由 MiMo CLI 管理）",
     _src_mimo, lambda: [_mh().MIMO_CONFIG]),
    ("pi", "Pi", "~/.pi/agent/auth.json 原生 OAuth（使用订阅账号额度，不标记 Free）",
     _src_pi, lambda: [_mh().PI_AUTH, _mh().PI_MODELS]),
    ("continue", "Continue", "~/.continue/config.yaml 的 models",
     _src_continue, lambda: [os.path.join(_mh().CONTINUE_DIR, "config.yaml"),
                             os.path.join(_mh().CONTINUE_DIR, "config.json")]),
    ("cursor", "Cursor", "Cursor 的 cursorAuth/openAIKey + 自定义 Base URL",
     _src_cursor, lambda: [_mh().CURSOR_DB]),
    ("trae", "Trae", "Trae / Trae SOLO 中自定义模型的 Base URL + AK",
     _src_trae, lambda: list(_mh().TRAE_DBS)),
    ("dsh", "DeepSeek Harness", "~/.dsh/settings.yaml 的 llm-deepseek（密钥走 ~/.dsh/.env）",
     _src_dsh, lambda: [_mh().DSH_SETTINGS]),
]

_SOURCE_NAMES = {sid: name for sid, name, _d, _f, _p in _SOURCES}


def source_names():
    """来源 id → 显示名（UI 打标签用）。"""
    return dict(_SOURCE_NAMES)


def _collect(fn):
    try:
        res = fn() or {}
    except Exception as e:
        return {"providers": [], "pricing": {}, "note": "", "found": False,
                "error": "%s: %s" % (type(e).__name__, e)}
    res.setdefault("providers", [])
    res.setdefault("pricing", {})
    res.setdefault("note", "")
    res.setdefault("note_args", [])
    res.setdefault("error", "")
    res.setdefault("found", False)
    return res


def sources():
    """本机所有支持的导入来源及探测结果（配置文件是否存在、可导入几条）。"""
    rows = []
    for sid, name, desc, fn, get_paths in _SOURCES:
        ps = [p for p in get_paths() if p]
        found = any(os.path.isfile(p) for p in ps)
        row = {"id": sid, "name": name, "desc": desc, "paths": ps,
               "found": found, "count": 0, "note": "", "note_args": [], "error": ""}
        if found:
            res = _collect(fn)
            row["count"] = len(res["providers"])
            row["note"] = res["note"]
            row["note_args"] = res.get("note_args", [])
            row["error"] = res["error"]
        rows.append(row)
    return rows


def import_sources(ids=None):
    """从选定的本机工具配置幂等导入供应商。

    ids=None 表示全部来源；显式传入空列表表示什么都不导入（UI 未勾选时不误触）。
    返回 {imported, added, updated, duplicate, sources: [...], message}。
    """
    want = None if ids is None else set(ids)
    chosen = [(sid, name, fn) for sid, name, _d, fn, _p in _SOURCES
              if want is None or sid in want]
    out = {"imported": 0, "added": 0, "updated": 0, "duplicate": 0, "pricing": 0,
           "sources": [], "message": ""}
    with _mh()._LOCK:
        data = _mh()._load()
        for sid, name, fn in chosen:
            res = _collect(fn)
            added = updated = dup = 0
            for prov in res["providers"]:
                if not prov:
                    continue
                state = _merge_provider(data, prov)
                if state == "added":
                    added += 1
                elif state == "duplicate":
                    dup += 1
                else:
                    updated += 1
            if res["pricing"]:
                data.setdefault("pricing", {}).update(res["pricing"])
                out["pricing"] = len(data["pricing"])
            out["added"] += added
            out["updated"] += updated
            out["duplicate"] += dup
            out["sources"].append({
                "id": sid, "name": name, "found": res["found"],
                "added": added, "updated": updated, "duplicate": dup,
                "count": added + updated, "note": res["note"],
                "note_args": res.get("note_args", []), "error": res["error"]})
        if out["added"] or out["updated"] or out["pricing"]:
            _mh()._save(data)
    out["imported"] = out["added"] + out["updated"]
    parts = []
    if out["added"]:
        parts.append("新增 %d" % out["added"])
    if out["updated"]:
        parts.append("更新 %d" % out["updated"])
    if out["duplicate"]:
        parts.append("跳过重复 %d" % out["duplicate"])
    if out["pricing"]:
        parts.append("价格 %d 条" % out["pricing"])
    out["message"] = ("导入完成：" + "，".join(parts)) if parts else "未发现可导入的供应商"
    return out


def import_ccswitch():
    """兼容旧接口：仅导入 CCSwitch。返回 (导入数, 说明)。"""
    r = import_sources(["ccswitch"])
    s = r["sources"][0] if r["sources"] else {}
    msg = r["message"]
    if s.get("note"):
        msg += "；" + s["note"]
    if s.get("error"):
        msg += "；" + s["error"]
    return r["imported"], msg
