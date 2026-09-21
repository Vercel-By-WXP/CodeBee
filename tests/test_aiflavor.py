# -*- coding: utf-8 -*-
"""aiflavor：AI 味确定性检测（借鉴 oh-story 去AI味）单测。

跑法：python -m unittest discover -s tests -p "test_aiflavor.py" -v
"""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
