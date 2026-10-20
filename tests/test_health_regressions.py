"""Regression tests for provider health false positives."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import health


class ProviderHealthRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        health.init(self.tmp.name, start_probe=False)

    def tearDown(self):
        health.init(self.tmp.name, start_probe=False)
        self.tmp.cleanup()

    def test_generic_cli_crash_with_structured_vendor_error_is_ignored(self):
        health.report_failure(
            "OpenCode 原生免费模型",
            "退出码 1；stderr/stdout: > build · deepseek-v4.1-flash-free",
            model="opencode/deepseek-v4.1-flash-free",
            provider_id="opencode-native",
            error_code="VENDOR_ERROR",
        )

        self.assertEqual(health.snapshot()["providers"], [])

    def test_tool_launch_failure_is_not_a_provider_failure(self):
        health.report_failure(
            "Gemini CLI",
            "本地 CLI 启动失败（可执行文件不存在，与供应商无关）",
            error_code="TOOL_LAUNCH",
        )

        self.assertEqual(health.snapshot()["providers"], [])

    def test_real_upstream_http_failure_still_enters_provider_health(self):
        health.report_failure(
            "API provider",
            "退出码 1；stderr/stdout: HTTP 502 bad gateway",
            model="model-a",
            provider_id="provider-a",
            error_code="VENDOR_ERROR",
        )

        providers = health.snapshot()["providers"]
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["provider"], "API provider")
        self.assertIn("HTTP 502", providers[0]["last_error"])

    def test_init_removes_persisted_local_cli_false_positive(self):
        path = Path(self.tmp.name) / "provider_health.json"
        path.write_text(json.dumps({"providers": {
            "Gemini CLI": {
                "name": "Gemini CLI",
                "status": "down",
                "last_error_code": "TOOL_LAUNCH",
                "last_error": "本地 CLI 启动失败",
            },
            "API provider": {
                "name": "API provider",
                "status": "down",
                "last_error_code": "UPSTREAM_SERVER",
                "last_error": "HTTP 502 bad gateway",
            },
        }}), encoding="utf-8")

        with mock.patch.object(health, "_prune_missing"):
            health.init(self.tmp.name, start_probe=False)

        providers = {row["provider"] for row in health.snapshot()["providers"]}
        self.assertNotIn("Gemini CLI", providers)
        self.assertIn("API provider", providers)

    def test_init_removes_persisted_generic_vendor_error_without_upstream_signal(self):
        path = Path(self.tmp.name) / "provider_health.json"
        path.write_text(json.dumps({"providers": {
            "OpenCode 原生免费模型": {
                "name": "OpenCode 原生免费模型",
                "status": "down",
                "last_error_code": "VENDOR_ERROR",
                "last_error": "退出码 1；stderr/stdout: > build · model-a",
            },
        }}), encoding="utf-8")

        with mock.patch.object(health, "_prune_missing"):
            health.init(self.tmp.name, start_probe=False)

        self.assertEqual(health.snapshot()["providers"], [])

    def test_init_keeps_opaque_cli_envelope_with_structured_upstream_code(self):
        path = Path(self.tmp.name) / "provider_health.json"
        path.write_text(json.dumps({"providers": {
            "Rate-limited provider": {
                "name": "Rate-limited provider",
                "status": "down",
                "last_error_code": "RATE_LIMIT",
                "last_error": "退出码 1；stderr/stdout: opaque provider response",
            },
        }}), encoding="utf-8")

        with mock.patch.object(health, "_prune_missing"):
            health.init(self.tmp.name, start_probe=False)

        providers = health.snapshot()["providers"]
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["provider"], "Rate-limited provider")

    def test_init_keeps_opaque_envelopes_for_all_structured_transient_codes(self):
        """Restart migration must not erase a real, already-classified failure."""
        path = Path(self.tmp.name) / "provider_health.json"
        codes = ("TIMEOUT", "NETWORK", "UPSTREAM_SERVER")
        path.write_text(json.dumps({"providers": {
            "provider-%s" % code: {
                "name": "provider-%s" % code,
                "status": "down",
                "last_error_code": code,
                "last_error": "退出码 1；stderr/stdout: opaque provider response",
            }
            for code in codes
        }}), encoding="utf-8")

        with mock.patch.object(health, "_prune_missing"):
            health.init(self.tmp.name, start_probe=False)

        providers = {row["provider"] for row in health.snapshot()["providers"]}
        self.assertEqual(providers, {"provider-%s" % code for code in codes})


if __name__ == "__main__":
    unittest.main()
