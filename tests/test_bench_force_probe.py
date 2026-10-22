# -*- coding: utf-8 -*-
"""手动「重新体检」的账号侧半环回归（2026-10-22 用户实案）。

CLI 账号隔离（bench 台账）的自动探针按 5→30 分钟退避走场，健康页
「重新体检」此前只重探供应商台账——用户登录/充值后点按钮对那排
「账号失效」行毫无作用，最长干等 30 分钟。现在 force_probe 同轮并发
重探隔离账号。
"""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from app.core import bench, health


class BenchManualProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bench.init(self.tmp.name, start_probe=False)
        bench._BENCHED.clear()
        health.init(self.tmp.name, start_probe=False)

    def tearDown(self):
        bench._BENCHED.clear()
        bench.init(self.tmp.name, start_probe=False)
        health.init(self.tmp.name, start_probe=False)
        self.tmp.cleanup()

    def test_quarantined_ids_lists_only_down_rows(self):
        bench.record_success("kimi-code")
        bench.record_failure("gemini-cli", "MISSING_CREDENTIAL")
        self.assertEqual(bench.quarantined_ids(), ["gemini-cli"])

    def test_probe_account_noops_for_unknown_or_recovered(self):
        self.assertFalse(bench.probe_account("nope"))
        bench.record_success("kimi-code")
        with mock.patch.object(bench, "_probe_account",
                               side_effect=AssertionError("must not probe")):
            self.assertFalse(bench.probe_account("kimi-code"))

    def test_probe_account_success_recovers_and_clears_run_bench(self):
        bench.record_failure("gemini-cli", "MISSING_CREDENTIAL")
        bench.bench("run-1", "gemini-cli", "AUTH")
        self.assertTrue(bench.is_benched("run-1", "gemini-cli"))
        with mock.patch.object(bench, "_probe_account", return_value=True):
            self.assertTrue(bench.probe_account("gemini-cli"))
        self.assertFalse(bench.is_globally_benched("gemini-cli"))
        self.assertFalse(bench.is_benched("run-1", "gemini-cli"))

    def test_probe_account_failure_keeps_quarantine_with_backoff(self):
        bench.record_failure("pi", "MISSING_CREDENTIAL")
        with mock.patch.object(bench, "_probe_account", return_value=False):
            self.assertTrue(bench.probe_account("pi"))
        state = bench.snapshot()["accounts"][0]
        self.assertEqual(state["status"], "down")
        self.assertEqual(state["probe_failures"], 1)
        self.assertGreater(float(state["next_probe_at"]), 0)

    def test_health_force_probe_includes_quarantined_accounts(self):
        bench.record_failure("gemini-cli", "MISSING_CREDENTIAL")
        with mock.patch.object(bench, "quarantined_ids",
                               return_value=["gemini-cli"]), \
             mock.patch.object(bench, "probe_account",
                               return_value=True) as probe_account:
            targets, done = health.force_probe()
        self.assertEqual((targets, done), (1, 1))
        probe_account.assert_called_once_with("gemini-cli")

    def test_health_force_probe_survives_bench_errors(self):
        with mock.patch.object(bench, "quarantined_ids",
                               side_effect=RuntimeError("ledger down")):
            self.assertEqual(health.force_probe(), (0, 0))


if __name__ == "__main__":
    unittest.main()
