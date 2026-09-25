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

    def test_novel_madman_architect_carpenter(self):
        """四角色写作框架蒸馏（2026-09-25 借鉴 writing-with-agents）：
        起草侧先发散后收敛三步进契约；Judge 角色由评审链承担不进起草契约。"""
        from app.core import pipeline

        novel = pipeline._content_contract({"type": "novel"})
        self.assertIn("素材清单", novel)                           # 狂人：发散倾倒
        self.assertIn("组织成结构", novel)                         # 建筑师：结构化
        self.assertIn("按结构成文", novel)                         # 木匠：按结构成文
        self.assertIn("初稿期不做质量审判", novel)                 # Judge 不在起草期

    def test_research_evidence_conflict_and_gap_driven(self):
        """调研证据纪律（2026-09-25 借鉴 deepresearch-agent 记忆层矛盾检测消解
        + dzhng/deep-research 缺口驱动迭代）：来源冲突显式裁决不各说一半，
        子问题清单缺口驱动补查、查不到的显式标注证据不足。"""
        from app.core import pipeline

        appendix = pipeline.RESEARCH_APPENDIX
        self.assertIn("来源冲突显式裁决", appendix)      # 矛盾不悄悄取舍
        self.assertIn("不各说一半", appendix)            # 冲突处理去向
        self.assertIn("按来源可信度加权", appendix)      # Source-Weight 消解
        self.assertIn("缺口驱动补查", appendix)          # 子问题清单驱动
        self.assertIn("证据不足", appendix)              # 缺口显式标注

    def test_email_thread_action_items(self):
        """线程级交付契约（2026-09-25 借鉴 agentic-inbox）：回复邮件逐条回应不漏问，
        行动项带负责人与截止时间。"""
        from app.core import pipeline

        email = pipeline._content_contract({"type": "email"})
        self.assertIn("逐条回应", email)
        self.assertIn("不漏问", email)
        self.assertIn("负责人", email)


if __name__ == "__main__":
    import unittest
    unittest.main()
