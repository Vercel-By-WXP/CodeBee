# -*- coding: utf-8 -*-
"""内置智能体的 MCP 客户端（Model Context Protocol，stdio 形态）。

零依赖：JSON-RPC 2.0 走子进程 stdin/stdout（按行分帧——MCP stdio 传输约定）。
每次工具调用重新拉起服务器进程：没有长驻会话要管，冷启动成本对内置智能体
的低频工具循环可接受；进程用完即关（finally 杀树），绝不留孤儿。

配置（settings.mcp_servers，JSON 数组字符串，≤4 个服务器）：
  [{"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-fs", "C:/x"],
    "env": {"KEY": "v"}}]
name 用于工具名前缀（mcp__<name>__<tool>），必须 [a-z0-9_-]。

安全口径：服务器命令是用户在本机自己配置的（与内置智能体 run_command 同一
自主权级别）；本模块只做传输与解析，不校验工具语义。工具清单按服务器缓存
（TTL 300s）——每次对话请求重建 tools 列表时不能每次都冷启动全量服务器。
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import threading
import time

_LOCK = threading.RLock()
_TOOLS_CACHE = {}          # server_key → (ts, tools)
_TOOLS_TTL = 300.0

MAX_SERVERS = 4
MAX_MCP_TOOLS = 24         # 全部服务器合计注入内置智能体的工具数上限
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
_INIT_TIMEOUT = 15.0
_CALL_TIMEOUT = 60.0
_CALL_TIMEOUT_MAX = 300.0

_CLIENT_INFO = {"name": "CodeBee", "version": "1.0"}


def invalidate_tools_cache():
    """Drop discovered MCP tools after a server configuration change."""
    with _LOCK:
        _TOOLS_CACHE.clear()


def parse_servers(text):
    """settings.mcp_servers 文本 → 校验后的服务器配置列表。返回 (servers, 错误)。"""
    raw = str(text or "").strip()
    if not raw:
        return [], None
    try:
        arr = json.loads(raw)
    except Exception:
        return None, "mcp_servers 不是合法 JSON"
    if not isinstance(arr, list):
        return None, "mcp_servers 必须是数组"
    if len(arr) > MAX_SERVERS:
        return None, "最多 %d 个 MCP 服务器" % MAX_SERVERS
    out = []
    for item in arr:
        if not isinstance(item, dict):
            return None, "MCP 服务器配置必须是对象"
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not _NAME_RE.match(name):
            return None, "服务器名 %r 只能是小写字母/数字/-/_（≤24 位）" % name[:24]
        if not command:
            return None, "服务器 %s 缺 command" % name
        args = item.get("args") if isinstance(item.get("args"), list) else []
        args = [str(a) for a in args][:16]
        env = item.get("env") if isinstance(item.get("env"), dict) else {}
        env = {str(k): str(v) for k, v in list(env.items())[:16]}
        out.append({"name": name, "command": command, "args": args, "env": env})
    return out, None


class _Session:
    """一次性的 MCP 服务器会话：spawn → initialize → （list/call）→ 杀树。"""

    def __init__(self, server):
        self.server = server
        self.proc = None
        self.lines = None       # reader 线程产出的行队列
        self.err = None

    def _pump_err(self):
        try:
            for _ in self.proc.stderr:
                pass
        except Exception:
            pass

    def _pump_out(self):
        try:
            for raw in self.proc.stdout:
                self.lines.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except Exception:
            pass
        finally:
            self.lines.put(None)      # EOF 哨兵

    def __enter__(self):
        try:
            self.proc = subprocess.Popen(
                [self.server["command"]] + list(self.server.get("args") or []),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False,
                env={**os.environ, **(self.server.get("env") or {})})
        except Exception as e:
            raise RuntimeError("MCP 服务器启动失败: %s" % e)
        import queue as _queue
        self.lines = _queue.Queue()
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": _CLIENT_INFO}, timeout=_INIT_TIMEOUT)
        self._notify("notifications/initialized")
        return self

    def __exit__(self, *exc):
        try:
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
        finally:
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass
        return False

    def _send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method, params, timeout):
        """单请求单响应；忽略通知与未知 id 的服务端请求。"""
        rid = id({})
        self._send({"jsonrpc": "2.0", "id": 1 if method == "initialize" else rid,
                    "method": method, "params": params or {}})
        deadline = time.time() + max(1.0, timeout)
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise RuntimeError("%s 超时（%.0fs）" % (method, timeout))
            try:
                line = self.lines.get(timeout=remain)
            except Exception:
                raise RuntimeError("%s 超时（%.0fs）" % (method, timeout))
            if line is None:
                raise RuntimeError("MCP 服务器提前退出")
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("id") is None:
                continue                     # 通知/服务端请求：不匹配本调用
            if msg.get("id") != (1 if method == "initialize" else rid):
                continue
            if msg.get("error"):
                raise RuntimeError("MCP %s 失败: %s" % (
                    method, json.dumps(msg["error"], ensure_ascii=False)[:200]))
            return msg.get("result") or {}


def list_tools(server):
    """发现服务器工具。返回 {"ok", "tools":[{"name","description","input_schema"}], "error"}。"""
    try:
        with _Session(server) as sess:
            res = sess._request("tools/list", {}, timeout=_INIT_TIMEOUT)
        tools = []
        for t in (res.get("tools") or [])[:16]:
            if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]:
                tools.append({"name": t["name"],
                              "description": str(t.get("description") or "")[:400],
                              "input_schema": t.get("input_schema")
                              if isinstance(t.get("input_schema"), dict) else
                              {"type": "object", "properties": {}}})
        return {"ok": True, "tools": tools, "error": ""}
    except Exception as e:
        return {"ok": False, "tools": [], "error": str(e)[:200]}


def call_tool(server, tool_name, arguments, timeout_s=_CALL_TIMEOUT):
    """调用一个工具。返回 {"ok", "text", "error", "is_error"}。"""
    try:
        timeout = min(_CALL_TIMEOUT_MAX, max(5.0, float(timeout_s or _CALL_TIMEOUT)))
        with _Session(server) as sess:
            res = sess._request("tools/call",
                                {"name": tool_name,
                                 "arguments": arguments if isinstance(arguments, dict) else {}},
                                timeout=timeout)
        parts = []
        for c in res.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                parts.append(str(c["text"]))
        text = "\n".join(parts)[:20000]
        is_err = bool(res.get("isError"))
        return {"ok": bool(text) and not is_err, "text": text,
                "error": "" if not is_err else (text or "工具返回 isError"),
                "is_error": is_err}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:200], "is_error": False}


# ---- 面向 builtin_agent 的合并视图（带缓存） ----

def tool_specs_cached(force=False):
    """全部服务器的工具合并清单：[{server, name(原名), full_name, description,
    input_schema}]。TTL 内走缓存；单个服务器失败跳过（error 挂在条目外不打断）。"""
    with _LOCK:
        if not force:
            hit = True
            for key, (ts, _t) in _TOOLS_CACHE.items():
                if time.time() - ts > _TOOLS_TTL:
                    hit = False
                    break
            if hit and _TOOLS_CACHE:
                out = []
                for _key, (_ts, tools) in _TOOLS_CACHE.items():
                    out.extend(tools)
                return out[:MAX_MCP_TOOLS]
    servers, err = parse_servers(_settings_text())
    if err or not servers:
        return []
    out = []
    with _LOCK:
        _TOOLS_CACHE.clear()
    for srv in servers:
        res = list_tools(srv)
        if not res.get("ok"):
            continue
        specs = []
        for t in res["tools"]:
            specs.append({"server": srv["name"], "name": t["name"],
                          "full_name": "mcp__%s__%s" % (srv["name"], t["name"]),
                          "description": t["description"],
                          "input_schema": t["input_schema"]})
        with _LOCK:
            _TOOLS_CACHE[srv["name"]] = (time.time(), specs)
        out.extend(specs)
        if len(out) >= MAX_MCP_TOOLS:
            break
    return out[:MAX_MCP_TOOLS]


def dispatch_full_name(full_name, arguments, timeout_s=_CALL_TIMEOUT):
    """mcp__<server>__<tool> → 找服务器配置并调用。返回 {"ok","text","error"}。"""
    m = re.match(r"^mcp__([a-z0-9_-]{1,24})__(.+)$", str(full_name or ""))
    if not m:
        return {"ok": False, "text": "", "error": "非法 MCP 工具名"}
    sname, tool = m.group(1), m.group(2)
    servers, err = parse_servers(_settings_text())
    if err:
        return {"ok": False, "text": "", "error": err}
    srv = next((s for s in servers if s["name"] == sname), None)
    if not srv:
        return {"ok": False, "text": "", "error": "MCP 服务器 %s 未配置" % sname}
    return call_tool(srv, tool, arguments, timeout_s=timeout_s)


def _settings_text():
    """Merge explicit settings with enabled, locally installed plugin servers."""
    try:
        from . import settings
        raw = str(settings.load().get("mcp_servers") or "")
        try:
            configured = json.loads(raw) if raw.strip() else []
        except Exception:
            return raw
        if not isinstance(configured, list):
            return raw
        try:
            from . import plugins
            plugin_servers = plugins.active_mcp_servers()
        except Exception:
            # A broken optional plugin must never hide explicitly configured
            # MCP servers.
            plugin_servers = []
        for item in plugin_servers:
            if not isinstance(item, dict):
                continue
            # Keep plugin tools isolated from user server names and from each
            # other. The normal parser still validates the final merged list.
            plugin_id = str(item.pop("plugin_id", "plugin"))
            server_name = str(item.get("name") or "server")
            digest = hashlib.sha256(
                (plugin_id + ":" + server_name).encode("utf-8")).hexdigest()[:6]
            safe = re.sub(r"[^a-z0-9_-]", "-", plugin_id.lower())
            item["name"] = ("plg_" + safe[:11] + "_" + digest)[:24]
            if not any(x.get("name") == item["name"] for x in configured
                       if isinstance(x, dict)):
                configured.append(item)
        return json.dumps(configured, ensure_ascii=False)
    except Exception:
        return ""
