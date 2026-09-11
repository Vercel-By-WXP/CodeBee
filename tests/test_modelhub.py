# -*- coding: utf-8 -*-
"""模型接入层测试：CCSwitch 导入、绑定解析、难度路由、codex -c 注入。

注意：所有密钥均为运行时构造的假值，不含真实凭据。
"""
from __future__ import annotations

import json
import sqlite3

from base import BaseTest

FAKE_KEY = "sk-test-" + "abcdefgh" * 3      # 假密钥：运行时拼装
FAKE_KEY2 = "sk-test-" + "ijklmnop" * 3


class TestModelHub(BaseTest):
    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        # 1) 手动供应商 upsert + 脱敏
        self.assertIsNone(modelhub.upsert_provider({
            "name": "测试网关", "protocol": "anthropic",
            "base_url": "https://api.test.com/v1", "api_key": FAKE_KEY,
            "model": "test-pro"}))
        view = modelhub.provider_view()
        self.assertEqual(len(view), 1)
        self.assertNotIn(FAKE_KEY, json.dumps(view))  # API 层必须脱敏
        # 非法 base_url 拒绝
        err = modelhub.upsert_provider({"name": "x", "base_url": "ftp://x"})
        self.assertTrue(err)

        # 2) 绑定 + 难度路由解析
        pid = view[0]["id"]
        modelhub.set_binding("claude-code", provider_id=pid, difficulty_routing=True)
        # model_easy 未配置 → 回落供应商默认模型
        r_e = modelhub.resolve_binding("claude-code", "easy")
        self.assertEqual(r_e["model"], "test-pro")
        r2 = modelhub.resolve_binding("claude-code", "default")
        self.assertIsNotNone(r2)
        self.assertEqual(r2["env"]["ANTHROPIC_BASE_URL"], "https://api.test.com/v1")
        self.assertEqual(r2["env"]["ANTHROPIC_AUTH_TOKEN"], FAKE_KEY)
        self.assertEqual(r2["model"], "test-pro")
        # 配置难度模型后生效（key 留空 = 沿用旧值）
        modelhub.upsert_provider({"id": pid, "name": "测试网关", "protocol": "anthropic",
                                  "base_url": "https://api.test.com/v1",
                                  "model": "test-pro", "model_easy": "test-mini",
                                  "model_hard": "test-max", "api_key": ""})
        self.assertEqual(modelhub.resolve_binding("claude-code", "easy")["model"], "test-mini")
        self.assertEqual(modelhub.resolve_binding("claude-code", "hard")["model"], "test-max")
        # bind_agent 注入副本，且不污染原对象
        agent = {"id": "claude-code", "kind": "claude", "mode": "real", "command": "claude"}
        b = modelhub.bind_agent(agent, "hard")
        self.assertEqual(b["model"], "test-max")
        self.assertIn("ANTHROPIC_BASE_URL", b["env"])
        self.assertNotIn("env", agent)

        # 3) openai 协议（codex）→ codex_provider
        modelhub.upsert_provider({"name": "codex网关", "protocol": "openai",
                                  "base_url": "https://oai.test.com/v1",
                                  "api_key": FAKE_KEY2, "model": "gpt-x"})
        pid2 = modelhub.providers()[-1]["id"]
        modelhub.set_binding("codex-cli", provider_id=pid2)
        r5 = modelhub.resolve_binding("codex-cli")
        self.assertEqual(r5["env"]["ORCH_API_KEY"], FAKE_KEY2)
        self.assertEqual(r5["codex_provider"]["base_url"], "https://oai.test.com/v1")
        cagent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        self.assertIn("codex_provider", modelhub.bind_agent(cagent, "default"))

        # 4) 删除供应商 → 绑定自动解绑
        modelhub.delete_provider(pid)
        self.assertIsNone(modelhub.resolve_binding("claude-code"))


