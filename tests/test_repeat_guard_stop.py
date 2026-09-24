# -*- coding: utf-8 -*-
"""repeat-guard 判死终态回归（2026-09-23 实案）：守卫 stop=True 后外层重试
继续烧（count 5→6→7），修为守卫结果带 repeat_stop 标记、重试/换将循环见
标记即终态跳出。"""
from __future__ import annotations

from base import BaseTest


class TestRepeatGuardStop(BaseTest):

    def test_guard_stop_result_carries_terminal_marker(self):
        from app.core import pipeline, repeat_guard, store
        from app.core.error_codes import ErrorCode
        task = store.create_task({"type": "code", "title": "重复守卫",
                                  "goal": "验证守卫判死终态", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        prompt = "同一个提示词模板内容" * 10
        # 预热计数链到阈值-1（阈值 3 提醒 / 5 强停）
        for _ in range(4):
            repeat_guard.guard.check(run["id"], "draft", prompt,
                                     identity="claude-code")
        agent = {"id": "claude-code", "label": "Claude Code",
                 "kind": "claude", "mode": "real"}   # 无 binding_configured → 过死链闸
        res = pipeline._run_step(run["id"], "draft", agent, prompt,
                                 str(self.workdir), readonly=True, ev=None)
        self.assertFalse(res["ok"])
        self.assertIn("强制停止", res["error"])
        self.assertEqual(res["error_code"], ErrorCode.ENV_BLOCK)
        self.assertTrue((res.get("raw") or {}).get("repeat_stop"),
                        "守卫判死结果必须携带 repeat_stop 终态标记")
        step = (store.get_run(run["id"]).get("steps") or [])[0]
        self.assertEqual(step["status"], "failed")
        self.assertIn("强制停止", step.get("error") or res["error"],
                      "判死原因必须可见（步骤错误或备注）")

    def test_prefill_below_threshold_does_not_stop(self):
        from app.core import pipeline, repeat_guard, store
        task = store.create_task({"type": "code", "title": "重复守卫-未达阈",
                                  "goal": "x", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        prompt = "另一种提示词内容" * 8
        for _ in range(2):   # 阈值 3 以下：只提醒不停止
            repeat_guard.guard.check(run["id"], "draft", prompt)
        from app.core import pipeline as P
        orig = P._spawn_step

        def fake(*a, **k):
            return {"ok": True, "text": "done", "json": None, "cost_usd": 0.0,
                    "tokens": 1, "usage": None, "error": "",
                    "raw": {"exit_code": 0}}
        P._spawn_step = fake
        try:
            res = P._run_step(run["id"], "draft",
                              {"id": "claude-code", "kind": "claude", "mode": "real"},
                              prompt, str(self.workdir), readonly=True, ev=None)
        finally:
            P._spawn_step = orig
        self.assertTrue(res["ok"], "未达阈值的重复不应判死")
