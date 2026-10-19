from __future__ import annotations

import threading
import time
from datetime import datetime
from unittest.mock import patch

from base import BaseTest


class SerialDailyCase(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import serial_daily
        self.daily = serial_daily
        self.daily._FILE = self.data_dir / "serial_daily.json"
        with self.daily._LOCK:
            self.daily._JOBS.clear()
            self.daily._LOADED = False
        self.task = self._serial_task()
        self.addCleanup(self._clear)

    def _serial_task(self):
        from app.core import store
        return store.create_task({"type": "serial_novel", "goal": "继续连载",
                                  "workdir": str(self.workdir)})

    def _clear(self):
        with self.daily._LOCK:
            self.daily._JOBS.clear()
            self.daily._LOADED = False

    def make(self, **values):
        body = {"task_id": self.task["id"], "platform": "fanqie",
                "chapters_per_day": 2, "publish_time": "21:00"}
        body.update(values)
        return self.daily.create(body)

    def test_create_validates_and_persists_one_click_plan(self):
        job = self.make()
        self.assertEqual(job["status"], "active")
        self.assertEqual(job["chapters_per_day"], 2)
        self.assertTrue(job["id"])
        self.daily._JOBS.clear()
        self.daily._LOADED = False
        self.assertEqual(self.daily.get(job["id"])["task_id"], self.task["id"])
        with self.assertRaises(ValueError):
            self.make(chapters_per_day=0)
        with self.assertRaises(ValueError):
            self.make(publish_time="25:99")

    def test_pause_and_cancel_are_distinct_and_cancel_is_terminal(self):
        job = self.make()
        paused = self.daily.pause(job["id"])
        self.assertEqual(paused["status"], "paused")
        cancelled = self.daily.cancel(job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(self.daily.resume(job["id"]))

    def test_pause_stops_remaining_publish_batch_after_current_action(self):
        from app.core.publish import auto
        job = self.make()
        event = threading.Event()
        with auto._LOCK:
            auto._cancel_events[job["task_id"]] = event
        try:
            self.daily.pause(job["id"])
            self.assertTrue(event.is_set())
        finally:
            with auto._LOCK:
                auto._cancel_events.pop(job["task_id"], None)

    def test_due_day_starts_one_generation_run_and_tracks_day(self):
        job = self.make()
        self.daily._start_generation = lambda snapshot, day: ("run-test", [], "daily-task")
        self.daily._tick(now=datetime(2026, 10, 19, 10, 0))
        state = self.daily.get(job["id"])
        self.assertEqual(state["day"], "2026-10-19")
        self.assertEqual(state["run_id"], "run-test")
        self.assertEqual(state["status"], "active")
        self.daily._tick(now=datetime(2026, 10, 19, 10, 1))
        self.assertEqual(self.daily.get(job["id"])["run_id"], "run-test")

    def test_missing_run_enters_retry_path_instead_of_sticking(self):
        job = self.make()
        self.daily._JOBS[job["id"]].update(day="2026-10-19", phase="generating",
                                           run_id="missing-run")
        started = []
        self.daily._start_generation = lambda snapshot, day, chapters=None: (
            started.append(chapters) or ("retry-run", [], "daily-task"))
        self.daily._tick(now=datetime(2026, 10, 19, 10, 1))
        self.assertEqual(self.daily.get(job["id"])["phase"], "retry_generation")
        self.daily._tick(now=datetime(2026, 10, 19, 10, 2))
        self.assertEqual(self.daily.get(job["id"])["run_id"], "retry-run")
        self.assertEqual(started, [2])

    def test_publish_due_uses_only_today_chapters_and_records_confirmed_success(self):
        job = self.make()
        self.daily._JOBS[job["id"]].update(day="2026-10-19", run_id="run-done",
                                           new_chapters=[4, 5], phase="waiting_publish")
        from app.core import store
        run = store.create_run("orchestration", "写作", task_id=self.task["id"])
        store.update_run(run["id"], status="done", ended_at="2026-10-19 20:00:00")
        self.daily._JOBS[job["id"]]["run_id"] = run["id"]
        calls = []
        self.daily._publish = lambda *args, **kwargs: calls.append((args, kwargs)) or (True, "")
        with patch.object(self.daily, "_published_chapters", side_effect=[set(), {4, 5}]):
            self.daily._tick(now=datetime(2026, 10, 19, 21, 0))
        self.assertEqual(calls[0][0][1], [4, 5])
        self.assertEqual(self.daily.get(job["id"])["last_day_status"], "done")

    def test_deadline_alerts_once_when_daily_target_is_unmet(self):
        job = self.make()
        self.daily._JOBS[job["id"]].update(day="2026-10-19", phase="waiting_publish",
                                           run_id="run-failed", new_chapters=[3])
        from app.core import store
        run = store.create_run("orchestration", "写作", task_id=self.task["id"])
        store.update_run(run["id"], status="failed", error="模型失败")
        self.daily._JOBS[job["id"]]["run_id"] = run["id"]
        alerts = []
        self.daily._alert = lambda text: alerts.append(text)
        self.daily._tick(now=datetime(2026, 10, 19, 23, 59))
        self.daily._tick(now=datetime(2026, 10, 19, 23, 59))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(self.daily.get(job["id"])["last_day_status"], "alerted")
