# -*- coding: utf-8 -*-
"""能力维度（4D 按能力选模型）测试。
设计稿：docs/migration/05-model-seams.md §4D。
"""
from __future__ import annotations

from base import BaseTest


class TestClassify(BaseTest):

    def test_explicit_task_type_wins(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type({"task_type": "vision", "goal": "写小说"}), "vision")

    def test_writing_keywords(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type({"type": "serial_novel", "goal": "写一部两万字连载"}), "writing")

    def test_coding_keywords(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type({"type": "code", "goal": "修复这个 bug"}), "coding")

    def test_reasoning_keywords(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type({"goal": "调研并对比几个方案"}), "reasoning")

    def test_vision_keywords(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type({"goal": "识别这张图片里的表格"}), "vision")

    def test_default_fallback_coding(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type({"goal": "xyzzy"}), "coding")

    def test_empty_safe(self):
        from app.core.capability import classify_task_type
        self.assertEqual(classify_task_type(None), "coding")


class TestPickByStrength(BaseTest):

    def test_strong_first(self):
        from app.core.capability import pick_by_strength
        provs = [
            {"id": "a", "enabled": True, "strengths": ["coding"]},
            {"id": "b", "enabled": True, "strengths": ["writing", "reasoning"]},
            {"id": "c", "enabled": True},
        ]
        ranked, reason = pick_by_strength(provs, "writing")
        self.assertEqual(ranked[0]["id"], "b")
        self.assertIn("b", reason)

    def test_no_declaration_keeps_order(self):
        from app.core.capability import pick_by_strength
        provs = [{"id": "a", "enabled": True}, {"id": "b", "enabled": True}]
        ranked, reason = pick_by_strength(provs, "writing")
        self.assertEqual([p["id"] for p in ranked], ["a", "b"])
        self.assertIn("无声明", reason)

    def test_disabled_filtered(self):
        from app.core.capability import pick_by_strength
        provs = [
            {"id": "off", "enabled": False, "strengths": ["writing"]},
            {"id": "on", "enabled": True},
        ]
        ranked, _ = pick_by_strength(provs, "writing")
        self.assertEqual([p["id"] for p in ranked], ["on"])

    def test_disabled_can_be_kept(self):
        from app.core.capability import pick_by_strength
        provs = [
            {"id": "off", "enabled": False, "strengths": ["writing"]},
            {"id": "on", "enabled": True},
        ]
        ranked, _ = pick_by_strength(provs, "writing", enabled_only=False)
        self.assertEqual(ranked[0]["id"], "off")

    def test_invalid_strengths_ignored(self):
        from app.core.capability import pick_by_strength, provider_strengths
        self.assertEqual(provider_strengths({"strengths": "not-a-list"}), [])
        self.assertEqual(provider_strengths({"strengths": ["flying", "writing"]}), ["writing"])
        provs = [{"id": "a", "enabled": True, "strengths": ["flying"]}]
        ranked, _ = pick_by_strength(provs, "writing")
        self.assertEqual(ranked[0]["id"], "a")

    def test_task_type_string_accepted(self):
        """task_type 参数也可以直接传一段目标文本。"""
        from app.core.capability import pick_by_strength
        provs = [{"id": "w", "enabled": True, "strengths": ["writing"]}]
        ranked, _ = pick_by_strength(provs, "写一篇营销文案")
        self.assertEqual(ranked[0]["id"], "w")


class TestResolveBinding(BaseTest):

    def test_binding_by_dimension(self):
        from app.core.capability import resolve_binding_by_task
        bindings = {
            "writing": {"provider_id": "w1"},
            "coding": {"provider_id": "c1"},
            "default": {"provider_id": "d1"},
        }
        b, tt = resolve_binding_by_task({"goal": "写连载"}, {}, bindings)
        self.assertEqual(tt, "writing")
        self.assertEqual(b["provider_id"], "w1")

    def test_missing_dimension_falls_back_default(self):
        from app.core.capability import resolve_binding_by_task
        bindings = {"default": {"provider_id": "d1"}}
        b, tt = resolve_binding_by_task({"goal": "识别图片"}, {}, bindings)
        self.assertEqual(tt, "vision")
        self.assertEqual(b["provider_id"], "d1")

    def test_no_bindings_returns_none(self):
        from app.core.capability import resolve_binding_by_task
        b, tt = resolve_binding_by_task({"goal": "写"}, {}, {})
        self.assertIsNone(b)
        self.assertEqual(tt, "writing")