# -*- coding: utf-8 -*-
"""Platform-writing notes must distinguish evidence from hard requirements."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "app" / "core" / "skillpacks"


class NovelSkillpackEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qimao = (PACKS / "qimao-signing.md").read_text(encoding="utf-8")
        cls.fanqie = (PACKS / "fanqie-novel.md").read_text(encoding="utf-8")

    def test_qimao_experience_is_linked_and_not_a_signing_gate(self):
        from app.core import market

        self.assertIn("https://bbs.qimao.com/column/6859f2539995c", self.qimao)
        self.assertIn("A级：当前平台事实", self.qimao)
        self.assertIn("未满足不等于质量不合格", self.qimao)
        self.assertIn("五人只是阅读负担参考，不是硬门槛", self.qimao)
        self.assertNotIn("老编辑看第一章就能定过不过", self.qimao)
        self.assertNotIn("第一章具名人物 ≤ 5", self.qimao)
        self.assertNotIn("\n+ 签约作者", self.qimao)
        self.assertIn("构思工具，不是充分条件", self.qimao)
        self.assertIn("伏笔不以数量取胜", self.qimao)
        self.assertIn("历史点位和平台政策不是硬门槛",
                      market._BUILTIN_META["qimao-signing"]["desc"])

    def test_fanqie_metrics_are_diagnostic_hypotheses(self):
        from app.core import market

        self.assertIn("https://fanqienovel.com/main/writer/", self.fanqie)
        self.assertIn("推荐路径和权重不可见", self.fanqie)
        self.assertIn("不能代替因果证据", self.fanqie)
        self.assertIn("不作硬门槛", self.fanqie)
        self.assertIn("复验", self.fanqie)
        self.assertIn("不能直接换算成收益", self.fanqie)
        self.assertIn("后台指标只作假设",
                      market._BUILTIN_META["fanqie-novel"]["desc"])
        for unsupported_claim in (
            "决定能否进下一流量层",
            "决定推荐是否持续",
            "多线叙事、慢热铺垫在番茄大概率数据判死刑",
            "算法按这些做人群匹配",
        ):
            self.assertNotIn(unsupported_claim, self.fanqie)


if __name__ == "__main__":
    unittest.main()
