# -*- coding: utf-8 -*-
"""HTTP 编排入口的失败收口、错误脱敏和终态摘要回归。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
import main as main_module  # noqa: E402  （main.py 以 app 目录为运行根）


class TestMainHardening(unittest.TestCase):
    def _handler(self, body=None):
        handler = object.__new__(main_module.Handler)
        handler._body = lambda: body or {}
        handler.responses = []
        handler._json = lambda code, payload: handler.responses.append((code, payload)) or payload
        return handler

    def test_create_task_closes_orphan_run_without_leaking_exception(self):
        handler = self._handler({"type": "code", "goal": "g"})
        task = {"id": "t-1", "title": "任务"}
        run = {"id": "r-1"}
        with mock.patch.object(main_module.log, "exception"), \
             mock.patch.object(main_module.store, "create_task", return_value=task), \
             mock.patch.object(main_module.store, "create_run", return_value=run), \
             mock.patch.object(main_module.store, "update_task_status",
                               side_effect=RuntimeError("C:\\private\\internal.json")), \
             mock.patch.object(main_module.store, "update_run") as update_run:
            handler._api_create_task()

        self.assertEqual(handler.responses[0][0], 503)
        self.assertNotIn("private", handler.responses[0][1]["error"])
        update_run.assert_called_once()
        self.assertEqual(update_run.call_args.kwargs["status"], "failed")

    def test_enqueue_failure_is_sanitized_and_persisted(self):
        handler = self._handler()
        with mock.patch.object(main_module.log, "exception"), \
             mock.patch.object(main_module.jobs, "enqueue",
                               side_effect=RuntimeError("C:\\private\\worker.log")), \
             mock.patch.object(main_module.store, "update_run") as update_run:
            ok, error = handler._enqueue_run("r-1", "t-1", {"run_id": "r-1"})

        self.assertFalse(ok)
        self.assertNotIn("private", error)
        update_run.assert_called_once()
        self.assertEqual(update_run.call_args.kwargs["status"], "failed")
        self.assertNotIn("private", update_run.call_args.kwargs["error"])

    def test_busy_rejection_is_explicit_and_never_queued(self):
        handler = self._handler()
        with mock.patch.object(main_module.log, "exception"), \
             mock.patch.object(main_module.jobs, "enqueue",
                               side_effect=main_module.jobs.JobsBusyError("full")), \
             mock.patch.object(main_module.store, "update_run") as update_run:
            ok, error = handler._enqueue_run("r-1", "t-1", {"run_id": "r-1"})

        self.assertFalse(ok)
        self.assertIn("未排队", error)
        self.assertEqual(update_run.call_args.kwargs["status"], "failed")

    def test_direct_result_includes_timeout_terminal_state(self):
        handler = self._handler()
        latest = {
            "id": "r-1", "task_id": "t-1", "status": "timeout",
            "error": "供应商请求超时", "created_at": "2026-09-18 10:00:00",
            "ended_at": "2026-09-18 10:00:03",
        }
        with mock.patch.object(main_module.store, "run_artifacts", return_value=("", [])), \
             mock.patch.object(main_module.store, "task_step_count", return_value=1):
            result = handler._direct_result(latest, "direct")

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["error"], "供应商请求超时")
        self.assertEqual(result["duration_s"], 3)


if __name__ == "__main__":
    unittest.main()
