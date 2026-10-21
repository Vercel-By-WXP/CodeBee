import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import pipeline, review_storage, store


class ReviewPipelinePersistenceTests(unittest.TestCase):
    def test_pipeline_persists_structured_review_without_raw_content(self):
        previous_db = review_storage._DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            review_storage.init(tmp)
            try:
                pipeline._persist_review_record(
                    "run-1",
                    {"id": "task-1"},
                    {"id": "codex", "model_family": "openai"},
                    {"id": "claude", "model_family": "anthropic"},
                    {"pass": False, "scores": {"security": 6},
                     "issues": [{"path": "src/app.py", "line": 12,
                                 "severity": "high", "title": "Unsafe input",
                                 "detail": "Validate before use"}],
                     "raw_output": "must not be persisted"},
                )

                [record] = review_storage.list_reviews("task-1")
                self.assertEqual(record["run_id"], "run-1")
                self.assertEqual(record["author_cli"], "codex")
                self.assertEqual(record["author_model_family"], "openai")
                self.assertEqual(record["reviewer_cli"], "claude")
                self.assertEqual(record["reviewer_model_family"], "anthropic")
                self.assertEqual(record["scores"], {"security": 6.0})
                self.assertEqual(record["line_comments"], [{
                    "path": "src/app.py", "line": 12, "severity": "major",
                    "message": "Unsafe input: Validate before use",
                }])
                self.assertFalse(record["passed"])
            finally:
                review_storage._DB_PATH = previous_db

    def test_storage_failure_does_not_escape_into_pipeline(self):
        with mock.patch.object(review_storage, "record_review",
                               side_effect=OSError("read-only")):
            pipeline._persist_review_record(
                "run-1", {"id": "task-1"}, {"id": "codex"},
                {"id": "claude"}, {"pass": True},
            )

    def test_task_delete_removes_review_history(self):
        self._run_delete_case(storage_failure=False)

    def test_task_delete_survives_review_history_cleanup_failure(self):
        self._run_delete_case(storage_failure=True)

    def _run_delete_case(self, *, storage_failure):
        previous_db = review_storage._DB_PATH
        previous_tasks, previous_runs = store._TASKS, store._RUNS
        previous_tasks_dir, previous_runs_dir = store.paths.TASKS_DIR, store.paths.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir, runs_dir = root / "tasks", root / "runs"
            tasks_dir.mkdir()
            runs_dir.mkdir()
            store.paths.TASKS_DIR, store.paths.RUNS_DIR = tasks_dir, runs_dir
            store._TASKS = {"task-1": {"id": "task-1", "title": "Demo",
                                       "status": "done", "type": "code"}}
            store._RUNS = {}
            (tasks_dir / "task-1.json").write_text("{}", encoding="utf-8")
            review_storage.init(root / "data")
            review_storage.record_review({
                "task_id": "task-1", "author_cli": "codex",
                "reviewer_cli": "claude", "passed": True,
            })
            cleanup = (mock.patch.object(
                review_storage, "delete_task_reviews",
                side_effect=OSError("read-only")) if storage_failure
                else mock.patch.object(
                    review_storage, "delete_task_reviews",
                    wraps=review_storage.delete_task_reviews))
            try:
                with cleanup as delete_reviews, \
                     mock.patch.object(store, "_schedule_run_dir_cleanup"), \
                     mock.patch.object(store.audit, "record"), \
                     mock.patch.object(store, "bump_state"):
                    ok, error = store.delete_task("task-1")

                self.assertTrue(ok, error)
                self.assertNotIn("task-1", store._TASKS)
                self.assertFalse((tasks_dir / "task-1.json").exists())
                delete_reviews.assert_called_once_with("task-1")
                if not storage_failure:
                    self.assertEqual(review_storage.list_reviews("task-1"), [])
            finally:
                review_storage._DB_PATH = previous_db
                store._TASKS, store._RUNS = previous_tasks, previous_runs
                store.paths.TASKS_DIR, store.paths.RUNS_DIR = (
                    previous_tasks_dir, previous_runs_dir)


if __name__ == "__main__":
    unittest.main()
