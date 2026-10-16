# -*- coding: utf-8 -*-
"""Curated MCP server catalog and safe configuration installer.

The catalog deliberately installs declarations, not arbitrary plugin code. A
server becomes runnable only after the user explicitly commits its config and
the normal MCP sandbox still validates and isolates the process at runtime.
"""
from __future__ import annotations

import copy
import json
import re

from . import mcp_client, settings

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")

# These are intentionally boring, well-known stdio entry points.  The
# package manager may need to fetch them on first use; CodeBee never executes
# that package manager as part of a catalog install.
_CATALOG = {
    "filesystem": {
        "name": "Filesystem",
        "description": "读写任务工作目录内的文件（由任务沙箱限制范围）。",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "{workdir}"],
        "options": {"workdir": {"required": True, "description": "允许访问的绝对目录"}},
        "package": "@modelcontextprotocol/server-filesystem",
        "package_manager": "npm",
    },
    "fetch": {
        "name": "Fetch",
        "description": "抓取公开网页并返回文本。",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "options": {},
        "package": "mcp-server-fetch",
        "package_manager": "uv",
    },
    "time": {
        "name": "Time",
        "description": "查询时区和时间转换。",
        "command": "uvx",
        "args": ["mcp-server-time"],
        "options": {},
        "package": "mcp-server-time",
        "package_manager": "uv",
    },
    "memory": {
        "name": "Memory",
        "description": "提供轻量知识图谱记忆服务。",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "options": {},
        "package": "@modelcontextprotocol/server-memory",
        "package_manager": "npm",
    },
    "github": {
        "name": "GitHub",
        "description": "访问 GitHub API；令牌只从本机环境变量读取。",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "options": {},
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        "package": "@modelcontextprotocol/server-github",
        "package_manager": "npm",
    },
}


def _catalog_item(server_id):
    key = str(server_id or "").strip().lower()
    if key not in _CATALOG:
        raise ValueError("未知 MCP 目录项：%s" % key)
    return key, copy.deepcopy(_CATALOG[key])


def _configured():
    raw = settings.load().get("mcp_servers") or ""
    servers, error = mcp_client.parse_servers(raw)
    if error:
        raise ValueError(error)
    return list(servers or [])


def _safe_name(value):
    name = str(value or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError("MCP 名称必须匹配 [a-z0-9_-]{1,24}")
    return name


def _redact(server):
    out = copy.deepcopy(server)
    env = out.get("env") or {}
    out["env"] = {key: "<redacted>" if value else ""
                  for key, value in env.items()}
    return out


def _render(key, spec, options=None, name=None):
    options = dict(options or {})
    option_specs = spec.get("options") or {}
    for option, meta in option_specs.items():
        if meta.get("required") and not str(options.get(option) or "").strip():
            raise ValueError("MCP %s 需要参数 %s" % (key, option))
    unknown = set(options) - set(option_specs) - {"env"}
    if unknown:
        raise ValueError("MCP %s 不支持参数：%s" % (key, ",".join(sorted(unknown))))
    rendered_name = _safe_name(name or "cb_" + key)
    args = []
    for arg in spec.get("args") or []:
        value = str(arg)
        for option in option_specs:
            value = value.replace("{" + option + "}", str(options.get(option) or ""))
        args.append(value)
    env = dict(spec.get("env") or {})
    extra_env = options.get("env") or {}
    if not isinstance(extra_env, dict):
        raise ValueError("env 必须是对象")
    for env_key, env_value in extra_env.items():
        env_key = str(env_key).strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", env_key):
            raise ValueError("非法环境变量名：%s" % env_key)
        env[env_key] = str(env_value)
    return {"name": rendered_name, "command": spec["command"],
            "args": args, "env": env}


def catalog():
    """Return catalog metadata plus whether each default name is configured."""
    configured = {x.get("name") for x in _configured()}
    rows = []
    for key, spec in _CATALOG.items():
        rows.append({"id": key, "name": spec["name"],
                     "description": spec["description"],
                     "package": spec.get("package"),
                     "package_manager": spec.get("package_manager"),
                     "options": spec.get("options") or {},
                     "default_name": "cb_" + key,
                     "configured": "cb_" + key in configured})
    return rows


def install(server_id, *, options=None, name=None, commit=False):
    """Preview or commit a catalog declaration into settings.mcp_servers."""
    key, spec = _catalog_item(server_id)
    server = _render(key, spec, options=options, name=name)
    configured = _configured()
    replaced = False
    merged = []
    for item in configured:
        if item.get("name") == server["name"]:
            merged.append(server)
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(server)
    result = {"id": key, "name": server["name"], "commit": bool(commit),
              "changed": not replaced or merged != configured,
              "server": _redact(server), "package": spec.get("package"),
              "package_manager": spec.get("package_manager"),
              "package_install_required": True}
    if not commit:
        return result
    if len(merged) > mcp_client.MAX_SERVERS:
        raise ValueError("最多 %d 个 MCP 服务器" % mcp_client.MAX_SERVERS)
    _, error = settings.save({"mcp_servers": json.dumps(merged, ensure_ascii=False)})
    if error:
        raise ValueError(error)
    return result


def remove(name, *, commit=False):
    """Preview or commit removal of one configured MCP declaration."""
    name = _safe_name(name)
    configured = _configured()
    remaining = [x for x in configured if x.get("name") != name]
    result = {"name": name, "commit": bool(commit),
              "changed": len(remaining) != len(configured)}
    if not commit:
        return result
    _, error = settings.save({"mcp_servers": json.dumps(remaining, ensure_ascii=False)})
    if error:
        raise ValueError(error)
    return result


def state():
    return {"catalog": catalog(), "configured": [_redact(x) for x in _configured()]}
