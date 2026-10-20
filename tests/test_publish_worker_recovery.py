"""Read failures yield the account slot while keeping unattended recovery durable."""
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class PublishWorkerRecoveryTests(unittest.TestCase):
    def bounded_wait(self):
        waits = []

        def wait(task_id, seconds):
            if len(waits) >= 2:
                self.fail("worker still owns the account after three failed reads")
            waits.append(seconds)
            return True

        return waits, wait

    def test_unavailable_reconciliation_returns_for_durable_retry(self):
        from core.publish import auto

        manager = mock.Mock()
        manager.sync_published.return_value = False, "浏览器未连接"
        manager.connect.return_value = False, "登录态已失效"
        waits, wait = self.bounded_wait()
        with mock.patch.object(auto, "_batch_sleep", side_effect=wait):
            ok, error = auto._sync_with_session_recovery(
                manager, "task-1", "fanqie", "writer")

        self.assertFalse(ok)
        self.assertIn("浏览器未连接", error)
        self.assertIn("重试次数已达上限", error)
        self.assertTrue(auto._scheduled_error_is_persistent(error))
        self.assertEqual(manager.sync_published.call_count, 3)
        self.assertEqual(waits, [60, 120])

    def test_unreadable_chapter_list_returns_for_durable_retry(self):
        from core.publish import auto

        waits, wait = self.bounded_wait()
        with mock.patch.object(auto, "pending", side_effect=PermissionError("sharing violation")) as read, \
                mock.patch.object(auto, "_batch_sleep", side_effect=wait):
            chapters, error = auto._pending_with_recovery("task-1", "qimao")

        self.assertEqual(chapters, [])
        self.assertIn("sharing violation", error)
        self.assertIn("重试次数已达上限", error)
        self.assertTrue(auto._scheduled_error_is_persistent(error))
        self.assertEqual(read.call_count, 3)
        self.assertEqual(waits, [60, 120])

    def test_failed_batch_releases_slot_and_restart_reconciles_before_upload(self):
        from core import store
        from core.publish import auto, ledger, manager

        for failure in ("reconcile", "chapter_list"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                task = {"id": "task-1", "workdir": temp}
                item = {"chapter_no": 1, "file": "第1章.txt"}
                remote_done = False
                read_count = 0

                def pending(*args):
                    nonlocal read_count
                    read_count += 1
                    if failure == "chapter_list" and 2 <= read_count <= 4:
                        return [], "待发章节清单读取异常：sharing violation"
                    return ([] if remote_done else [item]), ""

                def sync(*args, **kwargs):
                    if failure == "reconcile" and not remote_done:
                        return False, "浏览器未连接"
                    return True, ""

                patches = [
                    mock.patch.object(auto, "_BATCH_JOB_FILE", Path(temp) / "batches.json"),
                    mock.patch.object(auto, "_BATCH_JOBS", None),
                    mock.patch.object(auto, "_BATCH_RESUME_LOCK", threading.Lock()),
                    mock.patch.object(auto, "_running", {}),
                    mock.patch.object(auto, "_cancel_events", {}),
                    mock.patch.object(auto, "_BATCH_SLOT_LOCKS", {}),
                    mock.patch.object(auto, "calibrated", return_value=True),
                    mock.patch.object(auto, "guards", return_value=(True, "")),
                    mock.patch.object(auto, "pending", side_effect=pending),
                    mock.patch.object(auto, "_batch_sleep", return_value=True),
                    mock.patch.object(auto.threading, "Thread", ImmediateThread),
                    mock.patch.object(store, "get_task", return_value=task),
                    mock.patch.object(ledger, "book_for", return_value={"book_id": "123", "title": "测试"}),
                    mock.patch.object(manager, "_quality_release_guard", return_value={"allowed": True}),
                    mock.patch.object(manager, "sync_published", side_effect=sync),
                    mock.patch.object(manager, "connect", return_value=(False, "登录态失效")),
                ]
                for patch in patches:
                    stack.enter_context(patch)
                upload = stack.enter_context(mock.patch.object(manager, "upload_chapter_async"))

                self.assertEqual(auto.publish_pending_async("task-1", "fanqie"), (True, ""))
                self.assertEqual(auto._running["task-1"]["status"], "error")
                self.assertFalse(auto._batch_slot_lock("fanqie").locked())
                self.assertNotIn("task-1", auto._cancel_events)
                job = auto._load_batch_jobs()["task-1"]
                self.assertEqual(job["status"], "retry_wait")

                # Drop all process-local state; the platform has accepted the
                # chapter meanwhile. Recovery must check again and finish
                # without issuing another upload.
                auto._BATCH_JOBS = None
                auto._running.clear()
                remote_done = True
                # Preflight must enumerate the local chapter before the live
                # reconciliation removes it from the worker's pending list.
                read_count = 0

                def recovered_pending(*args):
                    nonlocal read_count
                    read_count += 1
                    return ([item] if read_count == 1 else []), ""

                with mock.patch.object(auto, "pending", side_effect=recovered_pending):
                    self.assertEqual(auto.resume_interrupted_batches(now=job["next_retry_at"]), 1)
                self.assertEqual(auto._running["task-1"]["status"], "done")
                self.assertEqual(auto._load_batch_jobs(), {})
                self.assertFalse(auto._batch_slot_lock("fanqie").locked())
                upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
