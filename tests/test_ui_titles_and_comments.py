# -*- coding: utf-8 -*-
"""设置页标题/注释一致性回归（2026-10-11 班 G/C 巡检落地件）：
1) TAB_TITLES 漏 evalbench 键——「模型评测」子页顶栏回退显示「设置」；
2) SET_TABS 死常量与 i18n 孤儿词条「单智能体直达…」清理后不得回流；
3) pipeline.py 扫榜注释/注释型描述与实现不符（单平台残留、类型误标）勘误后不回退。

纯源码静态断言，不起服务不碰数据。
跑法：python -m unittest discover -s tests -p "test_ui_titles_and_comments.py" -v
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class UiTitlesAndCommentsTests(unittest.TestCase):

    def test_tab_titles_covers_evalbench(self):
        """「模型评测」子页在 TAB_TITLES 有键，顶栏标题不再回退「设置」。"""
        js = read("app/ui/app.js")
        self.assertIn('evalbench: "模型评测"', js)
        self.assertIn('"模型评测": "Model Bench"', read("app/ui/i18n.js"))

    def test_dead_settabs_and_orphan_i18n_stay_removed(self):
        """SET_TABS 死常量与 i18n 孤儿词条不回流；在役兄弟词条不受误伤。"""
        self.assertNotIn("SET_TABS", read("app/ui/app.js"))
        i18n = read("app/ui/i18n.js")
        self.assertNotIn("单智能体直达：目标+附件交给一个 CLI 跑完即止", i18n)
        self.assertIn("直连引擎（单智能体直达，无拆解/评审，快）", i18n)

    def test_direct_engine_comment_matches_reality(self):
        """首屏默认注释与 direct 引擎实际形态（直连模型 API）一致。"""
        self.assertIn("直连模型 API，无拆解/评审", read("app/ui/app.js"))
        self.assertNotIn("单 CLI 直达", read("app/ui/app.js"))

    def test_rank_scan_comment_covers_four_platforms(self):
        """扫榜注释与四平台实现一致（flows.py 菜单文案同口径）。"""
        py = read("app/core/pipeline.py")
        self.assertIn("抓七猫/番茄/起点/纵横四平台排行榜", py)
        self.assertNotIn("抓七猫排行榜公开数据", py)
        self.assertIn("抓取七猫/番茄/起点/纵横四平台排行榜公开数据", read("app/core/flows.py"))

    def test_defect_retro_dimension_comment_not_mislabeled(self):
        """defect_retro 注释标自身类型，不再误标「禅道工单」（禅道建的是 code 任务）。"""
        disp = read("app/core/dispatch.py")
        self.assertIn('"defect_retro": "reasoning",   # 缺陷复盘：排查定责推理向', disp)
        self.assertNotIn("禅道工单：排查定责", disp)


if __name__ == "__main__":
    unittest.main()
