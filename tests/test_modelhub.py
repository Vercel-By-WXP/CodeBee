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
        self.assertEqual(b["reasoning_effort"], "high")
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
        self.assertEqual(provs["测试Codex"]["protocol"], "openai")
        self.assertEqual(provs["测试Codex"]["base_url"], "https://codex.test/v1")
        self.assertEqual(provs["测试Codex"]["model"], "gpt-t")
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

    def test_explicit_chain_difficulty_routing_reorders_only_when_enabled(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("codex-cli", chain=[
            {"provider_id": pid, "model": "premium"},
            {"provider_id": pid, "model": "cheap"}],
            difficulty_routing=False)
        data = modelhub._load()
        data["providers"][0]["tier"] = "standard"
        data["providers"][0]["models"] = [
            {"name": "premium", "tier": "premium", "priority": 1},
            {"name": "cheap", "tier": "budget", "priority": 2}]
        modelhub._save(data)
        self.assertEqual(modelhub.resolve_binding("codex-cli", "easy")["model"], "premium")
        modelhub.set_binding("codex-cli", difficulty_routing=True)
        self.assertEqual(modelhub.resolve_binding("codex-cli", "easy")["model"], "cheap")

    def test_unbound_agent_uses_runtime_recommendation_without_persisting(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "Budget", "protocol": "openai",
                                  "base_url": "https://budget.test/v1",
                                  "api_key": FAKE_KEY, "tier": "budget"})
        modelhub.upsert_provider({"name": "Premium", "protocol": "openai",
                                  "base_url": "https://premium.test/v1",
                                  "api_key": FAKE_KEY2, "tier": "premium"})
        data = modelhub._load()
        data["providers"][0]["models"] = [
            {"name": "cheap", "enabled": True, "priority": 1,
             "tier": "budget"}]
        data["providers"][1]["models"] = [
            {"name": "strong", "enabled": True, "priority": 1,
             "tier": "premium"}]
        modelhub._save(data)
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real"}

        easy = modelhub.bind_agent(agent, "easy", task_type="code")
        hard = modelhub.bind_agent(agent, "hard", task_type="code")

        self.assertEqual(easy["binding_mode"], "auto")
        self.assertFalse(easy["binding_configured"])
        self.assertEqual(easy["model"], "cheap")
        self.assertEqual(hard["model"], "strong")
        self.assertEqual(easy["reasoning_effort"], "low")
        self.assertEqual(hard["reasoning_effort"], "high")
        self.assertEqual(modelhub.bindings(), {}, "自动推荐不得落盘成显式绑定")

    def test_unbound_recommendation_expands_keys_and_skips_cooled_key(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "Multi key", "protocol": "openai",
                                  "base_url": "https://multi.test/v1",
                                  "api_key": FAKE_KEY, "model": "m1"})
        pid = modelhub.providers()[0]["id"]
        modelhub.key_op(pid, "add", key=FAKE_KEY2, label="backup")
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real"}

        first = modelhub.bind_agent(agent, "easy", task_type="code")
        self.assertEqual([e["key_id"] for e in first["call_chain"]], ["k1", "k2"])
        self.assertEqual([e["env"]["ORCH_API_KEY"] for e in first["call_chain"]],
                         [FAKE_KEY, FAKE_KEY2])

        modelhub.note_key_error(pid, "k1", "HTTP 402 insufficient balance")
        second = modelhub.bind_agent(agent, "easy", task_type="code")
        self.assertEqual([e["key_id"] for e in second["call_chain"]], ["k2"])
        self.assertEqual(modelhub.bindings(), {}, "多 KEY 自动推荐同样不得落盘")

    def test_explicit_binding_overrides_runtime_recommendation(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "Chosen", "protocol": "openai",
                                  "base_url": "https://chosen.test/v1",
                                  "api_key": FAKE_KEY, "model": "chosen"})
        modelhub.upsert_provider({"name": "Other", "protocol": "openai",
                                  "base_url": "https://other.test/v1",
                                  "api_key": FAKE_KEY2, "model": "other"})
        chosen = modelhub.providers()[0]["id"]
        modelhub.set_binding("codex-cli", provider_id=chosen, model="chosen")
        out = modelhub.bind_agent(
            {"id": "codex-cli", "kind": "codex", "mode": "real"},
            "hard", task_type="code")
        self.assertEqual(out["binding_mode"], "explicit")
        self.assertTrue(out["binding_configured"])
        self.assertEqual(out["model"], "chosen")

    def test_provider_only_binding_is_explicit_override(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "Chosen", "protocol": "openai",
                                  "base_url": "https://chosen.test/v1",
                                  "api_key": FAKE_KEY, "model": "provider-default"})
        chosen = modelhub.providers()[0]["id"]
        modelhub.set_binding("codex-cli", provider_id=chosen, chain=[])
        out = modelhub.bind_agent(
            {"id": "codex-cli", "kind": "codex", "mode": "real"},
            "default", task_type="code")
        self.assertEqual(out["binding_mode"], "explicit")
        self.assertTrue(out["binding_configured"])
        self.assertEqual(out["model"], "provider-default")

    def test_unrelated_cli_does_not_inherit_claude_binding(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "Claude gateway", "protocol": "anthropic",
                                  "base_url": "https://claude.test/v1",
                                  "api_key": FAKE_KEY, "model": "claude-x"})
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("claude-code", provider_id=pid, model="claude-x")

        self.assertIsNone(modelhub.resolve_binding("opencode"))
        out = modelhub.bind_agent(
            {"id": "opencode", "kind": "opencode", "mode": "real"},
            "default", task_type="code")
        self.assertFalse(out["binding_configured"])
        self.assertNotEqual(out.get("binding_mode"), "explicit")

    def test_unbound_agent_falls_back_to_cli_default_when_no_compatible_provider(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "Anthropic only", "protocol": "anthropic",
                                  "base_url": "https://anthropic.test/v1",
                                  "api_key": FAKE_KEY, "model": "claude-x"})
        out = modelhub.bind_agent(
            {"id": "codex-cli", "kind": "codex", "mode": "real"},
            "default", task_type="code")
        self.assertEqual(out["binding_mode"], "cli_default")
        self.assertFalse(out["binding_configured"])
        self.assertNotIn("call_chain", out)


