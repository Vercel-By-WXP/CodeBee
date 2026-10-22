# -*- coding: utf-8 -*-
"""技能闸锐化回归（MatrixHub 借鉴，2026-10-22）。

两条：①安检证据绑定扫描器代数——规则库换代后旧装包标记待重扫，不冒充
现行规则的结论；②内容变更升待定——新落盘内容与上次见过的不一样 = 停用
待复核，用户核对 diff 后手动重新启用（跨卸载投毒对账的处置半环）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import market, paths, skill_scan, skills


def _files(body):
    return {"market-gen-demo.md": (
        "---\nname: 代数演示\nscopes:\n- \"*\"\nsource: market\n"
        "market_id: gen-demo\n---\n\n" + body)}


class MarketScanGenerationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_data = paths.DATA_DIR
        self._old_file = skills._FILE
        paths.DATA_DIR = Path(self._tmp.name)
        skills._FILE = paths.DATA_DIR / "skills.json"
        self._reset_caches()
        self.addCleanup(self._restore)

    def _reset_caches(self):
        skills._user_pack_cache.clear()
        skills._user_dir_mtime["ts"] = 0.0
        skills._user_dir_mtime["ids"] = None

    def _restore(self):
        paths.DATA_DIR = self._old_data
        skills._FILE = self._old_file
        self._reset_caches()

    def _catalog_item(self, pid):
        item = next((it for it in market.view()["catalog"]
                     if it["id"] == pid), None)
        self.assertIsNotNone(item)
        return item

    def _user_pack_id(self):
        ups = [p for p in skills.user_packs() if p["name"] == "代数演示"]
        self.assertEqual(len(ups), 1)
        return ups[0]["id"]

    def test_catalog_items_expose_new_fields(self):
        """目录项带待复核/待重扫字段（内置目录项未装 → 默认全否）。"""
        item = self._catalog_item("git-workflow")
        self.assertIn("pending_review", item)
        self.assertIn("scan_stale", item)
        self.assertFalse(item["pending_review"])
        self.assertFalse(item["scan_stale"])

    def test_install_records_current_scan_generation(self):
        res, err = market.install_files("gen-demo", "代数演示", _files("v1"))
        self.assertIsNone(err)
        self.assertFalse(res["pending_review"])

        card = market.skill_card("gen-demo")
        self.assertEqual(card["scan_generation"], skill_scan.rules_generation())
        self.assertFalse(card["scan_stale"])

    def test_rules_upgrade_marks_installed_pack_stale(self):
        market.install_files("gen-demo", "代数演示", _files("v1"))
        with mock.patch.object(skill_scan, "rules_generation",
                               return_value="deadbeef0000"):
            self.assertTrue(market.skill_card("gen-demo")["scan_stale"])

    def test_rules_generation_tracks_pattern_changes(self):
        gen_before = skill_scan.rules_generation()
        with mock.patch.object(skill_scan, "_PATTERNS",
                               list(skill_scan._PATTERNS) + [
                                   (r"never-matches-xyz", "代码执行", "测试规则")]):
            gen_after = skill_scan.rules_generation()
        self.assertNotEqual(gen_before, gen_after)

    def test_changed_content_after_reinstall_disables_pending_review(self):
        """卸载后重装变了内容的包：新内容已落盘 → 停用待复核。"""
        market.install_files("gen-demo", "代数演示", _files("v1 body"))
        market.remove("gen-demo")

        res, err = market.install_files("gen-demo", "代数演示",
                                        _files("v2 body changed"))
        self.assertIsNone(err)
        self.assertTrue(res["pending_review"])
        self.assertTrue(any("已停用待复核" in w for w in res["warnings"]))
        self.assertTrue(market.skill_card("gen-demo")["pending_review"])

        pid = self._user_pack_id()
        self.assertFalse(skills._pack_enabled(pid))

        # 用户核对 diff 后手动重新启用 = 复核完成
        skills.pack_op(pid, "enable")
        self.assertTrue(skills._pack_enabled(pid))

    def test_fresh_install_and_unchanged_reinstall_do_not_pending(self):
        res, err = market.install_files("gen-demo", "代数演示", _files("v1"))
        self.assertIsNone(err)
        self.assertFalse(res["pending_review"])

        res, err = market.install_files("gen-demo", "代数演示", _files("v1"))
        self.assertIsNone(err)
        self.assertTrue(res["already"])
        self.assertFalse(res["changed"])
        self.assertFalse(res["pending_review"])

    def test_already_path_keeps_warning_without_disabling(self):
        """幂等重装路径：磁盘上生效的内容没变（只是目录内容源更新未应用），
        保持警示语义，不误停用。"""
        market.install_files("gen-demo", "代数演示", _files("v1 body"))
        pid = self._user_pack_id()

        res, err = market.install_files("gen-demo", "代数演示",
                                        _files("v2 body changed"))
        self.assertIsNone(err)
        self.assertTrue(res["already"])
        self.assertTrue(res["changed"])
        self.assertFalse(res["pending_review"])
        self.assertTrue(any("建议核对diff" in w for w in res["warnings"]))
        self.assertTrue(skills._pack_enabled(pid))


if __name__ == "__main__":
    unittest.main()
