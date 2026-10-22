# -*- coding: utf-8 -*-
"""运行器：把各家 CLI 的无头调用统一成一个接口。

实测验证过的接入方式（2026-09，本机）：
  codex  : codex exec --skip-git-repo-check --json -s <sandbox> [prompt从stdin]
           stdout 为 JSONL 事件流；item.completed(type=agent_message)=最终回答，
           turn.completed=token 用量。pnpm 的 .cmd 垫片需 cmd /c 包装。
  claude : claude -p --output-format json [prompt从stdin]
           stdout 为单个 JSON：result / total_cost_usd / usage。
           需环境变量 CLAUDE_CODE_GIT_BASH_PATH（原生 exe 找不到 bash 会拒绝启动）
           与 CLAUDE_CODE_MAX_OUTPUT_TOKENS（自定义网关模型常见 32768 上限）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time

from . import beekeeper
from . import upstream
from .env_scrub import scrub_env
from .error_codes import (
    ErrorCode, classify_error_text, error_code_value,
    is_account_dead_error,
    is_auth_error as _shared_auth_error,
    is_forbidden_error as _shared_forbidden_error,
    is_quota_error as _shared_quota_error,
    is_rate_limited_error as _shared_rate_limited_error,
    is_transient_error as _shared_transient_error,
)

from .cli_text import (
    _decode_line as _decode_line, _fmt_codex_item as _fmt_codex_item,
    clean_cli_text as clean_cli_text,
    collapse_dup_lines as collapse_dup_lines,
    decode_output as decode_output, pretty_cli_log as pretty_cli_log,
    read_text_any_enc as read_text_any_enc, strip_ansi as strip_ansi,
    tail_decoded as tail_decoded,
)

from .cli_commands import (
    _fallback_probe_cli as _fallback_probe_cli,
    _git_repo_issue as _git_repo_issue,
    _npm_shim_bypass as _npm_shim_bypass,
    _orphan_pack_files as _orphan_pack_files,
    _stable_timeout as _stable_timeout, find_git_bash as find_git_bash,
    redact_command as redact_command, resolve_command as resolve_command,
)

from .cli_parsers import (
    _codex_fail_msg as _codex_fail_msg,
    _codex_provider_args as _codex_provider_args,
    _codex_silent_no_answer as _codex_silent_no_answer,
    _codex_work_events as _codex_work_events,
    _model_flag as _model_flag,
    _parse_claude_json as _parse_claude_json,
    _parse_codex_jsonl as _parse_codex_jsonl,
)


from .runner_parsing import (  # noqa: F401 - 旧调用面再导出
    _loads_lenient, as_scores, extract_json, extract_scores_from_text,
    scores_from_prose, verdict_brief, verdict_digest,
)

log = logging.getLogger(__name__)


def _attempt_ledger(run_id=""):
    """Return the shared attempt ledger without making it a hard dependency."""
    try:
        from . import attempt_ledger, paths
        return attempt_ledger.AttemptLedger(paths.DATA_DIR / "attempts" / "attempts.jsonl")
    except Exception:
        return None

# 非 Windows 必须置 0：POSIX 的 Popen 对非零 creationflags 直接抛 ValueError，
# 置 0 则两边通用（remote.py 同款守卫）。
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DEFAULT_TIMEOUT = 1200  # 单步 20 分钟
DEFAULT_STREAM_ACTIVITY_TIMEOUT_S = 180  # 流式 CLI 的活动延长窗口
DEFAULT_PROCESS_CAPTURE_BYTES = 16 * 1024 * 1024  # stdout/stderr 各限 16 MiB
DEFAULT_PROCESS_LOG_BYTES = 32 * 1024 * 1024  # 每个步骤日志文件最多 32 MiB
MAX_AGENT_CAPTURE_BYTES = 8 * 1024 * 1024  # stdout/stderr 各限 8 MiB，日志仍流式落盘

# dsh is a one-shot CLI, but some endpoint failures are retried internally by
# its node launcher.  Keep a small, run-scoped admission budget outside the
# child process as a second circuit breaker.  The key includes the step role so
# a failed draft retry cannot consume the budget for an unrelated review step.
_DSH_LAUNCH_LOCK = threading.Lock()
_DSH_LAUNCH_HISTORY = {}


def _dsh_launch_limits():
    """Return (max launches, rolling window, minimum gap) for dsh.

    Environment overrides are intentionally bounded so a bad setting cannot
    silently restore the old unbounded retry storm.
    """
    def _number(name, default, lower, upper, integer=False):
        try:
            value = int(os.environ.get(name, str(default))) if integer else float(
                os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(lower, min(upper, value))

    return (
        int(_number("TUTTI_DSH_MAX_LAUNCHES", 4, 1, 12, integer=True)),
        _number("TUTTI_DSH_LAUNCH_WINDOW_S", 600, 30, 3600),
        _number("TUTTI_DSH_MIN_LAUNCH_GAP_S", 10, 0, 300),
    )


def _dsh_launch_admit(run_id, role, agent_id, upstream):
    """Reserve one dsh process launch, or return a human-readable block reason."""
    if agent_id not in ("deepseek-harness", "dsh"):
        return None
    # Legacy/direct callers may not provide a durable run and role.  Do not
    # make those unrelated calls share the synthetic ``unbound`` bucket.
    if not run_id or not role:
        return None
    max_launches, window_s, gap_s = _dsh_launch_limits()
    key = (str(run_id or "unbound"), str(role or ""), str(upstream or "unknown"))
    now = time.monotonic()
    with _DSH_LAUNCH_LOCK:
        # Prune abandoned run keys as well as the key being admitted.  This
        # keeps a long-lived CodeBee service from accumulating one small list
        # for every failed/expired run id.
        prune_after = max(window_s, 3600)
        for old_key, old_records in list(_DSH_LAUNCH_HISTORY.items()):
            kept = [record for record in old_records
                    if now - record["ts"] < prune_after]
            if kept:
                _DSH_LAUNCH_HISTORY[old_key] = kept
            else:
                _DSH_LAUNCH_HISTORY.pop(old_key, None)
        if len(_DSH_LAUNCH_HISTORY) > 1024:
            oldest = sorted(
                _DSH_LAUNCH_HISTORY,
                key=lambda item: _DSH_LAUNCH_HISTORY[item][-1]["ts"],
            )
            for old_key in oldest[:-1024]:
                _DSH_LAUNCH_HISTORY.pop(old_key, None)
        history = [record for record in _DSH_LAUNCH_HISTORY.get(key, ())
                   if now - record["ts"] < window_s]
        failed = [record for record in history if record["state"] == "failed"]
        if failed and now - failed[-1]["ts"] < gap_s:
            _DSH_LAUNCH_HISTORY[key] = history
            return ("dsh 启动熔断：同一运行/步骤距离上次失败仅 %.1fs，"
                    "冷却 %.1fs 后再试" % (now - failed[-1]["ts"], gap_s))
        if len(history) >= max_launches:
            _DSH_LAUNCH_HISTORY[key] = history
            return ("dsh 启动熔断：同一运行/步骤在 %.0fs 内已失败 %d 次，"
                    "停止继续拉起子进程" % (
                        window_s, len([r for r in history if r["state"] == "failed"])))
        token = (key, object())
        history.append({"ts": now, "state": "active", "token": token})
        _DSH_LAUNCH_HISTORY[key] = history
    return token


def _dsh_launch_complete(token, ok):
    """Finalize a dsh launch reservation, clearing success or recording failure."""
    if not token:
        return
    key = token[0] if isinstance(token, tuple) and len(token) == 2 else None
    if key is None:
        return
    with _DSH_LAUNCH_LOCK:
        records = _DSH_LAUNCH_HISTORY.get(key, [])
        for index, record in enumerate(records):
            if record.get("token") == token:
                if ok:
                    records.pop(index)
                else:
                    record["state"] = "failed"
                    record["ts"] = time.monotonic()
                break
        if records:
            _DSH_LAUNCH_HISTORY[key] = records
        else:
            _DSH_LAUNCH_HISTORY.pop(key, None)

def _win_descendant_pids(root_pid):
    """Toolhelp32 快照枚举 root_pid 的全部后代 pid（子/孙/…，顺序无保证）。

    纯进程内调用，毫秒级返回，不依赖 taskkill/PowerShell 等外部进程——
    外部枚举在负载高的机器上会慢满超时（Get-CimInstance 真实装机 60s 实案），
    而杀树恰恰发生在最不该等的时刻。快照必须在开杀之前取：根进程一死，
    父子链接就断了，任何「先杀根再遍历树」的外部工具都找不到孩子。"""
    import ctypes
    TH32CS_SNAPPROCESS = 0x00000002

    class _PE32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong),
                    ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (-1, 0xFFFFFFFFFFFFFFFF, 4294967295):   # INVALID_HANDLE_VALUE
        return []
    links = []
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(_PE32)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            links.append((e.th32ProcessID, e.th32ParentProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    by_parent = {}
    for pid, ppid in links:
        by_parent.setdefault(ppid, []).append(pid)
    out, stack = [], [root_pid]
    while stack:
        for c in by_parent.get(stack.pop(), ()):
            out.append(c)
            stack.append(c)
    return out


def _win_pids_alive(pids):
    """任一 pid 仍存活即 True（TerminateProcess 后退出是异步收尸，需真死确认）。"""
    import ctypes
    k32 = ctypes.windll.kernel32
    QUERY_LIMITED = 0x1000
    STILL_ACTIVE = 259
    for p in pids:
        h = k32.OpenProcess(QUERY_LIMITED, False, p)
        if not h:
            continue   # 打不开 = 已退出或不可见，都不算活
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)) \
                    and code.value == STILL_ACTIVE:
                return True
        finally:
            k32.CloseHandle(h)
    return False


def _kill_tree(pid):
    """杀整棵进程树并确认真死。Windows：先在树存活时快照枚举全部后代，再逐个
    TerminateProcess（进程内毫秒级），最后短窗轮询确认停稳。

    旧实现「os.kill 根 → taskkill /T 兜底」有两个系统性坑（2026-10-13 18 个
    `opencode --version` 僵尸堆积实案）：①根先死，taskkill 的树遍历对着已死
    pid 什么都找不到，孙进程必然孤儿；②taskkill 在某些机器状态下挂死 30s+
    （2026-09-19 实案），等满预算会把每次超时杀进程都拖死。现全改为进程内
    快照 + 直杀，无外部进程可挂；杀不到的（快照后新生的孩子）由常驻孤儿
    清扫兜底。POSIX 靠 spawn 时的 start_new_session（pgid==pid）killpg 连孙带杀。

    返回是否确认整树真死：False = 停稳未知（孙进程可能仍在），调用方据此落
    quiescence_unknown 诊断，不改变任何终态语义。"""
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
            return True
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGKILL)   # 组杀失败退而杀根：子孙已不可确认
        except Exception:
            pass
        return False
    victims = [pid] + _win_descendant_pids(pid)
    issued = False
    for vp in victims:
        try:
            os.kill(vp, signal.SIGTERM)   # Windows 语义 = TerminateProcess
            issued = True
        except OSError:
            pass                          # 已退出/权限不可见，随它去
        except Exception:
            pass
    # 「真死才清」：给 2s 停稳确认窗（TerminateProcess 的退出是异步收尾），
    # 确认到全灭才算清空； issuing 了但没等到 = 停稳未知
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not _win_pids_alive(victims):
            return True
        time.sleep(0.1)
    return issued


def _drain_streams(proc, t_out, t_err, *, timeout=10):
    """kill_tree 后等管道线程读完剩余字节。

    daemon=True 线程 join 超时即结束（不会卡死主流程）。
    设计稿：docs/migration/01-defense-patterns.md §5B。
    参考 dsh docs/defensive-patterns.zh.md 第 21 行（"dispose 必须达到完全停稳"）。
    """
    deadline = time.time() + timeout
    for t in (t_out, t_err):
        remaining = max(0.0, deadline - time.time())
        if remaining > 0:
            t.join(timeout=remaining)
    # Killing the tree does not necessarily reap the root process on Windows.
    # Reap it with the remaining bounded budget before callers inspect
    # ``returncode``; otherwise long-running timeout tests can leak a live
    # Popen handle and emit ResourceWarning during interpreter shutdown.
    remaining = max(0.0, deadline - time.time())
    # proc 可为 None（旧契约：只收线程不收割进程的调用方，tests/test_runner_drain）
    if proc is not None and remaining > 0 and proc.poll() is None:
        try:
            proc.wait(timeout=remaining)
        except (subprocess.TimeoutExpired, OSError):
            pass


class _RecentBytes:
    """Thread-safe, fixed-size tail buffer for streaming error detection."""

    def __init__(self, max_bytes):
        self._max_bytes = max(1, int(max_bytes))
        self._data = bytearray()
        self._lock = threading.Lock()

    def append(self, chunk):
        if not chunk:
            return
        with self._lock:
            if len(chunk) >= self._max_bytes:
                self._data[:] = chunk[-self._max_bytes:]
                return
            self._data.extend(chunk)
            overflow = len(self._data) - self._max_bytes
            if overflow > 0:
                del self._data[:overflow]

    def snapshot(self):
        with self._lock:
            return bytes(self._data)


class _CapturedBytes:
    """Accumulate one process stream, optionally retaining only a fixed prefix."""

    def __init__(self, max_bytes=None):
        self._max_bytes = max_bytes
        self._chunks = []
        self._size = 0
        self._lock = threading.Lock()
        self.truncated = False

    def append(self, chunk):
        if not chunk:
            return
        with self._lock:
            if self._max_bytes is None:
                self._chunks.append(chunk)
                return
            remaining = self._max_bytes - self._size
            kept = chunk[:max(0, remaining)]
            if kept:
                self._chunks.append(kept)
                self._size += len(kept)
            if len(kept) < len(chunk):
                self.truncated = True

    def getvalue(self):
        with self._lock:
            return b"".join(self._chunks)


class _BoundedLogWriter:
    """Serialize concurrent stream writes and cap a process log's total size."""

    _MARKER = b"\n[CodeBee: process log truncated; remaining output omitted]\n"
    _OLD_MARKER = b"\n[CodeBee: older log content discarded]\n"

    def __init__(self, file_obj, max_bytes=None, shared_state=None):
        self._file = file_obj
        requested_max = (max(0, int(max_bytes))
                         if max_bytes is not None else None)
        # All writers for one normalized path use one lock and one byte
        # counter. This covers the two pipe-reader threads and side-channel
        # notes appended while a process is running. Cross-process writers are
        # intentionally outside this guarantee; use a file lock if that
        # deployment model is ever supported.
        self._state = shared_state or {
            "lock": threading.Lock(),
            "written": 0,
            "max_bytes": requested_max,
            "truncated": False,
            "dropped_bytes": 0,
            "stopped": False,
        }
        self._lock = self._state["lock"]
        with self._lock:
            try:
                file_obj.seek(0, os.SEEK_END)
                current_size = max(0, int(file_obj.tell()))
            except (OSError, ValueError):
                current_size = 0
            # Reconcile the state with the actual file when a writer is first
            # opened (or an external actor truncated it).
            self._state["written"] = current_size
            existing_max = self._state.get("max_bytes")
            if requested_max is not None and (
                    existing_max is None or requested_max < existing_max):
                self._state["max_bytes"] = requested_max
            self._max_bytes = self._state.get("max_bytes")
            if self._max_bytes is not None and current_size >= self._max_bytes:
                self._trim_existing()

    @property
    def _written(self):
        return int(self._state["written"])

    @_written.setter
    def _written(self, value):
        self._state["written"] = max(0, int(value))

    @property
    def truncated(self):
        return bool(self._state["truncated"])

    @truncated.setter
    def truncated(self, value):
        self._state["truncated"] = bool(value)

    @property
    def dropped_bytes(self):
        return int(self._state["dropped_bytes"])

    @dropped_bytes.setter
    def dropped_bytes(self, value):
        self._state["dropped_bytes"] = max(0, int(value))

    def _trim_existing(self):
        """Keep a short recent tail and reserve room for this invocation."""
        old_size = self._written
        max_bytes = self._state.get("max_bytes")
        if not isinstance(max_bytes, int):
            return
        if max_bytes <= 0:
            self._file.seek(0)
            self._file.truncate(0)
            self._written = 0
            self.dropped_bytes += old_size
            self.truncated = True
            self._state["stopped"] = True
            return

        marker_size = min(len(self._OLD_MARKER), max(1, max_bytes // 4))
        marker = self._OLD_MARKER[:marker_size]
        tail_budget = max(0, (max_bytes - marker_size) // 2)
        tail_size = min(old_size, tail_budget)
        self._file.seek(max(0, old_size - tail_size), os.SEEK_SET)
        tail = self._file.read(tail_size) if tail_size else b""
        self._file.seek(0)
        self._file.truncate(0)
        self._file.write(marker + tail)
        self._file.flush()
        self._written = len(marker) + len(tail)
        self.dropped_bytes += max(0, old_size - len(tail))
        self.truncated = True

    def write(self, data):
        payload = bytes(data)
        if not payload:
            return 0
        with self._lock:
            max_bytes = self._state.get("max_bytes")
            if not isinstance(max_bytes, int):
                self._file.write(payload)
                self._written += len(payload)
                return len(payload)
            if self._state["stopped"]:
                self.dropped_bytes += len(payload)
                return len(payload)

            remaining = max(0, max_bytes - self._written)
            if len(payload) <= remaining:
                self._file.write(payload)
                self._written += len(payload)
                return len(payload)

            # Reserve room for a visible marker so readers know the tail is
            # missing. The same total-size bound applies across append retries.
            marker_size = min(remaining, len(self._MARKER))
            if marker_size < min(len(self._MARKER), max_bytes):
                reclaim = min(self._written,
                              min(len(self._MARKER), max_bytes) - marker_size)
                if reclaim:
                    self._file.seek(-reclaim, os.SEEK_END)
                    self._file.truncate()
                    self._written -= reclaim
                    self.dropped_bytes += reclaim
                    remaining += reclaim
                    marker_size = min(remaining, len(self._MARKER))
            keep_size = max(0, remaining - marker_size)
            kept = payload[:keep_size]
            if kept:
                self._file.write(kept)
                self._written += len(kept)
            if marker_size:
                self._file.write(self._MARKER[:marker_size])
                self._written += marker_size
            self.dropped_bytes += len(payload) - len(kept)
            self.truncated = True
            self._state["stopped"] = True
            return len(payload)

    def flush(self):
        with self._lock:
            self._file.flush()

    def close(self):
        try:
            with self._lock:
                self._file.close()
        finally:
            state_path = getattr(self, "_state_path", None)
            if state_path is not None:
                self._state_path = None
                _release_log_state(state_path, self._state)


_LOG_STATES_LOCK = threading.Lock()
_LOG_STATES = {}


def _log_state_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _shared_log_state(path, max_bytes):
    key = _log_state_key(path)
    with _LOG_STATES_LOCK:
        state = _LOG_STATES.get(key)
        if state is None:
            state = {
                "lock": threading.Lock(),
                "written": 0,
                "max_bytes": (max(0, int(max_bytes))
                              if max_bytes is not None else None),
                "truncated": False,
                "dropped_bytes": 0,
                "stopped": False,
                "users": 0,
            }
            _LOG_STATES[key] = state
        elif max_bytes is not None:
            requested = max(0, int(max_bytes))
            existing = state.get("max_bytes")
            if existing is None or requested < existing:
                state["max_bytes"] = requested
        state["users"] += 1
        return state


def _release_log_state(path, state):
    key = _log_state_key(path)
    with _LOG_STATES_LOCK:
        if _LOG_STATES.get(key) is not state:
            return
        state["users"] = max(0, state["users"] - 1)
        if state["users"] == 0:
            _LOG_STATES.pop(key, None)


def _open_bounded_log(path, max_bytes):
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    state = None
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        state = _shared_log_state(path, max_bytes)
        # Unbuffered writes keep the shared counter aligned with file size even
        # when another helper opens the same path before an explicit flush.
        writer = _BoundedLogWriter(
            os.fdopen(fd, "a+b", buffering=0), max_bytes, state)
        writer._state_path = path
        return writer
    except Exception:
        os.close(fd)
        if state is not None:
            _release_log_state(path, state)
        raise


def _append_log_bytes(path, payload, max_bytes=DEFAULT_PROCESS_LOG_BYTES):
    if not path or not payload:
        return
    writer = None
    try:
        writer = _open_bounded_log(path, max_bytes)
        writer.write(payload)
        writer.flush()
    except OSError:
        pass
    finally:
        if writer is not None:
            try:
                writer.close()
            except OSError:
                pass


def _pipe_reader(stream, chunks, log_fh, stamp=None, recent=None):
    """持续读子进程管道并实时落盘。

    必须用 read1()：BufferedReader.read(n) 会阻塞到凑满 n 字节或 EOF，
    在长命令（npm 安装等）上等于"进程结束才一次性返回"，日志面板全程空白。
    read1() 只要有数据就返回，日志才能真正边跑边看。
    stamp：共享 [最后输出时刻]，停滞看门狗据此判定进程是否卡死。
    关闭也归本线程：EOF（或杀树后的异常）后自关。主线程绝不能 close()——
    close 要抢 BufferedReader 的锁，而锁被还在 read1 等 EOF 的本线程攥着，
    孙进程漏杀时它攥着管道到自然死，主线程就陪锁到天荒地老（2026-09-22
    run_command 超时杀 ping 实测：taskkill /T 失手 → close 卡满命令时长）。
    """
    try:
        while True:
            b = stream.read1(65536)
            if not b:
                break
            chunks.append(b)
            if recent is not None:
                recent.append(b)
            if stamp is not None:
                stamp[0] = time.time()
                if len(stamp) > 1:
                    stamp[1] = True
                if len(stamp) > 2:
                    stamp[2] = time.monotonic()
            if log_fh:
                try:
                    log_fh.write(b)
                    log_fh.flush()
                except Exception:
                    pass
    except Exception:
        pass                   # 杀树瞬间句柄作废：ValueError/OSError 都算正常收场
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _normalize_abort_markers(value):
    """把重复错误配置规范成 ``[(marker, count), ...]``。

    旧调用方传的是单个 ``(marker, count)`` 元组，必须继续支持；新的
    调用方可以传字典或元组列表，以便同一 CLI 同时对多种断流文案止损。
    """
    if not value:
        return []
    if isinstance(value, str):
        return [(value, 1)]
    if isinstance(value, dict):
        raw = list(value.items())
    elif (isinstance(value, (tuple, list)) and len(value) == 2
          and isinstance(value[0], str)):
        raw = [value]
    else:
        try:
            raw = list(value)
        except TypeError:
            return []
    out = []
    for item in raw:
        if isinstance(item, str):
            marker, limit = item, 1
        else:
            try:
                marker, limit = item
            except (TypeError, ValueError):
                continue
        marker = str(marker or "")
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            continue
        if marker:
            out.append((marker, limit))
    return out


def run_process(argv=None, shell_cmd=None, stdin_text=None, cwd=None, env=None,
                timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None,
                stall_timeout=0, repeat_abort=None, deadline=None,
                abort_markers=None, activity_timeout=0, audit_notes=None,
                max_capture_bytes=DEFAULT_PROCESS_CAPTURE_BYTES,
                max_log_bytes=DEFAULT_PROCESS_LOG_BYTES):
    """通用子进程执行：并发读管道防死锁；超时/取消杀整棵进程树。

    stall_timeout：停滞看门狗（秒，0=关闭）——超过该时长 stdout/stderr 无任何
    新输出即判卡死，提前杀树返回（timed_out=True + stalled=True）。只对输出
    持续流动的 CLI 开（codex JSONL 事件流）；claude json 到结束才一次性输出，
    开了会把正常长任务误杀。

    activity_timeout：基础 timeout 到期后，最近仍有真实输出的流式 CLI 允许继续
    运行的最长静默窗口（秒，0=关闭）。它独立于 stall_timeout，避免环境把
    停滞看门狗设为 0 后，Claude/Codex 又在基础超时处被硬杀；窗口按每次输出
    滑动，不设过紧的总时长上限。调用方传入的 deadline 仍是整个任务的硬上限。

    repeat_abort：可选 (marker, count)，也接受多个 ``(marker, count)``；同一
    致命错误达到次数即提前杀树，处理 CLI 自身不断打印重连消息、因此永远
    触发不了静默看门狗的假运行。abort_markers 是兼容扩展，用于不改变旧
    repeat_abort 参数形状的情况下追加多种一次性断流标记。

    deadline：可选的 ``time.monotonic()`` 绝对截止时刻。它比 timeout 优先，
    到点会杀掉进程树并返回 deadline_exceeded=True，调用方可把整个任务收口
    为 timeout，而不是把它误记成普通供应商失败。

    audit_notes：可选字符串列表，逐行追加在审计头命令行下方——用于把
    argv/env 里看不出的运行形态钉进日志（如 claude 凭据=绑定链注入/本机默认），
    事后诊断不用靠猜。只写形态名，绝不写密钥值。

    max_capture_bytes：stdout/stderr 各自的内存捕获上限，默认 16 MiB。超限后
    仍排空子进程管道并流式写步骤日志，但只保留每路前缀、设置 output_truncated，
    并将本次调用标记失败。显式传 None 可保留完整捕获行为；模型 CLI 使用更低上限。
    max_log_bytes：步骤日志文件累计上限，默认 32 MiB；超限后停止追加并写截断标记，
    返回 log_truncated/log_dropped_bytes。显式传 None 可关闭上限。

    返回 {ok, exit_code, stdout, stderr, duration, cancelled, timed_out, stalled,
    deadline_exceeded, abort_marker}；杀树后未能确认整树清空时额外带
    quiescence_unknown=True（孙进程可能仍在，诊断用，不改变终态语义）。
    """
    if shell_cmd:
        # shell 串的解析器随平台：重定向/引号语法两边通用，只是解释器不同
        argv = ["cmd", "/c", shell_cmd] if os.name == "nt" else ["/bin/sh", "-c", shell_cmd]
    # Windows 上含内层双引号的 cmd /c 串（powershell -Command "irm … | iex" 等）
    # 不能按 argv 列表 spawn：list2cmdline 给整串再包一层引号、内层引号转义成
    # \"，cmd 老式剥离后 PowerShell 把 -Command 的值当字符串字面量求值——原样
    # 回显命令即退出 0，脚本一行未跑还报成功（2026-10-22 minimax-code/goose/
    # trae 安装假「完成」实案，本机复现实锤）。改原始命令行字符串 spawn：cmd
    # /s /c 只剥最外层一对引号，内层引号原样到达目标程序；无引号命令维持
    # argv 列表，热路径零变化。
    spawn_args = [str(a) for a in argv]
    if os.name == "nt" and shell_cmd and '"' in shell_cmd:
        spawn_args = 'cmd /s /c "' + shell_cmd + '"'
    if argv is None:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "argv 为空",
                "duration": 0.0, "cancelled": False, "timed_out": False,
                "stalled": False, "deadline_exceeded": False, "abort_marker": None,
                "output_truncated": False, "log_truncated": False,
                "log_dropped_bytes": 0}
    try:
        max_capture_bytes = (max(0, int(max_capture_bytes))
                             if max_capture_bytes is not None else None)
    except (TypeError, ValueError, OverflowError):
        max_capture_bytes = 0
    try:
        max_log_bytes = (max(0, int(max_log_bytes))
                         if max_log_bytes is not None else None)
    except (TypeError, ValueError, OverflowError):
        max_log_bytes = 0
    try:
        deadline = float(deadline) if deadline is not None else None
    except (TypeError, ValueError):
        deadline = None
    try:
        timeout = max(0.0, float(timeout))
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT)
    try:
        activity_timeout = max(0.0, float(activity_timeout))
    except (TypeError, ValueError):
        activity_timeout = 0.0
    if deadline is not None and time.monotonic() >= deadline:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": "[任务总时限已到，未启动子进程]", "duration": 0.0,
                "cancelled": False, "timed_out": True, "stalled": False,
                "deadline_exceeded": True, "abort_marker": None,
                "output_truncated": False, "log_truncated": False,
                "log_dropped_bytes": 0}
    aborts = _normalize_abort_markers(repeat_abort)
    aborts.extend(_normalize_abort_markers(abort_markers))
    full_env = scrub_env(os.environ.copy(), mode="drop")
    if env:
        # 5A：env 关键字环境变量注入用户传入的 env（属于有意注入，例如模型 API key）
        full_env.update({str(k): str(v) for k, v in env.items()})
    log_fh = None
    if log_path:
        try:
            log_fh = _open_bounded_log(log_path, max_log_bytes)
        except OSError:
            # A diagnostic log must not prevent an otherwise valid CLI call.
            log_fh = None
    # 审计：调用下达前先把「执行的命令 + 发给智能体的指令原文」写进日志，
    # 运行中点开步骤就能看到"编排者下了什么令"，不用等结束猜。
    # env 绝不写（含 API key）；argv 里只有 base_url/env_key 名，无密钥值。
    # 指令超 12000 字符截断（章节正文可能很长），标注原始长度防误读。
    if log_fh:
        try:
            head = ["===== 下达 %s =====" % time.strftime("%Y-%m-%d %H:%M:%S"),
                    "$ " + " ".join(redact_command(argv))]
            for _note in (audit_notes or []):
                if _note:
                    head.append("· " + str(_note))
            if stdin_text:
                capped = stdin_text[:12000]
                head.append("--- 指令（%d 字符%s）---" % (
                    len(stdin_text), "，已截断" if len(stdin_text) > 12000 else ""))
                head.append(capped)
            log_fh.write(("\n".join(head) + "\n--- 输出 ---\n").encode("utf-8", "replace"))
            log_fh.flush()
        except Exception:
            pass
    try:
        try:
            proc = subprocess.Popen(
                spawn_args, cwd=cwd, env=full_env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                start_new_session=(os.name != "nt"))  # POSIX 需独立进程组供 killpg 杀树；Windows 忽略该参数
        except FileNotFoundError as e:
            # filename=None 时 %r 只有一句「系统找不到指定的文件」，无从排查
            # （2026-09-30 claude winget 迁移实案：服务 PATH 快照过旧）。
            # e.filename 在场（如 POSIX cwd 缺失）时点名真实缺失路径，不猜 argv[0]。
            tgt = str(e.filename) if getattr(e, "filename", None) \
                else (str(argv[0]) if argv else "")
            return {"ok": False, "exit_code": None, "stdout": "",
                    "stderr": ("启动失败：找不到可执行文件「%s」。CLI 未安装或已卸载；"
                               "若刚安装，重启 CodeBee 服务让 PATH 生效即可。"
                               % tgt),
                    "duration": 0.0, "cancelled": False, "timed_out": False,
                    "stalled": False, "deadline_exceeded": False, "abort_marker": None,
                    "output_truncated": False,
                    "log_truncated": bool(log_fh and log_fh.truncated),
                    "log_dropped_bytes": int(log_fh.dropped_bytes) if log_fh else 0}
        except Exception as e:
            return {"ok": False, "exit_code": None, "stdout": "",
                    "stderr": "启动失败: %r" % e, "duration": 0.0,
                    "cancelled": False, "timed_out": False, "stalled": False,
                    "deadline_exceeded": False, "abort_marker": None,
                    "output_truncated": False,
                    "log_truncated": bool(log_fh and log_fh.truncated),
                    "log_dropped_bytes": int(log_fh.dropped_bytes) if log_fh else 0}
        # 挂巢（beekeeper）：服务无论以何种方式退出（含被硬杀），蜜蜂 CLI
        # 一起带走。挂巢失败只降级，绝不影响本步起跑。
        beekeeper.adopt(proc)
        out_capture = _CapturedBytes(max_capture_bytes)
        err_capture = _CapturedBytes(max_capture_bytes)
        stamp = [time.time(), False, time.monotonic()]  # [墙钟、是否有输出、单调时钟]
        out_recent = _RecentBytes(8000)
        err_recent = _RecentBytes(8000)
        t_out = threading.Thread(
            target=_pipe_reader,
            args=(proc.stdout, out_capture, log_fh, stamp, out_recent), daemon=True)
        t_err = threading.Thread(
            target=_pipe_reader,
            args=(proc.stderr, err_capture, log_fh, stamp, err_recent), daemon=True)
        t_out.start()
        t_err.start()
        if stdin_text is not None:
            def _feed():
                try:
                    proc.stdin.write(stdin_text.encode("utf-8"))
                except Exception:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
            threading.Thread(target=_feed, daemon=True).start()
        start = time.time()
        start_mono = time.monotonic()
        cancelled = timed_out = stalled = repeat_aborted = False
        deadline_exceeded = False
        abort_marker = None
        tree_quiet = None   # 杀树结果：None=未杀，True=确认清空，False=停稳未知
        while True:
            try:
                proc.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                tree_quiet = _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=10)
                break
            if deadline is not None and time.monotonic() >= deadline:
                deadline_exceeded = True
                timed_out = True
                tree_quiet = _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=5)
                break
            elapsed = time.monotonic() - start_mono
            if elapsed > timeout:
                # Claude/Codex 的工具循环可能持续吐 stream-json 事件而迟迟
                # 不给最终 result。活动延长与 stall 看门狗分离：无输出的挂死
                # 仍按原单模型 timeout 快速换将；有输出则按最近输出滑动续跑。
                active = (activity_timeout > 0 and stamp[1]
                          and time.monotonic() - stamp[2] < activity_timeout)
                if not active:
                    # A slow hosted runner can wake the wait timeout after the
                    # child has already exited.  Reap that race before killing
                    # an otherwise successful process at the activity boundary.
                    if proc.poll() is not None:
                        break
                    timed_out = True
                    tree_quiet = _kill_tree(proc.pid)
                    _drain_streams(proc, t_out, t_err, timeout=5)
                    break
            if stall_timeout and time.time() - stamp[0] > stall_timeout:
                # 停滞看门狗：长静默多为卡死（网关挂起/CLI 假死），与其耗满总
                # 超时不如提前杀——错误按超时归类，走既有的换模型/换将链路
                stalled = True
                timed_out = True
                tree_quiet = _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=5)
                break
            if aborts:
                try:
                    recent_text = decode_output(
                        out_recent.snapshot() + b"\n" + err_recent.snapshot())
                    for marker, limit in aborts:
                        if recent_text.count(marker) >= limit:
                            repeat_aborted = True
                            abort_marker = marker
                            timed_out = True
                            tree_quiet = _kill_tree(proc.pid)
                            _drain_streams(proc, t_out, t_err, timeout=5)
                            break
                    if repeat_aborted:
                        break
                except (TypeError, ValueError):
                    pass
        duration = round(time.time() - start, 1)
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        exit_code = proc.returncode
        stdout = decode_output(out_capture.getvalue())
        stderr = decode_output(err_capture.getvalue())
        output_truncated = out_capture.truncated or err_capture.truncated
        # stdout/stderr 的 close 已归 _pipe_reader 自线程（EOF 后自关）：这里再
        # close 会抢 read1 手里的锁，孙进程漏杀攥着管道时就死等（见其 docstring）。
        # stdin 无读线程，_feed 写完自关；这里兜底一次（DEVNULL 时本就无事）。
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if cancelled:
            stderr += "\n[已被用户取消]"
        elif output_truncated:
            stderr += ("\n[输出超过捕获上限：stdout/stderr 各最多 %s 字节，"
                       "结果不完整，已标记失败]" % max_capture_bytes)
        elif repeat_aborted:
            limit = next((n for m, n in aborts if m == abort_marker), "?")
            if len(aborts) == 1 and abort_markers is None:
                # 保留旧版 UI/测试已经依赖的文案；多标记配置使用更具体的
                # 标记名，方便定位究竟是哪一种断流触发了止损。
                stderr += "\n[同一网络错误重复 %s 次，已提前终止进程树]" % limit
            else:
                stderr += "\n[错误标记 %s 重复 %s 次，已提前终止进程树]" % (
                    abort_marker, limit)
        elif deadline_exceeded:
            stderr += "\n[任务总时限已到，已终止进程树]"
        elif stalled:
            stderr += "\n[输出停滞 %ss，已终止进程树]" % stall_timeout
        elif timed_out:
            stderr += "\n[超时 %ss，已终止进程树]" % timeout
        result = {
            "ok": (exit_code == 0 and not cancelled and not timed_out
                   and not output_truncated),
            "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": duration, "cancelled": cancelled, "timed_out": timed_out,
            "stalled": stalled, "deadline_exceeded": deadline_exceeded,
            "abort_marker": abort_marker, "output_truncated": output_truncated,
            "log_truncated": bool(log_fh and log_fh.truncated),
            "log_dropped_bytes": int(log_fh.dropped_bytes) if log_fh else 0,
            "activity_extended": bool(activity_timeout and stamp[1]
                                       and duration > timeout),
            "activity_timeout": activity_timeout,
            "last_output_age": max(0.0, time.time() - stamp[0]),
        }
        if tree_quiet is False:
            # 杀树完成但未确认整树清空（taskkill 卡死放弃 / killpg 失手只杀了
            # 根）：孙进程可能仍在运行。只落诊断标记，不改变 cancelled/timed_out
            # 等任何终态语义——KEY 冷却豁免、健康惩罚仍按结构化取消标记判断。
            result["quiescence_unknown"] = True
            result["stderr"] += "\n[进程树未确认清空：孙进程可能仍在运行]"
        return result
    finally:
        if log_fh:
            try:
                log_fh.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 各家适配





