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
  "orchestrator": {"provider_id", "model", "enabled"}   # 编排设置：直连 API 的规划/管理模型
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

import copy
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
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
# 聚合中转网关（new-api/one-api 系）一个密钥常同时开多条 wire，导入时不必先问
# 用户选哪条：protocol="auto" 表示「不指定，按实测能力集挑」。旧数据里的
# anthropic/openai/google 一律视为显式指定（用户可覆盖），行为完全不变。
_PROTOCOL_AUTO = "auto"
_PROTOCOL_CHOICES = _PROTOCOLS + (_PROTOCOL_AUTO,)
# auto 挑主协议时的偏好顺序：只挑「实测过的」wire（wire_caps），不猜。
_WIRE_PREFERENCE = ("anthropic", "openai")


def _load():
    try:
        data = _normalize_ids(json.loads(_FILE.read_text(encoding="utf-8")))
    except Exception:
        return {"providers": [], "bindings": {}}
    # 多 KEY 供应商的 api_key 是「首个可用 KEY」的镜像：每次读取时重算，冷却
    # 到期自动把首选 KEY 换回来（或已切到备用）。没有 keys 数组的老数据不动。
    for p in data.get("providers") or []:
        if isinstance(p.get("keys"), list):
            _sync_api_key(p)
    return data


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

# 图片输入命名惯例：vision/omni 词段、glm-4v 式「数字+v」段；vl 子串另判
# （qwen-vl / internvl2 / cogvlm 等视觉家族的通用命名根，文本模型名含 vl 的极罕见）
_IMAGE_IN_RE = re.compile(
    r"(?:^|[-_.])(?:vision|visual|omni)(?:[-_.0-9]|$)|[-_.]\dv(?:[-_.]|$)")


