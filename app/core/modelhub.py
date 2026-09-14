# -*- coding: utf-8 -*-
"""模型接入层：供应商注册表 + 本机多源导入 + 按 CLI 绑定 + 难度路由。

data/models.json 结构：
{
  "providers": [{"id","name","protocol":"anthropic|openai|google","base_url","api_key",
                 "model","model_easy","model_hard","source","source_id"}],
  "bindings": {"<agent-id>": {"chain",           # 跨厂商有序模型链（唯一真源）：
                             [{"provider_id","model"}, ...]
                             # 第 1 条主模型，其余降级备选；各条可来自不同供应商
                             "provider_id",    # 主供应商（链首非空 provider 的冗余，兼容旧读方）
                             "models",         # 链内模型名序列（冗余，兼容旧读方）
                             "model",          # 链首模型名（冗余）
                             "difficulty_routing"}},
  "orchestrator": {"provider_id", "model", "enabled"}   # 编排中枢：直连 API 的规划/管理模型
}

导入来源（只读，不改写任何工具自身的配置）：CCSwitch、Claude Code、Codex CLI、
ZCode、Qwen Code、Gemini CLI、OpenCode、Continue、Cursor、Trae。

密钥只落本地盘（与 CCSwitch 同等信任域）；API 一律脱敏返回。
anthropic/openai 可注入到 CLI；google 仅登记（当前无对应 CLI 可注入）。
启用的供应商/模型自动排在最前（启用即置顶，成为该供应商的默认模型 #1），
停用的退到启用块之后；排序同时落盘，UI 与运行时解析看到的是同一顺序。
按运行注入，不改写任何 CLI 的全局配置文件：
  claude : env ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL
  codex  : env ORCH_API_KEY + -c model_provider/model_providers.orch.* 覆盖
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
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

# 可注入 CLI 的协议；google 只登记（当前 catalog 里没有可注入的 gemini CLI）
_PROTOCOLS = ("anthropic", "openai", "google")
_BINDABLE_PROTOCOLS = ("anthropic", "openai")


def _load():
    try:
        return _normalize_ids(json.loads(_FILE.read_text(encoding="utf-8")))
    except Exception:
        return {"providers": [], "bindings": {}}


def _normalize_ids(data):
    """修复历史数据的重复 prov-N id（早期按 len(plist)+1 分配会撞号）。

    只给「重复出现」的条目重新编号，首次出现的保留原 id；不改顺序，也不动绑定
    引用——绑定原本就解析到首次出现的那条，修复前后语义一致。无重复时零改动。
    重复 id 会让 UI 无法区分、按供应商的操作（删除/启停/刷新）打到错误的那条。
    """
    plist = data.get("providers") or []
    seen, has_dup = set(), False
    for p in plist:
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
        else:
            has_dup = True
            break
    if not has_dup:
        return data
    seen = set()
    for p in plist:
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
            continue
        new_id = _next_pid(plist)
        while new_id in seen:
            new_id = "prov-%d" % (int(new_id.split("-")[1]) + 1)
        p["id"] = new_id
        seen.add(new_id)
    return data


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
    if protocol == "google":
        return [{"x-goog-api-key": key, "User-Agent": "tutti-orchestrator/1.0"}]
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
    google 走 /v1beta/models，返回项形如 {"name": "models/gemini-x"}。
    """
    import urllib.parse
    base = (base_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None, "base_url 必须是 http/https"
    if protocol == "google":
        urls = [base + "/models"] if base.endswith("/v1beta") else [base + "/v1beta/models"]
    else:
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
                items = (data.get("data") or data.get("models")) if isinstance(data, dict) else data
                names = []
                for it in items or []:
                    if isinstance(it, str):
                        names.append(it)
                    elif isinstance(it, dict):
                        n = it.get("id") or it.get("name") or it.get("model")
                        if n and protocol == "google":
                            n = n.split("/")[-1]  # "models/gemini-x" → "gemini-x"
                        names.append(n)
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
        existing, fresh, hidden = [], [], []
        for n in names:
            o = old.get(n)
            if o:
                # 保留 hidden：用户删掉的模型刷新时不能被重新带回
                item = {"name": n, "enabled": bool(o.get("enabled", True)),
                        "priority": o.get("priority", 0),
                        "hidden": bool(o.get("hidden"))}
                (hidden if item["hidden"] else existing).append(item)
            else:
                fresh.append({"name": n, "enabled": True,
                              "auto": _auto_priority(n)})
        # 已删除但本次未返回的条目也保留，否则下次拉取会当作新模型“复活”
        hidden += [dict(o) for n, o in old.items()
                   if o.get("hidden") and n not in names]
        # 顺序 = 既有启用（保持用户手动排序）+ 新模型（按强弱估分）+ 既有停用 + 已删除墓碑。
        # 启用块始终在停用块之前（顺带自愈历史数据）；新模型不能插到启用块前面，
        # 否则会顶掉手动顺序与「#1 即默认模型」语义。
        existing.sort(key=lambda m: m.get("priority", 999))
        fresh.sort(key=lambda m: -m.pop("auto"))
        hidden.sort(key=lambda m: m.get("priority", 999))
        allm = ([m for m in existing if m.get("enabled", True)] + fresh +
                [m for m in existing if not m.get("enabled", True)] + hidden)
        for i, m in enumerate(allm):
            m["priority"] = i + 1
        prov["models"] = allm
        prov["models_fetched_at"] = time.strftime("%Y-%m-%d %H:%M")
        _save(data)
        return len(allm), ""


def refresh_all_async():
    """后台逐个刷新全部供应商的模型列表（导入后自动触发）。返回供应商数。

    已停用的供应商跳过——停用即「暂不参与编排」，也不该再发网络请求。
    """
    ids = [p.get("id") for p in providers()
           if p.get("api_key") and p.get("enabled", True)]

    def _worker():
        for pid in ids:
            try:
                refresh_models(pid)
            except Exception:
                pass

    threading.Thread(target=_worker, name="model-refresh", daemon=True).start()
    return len(ids)


def _ranked(models):
    """参与排序的模型（已删除的除外），按优先级。"""
    return sorted([m for m in models if not m.get("hidden")],
                  key=lambda m: m.get("priority", 999))


def _renumber(models):
    """重排优先级：可见模型按现有顺序占 1..k，已删除的顺位排在末尾。"""
    visible = _ranked(models)
    hidden = sorted([m for m in models if m.get("hidden")],
                    key=lambda m: m.get("priority", 999))
    for i, m in enumerate(visible + hidden):
        m["priority"] = i + 1


def _promote(models, front_names=()):
    """启用置顶重排：front_names（本次转为启用的模型）排最前，
    其余启用模型按原优先级跟随，停用的退到启用块之后，已删除的仍在末尾。

    启用即「参与编排」，置顶后它就是该供应商的默认模型（#1）；停用的
    无论原优先级多靠前，都排在所有启用模型之后。排序稳定，各块内部
    保持既有相对顺序（手动拖拽的结果不会被无关操作打乱）。
    """
    front = {n for n in front_names if n}
    visible = _ranked(models)
    newly = [m for m in visible if m.get("name") in front]
    rest = [m for m in visible if m.get("name") not in front]
    rest.sort(key=lambda m: not m.get("enabled", True))
    hidden = sorted([m for m in models if m.get("hidden")],
                    key=lambda m: m.get("priority", 999))
    for i, m in enumerate(newly + rest + hidden):
        m["priority"] = i + 1


def _drop_model_refs(data, prov, name):
    """删除模型后清空指向它的默认模型/难度映射与 CLI 绑定，避免留下悬空模型名。"""
    pid = prov.get("id")
    for field in ("model", "model_easy", "model_hard"):
        if (prov.get(field) or "") == name:
            prov[field] = ""
    for b in (data.get("bindings") or {}).values():
        if b.get("provider_id") == pid and (b.get("model") or "") == name:
            b["model"] = ""
        if b.get("models"):
            b["models"] = [m for m in b["models"] if m != name]
        if b.get("chain"):
            b["chain"] = [c for c in b["chain"]
                          if not (c.get("provider_id") == pid and c.get("model") == name)]
            _sync_chain_refs(b)


_MODEL_OPS = ("enable", "disable", "delete", "restore")


def _find_prov(data, provider_id):
    return next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)


