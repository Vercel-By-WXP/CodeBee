# -*- coding: utf-8 -*-
"""发布成功率（publish.stats）单测。

口径比算术重要：这个数一旦被算松，产品级指标就失去意义。所以测试重点在
**哪些记录该进、哪些绝不能进**——草稿不算上线、连接探测不算发布、
没有样本时是 None 而不是 0%。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from core import paths
from core.publish import ledger, stats


class _LedgerCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self._saved = paths.PUBLISH_DIR
        paths.PUBLISH_DIR = self.root
        self.today = time.strftime("%Y-%m-%d")

    def tearDown(self):
        paths.PUBLISH_DIR = self._saved
        self._dir.cleanup()

    def write(self, records):
        fp = self.root / ("publish-%s.jsonl" % self.today[:7].replace("-", ""))
        fp.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for rec in records:
            row = {"ts": self.today + " 10:00:00", "day": self.today,
                   "platform": "fanqie", "action": "upload_chapter",
                   "task_id": "t1", "chapter_no": 0, "ok": True, "error": ""}
            row.update(rec)
            lines.append(json.dumps(row, ensure_ascii=False))
        fp.write_text("\n".join(lines) + "\n", encoding="utf-8")


class EmptyTest(_LedgerCase):
    def test_no_records_means_rate_is_none_not_zero(self):
        got = stats.summary(7)
        self.assertEqual(got["live"]["attempts"], 0)
        self.assertIsNone(got["live"]["rate"])        # 0% 会被读成「全线失败」
        self.assertEqual(got["failures"], [])
        self.assertEqual(got["goal"], 0.95)


class CountingTest(_LedgerCase):
    def test_live_actions_only_and_rate(self):
        self.write([
            {"action": "upload_chapter", "ok": True},
            {"action": "publish_short_story", "ok": True},
            {"action": "create_book", "ok": True},
            {"action": "upload_chapter_scheduled", "ok": False, "error": "登录已失效"},
        ])
        got = stats.summary(7)
        self.assertEqual(got["live"]["attempts"], 4)
        self.assertEqual(got["live"]["ok"], 3)
        self.assertEqual(got["live"]["failed"], 1)
        self.assertEqual(got["live"]["rate"], 0.75)
        self.assertEqual(len(got["failures"]), 1)
        self.assertEqual(got["failures"][0]["error"], "登录已失效")

    def test_drafts_never_count_as_live(self):
        self.write([
            {"action": "upload_chapter", "ok": True},
            {"action": "upload_chapter_draft", "ok": False, "error": "草稿框没找到"},
            {"action": "upload_short_story_draft", "ok": False, "error": "草稿框没找到"},
        ])
        got = stats.summary(7)
        self.assertEqual(got["live"]["attempts"], 1)
        self.assertEqual(got["live"]["rate"], 1.0)     # 草稿失败不该拉低上线成功率
        self.assertEqual(got["draft"]["attempts"], 2)
        self.assertEqual(got["draft"]["rate"], 0.0)

    def test_connection_and_probe_actions_are_ignored(self):
        self.write([
            {"action": "connect", "ok": False, "error": "扫码超时"},
            {"action": "probe", "ok": False},
            {"action": "auto_fire", "ok": True},
            {"action": "upload_chapter", "ok": True},
        ])
        got = stats.summary(7)
        self.assertEqual(got["live"]["attempts"], 1)
        self.assertEqual(got["draft"]["attempts"], 0)
        self.assertEqual(got["failures"], [])

    def test_per_platform_split(self):
        self.write([
            {"platform": "fanqie", "ok": True},
            {"platform": "fanqie", "ok": False, "error": "x"},
            {"platform": "qimao", "ok": True},
        ])
        got = stats.summary(7)
        self.assertEqual(got["platforms"]["fanqie"]["live"]["attempts"], 2)
        self.assertEqual(got["platforms"]["fanqie"]["live"]["rate"], 0.5)
        self.assertEqual(got["platforms"]["qimao"]["live"]["rate"], 1.0)

    def test_task_filter(self):
        self.write([
            {"task_id": "t1", "ok": True},
            {"task_id": "t2", "ok": False, "error": "不是这本书"},
        ])
        got = stats.summary(7, task_id="t1")
        self.assertEqual(got["live"]["attempts"], 1)
        self.assertEqual(got["live"]["rate"], 1.0)

    def test_failures_are_newest_first_and_capped(self):
        self.write([
            {"action": "upload_chapter", "ok": False, "error": "第%d次" % i,
             "ts": self.today + " 10:0%d:00" % i}
            for i in range(8)
        ])
        got = stats.summary(7)
        self.assertEqual(len(got["failures"]), 5)
        self.assertEqual(got["failures"][0]["error"], "第7次")

    def test_window_clamped(self):
        self.assertEqual(stats.summary(0)["days"], 1)
        self.assertEqual(stats.summary(9999)["days"], 365)
        self.assertEqual(stats.summary("abc")["days"], 7)

    def test_old_records_fall_outside_window(self):
        self.write([{"action": "upload_chapter", "ok": True,
                     "day": "2000-01-01", "ts": "2000-01-01 10:00:00"}])
        self.assertEqual(stats.summary(7)["live"]["attempts"], 0)


class RealLedgerPathTest(unittest.TestCase):
    """schema 对不上的风险在 record()：stats 读的是它写出来的行。"""

    def test_record_then_summary_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = paths.PUBLISH_DIR
            paths.PUBLISH_DIR = Path(tmp)
            try:
                ledger.record("fanqie", "upload_chapter", task_id="r1",
                              chapter_no=3, title="书", ok=True)
                ledger.record("fanqie", "upload_chapter", task_id="r1",
                              chapter_no=4, title="书", ok=False, error="弹窗没关")
                got = stats.summary(7, task_id="r1")
                self.assertEqual(got["live"]["attempts"], 2)
                self.assertEqual(got["live"]["rate"], 0.5)
                self.assertEqual(got["failures"][0]["chapter_no"], 4)
            finally:
                paths.PUBLISH_DIR = saved


if __name__ == "__main__":
    unittest.main()
