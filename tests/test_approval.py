# -*- coding: utf-8 -*-
"""approval seam（5G NEVER 一线）测试。
设计稿：docs/migration/01-defense-patterns.md §5G。
"""
from __future__ import annotations

import os
from base import BaseTest


class TestCheckApproval(BaseTest):

    def setUp(self):
        super().setUp()
        # 保存并清环境
        self._old = os.environ.get("TUTTI_APPROVAL_POLICY")
        os.environ.pop("TUTTI_APPROVAL_POLICY", None)

    def tearDown(self):
        super().tearDown()
        if self._old is None:
            os.environ.pop("TUTTI_APPROVAL_POLICY", None)
        else:
            os.environ["TUTTI_APPROVAL_POLICY"] = self._old

    def test_non_sensitive_agent_allowed(self):
        """agent 无 sensitive → 默认 NEVER 策略也放行。"""
        from app.core.runner import _check_approval
        ok, reason = _check_approval({"orch": {}})
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_sensitive_agent_blocked_by_default_never(self):
        """agent 有 sensitive + 默认 policy=never → 拒绝。"""
        from app.core.runner import _check_approval
        ok, reason = _check_approval({"orch": {"sensitive": True}})
        self.assertFalse(ok)
        self.assertIn("policy=never", reason)

    def test_sensitive_agent_blocked_with_explicit_never(self):
        from app.core.runner import _check_approval
        os.environ["TUTTI_APPROVAL_POLICY"] = "never"
        ok, reason = _check_approval({"orch": {"sensitive": True}})
        self.assertFalse(ok)

    def test_sensitive_agent_blocked_when_policy_ask(self):
        """policy=ask 当前仍未实现 UI → 也拒绝（避免静默放行）。"""
        from app.core.runner import _check_approval
        os.environ["TUTTI_APPROVAL_POLICY"] = "ask"
        ok, reason = _check_approval({"orch": {"sensitive": True}})
        self.assertFalse(ok)
        self.assertIn("Phase 5", reason)


class TestRunAgentApprovalBlock(BaseTest):
    """run_agent 集成：sensitive + never → 早期 return 不 spawn 子进程。"""

    def setUp(self):
        super().setUp()
        self._old = os.environ.get("TUTTI_APPROVAL_POLICY")

    def tearDown(self):
        super().tearDown()
        if self._old is None:
            os.environ.pop("TUTTI_APPROVAL_POLICY", None)
        else:
            os.environ["TUTTI_APPROVAL_POLICY"] = self._old

    def test_sensitive_returns_early(self):
        os.environ["TUTTI_APPROVAL_POLICY"] = "never"
        from app.core import runner
        agent = {
            "kind": "generic",
            "command": "/bin/echo",
            "argv_template": ["{prompt}"],
            "orch": {"sensitive": True, "reason": "访问生产数据"},
        }
        out = runner.run_agent(agent, "hi", readonly=True, timeout=10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_code"], "ENV_BLOCK")
        self.assertIn("policy=never", out["error"])

    def test_non_sensitive_runs_normally(self):
        from app.core import runner
        from pathlib import Path
        import sys
        fixtures = Path(__file__).parent
        agent = {
            "kind": "generic",
            "command": sys.executable,
            "argv_template": [str(fixtures / "fixtures_role_cli.py"), "{prompt}"],
            "orch": {},  # 无 sensitive
        }
        out = runner.run_agent(agent, "网文主编", readonly=True, timeout=10)
        self.assertTrue(out["ok"], msg=out.get("error"))