def _apply_model_op(data, prov, models, name, op):
    """对单个模型套用 enable/disable/delete/restore。返回 (是否改动, 提示或错误)。

    无改动且无可提示原因时返回 (False, "")——批量调用里「已是目标状态」不算失败。
    """
    target = next((m for m in models if m.get("name") == name), None)
    if not target:
        return False, "模型不存在"
    if op == "enable":
        if target.get("hidden"):
            return False, "已删除的模型不能启用（先恢复）"
        if target.get("enabled", True):
            return False, ""
        target["enabled"] = True
    elif op == "disable":
        if target.get("hidden"):
            return False, "已删除的模型不能停用"
        if not target.get("enabled", True):
            return False, ""
        target["enabled"] = False
    elif op == "delete":
        if target.get("hidden"):
            return False, ""
        target["hidden"] = True
        target["enabled"] = False
        _drop_model_refs(data, prov, name)
    elif op == "restore":
        if not target.get("hidden"):
            return False, "该模型未被删除"
        target["hidden"] = False
        target["enabled"] = True
    return True, ""


def model_ops(provider_id, names, op):
    """批量模型操作（enable | disable | delete | restore）。返回 (改动数, 错误)。

    先整体校验再落盘：任一名不存在、或 enable/disable 选中了已删除的模型，
    整个请求都不生效，避免只改一半。全部改动合并为一次写盘。
    restore 与可见模型混选时，可见项直接跳过（不算失败），便于「恢复所选」。
    """
    names = list(dict.fromkeys(n for n in (names or []) if n))
    if op not in _MODEL_OPS:
        return 0, "未知操作 " + op
    if not names:
        return 0, "未选择模型"
    with _LOCK:
        data = _load()
        prov = _find_prov(data, provider_id)
        if not prov:
            return 0, "供应商不存在"
        models = prov.get("models") or []
        by_name = {m.get("name"): m for m in models}
        missing = [n for n in names if n not in by_name]
        if missing:
            return 0, "模型不存在：" + "、".join(missing[:3]) + ("…" if len(missing) > 3 else "")
        if op in ("enable", "disable"):
            gone = [n for n in names if by_name[n].get("hidden")]
            if gone:
                return 0, "%s已删除的模型：%s（请先恢复）" % (
                    "不能启用" if op == "enable" else "不能停用",
                    "、".join(gone[:3]) + ("…" if len(gone) > 3 else ""))
        changed = 0
        newly_enabled = []   # 本次转为启用的模型（enable/restore），重排时置顶
        for n in names:
            ok, _ = _apply_model_op(data, prov, models, n, op)
            if ok:
                changed += 1
                if op in ("enable", "restore"):
                    newly_enabled.append(n)
        if changed:
            _promote(models, newly_enabled)  # 启用的置顶，停用的退到启用块之后
            _save(data)
        return changed, ""


def _restore_all(provider_id):
    with _LOCK:
        data = _load()
        prov = _find_prov(data, provider_id)
        if not prov:
            return "供应商不存在"
        models = prov.get("models") or []
        hidden = [m for m in models if m.get("hidden")]
        if not hidden:
            return "没有已删除的模型"
        names = [m["name"] for m in hidden]
        for m in hidden:
            m["hidden"] = False
            m["enabled"] = True
        _promote(models, names)  # 恢复 = 重新启用：同样置顶
        _save(data)
        return None


def _move_model(provider_id, name, op):
    with _LOCK:
        data = _load()
        prov = _find_prov(data, provider_id)
        if not prov:
            return "供应商不存在"
        models = prov.get("models") or []
        target = next((m for m in models if m.get("name") == name), None)
        if not target:
            return "模型不存在"
        ordered = _ranked(models)
        if target not in ordered:
            return "已删除的模型不能调序"
        idx = ordered.index(target)
        j = idx - 1 if op == "up" else idx + 1
        if 0 <= j < len(ordered):
            ordered[idx], ordered[j] = ordered[j], ordered[idx]
        for i, m in enumerate(ordered):
            m["priority"] = i + 1
        _promote(models)  # 越过启用/停用边界的移动会被收回：启用的始终在前
        _save(data)
        return None


def model_op(provider_id, name, op):
    """单个模型操作。

    op: enable | disable | up | down | delete | restore | restore-all。
    删除是「标记隐藏」而非物理移除——刷新/重导入模型列表时不会被带回，
    恢复后重新启用并置顶。批量走 model_ops()。
    """
    if op == "restore-all":
        return _restore_all(provider_id)
    if op in ("up", "down"):
        return _move_model(provider_id, name, op)
    if op in _MODEL_OPS:
        return model_ops(provider_id, [name], op)[1] or None
    return "未知操作 " + op


