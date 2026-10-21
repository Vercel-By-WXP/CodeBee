"""Verdict-blocked publish batches sleep for the day instead of waking the browser.

质量门禁族错误重试不可能改变评审结论：按天给小额重试预算，花完转
blocked_verdict 安睡（不在恢复集合里），第二天重新拿到预算。恢复钩子
同时尊重任务上 enabled=false 的定时开关——关掉即停，不再动浏览器。
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

NOW = 1000
DAY = time.strftime("%Y-%m-%d", time.localtime(NOW))
OTHER_DAY = "1999-01-01"
GATE_ERROR = "质量门禁拦截：最近一次质量评审未达标"
BUSY_ERROR = "该平台账号浏览器正在执行另一项发布操作"


class RetainVerdictBudgetTests(unittest.TestCase):
    def _retain(self, error, attempt=0, day=None, status="in_flight"):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                record = {"task_id": "task-1", "platform": "fanqie",
                          "status": status, "attempt": attempt,
                          "params": {"auto_submit": True}}
                if day:
                    record["day"] = day
                auto._load_batch_jobs()["task-1"] = record
                auto._save_batch_jobs()
                ok = auto._retain_automatic_batch_for_retry(
                    "task-1", error, now=NOW)
                saved = auto._load_batch_jobs()["task-1"]
        return ok, saved

    def test_gate_error_blocks_after_the_daily_budget(self):
        ok, record = self._retain(GATE_ERROR, attempt=3, day=DAY)
        self.assertTrue(ok)
        self.assertEqual(record["status"], "blocked_verdict")
        self.assertEqual(record["attempt"], 4)
        self.assertNotIn("next_retry_at", record)

    def test_gate_error_keeps_backoff_within_the_daily_budget(self):
        ok, record = self._retain(GATE_ERROR, attempt=2, day=DAY)
        self.assertTrue(ok)
        self.assertEqual(record["status"], "retry_wait")
        self.assertGreater(record["next_retry_at"], NOW)

    def test_stale_day_record_gets_a_fresh_budget(self):
        ok, record = self._retain(GATE_ERROR, attempt=9, day=OTHER_DAY)
        self.assertTrue(ok)
        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["attempt"], 1)

    def test_busy_error_is_not_capped_by_the_verdict_budget(self):
        ok, record = self._retain(BUSY_ERROR, attempt=9, day=DAY)
        self.assertTrue(ok)
        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["attempt"], 10)

    def test_fresh_acceptance_rearms_a_blocked_verdict_budget(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                auto._load_batch_jobs()["task-1"] = {
                    "task_id": "task-1", "platform": "fanqie",
                    "status": "blocked_verdict", "attempt": 4,
                    "params": {}, "day": DAY}
                auto._save_batch_jobs()
                auto._remember_automatic_batch(
                    "task-1", "fanqie", True, False, None,
                    False, False, "", False, True, None)
                record = auto._load_batch_jobs()["task-1"]

        self.assertEqual(record["status"], "in_flight")
        self.assertEqual(record["attempt"], 0)


class ResumeRespectsDisabledScheduleTests(unittest.TestCase):
    def test_disabled_auto_publish_forgets_the_durable_record(self):
        from core import store
        from core.publish import auto, auto_batch

        jobs = {"task-1": {"task_id": "task-1", "platform": "fanqie",
                           "status": "retry_wait", "attempt": 1,
                           "next_retry_at": 1.0, "day": OTHER_DAY,
                           "params": {"auto_submit": True}}}
        forgotten = []
        task = {"id": "task-1",
                "auto_publish": {"enabled": False, "platform": "fanqie",
                                 "time": "03:05"}}
        with mock.patch.object(auto, "_load_batch_jobs",
                               return_value=jobs), \
                mock.patch.object(auto, "_save_batch_jobs"), \
                mock.patch.object(auto, "_forget_automatic_batch",
                                  side_effect=lambda tid: forgotten.append(tid)), \
                mock.patch.object(store, "get_task", return_value=task):
            started = auto_batch.resume_interrupted_batches(now=10.0)

        self.assertEqual(started, 0)
        self.assertEqual(forgotten, ["task-1"])

    def test_enabled_schedule_record_is_still_resumable(self):
        from core import store
        from core.publish import auto, auto_batch

        jobs = {"task-1": {"task_id": "task-1", "platform": "fanqie",
                           "status": "retry_wait", "attempt": 1,
                           "next_retry_at": 1.0, "day": OTHER_DAY,
                           "params": {"auto_submit": True}}}
        task = {"id": "task-1",
                "auto_publish": {"enabled": True, "platform": "fanqie",
                                 "time": "03:05"}}
        with mock.patch.object(auto, "_load_batch_jobs",
                               return_value=jobs), \
                mock.patch.object(auto, "_save_batch_jobs"), \
                mock.patch.object(auto, "_forget_automatic_batch") as forget, \
                mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(auto, "publish_pending_async",
                                  return_value=(True, "")) as publish:
            started = auto_batch.resume_interrupted_batches(now=10.0)

        self.assertEqual(started, 1)
        self.assertFalse(forget.called)
        publish.assert_called_once_with(
            "task-1", "fanqie", auto_submit=True)


class CancelUserBatchTests(unittest.TestCase):
    def test_cancel_forgets_record_and_terminalizes_today(self):
        from core.publish import auto, auto_batch

        forgotten = []
        retry_states = {}
        with mock.patch.object(auto, "_cancel_events", {}), \
                mock.patch.object(auto, "_running", {}), \
                mock.patch.object(auto, "_forget_automatic_batch",
                                  side_effect=lambda tid: forgotten.append(tid)), \
                mock.patch.object(auto, "_set_scheduled_retry",
                                  side_effect=lambda tid, st:
                                  retry_states.update({tid: st})):
            stopped = auto_batch.cancel_user_batch("task-1")

        self.assertFalse(stopped)
        self.assertEqual(forgotten, ["task-1"])
        self.assertEqual(retry_states["task-1"]["status"], "cancelled")
        self.assertEqual(retry_states["task-1"]["attempt"], 0)

    def test_cancel_reports_a_killed_live_batch(self):
        from core.publish import auto, auto_batch

        with mock.patch.object(auto, "_cancel_events", {}), \
                mock.patch.object(auto, "_running", {
                    "task-1": {"status": "running"}}), \
                mock.patch.object(auto, "_forget_automatic_batch"), \
                mock.patch.object(auto, "_set_scheduled_retry"):
            stopped = auto_batch.cancel_user_batch("task-1")

        self.assertTrue(stopped)


if __name__ == "__main__":
    unittest.main()
