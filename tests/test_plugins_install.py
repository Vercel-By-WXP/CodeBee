# -*- coding: utf-8 -*-
"""本地插件安装链回归：残缺目标自愈 + 异常回滚路径不再炸 UnboundLocalError。

背景（2026-10-10 Mac 实案）：install() 的 market_written 在 try 中段才赋值，
_copy_plugin/load_manifest 先行抛错时 except 读未绑定变量 → 人话错误
（如「安装目标已存在」）被炸成 500「服务内部错误」，且残留目录清理被跳过，
之后每次重装都撞同一堵墙。

跑法：python -m unittest discover -s tests -p "test_plugins_install.py" -v
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest

PID = "auto-retry-429"

MANIFEST = {
    "name": PID,
    "version": "0.1.0",
    "description": "Retry Codex after a 429 limit error.",
    "author": {"name": "Local developer"},
    "interface": {
        "displayName": "Auto Retry 429",
        "shortDescription": "retry on 429",
        "longDescription": "retry on 429",
        "developerName": "Local developer",
        "category": "Productivity",
        "defaultPrompt": "retry the failed call",
    },
    "skills": ["skills"],
}


def _seed_plugin(tmp):
    """种一个可发现的本地插件源目录，返回其父目录（可作 roots 传入）。"""
    src_root = tmp / "src-plugins"
    plugin = src_root / PID
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
    (plugin / "skills").mkdir()
    (plugin / "skills" / "retry.md").write_text(
        "---\nname: retry-429\n---\n遇到 429 限流错误时等待后重试。" * 20,
        encoding="utf-8")
    return src_root


class PluginInstallTests(BaseTest):
    def test_stale_partial_target_self_heals(self):
        """上次安装中途失败的残缺目录（无登记记录）→ 清掉重装而非报错。"""
        from app.core import plugins
        src_root = _seed_plugin(self.tmp)
        stale = plugins._install_root() / PID
        stale.mkdir(parents=True)
        (stale / "half-copied.bin").write_text("junk", encoding="utf-8")
        res, err = plugins.install(PID, roots=[src_root])
        self.assertIsNone(err, err)
        self.assertFalse(res.get("already"))
        self.assertTrue((stale / ".codex-plugin" / "plugin.json").is_file())
        self.assertFalse((stale / "half-copied.bin").exists())   # 残缺物被换掉
        reg = json.loads((self.data_dir / "plugins.json").read_text(encoding="utf-8"))
        self.assertIn(PID, reg["installed"])

    def test_pre_copy_failure_returns_message_not_unbound(self):
        """记账前（_copy_plugin 阶段）抛错 → 返回人话错误且不炸 UnboundLocalError。"""
        from app.core import plugins
        src_root = _seed_plugin(self.tmp)
        with mock.patch.object(plugins, "_copy_plugin",
                               side_effect=plugins.PluginError("模拟拷贝中断")):
            res, err = plugins.install(PID, roots=[src_root])
        self.assertIsNone(res)
        self.assertEqual(err, "模拟拷贝中断")

    def test_market_failure_rolls_back_and_cleans_target(self):
        """market.install_files 报错 → 返回人话错误并清掉已拷贝的目标目录。"""
        from app.core import plugins
        src_root = _seed_plugin(self.tmp)
        with mock.patch.object(plugins.market, "install_files",
                               return_value=(None, "mock注入失败")):
            res, err = plugins.install(PID, roots=[src_root])
        self.assertIsNone(res)
        self.assertEqual(err, "mock注入失败")
        self.assertFalse((plugins._install_root() / PID).exists())
        self.assertNotIn(PID, plugins._load_registry()["installed"])

    def test_reinstall_after_success_is_already(self):
        """装成功后再点安装 → already 幂等返回，不重拷不重记账。"""
        from app.core import plugins
        src_root = _seed_plugin(self.tmp)
        res, err = plugins.install(PID, roots=[src_root])
        self.assertIsNone(err, err)
        res2, err2 = plugins.install(PID, roots=[src_root])
        self.assertIsNone(err2, err2)
        self.assertTrue(res2["already"])
        self.assertEqual(res2["skill_count"], 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
