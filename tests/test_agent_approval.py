import unittest
import tempfile
from pathlib import Path
from unittest import mock

from app.core import agent_approval, audit, builtin_agent


class AgentApprovalTests(unittest.TestCase):
    def test_approval_is_bound_to_run_and_tool(self):
        store = agent_approval.RuntimeApprovalStore(now=lambda: 100.0)
        store.approve("task text", run_id="run-1", tools=["write_file"])

        self.assertTrue(store.is_approved("task text", run_id="run-1", action="write_file"))
        self.assertFalse(store.is_approved("task text", run_id="run-1", action="run_command"))
        self.assertFalse(store.is_approved("task text", run_id="run-2", action="write_file"))

    def test_approval_expires_and_never_persists(self):
        clock = [100.0]
        store = agent_approval.RuntimeApprovalStore(
            ttl_s=10, now=lambda: clock[0])
        store.approve("task", run_id="run", tools=["write_file"])
        clock[0] = 110.0
        self.assertFalse(store.is_approved("task", run_id="run", action="write_file"))
        self.assertEqual(store.size(), 0)
        self.assertEqual(store.snapshot(), [])

    def test_group_approval_covers_registered_tools(self):
        store = agent_approval.RuntimeApprovalStore(now=lambda: 1.0)
        store.approve("task", run_id="run", groups=["write"])
        self.assertTrue(store.is_approved("task", run_id="run", action="edit_file", group="write"))

    def test_builtin_tool_uses_only_ephemeral_approval(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "out.txt"
            sandbox = {"require_approval": True, "allowed_roots": [root],
                       "approval_task": "task", "approved_tools": []}
            with mock.patch.object(audit.paths, "DATA_DIR", Path(root) / "data"):
                pending = builtin_agent._exec_tool(
                    root, "write_file", {"path": "out.txt", "content": "ok"},
                    sandbox=sandbox, checkpoint_run_id="run")
                self.assertIn("需要显式审批", pending)
                agent_approval.approve("task", "run", tools=["write_file"])
                done = builtin_agent._exec_tool(
                    root, "write_file", {"path": "out.txt", "content": "ok"},
                    sandbox=sandbox, checkpoint_run_id="run")
                self.assertTrue(target.exists())
                self.assertIn("已写入", done)


if __name__ == "__main__":
    unittest.main()
