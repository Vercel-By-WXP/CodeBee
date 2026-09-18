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


if __name__ == "__main__":
    unittest.main()
