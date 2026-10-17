# -*- coding: utf-8 -*-
"""只读流式供应商断流的有界原地重试。"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


def _result(*, ok, stdout="", stderr="", abort_marker=None):
    return {
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "stdout": stdout,
        "stderr": stderr,
        "duration": 0.01,
        "cancelled": False,
        "timed_out": False,
        "stalled": False,
        "deadline_exceeded": False,
        "abort_marker": abort_marker,
    }


_AGENT = {
    "id": "claude-code",
    "kind": "claude",
    "mode": "real",
    "command": "claude",
    "env": {},
    "model": "m1",
    "model_fallbacks": [],
}


class TestReadonlyStreamRetry(BaseTest):
    def _run(self, results, *, readonly=True):
        calls = []

        def fake_run_process(**kwargs):
            calls.append(kwargs)
            return results[min(len(calls) - 1, len(results) - 1)]

        with mock.patch("app.core.runner.run_process", side_effect=fake_run_process), \
                mock.patch("app.core.runner.STREAM_RETRY_DELAY_S", 0):
            out = __import__("app.core.runner", fromlist=["run_agent"]).run_agent(
                dict(_AGENT), "评审代码", workdir=str(self.workdir),
                readonly=readonly, timeout=10)
        return out, calls

    def test_readonly_stream_disconnect_retries_twice_then_succeeds(self):
        success = json.dumps({"result": "评审完成", "is_error": False,
                              "total_cost_usd": 0, "usage": {}})
        out, calls = self._run([
            _result(ok=False, stderr="provider stream disconnected",
                    abort_marker="stream disconnected"),
            _result(ok=False, stderr="connection reset by peer",
                    abort_marker="stream disconnected"),
            _result(ok=True, stdout=success),
        ])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["text"], "评审完成")
        self.assertEqual(len(calls), 3)
        self.assertEqual(out.get("stream_retries"), 2)

    def test_write_step_does_not_replay_after_stream_disconnect(self):
        out, calls = self._run([
            _result(ok=False, stderr="provider stream disconnected",
                    abort_marker="stream disconnected"),
        ], readonly=False)
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(out.get("stream_retries"), 0)

    def test_readonly_stream_retry_is_bounded(self):
        failure = _result(ok=False, stderr="provider stream disconnected",
                          abort_marker="stream disconnected")
        out, calls = self._run([failure, failure, failure, failure])
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(out.get("stream_retries"), 2)

