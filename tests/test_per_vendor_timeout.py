# -*- coding: utf-8 -*-
"""每 CLI 独立超时（per-vendor timeout）测试。
设计稿：docs/migration/01-defense-patterns.md §5E。
"""
from __future__ import annotations

import sys
from pathlib import Path
from base import BaseTest


FIXTURES = Path(__file__).parent


class TestPerVendorTimeout(BaseTest):

    def test_default_used_when_orch_timeout_missing(self):
        """agent 没有 orch.timeout_ms → 走 caller timeout（默认 DEFAULT_TIMEOUT）。"""
        from app.core import runner
        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
                 "mode": "real"}
        # 不传 timeout → 用 DEFAULT_TIMEOUT
        out = runner.run_agent(agent, "hi", readonly=True, timeout=30)
        self.assertTrue(out["ok"])

    def test_orch_timeout_overrides_caller(self):
        """agent.orch.timeout_ms=1000 → 实际超时 1s（即使 caller 传 30s）。"""
        import tempfile, os
        # 子进程：永久 sleep
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write("import time\ntime.sleep(60)\n")
            tmp = f.name
        try:
            from app.core import runner
            agent = {
                "kind": "generic",
                "command": sys.executable,
                "argv_template": [tmp, "{prompt}"],
                "mode": "real",
                "orch": {"timeout_ms": 1000},  # 1s
            }
            out = runner.run_agent(agent, "hi", readonly=True, timeout=30)  # caller 30s
            # 实际应 1s 超时（duration < 3s）
            self.assertFalse(out["ok"])
            raw = out.get("raw", {})
            self.assertTrue(raw.get("timed_out"), msg=f"raw={raw}")
            self.assertLess(raw.get("duration", 99), 3.0,
                            msg=f"expected ~1s timeout, got {raw.get('duration')}s")
        finally:
            os.unlink(tmp)

    def test_configured_timeout_above_one_minute_is_not_clipped(self):
        """Per-agent budgets above one minute must reach the process runner unchanged."""
        from unittest.mock import patch
        from app.core import runner

        agent = {
            "kind": "generic",
            "command": sys.executable,
            "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
            "mode": "real",
            "orch": {"timeout_ms": 120_000},
        }
        process_result = {
            "ok": True, "stdout": "done", "stderr": "", "exit_code": 0,
            "duration": 0.01, "timed_out": False, "cancelled": False,
            "stalled": False, "deadline_exceeded": False,
        }
        with patch("app.core.runner.run_process", return_value=process_result) as run:
            out = runner.run_agent(agent, "hi", readonly=True, timeout=30)

        self.assertTrue(out["ok"])
        self.assertEqual(run.call_args.kwargs["timeout"], 120)

    def test_caller_timeout_above_one_minute_is_not_clipped(self):
        """Without an agent override, the caller's step budget is respected."""
        from unittest.mock import patch
        from app.core import runner

        agent = {
            "kind": "generic",
            "command": sys.executable,
            "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
            "mode": "real",
        }
        process_result = {
            "ok": True, "stdout": "done", "stderr": "", "exit_code": 0,
            "duration": 0.01, "timed_out": False, "cancelled": False,
            "stalled": False, "deadline_exceeded": False,
        }
        with patch("app.core.runner.run_process", return_value=process_result) as run:
            out = runner.run_agent(agent, "hi", readonly=True, timeout=120)

        self.assertTrue(out["ok"])
        self.assertEqual(run.call_args.kwargs["timeout"], 120)

    def test_caller_timeout_when_orch_missing(self):
        """caller 显式传 30s、orch 无 timeout → 用 30s。"""
        from app.core import runner
        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
                 "mode": "real"}  # 无 orch.timeout_ms
        out = runner.run_agent(agent, "hi", readonly=True, timeout=30)
        self.assertTrue(out["ok"])

    def test_orch_timeout_zero_uses_default(self):
        """orch.timeout_ms=0 → 视为未设，走 caller/default。"""
        from app.core import runner
        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
                 "mode": "real", "orch": {"timeout_ms": 0}}
        # 用 role_cli，正常返回
        out = runner.run_agent(agent, "hi", readonly=True, timeout=30)
        self.assertTrue(out["ok"])

    def test_catalog_schema_field_present(self):
        """catalog DEFAULT_CATALOG 中的 entries 应有 orch 字段（向后兼容）。"""
        from app.core.catalog import DEFAULT_CATALOG
        for entry in DEFAULT_CATALOG:
            self.assertIn("orch", entry, f"entry {entry.get('id')} missing orch")
