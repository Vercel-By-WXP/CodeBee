import unittest
from unittest.mock import patch

import mcp_server


class McpServerTests(unittest.TestCase):
    def test_create_task_rejects_invalid_contract_types(self):
        result = mcp_server._tool_create_task({
            "goal": "test", "type": "doc", "workdir": ".",
            "acceptance_criteria": "must be a list",
        })
        self.assertTrue(result.get("isError"))
        self.assertIn("acceptance_criteria", result.get("content", ""))

        result = mcp_server._tool_create_task({
            "goal": "test", "type": "doc", "workdir": ".",
            "approval_required": "false",
        })
        self.assertTrue(result.get("isError"))
        self.assertIn("approval_required", result.get("content", ""))

    def test_search_knowledge_rejects_invalid_limit(self):
        result = mcp_server._tool_search({"query": "term", "limit": "bad"})
        self.assertTrue(result.get("isError"))
        self.assertIn("limit", result.get("content", ""))

    def test_create_task_closes_partial_run_when_enqueue_fails(self):
        calls = []

        class FakeStore:
            def create_task(self, payload):
                return {"id": "t1", "title": payload["title"]}

            def create_run(self, *args, **kwargs):
                return {"id": "r1"}

            def update_task_status(self, task_id, status):
                calls.append(("task", task_id, status))

            def update_run(self, run_id, **kwargs):
                calls.append(("run", run_id, kwargs))

        fake = FakeStore()
        with patch("core.store.create_task", side_effect=fake.create_task), \
             patch("core.store.create_run", side_effect=fake.create_run), \
             patch("core.store.update_task_status", side_effect=fake.update_task_status), \
             patch("core.store.update_run", side_effect=fake.update_run), \
             patch.object(mcp_server, "_enqueue", side_effect=RuntimeError("boom")):
            result = mcp_server._tool_create_task({"goal": "test", "type": "doc", "workdir": "."})
        self.assertTrue(result.get("isError"))
        self.assertIn(("task", "t1", "failed"), calls)
        self.assertTrue(any(x[0] == "run" and x[1] == "r1" and x[2].get("status") == "failed"
                            for x in calls))


if __name__ == "__main__":
    unittest.main()
