# -*- coding: utf-8 -*-
"""翻译术语表先行契约单测：动笔前提炼术语表（源文→译名）、全篇译名以表为准、
无术语短文可省略——三条硬要求必须在 TRANSLATION_APPENDIX 里，且注入路径只对
translation 类型生效——防止提示词写了没人用或误伤其他类型。

跑法：python -m unittest discover -s tests -p "test_translation_glossary.py" -v
"""
from __future__ import annotations

from base import BaseTest


class TranslationGlossaryTests(BaseTest):

    def test_appendix_has_glossary_first(self):
        """术语表先行：动笔前通读提炼，逐条「源文 → 译名」放稿件开头。"""
        from app.core import pipeline
        text = pipeline.TRANSLATION_APPENDIX
        self.assertIn("术语表", text)
        self.assertIn("动笔前先通读全文", text)
        self.assertIn("源文 → 译名", text)
        self.assertIn("## 术语表", text)

    def test_appendix_has_consistency_rule(self):
        """译名一致性：全篇以表为准、不随上下文漂移、通用译名优先。"""
        from app.core import pipeline
        text = pipeline.TRANSLATION_APPENDIX
        self.assertIn("不随上下文漂移", text)
        self.assertIn("通用译名", text)

    def test_appendix_allows_short_text_skip(self):
        """无术语短文可省略术语表，但一致性要求不变——避免教条化。"""
        from app.core import pipeline
        text = pipeline.TRANSLATION_APPENDIX
        self.assertIn("可省略术语表", text)
        self.assertIn("一致性要求不变", text)

    def test_appendix_only_injected_for_translation(self):
        """注入路径只挂 translation 类型：拼接处必须包在类型闸内。"""
        import re
        import inspect
        from app.core import pipeline
        src = inspect.getsource(pipeline)
        m = re.search(
            r'if task\.get\("type"\) == "translation":'
            r"[^\n]*\n(?:[^\n]*\n){0,2}?[^\n]*p \+= TRANSLATION_APPENDIX",
            src,
        )
        self.assertIsNotNone(m, "TRANSLATION_APPENDIX 拼接应在 translation 类型闸内")

    def test_research_appendix_untouched(self):
        """上轮降噪契约不回归：RESEARCH_APPENDIX 两条硬要求仍在位。"""
        from app.core import pipeline
        text = pipeline.RESEARCH_APPENDIX
        self.assertIn("来源先审后用", text)
        self.assertIn("结论要经得起反例", text)


if __name__ == "__main__":
    import unittest
    unittest.main()
