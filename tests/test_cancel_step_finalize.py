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
    # env=自带配置：死链闸门（2026-09-17）不再放行无绑定链的真实智能体
    return {"id": "a1", "mode": "real", "kind": "generic", "label": "测试智能体",
            "env": {"TUTTI_TEST_SELFCONFIG": "1"}}


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

    def test_cancel_queued_run_takes_effect_immediately(self):
        """排队中的任务点取消：直接落 cancelled 终态，不再等起跑。

        真实缺口（2026-09-15）：取消事件在 worker 起跑时才创建，排队任务
        jobs.cancel 返回 False，起跑后照跑不误——取消像没反应。
        """
        from app.core import jobs, store
        run = store.create_run("orchestration", "排队取消测试")
        rid = run["id"]
        self.assertEqual(run["status"], "queued")
        # 模拟 main.py 取消路由：先落标记再调 jobs.cancel
        store.update_run(rid, cancelled_by_user=True)
        ok = jobs.cancel(rid)
        self.assertTrue(ok, "排队任务必须能立即取消")
        self.assertEqual((store.get_run(rid) or {}).get("status"), "cancelled")
        self.assertTrue((store.get_run(rid) or {}).get("cancelled_by_user"))
        # 取消后的 run 不再被当成排队任务继续（终态幂等：再取消不报错不变态）
        ok2 = jobs.cancel(rid)
        self.assertFalse(ok2)
        self.assertEqual((store.get_run(rid) or {}).get("status"), "cancelled")

    def test_execute_run_honors_cancelled_by_user_flag(self):
        """起跑兜底：run 已带 cancelled_by_user 标记时，execute_run 不再启动流水线。

        覆盖事件与标记竞态的场景（排队期取消后 CANCELS 被清掉、事件丢失）。
        """
        from app.core import jobs, pipeline, store
        rid = store.create_run("orchestration", "标记兜底测试")["id"]
        store.update_run(rid, cancelled_by_user=True)
        # execute_run 会调 cancel_event_for 拿事件；这里直接跑入口
        pipeline.execute_run(rid)
        run = store.get_run(rid)
        self.assertEqual(run["status"], "cancelled")
        self.assertFalse(run.get("steps"), "不得产生任何步骤——流水线没跑")

    def test_cancel_between_run_read_and_start_confirmation_stays_cancelled(self):
        """取消落在 execute_run 初读之后，CAS 起跑确认必须立即退出。"""
        from app.core import jobs, pipeline, store
        task = store.create_task({"type": "direct", "goal": "取消竞态",
                                  "workdir": str(self.workdir)})
        rid = store.create_run("orchestration", task["title"],
                               task_id=task["id"])["id"]
        store.update_run(rid, status="running")
        jobs.cancel_event_for(rid)
        original_get_task = store.get_task

        def cancel_then_get(task_id):
            store.update_run(rid, cancelled_by_user=True)
            self.assertTrue(jobs.cancel(rid))
            return original_get_task(task_id)

        try:
            store.get_task = cancel_then_get
            pipeline.execute_run(rid)
        finally:
            store.get_task = original_get_task
        run = store.get_run(rid)
        self.assertEqual(run["status"], "cancelled")
        self.assertFalse(run.get("steps"))

    def test_cancel_running_run_force_finalizes(self):
        """运行中点取消：立即落 cancelled 终态（含步骤收尸），不等流水线收口。

        强制终止语义（2026-09-20）：jobs.cancel 置位事件后用 CAS 把 running
        翻成 cancelled——update_run 的终态收尸同步关掉还挂「运行中」的步骤，
        UI 即刻翻牌，不再等当前调用跑完。
        """
        from app.core import jobs, store
        rid = store.create_run("orchestration", "运行中强杀测试")["id"]
        store.update_run(rid, status="running")
        store.add_step(rid, "implement", "a1", "实现")   # 挂「运行中」的步骤
        jobs.cancel_event_for(rid)      # worker 起跑即建事件（in-flight 前提）
        store.update_run(rid, cancelled_by_user=True)
        ok = jobs.cancel(rid)
        self.assertTrue(ok)
        run = store.get_run(rid)
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(run["steps"][-1]["status"], "cancelled")
        self.assertEqual(run.get("error"), "用户主动取消")

    def test_cancel_finished_run_not_clobbered(self):
        """已正常结束的运行再点取消：终态不被改写（expected_status CAS 守卫）。"""
        from app.core import jobs, store
        rid = store.create_run("orchestration", "已完成误触取消")["id"]
        store.update_run(rid, status="done", ended_at="t")
        jobs.cancel_event_for(rid)   # 事件尚在（worker 未收尾的窗口）
        ok = jobs.cancel(rid)
        self.assertTrue(ok)
        run = store.get_run(rid)
        self.assertEqual(run["status"], "done")
        self.assertNotEqual(run.get("error"), "用户主动取消")

    def test_builtin_cancel_during_http_returns_promptly(self):
        """内置智能体生成中的模型调用被取消：立即返回「已取消」，不陪跑长请求。

        强制终止语义（2026-09-20）：direct 类任务优先走内置智能体直连模型，
        单次生成本可跑满 timeout——取消事件置位即放弃等待（HTTP 交给守护线程
        收尾），且健康 KEY 不因被放弃的请求背上冷却。
        """
        import threading
        from app.core import builtin_agent
        bi = {"prov": {"id": "p9", "name": "P9", "protocol": "openai",
                       "base_url": "http://gw.test/v1", "api_key": "sk-test",
                       "enabled": True, "model": "m9"},
              "model": "m9", "provider_id": "p9", "provider_name": "P9"}
        ev = threading.Event()

        def slow_post(url, headers, body, allow_private, timeout):
            time.sleep(30)   # 模拟生成中的长请求
            return 200, {"choices": [{"message": {"content": "迟到的回答"}}],
                         "usage": {}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = slow_post
        try:
            box = {}

            def target():
                box["r"] = builtin_agent.run(bi, "长生成", str(self.workdir),
                                             cancel_event=ev)

            th = threading.Thread(target=target, daemon=True)
            th.start()
            time.sleep(0.5)          # 让请求走进 slow_post
            t0 = time.time()
            ev.set()                 # 用户点「取消运行」
            th.join(timeout=6)
            self.assertFalse(th.is_alive(), "取消后必须立即返回，不陪跑 30s 生成")
            self.assertLess(time.time() - t0, 3.0)
            r = box.get("r") or {}
            self.assertFalse(r["ok"])
            self.assertIn("已取消", r.get("error") or "")
        finally:
            builtin_agent._post_json = orig

    def test_mgmt_cancel_not_clobbered_by_step_summary(self):
        """管理操作被强杀后：步骤汇总不得把 cancelled 改写成 failed。

        _do_mgmt 收尾本按步骤汇总落 failed（被杀步骤记的就是非 done），
        expected_status 守卫让用户已终止的终态保持 cancelled。
        """
        from app.core import store
        rid = store.create_run("mgmt", "安装被取消")["id"]
        store.update_run(rid, status="cancelled", ended_at="t",
                         error="用户主动取消")
        store.add_step(rid, "install", "e1", "某CLI")
        store.finish_step(rid, 1, "failed", summary="被取消")
        # 复现收尾写入口径：CAS 应拒绝（当前非 running）
        store.update_run(rid, expected_status="running", status="failed", ended_at="t2")
        self.assertEqual((store.get_run(rid) or {}).get("status"), "cancelled")

    def test_mgmt_missing_entry_cannot_clobber_cancelled(self):
        """管理任务初检后的取消不能被 catalog 缺项分支改成 failed。"""
        from app.core import jobs, store
        rid = store.create_run("mgmt", "缺失条目取消")["id"]
        store.update_run(rid, status="running")
        ev = jobs.cancel_event_for(rid)
        original_get = store.get_run
        reads = {"n": 0}

        def cancel_after_initial_check(run_id):
            reads["n"] += 1
            value = original_get(run_id)
            if reads["n"] == 1:
                store.update_run(rid, cancelled_by_user=True)
                jobs.cancel(rid)
            return value

        try:
            store.get_run = cancel_after_initial_check
            jobs._do_mgmt({"kind": "mgmt", "run_id": rid,
                           "entry_id": "missing-entry", "op": "upgrade"}, ev)
        finally:
            store.get_run = original_get
        self.assertEqual(store.get_run(rid)["status"], "cancelled")
