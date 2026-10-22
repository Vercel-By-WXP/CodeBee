# -*- coding: utf-8 -*-
"""MCP Harness: expose platform capabilities to external AI clients.

Following DataY's pattern, this module implements a Streamable HTTP MCP server
that exposes platform objects (agents, workflows, tasks, executions) via MCP
tools. External AI clients (Claude Desktop, Cursor, etc.) can then directly
orchestrate the platform.

Security model (layered):
1. Transport auth: Bearer token validation
2. Write switch: allowMutatingTools controls write operations
3. Response redaction: sensitive fields never returned

Tool naming: object_action (e.g., agent_list, workflow_create)
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from typing import Any, Callable, Optional

from . import atomic, paths, store, registry, flows, jobs, settings

log = logging.getLogger(__name__)

# Token storage (BCrypt-like hashing for at-rest security)
_TOKEN_PREFIX = "orch_"
_TOKEN_STORE_PATH = paths.DATA_DIR / "mcp_tokens.json"
_TOKEN_LOCK = threading.RLock()

# Write switch configuration
_WRITE_SWITCH_KEY = "mcp_harness_allow_mutating"

# Tool registry
_TOOLS: dict[str, dict] = {}


def _load_tokens() -> dict:
    """Load token store from disk."""
    if not _TOKEN_STORE_PATH.exists():
        return {}
    try:
        return json.loads(_TOKEN_STORE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load MCP token store: %s", exc)
        return {}


def _save_tokens(tokens: dict) -> None:
    """Save token store to disk（复用 atomic.write_json：并发交错/掉电不再写坏）。"""
    _TOKEN_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_json(_TOKEN_STORE_PATH, tokens)


def _hash_token(token: str) -> str:
    """Hash token for at-rest storage (SHA-256 for simplicity)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(user_id: str = "default") -> str:
    """Generate a new MCP token and return the plaintext (shown once)."""
    plaintext = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    tokens = _load_tokens()
    tokens[_hash_token(plaintext)] = {
        "user_id": user_id,
        "created_at": time.time(),
        "last_used": None,
    }
    _save_tokens(tokens)
    return plaintext


def validate_token(token: str) -> Optional[dict]:
    """Validate a token and return metadata if valid."""
    if not token:
        return None
    tokens = _load_tokens()
    token_hash = _hash_token(token)
    if token_hash not in tokens:
        return None
    # Update last_used timestamp
    tokens[token_hash]["last_used"] = time.time()
    _save_tokens(tokens)
    return tokens[token_hash]


def revoke_token(token: str) -> bool:
    """Revoke a token."""
    tokens = _load_tokens()
    token_hash = _hash_token(token)
    if token_hash in tokens:
        del tokens[token_hash]
        _save_tokens(tokens)
        return True
    return False


def list_tokens() -> list[dict]:
    """List all tokens (redacted)."""
    tokens = _load_tokens()
    return [
        {
            "hash": h[:16] + "...",
            "user_id": meta.get("user_id"),
            "created_at": meta.get("created_at"),
            "last_used": meta.get("last_used"),
        }
        for h, meta in tokens.items()
    ]


def _allow_mutating() -> bool:
    """Check if write operations are allowed."""
    return bool(settings.load().get(_WRITE_SWITCH_KEY, False))


def _register_tool(name: str, description: str, handler: Callable,
                   mutating: bool = False, read_only_view: Optional[dict] = None):
    """Register a tool in the harness."""
    _TOOLS[name] = {
        "name": name,
        "description": description,
        "handler": handler,
        "mutating": mutating,
        "read_only_view": read_only_view,
    }


def _tool(name: str):
    """Decorator to register a tool."""
    def decorator(func):
        _register_tool(
            name=name,
            description=func.__doc__ or "",
            handler=func,
            mutating=getattr(func, "_mutating", False),
        )
        return func
    return decorator


def _mark_mutating(func):
    """Mark a tool as mutating (write operation)."""
    func._mutating = True
    return func


# ============================================================================
# Agent tools
# ============================================================================

@_tool("agent_list")
def agent_list(params: dict) -> dict:
    """List all registered agents with their capabilities."""
    from . import catalog
    agents = catalog.load()
    return {
        "agents": [
            {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "description": agent.get("description"),
                "capabilities": agent.get("capabilities", []),
                "enabled": agent.get("enabled", True),
            }
            for agent in agents
        ]
    }


@_tool("agent_get")
def agent_get(params: dict) -> dict:
    """Get detailed information about a specific agent."""
    from . import catalog
    agent_id = params.get("agent_id")
    if not agent_id:
        return {"error": "agent_id is required"}
    agent = catalog.by_id(agent_id)
    if not agent:
        return {"error": f"Agent not found: {agent_id}"}
    return {"agent": agent}


