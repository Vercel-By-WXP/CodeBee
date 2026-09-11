# -*- coding: utf-8 -*-
"""智能体目录：参与编排开关（模型链已并入 CLI 绑定）+ 后台版本检查状态。"""
from __future__ import annotations

import time

from base import BaseTest


class TestOrchPreferenceEnabledOnly(BaseTest):
    def runTest(self):
        from app.core import registry

        # 编排偏好只管 enabled；模型链归 modelhub bindings（CLI 绑定页）
        pref = registry.set_preference("codex-cli", enabled=True)
        self.assertEqual(pref, {"enabled": True})
        self.assertEqual(registry.load_enabled()["codex-cli"], {"enabled": True})

        # 旧调用方传 model/models 不报错，且历史遗留字段被顺手清掉
        self._paths.ENABLED_FILE.write_text(
            '{"codex-cli": {"enabled": true, "model": "legacy", "models": ["a", "b"]}}',
            encoding="utf-8")
        pref = registry.set_preference("codex-cli", enabled=False,
                                       model="x", models=["x", "y"])
        self.assertEqual(pref, {"enabled": False})
        self.assertEqual(registry.load_enabled()["codex-cli"], {"enabled": False})


class TestEffectiveAgentsNoModel(BaseTest):
    def _entry(self):
        return {"id": "codex-cli", "name": "Codex CLI", "default_enabled": True,
                "orch": {"kind": "codex", "command": "codex"}}

    def runTest(self):
        from app.core import registry

        entry = self._entry()
        detected = {"codex-cli": {"installed": True}}

        # 运行时模型由 modelhub.bind_agent() 注入，registry 不再输出 model
        registry.set_preference("codex-cli", enabled=True)
        agents = registry.effective_agents([entry], detected)
        a = next(x for x in agents if x["id"] == "codex-cli")
        self.assertNotIn("model", a)
        self.assertNotIn("model_fallbacks", a)

        # 旧数据（只有 model 字段）也不解析出模型（迁移由 migrate_orch_models 负责）
        cfg = self._paths.ENABLED_FILE
        cfg.write_text('{"codex-cli": {"enabled": true, "model": "legacy"}}',
                       encoding="utf-8")
        agents = registry.effective_agents([entry], detected)
        a = next(x for x in agents if x["id"] == "codex-cli")
        self.assertNotIn("model", a)

        # 未启用 → 不进编排列表
        registry.set_preference("codex-cli", enabled=False)
        agents = registry.effective_agents([entry], detected)
        self.assertFalse(any(x["id"] == "codex-cli" for x in agents))


class TestUpdateCheckState(BaseTest):
    def runTest(self):
        from app.core import manager

        manager._UPDATE_CACHE.clear()
        manager._UPDATE_CHECK.update(running=False, total=0, done=0)

        # 没查过 → unknown
        self.assertEqual(manager.update_info({"id": "codex-cli"})["status"], "unknown")
        self.assertFalse(manager.updates_checking())

        # 有缓存 → 按 updatable 映射状态
        manager._UPDATE_CACHE["codex-cli"] = (
            time.time(), {"updatable": True, "latest": "9.9.9", "note": ""})
        info = manager.update_info({"id": "codex-cli"})
        self.assertEqual(info["status"], "updatable")
        self.assertEqual(info["latest"], "9.9.9")

        manager._UPDATE_CACHE["codex-cli"] = (
            time.time(), {"updatable": False, "latest": "1.0.0", "note": "已是最新版本"})
        self.assertEqual(manager.update_info({"id": "codex-cli"})["status"], "current")

        # 渠道不支持自动检查（updatable 为 None）
        manager._UPDATE_CACHE["aider"] = (
            time.time(), {"updatable": None, "latest": None, "note": "该渠道暂不支持"})
        self.assertEqual(manager.update_info({"id": "aider"})["status"], "unsupported")

        # 没有可检查条目时不启动后台线程（避免测试触发真实 npm/winget 调用）
        orig = manager._checkable_entries
        manager._checkable_entries = lambda: []
        try:
            self.assertEqual(manager.check_updates_async(), 0)
            self.assertFalse(manager.updates_checking())
        finally:
            manager._checkable_entries = orig


if __name__ == "__main__":
    import unittest as _u
    _u.main()
