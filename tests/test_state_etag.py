# -*- coding: utf-8 -*-
"""真 HTTP 回归：稳定状态快照支持条件请求并省掉重复响应体。"""
import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import main  # noqa: E402
from core import paths  # noqa: E402


class StateEtagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.old_paths = paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR
        paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR = root, root / "tasks", root / "runs"
        cls.server = main.ThreadedServer(("127.0.0.1", 0), main.Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR = cls.old_paths
        cls.tmp.cleanup()

    def get(self, etag=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        headers = {"X-CodeBee-Client": "etag-test"}
        if etag:
            headers["If-None-Match"] = etag
        conn.request("GET", "/api/state", headers=headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_unchanged_state_returns_304_without_snapshot_body(self):
        status, headers, body = self.get()
        self.assertEqual(status, 200)
        self.assertTrue(body)
        etag = headers.get("ETag")
        self.assertTrue(etag)

        with mock.patch.object(main, "_state_payload_json",
                               side_effect=AssertionError("unchanged poll rebuilt snapshot")):
            status, headers, body = self.get(etag)
        self.assertEqual(status, 304)
        self.assertEqual(headers.get("ETag"), etag)
        self.assertNotIn("Content-Length", headers)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
