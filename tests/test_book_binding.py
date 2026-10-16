# -*- coding: utf-8 -*-
"""作品绑定改绑/解绑：台账落点、校准作废、接口边界（2026-10-16）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-binding-"))
sys.path.insert(0, str(ROOT / "app"))

from core import store  # noqa: E402
from core.publish import ledger  # noqa: E402
from core.publish import manager as pub  # noqa: E402
import main as cb_main  # noqa: E402


class _ResponseHandler(cb_main.Handler):
    def __init__(self, body):
        self._test_body = body
        self.response = None

    def _body(self):
        return self._test_body

    def _json(self, code, payload):
        self.response = (code, payload)


def _fresh_books():
    ledger._BOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ledger._BOOKS_FILE.write_text("{}", encoding="utf-8")


class TestRemoveBook(unittest.TestCase):
    def setUp(self):
        _fresh_books()

    def test_remove_follows_chain_to_root(self):
        ledger.save_book("t-root", "fanqie", {"book_id": "1234567890123456",
                                              "title": "书一"})
        # 登记落在连载链根任务上：从续写批次发起也要能删干净
        with patch.object(store, "serial_chain_ids",
                          return_value=["t-root", "t-next"]):
            self.assertTrue(ledger.remove_book("t-next", "fanqie"))
        self.assertIsNone(ledger.book_for("t-root", "fanqie"))
        self.assertEqual(ledger.load_books(), {})   # 空壳任务条目一并摘掉

    def test_remove_missing_is_idempotent_false(self):
        self.assertFalse(ledger.remove_book("t-none", "fanqie"))

    def test_remove_keeps_other_platform(self):
        ledger.save_book("t-a", "fanqie", {"book_id": "1234567890123456",
                                           "title": "书一"})
        ledger.save_book("t-a", "qimao", {"book_id": "13747182", "title": "书二"})
        self.assertTrue(ledger.remove_book("t-a", "fanqie"))
        self.assertIsNone(ledger.book_for("t-a", "fanqie"))
        self.assertIsNotNone(ledger.book_for("t-a", "qimao"))


class TestRegisterOverwrite(unittest.TestCase):
    def setUp(self):
        _fresh_books()
        ledger.save_book("t-r", "fanqie", {"book_id": "1234567890123456",
                                           "title": "旧书"})
        ledger.update_book("t-r", "fanqie", remote_total=3,
                           remote_synced_at="x")

    def test_rebind_clears_stale_calibration(self):
        with patch.object(store, "get_task", return_value={"id": "t-r"}):
            ok, err = pub.register_book("t-r", "fanqie", "新书",
                                        book_id="6789012345678901")
        self.assertTrue(ok, err)
        ent = ledger.book_for("t-r", "fanqie")
        self.assertEqual(ent["book_id"], "6789012345678901")
        self.assertEqual(ent["title"], "新书")
        self.assertEqual(ent["source"], "manual")
        self.assertNotIn("remote_total", ent)   # 换了书，旧校准数作废

    def test_rebind_same_values_keep_calibration(self):
        with patch.object(store, "get_task", return_value={"id": "t-r"}):
            ok, err = pub.register_book("t-r", "fanqie", "旧书",
                                        book_id="1234567890123456")
        self.assertTrue(ok, err)
        self.assertEqual(ledger.book_for("t-r", "fanqie").get("remote_total"), 3)

    def test_register_requires_title(self):
        with patch.object(store, "get_task", return_value={"id": "t-r"}):
            ok, err = pub.register_book("t-r", "fanqie", "  ")
        self.assertFalse(ok)
        self.assertIn("作品名", err)


class TestUnbindApi(unittest.TestCase):
    def setUp(self):
        _fresh_books()
        ledger.save_book("t-u", "fanqie", {"book_id": "1234567890123456",
                                           "title": "书一"})

    def _call(self, task_id="t-u", platform="fanqie", busy=False,
              task="__DEFAULT__"):
        handler = _ResponseHandler({"platform": platform})
        resolved = {"id": task_id} if task == "__DEFAULT__" else task
        with patch.object(store, "get_task", return_value=resolved), \
                patch.object(pub, "_st",
                             return_value={"status": "busy" if busy
                                           else "connected"}):
            handler._api_publish_unbind_book(task_id)
        return handler.response

    def test_unbind_ok(self):
        self.assertEqual(self._call(), (200, {"ok": True}))
        self.assertIsNone(ledger.book_for("t-u", "fanqie"))

    def test_unbind_missing_task_404(self):
        self.assertEqual(self._call(task_id="t-x", task=None),
                         (404, {"error": "任务不存在"}))

    def test_unbind_unknown_platform_400(self):
        code, _ = self._call(platform="douyin")
        self.assertEqual(code, 400)

    def test_unbind_busy_400(self):
        code, payload = self._call(busy=True)
        self.assertEqual(code, 400)
        self.assertIn("进行中", payload["error"])
        self.assertIsNotNone(ledger.book_for("t-u", "fanqie"))

    def test_unbind_without_binding_400(self):
        ledger.remove_book("t-u", "fanqie")
        code, _ = self._call()
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