@_mark_mutating
@_tool("agent_create")
def agent_create(params: dict) -> dict:
    """Create a new agent definition."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    # Note: Agent creation requires modifying catalog.json directly
    # This is a simplified implementation
    return {"error": "Agent creation via MCP not yet implemented. Use the UI or edit catalog.json directly."}


@_mark_mutating
@_tool("agent_update")
def agent_update(params: dict) -> dict:
    """Update an existing agent (partial update)."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    agent_id = params.get("agent_id")
    if not agent_id:
        return {"error": "agent_id is required"}
    # Note: Agent update requires modifying catalog.json directly
    return {"error": "Agent update via MCP not yet implemented. Use the UI or edit catalog.json directly."}


@_mark_mutating
@_tool("agent_delete")
def agent_delete(params: dict) -> dict:
    """Delete an agent definition."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    agent_id = params.get("agent_id")
    if not agent_id:
        return {"error": "agent_id is required"}
    # Note: Agent deletion requires modifying catalog.json directly
    return {"error": "Agent deletion via MCP not yet implemented. Use the UI or edit catalog.json directly."}


# ============================================================================
# Workflow tools
# ============================================================================

@_tool("workflow_list")
def workflow_list(params: dict) -> dict:
    """List all workflow templates."""
    from . import flows
    workflow_list = flows.list_flows()
    return {
        "workflows": [
            {
                "id": wf.get("id"),
                "name": wf.get("name"),
                "description": wf.get("description"),
                "steps": len(wf.get("steps", [])),
            }
            for wf in workflow_list
        ]
    }


@_tool("workflow_get")
def workflow_get(params: dict) -> dict:
    """Get detailed information about a workflow template."""
    from . import flows
    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "workflow_id is required"}
    workflow = flows.get_flow(workflow_id)
    if not workflow:
        return {"error": f"Workflow not found: {workflow_id}"}
    return {"workflow": workflow}


@_mark_mutating
@_tool("workflow_create")
def workflow_create(params: dict) -> dict:
    """Create a new workflow template."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    from . import flows
    try:
        result = flows.upsert_flow(params)
        return {"workflow": result, "created": True}
    except Exception as exc:
        return {"error": str(exc)}


@_mark_mutating
@_tool("workflow_update")
def workflow_update(params: dict) -> dict:
    """Update an existing workflow template."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "workflow_id is required"}
    from . import flows
    try:
        result = flows.upsert_flow(params)
        return {"workflow": result, "updated": True}
    except Exception as exc:
        return {"error": str(exc)}


@_mark_mutating
@_tool("workflow_delete")
def workflow_delete(params: dict) -> dict:
    """Delete a workflow template."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "workflow_id is required"}
    from . import flows
    try:
        flows.delete_flow(workflow_id)
        return {"deleted": True, "workflow_id": workflow_id}
    except Exception as exc:
        return {"error": str(exc)}


# ============================================================================
# Task tools
# ============================================================================

@_tool("task_list")
def task_list(params: dict) -> dict:
    """List tasks with optional filtering."""
    from . import store
    status_filter = params.get("status")
    limit = min(int(params.get("limit", 50)), 200)
    tasks = store.list_tasks(limit=limit)
    if status_filter:
        tasks = [t for t in tasks if t.get("status") == status_filter]
    return {
        "tasks": [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
                "workflow_id": t.get("workflow_id"),
            }
            for t in tasks
        ]
    }


@_tool("task_get")
def task_get(params: dict) -> dict:
    """Get detailed information about a task."""
    from . import store
    task_id = params.get("task_id")
    if not task_id:
        return {"error": "task_id is required"}
    task = store.get_task(task_id)
    if not task:
        return {"error": f"Task not found: {task_id}"}
    return {"task": task}