class TestCCSwitchImport(BaseTest):
    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        # 构造仿真 CCSwitch 数据库
        dbp = self.tmp / "cc-switch.db"
        con = sqlite3.connect(str(dbp))
        con.execute("CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, settings_config TEXT)")
        con.execute("INSERT INTO providers VALUES ('c1','claude','测试Claude',?)", (
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://claude.test/v1",
                                "ANTHROPIC_AUTH_TOKEN": FAKE_KEY,
                                "ANTHROPIC_MODEL": "m1"}}),))
        con.execute("INSERT INTO providers VALUES ('x1','codex','测试Codex',?)", (
            json.dumps({"auth": {"OPENAI_API_KEY": FAKE_KEY2},
                        "config": 'model = "gpt-t"\n[model_providers.custom]\n'
                                  'base_url = "https://codex.test/v1"\nwire_api = "responses"'}),))
        con.execute("INSERT INTO providers VALUES ('g1','gemini','忽略',?)",
                    (json.dumps({"env": {}}),))
        con.commit()
        con.close()
        modelhub.CCSWITCH_DB = str(dbp)
        n, _msg = modelhub.import_ccswitch()
        self.assertEqual(n, 2)  # claude + codex，gemini 跳过
        provs = {p["name"]: p for p in modelhub.providers()}
        self.assertEqual(provs["测试Claude"]["protocol"], "anthropic")
        self.assertEqual(provs["测试Claude"]["api_key"], FAKE_KEY)
        self.assertEqual(provs["[CC] 测试Codex"]["protocol"], "openai")
        self.assertEqual(provs["[CC] 测试Codex"]["base_url"], "https://codex.test/v1")
        self.assertEqual(provs["[CC] 测试Codex"]["model"], "gpt-t")
        # 重导入：幂等（不重复），并保留用户后期设置的难度映射
        modelhub.upsert_provider({"id": provs["测试Claude"]["id"], "name": "测试Claude",
                                  "protocol": "anthropic", "base_url": "https://claude.test/v1",
                                  "model_easy": "easy-m", "api_key": ""})
        n2, _ = modelhub.import_ccswitch()
        self.assertEqual(n2, 2)
        self.assertEqual(len(modelhub.providers()), 2)
        self.assertEqual(modelhub.providers()[0].get("model_easy"), "easy-m")


class TestCodexProviderArgs(BaseTest):
    def runTest(self):
        import app.core.runner as R
        captured = {}

        def fake(argv=None, **kw):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "",
                    "duration": 0, "cancelled": False, "timed_out": False}

        orig = R.run_process
        R.run_process = fake
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex",
                 "codex_provider": {"name": "orch", "base_url": "https://x/v1",
                                    "env_key": "ORCH_API_KEY", "wire_api": "responses"}}
        R.run_agent(agent, "hi", readonly=True)
        s = " ".join(str(x) for x in captured["argv"])
        self.assertIn('model_provider="orch"', s)
        self.assertIn("base_url=", s)
        self.assertIn("wire_api=", s)
        R.run_process = orig


