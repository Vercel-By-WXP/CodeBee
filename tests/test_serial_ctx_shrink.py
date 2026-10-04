# -*- coding: utf-8 -*-
"""连载起草重试的分层降级组装（_serial_shrunk_block）单测。

背景（2026-10-04 巡检实锤）：此前 bible 在组块时被折进 sk_block，重试时整块
当「经验库」传入 _shrink_context_block——设计的「模块库边界截断/圣经二级标题
边界截断」两层永不生效，超预算只剩 4K 硬截一层（圣经常被拦腰截断）。修复后
经验库/圣经按各自身份过收缩、知识库块永不动，此处逐层锁定。

跑法：python -m unittest discover -s tests -p "test_serial_ctx_shrink.py" -v
"""
from __future__ import annotations

from base import BaseTest


def _big_lessons(n=400):
    return "\n".join("- 教训条目 %d：这里是一些经验文字，用于撑起体量。" % i
                     for i in range(n))


def _module_bible(mods=8, mod_lines=60):
    head = "## 故事圣经\n\n主角：林晚，设定若干。\n\n"
    body = "".join(
        "## 剧情模块库\n" + "".join("模块 %d 行 %d：剧情推进细节文字，用于撑起体量。\n" % (m, k)
                                    for k in range(mod_lines))
        for m in range(mods))
    return head + body


def _section_bible(sections=30, size=500):
    return "\n\n".join("## 第 %d 节\n\n%s" % (i, "设定细节文字。" * (size // 6))
                       for i in range(sections))


class SerialCtxShrinkTests(BaseTest):

    def test_layers_fire_independently(self):
        """经验库→4K 与模块库边界截断各层独立生效；知识库块原样保留。"""
        from app.core import pipeline
        lessons, bible, kb = _big_lessons(), _module_bible(), "知识块：术语表。"
        out = pipeline._serial_shrunk_block(lessons, bible, kb)
        self.assertIn("经验库已因上下文容量限制精简", out)
        self.assertIn("模块库已因上下文容量限制精简", out)
        self.assertIn(kb, out)                       # 知识块永不动
        self.assertLess(len(out), len("\n\n".join((lessons, bible, kb))))

    def test_within_budget_untouched(self):
        """预算内整块原样返回（字节级对位，保证 replace 命中）。"""
        from app.core import pipeline
        lessons, bible, kb = "小经验块", "## 故事圣经\n设定。", "知识块"
        joined = "\n\n".join(p for p in (lessons, bible, kb) if p)
        self.assertEqual(pipeline._serial_shrunk_block(lessons, bible, kb), joined)

    def test_empty_parts_leave_no_blank_segments(self):
        """经验库/知识块为空时不出空段、不出多余分隔符。"""
        from app.core import pipeline
        bible = "## 故事圣经\n设定。"
        self.assertEqual(pipeline._serial_shrunk_block("", bible, ""), bible)
        self.assertEqual(pipeline._serial_shrunk_block("经验", "", ""), "经验")

    def test_bible_boundary_layer_without_modules(self):
        """无模块库标记的超长圣经走二级标题边界截断兜底层。"""
        from app.core import pipeline
        bible = _section_bible()
        out = pipeline._serial_shrunk_block("短经验", bible, "")
        self.assertIn("圣经已因上下文容量限制精简", out)
        self.assertIn("\n## ", out)                  # 截在标题边界，不拦腰
        self.assertLess(len(out), len(bible))


if __name__ == "__main__":
    import unittest
    unittest.main()
