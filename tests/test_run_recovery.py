# -*- coding: utf-8 -*-
"""crash-recovery 测试。
设计稿：docs/migration/01-defense-patterns.md §1E。
"""
from __future__ import annotations

from base import BaseTest


class TestRecoverOrphanedRuns(BaseTest):

    def test_no_op_when_no_orphans(self):
        """没有 status='running' 的 run → 返回 0。"""
        from app.core import store
        # 加一个已完成 run
        run = store.create_run(kind="orchestration", title="done task")
        store.update_run(run["id"], status="done", ended_at="2026-01-01 00:00:00")
        # 加一个 queued（未启动）
        run2 = store.create_run(kind="orchestration", title="queued task")
        self.assertEqual(store.recover_orphaned_runs(), 0)

    def test_recovers_running_orphan(self):
        """status='running' 但 ended_at=None → 标记为 failed。"""
        from app.core import store
        run = store.create_run(kind="orchestration", title="crashed task")
        store.update_run(run["id"], status="running", started_at="2026-01-01 00:00:00")
        # ended_at 仍是 None
        self.assertIsNone(store.get_run(run["id"]).get("ended_at"))

        recovered = store.recover_orphaned_runs()
        self.assertEqual(recovered, 1)

        r = store.get_run(run["id"])
        self.assertEqual(r["status"], "failed")
        self.assertIsNotNone(r["ended_at"])
        self.assertIn("interrupted", r["error"])

    def test_keeps_running_with_ended_at(self):
        """status='running' 即使已有 ended_at（异常数据），仍恢复——更安全。"""
        from app.core import store
        run = store.create_run(kind="orchestration", title="weird task")
        store.update_run(run["id"], status="running", started_at="t1", ended_at="t2")
        recovered = store.recover_orphaned_runs()
        self.assertEqual(recovered, 1)

    def test_keeps_done_run(self):
        """status='done' → 不恢复。"""
        from app.core import store
        run = store.create_run(kind="orchestration", title="done task")
        store.update_run(run["id"], status="done", ended_at="t1")
        recovered = store.recover_orphaned_runs()
        self.assertEqual(recovered, 0)

    def test_keeps_queued_run(self):
        """status='queued' → 不恢复。"""
        from app.core import store
        run = store.create_run(kind="orchestration", title="queued task")
        recovered = store.recover_orphaned_runs()
        self.assertEqual(recovered, 0)

    def test_recovers_multiple_orphans(self):
        from app.core import store
        r1 = store.create_run(kind="orchestration", title="orphan1")
        store.update_run(r1["id"], status="running")
        r2 = store.create_run(kind="orchestration", title="orphan2")
        store.update_run(r2["id"], status="running")
        r3 = store.create_run(kind="orchestration", title="done3")
        store.update_run(r3["id"], status="done", ended_at="t1")

        recovered = store.recover_orphaned_runs()
        self.assertEqual(recovered, 2)

    def test_recovers_orphan_with_task_id_no_crash(self):
        """run 关联到不存在的 task_id（孤儿 task）也能正常恢复，不抛异常。"""
        from app.core import store
        run = store.create_run(kind="orchestration", title="orphan",
                               task_id="t-nonexistent")
        store.update_run(run["id"], status="running")
        # 不应抛 KeyError
        recovered = store.recover_orphaned_runs()
        self.assertEqual(recovered, 1)
        r = store.get_run(run["id"])
        self.assertEqual(r["status"], "failed")

    def test_persistence_to_disk(self):
        """恢复后，状态会落盘到 run.json。"""
        from app.core import store
        from app.core import paths
        import json
        run = store.create_run(kind="orchestration", title="orphan")
        store.update_run(run["id"], status="running")
        store.recover_orphaned_runs()

        run_json_path = paths.RUNS_DIR / run["id"] / "run.json"
        data = json.loads(run_json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed")
        self.assertIsNotNone(data["ended_at"])
        self.assertIn("interrupted", data["error"])