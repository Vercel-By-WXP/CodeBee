# -*- coding: utf-8 -*-
"""Regression tests for shared upstream identity and config preflight guards."""
from __future__ import annotations

import unittest
from unittest import mock


class UpstreamIdentityTest(unittest.TestCase):
    def test_normalizes_scheme_case_default_ports_and_paths(self):
        from app.core.upstream import normalize_upstream

        self.assertEqual("api.example", normalize_upstream("https://API.EXAMPLE:443/v1"))
        self.assertEqual("api.example", normalize_upstream("http://api.example:80/chat"))
        self.assertEqual("api.example:8443", normalize_upstream("https://api.example:8443/v1"))
        self.assertEqual("api.example", normalize_upstream("api.example."))

    def test_normalizes_ipv6_and_rejects_malformed_values(self):
        from app.core.upstream import normalize_upstream

        self.assertEqual("2001:db8::1", normalize_upstream("https://[2001:0db8::1]:443/v1"))
        self.assertEqual("", normalize_upstream("https:///v1"))

    def test_runner_and_router_share_identity(self):
        from app.core import router, runner

        att = {"provider": {"base_url": "https://API.EXAMPLE:443/v1"}}
        self.assertEqual(router._host_of(att["provider"]["base_url"]),
                         runner._attempt_upstream(att))


class RuntimeConfigGuardTest(unittest.TestCase):
    def test_sync_cache_is_success(self):
        from app.core import manager, modelhub

        with mock.patch.object(manager, "_sync_launch_model") as sync_mock, \
                mock.patch.object(manager.catalog, "by_id", return_value={
                    "id": "fake", "config": {"path": "~/.fake/config.json"}}), \
                mock.patch.object(manager, "_runtime_cfg_hash", return_value="h"), \
                mock.patch("app.core.manager.os.path.expanduser", return_value="x"), \
                mock.patch.object(modelhub, "resolve_binding", return_value={
                    "model": "m", "env": {"X": "e"}, "codex_provider": "c"}):
            with mock.patch.object(manager, "_RUNTIME_SYNC", {
                    "fps": {"fake": ("m", repr([("X", "e")]), repr("c")[:300])},
                    "files": {"fake": "h"}, "lock": manager.threading.Lock()}):
                self.assertTrue(manager.sync_runtime_config({"id": "fake"}))
        # 缓存命中必须短路写盘：指纹/哈希任一失配导致缓存穿透时本守卫变红
        sync_mock.assert_not_called()

    def test_sync_failure_is_exposed_as_environment_block(self):
        from app.core import manager, modelhub, runner

        agent = {"id": "fake", "kind": "generic", "mode": "real"}
        with mock.patch.object(manager, "sync_runtime_config", return_value=False), \
                mock.patch.object(modelhub, "_binding_for", return_value={}), \
                mock.patch.object(modelhub, "resolve_binding", return_value=None), \
                mock.patch.object(modelhub, "recommend_binding", return_value=None):
            bound = modelhub.bind_agent(agent)
        self.assertTrue(bound.get("runtime_config_sync_failed"))
        result = runner.run_agent(bound, "prompt")
        self.assertFalse(result["ok"])
        self.assertEqual("ENV_BLOCK", runner.error_code_value(result["error_code"]))


if __name__ == "__main__":
    unittest.main()
