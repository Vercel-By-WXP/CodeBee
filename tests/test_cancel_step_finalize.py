# -*- coding: utf-8 -*-
"""取消收尾回归：取消运行后步骤记录必须落终态，不能永远停在「运行中」。

真实事故（2026-09-15）：取消确认落下时 implement 步骤刚起步，run_process 已把
进程杀停，但 _run_step 先抛 Cancelled 再收尾——步骤记录永远停在「运行中」，
UI 出现「运行已取消、蜂巢格还在转」的僵尸格。修复：先 _finish_step_result
再 _check_cancel（与 _run_verify 的顺序对齐），并把被杀步骤如实记「已取消」。
"""
from __future__ import annotations

import threading
import time

from base import BaseTest


def _real_agent():
    return {"id": "a1", "mode": "real", "kind": "generic", "label": "测试智能体"}


class TestCancelStepFinalize(BaseTest):

    def _make_run(self):
        from app.core import store
        run = store.create_run("orchestration", "取消收尾测试")
        store.update_run(run["id"], status="running")
        return run["id"]

    def test_cancel_midflight_finalizes_step(self):
        """进程被杀后步骤必须落「已取消」终态，而非永远「运行中」。"""
        from app.core import pipeline, runner, store
        run_id = self._make_run()
        ev = threading.Event()

        def fake_run_agent(agent, prompt, **kw):
            # 模拟已下达到 CLI 的步骤：挂起直到用户取消，返回被杀结果
            self.assertTrue(ev.wait(timeout=5))
            return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                    "tokens": 0, "error": "取消；[已被用户取消]", "sid": "",
                    "kind": "generic", "model": "m",
                    "raw": {"exit_code": None, "cancelled": True, "timed_out": False}}

        backup = runner.run_agent
        runner.run_agent = fake_run_agent
        try:
            box = {}

            def target():
                try:
                    pipeline._run_step(run_id, "implement", _real_agent(),
                                       "p", str(self.workdir), False, ev)
                    box["raised"] = False
                except pipeline.Cancelled:
                    box["raised"] = True

            th = threading.Thread(target=target, daemon=True)
            th.start()
            time.sleep(0.4)          # 让线程走进 fake_run_agent 的 wait
            ev.set()                 # 用户点「取消运行」→ jobs.cancel 置位
            th.join(timeout=5)
            self.assertFalse(th.is_alive())
            self.assertTrue(box.get("raised"), "Cancelled 必须照常向上抛")
            step = (store.get_run(run_id) or {}).get("steps", [])[-1]
            self.assertEqual(step["status"], "cancelled")   # 收尾了，不是僵尸
        finally:
            runner.run_agent = backup

    def test_cancel_after_normal_finish_keeps_done(self):
        """进程正常跑完、取消恰好落在收尾窗口：步骤记 done，取消仍向上抛。"""
        from app.core import pipeline, runner, store
        run_id = self._make_run()
        ev = threading.Event()
        ev.set()   # 进程已正常结束、收尾前取消被置位

        def fake_run_agent(agent, prompt, **kw):
            return {"ok": True, "text": "好了", "json": None, "cost_usd": 0.0,
                    "tokens": 0, "error": "", "sid": "", "kind": "generic",
                    "model": "m", "raw": {"exit_code": 0, "cancelled": False}}

        backup = runner.run_agent
        runner.run_agent = fake_run_agent
        try:
            with self.assertRaises(pipeline.Cancelled):
                pipeline._run_step(run_id, "implement", _real_agent(),
                                   "p", str(self.workdir), False, ev)
            step = (store.get_run(run_id) or {}).get("steps", [])[-1]
            self.assertEqual(step["status"], "done")
        finally:
            runner.run_agent = backup

    def test_timeout_step_gets_timeout_status(self):
        """超时被杀的步骤记「timeout」，与普通失败/取消三色区分；不抛 Cancelled。"""
        from app.core import pipeline, runner, store
        run_id = self._make_run()

        def fake_run_agent(agent, prompt, **kw):
            return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                    "tokens": 0, "error": "超时 1200s，已终止进程树；stderr: x",
                    "sid": "", "kind": "generic", "model": "m",
                    "raw": {"exit_code": None, "cancelled": False, "timed_out": True}}

        backup = runner.run_agent
        runner.run_agent = fake_run_agent
        try:
            res = pipeline._run_step(run_id, "implement", _real_agent(),
                                     "p", str(self.workdir), False, None)
            self.assertFalse(res["ok"])
            step = (store.get_run(run_id) or {}).get("steps", [])[-1]
            self.assertEqual(step["status"], "timeout")
        finally:
            runner.run_agent = backup

    def test_timeout_verify_gets_timeout_status(self):
        """验证命令超时被杀：步骤记 timeout 而非 failed（与 _finish_step_result 同口径）。"""
        from app.core import pipeline, runner, store
        run_id = store.create_run("orchestration", "验证超时测试")["id"]
        store.update_run(run_id, status="running")
        task = {"verify_command": "echo hi", "goal": "g", "workdir": str(self.workdir)}

        def fake_run_process(**kw):
            return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                    "duration": 600.0, "cancelled": False, "timed_out": True}

        backup = runner.run_process
        runner.run_process = fake_run_process
        try:
            ok, ran = pipeline._run_verify(run_id, task, str(self.workdir), None)
            self.assertFalse(ok)
            self.assertTrue(ran)
            step = (store.get_run(run_id) or {})["steps"][-1]
            self.assertEqual(step["status"], "timeout")
        finally:
            runner.run_process = backup

    def test_startup_sweep_heals_zombie_steps(self):
        """启动清扫：终态 run 里卡「运行中」的步骤落「已取消」，历史僵尸自愈。"""
        from app.core import store
        rid1 = store.create_run("orchestration", "已取消带僵尸步骤")["id"]
        store.update_run(rid1, status="cancelled", ended_at="t")
        store.add_step(rid1, "plan", "a1", "规划")
        store.add_step(rid1, "implement", "a1", "实现")
        # 崩溃遗留：run 还在 running → 先被孤儿恢复标 failed，步骤随后被清扫
        rid2 = store.create_run("orchestration", "崩溃遗留")["id"]
        store.update_run(rid2, status="running")
        store.add_step(rid2, "plan", "a1", "规划")
        store.recover_orphaned_runs()
        for s in (store.get_run(rid1) or {})["steps"]:
            self.assertEqual(s["status"], "cancelled")
        for s in (store.get_run(rid2) or {})["steps"]:
            self.assertEqual(s["status"], "cancelled")
