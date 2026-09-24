# -*- coding: utf-8 -*-
"""压缩省量入台账（A 专项：省了多少不再是黑箱）单测：
摘要调用真实消耗此前隐形 + 净省量无账；现在 compact_region 成功路径
入账 source=compaction（input=读入/ output=写出/ saved=净省），
summary() totals 增 compaction_saved 第七维。

跑法：python -m unittest discover -s tests -p "test_compact_ledger.py" -v
"""
from __future__ import annotations

from base import BaseTest


def _llm(summary="摘要"):
    def caller(messages):
        return summary
    return caller


class CompactLedgerTests(BaseTest):

    def _session(self):
        from app.core.session_log import Session
        s = Session("ledger-run")
        s.append("system_message", {"content": "sys"})
        for i in range(6):
            s.append("user_message", {"content": "u%d " % i + "字" * 3000})
            s.append("assistant_message", {"content": "a%d " % i + "字" * 3000})
        return s

    def _compact(self):
        from app.core import compaction
        s = self._session()
        start, end = compaction.select_range(s)
        self.assertTrue(start, "夹具应产生可压缩区域")
        ok = compaction.compact_region(s, start, end, _llm(), reason="test")
        self.assertTrue(ok)
        return s

    def test_compact_records_ledger_with_saved(self):
        """压缩成功 → 台账新增 compaction 条目：消耗与净省并列。"""
        self._compact()
        from app.core import usage
        recs = [r for r in usage._iter_records(0)
                if r.get("source") == "compaction"
                and r.get("run_id") == "ledger-run"]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.get("tool"), "compaction")
        self.assertGreater(r.get("input", 0), 0)
        self.assertGreaterEqual(r.get("output", 0), 0)
        self.assertGreater(r.get("saved", 0), 0)
        self.assertEqual(r.get("saved"), r["input"] - r["output"])

    def test_summary_totals_has_compaction_saved(self):
        """summary 第七维：saved 合计进 totals.compaction_saved。"""
        self._compact()
        from app.core import usage
        totals = usage.summary(days=0)["totals"]
        self.assertGreater(totals.get("compaction_saved", 0), 0)

    def test_record_saved_zero_not_persisted(self):
        """saved=0 不落字段——普通记录形态不膨胀（老读取方零影响）。"""
        from app.core import usage
        usage.record(source="probe-zero", run_id="ledger-run",
                     usage={"input": 10, "output": 5, "saved": 0})
        recs = [r for r in usage._iter_records(0)
                if r.get("source") == "probe-zero"]
        self.assertEqual(len(recs), 1)
        self.assertNotIn("saved", recs[0])


class ByDaySavedTests(BaseTest):
    def runTest(self):
        """by_day 逐日带 saved 维（KPI 卡 spark 数据源），无压缩日为 0。"""
        from app.core import compaction, usage
        from app.core.session_log import Session
        s = Session("byday-run")
        s.append("system_message", {"content": "sys"})
        for i in range(6):
            s.append("user_message", {"content": "u%d " % i + "字" * 3000})
            s.append("assistant_message", {"content": "a%d " % i + "字" * 3000})
        start, end = compaction.select_range(s)
        self.assertTrue(start)
        self.assertTrue(compaction.compact_region(s, start, end,
                                                  lambda m: "摘要", reason="test"))
        bd = usage.summary(days=1)["by_day"]
        self.assertTrue(bd)
        today = max(bd, key=lambda x: x["saved"])
        self.assertGreater(today["saved"], 0)
        # 无压缩日（夹具只压了今天，days=3 首日应为 0）
        bd3 = usage.summary(days=3)["by_day"]
        self.assertEqual(bd3[0].get("saved", 0), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
