import unittest
from unittest.mock import patch

from core import builtin_agent, pipeline


class BuiltinTaskToolTests(unittest.TestCase):
    def test_task_tool_is_available_when_creator_is_enabled(self):
        self.assertIn("create_task", [x["function"]["name"] for x in builtin_agent._openai_tools([builtin_agent.CREATE_TASK_SPEC])])

    def test_create_serial_novel_task(self):
        seen = {}

        def create(payload):
            seen.update(payload)
            return {"task_id": "t-child", "run_id": "r-child"}

        result = builtin_agent._tool_create_task(
            r"C:\\workspace", {
                "type": "serial_novel",
                "goal": "创建都市异能连载小说并先写十章",
                "chapters": "10",
                "words_per_chapter": "2200",
                "story_bible": "主角从底层觉醒，第一卷围绕失踪案展开。",
            }, task_creator=create)

        self.assertIn("t-child", result)
        self.assertEqual(seen["type"], "serial_novel")
        self.assertEqual(seen["serial"], {"chapters": "10", "words_per_chapter": "2200"})
        self.assertEqual(seen["story_bible"], "主角从底层觉醒，第一卷围绕失踪案展开。")

    def test_child_task_receives_only_explicit_context_and_inherited_policy(self):
        seen = {}
        sandbox = {"allowed_roots": [r"C:\\workspace"], "network": False,
                   "disabled_tools": ["write_file", "mcp__demo__danger"]}

        def create(payload):
            seen.update(payload)
            return {"task_id": "t-isolated", "run_id": "r-isolated"}

        result = builtin_agent._tool_create_task(
            r"C:\\workspace", {"type": "code", "goal": "只做独立任务",
                                 "context": "显式传入的背景"},
            task_creator=create, sandbox=sandbox)

        self.assertIn("t-isolated", result)
        self.assertEqual(seen["context"], "显式传入的背景")
        self.assertEqual(seen["sandbox"], sandbox)
        self.assertNotIn("parent_messages", seen)

    def test_create_task_requires_creator(self):
        result = builtin_agent._tool_create_task(
            r"C:\\workspace", {"type": "serial_novel", "goal": "写小说"})
        self.assertIn("不支持发起新任务", result)

    def test_pipeline_creates_and_enqueues_child_task(self):
        task = {"id": "t-child", "title": "新连载"}
        run = {"id": "r-child"}
        with patch.object(pipeline.store, "create_task", return_value=task) as create_task, \
             patch.object(pipeline.store, "create_run", return_value=run) as create_run, \
             patch.object(pipeline.store, "update_task_status") as update_status, \
             patch.object(pipeline.jobs, "enqueue") as enqueue:
            result = pipeline._create_task_from_builtin({"type": "serial_novel", "goal": "新连载"})

        self.assertEqual(result, {"task_id": "t-child", "run_id": "r-child"})
        create_task.assert_called_once()
        create_run.assert_called_once_with("orchestration", "新连载", task_id="t-child")
        update_status.assert_called_once_with("t-child", "queued")
        enqueue.assert_called_once_with({"kind": "orchestration", "run_id": "r-child", "task_id": "t-child"})

    def test_pipeline_closes_child_task_when_enqueue_fails(self):
        task = {"id": "t-child", "title": "新连载"}
        run = {"id": "r-child"}
        with patch.object(pipeline.store, "create_task", return_value=task), \
             patch.object(pipeline.store, "create_run", return_value=run), \
             patch.object(pipeline.store, "update_task_status") as update_status, \
             patch.object(pipeline.store, "update_run") as update_run, \
             patch.object(pipeline.jobs, "enqueue", side_effect=RuntimeError("busy")):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                pipeline._create_task_from_builtin({"type": "serial_novel", "goal": "新连载"})

        update_run.assert_called_once()
        self.assertEqual(update_run.call_args.kwargs["status"], "failed")
        self.assertEqual(update_status.call_args_list[-1].args, ("t-child", "failed"))


if __name__ == "__main__":
    unittest.main()
