# -*- coding: utf-8 -*-
"""Grok Build（grok-build）接入：catalog 条目 / toml-section 模型配置读写。

实测事实（2026-09，本机 grok 1.0.30，来源官方二进制内嵌文档）：
- 配置文件是 ~/.grok/config.toml（不是 config.json）；缺失时用内置默认，
  grok 自己不主动创建该文件——用户层覆盖正落在这里，缺了必须按需创建。
- 默认模型键是 [models] 表下的 default（`models.default`，文档明示等价于
  GROK_DEFAULT_MODEL / --model / -m）。
- 早期 catalog 把它错标成 config.json + format=json，文件永远不存在，
  「保存默认模型」必然报「配置文件尚未生成」——CONFIG_PATCH 负责纠偏。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


class TestCatalogEntry(BaseTest):

    def test_entry_shape(self):
        from app.core import catalog, manager
        e = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "grok-build")
        self.assertEqual(e["detect"], {"cli": "grok"})
        self.assertEqual(e["orch"]["argv_template"], ["-p", "{prompt}"])
        # 真实配置是 TOML：~/.grok/config.toml 的 [models] default
        self.assertEqual(e["config"]["path"], "~/.grok/config.toml")
        self.assertEqual(e["config"]["format"], "toml-section")
        self.assertEqual(e["config"]["model_key"], "models.default")
        # UI 可写入口由 format 决定
        self.assertIn(e["config"]["format"], manager._WRITABLE_FORMATS)

    def test_config_patch_fixes_legacy_json_entry(self):
        """老 catalog.json 里 grok-build 还是 config.json + json → 必须被纠正。"""
        from app.core import catalog
        self._paths.CATALOG_FILE.write_text(json.dumps([{
            "id": "grok-build",
            "config": {"path": "~/.grok/config.json", "format": "json",
                       "model_key": None},
        }]), encoding="utf-8")
        catalog._CACHE["entries"] = None
        try:
            e = next(x for x in catalog.load(force=True)
                     if x["id"] == "grok-build")
            self.assertEqual(e["config"]["path"], "~/.grok/config.toml")
            self.assertEqual(e["config"]["format"], "toml-section")
            self.assertEqual(e["config"]["model_key"], "models.default")
        finally:
            catalog._CACHE["entries"] = None

    def test_config_patch_covers_all_fixed_clis(self):
        """kimi/mimo/pi/openclaw 与 grok 同批纠偏：错标条目加载后必须是真实落点。"""
        from app.core import catalog, manager
        self._paths.CATALOG_FILE.write_text(json.dumps([
            {"id": cid,
             "config": {"path": "~/.broken/config.json", "format": "json",
                        "model_key": None}}
            for cid in ("kimi-code", "mimo-code", "pi", "openclaw")
        ]), encoding="utf-8")
        catalog._CACHE["entries"] = None
        try:
            loaded = {e["id"]: e["config"]
                      for e in catalog.load(force=True)}
        finally:
            catalog._CACHE["entries"] = None
        self.assertEqual(loaded["kimi-code"]["path"], "~/.kimi-code/config.toml")
        self.assertEqual(loaded["kimi-code"]["model_key"], "default_model")
        self.assertEqual(loaded["mimo-code"]["path"],
                         "~/.config/mimocode/mimocode.jsonc")
        self.assertEqual(loaded["mimo-code"]["model_key"], "model")
        self.assertEqual(loaded["pi"]["path"], "~/.pi/agent/settings.json")
        self.assertEqual(loaded["pi"]["model_key"], "defaultModel")
        # pi 不再带 defaultProvider 伴随键：写空串正是 2026-09-24 那次 403 连败的
        # 一半根因（defaultProvider 为空 → pi 回落内置供应商默认模型）。defaultProvider
        # 与端点由 manager._sync_pi_settings 与 models.json 同源托管。
        self.assertNotIn("model_extra_keys", loaded["pi"])
        self.assertEqual(loaded["openclaw"]["path"], "~/.openclaw/openclaw.json")
        # 绝不能写顶层 model（openclaw schema 校验会拒绝启动）
        self.assertEqual(loaded["openclaw"]["model_key"],
                         "agents.defaults.model.primary")
        for cid in ("kimi-code", "mimo-code", "pi", "openclaw"):
            self.assertIn(loaded[cid]["format"], manager._WRITABLE_FORMATS,
                          msg=cid)


class TestTomlSectionModelConfig(BaseTest):
    """~/.grok/config.toml 的 [models] default 读写。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.toml = self.home / ".grok" / "config.toml"

    def _entry(self):
        return {"id": "grok-build",
                "config": {"path": "~/.grok/config.toml", "format": "toml-section",
                           "model_key": "models.default"}}

    def test_write_creates_file_and_table_when_missing(self):
        """grok 不主动建 config.toml（缺失即内置默认）；写入必须能按需创建。"""
        from app.core import manager
        self.assertFalse(self.toml.exists())
        out = manager.write_model(self._entry(), "grok-4.5")
        self.assertTrue(out["ok"], msg=out)
        self.assertEqual(out["model"], "grok-4.5")
        text = self.toml.read_text(encoding="utf-8")
        self.assertIn("[models]", text)
        self.assertIn('default = "grok-4.5"', text)
        self.assertFalse((self.home / ".grok" / "config.toml.bak").exists())

    def test_write_replaces_value_and_preserves_rest(self):
        from app.core import manager
        self.toml.parent.mkdir(parents=True, exist_ok=True)
        self.toml.write_text(
            '[cli]\n'
            'installer = "npm"\n'
            '\n'
            '[models]\n'
            'default = "grok-old"  # 主模型\n'
            'temperature = 0.7\n'
            '\n'
            '[model.custom]\n'
            'model = "untouched"\n',
            encoding="utf-8")
        out = manager.write_model(self._entry(), "grok-4.6")
        self.assertTrue(out["ok"], msg=out)
        text = self.toml.read_text(encoding="utf-8")
        self.assertIn('default = "grok-4.6"', text)
        self.assertNotIn("grok-old", text)
        # 注释、其它键与其它表不受影响
        self.assertIn("# 主模型", text)
        self.assertIn("temperature = 0.7", text)
        self.assertIn('model = "untouched"', text)
        self.assertIn("[model.custom]", text)
        # 改动前留 .bak
        self.assertTrue((self.home / ".grok" / "config.toml.bak").exists())
        self.assertEqual(manager.read_model(self._entry()), "grok-4.6")

    def test_write_inserts_key_into_existing_table(self):
        from app.core import manager
        self.toml.parent.mkdir(parents=True, exist_ok=True)
        self.toml.write_text(
            '[models]\n'
            'temperature = 0.7\n'
            '\n'
            '[ui]\n'
            'simple_mode = true\n',
            encoding="utf-8")
        out = manager.write_model(self._entry(), "m1")
        self.assertTrue(out["ok"], msg=out)
        text = self.toml.read_text(encoding="utf-8")
        self.assertEqual(manager.read_model(self._entry()), "m1")
        # 插在本表内（temperature 之后），不能跑到 [ui] 里去
        self.assertLess(text.index("temperature = 0.7"), text.index('default = "m1"'))
        self.assertLess(text.index('default = "m1"'), text.index("[ui]"))

    def test_read_missing_file_is_none(self):
        from app.core import manager
        self.assertIsNone(manager.read_model(self._entry()))

    def test_read_returns_none_when_table_or_key_missing(self):
        from app.core import manager
        self.toml.parent.mkdir(parents=True, exist_ok=True)
        self.toml.write_text('[cli]\ninstaller = "npm"\n', encoding="utf-8")
        self.assertIsNone(manager.read_model(self._entry()))

    def test_write_preserves_crlf(self):
        from app.core import manager
        self.toml.parent.mkdir(parents=True, exist_ok=True)
        self.toml.write_bytes(b'[models]\r\ndefault = "old"\r\n[ui]\r\nk = 1\r\n')
        out = manager.write_model(self._entry(), "new")
        self.assertTrue(out["ok"], msg=out)
        raw = self.toml.read_bytes()
        self.assertIn(b'default = "new"\r\n', raw)
        self.assertTrue(raw.endswith(b"\r\n"))
        self.assertEqual(manager.read_model(self._entry()), "new")


