# -*- coding: utf-8 -*-
"""市场来源文案与六源对账（2026-10-06 第 3/4 步落地班·提案乙）。

巡检发现设置页插件市场「外部目录」提示行仍写五源（缺 CocoLoop），且其
data-i18n key 在 i18n.js 无对应词条——英文界面回退中文裸奔（「双源落地
菜单仍写单源」同类的文案-实现漂移）。本组把「提示行列全六源 + key 词条
逐字一致」锁成契约：日后加源漏改文案、或改文案漏同步词条，在此先红。

跑法：python -m unittest discover -s tests -p "test_market_copy_sources.py" -v
"""
from __future__ import annotations

import io
import os
import re

from base import BaseTest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_HTML = os.path.join(_ROOT, "app", "ui", "index.html")
_I18N_JS = os.path.join(_ROOT, "app", "ui", "i18n.js")

# 提示行前缀（定位 index.html 的 mk-remote hint 与 i18n.js 词条键）
_HINT_PREFIX = "外部目录聚合公开生态："
# 六源中可唯一锚定的显示名（对应 market_remote.py SOURCES 六源：zcode/
# anthropic/anthropic-skills/claude-skills/clawhub/cocoloop 的用户可见名称；
# 「Anthropic 官方技能」「社区多端技能库」两源与 Anthropic 词面重叠，
# 不单列锚点）
_SOURCE_NAMES = ("ZCode", "Anthropic", "ClawHub", "CocoLoop")


class MarketRemoteHintI18nTests(BaseTest):
    """市场提示行六源齐全，且 data-i18n key 在 i18n.js 恰好一条词条。"""

    def _hint_key(self):
        """从 index.html 取提示行的 data-i18n key（应与可见文本逐字一致）。"""
        with io.open(_INDEX_HTML, encoding="utf-8") as fh:
            src = fh.read()
        keys = re.findall(r'data-i18n="(' + _HINT_PREFIX + r'[^"]+)"', src)
        self.assertEqual(len(keys), 1,
                         "外部目录提示行应恰好一条（找到了 %d 条）" % len(keys))
        return keys[0]

    def test_hint_lists_all_sources(self):
        """提示行列全六源（锚定可唯一匹配的四名称）：缺一即红（加源漏改文案形态）。"""
        key = self._hint_key()
        for name in _SOURCE_NAMES:
            self.assertIn(name, key, "提示行缺来源 %s" % name)

    def test_hint_key_has_exactly_one_i18n_entry(self):
        """data-i18n key 在 i18n.js 恰好一条 EN 词条（缺=英文回退中文裸奔）。"""
        key = self._hint_key()
        with io.open(_I18N_JS, encoding="utf-8") as fh:
            i18n = fh.read()
        self.assertEqual(i18n.count('"%s":' % key), 1,
                         "词条键应恰好一条（失配即英文界面回退中文）")

    def test_stale_five_source_copy_gone(self):
        """旧五源文案（「…兼容）与 ClawHub。」结尾）在 index.html 零残留。"""
        with io.open(_INDEX_HTML, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("（Codex / Gemini 兼容）与 ClawHub。", src,
                         "旧五源文案残留（漏 CocoLoop 的旧版）")


if __name__ == "__main__":
    import unittest
    unittest.main()
