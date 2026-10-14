"""CLI activity extraction is tolerant, ordered and does not invent success."""
import json
import unittest

from app.core.tool_activity import parse_tool_activity


class ToolActivityTests(unittest.TestCase):
    def test_codex_merges_updates_and_keeps_message_order(self):
        events = [
            {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Checking files"}},
            {"type": "item.started", "item": {"id": "c", "type": "command_execution", "command": "git status"}},
            {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": "git status",
                                               "exit_code": 1, "aggregated_output": "failure"}},
            {"type": "item.completed", "item": {"id": "mcp", "type": "mcp_tool_call", "server": "context7",
                                               "tool": "query", "arguments": {"query": "docs"}, "result": {"ok": True}}},
        ]
        result = parse_tool_activity("partial line\n" + "\n".join(map(json.dumps, events)) + '\n{"type":', "done")
        self.assertEqual(len(result["activities"]), 2)
        command, mcp = result["activities"]
        self.assertEqual((command["status"], command["exit_code"], command["output"]), ("failed", 1, "failure"))
        self.assertEqual((mcp["name"], mcp["summary"]), ("context7.query", "docs"))
        self.assertEqual([block["kind"] for block in result["transcript"]], ["message", "tool", "tool"])

    def test_claude_tool_results_are_matched_by_id(self):
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Reading"},
                {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "npm test"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "b", "is_error": True, "content": "failed"},
            ]}},
        ]
        result = parse_tool_activity("\n".join(map(json.dumps, events)), "done")
        self.assertEqual([a["status"] for a in result["activities"]], ["unknown", "failed"])
        self.assertEqual(result["activities"][0]["summary"], "a.py")
        self.assertEqual(result["activities"][0]["kind"], "read")

    def test_opencode_and_builtin_activity(self):
        events = [
            {"type": "tool_use", "part": {"callID": "o", "tool": "read", "state": {"status": "running", "input": {"path": "a.md"}}}},
            {"type": "tool_use", "part": {"callID": "o", "tool": "read", "state": {"status": "completed", "output": "ok"}}},
        ]
        result = parse_tool_activity("\n".join(map(json.dumps, events)) + "\n[工具] read_file → hello\n【活动】read_file b.py", "running",
                                     allow_text_activity=True)
        self.assertEqual(len(result["activities"]), 3)
        self.assertEqual(result["activities"][0]["status"], "completed")
        self.assertEqual(result["activities"][0]["summary"], "a.md")
        self.assertEqual(result["activities"][2]["summary"], "b.py")

    def test_cli_output_cannot_claim_builtin_activity(self):
        result = parse_tool_activity("[工具] delete_file → completed\n【活动】delete_file secret.txt", "done")
        self.assertEqual(result["activities"], [])

    def test_limits_and_malformed_events(self):
        lines = ["...(已截断)...", "[]", '{"type":"assistant","message":null}',
                 '{"type":"assistant","message":{"content":"text"}}', '{"type":"tool_use","part":null}']
        lines += [json.dumps({"type": "item.started", "item": {"id": str(i), "type": "command_execution",
                                                               "command": "echo " + "x" * 7000}}) for i in range(105)]
        result = parse_tool_activity("\n".join(lines), "done")
        self.assertTrue(result["activities_truncated"])
        self.assertEqual(len(result["activities"]), 100)
        self.assertEqual(result["activities"][0]["id"], "5")
        self.assertEqual(len(result["activities"][0]["input"]), 6000)
        self.assertEqual(len(result["activities"][0]["summary"]), 240)
        self.assertTrue(all(a["status"] == "unknown" for a in result["activities"]))


if __name__ == "__main__":
    unittest.main()
