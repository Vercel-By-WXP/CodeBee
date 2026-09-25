# -*- coding: utf-8 -*-
"""Task budget defaults and queue/execution boundary regression tests."""
from __future__ import annotations

import time
from unittest import mock

from base import BaseTest


class TestTaskTimeoutBudget(BaseTest):
    def test_defaults_match_task_workload_and_explicit_budget_wins(self):
        from app.core import store

        def create(kind, **extra):
            return store.create_task(dict(type=kind, goal="执行任务",
                                          workdir=str(self.workdir), **extra))

        self.assertEqual(create("direct")["timeout_s"], 3600)
        self.assertEqual(create("doc")["timeout_s"], 7200)
        self.assertEqual(create("code")["timeout_s"], 14400)
        self.assertEqual(create("serial_novel", serial={
            "chapters": 8, "words_per_chapter": 2500})["timeout_s"], 14400)
        self.assertEqual(create("serial_novel", serial={
            "chapters": 20, "words_per_chapter": 2500})["timeout_s"], 36000)
        self.assertEqual(create("code", timeout_s=1800)["timeout_s"], 1800)
        with mock.patch.dict("os.environ", {"TUTTI_TASK_TIMEOUT_S": "5400"}):
            self.assertEqual(create("code")["timeout_s"], 5400)

    def test_queued_time_does_not_consume_execution_budget(self):
        from app.core import jobs, store

        task = store.create_task({"type": "direct", "goal": "执行任务",
                                  "workdir": str(self.workdir), "timeout_s": 30})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        # Simulate a run that sat in a persistent queue beyond its provisional
        # deadline.  The first successful claim must still receive 30 seconds.
        store.update_run(run["id"], deadline_at=time.time() - 60)
        with mock.patch.object(jobs.threading, "Thread") as thread:
            self.assertTrue(jobs._try_start_once({"kind": "orchestration",
                                                  "run_id": run["id"],
                                                  "task_id": task["id"]}))
            thread.return_value.start.assert_called_once()
        claimed = store.get_run(run["id"])
        self.assertEqual(claimed["status"], "running")
        self.assertAlmostEqual(claimed["deadline_at"], time.time() + 30, delta=3)
        self.assertEqual(claimed["timeout_budget_s"], 30)
        jobs._release_slot({"light": True})
        jobs.CANCELS.pop(run["id"], None)

    def test_busy_queue_retries_after_provisional_deadline(self):
        from app.core import jobs, store

        task = store.create_task({"type": "direct", "goal": "执行任务",
                                  "workdir": str(self.workdir), "timeout_s": 30})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running", deadline_at=time.time() - 60)
        callback = []

        class FakeTimer:
            daemon = False

            def __init__(self, _delay, fn):
                callback.append(fn)

            def start(self):
                pass

        job = {"kind": "orchestration", "run_id": run["id"],
               "task_id": task["id"]}
        with mock.patch.object(jobs.threading, "Timer", FakeTimer), \
             mock.patch.object(jobs.threading, "Thread") as thread:
            self.assertTrue(jobs._requeue_await_slot(job))
            self.assertEqual(store.get_run(run["id"])["status"], "queued")
            callback[0]()
            thread.return_value.start.assert_called_once()
        claimed = store.get_run(run["id"])
        self.assertEqual(claimed["status"], "running")
        self.assertAlmostEqual(claimed["deadline_at"], time.time() + 30, delta=3)
        jobs._release_slot({"light": True})
        jobs.CANCELS.pop(run["id"], None)

    def test_full_pool_does_not_refresh_deadline_before_slot_claim(self):
        from app.core import jobs, store

        task = store.create_task({"type": "direct", "goal": "执行任务",
                                  "workdir": str(self.workdir), "timeout_s": 30})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        past = time.time() - 60
        store.update_run(run["id"], deadline_at=past)
        with jobs._pool_lock:
            old_alive = jobs._chat_alive
            jobs._chat_alive = jobs.CHAT_POOL
        try:
            self.assertFalse(jobs._try_start_once({"kind": "orchestration",
                                                   "run_id": run["id"],
                                                   "task_id": task["id"]}))
            self.assertEqual(store.get_run(run["id"])["deadline_at"], past)
        finally:
            with jobs._pool_lock:
                jobs._chat_alive = old_alive
