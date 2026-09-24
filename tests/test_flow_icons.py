# -*- coding: utf-8 -*-
"""预置流程图标完整性：图标必须在 index.html 精灵表有 symbol 定义（缺失=菜单
图标空白，09-25 巡检发现 defect_retro 的 i-clipboard 未定义），且互不重复
（重复=类型菜单两个流程同图标难分辨，09-25 发现 bid_doc 与 research 撞车）。

跑法：python -m unittest discover -s tests -p "test_flow_icons.py" -v
"""
from __future__ import annotations

import re
from pathlib import Path

from base import BaseTest

_HTML = Path(__file__).resolve().parent.parent / "app" / "ui" / "index.html"


class FlowIconsTests(BaseTest):

    def _defined(self):
        html = _HTML.read_text(encoding="utf-8")
        return set(re.findall(r'symbol id="(i-[a-z-]+)"', html))

    def test_all_flow_icons_defined(self):
        """每个预置流程的图标都在精灵表有定义。"""
        from app.core.flows import BUILTIN_FLOWS

        defined = self._defined()
        for f in BUILTIN_FLOWS:
            self.assertIn(f["icon"], defined,
                          "流程 %s 图标 %s 未在精灵表定义" % (f["id"], f["icon"]))

    def test_flow_icons_unique(self):
        """预置流程图标互不重复。"""
        from app.core.flows import BUILTIN_FLOWS

        icons = [f["icon"] for f in BUILTIN_FLOWS]
        self.assertEqual(len(icons), len(set(icons)),
                         "流程图标重复：%s" % sorted(icons))


if __name__ == "__main__":
    import unittest
    unittest.main()
