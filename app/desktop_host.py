"""Desktop sidecar: the existing engine with private lifecycle endpoints.

The desktop shell owns this process. Its secret stays in the main process and
sidecar environment, and is never exposed to the renderer or URL parameters.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
import time
import uuid
from urllib.parse import urlparse
from typing import Callable, Dict, Tuple

from core import paths
from core.runtime_lock import ServiceAlreadyRunning, acquire_service


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


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeBee desktop sidecar")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    secret = os.environ.get("CODEBEE_DESKTOP_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("Desktop launcher secret is missing")
    if not 0 <= args.port <= 65535:
        raise SystemExit("Invalid desktop port")
    os.environ["CODEBEE_DESKTOP"] = "1"
    try:
        acquire_service(paths.DATA_DIR)
    except ServiceAlreadyRunning as exc:
        raise SystemExit(str(exc))

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
            if urlparse(self.path).path != "/api/desktop/shutdown":
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

    class DesktopServer(engine.ThreadedServer):
        daemon_threads = True

        def serve_forever(self, *a, **kw):
            print("CODEBEE_DESKTOP_READY " + json.dumps(
                  {"port": self.server_address[1], "pid": os.getpid()}), flush=True)
            threading.Thread(target=pipe.listen, daemon=True).start()
            return super().serve_forever(*a, **kw)

    # Bind before recovery/schedulers start. Never enter the CLI's stale-port
    # cleanup path: a desktop launch must not terminate a running CLI service.
    try:
        server = DesktopServer(("127.0.0.1", args.port), DesktopHandler)
    except OSError as exc:
        raise SystemExit("CodeBee 端口已被占用，请退出已有服务后重试：%s" % exc)
    port = server.server_address[1]
    engine.ThreadedServer = lambda *a, **kw: server
    engine._start_pet_keeper = lambda *a, **kw: None
    engine.pick_dialog.ask_directory = lambda initial="", title="选择文件夹": pipe.pick(
        "directory", initial, title)
    engine.pick_dialog.ask_file = lambda initial="", title="选择文件": pipe.pick(
        "file", initial, title)
    sys.argv = [str(paths.APP_DIR / "main.py"), "--host", "127.0.0.1",
                "--port", str(port), "--no-browser", "--no-public-tunnel"]
    try:
        engine.main()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
