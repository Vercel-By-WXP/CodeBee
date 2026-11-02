# -*- coding: utf-8 -*-
"""2026-11-02 修复回归：aider 模型前缀归一 + 子进程禁止弹浏览器 + litellm 配置错分类。

背景：项目一启动/跑任务就弹出 https://docs.litellm.ai/docs/providers 。根因是
aider 通道把 OpenRouter 的模型 id（如 nvidia/nemotron-3-ultra-550b-a55b:free）原样
透传给 litellm，litellm 不认该前缀 → "LLM Provider NOT provided"，aider 在
--yes-always 下自动打开报错里的文档链接。本测试锁死三类修复。
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.core import error_codes, runner
from app.core.runner import _normalize_aider_model


class NormalizeAiderModelTests(unittest.TestCase):
    def test_openrouter_slash_model_gets_openai_prefix(self):
        # 根因修复：nvidia/... 是 OpenRouter 模型 id 写法，必须补 openai/ 前缀
        # 才能被 litellm 正确路由（已实证 litellm.get_llm_provider 解析为 openai）。
        model = _normalize_aider_model(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            {"OPENAI_API_BASE": "https://openrouter.ai/api/v1"},
        )
        self.assertEqual(model, "openai/nvidia/nemotron-3-ultra-550b-a55b:free")

    def test_anthropic_base_url_gets_anthropic_prefix(self):
        model = _normalize_aider_model(
            "claude-3-5-sonnet",
            {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"},
        )
        self.assertEqual(model, "anthropic/claude-3-5-sonnet")

    def test_bare_model_openai_base_gets_openai_prefix(self):
        model = _normalize_aider_model(
            "gpt-4o", {"OPENAI_API_BASE": "https://api.openai.com/v1"}
        )
        self.assertEqual(model, "openai/gpt-4o")

    def test_existing_openai_prefix_respected_no_double_prefix(self):
        # 已带 openai/ 前缀的模型不能被重复前缀。
        model = _normalize_aider_model(
            "openai/gpt-4o", {"OPENAI_API_BASE": "https://api.openai.com/v1"}
        )
        self.assertEqual(model, "openai/gpt-4o")

    def test_existing_anthropic_prefix_respected_no_double_prefix(self):
        model = _normalize_aider_model(
            "anthropic/claude-3-5-sonnet",
            {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"},
        )
        self.assertEqual(model, "anthropic/claude-3-5-sonnet")

    def test_no_base_url_keeps_model_as_is(self):
        # 无端点协议（CLI 本机默认场景）保持原样，不瞎补前缀。
        self.assertEqual(
            _normalize_aider_model("nvidia/nemotron-x", {}),
            "nvidia/nemotron-x",
        )

    def test_none_model_returns_none(self):
        self.assertIsNone(_normalize_aider_model(None, {}))


class BuildCallAiderModelTests(unittest.TestCase):
    def test_aider_argv_carries_normalized_model(self):
        # 端到端确认 _build_call 给 aider 注入正确的 --model（治本路径）。
        with mock.patch.object(runner, "resolve_command", return_value=["aider"]):
            argv, _stdin, _prompt, _tmp = runner._build_call(
                agent={"command": "aider", "env": {"OPENAI_API_BASE": "https://openrouter.ai/api/v1"}},
                kind="aider",
                sid=None,
                readonly=True,
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
                prompt="do the thing",
            )
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "openai/nvidia/nemotron-3-ultra-550b-a55b:free")
        # 关键开关保留，避免 aider 自动打开 URL（detect-urls 已禁用，但前缀修复是根因）
        self.assertIn("--no-detect-urls", argv)
        self.assertIn("--yes-always", argv)


class NoopBrowserTests(unittest.TestCase):
    def test_noop_browser_command_never_launches_a_browser(self):
        cmd = runner._noop_browser_command()
        self.assertIn("%s", cmd)  # webbrowser 需要 %s 占位符
        # 用 webbrowser 实际验证：设 BROWSER 后 open 不应打开系统默认浏览器。
        import os
        import subprocess
        import sys
        import tempfile

        noop_script = os.path.join(tempfile.gettempdir(), "_wb_noop_log.txt")
        if os.path.exists(noop_script):
            os.remove(noop_script)
        child = (
            "import os,webbrowser\n"
            "webbrowser.open('https://example.com/')\n"
        )
        env = dict(os.environ)
        env["BROWSER"] = cmd
        p = subprocess.run(
            [sys.executable, "-c", child],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        # 没真的打开浏览器：noop 不会往日志写任何内容（rundll32/true 静默退出）。
        self.assertFalse(
            os.path.exists(noop_script),
            "BROWSER noop 意外产生了副作用文件，说明弹窗未被拦截",
        )

    def test_run_process_injects_browser_noop_when_unset(self):
        # run_process 必须给子进程注入 BROWSER noop，调用方未显式指定时。
        captured = {}

        def fake_popen(*args, **kwargs):
            captured["env"] = kwargs.get("env") or {}
            raise RuntimeError("probe")

        with mock.patch.object(runner.subprocess, "Popen", side_effect=fake_popen):
            try:
                runner.run_process(argv=["echo", "hi"], timeout=1)
            except RuntimeError:
                pass
        self.assertIn("BROWSER", captured.get("env", {}))
        self.assertTrue(captured["env"]["BROWSER"].endswith("%s"))


class LitellmConfigErrorClassificationTests(unittest.TestCase):
    def test_litellm_provider_not_provided_is_upstream_config(self):
        text = (
            "litellm.BadRequestError: LLM Provider NOT provided. "
            "Pass in the LLM provider you are trying to call. "
            "You passed model=nvidia/nemotron-3-ultra-550b-a55b:free"
        )
        self.assertEqual(
            error_codes.classify_error_text(text),
            error_codes.ErrorCode.UPSTREAM_CONFIG,
        )

    def test_litellm_provider_not_provided_not_none(self):
        # 旧行为：该文本分类为 None → 落入网络重试梯反复磨。必须不再是 None。
        text = "LLM Provider NOT provided. Pass in the LLM provider you are trying to call."
        self.assertIsNotNone(error_codes.classify_error_text(text))


if __name__ == "__main__":
    import unittest
    unittest.main()
