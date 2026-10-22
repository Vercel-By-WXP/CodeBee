# -*- coding: utf-8 -*-
"""日志字色 --log-text / --term 必须与日志深底配对使用。

hive-ui.css 的共享契约把旧变量重映射（html[data-skin][data-theme] →
--log-text: var(--cb-log-text)），亮色主题下日志字色是给深色终端底
（--cb-log-bg #172027 / --cb-log-bg #0d1318）配的浅色 #d8e5e8。
凡坐在普通面板底上却借用 --log-text / --term 当字色的元素，亮色主题
直接白底白字（2026-10-22 实案两处）：

- .rd-bible .bible-view（运行详情「圣经」页签正文，白底 #d8e5e8 几乎不可读）
- board #gate-token（大屏令牌门输入框，--sunk 深底配 --t1 深字）

闸门锁定：这两处不得再引用日志字色变量，正文字色跟 --text / 输入字色
跟 --term（与所在底色同契约）。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _rule_block(css, selector):
    """取 css 中首个匹配 selector 的规则体（不支持嵌套，够静态闸门用）。"""
    for m in re.finditer(re.escape(selector) + r"\s*\{", css):
        start = m.end() - 1
        depth = 0
        for i in range(start, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    return css[start + 1:i]
        break
    return None


class TestLogTextSurfacePairing(unittest.TestCase):
    def test_bible_view_not_borrowing_log_text(self):
        css = _read("style.css")
        block = _rule_block(css, ".rd-bible .bible-view")
        self.assertIsNotNone(block, "style.css 丢失 .rd-bible .bible-view 规则")
        self.assertNotIn(
            "var(--log-text)", block,
            "圣经正文又借回 --log-text：亮色主题下它是深底浅字，白底不可读"
            "（正文字色用 var(--text)）")

    def test_board_gate_token_pairs_term_with_sunk(self):
        css = _read("board.css")
        block = _rule_block(css, "#gate-token")
        self.assertIsNotNone(block, "board.css 丢失 #gate-token 规则")
        self.assertIn("var(--sunk)", block, "#gate-token 应坐 --sunk 终端面")
        self.assertNotIn(
            "color: var(--t1)", block,
            "令牌门输入框暗底（--sunk）配暗字（--t1）：亮色主题不可读，字色用 var(--term)")


if __name__ == "__main__":
    unittest.main()