class TestModelDelete(BaseTest):
    """删除模型 = 标记隐藏：从路由/列表中消失，且刷新不会把它带回来。"""

    def _seed(self, modelhub):
        modelhub.upsert_provider({"name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["models"] = [
                    {"name": "m1", "enabled": True, "priority": 1},
                    {"name": "m2", "enabled": True, "priority": 2},
                    {"name": "m3", "enabled": True, "priority": 3},
                ]
                p["model"] = "m2"       # 默认模型指向待删项
                p["model_hard"] = "m2"  # 困难映射同样指向它
        modelhub._save(data)
        modelhub.set_binding("claude-code", provider_id=pid, model="m2",
                             difficulty_routing=True)
        return pid

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._seed(modelhub)

        # 1) 删除：标记隐藏而非物理移除，悬空引用同步清空
        self.assertIsNone(modelhub.model_op(pid, "m2", "delete"))
        prov = modelhub.providers()[0]
        self.assertTrue({m["name"]: m for m in prov["models"]}["m2"]["hidden"])
        self.assertFalse({m["name"]: m for m in prov["models"]}["m2"]["enabled"])
        self.assertEqual(prov["model"], "")
        self.assertEqual(prov["model_hard"], "")
        self.assertEqual(modelhub.bindings()["claude-code"]["model"], "")
        # 路由与列表视图都不再出现，可见项优先级重排为 1..2
        visible = modelhub._enabled_models(prov)
        self.assertEqual([m["name"] for m in visible], ["m1", "m3"])
        self.assertEqual([m["priority"] for m in visible], [1, 2])
        self.assertNotIn("m2", [r["name"] for r in modelhub.models_view()])

        # 2) 刷新模型列表不会让已删模型“复活”（接口仍返回 / 不再返回都试）
        orig = modelhub._fetch_models_http
        try:
            modelhub._fetch_models_http = lambda *a, **k: (["m1", "m2", "m3"], "")
            modelhub.refresh_models(pid)
            after = {m["name"]: m for m in modelhub.providers()[0]["models"]}
            self.assertTrue(after["m2"]["hidden"])
            self.assertNotIn("m2", [m["name"] for m in
                                    modelhub._enabled_models(modelhub.providers()[0])])
            # 接口不再返回 m2 时墓碑也保留，否则下次拉取会把它当新模型
            modelhub._fetch_models_http = lambda *a, **k: (["m1", "m3"], "")
            modelhub.refresh_models(pid)
            self.assertTrue({m["name"]: m for m in
                             modelhub.providers()[0]["models"]}["m2"]["hidden"])
        finally:
            modelhub._fetch_models_http = orig

        # 3) 恢复：重新可见、重新启用并置顶（启用即排最前）
        self.assertIsNone(modelhub.model_op(pid, "m2", "restore"))
        prov = modelhub.providers()[0]
        self.assertFalse({m["name"]: m for m in prov["models"]}["m2"]["hidden"])
        self.assertEqual([m["name"] for m in modelhub._enabled_models(prov)][0], "m2")

        # 4) restore-all 与错误分支
        self.assertIsNone(modelhub.model_op(pid, "m1", "delete"))
        self.assertIsNone(modelhub.model_op(pid, "", "restore-all"))
        self.assertFalse([m for m in modelhub.providers()[0]["models"] if m.get("hidden")])
        self.assertTrue(modelhub.model_op(pid, "nope", "delete"))    # 模型不存在
        self.assertTrue(modelhub.model_op("nope", "m1", "delete"))   # 供应商不存在
        self.assertTrue(modelhub.model_op(pid, "", "restore-all"))   # 没有已删模型
        # 已删除的模型不能调序
        self.assertIsNone(modelhub.model_op(pid, "m1", "delete"))
        self.assertTrue(modelhub.model_op(pid, "m1", "up"))


class TestBatchOps(BaseTest):
    """批量启停 / 删除：模型与供应商，且校验失败时不得只改一半。"""

    def _seed(self, modelhub, n=3):
        modelhub.upsert_provider({"name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["models"] = [{"name": "m%d" % i, "enabled": True, "priority": i}
                               for i in range(1, n + 1)]
        modelhub._save(data)
        return pid

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._seed(modelhub)

        # 1) 批量停用 / 启用
        self.assertEqual(modelhub.model_ops(pid, ["m1", "m3"], "disable"), (2, ""))
        ms = {m["name"]: m for m in modelhub.providers()[0]["models"]}
        self.assertFalse(ms["m1"]["enabled"])
        self.assertFalse(ms["m3"]["enabled"])
        self.assertTrue(ms["m2"]["enabled"])
        self.assertEqual([m["name"] for m in modelhub._enabled_models(modelhub.providers()[0])],
                         ["m2"])
        self.assertEqual(modelhub.model_ops(pid, ["m1", "m3"], "enable"), (2, ""))
        self.assertEqual(len(modelhub._enabled_models(modelhub.providers()[0])), 3)
        # 已是目标状态 → 0 改动，且不算错误
        self.assertEqual(modelhub.model_ops(pid, ["m1"], "enable"), (0, ""))
        # 名字去重
        self.assertEqual(modelhub.model_ops(pid, ["m1", "m1"], "disable"), (1, ""))

        # 2) 批量删除：标记隐藏 + 清悬空引用 + 可见项优先级连号
        modelhub.upsert_provider({"id": pid, "name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "model": "m1",
                                  "api_key": ""})
        modelhub.set_binding("claude-code", provider_id=pid, model="m2")
        self.assertEqual(modelhub.model_ops(pid, ["m1", "m2"], "delete"), (2, ""))
        ms = {m["name"]: m for m in modelhub.providers()[0]["models"]}
        self.assertTrue(ms["m1"]["hidden"] and ms["m2"]["hidden"])
        self.assertEqual(modelhub.providers()[0]["model"], "")
        self.assertEqual(modelhub.bindings()["claude-code"]["model"], "")
        vis = modelhub._enabled_models(modelhub.providers()[0])
        self.assertEqual([m["name"] for m in vis], ["m3"])
        self.assertEqual([m["priority"] for m in vis], [1])
        self.assertEqual([r["name"] for r in modelhub.models_view()], ["m3"])

        # 3) 恢复所选：重新启用并置顶
        self.assertEqual(modelhub.model_ops(pid, ["m1"], "restore"), (1, ""))
        self.assertEqual([m["name"] for m in modelhub._enabled_models(modelhub.providers()[0])],
                         ["m1", "m3"])
        # 启停不得静默跳过已删除项：混选即整体拒绝，可见项也不改
        n, err = modelhub.model_ops(pid, ["m3", "m2"], "disable")
        self.assertEqual(n, 0)
        self.assertIn("m2", err)
        self.assertTrue({m["name"]: m for m in
                         modelhub.providers()[0]["models"]}["m3"]["enabled"])

        # 4) 任一名不存在 → 整体不生效（不落一半），也不留错误状态
        n, err = modelhub.model_ops(pid, ["m1", "ghost"], "disable")
        self.assertEqual(n, 0)
        self.assertIn("ghost", err)
        self.assertTrue({m["name"]: m for m in
                         modelhub.providers()[0]["models"]}["m1"]["enabled"])
        # 空选 / 未知 op / 供应商不存在
        self.assertTrue(modelhub.model_ops(pid, [], "disable")[1])
        self.assertTrue(modelhub.model_ops(pid, ["m1"], "explode")[1])
        self.assertTrue(modelhub.model_ops("nope", ["m1"], "disable")[1])

        # 5) 供应商批量停用：运行时回落 CLI 默认，配置保留
        # （codex-cli×openai 是协议合法配对；2026-09-15 起 resolve 按 CLI 过滤 wire 协议）
        modelhub.set_binding("codex-cli", provider_id=pid, model="")
        self.assertIsNotNone(modelhub.resolve_binding("codex-cli"))
        self.assertEqual(modelhub.providers_op([pid], "disable"), (1, ""))
        self.assertFalse(modelhub.providers()[0]["enabled"])
        self.assertIsNone(modelhub.resolve_binding("codex-cli"))
        self.assertFalse([p for p in modelhub.provider_view() if p["id"] == pid][0]["enabled"])
        self.assertEqual(modelhub.providers_op([pid], "disable")[0], 0)   # 重复停用不计数
        # 保存配置（upsert）不得把已停用状态覆盖回启用
        modelhub.upsert_provider({"id": pid, "name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "api_key": ""})
        self.assertFalse(modelhub.providers()[0]["enabled"])
        self.assertEqual(modelhub.providers_op([pid], "enable"), (1, ""))
        self.assertIsNotNone(modelhub.resolve_binding("codex-cli"))

        # 6) 混合不存在的 id → 整体不生效；删除则连带解绑
        modelhub.upsert_provider({"name": "Q", "protocol": "openai",
                                  "base_url": "https://q.test/v1", "api_key": FAKE_KEY})
        n, err = modelhub.providers_op([pid, "ghost"], "delete")
        self.assertEqual(n, 0)
        self.assertTrue(err)
        self.assertEqual(len(modelhub.providers()), 2)
        n, err = modelhub.providers_op([p["id"] for p in modelhub.providers()], "delete")
        self.assertEqual((n, err), (2, ""))
        self.assertEqual(modelhub.providers(), [])
        self.assertEqual(modelhub.bindings()["claude-code"]["provider_id"], "")
        # 空选 / 未知 op
        self.assertTrue(modelhub.providers_op([], "delete")[1])
        self.assertTrue(modelhub.providers_op(["ghost"], "explode")[1])


class TestProviderEnabledPersistence(BaseTest):
    """重导入 CCSwitch 时不得复活已停用状态，也不得丢掉模型列表与删除墓碑。"""

    def runTest(self):
        from app.core import modelhub
        d = self.data_dir
        modelhub._FILE = d / "models.json"
        db = d / "cc-switch.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, settings_config TEXT)")
        con.execute("CREATE TABLE model_pricing (model_id TEXT, input_cost_per_million REAL,"
                    " output_cost_per_million REAL)")
        con.execute("INSERT INTO providers VALUES (?,?,?,?)",
                    ("p1", "claude", "网关",
                     json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://api.test.com/v1",
                                         "ANTHROPIC_AUTH_TOKEN": FAKE_KEY}})))
        con.commit()
        con.close()
        orig_db = modelhub.CCSWITCH_DB
        modelhub.CCSWITCH_DB = str(db)
        try:
            n, _ = modelhub.import_ccswitch()
            self.assertEqual(n, 1)
            pid = modelhub.providers()[0]["id"]
            data = modelhub._load()
            for p in data["providers"]:
                p["enabled"] = False
                p["models"] = [{"name": "old", "enabled": True, "priority": 1},
                               {"name": "gone", "enabled": False, "priority": 2, "hidden": True}]
                p["models_fetched_at"] = "2026-01-01 00:00"
            modelhub._save(data)

            modelhub.import_ccswitch()          # 重新导入同一来源
            p = modelhub.providers()[0]
            self.assertFalse(p["enabled"])      # 停用状态保留
            self.assertEqual(p["id"], pid)      # id 不变 → 绑定不断链
            self.assertEqual([m["name"] for m in p["models"]], ["old", "gone"])
            self.assertTrue({m["name"]: m for m in p["models"]}["gone"]["hidden"])
        finally:
            modelhub.CCSWITCH_DB = orig_db


