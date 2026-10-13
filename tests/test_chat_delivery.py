"""Queue defaults, immediate follow-ups and safe workspace handoff."""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from base import BaseTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


class ChatDeliveryTests(BaseTest):
    def setUp(self):
        super().setUp()
        import main
        from app.core import jobs, store
        self.main, self.jobs, self.store = main, jobs, store
        for name, value in (("store", store), ("jobs", jobs)):
            p = patch.object(main, name, value)
            p.start()
            self.addCleanup(p.stop)
        hook = patch("core.hooks.run_event", return_value="")
        hook.start()
        self.addCleanup(hook.stop)
        self.task = store.create_task({"type": "direct", "title": "Delivery",
                                       "goal": "Finish the task", "workdir": str(self.workdir)})
        self.run = store.create_run("orchestration", "Delivery", task_id=self.task["id"])

    def submit(self, body, run_id=None, enqueue=None):
        handler = self.main.Handler.__new__(self.main.Handler)
        handler._body = lambda: body
        handler._client_name = lambda: "test"
        handler._json = lambda code, obj: (code, obj)
        handler._enqueue_run = enqueue or (lambda rid, tid, job: (True, ""))
        return handler._api_direct_chat(run_id or self.run["id"])

    def test_queue_is_default_and_does_not_interrupt(self):
        self.store.update_run(self.run["id"], status="running")
        with patch.object(self.jobs, "cancel") as cancel:
            code, result = self.submit({"text": "Later"})
        self.assertEqual(code, 200)
        cancel.assert_not_called()
        self.assertEqual(result["message"]["text"], "Later")
        self.assertEqual(len(self.store.task_runs(self.task["id"])), 1)

    def test_immediate_preserves_queue_and_waits_before_starting(self):
        self.store.update_run(self.run["id"], status="running")
        self.store.add_message(self.run["id"], "Queued context")
        order = []

        def stopped(rid):
            self.assertEqual(self.store.get_run(rid)["status"], "cancelled")
            order.append("stopped")
            return True

        def start(rid, tid, job):
            order.append("start")
            return True, ""

        with patch.object(self.jobs, "wait_run_stopped", side_effect=stopped):
            code, result = self.submit({"text": "Change direction", "delivery": "now"}, enqueue=start)
        self.assertEqual(code, 200)
        self.assertEqual(order, ["stopped", "start"])
        self.assertEqual(result["delivery"], "now")
        new_run = self.store.get_run(result["run_id"])
        self.assertEqual([m["text"] for m in new_run["messages"]], ["Queued context", "Change direction"])
        self.assertTrue(self.store.get_run(self.run["id"])["cancelled_by_user"])

    def test_slow_stop_retains_draft_without_starting_another_writer(self):
        self.store.update_run(self.run["id"], status="running")
        with patch.object(self.jobs, "wait_run_stopped", return_value=False):
            code, result = self.submit({"text": "Guidance", "delivery": "now"})
        self.assertEqual(code, 409)
        self.assertIn("仍在停止", result["error"])
        self.assertEqual(self.store.peek_messages(self.run["id"]), [])
        self.assertEqual(len(self.store.task_runs(self.task["id"])), 1)

    def test_empty_and_invalid_delivery_do_not_cancel(self):
        self.store.update_run(self.run["id"], status="running")
        with patch.object(self.jobs, "cancel") as cancel:
            self.assertEqual(self.submit({"text": " ", "delivery": "now"})[0], 400)
            self.assertEqual(self.submit({"text": "x", "delivery": "invalid"})[0], 400)
        cancel.assert_not_called()

    def test_turn_finished_before_queue_receipt_starts_followup(self):
        self.store.update_run(self.run["id"], status="done")
        code, result = self.submit({"text": "Follow up", "delivery": "queue"})
        self.assertEqual(code, 200)
        self.assertEqual(result["delivery"], "sent")
        self.assertEqual(self.store.get_run(result["run_id"])["messages"][0]["text"], "Follow up")

    def test_stale_immediate_cannot_cancel_new_turn(self):
        self.store.update_run(self.run["id"], status="done")
        _, first = self.submit({"text": "New turn"})
        self.store.update_run(first["run_id"], status="running")
        code, _ = self.submit({"text": "Stale", "delivery": "now"})
        self.assertEqual(code, 409)
        self.assertEqual(self.store.get_run(first["run_id"])["status"], "running")

    def test_stale_queue_routes_to_latest_turn(self):
        self.store.update_run(self.run["id"], status="done")
        _, first = self.submit({"text": "New turn"})
        self.store.update_run(first["run_id"], status="running")
        code, result = self.submit({"text": "Queued in new turn"})
        self.assertEqual(code, 200)
        self.assertEqual(result["message"]["text"], "Queued in new turn")
        self.assertEqual(self.store.get_run(first["run_id"])["messages"][-1]["text"], "Queued in new turn")

    def test_retract_removes_inherited_pending_copies(self):
        message = self.store.add_message(self.run["id"], "Withdraw me")
        self.store.update_run(self.run["id"], status="cancelled")
        _, _, followup = self.store.retry_task(self.task["id"])
        ok, error = self.store.retract_message(followup["id"], message["id"])
        self.assertTrue(ok, error)
        self.assertEqual(self.store.peek_messages(self.run["id"]), [])
        self.assertEqual(self.store.peek_messages(followup["id"]), [])

    def test_delivered_inherited_copy_cannot_be_retracted(self):
        message = self.store.add_message(self.run["id"], "Delivered")
        self.store.update_run(self.run["id"], status="cancelled")
        _, _, followup = self.store.retry_task(self.task["id"])
        self.store.drain_messages(followup["id"])
        self.assertFalse(self.store.retract_message(self.run["id"], message["id"])[0])
        handler = self.main.Handler.__new__(self.main.Handler)
        handler._json = lambda code, obj: obj
        timeline = handler._api_run_timeline(followup["id"])
        item = next(item for item in timeline["items"] if item["text"] == "Delivered")
        self.assertTrue(item["consumed"])
        self.assertEqual(item["run"], followup["id"])

    def test_wait_targets_one_worker_and_respects_timeout(self):
        release = threading.Event()
        worker = threading.Thread(target=lambda: release.wait(2))
        worker.start()
        try:
            with patch.dict(self.jobs._workers, {"fixture": worker}):
                self.assertFalse(self.jobs.wait_run_stopped("fixture", timeout=.01))
                self.assertTrue(self.jobs.wait_run_stopped("other", timeout=.01))
                release.set()
                self.assertTrue(self.jobs.wait_run_stopped("fixture", timeout=1))
        finally:
            release.set()
            worker.join(2)

    def test_real_worker_exits_before_immediate_followup(self):
        from app.core import pipeline
        entered, finished = threading.Event(), threading.Event()
        rid = self.run["id"]

        def execute(run_id):
            entered.set()
            self.jobs.cancel_event_for(run_id).wait(2)
            (self.workdir / "old-writer.txt").write_text("finished", encoding="utf-8")
            finished.set()

        def start(new_id, tid, job):
            self.assertTrue(finished.is_set())
            self.assertEqual((self.workdir / "old-writer.txt").read_text(), "finished")
            return True, ""

        with patch.object(pipeline, "execute_run", side_effect=execute), \
             patch.object(self.jobs, "_maybe_auto_resume"), patch("app.core.notify.push_run_async"):
            self.jobs.enqueue({"kind": "orchestration", "run_id": rid, "task_id": self.task["id"]})
            self.assertTrue(entered.wait(1))
            try:
                code, _ = self.submit({"text": "Now", "delivery": "now"}, enqueue=start)
                self.assertEqual(code, 200)
            finally:
                self.jobs.cancel(rid)
                self.jobs.wait_run_stopped(rid, timeout=2)
