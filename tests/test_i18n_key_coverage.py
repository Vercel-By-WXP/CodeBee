# -*- coding: utf-8 -*-
"""index.html data-i18n 全量键 ↔ i18n.js 词条对账（2026-10-06 第 3/4 步落地班·提案乙）。

巡检 G-② 残件复测：429 个 data-i18n 键中仍有 7 处在 i18n.js 无 EN 词条——
英文界面 t() 查不中即回退键本身（中文裸奔）。此前失配屡次漏网的根源是
「全量对账」测试不存在（CocoLoop 一例只锁了单行）。本组把「缺口归零」
锁成契约：日后加 UI 文案漏补词条，在此先红。

注意：键含双引号时（如 webhooks 133 字提示），i18n.js 文件表示是 JS 转义
形态 \\"——运行时解析回真实引号、词条有效；文件级比对必须两种形态都查，
否则会把在位词条误报成缺失（07 时班「8 处」清单正含此误报，实为 7 处）。

跑法：python -m unittest discover -s tests -p "test_i18n_key_coverage.py" -v
"""
from __future__ import annotations

import html
import io
import os
import re

from base import BaseTest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_HTML = os.path.join(_ROOT, "app", "ui", "index.html")
_I18N_JS = os.path.join(_ROOT, "app", "ui", "i18n.js")

# G-② 真缺残件 7 键（2026-10-06 补全；webhooks 133 字键词条早已在位 :2862，
# 由全量对账以转义形态覆盖，不在此列）
_REMNANT_KEYS = (
    "本地插件",
    "本地插件遵循统一 manifest 规范。技能会接入经验库；MCP 只在启用后合并到内置智能体工具，不执行插件脚本或钩子。",
    "＋ 添加自定义样题",
    "导入样题分享码",
    "导出报告（Markdown）",
    "选择一个任务开始工作",
    "重新运行",
)


def _data_i18n_keys():
    """index.html 全部 data-i18n 键（HTML 实体还原后）。"""
    with io.open(_INDEX_HTML, encoding="utf-8") as fh:
        src = fh.read()
    return [html.unescape(m.group(1)) for m in
            re.finditer(r'data-i18n="([^"]+)"', src)]


def _i18n_src():
    with io.open(_I18N_JS, encoding="utf-8") as fh:
        return fh.read()


def _entry_count(i18n, key):
    """词条出现次数：裸形态 + JS 转义形态（键含双引号时文件里是 \\"）。"""
    bare = i18n.count('"%s":' % key)
    if bare:
        return bare
    return i18n.count('"%s":' % key.replace('"', '\\"'))


class I18nKeyCoverageTests(BaseTest):
    """data-i18n 键在 i18n.js 均有词条：英文界面不回退中文裸奔。"""

    def test_all_data_i18n_keys_have_entry(self):
        """全量对账：每个 data-i18n 键在 i18n.js 至少一条词条（转义形态兼容）。"""
        i18n = _i18n_src()
        missing = [k for k in _data_i18n_keys() if _entry_count(i18n, k) < 1]
        self.assertEqual(missing, [],
                         "data-i18n 键缺 EN 词条（英文界面回退中文）：%s"
                         % " | ".join(m[:30] for m in missing[:5]))

    def test_remnant_keys_locked(self):
        """G-② 残件锁定：本轮补全的 7 键逐一断言在位。"""
        i18n = _i18n_src()
        for key in _REMNANT_KEYS:
            self.assertGreaterEqual(_entry_count(i18n, key), 1, key)

    def test_remnant_keys_no_duplicate(self):
        """补全键不与既有词条重复（i18n.js 同键双条 = 覆盖顺序歧义）。"""
        i18n = _i18n_src()
        for key in _REMNANT_KEYS:
            self.assertEqual(_entry_count(i18n, key), 1, key)


if __name__ == "__main__":
    import unittest
    unittest.main()
