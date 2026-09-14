# -*- coding: utf-8 -*-
"""运行时不变量注册表测试。
设计稿：docs/migration/01-defense-patterns.md §5F。
"""
from __future__ import annotations

from base import BaseTest


class TestInvariantRegistryBasic(BaseTest):

    def test_empty_registry_returns_no_fails(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        self.assertEqual(r.run_for("pipeline", {}), [])

    def test_passing_check_returns_no_fails(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "always_pass", lambda ctx: None)
        self.assertEqual(r.run_for("pipeline", {}), [])

    def test_failing_check_returns_message(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "always_fail", lambda ctx: "something wrong")
        fails = r.run_for("pipeline", {})
        self.assertEqual(len(fails), 1)
        s, n, m = fails[0]
        self.assertEqual(s, "pipeline")
        self.assertEqual(n, "always_fail")
        self.assertEqual(m, "something wrong")

    def test_check_receives_ctx(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        captured = []
        r.register("pipeline", "capture", lambda ctx: captured.append(ctx) or None)
        r.run_for("pipeline", {"foo": "bar"})
        self.assertEqual(captured, [{"foo": "bar"}])

    def test_check_exception_caught(self):
        """check 函数自身抛异常 → 捕获并标记失败，不向上冒。"""
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "boom", lambda ctx: 1/0)
        fails = r.run_for("pipeline", {})
        self.assertEqual(len(fails), 1)
        self.assertIn("ZeroDivision", fails[0][2])


class TestInvariantRegistryFailFast(BaseTest):

    def test_fail_fast_raises(self):
        from app.core.diagnostics import InvariantRegistry, InvariantError
        r = InvariantRegistry()
        r.register("pipeline", "fails", lambda ctx: "nope")
        with self.assertRaises(InvariantError) as cm:
            r.run_for("pipeline", {}, fail_fast=True)
        self.assertEqual(cm.exception.check_name, "fails")

    def test_fail_fast_false_returns_list(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "fails", lambda ctx: "nope")
        fails = r.run_for("pipeline", {}, fail_fast=False)
        self.assertEqual(len(fails), 1)


class TestInvariantRegistryIsolation(BaseTest):

    def test_different_sources_isolated(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "p1", lambda ctx: None)
        r.register("store", "s1", lambda ctx: "fail")
        self.assertEqual(r.run_for("pipeline", {}), [])
        fails = r.run_for("store", {})
        self.assertEqual(len(fails), 1)

    def test_register_overwrites(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "x", lambda ctx: "v1")
        r.register("pipeline", "x", lambda ctx: "v2")
        fails = r.run_for("pipeline", {})
        self.assertEqual(fails[0][2], "v2")

    def test_unregister_specific(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "x", lambda ctx: "fail")
        r.unregister("pipeline", "x")
        self.assertEqual(r.run_for("pipeline", {}), [])

    def test_unregister_whole_source(self):
        from app.core.diagnostics import InvariantRegistry
        r = InvariantRegistry()
        r.register("pipeline", "x", lambda ctx: "fail")
        r.register("pipeline", "y", lambda ctx: "fail")
        r.unregister("pipeline")
        self.assertEqual(r.run_for("pipeline", {}), [])


class TestInvariantRegistryConcurrency(BaseTest):
    """并发注册 + 运行。"""

    def test_concurrent_register_no_deadlock(self):
        from app.core.diagnostics import InvariantRegistry
        import threading
        r = InvariantRegistry()

        def worker(i):
            r.register("pipeline", f"x{i}", lambda ctx: None)
            r.run_for("pipeline", {})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)


class TestDefaultChecks(BaseTest):
    """register_default_checks 注册的默认 invariants。"""

    def test_register_default(self):
        from app.core import diagnostics
        # 先清空再注册
        diagnostics.invariants.unregister("pipeline")
        diagnostics.register_default_checks()
        listed = diagnostics.invariants.list("pipeline")
        names = [n for _, n in listed]
        self.assertIn("review_no_all_fail_zero", names)
        self.assertIn("step_count_consistency", names)

    def test_review_no_all_fail_zero_passes(self):
        from app.core import diagnostics
        diagnostics.invariants.unregister("pipeline")
        diagnostics.register_default_checks()
        # 全 0 分但 final_status != success → 通过
        fails = diagnostics.invariants.run_for("pipeline", {
            "review_scores": [0, 0],
            "final_status": "failed",
        })
        relevant = [f for f in fails if f[1] == "review_no_all_fail_zero"]
        self.assertEqual(relevant, [])

    def test_review_no_all_fail_zero_fails(self):
        from app.core import diagnostics
        diagnostics.invariants.unregister("pipeline")
        diagnostics.register_default_checks()
        # 全 0 分且 final_status = success → 触发
        fails = diagnostics.invariants.run_for("pipeline", {
            "review_scores": [0, 0],
            "final_status": "success",
        })
        relevant = [f for f in fails if f[1] == "review_no_all_fail_zero"]
        self.assertEqual(len(relevant), 1)
        self.assertIn("评审全部 0 分", relevant[0][2])