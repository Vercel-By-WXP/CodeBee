# -*- coding: utf-8 -*-
"""手工发布回执守测（2026-11-05，补「手动发布包之后没有回执」缺口）。

守六件事：
1. 补登即进账：回执章写 upload_chapter 成功行（source=manual），
   stats.summary 的 live 桶（已发数/成功率）立刻能数到。
2. 幂等：同一章重复登记 → skipped，不写重复行。
3. 校准集并入：published_chapters 的并集口径认回执行——补登的章自动进
   「发布全部待发」的幂等跳过集，不会重发。
4. 章号闸：0/负数/超界/非数字丢弃；单次封顶 200 章。
5. 平台未登记 / 空章号 → 空列表（调用方转 400）。
6. 无 source 的普通行不带 source 键——老行结构保持干净。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.publish import ledger
from core.publish import stats
from core import paths
from core import store as core_store


class ManualReceiptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(paths, "PUBLISH_DIR", base / "publish"),
            mock.patch.object(ledger, "book_for", return_value=None),
            mock.patch.object(core_store, "serial_chain_ids",
                              side_effect=lambda tid: [str(tid)]),
            mock.patch.object(core_store, "get_task",
                              return_value={"id": "T-1", "title": "界碑以北"}),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _rows(self):
        out = []
        for fp in sorted(paths.PUBLISH_DIR.glob("publish-*.jsonl")):
            for ln in fp.read_text(encoding="utf-8").splitlines():
                out.append(json.loads(ln))
        return out

    # 1. 补登即进账
    def test_receipt_writes_live_rows(self):
        added, skipped = ledger.record_manual_receipt("T-1", "fanqie", [5, 6, 7])
        self.assertEqual(added, [5, 6, 7])
        self.assertEqual(skipped, [])
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        for r, n in zip(rows, (5, 6, 7)):
            self.assertEqual(r["action"], "upload_chapter")
            self.assertTrue(r["ok"])
            self.assertEqual(r["source"], "manual")
            self.assertEqual(r["chapter_no"], n)
            self.assertEqual(r["task_id"], "T-1")
            self.assertEqual(r["title"], "界碑以北")
        s = stats.summary(days=7, task_id="T-1")
        self.assertEqual(s["live"]["attempts"], 3)
        self.assertEqual(s["live"]["ok"], 3)
        self.assertEqual(s["live"]["rate"], 1.0)

    # 2+3. 幂等 + 校准集并入
    def test_idempotent_and_dedup_set(self):
        ledger.record_manual_receipt("T-1", "fanqie", [5, 6])
        added, skipped = ledger.record_manual_receipt("T-1", "fanqie", [6, 7])
        self.assertEqual(added, [7])
        self.assertEqual(skipped, [6])
        self.assertEqual(len(self._rows()), 3)
        self.assertEqual(ledger.published_chapters("T-1", "fanqie"), {5, 6, 7})

    # 4. 章号闸
    def test_chapter_gate(self):
        added, _ = ledger.record_manual_receipt(
            "T-1", "fanqie", [0, -3, "x", 999999999, 12, "12", 12.0])
        self.assertEqual(added, [12])
        # 封顶 200 在输入侧（1..200 收进来），其中 12 已在账被跳过 → 199
        added2, skipped2 = ledger.record_manual_receipt("T-1", "fanqie", list(range(1, 500)))
        self.assertEqual(len(added2), 199)
        self.assertEqual(skipped2, [12])
        self.assertEqual(len(self._rows()), 200)   # 1（首段）+ 199（本段）

    # 5. 无效入参
    def test_invalid_inputs(self):
        self.assertEqual(ledger.record_manual_receipt("", "fanqie", [1]), ([], []))
        self.assertEqual(ledger.record_manual_receipt("T-1", "", [1]), ([], []))
        self.assertEqual(ledger.record_manual_receipt("T-1", "fanqie", []),
                         ([], []))
        self.assertEqual(ledger.record_manual_receipt(None, None, None),
                         ([], []))

    # 6. 老行不带 source 键
    def test_plain_rows_have_no_source_key(self):
        ledger.record("fanqie", "upload_chapter", task_id="T-1", chapter_no=1)
        row = self._rows()[0]
        self.assertNotIn("source", row)


if __name__ == "__main__":
    unittest.main()
