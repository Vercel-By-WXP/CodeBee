# -*- coding: utf-8 -*-
"""trae-agent 安装命令钉 cryptography<49 的回归。

背景：cryptography 49.0.0 起不再发 Intel Mac wheel（只剩 macosx_11_0_arm64），
uv 找不到 wheel 落 sdist 经 maturin/cargo 编译，openssl-sys 在无 brew 的 Mac
上缺 pkg-config/OpenSSL 开发头必死（2026-10-10 Mac trae-agent 实案）。
钉 <49（48.x 是最后带 macosx_10_9_universal2 的系列，google-auth 2.61 只要求
>=38.0.3）让 uv 恒走预编译 wheel；存量 catalog.json 里未钉版的旧命令由
_apply_install_patch 幂等迁移。
"""
import unittest

from core import catalog

_TRAE_GIT = "git+https://github.com/bytedance/trae-agent"


class TraeCryptoPinTests(unittest.TestCase):
    def test_default_catalog_pinned(self):
        entry = next(e for e in catalog.DEFAULT_CATALOG if e.get("id") == "trae-agent")
        self.assertIn("cryptography<49", entry["install"])
        self.assertIn("cryptography<49", entry["upgrade"])
        self.assertIn(_TRAE_GIT, entry["install"])

    def test_legacy_uv_command_migrated(self):
        entries = [{"id": "trae-agent",
                    "install": "uv tool install --python 3.12 " + _TRAE_GIT,
                    "upgrade": "uv tool install --force --python 3.12 " + _TRAE_GIT}]
        catalog._apply_install_patch(entries)
        self.assertIn("cryptography<49", entries[0]["install"])
        self.assertIn("cryptography<49", entries[0]["upgrade"])
        self.assertIn(_TRAE_GIT, entries[0]["install"])

    def test_already_pinned_untouched(self):
        pinned = 'uv tool install --python 3.12 --with "cryptography<49" ' + _TRAE_GIT
        entries = [{"id": "trae-agent", "install": pinned, "upgrade": pinned}]
        catalog._apply_install_patch(entries)
        self.assertEqual(entries[0]["install"], pinned)
        self.assertEqual(entries[0]["upgrade"], pinned)

    def test_custom_non_uv_command_untouched(self):
        custom = "pip install /local/trae-agent"
        entries = [{"id": "trae-agent", "install": custom, "upgrade": custom}]
        catalog._apply_install_patch(entries)
        self.assertEqual(entries[0]["install"], custom)

    def test_other_entries_untouched(self):
        entries = [{"id": "aider",
                    "install": "uv tool install --python 3.12 aider-chat",
                    "upgrade": "uv tool upgrade aider-chat"}]
        catalog._apply_install_patch(entries)
        self.assertNotIn("cryptography", entries[0]["install"])


if __name__ == "__main__":
    unittest.main()
