# -*- coding: utf-8 -*-
"""Regression tests for the passed/failed/blocked verdict axis and evidence kinds."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import attempt_ledger, contracts, error_codes, failure_notify, paths


class VerdictTaxonomyTests(unittest.TestCase):
    def test_success_is_passed(self):
        self.assertEqual(error_codes.attempt_verdict(True, ""), "passed")

    def test_environment_and_credential_failures_are_blocked(self):
        for code in ("MODEL_UNAVAILABLE", "NETWORK", "UPSTREAM_SERVER",
                     "RATE_LIMIT", "AUTH", "QUOTA", "TIMEOUT", "TOOL_LAUNCH",
                     "CANCELLED", "ENV_BLOCK", "LOCAL_STATE", "SIGNAL_IGNORED"):
            with self.subTest(code=code):
                self.assertEqual(error_codes.attempt_verdict(False, code), "blocked")
                self.assertTrue(error_codes.is_blocked_code(code))

    def test_product_failures_remain_failed(self):
        for code in ("PARSE_FAIL", "EMPTY", "VENDOR_REFUSAL", "VENDOR_ERROR",
                     "MAX_TOKENS", "CONTEXT_OVERFLOW"):
            with self.subTest(code=code):
                self.assertEqual(error_codes.attempt_verdict(False, code), "failed")
                self.assertFalse(error_codes.is_blocked_code(code))

    def test_unknown_code_is_failed_not_excused(self):
        self.assertEqual(error_codes.attempt_verdict(False, "SOMETHING_NEW"), "failed")


class FailureNotifyBlockedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(paths, "DATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self):
        return failure_notify._read()

    def test_blocked_only_chain_does_not_count_as_product_failure(self):
        result = failure_notify.record_all_candidates_failed(
            run_id="run-blocked", role="draft",
            attempts=[{"ok": False, "error_code": "MODEL_UNAVAILABLE"},
                      {"ok": False, "error_code": "NETWORK"}])
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "blocked_no_product_judgment")
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(self._state(), {})

    def test_product_failure_still_counts(self):
        result = failure_notify.record_all_candidates_failed(
            run_id="run-failed", role="draft",
            attempts=[{"ok": False, "error_code": "MODEL_UNAVAILABLE"},
                      {"ok": False, "error_code": "PARSE_FAIL"}])
        self.assertTrue(result["recorded"])
        self.assertEqual(result["consecutive_failures"], 1)

    def test_codeless_attempt_is_failed_conservatively(self):
        result = failure_notify.record_all_candidates_failed(
            run_id="run-codeless", role="draft",
            attempts=[{"ok": False}])
        self.assertTrue(result["recorded"])
        self.assertEqual(result["consecutive_failures"], 1)


class AttemptLedgerVerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = attempt_ledger.AttemptLedger(Path(self.tmp.name) / "attempts.jsonl")

    def test_finish_records_verdict_and_cache_status(self):
        row = self.ledger.finish("run-1", "draft", "p", "m", ok=False,
                                 error_code="MODEL_UNAVAILABLE",
                                 cache_status="saved")
        self.assertEqual(row["verdict"], "blocked")
        self.assertEqual(row["error_code"], "MODEL_UNAVAILABLE")
        self.assertEqual(row["cache_status"], "saved")

    def test_verdict_falls_back_to_error_text(self):
        row = self.ledger.finish("run-1", "draft", "p", "m", ok=False,
                                 error="Model is not configured for this provider")
        self.assertEqual(row["error_code"], "MODEL_UNAVAILABLE")
        self.assertEqual(row["verdict"], "blocked")

    def test_unknown_cache_status_is_not_persisted(self):
        row = self.ledger.finish("run-1", "draft", "p", "m", ok=True,
                                 cache_status="bogus")
        self.assertEqual(row["verdict"], "passed")
        self.assertNotIn("cache_status", row)

    def test_summary_separates_blocked_from_failed(self):
        self.ledger.finish("run-1", "draft", "p", "m", ok=True)
        self.ledger.finish("run-1", "draft", "p", "m", ok=False,
                           error_code="QUOTA")
        self.ledger.finish("run-1", "draft", "p", "m", ok=False,
                           error_code="PARSE_FAIL")
        summary = self.ledger.summary("run-1")
        self.assertEqual(summary["verdicts"], {"passed": 1, "failed": 1, "blocked": 1})
        self.assertEqual(summary["successful"], 1)
        self.assertEqual(summary["failed"], 1)


class EvidenceKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "contracts"
        self.dir.mkdir()
        patcher = mock.patch.object(contracts, "_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_is_not_verified_evidence(self):
        normalized = contracts.normalize_evidence(
            {"criterion": "output", "status": "passed", "kind": "read"})
        self.assertFalse(normalized["verified"])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            contracts.normalize_evidence(
                {"criterion": "output", "status": "passed", "kind": "vibes"})

    def test_receipt_separates_unverified_passes(self):
        contracts.create("task-1", {"acceptance_criteria": ["read output",
                                                           "assert result"]})
        contracts.add_evidence("task-1", {"criterion": "read output",
                                          "status": "passed", "kind": "read"})
        contracts.add_evidence("task-1", {"criterion": "assert result",
                                          "status": "passed", "kind": "assertion"})
        receipt, error = contracts.complete("task-1", {"id": "run-1", "status": "done"})
        self.assertIsNone(error)
        self.assertEqual(receipt["criteria_total"], 2)
        self.assertEqual(receipt["criteria_passed"], 1)
        self.assertEqual(receipt["criteria_passed_unverified"], 1)
        self.assertEqual(
            receipt["criteria_total"],
            receipt["criteria_passed"] + receipt["criteria_passed_unverified"]
            + receipt["criteria_failed"] + receipt["criteria_unverified"])

    def test_legacy_evidence_without_kind_still_counts(self):
        contracts.create("task-2", {"acceptance_criteria": ["assert result"]})
        contracts.add_evidence("task-2", {"criterion": "assert result",
                                          "status": "passed"})
        receipt, _ = contracts.complete("task-2", {"id": "run-2", "status": "done"})
        self.assertEqual(receipt["criteria_passed"], 1)
        self.assertEqual(receipt["criteria_passed_unverified"], 0)


if __name__ == "__main__":
    unittest.main()