def _save(data):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def _backup_file(path):
    """改写用户数据前留 .bak（data/ 不在 git 里，损坏没有回滚）。"""
    try:
        if path.is_file():
            shutil.copyfile(path, str(path) + ".bak")
    except Exception:
        pass


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
    """脱敏后的供应商列表（给 UI/API）。enabled 缺省视为启用；启用的排在停用前。"""
    out = []
    for p in providers():
        row = {k: (_mask(v) if k == "api_key" else v) for k, v in p.items()}
        row["enabled"] = bool(p.get("enabled", True))
        out.append(row)
    out.sort(key=lambda r: not r["enabled"])  # 兜底：导入等未走启停操作的也保持启用在前
    return out

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
        if proto not in _PROTOCOLS:
            return "protocol 只能是 %s" % " / ".join(_PROTOCOLS)
        target = None
        if pid:
            target = next((p for p in plist if p["id"] == pid), None)
        if target is None:
            target = {"id": _next_pid(plist)}
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


def providers_op(ids, op):
    """批量供应商操作（enable | disable | delete）。返回 (改动数, 错误)。

    停用只影响 Tutti 编排时的运行时解析（resolve_binding 返回空 → 回落 CLI 默认），
    不清除配置，也不影响绑定引用，随时可再启用。
    """
    ids = list(dict.fromkeys(i for i in (ids or []) if i))
    if op not in ("enable", "disable", "delete"):
        return 0, "未知操作 " + op
    if not ids:
        return 0, "未选择供应商"
    with _LOCK:
        data = _load()
        plist = data.get("providers", [])
        known = {p.get("id") for p in plist}
        missing = [i for i in ids if i not in known]
        if missing:
            return 0, "供应商不存在：%d 个" % len(missing)
        changed = 0
        if op == "delete":
            data["providers"] = [p for p in plist if p.get("id") not in ids]
            for b in (data.get("bindings") or {}).values():
                if b.get("provider_id") in ids:
                    b["provider_id"] = ""
                if b.get("chain"):
                    b["chain"] = [c for c in b["chain"] if c.get("provider_id") not in ids]
                    _sync_chain_refs(b)
            orch = data.get("orchestrator")
            if orch and orch.get("provider_id") in ids:
                orch["provider_id"] = ""
                orch["enabled"] = False
            changed = len(ids)
        else:
            want = (op == "enable")
            for p in plist:
                if p.get("id") in ids and bool(p.get("enabled", True)) != want:
                    p["enabled"] = want
                    changed += 1
            if changed:
                # 启用置顶：本次选中的启用者排最前，其余启用跟随，停用的殿后；
                # 排序稳定，各块内部保持原有顺序。落盘重排，列表页看到的就是它。
                sel = set(ids)
                plist.sort(key=lambda p: (
                    not (p.get("id") in sel and p.get("enabled", True)),
                    not bool(p.get("enabled", True))))
        _save(data)
        return changed, ("" if changed else "所选供应商已是目标状态")


def delete_provider(pid):
    """删除单个供应商（兼容旧调用）。"""
    providers_op([pid], "delete")


# 绑定模型链最多几个：1 个主模型 + 2 个降级备选（与 runner 降级链上限一致）
MAX_BIND_MODELS = 3


def _clean_models(models):
    """去空、去重、限长，保持勾选顺序（第 1 个即主模型）。"""
    out = []
    for m in models or []:
        m = str(m or "").strip()
        if m and m not in out:
            out.append(m)
    return out[:MAX_BIND_MODELS]


def _clean_chain(chain):
    """规范化跨厂商模型链：[{provider_id, model}]，去空去重限长，保持顺序。"""
    out, seen = [], set()
    for c in chain or []:
        if not isinstance(c, dict):
            continue
        pid = str(c.get("provider_id") or "").strip()
        m = str(c.get("model") or "").strip()
        if not m or (pid, m) in seen:
            continue
        seen.add((pid, m))
        out.append({"provider_id": pid, "model": m})
    return out[:MAX_BIND_MODELS]


def _sync_chain_refs(b):
    """chain 是唯一真源：重建 models / model 兼容冗余与主供应商字段。

    链空时保留既有 provider_id——主供应商是用户显式绑定的，不因模型链
    清空而丢失（此后按该供应商默认/难度模型解析）。
    """
    chain = b.get("chain") or []
    b["models"] = [c["model"] for c in chain]
    b["model"] = b["models"][0] if b["models"] else ""
    pid = next((c["provider_id"] for c in chain if c.get("provider_id")), None)
    if pid is not None:
        b["provider_id"] = pid


def _binding_chain(b):
    """取绑定的跨厂商链：优先 chain；旧数据从 provider_id + models 推导。"""
    chain = b.get("chain") or []
    if chain:
        return chain
    pid = b.get("provider_id") or ""
    names = _clean_models(b.get("models") or ([b["model"]] if b.get("model") else []))
    return [{"provider_id": pid, "model": n} for n in names]


def set_binding(agent_id, provider_id=None, model=None, models=None,
                difficulty_routing=None, chain=None):
    """写一条 CLI 绑定。chain=[{provider_id, model}] 是跨厂商模型链（唯一真源），
    models/model 是它的兼容冗余；任一形式写入都会同步重建其余字段。"""
    with _LOCK:
        data = _load()
        b = data.setdefault("bindings", {}).setdefault(agent_id, {})
        eff_pid = b.get("provider_id") or ""
        if provider_id is not None:
            eff_pid = provider_id if provider_id in [p["id"] for p in data.get("providers", [])] else ""
        if chain is not None:
            b["chain"] = _clean_chain(chain)
            _sync_chain_refs(b)
            if provider_id is not None:
                b["provider_id"] = eff_pid  # 显式指定主供应商时覆盖链首推导
        elif models is not None:
            # 有序模型链：第 1 个主模型，其余按序降级
            clean = _clean_models(models)
            b["chain"] = [{"provider_id": eff_pid, "model": m} for m in clean]
            _sync_chain_refs(b)
        elif model is not None:
            b["model"] = (model or "").strip()
            clean = [b["model"]] if b["model"] else []
            b["chain"] = [{"provider_id": eff_pid, "model": m} for m in clean]
            b["models"] = clean
        elif provider_id is not None:
            b["provider_id"] = eff_pid
        if difficulty_routing is not None:
            b["difficulty_routing"] = bool(difficulty_routing)
        _save(data)
        return b


# ---------------------------------------------------------------- 多源导入
#
# 每个来源一个采集器：只读本机已有 AI 工具 / CLI 的配置，产出统一的供应商条目。
# 采集器不改写任何工具的配置；密钥仅落到 data/models.json（与 CCSwitch 同等信任域）。
# 采集器返回 {"providers": [...], "pricing": {...}, "note": str, "error": str}。

_HOME = os.path.expanduser("~")
_APPDATA = os.environ.get("APPDATA") or os.path.join(_HOME, "AppData", "Roaming")

