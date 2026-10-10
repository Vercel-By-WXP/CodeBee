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
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()
_TOOLS_CACHE = {}          # server_key → (ts, tools)
_TOOLS_TTL = 300.0
_TOOLS_CONFIG_SHA = ""

MAX_SERVERS = 4
MAX_MCP_TOOLS = 24         # 全部服务器合计注入内置智能体的工具数上限
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
_INIT_TIMEOUT = 15.0
_CALL_TIMEOUT = 60.0
_CALL_TIMEOUT_MAX = 300.0

_CLIENT_INFO = {"name": "CodeBee", "version": "1.0"}


def invalidate_tools_cache():
    """服务器配置变化后丢弃已发现工具（插件启停等场景调用）。"""
    global _TOOLS_CONFIG_SHA
    with _LOCK:
        _TOOLS_CACHE.clear()
        _TOOLS_CONFIG_SHA = ""


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
        disabled = item.get("disabled_tools")
        if not isinstance(disabled, list):
            disabled = []
        disabled = list(dict.fromkeys(str(x).strip()[:120] for x in disabled
                                      if str(x or "").strip()))[:200]
        out.append({"name": name, "command": command, "args": args, "env": env,
                    "disabled_tools": disabled})
    return out, None


def _sandbox_launch(server, sandbox, workdir):
    """Build an OS-isolated stdio server launch or refuse to spawn it.

    MCP tools are arbitrary configured programs. On Linux they run under
    bubblewrap with only declared roots mounted; platforms without an equivalent
    backend fail closed. Runtime variables are synthetic/minimal unless explicitly
    included in the task env allowlist.
    """
    from . import policy
    if not sandbox or not workdir:
        raise RuntimeError("MCP 调用缺少任务沙箱策略或工作目录，拒绝启动")
    root = Path(workdir).expanduser().resolve(strict=True)
    normalized = policy.normalize_sandbox(sandbox, root)
    if normalized.get("backend") == "docker":
        # MCP command/args may include host-specific executables and absolute
        # script paths. Until those are explicitly mapped into an image and
        # verified mounts, refuse instead of launching a misleading container.
        raise RuntimeError("MCP Docker 启动映射尚未配置；拒绝降级到宿主机")
    if os.name != "posix" or sys.platform == "darwin" or not shutil.which("bwrap"):
        raise RuntimeError("MCP 服务尚无可用的 OS 隔离后端，拒绝启动")
    roots = [Path(value).resolve(strict=True)
             for value in normalized.get("allowed_roots") or []]
    if not roots:
        raise RuntimeError("MCP 沙箱没有允许目录，拒绝启动")
    cwd = root if policy.path_allowed(root, normalized) else roots[0]
    argv = _bubblewrap_argv(server, normalized, roots, cwd)
    return argv, _sandbox_env(server, normalized), str(cwd)


