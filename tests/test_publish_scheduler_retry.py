"""Scheduled publishing retries failed unattended batches without duplicate starts."""
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


class ScheduledPublishRetryTests(unittest.TestCase):
    def test_new_schedule_is_ready_without_prior_state(self):
        from core.publish.auto import scheduled_retry_state

        state, ready = scheduled_retry_state(None, None, now=1000)
        self.assertTrue(ready)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["attempt"], 0)

    def test_running_batch_is_not_started_twice(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 1, "status": "in_flight"}
        state, ready = scheduled_retry_state(prior, "running", now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "in_flight")

    def test_retry_wait_adopts_another_live_batch_for_the_task(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 1,
                 "status": "retry_wait", "next_retry_at": 900}
        state, ready = scheduled_retry_state(prior, "running", now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "in_flight")

    def test_terminal_failure_enters_backoff_and_then_retries(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 1, "status": "in_flight"}
        state, ready = scheduled_retry_state(prior, "error", now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "retry_wait")
        self.assertGreater(state["next_retry_at"], 1000)
        state, ready = scheduled_retry_state(state, None,
                                             now=state["next_retry_at"])
        self.assertTrue(ready)
        self.assertEqual(state["attempt"], 1)

    def test_success_and_cancel_are_terminal_for_the_day(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 1, "status": "in_flight"}
        state, ready = scheduled_retry_state(prior, "done", now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "succeeded")
        state, ready = scheduled_retry_state(prior, "cancelled", now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "cancelled")

    def test_retry_budget_is_finite(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5, "status": "in_flight"}
        state, ready = scheduled_retry_state(prior, "error", now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "exhausted")

    def test_recoverable_login_error_keeps_retrying_after_budget(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5,
                 "status": "in_flight"}
        state, ready = scheduled_retry_state(
            prior, "error", now=1000, error="登录态已失效，请重新登录")
        self.assertFalse(ready)
        self.assertEqual(state["status"], "retry_wait")
        self.assertEqual(state["attempt"], 5)
        self.assertEqual(state["error"], "登录态已失效，请重新登录")
        self.assertGreater(state["next_retry_at"], 1000)

    def test_recoverable_browser_busy_error_keeps_retrying_after_budget(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5,
                 "status": "retry_wait", "next_retry_at": 900}
        state, ready = scheduled_retry_state(
            prior, None, now=1000, error="该平台账号浏览器正在执行另一项发布操作")
        self.assertTrue(ready)
        self.assertEqual(state["status"], "retry_wait")
        self.assertEqual(state["attempt"], 5)

    def test_recoverable_account_busy_error_keeps_retrying_after_budget(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5,
                 "status": "in_flight"}
        state, ready = scheduled_retry_state(
            prior, "error", now=1000, error="该账号有操作正在进行中")
        self.assertFalse(ready)
        self.assertEqual(state["status"], "retry_wait")

    def test_platform_rate_limit_keeps_batch_for_cooldown_retry(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5,
                 "status": "in_flight"}
        for error in ("访问过于频繁，请稍后再试", "HTTP 429: Too Many Requests",
                      "异常请求，请降低操作频率"):
            with self.subTest(error=error):
                state, ready = scheduled_retry_state(
                    prior, "error", now=1000, error=error)
                self.assertFalse(ready)
                self.assertEqual(state["status"], "retry_wait")
                self.assertEqual(state["error"], error)
                self.assertGreater(state["next_retry_at"], 1000)

    def test_content_error_still_exhausts_after_budget(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5,
                 "status": "in_flight"}
        state, ready = scheduled_retry_state(
            prior, "error", now=1000, error="章节正文过短，不符合平台规则")
        self.assertFalse(ready)
        self.assertEqual(state["status"], "exhausted")
        self.assertEqual(state["error"], "章节正文过短，不符合平台规则")

    def test_legacy_exhausted_recoverable_error_is_migrated(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": TODAY, "attempt": 5,
                 "status": "exhausted", "error": "浏览器连接超时"}
        state, ready = scheduled_retry_state(prior, None, now=1000)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "retry_wait")
        self.assertGreater(state["next_retry_at"], 1000)

    def test_new_day_starts_a_fresh_retry_budget(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": YESTERDAY, "attempt": 5, "status": "exhausted"}
        state, ready = scheduled_retry_state(prior, None, now=1000,
                                             day=TODAY)
        self.assertTrue(ready)
        self.assertEqual(state["attempt"], 0)
        self.assertEqual(state["day"], TODAY)

    def test_active_batch_crossing_midnight_stays_in_flight_then_succeeds(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": YESTERDAY, "attempt": 1, "status": "in_flight"}
        state, ready = scheduled_retry_state(
            prior, "running", now=1000, day=TODAY)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "in_flight")
        self.assertEqual(state["day"], TODAY)
        state, ready = scheduled_retry_state(
            state, "done", now=1001, day=TODAY)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "succeeded")

    def test_untracked_manual_batch_is_adopted_and_completion_is_terminal(self):
        from core.publish.auto import scheduled_retry_state

        state, ready = scheduled_retry_state(
            None, "running", now=1000, day=TODAY)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "in_flight")
        state, ready = scheduled_retry_state(
            state, "done", now=1001, day=TODAY)
        self.assertFalse(ready)
        self.assertEqual(state["status"], "succeeded")

    def test_completed_previous_day_batch_marks_current_day_succeeded(self):
        from core.publish.auto import scheduled_retry_state

        prior = {"day": YESTERDAY, "attempt": 1, "status": "in_flight"}
        state, ready = scheduled_retry_state(
            prior, "done", now=1000, day=TODAY)
        self.assertFalse(ready)
        self.assertEqual(state["day"], TODAY)
        self.assertEqual(state["status"], "succeeded")

    def test_retry_state_is_persisted_and_reloaded(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            retry_path = Path(temp_dir) / "retry.json"
            with mock.patch.object(auto, "_AP_RETRY_FILE", retry_path), \
                 mock.patch.object(auto, "_AP_RETRY", None):
                auto._set_scheduled_retry("task-1", {
                    "day": TODAY, "attempt": 2, "status": "in_flight"})
                auto._AP_RETRY = None
                loaded = auto._load_retry_state()

        self.assertEqual(loaded["task-1"]["attempt"], 2)
        self.assertEqual(loaded["task-1"]["status"], "in_flight")

    def test_failed_scheduler_start_is_not_recorded_as_fired(self):
        from core.publish import auto, ledger
        import time

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_AP_RETRY_FILE",
                                   Path(temp_dir) / "retry.json"), \
                 mock.patch.object(auto, "_AP_RETRY", {}), \
                 mock.patch.object(auto, "_AP_FILE",
                                   Path(temp_dir) / "fired.json"), \
                 mock.patch.object(auto, "_AP_FIRED", {}), \
                 mock.patch.object(auto, "due_tasks", return_value=[
                     ({"id": "task-1"}, {"platform": "fanqie", "time": "08:00"})
                 ]), \
                 mock.patch.object(auto, "publish_pending_async",
                                   return_value=(False, "浏览器忙")), \
                 mock.patch.object(ledger, "record"):
                now = time.struct_time((2026, 10, 19, 9, 0, 0, 0, 292, -1))
                self.assertEqual(auto.fire_due(now=now), 0)
                retry = auto._load_retry_state()["task-1"]
                fired = auto._load_fired()

        self.assertEqual(retry["status"], "retry_wait")
        self.assertNotIn("task-1", fired)

    def test_due_task_without_book_is_eligible_when_chapters_are_pending(self):
        from core import contracts, store
        from core.publish import auto, ledger

        task = {"id": "task-1", "auto_publish": {
            "enabled": True, "platform": "fanqie", "time": "08:00"}}
        now = time.struct_time((2026, 10, 19, 9, 0, 0, 0, 292, -1))
        with mock.patch.object(store, "list_tasks", return_value=[task]), \
                mock.patch.object(store, "serial_chain_ids", return_value=["task-1"]), \
                mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(auto, "_load_fired", return_value={}), \
                mock.patch.object(auto, "_scheduled_retry", return_value=(
                    {"status": "ready", "attempt": 0}, True)), \
                mock.patch.object(auto, "pending", return_value=([{
                    "chapter_no": 1, "file": "chapter-1.md"}], "")):
            due = auto.due_tasks(now=now)

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0][0]["id"], "task-1")

    def test_scheduler_starts_batch_with_automatic_book_preparation(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(auto, "_AP_RETRY_FILE",
                                  Path(temp_dir) / "retry.json"), \
                mock.patch.object(auto, "_AP_RETRY", {}), \
                mock.patch.object(auto, "_AP_FILE",
                                  Path(temp_dir) / "fired.json"), \
                mock.patch.object(auto, "_AP_FIRED", {}), \
                mock.patch.object(auto, "due_tasks", return_value=[
                    ({"id": "task-1"}, {"platform": "qimao", "time": "08:00"})
                ]), \
                mock.patch.object(auto, "publish_pending_async",
                                  return_value=(True, "")) as publish, \
                mock.patch("core.publish.ledger.record"):
            now = time.struct_time((2026, 10, 19, 9, 0, 0, 0, 292, -1))
            auto.fire_due(now=now)

        publish.assert_called_once_with(
            "task-1", "qimao", auto_submit=True, auto_prepare_book=True)


if __name__ == "__main__":
    unittest.main()
