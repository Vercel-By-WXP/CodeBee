# -*- coding: utf-8 -*-
"""EverOS Tools 市场源（Raven 生态借鉴·C 件）单测。

fixture 复刻 2026-10-22 实抓的 EverMind-AI/everos-plugins
.claude-plugin/marketplace.json 载荷形状（repo-relative 条目），不打真网：
验证源注册、条目归一化为 git 克隆安装（仓库取子目录）。
"""
import unittest

from app.core import market_remote


EVEROS_MANIFEST = {
    "name": "everos-tools",
    "metadata": {"description": "Official EverOS developer tools"},
    "owner": {"name": "EverMind AI"},
    "plugins": [
        {"name": "everos-sdk-upgrade",
         "source": "./plugins/everos-sdk-upgrade",
         "description": "Migrate EverOS Cloud callers between API/SDK versions."},
        {"name": "broken-no-source", "description": "source 缺省 → ./ 全仓库"},
    ],
}


class EverosSourceTests(unittest.TestCase):
    def _source_meta(self):
        return next(s for s in market_remote.SOURCES if s["id"] == "everos")

    def test_source_registered_with_repo(self):
        src = self._source_meta()
        self.assertEqual(src["kind"], "marketplace")
        self.assertIn("EverMind-AI/everos-plugins", src["repo"])
        self.assertTrue(any("jsdelivr" in u for u in src["urls"]))
        self.assertTrue(any("raw.githubusercontent" in u for u in src["urls"]))

    def test_normalize_repo_relative_entry_to_git_install(self):
        meta = self._source_meta()
        entry = market_remote._normalize(meta, EVEROS_MANIFEST["plugins"][0])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["id"], "remote-everos-everos-sdk-upgrade")
        self.assertIn("Migrate EverOS", entry["desc"])
        install = entry["install"]
        self.assertEqual(install["kind"], "git")
        self.assertEqual(install["url"], meta["repo"])
        self.assertEqual(install["path"], "plugins/everos-sdk-upgrade")

    def test_normalize_missing_source_defaults_to_repo_root(self):
        meta = self._source_meta()
        entry = market_remote._normalize(meta, EVEROS_MANIFEST["plugins"][1])
        self.assertEqual(entry["install"]["kind"], "git")
        self.assertEqual(entry["install"]["path"], "")

    def test_repo_relative_without_repo_meta_is_unsupported(self):
        meta = dict(self._source_meta())
        meta.pop("repo")
        entry = market_remote._normalize(meta, EVEROS_MANIFEST["plugins"][0])
        self.assertEqual(entry["install"]["kind"], "unsupported")


if __name__ == "__main__":
    unittest.main()
