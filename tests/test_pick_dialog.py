# -*- coding: utf-8 -*-
"""原生「选择文件夹」对话框回归：/api/pick_folder 路由语义 + ask_directory 子进程收口。

对话框本体（tkinter）在无头测试里不真弹：子进程调用一律 mock，
只验证参数传递、stdout 解析和 fallback/取消三态映射。"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
import main as main_module  # noqa: E402  （main.py 以 app 目录为运行根）
import pick_dialog  # noqa: E402


def _ok(path):
    return subprocess.CompletedProcess(args=[], returncode=0,
                                       stdout=json.dumps({"path": path}).encode("utf-8"),
                                       stderr=b"")


class TestPickFolderRoute(unittest.TestCase):
    def _handler(self, body=None):
        handler = object.__new__(main_module.Handler)
        handler._body = lambda: body or {}
        handler.responses = []
        handler._json = lambda code, payload: handler.responses.append((code, payload)) or payload
        handler._forwarded_ip = lambda: ("127.0.0.1", None)
        return handler

    def test_remote_request_rejected(self):
        handler = self._handler({"initial": "E:\\x"})
        handler._forwarded_ip = lambda: ("192.168.1.9", "1")
        handler._api_pick_folder()
        code, payload = handler.responses[0]
        self.assertEqual(code, 403)
        self.assertIn("仅限本机", payload["error"])

    def test_success_returns_path(self):
        handler = self._handler({"initial": "E:\\a", "title": "选择文件夹"})
        with mock.patch.object(main_module.pick_dialog, "ask_directory",
                               return_value=("E:\\选中的目录", "", False)) as ad:
            handler._api_pick_folder()
        code, payload = handler.responses[0]
        self.assertEqual(code, 200)
        self.assertEqual(payload, {"path": "E:\\选中的目录"})
        ad.assert_called_once_with("E:\\a", "选择文件夹")

    def test_cancel_returns_empty_path(self):
        handler = self._handler()
        with mock.patch.object(main_module.pick_dialog, "ask_directory",
                               return_value=("", "", False)):
            handler._api_pick_folder()
        self.assertEqual(handler.responses[0][1], {"path": ""})

    def test_no_tkinter_maps_to_fallback(self):
        handler = self._handler()
        with mock.patch.object(main_module.pick_dialog, "ask_directory",
                               return_value=("", "No module named 'tkinter'", True)):
            handler._api_pick_folder()
        code, payload = handler.responses[0]
        self.assertEqual(code, 200)
        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["path"], "")

    def test_second_request_busy_while_dialog_open(self):
        handler = self._handler()
        self.assertTrue(main_module._PICK_LOCK.acquire(blocking=False))
        try:
            handler._api_pick_folder()
            self.assertTrue(handler.responses[0][1].get("busy"))
        finally:
            main_module._PICK_LOCK.release()


class TestAskDirectory(unittest.TestCase):
    def test_child_payload_and_result(self):
        with mock.patch.object(pick_dialog.subprocess, "run",
                               return_value=_ok("E:\\目录")) as run:
            path, err, fb = pick_dialog.ask_directory("E:\\a", "选择文件夹")
        self.assertEqual((path, err, fb), ("E:\\目录", "", False))
        req = json.loads(run.call_args.kwargs["input"].decode("utf-8"))
        self.assertEqual(req, {"initial": "E:\\a", "title": "选择文件夹"})
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("pick_dialog.py"))

    def test_child_failure_maps_to_fallback(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"",
                                          stderr=b"Traceback\nModuleNotFoundError: tkinter")
        with mock.patch.object(pick_dialog.subprocess, "run", return_value=bad):
            path, err, fb = pick_dialog.ask_directory("", "t")
        self.assertEqual((path, fb), ("", True))
        self.assertIn("tkinter", err)

    def test_spawn_failure_maps_to_fallback(self):
        with mock.patch.object(pick_dialog.subprocess, "run",
                               side_effect=OSError("python not found")):
            path, err, fb = pick_dialog.ask_directory("", "t")
        self.assertEqual((path, fb), ("", True))
        self.assertIn("python not found", err)

    def test_bad_stdout_maps_to_fallback(self):
        junk = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"\xff\xfe not json",
                                           stderr=b"")
        with mock.patch.object(pick_dialog.subprocess, "run", return_value=junk):
            path, err, fb = pick_dialog.ask_directory("", "t")
        self.assertEqual((path, fb), ("", True))


if __name__ == "__main__":
    unittest.main()
