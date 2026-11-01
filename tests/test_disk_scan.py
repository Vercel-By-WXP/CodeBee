# -*- coding: utf-8 -*-
"""AI 磁盘清理（disk_scan）回归。

核心语义（2026-11-01，借鉴 Purrge「AI 逐项判定 → 人勾选 → 才清理」）：
1. scan 只读盘点：可清候选（logs/review）分级，活数据（tasks/publish/...）永不入清单；
2. clean = **移动到 data/trash/<批次>/**，不是删除；白名单外拒收；
3. restore 整批搬回；trash 本身不在盘点清单里（防递归）；
4. judge：AI 不可用 → 回落启发式，不编造。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import disk_scan, paths


class _TempData(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data = Path(self._dir.name)
        # 建目录骨架
        for name in ("runs", "usage", "errors", "audit", "tasks", "publish",
                     "workspace", "mystery-dir"):
            (self.data / name).mkdir(exist_ok=True)
        (self.data / "runs" / "a.log").write_text("x" * 2048, encoding="utf-8")
        (self.data / "usage" / "u.jsonl").write_text("{}\n", encoding="utf-8")
        (self.data / "tasks" / "t.json").write_text("{}", encoding="utf-8")
        (self.data / "mystery-dir" / "m.bin").write_text("y" * 512, encoding="utf-8")
        self._saved = paths.DATA_DIR
        self._saved_root = disk_scan.paths.DATA_DIR
        p1 = mock.patch.object(paths, "DATA_DIR", self.data)
        p2 = mock.patch.object(disk_scan.paths, "DATA_DIR", self.data)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def tearDown(self):
        self._dir.cleanup()


class ScanTests(_TempData):
    def test_scan_lists_candidates_and_excludes_never_and_trash(self):
        res = disk_scan.scan()
        names = {it["path"] for it in res["items"]}
        self.assertIn("runs", names)
        self.assertIn("usage", names)
        self.assertNotIn("tasks", names)          # 活数据永不入清单
        self.assertNotIn("publish", names)
        self.assertNotIn("trash", names)          # 防递归
        runs = next(it for it in res["items"] if it["path"] == "runs")
        self.assertEqual(runs["kind"], "logs")
        self.assertEqual(runs["heuristic"], "cleanable")
        self.assertGreaterEqual(runs["size"], 2048)
        mystery = next(it for it in res["items"] if it["path"] == "mystery-dir")
        self.assertEqual(mystery["kind"], "unknown")
        self.assertEqual(mystery["heuristic"], "never")

    def test_scan_read_only(self):
        before = sorted(str(p) for p in self.data.rglob("*"))
        disk_scan.scan()
        after = sorted(str(p) for p in self.data.rglob("*"))
        self.assertEqual(before, after)


class CleanTests(_TempData):
    def test_clean_moves_to_trash_not_delete(self):
        res = disk_scan.clean(["runs", "mystery-dir", "../escape"])
        self.assertIn("runs", res["moved"])
        refused = {r["path"] for r in res["refused"]}
        self.assertIn("mystery-dir", refused)     # unknown 不在白名单
        self.assertIn("../escape", refused)       # 路径穿越拒收
        self.assertFalse((self.data / "runs").exists())
        trash = self.data / "trash" / res["trash"] / "runs"
        self.assertTrue(trash.is_dir())
        self.assertTrue((trash / "a.log").exists())

    def test_restore_moves_back(self):
        res = disk_scan.clean(["runs"])
        batch = res["trash"]
        out = disk_scan.restore(batch)
        self.assertEqual(out["restored"], ["runs"])
        self.assertTrue((self.data / "runs" / "a.log").exists())
        self.assertFalse((self.data / "trash" / batch).exists())

    def test_restore_bad_batch_rejected(self):
        with self.assertRaises(ValueError):
            disk_scan.restore("no-such-batch")
        with self.assertRaises(ValueError):
            disk_scan.restore("../../etc")

    def test_clean_refuses_missing_dir(self):
        res = disk_scan.clean(["usage"])
        self.assertIn("usage", res["moved"])
        res2 = disk_scan.clean(["usage"])
        self.assertEqual(res2["moved"], [])
        self.assertIn("目录不存在", res2["refused"][0]["reason"])


class JudgeTests(_TempData):
    def test_judge_falls_back_to_heuristic_without_model(self):
        def _no_orch():
            return None
        from core import modelhub
        with mock.patch.object(modelhub, "resolve_orchestrator", _no_orch):
            res = disk_scan.judge()
        runs = next(it for it in res["items"] if it["path"] == "runs")
        self.assertEqual(runs["ai_verdict"], "cleanable")
        usage = next(it for it in res["items"] if it["path"] == "usage")
        self.assertEqual(usage["ai_verdict"], "review")

    def test_judge_uses_ai_verdicts_when_valid(self):
        from core import modelhub
        fake_orch = ({"id": "p1"}, "m1")
        fake_res = {"ok": True, "text": '[{"path":"runs","verdict":"cleanable",'
                                        '"reason":"纯日志"},'
                                        '{"path":"usage","verdict":"keep",'
                                        '"reason":"台账"}]'}
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=fake_orch), \
             mock.patch.object(modelhub, "chat", return_value=fake_res):
            res = disk_scan.judge()
        runs = next(it for it in res["items"] if it["path"] == "runs")
        usage = next(it for it in res["items"] if it["path"] == "usage")
        self.assertEqual(runs["ai_verdict"], "cleanable")
        self.assertEqual(runs["ai_reason"], "纯日志")
        self.assertEqual(usage["ai_verdict"], "keep")


if __name__ == "__main__":
    unittest.main()
