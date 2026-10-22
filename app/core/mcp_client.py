# -*- coding: utf-8 -*-
"""内置智能体的 MCP 客户端（Model Context Protocol，stdio 形态）。

零依赖：JSON-RPC 2.0 走子进程 stdin/stdout（按行分帧——MCP stdio 传输约定）。
每次工具调用重新拉起服务器进程：没有长驻会话要管，冷启动成本对内置智能体
的低频工具循环可接受；进程用完即关（finally 杀树），绝不留孤儿。

配置（settings.mcp_servers，JSON 数组字符串，≤16 个服务器）：
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
import queue
import re
import shutil  # noqa: F401  (retained as a patch seam for MCP backend tests)
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_LOCK = threading.RLock()
_REFRESH_LOCK = threading.Lock()
_TOOLS_CACHE = {}          # server_key → (ts, tools)
_TOOLS_TTL = 300.0
_TOOLS_CONFIG_SHA = ""

MAX_SERVERS = 16
MAX_MCP_TOOLS = 64         # 全部服务器合计注入内置智能体的工具数上限
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
_MODEL_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
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


def sandbox_backend_available():
    """Whether bubblewrap is installed and can create its required namespace."""
    from .sandbox_runtime import native_sandbox_available
    return native_sandbox_available()


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
        url = str(item.get("url") or "").strip()
        if not _NAME_RE.match(name):
            return None, "服务器名 %r 只能是小写字母/数字/-/_（≤24 位）" % name[:24]
        if "__" in name:
            return None, "服务器名 %s 不能包含工具分隔符 __" % name
        if name in names:
            return None, "MCP 服务器名 %s 重名" % name
        names.add(name)
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        headers = {str(k): str(v) for k, v in list(headers.items())[:8]
                   if str(k).strip() and len(str(v)) <= 500}
        if url and command:
            return None, "服务器 %s 的 url 与 command 只能二选一" % name
        if not command and not url:
            return None, "服务器 %s 缺 command（stdio）或 url（HTTP）" % name
        if url:
            # HTTP transport（Streamable HTTP，B9）：不落子进程，无 OS 沙箱诉求；
            # 只收 http/https。host 不设白名单——与 stdio「用户本机自配同自主权」
            # 同档（含 localhost 场景：本机工具起 HTTP 服务器是常态）。
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return None, "服务器 %s 的 url 必须是 http/https 且带主机" % name
        args = item.get("args") if isinstance(item.get("args"), list) else []
        args = [str(a) for a in args][:16]
        env = item.get("env") if isinstance(item.get("env"), dict) else {}
        env = {str(k): str(v) for k, v in list(env.items())[:16]}
        disabled = item.get("disabled_tools")
        if not isinstance(disabled, list):
            disabled = []
        disabled = list(dict.fromkeys(str(x).strip()[:120] for x in disabled
                                      if str(x or "").strip()))[:200]
        out.append({"name": name, "command": command, "url": url, "args": args,
                    "env": env, "headers": headers, "disabled_tools": disabled})
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
    if not sandbox_backend_available():
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

    def __init__(self, server, sandbox=None, workdir=None, timeout_s=None,
                 cancel_event=None, deadline=None):
        self.server = server
        self.sandbox = sandbox
        self.workdir = workdir
        self.timeout_s = timeout_s
        self.deadline = deadline
        self.task_deadline = deadline
        self.cancel_event = cancel_event
        self._nest = None
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
            call_deadline = time.monotonic() + max(0.0, float(self.timeout_s))
            self.deadline = min(self.deadline, call_deadline) if self.deadline is not None else call_deadline
        self._check_interrupted()
        try:
            argv, env, cwd = _sandbox_launch(self.server, self.sandbox, self.workdir)
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise RuntimeError("MCP 任务时限已到，拒绝启动")
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False,
                env=env, cwd=cwd, start_new_session=(os.name != "nt"))
        except Exception as e:
            raise RuntimeError("MCP 服务器启动失败: %s" % e)
        try:
            import queue as _queue
            self.lines = _queue.Queue()
            self._reader_threads = [
                threading.Thread(target=self._pump_out, daemon=True),
                threading.Thread(target=self._pump_err, daemon=True),
            ]
            for thread in self._reader_threads:
                thread.start()
            if os.name == "nt":
                from . import beekeeper
                self._nest = beekeeper.BeeNest()
                self._nest.adopt(self.proc)
            init_timeout = _INIT_TIMEOUT
            if self.deadline is not None:
                init_timeout = min(init_timeout, self.deadline - time.monotonic())
                if init_timeout <= 0:
                    raise RuntimeError("MCP 任务时限已到，初始化未执行")
            self._request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": _CLIENT_INFO}, timeout=init_timeout)
            self._notify("notifications/initialized")
        except BaseException:
            # Context manager __exit__ is not called when __enter__ raises.
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
        for thread, pipe in zip(self._reader_threads, (proc.stdout, proc.stderr)):
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
        if self.task_deadline is not None and time.monotonic() >= self.task_deadline:
            raise RuntimeError("任务总时限已到")

    def _request(self, method, params, timeout):
        """单请求单响应；忽略通知与未知 id 的服务端请求。"""
        self._check_interrupted()
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
            self._check_interrupted()
            remain = deadline - time.monotonic()
            if remain <= 0:
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


_HTTP_BODY_CAP = 2 * 1024 * 1024   # 单次响应体上限（防炸弹）


class _HttpSession:
    """Streamable HTTP 形态的 MCP 会话（B9）：每次使用做一遍
    initialize → initialized → 请求，不保活。

    与 stdio _Session 同接口（__enter__/__exit__/_request），list_tools /
    call_tool 按 server 形态选择会话类。Windows 上 stdio 服务器因无 OS 隔离
    后端一律拒启（_sandbox_launch 纪律），HTTP 是该平台可用的主通道：不落
    子进程就没有进程面要隔离，边界收在「只打用户配置的 URL + 响应体上限」。

    直连（不走系统代理）：localhost 服务器经代理转发必断，远端 https MCP
    也以直连为默认口径，与探测/模型链路一致。"""

    def __init__(self, server, sandbox=None, workdir=None, timeout_s=None,
                 cancel_event=None, deadline=None):
        self.server = server
        self.url = str(server.get("url") or "").strip()
        self.timeout_s = timeout_s
        self.deadline = deadline
        self.task_deadline = deadline
        self.cancel_event = cancel_event
        self.session_id = ""

    def _check_interrupted(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RuntimeError("任务已取消")
        if self.task_deadline is not None and time.monotonic() >= self.task_deadline:
            raise RuntimeError("任务总时限已到")

    def _post(self, payload, timeout):
        headers = dict(self.server.get("headers") or {})
        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, method="POST", headers=headers, data=data)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=max(0.001, float(timeout))) as resp:
                body = resp.read(_HTTP_BODY_CAP + 1)
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = str(sid)[:200]
                ctype = str(resp.headers.get("Content-Type") or "").lower()
                status = resp.status
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(2048).decode("utf-8", "replace")
            except Exception:
                pass
            raise RuntimeError("MCP HTTP %s: %s" % (e.code, detail[:200]))
        if len(body) > _HTTP_BODY_CAP:
            raise RuntimeError("MCP HTTP 响应体超上限")
        return status, ctype, body.decode("utf-8", "replace")

    def _parse_payload(self, ctype, body, want_id):
        """响应体（JSON 或 SSE 流）→ id 匹配的 JSON-RPC 消息 dict。"""
        candidates = []
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    candidates.append(obj)
        else:
            try:
                obj = json.loads(body)
            except Exception:
                raise RuntimeError("MCP HTTP 响应不是合法 JSON")
            if isinstance(obj, dict):
                candidates.append(obj)
        for msg in candidates:
            if msg.get("id") == want_id:
                return msg
        return None

    def __enter__(self):
        if self.timeout_s is not None:
            call_deadline = time.monotonic() + max(0.0, float(self.timeout_s))
            self.deadline = min(self.deadline, call_deadline) if self.deadline is not None else call_deadline
        self._check_interrupted()
        init_timeout = _INIT_TIMEOUT
        if self.deadline is not None:
            init_timeout = min(init_timeout, self.deadline - time.monotonic())
            if init_timeout <= 0:
                raise RuntimeError("MCP 任务时限已到，初始化未执行")
        self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": _CLIENT_INFO}, timeout=init_timeout, rid=1)
        # notifications/initialized：合法响应是 202/空体；SSE 长连接场景下服务端
        # 可能挂住连接等后续事件——超短超时把「挂住」当送达处理（会话已建立）。
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                       timeout=min(3.0, init_timeout))
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        return False

    def _request(self, method, params, timeout, rid=None):
        """单请求单响应；SSE 流里按 id 匹配（其余事件丢弃）。"""
        self._check_interrupted()
        duration = max(0.0, float(timeout))
        if self.deadline is not None:
            duration = min(duration, self.deadline - time.monotonic())
        if duration <= 0:
            raise RuntimeError("%s 超时（任务时限已到）" % method)
        if rid is None:
            rid = id({})
        status, ctype, body = self._post(
            {"jsonrpc": "2.0", "id": rid, "method": method,
             "params": params or {}}, duration)
        msg = self._parse_payload(ctype, body, rid)
        if msg is None:
            raise RuntimeError("MCP %s 未等到 id 匹配的响应" % method)
        if msg.get("error"):
            raise RuntimeError("MCP %s 失败: %s" % (
                method, json.dumps(msg["error"], ensure_ascii=False)[:200]))
        return msg.get("result") or {}


def _session_for(server, **kw):
    """按服务器形态选会话类：url → HTTP；command → stdio（OS 沙箱纪律不变）。"""
    if str(server.get("url") or "").strip():
        return _HttpSession(server, **kw)
    return _Session(server, **kw)


def list_tools(server, *, sandbox=None, workdir=None):
    """发现服务器工具。返回 {"ok", "tools":[{"name","description","input_schema"}], "error"}。"""
    try:
        with _session_for(server, sandbox=sandbox, workdir=workdir) as sess:
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
              *, sandbox=None, workdir=None, cancel_event=None, deadline=None):
    """调用一个工具。返回 {"ok", "text", "error", "is_error"}。"""
    try:
        timeout = min(_CALL_TIMEOUT_MAX, max(0.001, float(timeout_s or _CALL_TIMEOUT)))
        with _session_for(server, sandbox=sandbox, workdir=workdir,
                          timeout_s=timeout, cancel_event=cancel_event,
                          deadline=deadline) as sess:
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
    """Serialize discoveries so callers cannot observe incomplete refreshes."""
    with _REFRESH_LOCK:
        return _tool_specs_cached(force=force, sandbox=sandbox, workdir=workdir)


def _tool_specs_cached(force=False, *, sandbox=None, workdir=None):
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
    discovered = {}
    seen_names = set()
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
            allowlist = (sandbox or {}).get("allowed_tools")
            if allowlist is not None and full_name not in {str(x).strip() for x in allowlist} \
                    and t["name"] not in {str(x).strip() for x in allowlist}:
                continue
            if not _MODEL_TOOL_NAME_RE.fullmatch(full_name) or full_name in seen_names:
                continue
            seen_names.add(full_name)
            specs.append({"server": srv["name"], "name": t["name"],
                          "full_name": full_name,
                          "description": t["description"],
                          "input_schema": t["input_schema"]})
        discovered[(scope_sha, srv["name"])] = (time.time(), specs)
        out.extend(specs)
        if len(out) >= MAX_MCP_TOOLS:
            break
    with _LOCK:
        if config_sha != _TOOLS_CONFIG_SHA:
            return []
        for key in list(_TOOLS_CACHE):
            if isinstance(key, tuple) and key[0] == scope_sha:
                del _TOOLS_CACHE[key]
        _TOOLS_CACHE.update(discovered)
    return out[:MAX_MCP_TOOLS]


def dispatch_full_name(full_name, arguments, timeout_s=_CALL_TIMEOUT, *,
                       sandbox=None, workdir=None, disabled_tools=None,
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
    if tool in (srv.get("disabled_tools") or []):
        return {"ok": False, "text": "", "error": "MCP 工具已禁用: %s" % full_name}
    if (full_name in set(str(x) for x in (disabled_tools or []))
            or tool in set(str(x) for x in (disabled_tools or []))):
        return {"ok": False, "text": "", "error": "任务策略已禁用 MCP 工具: %s" % full_name}
    allowlist = (sandbox or {}).get("allowed_tools")
    if allowlist is not None and full_name not in {str(x).strip() for x in allowlist} \
            and tool not in {str(x).strip() for x in allowlist}:
        return {"ok": False, "text": "", "error": "MCP 工具不在任务能力白名单: %s" % full_name}
    if sandbox is None or not workdir:
        return {"ok": False, "text": "", "error": "MCP 调用缺少任务沙箱策略或工作目录，拒绝启动"}
    # Discovery and execution are separate capabilities. A configured server
    # is not permission to invoke an arbitrary tool name on it.
    discovered = list_tools(srv, sandbox=sandbox, workdir=workdir)
    if not discovered.get("ok"):
        return {"ok": False, "text": "", "error": "MCP 工具未能完成能力发现，拒绝执行"}
    known = {str(item.get("name") or "") for item in discovered.get("tools", [])
             if isinstance(item, dict)}
    if tool not in known:
        return {"ok": False, "text": "", "error": "MCP 工具未登记，拒绝执行: %s" % full_name}
    return call_tool(srv, tool, arguments, timeout_s=timeout_s,
                     sandbox=sandbox, workdir=workdir,
                     cancel_event=cancel_event, deadline=deadline)


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


def configured_servers():
    """Return the validated configured stdio servers for protocol adapters.

    This is intentionally a read-only view.  It includes the same plugin
    declarations merged by ``main.py`` as the built-in tool loop, so ACP and
    future external transports cannot silently receive a different MCP set.
    """
    servers, error = parse_servers(_settings_text())
    return [] if error or not servers else servers
