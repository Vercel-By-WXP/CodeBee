"""Built-in chat tools expose configured capabilities without widening task access."""
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import builtin_agent, policy


class BuiltinCapabilityTests(unittest.TestCase):
    def test_skills_require_enabled_entry_and_disambiguate_name(self):
        rows = [
            {"id": "skill:a", "name": "review", "enabled": True, "installed": True, "prompt": "A"},
            {"id": "skill:b", "name": "review", "enabled": True, "installed": True, "prompt": "B"},
            {"id": "skill:off", "name": "hidden", "enabled": False, "installed": True, "prompt": "NO"},
        ]
        with patch("app.core.plugins.capabilities_view", return_value={"skills": rows}):
            self.assertIn("skill:a", builtin_agent._tool_list_skills())
            self.assertNotIn("skill:off", builtin_agent._tool_list_skills())
            self.assertIn("skill:a, skill:b", builtin_agent._tool_use_skill({"name": "review"}))
            self.assertEqual(builtin_agent._tool_use_skill({"name": "skill:b"}), "B")
            self.assertIn("未找到已启用", builtin_agent._tool_use_skill({"name": "hidden"}))

    def test_long_skill_can_be_read_in_following_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "SKILL.md"
            source.write_text("a" * 20000 + "TAIL", encoding="utf-8")
            rows = [{"id": "skill:long", "name": "long", "enabled": True,
                     "installed": True, "source": str(source)}]
            with patch("app.core.plugins.capabilities_view", return_value={"skills": rows}):
                first = builtin_agent._tool_use_skill({"name": "long"})
                second = builtin_agent._tool_use_skill({"name": "long", "offset": "20000"})
            self.assertIn("offset=20000", first)
            self.assertIn("TAIL", second)
            self.assertNotIn("offset=", second)

    def test_web_search_respects_network_and_deadline(self):
        with patch("app.core.builtin_agent.urllib.request.urlopen") as open_url:
            self.assertIn("禁止联网", builtin_agent._tool_web_search({"query": "query"},
                         sandbox={"network": False}))
            self.assertIn("时限已到", builtin_agent._tool_web_search({"query": "query"},
                         deadline=time.monotonic() - 1))
            open_url.assert_not_called()
            open_url.return_value.__enter__.return_value = io.BytesIO(
                b"<rss><channel><item><title>Found</title><link>https://example.com</link>"
                b"<description>Summary</description></item></channel></rss>")
            result = builtin_agent._tool_web_search({"query": "a b"}, sandbox={"network": True})
            self.assertIn("Found\nhttps://example.com\nSummary", result)
            self.assertIn("q=a%20b", open_url.call_args.args[0].full_url)
            open_url.side_effect = OSError("offline")
            self.assertIn("网页搜索失败", builtin_agent._tool_web_search({"query": "query"}))

    def test_task_output_obeys_workspace_and_narrower_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            other = root / "other"
            other.mkdir()
            task = {"title": "Example", "status": "done", "workdir": str(other)}
            run = {"id": "r1", "status": "done", "steps": [
                {"n": 1, "status": "done", "output": "result"}]}
            sandbox = policy.normalize_sandbox({"allowed_roots": [str(allowed)]}, str(root))
            with patch("app.core.store.get_task", return_value=task), \
                 patch("app.core.store.task_runs", return_value=[run]) as runs:
                denied = builtin_agent._exec_tool(str(root), "read_task_output",
                                                  {"task_id": "t1"}, sandbox=sandbox)
                self.assertIn("沙箱拒绝", denied)
                runs.assert_not_called()
                task["workdir"] = str(allowed)
                result = builtin_agent._exec_tool(str(root), "read_task_output",
                                                  {"task_id": "t1"}, sandbox=sandbox)
                self.assertIn("步骤 1（done）：result", result)
                task["workdir"] = str(root.parent)
                self.assertIn("不在当前工作目录", builtin_agent._exec_tool(
                    str(root), "read_task_output", {"task_id": "t1"}, sandbox=sandbox))


if __name__ == "__main__":
    unittest.main()
