"""Daily serial publishing backs off failed starts and resumes durably."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class SerialDailyPublishRetryTests(unittest.TestCase):
    def test_serial_daily_publish_can_prepare_the_platform_book(self):
        from core import serial_daily
        from core.publish import auto

        job = {"run_task_id": "daily-task", "task_id": "root-task",
               "platform": "qimao"}
        with mock.patch.object(auto, "publish_pending_async",
                               return_value=(True, "")) as publish:
            self.assertEqual(serial_daily._publish(job, [12]), (True, ""))

        publish.assert_called_once_with(
            "daily-task", "qimao", auto_submit=True, auto_prepare_book=True,
            only=[12], scheduled=False)

    def test_unpublished_chapters_roll_into_next_day_backlog(self):
        from core import serial_daily

        now = datetime(2026, 10, 20, 8, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "waiting_publish", "status": "active",
            "run_task_id": "task-1", "new_chapters": [7, 8],
            "publish_backlog": [6], "last_day_status": "alerted",
            "alerted_day": "2026-10-19",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value={8}), \
                mock.patch.object(serial_daily, "_start_generation",
                                  return_value=("run-2", [], "task-1")):
            serial_daily._process_job(job, "2026-10-20", now)
            saved = serial_daily.get("daily-1")

        self.assertEqual(saved["publish_backlog"], [6, 7])
        self.assertEqual(saved["new_chapters"], [])
        self.assertEqual(saved["day"], "2026-10-20")

    def test_partial_generation_is_backlogged_and_old_run_cancelled_at_day_boundary(self):
        from core import serial_daily, store
        from core import jobs as job_runner

        now = datetime(2026, 10, 20, 8, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 2,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "generating", "status": "active",
            "run_id": "run-old", "run_task_id": "task-1",
            "baseline_chapters": [1, 2], "new_chapters": [3],
            "publish_backlog": [], "last_day_status": "running",
            "alerted_day": "2026-10-19",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value={2}), \
                mock.patch.object(serial_daily, "_chapter_numbers", return_value={1, 2, 3, 4}), \
                mock.patch.object(serial_daily, "_start_generation",
                                  return_value=("run-new", [1, 2, 3, 4], "task-1")), \
                mock.patch.object(store, "get_run", return_value={"status": "running"}), \
                mock.patch.object(job_runner, "cancel") as cancel:
            serial_daily._process_job(job, "2026-10-20", now)
            saved = serial_daily.get("daily-1")

        self.assertEqual(saved["publish_backlog"], [3, 4])
        self.assertEqual(saved["run_id"], "run-new")
        cancel.assert_called_once_with("run-old")

    def test_backlog_retries_even_when_today_has_no_new_chapters(self):
        from core import serial_daily
        from core.publish import auto

        now = datetime(2026, 10, 20, 12, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-20",
            "phase": "waiting_publish", "status": "active",
            "run_task_id": "task-1", "new_chapters": [],
            "publish_backlog": [7], "publish_retry": {"attempt": 0, "status": "ready"},
            "last_day_status": "running", "alerted_day": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value=set()), \
                mock.patch.object(serial_daily, "_publish", return_value=(True, "")) as publish, \
                mock.patch.object(serial_daily, "_alert_due", return_value=False), \
                mock.patch.object(auto, "status", return_value={"running": {}}):
            serial_daily._process_job(job, "2026-10-20", now)

        publish.assert_called_once_with(mock.ANY, [7])

    def test_published_backlog_does_not_mark_short_generation_as_complete(self):
        from core import serial_daily

        job = {"id": "daily-1", "chapters_per_day": 2,
               "new_chapters": [9], "publish_backlog": [7]}
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True):
            serial_daily._mark_published("daily-1", serial_daily.get("daily-1"))
            saved = serial_daily.get("daily-1")

        self.assertEqual(saved["phase"], "done")
        self.assertEqual(saved["last_day_status"], "incomplete")
        self.assertEqual(saved["last_error"], "当日章节不足（1/2）")
        self.assertEqual(saved["publish_backlog"], [])

    def test_accepted_chapters_include_published_scheduled_and_review(self):
        from core import serial_daily
        from core.publish import ledger

        with mock.patch.object(ledger, "published_chapters", return_value={1}), \
                mock.patch.object(ledger, "scheduled_chapters", return_value={2}), \
                mock.patch.object(ledger, "review_chapters", return_value={3}):
            self.assertEqual(serial_daily._accepted_chapters("task-1", "fanqie"),
                             {1, 2, 3})

    def test_current_platform_snapshot_replaces_stale_schedule_receipts(self):
        from core import serial_daily
        from core.publish import ledger

        snapshot = {
            "remote_synced_at": "2026-10-19 11:00:00",
            "remote_published_nos": [1],
            "remote_scheduled_nos": [],
            "remote_review_nos": [3],
        }
        receipts = [
            {"action": "upload_chapter_scheduled", "ok": True,
             "task_id": "task-1", "platform": "fanqie", "chapter_no": 2,
             "ts": "2026-10-19 10:00:00"},
            {"action": "upload_chapter_scheduled", "ok": True,
             "task_id": "task-1", "platform": "fanqie", "chapter_no": 4,
             "ts": "2026-10-19 11:30:00"},
        ]
        with mock.patch.object(ledger, "book_for", return_value=snapshot), \
                mock.patch.object(ledger, "published_chapters", return_value={1}), \
                mock.patch.object(ledger, "scheduled_chapters", return_value={2, 4}), \
                mock.patch.object(ledger, "review_chapters", return_value={3}), \
                mock.patch.object(ledger, "_iter_records", return_value=iter(receipts)), \
                mock.patch("core.store.serial_chain_ids", return_value=["task-1"]):
            self.assertEqual(serial_daily._accepted_chapters("task-1", "fanqie"),
                             {1, 3, 4})

    def test_start_failure_is_persisted_and_not_retried_before_backoff(self):
        from core import serial_daily

        now = datetime(2026, 10, 19, 12, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "waiting_publish", "status": "active",
            "run_task_id": "task-1", "new_chapters": [1],
            "publish_retry": {"attempt": 0, "status": "ready"},
            "last_day_status": "running", "alerted_day": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value=set()), \
                mock.patch.object(serial_daily, "_publish", return_value=(False, "browser busy")) as publish, \
                mock.patch.object(serial_daily, "_alert_due", return_value=False), \
                mock.patch.object(serial_daily, "_deadline_passed", return_value=False):
            serial_daily._process_job(job, "2026-10-19", now)
            # Simulate a process restart: the retry deadline must be read
            # from the durable JSON record, not just the in-memory dict.
            serial_daily._JOBS.clear()
            serial_daily._LOADED = False
            saved = serial_daily.get("daily-1")
            serial_daily._process_job(saved, "2026-10-19", now + timedelta(seconds=20))

        publish.assert_called_once()
        self.assertEqual(saved["publish_retry"]["status"], "retry_wait")
        self.assertEqual(saved["publish_retry"]["attempt"], 1)
        self.assertEqual(saved["last_error"], "browser busy")

    def test_publisher_start_exception_is_persisted_and_backed_off(self):
        from core import serial_daily

        now = datetime(2026, 10, 19, 12, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "waiting_publish", "status": "active",
            "run_task_id": "task-1", "new_chapters": [1],
            "publish_retry": {"attempt": 0, "status": "ready"},
            "last_day_status": "running", "alerted_day": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value=set()), \
                mock.patch.object(serial_daily, "_publish",
                                  side_effect=RuntimeError("browser busy")) as publish, \
                mock.patch.object(serial_daily, "_alert_due", return_value=False), \
                mock.patch.object(serial_daily, "_deadline_passed", return_value=False):
            serial_daily._process_job(job, "2026-10-19", now)
            serial_daily._JOBS.clear()
            serial_daily._LOADED = False
            saved = serial_daily.get("daily-1")
            serial_daily._process_job(saved, "2026-10-19", now + timedelta(seconds=20))

        publish.assert_called_once()
        self.assertEqual(saved["publish_retry"]["status"], "retry_wait")
        self.assertEqual(saved["publish_retry"]["attempt"], 1)
        self.assertEqual(saved["publish_retry"]["next_retry_at"],
                         now.timestamp() + serial_daily.PUBLISH_RETRY_BASE_S)
        self.assertIn("browser busy", saved["last_error"])

    def test_retry_due_time_and_attempt_limit_are_durable(self):
        from core import serial_daily

        now = datetime(2026, 10, 19, 12, 0)
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": {
                    "id": "daily-1", "publish_retry": {"attempt": 2, "status": "in_flight"}
                }}), \
                mock.patch.object(serial_daily, "_LOADED", True):
            serial_daily._set_publish_retry("daily-1", serial_daily.get("daily-1"),
                                            "retry_wait", now, "failed")
            state = serial_daily.get("daily-1")["publish_retry"]

        self.assertEqual(state["status"], "retry_wait")
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(state["next_retry_at"], now.timestamp() + 120)
        exhausted = {"publish_retry": {
            "attempt": serial_daily.PUBLISH_RETRY_MAX_ATTEMPTS,
            "status": "exhausted"}}
        self.assertFalse(serial_daily._publish_retry_ready(exhausted, now))

    def test_transient_disconnect_keeps_retrying_after_normal_attempt_budget(self):
        from core import serial_daily
        from core.publish import auto

        now = datetime(2026, 10, 19, 12, 0)
        job = {"id": "daily-1", "publish_retry": {
            "attempt": serial_daily.PUBLISH_RETRY_MAX_ATTEMPTS,
            "status": "in_flight"}}
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(auto, "retryable_publish_start", return_value=True):
            serial_daily._set_publish_retry(
                "daily-1", serial_daily.get("daily-1"), "retry_wait", now,
                "连接被关闭")
            saved = serial_daily.get("daily-1")

        self.assertEqual(saved["publish_retry"]["status"], "retry_wait")
        self.assertEqual(saved["publish_retry"]["attempt"],
                         serial_daily.PUBLISH_RETRY_MAX_ATTEMPTS)
        self.assertFalse(serial_daily._publish_retry_ready(saved, now))
        retry_at = datetime.fromtimestamp(saved["publish_retry"]["next_retry_at"])
        self.assertTrue(serial_daily._publish_retry_ready(saved, retry_at))

    def test_login_and_platform_challenges_remain_retryable_without_bypass(self):
        from core import serial_daily
        from core.publish import auto

        for error in (
                "平台未登录（当前页面 /login），请先扫码",
                "平台要求安全验证：验证码",
                "上一批发布正在回收，请稍后再点发布",
                "该任务已有自动发布进行中，请等本轮结束",
                "同一平台账号已有另一批自动发布进行中（任务 task-2）",
                "该平台账号浏览器正在执行另一项发布操作，请稍后重试",
        ):
            with self.subTest(error=error):
                self.assertTrue(serial_daily._publish_error_is_persistent(auto, error))
        self.assertFalse(serial_daily._publish_error_is_persistent(
            auto, "章节标题不符合平台规则"))

        now = datetime(2026, 10, 19, 12, 0)
        job = {"id": "daily-1", "publish_retry": {
            "attempt": serial_daily.PUBLISH_RETRY_MAX_ATTEMPTS,
            "status": "in_flight"}}
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True):
            serial_daily._set_publish_retry(
                "daily-1", serial_daily.get("daily-1"), "retry_wait", now,
                "平台要求安全验证：验证码")
            saved = serial_daily.get("daily-1")
        self.assertEqual(saved["publish_retry"]["status"], "retry_wait")

    def test_uncertain_receipts_and_timeouts_keep_retrying_after_budget(self):
        from core import serial_daily
        from core.publish import auto

        errors = (
            "第 12 章提交结果不确定，暂无法完成平台对账",
            "第 12 章发布等待超时",
            "自动创建fanqie作品超时",
            "无法连接浏览器，CDP connection refused",
        )
        now = datetime(2026, 10, 19, 12, 0)
        for index, error in enumerate(errors):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp_dir:
                job = {"id": "daily-%d" % index, "publish_retry": {
                    "attempt": serial_daily.PUBLISH_RETRY_MAX_ATTEMPTS,
                    "status": "in_flight"}}
                with mock.patch.object(serial_daily, "_FILE",
                                       Path(temp_dir) / "jobs.json"), \
                        mock.patch.object(serial_daily, "_JOBS", {job["id"]: job}), \
                        mock.patch.object(serial_daily, "_LOADED", True), \
                        mock.patch.object(auto, "retryable_publish_start",
                                          return_value=False):
                    serial_daily._set_publish_retry(
                        job["id"], serial_daily.get(job["id"]), "retry_wait",
                        now, error)
                    saved = serial_daily.get(job["id"])

                self.assertEqual(saved["publish_retry"]["status"], "retry_wait")
                self.assertEqual(saved["publish_retry"]["attempt"],
                                 serial_daily.PUBLISH_RETRY_MAX_ATTEMPTS)
                self.assertGreater(saved["publish_retry"]["next_retry_at"],
                                   now.timestamp())

    def test_terminal_batch_failure_returns_to_backoff_and_reconciles(self):
        from core import serial_daily
        from core.publish import auto

        now = datetime(2026, 10, 19, 12, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "publishing", "status": "active",
            "run_task_id": "task-1", "new_chapters": [1],
            "publish_retry": {"attempt": 1, "status": "in_flight"},
            "last_day_status": "running", "alerted_day": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value=set()), \
                mock.patch.object(serial_daily, "_alert_due", return_value=False), \
                mock.patch.object(serial_daily, "_deadline_passed", return_value=False), \
                mock.patch.object(auto, "status", return_value={"running": {
                    "status": "error", "error": "upload failed"}}):
            serial_daily._process_job(job, "2026-10-19", now)
            saved = serial_daily.get("daily-1")
            serial_daily._process_job(saved, "2026-10-19", now + timedelta(seconds=30))

        self.assertEqual(saved["phase"], "waiting_publish")
        self.assertEqual(saved["publish_retry"]["status"], "retry_wait")
        self.assertEqual(saved["publish_retry"]["attempt"], 1)

    def test_accepted_chapter_wins_after_restart_and_marks_success(self):
        from core import serial_daily

        now = datetime(2026, 10, 19, 12, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "publishing", "status": "active",
            "run_task_id": "task-1", "new_chapters": [1],
            "publish_retry": {"attempt": 1, "status": "in_flight"},
            "last_day_status": "running", "alerted_day": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value={1}), \
                mock.patch.object(serial_daily, "_alert_due", return_value=False):
            serial_daily._process_job(job, "2026-10-19", now)
            saved = serial_daily.get("daily-1")

        self.assertEqual(saved["phase"], "done")
        self.assertEqual(saved["publish_retry"]["status"], "succeeded")

    def test_platform_review_queue_receipt_wins_after_restart(self):
        from core import serial_daily

        now = datetime(2026, 10, 19, 12, 0)
        job = {
            "id": "daily-1", "task_id": "task-1", "task_title": "Book",
            "platform": "fanqie", "chapters_per_day": 1,
            "publish_time": "08:00", "day": "2026-10-19",
            "phase": "publishing", "status": "active",
            "run_task_id": "task-1", "new_chapters": [1],
            "publish_retry": {"attempt": 1, "status": "in_flight"},
            "last_day_status": "running", "alerted_day": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(serial_daily, "_FILE", Path(temp_dir) / "jobs.json"), \
                mock.patch.object(serial_daily, "_JOBS", {"daily-1": job}), \
                mock.patch.object(serial_daily, "_LOADED", True), \
                mock.patch.object(serial_daily, "_accepted_chapters", return_value={1}), \
                mock.patch.object(serial_daily, "_alert_due", return_value=False):
            serial_daily._process_job(job, "2026-10-19", now)
            saved = serial_daily.get("daily-1")

        self.assertEqual(saved["phase"], "done")
        self.assertEqual(saved["publish_retry"]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
