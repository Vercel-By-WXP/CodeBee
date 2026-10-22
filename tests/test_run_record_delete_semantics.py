import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import store


class RunRecordDeleteSemanticsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tasks_dir = self.root / "tasks"
        self.runs_dir = self.root / "runs"
        self.workdir = self.root / "work"
        for d in (self.tasks_dir, self.runs_dir, self.workdir):
            d.mkdir(parents=True)
        self._prev = (store._TASKS, store._RUNS, store.paths.TASKS_DIR,
                      store.paths.RUNS_DIR)
        store._TASKS = {}
        store._RUNS = {}
        store.paths.TASKS_DIR = self.tasks_dir
        store.paths.RUNS_DIR = self.runs_dir
        self._bump = mock.patch.object(store, "bump_state")
        self._bump.start()

    def tearDown(self):
        self._bump.stop()
        store._TASKS, store._RUNS, store.paths.TASKS_DIR, store.paths.RUNS_DIR = self._prev
        self._tmp.cleanup()

    def _add_task(self, task_id="task-1"):
        store._TASKS[task_id] = {
            "id": task_id, "title": "Demo", "status": "done",
            "type": "code", "workdir": str(self.workdir),
        }
        (self.tasks_dir / (task_id + ".json")).write_text("{}", encoding="utf-8")

    def _add_run(self, run_id, *, task_id="task-1", status="done"):
        run = {
            "id": run_id, "task_id": task_id, "title": "Run " + run_id,
            "status": status, "created_at": "2026-10-22 10:00:00",
        }
        store._RUNS[run_id] = run
        run_dir = self.runs_dir / run_id
        (run_dir / "steps").mkdir(parents=True)
        (run_dir / "steps" / "01.log").write_text("log", encoding="utf-8")
        (run_dir / "report.md").write_text("report", encoding="utf-8")
        store.atomic.write_json(run_dir / "run.json", run)
        return run

    def _run_dir(self, run_id):
        return self.runs_dir / run_id

    def _assert_record_files_intact(self, run_id):
        self.assertTrue((self._run_dir(run_id) / "run.json").is_file())
        self.assertTrue((self._run_dir(run_id) / "report.md").is_file())
        self.assertTrue((self._run_dir(run_id) / "steps" / "01.log").is_file())

    def test_delete_run_hides_record_but_keeps_task_and_files(self):
        self._add_task()
        self._add_run("run-1")
        artifact = self.workdir / "result.txt"
        artifact.write_text("kept", encoding="utf-8")

        ok, error = store.delete_run("run-1")

        self.assertTrue(ok, error)
        self.assertIn("run-1", store._RUNS)
        self.assertTrue(store._RUNS["run-1"]["record_deleted"])
        self.assertEqual(store.list_runs(), [])
        self.assertEqual([r["id"] for r in store.task_runs("task-1")], ["run-1"])
        self.assertIn("task-1", store.latest_run_by_task())
        self.assertEqual(store.latest_run_by_task()["task-1"]["id"], "run-1")
        self.assertIn("task-1", store._TASKS)
        self._assert_record_files_intact("run-1")
        self.assertTrue(artifact.is_file())

        persisted = json.loads(
            (self._run_dir("run-1") / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(persisted["record_deleted"])

    def test_internal_history_can_still_read_hidden_records(self):
        self._add_run("run-1")
        store.delete_run("run-1")

        self.assertEqual(store.list_runs(), [])
        self.assertEqual([r["id"] for r in store.list_runs(include_hidden=True)],
                         ["run-1"])

    def test_delete_run_twice_is_idempotent(self):
        self._add_run("run-1")

        self.assertEqual(store.delete_run("run-1"), (True, ""))
        self.assertEqual(store.delete_run("run-1"), (True, ""))
        self.assertEqual(store.list_runs(), [])

    def test_delete_run_refuses_active_records(self):
        self._add_run("run-run", status="running")
        self._add_run("run-queue", status="queued")

        for run_id in ("run-run", "run-queue"):
            ok, error = store.delete_run(run_id)
            self.assertFalse(ok)
            self.assertIn("运行中", error)
            self.assertFalse(store._RUNS[run_id].get("record_deleted"))
        self.assertEqual({r["id"] for r in store.list_runs()}, {"run-run", "run-queue"})

    def test_delete_run_write_failure_keeps_record_visible(self):
        self._add_run("run-1")
        with mock.patch.object(store.atomic, "write_json", side_effect=OSError("disk")):
            ok, error = store.delete_run("run-1")

        self.assertFalse(ok)
        self.assertIn("无法写入磁盘", error)
        self.assertFalse(store._RUNS["run-1"].get("record_deleted"))
        self.assertEqual([r["id"] for r in store.list_runs()], ["run-1"])

    def test_delete_runs_skips_invalid_active_and_hidden(self):
        self._add_run("run-keep")
        self._add_run("run-hidden")
        store.delete_run("run-hidden")
        self._add_run("run-run", status="running")
        self._add_run("run-queue", status="queued")

        deleted, skipped, error = store.delete_runs(
            ["run-keep", "run-hidden", "run-run", "run-queue", "missing", "bad/id"])

        self.assertEqual(deleted, 1)
        self.assertEqual(skipped, 4)  # 已隐藏 / 运行中 / 排队中 / 不存在
        self.assertIn("非法", error)
        self.assertTrue(store._RUNS["run-keep"]["record_deleted"])
        self.assertIn("run-hidden", store._RUNS)
        self.assertEqual({r["id"] for r in store.list_runs()},
                         {"run-run", "run-queue"})
        self._assert_record_files_intact("run-keep")

    def test_clear_runs_hides_only_finished_records(self):
        for run_id, status in (("run-a", "done"), ("run-b", "failed"),
                               ("run-c", "cancelled"), ("run-running", "running"),
                               ("run-queued", "queued")):
            self._add_run(run_id, status=status)

        deleted, skipped = store.clear_runs()

        self.assertEqual((deleted, skipped), (3, 2))
        self.assertEqual({r["id"] for r in store.list_runs()},
                         {"run-running", "run-queued"})
        self.assertEqual(len(store._RUNS), 5)
        for run_id in ("run-a", "run-b", "run-c"):
            self._assert_record_files_intact(run_id)

    def test_delete_task_removes_hidden_runs_and_run_dirs_only(self):
        self._add_task()
        self._add_run("run-visible")
        self._add_run("run-hidden")
        store.delete_run("run-hidden")
        artifact = self.workdir / "result.txt"
        artifact.write_text("kept", encoding="utf-8")
        cleanup = []

        with mock.patch.object(store, "_schedule_run_dir_cleanup",
                               side_effect=cleanup.append), \
             mock.patch.object(store.audit, "record"), \
             mock.patch("app.core.review_storage.delete_task_reviews"):
            ok, error = store.delete_task("task-1")

        self.assertTrue(ok, error)
        self.assertNotIn("task-1", store._TASKS)
        self.assertNotIn("run-visible", store._RUNS)
        self.assertNotIn("run-hidden", store._RUNS)
        self.assertEqual(sorted(cleanup), ["run-hidden", "run-visible"])
        self.assertFalse((self.tasks_dir / "task-1.json").exists())
        self.assertTrue(artifact.is_file())


if __name__ == "__main__":
    unittest.main()
