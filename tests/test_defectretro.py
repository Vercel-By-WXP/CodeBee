# -*- coding: utf-8 -*-
"""缺陷复盘（defect_retro，借鉴 zl2237/test-defect-retrospective）单测。

跑法：python -m unittest discover -s tests -p "test_defectretro.py" -v
"""
from __future__ import annotations

import os
from unittest import mock

from base import BaseTest


class RetroPromptTests(BaseTest):

    def test_prompt_has_three_lenses(self):
        from app.core import defectretro
        p = defectretro.retro_prompt("v2.3 版本")
        for sec in ("产品视角", "开发视角", "测试视角", "改进动作", "整体画像"):
            self.assertIn(sec, p)
        self.assertIn("v2.3 版本", p)   # 用户关注点注入

    def test_prompt_default_goal(self):
        from app.core import defectretro
        p = defectretro.retro_prompt("")
        self.assertIn("全量缺陷复盘", p)

    def test_prompt_no_fabrication_discipline(self):
        # 数字必须数出来、缺数据不硬写——与「不信任自报」族同向的防编造纪律
        from app.core import defectretro
        p = defectretro.retro_prompt("x")
        self.assertIn("数据未提供", p)
        self.assertIn("不要硬写报告", p)

    def test_prompt_always_returns(self):
        # 纯框架无抓取，恒有返回值（与 paihang 的 None 回落不同）
        from app.core import defectretro
        self.assertTrue(defectretro.retro_prompt(""))

    def test_flow_registered(self):
        from app.core import flows
        flow = flows.get_flow("defect_retro")
        self.assertIsNotNone(flow)
        self.assertEqual(flow["engine"], "direct")
        self.assertTrue(flow.get("builtin"))


if __name__ == "__main__":
    unittest.main()
