# -*- coding: utf-8 -*-
"""usage.estimate：同类任务开跑前成本预估（借鉴 omnigent pre-run estimate）。

跑法：python -m unittest tests.test_estimate -v
"""
from __future__ import annotations

from base import BaseTest


class EstimateTests(BaseTest):
    def _rec(self, run_id, step, total, cost=0.0, ok=True, ttype="serial"):
        from app.core import usage
        usage.record(source="pipeline", run_id=run_id, task_id="t-" + run_id,
                     task_type=ttype, role="implement", step=step,
                     model="m", ok=ok, cost_usd=cost,
                     usage={"total": total})

    def test_empty(self):
        from app.core import usage
        self.assertEqual(usage.estimate(task_type="serial")["samples"], 0)

    def test_group_by_run(self):
        from app.core import usage
        self._rec("r1", 1, 1000, cost=0.01)
        self._rec("r1", 2, 2000, cost=0.02)      # 同一 run 的两条步骤记录加总为一个样本
        self._rec("r2", 1, 10000, cost=0.10)
        d = usage.estimate(task_type="serial")
        self.assertEqual(d["samples"], 2)
        self.assertEqual(d["median_tokens"], 6500)   # (3000+10000)/2
        self.assertEqual(d["p90_tokens"], 10000)
        self.assertAlmostEqual(d["median_cost_usd"], 0.065, places=3)
        self.assertEqual(d["avg_tokens"], 6500)
        self.assertEqual(d["ok_samples"], 2)

    def test_type_filter_and_failed_only(self):
        from app.core import usage
        self._rec("r1", 1, 500, ttype="code")
        self.assertEqual(usage.estimate(task_type="serial")["samples"], 0)
        self._rec("r2", 1, 900, ok=False)
        d = usage.estimate(task_type="serial")
        self.assertEqual(d["samples"], 1)        # 只有失败样本也照给量级参考
        self.assertEqual(d["ok_samples"], 0)

    def test_all_types_when_blank(self):
        from app.core import usage
        self._rec("r1", 1, 100, ttype="code")
        self._rec("r2", 1, 200, ttype="serial")
        self.assertEqual(usage.estimate()["samples"], 2)


if __name__ == "__main__":
    unittest.main()
