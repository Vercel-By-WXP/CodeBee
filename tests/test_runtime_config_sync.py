# -*- coding: utf-8 -*-
"""运行前配置同步防线（manager.sync_runtime_config）回归。

2026-09-18 opencode 钉死讯飞 404 旧端点案：绑定切到云知声后 CLI 自家配置
不会自愈，同步只发生在一键打开 → 起跑撞旧配置 2 秒 UnknownError，3 次续跑
全灭。防线挂 bind_agent 入口（所有真实派发含死链补位都经此），把当前链首
落进 CLI 自家配置。

锁定的不变量：
1. 无绑定（没配链/链全死）绝不写用户配置——测试空转、不碰真实家目录；
2. 有绑定经 bind_agent 派发时配置落盘（模型写入 model_key）；
3. 指纹缓存：绑定没变不重复写盘（不与用户手工编辑打架）；
4. 指纹任一分量（含 api_key）变化即重写；
5. kind → 条目 id 映射：编排智能体 id 是业务名时按 kind 找到条目。
"""
from __future__ import annotations

import json
import unittest.mock as mock

from base import BaseTest


class TestRuntimeConfigSync(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import manager
        self.manager = manager
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cfg = self.home / ".fake" / "config.json"
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(json.dumps({"model": "old-model"}), encoding="utf-8")
        self.entry = {"id": "fake-cli", "name": "Fake CLI",
                      "config": {"path": "~/.fake/config.json",
                                 "format": "json", "model_key": "model"}}

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        manager._RUNTIME_SYNC["fps"].clear()
        self.addCleanup(manager._RUNTIME_SYNC["fps"].clear)

    def _seed_binding(self, model="m1", key="k1"):
        """种一个活的 openai 供应商 + fake-cli 绑定链（models.json 直写）。"""
        from app.core import modelhub as MH
        MH._FILE.write_text(json.dumps({
            "providers": [{"id": "p1", "name": "P1", "protocol": "openai",
                           "base_url": "https://p1.example/v1", "enabled": True,
                           "api_key": key,
                           "models": [{"name": model, "enabled": True}]}],
            "bindings": {"fake-cli": {"provider_id": "p1", "model": model,
                                      "chain": [{"provider_id": "p1", "model": model}],
                                      "models": [model]}},
        }), encoding="utf-8")

    def _patch_catalog(self):
        return mock.patch("app.core.catalog.by_id",
                          side_effect=lambda x: self.entry if x == "fake-cli" else None)

    def _read(self):
        return json.loads(self.cfg.read_text(encoding="utf-8"))

    def test_no_binding_never_writes(self):
        """没配链：派发不得动 CLI 自家配置（测试空转、不碰真实家目录）。"""
        from app.core import modelhub
        with self._patch_catalog():
            modelhub.bind_agent({"id": "fake-cli", "kind": "generic",
                                 "mode": "real"})
        self.assertEqual(self._read(), {"model": "old-model"})
        self.assertFalse((self.cfg.parent / "config.json.bak").exists())

    def test_dead_binding_never_writes(self):
        """链配过但供应商已停用（解析为空）：同样不写。"""
        from app.core import modelhub as MH
        self._seed_binding()
        MH.providers_op(["p1"], "disable")
        with self._patch_catalog():
            MH.bind_agent({"id": "fake-cli", "kind": "generic", "mode": "real"})
        self.assertEqual(self._read(), {"model": "old-model"})

    def test_dispatch_syncs_config(self):
        """有活绑定：bind_agent 派发即把链首模型落进 CLI 自家配置。"""
        from app.core import modelhub
        self._seed_binding(model="fresh-model")
        with self._patch_catalog():
            modelhub.bind_agent({"id": "fake-cli", "kind": "generic",
                                 "mode": "real"})
        self.assertEqual(self._read().get("model"), "fresh-model")
        self.assertTrue((self.cfg.parent / "config.json.bak").exists())

    def test_fingerprint_cache_skips_rewrite(self):
        """绑定没变：重复派发不重写盘（用户手工加的字段不被冲掉）。"""
        from app.core import modelhub
        self._seed_binding()
        with self._patch_catalog():
            modelhub.bind_agent({"id": "fake-cli", "kind": "generic",
                                 "mode": "real"})
        raw = json.loads(self.cfg.read_text(encoding="utf-8"))
        raw["user-marker"] = True
        self.cfg.write_text(json.dumps(raw), encoding="utf-8")
        with self._patch_catalog():
            modelhub.bind_agent({"id": "fake-cli", "kind": "generic",
                                 "mode": "real"})
        self.assertTrue(self._read().get("user-marker"),
                        "同绑定二次派发不得重写配置")

    def test_key_change_rewrites(self):
        """指纹任一分量变化（如换 api_key）：即使模型没变也重写。

        write_model 是合并语义（保留未知字段），所以「重写发生」用 .bak
        快照验证：bak 是写前内容，第二次写会把带 marker 的文件快照进去。"""
        from app.core import modelhub
        self._seed_binding(key="k1")
        with self._patch_catalog():
            modelhub.bind_agent({"id": "fake-cli", "kind": "generic",
                                 "mode": "real"})
        bak1 = json.loads((self.cfg.parent / "config.json.bak")
                          .read_text(encoding="utf-8"))
        self.assertEqual(bak1.get("model"), "old-model",
                         "首次写盘的 bak 应是同步前内容")
        raw = json.loads(self.cfg.read_text(encoding="utf-8"))
        raw["user-marker"] = True
        self.cfg.write_text(json.dumps(raw), encoding="utf-8")
        self._seed_binding(key="k2")
        with self._patch_catalog():
            modelhub.bind_agent({"id": "fake-cli", "kind": "generic",
                                 "mode": "real"})
        bak2 = json.loads((self.cfg.parent / "config.json.bak")
                          .read_text(encoding="utf-8"))
        self.assertTrue(bak2.get("user-marker"),
                        "KEY 变更必须触发第二次写盘（bak 快照到带 marker 的写前内容）")

    def test_kind_maps_to_entry(self):
        """编排智能体 id 是业务名（draft-c1）：按 kind 找到条目完成同步。"""
        from app.core import modelhub, manager
        self._seed_binding(model="via-kind")
        self.manager._KIND_ENTRY["fake-kind"] = "fake-cli"
        self.addCleanup(manager._KIND_ENTRY.pop, "fake-kind", None)
        with self._patch_catalog():
            modelhub.bind_agent({"id": "draft-c1", "kind": "fake-kind",
                                 "mode": "real"})
        self.assertEqual(self._read().get("model"), "via-kind")
