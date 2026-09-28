# -*- coding: utf-8 -*-
"""调研报告对抗降噪契约单测：来源先审后用（软文/农场/利益相关降权）与
结论经得起反例（主动找反对证据）两条硬要求必须在 RESEARCH_APPENDIX 里，
且注入路径只对 research 类型生效——防止提示词写了没人用或误伤其他类型。

跑法：python -m unittest discover -s tests -p "test_research_noise_guard.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ResearchNoiseGuardTests(BaseTest):

    def test_appendix_has_source_credibility_review(self):
        """来源先审后用：营销软文/内容农场/利益相关背书降权并点明立场。"""
        from app.core import pipeline
        text = pipeline.RESEARCH_APPENDIX
        self.assertIn("来源先审后用", text)
        self.assertIn("内容农场", text)
        self.assertIn("利益相关方", text)
        self.assertIn("点明其立场", text)

    def test_appendix_has_adversarial_counter_evidence(self):
        """结论经得起反例：主动找反对证据+自问什么证据能推翻。"""
        from app.core import pipeline
        text = pipeline.RESEARCH_APPENDIX
        self.assertIn("反对证据", text)
        self.assertIn("推翻", text)

    def test_appendix_keeps_existing_evidence_chain(self):
        """既有证据链要求不回归：交叉验证/冲突裁决/缺口补查/结论先行。"""
        from app.core import pipeline
        text = pipeline.RESEARCH_APPENDIX
        self.assertIn("交叉验证", text)
        self.assertIn("来源冲突显式裁决", text)
        self.assertIn("缺口驱动补查", text)
        self.assertIn("核心结论", text)

    def test_appendix_only_injected_for_research(self):
        """注入路径只挂 research 类型：拼接处必须包在 is_research 类型闸内。"""
        import re
        import inspect
        from app.core import pipeline
        src = inspect.getsource(pipeline)
        m = re.search(r"if is_research:[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*p \+= RESEARCH_APPENDIX", src)
        self.assertIsNotNone(m, "RESEARCH_APPENDIX 拼接应在 if is_research 闸内")


if __name__ == "__main__":
    import unittest
    unittest.main()
