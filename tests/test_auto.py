# -*- coding: utf-8 -*-
"""智能编排模式测试：路由器、规划器退化、自动修复循环、换将。"""
from __future__ import annotations

from base import BaseTest


class TestRouter(BaseTest):
    def runTest(self):
        from app.core import router
        agents = self.mock_agents() + [
            {"id": "x-cli", "label": "X", "kind": "codex", "mode": "real", "command": "x"},
            {"id": "y-cli", "label": "Y", "kind": "claude", "mode": "real", "command": "y"},
        ]
        stats = {"x-cli": {"code": {"runs": 4, "wins": 4}},
                 "y-cli": {"code": {"runs": 4, "wins": 0}}}
        pick, reason = router.pick(agents, "implement", "code", stats)
        self.assertEqual(pick["id"], "x-cli")            # 历史全胜者胜出
        self.assertIn("历史", reason)
        # 评审者必须跨厂商：实现者是 x-cli 时不得再选 x-cli
        rev, note = router.pick_reviewer(agents, pick, "code", stats)
        self.assertEqual(rev["id"], "y-cli")
        self.assertTrue(note.startswith("跨厂商评审（claude ≠ codex）"))
        # 无历史时也不选 mock（真实智能体基线更高）
        pick2, _ = router.pick(agents, "implement", "code", {})
        self.assertEqual(pick2["id"], "x-cli")  # 同基线取第一个真实者


class TestPlannerFallback(BaseTest):
    def runTest(self):
        from app.core import planner
        task = {"goal": "做个功能", "context": "", "verify_command": ""}
        # mock / 解析失败 → 模板单步
        plan = planner.make_code_plan(task, {"id": "mock-a", "mode": "mock"}, ".")
        self.assertEqual(plan["source"], "template")
        self.assertEqual(len(plan["steps"]), 1)
        # LLM 输出解析
        steps = planner._norm_subtasks({"subtasks": [
            {"title": "A", "detail": "do a"}, {"title": "B", "detail": "do b"}]})
        self.assertEqual(len(steps), 2)
        self.assertIsNone(planner._norm_subtasks({"foo": 1}))
        workflow = planner._plan_workflow({"workflow": {
            "review_required": False, "max_repair_rounds": 9,
            "allow_switch": True}})
        self.assertEqual(workflow, {"review_required": False,
                                    "max_repair_rounds": 2,
                                    "allow_switch": True})
        self.assertIsNone(planner._plan_workflow({"workflow": "bad"}))
        novel = planner.make_novel_plan(
            {"threshold": 7.0, "rounds": 2, "rubric": ["情节"]},
            {"id": "mock-a", "label": "A"}, [{"id": "mock-b", "label": "B"}])
        self.assertGreaterEqual(len(novel["steps"]), 3)


class TestAutoCodeRepairLoop(BaseTest):
    def runTest(self):
        from app.core import pipeline, store
        # 验证永远失败：应触发 2 轮自动修复 + 换将，最终仍失败但记录完整
        task = store.create_task({
            "type": "code", "title": "修复循环", "goal": "重构并发架构并修复安全问题",
            "workdir": str(self.workdir), "mode": "auto",
            "verify_command": "exit 1",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertFalse(v["pass"])
        self.assertTrue(v["switched"])                    # 触发了换将
        kinds = [r["kind"] for r in v["repairs"]]
        self.assertIn("repair", kinds)                    # 有修复轮
        self.assertIn("switch", kinds)                    # 有换将轮
        roles = [s["role"] for s in run["steps"]]
        self.assertTrue(any(r.startswith("fix-r") for r in roles))
        self.assertTrue(any(r.startswith("implement") for r in roles))
        self.assertIn("plan", roles)


class TestAutoCodeHappyPath(BaseTest):
    def runTest(self):
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "一次通过", "goal": "g",
            "workdir": str(self.workdir), "mode": "auto",
            "verify_command": "exit 0",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(v["pass"])
        self.assertEqual(len(v["repairs"]), 1)            # 只有 initial 一轮，无修复
        self.assertFalse(v["switched"])
        self.assertTrue(v["review_skipped"])
        self.assertEqual(v["workflow"]["planning"], "inline")
        self.assertEqual(v["workflow"]["reviewers"], 0)
        roles = [s["role"] for s in run["steps"]]
        self.assertNotIn("plan", roles)
        self.assertNotIn("review", roles)
        self.assertEqual(roles, ["implement", "verify"])


class TestAutoNovel(BaseTest):
    def runTest(self):
        from app.core import pipeline, store
        task = store.create_task({
            "type": "novel", "title": "智能小说", "goal": "写开篇",
            "workdir": str(self.workdir), "mode": "auto",
            "rounds": 2, "threshold": 7.0,
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(v["publishable"])
        self.assertEqual(v["mode"], "auto")
        self.assertIn("author", run["route"])             # 记录了路由依据
        self.assertIn("critics", run["route"])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
