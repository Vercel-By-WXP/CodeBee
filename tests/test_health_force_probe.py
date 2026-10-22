"""「重新体检」真探测链回归：force_probe 清退避、并行重探异常通道。

背景（2026-10-22 用户实案）：通道体检页的「重新体检」原先只重读台账
（availability.payload 只读聚合），真实探测只在后台循环按 60/120/300s
退避做、熔断未过冷却的行根本不进名单——按钮点了状态永远不动。
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from unittest import mock

from app.core import health


class ForceProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        health.init(self.tmp.name, start_probe=False)

    def tearDown(self):
        health.init(self.tmp.name, start_probe=False)
        self.tmp.cleanup()

    def _failing(self, name="API provider", provider_id="provider-a"):
        health.report_failure(
            name,
            "退出码 1；stderr/stdout: HTTP 502 bad gateway",
            model="model-a",
            provider_id=provider_id,
            error_code="VENDOR_ERROR",
        )
        return health._PROVIDERS[name]

    def test_no_unhealthy_targets_returns_zeroes(self):
        self.assertEqual(health.force_probe(), (0, 0))

    def test_recovery_probe_flips_row_back_to_recovered(self):
        st = self._failing()
        with mock.patch.object(health, "_probe_one",
                               return_value=(True, False, True)):
            self.assertEqual(health.force_probe(), (1, 1))
        self.assertEqual(st["status"], "recovered")
        self.assertEqual(st["consecutive_failures"], 0)

    def test_wire_failure_keeps_down_row_and_marks_observed(self):
        st = self._failing()
        st["status"] = "down"
        with mock.patch.object(health, "_probe_one",
                               return_value=(False, False, True)):
            self.assertEqual(health.force_probe(), (1, 1))
        self.assertEqual(st["status"], "down")
        self.assertEqual(st.get("evidence"), health.EVIDENCE_OBSERVED)
        self.assertGreater(float(st.get("circuit_open_until") or 0), 0)

    def test_backoff_is_reset_even_when_probe_times_out(self):
        st = self._failing()
        st["probe_next_at"] = time.time() + 3600   # 原本 1 小时内后台不会探它
        st["probe_backoff_idx"] = 3
        gate = threading.Event()

        def hang(_st):
            gate.wait(5)
            return False, False, False

        with mock.patch.object(health, "_probe_one", side_effect=hang):
            targets, done = health.force_probe(join_timeout=0.2)
        self.assertEqual(targets, 1)
        self.assertEqual(done, 0)   # 没探完：完成数如实为 0，不假装成功
        self.assertLessEqual(st["probe_next_at"], time.time())   # 退避已清零
        gate.set()

    def test_static_and_healthy_rows_are_not_probed(self):
        st = self._failing(name="Static provider", provider_id="provider-s")
        st["static"] = True
        healthy = self._failing(name="Recovered provider",
                                provider_id="provider-r")
        healthy["status"] = "ok"
        with mock.patch.object(health, "_probe_one",
                               side_effect=AssertionError("must not probe")):
            self.assertEqual(health.force_probe(), (0, 0))

    def test_stale_row_identity_is_respected(self):
        # init 重建状态表后，旧引用的探针结果必须被身份校验丢弃
        st = self._failing()
        saved = (st["name"], st)
        health.init(self.tmp.name, start_probe=False)
        health.report_failure(
            saved[0],
            "退出码 1；stderr/stdout: HTTP 502 bad gateway",
            model="model-a",
            provider_id="provider-a",
            error_code="VENDOR_ERROR",
        )
        with mock.patch.object(health, "_probe_one",
                               return_value=(True, False, True)):
            self.assertEqual(health.force_probe(), (1, 1))
        self.assertIsNot(health._PROVIDERS.get("API provider"), saved[1])


if __name__ == "__main__":
    unittest.main()