class TestRefreshKeepsManualOrder(BaseTest):
    """刷新拉到新模型时不能顶掉既有手动排序（#1 必须还是原来的默认模型）。"""

    def _seed(self, modelhub):
        modelhub.upsert_provider({"name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["models"] = [{"name": "keep-me", "enabled": True, "priority": 1},
                               {"name": "second", "enabled": True, "priority": 2}]
        modelhub._save(data)
        return pid

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._seed(modelhub)
        orig = modelhub._fetch_models_http
        try:
            # 新模型按强弱排在既有顺序之后（opus 强于 mini），既有顺序原样保留
            modelhub._fetch_models_http = lambda *a, **k: (
                ["new-mini", "new-opus", "keep-me", "second"], "")
            n, err = modelhub.refresh_models(pid)
            self.assertFalse(err)
            ms = modelhub.providers()[0]["models"]
            order = [m["name"] for m in sorted(ms, key=lambda m: m["priority"])]
            self.assertEqual(order, ["keep-me", "second", "new-opus", "new-mini"])
            self.assertEqual(sorted(m["priority"] for m in ms), [1, 2, 3, 4])
            # 默认模型(#1)仍是用户原来选的那个
            self.assertEqual(modelhub._enabled_models(modelhub.providers()[0])[0]["name"],
                             "keep-me")

            # 已删除的墓碑排在新模型之后（恢复时才自然回到可见列表末尾）
            self.assertIsNone(modelhub.model_op(pid, "second", "delete"))
            modelhub._fetch_models_http = lambda *a, **k: (
                ["keep-me", "second", "new-opus", "new-mini", "newer-flash"], "")
            modelhub.refresh_models(pid)
            prov = modelhub.providers()[0]
            prio = {m["name"]: m["priority"] for m in prov["models"]}
            self.assertTrue({m["name"]: m for m in prov["models"]}["second"]["hidden"])
            visible = modelhub._enabled_models(prov)
            self.assertNotIn("second", [m["name"] for m in visible])
            self.assertGreater(prio["second"], max(m["priority"] for m in visible))
        finally:
            modelhub._fetch_models_http = orig


class TestEnabledFloatToFront(BaseTest):
    """启用置顶：启用的供应商/模型自动排到最前，停用的退到启用块之后。"""

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        # 供应商：停用的掉出前排，再启用的自动置顶（落盘顺序 + 视图顺序一致）
        for name in ("B", "C"):
            modelhub.upsert_provider({"name": name, "protocol": "openai",
                                      "base_url": "https://%s.test/v1" % name.lower(),
                                      "api_key": FAKE_KEY})
        by = {p["name"]: p for p in modelhub.providers()}
        self.assertEqual(modelhub.providers_op([by["B"]["id"]], "disable"), (1, ""))
        self.assertEqual([p["name"] for p in modelhub.providers()], ["C", "B"])
        self.assertEqual([p["name"] for p in modelhub.provider_view()], ["C", "B"])
        self.assertEqual(modelhub.providers_op([by["B"]["id"]], "enable"), (1, ""))
        self.assertEqual([p["name"] for p in modelhub.providers()], ["B", "C"])

        # 模型：初始顺序里停用项夹在中间（历史数据），任何启停操作都会自愈成启用块在前
        pid = by["C"]["id"]
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["models"] = [
                    {"name": "m1", "enabled": True, "priority": 1},
                    {"name": "m2", "enabled": False, "priority": 2},
                    {"name": "m3", "enabled": True, "priority": 3},
                ]
        modelhub._save(data)

        def order():
            prov = next(p for p in modelhub.providers() if p["id"] == pid)
            return [m["name"] for m in sorted(prov["models"],
                                              key=lambda m: m["priority"])]

        # 停用 m1 → m3 顶上，两个停用的排在启用块之后
        self.assertEqual(modelhub.model_ops(pid, ["m1"], "disable"), (1, ""))
        self.assertEqual(order(), ["m3", "m1", "m2"])
        # 启用 m2 → 置顶成为 #1（默认模型）
        self.assertEqual(modelhub.model_ops(pid, ["m2"], "enable"), (1, ""))
        self.assertEqual(order(), ["m2", "m3", "m1"])
        self.assertEqual(modelhub._enabled_models(
            next(p for p in modelhub.providers() if p["id"] == pid))[0]["name"], "m2")
        # 拖拽排序不能把停用模型排到启用模型前面（m1 塞到最前会被收回）
        self.assertIsNone(modelhub.reorder_models(pid, ["m1", "m3", "m2"]))
        self.assertEqual(order(), ["m3", "m2", "m1"])
        # 批量启用按原相对顺序整体置顶
        self.assertEqual(modelhub.model_ops(pid, ["m1"], "enable"), (1, ""))
        self.assertEqual(order(), ["m1", "m3", "m2"])


class TestDuplicateIdRepair(BaseTest):
    """历史数据里重复的 prov-N id 必须在读取时去重，否则按供应商的操作会打错对象。"""

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        data = {
            "providers": [
                {"id": "prov-11", "name": "A", "protocol": "openai",
                 "base_url": "https://a.test/v1", "api_key": FAKE_KEY},
                {"id": "prov-13", "name": "B", "protocol": "openai",
                 "base_url": "https://b.test/v1", "api_key": FAKE_KEY},
                {"id": "prov-11", "name": "C", "protocol": "openai",
                 "base_url": "https://c.test/v1", "api_key": FAKE_KEY},
                {"id": "prov-13", "name": "D", "protocol": "openai",
                 "base_url": "https://d.test/v1", "api_key": FAKE_KEY},
            ],
            "bindings": {"codex-cli": {"provider_id": "prov-11"}},
        }
        modelhub._save(data)

        provs = modelhub.providers()
        ids = [p["id"] for p in provs]
        self.assertEqual(len(ids), len(set(ids)))          # 全部唯一
        self.assertEqual([p["name"] for p in provs], ["A", "B", "C", "D"])  # 顺序不变
        self.assertEqual(ids[0], "prov-11")                # 首次出现的保留原 id
        self.assertEqual(ids[1], "prov-13")
        # 绑定仍解析到首次出现的那条（与修复前一致，不会静默改绑）
        # （codex-cli×openai 协议合法；resolve 自 2026-09-15 起按 CLI 过滤 wire 协议）
        r = modelhub.resolve_binding("codex-cli")
        self.assertEqual(r["provider"]["name"], "A")

        # 去重后：可以精确停用「原来撞号」的那条，另一条不受影响
        self.assertEqual(modelhub.providers_op([ids[2]], "disable"), (1, ""))
        by_name = {p["name"]: p for p in modelhub.providers()}
        self.assertFalse(by_name["C"].get("enabled", True))
        self.assertTrue(by_name["A"].get("enabled", True))
        self.assertTrue(by_name["D"].get("enabled", True))

        # 清理落盘后再次读取仍然唯一（幂等）
        data2 = modelhub._load()
        ids2 = [p["id"] for p in data2["providers"]]
        self.assertEqual(len(ids2), len(set(ids2)))


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


class TestStripEndpoint(BaseTest):
    """误填完整接口地址时要收敛回基址（Trae 自定义模型保存的就是完整 URL）。"""

    def runTest(self):
        from app.core import modelhub
        cases = [
            ("https://api.x.com/v1/chat/completions", "https://api.x.com/v1"),
            ("https://api.x.com/v1/completions", "https://api.x.com/v1"),
            ("https://api.x.com/v1/responses", "https://api.x.com/v1"),
            ("https://api.x.com/v1/messages", "https://api.x.com/v1"),
            ("https://api.x.com/v1beta/models", "https://api.x.com"),
            ("https://api.x.com/v1/", "https://api.x.com/v1"),
            ("https://api.x.com", "https://api.x.com"),
        ]
        for raw, want in cases:
            self.assertEqual(modelhub._strip_endpoint(raw), want, raw)
        p = modelhub._prov("X", "openai", "https://api.x.com/v1/chat/completions",
                           FAKE_KEY, "trae", "trae:x")
        self.assertEqual(p["base_url"], "https://api.x.com/v1")


class TestMultiSourceImport(BaseTest):
    """多来源扫描 + 导入：各工具配置形状不同，最终都归一化为供应商条目。"""

    def _patch(self, modelhub, **kw):
        """临时替换模块级路径常量，返回还原函数。"""
        orig = {k: getattr(modelhub, k) for k in kw}
        for k, v in kw.items():
            setattr(modelhub, k, v)

        def restore():
            for k, v in orig.items():
                setattr(modelhub, k, v)
        return restore

    def _fixtures(self, m):
        """造一套仿真配置文件，模拟本机装了这些工具。"""
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)

        # Claude Code
        claude = home / "claude-settings.json"
        claude.write_text(json.dumps({"env": {
            "ANTHROPIC_BASE_URL": "https://claude.test",
            "ANTHROPIC_AUTH_TOKEN": FAKE_KEY, "ANTHROPIC_MODEL": "claude-x"}}),
            encoding="utf-8")

        # Codex CLI：两个 model_providers，active 为 custom
        codex = home / "codex"
        codex.mkdir(exist_ok=True)
        (codex / "config.toml").write_text(
            'model = "gpt-x"\nmodel_provider = "custom"\n\n'
            '[model_providers.custom]\nname = "Codex主"\n'
            'base_url = "https://codex.test/v1"\nwire_api = "responses"\n\n'
            '[model_providers.backup]\nname = "Codex备"\n'
            'base_url = "https://codex2.test/v1"\nwire_api = "chat"\n', encoding="utf-8")
        (codex / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": FAKE_KEY2}),
                                         encoding="utf-8")

        # ZCode：provider 表，anthropic + openai-compatible
        zcode = home / "zcode.json"
        zcode.write_text(json.dumps({
            "model": "vendor/gpt-z",
            "provider": {
                "a1": {"name": "Z-anthropic", "kind": "anthropic", "source": "custom",
                       "options": {"apiKey": FAKE_KEY, "baseURL": "https://z1.test"},
                       "models": {"claude-z": {}, "claude-z-flash": {}}},
                "b2": {"name": "Z-openai", "kind": "openai-compatible", "source": "custom",
                       "options": {"apiKey": FAKE_KEY2, "baseURL": "https://z2.test/v1"},
                       "models": {"gpt-z": {}}},
                "bad": {"name": "Z-bad", "kind": "anthropic", "options": {}},
            }}), encoding="utf-8")

        # Qwen Code：modelProviders + env 间接引用密钥
        qwen = home / "qwen.json"
        qwen.write_text(json.dumps({
            "env": {"QWEN_KEY": FAKE_KEY},
            "model": {"name": "qwen-x"},
            "modelProviders": {"openai": [
                {"id": "qwen-x", "name": "Qwen网关", "baseUrl": "https://qwen.test/v1",
                 "envKey": "QWEN_KEY"}]}}), encoding="utf-8")

        # Gemini CLI：.env
        gem = home / "gemini"
        gem.mkdir(exist_ok=True)
        (gem / ".env").write_text(
            "GEMINI_API_KEY=%s\nGOOGLE_GEMINI_BASE_URL=https://gem.test\n" % FAKE_KEY,
            encoding="utf-8")

        # OpenCode：provider 表 + auth.json 提供密钥
        oc = home / "opencode.json"
        oc.write_text(json.dumps({"provider": {
            "oc1": {"name": "OC", "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": "https://oc.test/v1"},
                    "models": {"oc-model": {}}}}}), encoding="utf-8")
        ocauth = home / "opencode-auth.json"
        ocauth.write_text(json.dumps({"oc1": {"type": "api", "key": FAKE_KEY2}}),
                          encoding="utf-8")

        # Continue：yaml（含 fallback 解析器要处理的形状）
        cont = home / "continue"
        cont.mkdir(exist_ok=True)
        (cont / "config.yaml").write_text(
            "name: Local\nversion: 1.0.0\nmodels:\n"
            "  - name: cont-a\n    provider: anthropic\n"
            "    model: claude-c\n    apiBase: https://cont.test\n"
            "    apiKey: %s\n" % FAKE_KEY, encoding="utf-8")

        # Cursor / Trae：state.vscdb
        cur = home / "cursor.vscdb"
        c = sqlite3.connect(str(cur))
        c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
        c.execute("INSERT INTO ItemTable VALUES (?,?)",
                  ("cursorAuth/openAIKey", FAKE_KEY))
        c.execute("INSERT INTO ItemTable VALUES (?,?)",
                  ("src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl"
                   ".persistentStorage.applicationUser",
                   json.dumps({"openAIBaseUrl": "https://cursor.test/v1"})))
        c.commit()
        c.close()

        trae = home / "trae.vscdb"
        t = sqlite3.connect(str(trae))
        t.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
        t.execute("INSERT INTO ItemTable VALUES (?,?)",
                  ("1180215351973627_AI.agent.model.model_list_map",
                   json.dumps({"solo_coder": [
                       {"name": "trae-x", "display_name": "trae-x",
                        "base_url": "https://trae.test/v1/chat/completions", "ak": FAKE_KEY2},
                       {"name": "preset", "display_name": "preset",
                        "base_url": None, "ak": None}]})))
        t.execute("INSERT INTO ItemTable VALUES (?,?)",
                  ("1180215351973627_AI.agent.model.model_list_map",
                   json.dumps({"solo_coder": [
                       {"name": "trae-x", "display_name": "trae-x",
                        "base_url": "https://trae.test/v1/chat/completions", "ak": FAKE_KEY2}]})))
        t.commit()
        t.close()

        return self._patch(
            m,
            CLAUDE_SETTINGS=str(claude), CODEX_DIR=str(codex), ZCODE_CONFIG=str(zcode),
            QWEN_SETTINGS=str(qwen), GEMINI_DIR=str(gem),
            OPENCODE_CONFIG=str(oc), OPENCODE_AUTH=str(ocauth), CONTINUE_DIR=str(cont),
            CURSOR_DB=str(cur), TRAE_DBS=[str(trae)],
            # dsh 来源由 test_deepseek_harness.py 专项覆盖；这里指到临时目录，
            # 防止装了 dsh 的真机把 ~/.dsh 的真实供应商混进导入计数
            DSH_DIR=str(home), DSH_SETTINGS=str(home / "no-dsh-settings.yaml"))

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        restore = self._fixtures(modelhub)
        try:
            # 1) 扫描：每个来源都能被发现并解析出数量
            rows = {r["id"]: r for r in modelhub.sources()}
            expect = {"claude": 1, "codex": 2, "zcode": 2, "qwen": 1, "gemini": 1,
                      "opencode": 1, "continue": 1, "cursor": 1, "trae": 1}
            for sid, n in expect.items():
                self.assertTrue(rows[sid]["found"], sid)
                self.assertEqual(rows[sid]["count"], n, sid)
            self.assertIn("Z-bad", rows["zcode"]["note"])       # 缺地址的跳过并说明

            # 2) 导入：全部来源一次导入
            res = modelhub.import_sources()
            self.assertEqual(res["imported"], sum(expect.values()))
            self.assertEqual(res["added"], sum(expect.values()))
            self.assertEqual(res["updated"], 0)
            by_source = {s["id"]: s for s in res["sources"]}
            self.assertEqual(by_source["codex"]["added"], 2)

            provs = {p["name"]: p for p in modelhub.providers()}
            # 协议映射
            self.assertEqual(provs["Claude Code"]["protocol"], "anthropic")
            self.assertEqual(provs["Codex主"]["protocol"], "openai")
            self.assertEqual(provs["Codex主"]["model"], "gpt-x")        # active provider 带模型
            self.assertEqual(provs["Codex备"]["model"], "")             # 非 active 不带
            self.assertEqual(provs["Codex备"]["wire_api"], "chat")
            self.assertEqual(provs["Z-anthropic"]["protocol"], "anthropic")
            self.assertEqual(provs["Z-openai"]["protocol"], "openai")
            self.assertEqual(provs["Z-openai"]["model"], "gpt-z")       # 由顶层 model 反推
            self.assertEqual(provs["Qwen网关"]["api_key"], FAKE_KEY)    # envKey 解引用
            self.assertEqual(provs["Gemini CLI"]["protocol"], "google")
            self.assertEqual(provs["OC"]["api_key"], FAKE_KEY2)         # auth.json 补密钥
            self.assertEqual(provs["cont-a"]["protocol"], "anthropic")
            self.assertEqual(provs["Cursor · OpenAI"]["base_url"], "https://cursor.test/v1")
            self.assertEqual(provs["trae-x"]["base_url"], "https://trae.test/v1")  # 已收敛
            # 配置里带模型列表的来源会预置模型，省一次拉取
            self.assertEqual([m["name"] for m in provs["Z-anthropic"]["models"]],
                             ["claude-z", "claude-z-flash"])

            # 3) google 协议仅登记：可展示但不能注入 CLI
            gid = provs["Gemini CLI"]["id"]
            modelhub.set_binding("claude-code", provider_id=gid)
            self.assertIsNone(modelhub.resolve_binding("claude-code"))
            # anthropic 的正常注入
            modelhub.set_binding("claude-code", provider_id=provs["Claude Code"]["id"])
            self.assertEqual(modelhub.resolve_binding("claude-code")["model"], "claude-x")

            # 4) 幂等：重导入不新增，id 与用户改动都保留
            before = {p["name"]: p["id"] for p in modelhub.providers()}
            modelhub.upsert_provider({"id": provs["Claude Code"]["id"], "name": "Claude Code",
                                      "protocol": "anthropic", "base_url": "https://claude.test",
                                      "model_easy": "claude-mini", "api_key": ""})
            res2 = modelhub.import_sources()
            self.assertEqual(res2["added"], 0)
            self.assertEqual(res2["updated"], sum(expect.values()))
            self.assertEqual(len(modelhub.providers()), sum(expect.values()))
            after = {p["name"]: p for p in modelhub.providers()}
            self.assertEqual(after["Claude Code"]["id"], before["Claude Code"])
            self.assertEqual(after["Claude Code"]["model_easy"], "claude-mini")

            # 5) 按来源筛选导入
            modelhub.delete_provider(after["Codex主"]["id"])
            res3 = modelhub.import_sources(["codex"])
            self.assertEqual(res3["imported"], 2)                       # 只补 codex 的两个
            self.assertEqual(len(modelhub.providers()), sum(expect.values()))
        finally:
            restore()


