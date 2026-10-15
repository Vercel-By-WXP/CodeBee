# -*- coding: utf-8 -*-
"""专家模板注册、版本快照与任务绑定契约。"""
from __future__ import annotations

from base import BaseTest


class TestExperts(BaseTest):
    def test_builtin_experts_bind_existing_flows(self):
        from app.core import experts, flows

        items = experts.list_experts()
        self.assertGreaterEqual(len(items), 6)
        self.assertTrue({"research", "code_fix", "article", "translation",
                         "presentation", "work_report"}.issubset(
            {x["id"] for x in items}))
        flow_ids = {f["id"] for f in flows.list_flows()}
        for item in items:
            self.assertIn(item["flow_id"], flow_ids)
            self.assertTrue(item["version"])
            self.assertTrue(item["prompt"])
            self.assertTrue(item["knowledge_scope"])

    def test_task_pins_expert_snapshot(self):
        from app.core import experts, store

        task = store.create_task({
            "type": "research", "expert_id": "research",
            "goal": "分析供应链软件市场", "workdir": str(self.workdir),
        })
        snap = task.get("expert_snapshot") or {}
        current = experts.get_expert("research")
        self.assertEqual(snap["id"], "research")
        self.assertEqual(snap["version"], current["version"])
        self.assertEqual(snap["flow_id"], "research")
        self.assertEqual(snap["content_sha256"], experts.content_sha256(current))
        self.assertEqual(task["expert_id"], "research")

    def test_unknown_expert_is_rejected(self):
        from app.core import store

        with self.assertRaises(ValueError):
            store.create_task({"type": "research", "expert_id": "missing",
                               "goal": "做调研", "workdir": str(self.workdir)})

    def test_expert_must_match_task_flow(self):
        from app.core import store

        with self.assertRaisesRegex(ValueError, "流程与任务类型不一致"):
            store.create_task({"type": "research", "expert_id": "code_fix",
                               "goal": "分析一个代码问题", "workdir": str(self.workdir)})

    def test_expert_snapshot_is_immutable_after_registry_change(self):
        from app.core import experts, store

        task = store.create_task({"type": "article", "expert_id": "article",
                                  "goal": "写一篇文章", "workdir": str(self.workdir)})
        before = dict(task["expert_snapshot"])
        experts._EXPERTS["article"]["prompt"] = "changed"
        self.addCleanup(lambda: experts._EXPERTS["article"].update({"prompt": before["prompt"]}))
        self.assertEqual(task["expert_snapshot"], before)

    def test_context_block_uses_pinned_snapshot(self):
        from app.core import experts

        block = experts.context_block({
            "id": "code_fix", "name": "代码修复工程师", "version": "1.0.0",
            "prompt": "先复现，再修复", "skills": ["测试"],
            "knowledge_scope": ["代码库"],
            "deliverables": ["验证结果"],
        })
        self.assertIn("先复现，再修复", block)
        self.assertIn("测试", block)
        self.assertIn("验证结果", block)
        self.assertIn("代码库", block)

    def test_run_execution_snapshot_records_expert_revision(self):
        from app.core import store

        task = store.create_task({"type": "code", "expert_id": "code_fix",
                                  "goal": "修复测试失败", "workdir": str(self.workdir)})
        snap = store._execution_snapshot(task)
        self.assertEqual(snap["expert_snapshot"]["id"], "code_fix")
        self.assertEqual(snap["expert_snapshot"]["version"], "1.0.0")
        self.assertEqual(len(snap["expert_snapshot"]["content_sha256"]), 64)

    def test_openapi_declares_expert_contract(self):
        from app.core import openapi

        doc = openapi.document()
        self.assertIn("expert_id", doc["components"]["schemas"]["TaskCreate"]["properties"])
        self.assertIn("/api/experts", doc["paths"])