_CODEX_NUDGE_PROMPT = (
    "你上一轮执行了调查/工具命令，但没有输出最终结论就结束了。"
    "请基于已获取的信息直接给出最终回答：先结论、后依据。"
    "除非确有必要，不要再执行新命令；最终结论必须以文字形式完整给出。"
)




def _codex_nudge_recover(eff_agent, sid, readonly, model, env, workdir,
                         timeout, deadline, cancel_event, log_path,
                         stall_t, activity_t):
    """静默空答补答：带同一会话原地追问一次结论，返回 (是否取得, 回答, usage)。

    只认「非静默且有正文」的追问结果——追问轮再犯静默空答视同未恢复，
    由调用方落 partial，不无限追问。"""
    argv, stdin_text, _p, tmp_files = _build_call(
        eff_agent, "codex", sid, readonly, model,
        _CODEX_NUDGE_PROMPT, workdir=workdir)
    try:
        res = run_process(argv=argv, stdin_text=stdin_text, cwd=workdir, env=env,
                          timeout=timeout, deadline=deadline,
                          cancel_event=cancel_event, log_path=log_path,
                          stall_timeout=stall_t, activity_timeout=activity_t,
                          audit_notes=["静默空答自动追问（同会话补答一次）"])
    finally:
        for tf in tmp_files:
            try:
                os.remove(tf)
            except OSError:
                pass
    text, usage, _sid2 = _parse_codex_jsonl(res.get("stdout") or "")
    ok = (bool(str(text or "").strip())
          and not _codex_silent_no_answer(res.get("stdout") or "")[0])
    return ok, str(text or "").strip(), usage










