"""Saved publishing profiles are resumed automatically after service restart."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class SavedPublishSessionResumeTests(unittest.TestCase):
    def test_strict_task_read_preserves_existing_missing_and_tolerant_semantics(self):
        from core import paths, store

        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(paths, "TASKS_DIR", Path(temp_dir)), \
                mock.patch.object(store, "_TASKS", {}):
            self.assertIsNone(store.get_task("missing-task", strict=True))
            task_file = Path(temp_dir) / "broken-task.json"
            task_file.write_text("{broken", encoding="utf-8")
            self.assertIsNone(store.get_task("broken-task"))
            with self.assertRaises(ValueError):
                store.get_task("broken-task", strict=True)
            with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError):
                self.assertIsNone(store.get_task("broken-task", strict=True))

    def test_real_task_read_failure_keeps_request_until_file_recovers(self):
        import json
        import threading
        from core import paths, store
        from core.publish import auto

        for failure in ("invalid_json", "permission"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                task_file = root / "task-recovery.json"
                task_file.write_text("{broken" if failure == "invalid_json" else
                                     '{"id":"task-recovery"}', encoding="utf-8")
                batch_file = root / "batches.json"
                job = {"task_id": "task-recovery", "platform": "fanqie",
                       "params": {"auto_submit": True}, "status": "in_flight"}
                batch_file.write_text(json.dumps({"task-recovery": job}), encoding="utf-8")
                with mock.patch.object(paths, "TASKS_DIR", root), \
                        mock.patch.object(store, "_TASKS", {}), \
                        mock.patch.object(auto, "_BATCH_JOB_FILE", batch_file), \
                        mock.patch.object(auto, "_BATCH_JOBS", None), \
                        mock.patch.object(auto, "_running", {}), \
                        mock.patch.object(auto, "_BATCH_RESUME_LOCK", threading.Lock()), \
                        mock.patch.object(auto, "publish_pending_async",
                                          return_value=(True, "")) as publish:
                    if failure == "permission":
                        # Load the batch before simulating a task-only I/O failure.
                        auto._load_batch_jobs()
                        with mock.patch.object(Path, "read_text",
                                               side_effect=PermissionError("sharing violation")):
                            self.assertEqual(auto.resume_interrupted_batches(now=100), 0)
                    else:
                        self.assertEqual(auto.resume_interrupted_batches(now=100), 0)
                    publish.assert_not_called()
                    self.assertIn("task-recovery", auto._load_batch_jobs())
                    self.assertIn("task-recovery", json.loads(batch_file.read_text(encoding="utf-8")))

                    task_file.write_text('{"id":"task-recovery"}', encoding="utf-8")
                    auto._BATCH_JOBS = None
                    self.assertEqual(auto.resume_interrupted_batches(now=101), 1)
                    publish.assert_called_once_with("task-recovery", "fanqie", auto_submit=True)

    def test_interrupted_batches_for_deleted_or_archived_tasks_are_discarded(self):
        from core import store
        from core.publish import auto

        for task in (None, {"id": "task-1", "archived": True}):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as temp_dir:
                batch_file = Path(temp_dir) / "auto_publish_batches.json"
                with mock.patch.object(auto, "_BATCH_JOB_FILE", batch_file), \
                     mock.patch.object(auto, "_BATCH_JOBS", {
                         "task-1": {
                             "task_id": "task-1", "platform": "fanqie",
                             "params": {"auto_submit": True},
                             "status": "retry_wait", "next_retry_at": 0,
                         }
                     }), \
                     mock.patch.object(auto, "_BATCH_RESUME_LOCK",
                                       __import__("threading").Lock()), \
                     mock.patch.object(store, "get_task", return_value=task), \
                     mock.patch.object(auto, "publish_pending_async") as publish:
                    self.assertEqual(auto.resume_interrupted_batches(now=100), 0)
                    publish.assert_not_called()
                    self.assertEqual(auto._load_batch_jobs(), {})

    def test_task_store_read_failure_preserves_batch_for_next_tick(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            batch_file = Path(temp_dir) / "auto_publish_batches.json"
            with mock.patch.object(auto, "_BATCH_JOB_FILE", batch_file), \
                 mock.patch.object(auto, "_BATCH_JOBS", {
                     "task-1": {
                         "task_id": "task-1", "platform": "fanqie",
                         "params": {"auto_submit": True},
                         "status": "retry_wait", "next_retry_at": 0,
                     }
                 }), \
                 mock.patch.object(auto, "_BATCH_RESUME_LOCK",
                                   __import__("threading").Lock()), \
                 mock.patch("core.store.get_task", side_effect=OSError("temporary read error")), \
                 mock.patch.object(auto, "publish_pending_async") as publish:
                self.assertEqual(auto.resume_interrupted_batches(now=100), 0)
                publish.assert_not_called()
                self.assertIn("task-1", auto._load_batch_jobs())

    def test_stale_login_recovery_is_not_started_as_a_second_browser_worker(self):
        from core.publish import manager

        manager._state = {"platforms": {
            "fanqie": {"port": 9321, "status": "error",
                       "error": "等待登录超时"},
        }}
        with mock.patch.object(manager, "_load"), \
             mock.patch.object(manager.ledger, "load_books", return_value={}), \
             mock.patch.object(manager.threading, "Thread") as thread:
            manager.recover_orphans()

        thread.assert_not_called()
        with mock.patch.object(manager.ledger, "load_books", return_value={}):
            self.assertEqual(manager.saved_session_slots(), ["fanqie"])

    def test_resume_targets_saved_and_book_bound_profiles_once(self):
        from core.publish import manager

        manager._state = {"platforms": {
            "fanqie": {"port": 9321, "status": "error"},
            "qimao@writer2": {"last_login": "10-18 10:00", "status": "error"},
            "qimao@unused": {"status": "none"},
        }}
        with mock.patch.object(manager.ledger, "load_books", return_value={
                "task-1": {"fanqie": {"account": ""},
                           "qimao": {"account": "writer2"}},
                "task-2": {"qimao": {"account": "writer3"}},
        }), mock.patch.object(manager, "load_accounts", return_value=[
                {"platform": "qimao", "id": "writer2"},
        ]):
            slots = manager.saved_session_slots()

        self.assertEqual(slots, ["fanqie", "qimao@writer2", "qimao@writer3"])

    def test_resume_starts_login_checks_for_each_saved_profile(self):
        from core.publish import manager

        class ImmediateThread:
            def __init__(self, target, args=(), kwargs=None, **thread_options):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}

            def start(self):
                self.target(*self.args, **self.kwargs)

        with mock.patch.object(manager, "saved_session_slots",
                               return_value=["fanqie", "qimao@writer2"]), \
             mock.patch.object(manager, "connect") as connect, \
             mock.patch.object(manager.threading, "Thread", ImmediateThread):
            started = manager.resume_saved_sessions()

        self.assertEqual(started, 2)
        self.assertEqual(connect.call_args_list, [
            mock.call(plat="fanqie", account="", unattended=True),
            mock.call(plat="qimao", account="writer2", unattended=True),
        ])


if __name__ == "__main__":
    unittest.main()
