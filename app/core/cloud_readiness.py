# -*- coding: utf-8 -*-
"""Gate for a future hosted executor; this release intentionally stays local."""
from __future__ import annotations

import os


def snapshot():
    from . import phone_relay, store
    runs = store.list_runs(limit=100000)
    active = sum(1 for r in runs if r.get("status") in ("queued", "running"))
    relay = phone_relay.view()
    checks = {
        "local_executor": True,
        "remote_control": bool(relay.get("connected")),
        "active_runs": active == 0,
        "cloud_executor": False,
    }
    return {"status": "ready_for_review" if all(checks[k] for k in ("local_executor", "remote_control", "active_runs"))
            else "hold", "checks": checks,
            "policy": "云端 7×24 执行尚未启用；需先完成本地接管、远程控制和故障恢复验收。",
            "active_runs": active, "relay": {"connected": bool(relay.get("connected")),
                                               "enabled": bool(relay.get("enabled"))}}
