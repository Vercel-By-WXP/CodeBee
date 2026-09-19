# -*- coding: utf-8 -*-
"""扫榜选材（rank_scan，借鉴 oh-story 扫榜）单测。

跑法：python -m unittest discover -s tests -p "test_paihang.py" -v
"""
from __future__ import annotations

from base import BaseTest

HTML_SAMPLE = (
    "<html><body>"
    '<a title="盖世神医">盖世神医</a>'
    '<a title="逍遥四公子">逍遥四公子</a>'
    "<a>加入书架</a><a>立即阅读</a>"
    '<span>都市高武</span>'
    "</body></html>")


class PaihangTests(BaseTest):

    def test_fetch_parses_and_filters_noise(self):
        from unittest import mock
        from app.core import paihang
        import app.core.runner as _runner_mod
        with mock.patch.object(_runner_mod, "run_process",
                               return_value={"ok": True, "stdout": HTML_SAMPLE}):
            items = paihang.fetch_rank_items()
        self.assertIn("盖世神医", items)
        self.assertIn("逍遥四公子", items)
        self.assertNotIn("加入书架", items)   # UI 噪音被过滤
        self.assertNotIn("立即阅读", items)

    def test_fetch_fail_returns_empty(self):
        from unittest import mock
        from app.core import paihang
        import app.core.runner as _runner_mod
        with mock.patch.object(_runner_mod, "run_process",
                               return_value={"ok": False, "stdout": ""}):
            self.assertEqual(paihang.fetch_rank_items(), [])

    def test_rank_scan_prompt_none_without_data(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_rank_items", return_value=[]):
            self.assertIsNone(paihang.rank_scan_prompt("都市"))

    def test_rank_scan_prompt_contains_material_and_goal(self):
        from unittest import mock
        from app.core import paihang
        with mock.patch.object(paihang, "fetch_rank_items",
                               return_value=["盖世神医", "逍遥四公子"]):
            p = paihang.rank_scan_prompt("都市高武")
        self.assertIn("盖世神医", p)
        self.assertIn("都市高武", p)
        self.assertIn("差异化切入", p)

    def test_flow_registered(self):
        from app.core import flows
        flow = flows.get_flow("rank_scan")
        self.assertIsNotNone(flow)
        self.assertEqual(flow["engine"], "direct")


if __name__ == "__main__":
    unittest.main()
