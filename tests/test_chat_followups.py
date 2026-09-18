# -*- coding: utf-8 -*-
"""直连对话「建议追问」协议（借鉴 freebuff suggest_followups）解析测试。

跑法：python -m unittest discover -s tests -p "test_chat_followups.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ParseFollowupsTests(BaseTest):

    def _parse(self, text):
        from app.core.pipeline import _parse_followups
        return _parse_followups(text)

    def test_no_block(self):
        clean, fups = self._parse("普通回答")
        self.assertEqual(clean, "普通回答")
        self.assertEqual(fups, [])

    def test_parse_and_strip(self):
        text = ("回答正文。\n\n<followups>\n看第2章 | 把第2章也写出来\n"
                "换个结局 | 帮我把结局改成开放式\n</followups>")
        clean, fups = self._parse(text)
        self.assertEqual(clean, "回答正文。")
        self.assertEqual(len(fups), 2)
        self.assertEqual(fups[0]["label"], "看第2章")
        self.assertEqual(fups[0]["prompt"], "把第2章也写出来")
        self.assertEqual(fups[1]["prompt"], "帮我把结局改成开放式")

    def test_caps_and_fullwidth_pipe(self):
        text = ("<followups>\n- 甲｜消息A\n- 乙 | 消息B\n- 丙 | 消息C\n"
                "- 丁 | 消息D\n</followups>")
        clean, fups = self._parse(text)
        self.assertEqual(clean, "")
        self.assertEqual(len(fups), 3)          # 最多 3 条
        self.assertEqual(fups[0]["label"], "甲")  # 行首列表符剥掉、全角竖线兼容
        self.assertEqual(fups[0]["prompt"], "消息A")

    def test_drops_invalid_lines(self):
        text = "<followups>\n只有标签没有竖线\n\nok | 有消息\n</followups>尾部残留"
        clean, fups = self._parse(text)
        self.assertEqual(len(fups), 1)
        self.assertEqual(fups[0]["prompt"], "有消息")
        self.assertTrue(clean.endswith("尾部残留"))


if __name__ == "__main__":
    unittest.main()