class TestImportCrossSourceDedupe(BaseTest):
    """跨来源去重：同地址同密钥只留一条；不同密钥视为两个账号；缺密钥可被别的工具补齐。"""

    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        home = self.tmp / "d"
        home.mkdir(exist_ok=True)

        claude = home / "claude.json"
        claude.write_text(json.dumps({"env": {
            "ANTHROPIC_BASE_URL": "https://dup.test",
            "ANTHROPIC_AUTH_TOKEN": FAKE_KEY}}), encoding="utf-8")

        zcode = home / "zcode.json"
        zcode.write_text(json.dumps({"provider": {
            "x1": {"name": "dup-same-key", "kind": "anthropic",
                   "options": {"baseURL": "https://dup.test", "apiKey": FAKE_KEY}},
            "x2": {"name": "dup-other-key", "kind": "anthropic",
                   "options": {"baseURL": "https://dup.test", "apiKey": FAKE_KEY2}},
            "x3": {"name": "no-key-yet", "kind": "anthropic",
                   "options": {"baseURL": "https://fill.test"}}}}), encoding="utf-8")

        codex = home / "codex"
        codex.mkdir(exist_ok=True)
        (codex / "config.toml").write_text(
            'model = "m"\n[model_providers.custom]\nbase_url = "https://fill.test"\n'
            'wire_api = "responses"\n', encoding="utf-8")
        (codex / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": FAKE_KEY2}),
                                         encoding="utf-8")

        orig = (modelhub.CLAUDE_SETTINGS, modelhub.ZCODE_CONFIG, modelhub.CODEX_DIR)
        modelhub.CLAUDE_SETTINGS = str(claude)
        modelhub.ZCODE_CONFIG = str(zcode)
        modelhub.CODEX_DIR = str(codex)
        try:
            r1 = modelhub.import_sources(["claude", "zcode"])
            # dup.test（claude 的密钥）+ dup.test 的另一个账号 + fill.test（暂无密钥）
            self.assertEqual(r1["added"], 3)
            self.assertEqual(r1["duplicate"], 1)   # 同地址同密钥 → 跳过
            self.assertEqual(len(modelhub.providers()), 3)
            by = {p["name"]: p for p in modelhub.providers()}
            self.assertEqual(by["no-key-yet"]["api_key"], "")
            nk_id = by["no-key-yet"]["id"]

            # codex 指向同一地址且带密钥 → 补齐密钥，而不是新增一条
            r2 = modelhub.import_sources(["codex"])
            self.assertEqual(r2["added"], 0)
            self.assertEqual(len(modelhub.providers()), 3)
            nk = next(p for p in modelhub.providers() if p["id"] == nk_id)
            self.assertEqual(nk["api_key"], FAKE_KEY2)
            self.assertEqual(nk["source"], "zcode")     # 保留原来源，只补密钥
            # 同地址不同密钥的两条各自保留
            self.assertEqual(len([p for p in modelhub.providers()
                                  if p["base_url"] == "https://dup.test"]), 2)
        finally:
            (modelhub.CLAUDE_SETTINGS, modelhub.ZCODE_CONFIG, modelhub.CODEX_DIR) = orig


