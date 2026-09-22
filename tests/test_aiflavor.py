# -*- coding: utf-8 -*-
"""aiflavor：AI 味确定性检测（借鉴 oh-story 去AI味）单测。

跑法：python -m unittest discover -s tests -p "test_aiflavor.py" -v
"""
from __future__ import annotations

import unittest

from base import BaseTest


class AiflavorTests(BaseTest):

    def test_clean_text_no_hits(self):
        from app.core import aiflavor
        r = aiflavor.analyze("他推开门，雨水顺着檐角滴落。灶上的粥还温着。")
        self.assertEqual(r["hits"], {})
        self.assertFalse(r["alert"])
        self.assertEqual(aiflavor.report_line("他推开门。"), "")

    def test_hits_and_density(self):
        from app.core import aiflavor
        text = "不禁一愣。" * 10 + "仿佛。" * 5 + "正文" * 100
        r = aiflavor.analyze(text)
        self.assertEqual(r["hits"].get("不禁"), 10)
        self.assertEqual(r["hits"].get("仿佛"), 5)
        self.assertGreater(r["per_kilo"], 0)

    def test_report_line_format_and_alert(self):
        from app.core import aiflavor
        text = ("总而言之。") * 6 + "值得值得注意的是" + "文" * 50
        line = aiflavor.report_line(text)
        self.assertIn("[AI味检测]", line)
        self.assertIn("总而言之", line)
        self.assertIn("重点评审", line)   # 超告警线

    def test_empty_text(self):
        from app.core import aiflavor
        r = aiflavor.analyze("")
        self.assertEqual(r["hits"], {})
        self.assertEqual(r["per_kilo"], 0.0)
        self.assertFalse(r["alert"])
        self.assertEqual(aiflavor.report_line(None), "")


class NarrativeTellsTests(BaseTest):
    """叙事架构层信号（借鉴 sepia/StoryScope：措辞改写不掉的架构级指纹）。"""

    def test_clean_text_no_narrative_hits(self):
        from app.core import aiflavor
        r = aiflavor.narrative_analyze("他推开门，雨水顺着檐角滴落。灶上的粥还温着。" * 10)
        self.assertEqual(r["cats"], {})
        self.assertFalse(r["alert"])

    def test_epiphany_and_body_emotion(self):
        from app.core import aiflavor
        text = "他终于明白了一切。" * 3 + "心脏猛地一缩。" * 3 + "正文填充。" * 200
        r = aiflavor.narrative_analyze(text)
        self.assertEqual(r["cats"].get("顿悟说教"), 3)
        self.assertEqual(r["cats"].get("情绪身体化"), 3)
        self.assertTrue(r["alert"])

    def test_growth_ending_only_counts_tail(self):
        from app.core import aiflavor
        # 「释然」出现在中段（剧情词，不构成收束指纹）
        mid = "双方释然。" + "正文。" * 400
        r1 = aiflavor.narrative_analyze(mid)
        self.assertNotIn("成长式收束", r1["cats"])
        # 同词出现在结尾 600 字内 → 计入
        tail = "正文。" * 400 + "他终于与自己和解，内心归于平静。"
        r2 = aiflavor.narrative_analyze(tail)
        self.assertGreaterEqual(r2["cats"].get("成长式收束", 0), 2)   # 和解+归于平静
        self.assertTrue(r2["alert"])                                   # 收束类 ≥2 即告警

    def test_report_line_two_layers(self):
        from app.core import aiflavor
        text = "总而言之。" * 6 + "他终于明白了一切。" * 3 + "文" * 50
        line = aiflavor.report_line(text)
        self.assertIn("[AI味检测]", line)
        self.assertIn("[叙事架构信号]", line)
        self.assertIn("架构级 AI 指纹", line)          # 叙事层告警提示

    def test_report_line_narrative_only(self):
        from app.core import aiflavor
        text = "他终于明白了一切。" * 3 + "正文填充。" * 300
        line = aiflavor.report_line(text)
        self.assertNotIn("[AI味检测]", line)          # 措辞层无命中不出现
        self.assertIn("[叙事架构信号]", line)

    def test_empty_and_none(self):
        from app.core import aiflavor
        for t in ("", None):
            r = aiflavor.narrative_analyze(t)
            self.assertEqual(r["cats"], {})
            self.assertFalse(r["alert"])
            self.assertEqual(r["ending_hits"], 0)


