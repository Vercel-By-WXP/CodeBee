# -*- coding: utf-8 -*-
"""pi 绑定注入回归（2026-09-24 实案）：Pi 19 连败、零次真实成功。

pi 解析启动模型要 (defaultProvider, defaultModel) 命中 ~/.pi/agent/models.json
的供应商注册表，否则回落到内置供应商的默认模型——CodeBee 此前只写
defaultModel、defaultProvider 留空串，pi 便拿内置 anthropic 的 claude-opus-4-8
去打注入的 Bigmodel 端点，每次都是 `403 {"type":"forbidden"}`。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


def _fake_expanduser(home):
    def fake(p):
        if p == "~":
            return str(home)
        if p.startswith("~/"):
            return str(home / p[2:])
        return p
    return fake


class TestPiInjector(BaseTest):
    """假 HOME 下的 pi 两件配置写入。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        fake = _fake_expanduser(self.home)
        for mod in ("manager", "router"):
            patcher = mock.patch("app.core.%s.os.path.expanduser" % mod, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.settings = self.home / ".pi" / "agent" / "settings.json"
        self.models = self.home / ".pi" / "agent" / "models.json"

    def _entry(self):
        return {"id": "pi", "config": {"path": "~/.pi/agent/settings.json",
                                       "format": "json-path",
                                       "model_key": "defaultModel"}}

    def _read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_anthropic_binding_writes_both_files_without_key(self):
        from app.core import manager
        prov = {"id": "p36", "name": "Bigmodel", "protocol": "anthropic",
                "base_url": "https://open.bigmodel.cn/api/anthropic"}
        env = {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
               "ANTHROPIC_AUTH_TOKEN": "sk-secret-value"}
        err = manager._sync_pi_settings(self._entry(), "glm-5.3-flash", prov, env)
        self.assertIsNone(err, err)
        blk = self._read(self.models)["providers"]["codebee"]
        self.assertEqual(blk["baseUrl"], "https://open.bigmodel.cn/api/anthropic")
        self.assertEqual(blk["api"], "anthropic-messages")
        self.assertEqual(blk["apiKey"], "$ANTHROPIC_AUTH_TOKEN")
        self.assertTrue(blk["authHeader"], "Z.ai/Bigmodel 的 anthropic 面吃 Bearer")
        self.assertEqual([m["id"] for m in blk["models"]], ["glm-5.3-flash"])
        cfg = self._read(self.settings)
        self.assertEqual(cfg["defaultProvider"], "codebee")
        self.assertEqual(cfg["defaultModel"], "glm-5.3-flash")
        # 密钥绝不落盘：两件配置文件里都搜不到明文
        blob = self.models.read_text(encoding="utf-8") \
            + self.settings.read_text(encoding="utf-8")
        self.assertNotIn("sk-secret-value", blob)

    def test_openai_protocol_maps_to_completions(self):
        from app.core import manager
        prov = {"id": "p41", "name": "维云", "protocol": "openai",
                "base_url": "https://vsllm.cc/v1"}
        err = manager._sync_pi_settings(
            self._entry(), "deepseek-v4-pro", prov, {"ORCH_API_KEY": "k"})
        self.assertIsNone(err, err)
        blk = self._read(self.models)["providers"]["codebee"]
        self.assertEqual(blk["api"], "openai-completions")
        self.assertEqual(blk["apiKey"], "$ORCH_API_KEY")
        self.assertNotIn("authHeader", blk)

    def test_user_provider_fields_and_settings_survive(self):
        from app.core import manager
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({
            "defaultProvider": "anthropic", "defaultModel": "claude-opus-4-8",
            "yolo": True, "theme": "dark"}), encoding="utf-8")
        self.models.parent.mkdir(parents=True, exist_ok=True)
        self.models.write_text(json.dumps({"providers": {
            "ollama": {"baseUrl": "http://localhost:11434/v1",
                       "models": [{"id": "qwen2.5-coder:7b"}]}}}), encoding="utf-8")
        err = manager._sync_pi_settings(
            self._entry(), "glm-5.3-flash",
            {"protocol": "anthropic", "base_url": "https://open.bigmodel.cn/api/anthropic"},
            {"ANTHROPIC_AUTH_TOKEN": "k"})
        self.assertIsNone(err, err)
        provs = self._read(self.models)["providers"]
        self.assertIn("ollama", provs, "用户自有供应商必须原样保留")
        cfg = self._read(self.settings)
        self.assertEqual(cfg["theme"], "dark")
        self.assertEqual(cfg["yolo"], True)
        self.assertEqual(cfg["defaultProvider"], "codebee", "托管段以绑定为准")

    def test_ambiguous_protocol_writes_nothing(self):
        from app.core import manager
        err = manager._sync_pi_settings(
            self._entry(), "glm", {"protocol": "auto", "base_url": "https://x"},
            {"ORCH_API_KEY": "k"})
        self.assertIn("只认 anthropic / openai", err)
        self.assertFalse(self.models.exists())
        self.assertFalse(self.settings.exists())

    def test_runtime_hash_covers_models_json(self):
        """models.json 被外部毒写必须让指纹失效，否则换将步骤带毒配置静默失败。"""
        from app.core import manager
        entry = self._entry()
        manager._sync_pi_settings(
            entry, "glm-5.3-flash",
            {"protocol": "anthropic", "base_url": "https://open.bigmodel.cn/api/anthropic"},
            {"ANTHROPIC_AUTH_TOKEN": "k"})
        before = manager._runtime_cfg_hash(entry)
        self.assertIsNotNone(before)
        data = self._read(self.models)
        data["providers"]["codebee"]["baseUrl"] = "http://poison.example"
        self.models.write_text(json.dumps(data), encoding="utf-8")
        self.assertNotEqual(before, manager._runtime_cfg_hash(entry))

    def test_router_reads_selected_provider_from_models_json(self):
        """上游去重看的是选中块：settings.json 挑 codebee，端点在 models.json。"""
        from app.core import manager, router
        manager._sync_pi_settings(
            self._entry(), "glm-5.3-flash",
            {"protocol": "anthropic", "base_url": "https://open.bigmodel.cn/api/anthropic"},
            {"ANTHROPIC_AUTH_TOKEN": "k"})
        # 未被选中的块不得参与判断（否则换将把同网关误判成异上游）
        data = self._read(self.models)
        data["providers"]["other"] = {"baseUrl": "https://unselected.example/v1"}
        self.models.write_text(json.dumps(data), encoding="utf-8")
        ups = router.agent_upstreams("pi")
        self.assertIn("open.bigmodel.cn", ups)
        self.assertNotIn("unselected.example", ups)


