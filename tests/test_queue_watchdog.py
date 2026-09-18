# -*- coding: utf-8 -*-
"""队列看门狗 + 状态同步回归（2026-09-18 排队僵尸/假排队双案）。

锁定五个行为：
1. 巡检模式（max_age_s）：刚入队的正常排队不补，卡死超过阈值的补——
   看门狗每 60s 自愈一次「job 蒸发」型僵尸（r-20260918-211920 实案：
   排队 1 小时无人接手、进程不重启则永远没人管）；
2. 退避窗口内的续跑副本（resume_enqueue_at 未到点）启动补队/巡检都不补
   ——重启不该把 300s 网关退避烧掉；到点后的照常补；
3. worker 出队闸：run 已非 queued（running/done/failed/cancelled）的重复
   job 直接跳过——看门狗重排与原 job 并存时同一运行绝不执行两次；
4. run 起跑（status=running）同步任务状态：run 在跑、任务却永远显示
   「排队中」（「# 重写·续」卡 queued 实际已写一小时实案）；
5. 并发上限放开：configure 钳到 12，settings 默认 6（存量 3 不强迁）。
"""
from __future__ import annotations

import threading
import time as _time
import unittest

from base import BaseTest


def _mk_serial_task(label="看门狗"):
    from app.core import store
    return store.create_task({
        "type": "serial_novel", "title": label, "goal": "写一章",
        "workdir": "", "serial": {"chapters": 2, "words_per_chapter": 100,
                                  "start_chapter": 1}})


class QueueWatchdogTest(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import jobs
        jobs._drain_test_queue()

    def tearDown(self):
        from app.core import jobs
        jobs._drain_test_queue()
        super().tearDown()

    def test_requeue_watchdog_mode_skips_fresh(self):
        """巡检模式只补卡死超阈值的：刚建的 queued 不动。"""
        from app.core import store, jobs
        task = _mk_serial_task("巡检新队")
        fresh = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(fresh["id"], status="queued")
        self.assertEqual(jobs.requeue_pending(max_age_s=120), 0,
                         "刚入队的正常排队不掺和巡检")

    def test_requeue_watchdog_mode_picks_stale(self):
        """卡死超阈值的 queued 补队（created_at 拨回 10 分钟前模拟）。"""
        from app.core import store, jobs
        task = _mk_serial_task("巡检僵尸")
        stale = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(stale["id"], status="queued",
                         created_at=_time.strftime(
                             "%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 600)))
        self.assertEqual(jobs.requeue_pending(max_age_s=120), 1, "超龄僵尸必须补")
        item = jobs._QUEUE.get_nowait()
        jobs._QUEUE.task_done()   # 配对结清，否则残留计数堵死后面的 join()
        self.assertEqual(item["run_id"], stale["id"])

    def test_requeue_skips_resume_backoff_window(self):
        """退避窗口内的续跑副本不补（启动模式也不补）；到点后照补。"""
        from app.core import store, jobs
        task = _mk_serial_task("退避窗口")
        future = _time.strftime("%Y-%m-%d %H:%M:%S",
                                _time.localtime(_time.time() + 200))
        past = _time.strftime("%Y-%m-%d %H:%M:%S",
                              _time.localtime(_time.time() - 400))
        waiting = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(waiting["id"], status="queued", resume_enqueue_at=future)
        self.assertEqual(jobs.requeue_pending(), 0,
                         "退避未到点的副本：Timer 自会入队，补队不得提前")
        task2 = _mk_serial_task("退避到点")
        due = store.create_run("orchestration", task2["title"], task_id=task2["id"])
        store.update_run(due["id"], status="queued", resume_enqueue_at=past,
                         created_at=past)
        self.assertEqual(jobs.requeue_pending(), 1, "退避已到点的照常补")
        item = jobs._QUEUE.get_nowait()
        jobs._QUEUE.task_done()   # 配对结清，否则残留计数堵死后面的 join()
        self.assertEqual(item["run_id"], due["id"])

    def test_worker_skips_non_queued_duplicate_job(self):
        """出队闸：run 已 running 的重复 job 不执行（看门狗重排与原 job 并存）。"""
        from app.core import jobs
        from app.core import pipeline
        task = _mk_serial_task("重复副本")
        from app.core import store
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")   # 原 job 正在跑
        jobs._QUEUE.put({"kind": "orchestration", "run_id": run["id"],
                         "task_id": task["id"]})
        calls = []
        orig = pipeline.execute_run
        pipeline.execute_run = lambda rid: calls.append(rid)
        try:
            w = threading.Thread(target=jobs._worker, daemon=True)
            w.start()
            jobs._QUEUE.join()      # 等出队收口（跳过路径也 task_done）
            for _ in range(200):    # join 无超时版在 py3.8 没有，双保险轮询
                if jobs._QUEUE.qsize() == 0 and not calls:
                    break
                _time.sleep(0.02)
        finally:
            pipeline.execute_run = orig
        self.assertEqual(calls, [], "running 的重复 job 必须被跳过，绝不双跑")

    def test_update_run_running_backfills_task(self):
        """run 起跑同步任务状态：任务不再永远显示排队中。"""
        from app.core import store
        task = _mk_serial_task("状态同步")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        store.update_run(run["id"], status="running")
        self.assertEqual(store.get_task(task["id"])["status"], "running",
                         "run 置 running 时任务必须跟 running")

    def test_concurrency_clamp_and_default(self):
        """并发钳制上限 12；新环境默认 6；存量 3 读出仍是 3（不强迁）。"""
        from app.core import jobs, settings
        self.assertEqual(jobs.configure(99), 12)
        self.assertEqual(jobs.configure(1), 1)
        fresh = settings.DEFAULTS["max_concurrent_jobs"]
        self.assertEqual(fresh, 6)
        import json
        settings._FILE.write_text(
            json.dumps({"max_concurrent_jobs": 3}), encoding="utf-8")
        self.assertEqual(settings.load()["max_concurrent_jobs"], 3,
                         "存量配置不强迁，用户在设置页自行调")


if __name__ == "__main__":
    unittest.main()
