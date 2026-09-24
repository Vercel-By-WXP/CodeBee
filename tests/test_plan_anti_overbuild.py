# -*- coding: utf-8 -*-
"""计划层反过度防御（HERO Overbuild 形态上移到编排者拆分）单测：
实现/修复提示词已反过度防御（test_anti_over），计划层此前缺位——
hard 任务多子任务额度会被拿去做没人要的缓存/重试/监控/抽象层子任务。

跑法：python -m unittest discover -s tests -p "test_plan_anti_overbuild.py" -v
"""
from __future__ import annotations

from base import BaseTest


class PlanAntiOverbuildTests(BaseTest):

    def test_prompt_forbids_defensive_subtasks(self):
        """提示词点名防御性扩展子任务四形态并给出正确出口（detail 备注）。"""
        from app.core import planner
        p = planner.CODE_PLAN_PROMPT
        self.assertIn("防御性扩展子任务", p)
        for kw in ("缓存", "重试", "监控", "抽象层"):
            self.assertIn(kw, p)
        self.assertIn("由人决定", p)   # 风险出口=备注给人，不是擅做

    def test_easy_rules_still_intact(self):
        """既有难度分流契约不回归（easy 钉死/禁止收尾子任务/hard 上限）。"""
        from app.core import planner
        p = planner.CODE_PLAN_PROMPT
        self.assertIn("easy 一律只拆 1 个子任务", p)
        self.assertIn("禁止拆出独立的核查/联调/收尾子任务", p)
        self.assertIn("只有 hard 才允许多个子任务", p)

    def test_norm_still_enforces_easy_truncation(self):
        """解析层兜底不回归：easy 多拆仍截断首步。"""
        from app.core import planner
        steps = planner._norm_subtasks({"difficulty": "easy", "subtasks": [
            {"title": "修 A"}, {"title": "加缓存"}, {"title": "整体收尾"}]})
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["title"], "修 A")


if __name__ == "__main__":
    import unittest
    unittest.main()
