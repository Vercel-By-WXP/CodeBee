# -*- coding: utf-8 -*-
"""扫榜选材（rank_scan，借鉴 oh-story 扫榜）单测——双源聚合版。

跑法：python -m unittest discover -s tests -p "test_paihang.py" -v
"""
from __future__ import annotations

import os
from unittest import mock

from base import BaseTest

QM_SAMPLE = ("<a>盖世神医</a><a>逍遥四公子</a><a>加入书架</a><a>立即阅读</a>")
FQ_SAMPLE = ("<a>西方奇幻</a><a>都市高武</a><a>帮助中心</a>")


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

    def test_fail_returns_empty(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "_curl_text", return_value=""):
            self.assertEqual(paihang.fetch_qimao_rank(), [])
            self.assertEqual(paihang.fetch_fanqie_rank(), [])


class RankScanPromptTests(BaseTest):

    def _make(self, qm, fq):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_qimao_rank", return_value=qm), \
             mock.patch.object(paihang, "fetch_fanqie_rank", return_value=fq):
            return paihang.rank_scan_prompt("都市高武")

    def test_dual_source_merged(self):
        from unittest import mock
        from app.core import paihang
        p = self._make(["盖世神医"], ["逆天邪神"])
        self.assertIn("七猫排行榜素材", p)
        self.assertIn("番茄排行榜素材", p)
        self.assertIn("差异化切入", p)

    def test_both_empty_returns_none(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_qimao_rank", return_value=[]), \
             mock.patch.object(paihang, "fetch_fanqie_rank", return_value=[]):
            self.assertIsNone(paihang.rank_scan_prompt("都市"))

    def test_dedup_across_sources(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_qimao_rank", return_value=["书A"]), \
             mock.patch.object(paihang, "fetch_fanqie_rank", return_value=["书A", "书B"]):
            p = paihang.rank_scan_prompt("都市")
        # 书A 去重后只出现一次（七猫在前）
        self.assertEqual(p.count("书A"), 1)
        self.assertIn("书B", p)

    def test_flow_registered(self):
        from app.core import flows
        flow = flows.get_flow("rank_scan")
        self.assertIsNotNone(flow)
        self.assertEqual(flow["engine"], "direct")


if __name__ == "__main__":
    unittest.main()
