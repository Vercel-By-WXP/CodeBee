# -*- coding: utf-8 -*-
"""pipeline × repeat_guard（5C）与 diagnostics（5F）接线测试。
设计稿：docs/migration/01-defense-patterns.md §5C/§5F 集成段。
"""
from __future__ import annotations

import sys
from pathlib import Path
from base import BaseTest

FIXTURES = Path(__file__).parent


def _real_agent():
    # env=自带配置：2026-09-17 起无绑定链的真实智能体会在 _run_step 死链闸门
    # 判失败；假 CLI 以「自带配置」语义过闸（本文件测的是守门接线，不是绑定）
    return {"id": "fakecli", "kind": "generic",
            "command": sys.executable,
            "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
            "mode": "real", "label": "Fake CLI",
            "env": {"TUTTI_TEST_SELFCONFIG": "1"}}


class TestPipelineRepeatGuardWiring(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import repeat_guard
        repeat_guard.DEFAULT = None
        # 每用例独立 guard 实例（避免模块单例跨用例串扰）
        from app.core import pipeline
        self._orig_guard = pipeline.repeat_guard
        pipeline.repeat_guard = repeat_guard.RepeatGuard(thresholds=(3, 5, 8))

    def tearDown(self):
        from app.core import pipeline
        pipeline.repeat_guard = self._orig_guard
        super().tearDown()

    def test_first_calls_pass_through(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "guard-t1")
        for _ in range(2):
            res = pipeline._run_step(run["id"], "draft", _real_agent(), "同一prompt",
                                     str(self.workdir), readonly=True, ev=None)
            self.assertTrue(res["ok"], res.get("error"))

    def test_reminder_injected_at_threshold(self):
        """达到阈值 3 时：step 仍执行，但 prompt 前被注入提醒（正文仍在结果里）。"""
        from app.core import pipeline, store
        run = store.create_run("orchestration", "guard-t2")
        for i in range(2):
            pipeline._run_step(run["id"], "draft", _real_agent(), "同一prompt",
                               str(self.workdir), readonly=True, ev=None)
        res3 = pipeline._run_step(run["id"], "draft", _real_agent(), "同一prompt",
                                  str(self.workdir), readonly=True, ev=None)
        # 第 3 次仍执行成功（提醒只是建议）
        self.assertTrue(res3["ok"])
        # 守门内部计数到 3
        stats = pipeline.repeat_guard.stats(run["id"], "draft")
        self.assertEqual(stats["count"], 3)

    def test_stop_beyond_last_threshold_no_spawn(self):
        """超过末位阈值：不再 spawn，返回 ENV_BLOCK 失败结果。"""
        from app.core import pipeline, store
        run = store.create_run("orchestration", "guard-t3")
        n_calls = 8
        last = None
        for _ in range(n_calls):
            last = pipeline._run_step(run["id"], "draft", _real_agent(), "same",
                                      str(self.workdir), readonly=True, ev=None)
        self.assertFalse(last["ok"])
        self.assertEqual(last["error_code"], "ENV_BLOCK")
        self.assertIn("强制停止", last["error"])
        # step 记录为 failed
        run_after = store.get_run(run["id"])
        self.assertEqual(run_after["steps"][-1]["status"], "failed")

    def test_different_prompt_does_not_accumulate(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "guard-t4")
        for i in range(6):
            res = pipeline._run_step(run["id"], "draft", _real_agent(),
                                     "不同的 prompt %d" % i,
                                     str(self.workdir), readonly=True, ev=None)
            self.assertTrue(res["ok"])

    def test_roles_isolated(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "guard-t5")
        for _ in range(4):
            pipeline._run_step(run["id"], "draft", _real_agent(), "same",
                               str(self.workdir), readonly=True, ev=None)
        res = pipeline._run_step(run["id"], "review", _real_agent(), "same",
                                 str(self.workdir), readonly=True, ev=None)
        self.assertTrue(res["ok"])  # 另一角色从 1 计数


class TestPipelineDiagnosticsWiring(BaseTest):

    def test_step_level_invariant_runs(self):
        """_run_step 收尾会跑 step 级 invariant（正常成功无告警，但调用必须发生）。"""
        from app.core import pipeline, store, diagnostics
        diagnostics.register_default_checks()
        called_sources = []
        orig = diagnostics.invariants.run_for

        def spy(source, ctx, **kw):
            called_sources.append(source)
            return orig(source, ctx, **kw)

        diagnostics.invariants.run_for = spy
        try:
            run = store.create_run("orchestration", "diag-t1")
            res = pipeline._run_step(run["id"], "draft", _real_agent(), "网文主编",
                                     str(self.workdir), readonly=True, ev=None)
            self.assertTrue(res["ok"])
            self.assertIn("step", called_sources)
        finally:
            diagnostics.invariants.run_for = orig

    def test_ok_but_error_code_flagged(self):
        """直接喂一个 ok=True + error_code 的结果 → step 级断言告警。"""
        from app.core import diagnostics
        diagnostics.register_default_checks()
        fails = diagnostics.invariants.run_for("step", {
            "run_id": "r-x", "role": "draft", "ok": True,
            "error_code": "EMPTY", "text_len": 10,
        })
        self.assertTrue(any(f[1] == "ok_but_error_code" for f in fails))

    def test_clean_step_no_flag(self):
        from app.core import diagnostics
        diagnostics.register_default_checks()
        fails = diagnostics.invariants.run_for("step", {
            "run_id": "r-x", "role": "draft", "ok": True,
            "error_code": "", "text_len": 100,
        })
        self.assertEqual(fails, [])