class PacingTests(BaseTest):
    """节奏/钩子密度（借鉴 web-novel-pacing-analyzer：连载章级节奏审计）。"""

    # 铺垫句刻意不含任何 HOOK_WORDS 与引号，保证「无信号」是确定的
    _DULL = "他把桌上的文件一份一份翻过去，纸页在灯下发出细碎的声响。"

    def _good_chapter(self):
        """快节奏章：多短段 + 对话多 + 开篇有冲突词 + 章末有悬念词。"""
        parts = ["他推开门，突然看见桌上的信。信封没有署名。"]
        for _ in range(12):
            parts.append("「你到底想说什么？」她盯着他。")
            parts.append("他没有回答，只是把信放在桌上。")
        parts.append("就在这时，门外传来一阵脚步声。")
        return "\n".join(parts)

    def test_short_text_skipped(self):
        """摘要/片段太短不做节奏统计（避免噪声）。"""
        from app.core import aiflavor
        r = aiflavor.pacing_analyze("他推开门。")
        self.assertFalse(r["ok"])
        self.assertEqual(aiflavor.report_line("他推开门。", "novel"), "")

    def test_well_paced_chapter_no_alert(self):
        from app.core import aiflavor
        r = aiflavor.pacing_analyze(self._good_chapter())
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["open_hits"], 1)      # 开篇有冲突信号
        self.assertGreaterEqual(r["tail_hits"], 1)      # 章末有悬念信号
        self.assertGreater(r["dialog_ratio"], aiflavor.DIALOG_ALERT_RATIO)
        self.assertEqual(r["alerts"], [])
        self.assertFalse(r["alert"])

    def test_dull_chapter_flags_all_signals(self):
        """一段到底 + 无对话 + 开头平 + 章末无钩子 → 五条告警齐发。"""
        from app.core import aiflavor
        r = aiflavor.pacing_analyze(self._DULL * 17)
        self.assertTrue(r["ok"])
        self.assertEqual(r["paras"], 1)
        self.assertEqual(r["open_hits"], 0)
        self.assertEqual(r["tail_hits"], 0)
        self.assertTrue(r["alert"])
        joined = "、".join(r["alerts"])
        for expect in ("段落偏长", "超长段", "对话段占比偏低", "章末", "开篇"):
            self.assertIn(expect, joined)

    def test_headings_not_counted_as_paragraphs(self):
        """章节标题行不算正文段落（否则每章开头都会拉高段数）。"""
        from app.core import aiflavor
        text = "# 第 1 章 雨夜\n\n" + self._good_chapter()
        r = aiflavor.pacing_analyze(text)
        self.assertTrue(r["ok"])
        self.assertEqual(r["paras"], len(self._good_chapter().split("\n")))

    def test_report_line_gated_by_kind(self):
        """节奏检测只对叙事类流程下发（邮件/汇报统计段落节奏没意义）。"""
        from app.core import aiflavor
        good = self._good_chapter()
        self.assertNotIn("[节奏检测]", aiflavor.report_line(good))            # 不传 kind
        self.assertNotIn("[节奏检测]", aiflavor.report_line(good, "email"))    # 非叙事
        for kind in aiflavor.NARRATIVE_FLOWS:
            self.assertIn("[节奏检测]", aiflavor.report_line(good, kind))

    def test_report_line_pacing_alert_text(self):
        """告警章：报告行要点名缺钩子，并保留「只做参考」的措辞。"""
        from app.core import aiflavor
        line = aiflavor.report_line(self._DULL * 17, "serial_novel")
        self.assertIn("[节奏检测]", line)
        self.assertIn("章末 200 字无悬念信号", line)
        self.assertIn("请评审官结合剧情判断", line)


class DetectionInjectionTests(BaseTest):
    """确定性检测行注入评审提示词（单稿件 + 连载逐章共用同一入口）。"""

    _TPL = "你是严格的评审。\n\n## 待评审稿件\n---\n__MANUSCRIPT__\n---"

    def test_injects_before_manuscript(self):
        """命中时插在「## 待评审稿件」之前——稿件内容仍是提示词最后一段。"""
        from app.core import aiflavor
        text = "不禁一愣。" * 10 + "正文" * 200
        out = aiflavor.inject_into_prompt(self._TPL, text, "novel")
        self.assertIn("## 确定性检测结果（供评审参考）", out)
        self.assertIn("[AI味检测]", out)
        # 检测块必须在稿件之前，且稿件占位符仍然在场（未被吞掉）
        self.assertLess(out.index("确定性检测结果"), out.index("## 待评审稿件"))
        self.assertIn("__MANUSCRIPT__", out)
        # 原文别无改动：只多了一个注入块
        self.assertEqual(out.count("## 待评审稿件"), 1)

    def test_no_hits_keeps_prompt_identical(self):
        """无命中时提示词一字不改（干净稿不多付 token）。"""
        from app.core import aiflavor
        out = aiflavor.inject_into_prompt(self._TPL, "他推开门。", "novel")
        self.assertEqual(out, self._TPL)

    def test_serial_chapter_gets_pacing_line(self):
        """连载逐章评审此前完全没有确定性检测——回归：节奏行必须下发。"""
        from app.core import aiflavor
        dull = "他把桌上的文件一份一份翻过去，纸页在灯下发出细碎的声响。" * 17
        out = aiflavor.inject_into_prompt(self._TPL, dull, "serial_novel")
        self.assertIn("[节奏检测]", out)
        self.assertIn("章末 200 字无悬念信号", out)

    def test_non_narrative_type_skips_pacing(self):
        """非叙事类型不出现节奏行（邮件段落节奏无意义），AI 味行仍照发。"""
        from app.core import aiflavor
        text = "不禁一愣。" * 10 + "正文" * 200
        out = aiflavor.inject_into_prompt(self._TPL, text, "email")
        self.assertIn("[AI味检测]", out)
        self.assertNotIn("[节奏检测]", out)


if __name__ == "__main__":
    unittest.main()