class TestDeadBindingSubstituteGuard(BaseTest):
    """死链补位不得把「换将」补回本轮刚撞死的 CLI / 已封 403 上游。"""

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.pipeline = pipeline
        self._saved_agents = pipeline._CURRENT_AGENTS
        self.addCleanup(setattr, pipeline, "_CURRENT_AGENTS", self._saved_agents)

    def _patch_live(self, live_id):
        """只有 live_id 这台配了活链。"""
        from app.core import modelhub
        patcher = mock.patch.object(modelhub, "bind_agent",
                                    lambda a, difficulty="default": dict(
                                        a,
                                        binding_configured=(a.get("id") == live_id),
                                        call_chain=([{}] if a.get("id") == live_id else [])))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_upstreams(self, mapping):
        from app.core import router
        patcher = mock.patch.object(
            router, "agent_upstreams",
            lambda agent_id, **kw: set(mapping.get(agent_id) or ()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_substitute_skips_excluded_cli(self):
        self._patch_live("pi")
        self._patch_upstreams({})
        self.pipeline._CURRENT_AGENTS = [{"id": "pi", "mode": "real"},
                                         {"id": "claude-code", "mode": "real"}]
        sub = self.pipeline._dead_binding_substitute(
            "claude-code", exclude={"pi", "claude-code"})
        self.assertIsNone(sub, "pi 刚因 403 失败过，不能再被补位回来")
        ok = self.pipeline._dead_binding_substitute("claude-code", exclude={"other"})
        self.assertEqual((ok or {}).get("id"), "pi", "未排除时仍要能补位")

    def test_substitute_skips_forbidden_upstream(self):
        self._patch_live("pi")
        self._patch_upstreams({"pi": ["open.bigmodel.cn"]})
        self.pipeline._CURRENT_AGENTS = [{"id": "pi", "mode": "real"}]
        self.assertIsNone(self.pipeline._dead_binding_substitute(
            "claude-code", blocked_upstreams=[{"open.bigmodel.cn"}]))
        # 上游未知（取不到 host）：宁白试不误杀
        self._patch_upstreams({"pi": []})
        self.assertIsNotNone(self.pipeline._dead_binding_substitute(
            "claude-code", blocked_upstreams=[{"open.bigmodel.cn"}]))

    def test_resume_never_substitutes(self):
        """会话钉在原 CLI 上：带 resume 时守卫再宽也不许换人。"""
        self._patch_live("pi")
        self._patch_upstreams({})
        self.pipeline._CURRENT_AGENTS = [{"id": "pi", "mode": "real"}]
        self.assertIsNone(self.pipeline._dead_binding_substitute(
            "claude-code", resume={"session": "s1"}))
