# -*- coding: utf-8 -*-
"""MCP task_create 执行链回归：必须走 web 端同款 orchestration 链。

旧实现 enqueue 缺 run_id 且 kind="task"（未定义类型）→ 工具恒失败并留下
永不运行的孤儿任务（2026-10-22 审查 P1）。锁两条：
  1) 成功路径：enqueue 带 run_id + kind=orchestration，task 置 queued
  2) 入队失败路径：已创建的 run/task 必须收口为 failed（不永久排队中）
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import mcp_harness, paths


class McpTaskCreateChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        # mcp_harness 的 token 路径是 import 期常量，这里一并指到临时目录
        self.patchers = [
            mock.patch.object(paths, "DATA_DIR", tmp),
            mock.patch.object(mcp_harness, "_TOKEN_STORE_PATH", tmp / "mcp_tokens.json"),
            mock.patch.object(mcp_harness, "_allow_mutating", return_value=True),
        ]
        for p in self.patchers:
            p.start()
        self.task = {"id": "t-1", "title": "标题"}
        self.run = {"id": "r-1"}

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def _stores(self):
        store = mock.MagicMock()
        store.create_task.return_value = dict(self.task)
        store.create_run.return_value = dict(self.run)
        return store

    def test_success_path_enqueues_orchestration_with_run_id(self):
        store = self._stores()
        jobs = mock.MagicMock()
        # task_create 内部是 `from . import store, jobs`——从包属性解析，
        # 必须 patch app.core 包上的名字而不是 mcp_harness 的引用
        with mock.patch("app.core.store", store), mock.patch("app.core.jobs", jobs):
            out = mcp_harness.task_create({"type": "code", "goal": "g"})
        self.assertTrue(out.get("created"), out)
        self.assertEqual(out.get("run_id"), "r-1")
        jobs.enqueue.assert_called_once_with(
            {"kind": "orchestration", "run_id": "r-1", "task_id": "t-1"})
        store.update_task_status.assert_called_once_with("t-1", "queued")

    def test_enqueue_failure_closes_run_and_task(self):
        store = self._stores()
        jobs = mock.MagicMock()
        jobs.enqueue.side_effect = RuntimeError("queue full")
        with mock.patch("app.core.store", store), mock.patch("app.core.jobs", jobs):
            out = mcp_harness.task_create({"type": "code", "goal": "g"})
        self.assertIn("error", out, out)
        self.assertIn("入队失败", out["error"])
        run_updates = [c.kwargs for c in store.update_run.call_args_list]
        self.assertTrue(any(u.get("status") == "failed" for u in run_updates), run_updates)
        task_updates = [c.args for c in store.update_task_status.call_args_list]
        self.assertIn(("t-1", "failed"), task_updates, task_updates)


if __name__ == "__main__":
    unittest.main()
