import unittest
from unittest import mock


from app.core import pipeline, runner


class RuntimeRegressionTests(unittest.TestCase):
    def test_transient_error_survives_missing_hot_reload_global(self):
        markers = runner.__dict__.pop("_TRANSIENT", None)
        try:
            self.assertTrue(runner._transient_error("stream disconnected"))
            self.assertFalse(runner._transient_error("permanent syntax error"))
        finally:
            if markers is not None:
                runner._TRANSIENT = markers

    def test_execute_run_clears_stale_workdir_warning_when_reclaimed(self):
        run = {
            "id": "run-1",
            "status": "queued",
            "task_id": "task-1",
            "warnings": ["工作目录被运行中任务「旧任务」占用，排队等待"],
        }
        updates = []

        def update_run(run_id, **fields):
            updates.append((run_id, fields))
            return run

        with mock.patch.object(pipeline.store, "get_run", return_value=run), \
                mock.patch.object(pipeline.store, "get_task", return_value=None), \
                mock.patch.object(pipeline.store, "update_run", side_effect=update_run), \
                mock.patch.object(pipeline.jobs, "cancel_event_for",
                                  return_value=mock.Mock()):
            pipeline.execute_run("run-1")

        self.assertEqual(updates[0][0], "run-1")
        self.assertEqual(updates[0][1]["status"], "running")
        self.assertEqual(updates[0][1]["resume_enqueue_at"], "")
        self.assertEqual(updates[0][1]["warnings"], [])

    def test_workdir_blocker_ignores_finished_task_even_if_workdir_matches(self):
        task = {"id": "task-1", "workdir": r"E:\repo\project"}
        finished = {"id": "task-2", "status": "failed",
                    "workdir": r"e:/repo/project/"}
        with mock.patch.object(pipeline.store, "list_tasks", return_value=[finished]), \
                mock.patch.object(pipeline.jobs, "_task_active_run") as active:
            self.assertIsNone(pipeline._workdir_blocker(task, "run-1"))
        active.assert_not_called()


if __name__ == "__main__":
    unittest.main()
