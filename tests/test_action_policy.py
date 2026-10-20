import unittest
import tempfile
from pathlib import Path

from app.core import action_policy, builtin_agent, policy


class ActionPolicyTests(unittest.TestCase):
    def test_unknown_action_is_denied_before_execution(self):
        result = action_policy.preflight("delete_everything", sandbox={})
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["reason"], "unknown_action")

    def test_allowlist_closes_capability_surface(self):
        result = action_policy.preflight(
            "write_file", sandbox={"allowed_tools": ["read_file"]})
        self.assertEqual(result["reason"], "action_not_allowed")
        mcp = action_policy.preflight(
            "mcp__docs__read", sandbox={"allowed_tools": ["read"]})
        self.assertEqual(mcp["decision"], "autoExecute")
        mcp_approved = action_policy.preflight(
            "mcp__docs__read", sandbox={"require_approval": True,
                                         "approved_tools": ["read"]})
        self.assertEqual(mcp_approved["decision"], "autoExecute")
        mcp_nested = action_policy.preflight(
            "mcp__docs__foo__bar", sandbox={"allowed_tools": ["foo__bar"]})
        self.assertEqual(mcp_nested["decision"], "autoExecute")

    def test_yellow_action_can_require_runtime_approval(self):
        pending = action_policy.preflight(
            "write_file", sandbox={"require_approval": True})
        self.assertEqual(pending["decision"], "needConfirm")
        approved = action_policy.preflight(
            "write_file", sandbox={"require_approval": True,
                                    "approved_tools": ["write_file"]})
        self.assertEqual(approved["decision"], "autoExecute")

    def test_delete_is_a_red_line_even_when_fs_manage_is_registered(self):
        result = action_policy.preflight("fs_manage", args={"action": "delete"}, sandbox={})
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["zone"], "red")
        spaced = action_policy.preflight(
            "fs_manage", args={"action": " delete "}, sandbox={})
        self.assertEqual(spaced["decision"], "deny")

    def test_red_line_reaches_no_filesystem_implementation(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "keep.txt"
            target.write_text("keep", encoding="utf-8")
            result = builtin_agent._exec_tool(
                root, "fs_manage", {"action": "delete", "path": "keep.txt"})
            self.assertIn("工具裁定拒绝", result)
            self.assertTrue(target.exists())

    def test_sandbox_preserves_legacy_unrestricted_tool_mode(self):
        normalized = policy.normalize_sandbox({}, ".")
        self.assertIsNone(normalized["allowed_tools"])
        self.assertFalse(normalized["require_approval"])


if __name__ == "__main__":
    unittest.main()
