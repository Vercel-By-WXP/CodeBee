# -*- coding: utf-8 -*-
"""直接调度 + 遗留排队恢复 + 状态同步回归。

锁定五个行为：
1. 新任务立即启动，不进入内存等待队列；并发满载时明确拒绝而非排队；
2. 巡检模式（max_age_s）：刚入队的正常排队不补，卡死超过阈值的补——
   看门狗每 60s 自愈一次「job 蒸发」型僵尸（r-20260918-211920 实案：
   排队 1 小时无人接手、进程不重启则永远没人管）；
3. 退避窗口内的续跑副本（resume_enqueue_at 未到点）启动补队/巡检都不补
   ——重启不该把 300s 网关退避烧掉；到点后的照常补；
4. 执行闸：run 已非 queued/running 的重复
   job 直接跳过——看门狗重排与原 job 并存时同一运行绝不执行两次；
5. run 起跑（status=running）同步任务状态：run 在跑、任务却永远显示
   「排队中」（「# 重写·续」卡 queued 实际已写一小时实案）；
6. 并发保护上限为 12，默认 12；达到上限时立即返回忙，不产生 queued 僵尸。
"""
from __future__ import annotations

import threading
import time as _time
import unittest
from unittest import mock

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

    def test_enqueue_starts_immediately_without_queue(self):
        """API 返回前 run 已进入 running；内存等待队列始终为空。"""
        from app.core import jobs, store, pipeline
        task = _mk_serial_task("立即启动")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        entered = threading.Event()
        release = threading.Event()

        def fake_execute(_run_id):
            entered.set()
            release.wait(3)

        jobs.configure(12)
        with mock.patch.object(pipeline, "execute_run", side_effect=fake_execute):
            jobs.enqueue({"kind": "orchestration", "run_id": run["id"],
                          "task_id": task["id"]})
            self.assertEqual(store.get_run(run["id"])["status"], "running")
            self.assertEqual(jobs._QUEUE.qsize(), 0)
            self.assertTrue(entered.wait(2), "任务应立即进入执行线程")
            release.set()
            self.assertTrue(jobs.wait_for_idle(3))

    def test_concurrency_full_rejects_instead_of_queueing(self):
        """满载时明确报忙，不把第二个任务留成一直排队。"""
        from app.core import jobs, store, pipeline
        t1 = _mk_serial_task("占满并发")
        t2 = _mk_serial_task("不得排队")
        r1 = store.create_run("orchestration", t1["title"], task_id=t1["id"])
        r2 = store.create_run("orchestration", t2["title"], task_id=t2["id"])
        entered = threading.Event()
        release = threading.Event()

        def fake_execute(_run_id):
            entered.set()
            release.wait(3)

        jobs.start_worker()
        jobs.configure(1)
        with mock.patch.object(pipeline, "execute_run", side_effect=fake_execute):
            jobs.enqueue({"kind": "orchestration", "run_id": r1["id"],
                          "task_id": t1["id"]})
            self.assertTrue(entered.wait(2))
            with self.assertRaises(jobs.DuplicateJobError):
                jobs.enqueue({"kind": "orchestration", "run_id": r1["id"],
                              "task_id": t1["id"]})
            self.assertEqual(store.get_run(r1["id"])["status"], "running",
                             "满载时重复请求也不能误伤正在运行的任务")
            with self.assertRaises(jobs.JobsBusyError):
                jobs.enqueue({"kind": "orchestration", "run_id": r2["id"],
                              "task_id": t2["id"]})
            self.assertEqual(jobs._QUEUE.qsize(), 0)
            self.assertEqual(store.get_run(r2["id"])["status"], "failed",
                             "满载拒绝必须立即收口，不留下 queued")
            release.set()
            self.assertTrue(jobs.wait_for_idle(3))
        jobs.configure(12)

    def test_concurrent_duplicate_claim_never_fails_owner(self):
        """同一 run 两个启动请求并发到达：一个执行，一个判重复，不得判满载。"""
        from app.core import jobs, store, pipeline
        task = _mk_serial_task("并发认领")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        callers_ready = threading.Barrier(2)
        release = threading.Event()
        results = []
        orig_get = store.get_run
        gated_threads = set()
        gated_lock = threading.Lock()

        def gated_get(run_id):
            value = orig_get(run_id)
            if threading.current_thread().name.startswith("claim-"):
                name = threading.current_thread().name
                with gated_lock:
                    first_read = name not in gated_threads
                    gated_threads.add(name)
                if first_read:
                    callers_ready.wait(2)
            return value

        def fake_execute(_run_id):
            release.wait(3)

        def launch():
            try:
                jobs.enqueue({"kind": "orchestration", "run_id": run["id"],
                              "task_id": task["id"]})
                results.append("started")
            except Exception as exc:
                results.append(type(exc).__name__)

        jobs.start_worker()
        jobs.configure(1)
        with mock.patch.object(store, "get_run", side_effect=gated_get), \
             mock.patch.object(pipeline, "execute_run", side_effect=fake_execute):
            ts = [threading.Thread(target=launch, name="claim-%d" % i) for i in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(3)
            self.assertCountEqual(results, ["started", "DuplicateJobError"])
            self.assertEqual(orig_get(run["id"])["status"], "running")
            release.set()
            self.assertTrue(jobs.wait_for_idle(3))
        jobs.configure(12)

    def test_cancelled_management_jobs_do_not_restart(self):
        """取消发生在执行线程调度边界时，管理/自升级操作都不得复活。"""
        from app.core import jobs, store, manager, selfupdate
        ev = threading.Event()
        ev.set()
        mgmt = store.create_run("mgmt", "升级 codex", entry_id="codex", op="upgrade")
        store.update_run(mgmt["id"], status="cancelled")
        with mock.patch.object(manager, "run_mgmt_command") as run_cmd:
            jobs._do_mgmt({"kind": "mgmt", "run_id": mgmt["id"],
                           "entry_id": "codex", "op": "upgrade"}, ev)
        run_cmd.assert_not_called()
        self.assertEqual(store.get_run(mgmt["id"])["status"], "cancelled")

        upgrade = store.create_run("mgmt", "升级 CodeBee", entry_id="__self__",
                                   op="selfupgrade")
        store.update_run(upgrade["id"], status="cancelled")
        with mock.patch.object(selfupdate, "run_upgrade") as run_upgrade:
            jobs._do_selfupgrade({"kind": "selfupgrade", "run_id": upgrade["id"]}, ev)
        run_upgrade.assert_not_called()
        self.assertEqual(store.get_run(upgrade["id"])["status"], "cancelled")

    def test_cancel_without_registered_event_still_closes_running_run(self):
        """取消撞在 running 已落盘、CANCELS 尚未登记的窗口也不能丢失。"""
        from app.core import jobs, store
        task = _mk_serial_task("取消竞态")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        jobs.CANCELS.pop(run["id"], None)
        self.assertTrue(jobs.cancel(run["id"]))
        closed = store.get_run(run["id"])
        self.assertEqual(closed["status"], "cancelled")
        self.assertTrue(closed.get("cancelled_by_user"))

    def test_restore_deferred_resume_rebuilds_timer(self):
        """重启后未来时刻的自动续跑会重建 Timer，无需等新任务唤醒。"""
        from app.core import jobs, store
        task = _mk_serial_task("恢复退避")
        future_ts = _time.time() + 180
        future = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(future_ts))
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], resume_enqueue_at=future)
        captured = {}

        class FakeTimer:
            daemon = False

            def __init__(self, delay, fn):
                captured["delay"] = delay
                captured["fn"] = fn

            def start(self):
                captured["started"] = True

            def cancel(self):
                pass

        with mock.patch.object(jobs.threading, "Timer", FakeTimer):
            self.assertEqual(jobs.restore_deferred_resumes(now=_time.time()), 1)
        self.assertTrue(captured.get("started"))
        self.assertGreater(captured.get("delay", 0), 100)

    def test_load_all_preserves_scheduled_resume(self):
        """重启读盘不得把有明确续跑时刻的 queued 误判为中断失败。"""
        from app.core import jobs, store
        task = _mk_serial_task("读盘恢复退避")
        future = _time.strftime("%Y-%m-%d %H:%M:%S",
                                _time.localtime(_time.time() + 180))
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], resume_enqueue_at=future)
        store._RUNS.clear()
        store._TASKS.clear()
        store.load_all()
        restored = store.get_run(run["id"])
        self.assertEqual(restored["status"], "queued")
        self.assertEqual(restored["resume_enqueue_at"], future)
        with mock.patch.object(jobs, "_schedule_enqueue", return_value=True) as schedule:
            self.assertEqual(jobs.restore_deferred_resumes(now=_time.time()), 1)
        self.assertEqual(schedule.call_count, 1)

    def test_restore_deferred_resumes_has_no_hidden_count_cap(self):
        """重启必须恢复全部定时续跑，超过旧上限 20 个也不能永久待启动。"""
        from app.core import jobs, store
        future = _time.strftime("%Y-%m-%d %H:%M:%S",
                                _time.localtime(_time.time() + 180))
        for i in range(25):
            task = _mk_serial_task("批量恢复退避-%02d" % i)
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            store.update_run(run["id"], resume_enqueue_at=future)
        with mock.patch.object(jobs, "_schedule_enqueue", return_value=True) as schedule:
            self.assertEqual(jobs.restore_deferred_resumes(now=_time.time()), 25)
        self.assertEqual(schedule.call_count, 25)

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
        captured = []
        with mock.patch.object(jobs, "enqueue", side_effect=captured.append):
            self.assertEqual(jobs.requeue_pending(max_age_s=120), 1, "超龄僵尸必须补")
        item = captured[0]
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
        captured = []
        with mock.patch.object(jobs, "enqueue", side_effect=captured.append):
            self.assertEqual(jobs.requeue_pending(), 1, "退避已到点的照常补")
        item = captured[0]
        self.assertEqual(item["run_id"], due["id"])

    def test_enqueue_rejects_duplicate_running_run(self):
        """同一 run 已 running 时不得再次启动，绝不双跑。"""
        from app.core import jobs, store
        task = _mk_serial_task("重复副本")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        with self.assertRaises(jobs.DuplicateJobError):
            jobs.enqueue({"kind": "orchestration", "run_id": run["id"],
                          "task_id": task["id"]})

    def test_requeue_mgmt_stale_backfills(self):
        """mgmt（CLI 安装/升级）超龄 queued 同样补队，job 带 entry_id/op
        （2026-09-19 三连升级排队无人接案：worker 起失败/入队丢失后，
        看门狗此前只认 orchestration，mgmt 永远「排队中」还堵去重闸）。"""
        from app.core import store, jobs
        run = store.create_run("mgmt", "升级 codex", entry_id="codex", op="upgrade")
        store.update_run(run["id"], status="queued",
                         created_at=_time.strftime(
                             "%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 600)))
        captured = []
        with mock.patch.object(jobs, "enqueue", side_effect=captured.append):
            self.assertEqual(jobs.requeue_pending(max_age_s=120), 1, "mgmt 超龄僵尸必须补")
        item = captured[0]
        self.assertEqual(item["run_id"], run["id"])
        self.assertEqual(item["kind"], "mgmt")
        self.assertEqual(item["entry_id"], "codex")
        self.assertEqual(item["op"], "upgrade")

    def test_requeue_mgmt_skips_entry_with_active_run(self):
        """同条目已有 running 的 mgmt 时，另一个 queued 副本不补（去重闸语义）。"""
        from app.core import store, jobs
        live = store.create_run("mgmt", "升级 codex（在跑）", entry_id="codex", op="upgrade")
        store.update_run(live["id"], status="running")
        dup = store.create_run("mgmt", "升级 codex（排队副本）", entry_id="codex", op="upgrade")
        store.update_run(dup["id"], status="queued",
                         created_at=_time.strftime(
                             "%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 600)))
        self.assertEqual(jobs.requeue_pending(max_age_s=120), 0,
                         "同条目已有活跃 run 的排队副本不得补")

    def test_update_run_running_backfills_task(self):
        """run 起跑同步任务状态：任务不再永远显示排队中。"""
        from app.core import store
        task = _mk_serial_task("状态同步")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        store.update_run(run["id"], status="running")
        self.assertEqual(store.get_task(task["id"])["status"], "running",
                         "run 置 running 时任务必须跟 running")

    def test_terminal_run_reaps_running_steps(self):
        """run 落终态时还挂 running 的步骤统一收尸为 failed
        （打磨组长被取消/异常打断残留假 running 的通用防线）。"""
        from app.core import store
        task = _mk_serial_task("终态收尸")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        s1, _ = store.add_step(run["id"], "polish-r1", "a", "A")
        s2, _ = store.add_step(run["id"], "polish-c1", "a", "A")
        store.finish_step(run["id"], s2["n"], "done", summary="正常收尾")
        store.update_run(run["id"], status="running")   # running 期不收尸
        cur = store.get_run(run["id"])
        st1 = next(x for x in cur["steps"] if x["n"] == s1["n"])
        self.assertEqual(st1["status"], "running", "running 期不得误收")
        store.update_run(run["id"], status="cancelled")
        cur = store.get_run(run["id"])
        st1 = next(x for x in cur["steps"] if x["n"] == s1["n"])
        st2 = next(x for x in cur["steps"] if x["n"] == s2["n"])
        self.assertEqual(st1["status"], "cancelled",
                         "终态后残留 running 必须收尸（与启动清扫语义对齐）")
        self.assertTrue(st1.get("ended_at"))
        self.assertIn("未正常收尾", st1.get("summary") or "")
        self.assertEqual(st2["status"], "done", "已收尾的步骤不得被覆盖")

    def test_concurrency_clamp_and_default(self):
        """并发保护钳到 12；新环境默认 12；显式配置仍按用户值生效。"""
        from app.core import jobs, settings
        self.assertEqual(jobs.configure(99), 12)
        self.assertEqual(jobs.configure(1), 1)
        fresh = settings.DEFAULTS["max_concurrent_jobs"]
        self.assertEqual(fresh, 12)
        import json
        settings._FILE.write_text(
            json.dumps({"max_concurrent_jobs": 3}), encoding="utf-8")
        self.assertEqual(settings.load()["max_concurrent_jobs"], 3,
                         "存量配置不强迁，用户在设置页自行调")


if __name__ == "__main__":
    unittest.main()
