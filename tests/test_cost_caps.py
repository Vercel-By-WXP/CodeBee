# -*- coding: utf-8 -*-
"""花费硬顶（budget.daily_cost_usd / monthly_cost_usd，美元口径）单测。

覆盖：schema 字段注册与钳制、台账快照 cost_snapshot（今日/当月口径）、
pipeline 花费闸 _cost_gate_block 的命中/放行/统计故障放行。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import BaseTest


class CostCapsBase(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import settings_schema as ss, usage
        ss.init(data_dir=str(self.data_dir))
        usage._FILE_CACHE.clear()
        self._ss, self._usage = ss, usage

    def tearDown(self):
        with self._ss._LOCK:
            self._ss._NAMESPACES.clear()
            self._ss._VALUES.clear()
            self._ss._REVISIONS.clear()
        super().tearDown()


class TestSchemaFields(CostCapsBase):

    def test_budget_ns_has_cost_caps(self):
        from app.core import settings_schema as ss
        ss.register_default_namespaces()
        d = ss.describe("budget")
        vals = d["values"]
        self.assertIn("daily_cost_usd", vals)
        self.assertIn("monthly_cost_usd", vals)
        self.assertEqual(vals["daily_cost_usd"], 0.0)
        self.assertEqual(vals["monthly_cost_usd"], 0.0)

    def test_mutate_coerces_and_clamps(self):
        from app.core import settings_schema as ss
        ss.register_default_namespaces()
        ss.mutate("budget", [{"op": "set", "path": "daily_cost_usd", "value": 5.5}])
        self.assertEqual(ss.get("budget", "daily_cost_usd"), 5.5)
        # 负值钳到 0；超过上限钳到 clamp.max
        ss.mutate("budget", [{"op": "set", "path": "monthly_cost_usd", "value": -3}])
        self.assertEqual(ss.get("budget", "monthly_cost_usd"), 0.0)
        ss.mutate("budget", [{"op": "set", "path": "monthly_cost_usd", "value": 9_999_999}])
        self.assertEqual(ss.get("budget", "monthly_cost_usd"), 1_000_000)


class TestCostSnapshot(CostCapsBase):

    def _seed_today(self):
        from app.core import usage
        usage.record(source="pipeline", run_id="r1", task_id="t1", task_type="code",
                     role="implement", step=1, cost_usd=0.3,
                     usage={"input": 10, "output": 5})
        usage.record(source="pipeline", run_id="r1", task_id="t1", task_type="code",
                     role="review", step=2, cost_usd=0.2,
                     usage={"input": 10, "output": 5})

    def test_today_and_month_sums(self):
        import datetime
        from app.core import paths, usage
        self._seed_today()
        today = datetime.date.today().isoformat()
        month = today[:7]
        first_day = month + "-01"
        # 手写一条本月 1 号的历史账（cost 2.0）与一条上月的账（不该计入）
        prev_month_day = "2000-01-15"
        with open(paths.USAGE_DIR / sorted(
                p.name for p in paths.USAGE_DIR.glob("usage-*.jsonl"))[-1],
                "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": first_day + " 00:00:00", "day": first_day,
                                "cost_usd": 2.0, "task_type": "novel"}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"ts": prev_month_day + " 00:00:00", "day": prev_month_day,
                                "cost_usd": 99.0, "task_type": "novel"}, ensure_ascii=False) + "\n")
        usage._FILE_CACHE.clear()
        t, m = usage.cost_snapshot()
        self.assertAlmostEqual(t, 0.5, places=3)
        # 今日 0.5 + 本月 1 号 2.0（今天若是 1 号，两笔今日账同时计入月度）
        self.assertAlmostEqual(m, 2.5, places=3)

    def test_snapshot_never_raises(self):
        from app.core import usage
        usage._FILE_CACHE.clear()
        t, m = usage.cost_snapshot()
        self.assertEqual((t, m), (0.0, 0.0))


class TestCostGate(CostCapsBase):

    def _gate(self, caps, snap, snap_raises=False):
        from app.core import pipeline
        # 注意：side_effect=元组会被 MagicMock 当迭代器逐元素吐出，
        # 必须用 return_value 才能返回 (today, month) 快照元组
        snap_kw = {"side_effect": RuntimeError("boom")} if snap_raises else \
                  {"return_value": snap}
        with mock.patch.object(pipeline, "_budget_cost_caps", return_value=caps), \
             mock.patch("app.core.usage.cost_snapshot", **snap_kw):
            return pipeline._cost_gate_block()

    def test_gate_off_when_no_caps(self):
        self.assertIsNone(self._gate((0.0, 0.0), (99.0, 999.0)))

    def test_daily_hit(self):
        msg = self._gate((5.0, 0.0), (5.0, 8.0))
        self.assertIsNotNone(msg)
        self.assertIn("每日", msg)
        self.assertIn("5.00", msg)

    def test_daily_under_but_monthly_capped_ignored(self):
        # 日上限未到、月上限为 0 → 放行（月不计费）
        self.assertIsNone(self._gate((5.0, 0.0), (4.99, 99.0)))

    def test_monthly_hit(self):
        msg = self._gate((0.0, 5.0), (1.0, 5.0))
        self.assertIsNotNone(msg)
        self.assertIn("每月", msg)

    def test_daily_priority_over_monthly(self):
        msg = self._gate((5.0, 5.0), (6.0, 6.0))
        self.assertIn("每日", msg)

    def test_stats_failure_releases_gate(self):
        # 统计层故障不能把业务锁死：快照抛错 = 放行
        self.assertIsNone(self._gate((5.0, 5.0), None, snap_raises=True))


if __name__ == "__main__":
    unittest.main()