def _auto_image_in(name):
    """按模型名启发式预填「支持图片输入」。claude/gemini 全系原生多模态直接标；
    其余只认显式命名（vision 词段、vl 子串、数字+v），宁缺勿滥——漏标的明模型
    用户手动打开即可，误标只是运行时网关显式报错、开关改回即恢复。
    仅用于新模型的初始声明。"""
    n = (name or "").lower()
    if n.startswith(("claude", "gemini")):
        return True
    return bool(_IMAGE_IN_RE.search(n)) or "vl" in n


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
        return [{"x-goog-api-key": key, "User-Agent": "codebee-orchestrator/1.0"}]
    h1 = {"Authorization": "Bearer " + key, "User-Agent": "codebee-orchestrator/1.0"}
    if protocol == "anthropic":
        h2 = {"x-api-key": key, "anthropic-version": "2023-06-01",
              "User-Agent": "codebee-orchestrator/1.0"}
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
    protocol="auto"（导入时未指定格式）两条 URL 形状都试——anthropic 与 openai
    的取列表路径本就相同，多试 google 那条只是让 auto 名副其实。
    """
    import urllib.parse
    base = (base_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None, "base_url 必须是 http/https"
    std = [base + "/models"] if base.endswith("/v1") else [base + "/v1/models", base + "/models"]
    goog = [base + "/models"] if base.endswith("/v1beta") else [base + "/v1beta/models"]
    if protocol == "google":
        urls = goog
    elif protocol == _PROTOCOL_AUTO:
        urls = std + [u for u in goog if u not in std]
    else:
        urls = std
    last_err = ""
    opener = urllib.request.build_opener(_NoRedirect)
    for url in urls:
        host_info = _validate_host(url, allow_private)
        if host_info is None:
            last_err = host_info[1]
            continue
        # auto 不知道是哪条 wire，鉴权头也按两种都试（google 那种单独补上）
        hdrs = _auth_header_variants(api_key, protocol)
        if protocol == _PROTOCOL_AUTO:
            hdrs = _auth_header_variants(api_key, "anthropic") + \
                [{"x-goog-api-key": api_key, "User-Agent": "codebee-orchestrator/1.0"}]
        for headers in hdrs:
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
                        "hidden": bool(o.get("hidden")),
                        # 模态声明同理：用户/预填设过的 image_in 刷新时不能被抹掉
                        "image_in": bool(o.get("image_in"))}
                (hidden if item["hidden"] else existing).append(item)
            else:
                fresh.append({"name": n, "enabled": True,
                              "auto": _auto_priority(n),
                              "image_in": _auto_image_in(n)})
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
    # 取列表成功后自动做一次 wire 适配探测，但丢到后台线程：探测最坏要等多个
    # 候选端点各自超时（网络不通/慢时几十秒），同步跑会把 /api/models/refresh
    # 拖成长请求，前端 await 不到响应——用户看就是「点了获取模型列表没反应」。
    # 探完落 wire_caps，页面下一轮轮询自然刷出「已适配」徽标。
    def _bg_probe(pid=provider_id):
        try:
            probe_wire_caps(pid)
        except Exception:
            pass
    threading.Thread(target=_bg_probe, name="wire-probe", daemon=True).start()
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
            sync_binding_alerts()  # 模型启停/恢复 → 绑定链死活立即刷新告警
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
        sync_binding_alerts()
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
    """脱敏后的供应商列表（给 UI/API）。enabled 缺省视为启用；启用的排在停用前。

    keys 里的密钥逐条脱敏；冷却状态实时算好（cooling）给界面显示。
    """
    out = []
    now = time.time()
    for p in providers():
        row = {k: (_mask(v) if k == "api_key" else v) for k, v in p.items()}
        row["enabled"] = bool(p.get("enabled", True))
        ks = []
        for k in _provider_keys(p):
            ks.append({"id": k["id"], "key": _mask(k["key"]),
                       "label": k.get("label") or "", "enabled": k["enabled"],
                       "cooling": k["cooling"],
                       "cool_until": float(k.get("cool_until") or 0),
                       "last_error": (k.get("last_error") or "")[:200],
                       "last_fail_at": float(k.get("last_fail_at") or 0)})
        row["keys"] = ks
        row["keys_enabled"] = sum(1 for k in ks if k["enabled"])
        out.append(row)
    out.sort(key=lambda r: not r["enabled"])  # 兜底：导入等未走启停操作的也保持启用在前
    return out


# ---------------------------------------------------------------- 多 KEY（同厂商多密钥）
# 一个厂商可配多把 KEY（不同账号，或欠费后的备用号）：
#   provider["keys"] = [{"id","key","label","enabled","cool_until","last_error"}, ...]
# 数组顺序即调用顺序（界面可拖拽）；api_key 是「首个可用 KEY」的镜像，所有既有
# 读取点（凭据注入/健康探测/取模型列表）无需感知多 KEY 结构。没配 keys 的老数据
# 由 _provider_keys 按 api_key 现场合成一条——文件一个字节都不用改。
_KEY_COOLDOWN_S = 30 * 60      # 欠费类失败后的冷却时长
MAX_PROVIDER_KEYS = 8          # 单厂商 KEY 上限
MAX_CHAIN_ATTEMPTS = 8         # 链展开后的尝试上限（模型 × KEY）
# 欠费/配额类失败：换 KEY 有意义（同厂商另一账号还能用），与瞬态网络错误分开记
_QUOTA_HINTS = ("insufficient", "quota", "balance", "credit", "billing", "arrears",
                "payment required", "402", "欠费", "余额", "额度", "exceeded")


def _quota_error(err):
    """疑似欠费/配额耗尽。误判的代价只是临时切到备用 KEY（冷却到期或手动恢复
    即回到首选），比「账单断了还死磕同一把 KEY」小得多。"""
    e = (err or "").lower()
    return any(k in e for k in _QUOTA_HINTS)


def _provider_keys(prov, available_only=False, now=None):
    """供应商的 KEY 列表，顺序=调用顺序。返回 [{id,key,label,enabled,cooling,...}]。

    available_only 只留「启用且不在冷却期」的；没配 keys 时按 api_key 合成一条。
    """
    ks = (prov or {}).get("keys")
    if not isinstance(ks, list) or not ks:
        legacy = ((prov or {}).get("api_key") or "").strip()
        ks = [{"id": "k1", "key": legacy, "label": "", "enabled": True}] if legacy else []
    now = time.time() if now is None else now
    out = []
    for i, k in enumerate(ks):
        if not isinstance(k, dict):
            continue
        kk = (k.get("key") or "").strip()
        if not kk:
            continue
        en = bool(k.get("enabled", True))
        cooling = float(k.get("cool_until") or 0) > now
        if available_only and (not en or cooling):
            continue
        out.append(dict(k, key=kk, enabled=en, cooling=cooling,
                        id=str(k.get("id") or ("k%d" % (i + 1)))))
    return out


def _chain_keys(prov):
    """链条目展开用的 KEY 序列：优先「启用且未冷却」；全都冷却时退回首个启用的
    （冷却没到期也得有人顶，否则整个供应商被跳过——代价比多试一次大）。"""
    avail = _provider_keys(prov, available_only=True)
    if avail:
        return avail[:MAX_PROVIDER_KEYS]
    return [k for k in _provider_keys(prov) if k["enabled"]][:1]


def _sync_api_key(prov):
    """把 api_key 镜像刷成「首个可用 KEY」（全冷却时取首个启用的）。

    这是多 KEY 与既有单 KEY 代码之间的唯一桥：注入/健康/探测读 api_key 的地方
    自动跟着切换，不必逐处改成读 keys。没有启用的 KEY 时置空——既有
    「无密钥即跳过」的判定自然生效。
    """
    if not isinstance(prov.get("keys"), list):
        return                      # 老结构：api_key 就是真源，别动它
    avail = _provider_keys(prov, available_only=True)
    pick = avail[0] if avail else next((k for k in _provider_keys(prov) if k["enabled"]), None)
    prov["api_key"] = pick["key"] if pick else ""


def _materialize_keys(prov):
    """把「按 api_key 合成」的隐式单 KEY 落成显式 keys 数组（首次改 KEY 时调用）。
    返回该数组（就地写入 prov）。"""
    if not isinstance(prov.get("keys"), list):
        prov["keys"] = [{"id": "k1", "key": k["key"], "label": k.get("label") or "",
                         "enabled": True} for k in _provider_keys(prov)]
    return prov["keys"]


def _next_key_id(keys):
    used = {str(k.get("id") or "") for k in keys if isinstance(k, dict)}
    for i in range(1, MAX_PROVIDER_KEYS + 2):
        if ("k%d" % i) not in used:
            return "k%d" % i
    return "k%d" % (len(used) + 1)


def key_op(provider_id, op, key_id="", key="", label="", enabled=None, ids=None):
    """KEY 级操作（add | update | delete | reorder | reset）。返回错误串或 None。

    reset 清掉冷却与最近错误（欠费充值后手动恢复）；reorder 用 ids 给全量顺序。
    """
    import time as _t
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return "供应商不存在"
        keys = _materialize_keys(prov)
        if op == "add":
            val = (key or "").strip()
            if not val:
                return "密钥不能为空"
            if len(keys) >= MAX_PROVIDER_KEYS:
                return "最多 %d 把密钥" % MAX_PROVIDER_KEYS
            keys.append({"id": _next_key_id(keys), "key": val,
                         "label": (label or "").strip(), "enabled": True})
        elif op in ("update", "delete", "enable", "disable", "reset"):
            target = next((k for k in keys if str(k.get("id")) == str(key_id)), None)
            if target is None:
                return "密钥不存在"
            if op == "delete":
                keys.remove(target)
            elif op == "update":
                if (key or "").strip():
                    target["key"] = key.strip()
                if label is not None:
                    target["label"] = (label or "").strip()
                if enabled is not None:
                    target["enabled"] = bool(enabled)
                    if target["enabled"]:
                        target.pop("cool_until", None)
                        target.pop("last_error", None)
            elif op in ("enable", "disable"):
                target["enabled"] = (op == "enable")
                if target["enabled"]:      # 重新启用即视为手动恢复
                    target.pop("cool_until", None)
                    target.pop("last_error", None)
            else:  # reset：只清冷却与错误，不动启用状态（欠费充值后恢复首选位）
                target.pop("cool_until", None)
                target.pop("last_error", None)
        elif op == "reorder":
            order = [str(i) for i in (ids or [])]
            byid = {str(k.get("id")): k for k in keys}
            if sorted(order) != sorted(byid):
                return "排序列表与现有密钥不一致"
            prov["keys"] = [byid[i] for i in order]
        else:
            return "未知操作 " + op
        _sync_api_key(prov)
        # 密钥变了：旧的 wire 适配探测结果作废（换号可能换了可用协议面）
        if op in ("add", "update", "delete"):
            prov.pop("wire_caps", None)
        _save(data)
        return None


def note_key_error(provider_id, key_id, error=""):
    """一次 KEY 级失败回写：欠费类进冷却（后续解析自动跳过 → 切备用 KEY）。"""
    import time as _t
    if not provider_id or not key_id:
        return
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov or not isinstance(prov.get("keys"), list):
            return          # 老结构没有 keys 数组：不值得为它落盘
        target = next((k for k in prov["keys"] if str(k.get("id")) == str(key_id)), None)
        if target is None:
            return
        target["last_error"] = (error or "")[:300]
        target["last_fail_at"] = _t.time()
        if _quota_error(error):
            target["cool_until"] = _t.time() + _KEY_COOLDOWN_S
        _sync_api_key(prov)
        _save(data)


def note_key_ok(provider_id, key_id):
    """一次 KEY 级成功回写：清最近错误（冷却不动——那是欠费标记，等它自己过期）。"""
    if not provider_id or not key_id:
        return
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return
        target = next((k for k in (prov.get("keys") or []) if str(k.get("id")) == str(key_id)), None)
        if target is None or not target.get("last_error"):
            return
        target.pop("last_error", None)
        _save(data)


def _is_codex_target(target):
    return (target or "").strip().lower() in ("codex-cli", "codex", "codex-code")


def note_codex_wire_dead(provider_id, minutes=30):
    """codex 撞上 wire 不兼容的供应商 → 供应商级冷却（自动绕开的记账位）。

    codex 0.154 起只支持 responses wire（chat 被官方移除），讯飞等只有
    chat completions 的 MaaS 每次 404/启动即拒。runner 按 404+no Route
    matched / wire_api no longer supported 特征自动调用本函数；链展开与
    路由绑定加分据此自动绕开，无需人工改绑定（2026-09-17 mo-so 实测）。
    """
    import time as _t
    if not provider_id:
        return
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return
        prov["codex_wire_dead_until"] = _t.time() + max(1, int(minutes)) * 60
        _save(data)


def codex_wire_blocked(prov):
    """该供应商是否处于 codex wire 不兼容冷却期。"""
    import time as _t
    try:
        return float((prov or {}).get("codex_wire_dead_until") or 0) > _t.time()
    except Exception:
        return False


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
        target = None
        if pid:
            target = next((p for p in plist if p["id"] == pid), None)
        if target is None:
            target = {"id": _next_pid(plist)}
            plist.append(target)
        # 未指定格式：更新时沿用旧值（不因表单漏传就把已定的格式打回 auto），
        # 新增时默认 auto——聚合网关一个密钥常同时开多条 wire，不必先问用户。
        proto = entry.get("protocol") or target.get("protocol") or _PROTOCOL_AUTO
        if proto not in _PROTOCOL_CHOICES:
            return "protocol 只能是 %s" % " / ".join(_PROTOCOL_CHOICES)
        # 地址或密钥变了，旧的 wire 适配探测结果作废（下次刷新/手动测试重测）
        if target.get("base_url") != base_url or (entry.get("api_key") or "").strip():
            target.pop("wire_caps", None)
        target.update({
            "name": name, "protocol": proto, "base_url": base_url,
            "model": (entry.get("model") or "").strip(),
            "model_easy": (entry.get("model_easy") or "").strip(),
            "model_hard": (entry.get("model_hard") or "").strip(),
            "source": entry.get("source") or target.get("source") or "manual",
        })
        key = (entry.get("api_key") or "").strip()
        if isinstance(target.get("keys"), list):
            # 多 KEY 供应商：表单里新填的密钥替换首选 KEY 的值（不另开一把），
            # 并清掉它的冷却/错误——用户手填密钥就是「这把是好的」的意思。
            if key:
                ks = _materialize_keys(target)
                if ks:
                    ks[0]["key"] = key
                    ks[0].pop("cool_until", None)
                    ks[0].pop("last_error", None)
                else:
                    target["keys"] = [{"id": "k1", "key": key, "label": "", "enabled": True}]
            _sync_api_key(target)
        elif key or "api_key" not in target:
            target["api_key"] = key
        if _is_private_host(target["base_url"]):
            target["allow_private"] = True
        _save(data)
        return None


def providers_op(ids, op):
    """批量供应商操作（enable | disable | delete | duplicate）。返回 (改动数, 错误)。

    停用只影响 Tutti 编排时的运行时解析（resolve_binding 返回空 → 回落 CLI 默认），
    不清除配置，也不影响绑定引用，随时可再启用。
    duplicate 复制一条（同地址同密钥的第二个账号/新网关），副本不带绑定引用。
    """
    ids = list(dict.fromkeys(i for i in (ids or []) if i))
    if op not in ("enable", "disable", "delete", "duplicate"):
        return 0, "未知操作 " + op
    if not ids:
        return 0, "未选择供应商"
    with _LOCK:
        data = _load()
        plist = data.get("providers", [])
        known = {p.get("id") for p in plist}
        if op == "duplicate":
            missing = [i for i in ids if i not in known]
            if missing:
                return 0, "供应商不存在"
            fresh = []
            for i in ids:
                src = next(p for p in plist if p.get("id") == i)
                dup = copy.deepcopy(src)
                dup["id"] = _next_pid(plist + fresh)
                dup["name"] = (src.get("name") or "供应商") + " 副本"
                dup["enabled"] = True
                # 副本的 KEY 重新编号，避免与源共用一个 id（界面按 id 定位）
                if isinstance(dup.get("keys"), list):
                    for n, k in enumerate(dup["keys"], 1):
                        if isinstance(k, dict):
                            k["id"] = "k%d" % n
                            k.pop("cool_until", None)
                            k.pop("last_error", None)
                dup.pop("orchestrator", None)
                fresh.append(dup)
            plist.extend(fresh)
            _save(data)
            return len(fresh), ""
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
        if changed or op == "delete":
            sync_binding_alerts()  # 厂商启停/删除 → 绑定链死活立即刷新告警
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
                # 链首非空时主供应商跟链首走：旧读方/下拉残留的 provider_id
                # 若与链首不一致，会复活成「下拉=停用的旧供应商」（2026-09-16
                # 一键推荐实测）。链首为空（纯 CLI 默认凭据）才保留显式指定。
                head_pid = next((c.get("provider_id") for c in b["chain"]
                                 if c.get("provider_id")), "")
                if head_pid:
                    b["provider_id"] = head_pid
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
            # model 为空 = 仅注入供应商凭据（resolve 文档承诺的状态）：
            # 显式指定 provider_id 时必须落盘，否则全新绑定静默丢主供应商
            if provider_id is not None:
                b["provider_id"] = eff_pid
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

# VSCode 系编辑器（Cursor/Trae）用户数据目录随平台不同：
# Windows %APPDATA%\<app>，macOS ~/Library/Application Support/<app>，Linux ~/.config/<app>
if sys.platform == "darwin":
    _VSC_USER_BASE = os.path.join(_HOME, "Library", "Application Support")
elif os.name == "nt":
    _VSC_USER_BASE = _APPDATA
else:
    _VSC_USER_BASE = os.path.join(_HOME, ".config")

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
CURSOR_DB = os.path.join(_VSC_USER_BASE, "Cursor", "User", "globalStorage", "state.vscdb")
TRAE_DBS = [os.path.join(_VSC_USER_BASE, "Trae CN", "User", "globalStorage", "state.vscdb"),
            os.path.join(_VSC_USER_BASE, "TRAE SOLO CN", "User", "globalStorage", "state.vscdb")]


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
        out["note"] = "跳过 {0} 条（缺地址或格式不识别）：{1}".format(
            len(skipped), "、".join(sorted(set(skipped))[:4]))
        out["note_args"] = [len(skipped), "、".join(sorted(set(skipped))[:4])]
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
        out["note"] = "跳过 {0} 个（缺合法 baseURL）：{1}".format(
            len(bad), "、".join(bad[:4]))
        out["note_args"] = [len(bad), "、".join(bad[:4])]
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
    res.setdefault("note_args", [])
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
                "count": added + updated, "note": res["note"],
                "note_args": res.get("note_args", []), "error": res["error"]})
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


def _model_bindable(prov, model):
    """模型是否可用于链降级：models[] 里显式停用/隐藏的不可用；
    名单里查不到（如纯字符串 models 或自由模型名）视为可用，不拦。"""
    for m in (prov.get("models") or []):
        if isinstance(m, dict) and m.get("name") == model:
            return bool(m.get("enabled", True)) and not m.get("hidden")
    return True


def _model_image_in(prov, model):
    """模型是否声明支持图片输入：models[] 条目 image_in；缺省/查不到=False（纯文本）。"""
    for m in ((prov or {}).get("models") or []):
        if isinstance(m, dict) and m.get("name") == model:
            return bool(m.get("image_in"))
    return False


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


def _chain_entry_env(prov, model, target="", endpoint=None, key="", key_id="",
                     provider_id=""):
    """一条链的运行时注入：env（claude=ANTHROPIC_*，codex=一次性 provider 覆盖，
    dsh=DEEPSEEK_*）。endpoint=(proto, base_url, wire_api) 是 wire_caps 适配出的
    生效端点；None 时按供应商原生协议与地址注入。key 指定用哪把密钥（多 KEY
    展开时逐条注入），空则用供应商当前的首选密钥。

    key_id/provider_id 原样带进条目，供 runner 把「哪把 KEY 失败了」回写冷却。
    """
    proto, base = ((endpoint[0], endpoint[1]) if endpoint
                   else (prov["protocol"], prov["base_url"]))
    use_key = key or prov.get("api_key") or ""
    out = {"model": model, "env": {}, "provider": prov,
           "provider_id": provider_id or prov.get("id") or "", "key_id": key_id}
    if _deepseek_env_target(target):
        out["env"] = {"DEEPSEEK_API_KEY": use_key,
                      "DEEPSEEK_BASE_URL": base}
        return out
    if proto == "anthropic":
        out["env"] = {"ANTHROPIC_BASE_URL": base,
                      "ANTHROPIC_AUTH_TOKEN": use_key}
        if model:
            out["env"]["ANTHROPIC_MODEL"] = model
    else:
        out["env"] = {"ORCH_API_KEY": use_key}
        wire_api = endpoint[2] if endpoint else prov.get("wire_api", "responses")
        out["codex_provider"] = {
            "name": "orch", "base_url": base,
            "env_key": "ORCH_API_KEY", "wire_api": wire_api}
    return out


def _entry_endpoint(prov, allowed):
    """链条目的生效端点：显式协议命中直接用；否则查 wire_caps——实测通过的
    wire（可能是同密钥的另一条协议面，也可能是 auto 供应商的分类结果）也可
    注入。返回 (proto, base_url, wire_api) 或 None（真不匹配，维持跳过语义）。

    protocol="auto"（导入时未指定格式）只认实测结果：没有 wire_caps 就返回
    None——不猜。分类由「获取模型列表」后的后台探测补齐，是瞬态状态。
    """
    proto = prov.get("protocol")
    if proto in allowed:
        return proto, prov.get("base_url"), prov.get("wire_api", "responses")
    caps = prov.get("wire_caps") or {}
    for p in allowed:
        cap = caps.get(p) or {}
        if cap.get("base"):
            return p, cap["base"], cap.get("wire_api") or "responses"
    return None


def _protocol_candidates(prov):
    """需要「唯一协议」时按序尝试的候选 [(proto, base)]。

    显式协议只有一条（就是它自己）；auto 列出实测过的 wire（偏好序），
    调用方逐个试、全失败才报错——不猜一条去发请求。"""
    proto = (prov or {}).get("protocol")
    if proto in _PROTOCOLS:
        return [(proto, (prov or {}).get("base_url") or "")]
    caps = (prov or {}).get("wire_caps") or {}
    return [(p, caps[p]["base"]) for p in _WIRE_PREFERENCE
            if (caps.get(p) or {}).get("base")]


def bindable_protocols(agent_kind_or_id):
    """该 CLI 可绑定的 wire 协议（与 resolve_binding 的 allowed 一致）。
    供死链告警/失败文案解释「为什么绑不上」：claude 只认 anthropic，
    codex/dsh 只认 openai，其余开放双协议（含 wire 适配）。"""
    if _deepseek_env_target(agent_kind_or_id) or agent_kind_or_id in ("codex-cli", "codex"):
        return ("openai",)
    if agent_kind_or_id in ("claude-code", "claude"):
        return ("anthropic",)
    return tuple(_BINDABLE_PROTOCOLS)


def binding_dead_msg(cli_id):
    """死链失败/告警文案（pipeline 死链闸门与本模块 sync 共用）：说明为什么
    不回落本机默认 + 该 CLI 需要什么协议的供应商。"""
    try:
        protos = bindable_protocols(cli_id)
    except Exception:
        protos = ()
    hint = ("该 CLI 仅接受 %s 协议的已启用供应商；" % "、".join(protos)) if protos else ""
    return ("绑定链全部失效（链上供应商已停用/删除/无密钥，或模型已停用），"
            "本步判失败、不回落 CLI 本机默认——%s请在「CLI 绑定」页为该 CLI 绑定已启用的供应商" % hint)


def sync_binding_alerts():
    """厂商/模型启停、删除、恢复后立即重评估各 CLI 绑定链的静态死链告警：
    恢复的当场解除、新死的当场亮起——不必等下一次步骤执行才发现
    （2026-09-17 起与死链硬失败闸门配套）。幂等：health 侧静默/恢复语义不变。"""
    from . import health
    try:
        binds = bindings()
    except Exception:
        return
    for cli_id in sorted(binds.keys()):
        b = binds.get(cli_id) or {}
        if not _binding_chain(b):
            continue  # 没配过链的 CLI 不归静态告警管（执行层闸门在跑时兜）
        try:
            r = resolve_binding(cli_id)
            ok = bool(r and r.get("call_chain"))
        except Exception:
            ok = False
        try:
            if ok:
                health.report_binding_ok(cli_id)
            else:
                health.report_binding_dead(cli_id, binding_dead_msg(cli_id))
        except Exception:
            pass  # 告警是尽力而为的旁路：persist 失败（如目录不可用）不拖累操作本身


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
    # 协议必须匹配（bindable_protocols）：dsh 只吃 OpenAI 兼容端点，codex 只吃
    # openai wire（codex_provider 机制），claude 只吃 anthropic wire——混着注入
    # 会产生「codex 拿到 ANTHROPIC_* env 却缺 ORCH_API_KEY」这类必然失败的组合
    # （2026-09-15 连载验收实测，症状：Missing environment variable: ORCH_API_KEY）。
    allowed = bindable_protocols(agent_kind_or_id)

    if chain:
        entries = []
        # 告警模块联动：已被健康监测判定 down 的供应商直接跳过——
        # 链降级的语义就是「别把时间浪费在已知挂掉的网关上」
        # （2026-09-15 实测：qwencode 对宕机网关内部重试 40 分钟才轮到补位）。
        try:
            from . import health
            down_set = health.down_names()
        except Exception:
            down_set = set()
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
            ep = _entry_endpoint(prov, allowed)
            if not ep:
                continue  # 原生协议与适配过的 wire 都不匹配：跳过
            if prov.get("name") in down_set:
                continue  # 健康监测判定 down：跳过，省掉无效等待
            if _is_codex_target(agent_kind_or_id) and (ep[2] == "chat" or codex_wire_blocked(prov)):
                continue  # codex 0.154+ 只讲 responses wire：chat-only 供应商在起跑前
                          # 就剔除（此前撞了才冷却 30 分钟，每轮白烧一次注定失败的
                          # 尝试——2026-09-17 续4 连载 c35 实测）
            if model and not _model_bindable(prov, model):
                continue  # 模型被停用/删除：该条跳过（2026-09-15 告警弹框「禁用该模型」）
            # 多 KEY：同一厂商按 KEY 展开成多条，顺序即调用顺序。欠费的 KEY 被
            # 冷却跳过（切备用），全冷却时仍留一条顶上——降级复用既有尝试循环。
            for kk in _chain_keys(prov):
                if len(entries) >= MAX_CHAIN_ATTEMPTS:
                    break
                entries.append(_chain_entry_env(
                    prov, model or prov.get("model") or "",
                    target=agent_kind_or_id, endpoint=ep, key=kk["key"],
                    key_id=kk.get("id") or "", provider_id=pid))
            if len(entries) >= MAX_CHAIN_ATTEMPTS:
                break
        if not entries:
            return None
        head = entries[0]
        out = {"model": head["model"], "env": head["env"], "provider": head.get("provider"),
               # model_fallbacks 是「换模型」的列表（runner 无链时的回退用）：
               # 同模型的其它 KEY 条目不算换模型，必须排除，否则主模型会被当成
               # 自己的降级备选。多 KEY 的切换由 call_chain 逐条尝试负责。
               "model_fallbacks": list(dict.fromkeys(
                   e["model"] for e in entries[1:]
                   if e.get("model") and e["model"] != head["model"])),
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
    ep = _entry_endpoint(prov, allowed)
    if not ep:
        return None  # google 只登记；dsh 只接受 OpenAI 兼容端点；未适配的不硬塞
    if _is_codex_target(agent_kind_or_id) and (ep[2] == "chat" or codex_wire_blocked(prov)):
        return None  # codex 0.154+ 只讲 responses：chat-only 供应商直接判不可绑
                     # （解析为空 → 死链闸门/路由降权接手，不浪费 CLI 尝试）
    names = [m["name"] for m in _enabled_models(prov)]
    model = prov.get("model_" + tier) or "" if (routing and tier) else ""
    if not model:
        model = prov.get("model") or ""
    if not model and names:
        model = names[0] if (not routing or difficulty != "easy") else names[-1]
    # model 可为空：仅注入供应商凭据，不指定模型（用网关默认）
    fallbacks = [n for n in names if n != model][:MAX_BIND_MODELS - 1]
    # 多 KEY：主模型先按 KEY 逐把试（欠费自动切备用），再降级到别的模型
    chain_keys = _chain_keys(prov)
    entries = []
    for kk in chain_keys:
        entries.append(_chain_entry_env(prov, model, target=agent_kind_or_id,
                                        endpoint=ep, key=kk["key"],
                                        key_id=kk.get("id") or "", provider_id=pid))
    for n in fallbacks:
        if len(entries) >= MAX_CHAIN_ATTEMPTS:
            break
        entries.append(_chain_entry_env(prov, n, target=agent_kind_or_id,
                                        endpoint=ep, provider_id=pid))
    head = entries[0]
    out = {"model": model, "env": head["env"], "provider": prov,
           "model_fallbacks": fallbacks,
           "call_chain": [dict(e) for e in entries]}
    if head.get("codex_provider"):
        out["codex_provider"] = head["codex_provider"]
    return out


def launch_pick(agent_id, protocols):
    """「一键打开」专属选链：绑定链里第一个「已启用+有密钥+协议匹配」的供应商。

    与 resolve_binding 的差别：协议不匹配不降级——交互 TUI 只认自家协议的凭据
    （claude 只吃 ANTHROPIC_*，塞 ORCH_API_KEY 等于没 key），宁可明确提示也不
    静默错注入。返回 (pick|None, note)：pick={model, provider}；note 面向用户的
    不可用原因（链上同协议供应商停用 / 协议不匹配 / 未绑定），可直达 toast。
    """
    protocols = tuple(p for p in (protocols or ()) if p)
    b = bindings().get(agent_id) or {}
    chain = _binding_chain(b)
    provs = {p.get("id"): p for p in providers()}
    disabled, mismatch = [], False
    for item in chain:
        pid = str(item.get("provider_id") or "").strip()
        if not pid:
            continue  # 纯链条目（只传 -m）：对打开场景无凭据可用
        prov = provs.get(pid)
        if not prov:
            continue
        if not prov.get("enabled", True) or not prov.get("api_key"):
            if prov.get("protocol") in protocols:
                disabled.append(prov.get("name") or pid)
            continue
        if prov.get("protocol") in protocols:
            return {"model": str(item.get("model") or "").strip() or prov.get("model") or "",
                    "provider": prov}, None
        # 原生协议不匹配但适配测试过（wire_caps）：按适配出的端点给一份
        # 协议/地址已覆写的供应商副本，下游凭据注入逻辑无需感知差异。
        caps = prov.get("wire_caps") or {}
        for proto in protocols:
            cap = caps.get(proto) or {}
            if cap.get("base"):
                adapted = dict(prov, protocol=proto, base_url=cap["base"])
                if cap.get("wire_api"):
                    adapted["wire_api"] = cap["wire_api"]
                return {"model": str(item.get("model") or "").strip() or prov.get("model") or "",
                        "provider": adapted}, None
        mismatch = True
    if disabled:
        return None, ("绑定链里的 %s 已停用或无密钥：打开后需在其自带界面登录；"
                      "要打开即用请在「CLI 绑定」页启用"
                      % "、".join(dict.fromkeys(disabled)))
    if mismatch:
        return None, ("当前绑定的供应商协议与该 CLI 不匹配：打开后需在其自带界面登录；"
                      "要打开即用请在「CLI 绑定」页换绑可注入协议的供应商")
    return None, ("未绑定供应商：打开后需在其自带界面登录；"
                  "要打开即用请到「CLI 绑定」页绑定")


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


def set_model_caps(provider_id, name, image_in):
    """声明单个模型的模态能力（当前仅 image_in 图片输入）。返回错误文案或 None。"""
    with _LOCK:
        data = _load()
        prov = next((p for p in data.get("providers", []) if p.get("id") == provider_id), None)
        if not prov:
            return "供应商不存在"
        target = next((m for m in (prov.get("models") or [])
                       if m.get("name") == name), None)
        if target is None:
            return "模型不存在: %s" % name
        image_in = bool(image_in)
        if bool(target.get("image_in")) != image_in:   # 有差异才落盘
            target["image_in"] = image_in
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


def _sse_parse(proto, obj):
    """解析一条 SSE 事件 JSON → (增量文本, usage增量或None)。三协议字段各异。

    只认文本增量与用量字段；tool_call / reasoning 等事件返回空串跳过。"""
    if not isinstance(obj, dict):
        return "", None
    if proto == "anthropic":
        t = obj.get("type")
        if t == "content_block_delta":
            d = obj.get("delta") or {}
            return (d.get("text") or ""), None
        if t == "message_start":
            u = (obj.get("message") or {}).get("usage") or obj.get("usage") or {}
            return "", {"input": int(u.get("input_tokens") or 0),
                        "cached": (int(u.get("cache_read_input_tokens") or 0)
                                   + int(u.get("cache_creation_input_tokens") or 0))}
        if t == "message_delta":
            u = obj.get("usage") or {}
            return "", {"output": int(u.get("output_tokens") or 0)}
        return "", None
    if proto == "google":
        text = ""
        for cand in obj.get("candidates") or []:
            for p in ((cand.get("content") or {}).get("parts") or []):
                if isinstance(p, dict):
                    text += p.get("text") or ""
        um = obj.get("usageMetadata")
        usage = None
        if isinstance(um, dict):
            usage = {"input": int(um.get("promptTokenCount") or 0),
                     "output": int(um.get("candidatesTokenCount") or 0),
                     "total": int(um.get("totalTokenCount") or 0)}
        return text, usage
    # openai 兼容（chat/completions 流）
    text = ""
    for ch in obj.get("choices") or []:
        d = ch.get("delta") or {}
        text += d.get("content") or ""
    usage = None
    u = obj.get("usage")
    if isinstance(u, dict):
        usage = {"input": int(u.get("prompt_tokens") or 0),
                 "output": int(u.get("completion_tokens") or 0),
                 "total": int(u.get("total_tokens") or 0),
                 "cached": int(((u.get("prompt_tokens_details") or {}) or {}).get("cached_tokens") or 0)}
    return text, usage


def _post_sse_http(url, headers, body, allow_private, timeout, proto, on_delta):
    """带 SSRF 防护的流式 POST（SSE）。返回 (status, text, usage, err)。

    编排者直连调用不经 run_process，此前生成全程日志只有一行标题（黑箱）；
    这里逐行读 data: 事件、边收边回调 on_delta(增量文本)，直连调用也能像
    CLI 步骤一样看到「正在吐字」。单条事件解析失败静默跳过，不中断整流。"""
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        return 0, "", None, "协议必须是 http/https"
    if _validate_host(url, allow_private) is None:
        return 0, "", None, "目标地址校验未通过"
    parts, usage = [], {}
    resp_status = 0
    try:
        req = urllib.request.Request(url, method="POST",
                                     headers=dict(headers, **{"Content-Type": "application/json"}),
                                     data=json.dumps(body).encode("utf-8"))
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            resp_status = resp.status
            if not 200 <= resp.status < 300:
                raw = resp.read(65536).decode("utf-8", "replace")
                return resp.status, "", None, "HTTP %s %s" % (resp.status, raw[:200])
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                delta, u = _sse_parse(proto, obj)
                if delta:
                    parts.append(delta)
                    try:
                        on_delta(delta)
                    except Exception:
                        pass
                if isinstance(u, dict):
                    usage.update({k: v for k, v in u.items() if v})
                if sum(len(s) for s in parts) > 2 * 1024 * 1024:   # 防失控
                    break
    except Exception as e:
        return 0, "", None, repr(e)[:300]
    text = "".join(parts)
    if not usage.get("total"):
        usage["total"] = usage.get("input", 0) + usage.get("output", 0) + usage.get("cached", 0)
    return resp_status, text, usage, ""


def test_provider(provider_id):
    """供应商连通性测试：GET /models 并测延迟。返回 {ok, latency_ms, count, error}。

    多 KEY：按顺序试，记录哪把通（key_id 回给界面），失败的 KEY 记账。
    """
    import time as _t
    with _LOCK:
        prov = next((p for p in providers() if p.get("id") == provider_id), None)
    if not prov:
        return {"ok": False, "error": "供应商不存在"}
    keys = _chain_keys(prov) or [{"key": prov.get("api_key") or "", "id": ""}]
    t0 = _t.time()
    last = ""
    for kk in keys:
        names, err = _fetch_models_http(prov.get("base_url"), kk["key"],
                                        prov.get("protocol"), bool(prov.get("allow_private")))
        if names is not None:
            note_key_ok(provider_id, kk.get("id") or "")
            return {"ok": True, "latency_ms": int((_t.time() - t0) * 1000),
                    "count": len(names), "error": "", "key_id": kk.get("id") or ""}
        last = err
        note_key_error(provider_id, kk.get("id") or "", err)
    return {"ok": False, "latency_ms": int((_t.time() - t0) * 1000), "error": last}


def test_model(provider_id, model_name, key_id=""):
    """单模型连通性测试：发一条 1 token 的最小对话。返回 {ok, latency_ms, error}。

    auto 供应商逐条试实测过的 wire（显式协议只有一条）；多 KEY 供应商逐把试
    （指定 key_id 则只测那把）。返回里带 protocol/key_id 说明这次是谁通的。
    """
    import time as _t
    import urllib.parse
    with _LOCK:
        prov = next((p for p in providers() if p.get("id") == provider_id), None)
    if not prov or not prov.get("api_key"):
        return {"ok": False, "error": "供应商不存在或未配置密钥"}
    protos = _protocol_candidates(prov)
    if not protos:
        return {"ok": False, "error": "该供应商还没有可用 wire——先「获取模型列表」或手动指定格式"}
    if key_id:
        keys = [k for k in _provider_keys(prov) if k["id"] == key_id]
        if not keys:
            return {"ok": False, "error": "密钥不存在"}
    else:
        keys = _chain_keys(prov) or [{"key": prov.get("api_key") or "", "id": ""}]
    t0 = _t.time()
    last = {"ok": False, "error": "无可用 wire"}
    for kk in keys:
        for proto, pbase in protos:
            base = (pbase or "").rstrip("/")
            if proto == "google":
                url = base + "/v1beta/models/%s:generateContent" % model_name
                if base.endswith("/v1beta"):
                    url = base + "/models/%s:generateContent" % model_name
                headers = {"x-goog-api-key": kk["key"]}
                body = {"contents": [{"parts": [{"text": "ping"}]}]}
            else:
                path = "/messages" if proto == "anthropic" else "/chat/completions"
                url = (base + path) if base.endswith("/v1") else (base + "/v1" + path)
                if proto == "anthropic":
                    headers = {"x-api-key": kk["key"], "anthropic-version": "2023-06-01"}
                else:
                    headers = {"Authorization": "Bearer " + kk["key"]}
                body = {"model": model_name, "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}]}
            status, data, err = _post_json_http(url, headers, body, bool(prov.get("allow_private")))
            if status == 0:
                last = {"ok": False, "error": err}
                note_key_error(provider_id, kk.get("id") or "", err)
                continue
            if 200 <= status < 300:
                note_key_ok(provider_id, kk.get("id") or "")
                return {"ok": True, "latency_ms": int((_t.time() - t0) * 1000),
                        "error": "", "protocol": proto, "key_id": kk.get("id") or ""}
            msg = ""
            if isinstance(data, dict):
                e = data.get("error")
                msg = e.get("message", "") if isinstance(e, dict) else str(e)
            last = {"ok": False, "error": "HTTP %s %s" % (status, str(msg)[:160])}
            note_key_error(provider_id, kk.get("id") or "", last["error"])
    last["latency_ms"] = int((_t.time() - t0) * 1000)
    return last


# ---------------------------------------------------------------- wire 协议适配
# 聚合中转网关（new-api/one-api 系）通常同一密钥同时开 openai(/chat/completions)
# 与 anthropic(/v1/messages) 两面 wire。在「模型接入」页用 1 token 最小对话实测，
# 通过的记入 provider["wire_caps"][proto]；绑定解析（_entry_endpoint）据此放宽
# 「协议必须原生匹配」——claude 链上的 openai 供应商、codex 链上的 anthropic
# 供应商不再被跳过。探不过就保持原样跳过：宁可 ⚠ 也不错注入（2026-09-15 实测
# 混注入会产生 Missing ORCH_API_KEY 这类必然失败组合）。

# 已知第一方双端点映射：(主机名集合, 原生路径前缀, 另一协议的端点 base)。
# 这类网关两种 wire 挂在不同路径，同 base 探测必 404，只能按已知映射补候选。
_KNOWN_WIRE_BASES = (
    (("api.z.ai",), "/api/anthropic", "https://api.z.ai/api/paas/v4"),
)


def _wire_base_candidates(base_url, target_proto):
    """探测候选 base 列表：常规 /v1 变体 + 已知双端点映射（映射优先）。"""
    import urllib.parse
    base = (base_url or "").rstrip("/")
    if target_proto == "openai":
        # openai 习惯：base 含 /v1 直接用；否则补 /v1，再兜一个不带 /v1 的
        cands = [base if base.endswith("/v1") else base + "/v1"]
        if not base.endswith("/v1"):
            cands.append(base)
    else:
        # anthropic 习惯：CLI 在 base 后拼 /v1/messages，base 本身不含 /v1
        cands = [base[:-3] if base.endswith("/v1") else base]
    u = urllib.parse.urlsplit(base)
    host, path = u.hostname or "", u.path or ""
    for hosts, suffix, alt in _KNOWN_WIRE_BASES:
        if host in hosts and path.startswith(suffix) and alt not in cands:
            cands.insert(0, alt)
    return cands


def _probe_wire_once(base, api_key, target_proto, model, allow_private, timeout=12):
    """对一个候选 base 实测目标 wire（1 token 最小对话）。返回 (ok, wire_api, err)。

    openai wire 先试 responses（codex 默认）再退 chat/completions；anthropic
    只试 /v1/messages（x-api-key 与 Bearer 两种鉴权头都试）。"""
    if target_proto == "anthropic":
        url = base.rstrip("/") + "/v1/messages"
        body = {"model": model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}]}
        variants = [("messages", url, body, {"x-api-key": api_key,
                                             "anthropic-version": "2023-06-01"}),
                    ("messages", url, body, {"Authorization": "Bearer " + api_key,
                                             "anthropic-version": "2023-06-01"})]
    else:
        b = base.rstrip("/")
        variants = [
            ("responses", b + "/responses",
             {"model": model, "input": "ping", "max_output_tokens": 16},
             {"Authorization": "Bearer " + api_key}),
            ("chat", b + "/chat/completions",
             {"model": model, "max_tokens": 1,
              "messages": [{"role": "user", "content": "ping"}]},
             {"Authorization": "Bearer " + api_key}),
        ]
        if target_proto != "openai":  # pragma: no cover — 调用方保证
            variants = variants[-1:]
    last = ""
    for item in variants:
        wire_api, url, body, headers = item
        status, _data, err = _post_json_http(url, headers, body, allow_private, timeout=timeout)
        if 200 <= status < 300:
            return True, wire_api, ""
        last = err if status == 0 else "HTTP %s" % status
    return False, "", last


def probe_wire_caps(provider_id):
    """适配测试：实测该供应商的可用 wire，存进 wire_caps。返回 (caps, note)。

    显式协议的供应商只测「除原生外」的 wire（原生天然可用，不必花请求）；
    protocol="auto"（导入未指定格式）则把可注入 wire 全测一遍，caps 就是分类
    结果。之前通过、本次失败的条目移除（网关两面变动以实测为准）。google 不参与。
    note 是给 UI 的补充说明（失败原因 / 未测原因），全通过时为空串。"""
    import time as _t
    with _LOCK:
        prov = next((p for p in providers() if p.get("id") == provider_id), None)
    if not prov:
        return {}, "供应商不存在"
    if not prov.get("api_key"):
        return {}, "该供应商未配置密钥"
    model = prov.get("model") or next(
        (m.get("name") for m in _ranked(prov.get("models") or [])
         if m.get("name") and m.get("enabled", True)), "")
    if not model:
        return {}, "没有可用模型名——先「获取模型列表」再测"
    native = prov.get("protocol")
    auto = native == _PROTOCOL_AUTO
    caps = dict(prov.get("wire_caps") or {})
    notes = []
    for target in _BINDABLE_PROTOCOLS:
        if not auto and target == native:
            continue  # 显式协议：原生那条不用测
        found, err = None, ""
        for base in _wire_base_candidates(prov.get("base_url"), target):
            ok, wire_api, err = _probe_wire_once(base, prov["api_key"], target,
                                                 model, bool(prov.get("allow_private")))
            if ok:
                found = {"base": base.rstrip("/"), "wire_api": wire_api,
                         "checked_at": _t.strftime("%Y-%m-%d %H:%M")}
                break
        if found:
            caps[target] = found
        else:
            caps.pop(target, None)
            notes.append("%s wire 不通（%s）" % (target, (err or "无响应")[:80]))
    if auto and not caps:
        notes.append("没有探到可用的 wire——检查地址/密钥，或手动指定格式")
    if caps != (prov.get("wire_caps") or {}):
        with _LOCK:
            data = _load()
            p = next((q for q in data.get("providers", []) if q.get("id") == provider_id), None)
            if p is not None:
                if caps:
                    p["wire_caps"] = caps
                else:
                    p.pop("wire_caps", None)
                _save(data)
    return caps, "；".join(notes)


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


# ---------------------------------------------------------------- 编排设置（编排者模型）

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


def chat(provider_id, model_name, prompt, max_tokens=2048, timeout=120, cache_ttl=0,
         on_delta=None):
    """直连供应商 API 做一次对话（编排者规划 / 连通性测试）。

    支持 anthropic / openai / google 三种协议；复用 SSRF 防护。
    返回 {ok, text, tokens, usage, error}；usage 为细分 {input, output, cached, reasoning, total}。
    cache_ttl>0 启用精确匹配响应缓存（key=供应商+模型+prompt+max_tokens，只缓存
    ok 结果）——仅限幂等调用（连通性测试等）；创作类调用不要开，否则同一 prompt
    的二次请求会屏蔽模型的新输出。
    on_delta 给定时走 SSE 流式：每收到一段增量文本回调一次。编排者直连调用
    不经 run_process、原本生成全程日志只有一行标题，靠它把「正在吐字」实时
    写进步骤日志（planner._log_streamer 节流落盘）。
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
    protos = _protocol_candidates(prov)
    if not protos:
        return {"ok": False, "text": "", "tokens": 0, "usage": None,
                "error": "该供应商还没有可用 wire——先「获取模型列表」或手动指定格式"}
    # 多 KEY：按「KEY 序 × 协议」展开逐条试。欠费的 KEY 先被跳过（切备用），
    # 冷却中的 KEY 一条都不剩时仍按原顺序试——比整家供应商不可用强。
    keys = _chain_keys(prov) or [{"key": prov.get("api_key") or "", "id": ""}]
    cands = [(proto, base, k["key"], k.get("id") or "")
             for k in keys for (proto, base) in protos]
    if on_delta is not None:
        cands = cands[:1]   # 流式不重试：回调会重复吐字，宁可按首选 wire 失败

    def _build(proto, base, use_key):
        """按协议构造 (url, headers, body)。base 已 strip。"""
        if proto == "google":
            if base.endswith("/v1beta"):
                url = base + "/models/%s:generateContent" % model_name
            else:
                url = base + "/v1beta/models/%s:generateContent" % model_name
            headers = {"x-goog-api-key": use_key}
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens}}
        else:
            path = "/messages" if proto == "anthropic" else "/chat/completions"
            url = (base + path) if base.endswith("/v1") else (base + "/v1" + path)
            if proto == "anthropic":
                headers = {"x-api-key": use_key, "anthropic-version": "2023-06-01"}
            else:
                headers = {"Authorization": "Bearer " + use_key}
            body = {"model": model_name, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}]}
        return url, headers, body

    last_err = ""
    for proto, pbase, use_key, key_id in cands:
        base = (pbase or "").rstrip("/")
        url, headers, body = _build(proto, base, use_key)

        if on_delta is not None:
            sbody = dict(body)
            sbody["stream"] = True
            if proto not in ("anthropic", "google"):
                sbody["stream_options"] = {"include_usage": True}   # openai 系最后一个 chunk 带 usage
            status, text, usage_d, err = _post_sse_http(
                url, headers, sbody, bool(prov.get("allow_private")), timeout, proto, on_delta)
            if status == 0 or err:
                return {"ok": False, "text": "", "tokens": 0, "usage": None, "error": err}
            if not (text or "").strip():
                # 网关对 stream 请求回了 200 但没吐任何 SSE 事件（空流/普通 JSON 体，
                # 实测 vsllm 大请求会这样）：绝不能当成功返回空文本，退回非流式重发
                pass
            else:
                if not usage_d.get("total"):
                    usage_d["total"] = (usage_d.get("input", 0) + usage_d.get("output", 0)
                                        + usage_d.get("cached", 0))
                return {"ok": True, "text": (text or "").strip(), "tokens": usage_d.get("total") or 0,
                        "usage": usage_d, "error": ""}

        status, data, err = _post_json_http(url, headers, body, bool(prov.get("allow_private")),
                                            timeout=timeout)
        if status == 0:
            last_err = err
            note_key_error(provider_id, key_id, err)   # 记账：欠费类进冷却，切备用
            continue          # 这条 wire/KEY 连不上：换下一条
        if not 200 <= status < 300:
            msg = ""
            if isinstance(data, dict):
                e = data.get("error")
                msg = e.get("message", "") if isinstance(e, dict) else str(e)
            last_err = "HTTP %s %s" % (status, str(msg)[:200])
            note_key_error(provider_id, key_id, last_err)
            continue
        note_key_ok(provider_id, key_id)
        break
    else:
        return {"ok": False, "text": "", "tokens": 0, "usage": None,
                "error": last_err or "所有可用 wire 均失败"}
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
