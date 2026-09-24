# -*- coding: utf-8 -*-
"""交付物从简（claude-token-efficient 借鉴）单测：实现步提示词要求回复
不写开场白、不复述任务、直接给干货——输出 token 也是成本。

跑法：python -m unittest discover -s tests -p "test_output_slim.py" -v
"""
from __future__ import annotations

from base import BaseTest


class OutputSlimTests(BaseTest):

    def test_impl_prompt_states_slim_output(self):
        """实现步提示词写明交付物从简纪律（开场白/复述/干货）。"""
        from app.core import pipeline
        p = pipeline.CODE_IMPL_PROMPT
        self.assertIn("交付物从简", p)
        self.assertIn("开场白", p)
        self.assertIn("干货", p)

    def test_summary_contract_still_intact(self):
        """既有「3-5 句总结」契约不回归。"""
        from app.core import pipeline
        self.assertIn("3-5 句话总结", pipeline.CODE_IMPL_PROMPT)


if __name__ == "__main__":
    import unittest
    unittest.main()
