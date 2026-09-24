# -*- coding: utf-8 -*-
"""统一 CLI/厂商/模型调度评分回归。"""
from __future__ import annotations

from base import BaseTest


class TestDispatch(BaseTest):
    def test_all_presets_have_dimension(self):
        from app.core.dispatch import task_dimension
        for kind in ("direct", "code", "novel", "serial_novel",
                     "article", "video_script", "translation",
                     "research", "speech", "weekly_report",
                     "email", "rank_scan", "tech_proposal", "zentao"):
            self.assertIn(task_dimension(kind), ("writing", "coding",
                                                  "reasoning", "vision"))

    def test_easy_prefers_budget_and_hard_prefers_premium(self):
        from app.core.dispatch import rank_model_entries
        providers = {
            "cheap": {"id": "cheap", "name": "Cheap",
                        "tier": "budget", "models": [{"name": "m1"}]},
            "rich": {"id": "rich", "name": "Rich",
                       "tier": "premium", "models": [{"name": "m2"}]},
        }
        chain = [{"provider_id": "rich", "model": "m2"},
                 {"provider_id": "cheap", "model": "m1"}]
        easy, _ = rank_model_entries(chain, providers, {}, "easy", "code", "implement")
        hard, _ = rank_model_entries(chain, providers, {}, "hard", "code", "implement")
        self.assertEqual(easy[0]["provider_id"], "cheap")
        self.assertEqual(hard[0]["provider_id"], "rich")

    def test_default_keeps_manual_order_and_explains(self):
        from app.core.dispatch import rank_model_entries
        chain = [{"provider_id": "a", "model": "m-a"},
                 {"provider_id": "b", "model": "m-b"}]
        ranked, decisions = rank_model_entries(chain, {}, {}, "default", "code")
        self.assertEqual(ranked, chain)
        self.assertEqual([d["provider_id"] for d in decisions], ["a", "b"])
        self.assertTrue(all(d["reason"] for d in decisions))

    def test_unconfigured_binding_has_no_penalty(self):
        from app.core import router
        a = {"id": "x", "kind": "codex", "mode": "real",
             "_dispatch_task_type": "code"}
        score, reason = router.score(a, "implement", "code", {})
        self.assertNotIn("绑定链为空", reason)
        self.assertGreater(score, 80)
