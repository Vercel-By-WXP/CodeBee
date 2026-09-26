# -*- coding: utf-8 -*-
"""「一键升级全部」选条逻辑回归（manager.upgrade_all_targets）：

只收 已安装 + 配了 upgrade 命令 + 更新检查不是「已是最新」的条目。
四个边界各是一条防线：
- 只配 install 没配 upgrade 的不收——op=upgrade 硬依赖 upgrade 字段，
  收了只会起一个必败 run（run_mgmt_command 报「未配置 upgrade 命令」）；
- 「已是最新」不收——防无谓重装撞 EBUSY 文件锁（2026-09-18 dsh 案诱因）；
- unknown/unsupported 收——宁可空跑一次幂等升级也不漏升。
"""
import time
import types
import unittest

from base import BaseTest


class TestUpgradeAllTargets(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import manager
        self.manager = manager
        self._old_catalog = manager.catalog
        self._old_detect = manager.detect_all
        self._old_cache = dict(manager._UPDATE_CACHE)
        self.addCleanup(self._restore)

    def _restore(self):
        self.manager.catalog = self._old_catalog
        self.manager.detect_all = self._old_detect
        with self.manager._LOCK:
            self.manager._UPDATE_CACHE.clear()
            self.manager._UPDATE_CACHE.update(self._old_cache)

    def _seed(self, entries, detected, update_cache=None):
        self.manager.catalog = types.SimpleNamespace(load=lambda: entries)
        self.manager.detect_all = lambda force=False: detected
        with self.manager._LOCK:
            self.manager._UPDATE_CACHE.clear()
            for eid, info in (update_cache or {}).items():
                self.manager._UPDATE_CACHE[eid] = (time.time(), info)

    def test_installed_with_upgrade_and_unknown_status_included(self):
        """已安装+配了升级+没查过更新（unknown）→ 收。"""
        self._seed(
            [{"id": "a", "name": "A", "upgrade": "npm install -g a@latest"}],
            {"a": {"installed": True, "detail": "x"}},
        )
        got = [e["id"] for e in self.manager.upgrade_all_targets()]
        self.assertEqual(got, ["a"])

    def test_current_excluded(self):
        """更新检查结论「已是最新」→ 跳过。"""
        self._seed(
            [{"id": "a", "name": "A", "upgrade": "npm install -g a@latest"}],
            {"a": {"installed": True, "detail": "x"}},
            {"a": {"current": "1.0", "latest": "1.0", "updatable": False,
                   "note": "已是最新版本"}},
        )
        self.assertEqual(self.manager.upgrade_all_targets(), [])

    def test_unsupported_channel_included(self):
        """渠道查不了更新（unsupported，如 pip/winget 之外的自定义命令）→ 仍收。"""
        self._seed(
            [{"id": "a", "name": "A", "upgrade": "py -3.13 -m pip install -U a"}],
            {"a": {"installed": True, "detail": "x"}},
            {"a": {"current": "1.0", "latest": None, "updatable": None,
                   "note": "该渠道暂不支持自动检查更新，可直接点升级尝试"}},
        )
        got = [e["id"] for e in self.manager.upgrade_all_targets()]
        self.assertEqual(got, ["a"])

    def test_not_installed_excluded(self):
        """未安装 → 不收（一键升级不做首次安装）。"""
        self._seed(
            [{"id": "a", "name": "A", "upgrade": "npm install -g a@latest"}],
            {"a": {"installed": False, "detail": ""}},
        )
        self.assertEqual(self.manager.upgrade_all_targets(), [])

    def test_install_only_excluded(self):
        """只配 install 没配 upgrade → 不收（否则起必败 run）。"""
        self._seed(
            [{"id": "a", "name": "A", "install": "npm install -g a"}],
            {"a": {"installed": True, "detail": "x"}},
        )
        self.assertEqual(self.manager.upgrade_all_targets(), [])


if __name__ == "__main__":
    unittest.main()  # noqa: F821
