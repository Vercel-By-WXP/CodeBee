# -*- coding: utf-8 -*-
"""记忆 MD vault（memory_vault）回归。

核心语义（2026-11-01，借鉴 Obsidian 本地优先 vault）：
1. export：一 entry 一 MD（frontmatter 带 id/task_id/version），拒绝条目不进 vault；
2. sync_back：手改正文通过**追加 version+1 行**回流（append-only，与 decide 同款）；
3. MD 版本落后日志 = conflict，绝不旧盖新；
4. dry_run 只报不写。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import memory_vault, project_memory as pm, store


class VaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name) / "book"
        self.workdir.mkdir()
        self.vault = Path(self._tmp.name) / "vault"
        self.task = {"id": "t1", "title": "头七结账人", "workdir": str(self.workdir)}
        p1 = mock.patch.object(memory_vault, "vault_dir", return_value=self.vault)
        p2 = mock.patch.object(store, "list_tasks", return_value=[self.task])
        p3 = mock.patch.object(store, "get_task", return_value=self.task)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        # 一条已批准记忆
        self.entry = pm.propose(str(self.workdir), source_run="r1",
                                title="卷名纪律",
                                facts=["本地 json、平台、books.json 三方同步"])

    def _file(self):
        return self.vault / "头七结账人" / ("%s.md" % self.entry["id"])

    def test_export_writes_md_with_frontmatter(self):
        res = memory_vault.export_vault()
        self.assertEqual(res["entries"], 1)
        f = self._file()
        self.assertTrue(f.exists())
        text = f.read_text(encoding="utf-8")
        self.assertIn("id: %s" % self.entry["id"], text)
        self.assertIn("version: 1", text)
        self.assertIn("task_id: t1", text)
        self.assertIn("三方同步", text)
        self.assertTrue((self.vault / "头七结账人" / "_index.md").exists())

    def test_rejected_entries_excluded(self):
        # 直接造一条 rejected 行（propose 只产 approved，reject 需 pending）
        rejected = dict(self.entry, id="rejected-row-1", title="废稿",
                        status="rejected")
        with pm._LOCK:
            pm._append(pm._file(str(self.workdir)), rejected)
        res = memory_vault.export_vault()
        names = {p.name for p in self.vault.rglob("*.md")}
        self.assertNotIn("rejected-row-1.md", names)
        self.assertEqual(res["entries"], 1)

    def test_sync_back_appends_new_version(self):
        memory_vault.export_vault()
        f = self._file()
        text = f.read_text(encoding="utf-8")
        f.write_text(text.replace("三方同步", "三方同步 + 平台截图中留档"),
                     encoding="utf-8")
        dry = memory_vault.sync_back(dry_run=True)
        self.assertEqual(len(dry["updated"]), 1)
        rows = pm.list_entries(str(self.workdir))
        self.assertEqual(rows[0]["content"],
                         "- 本地 json、平台、books.json 三方同步")
        out = memory_vault.sync_back()
        self.assertEqual(out["updated"][0]["version"], 2)
        rows = pm.list_entries(str(self.workdir))
        self.assertEqual(rows[0]["version"], 2)
        self.assertIn("平台截图中留档", rows[0]["content"])
        self.assertTrue(rows[0]["verified"])

    def test_sync_back_conflict_on_stale_version(self):
        memory_vault.export_vault()
        pm.decide(str(self.workdir), self.entry["id"], "archive")   # 日志 v2
        pm.decide(str(self.workdir), self.entry["id"], "restore")   # v3
        f = self._file()
        text = f.read_text(encoding="utf-8")
        f.write_text(text + "\n手改内容\n", encoding="utf-8")   # 文件还写着 v1
        res = memory_vault.sync_back()
        self.assertEqual(len(res["conflicts"]), 1)
        self.assertEqual(res["conflicts"][0]["journal_version"], 3)
        self.assertEqual(pm.list_entries(str(self.workdir))[0]["version"], 3)

    def test_sync_back_unchanged_and_skipped(self):
        memory_vault.export_vault()
        res = memory_vault.sync_back()
        self.assertEqual(res["unchanged"], 1)
        # 未知条目 → skipped
        stray = self.vault / "头七结账人" / "zzz-stray.md"
        stray.write_text("---\nid: nope\ntask_id: t1\nversion: 1\n---\n\nx\n",
                         encoding="utf-8")
        res2 = memory_vault.sync_back()
        self.assertEqual(res2["skipped"], 1)

    def test_sync_back_no_vault_dir(self):
        res = memory_vault.sync_back()
        self.assertEqual(res, {"updated": [], "conflicts": [],
                               "unchanged": 0, "skipped": 0})


if __name__ == "__main__":
    unittest.main()
