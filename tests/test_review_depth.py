# -*- coding: utf-8 -*-
"""评审深度随 diff 规模分级（pr-af 借鉴）单测：小改动不逼评审凑字数，
大变更先概览后深看高风险区；中等规模不给指引（默认深度）；
空 diff（无法获取变更）不误导评审员放松。

跑法：python -m unittest discover -s tests -p "test_review_depth.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ReviewDepthTests(BaseTest):

    def test_small_diff_gets_fast_track(self):
        """小 diff（<40 行）→ 快速评审指引，聚焦正确性不凑字数。"""
        from app.core import pipeline
        note = pipeline._review_depth_note("a\n" * 10)
        self.assertIn("小改动快速评审", note)
        self.assertIn("10 行", note)

    def test_large_diff_gets_risk_first(self):
        """大 diff（≥600 行）→ 概览+高风险区深看指引。"""
        from app.core import pipeline
        note = pipeline._review_depth_note("a\n" * 700)
        self.assertIn("大变更评审", note)
        self.assertIn("高风险区", note)

    def test_medium_diff_untouched(self):
        """中等规模 → 无指引（默认深度）。"""
        from app.core import pipeline
        self.assertEqual(pipeline._review_depth_note("a\n" * 100), "")

    def test_empty_diff_no_guidance(self):
        """空 diff → 不给快速评审指引（无法获取变更时应谨慎，不该说改动很小）。"""
        from app.core import pipeline
        self.assertEqual(pipeline._review_depth_note(""), "")
        self.assertEqual(pipeline._review_depth_note(None), "")


if __name__ == "__main__":
    import unittest
    unittest.main()
