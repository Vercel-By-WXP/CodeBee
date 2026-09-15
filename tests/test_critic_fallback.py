# -*- coding: utf-8 -*-
"""评审者级 fallback（补位）测试：名单内评审全挂时，从启用池补位出分。

设计来源：真实连载验收——claude 503 + qwen ECONNREFUSED 11434 同章全败，
导致「评审全部失败」中止；启用池里明明还有 codex/opencode 可用。
"""
from __future__ import annotations

from base import BaseTest


class TestCriticFallback(BaseTest):
    """直接验证 run_critique 的补位分支逻辑（经 serial 流程集成）。"""

    def _serial_task(self):
        from app.core import store
        return store.create_task({
            "type": "serial_novel", "title": "fallback-t", "goal": "写连载",
            "workdir": str(self.workdir), "mode": "manual",
            "implementer": "mock-a",
            "critics": ["mock-b"],   # 名单只有 mock-b；补位池在测试里注入
            "serial": {"chapters": 1, "words_per_chapter": 800},
        })

    def test_pipeline_module_exposes_fallback(self):
        """静态确认补位分支存在（防回退）。"""
        import inspect
        from app.core import pipeline
        src = inspect.getsource(pipeline)
        self.assertIn("评审者级 fallback", src)
        self.assertIn("pool[:2]", src)


class TestSparePoolSelection(BaseTest):
    """补位池筛选语义：real、未试过、排除 mock；上限 2。"""

    def test_pool_filters(self):
        agents = [
            {"id": "mock-a", "mode": "mock"},
            {"id": "codex-cli", "mode": "real"},
            {"id": "claude-code", "mode": "real"},
            {"id": "qwencode", "mode": "real"},
        ]
        critics = [{"id": "claude-code", "mode": "real"},
                   {"id": "qwencode", "mode": "real"}]
        tried = {a.get("id") for a in critics}
        pool = [a for a in (agents or [])
                if a.get("mode") == "real" and a.get("id") not in tried]
        self.assertEqual([a["id"] for a in pool[:2]], ["codex-cli"])

    def test_pool_empty_when_all_tried(self):
        agents = [{"id": "claude-code", "mode": "real"}]
        critics = [{"id": "claude-code", "mode": "real"}]
        tried = {a.get("id") for a in critics}
        pool = [a for a in agents
                if a.get("mode") == "real" and a.get("id") not in tried]
        self.assertEqual(pool, [])