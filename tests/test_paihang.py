# -*- coding: utf-8 -*-
"""扫榜选材（rank_scan，借鉴 oh-story 扫榜）单测——四源聚合版。

跑法：python -m unittest discover -s tests -p "test_paihang.py" -v
"""
from __future__ import annotations

import os
from unittest import mock

from base import BaseTest

QM_SAMPLE = ("<a>盖世神医</a><a>逍遥四公子</a><a>加入书架</a><a>立即阅读</a>")
FQ_SAMPLE = ("<a>西方奇幻</a><a>都市高武</a><a>帮助中心</a>")
QD_SAMPLE = ("<a>月票榜</a><a>阅读榜</a><a>大家都在搜</a><a>夜无疆</a>"
             "<a>诡秘之主</a><a>男生小说排行榜</a>")
ZH_SAMPLE = ("<a>月票</a><a>人气</a><a>万字</a><a>剑来</a><a>太荒吞天诀</a>")


class FetchTests(BaseTest):

    def test_qimao_parses_and_filters(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "_curl_text", return_value=QM_SAMPLE):
            items = paihang.fetch_qimao_rank()
        self.assertIn("盖世神医", items)
        self.assertNotIn("加入书架", items)   # UI 噪音过滤

    def test_fanqie_parses(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "_curl_text", return_value=FQ_SAMPLE):
            items = paihang.fetch_fanqie_rank()
        self.assertIn("西方奇幻", items)
        self.assertIn("都市高武", items)
        self.assertNotIn("帮助中心", items)

    def test_qidian_mobile_parses_and_filters(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "_curl_text", return_value=QD_SAMPLE):
            items = paihang.fetch_qidian_rank()
        self.assertIn("夜无疆", items)
        self.assertIn("诡秘之主", items)
        for noise in ("月票榜", "阅读榜", "大家都在搜", "男生小说排行榜"):
            self.assertNotIn(noise, items)   # 榜单 tab 名过滤

    def test_zongheng_parses_and_filters(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "_curl_text", return_value=ZH_SAMPLE):
            items = paihang.fetch_zongheng_rank()
        self.assertIn("剑来", items)
        self.assertIn("太荒吞天诀", items)
        for noise in ("月票", "人气", "万字"):
            self.assertNotIn(noise, items)   # 统计标签过滤

    def test_fail_returns_empty(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "_curl_text", return_value=""):
            self.assertEqual(paihang.fetch_qimao_rank(), [])
            self.assertEqual(paihang.fetch_fanqie_rank(), [])
            self.assertEqual(paihang.fetch_qidian_rank(), [])
            self.assertEqual(paihang.fetch_zongheng_rank(), [])

    def test_aggregate_merges_four_sources(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_qimao_rank", return_value=["书A"]), \
             mock.patch.object(paihang, "fetch_fanqie_rank", return_value=["书A", "书B"]), \
             mock.patch.object(paihang, "fetch_qidian_rank", return_value=["书C"]), \
             mock.patch.object(paihang, "fetch_zongheng_rank", return_value=[]):
            items = paihang.fetch_rank_items()
        self.assertEqual(items, ["书A", "书B", "书C"])   # 跨源去重、元老源在前


class RankScanPromptTests(BaseTest):

    def _make(self, qm, fq, qd=None, zh=None):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_qimao_rank", return_value=qm), \
             mock.patch.object(paihang, "fetch_fanqie_rank", return_value=fq), \
             mock.patch.object(paihang, "fetch_qidian_rank", return_value=qd or []), \
             mock.patch.object(paihang, "fetch_zongheng_rank", return_value=zh or []):
            return paihang.rank_scan_prompt("都市高武")

    def test_dual_source_merged(self):
        p = self._make(["盖世神医"], ["逆天邪神"])
        self.assertIn("七猫排行榜素材", p)
        self.assertIn("番茄排行榜素材", p)
        self.assertIn("差异化切入", p)

    def test_quad_source_merged(self):
        p = self._make(["盖世神医"], ["逆天邪神"], qd=["夜无疆"], zh=["剑来"])
        for sec in ("七猫排行榜素材", "番茄排行榜素材", "起点排行榜素材", "纵横排行榜素材"):
            self.assertIn(sec, p)

    def test_both_empty_returns_none(self):
        self.assertIsNone(self._make([], []))

    def test_single_new_source_survives(self):
        # 元老双源全挂、新源存活 → 仍有素材不回落
        p = self._make([], [], qd=["夜无疆"])
        self.assertIn("起点排行榜素材", p)

    def test_dedup_across_sources(self):
        p = self._make(["书A"], ["书A", "书B"], qd=["书B", "书C"])
        # 书A/书B 去重后只出现一次（源顺序即优先级）
        self.assertEqual(p.count("书A"), 1)
        self.assertEqual(p.count("书B"), 1)
        self.assertIn("书C", p)

    def test_flow_registered(self):
        from app.core import flows
        flow = flows.get_flow("rank_scan")
        self.assertIsNotNone(flow)
        self.assertEqual(flow["engine"], "direct")


if __name__ == "__main__":
    unittest.main()
