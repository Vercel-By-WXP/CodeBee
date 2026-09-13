# -*- coding: utf-8 -*-
"""CLI 绑定模型链测试：显式链优先、纯链模式（不绑供应商）、旧编排模型迁移。

运行时模型选择已统一收敛到 modelhub bindings（models 有序链）；
registry 只保留「参与编排」开关。
"""
from __future__ import annotations

import json
import unittest

from base import BaseTest

FAKE_KEY = "sk-test-" + "abcdefgh" * 3      # 假密钥：运行时拼装


class TestBindingModelChain(BaseTest):
    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        modelhub.upsert_provider({
            "name": "测试网关", "protocol": "anthropic",
            "base_url": "https://api.test.com/v1", "api_key": FAKE_KEY,
            "model": "prov-default", "model_easy": "prov-easy",
            "model_hard": "prov-hard"})
        pid = modelhub.providers()[0]["id"]

        # 1) set_binding 写模型链：models 有序、model 同步为链首
        modelhub.set_binding("claude-code", provider_id=pid,
                             models=["m2", "m1", "m3", "m2", ""])
        b = modelhub.bindings()["claude-code"]
        self.assertEqual(b["models"], ["m2", "m1", "m3"])   # 去空去重限 3
        self.assertEqual(b["model"], "m2")

        # 2) 显式链优先：难度映射与供应商默认都被链短路
        modelhub.set_binding("claude-code", provider_id=pid, difficulty_routing=True,
                             models=["m2", "m1"])
        r = modelhub.resolve_binding("claude-code", "hard")
        self.assertEqual(r["model"], "m2")                  # 链首优先于 model_hard
        self.assertEqual(r["model_fallbacks"], ["m1"])
        self.assertEqual(r["env"]["ANTHROPIC_MODEL"], "m2")

        # 3) 链空时回落原有解析：难度映射 → 供应商默认
        modelhub.set_binding("claude-code", provider_id=pid, difficulty_routing=True,
                             models=[])
        self.assertEqual(modelhub.resolve_binding("claude-code", "easy")["model"], "prov-easy")
        self.assertEqual(modelhub.resolve_binding("claude-code", "default")["model"], "prov-default")

        # 4) 纯链模式：不绑供应商，只传模型，不注入任何 env
        modelhub.set_binding("opencode", provider_id="", models=["p1", "p2"])
        r = modelhub.resolve_binding("opencode")
        self.assertEqual(r["model"], "p1")
        self.assertEqual(r["model_fallbacks"], ["p2"])
        self.assertEqual(r["env"], {})
        self.assertIsNone(r["provider"])
        self.assertNotIn("codex_provider", r)
        # bind_agent 应用纯链：只加模型，不碰 env
        agent = {"id": "opencode", "kind": "opencode", "mode": "real",
                 "command": "opencode", "env": {"X": "1"}}
        ba = modelhub.bind_agent(agent, "default")
        self.assertEqual(ba["model"], "p1")
        self.assertEqual(ba["model_fallbacks"], ["p2"])
        self.assertEqual(ba["env"], {"X": "1"})

        # 5) 什么都不配 → None（沿用 CLI 默认）
        self.assertIsNone(modelhub.resolve_binding("qwencode"))

        # 6) 删除供应商 → 其绑定整体不生效（即使链还在）
        modelhub.set_binding("claude-code", provider_id=pid, models=["m1"])
        modelhub.delete_provider(pid)
        self.assertIsNone(modelhub.resolve_binding("claude-code"))

    # ---------------------------------------------------- 迁移
    def test_migrate_orch_models(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        # 旧数据：orchestration.json 同时有 enabled 与模型链
        self._paths.ENABLED_FILE.write_text(json.dumps({
            "codex-cli": {"enabled": True, "models": ["g1", "g2"]},
            "claude-code": {"enabled": False, "model": "c1"},
            "opencode": {"enabled": True},
        }, ensure_ascii=False), encoding="utf-8")

        n = modelhub.migrate_orch_models()
        self.assertEqual(n, 2)
        bindings = modelhub.bindings()
        self.assertEqual(bindings["codex-cli"]["models"], ["g1", "g2"])
        self.assertEqual(bindings["codex-cli"]["model"], "g1")
        self.assertEqual(bindings["claude-code"]["models"], ["c1"])
        # orchestration.json 只留 enabled，模型字段被清掉
        state = json.loads(self._paths.ENABLED_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["codex-cli"], {"enabled": True})
        self.assertEqual(state["claude-code"], {"enabled": False})
        self.assertEqual(state["opencode"], {"enabled": True})
        # 留了备份
        self.assertTrue((self.data_dir / "orchestration.json.bak").is_file())

        # 幂等：再跑不改动、不覆盖用户后续在绑定页的选择
        modelhub.set_binding("codex-cli", models=["用户改的"])
        self.assertEqual(modelhub.migrate_orch_models(), 0)  # 源头已无模型字段
        self.assertEqual(modelhub.bindings()["codex-cli"]["models"], ["用户改的"])

        # 源头仍有链但 bindings 已被用户改过 → 跳过不覆盖
        self._paths.ENABLED_FILE.write_text(json.dumps({
            "codex-cli": {"enabled": True, "models": ["g1", "g2"]},
        }, ensure_ascii=False), encoding="utf-8")
        modelhub.migrate_orch_models()
        self.assertEqual(modelhub.bindings()["codex-cli"]["models"], ["用户改的"])

    def test_drop_model_clears_chain(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({
            "name": "测试网关", "protocol": "openai",
            "base_url": "https://oai.test.com/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        # 直接落盘两个模型，再绑链引用它
        data = modelhub._load()
        prov = data["providers"][0]
        prov["models"] = [{"name": "m1", "priority": 1},
                          {"name": "m2", "priority": 2}]
        modelhub._save(data)
        modelhub.set_binding("codex-cli", provider_id=pid, models=["m1", "m2"])
        # 删 m1 → 链里同步清除
        modelhub.model_op(pid, "m1", "delete")
        self.assertEqual(modelhub.bindings()["codex-cli"]["models"], ["m2"])


if __name__ == "__main__":
    unittest.main()
