# -*- coding: utf-8 -*-
"""opencode 纯启动横幅退出：不论跑多久都按本地状态错误交给跨 CLI 换将。

2026-10-22 实案：opencode 跑了 2 分 40 秒只输出「> build · glm-5.3」，退出码 1；
旧逻辑只在 ≤15s 时识别该签名，长时失败被归成 VENDOR_ERROR，换将闸门
（routing_strategy.should_fallback）直接判「无需跨 CLI 降级」。
"""
import unittest
from unittest import mock

from app.core import runner, routing_strategy
from app.core.error_codes import ErrorCode


def _res(**over):
    base = {"ok": False, "exit_code": 1, "stdout": "> build · glm-5.3",
            "stderr": "", "duration": 160.0, "cancelled": False,
            "timed_out": False, "stalled": False, "deadline_exceeded": False,
            "output_truncated": False, "abort_marker": None}
    base.update(over)
    return base


def _agent():
    return {"id": "opencode", "kind": "opencode", "mode": "real",
            "command": "opencode", "model": "glm-5.3",
            "orch": {"timeout_ms": 1000}}


class OpencodeBannerOnlyTests(unittest.TestCase):
    def test_detector_has_no_duration_limit_but_fast_path_does(self):
        self.assertTrue(runner._opencode_banner_only(_res()))
        self.assertTrue(runner._opencode_banner_only(_res(duration=2.0)))
        self.assertFalse(runner._opencode_fast_banner_death(_res()))
        self.assertTrue(runner._opencode_fast_banner_death(_res(duration=2.0)))

    def test_real_error_text_is_not_banner_only(self):
        self.assertFalse(runner._opencode_banner_only(_res(
            stdout="> build · glm-5.3\nError: 403 Forbidden")))
        self.assertFalse(runner._opencode_banner_only(_res(exit_code=0)))

    def test_long_banner_death_is_local_state_and_fallback_eligible(self):
        with mock.patch.object(runner, "_build_call",
                               return_value=(["opencode"], "p", "p", [])), \
             mock.patch.object(runner, "run_process", return_value=_res()):
            result = runner.run_agent(
                _agent(), "prompt", timeout=2,
                command_context={"run_id": "r-banner-long", "role": "draft"},
                checkpoint_run_id="r-banner-long")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], ErrorCode.LOCAL_STATE.value)
        self.assertIn("仅启动横幅", result["error"])
        self.assertTrue(routing_strategy.BuiltinStrategy().should_fallback(result))

    def test_short_banner_death_retries_once_in_place(self):
        proc = mock.Mock(return_value=_res(duration=12.0))
        with mock.patch.object(runner, "_build_call",
                               return_value=(["opencode"], "p", "p", [])), \
             mock.patch.object(runner, "run_process", proc), \
             mock.patch.object(runner.time, "sleep", lambda *_a, **_k: None):
            result = runner.run_agent(
                _agent(), "prompt", timeout=2,
                command_context={"run_id": "r-banner-short", "role": "draft"},
                checkpoint_run_id="r-banner-short")
        self.assertEqual(proc.call_count, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], ErrorCode.LOCAL_STATE.value)


if __name__ == "__main__":
    unittest.main()
