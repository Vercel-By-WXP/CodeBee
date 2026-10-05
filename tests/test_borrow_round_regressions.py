# -*- coding: utf-8 -*-
"""本轮借鉴落地回归守卫（2026-10-05 16 时班第 3/4 步，两件均纯提示词指引）：
1. 评审深读指引（OpenCodeReview 借鉴）——CODE_REVIEW_PROMPT 不再要求「只基于变更
   内容」：diff 为主要依据，允许只读打开涉及文件核对上下文，无法读文件时回退 diff；
   「不要修改任何文件」约束保留。
2. 半成品收工提醒（agent-delegate 借鉴第三件）——评审要求检查 TODO/FIXME/占位实现/
   未接线的函数或配置，发现按半成品如实降档；纯提醒不改 pass 判定语义。

跑法：python -m unittest discover -s tests -p "test_borrow_round_regressions.py" -v
（或 cd tests && python -m unittest test_borrow_round_regressions -v）
"""
from __future__ import annotations

from base import BaseTest


class ReviewDeepReadTests(BaseTest):
    """评审深读指引：旧句「请只基于下方提供的任务与变更内容进行评审」与评审步
    只读沙箱（可读不可写）的能力正面相抵，造成半盲评。"""

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.prompt = pipeline.CODE_REVIEW_PROMPT

    def test_deep_read_guidance_present(self):
        """允许在只读前提下打开涉及文件及周边代码核对上下文。"""
        self.assertIn("如运行环境允许读取文件", self.prompt)
        self.assertIn("只读，不得修改任何文件", self.prompt)

    def test_diff_fallback_kept(self):
        """读不了文件的环境有明确回退口径：基于 diff 评审。"""
        self.assertIn("无法读文件时基于 diff 评审", self.prompt)

    def test_blind_review_phrase_removed(self):
        """与深读相抵的旧句不再出现。"""
        self.assertNotIn("请只基于下方提供的任务与变更内容进行评审", self.prompt)

    def test_no_write_constraint_kept(self):
        """评审员禁改文件的既有约束保留：身份句原文+深读只读括注两处都在。"""
        self.assertIn("你是代码评审员（不要修改任何文件）", self.prompt)
        self.assertIn("只读，不得修改任何文件", self.prompt)


class HalfDoneReminderTests(BaseTest):
    """半成品收工提醒：占位实现静态提醒进评审要求（此前链路无 TODO/FIXME 检查）。"""

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.prompt = pipeline.CODE_REVIEW_PROMPT

    def test_reminder_present(self):
        """TODO/FIXME/占位实现/未接线逐项点名，发现按半成品降档。"""
        self.assertIn("半成品收工提醒", self.prompt)
        self.assertIn("TODO/FIXME", self.prompt)
        self.assertIn("占位实现", self.prompt)
        self.assertIn("未接线", self.prompt)
        self.assertIn("按半成品如实降档", self.prompt)

    def test_prompt_contract_intact(self):
        """既有 JSON 输出契约与占位符不被误伤（_run_review 渲染依赖）。"""
        for token in ("```json", '"pass"', "__GOAL__", "__VERIFY__", "__DIFF__"):
            self.assertIn(token, self.prompt, token)


if __name__ == "__main__":
    import unittest
    unittest.main()