def _transient_error(err):
    """Shared retryable upstream/transport classification."""
    return _shared_transient_error(err)


def _quota_error(err):
    """Quota and rate-limit checks share the taxonomy used by key health."""
    return _shared_quota_error(err)


def _auth_error(err):
    """Only explicit credential failures are auth errors; 403 is separate."""
    if _permission_error(err):
        return False
    return _shared_auth_error(err)


def _permission_error(err):
    """识别明确的 HTTP 403 权限拒绝；它是上游故障，不等同于无效 API Key。"""
    return _shared_forbidden_error(err)


def _attempt_credential(att):
    """绑定链中凭据的非敏感身份；不把密钥值写入日志或尝试记录。"""
    return (str((att or {}).get("provider_id") or ""),
            str((att or {}).get("key_id") or ""))


# 限流（429）专项：与一般瞬态不同，它是「等一个窗口就常能自愈」的病——
# 2026-09-22 四连败实测：claude ENOTFOUND 换将 codex 后仍与同一上游撞 429，
# 链上无第三条路时整步立刻判死；而限流窗口通常按分钟计，原地等一个窗口
# 再试一次，好过把整轮 run 直接烧成 failed（欠费不同源，不适用宽限）。
RATE_LIMIT_GRACE_S = 45      # 宽限等待时长：限流窗口通常按分钟计，等一个再试
RATE_LIMIT_GRACE_N = 1       # 每步宽限次数：只兜一次，防限流长拖整轮时间

# Cline 4.1.25 (2026-10-16) added bounded retries when a provider breaks a
# response stream.  Replaying a write-capable coding turn can duplicate side
# effects, so CodeBee only uses this budget for readonly/planning/review calls.
STREAM_RETRY_N = 2
STREAM_RETRY_DELAY_S = 1.0
_STREAM_RETRY_MARKERS = (
    "stream disconnected", "stream closed", "connection reset",
    "connection aborted", "incomplete stream", "bodytimeouterror",
    "body timeout",
)


def _stream_retryable_failure(res, error, *, kind, readonly):
    """Whether a readonly stream failed in a way safe to replay in place."""
    if not readonly or kind not in ("codex", "claude"):
        return False
    if not isinstance(res, dict) or res.get("ok"):
        return False
    if res.get("cancelled") or res.get("timed_out") or res.get("deadline_exceeded"):
        return False
    if res.get("abort_marker"):
        return True
    blob = " ".join(str(res.get(key) or "") for key in ("stderr", "stdout"))
    blob = (str(error or "") + " " + blob).lower()
    return any(marker in blob for marker in _STREAM_RETRY_MARKERS)


def _stream_retry_wait(cancel_event, seconds):
    """Wait briefly between readonly stream retries while honoring cancel."""
    try:
        seconds = max(0.0, float(seconds))
    except (TypeError, ValueError):
        seconds = STREAM_RETRY_DELAY_S
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel_event is not None and cancel_event.is_set():
            return False
        time.sleep(min(0.25, max(0.01, end - time.monotonic())))
    return cancel_event is None or not cancel_event.is_set()


def _stream_retry_cancelled(out, cancel_event):
    """Propagate cancellation observed during retry backoff to routing gates."""
    if cancel_event is None or not cancel_event.is_set():
        return False
    out["ok"] = False
    out["error"] = "调用已取消"
    out["error_code"] = ErrorCode.CANCELLED
    raw = out.get("raw")
    if isinstance(raw, dict):
        raw["cancelled"] = True
    return True


def _refusal_error(err):
    return classify_error_text(err) == ErrorCode.VENDOR_REFUSAL


def _attempt_upstream(att):
    """返回规范化上游身份；同一 host 换模型不重复烧超时。"""
    provider = (att or {}).get("provider") or {}
    base_url = str(provider.get("base_url") or "").strip()
    normalized = upstream.normalize_upstream(base_url)
    if normalized:
        return normalized
    provider_id = str((att or {}).get("provider_id") or "").strip()
    return "provider:" + provider_id if provider_id else "unknown"


def _claude_drift_note(att_model, parsed):
    """请求模型 vs 网关实服模型不一致时的注记；一致或无从判断返回空。"""
    served = (parsed or {}).get("served_models") or []
    if not served or not att_model or served == [att_model]:
        return ""
    return "[模型漂移] 请求 %s，上游实服 %s" % (att_model, ",".join(served))


_LOGIN_FIX_HINTS = {
    "claude": ("① 绑定页为 claude-code 配置供应商链（如 Bigmodel anthropic 面）；"
               "② 在 ~/.claude/settings.json 的 env 里配 ANTHROPIC_BASE_URL"
               " + ANTHROPIC_AUTH_TOKEN"),
    "codex": ("① 绑定页为 codex 配置供应商链；② 本机执行 codex login 完成登录"),
}


def _login_hint(kind, error_code, error_text):
    """凭据注入型 CLI 撞登录闸门的修复指引；非缺凭据或非登录措辞返回空。

    「无链回落本机默认」时 runner 不注入任何凭据（_resolve_attempts 的 env
    恒空），本机 CLI 又没登录 → 每步必死还烧满退避。把修法直接写进错误串，
    别让一句「Not logged in」冒充死因（2026-09-25 Mac 端 claude 实案：init
    JSON 里 apiKeySource=none，链上零候选可换将）。措辞闸保持窄词：
    login failed / invalid credentials 是 AUTH 语义，不进缺凭据指引。"""
    if error_code_value(error_code) != ErrorCode.MISSING_CREDENTIAL:
        return ""
    low = str(error_text or "").lower()
    if ("not logged in" not in low and "please run /login" not in low
            and "please log in" not in low):
        return ""
    fix = _LOGIN_FIX_HINTS.get(kind) or (
        "① 绑定页为该 CLI 配置供应商链（带密钥的供应商条目）；"
        "② 完成该 CLI 的本机登录或在其自家配置里注入 API Key")
    return ("；%s CLI 未登录且本步未注入绑定链凭据，修复二选一：%s"
            % (kind, fix))