class TestModelPriorityRouting(BaseTest):
    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "P1", "protocol": "openai",
                                  "base_url": "https://p1.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        # 直接写入模型列表（模拟拉取结果）：强弱顺序已排好
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["models"] = [
                    {"name": "gpt-x-pro", "enabled": True, "priority": 1},
                    {"name": "gpt-x", "enabled": True, "priority": 2},
                    {"name": "gpt-x-mini", "enabled": True, "priority": 3},
                    {"name": "gpt-x-old", "enabled": False, "priority": 4},
                ]
        modelhub._save(data)
        modelhub.set_binding("codex-cli", provider_id=pid, difficulty_routing=True)
        r_hard = modelhub.resolve_binding("codex-cli", "hard")
        self.assertEqual(r_hard["model"], "gpt-x-pro")            # 困难 → 优先级第 1
        self.assertEqual(r_hard["model_fallbacks"][:2], ["gpt-x", "gpt-x-mini"])  # 降级链跳过停用项
        r_easy = modelhub.resolve_binding("codex-cli", "easy")
        self.assertEqual(r_easy["model"], "gpt-x-mini")           # 简单 → 末位（最省）
        r_default = modelhub.resolve_binding("codex-cli", "default")
        self.assertEqual(r_default["model"], "gpt-x-pro")
        # bind_agent 透传降级链
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        b = modelhub.bind_agent(agent, "hard")
        self.assertEqual(b["model_fallbacks"][0], "gpt-x")

        # 启停与调序
        self.assertIsNone(modelhub.model_op(pid, "gpt-x-pro", "disable"))
        r2 = modelhub.resolve_binding("codex-cli", "hard")
        self.assertEqual(r2["model"], "gpt-x")                    # 停用后第一名顶上
        self.assertIsNone(modelhub.model_op(pid, "gpt-x-pro", "enable"))
        self.assertIsNone(modelhub.model_op(pid, "gpt-x-mini", "up"))
        ms = {m["name"]: m["priority"] for m in modelhub.providers()[0]["models"]}
        self.assertLess(ms["gpt-x-mini"], ms["gpt-x"])            # mini 上移一位


class TestAutoPriorityOrder(BaseTest):
    def runTest(self):
        from app.core import modelhub
        names = ["gpt-x-mini", "gpt-x-pro", "claude-y-flash", "claude-y-opus", "z-8b"]
        scored = sorted(names, key=lambda n: -modelhub._auto_priority(n))
        self.assertEqual(scored[0], "claude-y-opus")              # opus 最强
        self.assertIn(scored[-1], ("gpt-x-mini", "claude-y-flash", "z-8b"))  # lite 系垫底
        self.assertLess(modelhub._auto_priority("gpt-x-mini"),
                        modelhub._auto_priority("gpt-x-pro"))


class TestRunnerModelFallback(BaseTest):
    def runTest(self):
        import app.core.runner as R
        calls = []

        def fake(argv=None, **kw):
            calls.append(list(argv))
            # 第一次调用（主模型）返回 503 瞬态错误；第二次（降级模型）成功
            if "-m" in argv and argv[argv.index("-m") + 1] == "model-a":
                return {"ok": False, "exit_code": 1, "stdout": "",
                        "stderr": "unexpected status 503 Service Unavailable",
                        "duration": 0, "cancelled": False, "timed_out": False}
            return {"ok": True, "exit_code": 0,
                    "stdout": '{"type":"result","is_error":false,"result":"done","total_cost_usd":0,"usage":{}}',
                    "stderr": "", "duration": 0, "cancelled": False, "timed_out": False}

        orig = R.run_process
        R.run_process = fake
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex",
                 "model": "model-a", "model_fallbacks": ["model-b", "model-c"]}
        res = R.run_agent(agent, "hi", readonly=True)
        R.run_process = fake and orig
        self.assertTrue(res["ok"])
        self.assertEqual(res["model"], "model-b")                 # 降级到了下一个模型
        self.assertEqual(len(calls), 2)                           # 只多试了一次
        # 非瞬态错误不降级
        calls.clear()

        def fake2(argv=None, **kw):
            calls.append(argv)
            return {"ok": False, "exit_code": 1, "stdout": "",
                    "stderr": "Error: 401 unauthorized", "duration": 0,
                    "cancelled": False, "timed_out": False}
        R.run_process = fake2
        res2 = R.run_agent(agent, "hi", readonly=True)
        R.run_process = orig
        self.assertFalse(res2["ok"])
        self.assertEqual(len(calls), 1)                           # 401 不换模型


class TestDifficulty(BaseTest):
    def runTest(self):
        from app.core import modelhub
        self.assertEqual(modelhub.classify_difficulty("加个注释", ""), "easy")
        self.assertEqual(modelhub.classify_difficulty("重构整个认证模块，涉及并发", "pytest"), "hard")
        self.assertEqual(modelhub.classify_difficulty("x" * 150, "pytest"), "hard")


if __name__ == "__main__":
    import unittest as _u
    _u.main()
