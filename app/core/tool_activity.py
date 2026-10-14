"""Extract display-only tool activity from CLI logs, without executing log content."""
from __future__ import annotations

import json
import re


def _text(value, limit=6000):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value[:limit]


def _kind(name):
    name = name.lower()
    if any(word in name for word in ("read", "list", "glob")):
        return "read"
    if any(word in name for word in ("command", "shell", "bash", "exec", "terminal")):
        return "command"
    if any(word in name for word in ("write", "edit", "patch", "file_change")):
        return "edit"
    if any(word in name for word in ("search", "grep", "fetch", "browse")):
        return "search"
    return "tool"


def parse_tool_activity(text, step_status="", limit=100, allow_text_activity=False):
    """Merge Codex/Claude/OpenCode events by call ID; tolerate partial JSONL tails.

    Missing results stay unknown after a step ends: a tool request alone is not
    evidence of success. Text logs from the builtin agent are also supported.
    """
    records = {}
    transcript = []

    def put(key, name, args=None, output=None, status=None, exit_code=None):
        key = str(key or "line-%d" % len(records))
        if key not in records:
            transcript.append({"kind": "tool", "id": key})
        record = records.setdefault(key, {"id": key, "name": str(name or "tool"),
                                          "input": "", "output": "", "status": "unknown"})
        if name:
            record["name"] = str(name)
        if args is not None:
            record["input"] = _text(args)
        if output is not None:
            record["output"] = _text(output)
        if status:
            record["status"] = status
        if exit_code is not None:
            record["exit_code"] = exit_code

    for index, line in enumerate(str(text or "").splitlines()):
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            event = None
        if not isinstance(event, dict):
            if not allow_text_activity:
                continue
            # Builtin tools report their completed calls as ordinary log lines.
            match = re.match(r"^\[工具\]\s+(\S+)\s+→\s*(.*)$", line)
            if match:
                put("line-%d" % index, match[1], output=match[2])
            else:
                match = re.match(r"^【活动】(?!请求工具:)(\S+)\s*(.*)$", line)
                if match:
                    put("line-%d" % index, match[1], args=match[2])
            continue
        typ = event.get("type")
        item = event.get("item")
        if typ in ("item.started", "item.updated", "item.completed") and isinstance(item, dict):
            kind = item.get("type")
            if kind == "agent_message" and typ == "item.completed":
                transcript.append({"kind": "message", "text": _text(item.get("text"))})
            if kind not in ("command_execution", "mcp_tool_call", "file_change", "web_search"):
                continue
            status = str(item.get("status") or "")
            code = item.get("exit_code")
            failed = status in ("failed", "error") or (code is not None and code != 0)
            state = "failed" if failed else ("completed" if typ == "item.completed" else "running")
            if kind == "command_execution":
                name, args, output = "command", item.get("command"), item.get("aggregated_output")
            elif kind == "mcp_tool_call":
                name = ".".join(str(x) for x in (item.get("server"), item.get("tool")) if x) or "MCP"
                args, output = item.get("arguments"), item.get("error") or item.get("result")
                if item.get("error"):
                    state = "failed"
            elif kind == "file_change":
                name, args, output = "file_change", item.get("changes"), None
            else:
                name, args, output = "web_search", item.get("query"), None
            put(item.get("id") or "line-%d" % index, name, args, output, state, code)
        elif typ in ("assistant", "user"):
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    put(block.get("id"), block.get("name"), block.get("input"), status="running")
                elif block.get("type") == "tool_result":
                    put(block.get("tool_use_id"), None, output=block.get("content"),
                        status="failed" if block.get("is_error") else "completed")
                elif block.get("type") == "text" and typ == "assistant":
                    transcript.append({"kind": "message", "text": _text(block.get("text"))})
        elif typ in ("tool_use", "tool_result"):
            # OpenCode JSON events carry tool state in part.state.
            part = event.get("part")
            if not isinstance(part, dict):
                continue
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            status = state.get("status")
            normalized = {"completed": "completed", "error": "failed",
                          "running": "running", "pending": "running"}.get(status, "unknown")
            put(part.get("callID") or part.get("id"), part.get("tool"),
                state.get("input"), state.get("error") or state.get("output"), normalized)

    rows = list(records.values())
    for record in rows:
        record["kind"] = _kind(record["name"])
        if record["status"] == "running" and step_status not in ("running", "queued"):
            record["status"] = "unknown"
        args = record["input"]
        try:
            parsed = json.loads(args)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            args = next((_text(parsed[key]) for key in ("command", "cmd", "path", "file_path", "query", "url")
                         if parsed.get(key)), args)
        record["summary"] = " ".join((args or record["name"]).split())[:240]
    kept_ids = {record["id"] for record in rows[-limit:]}
    transcript = [block for block in transcript
                  if block["kind"] == "message" or block["id"] in kept_ids]
    return {"activities": rows[-limit:], "transcript": transcript[-200:],
            "activities_truncated": str(text or "").startswith("...(已截断)...") or len(rows) > limit}
