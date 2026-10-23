"""Terminal pipeline failures must leave an actionable report and state."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import pipeline, store


class PipelineFailureReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = {"id": "r-failure-report", "task_id": "t-failure-report",
                    "title": "terminal report", "status": "running"}
        self.task = {"id": "t-failure-report", "title": "terminal report",
                     "goal": "make progress", "workdir": self.tmp.name}
        self.updates = []
        self.reports = []

        def update(_run_id, expected_status=None, **fields):
            self.updates.append((expected_status, dict(fields)))
            self.run.update(fields)
            return self.run

        self.patchers = [
            mock.patch.object(store, "get_run", side_effect=lambda _rid: self.run),
            mock.patch.object(store, "get_task", return_value=self.task),
            mock.patch.object(store, "update_run", side_effect=update),
            mock.patch.object(store, "run_dir", return_value=Path(self.tmp.name)),
            mock.patch.object(store, "write_report",
                              side_effect=lambda _rid, text: self.reports.append(text)),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_early_task_timeout_sets_terminal_state_and_writes_report(self):
        with mock.patch.object(pipeline, "_execute_run_impl_inner",
                               side_effect=pipeline.TaskTimeout("expired")):
            pipeline._execute_run_impl(self.run["id"])

        self.assertEqual(self.run["status"], "timeout")
        self.assertTrue(self.run.get("ended_at"))
        self.assertEqual(len(self.reports), 1)
        self.assertIn("流程终止报告", self.reports[0])
        self.assertIn("任务总时限已到", self.reports[0])
        self.assertIn("未生成正式评分", self.reports[0])

    def test_unhandled_setup_exception_is_terminal_and_reported(self):
        with mock.patch.object(pipeline, "_execute_run_impl_inner",
                               side_effect=RuntimeError("setup exploded")):
            pipeline._execute_run_impl(self.run["id"])

        self.assertEqual(self.run["status"], "failed")
        self.assertIn("setup exploded", self.run.get("error") or "")
        self.assertEqual(len(self.reports), 1)
        self.assertIn("setup exploded", self.reports[0])


if __name__ == "__main__":
    unittest.main()
