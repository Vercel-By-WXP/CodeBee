# -*- coding: utf-8 -*-
"""token_meter 测试。
设计稿：docs/migration/02-context-compaction.md §1C。
"""
from __future__ import annotations

import threading
from base import BaseTest


class TestTokenMeterAccumulate(BaseTest):

    def test_accumulate_sums(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", {"input": 100, "output": 50, "reasoning": 10})
        m.accumulate("r1", {"input": 200, "output": 30, "reasoning": 0})
        self.assertEqual(m.used("r1"), 100 + 50 + 10 + 200 + 30)

    def test_cached_not_counted_in_used(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", {"input": 100, "output": 50, "cached": 5000})
        self.assertEqual(m.used("r1"), 150)
        self.assertEqual(m.cached("r1"), 5000)

    def test_none_usage_skipped(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", None)
        m.accumulate("r1", {})
        self.assertEqual(m.used("r1"), 0)

    def test_missing_fields_zero(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", {"input": 10})
        self.assertEqual(m.used("r1"), 10)

    def test_runs_isolated(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", {"input": 100})
        m.accumulate("r2", {"input": 200})
        self.assertEqual(m.used("r1"), 100)
        self.assertEqual(m.used("r2"), 200)


class TestTokenMeterPressure(BaseTest):

    def test_pressure_ratio(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000, "big": 1_000_000})
        m.accumulate("r1", {"input": 50_000, "output": 30_000})
        self.assertAlmostEqual(m.pressure_ratio("r1"), 0.8)
        # 显式传更大容量的模型
        self.assertAlmostEqual(m.pressure_ratio("r1", model="big"), 0.08)

    def test_pressure_uses_last_model(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000, "small": 10_000})
        m.accumulate("r1", {"input": 5000}, model="small")
        self.assertAlmostEqual(m.pressure_ratio("r1"), 0.5)

    def test_pressure_empty_run(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        self.assertEqual(m.pressure_ratio("nonexistent"), 0.0)

    def test_pressure_unknown_model_fallback(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", {"input": 90_000}, model="mystery-model")
        self.assertAlmostEqual(m.pressure_ratio("r1"), 0.9)


class TestTokenMeterReset(BaseTest):

    def test_reset_clears(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.accumulate("r1", {"input": 100})
        m.reset("r1")
        self.assertEqual(m.used("r1"), 0)
        self.assertEqual(m.pressure_ratio("r1"), 0.0)

    def test_reset_nonexistent_safe(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})
        m.reset("nonexistent")  # 不抛


class TestTokenMeterWindow(BaseTest):

    def test_window_bounded(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000}, window_size=10)
        for i in range(50):
            m.accumulate("r1", {"input": 1})
        # 窗口只保留最近 10 次
        self.assertEqual(m.used("r1"), 10)


class TestTokenMeterConcurrency(BaseTest):

    def test_concurrent_accumulate(self):
        from app.core.token_meter import TokenMeter
        m = TokenMeter(capacity={"": 100_000})

        def worker(n):
            for _ in range(20):
                m.accumulate("r1", {"input": 1})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(m.used("r1"), 100)


class TestSingleton(BaseTest):

    def test_singleton_exists(self):
        from app.core.token_meter import token_meter, TokenMeter
        self.assertIsInstance(token_meter, TokenMeter)