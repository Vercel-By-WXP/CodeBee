# -*- coding: utf-8 -*-
"""用量台账「清空」回归（2026-11-03 用户：「是不是应该支持清空，方便重新统计」）。

语义：
1. 清空 = 把 usage-*.jsonl 改名归档（.cleared-<时间戳>），不直接删；
2. 清空后 summary() 全部归零，且 glob 不再命中归档文件（不会被重复统计）；
3. 别的台账（routing-quality / estimate-audit）不受影响；
4. 目录不存在时安全返回 0；
5. 有归档发生才 bump 版本号（SSE 推送据此刷新）。

全部跑在临时目录，不碰真实数据。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import usage


def _rec(day="2026-11-03", total=1000, cost=0.5, ok=True):
    return {"ts": day + " 10:00:00", "day": day, "total": total,
            "cost_usd": cost, "ok": ok, "model": "m1", "tool": "codex",
            "agent": "ag", "role": "plan", "task_type": "article"}


class ClearLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        p = mock.patch.object(usage.paths, "USAGE_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        with usage.LOCK:
            usage._FILE_CACHE.clear()

    def _write(self, name, recs):
        f = self.dir / name
        f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                     encoding="utf-8")
        return f

    def test_clear_archives_files_and_zeroes_summary(self):
        self._write("usage-202611.jsonl", [_rec(), _rec(total=500)])
        self.assertGreater(usage.summary(days=0)["totals"]["tokens"], 0)

        moved = usage.clear_ledger()

        self.assertEqual(moved, 1)
        self.assertEqual(list(self.dir.glob("usage-*.jsonl")), [],
                         "归档后原名文件必须消失，否则会被重复统计")
        archived = list(self.dir.glob("usage-*.jsonl.cleared-*"))
        self.assertEqual(len(archived), 1, "必须保留一份归档，不能直删")
        self.assertIn("total", archived[0].read_text(encoding="utf-8"))

        summary = usage.summary(days=0)
        self.assertEqual(summary["totals"]["tokens"], 0)
        self.assertEqual(summary["totals"]["calls"], 0)
        self.assertEqual(summary["totals"]["cost_usd"], 0)
        self.assertEqual(summary["recent"], [])

    def test_clear_bumps_usage_version(self):
        self._write("usage-202611.jsonl", [_rec()])
        before = usage.usage_version()
        usage.clear_ledger()
        self.assertGreater(usage.usage_version(), before)

    def test_clear_leaves_other_ledgers(self):
        self._write("usage-202611.jsonl", [_rec()])
        self._write("routing-quality-202611.jsonl", [{"day": "2026-11-03", "quality_ok": True}])
        self._write("estimate-audit-202611.jsonl", [{"day": "2026-11-03"}])

        usage.clear_ledger()

        self.assertTrue((self.dir / "routing-quality-202611.jsonl").exists())
        self.assertTrue((self.dir / "estimate-audit-202611.jsonl").exists())

    def test_clear_on_missing_dir_is_safe(self):
        mock_dir = Path(self.tmp.name) / "nope"
        with mock.patch.object(usage.paths, "USAGE_DIR", mock_dir):
            self.assertEqual(usage.clear_ledger(), 0)

    def test_records_after_clear_start_fresh(self):
        self._write("usage-202611.jsonl", [_rec(total=9999)])
        usage.clear_ledger()
        # 清空后再记一笔：新文件重新累计，不会把归档里的旧数据读回来
        usage.record(source="test", task_type="article", role="plan",
                     usage={"total": 7})
        self.assertEqual(usage.summary(days=0)["totals"]["tokens"], 7)


class ClearUsageUiWiringTests(unittest.TestCase):
    """「清空」入口必须在用量页工具条上，且走应用内确认层（禁原生 confirm）。"""

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "app" / "ui"

    def _read(self, name):
        return (self.root / name).read_text(encoding="utf-8")

    def test_button_and_handler_are_wired(self):
        html = self._read("index.html")
        self.assertIn('id="btn-usage-clear"', html, "用量页缺少「清空」按钮")
        self.assertIn('data-i18n="清空"', html)
        js = self._read("app.js")
        self.assertIn('$("btn-usage-clear")', js, "按钮没有绑处理函数")
        self.assertIn('/api/usage/clear', js)
        self.assertIn("uiConfirm(", js, "危险动作必须走应用内确认层")
        # 二次确认文案要讲清「归零 + 归档可找回」，避免误以为是删数据
        self.assertIn("归档", js)

    def test_i18n_covers_new_strings(self):
        i18n = (self.root / "i18n.js").read_text(encoding="utf-8")
        for key in ("\"清空\":", "\"清空用量台账\":", "\"清空失败：\":"):
            self.assertIn(key, i18n, "缺少英文词条 %s" % key)


if __name__ == "__main__":
    unittest.main()