import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core import review_storage


class ReviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = review_storage._DB_PATH
        review_storage._DB_PATH = None
        review_storage.init(self.tmp.name)
        self.addCleanup(self._restore)

    def _restore(self):
        review_storage._DB_PATH = self.old

    def test_redacts_and_ignores_raw_prompt_fields(self):
        ident = review_storage.record_review({
            "id": "review-1", "task_id": "task-1", "run_id": "run-1",
            "author_cli": "codex", "reviewer_cli": "claude",
            "scores": {"security": 9.5, "bad": "not-a-number"},
            "line_comments": [{"path": r"C:\Users\alice\repo\x.py",
                                "line": 4, "severity": "high",
                                "message": "sk-secret-token-123456789"}],
            "passed": True, "prompt": "do not persist this prompt",
            "raw_output": "do not persist this output",
        })
        self.assertEqual(ident, "review-1")
        row = review_storage.list_reviews("task-1")[0]
        self.assertEqual(row["scores"], {"security": 9.5})
        self.assertNotIn("prompt", row)
        self.assertNotIn("raw_output", row)
        self.assertNotIn("sk-secret-token", json.dumps(row))
        self.assertNotIn("C:\\Users\\alice", json.dumps(row))

    def test_duplicate_id_is_idempotent_and_statistics_are_json1_free(self):
        value = {"id": "same", "task_id": "task-1", "author_cli": "codex",
                 "reviewer_cli": "claude", "scores": {"security": 8},
                 "passed": True}
        self.assertEqual(review_storage.record_review(value), "same")
        self.assertEqual(review_storage.record_review(dict(value, passed=False)), "same")
        rows = review_storage.list_reviews("task-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["passed"], True)
        stats = review_storage.cli_statistics(author_cli="codex")
        self.assertEqual(stats[0]["reviews"], 1)
        self.assertEqual(stats[0]["pass_rate"], 1.0)
        self.assertEqual(stats[0]["scores"], {"security": 8.0})

    def test_corrupt_json_does_not_break_history_page(self):
        review_storage.record_review({"id": "corrupt", "task_id": "task-1",
                                      "author_cli": "codex", "reviewer_cli": "claude",
                                      "scores": {"security": 8}, "passed": True})
        path = Path(self.tmp.name) / "reviews.sqlite3"
        conn = sqlite3.connect(path)
        conn.execute("UPDATE review_records SET scores_json = ?, line_comments_json = ?",
                     ("{broken", "[]"))
        conn.commit()
        conn.close()
        self.assertEqual(review_storage.list_reviews("task-1")[0]["scores"], {})

    def test_database_file_is_private_on_posix(self):
        path = Path(self.tmp.name) / "reviews.sqlite3"
        self.assertTrue(path.is_file())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_sqlite_sidecars_are_private_when_wal_is_present(self):
        if os.name == "nt":
            self.skipTest("Windows ACLs are inherited from the data directory")
        review_storage.record_review({
            "id": "wal-private", "task_id": "task-1",
            "author_cli": "codex", "reviewer_cli": "claude",
            "passed": True,
        })
        for sidecar in Path(self.tmp.name).glob("reviews.sqlite3-*"):
            self.assertFalse(sidecar.is_symlink())
            self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)

    def test_first_operation_creates_schema_without_init(self):
        review_storage._DB_PATH = None
        original = review_storage.paths.DATA_DIR
        review_storage.paths.DATA_DIR = Path(self.tmp.name) / "lazy-init"
        try:
            self.assertEqual(review_storage.list_reviews("task-1"), [])
            self.assertTrue((Path(self.tmp.name) / "lazy-init" /
                             "reviews.sqlite3").is_file())
        finally:
            review_storage.paths.DATA_DIR = original

    def test_strict_passed_values_and_non_finite_integer_fields(self):
        base = {"task_id": "task-1", "author_cli": "codex",
                "reviewer_cli": "claude"}
        for index, (passed, expected) in enumerate((
                ("false", False), ("true", True), (0, False), (1, True),
                ("unexpected", False))):
            review_storage.record_review(dict(
                base, id="strict-%d" % index, passed=passed,
                duration_ms=float("inf"), line_comments=[{
                    "line": float("inf"), "message": "bounded"}],
            ))
            row = review_storage.list_reviews("task-1")[-1]
            self.assertEqual(row["passed"], expected)
            self.assertIsNone(row["duration_ms"])
            self.assertIsNone(row["line_comments"][0]["line"])

    def test_task_delete_removes_reviews_and_reports_count(self):
        review_storage.record_review({"id": "delete-me", "task_id": "task-1",
                                      "author_cli": "codex", "reviewer_cli": "claude",
                                      "passed": False})
        review_storage.record_review({"id": "keep-me", "task_id": "task-2",
                                      "author_cli": "codex", "reviewer_cli": "claude",
                                      "passed": True})
        self.assertEqual(review_storage.delete_task_reviews("task-1"), 1)
        self.assertEqual(review_storage.list_reviews("task-1"), [])
        self.assertEqual(len(review_storage.list_reviews("task-2")), 1)


if __name__ == "__main__":
    unittest.main()
