# -*- coding: utf-8 -*-
"""每日垃圾清理（core/cleanup.py）：分类扫描、超期清理、保留用户数据、调度节流。"""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from tests.base import BaseTest

OLD_TS = "2020-01-01 00:00:00"


def _age(path, ts=OLD_TS):
    old = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    os.utime(str(path), (old, old))


class CleanupTest(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import cleanup
        self.cleanup = cleanup
        self.data = Path(self.data_dir)
        self.data.mkdir(parents=True, exist_ok=True)

    def _old_run(self, rid="r-20200101-000000-0001"):
        rd = self.data / "runs" / rid
        (rd / "steps").mkdir(parents=True)
        (rd / "run.json").write_text(
            json.dumps({"id": rid, "status": "done", "ended_at": OLD_TS}),
            encoding="utf-8")
        (rd / "steps" / "01-draft.log").write_text("x" * 4096, encoding="utf-8")
        (rd / "report.md").write_text("报告", encoding="utf-8")
        _age(rd / "run.json")
        return rd

    def _plan_keys(self, **kw):
        return {i["key"] for i in self.cleanup.plan(**kw)["items"]}

    # ------------------------------------------------------------ 扫描

    def test_plan_finds_old_run_logs_only(self):
        rd = self._old_run()
        # 新运行不动
        nr = self.data / "runs" / "r-29990101-000000-0002"
        (nr / "steps").mkdir(parents=True)
        (nr / "run.json").write_text(
            json.dumps({"id": "x", "status": "done",
                        "ended_at": time.strftime("%Y-%m-%d %H:%M:%S")}),
            encoding="utf-8")
        keys = self._plan_keys()
        self.assertIn("runs_old_logs", keys)
        item = next(i for i in self.cleanup.plan()["items"]
                    if i["key"] == "runs_old_logs")
        self.assertEqual([f["path"] for f in item["files"]], [str(rd / "steps")])

    def test_plan_bak_shots_pending_exports(self):
        bak = self.data / "models.json.bak-20200101"
        bak.write_text("{}", encoding="utf-8")
        _age(bak)
        new_bak = self.data / "skills.json.bak"
        new_bak.write_text("{}", encoding="utf-8")     # 新鲜的保留
        shots = self.data / "publish" / "shots"
        shots.mkdir(parents=True)
        shot = shots / "s1.png"
        shot.write_bytes(b"png")
        _age(shot)
        pend = self.data / "pending" / "abc"
        pend.mkdir(parents=True)
        pf = self.data / "pending" / "abc.json"
        pf.write_text("{}", encoding="utf-8")
        _age(pend)
        _age(pf)
        exp = self.data / "exports"
        exp.mkdir()
        z = exp / "codebee-backup-old.zip"
        z.write_bytes(b"zip")
        _age(z)
        keys = self._plan_keys()
        for k in ("bak_files", "publish_shots", "pending_stale", "backups_old"):
            self.assertIn(k, keys)
        # 新鲜 bak 不在清理清单
        item = next(i for i in self.cleanup.plan()["items"] if i["key"] == "bak_files")
        self.assertNotIn(str(new_bak), [f["path"] for f in item["files"]])

    def test_legacy_profiles_only_when_migrated(self):
        legacy = self.data / "publish" / "profiles" / "qimao"
        legacy.mkdir(parents=True)
        home = self.tmp / "home"
        # 家目录还没有同款平台目录：视为未迁移，不清（manager 启动时要搬）
        self.assertNotIn("legacy_profiles", self._plan_keys(home=home))
        # 家目录已有（迁移完成）：旧位置属废弃残留
        (home / ".codebee" / "publish_profiles" / "qimao").mkdir(parents=True)
        self.assertIn("legacy_profiles", self._plan_keys(home=home))
        # 手动类目（浏览器缓存本体）只在显式请求时出现
        self.assertIn("publish_profiles", self._plan_keys(home=home,
                                                          include_profiles=True))
        self.assertNotIn("publish_profiles", self._plan_keys(home=home))

    def test_log_truncate_plan(self):
        big = self.data / "server.log"
        big.write_bytes(b"x" * (6 * 1024 * 1024))
        small = self.data / "service-stdout.log"
        small.write_bytes(b"x" * 1024)
        keys = self._plan_keys()
        self.assertIn("logs_truncate", keys)
        item = next(i for i in self.cleanup.plan()["items"]
                    if i["key"] == "logs_truncate")
        paths = [f["path"] for f in item["files"]]
        self.assertIn(str(big), paths)
        self.assertNotIn(str(small), paths)

    # ------------------------------------------------------------ 执行

    def test_run_cleanup_keeps_records_and_reports(self):
        rd = self._old_run()
        bak = self.data / "models.json.bak-old"
        bak.write_text("{}", encoding="utf-8")
        _age(bak)
        r = self.cleanup.run_cleanup()
        self.assertGreater(r["freed"], 0)
        self.assertFalse((rd / "steps").exists())      # 过程日志没了
        self.assertTrue((rd / "run.json").is_file())   # 运行记录保留
        self.assertTrue((rd / "report.md").is_file())  # 结果报告保留
        self.assertFalse(bak.exists())
        # 状态落盘：设置页能展示上次清理
        st = json.loads((self.data / "cleanup.json").read_text(encoding="utf-8"))
        self.assertEqual(st["last_run"], r["ts"])
        self.assertGreater(st["last_freed"], 0)

    def test_run_cleanup_truncates_big_log(self):
        big = self.data / "server.log"
        big.write_bytes(b"x" * (6 * 1024 * 1024))
        self.cleanup.run_cleanup()
        self.assertTrue(big.is_file())
        self.assertLessEqual(big.stat().st_size, 1024 * 1024 + 64)

    def test_fire_due_daily_throttle(self):
        self._old_run()
        from app.core import settings
        # 停用：零开销返回
        settings.save({"cleanup_enabled": False})
        self.assertIsNone(self.cleanup.fire_due())
        self.assertTrue((self.data / "runs" / "r-20200101-000000-0001" / "steps").exists())
        # 启用：第一次真跑并释放
        settings.save({"cleanup_enabled": True})
        r = self.cleanup.fire_due()
        self.assertIsNotNone(r)
        self.assertGreater(r["freed"], 0)
        # 当天第二次：节流跳过
        self.assertIsNone(self.cleanup.fire_due())

    def test_settings_roundtrip_and_clamp(self):
        from app.core import settings
        view, err = settings.save({"cleanup_retention_days": 0})
        self.assertTrue(err)
        view, err = settings.save({"cleanup_enabled": False,
                                   "cleanup_retention_days": 30})
        self.assertIsNone(err)
        self.assertFalse(view["cleanup_enabled"])
        self.assertEqual(view["cleanup_retention_days"], 30)


if __name__ == "__main__":
    unittest.main()
