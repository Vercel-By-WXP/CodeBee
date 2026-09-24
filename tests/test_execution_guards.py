# -*- coding: utf-8 -*-
"""任务执行边界的回归测试。

这些测试刻意使用短命 Python 子进程/替身，不触发真实供应商调用；重点锁住
Windows 命令行降级、断流止损、共享 deadline 和 timeout 状态收口。
"""
from __future__ import annotations

import json
import os
import sys
import time
from unittest import mock

from base import BaseTest


class TestRunnerExecutionGuards(BaseTest):

    def test_repeat_abort_accepts_multiple_markers_and_reports_trigger(self):
        from app.core.runner import run_process

        script = (
            "import time\n"
            "print('stream disconnected', flush=True)\n"
            "time.sleep(30)\n"
        )
        started = time.time()
        result = run_process(
            argv=[sys.executable, "-c", script], timeout=20,
            repeat_abort=[("Reconnecting...", 2), ("stream disconnected", 1)])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result.get("abort_marker"), "stream disconnected")
        self.assertLess(time.time() - started, 5)

    def test_deadline_expires_without_starting_process(self):
        from app.core.runner import run_process

        result = run_process(argv=[sys.executable, "-c", "print('bad')"],
                             timeout=30, deadline=time.monotonic() - 1)
        self.assertFalse(result["ok"])
        self.assertTrue(result["deadline_exceeded"])
        self.assertEqual(result["duration"], 0.0)
        self.assertNotIn("bad", result["stdout"])

    def test_long_argv_prompt_is_written_to_file(self):
        from app.core import runner

        prompt = "长提示词" * 5000
        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": ["{prompt}"], "mode": "real"}
        argv, stdin_text, _prompt, tmp_files = runner._build_call(
            agent, "generic", "", True, None, prompt, workdir=str(self.workdir))
        try:
            self.assertIsNone(stdin_text)
            self.assertTrue(tmp_files)
            self.assertLess(sum(len(str(x)) + 1 for x in argv), 8191)
            self.assertIn("完整指令因命令行长度限制", argv[-1])
        finally:
            for path in tmp_files:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_invalid_placeholder_model_is_rejected_before_spawn(self):
        from app.core import runner

        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": ["-c", "print('spawned')"], "mode": "real",
                 "model": "auto"}
        with mock.patch.object(runner, "run_process") as process:
            result = runner.run_agent(agent, "hi", timeout=5)
        process.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("无效模型名", result["error"])

    def test_attempt_audit_records_model_and_duration(self):
        from app.core import runner

        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": ["-c", "print('ok')"], "mode": "real",
                 "call_chain": [{"model": "m1", "provider_id": "p1", "env": {}}]}
        result = runner.run_agent(agent, "hi", timeout=5)
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"][0]["model"], "m1")
        self.assertIn("duration", result["attempts"][0])


class TestTaskTimeoutState(BaseTest):

    def test_update_run_timeout_closes_steps_and_task(self):
        from app.core import store

        task = store.create_task({"type": "direct", "goal": "timeout",
                                  "workdir": str(self.workdir), "timeout_s": 1})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        store.add_step(run["id"], "draft", "x", "X")
        store.update_run(run["id"], expected_status="running", status="timeout",
                         error="任务总时限已到")
        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "timeout")
        self.assertEqual(saved["steps"][0]["status"], "timeout")
        self.assertEqual(store.get_task(task["id"])["status"], "timeout")
