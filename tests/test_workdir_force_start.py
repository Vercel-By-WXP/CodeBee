# -*- coding: utf-8 -*-
"""同工作目录互斥的 force_start 人工逃生口测试。

默认行为不变：同目录有 running 占用者 → 打回 queued + blocked_by + 60s 补跑
Timer（2026-09-23 拍板的互斥语义）。run 带 force_start 标记（详情页「强制
起跑」按钮）时放行并行并留痕 warning；jobs.force_start_run 负责落标记 +
摘 Timer + 立即入队（2026-10-22 拍板「限制得太死」的人工逃生口）。
隔离方式：全程 mock.patch，不碰真实 store 落盘（_TASKS/_RUNS 是模块级缓存）。
哨兵法：TASK 带 git_rev，执行越过互斥检查后第一个副作用是 git 检出，抛
_Sentinel 截断后续流水线，避免真的起跑 LLM 调用。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class _Sentinel(Exception):
    pass


TASK = {"id": "t1", "title": "后来者", "workdir": "E:/wd/project",
        "git_rev": "main"}
BLOCKER = {"id": "t2", "title": "占用者", "workdir": "E:/wd/project"}


def _execute_with(blocker, run_fields):
    """跑 _execute_run_impl 到互斥检查之后的 git 检出哨兵。

    返回 (update_run 收到的 kwargs 列表, register_workdir_waiter mock,
    是否越过了互斥段)。"""
    from core import attachments, dispatch_prompt, flows, gitmod, store
    from core import jobs as job_runner
    from core import pipeline

    run = {"id": "r1", "task_id": "t1", "status": "running",
           "cancelled_by_user": False}
    run.update(run_fields)
    calls = []
    raised = {"flag": False}

    def _rec(run_id, expected_status=None, **fields):
        calls.append({"expected_status": expected_status, **fields})
        return {}

    def _sentinel(*_a, **_kw):
        raised["flag"] = True
        raise _Sentinel

    with mock.patch.object(store, "get_run", return_value=run), \
         mock.patch.object(store, "get_task", return_value=TASK), \
         mock.patch.object(store, "update_run", side_effect=_rec), \
         mock.patch.object(store, "update_task_status", return_value=True), \
         mock.patch.object(flows, "flow_drift", return_value=None), \
         mock.patch.object(attachments, "verify_task", return_value=[]), \
         mock.patch.object(dispatch_prompt, "prompt_strategy", return_value="s"), \
         mock.patch.object(pipeline, "_workdir_blocker", return_value=blocker), \
         mock.patch.object(pipeline, "_task_budget"), \
         mock.patch.object(job_runner, "register_workdir_waiter") as waiter, \
         mock.patch.object(gitmod, "prepare_checkout", side_effect=_sentinel):
        try:
            pipeline._execute_run_impl("r1")
        except _Sentinel:
            pass
    return calls, waiter, raised["flag"]


class WorkdirMutexTests(unittest.TestCase):
    def test_blocker_rolls_back_queued_with_blocked_by(self):
        calls, waiter, raised = _execute_with(BLOCKER, {})
        self.assertFalse(raised)   # 回滚后直接 return，不进流水线
        roll = [c for c in calls if c.get("status") == "queued"]
        self.assertEqual(len(roll), 1)
        self.assertEqual(roll[0]["expected_status"], "running")
        self.assertEqual(roll[0]["blocked_by"], "E:/wd/project")
        self.assertIn("占用", roll[0]["warnings"][0])
        waiter.assert_called_once()

    def test_force_start_flag_bypasses_mutex_with_warning(self):
        calls, waiter, raised = _execute_with(BLOCKER, {"force_start": True})
        self.assertTrue(raised)   # 越过互斥继续执行到 git 检出哨兵
        waiter.assert_not_called()
        self.assertFalse([c for c in calls if c.get("status") == "queued"])
        warns = [c for c in calls if c.get("warnings")]
        self.assertTrue(any("强制并行" in c["warnings"][0] for c in warns))

    def test_no_blocker_runs_normally(self):
        calls, waiter, raised = _execute_with(None, {})
        self.assertTrue(raised)
        waiter.assert_not_called()
        self.assertFalse([c for c in calls if c.get("status") == "queued"])


class ForceStartRunTests(unittest.TestCase):
    def test_force_start_sets_flag_and_enqueues(self):
        from core import jobs, store
        run = {"id": "r1", "task_id": "t1", "kind": "orchestration",
               "status": "queued", "resume_enqueue_at": "2026-10-22 10:00:00"}
        upd = []
        timer = mock.Mock()
        with mock.patch.object(store, "get_run", return_value=run), \
             mock.patch.object(store, "update_run",
                               side_effect=lambda rid, **kw:
                                   (upd.append(kw) or {})), \
             mock.patch.object(jobs, "_deferred_timers", {"r1": timer}), \
             mock.patch.object(jobs, "enqueue") as enq:
            ok, err = jobs.force_start_run("r1")
        self.assertTrue(ok, err)
        self.assertEqual(upd[0], {"expected_status": "queued",
                                  "force_start": True, "blocked_by": ""})
        self.assertEqual(upd[1], {"resume_enqueue_at": ""})
        timer.cancel.assert_called_once()
        enq.assert_called_once_with(
            {"kind": "orchestration", "run_id": "r1", "task_id": "t1"})

    def test_duplicate_job_error_is_success(self):
        from core import jobs, store
        run = {"id": "r1", "task_id": "t1", "kind": "orchestration",
               "status": "queued"}
        with mock.patch.object(store, "get_run", return_value=run), \
             mock.patch.object(store, "update_run", return_value={}), \
             mock.patch.object(jobs, "_deferred_timers", {}), \
             mock.patch.object(jobs, "enqueue",
                               side_effect=jobs.DuplicateJobError("taken")):
            ok, err = jobs.force_start_run("r1")
        self.assertTrue(ok, err)

    def test_rejects_non_queued_and_non_orchestration(self):
        from core import jobs, store
        with mock.patch.object(store, "get_run", return_value=None):
            ok, err = jobs.force_start_run("r1")
            self.assertFalse(ok)
            self.assertIn("不存在", err)
        with mock.patch.object(store, "get_run",
                               return_value={"id": "r1", "status": "running"}):
            ok, err = jobs.force_start_run("r1")
            self.assertFalse(ok)
            self.assertIn("排队", err)
        with mock.patch.object(store, "get_run",
                               return_value={"id": "r1", "status": "queued",
                                             "kind": "mgmt"}):
            ok, err = jobs.force_start_run("r1")
            self.assertFalse(ok)
            self.assertIn("不支持", err)

    def test_cas_failure_reports_state_changed(self):
        from core import jobs, store
        run = {"id": "r1", "task_id": "t1", "kind": "orchestration",
               "status": "queued"}
        with mock.patch.object(store, "get_run", return_value=run), \
             mock.patch.object(store, "update_run", return_value=None):
            ok, err = jobs.force_start_run("r1")
        self.assertFalse(ok)
        self.assertIn("已变化", err)


if __name__ == "__main__":
    unittest.main()
