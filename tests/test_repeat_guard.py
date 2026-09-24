# -*- coding: utf-8 -*-
"""重复 CLI 调用检测测试。
设计稿：docs/migration/01-defense-patterns.md §5C。
"""
from __future__ import annotations

import threading
from base import BaseTest


class TestPromptFingerprint(BaseTest):

    def test_same_prompt_same_fingerprint(self):
        from app.core.repeat_guard import _prompt_fingerprint
        p1 = "hello world " * 100
        p2 = "hello world " * 100
        self.assertEqual(_prompt_fingerprint(p1), _prompt_fingerprint(p2))

    def test_different_length_different_fingerprint(self):
        from app.core.repeat_guard import _prompt_fingerprint
        self.assertNotEqual(
            _prompt_fingerprint("hello"),
            _prompt_fingerprint("hello world"),
        )

    def test_different_head_different_fingerprint(self):
        from app.core.repeat_guard import _prompt_fingerprint
        # 同样长度但 head 不同
        p1 = "abc" + "x" * 100
        p2 = "xyz" + "x" * 100
        self.assertNotEqual(_prompt_fingerprint(p1), _prompt_fingerprint(p2))

    def test_empty_prompt_safe(self):
        from app.core.repeat_guard import _prompt_fingerprint
        # 不抛异常
        self.assertIsInstance(_prompt_fingerprint(""), str)


class TestRepeatGuardBasic(BaseTest):

    def test_default_guard_stops_on_fifth_identical_step(self):
        from app.core.repeat_guard import RepeatGuard
        guard = RepeatGuard()
        result = None
        for _ in range(5):
            result = guard.check("run-default", "review", "same prompt")
        self.assertTrue(result["should_stop"])
        self.assertIn("强制停止", result["reminder"])

    def test_first_call_no_reminder(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard()
        r = g.check("run1", "planner", "do thing")
        self.assertEqual(r["count"], 1)
        self.assertIsNone(r["reminder"])
        self.assertFalse(r["should_stop"])

    def test_three_in_a_row_no_reminder(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(3):
            r = g.check("run1", "planner", "do thing")
        # 第 3 次命中第一个阈值，应有提醒
        self.assertEqual(r["count"], 3)
        self.assertIsNotNone(r["reminder"])
        self.assertIn("连续 3 次", r["reminder"])
        self.assertFalse(r["should_stop"])

    def test_five_in_a_row_second_reminder(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        r = None
        for i in range(5):
            r = g.check("run1", "planner", "do thing")
        self.assertEqual(r["count"], 5)
        self.assertIsNotNone(r["reminder"])
        self.assertIn("连续 5 次", r["reminder"])

    def test_eight_in_a_row_should_stop(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        r = None
        for i in range(8):
            r = g.check("run1", "planner", "do thing")
        self.assertEqual(r["count"], 8)
        self.assertTrue(r["should_stop"])
        self.assertIsNotNone(r["reminder"])
        self.assertIn("强制停止", r["reminder"])

    def test_above_last_threshold_keeps_stopping(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(10):
            r = g.check("run1", "planner", "do thing")
        self.assertEqual(r["count"], 10)
        self.assertTrue(r["should_stop"])


class TestRepeatGuardCounterReset(BaseTest):

    def test_different_prompt_resets_count(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(3):
            g.check("run1", "planner", "same prompt")
        # 换不同 prompt → count 归 1
        r = g.check("run1", "planner", "different prompt")
        self.assertEqual(r["count"], 1)
        self.assertIsNone(r["reminder"])

    def test_different_role_isolated(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(5):
            g.check("run1", "planner", "p")
        # 同一 run 不同 role → 独立计数
        r = g.check("run1", "reviewer", "p")
        self.assertEqual(r["count"], 1)
        self.assertIsNone(r["reminder"])

    def test_different_run_isolated(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(5):
            g.check("run1", "planner", "p")
        r = g.check("run2", "planner", "p")
        self.assertEqual(r["count"], 1)


class TestRepeatGuardResetMethod(BaseTest):

    def test_reset_clears_run_state(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(5):
            g.check("run1", "planner", "p")
            g.check("run1", "reviewer", "p")
        cleared = g.reset("run1")
        self.assertEqual(cleared, 2)
        # 再调用应从 1 开始
        r = g.check("run1", "planner", "p")
        self.assertEqual(r["count"], 1)

    def test_reset_only_clears_target_run(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))
        for i in range(3):
            g.check("run1", "planner", "p")
            g.check("run2", "planner", "p")
        g.reset("run1")
        # run2 的计数未受影响
        s = g.stats("run2", "planner")
        self.assertEqual(s["count"], 3)


class TestRepeatGuardStats(BaseTest):

    def test_stats_empty_chain(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard()
        s = g.stats("nonexistent", "planner")
        self.assertEqual(s["chain_len"], 0)

    def test_stats_after_calls(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard()
        for i in range(3):
            g.check("run1", "planner", "same")
        s = g.stats("run1", "planner")
        self.assertEqual(s["count"], 3)
        self.assertEqual(s["chain_len"], 3)
        self.assertNotEqual(s["last_fp"], "")


class TestRepeatGuardConcurrency(BaseTest):
    """并发安全。"""

    def test_concurrent_checks_no_deadlock(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8))

        def worker(role):
            for _ in range(10):
                g.check("run1", role, "same prompt")
        threads = [threading.Thread(target=worker, args=(f"role{i}",))
                   for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        # 不应死锁


class TestRepeatGuardMemoryBound(BaseTest):

    def test_chain_does_not_grow_unbounded(self):
        from app.core.repeat_guard import RepeatGuard
        g = RepeatGuard(thresholds=(3, 5, 8), max_history=10)
        # 模拟 100 次不同 prompt 调用
        for i in range(100):
            g.check("run1", "planner", f"prompt {i}")
        s = g.stats("run1", "planner")
        self.assertLessEqual(s["chain_len"], 10)


class TestRepeatGuardAgentIsolation(BaseTest):
    """换将是有效回退，不应继承前一执行者的重复预算。"""

    def test_fallback_agent_gets_a_fresh_budget(self):
        from app.core.repeat_guard import RepeatGuard

        guard = RepeatGuard()
        for _ in range(4):
            result = guard.check("run", "draft-c9", "draft the chapter",
                                 identity="pi")
        self.assertFalse(result["should_stop"])

        result = guard.check("run", "draft-c9", "draft the chapter",
                             identity="claude-code")
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["should_stop"])

        for _ in range(3):
            result = guard.check("run", "draft-c9", "draft the chapter",
                                 identity="claude-code")
        self.assertEqual(result["count"], 4)
        self.assertFalse(result["should_stop"])

        result = guard.check("run", "draft-c9", "draft the chapter",
                             identity="claude-code")
        self.assertEqual(result["count"], 5)
        self.assertTrue(result["should_stop"])
