import tempfile
import unittest
from pathlib import Path

from app.core import attempt_ledger


class AttemptLedgerTests(unittest.TestCase):
    def test_records_attempts_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = attempt_ledger.AttemptLedger(Path(tmp) / "attempts.jsonl")
            ledger.start("run-1", "draft", "p1", "m1")
            ledger.finish("run-1", "draft", "p1", "m1", ok=False,
                          error="429 rate limit", usage={"input": 10, "output": 2},
                          duration_s=1.2, retry_class="rate_limit")
            ledger.start("run-1", "draft", "p2", "m2")
            ledger.finish("run-1", "draft", "p2", "m2", ok=True,
                          usage={"input": 8, "output": 4, "cached": 3}, duration_s=0.8)
            rows = ledger.for_run("run-1")
            self.assertEqual(len(rows), 2)
            summary = ledger.summary("run-1")
            self.assertEqual(summary["attempts"], 2)
            self.assertEqual(summary["successful"], 1)
            self.assertEqual(summary["cached"], 3)
            self.assertEqual(summary["retry_classes"], {"rate_limit": 1})

    def test_error_fingerprint_is_stable_and_redacted(self):
        a = attempt_ledger.error_fingerprint("Authorization: sk-secret-123 failed")
        b = attempt_ledger.error_fingerprint("Authorization: sk-secret-999 failed")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)


if __name__ == "__main__":
    unittest.main()
