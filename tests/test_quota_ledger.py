# -*- coding: utf-8 -*-
"""per-key 四维额度计量 + 派单前节流（quota_ledger）回归。

核心语义（2026-11-01，借鉴 Freellmapi）：
1. 四维窗口口径：RPM/TPM=前 60 秒，RPD/TPD=当日；数据源=用量台账；
2. 只有「已配置且已用尽」才节流；token 维支持 est_tokens 预留判定；
3. filter_candidates：被节流者摘除保持原序；全部被节流→原样返回（fail-open）；
4. rank_model_entries 接线：排序前先过节流；
5. 上限配置落盘（save/load）+ 供应商条目 quota_limits 预设兜底。

台账与配置全走 mock/临时目录，不碰真实数据。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import dispatch, quota_ledger


def _rec(pid, ts, day, tokens=100):
    return {"provider": pid, "ts": ts, "day": day, "total": tokens, "ok": True}


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(quota_ledger, "_limits_file",
                              return_value=Path(self.tmp.name) / "quota-limits.json")
        p.start()
        self.addCleanup(p.stop)
        quota_ledger.invalidate_cache()

    def tearDown(self):
        quota_ledger.invalidate_cache()

    def test_load_empty_on_missing(self):
        self.assertEqual(quota_ledger.load_limits(), {})

    def test_save_and_load_roundtrip(self):
        quota_ledger.save_limits("prov-a", {"rpd": 10, "tpd": 5000, "rpm": -3})
        self.assertEqual(quota_ledger.load_limits().get("prov-a"),
                         {"rpm": 0.0, "rpd": 10.0, "tpm": 0.0, "tpd": 5000.0})
        # 清零配置 = 删除条目
        quota_ledger.save_limits("prov-a", {})
        self.assertNotIn("prov-a", quota_ledger.load_limits())

    def test_save_requires_pid(self):
        with self.assertRaises(ValueError):
            quota_ledger.save_limits("", {"rpd": 1})

    def test_preset_from_provider_entry(self):
        with mock.patch.object(quota_ledger, "load_limits", return_value={}):
            lim = quota_ledger.effective_limits(
                {"id": "p1", "quota_limits": {"rpd": 20, "tpd": 0, "rpm": "x"}})
        self.assertEqual(lim, {"rpd": 20.0})

    def test_explicit_config_beats_preset(self):
        quota_ledger.save_limits("p1", {"rpd": 99})
        lim = quota_ledger.effective_limits(
            {"id": "p1", "quota_limits": {"rpd": 20}})
        self.assertEqual(lim, {"rpd": 99.0})


class CheckTests(unittest.TestCase):
    def setUp(self):
        quota_ledger.invalidate_cache()
        self.now = time.time()
        self.day = time.strftime("%Y-%m-%d")

    def tearDown(self):
        quota_ledger.invalidate_cache()

    def _snap(self, pid, **kw):
        base = {"rpm": 0, "rpd": 0, "tpm": 0, "tpd": 0}
        base.update(kw)
        return {pid: base}

    def test_no_limits_never_throttled(self):
        res = quota_ledger.check("p1", snap=self._snap("p1", rpd=999999))
        self.assertFalse(res["throttled"])

    def test_rpd_exhausted(self):
        with mock.patch.object(quota_ledger, "load_limits",
                               return_value={"p1": {"rpd": 10}}):
            res = quota_ledger.check("p1", snap=self._snap("p1", rpd=10))
        self.assertTrue(res["throttled"])
        self.assertIn("rpd 10/10", res["reasons"])

    def test_tpd_with_est_tokens(self):
        with mock.patch.object(quota_ledger, "load_limits",
                               return_value={"p1": {"tpd": 1000}}):
            ok = quota_ledger.check("p1", est_tokens=200,
                                    snap=self._snap("p1", tpd=800))
            blocked = quota_ledger.check("p1", est_tokens=300,
                                         snap=self._snap("p1", tpd=800))
        self.assertFalse(ok["throttled"])   # 800+200 == 1000 边界内
        self.assertTrue(blocked["throttled"])
        self.assertIn("tpd 800+300/1000", blocked["reasons"])

    def test_rpm_counts_only_last_60s(self):
        ts_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - 10))
        ts_old = time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(self.now - 3600))
        recs = [_rec("p1", ts_now, self.day), _rec("p1", ts_old, self.day)]
        with mock.patch("core.usage._iter_records", return_value=recs):
            snap = quota_ledger.snapshot(now=self.now)
        provs = snap["providers"] if "providers" in snap else snap
        self.assertEqual(provs["p1"]["rpm"], 1)
        self.assertEqual(provs["p1"]["rpd"], 2)
        self.assertEqual(provs["p1"]["tpm"], 100)
        self.assertEqual(provs["p1"]["tpd"], 200)

    def test_snapshot_cache_and_invalidate(self):
        with mock.patch("core.usage._iter_records",
                        return_value=[_rec("p1", time.strftime(
                            "%Y-%m-%d %H:%M:%S"), self.day)]) as m:
            quota_ledger.snapshot(now=self.now)
            quota_ledger.snapshot(now=self.now)
            self.assertEqual(m.call_count, 1)
            quota_ledger.invalidate_cache()
            quota_ledger.snapshot(now=self.now)
            self.assertEqual(m.call_count, 2)

    def test_per_key_breakdown(self):
        """P3 per-key 分账：带 key_fp 的记录另入 per_key 桶（provider 合计不变）。"""
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - 5))
        recs = [
            dict(_rec("p1", ts, self.day, tokens=100), key_fp="aaaa1111"),
            dict(_rec("p1", ts, self.day, tokens=200), key_fp="bbbb2222"),
            _rec("p1", ts, self.day, tokens=50),          # 无指纹（旧记录）
        ]
        with mock.patch("core.usage._iter_records", return_value=recs):
            snap = quota_ledger.snapshot(now=self.now)
        provs = snap["providers"]
        self.assertEqual(provs["p1"]["rpd"], 3)
        self.assertEqual(provs["p1"]["tpd"], 350)
        pk = snap["per_key"]["p1"]
        self.assertEqual(pk["aaaa1111"]["tpd"], 100)
        self.assertEqual(pk["bbbb2222"]["tpd"], 200)
        self.assertNotIn("", pk)          # 无指纹不进分账（只在合计里）

    def test_view_includes_per_key_rows(self):
        from app.core import usage as u
        fp = u.key_fp("secret-key")
        self.assertEqual(len(fp), 8)
        with mock.patch.object(quota_ledger, "snapshot",
                               return_value={"providers": {}, "per_key": {
                                   "p1": {fp: {"rpm": 1, "rpd": 2,
                                               "tpm": 10, "tpd": 20}}}}), \
             mock.patch.object(quota_ledger, "load_limits", return_value={}):
            v = quota_ledger.view([{"id": "p1", "name": "甲"}])
        row = v["providers"][0]
        self.assertEqual(row["per_key"][0]["key_fp"], fp)
        self.assertEqual(row["per_key"][0]["rpd"], 2)

    def test_key_fp_stable_and_blind(self):
        from app.core import usage as u
        self.assertEqual(u.key_fp("k1"), u.key_fp("k1"))
        self.assertNotEqual(u.key_fp("k1"), u.key_fp("k2"))
        self.assertEqual(u.key_fp(""), "")
        self.assertNotIn("k1", u.key_fp("k1"))    # 不泄 key 本体


class FilterTests(unittest.TestCase):
    def setUp(self):
        quota_ledger.invalidate_cache()

    def tearDown(self):
        quota_ledger.invalidate_cache()

    def test_throttled_provider_filtered_keep_order(self):
        cands = [{"provider_id": "a"}, {"provider_id": "b"}, {"provider_id": "c"}]
        with mock.patch.object(quota_ledger, "load_limits",
                               return_value={"b": {"rpd": 1}}), \
             mock.patch.object(quota_ledger, "snapshot",
                               return_value={"b": {"rpm": 0, "rpd": 1,
                                                   "tpm": 0, "tpd": 0}}):
            kept, blocked = quota_ledger.filter_candidates(cands)
        self.assertEqual([e["provider_id"] for e in kept], ["a", "c"])
        self.assertEqual(blocked, ["b"])

    def test_all_blocked_fail_open(self):
        cands = [{"provider_id": "a"}, {"provider_id": "b"}]
        with mock.patch.object(quota_ledger, "load_limits",
                               return_value={"a": {"rpd": 1}, "b": {"rpd": 1}}), \
             mock.patch.object(quota_ledger, "snapshot",
                               return_value={"a": {"rpm": 0, "rpd": 1, "tpm": 0, "tpd": 0},
                                             "b": {"rpm": 0, "rpd": 1, "tpm": 0, "tpd": 0}}):
            kept, blocked = quota_ledger.filter_candidates(cands)
        self.assertEqual(kept, cands)
        self.assertEqual(blocked, ["a", "b"])

    def test_empty_candidates(self):
        kept, blocked = quota_ledger.filter_candidates([])
        self.assertEqual((kept, blocked), ([], []))


class DispatchWiringTests(unittest.TestCase):
    def setUp(self):
        quota_ledger.invalidate_cache()

    def tearDown(self):
        quota_ledger.invalidate_cache()

    def _prov(self, pid, name):
        return {"id": pid, "name": name}

    def test_rank_skips_throttled_provider(self):
        entries = [{"provider_id": "a", "model": "m"},
                   {"provider_id": "b", "model": "m"}]
        with mock.patch.object(quota_ledger, "load_limits",
                               return_value={"b": {"rpd": 1}}), \
             mock.patch.object(quota_ledger, "snapshot",
                               return_value={"b": {"rpm": 0, "rpd": 1,
                                                   "tpm": 0, "tpd": 0}}):
            ranked, decisions = dispatch.rank_model_entries(
                entries, {"a": self._prov("a", "A"), "b": self._prov("b", "B")},
                {}, "default", force=True)
        self.assertEqual([e["provider_id"] for e in ranked], ["a"])

    def test_rank_fail_open_when_all_throttled(self):
        entries = [{"provider_id": "a", "model": "m"}]
        with mock.patch.object(quota_ledger, "load_limits",
                               return_value={"a": {"rpd": 1}}), \
             mock.patch.object(quota_ledger, "snapshot",
                               return_value={"a": {"rpm": 0, "rpd": 1,
                                                   "tpm": 0, "tpd": 0}}):
            ranked, _ = dispatch.rank_model_entries(
                entries, {"a": self._prov("a", "A")}, {}, "default", force=True)
        self.assertEqual([e["provider_id"] for e in ranked], ["a"])

    def test_rank_unaffected_without_limits(self):
        entries = [{"provider_id": "a", "model": "m"}]
        with mock.patch.object(quota_ledger, "load_limits", return_value={}):
            ranked, _ = dispatch.rank_model_entries(
                entries, {"a": self._prov("a", "A")}, {}, "default", force=True)
        self.assertEqual(len(ranked), 1)


class ViewTests(unittest.TestCase):
    def test_view_merges_limits_and_usage(self):
        quota_ledger.invalidate_cache()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(quota_ledger, "_limits_file",
                                   return_value=Path(tmp) / "q.json"), \
                 mock.patch.object(quota_ledger, "snapshot",
                                   return_value={"p1": {"rpm": 1, "rpd": 3,
                                                        "tpm": 10, "tpd": 30},
                                                 "p2": {"rpm": 0, "rpd": 9,
                                                        "tpm": 0, "tpd": 0}}), \
                 mock.patch.object(quota_ledger, "load_limits",
                                   return_value={"p1": {"rpd": 5},
                                                 "p2": {"rpd": 5}}):
                v = quota_ledger.view([{"id": "p1", "name": "一号"}])
        row1 = next(r for r in v["providers"] if r["provider"] == "p1")
        row2 = next(r for r in v["providers"] if r["provider"] == "p2")
        self.assertEqual(row1["name"], "一号")
        self.assertFalse(row1["throttled"])   # rpd 3/5 未满
        self.assertTrue(row2["throttled"])    # rpd 9/5 已用尽
        self.assertEqual(row2["name"], "p2")  # 没挂供应商条目时回退 id
