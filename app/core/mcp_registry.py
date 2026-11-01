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


# ────────────────────────────────────────────────────────────────────────────
# 反向导入：外部 CLI（claude/codex/qwen/gemini/opencode）已装的 MCP → CodeBee。
# 与 export/register 对称，但方向相反：读外部配置文件，把其 mcpServers /
# mcp_servers / mcp 段转成 CodeBee settings.mcp_servers 的 {name,command,...}
# 形态。纯读取 + 归一化，不写任何外部文件，env 一律脱敏（可关闭）。
# ────────────────────────────────────────────────────────────────────────────
_LABELS = {
    "claude": "Claude Code", "codex": "Codex CLI", "opencode": "OpenCode",
    "qwen": "Qwen Code", "gemini": "Gemini CLI",
}


def _sanitize_server_name(name, used):
    """外部 server 名 → 合规 CodeBee 名（[a-z0-9_-]{1,24}，无 __，去重加后缀）。"""
    base = re.sub(r"[^a-z0-9_-]", "", str(name or "").lower())
    base = base.replace("__", "_").strip("_-") or "mcp"
    base = base[:24]
    cand, n = base, 2
    while cand in used:
        suffix = "-%d" % n
        cand = base[: 24 - len(suffix)] + suffix
        n += 1
    used.add(cand)
    return cand


def _normalize_server(name, cfg, target):
    """外部单条 server 配置 → CodeBee 形（{name,command,args,env,url,headers}）。
    无 command 且无 url 的条目跳过（返回 None）。"""
    item = {}
    if target == "opencode":
        # opencode 形：{type:"local", command:[...], environment:{...}}
        cmd = cfg.get("command")
        if isinstance(cmd, list) and cmd and str(cmd[0]).strip():
            item["command"] = str(cmd[0]).strip()
            item["args"] = [str(x) for x in cmd[1:] if str(x).strip()]
        env = cfg.get("environment")
        if not isinstance(env, dict):
            env = cfg.get("env")
        if isinstance(env, dict):
            item["env"] = {str(k): str(v) for k, v in env.items()}
    else:
        cmd = cfg.get("command")
        args = cfg.get("args")
        args = [str(x) for x in args if str(x).strip()] if isinstance(args, list) else []
        if isinstance(cmd, str) and cmd.strip():
            item["command"] = cmd.strip()
            # Windows 常见包装「cmd /c <real> ...」：解包成真实命令，
            # 否则导入后 command=cmd args=[/c,npx,...]，语义对但可读性差。
            if item["command"].lower() == "cmd" and len(args) >= 2 and \
                    args[0].lower() in ("/c", "/k", "/cmd"):
                item["command"] = args[1]
                args = args[2:]
        if args:
            item["args"] = args
        if isinstance(cfg.get("env"), dict):
            item["env"] = {str(k): str(v) for k, v in cfg["env"].items()}
        if isinstance(cfg.get("url"), str) and cfg["url"].strip():
            item["url"] = cfg["url"].strip()
        if isinstance(cfg.get("headers"), dict):
            item["headers"] = {str(k): str(v)
                               for k, v in list(cfg["headers"].items())[:8]}
    if not item.get("command") and not item.get("url"):
        return None
    return item


def _cap(servers):
    """控制导入条目体量：args/env 值截断（与 parse_servers 的量级一致）。"""
    out = []
    for s in servers:
        item = dict(s)
        if isinstance(item.get("args"), list):
            item["args"] = [str(x)[:500] for x in item["args"]][:32]
        if isinstance(item.get("env"), dict):
            item["env"] = {str(k)[:64]: str(v)[:2000]
                           for k, v in list(item["env"].items())[:32]}
        if isinstance(item.get("headers"), dict):
            item["headers"] = {str(k): str(v)[:500]
                               for k, v in list(item["headers"].items())[:8]}
        out.append(item)
    return out


def _read_target_text(target):
    path = Path(os.path.expanduser(_TARGETS[target]["path"])).expanduser().resolve()
    if not path.exists():
        return None, os.path.expanduser(_TARGETS[target]["path"]), \
            "配置文件不存在（该智能体可能未安装）"
    try:
        return path.read_text(encoding="utf-8"), str(path), ""
    except OSError as exc:
        return None, str(path), "读取失败：%s" % exc


def import_from(target, redact=True):
    """外部 CLI 已装的 MCP → CodeBee 服务器清单（脱敏审计）。

    只读、不写任何外部文件。返回 {target,format,path,servers,error,redacted}：
      - 文件不存在 / 解析失败 → error 非空，servers 空
      - 配置存在但无 mcpServers 段 → error 空，servers 空（该智能体没装 MCP）
    """
    target = _target_key(target)
    if target not in _TARGETS:
        raise ValueError("不支持的 MCP 来源 CLI：%s" % target)
    fmt = _TARGETS[target]["format"]
    root = _TARGETS[target]["root"]
    text, path, err = _read_target_text(target)
    base = {"target": target, "format": fmt, "path": path,
            "servers": [], "error": err, "redacted": bool(redact)}
    if err:
        return base
    try:
        if fmt == "toml":
            import tomllib
            doc = tomllib.loads(text)
        else:
            doc = json.loads(text)
    except Exception as exc:
        base["error"] = "配置文件解析失败：%s" % exc
        return base
    section = doc.get(root) if isinstance(doc, dict) else None
    if not isinstance(section, dict):
        return base                       # 存在但没配 MCP：合法空结果
    used = set()
    servers = []
    for name, cfg in section.items():
        if not isinstance(cfg, dict):
            continue
        norm = _normalize_server(name, cfg, target)
        if not norm:
            continue
        norm["name"] = _sanitize_server_name(str(name), used)
        servers.append(norm)
    servers = _cap(servers)
    base["servers"] = [_redact(x) if redact else x for x in servers]
    return base


def importable_sources(redact=True):
    """各外部 CLI 的导入源清单：路径存在性 + 已装 MCP 数（只读探测）。

    单家读取失败不拖垮整表（error 单独标注）。前端据此渲染「从其它智能体
    导入」区：只展示 exists=True 的源，server_count>0 才给「导入」。
    """
    out = []
    for target, spec in _TARGETS.items():
        path = os.path.expanduser(spec["path"])
        exists = Path(path).exists()
        entry = {"id": target, "name": _LABELS.get(target, target),
                 "path": path, "format": spec["format"],
                 "exists": exists, "server_count": 0, "error": ""}
        if exists:
            try:
                data = import_from(target, redact=redact)
                entry["server_count"] = len(data.get("servers") or [])
                entry["error"] = data.get("error") or ""
            except Exception as exc:      # pragma: no cover - 防御
                entry["error"] = str(exc)
        out.append(entry)
    return out
