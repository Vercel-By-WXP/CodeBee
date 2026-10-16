# -*- coding: utf-8 -*-
"""Explicit, auditable MCP registration for external CLIs.

The built-in agent consumes the canonical stdio list directly.  Other CLIs use
different on-disk formats, so registration is an opt-in translation step.  The
module never executes a server, never imports plugin code, and returns redacted
audit data so environment secrets cannot be echoed by an API response.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path

from . import mcp_client, paths, plugins, settings

_SECRET = re.compile(r"(key|token|secret|password|passwd|credential|auth)", re.I)
_TARGETS = {
    "claude": {"path": "~/.claude.json", "format": "json", "root": "mcpServers",
               "aliases": ["claude-code"]},
    "codex": {"path": "~/.codex/config.toml", "format": "toml", "root": "mcp_servers",
              "aliases": ["codex-cli"]},
    "opencode": {"path": "~/.config/opencode/opencode.json", "format": "json", "root": "mcp",
                  "aliases": ["mimo-code"]},
    "qwen": {"path": "~/.qwen/settings.json", "format": "json", "root": "mcpServers",
             "aliases": ["qwencode"]},
    "gemini": {"path": "~/.gemini/settings.json", "format": "json", "root": "mcpServers",
                "aliases": ["gemini-cli"]},
}
_ALIASES = {alias: name for name, spec in _TARGETS.items()
            for alias in spec.get("aliases") or []}


def _target_key(target):
    target = str(target or "").lower().strip()
    return _ALIASES.get(target, target)


def _configured():
    raw = plugins.merged_servers_text(settings.load().get("mcp_servers"))
    servers, error = mcp_client.parse_servers(raw)
    if error:
        raise ValueError(error)
    return servers or []


def targets():
    return {name: {k: (list(v) if isinstance(v, list) else v)
                   for k, v in spec.items()}
            for name, spec in _TARGETS.items()}


def _redact(server):
    out = copy.deepcopy(server)
    env = out.get("env") or {}
    out["env"] = {k: ("<redacted>" if _SECRET.search(str(k)) else v)
                  for k, v in env.items()}
    return out


def _redact_value(value, key=""):
    if isinstance(value, dict):
        return {str(k): ("<redacted>" if _SECRET.search(str(k))
                         else _redact_value(v, str(k))) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, key) for v in value]
    return value


def _toml_quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def _toml_fragment(servers):
    lines = ["# codebee:mcp:start"]
    for server in servers:
        name = str(server["name"]).replace('"', "")
        lines.append("[mcp_servers.%s]" % name)
        lines.append("command = %s" % _toml_quote(server["command"]))
        args = ", ".join(_toml_quote(x) for x in server.get("args") or [])
        lines.append("args = [%s]" % args)
        env = server.get("env") or {}
        if env:
            lines.append("[mcp_servers.%s.env]" % name)
            for key, value in env.items():
                safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", str(key))
                lines.append("%s = %s" % (safe_key, _toml_quote(value)))
    lines.append("# codebee:mcp:end")
    return "\n".join(lines) + "\n"


def _replace_toml_block(raw, fragment):
    begin, end = "# codebee:mcp:start", "# codebee:mcp:end"
    pattern = re.compile(re.escape(begin) + r"[\s\S]*?" + re.escape(end) + r"\s*\n?", re.M)
    if pattern.search(raw):
        return pattern.sub(fragment, raw, count=1)
    return raw.rstrip() + "\n\n" + fragment


def _json_document(target, servers, existing=None):
    doc = dict(existing) if isinstance(existing, dict) else {}
    root = _TARGETS[target]["root"]
    current = doc.get(root)
    if not isinstance(current, dict):
        current = {}
    for server in servers:
        if target == "opencode":
            item = {"type": "local", "command": [server["command"]] + list(server.get("args") or [])}
            if server.get("env"):
                item["environment"] = dict(server["env"])
        else:
            item = {"command": server["command"], "args": list(server.get("args") or [])}
            if server.get("env"):
                item["env"] = dict(server["env"])
        current[server["name"]] = item
    doc[root] = current
    return doc


def export(target, *, redact=True):
    """Return the translated config and a secret-safe audit manifest."""
    target = _target_key(target)
    if target not in _TARGETS:
        raise ValueError("不支持的 MCP 目标 CLI：%s" % target)
    servers = _configured()
    visible = [_redact(x) if redact else x for x in servers]
    if _TARGETS[target]["format"] == "toml":
        rendered = _toml_fragment(visible)
    else:
        rendered = json.dumps(_json_document(target, visible), ensure_ascii=False, indent=2)
    return {"target": target, "format": _TARGETS[target]["format"], "servers": visible,
            "config": rendered,
            "path": os.path.expanduser(_TARGETS[target]["path"]),
            "redacted": bool(redact)}


def register(target, path_override=None, *, dry_run=True):
    """Register configured servers after an explicit caller opt-in.

    Writes atomically and preserves an adjacent ``.codebee.bak``.  The return
    value contains only redacted server data; ``dry_run`` is the safe default.
    """
    target = _target_key(target)
    if target not in _TARGETS:
        raise ValueError("不支持的 MCP 目标 CLI：%s" % target)
    servers = _configured()
    path = Path(path_override or os.path.expanduser(_TARGETS[target]["path"])).expanduser().resolve()
    # A custom path is an explicit user choice (needed for portable CLI
    # profiles and Windows installations).  We still never write it during a
    # preview, and committed writes always create a sibling backup first.
    existing = {}
    existing_text = ""
    if path.exists():
        try:
            if _TARGETS[target]["format"] == "toml":
                existing_text = path.read_text(encoding="utf-8")
            else:
                existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("目标 CLI 配置不是可合并的 JSON/TOML：%s" % exc)
    merged = (_replace_toml_block(existing_text, _toml_fragment(servers))
              if _TARGETS[target]["format"] == "toml"
              else _json_document(target, servers, existing))
    result = {"target": target, "format": _TARGETS[target]["format"],
              "path": str(path), "dry_run": bool(dry_run),
              "servers": [_redact(x) for x in servers], "changed": False}
    if dry_run:
        if _TARGETS[target]["format"] == "toml":
            result["config"] = _toml_fragment([_redact(x) for x in servers])
        else:
            result["config"] = json.dumps(
                _redact_value(_json_document(target, [_redact(x) for x in servers], existing)),
                ensure_ascii=False, indent=2)
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".codebee.bak")
        backup.write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(prefix=".codebee-mcp-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            if _TARGETS[target]["format"] == "toml":
                handle.write(merged)
            else:
                json.dump(merged, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    result["changed"] = True
    return result
