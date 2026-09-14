# -*- coding: utf-8 -*-
"""Token 成本优化 T1 测试：会话复用（T1.1）+ 技能块任务内稳定化（T1.2'）。
设计稿：docs/migration/07-token-cost.md。
"""
from __future__ import annotations

from base import BaseTest


class TestResumeSid(BaseTest):
    """_resume_sid：按 CLI 能力决定是否复用会话。"""

    def test_real_vendor_kinds_pass_sid(self):
        from app.core.pipeline import _resume_sid
        for kind in ("codex", "claude", "opencode", "qwen"):
            self.assertEqual(_resume_sid({"kind": kind, "mode": "real"}, "sess-1"),
                             "sess-1", kind)

    def test_generic_with_template_passes(self):
        from app.core.pipeline import _resume_sid
        agent = {"kind": "generic", "mode": "real",
                 "resume_argv_template": ["cli", "-r", "{session}"]}
        self.assertEqual(_resume_sid(agent, "s9"), "s9")

    def test_generic_without_template_rejected(self):
        """generic 无 resume 模板 → None（避免 run_agent 硬失败打断修订流）。"""
        from app.core.pipeline import _resume_sid
        self.assertIsNone(_resume_sid({"kind": "generic", "mode": "real"}, "s1"))

    def test_mock_rejected(self):
        from app.core.pipeline import _resume_sid
        self.assertIsNone(_resume_sid({"kind": "codex", "mode": "mock"}, "s1"))

    def test_blank_rejected(self):
        from app.core.pipeline import _resume_sid
        self.assertIsNone(_resume_sid({"kind": "codex", "mode": "real"}, ""))
        self.assertIsNone(_resume_sid({"kind": "codex", "mode": "real"}, None))


class TestRunAgentReturnsSid(BaseTest):
    """run_agent 返回 dict 带 sid 字段（generic 输出无 id → 空串）。"""

    def test_generic_has_empty_sid(self):
        import sys
        from pathlib import Path
        from app.core import runner
        fixtures = Path(__file__).parent
        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": [str(fixtures / "fixtures_role_cli.py"), "{prompt}"],
                 "mode": "real"}
        out = runner.run_agent(agent, "网文主编", readonly=True, timeout=30)
        self.assertIn("sid", out)
        self.assertEqual(out["sid"], "")  # generic 无法解析出会话 id


class TestBlockForStableOrder(BaseTest):
    """T1.2'：stable_order=True 时教训按 id 排序，hits 变化不影响字节序。"""

    def setUp(self):
        super().setUp()
        from app.core import skills
        skills.upsert_lesson("novel", "教训甲", "内容甲")
        skills.upsert_lesson("novel", "教训乙", "内容乙")
        skills.upsert_lesson("novel", "教训丙", "内容丙")

    def test_default_order_follows_hits(self):
        from app.core import skills
        t = {"type": "novel"}
        t1, _ = skills.block_for(t)
        skills.bump_hits([x["id"] for x in skills.list_lessons("novel")])
        skills.bump_hits([x["id"] for x in skills.list_lessons("novel")])
        # 默认路径 hits 排序仍可用（不保证与 t1 相同，但不报错）
        t2, _ = skills.block_for(t)
        self.assertIn("教训甲", t2)

    def test_stable_order_ignores_hit_changes(self):
        from app.core import skills
        t = {"type": "novel"}
        s1, used1 = skills.block_for(t, stable_order=True)
        # 模拟任务中途 hits 变化（每章 bump）
        skills.bump_hits(used1)
        skills.bump_hits(used1)
        s2, _ = skills.block_for(t, stable_order=True)
        self.assertEqual(s1, s2, "stable_order 下技能块必须字节级一致（前缀缓存依赖）")

    def test_stable_order_deterministic(self):
        from app.core import skills
        t = {"type": "novel"}
        a, _ = skills.block_for(t, stable_order=True)
        b, _ = skills.block_for(t, stable_order=True)
        self.assertEqual(a, b)