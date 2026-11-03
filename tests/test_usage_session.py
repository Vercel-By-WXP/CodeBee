# -*- coding: utf-8 -*-
"""「本会话用量明细」聚合回归（2026-11-03 用户截图：输入框上方一条
「共 X Token · 积分 · 模型 × N」，点开是一张按模型列的明细表）。

守测盯住四件事：
1. 只统计目标运行——同一台账里别的 run / 别的 task 的消耗绝不能串进来
   （串了就是「本会话」显示成「全站」，比不显示更糟）；
2. 同一模型的不同大小写归一行（台账里 GLM-5.3-Flash 与 glm-5.3-flash 并存
   是真实现象，分成两行会读成两个模型）；
3. 没有记录时返回 calls=0 而不是造一行 0——前端据 calls 判空并整条隐藏；
4. 未标价模型（有 token、无费用）单独计数，前端据此说「不计费用」，
   不许把「没标价」显示成「花了 ¥0」。

全部跑在临时目录，不碰真实台账。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import usage


def _rec(run_id="r1", task_id="t1", model="m1", total=1000, cost=0.5,
         ok=True, day="2026-11-03", role="draft"):
    return {"ts": day + " 10:00:00", "day": day, "run_id": run_id,
            "task_id": task_id, "model": model, "total": total, "input": total,
            "output": 0, "cached": 0, "cost_usd": cost, "ok": ok,
            "role": role, "tool": "codex", "agent": "ag", "task_type": "article",
            "duration_s": 2.0}


class SessionUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        p = mock.patch.object(usage.paths, "USAGE_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        with usage.LOCK:
            usage._FILE_CACHE.clear()
        self.addCleanup(self._clear_cache)

    def _clear_cache(self):
        with usage.LOCK:
            usage._FILE_CACHE.clear()

    def _write(self, recs):
        f = self.dir / "usage-202611.jsonl"
        f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                     encoding="utf-8")

    def test_totals_and_model_rows_for_one_run(self):
        self._write([
            _rec(model="m-a", total=1000, cost=0.2),
            _rec(model="m-a", total=500, cost=0.1),
            _rec(model="m-b", total=3000, cost=0.6),
            _rec(run_id="r2", model="m-a", total=9999, cost=9.0),   # 别的运行
        ])
        d = usage.session_usage(run_id="r1")
        self.assertEqual(d["calls"], 3)
        self.assertEqual(d["tokens"], 4500)
        self.assertAlmostEqual(d["cost"], 0.9, places=4)
        self.assertEqual([m["model"] for m in d["models"]], ["m-b", "m-a"])
        self.assertEqual(d["models"][0]["calls"], 1)
        self.assertEqual(d["models"][1]["tokens"], 1500)

    def test_task_scope_collects_the_whole_thread(self):
        self._write([
            _rec(run_id="r1", task_id="t1", total=100),
            _rec(run_id="r2", task_id="t1", total=200),
            _rec(run_id="r3", task_id="t9", total=900),
        ])
        d = usage.session_usage(task_id="t1")
        self.assertEqual(d["calls"], 2)
        self.assertEqual(d["tokens"], 300)

    def test_run_wins_over_task(self):
        self._write([
            _rec(run_id="r1", task_id="t1", total=100),
            _rec(run_id="r2", task_id="t1", total=200),
        ])
        self.assertEqual(usage.session_usage(run_id="r1", task_id="t1")["tokens"], 100)

    def test_model_case_variants_merge(self):
        self._write([
            _rec(model="GLM-5.3-Flash", total=100),
            _rec(model="glm-5.3-flash", total=200),
        ])
        d = usage.session_usage(run_id="r1")
        self.assertEqual(len(d["models"]), 1, "大小写变体必须归成一行")
        self.assertEqual(d["models"][0]["tokens"], 300)

    def test_empty_returns_zero_calls_not_a_fake_row(self):
        self._write([_rec(run_id="other")])
        d = usage.session_usage(run_id="nope")
        self.assertEqual(d["calls"], 0)
        self.assertEqual(d["models"], [])
        self.assertEqual(d["tokens"], 0)

    def test_no_scope_is_empty(self):
        self._write([_rec()])
        self.assertEqual(usage.session_usage()["calls"], 0)

    def test_unpriced_models_are_counted(self):
        self._write([
            _rec(model="priced", total=100, cost=0.01),
            _rec(model="free-unknown", total=100, cost=0.0),
        ])
        d = usage.session_usage(run_id="r1")
        self.assertEqual(d["unpriced"], 1)

    def test_failed_calls_are_reported(self):
        self._write([_rec(ok=True), _rec(ok=False)])
        d = usage.session_usage(run_id="r1")
        self.assertEqual(d["ok"], 1)
        self.assertEqual(d["failed"], 1)

    def test_timestamps_bracket_the_session(self):
        self._write([
            _rec(day="2026-11-01"),
            _rec(day="2026-11-03"),
        ])
        d = usage.session_usage(run_id="r1")
        self.assertTrue(d["first_ts"].startswith("2026-11-01"))
        self.assertTrue(d["last_ts"].startswith("2026-11-03"))


if __name__ == "__main__":
    unittest.main()
