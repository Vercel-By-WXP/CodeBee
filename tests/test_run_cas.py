# -*- coding: utf-8 -*-
"""store.update_run CAS（2C 最小化）测试。
设计稿：docs/migration/03-state-externalization.md §2C（按 Tutti 实际架构最小化）。
"""
from __future__ import annotations

import threading
from base import BaseTest


class TestUpdateRunCAS(BaseTest):

    def test_no_expected_status_backward_compatible(self):
        """不传 expected_status → 原行为（无条件写）。"""
        from app.core import store
        run = store.create_run("orchestration", "t")
        r = store.update_run(run["id"], status="running")
        self.assertEqual(r["status"], "running")
        # 状态已经变了，仍然可以无条件改
        r2 = store.update_run(run["id"], title="改了")
        self.assertEqual(r2["title"], "改了")

    def test_cas_match_writes(self):
        from app.core import store
        run = store.create_run("orchestration", "t")
        store.update_run(run["id"], status="running")
        r = store.update_run(run["id"], expected_status="running",
                             status="done", ended_at="2026-09-14 00:00:00")
        self.assertIsNotNone(r)
        self.assertEqual(r["status"], "done")

    def test_cas_mismatch_rejects(self):
        """当前状态与期望不符 → 返回 None，不写入。"""
        from app.core import store
        run = store.create_run("orchestration", "t")
        store.update_run(run["id"], status="running")
        # 用户已取消（cancelled），陈旧 worker 还以为在 running
        store.update_run(run["id"], status="cancelled", ended_at="t")
        r = store.update_run(run["id"], expected_status="running",
                             status="done", verdict={"ok": True})
        self.assertIsNone(r)
        # 被拒绝的写入没有落地
        self.assertEqual(store.get_run(run["id"])["status"], "cancelled")
        self.assertEqual(store.get_run(run["id"]).get("verdict"), None)

    def test_cas_nonexistent_run(self):
        from app.core import store
        self.assertIsNone(store.update_run("r-nonexistent", expected_status="running",
                                           status="done"))

    def test_cas_task_backfill_still_works_on_match(self):
        """CAS 命中时终态照常回填 task。"""
        from app.core import store
        run = store.create_run("orchestration", "t", task_id="t-cas")
        store.update_run(run["id"], status="running")
        store._TASKS["t-cas"] = {"id": "t-cas", "status": "running"}
        store.update_run(run["id"], expected_status="running", status="failed")
        self.assertEqual(store._TASKS["t-cas"]["status"], "failed")

    def test_cas_task_backfill_skipped_on_mismatch(self):
        """CAS 拒绝时 task 不被回填。"""
        from app.core import store
        run = store.create_run("orchestration", "t", task_id="t-cas2")
        store.update_run(run["id"], status="cancelled")
        store._TASKS["t-cas2"] = {"id": "t-cas2", "status": "cancelled"}
        store.update_run(run["id"], expected_status="running", status="done")
        self.assertEqual(store._TASKS["t-cas2"]["status"], "cancelled")

    def test_concurrent_cas_one_winner(self):
        """两个 worker 抢写终态：CAS 保证只有一个成功。"""
        from app.core import store
        run = store.create_run("orchestration", "t")
        store.update_run(run["id"], status="running")
        results = []

        def worker(final):
            r = store.update_run(run["id"], expected_status="running",
                                 status=final)
            results.append((final, r is not None))

        threads = [threading.Thread(target=worker, args=("done",)),
                   threading.Thread(target=worker, args=("failed",))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        winners = [f for f, ok in results if ok]
        self.assertEqual(len(winners), 1)
        self.assertEqual(store.get_run(run["id"])["status"], winners[0])


class TestRecoverUsesCAS(BaseTest):
    """recover_orphaned_runs 与 CAS 协同：恢复后重放不重复标记。"""

    def test_recover_then_cas_replay_safe(self):
        from app.core import store
        run = store.create_run("orchestration", "orphan")
        store.update_run(run["id"], status="running")
        self.assertEqual(store.recover_orphaned_runs(), 1)
        # 误重放：expected running 已不成立 → 拒绝
        r = store.update_run(run["id"], expected_status="running",
                             status="failed", ended_at="x")
        self.assertIsNone(r)
        self.assertEqual(store.get_run(run["id"])["status"], "failed")


class TestStateVersionBump(BaseTest):
    """run 生命周期必须唤醒 SSE（bump_state）。SSE 存活的前端不轮询
    /api/state，全靠版本号变化收推送：run 状态变化不 bump，侧栏
    task_latest 就冻在旧快照——详情页已「完成」、侧栏还转「在跑」
    （2026-09-22 第二十九班实案，空闲时可无限期不同步）。"""

    def test_create_and_update_bump(self):
        from app.core import store
        ver0 = store.state_version()
        run = store.create_run("orchestration", "t")
        self.assertGreater(store.state_version(), ver0)
        ver1 = store.state_version()
        self.assertTrue(store.update_run(run["id"], status="running"))
        self.assertGreater(store.state_version(), ver1)

    def test_terminal_bump_and_zombie_step_finalize(self):
        """落终态必然 bump；挂着的 running 步骤同一次写入里被收尸。"""
        from app.core import store
        run = store.create_run("orchestration", "t")
        store.update_run(run["id"], status="running", steps=[
            {"role": "outline", "status": "done"},
            {"role": "draft", "status": "running"},
        ])
        ver = store.state_version()
        self.assertTrue(store.update_run(run["id"], status="done"))
        self.assertGreater(store.state_version(), ver)
        steps = store.get_run(run["id"])["steps"]
        self.assertEqual(steps[1]["status"], "cancelled")

    def test_rejected_or_missing_write_no_bump(self):
        """没写进去就不算状态变化：CAS 拒绝 / run 不存在都不 bump，
        否则陈旧 worker 的无效重试会打成推送风暴。"""
        from app.core import store
        run = store.create_run("orchestration", "t")
        store.update_run(run["id"], status="running")
        ver = store.state_version()
        self.assertIsNone(store.update_run(run["id"], expected_status="queued",
                                           status="done"))
        self.assertIsNone(store.update_run("r-nonexistent-0000", status="done"))
        self.assertEqual(store.state_version(), ver)