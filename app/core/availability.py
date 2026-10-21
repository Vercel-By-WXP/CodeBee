# -*- coding: utf-8 -*-
"""通道可用性聚合视图（GET /api/availability）。

一页看清「现在哪些 CLI 和厂商模型能正常用」：厂商健康状态（真实调用 +
探针）、模型级熔断、CLI 降级链、CLI 账号探活，全部来自现成台账，本模块
只做只读聚合——不探测、不写状态，聚合失败降级为空视图而不是 500。
密钥沿用 provider_view() 的脱敏输出，本模块不再触碰任何 key 字段。
"""
from __future__ import annotations

import time


def _health_view(row):
    if not isinstance(row, dict) or not row:
        return None
    return {
        "status": row.get("status") or "ok",
        "consecutive_failures": int(row.get("consecutive_failures") or 0),
        "first_fail_at": row.get("first_fail_at") or "",
        "last_fail_at": row.get("last_fail_at") or "",
        "last_ok_at": row.get("last_ok_at") or "",
        "last_error": row.get("last_error") or "",
        "alerting": bool(row.get("alerting")),
        "silenced": bool(row.get("silenced")),
    }


def _account_view(acct):
    if not isinstance(acct, dict) or not acct:
        return None
    return {
        "status": acct.get("status") or "ok",
        "code": acct.get("code") or "",
        "summary": acct.get("summary") or "",
        "last_error": acct.get("last_error") or "",
    }


def payload():
    from . import health, modelhub
    try:
        snap = health.snapshot()
    except Exception:
        snap = {"providers": [], "accounts": []}
    rows = [r for r in (snap.get("providers") or []) if isinstance(r, dict)]
    by_id = {str(r.get("provider_id") or ""): r for r in rows if r.get("provider_id")}
    by_name = {str(r.get("provider") or ""): r for r in rows}
    try:
        down_pairs = set(health.down_models())
    except Exception:
        down_pairs = set()

    providers = []
    try:
        prov_views = modelhub.provider_view()
    except Exception:
        prov_views = []
    for p in prov_views:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        name = str(p.get("name") or "")
        row = by_id.get(pid) or by_name.get(name) or {}
        models = []
        for m in p.get("models") or []:
            if not isinstance(m, dict):
                continue
            mname = str(m.get("name") or "")
            models.append({
                "name": mname,
                "enabled": bool(m.get("enabled", True)) and not m.get("hidden"),
                "down": (name, mname) in down_pairs,
            })
        keys = p.get("keys") or []
        providers.append({
            "id": pid, "name": name,
            "base_url": str(p.get("base_url") or ""),
            "protocol": str(p.get("protocol") or ""),
            "enabled": bool(p.get("enabled", True)),
            "keys_enabled": int(p.get("keys_enabled") or 0),
            "keys_total": len(keys),
            "model": str(p.get("model") or ""),
            "health": _health_view(row),
            "models": models,
        })
    name_by_id = {p["id"]: p["name"] for p in providers}

    accounts = {}
    for a in snap.get("accounts") or []:
        if isinstance(a, dict) and a.get("agent_id"):
            accounts[str(a["agent_id"])] = a
    clis = []
    try:
        binding_map = modelhub.bindings() or {}
    except Exception:
        binding_map = {}
    for agent, b in binding_map.items():
        if not isinstance(b, dict):
            continue
        chain = []
        for e in b.get("chain") or []:
            if not isinstance(e, dict):
                continue
            pid = str(e.get("provider_id") or "")
            pname = name_by_id.get(pid, "")
            model = str(e.get("model") or "")
            chain.append({
                "provider_id": pid, "provider": pname, "model": model,
                "down": (pname, model) in down_pairs if pname else False,
            })
        clis.append({
            "id": str(agent or ""), "chain": chain,
            "account": _account_view(accounts.pop(str(agent or ""), None)),
        })
    for aid, acct in accounts.items():   # 只有账号探活、没进调度绑定的 CLI
        clis.append({"id": aid, "chain": [], "account": _account_view(acct)})
    clis.sort(key=lambda c: c["id"])

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "providers": providers,
        "clis": clis,
        "any_alerting": bool(snap.get("any_alerting")),
    }
