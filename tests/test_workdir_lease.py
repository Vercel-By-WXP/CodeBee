# -*- coding: utf-8 -*-
"""工作目录原子认领租约（workdir_lease）回归。

核心语义（2026-11-01，借鉴 Cumora 原子工作认领）：
1. 独占创建 = 认领的原子性：第二个认领者拿不到就失败；
2. 持有者本人幂等重入（续心跳）；
3. 陈旧租约（心跳超时 / pid 已死）可被夺走；
4. force=True 人工逃生口：无视新鲜持有直接夺走；
5. 释放只删自己的租约；
6. pipeline._workdir_lease_gate：拿不到 → 与内存互斥同款排队回滚（blocked_by +
   resume_enqueue_at + waiter）；任何异常 fail-open 不挡主流程。

全部走临时目录，不碰真实数据目录。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import workdir_lease as lease


class LeaseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wd = os.path.join(self.tmp.name, "book-a")

    def lease_file(self):
        return Path(self.wd) / ".codebee" / "workdir.lease.json"


class AcquireTests(LeaseBase):
    def test_first_acquire_ok_and_file_written(self):
        res = lease.acquire(self.wd, "run-1", task_id="t-1")
        self.assertTrue(res["ok"])
        self.assertFalse(res.get("skipped"))
        data = json.loads(self.lease_file().read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "run-1")
        self.assertEqual(data["task_id"], "t-1")
        self.assertEqual(data["pid"], os.getpid())

    def test_second_run_blocked_by_fresh_holder(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        res = lease.acquire(self.wd, "run-2")
        self.assertFalse(res["ok"])
        self.assertEqual((res["holder"] or {}).get("run_id"), "run-1")

    def test_reacquire_same_run_idempotent(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        before = json.loads(self.lease_file().read_text(encoding="utf-8"))
        time.sleep(0.01)
        res = lease.acquire(self.wd, "run-1")
        self.assertTrue(res["ok"])
        after = json.loads(self.lease_file().read_text(encoding="utf-8"))
        self.assertGreaterEqual(after["heartbeat_at"], before["heartbeat_at"])

    def test_stale_heartbeat_can_be_stolen(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        data = json.loads(self.lease_file().read_text(encoding="utf-8"))
        data["heartbeat_at"] = time.time() - (lease.STALE_S + 10)
        self.lease_file().write_text(json.dumps(data), encoding="utf-8")
        res = lease.acquire(self.wd, "run-2")
        self.assertTrue(res["ok"])
        self.assertTrue(res["stale"])
        self.assertEqual(
            json.loads(self.lease_file().read_text(encoding="utf-8"))["run_id"],
            "run-2")

    def test_dead_pid_holder_can_be_stolen(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        data = json.loads(self.lease_file().read_text(encoding="utf-8"))
        data["pid"] = 999999999   # 不存在的 pid
        self.lease_file().write_text(json.dumps(data), encoding="utf-8")
        res = lease.acquire(self.wd, "run-2")
        self.assertTrue(res["ok"])
        self.assertTrue(res["stale"])

    def test_force_overrides_fresh_holder(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        res = lease.acquire(self.wd, "run-2", force=True)
        self.assertTrue(res["ok"])
        self.assertEqual(
            json.loads(self.lease_file().read_text(encoding="utf-8"))["run_id"],
            "run-2")

    def test_no_workdir_fails_open(self):
        res = lease.acquire("", "run-1")
        self.assertTrue(res["ok"])
        self.assertEqual(res["skipped"], "no_workdir")


class HeartbeatReleaseTests(LeaseBase):
    def test_heartbeat_only_for_holder(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        self.assertTrue(lease.heartbeat(self.wd, "run-1"))
        self.assertFalse(lease.heartbeat(self.wd, "run-2"))

    def test_release_removes_own_only(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        self.assertFalse(lease.release(self.wd, "run-2"))
        self.assertTrue(lease.release(self.wd, "run-1"))
        self.assertFalse(self.lease_file().exists())
        self.assertFalse(lease.release(self.wd, "run-1"))   # 已无租约

    def test_start_stop_heartbeat_thread(self):
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        stop = lease.start_heartbeat(self.wd, "run-1")
        self.assertIsNotNone(stop)
        self.assertIs(lease.start_heartbeat(self.wd, "run-1"), stop)   # 幂等
        hb0 = json.loads(self.lease_file().read_text(encoding="utf-8"))["heartbeat_at"]
        lease.stop_heartbeat("run-1")
        self.assertIsNotNone(hb0)

    def test_holder_view_stale_flags(self):
        self.assertIsNone(lease.holder_view(self.wd))
        self.assertTrue(lease.acquire(self.wd, "run-1")["ok"])
        view = lease.holder_view(self.wd)
        self.assertEqual(view["run_id"], "run-1")
        self.assertFalse(view["stale"])
        data = json.loads(self.lease_file().read_text(encoding="utf-8"))
        data["heartbeat_at"] = time.time() - (lease.STALE_S + 10)
        self.lease_file().write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(lease.holder_view(self.wd)["stale"])


class GateTests(unittest.TestCase):
    """pipeline._workdir_lease_gate 的放行 / 回滚 / fail-open 三态。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wd = os.path.join(self.tmp.name, "repo")
        from core import pipeline
        self.pipeline = pipeline

    def _fake_store(self, run=None):
        class FakeStore:
            def __init__(self):
                self.runs = {}
                self.task_status = {}
                self.waiters = []

            def get_run(self, rid):
                return self.runs.get(rid) or run or {}

            def update_run(self, rid, **fields):
                self.runs.setdefault(rid, {}).update(fields)
                return True

            def update_task_status(self, tid, status):
                self.task_status[tid] = status
                return True

            def get_task(self, tid):
                return {"id": tid, "workdir": self_wd, "title": "书"}

        self_wd = self.wd
        return FakeStore()

    def _patch(self, store):
        class FakeJobs:
            def __init__(self):
                self.waiters = []

            def register_workdir_waiter(self, wd, job, delay):
                self.waiters.append((wd, job, delay))

        jobs = FakeJobs()
        p1 = mock.patch.object(self.pipeline, "store", store)
        p2 = mock.patch.object(self.pipeline, "jobs", jobs)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return jobs

    def test_gate_ok_starts_heartbeat(self):
        store = self._fake_store(run={"id": "r1", "task_id": "t1"})
        self._patch(store)
        task = {"id": "t1", "workdir": self.wd, "title": "书"}
        self.assertTrue(self.pipeline._workdir_lease_gate(task, "r1"))
        lease_file = Path(self.wd) / ".codebee" / "workdir.lease.json"
        self.assertEqual(
            json.loads(lease_file.read_text(encoding="utf-8"))["run_id"], "r1")
        self.pipeline._workdir_lease_release("r1")
        self.assertFalse(lease_file.exists())

    def test_gate_blocked_rolls_back_to_queued(self):
        # 预置一个新鲜持有者
        self.assertTrue(lease.acquire(self.wd, "run-other", task_id="t-other")["ok"])
        store = self._fake_store(run={"id": "r2", "task_id": "t2"})
        jobs = self._patch(store)
        task = {"id": "t2", "workdir": self.wd, "title": "书二"}
        self.assertFalse(self.pipeline._workdir_lease_gate(task, "r2"))
        self.assertEqual(store.runs["r2"]["status"], "queued")
        self.assertEqual(store.runs["r2"]["blocked_by"], self.wd.rstrip("/\\"))
        self.assertTrue(store.runs["r2"]["resume_enqueue_at"])
        self.assertEqual(store.task_status.get("t2"), "queued")
        self.assertEqual(len(jobs.waiters), 1)
        lease.release(self.wd, "run-other")

    def test_gate_force_start_steals(self):
        self.assertTrue(lease.acquire(self.wd, "run-other")["ok"])
        store = self._fake_store(run={"id": "r3", "task_id": "t3",
                                      "force_start": True})
        self._patch(store)
        task = {"id": "t3", "workdir": self.wd, "title": "书三"}
        self.assertTrue(self.pipeline._workdir_lease_gate(task, "r3"))
        self.assertEqual(
            json.loads((Path(self.wd) / ".codebee" / "workdir.lease.json")
                       .read_text(encoding="utf-8"))["run_id"], "r3")
        self.pipeline._workdir_lease_release("r3")

    def test_gate_empty_workdir_passes(self):
        store = self._fake_store()
        self._patch(store)
        self.assertTrue(self.pipeline._workdir_lease_gate(
            {"id": "t", "workdir": ""}, "r"))

    def test_gate_fail_open_on_error(self):
        store = self._fake_store()
        self._patch(store)
        with mock.patch.object(lease, "acquire", side_effect=OSError("boom")):
            self.assertTrue(self.pipeline._workdir_lease_gate(
                {"id": "t", "workdir": self.wd}, "r"))


if __name__ == "__main__":
    unittest.main()
