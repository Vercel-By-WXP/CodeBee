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
import os
import queue
import re
import subprocess
import threading
import time

_LOCK = threading.RLock()
_TOOLS_CACHE = {}          # server_key → (ts, tools)
_CACHE_CONFIG = None       # 当前缓存对应的完整服务器配置
_TOOLS_TTL = 300.0

MAX_SERVERS = 4
MAX_MCP_TOOLS = 24         # 全部服务器合计注入内置智能体的工具数上限
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
_MODEL_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_INIT_TIMEOUT = 15.0
_CALL_TIMEOUT = 60.0
_CALL_TIMEOUT_MAX = 300.0

_CLIENT_INFO = {"name": "CodeBee", "version": "1.0"}


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
    names = set()
    for item in arr:
        if not isinstance(item, dict):
            return None, "MCP 服务器配置必须是对象"
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not _NAME_RE.match(name):
            return None, "服务器名 %r 只能是小写字母/数字/-/_（≤24 位）" % name[:24]
        if "__" in name:
            return None, "服务器名 %s 不能包含工具分隔符 __" % name
        if name in names:
            return None, "MCP 服务器名 %s 重名" % name
        names.add(name)
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

    def __init__(self, server, cancel_event=None, deadline=None):
        self.server = server
        self.cancel_event = cancel_event
        self.deadline = deadline
        self.proc = None
        self.lines = None       # reader 线程产出的行队列
        self._nest = None
        self._out_thread = None
        self._err_thread = None

    def _pump_err(self):
        try:
            for _ in self.proc.stderr:
                pass
        except Exception:
            pass
        finally:
            try:
                self.proc.stderr.close()
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
            try:
                self.proc.stdout.close()
            except Exception:
                pass

    def __enter__(self):
        self._check_interrupted()
        try:
            self.proc = subprocess.Popen(
                [self.server["command"]] + list(self.server.get("args") or []),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False,
                env={**os.environ, **(self.server.get("env") or {})},
                start_new_session=(os.name != "nt"))
        except Exception as e:
            raise RuntimeError("MCP 服务器启动失败: %s" % e)
        try:
            if os.name == "nt":
                from . import beekeeper
                self._nest = beekeeper.BeeNest()
                self._nest.adopt(self.proc)
            self.lines = queue.Queue()
            self._out_thread = threading.Thread(target=self._pump_out, daemon=True)
            self._err_thread = threading.Thread(target=self._pump_err, daemon=True)
            self._out_thread.start()
            self._err_thread.start()
            self._request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": _CLIENT_INFO}, timeout=_INIT_TIMEOUT)
            self._notify("notifications/initialized")
        except Exception:
            # __enter__ 抛错时 Python 不会调用 __exit__。
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        proc = self.proc
        if proc is None:
            return False
        try:
            if self._nest is not None:
                self._nest.close()
        except Exception:
            pass
        if os.name != "nt" or proc.poll() is None:
            try:
                from . import runner
                runner._kill_tree(proc.pid)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        for thread, pipe in ((self._out_thread, proc.stdout),
                             (self._err_thread, proc.stderr)):
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.5)
            # 读线程持有管道锁时主线程 close 可能卡到孙进程自然退出。
            if pipe is not None and (thread is None or not thread.is_alive()):
                try:
                    pipe.close()
                except Exception:
                    pass
        return False

    def _send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _check_interrupted(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RuntimeError("任务已取消")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RuntimeError("任务总时限已到")

    def _request(self, method, params, timeout):
        """单请求单响应；忽略通知与未知 id 的服务端请求。"""
        self._check_interrupted()
        rid = id({})
        self._send({"jsonrpc": "2.0", "id": 1 if method == "initialize" else rid,
                    "method": method, "params": params or {}})
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            self._check_interrupted()
            remain = deadline - time.monotonic()
            if self.deadline is not None:
                remain = min(remain, self.deadline - time.monotonic())
            if remain <= 0:
                self._check_interrupted()
                raise RuntimeError("%s 超时（%.0fs）" % (method, timeout))
            try:
                wait = min(remain, 0.1) if self.cancel_event is not None else remain
                line = self.lines.get(timeout=wait)
            except queue.Empty:
                continue
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
                schema = t.get("inputSchema", t.get("input_schema"))
                tools.append({"name": t["name"],
                              "description": str(t.get("description") or "")[:400],
                              "input_schema": schema
                              if isinstance(schema, dict) else
                              {"type": "object", "properties": {}}})
        return {"ok": True, "tools": tools, "error": ""}
    except Exception as e:
        return {"ok": False, "tools": [], "error": str(e)[:200]}


def call_tool(server, tool_name, arguments, timeout_s=_CALL_TIMEOUT,
              cancel_event=None, deadline=None):
    """调用一个工具。返回 {"ok", "text", "error", "is_error"}。"""
    try:
        timeout = min(_CALL_TIMEOUT_MAX, max(5.0, float(timeout_s or _CALL_TIMEOUT)))
        with _Session(server, cancel_event=cancel_event, deadline=deadline) as sess:
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
    servers, err = parse_servers(_settings_text())
    config = json.dumps(servers, sort_keys=True, ensure_ascii=False) if not err else None
    global _CACHE_CONFIG
    with _LOCK:
        if config != _CACHE_CONFIG:
            _TOOLS_CACHE.clear()
            _CACHE_CONFIG = config
        if err or not servers:
            return []
        if not force and _TOOLS_CACHE and all(
                time.time() - ts <= _TOOLS_TTL for ts, _tools in _TOOLS_CACHE.values()):
            out = []
            for _key, (_ts, tools) in _TOOLS_CACHE.items():
                out.extend(tools)
            return out[:MAX_MCP_TOOLS]
        _TOOLS_CACHE.clear()
    out = []
    discovered = {}
    seen_names = set()
    for srv in servers:
        res = list_tools(srv)
        if not res.get("ok"):
            continue
        specs = []
        for t in res["tools"]:
            full_name = "mcp__%s__%s" % (srv["name"], t["name"])
            if not _MODEL_TOOL_NAME_RE.fullmatch(full_name) or full_name in seen_names:
                continue
            seen_names.add(full_name)
            specs.append({"server": srv["name"], "name": t["name"],
                          "full_name": full_name,
                          "description": t["description"],
                          "input_schema": t["input_schema"]})
        discovered[srv["name"]] = (time.time(), specs)
        out.extend(specs)
        if len(out) >= MAX_MCP_TOOLS:
            break
    with _LOCK:
        # 发现期间配置可能已更换；旧服务器结果不能写回新配置的缓存。
        if config != _CACHE_CONFIG:
            return []
        _TOOLS_CACHE.update(discovered)
    return out[:MAX_MCP_TOOLS]


def dispatch_full_name(full_name, arguments, timeout_s=_CALL_TIMEOUT,
                       cancel_event=None, deadline=None):
    """mcp__<server>__<tool> → 找服务器配置并调用。返回 {"ok","text","error"}。"""
    full_name = str(full_name or "")
    sname, sep, tool = full_name[5:].partition("__") if full_name.startswith("mcp__") \
        else ("", "", "")
    if not sep or not _NAME_RE.fullmatch(sname) or "__" in sname or not tool:
        return {"ok": False, "text": "", "error": "非法 MCP 工具名"}
    servers, err = parse_servers(_settings_text())
    if err:
        return {"ok": False, "text": "", "error": err}
    srv = next((s for s in servers if s["name"] == sname), None)
    if not srv:
        return {"ok": False, "text": "", "error": "MCP 服务器 %s 未配置" % sname}
    return call_tool(srv, tool, arguments, timeout_s=timeout_s,
                     cancel_event=cancel_event, deadline=deadline)


def _settings_text():
    try:
        from . import settings
        return str(settings.load().get("mcp_servers") or "")
    except Exception:
        return ""
