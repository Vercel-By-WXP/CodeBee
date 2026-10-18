"""Desktop sidecar: the existing engine with private lifecycle endpoints.

The desktop shell owns this process. Its secret stays in the main process and
sidecar environment, and is never exposed to the renderer or URL parameters.
"""
from __future__ import annotations

import argparse
import faulthandler
import hmac
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable, Dict, Tuple
from http.server import ThreadingHTTPServer
from socketserver import TCPServer

# The Windows 7 runtime is the embeddable Python: its ._pth file puts the
# interpreter into isolated mode where the script directory never reaches
# sys.path. Bootstrap our own directory (app/, the engine root) before any
# core import; harmless for the full modern runtime.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import paths
from core.runtime_lock import (
    ServiceAlreadyRunning,
    acquire_service,
    read_running_service,
    record_service,
)


class DesktopServer(ThreadingHTTPServer):
    """Private numeric loopback listener; startup must never depend on DNS."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # HTTPServer.server_bind calls getfqdn(), which can stall on macOS.
        # The desktop only serves loopback; its hostname is already known.
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


class DesktopPipe:
    """Native picker replies and parent liveness over the inherited stdin pipe."""

    def __init__(self, on_closed: Callable[[], None]):
        self.on_closed = on_closed
        self.condition = threading.Condition()
        self.pending: Dict[str, object] = {}
        self.closed = False

    def listen(self) -> None:
        try:
            while True:
                line = sys.stdin.buffer.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        continue
                    request_id = message.get("id")
                    if not isinstance(request_id, str):
                        continue
                    with self.condition:
                        if request_id in self.pending:
                            self.pending[request_id] = str(message.get("path") or "")
                            self.condition.notify_all()
                except (ValueError, TypeError):
                    continue
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()
            self.on_closed()

    def pick(self, mode: str, initial: str = "", title: str = "") -> Tuple[str, str, bool]:
        request_id = uuid.uuid4().hex
        with self.condition:
            if self.closed:
                return "", "桌面窗口已退出", True
            self.pending[request_id] = None
            print("CODEBEE_DESKTOP_PICK " + json.dumps({"id": request_id,
                  "mode": mode, "initial": initial, "title": title}, ensure_ascii=True),
                  flush=True)
            self.condition.wait_for(
                lambda: self.closed or self.pending[request_id] is not None, timeout=600)
            result = self.pending.pop(request_id)
            if result is None:
                return "", "原生对话框未返回", True
            return str(result), "", False


def _probe_codebee(port: int) -> bool:
    """A live CodeBee answers plain loopback GET / with 200 (token-gated only
    for remote callers, and the desktop attaches from 127.0.0.1)."""
    import http.client

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/")
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.status == 200
    except OSError:
        return False


def _read_persisted_token() -> str:
    try:
        data = json.loads((paths.DATA_DIR / "remote.json").read_text(encoding="utf-8"))
        token = str(data.get("token") or "")
    except Exception:
        token = ""
    return token


def _attach_to_running() -> None:
    """Data directory already owned: hand the window to the running service.

    Prints CODEBEE_DESKTOP_ATTACH and exits 0 — the shell then loads the
    existing engine instead of spawning (or killing!) one. Ports: prefer the
    holder's recorded port, fall back to the CLI default for owners started
    before recording existed; TUTTI_ATTACH_FALLBACK_PORTS overrides the
    fallback list (tests set it empty for determinism).
    """
    candidates: list = []
    recorded = int(read_running_service(paths.DATA_DIR).get("port") or 0)
    if recorded:
        candidates.append(recorded)
    fallback = os.environ.get("TUTTI_ATTACH_FALLBACK_PORTS", "8765")
    for item in fallback.split(","):
        item = item.strip()
        if item.isdigit() and int(item) not in candidates:
            candidates.append(int(item))
    for port in candidates:
        if _probe_codebee(port):
            print("CODEBEE_DESKTOP_ATTACH " + json.dumps(
                {"port": port, "token": _read_persisted_token()}), flush=True)
            return
    raise SystemExit("检测到 CodeBee 数据目录已被占用，但未能连上正在运行的服务；"
                     "请退出已有服务后重试（详见数据目录内 desktop-logs 日志）。")


def main() -> None:
    # Cover engine imports and the initial socket bind as well as engine.main().
    # Native DNS/file operations can stall before the engine's watchdog starts.
    faulthandler.dump_traceback_later(20, repeat=True, file=sys.stderr)
    parser = argparse.ArgumentParser(description="CodeBee desktop sidecar")
    # Dynamic port by default: the sidecar is a private loopback backend for
    # its shell, so it must never fight the CLI (or anything else) over 8765.
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    secret = os.environ.get("CODEBEE_DESKTOP_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("Desktop launcher secret is missing")
    if not 0 <= args.port <= 65535:
        raise SystemExit("Invalid desktop port")
    os.environ["CODEBEE_DESKTOP"] = "1"
    try:
        acquire_service(paths.DATA_DIR)
    except ServiceAlreadyRunning:
        _attach_to_running()
        return

    import main as engine
    from core import jobs, store

    shutdown_started = threading.Event()

    def shutdown(force: bool = False) -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        if force:
            jobs.begin_shutdown_drain()
            jobs.cancel_all()
        # Runner cancellation kills child trees first, then drains stdout/stderr
        # for up to ten seconds. Keep the sidecar alive long enough for that
        # cleanup so force quit does not leave a CLI child behind.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            info = jobs.workers_info()
            if not (info["alive"] or info["chat_alive"]):
                break
            time.sleep(0.1)
        # os._exit() below intentionally bypasses Python atexit handlers. Stop
        # the standalone child explicitly so closing the desktop shell also
        # closes the Our Free Model service owned by this sidecar.
        try:
            from core import our_free_model_service
            our_free_model_service.stop()
        except Exception:
            pass
        # The shell owns the sidecar process. Once the response is sent and
        # workers have drained, exit directly; calling HTTPServer.shutdown()
        # from an auxiliary thread can wait behind a keep-alive request.
        os._exit(0)

    pipe = DesktopPipe(lambda: shutdown(force=True))

    class DesktopHandler(engine.Handler):
        def _desktop_authed(self) -> bool:
            return (
                self.client_address[0] == "127.0.0.1"
                and not self.headers.get("X-Forwarded-For")
                and not self.headers.get("CF-Connecting-IP")
                and hmac.compare_digest(
                    self.headers.get("X-CodeBee-Desktop", ""), secret)
            )

        def _authed(self):
            # The renderer receives the secret through the private loopback
            # request interceptor; it never appears in the URL or page DOM.
            return self._desktop_authed() or super()._authed()

        def _route_get(self):
            if urlparse(self.path).path != "/api/desktop/status":
                return super()._route_get()
            if not self._desktop_authed():
                return self._json(403, {"error": "desktop authorization required"})
            events = [{key: run.get(key) for key in
                       ("id", "title", "task_id", "status", "ended_at")}
                      for run in store.list_runs(limit=50)]
            return self._json(200, {"pid": os.getpid(), "jobs": jobs.workers_info(),
                                    "events": events})

        def _route_post(self):
            apply_path = urlparse(self.path).path
            if apply_path == "/api/selfupdate/apply":
                # 桌面模式：引擎不能 npm 自升级，把意图移交给壳——壳从 registry
                # 拉 tarball 热替换 app/ 并重启本 sidecar（渲染层拿 started 轮询）。
                if not self._desktop_authed():
                    return self._json(403, {"error": "desktop authorization required"})
                latest = os.environ.get("TUTTI_SELFUPDATE_FAKE_LATEST", "")
                if not latest:
                    try:
                        from core import selfupdate
                        latest = str(selfupdate.check().get("latest") or "")
                    except Exception:
                        latest = ""
                if not latest:
                    return self._json(409, {"error": "未能获取最新版本号，请稍后再试"})
                print("CODEBEE_DESKTOP_APPLY " + json.dumps(
                    {"version": latest}), flush=True)
                return self._json(200, {"ok": True, "mode": "desktop-shell",
                                        "version": latest})
            if apply_path != "/api/desktop/shutdown":
                return super()._route_post()
            if not self._desktop_authed():
                return self._json(403, {"error": "desktop authorization required"})
            body = self._body()
            if body.get("force") is not True and not jobs.begin_restart_drain():
                return self._json(409, {"error": "任务仍在运行，请先完成或确认停止任务"})
            if body.get("force") is True:
                jobs.begin_shutdown_drain()
            self._json(200, {"ok": True})
            threading.Thread(target=shutdown, args=(body.get("force") is True,),
                             daemon=True).start()

    class ReadyServer(DesktopServer):
        def serve_forever(self, *a, **kw):
            print("CODEBEE_DESKTOP_READY " + json.dumps(
                  {"port": self.server_address[1], "pid": os.getpid()}), flush=True)
            threading.Thread(target=pipe.listen, daemon=True).start()
            return super().serve_forever(*a, **kw)

    # Bind before recovery/schedulers start. Never enter the CLI's stale-port
    # cleanup path: a desktop launch must not terminate a running CLI service.
    # 端口候选：请求端口优先（launcher 默认 8766，窗口 origin 跨重启稳定，
    # localStorage 界面偏好不丢）；被占自动回落随机端口——绝不与 CLI 的
    # 8765 冲突，也绝不因为偏好端口被占而起不来。--port 0 = 纯随机。
    candidates = ([args.port] if args.port else [8766]) + [0]
    server = None
    bind_errors = []
    for candidate in candidates:
        try:
            server = ReadyServer(("127.0.0.1", candidate), DesktopHandler)
            break
        except OSError as exc:
            bind_errors.append("%s: %s" % (candidate, exc))
    if server is None:
        raise SystemExit("CodeBee 端口无法绑定，请退出占用后重试："
                         + "; ".join(bind_errors))
    port = server.server_address[1]
    record_service(paths.DATA_DIR, port, os.getpid())
    engine.ThreadedServer = lambda *a, **kw: server
    engine._start_pet_keeper = lambda *a, **kw: None
    engine.pick_dialog.ask_directory = lambda initial="", title="选择文件夹": pipe.pick(
        "directory", initial, title)
    engine.pick_dialog.ask_file = lambda initial="", title="选择文件": pipe.pick(
        "file", initial, title)
    sys.argv = [str(paths.APP_DIR / "main.py"), "--host", "127.0.0.1",
                "--port", str(port), "--no-browser"]
    try:
        engine.main()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
