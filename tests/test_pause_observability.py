# -*- coding: utf-8 -*-
"""暂停请求与实际到达步骤闸门之间的可观测状态。"""
from __future__ import annotations

from base import BaseTest


class PauseObservabilityTests(BaseTest):
    def test_pause_request_transitions_to_reached_at_gate(self):
        from app.core import store

        run = store.create_run("orchestration", "pause lifecycle")
        rid = run["id"]
        store.update_run(rid, status="running")
        self.assertEqual(store.get_run(rid)["pause_phase"], "none")

        self.assertTrue(store.set_paused(rid, True))
        self.assertEqual(store.get_run(rid)["pause_phase"], "requested")
        self.assertEqual(store.pause_observability(),
                         {"active": 1, "requested": 1, "reached": 0})

        self.assertTrue(store.mark_pause_reached(rid))
        self.assertEqual(store.get_run(rid)["pause_phase"], "paused")
        self.assertEqual(store.pause_observability(),
                         {"active": 1, "requested": 0, "reached": 1})

        self.assertTrue(store.set_paused(rid, False))
        self.assertEqual(store.get_run(rid)["pause_phase"], "none")

    def test_running_step_keeps_request_pending_and_terminal_clears_phase(self):
        from app.core import store

        run = store.create_run("orchestration", "parallel pause")
        rid = run["id"]
        step, _ = store.add_step(rid, "implement", "a1", "Agent")
        store.update_run(rid, status="running")
        store.set_paused(rid, True)
        self.assertFalse(store.mark_pause_reached(rid))
        self.assertEqual(store.get_run(rid)["pause_phase"], "requested")

        store.finish_step(rid, step["n"], "done")
        self.assertTrue(store.mark_pause_reached(rid))
        self.assertEqual(store.get_run(rid)["pause_phase"], "paused")

        store.update_run(rid, status="done")
        final = store.get_run(rid)
        self.assertEqual(final["pause_phase"], "none")
        self.assertIsNone(final["pause_reached_at"])


if __name__ == "__main__":
    import unittest
    unittest.main()