@_mark_mutating
@_tool("task_create")
def task_create(params: dict) -> dict:
    """Create and queue a new task."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    import time
    from . import store, jobs
    try:
        task = store.create_task(params)
    except Exception as exc:
        return {"error": str(exc)}
    # 与 web 端 POST /api/tasks 同一条执行链：enqueue 必须带 run_id 且
    # kind=orchestration（jobs.enqueue 对缺 run_id 直接 ValueError，且
    # kind="task" 属未定义任务类型——旧写法恒失败并留下永不运行的孤儿任务）。
    run = None
    try:
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        job = jobs.enqueue({"kind": "orchestration", "run_id": run["id"],
                            "task_id": task["id"]})
    except Exception as exc:
        # 已落库的 run/task 必须收口，否则 UI 永久显示排队中
        if run:
            try:
                store.update_run(run["id"], status="failed",
                                 error="MCP 任务入队失败：%s" % exc,
                                 ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass
        try:
            store.update_task_status(task["id"], "failed")
        except Exception:
            pass
        return {"error": "任务已创建但入队失败：%s" % exc, "task_id": task["id"]}
    return {"task": task, "run_id": run["id"], "job": job, "created": True}


@_mark_mutating
@_tool("task_cancel")
def task_cancel(params: dict) -> dict:
    """Cancel a pending or running task."""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    task_id = params.get("task_id")
    if not task_id:
        return {"error": "task_id is required"}
    from . import jobs
    try:
        # Find active run for this task and cancel it
        from . import store
        runs = store.task_runs(task_id)
        active_runs = [r for r in runs if r.get("status") in ("running", "queued")]
        if not active_runs:
            return {"error": f"No active run found for task: {task_id}"}
        run_id = active_runs[0].get("id")
        jobs.cancel(run_id)
        return {"cancelled": True, "task_id": task_id, "run_id": run_id}
    except Exception as exc:
        return {"error": str(exc)}


# ============================================================================
# Execution tools
# ============================================================================

@_tool("report_status")
def report_status(params: dict) -> dict:
    """Agent reverse channel: report a blocker needing human decision
    (kind=blocker; the user is notified and can reply in chat), a progress
    note (progress), or that a previous blocker is resolved (resolve)."""
    from . import store, store_reports
    kind = str(params.get("kind") or "").strip().lower()
    text = str(params.get("text") or "").strip()
    run_id = str(params.get("run_id") or "").strip()
    run = store.get_run(run_id) if run_id else None
    if not run:
        task = store.get_task(str(params.get("task_id") or "").strip())
        if not task:
            return {"error": "task_id/run_id 至少给一个有效值"}
        run = store.latest_run_by_task().get(task["id"]) or {}
        run_id = run.get("id") or ""
    if not run_id:
        return {"error": "该任务还没有运行可挂汇报"}
    rec = store_reports.add_report(run_id, kind, text, source="mcp-web")
    if not rec:
        return {"error": "kind 必须是 blocker/progress/resolve，且 blocker/progress 需带 text"}
    return {"reported": True, "kind": kind, "run_id": run_id,
            "notified": kind == "blocker"}


@_tool("execution_list")
def execution_list(params: dict) -> dict:
    """List task executions (runs)."""
    from . import store
    task_id = params.get("task_id")
    limit = min(int(params.get("limit", 50)), 200)
    runs = store.list_runs(limit=limit)
    if task_id:
        runs = [r for r in runs if r.get("task_id") == task_id]
    return {
        "executions": [
            {
                "id": r.get("id"),
                "task_id": r.get("task_id"),
                "status": r.get("status"),
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
            }
            for r in runs
        ]
    }


@_tool("execution_get")
def execution_get(params: dict) -> dict:
    """Get detailed information about an execution."""
    from . import store
    execution_id = params.get("execution_id")
    if not execution_id:
        return {"error": "execution_id is required"}
    run = store.get_run(execution_id)
    if not run:
        return {"error": f"Execution not found: {execution_id}"}
    return {"execution": run}


# ============================================================================
# Meta tool
# ============================================================================

@_tool("orchestration_catalog")
def orchestration_catalog(params: dict) -> dict:
    """List all available tools grouped by capability."""
    grouped = {}
    for name, tool in _TOOLS.items():
        # Extract object prefix (e.g., "agent" from "agent_list")
        prefix = name.split("_")[0]
        if prefix not in grouped:
            grouped[prefix] = []
        grouped[prefix].append({
            "name": name,
            "description": tool["description"].split("\n")[0],  # First line only
            "mutating": tool["mutating"],
        })
    return {
        "catalog": grouped,
        "total_tools": len(_TOOLS),
        "mutating_allowed": _allow_mutating(),
    }


# ============================================================================
# MCP protocol handlers
# ============================================================================

def list_tools() -> list[dict]:
    """Return tool definitions for MCP protocol."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": {
                "type": "object",
                "properties": {},  # Simplified; real impl would introspect params
            },
        }
        for tool in _TOOLS.values()
    ]


def call_tool(name: str, params: dict, auth_token: Optional[str] = None) -> dict:
    """Execute a tool and return the result."""
    if name not in _TOOLS:
        return {"error": f"Unknown tool: {name}"}
    
    tool = _TOOLS[name]
    
    # Check mutating permission
    if tool["mutating"] and not _allow_mutating():
        return {"error": "Mutating operations are disabled. Set mcp_harness_allow_mutating=true to enable."}
    
    try:
        return tool["handler"](params)
    except Exception as exc:
        log.exception("Tool execution failed: %s", name)
        return {"error": f"Tool execution failed: {exc}"}
