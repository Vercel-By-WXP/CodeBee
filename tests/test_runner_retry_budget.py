import os
import unittest
from unittest.mock import patch

from app.core import catalog, runner


class DshLaunchGuardTests(unittest.TestCase):
    def setUp(self):
        with runner._DSH_LAUNCH_LOCK:
            runner._DSH_LAUNCH_HISTORY.clear()

    def tearDown(self):
        with runner._DSH_LAUNCH_LOCK:
            runner._DSH_LAUNCH_HISTORY.clear()

    def test_failed_launches_are_bounded_per_run_and_role(self):
        with patch.dict(os.environ, {
            "TUTTI_DSH_MAX_LAUNCHES": "2",
            "TUTTI_DSH_LAUNCH_WINDOW_S": "600",
            "TUTTI_DSH_MIN_LAUNCH_GAP_S": "0",
        }, clear=False):
            first = runner._dsh_launch_admit("run-1", "draft", "deepseek-harness", "upstream")
            second = runner._dsh_launch_admit("run-1", "draft", "deepseek-harness", "upstream")
            blocked = runner._dsh_launch_admit("run-1", "draft", "deepseek-harness", "upstream")

        self.assertIsInstance(first, tuple)
        self.assertIsInstance(second, tuple)
        self.assertIsInstance(blocked, str)
        self.assertIn("停止继续拉起", blocked)

    def test_success_clears_failure_streak(self):
        with patch.dict(os.environ, {
            "TUTTI_DSH_MAX_LAUNCHES": "1",
            "TUTTI_DSH_MIN_LAUNCH_GAP_S": "0",
        }, clear=False):
            token = runner._dsh_launch_admit("run-2", "review", "dsh", "upstream")
            runner._dsh_launch_complete(token, True)
            next_token = runner._dsh_launch_admit("run-2", "review", "dsh", "upstream")

        self.assertIsInstance(next_token, tuple)

    def test_catalog_bounds_dsh_child_process(self):
        entry = next(item for item in catalog.DEFAULT_CATALOG
                     if item.get("id") == "deepseek-harness")
        self.assertEqual(entry["orch"].get("timeout_ms"), 180000)
        self.assertEqual(entry["orch"].get("stall_timeout_s"), 45)

    def test_legacy_call_without_run_identity_is_not_shared(self):
        with patch.dict(os.environ, {"TUTTI_DSH_MIN_LAUNCH_GAP_S": "300"}, clear=False):
            self.assertIsNone(runner._dsh_launch_admit("", "", "dsh", "upstream"))

    def test_budget_block_does_not_hide_a_later_endpoint(self):
        provider = lambda host: {"id": host, "name": host,
                                 "base_url": "https://%s.example.test" % host}
        chain = [
            {"model": "m1", "provider": provider("dead"), "provider_id": "dead"},
            {"model": "m2", "provider": provider("dead"), "provider_id": "dead"},
            {"model": "m3", "provider": provider("live"), "provider_id": "live"},
        ]
        failed = {"ok": False, "exit_code": 1, "stdout": "",
                  "stderr": "429 Too Many Requests", "duration": 0.01,
                  "cancelled": False, "timed_out": False, "stalled": False,
                  "deadline_exceeded": False, "abort_marker": None}
        with patch.dict(os.environ, {
            "TUTTI_DSH_MAX_LAUNCHES": "1",
            "TUTTI_DSH_MIN_LAUNCH_GAP_S": "300",
        }, clear=False), patch.object(runner, "_build_call",
                                      return_value=(["dsh"], "prompt", "prompt", [])), \
             patch.object(runner, "run_process", return_value=failed) as run_process:
            agent = {"id": "dsh", "kind": "generic", "mode": "real",
                     "command": "dsh", "call_chain": chain,
                     "orch": {"timeout_ms": 1000}}
            result = runner.run_agent(
                agent, "prompt", timeout=1,
                command_context={"run_id": "run-fallback", "role": "draft"},
                checkpoint_run_id="run-fallback")

        self.assertEqual(run_process.call_count, 2)
        self.assertEqual(result["attempts"][-1]["provider_id"], "live")


if __name__ == "__main__":
    unittest.main()
