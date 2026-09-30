# -*- coding: utf-8 -*-
"""立即重试（跳过自动续跑退避窗口）单元测试。

覆盖 jobs.enqueue_now 的三条守卫分支与主路径：摘到点 Timer、清
resume_enqueue_at、立即入队起跑（pipeline.execute_run 打桩，不碰真实执行）。
"""
from __future__ import annotations

import time
import unittest

from base import BaseTest


def _future(hours_ahead=False):
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() + (3600 if hours_ahead else 300)))


class TestResumeNow(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import jobs
        # 离场清 Timer：断言失败路径上遗留的 300s 定时器不能在后续用例里
        # 醒过来真跑 pipeline（daemon 线程会活到进程结束）。
        self._jobs = jobs
        with jobs._timer_lock:
            self._leftover = dict(jobs._deferred_timers)
            jobs._deferred_timers.clear()
        for t in self._leftover.values():
            t.cancel()

    def tearDown(self):
        with self._jobs._timer_lock:
            timers = list(self._jobs._deferred_timers.values())
            self._jobs._deferred_timers.clear()
        for t in timers:
            t.cancel()
        super().tearDown()

    def _seed_backoff_copy(self, task_id, title):
        """造出与 _maybe_auto_resume 落盘后一致的退避副本（queued+退避时刻）。"""
        from app.core import store
        failed = store.create_run("orchestration", title, task_id=task_id)
        store.update_run(failed["id"], status="failed",
                         error="网关限流", ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        copy = store.create_run("orchestration", title, task_id=task_id)
        store.update_run(copy["id"], auto_resumes=1, auto_resumed_from=failed["id"],
                         resume_enqueue_at=_future())
        return copy

    def test_guards(self):
        from app.core import jobs, store
        # 运行不存在
        ok, err = jobs.enqueue_now("r-not-exist")
        self.assertFalse(ok)
        self.assertIn("不存在", err)
        # 已终态：不在排队等待
        task = store.create_task({"type": "serial_novel", "goal": "守村人", "workdir": str(self.workdir),
                                  "serial": {"chapters": 2}})
        failed = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(failed["id"], status="failed", error="x",
                         ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        ok, err = jobs.enqueue_now(failed["id"])
        self.assertFalse(ok)
        self.assertIn("排队", err)
        # queued 但没挂退避时刻：普通并发排队不走这个入口
        plain = store.create_run("orchestration", task["title"], task_id=task["id"])
        ok, err = jobs.enqueue_now(plain["id"])
        self.assertFalse(ok)
        self.assertIn("自动续跑等待", err)

    def test_enqueue_now_starts_immediately(self):
        from app.core import jobs, pipeline, store
        task = store.create_task({"type": "serial_novel", "goal": "不周山", "workdir": str(self.workdir),
                                  "serial": {"chapters": 3}})
        copy = self._seed_backoff_copy(task["id"], task["title"])
        # 挂上真实的到点 Timer（同 _maybe_auto_resume 的排程方式）
        job = {"kind": "orchestration", "run_id": copy["id"], "task_id": task["id"]}
        self.assertTrue(jobs._schedule_enqueue(dict(job), 300))
        self.assertIn(copy["id"], jobs._deferred_timers)
        # 打桩执行体：起跑即落终态——防真实执行，也防 finally 的收尸分支把
        # run 判 failed 后又触发 _maybe_auto_resume 再造副本
        orig = pipeline.execute_run
        pipeline.execute_run = lambda rid: store.update_run(
            rid, status="done", ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            ok, err = jobs.enqueue_now(copy["id"])
            self.assertTrue(ok, err)
            deadline = time.time() + 10
            while time.time() < deadline:
                if (store.get_run(copy["id"]) or {}).get("status") == "done":
                    break
                time.sleep(0.05)
            run = store.get_run(copy["id"])
            self.assertEqual(run.get("status"), "done", "立即重试未把副本跑起来")
            self.assertEqual(run.get("resume_enqueue_at"), "", "退避标记未清空")
            self.assertNotIn(copy["id"], jobs._deferred_timers, "到点 Timer 未摘除")
        finally:
            pipeline.execute_run = orig

    def test_enqueue_now_busy_goes_to_slot_queue(self):
        """满载时转排队等待空位：退避标记已清，按普通排队记录对待。"""
        from app.core import jobs, pipeline, store
        task = store.create_task({"type": "serial_novel", "goal": "满载", "workdir": str(self.workdir),
                                  "serial": {"chapters": 1}})
        copy = self._seed_backoff_copy(task["id"], task["title"])
        orig_exec = pipeline.execute_run

        def slow_run(rid):
            time.sleep(1.2)
            store.update_run(rid, status="done", ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))

        jobs.start_worker()          # 先起 worker（configure 会重设 _target），再占死并发位
        orig_target = jobs._target
        orig_wait = jobs.BUSY_WAIT_S
        jobs._target = 0             # enqueue 必走 _requeue_await_slot
        jobs.BUSY_WAIT_S = 0.2       # 补跑拍提速（模块注释明说测试可调小）
        pipeline.execute_run = slow_run
        try:
            ok, err = jobs.enqueue_now(copy["id"])
            self.assertTrue(ok, err)
            run = store.get_run(copy["id"])
            self.assertEqual(run.get("status"), "queued", "满载应回滚排队")
            self.assertEqual(run.get("resume_enqueue_at"), "", "转排队后不该再带退避标记")
            jobs._target = orig_target   # 先还位，补跑 Timer 下一拍才起得来
            jobs.BUSY_WAIT_S = orig_wait
            # 等补跑 Timer 接管起跑并跑完打桩体。桩必须保持到跑完——提前还原
            # 会让 0.2s 后的补跑起跑真 pipeline（实测挂 15s 假失败）。
            deadline = time.time() + 15
            while time.time() < deadline:
                if (store.get_run(copy["id"]) or {}).get("status") == "done":
                    break
                time.sleep(0.1)
            self.assertEqual((store.get_run(copy["id"]) or {}).get("status"), "done")
        finally:
            jobs._target = orig_target
            jobs.BUSY_WAIT_S = orig_wait
            pipeline.execute_run = orig_exec


if __name__ == "__main__":
    unittest.main()
