"""Regression tests for CLI health-alert qualification."""
from __future__ import annotations

import tempfile
import unittest

from app.core import bench


class BenchAlertQualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bench.init(self.tmp.name, start_probe=False)
        bench._BENCHED.clear()

    def tearDown(self):
        bench._BENCHED.clear()
        bench.init(self.tmp.name, start_probe=False)
        self.tmp.cleanup()

    def test_first_failure_is_quarantined_but_not_system_alert(self):
        self.assertTrue(bench.record_failure("gemini-cli", "TOOL_LAUNCH"))

        self.assertTrue(bench.is_globally_benched("gemini-cli"))
        state = bench.snapshot()
        self.assertEqual([row["agent_id"] for row in state["accounts"]], ["gemini-cli"])
        self.assertEqual(state["alerts"], [])
        self.assertFalse(state["any_alerting"])

    def test_success_then_failure_is_a_real_recovery_alert(self):
        # A successful probe/real call establishes that this account was once
        # usable. A later dead error is then actionable system degradation.
        self.assertTrue(bench.record_success("cline-cli", at=100.0))
        self.assertTrue(bench.record_failure("cline-cli", "AUTH", at=200.0))

        state = bench.snapshot()
        self.assertEqual([row["agent_id"] for row in state["alerts"]], ["cline-cli"])
        self.assertTrue(state["any_alerting"])

    def test_legacy_down_row_without_success_evidence_is_not_alerting(self):
        bench._ACCOUNTS["pi"] = bench._normalize_state("pi", {
            "status": "down",
            "code": "MISSING_CREDENTIAL",
            "failed_at": 10,
            "recovered_at": 0,
        })

        state = bench.snapshot()
        self.assertEqual(state["alerts"], [])
        self.assertFalse(state["accounts"][0]["alerting"])

    def test_recovery_removes_alert_after_verified_failure(self):
        bench.record_success("qwencode", at=100.0)
        bench.record_failure("qwencode", "QUOTA", at=200.0)
        self.assertTrue(bench.snapshot()["any_alerting"])

        self.assertTrue(bench.record_success("qwencode", at=300.0))
        self.assertEqual(bench.snapshot()["alerts"], [])
        self.assertFalse(bench.snapshot()["any_alerting"])


if __name__ == "__main__":
    unittest.main()
