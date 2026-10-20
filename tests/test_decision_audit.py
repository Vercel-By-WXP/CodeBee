import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import audit, builtin_agent


class DecisionAuditTests(unittest.TestCase):
    def test_builtin_tool_call_writes_decision_receipt_without_arguments(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(audit.paths, "DATA_DIR", Path(root)):
                result = builtin_agent._exec_tool(root, "read_file", {"path": "missing.txt"})
                self.assertIn("文件不存在", result)
                rows = [json.loads(line) for line in
                        (Path(root) / "audit" / ("tool_decision-" + audit.time.strftime("%Y-%m") + ".jsonl")).read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["event"], "tool_decision")
                self.assertEqual(rows[0]["action"], "read_file")
                self.assertNotIn("missing.txt", rows[0].get("args", ""))
                self.assertTrue(audit.verify("tool_decision")["ok"])

    def test_unknown_tool_is_audited_as_denied(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(audit.paths, "DATA_DIR", Path(root)):
                result = builtin_agent._exec_tool(root, "unknown_tool", {})
                self.assertIn("拒绝", result)
                path = Path(root) / "audit" / ("tool_decision-" + audit.time.strftime("%Y-%m") + ".jsonl")
                row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(row["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
