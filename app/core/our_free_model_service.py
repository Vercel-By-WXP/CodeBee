# -*- coding: utf-8 -*-
"""托管 dsh-our-free-model standalone 的进程生命周期。

CodeBee 只停止自己创建的 Node 进程。用户已经手动启动的 standalone 会被
探测并复用，退出 CodeBee 时不会误杀它。所有失败都是可显示的状态，不阻塞
CodeBee 主服务启动。
"""
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


_DEFAULT_PORT = 18900
_MIN_NODE = (22, 19, 0)
_READY_SERVICE = "our-free-model-standalone"


class OurFreeModelServiceManager:
    """Discover, start, reuse and stop the local Our Free Model service."""

    def __init__(self, roots=None, data_dir=None, node=None, ready_timeout=8.0,
                 poll_interval=0.2):
        self._roots_override = list(roots) if roots is not None else None
        self._data_dir_override = Path(data_dir).expanduser() if data_dir else None
        self._node_override = node
        self.ready_timeout = max(0.0, float(ready_timeout))
        self.poll_interval = max(0.01, float(poll_interval))
        self._lock = threading.RLock()
        self._proc = None
        self._log_handle = None
        self._managed = False
        self._status = {
            "state": "not_started",
            "managed": False,
            "pid": None,
            "root": None,
            "data_dir": None,
            "port": None,
            "error": "",
            "message": "尚未启动",
        }

    @staticmethod
    def _valid_root(root):
        path = Path(root).expanduser()
        return path if (path / "packages" / "standalone" / "cli.mjs").is_file() else None

    def _default_roots(self):
        from . import paths

        repo = paths.ROOT
        candidates = []
        for key in ("OUR_FREE_MODEL_ROOT", "OFM_STANDALONE_ROOT"):
            value = os.environ.get(key, "").strip()
            if value:
                candidates.append(Path(value).expanduser())
        parent = repo.parent
        candidates.extend([
            parent / "dsh-our-free-model",
            parent / "dsh-our-free-model-main",
            parent / "dsh-ofm-review-extracted" / "dsh-our-free-model-main",
            repo / "dsh-our-free-model",
            repo / "dsh-our-free-model-main",
        ])
        return candidates

    def find_installation(self):
        candidates = self._roots_override if self._roots_override is not None else self._default_roots()
        seen = set()
        for candidate in candidates:
            try:
                path = Path(candidate).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            marker = str(path).lower() if os.name == "nt" else str(path)
            if marker in seen:
                continue
            seen.add(marker)
            if self._valid_root(path):
                return path
        return None

    def _data_dir(self):
        if self._data_dir_override is not None:
            return self._data_dir_override.resolve()
        configured = (os.environ.get("OFM_HOME") or
                      os.environ.get("OUR_FREE_MODEL_HOME") or "").strip()
        if not configured and os.environ.get("TUTTI_DATA", "").strip():
            # CodeBee 的隔离/多实例运行也必须隔离 standalone 数据，避免测试或
            # 临时实例触碰用户的 ~/.our-free-model。
            return (Path(os.environ["TUTTI_DATA"]).expanduser().resolve() /
                    "our-free-model")
        return (Path(configured).expanduser().resolve() if configured else
                (Path.home() / ".our-free-model").resolve())

    def _node(self):
        if self._node_override:
            return str(self._node_override)
        configured = os.environ.get("OUR_FREE_MODEL_NODE", "").strip()
        return configured or shutil.which("node")

    @staticmethod
    def _creationflags():
        return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def _kill_owned_process(self, proc):
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=5, creationflags=self._creationflags(),
                )
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass

    def _node_version(self, node):
        try:
            result = subprocess.run(
                [node, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False, timeout=4,
                creationflags=self._creationflags(),
            )
            raw = (result.stdout or result.stderr or "").strip()
            if not raw.startswith("v"):
                return None
            parts = raw[1:].split(".")
            values = tuple(int(part) for part in parts[:3])
            return values if len(values) == 3 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _node_supported(version):
        if not version:
            return False
        major, minor, patch = version
        return (major == 22 and (minor, patch) >= _MIN_NODE[1:]) or major >= 24

    def _read_port(self, data_dir):
        try:
            settings = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            settings = {}
        port = settings.get("standalonePort") if isinstance(settings, dict) else None
        return port if isinstance(port, int) and 1 <= port <= 65535 else _DEFAULT_PORT

    @staticmethod
    def _live_lock_pid(data_dir):
        """Return the live standalone PID recorded for this exact data dir."""
        try:
            owner = json.loads((data_dir / "service.lock").read_text(encoding="utf-8"))
            pid = owner.get("pid") if isinstance(owner, dict) else None
            if (owner.get("product") != _READY_SERVICE or
                    not isinstance(pid, int) or pid <= 0):
                return None
            try:
                os.kill(pid, 0)
            except PermissionError:
                return pid
            except (OSError, SystemError):
                return None
            return pid
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _health_url(port):
        return "http://127.0.0.1:%d/health" % int(port)

    def _probe_health(self, port):
        try:
            req = urllib.request.Request(self._health_url(port), method="GET",
                                         headers={"User-Agent": "codebee-orchestrator/1.0"})
            with urllib.request.urlopen(req, timeout=0.35) as response:
                if int(getattr(response, "status", 200)) >= 400:
                    return False
                payload = json.loads(response.read(64 * 1024).decode("utf-8", "replace"))
            if not isinstance(payload, dict):
                return False
            service = str(payload.get("service") or "")
            return payload.get("product") == "standalone" or service == _READY_SERVICE
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            return False

    def _probe_existing(self):
        data_dir = self._data_dir()
        # A health response on the shared default port is insufficient: another
        # CodeBee test/instance may own a different standalone data directory.
        # Require the lock created by standalone in this exact directory first.
        if self._live_lock_pid(data_dir) is None:
            return False, None
        configured = self._read_port(data_dir)
        ports = [configured] if configured == _DEFAULT_PORT else [configured, _DEFAULT_PORT]
        for port in ports:
            if self._probe_health(port):
                return True, port
        return False, None

    def _wait_ready(self, data_dir):
        deadline = time.monotonic() + self.ready_timeout
        while True:
            port = self._read_port(data_dir)
            owner_pid = self._live_lock_pid(data_dir)
            if (self._proc is not None and owner_pid == getattr(self._proc, "pid", None)
                    and self._probe_health(port)):
                return port
            if self._proc is not None and self._proc.poll() is not None:
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval)

    def _set_status(self, **updates):
        self._status.update(updates)
        return dict(self._status)

    def _cleanup_dead_process(self):
        """Release handles and POSIX descendants after the parent already died."""
        proc = self._proc
        if proc is None or proc.poll() is None:
            return
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (AttributeError, OSError):
                pass
        self._managed = False
        self._proc = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def status(self):
        with self._lock:
            if self._proc is not None and self._managed and self._proc.poll() is not None:
                self._cleanup_dead_process()
                self._status.update({
                    "state": "failed",
                    "managed": False,
                    "pid": None,
                    "error": "standalone 进程已提前退出",
                    "message": "standalone 启动后异常退出",
                })
            return dict(self._status)

    def public_status(self):
        """Return lifecycle fields safe to expose through CodeBee's API."""
        current = self.status()
        error = str(current.get("error") or "")[:240]
        for private in (current.get("root"), current.get("data_dir")):
            if private:
                error = error.replace(str(private), "<local>")
        return {"state": current.get("state"), "managed": bool(current.get("managed")),
                "port": current.get("port"), "message": current.get("message") or "",
                "error": error}

    def start(self):
        with self._lock:
            if self._proc is not None and self._managed and self._proc.poll() is None:
                return self.status()
            if self._proc is not None and self._managed:
                self._cleanup_dead_process()

            data_dir = self._data_dir()
            existing, port = self._probe_existing()
            if existing:
                return self._set_status(
                    state="already_running", managed=False, pid=None,
                    root=str(self.find_installation() or ""), data_dir=str(data_dir),
                    port=port, error="", message="检测到已运行的 standalone，CodeBee 复用它",
                )

            root = self.find_installation()
            if root is None:
                return self._set_status(
                    state="unavailable", managed=False, pid=None, root=None,
                    data_dir=str(data_dir), port=None,
                    error="未找到 dsh-our-free-model standalone",
                    message="未找到 standalone；CodeBee 继续启动",
                )
            node = self._node()
            if not node:
                return self._set_status(
                    state="unavailable", managed=False, pid=None, root=str(root),
                    data_dir=str(data_dir), port=None, error="未找到 Node.js",
                    message="未找到 Node.js；CodeBee 继续启动",
                )
            version = self._node_version(node)
            if not self._node_supported(version):
                display = ".".join(str(part) for part in version) if version else "未知"
                return self._set_status(
                    state="unavailable", managed=False, pid=None, root=str(root),
                    data_dir=str(data_dir), port=None,
                    error="Node.js %s 不满足 ^22.19.0 或 >=24.0.0" % display,
                    message="Node.js 版本不足；CodeBee 继续启动",
                )

            log_handle = None
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                log_handle = (data_dir / "codebee-standalone.log").open(
                    "a", encoding="utf-8", errors="replace")
                cli = root / "packages" / "standalone" / "cli.mjs"
                env = dict(os.environ)
                env["OFM_HOME"] = str(data_dir)
                kwargs = {
                    "cwd": str(root),
                    "env": env,
                    "stdin": subprocess.DEVNULL,
                    "stdout": log_handle,
                    "stderr": log_handle,
                    "creationflags": self._creationflags(),
                }
                if os.name != "nt":
                    kwargs["start_new_session"] = True
                proc = subprocess.Popen(
                    [node, str(cli), "--data-dir", str(data_dir)], **kwargs)
                self._proc = proc
                self._log_handle = log_handle
                self._managed = True
                self._status = {
                    "state": "started",
                    "managed": True,
                    "pid": getattr(proc, "pid", None),
                    "root": str(root),
                    "data_dir": str(data_dir),
                    "port": None,
                    "error": "",
                    "message": "CodeBee 已启动 standalone",
                }
                ready_port = self._wait_ready(data_dir)
                if ready_port:
                    self._status.update({"port": ready_port,
                                         "message": "CodeBee 已托管运行 standalone"})
                elif proc.poll() is not None:
                    self._status.update({"state": "failed", "managed": False,
                                         "error": "standalone 启动后异常退出",
                                         "message": "standalone 启动失败"})
                    self._cleanup_dead_process()
                return dict(self._status)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self._managed = False
                self._proc = None
                if log_handle is not None:
                    log_handle.close()
                return self._set_status(
                    state="failed", managed=False, pid=None, root=str(root),
                    data_dir=str(data_dir), port=None, error=str(exc),
                    message="standalone 启动失败；CodeBee 继续启动",
                )

    def stop(self):
        with self._lock:
            proc = self._proc if self._managed else None
            if proc is None:
                return self.status()
            try:
                if proc.poll() is None:
                    if os.name != "nt":
                        try:
                            # start_new_session=True makes the child PID the
                            # process-group leader, so sidecars such as mihomo
                            # cannot outlive the Node parent.
                            os.killpg(proc.pid, signal.SIGTERM)
                        except (AttributeError, OSError):
                            proc.terminate()
                    else:
                        proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name != "nt":
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except (AttributeError, OSError):
                                self._kill_owned_process(proc)
                        else:
                            self._kill_owned_process(proc)
                        proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                try:
                    if proc.poll() is None:
                        self._kill_owned_process(proc)
                except (OSError, subprocess.SubprocessError):
                    pass
            finally:
                self._managed = False
                self._proc = None
                if self._log_handle is not None:
                    try:
                        self._log_handle.close()
                    except OSError:
                        pass
                    self._log_handle = None
                self._status.update({"state": "stopped", "managed": False,
                                     "pid": None, "message": "CodeBee 已关闭 standalone"})
            return dict(self._status)


manager = OurFreeModelServiceManager()


def start():
    """Start or reuse standalone using the production manager."""
    return manager.start()


def stop():
    """Stop only the standalone process created by this CodeBee instance."""
    return manager.stop()


def status():
    return manager.status()