class _HomeIsolated(BaseTest):
    """把 expanduser 重定向到临时 home 的公共 setUp。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestJsonPathModelConfig(_HomeIsolated):
    """json-path 格式：pi（顶层 defaultModel）与 openclaw（嵌套 primary）。"""

    def _pi_entry(self):
        return {"id": "pi",
                "config": {"path": "~/.pi/agent/settings.json", "format": "json-path",
                           "model_key": "defaultModel"}}

    def _openclaw_entry(self):
        return {"id": "openclaw",
                "config": {"path": "~/.openclaw/openclaw.json", "format": "json-path",
                           "model_key": "agents.defaults.model.primary"}}

    def test_pi_creates_file_and_reads_back(self):
        """pi 不主动建 settings.json；write_model 只落 defaultModel。

        defaultProvider 不归这里管：pi 的供应商定义在 models.json，两件由
        manager._sync_pi_settings 同源托管（2026-09-24 空串 defaultProvider 案）。"""
        from app.core import manager
        out = manager.write_model(self._pi_entry(), "gpt-5.5")
        self.assertTrue(out["ok"], msg=out)
        f = self.home / ".pi" / "agent" / "settings.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data["defaultModel"], "gpt-5.5")
        self.assertNotIn("defaultProvider", data)
        self.assertEqual(manager.read_model(self._pi_entry()), "gpt-5.5")

    def test_pi_preserves_existing_keys(self):
        from app.core import manager
        f = self.home / ".pi" / "agent" / "settings.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "defaultProvider": "anthropic", "defaultModel": "old",
            "defaultThinkingLevel": "high", "theme": "dark"}),
            encoding="utf-8")
        out = manager.write_model(self._pi_entry(), "claude-x")
        self.assertTrue(out["ok"], msg=out)
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data["defaultModel"], "claude-x")
        # 选择位由 _sync_pi_settings 决定，write_model 一律不碰
        self.assertEqual(data["defaultProvider"], "anthropic")
        self.assertEqual(data["defaultThinkingLevel"], "high")
        self.assertEqual(data["theme"], "dark")
        self.assertTrue((self.home / ".pi" / "agent" / "settings.json.bak").exists())

    def test_openclaw_writes_nested_primary_only(self):
        """openclaw 顶层加 model 键会被 schema 拒绝启动——只能落嵌套 primary。"""
        from app.core import manager
        out = manager.write_model(self._openclaw_entry(), "astron-code-latest")
        self.assertTrue(out["ok"], msg=out)
        f = self.home / ".openclaw" / "openclaw.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertNotIn("model", data)  # 顶层绝不能出现 model
        self.assertEqual(
            data["agents"]["defaults"]["model"]["primary"], "astron-code-latest")
        self.assertEqual(manager.read_model(self._openclaw_entry()),
                         "astron-code-latest")

    def test_openclaw_upgrades_string_shorthand_and_preserves_rest(self):
        """agents.defaults.model 已是字符串简写形式时升级为对象，其它键原样保留。"""
        from app.core import manager
        f = self.home / ".openclaw" / "openclaw.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "agents": {"defaults": {"model": "old-model"}},
            "gateway": {"port": 1}}), encoding="utf-8")
        out = manager.write_model(self._openclaw_entry(), "new-model")
        self.assertTrue(out["ok"], msg=out)
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data["agents"]["defaults"]["model"]["primary"], "new-model")
        self.assertEqual(data["gateway"], {"port": 1})

    def test_openclaw_missing_file_creates_minimal(self):
        from app.core import manager
        out = manager.write_model(self._openclaw_entry(), "m1")
        self.assertTrue(out["ok"], msg=out)
        data = json.loads(
            (self.home / ".openclaw" / "openclaw.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data["agents"]["defaults"]["model"]["primary"], "m1")


class TestJsoncModelConfig(_HomeIsolated):
    """jsonc 格式：mimo（~/.config/mimocode/mimocode.jsonc 顶层 model）。"""

    def _entry(self):
        return {"id": "mimo-code",
                "config": {"path": "~/.config/mimocode/mimocode.jsonc",
                           "format": "jsonc", "model_key": "model"}}

    def test_write_adds_model_to_schema_only_file(self):
        """本机实况：mimocode.jsonc 只有 $schema；写入必须保留 $schema 并加 model。"""
        from app.core import manager
        f = self.home / ".config" / "mimocode" / "mimocode.jsonc"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{\n  "$schema": "https://mimo.xiaomi.com/mimocode/config.json"\n}\n',
                     encoding="utf-8")
        out = manager.write_model(self._entry(), "mimo/mimo-auto")
        self.assertTrue(out["ok"], msg=out)
        text = f.read_text(encoding="utf-8")
        self.assertIn('"model": "mimo/mimo-auto"', text)
        self.assertIn('"$schema"', text)
        self.assertEqual(manager.read_model(self._entry()), "mimo/mimo-auto")

    def test_write_replaces_existing_model(self):
        from app.core import manager
        f = self.home / ".config" / "mimocode" / "mimocode.jsonc"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{\n  "$schema": "x",\n  "model": "old/p"\n}\n', encoding="utf-8")
        out = manager.write_model(self._entry(), "new/p")
        self.assertTrue(out["ok"], msg=out)
        self.assertEqual(manager.read_model(self._entry()), "new/p")
        self.assertNotIn("old/p", f.read_text(encoding="utf-8"))

    def test_write_creates_file_when_missing(self):
        from app.core import manager
        out = manager.write_model(self._entry(), "mimo/mimo-auto")
        self.assertTrue(out["ok"], msg=out)
        f = self.home / ".config" / "mimocode" / "mimocode.jsonc"
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "mimo/mimo-auto")

    def test_write_preserves_line_comments(self):
        """jsonc 有注释不能整体重解析丢注释；值替换只动那一行。"""
        from app.core import manager
        f = self.home / ".config" / "mimocode" / "mimocode.jsonc"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{\n  // 主模型\n  "model": "old", // 行尾注释\n}\n',
                     encoding="utf-8")
        out = manager.write_model(self._entry(), "new")
        self.assertTrue(out["ok"], msg=out)
        text = f.read_text(encoding="utf-8")
        self.assertIn('"model": "new"', text)
        self.assertIn("// 主模型", text)
        self.assertIn("// 行尾注释", text)


class TestKimiTomlSection(_HomeIsolated):
    """kimi（~/.kimi-code/config.toml 顶层 default_model，toml-section 无表路径）。"""

    def _entry(self):
        return {"id": "kimi-code",
                "config": {"path": "~/.kimi-code/config.toml", "format": "toml-section",
                           "model_key": "default_model"}}

    def test_write_creates_file_with_top_level_key(self):
        """kimi 首启自动建 config.toml，但写入不应等它——缺文件按需创建。"""
        from app.core import manager
        out = manager.write_model(self._entry(), "kimi-code/k3")
        self.assertTrue(out["ok"], msg=out)
        f = self.home / ".kimi-code" / "config.toml"
        text = f.read_text(encoding="utf-8")
        self.assertIn('default_model = "kimi-code/k3"', text)
        self.assertEqual(manager.read_model(self._entry()), "kimi-code/k3")

    def test_write_replaces_top_level_value(self):
        from app.core import manager
        f = self.home / ".kimi-code" / "config.toml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('default_model = "old"\n\n[models."old"]\nprovider = "m"\n',
                     encoding="utf-8")
        out = manager.write_model(self._entry(), "kimi-code/k3")
        self.assertTrue(out["ok"], msg=out)
        text = f.read_text(encoding="utf-8")
        self.assertIn('default_model = "kimi-code/k3"', text)
        # [models] 表原样保留（default_model 的值须指向表里定义的别名）
        self.assertIn('[models."old"]', text)
        self.assertEqual(manager.read_model(self._entry()), "kimi-code/k3")


if __name__ == "__main__":
    import unittest as _u
    _u.main()
