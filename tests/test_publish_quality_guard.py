"""Publishing must use the newest completed quality verdict."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core import quality_gate, store
from app.core.publish import ledger, manager


class PublishQualityGuardTests(unittest.TestCase):
    def test_release_guard_uses_store_chronology_not_random_run_id_suffix(self):
        older = {
            "id": "r-20261019-120000-9999", "created_at_ns": 100,
            "status": "done", "op": "run",
            "verdict": {"publishable": True, "revision": "older"},
        }
        newer = {
            "id": "r-20261019-120000-0001", "created_at_ns": 200,
            "status": "done", "op": "run",
            "verdict": {"publishable": True, "revision": "newer"},
        }
        with patch.object(store, "task_runs", return_value=[newer, older]), \
                patch.object(ledger, "book_for", return_value={}), \
                patch.object(quality_gate, "local_version",
                             return_value={"fingerprint": "current"}), \
                patch.object(quality_gate, "evaluate_release",
                             return_value={"allowed": True, "blockers": []}) as evaluate:
            decision = manager._quality_release_guard(
                {"id": "task-1", "type": "serial_novel"}, "qimao")

        self.assertTrue(decision["allowed"])
        self.assertEqual(evaluate.call_args.args[0]["revision"], "newer")
        self.assertEqual(
            evaluate.call_args.kwargs["current_manuscript_fingerprint"], "current")


if __name__ == "__main__":
    unittest.main()
