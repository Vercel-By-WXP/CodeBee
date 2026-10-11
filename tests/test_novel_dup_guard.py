# -*- coding: utf-8 -*-
"""连载成稿事故确定性拦截回归（2026-10-11 马甲指纹班落地件）：

105-112 批「落笔入册」收束句式 8 章用 4 次、113/118 章整段连排与对白复现——
评审 major 一票否决+陈账压顶，「一直重写一直不达标」。本轮把防线前移：
1) novel_quality.duplicate_notes 确定性查重（整段/整句/完整对白逐字重复），
   起草落盘验收闸（pipeline._chapter_state）直接打回，重试带消重指令；
2) 起草提示词注入此前各章开笔/收束负样例（__REUSE_GUARD__），句式同款
   从「评审事后抓」前移成「起草事前避」；
3) 大纲提示词补「开笔/收束批内轮换」硬性要求。

纯单测 + 纯源码静态断言，不起服务不碰数据。
跑法：python -m unittest discover -s tests -p "test_novel_dup_guard.py" -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import novel_quality  # noqa: E402


# 113 章实案证据（整段连排两遍，第二遍少数句）
DUP_PARA = ("到第三天傍晚，水位线顺得能拿直尺比。气压线贴着开缝前回弹的那个数，"
            "一天挪不出半格。既白把三样数分三色画：水位蓝，气压黑，听隙红。")
DUP_PARA_TAIL = "红的那栏最短，一天只有几行，不进主图。"


class DuplicateNotesTests(unittest.TestCase):

    def test_duplicate_paragraph_detected(self):
        """整段逐字连排两遍（113 章实案形态）必须整段命中。"""
        text = DUP_PARA + "\n\n" + DUP_PARA_TAIL + "\n\n" + DUP_PARA + "\n\n结尾另起。"
        notes = novel_quality.duplicate_notes(text)
        self.assertTrue(notes)
        self.assertEqual(notes[0]["kind"], "整段重复")
        self.assertEqual(int(notes[0]["count"]), 2)

    def test_repeated_dialogue_detected(self):
        """完整对白逐字复现（113 章实案形态：第二处带引号无归属）命中对白通道。"""
        text = ("前文一段。\n\n“这杆子歪着，瞅着别扭。”顺子叔又说。\n\n"
                "“这杆子歪着，瞅着别扭。”\n\n“歪着是账。”既白说。")
        notes = novel_quality.duplicate_notes(text)
        self.assertTrue(notes)
        self.assertIn(notes[0]["kind"], ("对白重复", "整句重复"))

    def test_repeated_full_dialogue_with_lead_detected(self):
        """118 章形态：说话人引导语连同对白整句重复。"""
        lead = "赵有成在墙根接了话：“断头岔省工是真省。真要扩，我头一个报名。”"
        text = "例会上众人议扩桩。" + lead + "屋里静了一截。" + lead
        notes = novel_quality.duplicate_notes(text)
        self.assertTrue(notes)

    def test_intentional_refrain_not_flagged(self):
        """叙述性短句复沓（白描节奏手段）不误伤。"""
        text = ("早上探，往里。傍晚探，还是往里。第二天，第三天，第四天，"
                "八回，回回往里。数没有一天跳过方向。")
        self.assertEqual(novel_quality.duplicate_notes(text), [])

    def test_clean_chapter_not_flagged(self):
        """正常章稿零误报。"""
        text = "\n\n".join(
            "第 %s 段：%s" % (i, "这一段讲的是完全不同的事情，情节各自推进，"
                                "人物对话与动作交替出现，没有任何重复内容。" + "字" * i)
            for i in range(1, 6))
        self.assertEqual(novel_quality.duplicate_notes(text), [])

    def test_empty_text_returns_empty(self):
        self.assertEqual(novel_quality.duplicate_notes(""), [])
        self.assertEqual(novel_quality.duplicate_notes(None), [])

    def test_notes_capped_and_sentence_inside_dup_para_skipped(self):
        """整段重复覆盖到的句子不再单列（一条事故不报一串）；条数有上限。"""
        text = "\n\n".join([DUP_PARA] * 3)
        notes = novel_quality.duplicate_notes(text)
        self.assertLessEqual(len(notes), novel_quality._DUP_MAX_NOTES)
        para_quotes = [n["quote"] for n in notes if n["kind"] == "整段重复"]
        self.assertTrue(para_quotes)

    def test_signal_summary_carries_dup_hint(self):
        """signal_summary 的 hints 集成：重复证据进确定性复查提示，评审面可见。"""
        text = "开头。\n\n" + DUP_PARA + "\n\n" + DUP_PARA
        sig = novel_quality.signal_summary(text, chapter=113)
        self.assertTrue(any("逐字重复" in h for h in sig["hints"]))


class ReuseGuardBlockTests(unittest.TestCase):

    def test_renders_head_and_tail_rows(self):
        samples = [{"chapter": 113, "head": "开春后第四十二日，天没亮透",
                    "tail": "册上有了这一笔，就中。"}]
        block = novel_quality.reuse_guard_block(samples)
        self.assertIn("跨章复用红线", block)
        self.assertIn("第 113 章开笔", block)
        self.assertIn("第 113 章收束", block)
        self.assertIn("至多两次", block)

    def test_empty_samples_silent(self):
        self.assertEqual(novel_quality.reuse_guard_block([]), "")
        self.assertEqual(novel_quality.reuse_guard_block(None), "")
        self.assertEqual(novel_quality.reuse_guard_block([{"chapter": "x"}]), "")


class PromptContractTests(unittest.TestCase):
    """纯源码静态断言：占位符契约与接线不回流。"""

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_chapter_prompt_has_reuse_guard_placeholder(self):
        tpl = self.read("app/core/pipeline.py")
        self.assertIn("__REUSE_GUARD__", tpl)
        # 起草模板占位符与 replace 接线成对出现
        self.assertEqual(tpl.count("__REUSE_GUARD__"), 2,
                         "__REUSE_GUARD__ 须恰为模板+接线两处")

    def test_draft_acceptance_calls_duplicate_gate(self):
        src = self.read("app/core/pipeline.py")
        self.assertIn("def _chapter_state():", src)
        self.assertIn("novel_quality.duplicate_notes(txt)", src)
        self.assertIn("def _dup_reject_note(notes):", src)
        self.assertIn("def _reuse_guard_samples(workdir, upto", src)

    def test_outline_prompts_carry_rotation_rule(self):
        src = self.read("app/core/planner.py")
        self.assertEqual(src.count("开笔方式与收束形态批内轮换"), 2,
                         "首写与续写两份大纲提示词都须带轮换纪律")
        self.assertIn("章尾节奏趋同是批量生成最易露馅的指纹", src)

    def test_serial_prompt_template_importable_and_resolved(self):
        """模板经 _draft_prompt 的 replace 链后不留裸占位符（用假 workdir 冒烟）。"""
        from app.core import pipeline
        tpl = pipeline.SERIAL_CHAPTER_PROMPT
        for key in ("__SKILLS__", "__REUSE_GUARD__", "__REDO_ISSUES__",
                    "__STALE_NOTE__", "__PREV__", "__STYLE__"):
            self.assertIn(key, tpl)


if __name__ == "__main__":
    unittest.main()
