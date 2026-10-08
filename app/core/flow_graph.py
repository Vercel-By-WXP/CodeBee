# -*- coding: utf-8 -*-
"""Stable graph projection for the existing flow/version registry."""
from __future__ import annotations

import hashlib
import json

from . import flows


def build(flow_id):
    flow = flows.get_flow(str(flow_id or ""))
    if not flow:
        raise ValueError("流程不存在")
    engine = flow.get("engine") or "review"
    if engine == "code":
        names = [("plan", "规划"), ("implement", "实现"), ("verify", "验证"),
                 ("review", "评审"), ("repair", "修复")]
    elif engine == "direct":
        names = [("input", "输入"), ("execute", "执行"), ("output", "产出")]
    else:
        names = [("outline", "大纲"), ("draft", "起草"), ("review", "评审"),
                 ("revise", "修订"), ("publish", "发布门禁")]
    nodes = [{"id": key, "label": label, "kind": "step", "order": i}
             for i, (key, label) in enumerate(names)]
    edges = [{"from": names[i][0], "to": names[i + 1][0]} for i in range(len(names) - 1)]
    digest = hashlib.sha256(json.dumps({"flow": flow, "nodes": nodes, "edges": edges},
                                       ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {"flow_id": flow.get("id"), "name": flow.get("name") or flow.get("id"),
            "engine": engine, "version_id": "flowgraph-" + digest[:16],
            "nodes": nodes, "edges": edges,
            "summary": {"threshold": flow.get("threshold"), "rounds": flow.get("rounds"),
                        "builtin": bool(flow.get("builtin")), "edited": bool(flow.get("edited"))}}