class TestNoListGateway(BaseTest):
    """无 /models 列表接口的网关（zcode-plan 等）：404 是良性结果，不是失败。

    获取模型列表 → 返回带 _NO_LIST_SUFFIX 的提示（端点据此回 ok+note）；
    测试连接 → 连通 + 提示，且不得记 KEY 错误（404≠密钥错）。
    """

    def runTest(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        class _Gw(BaseHTTPRequestHandler):
            """全部请求回 404（log_404=True）或 500（模拟真错误混入）。"""
            log_404 = True

            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b'{"error":"x"}'
                self.send_response(404 if _Gw.log_404 else 500)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer(("127.0.0.1", 0), _Gw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            # 全部候选端点皆 404 → 良性提示，is_no_list_note 判真
            names, err = modelhub._fetch_models_http(base, FAKE_KEY, "openai", True)
            self.assertIsNone(names)
            self.assertTrue(modelhub.is_no_list_note(err), err)
            # 混入真错误（500）→ 报真错误，不冒充良性
            _Gw.log_404 = False
            names, err = modelhub._fetch_models_http(base, FAKE_KEY, "openai", True)
            self.assertIsNone(names)
            self.assertFalse(modelhub.is_no_list_note(err), err)
        finally:
            srv.shutdown()

        # 刷新与测试连接路径：良性 404 → 提示不失败，且不记 KEY 错误
        modelhub.upsert_provider({"name": "P", "protocol": "openai",
                                  "base_url": "https://p.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        modelhub.key_op(pid, "add", key=FAKE_KEY2)   # 落成显式 keys 数组
        benign = "https://p.test/models " + modelhub._NO_LIST_SUFFIX
        orig = modelhub._fetch_models_http
        try:
            modelhub._fetch_models_http = lambda *a, **k: (None, benign)
            n, err = modelhub.refresh_models(pid)
            self.assertEqual((n, err), (0, benign))
            self.assertTrue(modelhub.is_no_list_note(err))
            res = modelhub.test_provider(pid)
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "reachable_unverified")
            self.assertEqual(res["note"], benign)
            self.assertFalse(modelhub.providers()[0]["keys"][0].get("last_error"))
            # 真错误照旧：测试连接判失败并记账 KEY
            modelhub._fetch_models_http = lambda *a, **k: (None, "HTTP 500 内部错误")
            res = modelhub.test_provider(pid)
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "failed")
            self.assertIn("500", modelhub.providers()[0]["keys"][0]["last_error"])
        finally:
            modelhub._fetch_models_http = orig


if __name__ == "__main__":
    import unittest as _u
    _u.main()
