# -*- coding: utf-8 -*-
"""只读流式供应商断流的有界原地重试。"""
from __future__ import annotations

import json
import threading
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
    def _run(self, results, *, readonly=True, cancel_event=None,
             checkpoint_run_id="", ledger=None):
        calls = []

        def fake_run_process(**kwargs):
            calls.append(kwargs)
            return results[min(len(calls) - 1, len(results) - 1)]

        patches = [
            mock.patch("app.core.runner.run_process", side_effect=fake_run_process),
            mock.patch("app.core.runner.STREAM_RETRY_DELAY_S", 0),
        ]
        if ledger is not None:
            patches.append(mock.patch("app.core.runner._attempt_ledger",
                                      return_value=ledger))
        with patches[0], patches[1], (patches[2] if len(patches) > 2
                                      else mock.patch("app.core.runner._attempt_ledger",
                                                      return_value=None)):
            out = __import__("app.core.runner", fromlist=["run_agent"]).run_agent(
                dict(_AGENT), "评审代码", workdir=str(self.workdir),
                readonly=readonly, timeout=10, cancel_event=cancel_event,
                checkpoint_run_id=checkpoint_run_id)
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

    def test_cancel_during_backoff_is_final_and_does_not_retry(self):
        import app.core.runner as runner
        cancel = threading.Event()
        failure = _result(ok=False, stderr="provider stream disconnected",
                          abort_marker="stream disconnected")

        def cancel_wait(_event, _seconds):
            cancel.set()
            return False

        with mock.patch.object(runner, "run_process", return_value=failure) as process, \
                mock.patch.object(runner, "_stream_retry_wait", side_effect=cancel_wait):
            out = runner.run_agent(dict(_AGENT), "评审代码",
                                   workdir=str(self.workdir), cancel_event=cancel,
                                   timeout=10)
        self.assertEqual(process.call_count, 1)
        from app.core.error_codes import ErrorCode
        self.assertEqual(out["error_code"], ErrorCode.CANCELLED)
        self.assertTrue(out["raw"]["cancelled"])

    def test_each_stream_attempt_is_closed_in_provider_ledger(self):
        success = json.dumps({"result": "恢复", "is_error": False,
                              "total_cost_usd": 0, "usage": {}})

        class Ledger:
            def __init__(self):
                self.started = 0
                self.finished = []

            def start(self, *args, **kwargs):
                self.started += 1

            def finish(self, *args, **kwargs):
                self.finished.append(kwargs)

        ledger = Ledger()
        _out, _calls = self._run([
            _result(ok=False, stderr="provider stream disconnected",
                    abort_marker="stream disconnected"),
            _result(ok=True, stdout=success),
        ], ledger=ledger, checkpoint_run_id="run-1")
        self.assertEqual(ledger.started, 2)
        self.assertEqual(len(ledger.finished), 2)

