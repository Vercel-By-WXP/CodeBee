"""Activity snapshots remain small and use real approval/step state."""
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest import mock

from app.core import activity


class TestActivity(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend tests")
    def test_frontend_model(self):
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name("test_activity_model.cjs"))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_running_projection_excludes_output_and_selects_active_step(self):
        run = {"steps": [
            {"n": 1, "status": "done", "output": "private" * 10000},
            {"n": 2, "status": "running", "role": "implement", "agent": "codex",
             "summary": "brief " * 1000, "output": "private" * 10000},
            {"n": 3, "status": "queued", "prompt": "private"},
        ]}
        result = activity.run_activity(run)
        self.assertEqual(result["current_step"]["n"], 2)
        self.assertEqual(result["current_step"]["agent"], "codex")
        self.assertLessEqual(len(result["current_step"]["summary"]), 240)
        self.assertNotIn("output", str(result))
        self.assertNotIn("prompt", str(result))

    def test_failure_target_is_latest_failed_step(self):
        result = activity.run_activity({"steps": [
            {"n": 1, "status": "failed"}, {"n": 2, "status": "timeout"},
            {"n": 3, "status": "done"}]})
        self.assertEqual(result["failed_step"]["n"], 2)
        self.assertEqual(result["current_step"]["n"], 3)
        self.assertEqual(activity.run_activity({}), {})

    def test_approval_is_only_pending_after_success_and_until_approved(self):
        task = {"id": "t-approval", "approval_required": True}
        latest = {task["id"]: {"status": "done"}}
        with mock.patch.object(activity.contracts, "get", return_value=None):
            self.assertTrue(activity.task_attention([task], latest)[task["id"]]["approval_pending"])
            self.assertEqual(activity.task_attention([task], {task["id"]: {"status": "running"}}), {})
        with mock.patch.object(activity.contracts, "get", return_value={"approval": {"status": "approved"}}):
            self.assertEqual(activity.task_attention([task], latest), {})
        with mock.patch.object(activity.contracts, "get", return_value={"approval": {"status": "rejected"}}):
            self.assertTrue(activity.task_attention([task], latest))

    def test_contract_only_approval_requirement_is_pending(self):
        task = {"id": "t-contract-approval", "approval_required": False}
        latest = {task["id"]: {"status": "done"}}
        contract = {"approval_required": True, "approval": {"status": "pending"}}
        with mock.patch.object(activity.contracts, "get", return_value=contract):
            self.assertTrue(activity.task_attention([task], latest)[task["id"]]["approval_pending"])


if __name__ == "__main__":
    unittest.main()
