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


class TestAutoResumeBackoff(BaseTest):
    """自动续跑退避窗口要可观测：重排出的新 run 记录预定入队时刻。

    失败后延迟 AUTO_RESUME_DELAY_S 秒才真正入队（防网关限流撞墙），期间
    run 以 queued 状态干等——前端靠 resume_enqueue_at 显示「将于 HH:MM
    自动续跑」，而不是笼统的排队中（2026-09-18 重写任务误判案）。
    """

    def _seed_failed_serial_run(self, store, title, auto_resumes=0):
        task = store.create_task({
            "type": "serial_novel", "title": title, "goal": "写连载",
            "workdir": str(self.workdir),
            "serial": {"chapters": 3, "words_per_chapter": 800},
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="failed",
                         error="评审全部失败", auto_resumes=auto_resumes)
        return task, run

    def test_failed_serial_run_records_resume_enqueue_at(self):
        import time as _t
        from app.core import jobs, store
        task, run = self._seed_failed_serial_run(store, "backoff book")
        before = _t.time()
        captured = {}
        orig_timer = jobs.threading.Timer

        def fake_timer(interval, fn):
            captured["interval"] = interval
            class _T:                      # 不真起线程：退避到期行为不属本用例
                daemon = False
                def start(self):
                    pass
            return _T()

        jobs.threading.Timer = fake_timer
        try:
            self.assertTrue(jobs._maybe_auto_resume(run["id"]))
        finally:
            jobs.threading.Timer = orig_timer
        runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
        self.assertEqual(len(runs), 2)
        new = next(r for r in runs if r["id"] != run["id"])
        self.assertEqual(new["status"], "queued")
        self.assertEqual(new["auto_resumes"], 1)
        self.assertEqual(new["auto_resumed_from"], run["id"])
        self.assertTrue(new.get("resume_enqueue_at"), "必须记录预定入队时刻")
        ts = _t.mktime(_t.strptime(new["resume_enqueue_at"], "%Y-%m-%d %H:%M:%S"))
        self.assertTrue(before + jobs.AUTO_RESUME_DELAY_S - 5 <= ts
                        <= _t.time() + jobs.AUTO_RESUME_DELAY_S + 5)
        self.assertEqual(captured.get("interval"), jobs.AUTO_RESUME_DELAY_S)
        self.assertEqual(jobs._QUEUE.qsize(), 0, "退避窗口内任务不得提前入队")

    def test_no_resume_after_max_reached(self):
        from app.core import jobs, store
        task, run = self._seed_failed_serial_run(
            store, "maxed book", auto_resumes=jobs.AUTO_RESUME_MAX)
        self.assertFalse(jobs._maybe_auto_resume(run["id"]))

    # 同因连撞的两组错误：章号/引用号/数字不同，病根文本相同（kimi 欠费、
    # opencode UnknownError 这类死墙每次撞都长一样，只是易变片段在换皮）
    _ERR_A = ('第 12 章起草失败: 退出码 1；stderr/stdout: Error: {"name": "UnknownError", '
              '"data": {"message": "Unexpected server error. Check server logs for details.", '
              '"ref": "err_b20f42d6"}}')
    _ERR_A_RELABELED = ('第 44 章起草失败: 退出码 1；stderr/stdout: Error: {"name": "UnknownError", '
                        '"data": {"message": "Unexpected server error. Check server logs for details.", '
                        '"ref": "err_ffff9999"}}')
    _ERR_B = '第 44 章起草失败: 退出码 1；provider.auth_error: 403 no valid authorization'

    def test_same_cause_stops_resume_early(self):
        """续跑副本再失败且与上一轮错误同因：止损落终态，不再排下一轮。"""
        from app.core import jobs, store
        task, run_a = self._seed_failed_serial_run(store, "same-cause book")
        store.update_run(run_a["id"], error=self._ERR_A)
        captured = {}
        orig_timer = jobs.threading.Timer

        def fake_timer(interval, fn):
            captured["interval"] = interval

            class _T:
                daemon = False
                def start(self):
                    pass
            return _T()

        jobs.threading.Timer = fake_timer
        try:
            self.assertTrue(jobs._maybe_auto_resume(run_a["id"]))
            runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
            run_b = next(r for r in runs if r["id"] != run_a["id"])
            store.update_run(run_b["id"], status="failed", error=self._ERR_A_RELABELED)
            self.assertFalse(jobs._maybe_auto_resume(run_b["id"]),
                             "同因连撞必须止损，不得再排续跑副本")
        finally:
            jobs.threading.Timer = orig_timer
        runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
        self.assertEqual(len(runs), 2, "同因止损后不得出现第 3 个运行")
        run_b = next(r for r in runs if r["id"] != run_a["id"])
        self.assertEqual(run_b.get("auto_resume_stopped"), "same_cause")
        self.assertIn("止损", run_b.get("error") or "", "终态错误里要写明止损死因")
        self.assertEqual(jobs._QUEUE.qsize(), 0)

    def test_different_cause_still_resumes(self):
        """失败原因变了（换墙了）：退避重试仍值得烧，继续自动续跑。"""
        from app.core import jobs, store
        task, run_a = self._seed_failed_serial_run(store, "diff-cause book")
        store.update_run(run_a["id"], error=self._ERR_A)
        orig_timer = jobs.threading.Timer

        def fake_timer(interval, fn):
            class _T:
                daemon = False
                def start(self):
                    pass
            return _T()

        jobs.threading.Timer = fake_timer
        try:
            self.assertTrue(jobs._maybe_auto_resume(run_a["id"]))
            runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
            run_b = next(r for r in runs if r["id"] != run_a["id"])
            store.update_run(run_b["id"], status="failed", error=self._ERR_B)
            self.assertTrue(jobs._maybe_auto_resume(run_b["id"]),
                            "错误签名不同说明墙变了，应继续续跑")
        finally:
            jobs.threading.Timer = orig_timer
        runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
        self.assertEqual(len(runs), 3, "异因失败应排出第 3 个运行")


class TestAutoResumeTimeout(BaseTest):
    """超时终态纳入自动续跑（2026-09-24 戍边骑奴 9-20 案）。

    「任务总时限已到」此前不触发续跑：总时限默认 1 小时、慢链天 12 章连载
    天然跑不完，用户只能守着手动点重试。超时是最该接着写的一种中断——
    进度锚点用 chapter_scores：本轮比上轮多过审了章就不算同因无效重试；
    两轮一章未进（错误签名又恒同句）则照常止损交给人工。
    """

    def _seed_timeout_serial_run(self, store, title, chapters_passed=0):
        task = store.create_task({
            "type": "serial_novel", "title": title, "goal": "写连载",
            "workdir": str(self.workdir),
            "serial": {"chapters": 12, "words_per_chapter": 2500},
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        scores = [{"chapter": i, "title": "第%d章" % i, "means": {"情节": 8.0},
                   "passed": True, "rounds": 1} for i in range(1, chapters_passed + 1)]
        store.update_run(run["id"], status="timeout", error="任务总时限已到",
                         chapter_scores=scores)
        return task, run

    def _fake_timer(self):
        from app.core import jobs
        orig = jobs.threading.Timer

        def fake_timer(interval, fn):
            class _T:
                daemon = False
                def start(self):
                    pass
            return _T()
        jobs.threading.Timer = fake_timer
        return orig

    def test_timeout_serial_run_resumes(self):
        """超时的连载任务自动续跑，新 run 继承次数并记录退避入队时刻。"""
        from app.core import jobs, store
        task, run = self._seed_timeout_serial_run(store, "timeout book", chapters_passed=2)
        orig = self._fake_timer()
        try:
            self.assertTrue(jobs._maybe_auto_resume(run["id"]),
                            "timeout 终态必须触发自动续跑")
        finally:
            jobs.threading.Timer = orig
        runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
        new = next(r for r in runs if r["id"] != run["id"])
        self.assertEqual(new["status"], "queued")
        self.assertEqual(new["auto_resumes"], 1)
        self.assertTrue(new.get("resume_enqueue_at"))

    def test_timeout_progressed_still_resumes_despite_same_error(self):
        """两轮同为「任务总时限已到」但本轮多过审了章：不算同因无效重试。"""
        from app.core import jobs, store
        task, run_a = self._seed_timeout_serial_run(store, "slow book", chapters_passed=2)
        orig = self._fake_timer()
        try:
            self.assertTrue(jobs._maybe_auto_resume(run_a["id"]))
            runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
            run_b = next(r for r in runs if r["id"] != run_a["id"])
            store.update_run(run_b["id"], status="timeout", error="任务总时限已到",
                             chapter_scores=[{"chapter": i, "title": "第%d章" % i,
                                              "means": {"情节": 8.0},
                                              "passed": True, "rounds": 1}
                                             for i in range(1, 6)])
            self.assertTrue(jobs._maybe_auto_resume(run_b["id"]),
                            "有进度（5 章 > 2 章）的超时必须继续续跑")
        finally:
            jobs.threading.Timer = orig

    def test_timeout_no_progress_stops_same_cause(self):
        """两轮超时一章未进：退避没换来结果，止损落终态。"""
        from app.core import jobs, store
        task, run_a = self._seed_timeout_serial_run(store, "stuck book", chapters_passed=0)
        orig = self._fake_timer()
        try:
            self.assertTrue(jobs._maybe_auto_resume(run_a["id"]))
            runs = [r for r in store.list_runs(50) if r.get("task_id") == task["id"]]
            run_b = next(r for r in runs if r["id"] != run_a["id"])
            store.update_run(run_b["id"], status="timeout", error="任务总时限已到")
            self.assertFalse(jobs._maybe_auto_resume(run_b["id"]),
                             "零进度同因超时必须止损")
        finally:
            jobs.threading.Timer = orig
        run_b = store.get_run(run_b["id"])
        self.assertEqual(run_b.get("auto_resume_stopped"), "same_cause")
        self.assertIn("止损", run_b.get("error") or "")

    def test_non_serial_timeout_not_resumed(self):
        """非连载任务超时不自动续跑（自动续跑的语义是连载接着写）。"""
        from app.core import jobs, store
        task = store.create_task({"type": "direct", "goal": "干活",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="timeout", error="任务总时限已到")
        self.assertFalse(jobs._maybe_auto_resume(run["id"]))