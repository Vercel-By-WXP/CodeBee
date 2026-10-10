# -*- coding: utf-8 -*-
"""Minimal ACP v1 stdio client for locally configured agent processes."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid

from . import runner
from .env_scrub import scrub_env

_MAX_LINE = 2 * 1024 * 1024
_MAX_TEXT = 2 * 1024 * 1024


class ACPError(Exception):
    pass


class _Connection:
    def __init__(self, process):
        self.process = process
        self.incoming = queue.Queue()
        self.write_lock = threading.Lock()
        self.next_id = 1
        self.closed = False
        self.reader = threading.Thread(target=self._read_lines,
                                       name="acp-stdio-reader", daemon=True)
        self.reader.start()

    def _read_lines(self):
        try:
            while True:
                line = self.process.stdout.readline()
                if not line:
                    break
                if len(line) > _MAX_LINE:
                    self.incoming.put({"_transport_error": "ACP 响应超过 2 MiB"})
                    break
                try:
                    message = json.loads(line.decode("utf-8") if isinstance(line, bytes)
                                         else line)
                    if isinstance(message, dict):
                        self.incoming.put(message)
                except (ValueError, UnicodeDecodeError):
                    self.incoming.put({"_transport_error": "ACP 返回了无效 JSON"})
        except Exception as exc:
            self.incoming.put({"_transport_error": "ACP 读取失败：%s" % exc})
        finally:
            self.incoming.put(None)

    def send(self, message):
        raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        with self.write_lock:
            if self.closed or self.process.poll() is not None:
                raise ACPError("ACP 进程已退出")
            try:
                self.process.stdin.write(raw.encode("utf-8"))
            except TypeError:
                self.process.stdin.write(raw)
            self.process.stdin.flush()

    def request(self, method, params, deadline, cancel_event, on_message):
        request_id = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id,
                   "method": method, "params": params})
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self.send({"jsonrpc": "2.0", "method": "session/cancel",
                           "params": {"sessionId": params.get("sessionId", "")}})
                raise InterruptedError("ACP 任务已取消")
            if deadline is not None and time.monotonic() >= deadline:
                self.send({"jsonrpc": "2.0", "method": "session/cancel",
                           "params": {"sessionId": params.get("sessionId", "")}})
                raise TimeoutError("ACP 任务总时限已到")
            wait = 0.2
            if deadline is not None:
                wait = min(wait, max(0.01, deadline - time.monotonic()))
            try:
                message = self.incoming.get(timeout=wait)
            except queue.Empty:
                if self.process.poll() is not None and self.incoming.empty():
                    raise ACPError("ACP 进程退出码 %s" % self.process.poll())
                continue
            if message is None:
                raise ACPError("ACP 连接已关闭")
            if message.get("_transport_error"):
                raise ACPError(message["_transport_error"])
            if message.get("id") == request_id and ("result" in message or "error" in message):
                if "error" in message:
                    error = message.get("error") or {}
                    raise ACPError("ACP %s 失败：%s" %
                                   (method, str(error.get("message") or error)[:500]))
                return message.get("result") or {}
            if message.get("method"):
                response = on_message(message)
                if response is not None:
                    self.send(response)

    def close(self):
        self.closed = True
        try:
            if self.process.poll() is None:
                self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.wait(timeout=0.5)
        except Exception:
            try:
                runner._kill_tree(self.process.pid)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        for stream_name in ("stdout", "stderr"):
            try:
                getattr(self.process, stream_name).close()
            except Exception:
                pass


def _profile_runtime(agent):
    if not agent.get("profile_managed"):
        return dict(agent)
    from . import backend_profiles
    profile = backend_profiles.get_runtime(agent.get("profile_id"))
    if not profile:
        raise ACPError("ACP Backend Profile 不存在或已停用")
    merged = dict(agent)
    merged.update({"command": profile["command"], "args": profile["args"],
                   "env": profile["env"],
                   "workspace_path": profile.get("workspace_path") or ""})
    return merged


def _path(workdir, configured_root, requested):
    from pathlib import Path
    root = Path(workdir or ".").expanduser().resolve(strict=True)
    raw = str(requested or "")
    if not raw or "\x00" in raw:
        raise ValueError("ACP 文件路径无效")
    configured = str(configured_root or "").rstrip("/\\")
    if configured and (raw == configured or raw.startswith(configured + "/") or
                       raw.startswith(configured + "\\")):
        rel = raw[len(configured):].lstrip("/\\")
        target = root.joinpath(*rel.replace("\\", "/").split("/"))
    else:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            raise ValueError("ACP 文件路径必须映射到当前工作区")
    resolved = target.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError("ACP 文件路径超出当前工作区")
    return root, resolved


def _fs_request(method, params, workdir, configured_root, readonly, checkpoint_run_id):
    from . import checkpoints
    root, target = _path(workdir, configured_root, params.get("path"))
    if method == "fs/read_text_file":
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError("文件不可读取：%s" % exc)
        lines = content.splitlines(keepends=True)
        line = max(1, int(params.get("line") or 1))
        limit = max(1, min(10000, int(params.get("limit") or 10000)))
        if line > 1 or len(lines) > limit:
            content = "".join(lines[line - 1:line - 1 + limit])
        return {"content": content}
    if method == "fs/write_text_file":
        if readonly:
            raise ValueError("此步骤为只读模式")
        content = params.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("写入内容无效或超过 16 MiB")
        if checkpoint_run_id:
            saved = checkpoints.capture_file(checkpoint_run_id, str(root), target)
            if not saved.get("ok"):
                raise ValueError("检查点无法保存原始文件，拒绝写入")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep Python 3.8 compatibility: pathlib.Path.write_text did not yet
        # accept ``newline`` on all supported runtimes.
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        if checkpoint_run_id and not checkpoints.mark_file_after(
                checkpoint_run_id, str(root), target):
            raise ValueError("检查点无法记录写入后的状态")
        return {}
    raise ValueError("不支持的 ACP 文件系统方法")


def _agent_request(message, *, connection, session_id, workdir, configured_root,
                   readonly, checkpoint_run_id, cancel_event, deadline, sandbox,
                   command_context, terminals):
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    try:
        if method in ("fs/read_text_file", "fs/write_text_file"):
            blocked = set(sandbox.get("disabled_tools") or [])
            required = "read_file" if method == "fs/read_text_file" else "write_file"
            if required in blocked:
                raise ValueError("沙箱策略已禁用 %s" % required)
            result = _fs_request(method, params, workdir, configured_root,
                                 readonly, checkpoint_run_id)
        elif method == "session/request_permission":
            result = {"outcome": {"outcome": "cancelled"}}
        elif method == "terminal/create":
            if readonly:
                raise ValueError("此步骤禁止执行终端命令")
            if not command_context.get("allow_terminal", True):
                raise ValueError("ACP 终端能力已禁用")
            command = str(params.get("command") or "").strip()
            args = params.get("args") or []
            if not command or not isinstance(args, list) or len(args) > 100:
                raise ValueError("ACP 命令参数无效")
            if any(not isinstance(arg, str) or "\x00" in arg or len(arg) > 8192
                   for arg in args):
                raise ValueError("ACP 命令参数无效")
            cwd = str(params.get("cwd") or workdir or "")
            from . import policy
            if not policy.path_allowed(cwd, sandbox):
                raise ValueError("沙箱拒绝：终端工作目录超出允许范围")
            if os.path.realpath(cwd) != os.path.realpath(workdir or "."):
                raise ValueError("ACP 终端目前仅允许在任务工作目录执行")
            from .builtin_agent import _exec_tool
            import shlex
            terminal_id = "term-" + uuid.uuid4().hex[:16]
            terminal = {"output": "", "done": threading.Event(),
                        "cancel": threading.Event(), "released": False}
            terminals[terminal_id] = terminal

            def _run_terminal():
                rendered = shlex.join([command] + args)
                terminal["output"] = _exec_tool(
                    workdir, "run_command", {"command": rendered},
                    cancel_event=terminal["cancel"], deadline=deadline,
                    disabled_tools=sandbox.get("disabled_tools"), sandbox=sandbox,
                    checkpoint_run_id=command_context.get("run_id", ""))
                terminal["done"].set()

            threading.Thread(target=_run_terminal, name="acp-terminal", daemon=True).start()
            result = {"terminalId": terminal_id}
        elif method == "terminal/output":
            terminal = terminals.get(str(params.get("terminalId") or ""))
            if not terminal:
                raise ValueError("terminalId 不存在或已释放")
            output = terminal["output"]
            marker = "退出码: "
            code = None
            if marker in output:
                try:
                    code = int(output.split(marker, 1)[1].splitlines()[0].strip())
                except (ValueError, IndexError):
                    pass
            limit = max(1, min(4 * 1024 * 1024,
                               int(params.get("outputByteLimit") or 1024 * 1024)))
            encoded = output.encode("utf-8")
            truncated = len(encoded) > limit
            if truncated:
                output = encoded[-limit:].decode("utf-8", errors="ignore")
            result = {"output": output, "truncated": truncated,
                      "exitStatus": {"exitCode": code, "signal": None}}
        elif method == "terminal/wait_for_exit":
            terminal = terminals.get(str(params.get("terminalId") or ""))
            if not terminal:
                raise ValueError("terminalId 不存在或已释放")
            while not terminal["done"].wait(0.1):
                if cancel_event is not None and cancel_event.is_set():
                    terminal["cancel"].set()
                if deadline is not None and time.monotonic() >= deadline:
                    terminal["cancel"].set()
                    raise TimeoutError("ACP 终端等待超过任务时限")
            code = None
            if "退出码: " in terminal["output"]:
                try:
                    code = int(terminal["output"].split("退出码: ", 1)[1].splitlines()[0])
                except (ValueError, IndexError):
                    pass
            result = {"exitCode": code, "signal": None}
        elif method == "terminal/kill":
            terminal = terminals.get(str(params.get("terminalId") or ""))
            if not terminal:
                raise ValueError("terminalId 不存在或已释放")
            terminal["cancel"].set()
            result = {}
        elif method == "terminal/release":
            terminal = terminals.pop(str(params.get("terminalId") or ""), None)
            if not terminal:
                raise ValueError("terminalId 不存在或已释放")
            if not terminal["done"].is_set():
                terminal["cancel"].set()
            result = {}
        else:
            raise ValueError("不支持的 ACP client 方法：%s" % str(method or "")[:80])
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32000, "message": str(exc)[:500]}}


def run_agent(agent, prompt, workdir=None, readonly=True, timeout=1200,
              cancel_event=None, deadline=None, command_context=None,
              checkpoint_run_id="", resume=None):
    """Run an ACP v1 agent over stdio and return the common runner result shape."""
    started = time.monotonic()
    process = None
    connection = None
    command_context = dict(command_context or {})
    try:
        profile = _profile_runtime(agent)
        cwd = os.path.realpath(workdir or ".")
        if not os.path.isdir(cwd):
            raise ACPError("ACP 工作目录不可用")
        command = str(profile.get("command") or "").strip()
        args = profile.get("args") or []
        if not command or not isinstance(args, list) or any(not isinstance(x, str) for x in args):
            raise ACPError("ACP 启动命令配置无效")
        env = scrub_env(os.environ.copy())
        env.update({str(k): str(v) for k, v in (profile.get("env") or {}).items()})
        argv = runner.resolve_command(command) + args
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd, env=env,
                                   bufsize=0, start_new_session=(os.name != "nt"),
                                   creationflags=runner.CREATE_NO_WINDOW)
        connection = _Connection(process)
        absolute_deadline = deadline
        if absolute_deadline is None:
            absolute_deadline = started + max(1.0, float(timeout or 1200))
        text_parts = []
        terminals = {}
        session_id = str(resume or "")

        def on_message(message):
            nonlocal session_id
            if message.get("method") == "session/update":
                params = message.get("params") or {}
                update = params.get("update") or {}
                if params.get("sessionId") == session_id and \
                        update.get("sessionUpdate") == "agent_message_chunk":
                    content = update.get("content") or {}
                    if content.get("type") == "text":
                        piece = str(content.get("text") or "")
                        if sum(map(len, text_parts)) < _MAX_TEXT:
                            text_parts.append(piece[:_MAX_TEXT - sum(map(len, text_parts))])
                return None
            return _agent_request(
                message, connection=connection, session_id=session_id,
                workdir=cwd, configured_root=profile.get("workspace_path"),
                readonly=readonly, checkpoint_run_id=checkpoint_run_id,
                cancel_event=cancel_event, deadline=absolute_deadline,
                sandbox=command_context.get("sandbox") or {},
                command_context=command_context, terminals=terminals)

        client_capabilities = {"fs": {"readTextFile": True,
                                       "writeTextFile": not readonly},
                               "terminal": not readonly and
                                   command_context.get("allow_terminal", True)}
        initialized = connection.request("initialize", {
            "protocolVersion": 1, "clientCapabilities": client_capabilities,
            "clientInfo": {"name": "CodeBee", "title": "CodeBee", "version": "1"}},
            absolute_deadline, cancel_event, on_message)
        if int(initialized.get("protocolVersion") or 0) != 1:
            raise ACPError("ACP 版本不兼容：服务端未协商 protocolVersion 1")
        mcp_servers = []
        if session_id:
            capabilities = initialized.get("agentCapabilities") or {}
            if not capabilities.get("loadSession"):
                raise ACPError("此 ACP Agent 不支持恢复会话")
            connection.request("session/load", {
                "sessionId": session_id,
                "cwd": profile.get("workspace_path") or cwd,
                "mcpServers": mcp_servers}, absolute_deadline, cancel_event, on_message)
        else:
            session = connection.request("session/new", {
                "cwd": profile.get("workspace_path") or cwd,
                "mcpServers": mcp_servers}, absolute_deadline, cancel_event, on_message)
            session_id = str(session.get("sessionId") or "")
            if not session_id:
                raise ACPError("ACP 未返回 sessionId")
        connection.request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": str(prompt or "")}]},
            absolute_deadline, cancel_event, on_message)
        if not text_parts:
            raise ACPError("ACP 完成但未返回文本消息")
        return {"ok": True, "text": "".join(text_parts)[:_MAX_TEXT], "json": None,
                "cost_usd": 0.0, "tokens": 0, "usage": None, "error": "",
                "error_code": "", "raw": {"exit_code": 0,
                                               "duration": time.monotonic() - started},
                "kind": "acp", "model": None, "sid": session_id,
                "attempts": []}
    except InterruptedError as exc:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None, "error": str(exc),
                "raw": {"exit_code": None, "cancelled": True,
                        "duration": time.monotonic() - started},
                "kind": "acp", "model": None, "attempts": []}
    except TimeoutError as exc:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None, "error": str(exc),
                "raw": {"exit_code": None, "timed_out": True,
                        "deadline_exceeded": deadline is not None,
                        "duration": time.monotonic() - started},
                "kind": "acp", "model": None, "attempts": []}
    except Exception as exc:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None, "error": str(exc)[:1000],
                "raw": {"exit_code": None, "duration": time.monotonic() - started},
                "kind": "acp", "model": None, "attempts": []}
    finally:
        if connection is not None:
            connection.close()
        elif process is not None:
            try:
                runner._kill_tree(process.pid)
            except Exception:
                pass
