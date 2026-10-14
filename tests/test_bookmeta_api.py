# -*- coding: utf-8 -*-
"""作品信息生成接口的并行状态边界。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-bm-api-"))
sys.path.insert(0, str(ROOT / "app"))

from core import bookmeta  # noqa: E402
from core import store  # noqa: E402
import main as cb_main  # noqa: E402


class _ResponseHandler(cb_main.Handler):
    def __init__(self, body):
        self._test_body = body
        self.response = None

    def _body(self):
        return self._test_body

    def _json(self, code, payload):
        self.response = (code, payload)


class TestBookMetaGenerateApi(unittest.TestCase):
    def _call(self, task):
        handler = _ResponseHandler({"platform": "fanqie"})
        with patch.object(store, "get_task", return_value=task), \
                patch.object(store, "serial_chain_ids", return_value=[task["id"]]), \
                patch.object(store, "set_book_meta", return_value=True), \
                patch.object(bookmeta, "_resolve_author", return_value=None), \
                patch("main.threading.Thread") as thread:
            handler._api_book_meta_generate(task["id"])
        return handler.response, thread

    def test_allows_generation_while_main_task_runs(self):
        response, thread = self._call({
            "id": "t-bookmeta-api-running",
            "status": "running",
            "book_meta": {},
        })
        self.assertEqual(response, (200, {"ok": True, "started": True}))
        thread.assert_called_once()

    def test_same_platform_generation_remains_idempotent(self):
        response, thread = self._call({
            "id": "t-bookmeta-api-meta-running",
            "status": "running",
            "book_meta": {"fanqie": {"status": "running"}},
        })
        self.assertEqual(response, (200, {"ok": True, "already": True}))
        thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