CCSWITCH_DB = os.path.join(_HOME, ".cc-switch", "cc-switch.db")
CLAUDE_SETTINGS = os.path.join(_HOME, ".claude", "settings.json")
CODEX_DIR = os.path.join(_HOME, ".codex")
DSH_DIR = os.environ.get("DSH_HOME") or os.path.join(_HOME, ".dsh")
DSH_SETTINGS = os.path.join(DSH_DIR, "settings.yaml")
ZCODE_CONFIG = os.path.join(_HOME, ".zcode", "v2", "config.json")
QWEN_SETTINGS = os.path.join(_HOME, ".qwen", "settings.json")
GEMINI_DIR = os.path.join(_HOME, ".gemini")
OPENCODE_CONFIG = os.path.join(_HOME, ".config", "opencode", "opencode.json")
OPENCODE_AUTH = os.path.join(_HOME, ".local", "share", "opencode", "auth.json")
CONTINUE_DIR = os.path.join(_HOME, ".continue")
CURSOR_DB = os.path.join(_APPDATA, "Cursor", "User", "globalStorage", "state.vscdb")
TRAE_DBS = [os.path.join(_APPDATA, "Trae CN", "User", "globalStorage", "state.vscdb"),
            os.path.join(_APPDATA, "TRAE SOLO CN", "User", "globalStorage", "state.vscdb")]


def _next_pid(plist):
    """分配下一个 prov-N id（取现有最大序号 +1，避免删除后再新增撞号）。"""
    n = 0
    for p in plist:
        m = re.match(r"^prov-(\d+)$", p.get("id") or "")
        if m:
            n = max(n, int(m.group(1)))
    return "prov-%d" % (n + 1)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _read_env_file(path):
    """解析 KEY=VALUE 形式的 .env（忽略注释与空行）。"""
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _sqlite_ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def _vsc_rows(db):
    """读取 VS Code 系（Cursor / Trae）state.vscdb 的 ItemTable：{key: str}。"""
    out = {}
    try:
        con = _sqlite_ro(db)
        try:
            cur = con.cursor()
            cur.execute("SELECT key, value FROM ItemTable")
            for k, v in cur.fetchall():
                out[k] = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        finally:
            con.close()
    except Exception:
        return {}
    return out


