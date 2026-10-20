# -*- coding: utf-8 -*-
"""Deterministic capability surface for model-proposed tool calls."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    name: str
    zone: str
    group: str
    side_effects: tuple[str, ...] = ()


_READ = (
    "list_skills", "use_skill", "web_search", "read_task_output",
    "list_files", "read_file", "search_content", "find_files",
    "read_tool_output",
)
_WRITE = ("write_file", "edit_file", "append_file", "fs_manage",
          "create_task", "run_command")
_SPECS = {name: ActionSpec(name, "green", "read") for name in _READ}
_SPECS.update({name: ActionSpec(name, "yellow", "write", ("workspace",))
               for name in _WRITE})


def spec_for(name: str) -> ActionSpec | None:
    name = str(name or "").strip()
    if name.startswith("mcp__") and name.count("__") >= 2:
        return ActionSpec(name, "yellow", "mcp", ("external_process",))
    return _SPECS.get(name)


def catalog() -> tuple[ActionSpec, ...]:
    return tuple(_SPECS.values())


def preflight(name: str, *, args: dict | None = None,
              sandbox: dict | None = None, known: bool = True) -> dict:
    """Return a pure decision envelope before a tool implementation runs."""
    sandbox = sandbox if isinstance(sandbox, dict) else {}
    name = str(name or "").strip()
    spec = spec_for(name)
    if not known or spec is None:
        return {"decision": "deny", "reason": "unknown_action", "zone": "yellow"}
    args = args if isinstance(args, dict) else {}
    if name == "fs_manage" and str(args.get("action") or "").strip().lower() == "delete":
        return {"decision": "deny", "reason": "red_line_delete", "zone": "red"}
    disabled = {str(item).strip() for item in (sandbox.get("disabled_tools") or [])}
    if name in disabled:
        return {"decision": "deny", "reason": "disabled_action", "zone": spec.zone}
    allowlist = sandbox.get("allowed_tools")
    allowed = {str(x).strip() for x in (allowlist or [])}
    bare_name = name.split("__", 2)[2] if name.startswith("mcp__") and name.count("__") >= 2 else name
    if allowlist is not None and name not in allowed and bare_name not in allowed:
        return {"decision": "deny", "reason": "action_not_allowed", "zone": spec.zone}
    if spec.zone == "red":
        return {"decision": "deny", "reason": "red_line", "zone": "red"}
    if spec.zone == "yellow" and sandbox.get("require_approval"):
        approved = {str(x).strip() for x in (sandbox.get("approved_tools") or [])}
        if name not in approved and bare_name not in approved:
            return {"decision": "needConfirm", "reason": "approval_required", "zone": "yellow"}
    return {"decision": "autoExecute", "reason": "allowed", "zone": spec.zone}
