# -*- coding: utf-8 -*-
"""内置 review 类型必须把菜单分型落实到生成与修订提示词。"""
from __future__ import annotations

from unittest.mock import patch

from base import BaseTest


class ContentContractTests(BaseTest):

    def test_every_non_serial_builtin_review_type_has_contract(self):
        from app.core import flows, pipeline

        expected = {
            item["id"] for item in flows.BUILTIN_FLOWS
            if item["engine"] == "review" and item["id"] != "serial_novel"
        }
        self.assertEqual(expected, set(pipeline.CONTENT_DELIVERY_CONTRACTS))
        for flow_id in expected:
            task = {"type": flow_id}
            self.assertIn("本类型交付约束", pipeline._content_contract(task))
            self.assertNotEqual("内容交付专家", pipeline._content_role(task))

    def test_non_review_and_custom_types_keep_generic_fallback(self):
        from app.core import pipeline

        for flow_id in ("direct", "code", "rank_scan", "my_custom_flow"):
            task = {"type": flow_id}
            self.assertEqual("", pipeline._content_contract(task))
            self.assertEqual("内容交付专家", pipeline._content_role(task))

    def test_truthfulness_and_fidelity_rules_cover_high_risk_outputs(self):
        from app.core import pipeline

        translation = pipeline._content_contract({"type": "translation"})
        self.assertIn("不增译或漏译", translation)
        self.assertIn("占位符", translation)

        weekly = pipeline._content_contract({"type": "weekly_report"})
        self.assertIn("不虚构业绩", weekly)
        self.assertIn("负责人/时间", weekly)

        email = pipeline._content_contract({"type": "email"})
        self.assertIn("不得凭空补造", email)

        proposal = pipeline._content_contract({"type": "tech_proposal"})
        self.assertIn("风险与回滚", proposal)
        self.assertIn("假设和待验证项", proposal)

    def test_auto_routing_uses_real_content_type(self):
        from app.core import pipeline, router, store

        task = store.create_task({
            "type": "translation", "title": "翻译", "goal": "译成英文",
            "workdir": str(self.workdir), "rounds": 1, "threshold": 1.0,
            "manuscript": "translation.md",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        with patch.object(router, "pick", wraps=router.pick) as pick, \
                patch.object(router, "pick_critics", wraps=router.pick_critics) as pick_critics:
            pipeline.execute_run(run["id"])

        self.assertEqual("done", store.get_run(run["id"])["status"])
        self.assertTrue(any(call.args[2] == "translation" for call in pick.call_args_list))
        self.assertTrue(any(call.args[1] == "translation" for call in pick_critics.call_args_list))

    def test_venue_rules_from_sepia(self):
        """sepia 分场合规则（2026-09-21 借鉴）：四类文档契约带体裁硬规则。"""
        from app.core import pipeline

        weekly = pipeline._content_contract({"type": "weekly_report"})
        self.assertIn("第一段先给本期最重要的结论", weekly)      # 结论先行
        self.assertIn("不指名甩锅", weekly)                      # 对机制严格

        email = pipeline._content_contract({"type": "email"})
        self.assertIn("先给结论或答复", email)                    # 先答再铺陈
        self.assertIn("篇幅与事情轻重成正比", email)              # 篇幅∝利害

        proposal = pipeline._content_contract({"type": "tech_proposal"})
        self.assertIn("从要解决的问题开场", proposal)              # 问题开场
        self.assertIn("真实分析过又被否决的方向", proposal)        # 真实死胡同
        self.assertIn("明确表态的推荐意见", proposal)              # 明确观点
        self.assertIn("带适用条件与计算口径", proposal)            # 带条件数字

        doc = pipeline._content_contract({"type": "doc"})
        self.assertIn("标题写结果或结论", doc)                     # 标题=结果
        self.assertIn("可测试的验收标准", doc)                     # 验收可测试


if __name__ == "__main__":
    import unittest
    unittest.main()
