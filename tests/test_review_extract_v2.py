# -*- coding: utf-8 -*-
"""评审分数提取分隔符扩展（批1 代码质量 09-25）单测：
「正确性 - 9 分」短横线/「＝」全角等号/「→」箭头三种真实输出形态
此前全漏——格式错被误判质量差白烧修复轮。

跑法：python -m unittest discover -s tests -p "test_review_extract_v2.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ReviewExtractV2Tests(BaseTest):

    def _x(self):
        from app.core import runner
        return runner.extract_scores_from_text

    def test_dash_separator_now_extracted(self):
        """短横线形态：正确性 - 9 分 → 全部三维命中。"""
        got = self._x()("正确性 - 9 分，可维护性 - 8 分，安全 - 7 分")
        self.assertEqual(got, {"正确性": 9.0, "可维护性": 8.0, "安全": 7.0})

    def test_fullwidth_equals_separator(self):
        """全角等号形态。"""
        got = self._x()("正确性＝9分，可维护性＝8分，安全＝7分")
        self.assertEqual(got, {"正确性": 9.0, "可维护性": 8.0, "安全": 7.0})

    def test_arrow_separator(self):
        """箭头形态（无「分」后缀）。"""
        got = self._x()("正确性 → 9，可维护性 → 8，安全 → 7")
        self.assertEqual(got, {"正确性": 9.0, "可维护性": 8.0, "安全": 7.0})

    def test_colon_forms_not_regressed(self):
        """既有形态不回归：中文冒号/英文冒号+分母/带小数。"""
        x = self._x()
        self.assertEqual(x("正确性：9 分，可维护性：8 分，安全：7 分"),
                         {"正确性": 9.0, "可维护性": 8.0, "安全": 7.0})
        self.assertEqual(x("正确性: 9/10, 可维护性: 8.5, 安全: 7"),
                         {"正确性": 9.0, "可维护性": 8.5, "安全": 7.0})

    def test_noise_still_rejected(self):
        """噪声守卫不放松：少于三维 / 越界值 / 普通行文数字不误报。"""
        x = self._x()
        self.assertEqual(x("正确性 - 9，可维护性 - 8"), {})      # 只两维
        self.assertEqual(x("A - 99，B - 88，C - 77"), {})        # 越界
        self.assertEqual(x("本文共 3 段，改了 2 处，删了 1 处。"), {})


if __name__ == "__main__":
    import unittest
    unittest.main()
