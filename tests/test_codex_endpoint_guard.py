from __future__ import annotations

from base import BaseTest


class TestCodexEndpointGuard(BaseTest):
    """codex 落盘端点防线（2026-09-20 orch/p1.test 反复劫持全局配置事故）。

    契约：
    - _is_dead_endpoint 拦下测试/保留地址（*.test、*.example、example.com、localhost）
    - 真实端点放行
    - _sync_codex_settings 遇假端点拒绝写盘，返回错误串而非静默写坏配置
    """

    def test_dead_endpoints_rejected(self):
        from app.core import manager
        for bad in ("https://p1.test/v1", "https://x.example/v1",
                    "http://example.com/api", "https://api.foo.invalid",
                    "http://localhost:8080/v1", "", None):
            self.assertTrue(manager._is_dead_endpoint(bad), bad)

    def test_real_endpoints_allowed(self):
        from app.core import manager
        for good in ("https://api.pateway.ai/v1", "http://10.143.18.157:3000/v1",
                     "https://maas-api.unisound.com/v1", "https://vsllm.cc"):
            self.assertFalse(manager._is_dead_endpoint(good), good)

    def test_sync_refuses_dead_endpoint(self):
        import json
        from app.core import manager
        entry = {"id": "codex-cli", "config": {"path": "~/.codex/config.toml"}}
        cp = {"name": "P1", "base_url": "https://p1.test/v1",
              "env_key": "ORCH_API_KEY", "wire_api": "responses"}
        err = manager._sync_codex_settings(entry, "m1", cp)
        self.assertIsInstance(err, str)
        self.assertIn("测试/保留地址", err)

    def test_sync_still_writes_real_endpoint(self):
        """真实端点 → 照常写盘（防毒闸放行形态：假 HOME 在临时目录下）。"""
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock
        from app.core import manager
        # 011023c 防毒闸：TUTTI_DATA 在临时目录 + 真实主目录时拒绝写真实
        # CLI 配置。生产写形态用「假 HOME 在临时目录下」测试——闸放行，
        # 写入落在假家，同样验证完整 TOML 形态。
        home_p = Path(tempfile.mkdtemp(prefix="cb-fakehome-"))
        (home_p / ".codex").mkdir(parents=True, exist_ok=True)
        cfg = home_p / ".codex" / "config.toml"
        cfg.write_text('model = "x"\n', encoding="utf-8")

        def fake_expanduser(p):
            if p == "~":
                return str(home_p)
            try:
                rel = Path(p).relative_to("~")
            except ValueError:
                return p
            cand = (home_p / rel).resolve()
            if cand == home_p.resolve() or home_p.resolve() in cand.parents:
                return str(cand)
            return p  # 越出假家的路径不改写

        entry = {"id": "codex-cli", "config": {"path": "~/.codex/config.toml"}}
        cp = {"name": "PatewayAI", "base_url": "https://api.pateway.ai/v1",
              "env_key": "ORCH_API_KEY", "wire_api": "responses"}
        try:
            with mock.patch("app.core.manager.os.path.expanduser", fake_expanduser):
                err = manager._sync_codex_settings(entry, "gpt-x-pro", cp)
            self.assertIsNone(err)
            data = cfg.read_text(encoding="utf-8")
            # 段名/顶层跟随 cp["name"]（modelhub 链路显式传 "orch" 才会叫 orch）
            self.assertIn('model_provider = "PatewayAI"', data)
            self.assertIn("[model_providers.PatewayAI]", data)
            self.assertIn("api.pateway.ai", data)
            self.assertIn('"gpt-x-pro"', data)
        finally:
            shutil.rmtree(home_p, ignore_errors=True)
