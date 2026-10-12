"""Desktop behavior tests using real isolated service processes, never user data."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.runtime_lock import ServiceAlreadyRunning, ServiceLease, acquire_service


class TestServiceLease(unittest.TestCase):
    def test_competing_process_and_crash_release(self):
        with tempfile.TemporaryDirectory(prefix="cb-lease-") as temporary:
            directory = Path(temporary)
            code = (
                "import sys,time; from pathlib import Path; "
                "from app.core.runtime_lock import ServiceLease; "
                "lease=ServiceLease(Path(sys.argv[1])); lease.acquire(); "
                "print('locked',flush=True); time.sleep(60)"
            )
            child = subprocess.Popen([sys.executable, "-u", "-c", code, temporary],
                                     cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(), b"locked")
                with self.assertRaises(ServiceAlreadyRunning):
                    ServiceLease(directory).acquire()
            finally:
                child.kill()
                child.communicate(timeout=5)
            with ServiceLease(directory):
                self.assertTrue((directory / "service.lock").exists())

    def test_different_data_directories_can_run_independently(self):
        with tempfile.TemporaryDirectory(prefix="cb-lease-") as temporary:
            with ServiceLease(Path(temporary) / "a"), ServiceLease(Path(temporary) / "b"):
                pass

    def test_context_releases_on_exception(self):
        with tempfile.TemporaryDirectory(prefix="cb-lease-") as temporary:
            with self.assertRaises(ValueError):
                with ServiceLease(Path(temporary)):
                    raise ValueError("test")
            with ServiceLease(Path(temporary)):
                pass

    def test_cached_service_can_be_reacquired_after_release(self):
        with tempfile.TemporaryDirectory(prefix="cb-lease-") as temporary:
            first = acquire_service(Path(temporary))
            first.release()
            second = acquire_service(Path(temporary))
            self.assertIsNot(first, second)
            second.release()


class Sidecar:
    def __init__(self, directory: Path, port: int = 0):
        self.directory = directory
        self.secret = "c" * 64
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "catalog.json").write_text("[]", encoding="utf-8")
        (directory / "settings.json").write_text('{"telemetry_errors": false}', encoding="utf-8")
        env = dict(os.environ, TUTTI_DATA=str(directory), TUTTI_ISOLATE_HOME="1",
                   TUTTI_TEST_NO_DEFAULT_CATALOG="1", CODEBEE_DESKTOP_SECRET=self.secret,
                   PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "app" / "desktop_host.py"), "--port", str(port)],
            cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        self.lines = queue.Queue()
        self.output = []

        def read():
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                self.output.append(line)
                self.lines.put(line)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.port = 0

    def ready(self):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("Sidecar exited: " + "\n".join(self.output))
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line.startswith("CODEBEE_DESKTOP_READY "):
                message = json.loads(line.split(" ", 1)[1])
                self.port = message["port"]
                return message
        raise AssertionError("Sidecar startup timed out: " + "\n".join(self.output))

    def request(self, method, endpoint, body=None, authenticated=True, headers=None):
        request_headers = dict(headers or {})
        if authenticated:
            request_headers["X-CodeBee-Desktop"] = self.secret
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=12)
        try:
            conn.request(method, endpoint, data, request_headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()  # Parent death protocol, no private HTTP needed.
                self.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if not self.process.stdin.closed:
            self.process.stdin.close()
        self.reader.join(timeout=2)
        self.process.stdout.close()


class TestDesktopSidecar(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cb-desktop-test-")
        self.directory = Path(self.temporary.name) / "data"
        self.sidecar = Sidecar(self.directory)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.sidecar.close)
        self.ready = self.sidecar.ready()

    def test_real_engine_ready_and_private_endpoints(self):
        self.assertEqual(self.ready["pid"], self.sidecar.process.pid)
        status, data = self.sidecar.request("GET", "/api/desktop/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["pid"], self.sidecar.process.pid)
        self.assertIn("events", data)
        for method, endpoint in [("GET", "/api/desktop/status"),
                                 ("POST", "/api/desktop/shutdown")]:
            status, _ = self.sidecar.request(method, endpoint, {}, authenticated=False)
            self.assertEqual(status, 403)
        status, _ = self.sidecar.request("GET", "/api/desktop/status", headers={"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(status, 403)

    def test_desktop_update_never_invokes_npm(self):
        status, data = self.sidecar.request("GET", "/api/selfupdate")
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "desktop")
        self.assertIn("/releases", data["download_url"])
        status, data = self.sidecar.request("POST", "/api/selfupdate/apply", {})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        status, _ = self.sidecar.request("POST", "/api/selfupdate/restart", {})
        self.assertEqual(status, 400)
        self.assertEqual(self.sidecar.request("GET", "/api/desktop/status")[0], 200)

    def test_shutdown_and_reopen_preserves_existing_data(self):
        marker = self.directory / "user-content.txt"
        marker.write_text("keep my data", encoding="utf-8")
        self.assertEqual(self.sidecar.request("POST", "/api/desktop/shutdown", {})[0], 200)
        self.assertEqual(self.sidecar.process.wait(timeout=12), 0)
        replacement = Sidecar(self.directory)
        self.addCleanup(replacement.close)
        replacement.ready()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep my data")

    def test_parent_pipe_eof_stops_service(self):
        self.sidecar.process.stdin.close()
        self.assertEqual(self.sidecar.process.wait(timeout=12), 0)
        with ServiceLease(self.directory):
            pass

    def test_second_service_is_rejected_before_store_recovery(self):
        second = Sidecar(self.directory)
        self.addCleanup(second.close)
        self.assertNotEqual(second.process.wait(timeout=10), 0)
        second.reader.join(timeout=2)
        self.assertTrue(any("已在使用此数据目录" in line for line in second.output))
        self.assertEqual(self.sidecar.request("GET", "/api/desktop/status")[1]["pid"], self.ready["pid"])

    def test_folder_picker_roundtrip_uses_parent_native_dialog(self):
        result = queue.Queue()

        def request_picker():
            try:
                result.put(self.sidecar.request("POST", "/api/pick_folder", {}))
            except Exception as exc:
                result.put(exc)

        thread = threading.Thread(target=request_picker, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        request = None
        while time.monotonic() < deadline:
            try:
                line = self.sidecar.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line.startswith("CODEBEE_DESKTOP_PICK "):
                request = json.loads(line.split(" ", 1)[1])
                break
        self.assertIsNotNone(request)
        self.assertEqual(request["mode"], "directory")
        reply = json.dumps({"id": request["id"], "path": str(self.directory)}).encode() + b"\n"
        self.sidecar.process.stdin.write(reply)
        self.sidecar.process.stdin.flush()
        thread.join(timeout=5)
        response = result.get(timeout=1)
        if isinstance(response, Exception):
            raise response
        self.assertEqual(response[0], 200)
        self.assertEqual(response[1]["path"], str(self.directory))


class TestDesktopModeGuards(unittest.TestCase):
    def test_desktop_relaunch_and_upgrade_are_denied_without_subprocesses(self):
        from app.core import selfupdate
        with mock.patch.dict(os.environ, {"CODEBEE_DESKTOP": "1"}), \
                mock.patch.object(selfupdate.subprocess, "Popen") as spawn:
            self.assertFalse(selfupdate.relaunch(8765))
            self.assertIn("error", selfupdate.apply_upgrade())
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