def _cred_audit_note(kind, att, argv):
    """步骤审计头的凭据模式行（run_process audit_notes 用，所有 CLI 通用）。

    注入真值按 kind 分流：claude 只认 argv 里的 --settings（_build_call 只在
    env 带 ANTHROPIC_* 时落它——ORCH_API_KEY 这类别家约定的键对 claude 无效，
    不得按 env 非空误报「绑定链注入」）；其余 kind 的 env 直接传给进程，
    env 非空即有效注入。条目级 no_cred 标记（resolve_binding 给纯模型条目
    打标）独立点名。只写形态名与变量名，密钥值绝不入日志。
    2026-09-25 Mac 实案：纯模型条目零注入，进程死在登录闸门后只能靠 init
    JSON 的 apiKeySource=none 反推死因；起跑就把凭据模式钉进日志头，且不限
    claude——codex/kimi/qwen 等 generic 的零凭据起跑同样一眼可读。"""
    if kind == "claude":
        if any(str(a) == "--settings" for a in (argv or [])):
            return "凭据=绑定链（一次性 --settings 注入 ANTHROPIC_*）"
        if att.get("no_cred"):
            return ("凭据=无注入（链上纯模型条目零凭据，只传 -m 走本机 claude "
                    "登录态；未登录则 -p 必死在登录闸门）")
        return ("凭据=本机默认（未注入 ANTHROPIC_*，走本机 claude 登录态；"
                "未登录则 -p 必死在登录闸门）")
    if att.get("no_cred"):
        return ("凭据=无注入（链上纯模型条目零凭据，只传 -m 走本机 CLI 登录态；"
                "本机未登录/未配置则必死在凭据闸门）")
    env = att.get("env") or {}
    if env:
        return "凭据=绑定链注入（env: %s）" % ",".join(sorted(env))
    return ("凭据=本机默认（本步未注入任何凭据 env，走 CLI 本机登录态/配置；"
            "未登录则必死在登录/凭据闸门）")


def _log_note(log_path, text, max_bytes=DEFAULT_PROCESS_LOG_BYTES):
    if not log_path or not text:
        return
    _append_log_bytes(log_path, ("\n[%s]\n" % text).encode("utf-8", "replace"),
                      max_bytes=max_bytes)


def _finish_attempt_ledger(ledger, run_id, command_context, att, out):
    """Close one provider-attempt row, including an in-place stream retry."""
    if not ledger:
        return
    raw = out.get("raw") if isinstance(out.get("raw"), dict) else {}
    usage = out.get("usage") or {}
    retry_class = ""
    if not out.get("ok"):
        code = error_code_value(out.get("error_code"))
        retry_class = "rate_limit" if _rate_limited(out.get("error")) else (
            "transient" if _transient_error(out.get("error")) else code)
    try:
        ledger.finish(
            run_id or "unbound",
            command_context.get("role", "")
            if isinstance(command_context, dict) else "",
            ((att.get("provider") or {}).get("name", "")
             if isinstance(att.get("provider"), dict) else ""),
            out.get("model") or att.get("model") or "", ok=bool(out.get("ok")),
            error=out.get("error") or "", error_code=out.get("error_code"), usage=usage,
            duration_s=raw.get("duration", 0.0), retry_class=retry_class,
            ttft_ms=(raw.get("first_token_ms") or 0),
            tokens_per_sec=(raw.get("tokens_per_sec") or 0),
            attempt=out.get("attempt_ordinal", 1))
    except Exception:
        pass


def _rate_limited(err):
    return _shared_rate_limited_error(err)


# 审计日志错误行探针：只认强信号，避免把回显提示词里的普通词当错误
_LOG_ERR_HINT = re.compile(
    r"(\b403\b|forbidden|request not allowed|permission denied|access denied"
    r"|\b429\b|\b402\b|rate[_ ]?limit|quota|余额|欠费|insufficient|billing|arrears"
    r"|\b401\b|unauthorized|unauthorised|invalid[_ ]api[_ ]key|authentication[_ ]error"
    r"|ENOTFOUND|ECONNREFUSED|failed to run prompt|api_error|overloaded)",
    re.I)

# 「路径:行号(:列号):内容」形态（grep -n / 编译器诊断 / ripgrep 命中行）。
# 前缀允许 ./ ../ 绝对盘符与 ~；结尾必须有真实扩展名，避免误吞普通报错。
_GREP_HIT_LINE_RE = re.compile(
    r"^(?:\.{0,2}/|[A-Za-z]:[\\/]|~)?[^\s:]+\.[A-Za-z0-9]{1,10}"
    r":\d{1,6}(?::\d{1,6})?(?::|\s|$)")


def _json_envelope_message(ev, depth=0):
    """从解析后的 JSON 错误信封里挖人话 message（opencode/codex/grok 形态各异）。"""
    if depth > 4:
        return ""
    if isinstance(ev, str):
        return ev.strip()
    if isinstance(ev, dict):
        for key in ("message", "error", "data", "detail", "result", "errors"):
            if key in ev:
                found = _json_envelope_message(ev[key], depth + 1)
                if found:
                    return found
    if isinstance(ev, list) and ev:
        return _json_envelope_message(ev[0], depth + 1)
    return ""


_ERROR_ENV_MAX_LINES = 40


def _absorb_error_envelope(lines, idx, first):
    """`Error: {` 多行 JSON 信封：opencode 把上游错误打成「Error: 」+ 缩进
    JSON 体（2026-10-16 公司网关 403 实案：探针按行切只剩首行 `Error: {`，
    健康告警与错误分类全瞎）。向后吞行直到花括号配平并解析，抽出 message
    拼回 `Error: <name>: <message>`；配不平或解析不动 → 维持原首行不吞
    （保守，别把模型正文误收进错误）。返回（替换后的行, 继续扫描的下标）。"""
    buf = first
    depth = buf.count("{") - buf.count("}")
    j = idx
    while depth > 0 and j < len(lines) and j - idx < _ERROR_ENV_MAX_LINES:
        buf += "\n" + lines[j].strip()
        depth = buf.count("{") - buf.count("}")
        j += 1
    if depth > 0:
        return first, idx
    brace = buf.find("{")
    try:
        ev = json.loads(buf[brace:])
    except (ValueError, TypeError):
        return first, idx
    detail = _json_envelope_message(ev)
    if not detail:
        return " ".join(buf.split()), j
    if isinstance(ev, dict):
        name = str(ev.get("name") or "")
        if name and name.lower() not in detail.lower():
            detail = "%s: %s" % (name, detail)
    prefix = buf[:brace].strip()
    return ("%s %s" % (prefix, detail)).strip(), j


