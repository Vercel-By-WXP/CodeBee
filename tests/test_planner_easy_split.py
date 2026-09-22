# -*- coding: utf-8 -*-
"""easy 任务计划单子任务钉死（编排者拆分粒度按难度分流）单测。

跑法：python -m unittest discover -s tests -p "test_planner_easy_split.py" -v

契约：难度判定先行，easy 一律只拆 1 个子任务（单步做完+自检即收尾）；
「粒度可独立验证/最后一步联调收尾」仅对 hard 生效。提示词要求之外，
_norm_subtasks 在解析层兜底——编排者输出 easy 却多拆时截断只留首步。
"""
from __future__ import annotations

from base import BaseTest


def _plan(difficulty, titles):
    subs = [{"title": t, "detail": "d"} for t in titles]
    return {"difficulty": difficulty, "subtasks": subs}


class PlannerEasySplitTests(BaseTest):
    def _norm(self, data):
        from app.core import planner
        return planner._norm_subtasks(data)

    def test_easy_multi_subtasks_truncated_to_one(self):
        """easy 多拆（修复→核查→收尾）→ 只留首步，收尾类子任务不进执行链。"""
        steps = self._norm(_plan("easy", ["修复空指针", "核查边界", "整体联调收尾"]))
        self.assertIsNotNone(steps)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["title"], "修复空指针")

    def test_hard_multi_subtasks_kept(self):
        """hard 保留多子任务拆分（联调收尾要求照旧）。"""
        titles = ["改 A 模块", "改 B 模块", "整体联调收尾"]
        steps = self._norm(_plan("hard", titles))
        self.assertEqual([s["title"] for s in steps], titles)

    def test_difficulty_missing_not_truncated(self):
        """旧计划/未给难度 → 不截断（向后兼容）。"""
        titles = ["第一步", "收尾"]
        steps = self._norm({"subtasks": [{"title": t} for t in titles]})
        self.assertEqual(len(steps), 2)

    def test_easy_prompt_rules_present(self):
        """提示词含难度分流规则：easy 单子任务、宁易勿难、联调收尾仅 hard。"""
        from app.core import planner
        p = planner.CODE_PLAN_PROMPT
        self.assertIn("先判定任务难度", p)
        self.assertIn("easy 一律只拆 1 个子任务", p)
        self.assertIn("宁易勿难", p)
        self.assertIn("禁止拆出独立的核查/联调/收尾子任务", p)
        self.assertIn("只有 hard 才允许多个子任务", p)


if __name__ == "__main__":
    import unittest
    unittest.main()
