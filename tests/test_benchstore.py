# -*- coding: utf-8 -*-
"""评测台实测分软信号（benchstore + dispatch 接线）单测。

口径（docs/execution-standard.md 候选打分权重表「实测（评测台）」行）：
bonus = clamp((overall − 7.0) × 1.5, ±4.5)，14 天新鲜度，无数据记 0。

跑法：python -m unittest discover -s tests -p "test_benchstore.py" -v
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from base import BaseTest

from app.core import benchstore, dispatch, evalbench


def _ts(days_ago=0):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days_ago * 86400))


def _seed(provider_id, model, overall, ts=None, scored=True):
    evalbench._persist_result({
        "ts": ts or _ts(), "sample_id": "writing", "provider_id": provider_id,
        "model": model, "ok": True, "scored": scored,
        "verify_ok": None, "error": "", "overall": overall if scored else None,
        "scores": {}, "comment": "", "cost_usd": 0.0, "duration_s": 1.0,
        "same_family": False})


class TestBonusFor(BaseTest):

    def test_no_data_is_zero(self):
        self.assertEqual(benchstore.bonus_for("p", "m"), (0.0, ""))

    def test_scale_and_clamp(self):
        cases = {9.0: 3.0, 5.0: -3.0, 7.0: 0.0, 10.0: 4.5, 0.0: -4.5}
        for overall, want in cases.items():
            benchstore.write_doc({})
            _seed("p", "m%d" % int(overall * 10), overall)
            score, reason = benchstore.bonus_for("p", "m%d" % int(overall * 10))
            self.assertEqual(score, want, "overall=%s" % overall)
            self.assertIn("实测", reason)
            self.assertIn("（%+.1f" % score, reason)

    def test_unscored_rows_ignored(self):
        _seed("p", "m", None, scored=False)
        self.assertEqual(benchstore.bonus_for("p", "m"), (0.0, ""))

    def test_stale_beyond_freshness_is_zero(self):
        _seed("p", "m", 9.0, ts=_ts(days_ago=15))
        self.assertEqual(benchstore.bonus_for("p", "m"), (0.0, ""))
        # 显式放宽窗口又能吃到
        score, _ = benchstore.bonus_for("p", "m", max_age_days=30)
        self.assertEqual(score, 3.0)

    def test_latest_per_key_wins(self):
        _seed("p", "m", 5.0, ts=_ts(days_ago=2))
        _seed("p", "m", 9.0, ts=_ts(days_ago=1))
        score, _ = benchstore.bonus_for("p", "m")
        self.assertEqual(score, 3.0)

    def test_malformed_ts_treated_stale(self):
        _seed("p", "m", 9.0, ts="not-a-date")
        self.assertEqual(benchstore.bonus_for("p", "m"), (0.0, ""))

    def test_corrupted_doc_is_empty(self):
        from app.core import paths
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (paths.DATA_DIR / "eval_bench.json").write_text("not json{", encoding="utf-8")
        self.assertEqual(benchstore.read_doc(), {})
        self.assertEqual(benchstore.read_results(), [])


class TestDispatchIntegration(BaseTest):

    _PROVIDERS = {"p1": {"id": "p1", "name": "P1",
                         "models": [{"name": "m1", "priority": 1}]}}

    def _score(self):
        entry = {"provider_id": "p1", "model": "m1"}
        return dispatch.score_model_entry(entry, self._PROVIDERS, {}, "hard",
                                          "code", "implement")

    def test_bench_shifts_score_and_reason(self):
        base_score, base_reason = self._score()
        self.assertNotIn("实测", base_reason)            # 无数据：与旧口径完全一致
        _seed("p1", "m1", 9.0)
        with_bench, with_reason = self._score()
        self.assertAlmostEqual(with_bench - base_score, 3.0, places=2)
        self.assertIn("实测 9.0 分（+3.0", with_reason)

    def test_negative_bench_applies(self):
        base_score, _ = self._score()                    # 基线必须先于种数据
        _seed("p1", "m1", 4.0)
        with_bench, with_reason = self._score()
        self.assertAlmostEqual(with_bench - base_score, -4.5, places=2)   # (4−7)×1.5=−4.5
        self.assertIn("−4.5", with_reason.replace("-", "−"))

    def test_bonus_map_shape(self):
        _seed("p1", "m1", 8.0)
        _seed("p1", "", 9.0)                             # 无模型名不入表
        m = benchstore.bonus_map()
        self.assertEqual(set(m), {("p1", "m1")})
        self.assertEqual(m[("p1", "m1")]["overall"], 8.0)

    def test_bonus_map_carries_bonus_value(self):
        """bonus 值随 map 透出（评测页反哺视图/通知不复算常量）"""
        _seed("p1", "m1", 8.0)                           # (8−7)×1.5 = +1.5
        _seed("p1", "m2", 4.0)                           # clamp 到 −4.5
        m = benchstore.bonus_map()
        self.assertEqual(m[("p1", "m1")]["bonus"], 1.5)
        self.assertEqual(m[("p1", "m2")]["bonus"], -4.5)


class TestFeedbackGate(BaseTest):
    """settings.bench_feedback_enabled=False：整路软信号关闭，纯榜单展示"""

    _PROVIDERS = {"p1": {"id": "p1", "name": "P1",
                         "models": [{"name": "m1", "priority": 1}]}}

    def _score(self):
        entry = {"provider_id": "p1", "model": "m1"}
        return dispatch.score_model_entry(entry, self._PROVIDERS, {}, "hard",
                                          "code", "implement")

    def test_gate_off_disables_signal(self):
        from app.core import settings as settings_mod
        _seed("p1", "m1", 9.0)
        with mock.patch.object(settings_mod, "load",
                               return_value={"bench_feedback_enabled": False}):
            self.assertEqual(
                dispatch._bench_bonus({"provider_id": "p1", "model": "m1"}),
                (0.0, ""))
            score, reason = self._score()
            self.assertNotIn("实测", reason)
        with mock.patch.object(settings_mod, "load",
                               return_value={"bench_feedback_enabled": True}):
            score2, reason2 = self._score()
        self.assertIn("实测 9.0 分（+3.0", reason2)
        self.assertGreater(score2, score)

    def test_gate_defaults_on_when_key_missing(self):
        """老配置无此键：默认开（评测了就该去影响）"""
        from app.core import settings as settings_mod
        _seed("p1", "m1", 9.0)
        with mock.patch.object(settings_mod, "load", return_value={}):
            score, reason = dispatch._bench_bonus({"provider_id": "p1", "model": "m1"})
        self.assertEqual(score, 3.0)


if __name__ == "__main__":
    unittest.main()