def _cli_error_lines(text):
    """Extract error events without treating model prose/tool output as errors."""
    hits = []
    lines = clean_cli_text(text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if re.match(r"^\d+:\s", line) or line.startswith(("$ ", "【命令】")):
            continue  # grep/Read source line numbers and echoed commands.
        if _GREP_HIT_LINE_RE.match(line):
            # grep「路径:行号:内容」命中行是模型工具的扫描输出，不是 CLI 自己
            # 的报错；行号里的 429/5xx 会伪装成上游信号（2026-10-21 实案：
            # types.gen.d.ts:429:export type Todo 把免费通道推进 down 告警）。
            continue
        if line.startswith("{"):
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue  # A partial JSON event is not a reliable error signal.
            if not isinstance(ev, dict):
                continue
            typ = ev.get("type")
            if typ == "result" and ev.get("is_error"):
                line = str(ev.get("result") or ev.get("errors") or "")
            elif typ in ("error", "turn.failed"):
                detail = ev.get("error") or ev.get("message") or ""
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("data") or detail
                line = str(detail)
            elif typ == "system" and ev.get("subtype") == "api_retry":
                line = str(ev.get("error") or "")
            else:
                continue
            if line:
                hits.append(line)
        elif (_LOG_ERR_HINT.search(line)
              or re.match(r"^(?:error|fatal|panic|\w*Error):", line, re.I)):
            if line.endswith("{"):
                # 彩色「Error: 」+ 缩进 JSON 体多行信封，见 _absorb_error_envelope
                line, i = _absorb_error_envelope(lines, i, line)
            hits.append(line)
    return hits


def _failure_output_detail(res):
    """Keep stderr diagnostics even when stdout ends with a large tool dump."""
    stderr = clean_cli_text(res.get("stderr") or "").strip()
    stdout = res.get("stdout") or ""
    errors = (_cli_error_lines(stderr)[-1:]
              + _cli_error_lines(stdout)[-1:])
    if errors:
        return "；".join(error[:290] for error in errors)
    # Translate structured events before truncation, so UUIDs and heartbeats
    # cannot displace the last useful message. Keep both streams independently.
    stdout = clean_cli_text(pretty_cli_log(stdout)).strip()
    if stderr and stdout:
        return "stderr: %s；stdout: %s" % (stderr[-300:], stdout[-250:])
    return (stderr or stdout)[-600:]


def _augment_error_from_log(out, log_path, limit=300):
    """stdout/stderr 尾段没有错误信号时，从流式审计日志里补最后一条错误行。
    真错误有时只走流式管道（kimi 撞 429 时捕获输出只剩启动横幅），不补的
    话路由层与用户都只看到「退出码 1；stderr/stdout: kimi version 2.0.2」。"""
    if not log_path or not out.get("error"):
        return
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            blob = fh.read().decode("utf-8", "ignore")
    except OSError:
        return
    # Audit instructions are not CLI output; probe only the latest call if its
    # header is still in the tail window.
    blob = blob.rsplit("--- 输出 ---", 1)[-1]
    hits = _cli_error_lines(blob)
    if not hits:
        return
    out["error"] = out["error"][:450] + "；日志错误行: " + hits[-1][:limit]


def _grace_wait(cancel_event, seconds, log_path=None, why=""):
    """链尾限流宽限等待：小片睡眠随时响应取消。返回 False=已取消（别再重试）。"""
    if log_path:
        _append_log_bytes(
            log_path,
            ("\n[限流宽限] %s：等待 %ds 后原地重试一次\n"
             % (why or "上游限流（429）", seconds)).encode("utf-8", "replace"))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel_event is not None and cancel_event.is_set():
            return False
        time.sleep(min(3, max(0.5, end - time.monotonic())))
    return cancel_event is None or not cancel_event.is_set()


def attempt_cancelled(res):
    """这次调用是不是被用户取消的。

    取消是用户意志不是上游表现：KEY 冷却、供应商健康、评测作废三本惩罚账都必须
    跳过（docs/execution-standard.md 统一执行算法第 3 条与 CANCELLED 行）。只看
    错误文案不行——取消串是「取消；stderr/stdout: <被杀进程尾部>」，尾部碰巧含
    429/欠费字样就会把健康 KEY 打进 30 分钟冷却。
    """
    res = res if isinstance(res, dict) else {}
    raw = res.get("raw") if isinstance(res.get("raw"), dict) else {}
    return (bool(res.get("cancelled")) or bool(raw.get("cancelled"))
            or error_code_value(res.get("error_code")) == ErrorCode.CANCELLED)


def _report_key(att, out):
    """把这次尝试的结果回写到 KEY 账本：欠费/失败 → 冷却，成功 → 清错误。

    回写失败绝不能影响主流程（账本是旁路），所以整体吞异常。
    """
    pid, kid = att.get("provider_id") or "", att.get("key_id") or ""
    raw = out.get("raw") if isinstance(out.get("raw"), dict) else {}
    if not (pid and kid) or attempt_cancelled(out) or raw.get("output_truncated"):
        return
    try:
        from . import modelhub
        if out.get("ok"):
            modelhub.note_key_ok(pid, kid)
        elif out.get("error"):
            modelhub.note_key_error(pid, kid, out["error"])
    except Exception:
        pass


# CLI 通道停摆横幅（exit 0 假成功家族）：免费服务下线时 CLI 打一行 error
# 横幅就正常退出（2026-10-22 mimo 实案 "error: MiMo free API service has
# ended. Sign in or configure a third-party API."）。签名从窄：只收确切短语，
# 不做 "error:" 前缀泛匹配——转录里回显提示词时会把无辜运行误杀。
_GENERIC_DEAD_BANNERS = (
    "free api service has ended",
)


def _generic_dead_banner(res):
    """exit 0 但输出里带停摆横幅时返回横幅行（供错误文案），否则 None。"""
    if res.get("ok") is not True or res.get("cancelled") or res.get("timed_out"):
        return None
    for stream in (res.get("stderr"), res.get("stdout")):
        for line in str(stream or "").splitlines():
            plain = clean_cli_text(line).strip().lower()
            if not plain:
                continue
            for marker in _GENERIC_DEAD_BANNERS:
                if marker in plain:
                    return clean_cli_text(line).strip()[:300]
    return None


def _classify_failure(res, *, parsed=None, kind="", attempt_done=False, empty_output=False):
    """根据 run_process 结果 + 解析结果，返回 ErrorCode 字符串。

    设计稿：docs/migration/01-defense-patterns.md §5D + §2D。

    Args:
        res: run_process 返回的 dict（含 ok/cancelled/timed_out/exit_code/stdout/stderr）
        parsed: claude 解析后的 dict（或 None）；codex 由 caller 处理
        kind: "claude" | "codex" | "generic" | ...
        attempt_done: claude 空响应是否已重试一次（True=已是第二次）
        empty_output: 调用方已确认 stdout 为空（用于 generic/codex 的空响应）

    Returns:
        ErrorCode 枚举值字符串；成功返回 ""。
    """
    if res.get("cancelled"):
        return ErrorCode.CANCELLED
    if res.get("timed_out"):
        # 超时只是死法不是死因：输出尾部若带账号级死错（codex 内部 Reconnecting
        # 一直重试到外部超时，真凶是 403 用户额度不足¥0——2026-10-22 vsllm 实案），
        # 以死因为准：换将/健康熔断按终态处置，而不是当瞬态超时盲重试死号。
        detail = "\n".join((str(res.get("stderr") or ""),
                            str(res.get("stdout") or ""),
                            str((parsed or {}).get("text") or "")))
        dead = classify_error_text(detail)
        if dead is not None and is_account_dead_error(dead):
            return dead
        return ErrorCode.TIMEOUT
    if res.get("output_truncated"):
        return ErrorCode.PARSE_FAIL
    if not res.get("ok") or (parsed and parsed.get("is_error")):
        detail = "\n".join((str(res.get("stderr") or ""),
                            str(res.get("stdout") or ""),
                            str((parsed or {}).get("text") or "")))
        if _permission_error(detail):
            return ErrorCode.FORBIDDEN
    # Preserve actionable upstream distinctions before collapsing non-zero exits
    # into VENDOR_ERROR. The shared classifier is also used by modelhub and routing.
    if not res.get("ok") or (parsed and parsed.get("is_error")):
        detail = "\n".join((str(res.get("stderr") or ""),
                            str(res.get("stdout") or ""),
                            str((parsed or {}).get("text") or "")))
        classified = classify_error_text(detail)
        if classified is not None:
            return classified
    # claude 解析失败（进程 ok 但无 result 事件）：先按 stdout 认错误——未登录
    # 闸门只吐一行「Not logged in · Please run /login」就退出，认不出才是
    # 真正的解析失败
    if kind == "claude" and parsed is None:
        return classify_error_text(res.get("stdout") or "") or ErrorCode.PARSE_FAIL
    # claude 明确 is_error
    if parsed and parsed.get("is_error"):
        return ErrorCode.VENDOR_REFUSAL
    # claude 两次都空
    if kind == "claude" and attempt_done and not (parsed or {}).get("text", "").strip():
        return ErrorCode.EMPTY
    # generic/codex 空输出（caller 已确认）
    if empty_output and not parsed:
        return ErrorCode.EMPTY
    # 退出码非 0（vendor 内部崩溃）
    ec = res.get("exit_code")
    if ec is not None and ec != 0:
        # Windows NTSTATUS 致命异常（0xC0000005 访问违例、0xC0000409 fail-fast
        # 等，>=0xC0000000）：CLI 进程本地硬崩溃，一个上游字都没吐——归
        # LOCAL_STATE 让跨 CLI 换将放行、健康台账按本地崩溃豁免（2026-10-22
        # opencode 退出码 3221226505 连记 3 次把免费通道打成供应商故障实案）。
        # VENDOR_ERROR 是 fatal 码，本地崩溃按它收场会整 run 判死不换将。
        try:
            if int(ec) >= 0xC0000000:
                return ErrorCode.LOCAL_STATE
        except (TypeError, ValueError):
            pass
        return ErrorCode.VENDOR_ERROR
    return ""


def _resolve_attempts(agent):
    """把 agent 的模型配置展开为逐次尝试列表。

    优先用跨厂商链 call_chain（每条自带 env / codex_provider，来自不同供应商）；
    无链时退回 model + model_fallbacks（同一 CLI 进程内换 -m，沿用 agent 级注入）。

    链条目可能带 key_id/provider_id（同一厂商多把 KEY 展开成的多条）——失败时
    据此把「哪把 KEY 不行」回写冷却，后续解析自动切备用。
    """
    chain = agent.get("call_chain") or []
    if chain:
        return [{"model": (e.get("model") or "").strip() or None,
                 "env": dict(e.get("env") or {}),
                 "provider": e.get("provider") or {},
                 "from_chain": True,
                 "own_cp": "codex_provider" in e,
                 "codex_provider": e.get("codex_provider"),
                 "provider_id": e.get("provider_id") or "",
                 "key_id": e.get("key_id") or "",
                 "no_cred": bool(e.get("no_cred"))} for e in chain]
    base_model = agent.get("model")
    fb = [m for m in (agent.get("model_fallbacks") or []) if m and m != base_model]
    models_to_try = ([base_model] if base_model else []) + fb
    provider = agent.get("provider") or {}
    return [{"model": m or None, "env": {}, "provider": provider,
             "from_chain": False, "own_cp": False,
             "codex_provider": None,
             "provider_id": provider.get("id") if isinstance(provider, dict) else "",
             "key_id": ""}
            for m in (models_to_try or [None])[:3]]


def _attempt_protocol(att):
    """尝试身份里的协议：链/供应商条目自带 protocol 才记，取不到留空——
    留空表示未声明，不得回填猜测（chat/responses 之分要靠适配层实测）。"""
    prov = att.get("provider")
    if isinstance(prov, dict) and prov.get("protocol"):
        return str(prov["protocol"])[:24]
    return ""


def _codex_sandbox(readonly):
    """Return the narrowest supported Codex sandbox mode.

    A full-access override defeats task-level filesystem policy and checkpoints;
    ignore the former ``danger-full-access`` value even if it remains in the
    user's environment. Read-only review steps must not be able to mutate files.
    """
    if readonly:
        return "read-only"
    env = os.environ.get("TUTTI_CODEX_SANDBOX", "").strip().lower()
    if env in ("workspace-write", "read-only"):
        return env
    return "workspace-write"


def _prompt_to_file(prompt, workdir):
    """超长提示词落盘（工作目录优先，保证评审 CLI 沙箱内可读），返回绝对路径。"""
    try:
        d = workdir if workdir and os.path.isdir(workdir) else None
        fd, path = tempfile.mkstemp(prefix="tutti_prompt_", suffix=".md", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        return path
    except Exception:
        return None


# Windows 上不少 CLI 先经过 cmd.exe/.cmd 垫片，实际安全上限接近 8191，
# 而不是 CreateProcess 的理论 32767。留出命令、路径和环境参数余量，超过
# 该值统一落盘，避免 MiMo/Kimi 评审在启动前直接报「命令行太长」。
_ARGV_PROMPT_SAFE = 20000
_ARGV_PROMPT_SHIM_SAFE = 6000


def _build_call(agent, kind, sid, readonly, model, prompt, images=None, workdir=None):
    """构建一次 CLI 调用的 (argv, stdin_text, prompt, tmp_files)。model 可为 None=CLI 默认。
    images 为图片附件绝对路径：codex 用 -i 原生附图；其余 kind 忽略（调用方已过滤）。
    tmp_files：为绕开命令行长度限制落盘的指令临时文件，调用方用完（进程结束后）删除。"""
    argv = None
    stdin_text = None
    tmp_files = []
    imgs = [str(p) for p in (images or []) if p]
    if kind == "codex":
        cp = agent.get("codex_provider")
        reasoning = str(agent.get("reasoning_effort") or "").strip().lower()
        if sid:
            # resume 子命令不支持 -s 也不支持 --full-auto（0.154 实测：
            # "unexpected argument '--full-auto'"）——读/写模式都用 -c sandbox_mode
            argv = resolve_command(agent["command"]) + [
                "exec", "resume", sid, "-",
                "--skip-git-repo-check", "--json"]
            if model:
                argv += ["-m", model]
            argv += ["-c", 'sandbox_mode="%s"' % _codex_sandbox(readonly)]
            if cp:
                argv += _codex_provider_args(cp)
        else:
            argv = resolve_command(agent["command"]) + [
                "exec", "--skip-git-repo-check", "--json",
                "-s", _codex_sandbox(readonly)]
            if model:
                argv += ["-m", model]
            if cp:
                argv += _codex_provider_args(cp)
        if reasoning in ("low", "medium", "high", "xhigh"):
            argv += ["-c", 'model_reasoning_effort="%s"' % reasoning]
        # codex exec 与 exec resume 都支持 -i：图片直接附到 prompt（exec resume 的
        # -i 附在恢复后发送的首条消息上，即本次 stdin prompt）
        for p in imgs:
            argv += ["-i", p]
        stdin_text = prompt
    elif kind == "claude":
        # stream-json（2026-09-17 起）：逐行事件边跑边吐——停滞看门狗有得看，
        # 步骤日志实时可读；result 末行与旧单 JSON 同构，解析器双形态兼容
        argv = resolve_command(agent["command"]) + [
            "-p", "--output-format", "stream-json", "--verbose"]
        if sid:
            argv += ["--resume", sid]
        if model:
            argv += ["--model", model]
        if readonly:
            # 实测本机自定义网关在 -p 模式下工具续接会丢最终结果（用工具必空）。
            # 评审/规划所需的上下文已内嵌在提示词中，显式禁用工具最稳。
            prompt = "（请勿使用任何工具，直接依据下方内容回答。）\n\n" + prompt
        elif os.environ.get("TUTTI_CLAUDE_PERMS", "").strip() == "acceptEdits":
            argv += ["--permission-mode", "acceptEdits"]
        else:
            # 无人值守 -p 下 acceptEdits 只自动放行改文件，Bash 一律权限拒绝 →
            # 模型以为环境受限谎报「被策略拦截」躺平（同 codex 沙箱误判案）。
            # 默认跳过全部权限检查（2026-09-17 用户拍板），env 可收回旧行为。
            argv += ["--dangerously-skip-permissions"]
        # 绑定链 env 落一次性 --settings：claude CLI 里用户 settings.json 的 env
        # 会覆盖进程 env（2026-09-22 双监听器实测），光靠 run_process 的 env 注入，
        # 链首供应商会被用户配置顶掉（a.test 毒配置案的放大器）。--settings 的
        # 优先级高于用户 settings.json（同日实测），临时文件进程结束随 tmp_files 删。
        ant = {k: v for k, v in (agent.get("env") or {}).items()
               if k.startswith("ANTHROPIC_")}
        if ant:
            try:
                fd, sp = tempfile.mkstemp(prefix="tutti_claude_settings_", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"env": ant}, f)
                argv += ["--settings", sp]
                tmp_files.append(sp)
            except Exception:
                pass   # 落盘失败退回纯 env 注入（旧路径），不拦运行
        stdin_text = prompt
    elif kind == "opencode":
        argv = resolve_command(agent["command"]) + ["run"]
        if workdir:
            # opencode 的 shell 不总跟进程 cwd 走（服务端复用时会落在旧项目根，
            # 2026-09-17 mo-so 实测 agent 在 Temp 里全盘找代码）——显式钉死
            argv += ["--dir", str(workdir)]
        if sid:
            argv += ["-s", sid]  # 无头续会话：-s 指定会话 id（-c 只能接最近一次）
        if model:
            # opencode 只认 provider/model 全名（裸名直接 UnknownError，实测）；
            # CodeBee 同步进其配置的 provider id 固定为 orch，故裸名补前缀。
            # 绑定里已写全名（含 /）的尊重原值。
            argv += ["--model", model if "/" in model else "orch/" + model]
        stdin_text = prompt  # 无位置参数且 stdin 有内容时读 stdin
    elif kind == "qwen":  # gemini-cli 系：无参数且 stdin 有内容时读 stdin
        argv = resolve_command(agent["command"])
        if sid:
            argv += ["-r", sid]  # 恢复指定会话（~/.qwen/projects/*/chats/<sid>.jsonl）
        if model:
            argv += ["-m", model]
        stdin_text = prompt
    elif kind == "aider":
        argv = resolve_command(agent["command"]) + [
            "--yes-always", "--no-auto-commits", "--no-check-update",
            "--no-gitignore", "--no-pretty", "--no-stream",
            # 无头执行没有 Windows 控制台，aider 建 PromptSession 抛错并打一行
            # "Can't initialize prompt toolkit: No Windows console found..."
            # （非致命，但它排在输出最前，UI 失败摘要只取头部 → 真错误被顶掉，
            # 2026-09-22 mo-so 实案整条失败原因显示成这句噪声）。关掉花式输入
            # 就不再建 PromptSession，噪声从源头消失。
            "--no-fancy-input",
            # 网关自定义模型名（glm-5.1 等）litellm 全都不认识，警告页+建议列表
            # 纯属刷屏（2026-09-20 实测占满步骤日志头部）
            "--no-show-model-warnings", "--message", prompt]
        if model:
            # litellm 靠 provider 前缀路由，裸模型名直接报
            # "LLM Provider NOT provided"。前缀按本条尝试实际注入的端点协议定：
            # ANTHROPIC_BASE_URL=anthropic 面（Bigmodel 等）、OPENAI_API_BASE=
            # openai 兼容面；都没有=CLI 本机默认场景，模型名保持用户原样
            envd = agent.get("env") or {}
            if "/" not in model:
                if envd.get("ANTHROPIC_BASE_URL"):
                    model = "anthropic/" + model
                elif envd.get("OPENAI_API_BASE"):
                    model = "openai/" + model
            argv += ["--model", model]
    else:  # generic：模板把 {prompt}/{session} 嵌进参数（注意 cmd 行长度限制）
        tmpl = agent.get("argv_template") or ["-p", "{prompt}"]
        if sid and agent.get("resume_argv_template"):
            tmpl = agent["resume_argv_template"]
        argv = resolve_command(agent["command"]) + [
            str(a).replace("{prompt}", prompt).replace("{session}", sid) for a in tmpl]
        # Normalize Windows npm .cmd shims before adding model/provider flags.
        # A raw ``cmd /c tool.cmd`` prefix must remain intact; inserting at
        # argv[1] would produce ``cmd --model ... /c tool.cmd`` and fail before
        # the CLI starts.
        argv = _npm_shim_bypass(argv)
        if len(argv) >= 3 and str(argv[0]).lower() == "cmd" and argv[1] == "/c":
            flag_at = 3
        elif len(argv) >= 2 and str(argv[0]).lower().endswith(("node", "node.exe")):
            flag_at = 2
        else:
            flag_at = 1
        # Pi OAuth providers are selected explicitly; its --model value is
        # provider-local while the binding stores the canonical provider/id.
        if model and agent.get("id") == "pi":
            native_provider = str((agent.get("provider") or {}).get("native_provider") or "").strip()
            model_id = model
            if native_provider and "/" in model_id and model_id.split("/", 1)[0].lower() == native_provider.lower():
                model_id = model_id.split("/", 1)[1]
            if native_provider:
                argv[flag_at:flag_at] = ["--provider", native_provider, "--model", model_id]
        # Gemini CLI and Kimi Code keep authentication in their own CLI
        # sessions.  A native binding may still select the model explicitly;
        # both official CLIs use the portable ``-m`` spelling.
        if model and agent.get("id") in ("gemini-cli", "kimi-code", "grok-build", "mimo-code"):
            argv[flag_at:flag_at] = ["-m", model]
        if "{prompt}" not in tmpl:
            stdin_text = prompt  # 恢复模板不带 {prompt}：提示词走 stdin（mimo 实测支持）
    # Windows CreateProcess 命令行上限 32767 字符：全局评审会把全书文本（约 6 万
    # 字）嵌进 argv，直接 WinError 206 启动失败（2026-09-18 七猫甜宠案，kimi 实证）。
    # 超长时落盘临时文件、参数位换成读文件指令——评审/作者 CLI 非交互模式均带
    # 读文件工具（kimi 0.43 实测可读），读不到的调用输出不可解析，由评审全挂
    # 防线兜底判失败，绝不静默降级。
    prompt_safe = _ARGV_PROMPT_SAFE
    if argv:
        # 垫片检测扫前三个 token：cmd /c xxx.CMD 形态下 argv[0] 是 cmd.exe，
        # 真正的 .cmd 垫片在 argv[2]——只看 argv[0] 会漏判，中等长度提示词
        # （6000-20000 字）留在指令行直接撞 cmd.exe 8191 上限
        # （2026-09-24 评审「命令行太长」实案）
        heads = [str(a).lower() for a in argv[:3]]
        if any(a.endswith((".cmd", ".bat")) or
               os.path.basename(a) in ("npm", "npx", "pnpm", "yarn", "bun")
               for a in heads):
            prompt_safe = _ARGV_PROMPT_SHIM_SAFE
        elif os.name == "nt" and kind == "generic":
            # Vendor wrappers are not always recognisable as npm shims. Keep
            # generic prompts below the cmd.exe limit in that case too.
            prompt_safe = _ARGV_PROMPT_SHIM_SAFE
    if argv is not None and stdin_text is None and len(prompt) >= prompt_safe \
            and argv.count(prompt) == 1:
        pf = _prompt_to_file(prompt, workdir)
        if pf:
            if kind == "aider":
                # aider 非交互态没有「读文件工具」语义：--message 指挥它读文件
                # 会被模型判定为提示注入直接拒绝（2026-10-03 不周山守村人全局
                # 评审实案：glm-5.3 十秒交白卷 → 全局有效评审恒 1 名，门禁
                # 「≥2 名」结构性不可能通过）。aider 的 --read 把文件作为只读
                # 会话文件由 CLI 自己读入，语义与交互式 aider file.md 一致。
                argv[argv.index(prompt)] = (
                    "本次完整任务指令已作为只读文件加入会话，"
                    "请直接依据该文件内容执行任务。")
                argv += ["--read", pf]
            else:
                argv[argv.index(prompt)] = (
                    "[系统] 本次完整指令因命令行长度限制已写入文件：%s\n"
                    "请先用读文件工具完整读取该文件，然后把文件内容当作你的任务指令执行。" % pf)
            # Keep the original payload on stdin as a compatibility path for
            # simple generic adapters and test shims that read stdin. CLI tools
            # that intentionally use argv still receive the bounded file hint.
            if kind == "generic" and stdin_text is None and len(tmpl) > 1:
                # 模板明确将提示词放进 argv 时，原文只保存在文件里；同时再
                # 写入 stdin 会让部分包装器重复读取，也无法解决 Windows 命令行上限。
                stdin_text = None
            tmp_files.append(pf)
    return argv, stdin_text, prompt, tmp_files


def _check_approval(agent):
    """5G：catalog entry sensitive + policy=NEVER → 拒绝（无人值守不静默降级）。

    Policy 来源：环境变量 TUTTI_APPROVAL_POLICY（默认 "never"）。
    sensitive 字段从 catalog orch.sensitive 读取。

    Returns:
        (True, "") 表示放行；
        (False, reason) 表示拒绝（error_code=ENV_BLOCK）。
    """
    policy = os.environ.get("TUTTI_APPROVAL_POLICY", "never").strip().lower()
    sensitive = bool((agent.get("orch") or {}).get("sensitive"))
    if not sensitive:
        return True, ""
    if policy == "never":
        return False, "sensitive step blocked by policy=never (set TUTTI_APPROVAL_POLICY=ask to require explicit approval)"
    # policy=ask 留 Phase 5 做完整 UI 弹窗流程；当前等价 never
    return False, "sensitive step requires policy=ask UI flow (Phase 5) — currently blocking"


def _stall_timeout(env_name, default):
    """停滞看门狗秒数：stdout/stderr 持续无新输出超过该时长 → 判卡死提前杀，
    错误按超时归类走既有换模型/换将链路。只对事件流持续流动的 CLI 启用
    （codex JSONL / claude stream-json）；qwen/opencode/generic 结束才一次性
    输出，开了会误杀正常长任务，仍靠总超时兜底。env 可调，0=关闭。"""
    try:
        return max(0, int(os.environ.get(env_name, str(default))))
    except Exception:
        return default


def _activity_timeout(env_name, default=DEFAULT_STREAM_ACTIVITY_TIMEOUT_S):
    """流式 CLI 的活动延长窗口，独立于停滞看门狗配置。"""
    try:
        return max(0, int(os.environ.get(env_name, str(default))))
    except Exception:
        return default


def _max_model_attempts(has_deadline=False):
    """返回一次 runner 调用允许消耗的候选数。

    生产任务会传入统一 deadline，默认收敛到 3 个候选；没有 deadline 的
    旧式直接调用保留最多 4 个候选，以兼容已有的「同一上游两模型后换
    供应商」策略。运维可用环境变量进一步收紧/放宽，最少 1 个。
    """
    default = 3 if has_deadline else 4
    try:
        value = int(os.environ.get("TUTTI_MAX_MODEL_ATTEMPTS", str(default)))
        return max(1, min(12, value))
    except (TypeError, ValueError):
        return default


_INVALID_MODEL_NAMES = frozenset(("auto", "default", "none"))


def _invalid_model(model):
    return str(model or "").strip().lower() in _INVALID_MODEL_NAMES


# opencode 启动横幅形如「> build · glm-5.3」（ANSI 清洗后一行、圆点分隔）；
# 真错误（配额/配置/网络）CLI 都会另打印文本，不会是纯横幅形态
_OC_BANNER_LINE_RE = re.compile(r"^\s*>?\s*[^\s·•]+\s*[·•]\s*\S+\s*$")


def _opencode_banner_only(res):
    """opencode 退出码 1 且清洗 ANSI 后只剩启动横幅、没有任何错误文本。

    真错误（配额/配置/网络）CLI 都会另打印文本，纯横幅形态只可能是本地
    启动/配置抖动，责任不在上游；不论跑了 2 秒还是 2 分钟，换一个 CLI 都
    比钉死在同一进程上重试更有机会活。"""
    if res.get("ok") or res.get("exit_code") != 1:
        return False
    blob = clean_cli_text((res.get("stderr") or "") + "\n"
                          + (res.get("stdout") or ""))
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    return bool(lines) and all(_OC_BANNER_LINE_RE.match(ln) for ln in lines)


def _opencode_fast_banner_death(res):
    """opencode 启动期秒退签名：横幅退出且总时长很短。

    该形态只值得原地立即重试一次（配置写读竞态/端口抢占类瞬时抖动，
    2026-09-30 实案：并行评审爆发期连续三发「退出码 1；stderr/stdout:
    > build · glm-5.3」，数分钟后同进程自愈）；长时横幅死亡直接交给换将。"""
    if not _opencode_banner_only(res):
        return False
    try:
        return float(res.get("duration") or 0) <= 15.0
    except (TypeError, ValueError):
        return False


def _opencode_salvage_complete_run(res):
    """opencode 退出码 1 但主输出已完整时的收账判定，返回可收账文本或空串。

    2026-10-21 实案：主回答流式完成后，CLI 收尾的会话标题小模型调用撞上游
    不可用（Endpoint is unavailable），重试 3 次失败把整个进程拖成退出码 1
    （opencode#30662 同款）——正文早已完整，按失败丢弃只会让链路原样重跑。
    只收非终止类失败：输出有实质内容、且 stdout/stderr 尾段没有任何错误信号
    （中途真炸的输出尾段必带错误行或上游措辞，不会误收）。"""
    if (res.get("exit_code") != 1
            or res.get("timed_out") or res.get("cancelled")
            or res.get("stalled") or res.get("deadline_exceeded")
            or res.get("output_truncated")):
        return ""
    text = clean_cli_text(res.get("stdout") or "").strip()
    if len(text) < 200:
        return ""
    tail = text[-2000:]
    if (_cli_error_lines(tail)
            or _cli_error_lines(clean_cli_text(res.get("stderr") or "")[-2000:])
            or classify_error_text(tail)):
        return ""
    return text


def run_agent(agent, prompt, workdir=None, readonly=True,
              timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None, resume=None,
              images=None, require_tools=False, deadline=None, command_context=None,
              checkpoint_run_id=""):
    """执行一次智能体调用，返回统一结构
    {ok, text, json, cost_usd, tokens, error, error_code, raw}。
    agent 来自 registry.effective_agents()；resume 为已有会话 id，仅真实智能体生效
    （codex: exec resume；claude: --resume；opencode/mimo: run -s；qwen: -r；
    generic: catalog orch.resume_argv_template）。
    images：任务图片附件的绝对路径，仅 codex 原生支持（-i）；其余智能体靠
    提示词里的 _attachments/ 路径 + 自身读文件能力获取，无读图工具时静默忽略。
    require_tools：实现步空转闸（仅 codex 事件流可判）——True 时本轮零
    command_execution/file_change/mcp_tool_call 事件即判 VENDOR_REFUSAL，
    防模型纯口头谎报「环境受限」蒙混过关（2026-09-17 mo-so 实测）。

    模型尝试顺序来自 _resolve_attempts：跨厂商链（每条独立 env）或
    主模型 + 降级备选；瞬态/配额/超时/上游拒答换下一条，取消与解析
    失败不降级；同一上游超时两次即跳过该上游剩余模型。

    5E：catalog `orch.timeout_ms`（毫秒）优先于 caller 传入的 timeout；
    单模型时限由该配置或 caller timeout 决定，任务 deadline 可进一步收紧；
    持续 stream 活动交由 stall_timeout 看门狗收口。deadline 是
    任务共享的 ``time.monotonic()`` 绝对截止时刻；传入后所有模型、空响应
    重试和限流宽限共用同一剩余预算。
    """
    # 5E：catalog orch.timeout_ms 优先
    command_context = dict(command_context or {})
    # Every CLI transport (including ACP) enters through this function. Resolve
    # the durable run capability snapshot here so direct callers cannot bypass
    # the experience/knowledge/Skill bridge. Existing pipeline prompts already
    # contain the same block and are left unchanged.
    try:
        from . import capability_runtime
        prompt, _capability_meta = capability_runtime.inject_prompt(
            prompt, task=command_context.get("task"),
            run_id=command_context.get("run_id") or checkpoint_run_id)
        capability_runtime.note_prompt(
            command_context.get("run_id") or checkpoint_run_id,
            agent=agent.get("id") or agent.get("kind") or "",
            capabilities_present=bool(_capability_meta.get("capabilities_present")),
        )
    except Exception:
        # Optional catalogs must never turn a valid CLI invocation into a hard
        # failure.
        pass
    orch_timeout_ms = (agent.get("orch") or {}).get("timeout_ms")
    if orch_timeout_ms:
        timeout = float(orch_timeout_ms) / 1000.0
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT)
    timeout = max(0.0, timeout)
    try:
        deadline = float(deadline) if deadline is not None else None
    except (TypeError, ValueError):
        deadline = None
    if deadline is not None and time.monotonic() >= deadline:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None, "error": "任务总时限已到，未启动模型",
                "error_code": ErrorCode.TIMEOUT,
                "raw": {"exit_code": None, "timed_out": True,
                        "deadline_exceeded": True, "duration": 0.0},
                "kind": agent.get("kind", "generic"), "model": None,
                "attempts": []}
    kind = agent.get("kind", "generic")
    if kind == "acp":
        from . import acp_client
        context = dict(command_context or {})
        context.setdefault("run_id", checkpoint_run_id)
        context.setdefault("sandbox", {})
        context.setdefault("allow_terminal", not readonly)
        return acp_client.run_agent(
            agent, prompt, workdir=workdir, readonly=readonly, timeout=timeout,
            cancel_event=cancel_event, deadline=deadline,
            command_context=context, checkpoint_run_id=checkpoint_run_id,
            resume=resume)
    if agent.get("runtime_config_sync_failed"):
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None,
                "error": agent.get("runtime_config_sync_error") or
                         "CLI 绑定配置同步失败，已阻断本次执行",
                "error_code": ErrorCode.ENV_BLOCK, "raw": None,
                "kind": kind, "model": None, "attempts": []}
    if kind == "aider":
        repo_issue = _git_repo_issue(workdir)
        if repo_issue:
            return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                    "tokens": 0, "usage": None, "error": repo_issue,
                    "error_code": ErrorCode.VENDOR_ERROR, "raw": None,
                    "kind": kind, "model": agent.get("model"), "attempts": []}
    if kind == "codex":
        stall_t = _stall_timeout("TUTTI_CODEX_STALL_TIMEOUT", 600)
        activity_t = _activity_timeout("TUTTI_CODEX_ACTIVITY_TIMEOUT")
    elif kind == "claude":
        stall_t = _stall_timeout("TUTTI_CLAUDE_STALL_TIMEOUT", 600)
        activity_t = _activity_timeout("TUTTI_CLAUDE_ACTIVITY_TIMEOUT")
    else:
        # 数据驱动：catalog orch.stall_timeout_s——给 kimi/qwen 这类评审用
        # CLI 配置后，静默挂死即杀（qwen 900s：2026-09-22 MCP 收尾死锁案）；
        # mimo 等结束才一次性输出的仍留 0（开了必误杀），靠总超时兜底
        try:
            stall_t = max(0, int((agent.get("orch") or {}).get("stall_timeout_s") or 0))
        except Exception:
            stall_t = 0
        activity_t = 0
    # 5G：approval NEVER 一线（无人值守不静默降级）
    ok, reason = _check_approval(agent)
    if not ok:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0, "tokens": 0,
                "usage": None, "error": reason,
                "error_code": ErrorCode.ENV_BLOCK,
                "raw": None, "kind": kind, "model": None, "attempts": []}
    base_env = dict(agent.get("env") or {})
    if kind == "claude":
        bash = find_git_bash()
        if bash:
            base_env.setdefault("CLAUDE_CODE_GIT_BASH_PATH", bash)
        # 32000=claude CLI 官方默认（自定义网关模型常见上限）：旧值 16000 在
        # 全局评审读 40 万字书稿时被输出顶爆——CLI 按 16000/轮反复续烧 23.5
        # 分钟、$3 后报 "response exceeded the 16000 output token maximum"
        # （2026-10-11 实案×3）；setdefault 保留：用户显式配置不覆盖
        base_env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "32000")
    sid = (resume or "").strip() if agent.get("mode") == "real" else ""
    if sid and kind == "generic" and not agent.get("resume_argv_template"):
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0, "tokens": 0,
                "usage": None,
                "error": "该 CLI 未配置会话恢复（catalog orch.resume_argv_template），"
                         "无法在已有会话上继续",
                "error_code": ErrorCode.VENDOR_ERROR,
                "sid": "", "raw": None, "kind": kind, "model": None, "attempts": []}

    attempts = _resolve_attempts(agent)
    # 启动前过滤明显的占位模型名。它们常由某个 CLI 的会话标题/默认配置
    # 泄漏进编排链；让子进程自己等待网关返回会把一个确定性配置错拖满
    # 多轮超时。非法项不消耗模型调用预算，后面的真实候选仍可接手。
    filtered_attempts = []
    preflight_failures = []
    for att in attempts:
        if _invalid_model(att.get("model")):
            preflight_failures.append({
                "model": att.get("model"),
                "provider_id": att.get("provider_id") or "",
                "provider": (att.get("provider") or {}).get("name", "")
                            if isinstance(att.get("provider"), dict) else "",
                "key_id": att.get("key_id") or "",
                "protocol": _attempt_protocol(att),
                "ok": False, "skipped": True, "error":
                    "无效模型名 %r，启动前跳过" % att.get("model"),
            })
            continue
        filtered_attempts.append(att)
    attempts = filtered_attempts[:_max_model_attempts(deadline is not None)]
    out = None
    attempt_history = list(preflight_failures)
    if not attempts:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None,
                "error": (preflight_failures[-1].get("error")
                           if preflight_failures else "没有可尝试的模型"),
                "error_code": ErrorCode.VENDOR_ERROR, "raw": None,
                "kind": kind, "model": None, "attempts": attempt_history}
    grace_left = RATE_LIMIT_GRACE_N  # 链尾限流宽限预算：整链撞 429 时原地等一个窗口再试一次
    timed_out_upstreams = {}  # 同一上游两次超时后跳过剩余模型
    skipped_upstreams = set()
    forbidden_upstreams = set()
    auth_failed_credentials = set()
    unavailable_models = set()
    for ai, att in enumerate(attempts):
        model_key = (str(att.get("provider_id") or ""),
                     str(att.get("model") or "").strip())
        if model_key in unavailable_models:
            attempt_history.append({
                "model": att.get("model") or "",
                "provider_id": att.get("provider_id") or "",
                "provider": ((att.get("provider") or {}).get("name", "")
                             if isinstance(att.get("provider"), dict) else ""),
                "kind": kind, "key_id": att.get("key_id") or "",
                "protocol": _attempt_protocol(att), "ok": False,
                "skipped": True, "error_code": ErrorCode.MODEL_UNAVAILABLE,
                "error": "同一供应商的该模型已确认不可用，跳过重复候选",
            })
            if out is not None:
                out["attempts"] = list(attempt_history)
            continue
        credential = _attempt_credential(att)
        if credential in auth_failed_credentials:
            attempt_history.append({
                "model": att.get("model") or "",
                "provider_id": att.get("provider_id") or "",
                "provider": ((att.get("provider") or {}).get("name", "")
                             if isinstance(att.get("provider"), dict) else ""),
                "kind": kind, "key_id": att.get("key_id") or "",
                "protocol": _attempt_protocol(att),
                "ok": False, "skipped": True,
                "error_code": ErrorCode.VENDOR_ERROR,
                "error": "同一凭据已被认证拒绝，跳过该凭据的其他模型",
            })
            if out is not None:
                out["attempts"] = list(attempt_history)
            continue
        upstream = _attempt_upstream(att)
        if upstream in skipped_upstreams or upstream in forbidden_upstreams:
            continue
        # 有任务 deadline 时，所有候选共享同一个截止时刻；否则每个候选
        # 使用 catalog 配置或 caller 提供的单模型时限。
        model_deadline = (att.get("_deadline") or deadline or
                          (time.monotonic() + timeout))
        env = dict(base_env)
        env.update(att["env"])
        if kind == "codex":
            # 网关自定义模型名（如 [opencode]xxx）是合法 API 模型名不能改，但方括号
            # 是非法 OTel tag 值 → codex_otel 每个 SSE 事件刷 2 条 WARN 淹没真输出。
            # 只静音遥测模块（codex 不认 RUST_LOG 也无害），其余 WARN 全保留。
            env.setdefault("RUST_LOG", "codex_otel=off")
        eff_agent = dict(agent)
        eff_agent["env"] = env
        if isinstance(att.get("provider"), dict):
            eff_agent["provider"] = att["provider"]
        if att["own_cp"]:
            eff_agent["codex_provider"] = att["codex_provider"]
        elif att["from_chain"] and "codex_provider" in eff_agent:
            # 链内条目未注入供应商时不能沿用上一条（可能是另一家厂商）的 -c 覆盖
            del eff_agent["codex_provider"]
        stream_retries = 0
        base_attempts = 2 if kind in ("claude", "opencode") else 1
        max_attempts = base_attempts + (STREAM_RETRY_N if readonly
                                        and kind in ("codex", "claude") else 0)
        for attempt in range(max_attempts):  # claude 空响应 + 只读流中断各自有界重试
            ledger = None
            remaining = model_deadline - time.monotonic()
            if remaining <= 0:
                res = {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                       "duration": timeout, "cancelled": False, "timed_out": True,
                       "stalled": False,
                       "deadline_exceeded": deadline is not None}
            else:
                argv, stdin_text, prompt_eff, tmp_files = _build_call(
                    eff_agent, kind, sid, readonly,
                    att["model"], prompt,
                    images=images if kind == "codex" else None,
                    workdir=workdir)
                # 凭据模式起跑钉进审计头（所有 CLI）：注入真值按 kind 分流，
                # claude 以 argv 的 --settings 为准，见 _cred_audit_note
                audit_notes = [_cred_audit_note(kind, att, argv)]
                try:
                    ledger = _attempt_ledger(checkpoint_run_id)
                    if ledger:
                        ledger.start(checkpoint_run_id or "unbound", command_context.get("role", "")
                                     if isinstance(command_context, dict) else "",
                                     ((att.get("provider") or {}).get("name", "")
                                      if isinstance(att.get("provider"), dict) else ""),
                                     att.get("model") or "")
                    repeat_guard = (("Reconnecting...", 2)
                                    if kind == "codex" else None)
                    stream_abort_markers = (
                        (("stream disconnected", 1),
                         ("stream closed before response.completed", 1))
                        if kind == "codex" else None)
                    # Grok's CLI can keep a dead HTTP connection alive for the
                    # full 20-minute generic timeout. Two identical transport
                    # errors are enough evidence to stop this process and let
                    # the next bound agent try.
                    if agent.get("id") == "grok-build":
                        stream_abort_markers = (
                            ("reqwest error stream", 2),
                            ("error sending request for url", 2),
                        )
                    elif agent.get("id") in ("deepseek-harness", "dsh"):
                        # dsh may keep printing the same endpoint retry while
                        # its node child is still alive.  Three identical 429
                        # signals are enough evidence to kill the whole tree;
                        # the outer launch budget handles repeated invocations.
                        stream_abort_markers = (
                            ("exceeded retry limit", 1),
                            ("429 Too Many Requests", 3),
                            ("too many requests", 3),
                        )
                    launch_token = _dsh_launch_admit(
                        checkpoint_run_id or command_context.get("run_id"),
                        command_context.get("role"), agent.get("id"), upstream)
                    if isinstance(launch_token, str):
                        res = {
                            "ok": False, "exit_code": None, "stdout": "",
                            "stderr": launch_token, "duration": 0.0,
                            "cancelled": False, "timed_out": False,
                            "stalled": False, "deadline_exceeded": False,
                            "abort_marker": None, "launch_budget_exhausted": True,
                        }
                    else:
                        res = None
                        try:
                            res = run_process(argv=argv, stdin_text=stdin_text, cwd=workdir, env=env,
                                              timeout=_stable_timeout(min(timeout, remaining)), deadline=deadline,
                                              cancel_event=cancel_event,
                                              log_path=log_path, stall_timeout=stall_t,
                                              activity_timeout=activity_t,
                                              repeat_abort=repeat_guard,
                                              abort_markers=stream_abort_markers,
                                              audit_notes=audit_notes,
                                              max_capture_bytes=MAX_AGENT_CAPTURE_BYTES)
                        finally:
                            _dsh_launch_complete(
                                launch_token,
                                bool(res and (res.get("ok") or res.get("cancelled"))))
                    res["command"] = redact_command(argv)
                finally:
                    # 超长指令临时文件：CLI 进程已结束（管道已收），即刻清场不污染工作目录
                    for tf in tmp_files:
                        try:
                            os.remove(tf)
                        except OSError:
                            pass
            out = {"ok": res["ok"], "text": "", "json": None, "cost_usd": 0.0,
                   "tokens": 0, "usage": None, "error": "", "error_code": "",
                   "sid": "", "raw": res, "kind": kind, "model": att["model"],
                   "provider_id": att.get("provider_id") or "",
                   "provider": att.get("provider") or {},
                   "stream_retries": stream_retries,
                   "attempt_ordinal": attempt + 1}
            if not res["ok"] and kind == "opencode":
                salvaged = _opencode_salvage_complete_run(res)
                if salvaged:
                    out["ok"] = True
                    out["text"] = salvaged
                    out["salvaged"] = True
                    _log_note(log_path,
                              "opencode 退出码 1 但主输出完整（收尾标题调用失败所致），按成功收账")
            if not out["ok"]:
                if res.get("launch_budget_exhausted"):
                    out["error"] = ((res.get("stderr") or
                                     "dsh 启动预算耗尽，已停止继续拉起子进程") +
                                    "（temporarily unavailable；可切换其他 CLI）")
                    # This is a local circuit breaker, not a permanent
                    # configuration block: let a model chain or the pipeline
                    # fail over to another endpoint/CLI.
                    out["error_code"] = ErrorCode.LOCAL_STATE
                    _finish_attempt_ledger(ledger, checkpoint_run_id,
                                           command_context, att, out)
                    break
                if (kind == "opencode" and attempt == 0
                        and _opencode_fast_banner_death(res)
                        and not (cancel_event is not None
                                 and cancel_event.is_set())):
                    _finish_attempt_ledger(ledger, checkpoint_run_id,
                                           command_context, att, {
                                               "ok": False,
                                               "error": "opencode 启动横幅后退出",
                                               "error_code": ErrorCode.LOCAL_STATE,
                                               "raw": res,
                                               "model": att.get("model"),
                                               "attempt_ordinal": attempt + 1,
                                               "usage": None})
                    _log_note(log_path,
                              "opencode 启动即退（仅横幅无错误文本）——按瞬时抖动 2s 后原地重试一次")
                    time.sleep(2.0)
                    continue
                # stderr 与 stdout 都要进错误串：codex 把 "Reading prompt from
                # stdin..." 打在 stderr，真正的配额/限流错误全在 stdout 的 JSONL
                # 里——只取其一会让 _quota_error/_transient_error 判空。
                # 先洗后切：切片会割断 ANSI 序列，留下 `[91m` 这种裸残尾直接进 UI
                tail = _failure_output_detail(res)
                head = ("输出停滞 %ss（stall timed out，疑似卡死已提前终止）" % stall_t
                        if res.get("stalled")
                        else "超时" if res["timed_out"]
                        else "取消" if res["cancelled"]
                        else "退出码 %s" % res["exit_code"])
                if res.get("deadline_exceeded"):
                    head = "任务总时限已到"
                if res.get("output_truncated"):
                    out["error"] = (
                        "CLI 输出超过捕获上限（stdout/stderr 各最多 %s 字节），"
                        "本次结果不完整" % MAX_AGENT_CAPTURE_BYTES)
                else:
                    out["error"] = head + ("；stderr/stdout: " + tail if tail else "")
                if kind == "codex" and not res.get("output_truncated"):
                    fm = _codex_fail_msg(res["stdout"])
                    if fm:
                        # 事件流终态错误比原始 JSONL 尾部可读，也是链降级判定依据
                        out["error"] = "codex: %s（退出码 %s）" % (fm, res["exit_code"])
                # 5D+2D：错误码归一
                if kind == "claude" and not res.get("output_truncated"):
                    parsed_err = _parse_claude_json(res["stdout"] or "")
                    if (not res.get("timed_out") and not res.get("cancelled")
                            and parsed_err and parsed_err.get("is_error")
                            and parsed_err.get("text")):
                        out["error"] = "claude 返回 is_error: " + parsed_err["text"][:500]
                        drift = _claude_drift_note(att.get("model"), parsed_err)
                        if drift:
                            out["error"] += "；" + drift
                            _log_note(log_path, drift)
                if (kind == "codex" and att.get("provider_id")
                        and not res.get("output_truncated")):
                    el = out["error"].lower()
                    if (("wire_api" in el and "no longer supported" in el)
                            or "no route matched" in el
                            or "同一网络错误重复" in out["error"]):
                        # codex 撞 chat-only 供应商（0.154 只讲 responses）：
                        # 自动冷却该供应商 30 分钟，链展开/路由绑定分随之
                        # 自动绕开——不用人工改绑定（2026-09-17 mo-so 实测）
                        try:
                            from . import modelhub as _mh
                            _mh.note_codex_wire_dead(att["provider_id"])
                        except Exception:
                            pass
                elif (not res.get("output_truncated")
                      and "no model configured" in out["error"].lower()):
                    # CLI 本体没配置（kimi 实测）→ 运行期自愈：把绑定注入其
                    # 自家配置，本次换将之后的下一轮即可用
                    try:
                        from . import manager as _mg
                        note = _mg.sync_cli_config_now(agent.get("id", ""))
                        if note and log_path:
                            _append_log_bytes(
                                log_path, ("\n[自愈] %s\n" % note).encode(
                                    "utf-8", "replace"))
                    except Exception:
                        pass
                banner_only = (kind == "opencode"
                               and not (res.get("timed_out")
                                        or res.get("cancelled")
                                        or res.get("output_truncated"))
                               and _opencode_banner_only(res))
                if not (res.get("timed_out") or res.get("cancelled")
                        or res.get("output_truncated")
                        or _quota_error(out["error"]) or _transient_error(out["error"])
                        or _rate_limited(out["error"])
                        or _permission_error(out["error"])):
                    # 尾段清洗后没有错误信号而流式审计日志里有：补日志错误行。
                    # 2026-09-22 实测 kimi 撞 429 时 stderr/stdout 只剩启动横幅
                    # （真错误只走了流式管道），路由层看不到配额关键词，换将
                    # 判死因全靠猜。
                    _augment_error_from_log(out, log_path)
                if banner_only:
                    out["error"] += ("（仅启动横幅、无错误文本：疑似本地启动/"
                                     "配置抖动，可换 CLI 补位）")
                # 超时只是死法不是死因：尾部带账号级死错（vsllm 403 额度¥0 案）
                # 时以死因为准，健康台账据此首观测即熔断，不再当瞬态盲重试。
                timeout_code = ErrorCode.TIMEOUT
                if res.get("timed_out"):
                    dead = classify_error_text(out["error"])
                    if dead is not None and is_account_dead_error(dead):
                        timeout_code = dead
                out["error_code"] = (ErrorCode.CANCELLED if res.get("cancelled")
                                      else timeout_code if res.get("timed_out")
                                      else ErrorCode.PARSE_FAIL
                                      if res.get("output_truncated")
                                      else ErrorCode.FORBIDDEN
                                      if _permission_error(out["error"])
                                      else classify_error_text(out["error"])
                                      or _classify_failure(res, kind=kind))
                # 纯横幅退出是 CLI 本地启动问题（VENDOR_ERROR 只是退出码兜底），
                # 归 LOCAL_STATE 让跨 CLI 换将闸门放行；日志里补出了真上游错误
                # （配额/权限等）时保留更强的分类。
                if (banner_only
                        and error_code_value(out["error_code"])
                        in ("", ErrorCode.VENDOR_ERROR.value)):
                    out["error_code"] = ErrorCode.LOCAL_STATE
                out["error"] += _login_hint(kind, out["error_code"],
                                            out["error"])
                retryable_stream = (not res.get("output_truncated")
                                    and stream_retries < STREAM_RETRY_N
                                    and _stream_retryable_failure(
                                        res, out["error"], kind=kind,
                                        readonly=readonly)
                                    and model_deadline - time.monotonic() > 1)
                if retryable_stream:
                    if _stream_retry_wait(cancel_event, STREAM_RETRY_DELAY_S):
                        _finish_attempt_ledger(ledger, checkpoint_run_id,
                                               command_context, att, out)
                        stream_retries += 1
                        _log_note(log_path, "只读流中断：第 %d 次原地重试" % stream_retries)
                        continue
                    _stream_retry_cancelled(out, cancel_event)
                _finish_attempt_ledger(ledger, checkpoint_run_id,
                                       command_context, att, out)
                break
            if kind == "codex":
                out["text"], out["usage"], out_sid = _parse_codex_jsonl(res["stdout"])
                out["sid"] = out_sid  # §07 T1.1：会话 id 供 revise/fix 复用
                out["tokens"] = out["usage"]["total"]
                # 退出码 0 不代表成功：配额耗尽时 turn.failed 收尾、进程仍正常退出，
                # 不判失败的话编排者会把错误信息当成果往下传
                fm = _codex_fail_msg(res["stdout"])
                if fm:
                    out["ok"] = False
                    out["error"] = "codex: %s" % fm
                    out["error_code"] = (ErrorCode.FORBIDDEN
                                          if _permission_error(out["error"])
                                          else classify_error_text(out["error"])
                                          or ErrorCode.VENDOR_ERROR)
                elif not out["text"]:  # 事件流解析失败时退化为取 stdout 尾部
                    out["text"] = clean_cli_text(res["stdout"][-2000:])
                if require_tools and out["ok"] and _codex_work_events(res["stdout"]) == 0:
                    # 实现步空转闸：exit 0 + 有话但零动手 → 判拒绝（fatal 语义正确，
                    # 不把谎报的「已改完」静默传给下游；auto 模式换将逻辑按 ok 触发，
                    # 仍可换别的 CLI 再试）。原文尾段留进错误供人核对。
                    out["ok"] = False
                    out["error"] = ("实现步零工具调用：模型未执行任何命令/文件改动，"
                                    "仅口头汇报（典型如谎报环境受限）。回答尾段: %s"
                                    % (out["text"] or "")[-300:])
                    out["error_code"] = ErrorCode.VENDOR_REFUSAL
                if not out["text"] and out["ok"]:
                    out["error_code"] = _classify_failure(res, kind="codex", empty_output=True)
                if (out["ok"] and not require_tools
                        and not res.get("cancelled") and not res.get("timed_out")):
                    # 轮次静默空答闸（2026-10-16 神瞳实案）：qa/评审等以回答为交付物
                    # 的步骤，模型吐完开场白、跑完工具就 turn.completed，exit 0 被
                    # 当成功，开场白冒充最终答案展示。先带会话原地追问一次补答；
                    # 仍不交卷则步骤标 partial，答案位明示「未给结论」，不再假装
                    # 完成。实现步（require_tools）不适用——改完文件静默收尾是常态。
                    silent, last_msg = _codex_silent_no_answer(res["stdout"])
                    if silent:
                        tried = False
                        n_left = model_deadline - time.monotonic()
                        if (out_sid and n_left > 2
                                and not (cancel_event is not None
                                         and cancel_event.is_set())):
                            tried = True
                            got, text2, usage2 = _codex_nudge_recover(
                                eff_agent, out_sid, readonly, att["model"],
                                env, workdir, min(timeout, n_left), deadline,
                                cancel_event, log_path, stall_t, activity_t)
                            for k in ("input", "output", "cached",
                                      "reasoning", "total"):
                                out["usage"][k] += int((usage2 or {}).get(k) or 0)
                            out["tokens"] = out["usage"]["total"]
                            if got:
                                out["text"] = text2
                                out["nudged"] = True
                                _log_note(log_path, "静默空答：已自动带会话追问一次，取得最终结论")
                                silent = False
                        if silent:
                            out["partial"] = True
                            warn = ("⚠ 模型本轮未给出结论：工具动作已执行但最终回答缺失"
                                    + ("（已自动追问一次仍未恢复）" if tried
                                       else "（会话不可续或预算已尽，未追问）")
                                    + "；可点「继续对话」让它接着作答。")
                            out["text"] = (((last_msg or "").strip() + "\n\n")
                                           if (last_msg or "").strip() else "") + warn
                            _log_note(log_path, warn)
            elif kind == "claude":
                parsed = _parse_claude_json(res["stdout"])
                if parsed is None:
                    out["ok"] = False
                    out["error"] = "claude 输出无法解析为 JSON；stdout 尾部: " + res["stdout"][-500:]
                    out["error_code"] = _classify_failure(res, kind="claude", parsed=None)
                    out["error"] += _login_hint("claude", out["error_code"], out["error"])
                    retryable_stream = (stream_retries < STREAM_RETRY_N
                                        and _stream_retryable_failure(
                                            res, out["error"], kind=kind,
                                            readonly=readonly)
                                        and model_deadline - time.monotonic() > 1)
                    if retryable_stream:
                        if _stream_retry_wait(cancel_event, STREAM_RETRY_DELAY_S):
                            _finish_attempt_ledger(ledger, checkpoint_run_id,
                                                   command_context, att, out)
                            stream_retries += 1
                            _log_note(log_path, "只读流中断：第 %d 次原地重试" % stream_retries)
                            continue
                        _stream_retry_cancelled(out, cancel_event)
                    _finish_attempt_ledger(ledger, checkpoint_run_id,
                                           command_context, att, out)
                    break
                out["text"] = parsed["text"]
                out["cost_usd"] = parsed["cost_usd"]
                out["tokens"] = parsed["tokens"]
                out["usage"] = parsed["usage"]
                out["sid"] = parsed.get("sid") or ""  # §07 T1.1
                drift = _claude_drift_note(att.get("model"), parsed)
                if drift:
                    _log_note(log_path, drift)
                if parsed["is_error"]:
                    out["ok"] = False
                    out["error"] = "claude 返回 is_error: " + parsed["text"][:500]
                    if drift:
                        out["error"] += "；" + drift
                    out["error_code"] = _classify_failure(res, kind="claude", parsed=parsed)
                    retryable_stream = (stream_retries < STREAM_RETRY_N
                                        and _stream_retryable_failure(
                                            res, out["error"], kind=kind,
                                            readonly=readonly)
                                        and model_deadline - time.monotonic() > 1)
                    if retryable_stream:
                        if _stream_retry_wait(cancel_event, STREAM_RETRY_DELAY_S):
                            _finish_attempt_ledger(ledger, checkpoint_run_id,
                                                   command_context, att, out)
                            stream_retries += 1
                            _log_note(log_path, "只读流中断：第 %d 次原地重试" % stream_retries)
                            continue
                        _stream_retry_cancelled(out, cancel_event)
                    _finish_attempt_ledger(ledger, checkpoint_run_id,
                                           command_context, att, out)
                    break
                if not out["text"] and attempt == 0:
                    _finish_attempt_ledger(ledger, checkpoint_run_id,
                                           command_context, att, out)
                    continue  # 空响应，同模型重试
                if not out["text"] and attempt == 1:
                    out["error_code"] = _classify_failure(
                        res, kind="claude", parsed=parsed, attempt_done=True)
            else:
                # generic（aider/opencode/kimi…）的 stdout 就是终端转录：ANSI 颜色、
                # \r 进度条、画线框全在里面。不清洗的话它同时污染两处——步骤摘要
                # （详情页卡片显示成 ◆/─ 墙）与评审 JSON 解析（转义混进正文）
                out["text"] = clean_cli_text(res["stdout"]).strip()
                if not out["text"]:
                    out["error_code"] = _classify_failure(res, kind=kind, empty_output=True)
                if out["ok"]:
                    # 退出码 0 不代表成功（同 codex 的 turn.failed 家族）：CLI 免费通道
                    # 停摆时打一行 error 横幅就正常退出，转录里全是横幅垃圾而没有回答
                    # ——不拦的话编排者把横幅当成果往下传，字数闸白白烧掉扩写轮
                    # （2026-10-22 短篇实案：mimo "free API service has ended"
                    # exit 0，两轮扩写各 6-8 秒「done」，字数原地踏步）。
                    dead = _generic_dead_banner(res)
                    if dead:
                        out["ok"] = False
                        out["text"] = ""
                        out["error"] = "CLI 通道停摆（exit 0）：%s" % dead
                        out["error_code"] = (classify_error_text(dead)
                                             or ErrorCode.VENDOR_ERROR)
            retryable_stream = (not out.get("ok")
                                and stream_retries < STREAM_RETRY_N
                                and _stream_retryable_failure(
                                    res, out.get("error"), kind=kind,
                                    readonly=readonly)
                                and model_deadline - time.monotonic() > 1)
            if retryable_stream:
                if _stream_retry_wait(cancel_event, STREAM_RETRY_DELAY_S):
                    _finish_attempt_ledger(ledger, checkpoint_run_id,
                                           command_context, att, out)
                    stream_retries += 1
                    _log_note(log_path,
                              "只读流中断：第 %d 次原地重试" % stream_retries)
                    continue
                _stream_retry_cancelled(out, cancel_event)
            _finish_attempt_ledger(ledger, checkpoint_run_id,
                                   command_context, att, out)
            break
        _report_key(att, out)
        raw = out.get("raw") or {}
        attempt_history.append({
            "model": out.get("model") or att.get("model") or "",
            "provider_id": out.get("provider_id") or att.get("provider_id") or "",
            "provider": ((out.get("provider") or {}).get("name", "")
                         if isinstance(out.get("provider"), dict) else ""),
            "kind": kind, "key_id": att.get("key_id") or "",
            "protocol": _attempt_protocol(att),
            "ok": bool(out.get("ok")),
            "duration": raw.get("duration", 0.0),
            "tokens": out.get("tokens") or 0,
            "usage": out.get("usage") or None,
            "cost_usd": out.get("cost_usd") or 0.0,
            "timed_out": bool(raw.get("timed_out")),
            "deadline_exceeded": bool(raw.get("deadline_exceeded")),
            "abort_marker": raw.get("abort_marker"),
            "error_code": error_code_value(out.get("error_code")),
            "error": (out.get("error") or "")[:500],
            "upstream": upstream,
        })
        out["attempts"] = list(attempt_history)
        out["forbidden_upstreams"] = sorted(forbidden_upstreams)
        # A local dsh admission block is a routing event, not a terminal
        # configuration error.  Give the remaining call-chain candidates a
        # chance before returning the exhausted dsh result.
        if raw.get("launch_budget_exhausted"):
            if ai < len(attempts) - 1:
                continue
            return out
        if (not out["ok"]
                and error_code_value(out.get("error_code"))
                == ErrorCode.MODEL_UNAVAILABLE.value):
            unavailable_models.add(model_key)
            provider = att.get("provider") or {}
            provider_name = provider.get("name") if isinstance(provider, dict) else ""
            if provider_name and model_key[1]:
                try:
                    from . import health
                    health.report_failure(
                        provider_name, out.get("error") or "",
                        model=model_key[1], provider_id=model_key[0],
                        error_code=ErrorCode.MODEL_UNAVAILABLE,
                    )
                except Exception:
                    log.debug("模型级健康状态记录失败", exc_info=True)
            # The same provider/model pair cannot be rescued by another key;
            # allow a different model or provider in the chain to take over.
            if any((str(candidate.get("provider_id") or ""),
                    str(candidate.get("model") or "").strip()) != model_key
                   for candidate in attempts[ai + 1:]):
                continue
            return out
        if out["ok"] or ai == len(attempts) - 1:
            remaining = model_deadline - time.monotonic()
            if (not out["ok"] and grace_left > 0 and _rate_limited(out.get("error"))
                    and remaining > 2
                    and not (cancel_event is not None and cancel_event.is_set())):
                # 链尾限流宽限：enumerate 活列表——把链尾这条 append 回去，
                # 下一轮迭代就是「原地重试一次」；再撞限流时预算已耗尽照常判死。
                grace_left -= 1
                grace_s = min(RATE_LIMIT_GRACE_S, remaining - 1)
                if _grace_wait(cancel_event, grace_s, log_path=log_path):
                    retry = dict(att, _deadline=model_deadline)
                    attempts.append(retry)
                    continue
            return out
        if _auth_error(out.get("error")):
            auth_failed_credentials.add(credential)
            if not any(_attempt_credential(candidate) not in auth_failed_credentials
                       for candidate in attempts[ai + 1:]):
                return out
            continue
        if _permission_error(out.get("error")):
            # 403 通常是该网关/上游拒绝当前请求；同一 host 换模型只会重撞，
            # 但不同 host 的候选仍可能有权限，应继续尝试异上游链项。
            if upstream != "unknown":
                forbidden_upstreams.add(upstream)
            out["forbidden_upstreams"] = sorted(forbidden_upstreams)
            if not any(_attempt_upstream(candidate) not in forbidden_upstreams
                       for candidate in attempts[ai + 1:]):
                return out
            continue
        # 换将闸门：什么失败值得烧链上下一个候选。
        # · 瞬态/配额（文本特征，含 CLI 内部 api_retry 的 429/5xx 字样）照旧；
        # · 超时：显式可换（看 res 标志位）——慢上游换快候选可能救回。
        #   2026-09-23 夜班实案：旧逻辑里超时能否换将全赌输出尾部碰巧含有
        #   "timeout" 字样（当时是步骤里读到的代码 timeout=900 撞的表）。
        # · 上游内容拒答（refusal）：是那家模型的过滤决定，不是任务终态，
        #   换异上游候选常常就活；判死则整 run 白烧（同案：gemini 拒答后
        #   链上云知声候选从未被尝试）。
        # · LOCAL_STATE 本地崩溃（NTSTATUS 硬崩、数据库锁）：责任不在上游也
        #   不是任务终态，链上其他 CLI 常常就活（与横幅秒退同族，2026-10-22
        #   opencode 3221226505 实案：文本无信号时闸门原先只认文本特征）。
        raw = out.get("raw") or {}
        local_state = (error_code_value(out.get("error_code"))
                       == ErrorCode.LOCAL_STATE.value)
        if (raw.get("cancelled")   # 取消是用户意志，绝不降级（尾段可能带瞬态字样）
                or not (local_state
                        or _transient_error(out.get("error"))
                        or _quota_error(out.get("error"))
                        or _refusal_error(out.get("error"))
                        or _permission_error(out.get("error"))
                        or raw.get("timed_out"))):
            return out  # 取消/解析失败等真终态不降级
        # 同一上游的模型连续两次超时就收手——
        # 链上候选绑同一上游时换将=换壳不换命（2026-09-22 429 集群教训；
        # 2026-09-23 夜班实案同一慢上游连烧 3×20 分钟全超时零产出），最多
        # 两个模型预算后转向其他上游。其余瞬态/拒答仍按链长全部尝试：各家病根
        # 不同，每个候选都值得一次机会。
        if raw.get("timed_out"):
            timed_out_upstreams[upstream] = timed_out_upstreams.get(upstream, 0) + 1
            if timed_out_upstreams[upstream] >= 2:
                skipped_upstreams.add(upstream)
    return out

