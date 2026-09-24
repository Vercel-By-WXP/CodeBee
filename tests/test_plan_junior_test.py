# -*- coding: utf-8 -*-
"""计划清晰度「初级工程师测试」（superpowers 借鉴②）单测：
编排者拆分的 detail 要清晰到没有项目上下文、不做隐含判断的初级工程师
也能照做不跑偏——计划可执行性的提示词契约。

跑法：python -m unittest discover -s tests -p "test_plan_junior_test.py" -v
"""
from __future__ import annotations

from base import BaseTest


class PlanJuniorTestTests(BaseTest):

    def test_prompt_states_junior_engineer_test(self):
        """提示词写明「初级工程师测试」标准与无上下文假设。"""
        from app.core import planner
        p = planner.CODE_PLAN_PROMPT
        self.assertIn("初级工程师测试", p)
        self.assertIn("没有项目上下文", p)
        self.assertIn("照做不跑偏", p)

    def test_explore_contract_still_intact(self):
        """既有「先探索再计划」契约不回归（真实路径/禁编路径）。"""
        from app.core import planner
        p = planner.CODE_PLAN_PROMPT
        self.assertIn("先探索再计划", p)
        self.assertIn("没探索过就不要凭想象编路径", p)
        self.assertIn("detail 必须点名真实存在的文件路径", p)


if __name__ == "__main__":
    import unittest
    unittest.main()
