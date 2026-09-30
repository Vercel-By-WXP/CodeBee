# -*- coding: utf-8 -*-
"""扫榜报告契约：逐条 A-E 跨平台证据分级（借鉴 career-ops 分级报告交付形态）。

分级只认四源素材实际出现情况，防止单源孤证被模型放大成「大热题材」。
跑法：python -m unittest discover -s tests -p "test_rank_scan_grading.py" -v
"""
from __future__ import annotations

from base import BaseTest


class RankScanGradingTests(BaseTest):

    def _prompt(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_qimao_rank", return_value=["书A"]), \
             mock.patch.object(paihang, "fetch_fanqie_rank", return_value=["书B"]), \
             mock.patch.object(paihang, "fetch_qidian_rank", return_value=["书C"]), \
             mock.patch.object(paihang, "fetch_zongheng_rank", return_value=["书D"]):
            return paihang.rank_scan_prompt("都市高武")

    def test_rubric_tied_to_cross_platform_evidence(self):
        # 五档口径全部与素材出现情况挂钩（A/D 为档位边界）
        p = self._prompt()
        for needle in ("A=三平台", "B=两平台", "C=单平台多次出现",
                       "D=单平台仅一次", "E=素材无直接证据"):
            self.assertIn(needle, p)

    def test_every_conclusion_tagged(self):
        p = self._prompt()
        self.assertIn("每一项结论末尾标注【证据X】", p)
        self.assertIn("X 为 A-E 之一", p)

    def test_conservative_rules(self):
        # 组合取较低档、拿不准降不升——证据不足时宁降勿升
        p = self._prompt()
        self.assertIn("较低档取值", p)
        self.assertIn("降一档、不升级", p)

    def test_legend_required_first_line(self):
        # 报告自带图例，读者不依赖外部文档即可读懂分级
        p = self._prompt()
        self.assertIn("报告第一行给图例", p)

    def test_report_sections_unchanged(self):
        # 三段产出结构不因加分级而变形
        p = self._prompt()
        for sec in ("热门题材 Top3", "高频人设/套路总结", "差异化切入建议"):
            self.assertIn(sec, p)

    def test_flow_note_mentions_grading(self):
        from app.core import flows
        note = flows.get_flow("rank_scan")["note"]
        self.assertIn("A-E 跨平台证据分级", note)


if __name__ == "__main__":
    unittest.main()
