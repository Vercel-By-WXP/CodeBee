# -*- coding: utf-8 -*-
"""插件市场目录 JSON 化（2026-11-01，借鉴 Obsidian community-plugins.json
目录分发协议：市场=目录 JSON + manifest 校验 + 条目=manifest+仓库链接）。

平台化「技能包分发格式」的地基：第三方作者按本格式发布 catalog.json，
CodeBee 导入时逐条 manifest 校验；能对上本地市场的条目标 installable，
对不上的如实报 unknown——不装没有内容的条目（装一个壳=欺骗用户）。

格式（v1）：
{"schema": "codebee-market-catalog/1", "name": "...",
 "plugins": [{"id","name","description","version","author","homepage","scopes"}]}
"""
from __future__ import annotations

import re
import threading

from . import market

_LOCK = threading.RLock()
SCHEMA = "codebee-market-catalog/1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_URL_RE = re.compile(r"^https?://\S+$")
_MAX = 200


def _cut(v, n=_MAX):
    return str(v or "")[:n]


def validate_manifest(m):
    """校验单条 manifest。返回 (normalized, errors)；errors 非空 = 无效。"""
    if not isinstance(m, dict):
        return {}, ["manifest 必须是对象"]
    errors = []
    mid = _cut(m.get("id"), 64).strip()
    name = _cut(m.get("name"), 80).strip()
    desc = _cut(m.get("description"), 500).strip()
    if not _ID_RE.match(mid):
        errors.append("id 必须匹配 ^[a-z0-9][a-z0-9_-]{1,63}$，得到 %r" % mid[:40])
    if not name:
        errors.append("name 不能为空")
    if not desc:
        errors.append("description 不能为空")
    out = {"id": mid, "name": name, "description": desc}
    ver = _cut(m.get("version"), 40).strip()
    if ver:
        out["version"] = ver
    author = _cut(m.get("author"), 80).strip()
    if author:
        out["author"] = author
    home = _cut(m.get("homepage"), 300).strip()
    if home and not _URL_RE.match(home):
        errors.append("homepage 必须是 http(s) URL")
    elif home:
        out["homepage"] = home
    scopes = m.get("scopes")
    if scopes is not None:
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            errors.append("scopes 必须是字符串数组")
        else:
            out["scopes"] = [_cut(s, 60) for s in scopes[:12]]
    return out, errors


def catalog_json(providers_view=None):
    """本地市场 → 分发格式 catalog。providers_view 供测试注入，缺省 market.view()。"""
    view = providers_view if providers_view is not None else market.view()
    plugins = []
    for it in view.get("catalog") or []:
        plugins.append({
            "id": str(it.get("id") or ""),
            "name": str(it.get("name") or ""),
            "description": str(it.get("desc") or ""),
            "category": str(it.get("category") or ""),
            "version": str(it.get("version") or ""),
            "scopes": list(it.get("scopes") or []),
            "installed": bool(it.get("installed")),
            "builtin": bool(it.get("builtin")),
            "installable": bool(it.get("installable")),
        })
    return {"schema": SCHEMA, "name": "CodeBee 插件市场", "plugins": plugins}


def import_catalog(data):
    """导入第三方 catalog：逐条校验 + 对账本地可安装性。
    返回 {"schema_ok", "valid", "errors", "installable_now", "unknown",
          "unpinned", "version_mismatch"}。
    版本钉扎（Ruflo 纪律：目录条目必须可复现）：清单里没写 version 的条目进
    unpinned（警告不是错误）；写了 version 且本地已装版本可对上的，版本不一致
    如实进 version_mismatch——装旧壳盖新包比不装更糟。"""
    res = {"schema_ok": False, "valid": [], "errors": [], "installable_now": [],
           "unknown": [], "unpinned": [], "version_mismatch": []}
    if not isinstance(data, dict):
        res["errors"].append({"index": -1, "id": "",
                              "errors": ["catalog 必须是对象"]})
        return res
    res["schema_ok"] = str(data.get("schema") or "") == SCHEMA
    local = {p["id"]: str(p.get("version") or "")
             for p in catalog_json()["plugins"]}
    for i, m in enumerate(data.get("plugins") or []):
        norm, errs = validate_manifest(m)
        if errs:
            res["errors"].append({"index": i,
                                  "id": str((m or {}).get("id") or "")[:40],
                                  "errors": errs})
            continue
        res["valid"] = res["valid"] + [norm]
        if not norm.get("version"):
            res["unpinned"].append(norm["id"])
        if norm["id"] in local:
            res["installable_now"].append(norm["id"])
            lv, cv = local[norm["id"]], str(norm.get("version") or "")
            if lv and cv and lv != cv:
                res["version_mismatch"].append({"id": norm["id"],
                                                "catalog": cv, "local": lv})
        else:
            res["unknown"].append(norm["id"])
    return res
