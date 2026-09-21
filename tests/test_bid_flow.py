# -*- coding: utf-8 -*-
"""标书编制（bid_doc）任务类型单测——BidCraft 标书匠灵感（2026-09-22）。

跑法：python -m unittest discover -s tests -p "test_bid_flow.py" -v
"""
from __future__ import annotations

from base import BaseTest


class BidFlowTests(BaseTest):
    def test_flow_registered_with_full_meta(self):
        from app.core import flows
        f = flows.get_flow("bid_doc")
        self.assertIsNotNone(f)
        self.assertTrue(f["builtin"])
        self.assertEqual(f["engine"], "review")
        self.assertEqual(f["manuscript"], "bid.md")
        self.assertGreaterEqual(len(f["rubric"]), 4)
        self.assertIn("应答完整性", f["rubric"])
        self.assertEqual(f["threshold"], 7.5)   # 标书合规要求高，门槛上调
        self.assertTrue(f["goal_hint"] and f["note"])

    def test_contract_wired_with_bid_rules(self):
        from app.core import pipeline
        role = pipeline._content_role({"type": "bid_doc"})
        self.assertEqual(role, "投标经理")
        c = pipeline._content_contract({"type": "bid_doc"})
        self.assertIn("评分点", c)              # 逐条对齐评分标准
        self.assertIn("待补", c)                # 缺失资质不虚构
        self.assertIn("废标", c)                # 废标风险自查

    def test_task_compile_routes_bid(self):
        from app.core import task_compile
        spec = task_compile.compile_task({"type": "bid_doc", "goal": "投xx标"})
        self.assertEqual(spec["engine"], "review")
        self.assertEqual(spec["dimension"], "writing")
        self.assertIn(spec["quality_dimensions"][0],
                      ["应答完整性", "合规符合度", "方案针对性",
                       "评分点覆盖", "商务清晰度"])


if __name__ == "__main__":
    import unittest
    unittest.main()