def _sandbox_env(server, sandbox):
    """Pass only the minimal runtime environment and explicitly allowed values."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": "/tmp", "TMPDIR": "/tmp"}
    for name in sandbox.get("env_allowlist") or []:
        if name in os.environ:
            env[name] = os.environ[name]
        elif name in (server.get("env") or {}):
            env[name] = str(server["env"][name])
    return env


def _bubblewrap_argv(server, sandbox, roots, cwd):
    """Build bwrap argv from already validated paths (also host-testable)."""
    argv = ["bwrap", "--die-with-parent", "--new-session", "--tmpfs", "/"]
    for system_path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
        if os.path.exists(system_path):
            argv.extend(["--dir", system_path, "--ro-bind", system_path, system_path])
    argv.extend(["--dir", "/proc", "--dir", "/dev", "--proc", "/proc",
                 "--dev", "/dev", "--tmpfs", "/tmp"])
    if sandbox.get("network") is False:
        argv.append("--unshare-net")
    for allowed_root in roots:
        for parent in reversed(allowed_root.parents):
            if str(parent) != "/":
                argv.extend(["--dir", str(parent)])
        argv.extend(["--dir", str(allowed_root), "--bind",
                     str(allowed_root), str(allowed_root)])
    argv.extend(["--chdir", str(cwd), "--", server["command"]]
                + list(server.get("args") or []))
    return argv


class _Session:
    """一次性的 MCP 服务器会话：spawn → initialize → （list/call）→ 杀树。"""

    def __init__(self, server, sandbox=None, workdir=None, timeout_s=None):
        self.server = server
        self.sandbox = sandbox
        self.workdir = workdir
        self.timeout_s = timeout_s
        self.deadline = None
        self.proc = None
        self.lines = None       # reader 线程产出的行队列
        self.err = None
        self._reader_threads = []

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
            try:
                self.proc.stdout.close()
            except Exception:
                pass
            self.lines.put(None)      # EOF 哨兵

    def __enter__(self):
        if self.timeout_s is not None:
            self.deadline = time.monotonic() + max(0.0, float(self.timeout_s))
        try:
            argv, env, cwd = _sandbox_launch(self.server, self.sandbox, self.workdir)
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise RuntimeError("MCP 任务时限已到，拒绝启动")
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False,
                env=env, cwd=cwd, start_new_session=True)
        except Exception as e:
            raise RuntimeError("MCP 服务器启动失败: %s" % e)
        import queue as _queue
        self.lines = _queue.Queue()
        self._reader_threads = [
            threading.Thread(target=self._pump_out, daemon=True),
            threading.Thread(target=self._pump_err, daemon=True),
        ]
        for thread in self._reader_threads:
            thread.start()
        try:
            init_timeout = _INIT_TIMEOUT
            if self.deadline is not None:
                init_timeout = min(init_timeout, self.deadline - time.monotonic())
                if init_timeout <= 0:
                    raise RuntimeError("MCP 任务时限已到，初始化未执行")
            self._request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": _CLIENT_INFO}, timeout=init_timeout)
            self._notify("notifications/initialized")
        except Exception:
            # Context manager __exit__ is not called when __enter__ raises.
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            try:
                if self.proc and self.proc.poll() is None:
                    # bwrap runs in its own process group; terminate descendants
                    # as well as the launcher so timed-out MCP servers cannot
                    # leave background children holding pipes or doing work.
                    try:
                        from . import runner
                        runner._kill_tree(self.proc.pid)
                    except Exception:
                        pass
                    self.proc.kill()
            except Exception:
                pass
            try:
                if self.proc:
                    self.proc.wait(timeout=1)
            except Exception:
                pass
        finally:
            # Close all parent-side pipe handles even if initialization failed.
            # Do not close a stream while its reader is blocked in read(): the
            # buffered stream lock can make close wait forever if an escaped
            # descendant still holds the pipe open. Let daemon readers finish
            # on EOF and close only streams no thread currently owns.
            for thread in self._reader_threads:
                try:
                    thread.join(timeout=0.2)
                except Exception:
                    pass
            for name in ("stdout", "stderr"):
                pipe = getattr(self.proc, name, None) if self.proc else None
                readers_alive = any(
                    thread.is_alive() for thread in self._reader_threads)
                if pipe and not readers_alive:
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

    def _request(self, method, params, timeout):
        """单请求单响应；忽略通知与未知 id 的服务端请求。"""
        duration = max(0.0, float(timeout))
        if self.deadline is not None:
            duration = min(duration, self.deadline - time.monotonic())
        if duration <= 0:
            raise RuntimeError("%s 超时（任务时限已到）" % method)
        rid = id({})
        self._send({"jsonrpc": "2.0", "id": 1 if method == "initialize" else rid,
                    "method": method, "params": params or {}})
        deadline = time.monotonic() + duration
        while True:
            remain = deadline - time.monotonic()
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


def list_tools(server, *, sandbox=None, workdir=None):
    """发现服务器工具。返回 {"ok", "tools":[{"name","description","input_schema"}], "error"}。"""
    try:
        with _Session(server, sandbox=sandbox, workdir=workdir) as sess:
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


def call_tool(server, tool_name, arguments, timeout_s=_CALL_TIMEOUT,
              *, sandbox=None, workdir=None):
    """调用一个工具。返回 {"ok", "text", "error", "is_error"}。"""
    try:
        timeout = min(_CALL_TIMEOUT_MAX, max(0.001, float(timeout_s or _CALL_TIMEOUT)))
        with _Session(server, sandbox=sandbox, workdir=workdir,
                      timeout_s=timeout) as sess:
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

def tool_specs_cached(force=False, *, sandbox=None, workdir=None):
    """全部服务器的工具合并清单：[{server, name(原名), full_name, description,
    input_schema}]。TTL 内走缓存；单个服务器失败跳过（error 挂在条目外不打断）。"""
    global _TOOLS_CONFIG_SHA
    if not sandbox or not workdir:
        return []
    config_text = _settings_text()
    config_sha = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    scope_sha = hashlib.sha256(json.dumps({
        "workdir": os.path.realpath(workdir), "sandbox": sandbox,
    }, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    with _LOCK:
        if config_sha != _TOOLS_CONFIG_SHA:
            _TOOLS_CACHE.clear()
            _TOOLS_CONFIG_SHA = config_sha
        if not force:
            hit = True
            scoped = [(key, value) for key, value in _TOOLS_CACHE.items()
                      if isinstance(key, tuple) and key[0] == scope_sha]
            for key, (ts, _t) in scoped:
                if time.time() - ts > _TOOLS_TTL:
                    hit = False
                    break
            if hit and scoped:
                out = []
                for _key, (_ts, tools) in scoped:
                    out.extend(tools)
                return out[:MAX_MCP_TOOLS]
    servers, err = parse_servers(config_text)
    if err or not servers:
        return []
    from . import policy
    disabled = set(policy.normalize_disabled_tools(
        (sandbox or {}).get("disabled_tools")))
    out = []
    with _LOCK:
        _TOOLS_CACHE.clear()
    for srv in servers:
        res = list_tools(srv, sandbox=sandbox, workdir=workdir)
        if not res.get("ok"):
            continue
        specs = []
        for t in res["tools"]:
            full_name = "mcp__%s__%s" % (srv["name"], t["name"])
            if (t["name"] in srv.get("disabled_tools", [])
                    or t["name"] in disabled or full_name in disabled):
                continue
            specs.append({"server": srv["name"], "name": t["name"],
                          "full_name": full_name,
                          "description": t["description"],
                          "input_schema": t["input_schema"]})
        with _LOCK:
            _TOOLS_CACHE[(scope_sha, srv["name"])] = (time.time(), specs)
        out.extend(specs)
        if len(out) >= MAX_MCP_TOOLS:
            break
    return out[:MAX_MCP_TOOLS]


def dispatch_full_name(full_name, arguments, timeout_s=_CALL_TIMEOUT, *,
                       sandbox=None, workdir=None, disabled_tools=None):
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
    if tool in (srv.get("disabled_tools") or []):
        return {"ok": False, "text": "", "error": "MCP 工具已禁用: %s" % full_name}
    if (full_name in set(str(x) for x in (disabled_tools or []))
            or tool in set(str(x) for x in (disabled_tools or []))):
        return {"ok": False, "text": "", "error": "任务策略已禁用 MCP 工具: %s" % full_name}
    if sandbox is None or not workdir:
        return {"ok": False, "text": "", "error": "MCP 调用缺少任务沙箱策略或工作目录，拒绝启动"}
    return call_tool(srv, tool, arguments, timeout_s=timeout_s,
                     sandbox=sandbox, workdir=workdir)


_SETTINGS_TEXT = None     # main.py 启动注入（读 settings.mcp_servers）——本模块
                           # 不直接依赖 settings(L0)，静态图保持干净（同 set_run_estimator 模式）


def set_settings_text(fn):
    """注入 MCP 服务器清单读取器；未注入时工具清单为空（MCP 功能关闭）。"""
    global _SETTINGS_TEXT
    _SETTINGS_TEXT = fn if callable(fn) else None


def _settings_text():
    try:
        return str(_SETTINGS_TEXT() or "") if _SETTINGS_TEXT else ""
    except Exception:
        return ""
