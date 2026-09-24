# -*- coding: utf-8 -*-
"""预估事后对账回归：终态单次入账、不回读当前台账、薄样本不得冒充实测。

背景（2026-09-24 定案）：`usage.estimate` 是**类型级**预估，而实测同一类型内部的
成本差 15~114 倍（serial_novel 中位 12.1 万 / P90 185 万 tok）。用它做进队否决会
误杀正常长任务，所以这一层只做「开跑前告警 + 终态事后对账」，阻断留给
`budget.max_tokens_per_run` 的真实用量闸。本文件钉住三件容易悄悄退化的事：
对账只在非终态→终态那一次翻转入账；预估值取 run 快照而非重算（重算会把本次
消耗算进预估，比值永远偏低）；`days=0`（UI「全部」）不得把基线坍缩成「只扫今天」。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from base import BaseTest


def _recs(path):
    if not Path(path).exists():
        return []
    return [json.loads(x) for x in
            Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


class TestAuditRun(BaseTest):
    def _ledger(self, run_id, tokens, duration_s):
        from app.core import paths, usage
        usage.record(source="pipeline", run_id=run_id, step=1, task_id="t-1",
                     task_type="novel", role="draft", agent="a1", ok=True,
                     duration_s=duration_s, cost_usd=0.5,
                     usage={"input": tokens, "output": 0, "total": tokens})
        return paths.USAGE_DIR

    def _audit_file(self):
        from app.core import paths
        return paths.USAGE_DIR / ("estimate-audit-%s.jsonl"
                                  % time.strftime("%Y-%m-%d")[:7].replace("-", ""))

    def setUp(self):
        super().setUp()
        from app.core import usage
        usage._AUDITED.clear()

    def test_writes_one_row_with_ratio(self):
        from app.core import usage
        self._ledger("r-ok", 1200, 90.0)
        rec = usage.audit_run({"id": "r-ok", "status": "done", "task_id": "t-1",
                               "task_type": "novel", "estimated_duration_s": 60.0,
                               "estimated_p90_s": 80.0, "estimate_source": "history",
                               "estimate_samples": 24})
        rows = _recs(self._audit_file())
        self.assertEqual(1, len(rows))
        self.assertEqual(rec["run_id"], "r-ok")
        self.assertAlmostEqual(1.5, rows[0]["ratio"], places=2)
        self.assertEqual(1200, rows[0]["actual_tokens"])
        self.assertTrue(rows[0]["over_p90"])
        self.assertEqual("stored", rows[0]["est_basis"])
        self.assertEqual(24, rows[0]["est_samples"])

    def test_second_terminal_write_does_not_double_count(self):
        from app.core import usage
        self._ledger("r-twice", 500, 30.0)
        run = {"id": "r-twice", "status": "failed", "task_type": "novel",
               "estimated_duration_s": 60.0}
        self.assertIsNotNone(usage.audit_run(run))
        self.assertIsNone(usage.audit_run(run))
        self.assertEqual(1, len(_recs(self._audit_file())))

    def test_snapshot_is_not_recomputed_from_current_ledger(self):
        """快照缺失才允许重算，且必须标 est_basis=recomputed。"""
        from app.core import usage
        self._ledger("r-nosnap", 300, 12.0)
        rec = usage.audit_run({"id": "r-nosnap", "status": "done",
                               "task_type": "novel"})
        self.assertIn(rec["est_basis"], ("recomputed", "none"))
        self.assertNotEqual("stored", rec["est_basis"])

    def test_non_terminal_status_is_ignored(self):
        from app.core import usage
        self.assertIsNone(usage.audit_run({"id": "r-run", "status": "running",
                                           "estimated_duration_s": 10.0}))
        self.assertEqual([], _recs(self._audit_file()))

    def test_auditor_never_raises(self):
        """对账器不能把异常抛回收尾路径——统计永远不能拖垮业务。"""
        from app.core import usage
        for bad in (None, {}, {"id": "r-x"}, {"id": "r-y", "status": "done",
                                              "estimated_duration_s": "not-a-number"}):
            with self.subTest(bad=bad):
                try:
                    usage.audit_run(bad)
                except Exception as exc:      # noqa: B036 - 断言的就是"不抛"
                    self.fail("audit_run 抛异常：%r" % exc)

    def test_cancel_and_timeout_also_audit(self):
        from app.core import store, usage
        store.set_run_auditor(usage.audit_run)
        self.addCleanup(store.set_run_auditor, None)
        run = store.create_run("orchestration", "t", task_id=None)
        store.update_run(run["id"], status="running")
        for st in ("cancelled", "timeout"):
            store.update_run(run["id"], expected_status="running", status=st)
            store.update_run(run["id"], status="failed")   # 迟到写手不得二次入账
        rows = _recs(self._audit_file())
        self.assertEqual(1, len(rows))
        self.assertEqual("cancelled", rows[0]["status"])


class TestEstimateBasis(BaseTest):
    def test_zero_days_scans_full_history_not_today(self):
        """UI「全部」档 days=0：不得坍缩成只扫今天，否则基线跟着窗口一起塌。"""
        from app.core import paths, usage
        day = time.strftime("%Y-%m-%d")
        paths.USAGE_DIR.mkdir(parents=True, exist_ok=True)
        f = paths.USAGE_DIR / ("usage-%s.jsonl" % day[:7].replace("-", ""))
        f.write_text(json.dumps({
            "ts": day + " 09:00:00", "day": day, "source": "pipeline",
            "run_id": "r-old", "step": 1, "task_id": "t", "task_type": "novel",
            "role": "draft", "agent": "a1", "tool": "codex", "model": "m",
            "provider": "", "ok": True, "duration_s": 400.0, "cost_usd": 0.1,
            "input": 4000, "output": 1000, "cached": 0, "reasoning": 0,
            "total": 5000}) + "\n", encoding="utf-8")
        est = usage.estimate(task_type="novel", days=0)
        self.assertGreater(est["samples"], 0)
        self.assertEqual("history", est["duration_source"])
        self.assertGreater(est["estimated_duration_s"], 100)

    def test_thin_sample_source_is_labeled(self):
        from app.core import usage
        est = usage.estimate(task_type="no-such-type", days=90)
        self.assertEqual(0, est["samples"])
        self.assertEqual("baseline", est["duration_source"])


if __name__ == "__main__":
    unittest.main()
