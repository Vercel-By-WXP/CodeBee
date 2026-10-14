# -*- coding: utf-8 -*-
"""Review transitions accept only the declared source state."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import review_requests


class ReviewRequestStateGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "reviews.jsonl"
        scoped_file = patch.object(review_requests, "_FILE", self.path)
        scoped_file.start()
        self.addCleanup(scoped_file.stop)

    def _record_with_status(self, status):
        row = review_requests.create("run-1")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row, status=status)) + "\n")
        return row["id"]

    def test_assign_rejects_substring_of_requested(self):
        request_id = self._record_with_status("request")
        with self.assertRaisesRegex(ValueError, "状态"):
            review_requests.transition(request_id, "assign", reviewer="alice")
        self.assertEqual(review_requests.get(request_id)["status"], "request")

    def test_approve_rejects_substring_of_assigned(self):
        request_id = self._record_with_status("assign")
        with self.assertRaisesRegex(ValueError, "状态"):
            review_requests.transition(request_id, "approve", reviewer="alice")
        self.assertEqual(review_requests.get(request_id)["status"], "assign")

    def test_changes_requested_rejects_substring_of_assigned(self):
        request_id = self._record_with_status("assign")
        with self.assertRaisesRegex(ValueError, "状态"):
            review_requests.transition(request_id, "changes_requested", reviewer="alice")
        self.assertEqual(review_requests.get(request_id)["status"], "assign")

    def test_changes_requested_accepts_assigned_and_is_idempotent(self):
        request_id = review_requests.create("run-1")["id"]
        review_requests.transition(request_id, "assign", reviewer="alice")
        changed = review_requests.transition(
            request_id, "changes_requested", reviewer="bob")
        repeated = review_requests.transition(
            request_id, "changes_requested", reviewer="bob")
        self.assertEqual(changed["status"], "changes_requested")
        self.assertEqual(repeated["version"], changed["version"])


if __name__ == "__main__":
    unittest.main()
