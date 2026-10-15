# -*- coding: utf-8 -*-
"""扫榜任务描述必须与当前四源聚合实现保持一致。"""

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class RankScanCopyTests(TestCase):
    def test_flow_description_names_all_rank_sources(self):
        app_js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        i18n_js = (ROOT / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('t("四平台选材 → 评审打磨 → 标准输入 → 新开连载")', app_js)
        self.assertIn('"四平台选材 → 评审打磨 → 标准输入 → 新开连载": '
                      '"Four-platform scan → review and revise → writing brief → new serial"',
                      i18n_js)
        self.assertNotIn("抓双平台榜", app_js + i18n_js)
