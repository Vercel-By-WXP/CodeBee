# -*- coding: utf-8 -*-
"""HERO 反过度防御块（ARIS 家族深挖借鉴）单测：实现/修复提示词点名四形态，
且只约束「提出」不约束「检查」——评审提示词不受此块约束。

跑法：python -m unittest discover -s tests -p "test_anti_over.py" -v
"""
from __future__ import annotations

from base import BaseTest


class AntiOverdefenseTests(BaseTest):

    def test_impl_prompt_names_four_shapes(self):
        """实现步提示词点名四形态：哈希/边界分支/评分标准/顺手加固。"""
        from app.core import pipeline
        p = pipeline.CODE_IMPL_PROMPT
        for kw in ("哈希", "边界分支", "评分标准", "顺手加固"):
            self.assertIn(kw, p, kw)
        self.assertIn("反过度防御", p)

    def test_fix_prompt_has_compact_block(self):
        """修复轮带紧凑版（修复轮最易蔓延）。"""
        from app.core import pipeline
        p = pipeline.CODE_FIX_PROMPT
        self.assertIn("反过度防御", p)
        self.assertIn("哈希", p)

    def test_bounds_proposals_not_inspection(self):
        """语义边界：块内声明「只约束提出、不约束检查」且风险进总结。"""
        from app.core import pipeline
        p = pipeline.CODE_IMPL_PROMPT
        self.assertIn("不约束你「检查」的范围", p)
        self.assertIn("写进总结", p)

    def test_review_prompt_unconstrained(self):
        """评审提示词不加反过度防御块（不得收窄评审的检查面）。"""
        from app.core import pipeline
        self.assertNotIn("反过度防御", pipeline.CODE_REVIEW_PROMPT)


if __name__ == "__main__":
    import unittest
    unittest.main()
