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

    def test_retry_failed_run_inherits_live_chapter_scores(self):
        """§07 补缺陷：failed run 没有 verdict，retry 应继承每章实时落账的分数。

        实测缺陷：inherit.chapter_scores 只取 verdict——failed/cancelled run
        永远没有 verdict，导致多轮失败恢复时全部已过线章节被重新评审
        （真实连载一晚白烧数百万 token）。
        """
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "t", "goal": "写连载",
            "workdir": str(self.workdir),
            "serial": {"chapters": 3, "words_per_chapter": 800},
        })
        prev = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(prev["id"], status="failed", ended_at="2026-09-15 00:00:00")
        store.update_run(prev["id"], outline={
            "book_title": "书", "source": "编排者",
            "chapters": [{"title": "第 1 章", "beats": "b", "hook": "h"}]})
        store.add_step(prev["id"], "draft-c1", "mock", "mock")
        store.finish_step(prev["id"], 1, "done", summary="x", duration_s=0.1)
        # failed run 没有 verdict，但每章分数实时落账在 run.chapter_scores
        store.update_run(prev["id"], chapter_scores=[
            {"chapter": 1, "title": "第 1 章",
             "means": {"情节": 8.0, "人物": 8.0}, "passed": True, "rounds": 1}])

        ok, err, nxt = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        scores = (nxt.get("inherit") or {}).get("chapter_scores") or []
        self.assertEqual([c.get("chapter") for c in scores], [1],
                         "failed run 的实时章节分数必须被继承")
        self.assertEqual(scores[0]["means"]["情节"], 8.0)


class TestResumeInterrupted(BaseTest):
    """启动恢复（jobs.resume_interrupted）——重点是 paused 尊重用户暂停意图。"""

    @staticmethod
    def _seed_serial_task(store, workdir, title):
        return store.create_task({
            "type": "serial_novel", "title": title, "goal": "写连载",
            "workdir": str(workdir),
            "serial": {"chapters": 3, "words_per_chapter": 800},
        })

    def test_skips_user_paused_run(self):
        """用户暂停 + 服务重启：收尸成 failed 后不得自动续跑。

        用户明确表达「停下」；重启替他拉起来违背意图——继续与否
        必须由用户点「继续任务」决定。
        """
        from app.core import store
        from app.core import jobs
        task = self._seed_serial_task(store, self.workdir, "paused serial")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running", paused=True)
        store.recover_orphaned_runs()   # 启动收尸：running → failed，paused 标志保留
        self.assertEqual(jobs.resume_interrupted(), 0)
        runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
        self.assertEqual(len(runs), 1, "暂停的任务重启后不得被自动续跑出新运行")

    def test_resumes_unpaused_interrupted_serial(self):
        """无暂停标志的中断连载仍自动续跑（既有行为回归）。"""
        from app.core import store
        from app.core import jobs
        task = self._seed_serial_task(store, self.workdir, "plain serial")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        store.recover_orphaned_runs()
        self.assertEqual(jobs.resume_interrupted(), 1)
        runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
        self.assertEqual(len(runs), 2, "普通中断的连载任务应被自动续跑")