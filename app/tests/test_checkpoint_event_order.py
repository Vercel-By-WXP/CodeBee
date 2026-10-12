"""Checkpoint previews must describe the latest attempt, not a prior one."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import checkpoints


class CheckpointEventOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.dir_patch = patch.object(checkpoints, "_DIR", self.root)
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)

    def month(self, name):
        return patch.object(checkpoints, "_file", return_value=self.root / name)

    def test_cross_month_finish_keeps_start_context_and_terminal_status(self):
        with self.month("checkpoints-202609.jsonl"):
            started = checkpoints.start("run", 1, "worker", "before midnight")
        with self.month("checkpoints-202610.jsonl"):
            ended = checkpoints.finish("run", 1, "done", output="result")
        view = checkpoints.replay_preview("run")
        self.assertTrue(view["replayable"])
        self.assertEqual(view["steps"][0]["event_id"], ended["event_id"])
        self.assertEqual(view["steps"][0]["status"], "done")
        self.assertEqual(view["steps"][0]["prompt_sha256"], started["prompt_sha256"])

    def test_cross_month_retry_cannot_inherit_previous_completion(self):
        with self.month("checkpoints-202609.jsonl"):
            checkpoints.start("run", 1, "worker", "old")
            checkpoints.finish("run", 1, "done", output="old result")
        with self.month("checkpoints-202610.jsonl"):
            started = checkpoints.start("run", 1, "worker", "retry", attempt=2)
        view = checkpoints.replay_preview("run")
        self.assertFalse(view["replayable"])
        self.assertEqual(view["blocked_reason"], "requires_reconciliation")
        self.assertEqual(view["steps"][0]["event_id"], started["event_id"])
        self.assertEqual(view["steps"][0]["attempt"], 2)
        self.assertEqual(view["steps"][0]["status"], "running")

    def test_retry_start_drops_old_finish_and_optional_tool_context(self):
        checkpoints.start("run", 1, "worker", "old", tool_call={"name": "shell"},
                          conversation_ref="old-session", side_effects=["external_cli"])
        checkpoints.finish("run", 1, "failed", error="old error", output="old result",
                           metadata={"conversation_ref": "old-session"})
        started = checkpoints.start("run", 1, "worker", "retry", attempt=2)
        item = checkpoints.replay_preview("run")["steps"][0]
        self.assertEqual(item["prompt_sha256"], started["prompt_sha256"])
        for field in ("error", "output_sha256", "metadata", "tool_call_digest", "conversation_ref"):
            with self.subTest(field=field):
                self.assertNotIn(field, item)
        self.assertEqual(item["side_effects"], [])
        self.assertFalse(item["repropose_required"])

    def test_completed_retry_does_not_inherit_old_side_effects(self):
        checkpoints.start("run", 1, "worker", "old", side_effects=["external_cli"])
        checkpoints.finish("run", 1, "failed", metadata={"repropose_required": True})
        checkpoints.start("run", 1, "reader", "retry", attempt=2)
        checkpoints.finish("run", 1, "done", output="new result")
        view = checkpoints.replay_preview("run")
        self.assertTrue(view["replayable"])
        self.assertEqual(view["steps"][0]["role"], "reader")
        self.assertEqual(view["steps"][0]["attempt"], 2)
        self.assertEqual(view["steps"][0]["side_effects"], [])
        self.assertFalse(view["steps"][0]["repropose_required"])

    def test_completed_attempt_keeps_its_own_side_effect_boundary(self):
        started = checkpoints.start("run", 1, "worker", "p", attempt=2,
                                    tool_call={"name": "write_file"},
                                    conversation_ref="new-session",
                                    side_effects=["workspace_files"])
        checkpoints.finish("run", 1, "done", metadata={"repropose_required": False})
        item = checkpoints.replay_preview("run")["steps"][0]
        self.assertEqual(item["tool_call_digest"], started["tool_call_digest"])
        self.assertEqual(item["conversation_ref"], "new-session")
        self.assertEqual(item["side_effects"], ["workspace_files"])
        self.assertTrue(item["repropose_required"])

    def test_append_order_is_authoritative_even_if_clock_moves_backwards(self):
        with patch.object(checkpoints.time, "time", return_value=200):
            checkpoints.start("run", 1, "worker", "p")
        with patch.object(checkpoints.time, "time", return_value=100):
            checkpoints.finish("run", 1, "done")
        self.assertTrue(checkpoints.replay_preview("run")["replayable"])

    def test_only_most_recent_twenty_four_months_are_read_oldest_first(self):
        for month in range(1, 26):
            with self.month("checkpoints-%06d.jsonl" % month):
                checkpoints.start("run", month, "worker", "p")
                checkpoints.finish("run", month, "done")
        rows = checkpoints._read("run")
        self.assertEqual(len(rows), 48)
        self.assertEqual(rows[0]["step"], 2)
        self.assertEqual(rows[-1]["step"], 25)

    def test_orphan_finish_and_unknown_finish_require_reconciliation(self):
        checkpoints.finish("orphan", 1, "done")
        checkpoints.start("unknown", 1, "worker")
        checkpoints.finish("unknown", 1, "unknown")
        for run_id in ("orphan", "unknown"):
            with self.subTest(run_id=run_id):
                self.assertFalse(checkpoints.replay_preview(run_id)["replayable"])

    def test_malformed_lines_and_other_runs_do_not_overwrite_target(self):
        checkpoints.start("run", 1, "worker")
        ended = checkpoints.finish("run", 1, "done")
        checkpoints.start("other", 1, "worker")
        with checkpoints._file().open("a", encoding="utf-8") as handle:
            handle.write('not json\n[]\n')
            handle.write(json.dumps({"run_id": "run", "event": "start", "step": "bad"}) + "\n")
            handle.write(json.dumps({"run_id": "run", "event": "finish", "step": -1}) + "\n")
        view = checkpoints.replay_preview("run")
        self.assertTrue(view["replayable"])
        self.assertEqual(view["total_steps"], 1)
        self.assertEqual(view["steps"][0]["event_id"], ended["event_id"])


if __name__ == "__main__":
    unittest.main()
