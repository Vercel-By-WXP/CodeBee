# -*- coding: utf-8 -*-
"""空间管家 HTTP 契约守测：默认只读、confirm 必须是真布尔、路径形状先验。

这层是「前端传什么都不能信」的最后一道：readonly 没解锁、confirm 写成字符串
"true"、路径相对或带 .. —— 任何一种都必须停在 dry-run / 400，绝不落到文件系统。
所有账本读写重定向到临时目录，测试不碰真实数据目录，也不碰 8765。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))

from core.cspace import api, ledger  # noqa: E402


class ApiContractTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cspace-api-")
        self.base = Path(self._tmp.name)
        patcher = mock.patch.object(ledger, "store_dir",
                                    side_effect=lambda base=None: Path(base) if base else self.base)
        self._mocked = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def call_get(self, tail, qs=None):
        return api.handle_get("/api/cspace/" + tail, qs or {})

    def call_post(self, tail, body):
        return api.handle_post("/api/cspace/" + tail, body)

    def test_status_is_readonly_by_default(self):
        code, data = self.call_get("status")
        self.assertEqual(200, code)
        self.assertTrue(data["readonly"], "全新安装必须默认只读")
        self.assertGreater(data["rules"], 100, "规则库没读到")
        self.assertIn("drives", data)
        self.assertIn("elevated", data)

    def test_unknown_endpoint_returns_404(self):
        code, data = self.call_get("no-such-api")
        self.assertEqual(404, code)
        self.assertIn("error", data)

    def test_scan_rejects_relative_and_traversal(self):
        for bad in ("relative/dir", "../outside", ""):
            code, data = self.call_get("scan", {"path": [bad]})
            self.assertEqual(400, code, "%s 应被拒" % bad)
            self.assertIn("error", data)

    def test_duplicates_requires_absolute_paths(self):
        code, data = self.call_get("duplicates", {"paths": ["relative"]})
        self.assertEqual(400, code)

    def test_write_blocked_while_readonly(self):
        for tail, body in (
            ("clean/run", {"plan_id": "p1", "confirm": True}),
            ("migrate/run", {"src": "C:\\a", "dst": "D:\\b", "confirm": True}),
            ("migrate/restore", {"src": "C:\\a", "confirm": True}),
            ("links/create", {"kind": "junction", "link": "C:\\l",
                              "target": "D:\\t", "confirm": True}),
            ("dedup/replace", {"keep": "C:\\a", "duplicate": "C:\\b",
                               "confirm": True}),
        ):
            code, data = self.call_post(tail, body)
            self.assertEqual(200, code, tail)
            self.assertFalse(data.get("started"), "%s 不该启动后台操作" % tail)
            self.assertIn("只读", str(data.get("error", "")), tail)

    def test_confirm_must_be_a_real_boolean(self):
        for value in ("true", 1, None, ["true"]):
            code, data = self.call_post(
                "clean/run", {"plan_id": "p1", "confirm": value})
            self.assertFalse(data.get("started"),
                             "confirm=%r 不该被当成确认" % value)
            self.assertTrue(data.get("dry_run") or data.get("error"),
                            "字符串/数字 confirm 必须落成 dry-run 或明确报错：%r" % data)

    def test_settings_can_unlock_but_stay_explicit(self):
        code, data = self.call_post("settings", {"readonly": False})
        self.assertEqual(200, code)
        self.assertFalse(data["settings"]["readonly"])
        code2, status = self.call_get("status")
        self.assertFalse(status["readonly"])
        # 即使解锁，confirm 仍必须显式给
        code3, dry = self.call_post("clean/run", {"plan_id": "p1"})
        self.assertFalse(dry.get("started"), "解锁不等于免确认")

    def test_settings_rejects_non_boolean(self):
        code, data = self.call_post("settings", {"readonly": "false"})
        self.assertEqual(400, code)

    def test_progress_unknown_op_is_explicit(self):
        code, data = self.call_get("progress", {"op": ["never-existed"]})
        self.assertEqual(200, code)
        self.assertEqual("unknown", data["status"])

    def test_no_endpoint_leaks_a_traceback(self):
        code, data = self.call_post("clean/plan", {"paths": ["../evil"]})
        self.assertEqual(400, code)
        self.assertNotIn("Traceback", str(data))


if __name__ == "__main__":
    unittest.main()
