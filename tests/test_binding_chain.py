# -*- coding: utf-8 -*-
"""跨厂商模型链测试：chain 唯一真源、逐条独立注入、失效跳过、迁移与级联清理。

一个 CLI 可绑定多个厂商的多个模型：链 = [{provider_id, model}, ...]，
第 1 条主模型，其余按序降级；运行时每条用自己的供应商凭据（env / codex -c）。
"""
from __future__ import annotations

import json
import os
import sys

from base import BaseTest

FAKE_KEY_A = "sk-test-" + "aaaaaaaa" * 3
FAKE_KEY_B = "sk-test-" + "bbbbbbbb" * 3


def _two_providers(modelhub):
    """建两个不同协议的供应商，返回 (pid_anthropic, pid_openai)。"""
    modelhub.upsert_provider({"name": "厂商A", "protocol": "anthropic",
                              "base_url": "https://a.test/v1", "api_key": FAKE_KEY_A})
    modelhub.upsert_provider({"name": "厂商B", "protocol": "openai",
                              "base_url": "https://b.test/v1", "api_key": FAKE_KEY_B})
    provs = {p["name"]: p["id"] for p in modelhub.providers()}
    return provs["厂商A"], provs["厂商B"]


class TestCrossProviderChain(BaseTest):
    def test_cross_provider_chain(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pa, pb = _two_providers(modelhub)

        # 1) chain 写入：跨厂商两厂商，兼容冗余同步
        modelhub.set_binding("claude-code", chain=[
            {"provider_id": pa, "model": "claude-x"},
            {"provider_id": pb, "model": "gpt-y"},
            {"provider_id": pa, "model": "claude-x"},   # 重复：被去重
            {"provider_id": pb, "model": ""},           # 空：被丢弃
        ])
        b = modelhub.bindings()["claude-code"]
        self.assertEqual(b["chain"], [{"provider_id": pa, "model": "claude-x"},
                                      {"provider_id": pb, "model": "gpt-y"}])
        self.assertEqual(b["models"], ["claude-x", "gpt-y"])
        self.assertEqual(b["model"], "claude-x")
        self.assertEqual(b["provider_id"], pa)          # 链首非空 provider

        # 2) 解析协议闸门：B 是 openai 且未适配 → claude-code 链里被跳过
        r = modelhub.resolve_binding("claude-code")
        self.assertEqual(r["model"], "claude-x")
        self.assertEqual(r["model_fallbacks"], [])
        self.assertEqual(len(r["call_chain"]), 1)

        # 2b) B 通过「模型接入」适配测试（wire_caps 记实测过的 anthropic 面）
        #     → 同一条链参与解析，按适配端点注入 ANTHROPIC_*（跨厂商降级恢复）
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pb:
                p["wire_caps"] = {"anthropic": {"base": "https://b.test",
                                                "wire_api": "messages",
                                                "checked_at": "2026-09-15 12:00"}}
        modelhub._save(data)
        r = modelhub.resolve_binding("claude-code")
        self.assertEqual(r["model_fallbacks"], ["gpt-y"])
        cc = r["call_chain"]
        self.assertEqual(len(cc), 2)
        self.assertEqual(cc[0]["env"]["ANTHROPIC_BASE_URL"], "https://a.test/v1")
        self.assertEqual(cc[0]["env"]["ANTHROPIC_MODEL"], "claude-x")
        self.assertNotIn("codex_provider", cc[0])
        self.assertEqual(cc[1]["env"]["ANTHROPIC_BASE_URL"], "https://b.test")
        self.assertEqual(cc[1]["env"]["ANTHROPIC_AUTH_TOKEN"], FAKE_KEY_B)
        self.assertNotIn("codex_provider", cc[1])
        # bind_agent 把链挂到副本
        agent = {"id": "claude-code", "kind": "claude", "mode": "real", "command": "claude"}
        ba = modelhub.bind_agent(agent)
        self.assertEqual(len(ba["call_chain"]), 2)
        self.assertEqual(ba["call_chain"][1]["env"]["ANTHROPIC_BASE_URL"], "https://b.test")

        # 3) 主供应商失效 → 链内跳过，适配过的 B 顶上（跨厂商自动降级）
        modelhub.providers_op([pa], "disable")
        r2 = modelhub.resolve_binding("claude-code")
        self.assertEqual(r2["model"], "gpt-y")
        self.assertEqual(len(r2["call_chain"]), 1)
        self.assertEqual(r2["call_chain"][0]["env"].get("ANTHROPIC_BASE_URL"), "https://b.test")
        # 全链失效 → 整体回落 CLI 默认
        modelhub.providers_op([pb], "disable")
        self.assertIsNone(modelhub.resolve_binding("claude-code"))
        modelhub.providers_op([pa, pb], "enable")

        # 4) 删除供应商 → 链内指向它的条目被清除
        modelhub.set_binding("claude-code", chain=[
            {"provider_id": pa, "model": "claude-x"},
            {"provider_id": pb, "model": "gpt-y"}])
        modelhub.providers_op([pb], "delete")
        b2 = modelhub.bindings()["claude-code"]
        self.assertEqual(b2["chain"], [{"provider_id": pa, "model": "claude-x"}])
        # 删掉的供应商作为编排者 → 自动失效
        modelhub.set_orchestrator(pb, model="gpt-y", enabled=True)
        modelhub.providers_op([pa], "delete")
        self.assertIsNone(modelhub.resolve_orchestrator())

    def test_disable_prunes_chain_entries(self):
        """停用即出调度（2026-09-22 拍板）：停用厂商/模型直接把条目从落盘链
        剔除，不靠运行时降级默默跳过占位；重新启用不回填；厂商默认模型与
        配置保留（停用可逆），与删除的连引用清空区分开。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pa, pb = _two_providers(modelhub)
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pb:
                p["models"] = [{"name": "gpt-y", "enabled": True, "priority": 1},
                               {"name": "gpt-z", "enabled": True, "priority": 2}]
                p["model"] = "gpt-y"   # 厂商默认模型：停用不该动它（可逆）
        modelhub._save(data)

        modelhub.set_binding("codex-cli", chain=[
            {"provider_id": pa, "model": "claude-x"},
            {"provider_id": pb, "model": "gpt-y"},
            {"provider_id": pb, "model": "gpt-z"}])

        # 停用模型 gpt-y：只摘 (pb, gpt-y) 一条，兼容冗余同步，默认模型保留
        self.assertIsNone(modelhub.model_op(pb, "gpt-y", "disable"))
        b = modelhub.bindings()["codex-cli"]
        self.assertEqual(b["chain"], [{"provider_id": pa, "model": "claude-x"},
                                      {"provider_id": pb, "model": "gpt-z"}])
        self.assertEqual(b["model"], "claude-x")
        prov_b = next(p for p in modelhub.providers() if p["id"] == pb)
        self.assertEqual(prov_b.get("model") or "", "gpt-y")
        # 重新启用模型：不回填
        self.assertIsNone(modelhub.model_op(pb, "gpt-y", "enable"))
        self.assertEqual(modelhub.bindings()["codex-cli"]["chain"],
                         [{"provider_id": pa, "model": "claude-x"},
                          {"provider_id": pb, "model": "gpt-z"}])

        # 停用厂商 pa：条目出链，主供应商跟随新链首
        self.assertEqual(modelhub.providers_op([pa], "disable"), (1, ""))
        b2 = modelhub.bindings()["codex-cli"]
        self.assertEqual(b2["chain"], [{"provider_id": pb, "model": "gpt-z"}])
        self.assertEqual(b2["provider_id"], pb)
        # 重新启用厂商：链不回填
        modelhub.providers_op([pa], "enable")
        self.assertEqual(modelhub.bindings()["codex-cli"]["chain"],
                         [{"provider_id": pb, "model": "gpt-z"}])

    def test_delete_model_clears_chain_entry(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pa, pb = _two_providers(modelhub)
        # 给 B 塞一个模型（模拟拉取结果），再绑链引用它
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pb:
                p["models"] = [{"name": "gpt-y", "enabled": True, "priority": 1}]
        modelhub._save(data)
        modelhub.set_binding("codex-cli", chain=[
            {"provider_id": pa, "model": "claude-x"},
            {"provider_id": pb, "model": "gpt-y"}])
        # 删除模型（墓碑）：链内对应条目清除，另一厂商的条目保留
        self.assertIsNone(modelhub.model_op(pb, "gpt-y", "delete"))
        b = modelhub.bindings()["codex-cli"]
        self.assertEqual(b["chain"], [{"provider_id": pa, "model": "claude-x"}])
        self.assertEqual(b["model"], "claude-x")


class TestChainMigration(BaseTest):
    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pa, _pb = _two_providers(modelhub)

        # 旧格式：{provider_id, models} 落盘 → migrate 生成 chain（幂等）
        modelhub._save({"providers": modelhub.providers(),
                        "bindings": {"claude-code": {"provider_id": pa,
                                                     "models": ["m1", "m2"],
                                                     "model": "m1"}}})
        self.assertTrue(modelhub.migrate_chains())
        b = modelhub.bindings()["claude-code"]
        self.assertEqual(b["chain"], [{"provider_id": pa, "model": "m1"},
                                      {"provider_id": pa, "model": "m2"}])
        self.assertEqual(b["models"], ["m1", "m2"])
        self.assertFalse(modelhub.migrate_chains())   # 幂等：已有 chain 不再动

        # 迁移后运行时解析与旧语义一致
        r = modelhub.resolve_binding("claude-code")
        self.assertEqual(r["model"], "m1")
        self.assertEqual(r["model_fallbacks"], ["m2"])

        # 备份存在（data/ 无回滚，改结构前必须留 .bak）
        self.assertTrue((self.data_dir / "models.json.bak").is_file())


class TestChainRuntimeFallback(BaseTest):
    """runner 按链逐条真实执行：第一条失败（瞬态）→ 换第二条（另一套 env）。"""

    def runTest(self):
        import app.core.runner as R
        py = sys.executable or "python"
        script = ("import os,sys;"
                  "sys.stderr.write('HTTP 503 unavailable');sys.exit(1)"
                  " if os.environ.get('TUTTI_FAKE_FAIL')=='1' else"
                  " print('CHAIN2-OK '+os.environ.get('TUTTI_FAKE_MODEL',''))")
        agent = {
            "id": "fake-cli", "kind": "generic", "mode": "real", "command": py,
            "argv_template": ["-c", script],
            "call_chain": [
                {"model": "m1", "env": {"TUTTI_FAKE_FAIL": "1", "TUTTI_FAKE_MODEL": "one"}},
                {"model": "m2", "env": {"TUTTI_FAKE_FAIL": "0", "TUTTI_FAKE_MODEL": "two"}},
            ],
        }
        out = R.run_agent(agent, "hi", readonly=True, timeout=60)
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["model"], "m2")          # 降级到第二条
        self.assertIn("CHAIN2-OK two", out["text"])   # 第二条自己的 env 生效

        # 无链：agent 级 codex_provider 必须保留（回归：不得误删）
        captured = {}

        def fake(argv=None, **kw):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "",
                    "duration": 0, "cancelled": False, "timed_out": False}

        orig = R.run_process
        R.run_process = fake
        agent2 = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex",
                  "codex_provider": {"name": "orch", "base_url": "https://x/v1",
                                     "env_key": "ORCH_API_KEY", "wire_api": "responses"}}
        R.run_agent(agent2, "hi", readonly=True)
        self.assertIn('model_provider="orch"', " ".join(str(x) for x in captured["argv"]))
        R.run_process = orig


if __name__ == "__main__":
    import unittest as _u
    _u.main()
