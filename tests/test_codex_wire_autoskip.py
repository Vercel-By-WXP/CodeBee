# -*- coding: utf-8 -*-
"""codex wire 不兼容自动冷却回归。

2026-09-17 mo-so 实测：codex 0.154 只讲 responses wire（chat 被官方移除），
绑定到只有 chat completions 的讯飞 MaaS 后每次 404，路由仍按高能力基线反复
派它。修后闭环：runner 识别 404+no Route matched / wire_api no longer
supported 特征 → modelhub.note_codex_wire_dead 供应商级冷却 30 分钟 →
resolve_binding 跳过该条 → 全链失效解析为 None → 路由绑定分 -25 自动降权。
全程自动，无需人工改绑定。
"""
from __future__ import annotations

import json

from base import BaseTest

_MODELS = {
    "providers": [
        {"id": "prov-x", "name": "ChatOnly", "protocol": "openai",
         "base_url": "https://x.example/v2", "api_key": "sk-1",
         "enabled": True, "wire_api": "responses"},
    ],
    "bindings": {"codex-cli": {"provider_id": "prov-x", "model": "m1",
                               "chain": [{"provider_id": "prov-x", "model": "m1"}],
                               "models": ["m1"]}},
}


class TestCodexWireAutoSkip(BaseTest):
    def runTest(self):
        from app.core import modelhub as MH
        from app.core import router
        MH._FILE.write_text(json.dumps(_MODELS), encoding="utf-8")

        # 冷却前：链正常解析
        b = MH.resolve_binding("codex-cli")
        self.assertTrue(b and b.get("call_chain"))

        # 记冷却后：供应商被跳过 → 链解析为空 → 绑定分 -25（codex 自动降权）
        MH.note_codex_wire_dead("prov-x")
        prov = next(p for p in MH._load()["providers"] if p["id"] == "prov-x")
        self.assertTrue(MH.codex_wire_blocked(prov))
        self.assertIsNone(MH.resolve_binding("codex-cli"))
        self.assertEqual(router._binding_bonus("codex-cli"), -25.0)


class TestCodexChatPreSkip(BaseTest):
    """chat-only 供应商在链解析时就剔除，不必等 codex 拒载配置再冷却。"""

    _MIXED = {
        "providers": [
            {"id": "prov-c", "name": "ChatOnly", "protocol": "openai",
             "base_url": "https://c.example/v2", "api_key": "sk-1",
             "enabled": True, "wire_api": "chat"},
            {"id": "prov-r", "name": "RespOK", "protocol": "openai",
             "base_url": "https://r.example/v1", "api_key": "sk-2",
             "enabled": True, "wire_api": "responses"},
        ],
        "bindings": {"codex-cli": {"chain": [
            {"provider_id": "prov-c", "model": "m1"},
            {"provider_id": "prov-r", "model": "m2"}], "models": ["m1", "m2"]}},
    }

    def runTest(self):
        from app.core import modelhub as MH
        MH._FILE.write_text(json.dumps(self._MIXED), encoding="utf-8")
        # 链首的 chat-only 条目起跑前就被剔除，直接落到 responses 供应商
        b = MH.resolve_binding("codex-cli")
        self.assertTrue(b and b.get("call_chain"))
        self.assertEqual([e["model"] for e in b["call_chain"]], ["m2"])
        # 全 chat 链 → 解析为空（死链闸门接手），不烧 CLI 尝试
        models = json.loads(json.dumps(self._MIXED))
        models["bindings"]["codex-cli"]["chain"] = [{"provider_id": "prov-c", "model": "m1"}]
        MH._FILE.write_text(json.dumps(models), encoding="utf-8")
        self.assertIsNone(MH.resolve_binding("codex-cli"))
        # 非 codex 目标不受影响：chat-only 对 opencode 仍可用
        models["bindings"]["opencode"] = {"chain": [{"provider_id": "prov-c", "model": "m1"}],
                                          "models": ["m1"]}
        MH._FILE.write_text(json.dumps(models), encoding="utf-8")
        b2 = MH.resolve_binding("opencode")
        self.assertTrue(b2 and b2.get("call_chain"))


class TestCodexSyncChatGuard(BaseTest):
    """一键打开的 codex 配置同步：chat wire 拒写，宁明确报错不落坏配置。"""

    def runTest(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock
        from app.core import manager
        # 防毒闸（011023c）放行形态：假 HOME 在临时目录下（resolve+parents 守卫），
        # 配置文件落在假家——chat 拒写 / responses 落盘的原语义照常验证。
        home_p = Path(tempfile.mkdtemp(prefix="cb-fakehome-"))
        scratch = str(home_p / ("codebee-test-codex-%d.toml" % os.getpid()))
        entry = {"id": "codex-cli", "config": {"path": scratch}}
        try:
            # 域名用 .internal：7b8c2d5 起 *.test/*.example 等是死端点，
            # 会被 _is_dead_endpoint 先拒掉，轮不到 chat 守卫出场
            with mock.patch("app.core.manager.os.path.expanduser",
                            lambda p: str(home_p) if p == "~" else p):
                err = manager._sync_codex_settings(
                    entry, "m1", {"name": "orch", "base_url": "https://c.internal/v2",
                                  "env_key": "ORCH_API_KEY", "wire_api": "chat"})
                self.assertTrue(err and "chat" in err)
                self.assertFalse(os.path.exists(scratch), "chat wire 不得落盘")
                err2 = manager._sync_codex_settings(
                    entry, "m1", {"name": "orch", "base_url": "https://r.internal/v1",
                                  "env_key": "ORCH_API_KEY", "wire_api": "responses"})
                self.assertIsNone(err2)
            self.assertTrue(os.path.exists(scratch))
            body = open(scratch, encoding="utf-8").read()
            self.assertIn('wire_api = "responses"', body)
        finally:
            for suffix in ("", ".bak"):
                try:
                    os.remove(scratch + suffix)
                except OSError:
                    pass


class TestRunnerSignatureHooksCooldown(BaseTest):
    def runTest(self):
        from app.core import runner as R
        import app.core.modelhub as MH
        MH._FILE.write_text(json.dumps(_MODELS), encoding="utf-8")
        called = []
        orig_fn = MH.note_codex_wire_dead
        MH.note_codex_wire_dead = lambda pid, minutes=30: called.append(pid)
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        agent["call_chain"] = [{"model": "m1", "env": {"ORCH_API_KEY": "k"},
                                "provider_id": "prov-x",
                                "codex_provider": {"name": "orch",
                                                   "base_url": "https://x.example/v2",
                                                   "env_key": "ORCH_API_KEY",
                                                   "wire_api": "responses"}}]
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t"}),
            json.dumps({"type": "error", "message":
                        "unexpected status 404 Not Found: no Route matched with "
                        "those values, url: https://x.example/v2/responses"}),
        ])
        orig = R.run_process
        R.run_process = lambda **kw: {"ok": False, "exit_code": 1, "stdout": stdout,
                                      "stderr": "", "duration": 0, "cancelled": False,
                                      "timed_out": False, "stalled": False}
        try:
            out = R.run_agent(agent, "hi", readonly=True, timeout=60)
        finally:
            R.run_process = orig
            MH.note_codex_wire_dead = orig_fn
        self.assertFalse(out["ok"])
        self.assertEqual(called, ["prov-x"])
