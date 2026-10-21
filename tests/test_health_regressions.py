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

    def test_init_prunes_opaque_envelopes_with_structured_transient_codes(self):
        """2026-10-21 政策反转：结构化瞬态码 + 信封文本分类不出任何供应商
        信号 = 无从核实的记账，按「拿不准宁可不记」清掉。真故障会在
        下一次真实失败（分钟级）重新记账；假告警却会永远赖在横幅里。"""
        path = Path(self.tmp.name) / "provider_health.json"
        codes = ("TIMEOUT", "NETWORK", "UPSTREAM_SERVER", "RATE_LIMIT")
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

        self.assertEqual(health.snapshot()["providers"], [])

    def test_grep_hit_source_line_never_enters_provider_health(self):
        """grep 命中行的行号会伪装成状态码：types.gen.d.ts:429: 曾把健康的
        OpenCode 免费通道推进 down 告警（2026-10-21 实案）。无论调用方自带
        什么码，信封剥掉源码位置后分类不出信号就必须跳过。"""
        text = ("退出码 1；stderr/stdout: ./.mimocode/node_modules/@mimo-ai/sdk"
                "/dist/gen/types.gen.d.ts:429:export type Todo = {")
        for supplied in ("VENDOR_ERROR", "UPSTREAM_SERVER", ""):
            health.report_failure(
                "OpenCode 原生免费模型",
                text,
                model="opencode/deepseek-v4.1-flash-free",
                provider_id="prov-48",
                error_code=supplied,
            )
        self.assertEqual(health.snapshot()["providers"], [])

    def test_init_prunes_persisted_grep_line_false_positive(self):
        path = Path(self.tmp.name) / "provider_health.json"
        path.write_text(json.dumps({"providers": {
            "OpenCode 原生免费模型": {
                "name": "OpenCode 原生免费模型",
                "status": "down",
                "consecutive_failures": 2,
                "last_error_code": "UPSTREAM_SERVER",
                "last_error": ("退出码 1；stderr/stdout: ./.mimocode/node_modules"
                               "/@mimo-ai/sdk/dist/gen/types.gen.d.ts:429:export "
                               "type Todo = {"),
            },
        }}), encoding="utf-8")

        with mock.patch.object(health, "_prune_missing"):
            health.init(self.tmp.name, start_probe=False)

        self.assertEqual(health.snapshot()["providers"], [])


if __name__ == "__main__":
    unittest.main()
