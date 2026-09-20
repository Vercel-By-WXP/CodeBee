# -*- coding: utf-8 -*-
"""aider 接入 + 瞬态换将回归（2026-09-20 连载评审四家全灭案）。

四家评审 CLI 同晚全挂，各有病根：
- aider：litellm 靠 provider 前缀路由 + 只认 ANTHROPIC_API_KEY（不认
  ANTHROPIC_AUTH_TOKEN），旧注入两样都没给 → LLM Provider NOT provided，
  且 aider 撞模型错误仍退出码 0，步骤被记成 done、正文全是终端噪声；
- codex 网关断流（stream disconnected）/ opencode 服务端 500（Unexpected
  server error）：都是重试或换将就能活的瞬态病，旧瞬态表判成终态，
  链上健康后继从未被尝试。
真实端到端连通已在会话中实测（anthropic/glm-5.1 回复「连通正常」）；
这里固化不花钱的部分：argv/env 形状 + 瞬态判定。
"""
from __future__ import annotations

from base import BaseTest


class TestAiderCallShape(BaseTest):
    def runTest(self):
        from app.core import runner as R

        # anthropic 面条目：env 带 ANTHROPIC_BASE_URL → 裸模型名自动加 anthropic/ 前缀
        agent = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider",
                 "env": {"ANTHROPIC_BASE_URL": "https://x/api/anthropic"}}
        argv, _, _, _ = R._build_call(agent, "aider", "", True, "glm-5.1", "p")
        i = argv.index("--model")
        self.assertEqual(argv[i + 1], "anthropic/glm-5.1")
        self.assertIn("--no-show-model-warnings", argv)   # 网关模型警告刷屏闸

        # openai 兼容面条目 → openai/ 前缀
        agent2 = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider",
                  "env": {"OPENAI_API_BASE": "https://y/v1"}}
        argv2, _, _, _ = R._build_call(agent2, "aider", "", True, "glm-5.1", "p")
        self.assertEqual(argv2[argv2.index("--model") + 1], "openai/glm-5.1")

        # 已带前缀的模型名不动；无注入（CLI 本机默认）也不动
        argv3, _, _, _ = R._build_call(agent, "aider", "", True, "zai/glm-5.1", "p")
        self.assertEqual(argv3[argv3.index("--model") + 1], "zai/glm-5.1")
        agent4 = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider", "env": {}}
        argv4, _, _, _ = R._build_call(agent4, "aider", "", True, "glm-5.1", "p")
        self.assertEqual(argv4[argv4.index("--model") + 1], "glm-5.1")


class TestAiderChainEnv(BaseTest):
    """链条目必须给 aider 递上它能读到的钥匙（litellm 不认 AUTH_TOKEN）。"""

    def runTest(self):
        from app.core import modelhub as M
        prov = {"id": "prov-x", "name": "X", "protocol": "anthropic",
                "base_url": "https://x/api/anthropic", "api_key": "sk-test",
                "enabled": True}
        e = M._chain_entry_env(prov, "glm-5.1", target="aider")
        self.assertEqual(e["env"].get("ANTHROPIC_API_KEY"), "sk-test")
        self.assertIn("ANTHROPIC_BASE_URL", e["env"])

        # claude 目标保持不注入 ANTHROPIC_API_KEY（x-api-key / Bearer 网关挑食，不混）
        e2 = M._chain_entry_env(prov, "glm-5.1", target="claude-code")
        self.assertNotIn("ANTHROPIC_API_KEY", e2["env"])
        self.assertIn("ANTHROPIC_AUTH_TOKEN", e2["env"])

        # openai 兼容面：aider 拿 OPENAI_API_KEY + OPENAI_API_BASE
        prov2 = dict(prov, protocol="openai", base_url="https://y/v1")
        e3 = M._chain_entry_env(prov2, "glm-5.1", target="aider")
        self.assertEqual(e3["env"].get("OPENAI_API_KEY"), "sk-test")
        self.assertEqual(e3["env"].get("OPENAI_API_BASE"), "https://y/v1")


class TestTransientNewPatterns(BaseTest):
    def runTest(self):
        from app.core import runner as R
        # codex 网关断流（_codex_fail_msg 原文进 error）
        self.assertTrue(R._transient_error(
            "codex: stream disconnected before completion: "
            "stream closed before response.completed（退出码 1）"))
        # opencode 服务端 500
        self.assertTrue(R._transient_error(
            '退出码 1；stderr/stdout: Error: {"name": "UnknownError", '
            '"data": {"message": "Unexpected server error."}}'))
        # kimi 连接错误旧表就认（回归护栏）
        self.assertTrue(R._transient_error(
            "error: failed to run prompt: provider.connection_error: Connection error."))
