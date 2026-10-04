import sys
from pathlib import Path

from base import BaseTest

# main.py 用 `from core import ...`，`import main` 前需把 app/ 加入 sys.path
# （对齐 test_encoding_gbk/test_state_payload 惯例——旧布局裸 import 在 discover 下必红）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

ROOT = Path(__file__).resolve().parents[1] / "app"


class TestConversationWorkspace(BaseTest):
    def _timeline(self, task_type="article", partial=False):
        from app.core import store
        task = store.create_task({"type": task_type, "title": "统一对话工作区",
                                  "goal": "整理章节结构", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="done")
        run = store.get_run(run["id"])
        run["steps"] = [{"n": 1, "role": "author", "agent": "mock",
                          "agent_label": "作者", "status": "done",
                          "output": "已整理章节结构", "summary": "已完成",
                          "partial": partial}]
        with store.LOCK:
            store._RUNS[run["id"]] = run
        import main as main_mod
        original = main_mod.store
        main_mod.store = store
        try:
            handler = main_mod.Handler.__new__(main_mod.Handler)
            holder = {}
            handler._json = lambda code, obj: holder.update(data=obj) or obj
            handler._api_run_timeline(run["id"])
            return holder["data"]
        finally:
            main_mod.store = original

    def test_timeline_includes_non_direct_task_goal_and_agent_output(self):
        data = self._timeline()
        self.assertEqual([item["kind"] for item in data["items"]], ["user", "agent"])
        self.assertEqual(data["items"][0]["text"], "整理章节结构")
        self.assertEqual(data["items"][1]["text"], "已整理章节结构")
        self.assertIsNotNone(data["result"])
        self.assertEqual(data["result"]["status"], "done")

    def test_result_preserves_partial_step_without_run_verdict(self):
        data = self._timeline(partial=True)
        self.assertTrue(data["result"]["partial"])
        self.assertIn("部分完成", data["result"]["error"])

    def test_frontend_and_chat_endpoint_are_engine_agnostic(self):
        main_text = (ROOT / "main.py").read_text(encoding="utf-8")
        ui_text = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('task.get("engine") != "direct" and not is_serial', main_text)
        self.assertIn('return !!(task && task.id === (run && run.task_id));', ui_text)