def _toml_parse(text):
    """极简 TOML：只取顶层标量与 [section] 标量（够解析 codex config.toml）。

    仅识别带引号的字符串、true/false 与裸值；不做类型推断，够用即止。
    """
    top, sections, cur = {}, {}, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            sec = m.group(1).strip()
            cur = sections.setdefault(sec, {})
            continue
        m = re.match(r"^([A-Za-z_][\w.-]*)\s*=\s*(.+?)\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        mq = re.match(r'^"([^"]*)"$', v)
        if mq:
            val = mq.group(1)
        elif v in ("true", "false"):
            val = (v == "true")
        else:
            val = v
        (cur if cur is not None else top)[k] = val
    return top, sections


def _toml_quoted(text, key):
    m = re.search(r'(?m)^\s*' + re.escape(key) + r'\s*=\s*"([^"]*)"', text or "")
    return m.group(1) if m else None


def _yaml_models(path):
    """解析 Continue 的 config.yaml（优先 PyYAML，缺失时用极简回退解析）。"""
    try:
        import yaml
        with open(path, "r", encoding="utf-8-sig") as f:
            d = yaml.safe_load(f)
        if isinstance(d, dict) and isinstance(d.get("models"), list):
            return d["models"]
    except Exception:
        pass
    # 回退：只解析顶层 models: 下的扁平映射列表（Continue 模型条目即此形状）
    try:
        lines = open(path, "r", encoding="utf-8-sig", errors="replace").read().splitlines()
    except Exception:
        return []
    items, cur, in_models = [], None, False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0:
            if s.startswith("models:"):
                in_models = True
                continue
            in_models = False
            continue
        if not in_models:
            continue
        if s.startswith("- "):
            if cur is not None:
                items.append(cur)
            cur, s = {}, s[2:].strip()
        if cur is not None and ":" in s:
            k, v = s.split(":", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                cur[k.strip()] = v
    if cur is not None:
        items.append(cur)
    return items


_ENDPOINT_SUFFIX = ("/chat/completions", "/completions", "/responses", "/messages",
                    "/v1beta/models", "/models")


def _strip_endpoint(url):
    """把误填成完整接口地址的 base_url 收敛回基址。

    部分工具（如 Trae 自定义模型）保存的是 https://host/v1/chat/completions，
    这里剥掉接口段，只保留 https://host/v1。
    """
    u = (url or "").strip().rstrip("/")
    for suf in _ENDPOINT_SUFFIX:
        if u.endswith(suf):
            u = u[: -len(suf)].rstrip("/")
            break
    return u


def _prov(name, protocol, base_url, api_key, source, source_id, model="",
          model_easy="", model_hard="", wire_api=None, models=None):
    """归一化一个导入条目；地址非法时返回 None。"""
    base_url = _strip_endpoint(base_url)
    if not base_url.startswith(("http://", "https://")):
        return None
    proto = protocol if protocol in _PROTOCOLS else "openai"
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
    if _is_private_host(base_url):
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
    prov["id"] = _next_pid(plist)
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
        model = _toml_quoted(text, "model") or ""
        secs = [(s, kv) for s, kv in _toml_parse(text)[1].items()
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
            p = _prov(name, "openai", _toml_quoted(text, "base_url"), key, "ccswitch", sid,
                      model=model, wire_api=_toml_quoted(text, "wire_api") or "responses")
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
    out = {"providers": [], "pricing": {}, "note": "", "found": os.path.isfile(CCSWITCH_DB)}
    if not out["found"]:
        return out
    try:
        con = _sqlite_ro(CCSWITCH_DB)
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
        con = _sqlite_ro(CCSWITCH_DB)
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
        out["note"] = "跳过 %d 条（缺地址或格式不识别）：%s" % (
            len(skipped), "、".join(sorted(set(skipped))[:4]))
    return out


def _src_claude():
    out = {"providers": [], "note": "", "found": os.path.isfile(CLAUDE_SETTINGS)}
    if not out["found"]:
        return out
    env = (_read_json(CLAUDE_SETTINGS) or {}).get("env") or {}
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
    return out


def _src_codex():
    cfg = os.path.join(CODEX_DIR, "config.toml")
    out = {"providers": [], "note": "", "found": os.path.isfile(cfg)}
    if not out["found"]:
        return out
    try:
        with open(cfg, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except Exception as e:
        out["error"] = "读取 config.toml 失败：%r" % (e,)
        return out
    key = (_read_json(os.path.join(CODEX_DIR, "auth.json")) or {}).get("OPENAI_API_KEY") or ""
    model = _toml_quoted(text, "model") or ""
    active = _toml_quoted(text, "model_provider") or ""
    top, sections = _toml_parse(text)
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
    if not out["providers"]:
        p = _prov("Codex CLI", "openai", top.get("base_url") or _toml_quoted(text, "base_url"),
                  key, "codex", "codex:default", model=model,
                  wire_api=_toml_quoted(text, "wire_api") or "responses")
        if p:
            out["providers"].append(p)
    if not out["providers"]:
        out["note"] = ("config.toml 未配置第三方 model_providers"
                       + ("" if key else "（且 auth.json 无 OPENAI_API_KEY）"))
    return out


def _src_zcode():
    out = {"providers": [], "note": "", "found": os.path.isfile(ZCODE_CONFIG)}
    if not out["found"]:
        return out
    d = _read_json(ZCODE_CONFIG) or {}
    default_model = str(d.get("model") or "")
    tail = default_model.split("/", 1)[1] if "/" in default_model else default_model
    bad = []
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
            out["providers"].append(pr)
        else:
            bad.append(str(p.get("name") or pid))
    if bad:
        out["note"] = "跳过 %d 个（缺合法 baseURL）：%s" % (len(bad), "、".join(bad[:4]))
    return out


def _src_qwen():
    out = {"providers": [], "note": "", "found": os.path.isfile(QWEN_SETTINGS)}
    if not out["found"]:
        return out
    d = _read_json(QWEN_SETTINGS) or {}
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
    if not out["providers"]:
        out["note"] = "settings.json 的 modelProviders 为空"
    return out


def _src_gemini():
    envp = os.path.join(GEMINI_DIR, ".env")
    setp = os.path.join(GEMINI_DIR, "settings.json")
    out = {"providers": [], "note": "",
           "found": os.path.isfile(envp) or os.path.isfile(setp)}
    if not out["found"]:
        return out
    env = _read_env_file(envp)
    key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or ""
    base = env.get("GOOGLE_GEMINI_BASE_URL") or env.get("GEMINI_BASE_URL") or ""
    if not base and key:
        base = "https://generativelanguage.googleapis.com"
    p = _prov("Gemini CLI", "google", base, key, "gemini", "gemini:env",
              model=env.get("GEMINI_MODEL") or "")
    if p:
        out["providers"].append(p)
    else:
        out["note"] = "未在 .env 找到 GEMINI_API_KEY / GOOGLE_GEMINI_BASE_URL"
    return out


def _src_opencode():
    out = {"providers": [], "note": "",
           "found": os.path.isfile(OPENCODE_CONFIG) or os.path.isfile(OPENCODE_AUTH)}
    if not out["found"]:
        return out
    d = _read_json(OPENCODE_CONFIG) or {}
    auth = _read_json(OPENCODE_AUTH) or {}
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
    if not out["providers"]:
        out["note"] = "opencode.json 的 provider 为空"
    return out


def _src_continue():
    yp = os.path.join(CONTINUE_DIR, "config.yaml")
    jp = os.path.join(CONTINUE_DIR, "config.json")
    out = {"providers": [], "note": "",
           "found": os.path.isfile(yp) or os.path.isfile(jp)}
    if not out["found"]:
        return out
    models = []
    d = _read_json(jp)
    if isinstance(d, dict) and isinstance(d.get("models"), list):
        models = d["models"]
    elif os.path.isfile(yp):
        models = _yaml_models(yp)
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
    out = {"providers": [], "note": "", "found": os.path.isfile(CURSOR_DB)}
    if not out["found"]:
        return out
    rows = _vsc_rows(CURSOR_DB)
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
    for db in TRAE_DBS:
        if not os.path.isfile(db):
            continue
        out["found"] = True
        for k, s in _vsc_rows(db).items():
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
                          "trae:%s:%s" % (name, _strip_endpoint(base)))
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
    out = {"providers": [], "note": "", "found": os.path.isfile(DSH_SETTINGS)}
    if not out["found"]:
        return out
    d = {}
    try:
        import yaml
        with open(DSH_SETTINGS, "r", encoding="utf-8-sig") as f:
            d = yaml.safe_load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    llm = d.get("llm-deepseek") if isinstance(d.get("llm-deepseek"), dict) else {}
    adm = d.get("agent-default-model") if isinstance(d.get("agent-default-model"), dict) else {}
    base = str(llm.get("baseURL") or "")
    names = [str(m["id"]) for m in (llm.get("models") or [])
             if isinstance(m, dict) and m.get("id")]
    if not base:
        out["note"] = "settings.yaml 未配置 llm-deepseek.baseURL"
        return out
    key = (_dotenv_get(os.path.join(DSH_DIR, ".env"), "DEEPSEEK_API_KEY")
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
     _src_ccswitch, lambda: [CCSWITCH_DB]),
    ("claude", "Claude Code", "~/.claude/settings.json 的 env 供应商",
     _src_claude, lambda: [CLAUDE_SETTINGS]),
    ("codex", "Codex CLI", "~/.codex/config.toml + auth.json（含多 model_providers）",
     _src_codex, lambda: [os.path.join(CODEX_DIR, "config.toml")]),
    ("zcode", "ZCode", "~/.zcode/v2/config.json 的 provider 表",
     _src_zcode, lambda: [ZCODE_CONFIG]),
    ("qwen", "Qwen Code", "~/.qwen/settings.json 的 modelProviders",
     _src_qwen, lambda: [QWEN_SETTINGS]),
    ("gemini", "Gemini CLI", "~/.gemini 的 .env / settings.json",
     _src_gemini, lambda: [os.path.join(GEMINI_DIR, ".env"),
                           os.path.join(GEMINI_DIR, "settings.json")]),
    ("opencode", "OpenCode", "~/.config/opencode/opencode.json 的 provider 表",
     _src_opencode, lambda: [OPENCODE_CONFIG, OPENCODE_AUTH]),
    ("continue", "Continue", "~/.continue/config.yaml 的 models",
     _src_continue, lambda: [os.path.join(CONTINUE_DIR, "config.yaml"),
                             os.path.join(CONTINUE_DIR, "config.json")]),
    ("cursor", "Cursor", "Cursor 的 cursorAuth/openAIKey + 自定义 Base URL",
     _src_cursor, lambda: [CURSOR_DB]),
    ("trae", "Trae", "Trae / Trae SOLO 中自定义模型的 Base URL + AK",
     _src_trae, lambda: list(TRAE_DBS)),
    ("dsh", "DeepSeek Harness", "~/.dsh/settings.yaml 的 llm-deepseek（密钥走 ~/.dsh/.env）",
     _src_dsh, lambda: [DSH_SETTINGS]),
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
    res.setdefault("error", "")
    res.setdefault("found", False)
    return res


def sources():
    """本机所有支持的导入来源及探测结果（配置文件是否存在、可导入几条）。"""
    rows = []
    for sid, name, desc, fn, paths in _SOURCES:
        ps = [p for p in paths() if p]
        found = any(os.path.isfile(p) for p in ps)
        row = {"id": sid, "name": name, "desc": desc, "paths": ps,
               "found": found, "count": 0, "note": "", "error": ""}
        if found:
            res = _collect(fn)
            row["count"] = len(res["providers"])
            row["note"] = res["note"]
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
    with _LOCK:
        data = _load()
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
                "count": added + updated, "note": res["note"], "error": res["error"]})
        if out["added"] or out["updated"] or out["pricing"]:
            _save(data)
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


# ---------------------------------------------------------------- 运行时解析

def _enabled_models(prov):
    ms = [m for m in (prov.get("models") or [])
          if m.get("enabled", True) and not m.get("hidden")]
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
    if r.get("call_chain"):
        a["call_chain"] = r["call_chain"]
    return a


# 用「自家 env 约定」而非 codex -c 覆盖来接收供应商的 CLI。
# dsh（DeepSeek Harness）的 llm-deepseek 适配器只认 DEEPSEEK_API_KEY /
# DEEPSEEK_BASE_URL，且端点必须是 OpenAI 兼容的 /chat/completions。
_DEEPSEEK_ENV_TARGETS = ("deepseek-harness", "dsh")


def _deepseek_env_target(target):
    return (target or "").strip().lower() in _DEEPSEEK_ENV_TARGETS


def _chain_entry_env(prov, model, target=""):
    """一条链的运行时注入：env（claude=ANTHROPIC_*，codex=一次性 provider 覆盖，
    dsh=DEEPSEEK_*）。"""
    out = {"model": model, "env": {}, "provider": prov}
    if _deepseek_env_target(target):
        out["env"] = {"DEEPSEEK_API_KEY": prov["api_key"],
                      "DEEPSEEK_BASE_URL": prov["base_url"]}
        return out
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


def resolve_binding(agent_kind_or_id, difficulty="default"):
    """返回 {env:{}, model:..., model_fallbacks:[...], codex_provider:..., call_chain:[...]} 或 None。

    call_chain 是跨厂商降级链（唯一真源）：每条 {model, env, provider, [codex_provider]}，
    按序尝试；供应商失效（停用/删除/无密钥/协议不可注入）的条目被跳过，全链失效
    → 整体回落 CLI 默认（None）。纯链条目（无供应商）不注入 env，只传 -m。
    难度路由只在无显式链时生效。
    """
    b = bindings().get(agent_kind_or_id) or bindings().get(
        "codex-cli" if agent_kind_or_id == "codex" else "claude-code") or {}
    chain = _binding_chain(b)
    provs = {p.get("id"): p for p in providers()}
    routing = bool(b.get("difficulty_routing"))
    tier = difficulty if difficulty in ("easy", "hard") else None
    # dsh 走 DEEPSEEK_* env，端点必须是 OpenAI 兼容的 /chat/completions，
    # anthropic 协议的网关注进去也调不通，直接判为不可绑定。
    allowed = ("openai",) if _deepseek_env_target(agent_kind_or_id) else _BINDABLE_PROTOCOLS

    if chain:
        entries = []
        for item in chain:
            model = (item.get("model") or "").strip()
            pid = (item.get("provider_id") or "").strip()
            if not pid:
                if model:
                    entries.append({"model": model, "env": {}, "provider": None})
                continue
            prov = provs.get(pid)
            if not prov or not prov.get("enabled", True) or not prov.get("api_key"):
                continue  # 该条失效：跳过（降级链的语义就是逐条顶上）
            if prov.get("protocol") not in allowed:
                continue
            entries.append(_chain_entry_env(prov, model or prov.get("model") or "",
                                            target=agent_kind_or_id))
        if not entries:
            return None
        head = entries[0]
        out = {"model": head["model"], "env": head["env"], "provider": head.get("provider"),
               "model_fallbacks": [e["model"] for e in entries[1:]],
               "call_chain": [dict(e) for e in entries]}
        if head.get("codex_provider"):
            out["codex_provider"] = head["codex_provider"]
        return out

    # 无显式链：难度映射 / 供应商默认 / 启用模型优先级（原语义）
    pid = b.get("provider_id")
    if not pid:
        return None
    prov = provs.get(pid)
    if not prov or not prov.get("enabled", True) or not prov.get("api_key"):
        return None
    if prov.get("protocol") not in allowed:
        return None  # google 只登记；dsh 只接受 OpenAI 兼容端点
    names = [m["name"] for m in _enabled_models(prov)]
    model = prov.get("model_" + tier) or "" if (routing and tier) else ""
    if not model:
        model = prov.get("model") or ""
    if not model and names:
        model = names[0] if (not routing or difficulty != "easy") else names[-1]
    # model 可为空：仅注入供应商凭据，不指定模型（用网关默认）
    fallbacks = [n for n in names if n != model][:MAX_BIND_MODELS - 1]
    head = _chain_entry_env(prov, model, target=agent_kind_or_id)
    out = {"model": model, "env": head["env"], "provider": prov,
           "model_fallbacks": fallbacks,
           "call_chain": [dict(head, model=n) for n in [model] + fallbacks]}
    if head.get("codex_provider"):
        out["codex_provider"] = head["codex_provider"]
    return out


def migrate_orch_models():
    """一次性迁移：把 orchestration.json 里的编排模型链搬进 bindings（幂等）。

    旧结构里「参与编排」与「编排模型」都存 orchestration.json；合并后模型链
    归 models.json 的 bindings 管（provider 可空 = 纯链，只传 -m），orchestration.json
    只留 enabled。bindings 里已有链的跳过（不覆盖用户后续改动）；改动前两个
    文件各留 .bak。返回本次迁移的条数。
    """
    try:
        state = json.loads(paths.ENABLED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(state, dict):
        return 0
    chains = {}
    for aid, pref in state.items():
        if not isinstance(pref, dict):
            continue
        chain = _clean_models(
            pref.get("models") or ([pref["model"]] if pref.get("model") else []))
        if chain:
            chains[aid] = chain
    if not chains:
        return 0
    with _LOCK:
        data = _load()
        changed = False
        for aid, chain in chains.items():
            b = data.setdefault("bindings", {}).setdefault(aid, {})
            if b.get("models"):
                continue
            b["models"] = chain
            b["model"] = chain[0]
            changed = True
        if not changed:
            return 0
        _backup_file(_FILE)
        _save(data)
    _backup_file(paths.ENABLED_FILE)
    for aid in chains:
        pref = state.get(aid) or {}
        pref.pop("models", None)
        pref.pop("model", None)
    paths.ENABLED_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(chains)


def models_view():
    """扁平化模型目录（画廊展示用）：跨供应商的模型卡片列表 + 价格。"""
    pricing = _load().get("pricing") or {}
    rows = []
    for p in sorted(providers(), key=lambda x: not x.get("enabled", True)):
        base = {"provider_id": p.get("id"), "provider_name": p.get("name"),
                "protocol": p.get("protocol"),
                "fetched_at": p.get("models_fetched_at") or "",
                "fetch_status": "ok" if p.get("models") is not None else "未获取"}
        ms = p.get("models")
        if ms is None:
            rows.append(dict(base, name="", enabled=False, priority=0))
            continue
        for m in sorted(ms, key=lambda x: x.get("priority", 999)):
            if m.get("hidden"):
                continue
            pr = pricing.get(m["name"]) or {}
            rows.append(dict(base, name=m["name"], enabled=bool(m.get("enabled", True)),
                             priority=m.get("priority", 0),
                             price_in=pr.get("in"), price_out=pr.get("out")))
    return rows


def reorder_models(provider_id, ordered_names):
    """按给定名称顺序重设优先级（列表中未出现的模型排在最后，保持相对顺序）。"""
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return "供应商不存在"
        models = prov.get("models") or []
        by_name = {m.get("name"): m for m in models}
        if not by_name:
            return "该供应商还没有模型列表，请先获取"
        prio, i = {}, 1
        for n in ordered_names or []:
            if n in by_name and n not in prio:
                prio[n] = i
                i += 1
        for m in models:
            if m.get("name") not in prio:
                prio[m["name"]] = i
                i += 1
        for m in models:
            m["priority"] = prio[m["name"]]
        _promote(models)  # 拖拽不能把停用模型排到启用模型前面
        _save(data)
        return None


def _post_json_http(url, headers, body, allow_private, timeout=20):
    """带 SSRF 防护的 POST。返回 (status, json_obj|None, err)。"""
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        return 0, None, "协议必须是 http/https"
    host_info = _validate_host(url, allow_private)
    if host_info is None:
        return 0, None, host_info[1]
    try:
        req = urllib.request.Request(url, method="POST",
                                     headers=dict(headers, **{"Content-Type": "application/json"}),
                                     data=json.dumps(body).encode("utf-8"))
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            raw = resp.read(1024 * 1024)
        return resp.status, json.loads(raw.decode("utf-8", "replace")), ""
    except Exception as e:
        return 0, None, repr(e)[:300]


def test_provider(provider_id):
    """供应商连通性测试：GET /models 并测延迟。返回 {ok, latency_ms, count, error}。"""
    import time as _t
    with _LOCK:
        prov = next((p for p in providers() if p.get("id") == provider_id), None)
    if not prov:
        return {"ok": False, "error": "供应商不存在"}
    t0 = _t.time()
    names, err = _fetch_models_http(prov.get("base_url"), prov.get("api_key") or "",
                                    prov.get("protocol"), bool(prov.get("allow_private")))
    latency = int((_t.time() - t0) * 1000)
    if names is None:
        return {"ok": False, "latency_ms": latency, "error": err}
    return {"ok": True, "latency_ms": latency, "count": len(names), "error": ""}


def test_model(provider_id, model_name):
    """单模型连通性测试：发一条 1 token 的最小对话。返回 {ok, latency_ms, error}。"""
    import time as _t
    import urllib.parse
    with _LOCK:
        prov = next((p for p in providers() if p.get("id") == provider_id), None)
    if not prov or not prov.get("api_key"):
        return {"ok": False, "error": "供应商不存在或未配置密钥"}
    proto = prov.get("protocol")
    base = prov["base_url"].rstrip("/")
    if proto == "google":
        url = base + "/v1beta/models/%s:generateContent" % model_name
        if base.endswith("/v1beta"):
            url = base + "/models/%s:generateContent" % model_name
        headers = {"x-goog-api-key": prov["api_key"]}
        body = {"contents": [{"parts": [{"text": "ping"}]}]}
    else:
        path = "/messages" if proto == "anthropic" else "/chat/completions"
        url = (base + path) if base.endswith("/v1") else (base + "/v1" + path)
        if proto == "anthropic":
            headers = {"x-api-key": prov["api_key"], "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": "Bearer " + prov["api_key"]}
        body = {"model": model_name, "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}]}
    t0 = _t.time()
    status, data, err = _post_json_http(url, headers, body, bool(prov.get("allow_private")))
    latency = int((_t.time() - t0) * 1000)
    if status == 0:
        return {"ok": False, "latency_ms": latency, "error": err}
    if 200 <= status < 300:
        return {"ok": True, "latency_ms": latency, "error": ""}
    msg = ""
    if isinstance(data, dict):
        e = data.get("error")
        msg = e.get("message", "") if isinstance(e, dict) else str(e)
    return {"ok": False, "latency_ms": latency,
            "error": "HTTP %s %s" % (status, str(msg)[:160])}


def classify_difficulty(goal, verify_command):
    """难度启发式（规划器 LLM 判定优先，这里只做退化）。"""
    text = goal or ""
    hard_words = ("重构", "架构", "迁移", "安全", "性能", "并发", "分布式", "设计")
    if verify_command and (len(text) > 120 or any(w in text for w in hard_words)):
        return "hard"
    if len(text) <= 60 and not any(w in text for w in hard_words):
        return "easy"
    return "hard"


# ---------------------------------------------------------------- 跨厂商链迁移

def migrate_chains():
    """把旧 bindings {provider_id, models} 升级为跨厂商 chain（幂等）。

    chain 是唯一真源，models/model 是它的兼容冗余。改动前留 .bak。
    返回是否发生了迁移。
    """
    with _LOCK:
        data = _load()
        changed = False
        for b in (data.get("bindings") or {}).values():
            if b.get("chain"):
                continue
            names = _clean_models(b.get("models") or ([b["model"]] if b.get("model") else []))
            if not names:
                continue
            pid = b.get("provider_id") or ""
            b["chain"] = [{"provider_id": pid, "model": n} for n in names]
            changed = True
        if not changed:
            return False
        _backup_file(_FILE)
        _save(data)
        return True


# ---------------------------------------------------------------- 编排中枢（编排者模型）

def orchestrator_view():
    """编排者配置（脱敏展示 + 可用性判定）。"""
    with _LOCK:
        cfg = dict(_load().get("orchestrator") or {})
    prov = next((p for p in providers() if p.get("id") == cfg.get("provider_id")), None)
    cfg["provider_name"] = (prov or {}).get("name", "")
    cfg["protocol"] = (prov or {}).get("protocol", "")
    cfg["enabled"] = bool(cfg.get("enabled", False))
    cfg["ready"] = bool(
        cfg.get("enabled") and prov and prov.get("api_key")
        and prov.get("enabled", True) and (cfg.get("model") or prov.get("model")))
    return cfg


def set_orchestrator(provider_id, model=None, enabled=None):
    """保存编排者配置。provider_id 空串 = 不使用编排者。返回错误或 None。"""
    with _LOCK:
        data = _load()
        pid = (provider_id or "").strip()
        if pid and not _find_prov(data, pid):
            return "供应商不存在"
        cfg = data.get("orchestrator") or {}
        cfg["provider_id"] = pid
        if model is not None:
            cfg["model"] = (model or "").strip()
        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if not pid:
            cfg["enabled"] = False
        data["orchestrator"] = cfg
        _save(data)
        return None


def resolve_orchestrator():
    """返回编排者实际可用的 (provider, model_name)，未启用/失效返回 None。"""
    with _LOCK:
        cfg = _load().get("orchestrator") or {}
    if not cfg.get("enabled", False):
        return None
    pid = cfg.get("provider_id") or ""
    prov = next((p for p in providers() if p.get("id") == pid), None)
    if not prov or not prov.get("enabled", True) or not prov.get("api_key"):
        return None
    model = (cfg.get("model") or "").strip() or prov.get("model") or ""
    if not model:
        names = _enabled_models(prov)
        model = names[0]["name"] if names else ""
    if not model:
        return None
    return prov, model


def _chat_cache_path(provider_id, model_name, prompt, max_tokens):
    """§07 T2.2：精确匹配响应缓存的落盘路径（只缓存 ok 的幂等调用）。"""
    import hashlib as _h
    key = "|".join([str(provider_id), str(model_name), str(max_tokens), str(prompt)])
    name = _h.sha256(key.encode("utf-8")).hexdigest()[:24]
    return paths.DATA_DIR / "chat_cache" / (name + ".json")


def chat(provider_id, model_name, prompt, max_tokens=2048, timeout=120, cache_ttl=0):
    """直连供应商 API 做一次对话（编排者规划 / 连通性测试）。

    支持 anthropic / openai / google 三种协议；复用 SSRF 防护。
    返回 {ok, text, tokens, usage, error}；usage 为细分 {input, output, cached, reasoning, total}。
    cache_ttl>0 启用精确匹配响应缓存（key=供应商+模型+prompt+max_tokens，只缓存
    ok 结果）——仅限幂等调用（连通性测试等）；创作类调用不要开，否则同一 prompt
    的二次请求会屏蔽模型的新输出。
    """
    if cache_ttl > 0:
        try:
            cache_path = _chat_cache_path(provider_id, model_name, prompt, max_tokens)
            if cache_path.is_file():
                age = time.time() - cache_path.stat().st_mtime
                if age <= cache_ttl:
                    import json as _json
                    data = _json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("ok"):
                        return data
        except Exception:
            pass
    with _LOCK:
        prov = next((p for p in providers() if p.get("id") == provider_id), None)
    if not prov or not prov.get("api_key"):
        return {"ok": False, "text": "", "tokens": 0, "usage": None,
                "error": "供应商不存在或未配置密钥"}
    proto = prov.get("protocol")
    base = prov["base_url"].rstrip("/")
    if proto == "google":
        if base.endswith("/v1beta"):
            url = base + "/models/%s:generateContent" % model_name
        else:
            url = base + "/v1beta/models/%s:generateContent" % model_name
        headers = {"x-goog-api-key": prov["api_key"]}
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens}}
    else:
        path = "/messages" if proto == "anthropic" else "/chat/completions"
        url = (base + path) if base.endswith("/v1") else (base + "/v1" + path)
        if proto == "anthropic":
            headers = {"x-api-key": prov["api_key"], "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": "Bearer " + prov["api_key"]}
        body = {"model": model_name, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}

    status, data, err = _post_json_http(url, headers, body, bool(prov.get("allow_private")),
                                        timeout=timeout)
    if status == 0:
        return {"ok": False, "text": "", "tokens": 0, "usage": None, "error": err}
    if not 200 <= status < 300:
        msg = ""
        if isinstance(data, dict):
            e = data.get("error")
            msg = e.get("message", "") if isinstance(e, dict) else str(e)
        return {"ok": False, "text": "", "tokens": 0, "usage": None,
                "error": "HTTP %s %s" % (status, str(msg)[:200])}
    text = ""
    usage = {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "total": 0}
    try:
        if proto == "anthropic":
            text = "\n".join(b.get("text", "") for b in (data.get("content") or [])
                             if isinstance(b, dict) and b.get("type") == "text")
            u = data.get("usage") or {}
            usage["input"] = int(u.get("input_tokens") or 0)
            usage["output"] = int(u.get("output_tokens") or 0)
            usage["cached"] = (int(u.get("cache_read_input_tokens") or 0)
                               + int(u.get("cache_creation_input_tokens") or 0))
        elif proto == "google":
            cand = ((data.get("candidates") or [{}])[0].get("content") or {})
            text = "\n".join(p.get("text", "") for p in (cand.get("parts") or [])
                             if isinstance(p, dict))
            um = data.get("usageMetadata") or {}
            usage["input"] = int(um.get("promptTokenCount") or 0)
            usage["output"] = int(um.get("candidatesTokenCount") or 0)
            usage["total"] = int(um.get("totalTokenCount") or 0)
        else:
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = msg.get("content") or ""
            u = data.get("usage") or {}
            usage["input"] = int(u.get("prompt_tokens") or 0)
            usage["output"] = int(u.get("completion_tokens") or 0)
            usage["total"] = int(u.get("total_tokens") or 0)
    except Exception as e:
        return {"ok": False, "text": "", "tokens": 0, "usage": None,
                "error": "响应解析失败: %r" % e}
    if not usage["total"]:
        usage["total"] = usage["input"] + usage["output"] + usage["cached"]
    result = {"ok": True, "text": (text or "").strip(), "tokens": usage["total"],
              "usage": usage, "error": ""}
    # §07 T2.2：cache_ttl>0 时落盘缓存（仅 ok 结果，原子写）
    if cache_ttl > 0:
        try:
            cache_path = _chat_cache_path(provider_id, model_name, prompt, max_tokens)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".tmp")
            import json as _json
            tmp.write_text(_json.dumps(result, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cache_path)
        except Exception:
            pass
    return result
