# -*- coding: utf-8 -*-
"""strategy_config（Raven Evolver 借鉴·机制闸第一件）单测。

覆盖：默认值链路（文件 > 模块常量 getattr 回退 > 规格钳位）、热更、
坏文件回退、挑战者生命周期（set→影子权重→adopt 并入/记账）、benchstore
口径接线。全部走临时数据目录，不碰真实 data/。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import benchstore, history, paths, router, strategy_config


def _isolated_dir(case):
    case.tmp = tempfile.TemporaryDirectory()
    root = Path(case.tmp.name)
    case.old = {
        "file": strategy_config._FILE,
        "cache": dict(strategy_config._CACHE),
        "data_dir": paths.DATA_DIR,
    }
    strategy_config._FILE = root / "strategy.json"
    strategy_config._CACHE = {"mtime": None, "doc": {}}
    paths.DATA_DIR = root
    return root


def _restore(case):
    strategy_config._FILE = case.old["file"]
    strategy_config._CACHE = case.old["cache"]
    paths.DATA_DIR = case.old["data_dir"]
    case.tmp.cleanup()


class StrategyConfigTests(unittest.TestCase):
    def setUp(self):
        _isolated_dir(self)

    def tearDown(self):
        _restore(self)

    def test_defaults_without_file_match_module_constants(self):
        weights = strategy_config.effective_weights()
        self.assertEqual(len(weights), 14)
        self.assertEqual(weights["sticky_bonus"], router.STICKY_BONUS)
        self.assertEqual(weights["streak_window_s"], history.STREAK_WINDOW_S)
        self.assertEqual(weights["bonus_k"], benchstore._K)

    def test_monkeypatched_module_constant_wins_over_spec_default(self):
        # 现有测试通过 monkeypatch 模块常量调权重——getattr 回退必须保住该语义
        with mock.patch.object(router, "STICKY_BONUS", 9.0):
            self.assertEqual(strategy_config.get("sticky_bonus"), 9.0)

    def test_file_weights_override_and_clamp(self):
        ok, err = strategy_config.update_weights({"sticky_bonus": 7.5})
        self.assertTrue(ok, err)
        self.assertEqual(strategy_config.get("sticky_bonus"), 7.5)
        # 越界写入钳位到范围
        ok, _ = strategy_config.update_weights({"sticky_bonus": 999.0})
        self.assertTrue(ok)
        self.assertEqual(strategy_config.get("sticky_bonus"), 100.0)
        # 手改文件越界也拦得住（读取侧钳位）
        strategy_config._FILE.write_text(
            json.dumps({"weights": {"streak_bench_min": -5}}), encoding="utf-8")
        strategy_config._CACHE.update({"mtime": None})
        self.assertEqual(strategy_config.get("streak_bench_min"), 1)

    def test_update_weights_rejects_unknown_and_bad(self):
        ok, err = strategy_config.update_weights({"no_such_key": 1})
        self.assertFalse(ok)
        self.assertIn("未知策略键", err)
        ok, err = strategy_config.update_weights({"sticky_bonus": "abc"})
        self.assertFalse(ok)
        ok, err = strategy_config.update_weights({})
        self.assertFalse(ok)

    def test_bad_file_falls_back_to_defaults(self):
        strategy_config._FILE.write_text("not-json{", encoding="utf-8")
        strategy_config._CACHE.update({"mtime": None})
        self.assertEqual(strategy_config.get("sticky_ttl_s"), router.STICKY_TTL_S)

    def test_hot_reload_without_cache_staleness(self):
        strategy_config._FILE.write_text(
            json.dumps({"weights": {"sticky_bonus": 3.0}}), encoding="utf-8")
        strategy_config._CACHE.update({"mtime": None})
        self.assertEqual(strategy_config.get("sticky_bonus"), 3.0)

    def test_overrides_beat_file_weights(self):
        strategy_config.update_weights({"sticky_bonus": 7.5})
        self.assertEqual(
            strategy_config.get("sticky_bonus", {"sticky_bonus": 12.0}), 12.0)
        self.assertEqual(strategy_config.get("sticky_bonus"), 7.5)

    def test_reset_weights(self):
        strategy_config.update_weights({"sticky_bonus": 7.5})
        strategy_config.reset_weights()
        self.assertEqual(strategy_config.raw_weights(), {})
        self.assertEqual(strategy_config.get("sticky_bonus"), router.STICKY_BONUS)

    def test_challenger_lifecycle_set_shadow_adopt(self):
        ok, err = strategy_config.set_challenger("c1", {"sticky_bonus": 999.0})
        self.assertTrue(ok, err)
        weights = strategy_config.challenger_weights()
        self.assertIsNotNone(weights)
        self.assertEqual(weights["sticky_bonus"], 100.0)   # 钳位
        self.assertEqual(weights["ledger_down_penalty"], router.LEDGER_DOWN_PENALTY)
        # 关闭后影子权重为 None（影子链路停摆）
        strategy_config.set_challenger("c1", {"sticky_bonus": 1.0}, enabled=False)
        self.assertIsNone(strategy_config.challenger_weights())
        strategy_config.set_challenger("c1", {"sticky_bonus": 1.0}, enabled=True)
        ok, err = strategy_config.adopt_challenger(summary={"total": 3})
        self.assertTrue(ok, err)
        self.assertEqual(strategy_config.get("sticky_bonus"), 1.0)
        self.assertIsNone(strategy_config.challenger())
        hist = strategy_config.adopt_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["name"], "c1")
        self.assertEqual(hist[0]["summary"], {"total": 3})
        # 再 adopt 无挑战者 → 拒绝
        ok, err = strategy_config.adopt_challenger()
        self.assertFalse(ok)

    def test_default_strategy_roundtrip(self):
        self.assertEqual(strategy_config.default_strategy(), "")
        ok, err = strategy_config.set_default_strategy("builtin")
        self.assertTrue(ok, err)
        self.assertEqual(strategy_config.default_strategy(), "builtin")


class BenchstoreWiringTests(unittest.TestCase):
    def setUp(self):
        _isolated_dir(self)
        # 一条新鲜满分记录：overall 7.5
        row = {"ts": "2026-10-22 12:00:00", "sample_id": "s", "provider_id": "p1",
               "model": "m1", "ok": True, "scored": True, "overall": 7.5}
        benchstore.write_doc({"results": [row]})
        # ts 是 write_doc 时的真实当前时间附近——直接重写为当前时刻保证新鲜
        import time as _time
        row["ts"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        benchstore.write_doc({"results": [row]})

    def tearDown(self):
        _restore(self)

    def test_bonus_formula_follows_config(self):
        # 默认口径：(7.5-7.0)*1.5 = 0.75
        score, reason = benchstore.bonus_for("p1", "m1")
        self.assertEqual(score, 0.75)
        self.assertIn("实测", reason)
        # 外置斜率翻倍：0.5*3.0 = 1.5
        strategy_config.update_weights({"bonus_k": 3.0})
        score, _ = benchstore.bonus_for("p1", "m1")
        self.assertEqual(score, 1.5)

    def test_bonus_formula_respects_monkeypatched_constant(self):
        with mock.patch.object(benchstore, "_K", 0.5):
            score, _ = benchstore.bonus_for("p1", "m1")
        self.assertEqual(score, 0.25)


if __name__ == "__main__":
    unittest.main()
