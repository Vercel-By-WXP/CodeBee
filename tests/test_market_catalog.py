# -*- coding: utf-8 -*-
"""插件市场目录 JSON（market_catalog）回归。

核心语义（2026-11-01，借鉴 Obsidian community-plugins.json 目录分发协议）：
1. validate_manifest：id/name/description 必填、id 域约束、homepage URL 校验；
2. catalog_json：本地市场 → 分发格式；
3. import_catalog：逐条校验，有效条目对账本地可安装性（installable_now/unknown）；
   单条失败只进 errors 不整单拒绝。
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import market_catalog


def _m(pid="good-pack", **kw):
    base = {"id": pid, "name": "包", "description": "说明"}
    base.update(kw)
    return base


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_passes(self):
        norm, errs = market_catalog.validate_manifest(_m(version="1.0.0",
                                                         homepage="https://x.y",
                                                         scopes=["novel"]))
        self.assertEqual(errs, [])
        self.assertEqual(norm["version"], "1.0.0")
        self.assertEqual(norm["scopes"], ["novel"])

    def test_missing_required_fields(self):
        _n, errs = market_catalog.validate_manifest({"id": "x", "name": ""})
        self.assertTrue(any("name" in e for e in errs))
        self.assertTrue(any("description" in e for e in errs))

    def test_bad_id_rejected(self):
        _n, errs = market_catalog.validate_manifest(
            _m(pid="Bad Id!"))
        self.assertTrue(any("id" in e for e in errs))

    def test_bad_homepage_rejected(self):
        _n, errs = market_catalog.validate_manifest(_m(homepage="javascript:alert(1)"))
        self.assertTrue(any("homepage" in e for e in errs))

    def test_non_object_rejected(self):
        _n, errs = market_catalog.validate_manifest("nope")
        self.assertEqual(len(errs), 1)
        self.assertIn("对象", errs[0])


class CatalogTests(unittest.TestCase):
    def test_catalog_json_from_injected_view(self):
        view = {"catalog": [{"id": "a", "name": "A", "desc": "d", "category": "c",
                             "scopes": ["*"], "installed": True,
                             "builtin": False, "installable": True}]}
        cat = market_catalog.catalog_json(view)
        self.assertEqual(cat["schema"], "codebee-market-catalog/1")
        self.assertEqual(cat["plugins"][0]["id"], "a")
        self.assertTrue(cat["plugins"][0]["installed"])

    def test_import_valid_and_unknown(self):
        local = market_catalog.catalog_json({"catalog": [
            {"id": "git-workflow", "name": "g", "desc": "d", "category": "c",
             "scopes": ["*"], "installed": False, "builtin": False,
             "installable": True}]})
        with mock.patch.object(market_catalog, "catalog_json", return_value=local):
            res = market_catalog.import_catalog({
                "schema": "codebee-market-catalog/1",
                "plugins": [{"id": "git-workflow", "name": "g", "description": "d"},
                            {"id": "third-party", "name": "t", "description": "d"}]})
        self.assertTrue(res["schema_ok"])
        self.assertEqual(res["installable_now"], ["git-workflow"])
        self.assertEqual(res["unknown"], ["third-party"])
        self.assertEqual(res["errors"], [])

    def test_import_single_bad_entry_does_not_reject_all(self):
        local = market_catalog.catalog_json({"catalog": []})
        with mock.patch.object(market_catalog, "catalog_json", return_value=local):
            res = market_catalog.import_catalog({
                "schema": "codebee-market-catalog/1",
                "plugins": [{"id": "ok-1", "name": "n", "description": "d"},
                            {"id": "BAD ID", "name": "n", "description": "d"},
                            {"id": "ok-2", "name": "n", "description": "d"}]})
        self.assertEqual(len(res["valid"]), 2)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0]["index"], 1)

    def test_import_non_object(self):
        res = market_catalog.import_catalog("nope")
        self.assertFalse(res["schema_ok"])
        self.assertEqual(len(res["errors"]), 1)


class VersionPinTests(unittest.TestCase):
    """版本钉扎（2026-11-01 借 Ruflo 纪律：目录条目必须可复现）。"""

    def test_catalog_json_passes_version_through(self):
        view = {"catalog": [{"id": "a", "name": "A", "desc": "d", "category": "c",
                             "version": "1.2.0", "scopes": ["*"],
                             "installed": True, "builtin": False,
                             "installable": True}]}
        cat = market_catalog.catalog_json(view)
        self.assertEqual(cat["plugins"][0]["version"], "1.2.0")

    def test_import_reports_unpinned_entries(self):
        local = market_catalog.catalog_json({"catalog": [
            {"id": "git-workflow", "name": "g", "desc": "d", "category": "c",
             "version": "2.0.0", "scopes": ["*"], "installed": False,
             "builtin": False, "installable": True}]})
        with mock.patch.object(market_catalog, "catalog_json",
                               return_value=local):
            res = market_catalog.import_catalog({
                "schema": "codebee-market-catalog/1",
                "plugins": [{"id": "git-workflow", "name": "g",
                             "description": "d"}]})
        self.assertEqual(res["unpinned"], ["git-workflow"])
        self.assertEqual(res["version_mismatch"], [])

    def test_import_version_mismatch_reported(self):
        local = market_catalog.catalog_json({"catalog": [
            {"id": "git-workflow", "name": "g", "desc": "d", "category": "c",
             "version": "2.0.0", "scopes": ["*"], "installed": False,
             "builtin": False, "installable": True}]})
        with mock.patch.object(market_catalog, "catalog_json",
                               return_value=local):
            res = market_catalog.import_catalog({
                "schema": "codebee-market-catalog/1",
                "plugins": [{"id": "git-workflow", "name": "g",
                             "description": "d", "version": "1.0.0"}]})
        self.assertEqual(res["installable_now"], ["git-workflow"])
        self.assertEqual(res["version_mismatch"],
                         [{"id": "git-workflow", "catalog": "1.0.0",
                           "local": "2.0.0"}])
        self.assertEqual(res["unpinned"], [])

    def test_import_same_version_no_mismatch(self):
        local = market_catalog.catalog_json({"catalog": [
            {"id": "git-workflow", "name": "g", "desc": "d", "category": "c",
             "version": "1.0.0", "scopes": ["*"], "installed": False,
             "builtin": False, "installable": True}]})
        with mock.patch.object(market_catalog, "catalog_json",
                               return_value=local):
            res = market_catalog.import_catalog({
                "schema": "codebee-market-catalog/1",
                "plugins": [{"id": "git-workflow", "name": "g",
                             "description": "d", "version": "1.0.0"}]})
        self.assertEqual(res["version_mismatch"], [])
        self.assertEqual(res["unpinned"], [])


if __name__ == "__main__":
    unittest.main()
