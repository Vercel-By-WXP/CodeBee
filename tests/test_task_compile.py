# -*- coding: utf-8 -*-
"""统一任务编译与路由计划测试。"""
from __future__ import annotations

from base import BaseTest


class TestTaskCompile(BaseTest):
    def test_all_presets_compile_to_stable_shape(self):
        from app.core import flows, task_compile
        for flow in flows.list_flows():
            spec = task_compile.compile_task({"type": flow["id"], "goal": "测试目标"})
            self.assertEqual(spec["schema_version"], 1)
            self.assertIn(spec["engine"], flows.ENGINES)
            self.assertIn(spec["dimension"], ("writing", "coding", "reasoning", "vision"))
            self.assertIn(spec["difficulty"], task_compile.DIFFICULTIES)
            self.assertTrue(spec["capabilities"])

    def test_explicit_values_and_safe_fallback(self):
        from app.core import task_compile
        spec = task_compile.compile_task({"type": "code", "engine": "bad",
                                          "difficulty": "hard", "goal": "修复",
                                          "verify_command": "pytest -q",
                                          "attachments": ["a.png"]})
        self.assertEqual(spec["engine"], "code")
        self.assertEqual(spec["difficulty"], "hard")
        self.assertIn("verification", spec["capabilities"])
        self.assertIn("attachments", spec["capabilities"])
        self.assertEqual(task_compile.compile_task("bad input")["type"], "direct")
        self.assertEqual(task_compile.compile_task({"type": "doc", "rubric": 42})[
            "quality_dimensions"], ["准确性", "结构清晰", "表达流畅", "实用价值"])

        hard = task_compile.compile_task({"type": "novel", "threshold": 9.0})
        easy = task_compile.compile_task({"type": "novel", "threshold": 5.0})
        self.assertEqual(hard["difficulty"], "hard")
        self.assertEqual(easy["difficulty"], "easy")
        self.assertEqual(task_compile.compile_task({"type": "code", "engine": "legacy"})[
            "engine"], "code")

    def test_malformed_internal_payloads_keep_compilable_defaults(self):
        from app.core import task_compile
        self.assertEqual(task_compile.compile_task(None)["type"], "direct")
        self.assertEqual(task_compile.compile_task({"type": "doc", "rubric": object()})[
            "quality_dimensions"], ["准确性", "结构清晰", "表达流畅", "实用价值"])

    def test_compiled_difficulty_is_used_by_review_helpers(self):
        from app.core import task_compile
        hard = task_compile.compile_task({"type": "article", "threshold": 9.0})
        easy = task_compile.compile_task({"type": "article", "threshold": 5.0})
        self.assertEqual(hard["difficulty"], "hard")
        self.assertEqual(easy["difficulty"], "easy")

    def test_route_plan_is_explainable_and_keeps_fallbacks(self):
        from app.core import router, task_compile
        spec = task_compile.compile_task({"type": "code", "goal": "修复 bug"})
        agents = [
            {"id": "mock", "label": "mock", "kind": "mock", "mode": "mock"},
            {"id": "codex", "label": "Codex", "kind": "codex", "mode": "real"},
            {"id": "claude", "label": "Claude", "kind": "claude", "mode": "real"},
        ]
        plan = router.route_plan(agents, "implement", spec, {})
        self.assertEqual(plan["selected"], "codex")
        self.assertEqual(plan["fallback"][0], "claude")
        self.assertTrue(plan["candidates"][0]["reason"])

        actual = router.route_plan(
            agents, "review", spec, {}, selected=agents[2],
            participants=[agents[2], agents[0]], selection_reason="跨厂商评审")
        self.assertEqual(actual["selected"], "claude")
        self.assertEqual(actual["participants"], ["claude", "mock"])
        self.assertNotIn("claude", actual["fallback"])
        self.assertNotIn("mock", actual["fallback"])
        self.assertEqual(actual["selection_reason"], "跨厂商评审")


if __name__ == "__main__":
    import unittest
    unittest.main()
