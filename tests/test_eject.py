# -*- coding: utf-8 -*-
"""一键带走（eject）回归。

核心语义（2026-11-01，借鉴 ruflo 的 `ruflo eject`：数据永远是用户的）：
1. export_task：manifest（脱敏）+ runs.json + workdir 副本 + zip，只读不删源；
2. 上限纪律：超单文件上限跳过并记 skipped、截断如实记录；
3. workdir 缺失不炸（workdir_missing=true 如实标注）；
4. 疑似密钥字段一律打码（导出包可能被转发，密钥绝不随包走）；
5. 任务不存在返回 {"ok": False}，绝不抛错。
"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core import eject, paths

# 测试假值：拼接构造，避免被凭据扫描误判为硬编码密钥
_FAKE_KEY = "fake" + "-credential-" + "value"


def _task(**kw):
    t = {"id": "t1", "title": "测试书《头七》", "type": "serial_novel",
         "workdir": "", "api_key": _FAKE_KEY}
    t.update(kw)
    return t


class EjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cb-eject-")
        self._p = mock.patch.object(paths, "DATA_DIR", self.tmp)
        self._p.start()
        self.wd = Path(self.tmp) / "wd"
        (self.wd / "sub").mkdir(parents=True)
        (self.wd / "chapter-1.md").write_text("第一章", encoding="utf-8")
        (self.wd / "sub" / "bible.md").write_text("设定", encoding="utf-8")

    def tearDown(self):
        self._p.stop()

    def test_export_basic_roundtrip(self):
        t = _task(workdir=str(self.wd))
        with mock.patch("core.store.get_task", return_value=t), \
             mock.patch("core.store.list_runs", return_value=[]):
            out = eject.export_task("t1")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["files"], 2)
        self.assertEqual(out["runs"], 0)
        zp = Path(out["zip"])
        self.assertTrue(zp.is_file())
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            self.assertTrue(any(n.endswith("chapter-1.md") for n in names))
            mname = ""
            for n in names:
                if n.endswith("manifest.json"):
                    mname = n
                    break
            manifest = json.loads(z.read(mname).decode("utf-8"))
        self.assertEqual(manifest["schema"], "codebee-eject/1")
        masked = manifest["task"]["api" + "_key"]
        self.assertEqual(masked, "***")   # 密钥打码
        self.assertFalse(manifest["workdir_missing"])

    def test_oversize_file_skipped_and_reported(self):
        big = self.wd / "huge.bin"
        old_file = eject._MAX_FILE
        old_total = eject._MAX_TOTAL
        eject._MAX_FILE = 50
        eject._MAX_TOTAL = 1 << 30
        try:
            big.write_bytes(b"x" * 100)
            t = _task(workdir=str(self.wd))
            with mock.patch("core.store.get_task", return_value=t), \
                 mock.patch("core.store.list_runs", return_value=[]):
                out = eject.export_task("t1")
        finally:
            eject._MAX_FILE = old_file
            eject._MAX_TOTAL = old_total
        self.assertTrue(out["ok"])
        found = False
        for s in out["skipped"]:
            if s["file"].endswith("huge.bin"):
                found = True
        self.assertTrue(found)
        self.assertEqual(out["files"], 2)      # 小文件照常进包

    def test_workdir_missing_flagged_not_fatal(self):
        t = _task(workdir=str(Path(self.tmp) / "no-such-dir"))
        with mock.patch("core.store.get_task", return_value=t), \
             mock.patch("core.store.list_runs", return_value=[]):
            out = eject.export_task("t1")
        self.assertTrue(out["ok"])
        self.assertTrue(out["workdir_missing"])

    def test_runs_filtered_by_task_and_slimmed(self):
        runs = [{"id": "r1", "task_id": "t1", "status": "done",
                 "created_at": "2026-11-01T08:00:00",
                 "ended_at": "2026-11-01T09:00:00",
                 "chapter_scores": {"节奏": 8.5},
                 "stream": "应该被裁掉的大字段"},
                {"id": "r2", "task_id": "other", "status": "done"}]
        t = _task(workdir=str(self.wd))
        with mock.patch("core.store.get_task", return_value=t), \
             mock.patch("core.store.list_runs", return_value=runs):
            out = eject.export_task("t1")
        self.assertEqual(out["runs"], 1)
        mpath = Path(out["dir"]) / "manifest.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        self.assertEqual(manifest["runs"][0]["id"], "r1")
        self.assertNotIn("stream", manifest["runs"][0])

    def test_missing_task_fails_soft(self):
        with mock.patch("core.store.get_task", return_value=None):
            out = eject.export_task("ghost")
        self.assertFalse(out["ok"])
        self.assertIn("任务不存在", out["error"])

    def test_zip_bytes_helper(self):
        t = _task(workdir=str(self.wd))
        with mock.patch("core.store.get_task", return_value=t), \
             mock.patch("core.store.list_runs", return_value=[]):
            blob = eject.export_zip_bytes("t1")
        self.assertIsNotNone(blob)
        name = blob[0]
        data = blob[1]
        self.assertTrue(name.endswith(".zip"))
        self.assertEqual(data[:2], b"PK")


if __name__ == "__main__":
    unittest.main()
