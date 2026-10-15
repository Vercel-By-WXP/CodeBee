"""Explain recorded configuration inputs and actual step bindings, without keys."""
from __future__ import annotations

import copy
import os

from .redact import scrub_text

FIELDS = ("type", "engine", "mode", "thinking", "difficulty", "timeout_s",
          "implementer", "direct_agent", "direct_provider_id", "direct_model",
          "rounds", "threshold", "best_of", "acceptance_thresholds")
_FLOW_FIELDS = ("rounds", "threshold", "best_of")


def _safe(value):
    if isinstance(value, str):
        return scrub_text(value, 160)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return ""


def sources(task, payload, flow):
    """Record only source labels; never keep the raw request or credentials."""
    result = {}
    for key in FIELDS:
        if key not in task:
            continue
        if key in ("type", "engine"):
            source = "flow"
        elif key in _FLOW_FIELDS:
            source = ("task.request" if payload.get(key) else
                      "flow" if flow.get(key) else "default")
        elif key == "timeout_s" and payload.get(key) in (None, ""):
            source = "environment" if os.environ.get("TUTTI_TASK_TIMEOUT_S") else "default.workload"
        elif key in payload:
            source = "task.request"
        elif key == "mode" and task.get("implementer"):
            source = "task.implementer"
        else:
            source = "default"
        result[key] = source
    return result


def task_view(task):
    recorded = task.get("configuration_sources") or {}
    result = {}
    for key in FIELDS:
        if key not in task:
            continue
        value = task[key]
        if key == "acceptance_thresholds":
            from .acceptance import normalize_thresholds
            try:
                value = normalize_thresholds(value)
            except ValueError:
                value = {}
        else:
            value = _safe(value)
        result[key] = {"value": value, "source": recorded.get(key) or "unknown",
                       "reason": "保存后的有效参数；请求值可能经过校验和归一化"}
    return result


def capture(task):
    overrides = []
    if (task.get("resume") or {}).get("agent"):
        source, agent = "task.resume", task["resume"]["agent"]
        overrides = [k for k in ("direct_agent", "direct_provider_id", "implementer") if task.get(k)]
    elif task.get("engine") == "direct" and task.get("direct_agent"):
        source, agent = "task.direct_agent", task["direct_agent"]
        overrides = [k for k in ("direct_provider_id", "direct_model") if task.get(k)]
    elif task.get("engine") == "direct" and task.get("direct_provider_id"):
        source, agent = "task.direct_provider", "builtin"
    elif task.get("mode") == "manual" and task.get("implementer"):
        source, agent = "task.implementer", task["implementer"]
    else:
        source, agent = "automatic", ""
    return {"version": 1, "parameters": task_view(task), "selection": {
        "source": source, "agent": _safe(agent), "overrides": overrides,
        "state": "requested", "reason": "任务选择规则；实际可用性、降级和模型见步骤记录"}}


def agent_view(agent, *, resume=None):
    mode = str(agent.get("binding_mode") or "")
    source = agent.get("selection_source") or {
        "explicit": "binding.explicit", "auto": "binding.automatic",
        "cli_default": "cli.default"}.get(mode, "unknown")
    if agent.get("kind") == "mock" or agent.get("mode") == "mock":
        source = "mock"
    chain = []
    for item in (agent.get("call_chain") or [])[:24]:
        provider = item.get("provider") or {}
        chain.append({"provider_id": _safe(item.get("provider_id") or provider.get("id") or ""),
                      "model": _safe(item.get("model") or "")})
    provider = agent.get("provider") or agent.get("prov") or {}
    return {"agent": _safe(agent.get("id") or "builtin"),
            "transport": "builtin" if agent.get("prov") is not None else _safe(agent.get("kind") or "cli"),
            "binding_source": _safe(source), "model": _safe(agent.get("model") or ""),
            "provider_id": _safe(agent.get("provider_id") or provider.get("id") or ""),
            "reasoning_effort": _safe(agent.get("reasoning_effort") or ""),
            "call_chain": chain, "resume_requested": bool(resume),
            "sync_failed": bool(agent.get("runtime_config_sync_failed")),
            "reason": "解析后的调用配置；最终生效模型和降级尝试见 result"}


def result_view(result):
    attempts = [{"model": _safe(x.get("model") or ""),
                 "provider_id": _safe(x.get("provider_id") or ""), "ok": bool(x.get("ok"))}
                for x in (result.get("attempts") or [])[:32] if isinstance(x, dict)]
    return {"model": _safe(result.get("model") or ""),
            "provider_id": _safe(result.get("provider_id") or ""),
            "ok": bool(result.get("ok")), "attempts": attempts}


def run_view(run):
    frozen = (run.get("execution_snapshot") or {}).get("configuration") or {}
    return {"version": 1, "run_id": run.get("id") or "",
            "parameters": copy.deepcopy(frozen.get("parameters") or {}),
            "selection": copy.deepcopy(frozen.get("selection") or {"source": "unknown"}),
            "steps": [{"n": step.get("n"), "role": _safe(step.get("role") or ""),
                       "configuration": copy.deepcopy(step.get("configuration") or {})}
                      for step in run.get("steps") or []]}
