# -*- coding: utf-8 -*-
"""11-09 唯一烧额度的写路径守测：`--retry-one` 必须只写一本、且四道闸全过。

21:4x 实案：`--check` / `--check-inherit` / `judge_retry_readiness` /
`judge_quota_window` 四个判据都在，但 `POST /api/tasks/<id>/retry` 仍靠
11-09 人手敲 curl。交接单第 3 条写着「先一本、别四本抢同一个见底配额池」，
`--check-inherit` 也算出了「先只 retry t-20261104-170153-4815」，可结论
没有接到写路径：敲错本子（会倒退 4 章）或一次 retry 四本都会白烧刚恢复
的额度。`--retry-one` 把四个判据串到写路径，四道闸任一不过零 POST 拒写。

真样本：`tests/fixtures/retry_inherit_real_runs.json` 里 20 条生产只读取证
的真实 run（4 个 11-09 待续跑 task），推荐值由 `plan_check_inherit` 算出，
**不硬编码 task id**，并与 `cmd_check_inherit` 的 stdout 交叉验证。
合成边界（unknown / 写后四本齐跑 / 时钟）单列标注、不计真样本数。

决策 18：打印真被判过的真实样本数。绝不碰 8765：所有 HTTP 都是 mock。
"""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RUNS_FIXTURE = ROOT / "tests" / "fixtures" / "retry_inherit_real_runs.json"
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
_spec = importlib.util.spec_from_file_location("quota_recovery_retry_one", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

BEFORE = datetime(2026, 11, 6, 21, 45)
AFTER = datetime(2026, 11, 9, 15, 0)
# 上一轮生产真跑 --check-inherit 的结论，用来交叉验证判据没漂移。
EXPECTED_RECOMMENDED = "t-20261104-170153-4815"
REGRESS_TASKS = ("t-20261022-104907-1000", "t-20261029-102103-5910",
                 "t-20261105-152926-2324")

_fixture = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
REAL_RUNS = _fixture["runs"]


def _state_runs(reaped=False):
    out = []
    for r in REAL_RUNS:
        row = {"id": r["id"], "task_id": r["task_id"], "status": r["status"],
               "paused": r.get("paused"), "pause_phase": r.get("pause_phase")}
        if reaped and row["status"] in ("queued", "running"):
            row["status"] = "failed"
            row["interrupted"] = "restart"
        out.append(row)
    return out


def _state(reaped=False, extra_runs=None):
    tasks = [{"id": t, "status": "failed" if reaped else "running"}
             for t in qr.RESUME_TASK_IDS]
    return {"runs": _state_runs(reaped) + list(extra_runs or []),
            "tasks": tasks,
            "pause_control": {"active": 0 if reaped else 4,
                              "paused": 0 if reaped else 4}}


def _details():
    out = {r["id"]: dict(r) for r in REAL_RUNS}
    # 15:4x：_state_runs(reaped=True) 已在摘要层写 interrupted=restart，
    # 详情层必须自洽 —— 摘要有标记而详情没有，正是 13:20 收尸判据专防的
    # 「收尸只做一半」。⑧ 新增第五道闸后真写路径守测要提供完整详情。
    # 派生时刻不是真实落盘值，只用于让守测状态内部自洽。
    at = "2026-11-09 14:20:01"
    for rid, d in out.items():
        if str(rid).startswith("r-20261106-0948"):
            d.update({"status": "failed", "interrupted": "restart",
                      "interrupted_at": at, "ended_at": at,
                      "error": "服务重启中断，可重试"})
    return out


def _inherit(reaped=False):
    runs = _state_runs(reaped)
    return qr.plan_check_inherit(runs, _details())


class FakeAPI:
    """只认 /api/state 与 /api/runs/<id>；POST 记账，绝不发真实请求。"""

    def __init__(self, state_before, state_after=None, post_result=None):
        self.before = state_before
        self.after = state_after
        self.post_result = post_result or {"ok": True, "run_id": "r-new-0001"}
        self.state_calls = 0
        self.gets = []
        self.posts = []

    def get(self, _base, path):
        self.gets.append(path)
        if path == "/api/state":
            self.state_calls += 1
            if self.state_calls > 1 and self.after is not None:
                return self.after
            return self.before
        if path.startswith("/api/runs/"):
            rid = path.rsplit("/", 1)[-1]
            return {"run": _details()[rid]}
        if path == "/api/automation":
            return {"tasks": []}
        raise AssertionError("unexpected GET " + path)

    def post(self, _base, path, body):
        self.posts.append((path, body))
        return self.post_result


def _run_cli(argv, api, now=AFTER):
    buf = io.StringIO()
    with mock.patch.object(qr, "_now", return_value=now), \
         mock.patch.object(qr, "api_get", side_effect=api.get), \
         mock.patch.object(qr, "api_post", side_effect=api.post), \
         redirect_stdout(buf):
        rc = qr.main(argv)
    return rc, buf.getvalue()


class RealFixtureCrossValidationTest(unittest.TestCase):
    """推荐值必须来自真实判据，且与 --check-inherit 的输出对得上。"""

    def test_recommended_task_comes_from_real_inherit_judgement(self):
        plan = _inherit()
        self.assertEqual(plan["judged_run_count"], 20, "20 条真实 run 都要被判过")
        self.assertEqual(plan["judged_task_count"], 4)
        self.assertEqual(plan["historical_chapter_count"], 30)
        self.assertEqual(plan["recommended_first"], EXPECTED_RECOMMENDED)
        verdicts = {r["task_id"]: r["verdict"] for r in plan["results"]}
        self.assertEqual(verdicts[EXPECTED_RECOMMENDED], "ok")
        for tid in REGRESS_TASKS:
            self.assertEqual(verdicts[tid], "regress", tid)
            row = next(r for r in plan["results"] if r["task_id"] == tid)
            self.assertEqual(len(row["lost_chapters"]), 4, tid)
        print("\n真被判过的真实样本：%d/20 run、%d/4 task、%d 章；"
              "推荐 %s（判据推导，非硬编码）" % (
                  plan["judged_run_count"], plan["judged_task_count"],
                  plan["historical_chapter_count"], plan["recommended_first"]))

    def test_check_inherit_stdout_agrees_with_plan(self):
        """交叉验证：另一条函数路径（cmd_check_inherit）也指向同一本。"""
        api = FakeAPI(_state())
        buf = io.StringIO()
        with mock.patch.object(qr, "api_get", side_effect=api.get), \
             mock.patch.object(qr, "api_post", side_effect=api.post), \
             redirect_stdout(buf):
            rc = qr.main(["--check-inherit"])
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("真被判过的真实 run 样本数 20/20", text)
        self.assertIn("先只 retry " + EXPECTED_RECOMMENDED, text)
        self.assertEqual(api.posts, [], "--check-inherit 必须零 POST")


class PlanRetryOneGateTest(unittest.TestCase):
    """纯函数四道闸：任一不过 allow=False，且给出人话 blocker。"""

    def test_four_gates_and_success_path(self):
        inherit = _inherit()
        now, reaped = _state(), _state(reaped=True)
        cases = [
            ("confirm", dict(state=now, confirm=False, window_reached=True)),
            ("window", dict(state=now, confirm=True, window_reached=False)),
            ("retry_ready", dict(state=now, confirm=True, window_reached=True)),
            ("inherit", dict(state=reaped, confirm=True, window_reached=True,
                             task_id=REGRESS_TASKS[0])),
            ("unknown_task", dict(state=reaped, confirm=True, window_reached=True,
                                  task_id="t-not-real")),
        ]
        judged = 0
        for gate, kw in cases:
            p = qr.plan_retry_one(kw.pop("state"), inherit, **kw)
            self.assertEqual(p["gate"], gate, gate)
            self.assertFalse(p["allow"], gate)
            self.assertTrue(p["blockers"], gate)
            judged += 1
        ok = qr.plan_retry_one(reaped, inherit, confirm=True, window_reached=True)
        self.assertTrue(ok["allow"])
        self.assertEqual(ok["gate"], "allow")
        self.assertEqual(ok["target"], EXPECTED_RECOMMENDED)
        print("四道闸 + 成功路径共真被判过 %d 个真实夹具场景" % (judged + 1))

    def test_unfilled_task_status_is_rejected_by_readiness_gate(self):
        """合成边界：run 全收尸了但目标 task 状态没回填，retry 仍会被拒。

        retry_ready=True 的定义已含「4 个 task 全终态」，所以不再单列
        task_terminal 死分支；blockers 必须把未回填的 task 点名。
        """
        inherit = _inherit()
        state = _state(reaped=True)
        for t in state["tasks"]:
            if t["id"] == EXPECTED_RECOMMENDED:
                t["status"] = "running"
        p = qr.plan_retry_one(state, inherit, confirm=True, window_reached=True)
        self.assertEqual(p["gate"], "retry_ready")
        self.assertFalse(p["allow"])
        self.assertIn("还不是终态", p["blockers"][0])
        print("合成边界 1 条（单列标注、不计真样本）：task 未回填终态")

    def test_unknown_inherit_verdict_is_fail_closed(self):
        """合成边界：目标判不了（无大纲 / ledger 回炉）一律拒，不上绿标。"""
        inherit = _inherit()
        for r in inherit["results"]:
            if r["task_id"] == EXPECTED_RECOMMENDED:
                r["verdict"] = "unknown"
                r["reasons"] = ["published_chapters 不在 API 里，判不了"]
        p = qr.plan_retry_one(_state(reaped=True), inherit,
                              confirm=True, window_reached=True)
        self.assertFalse(p["allow"])
        self.assertEqual(p["gate"], "inherit")
        self.assertIn("unknown/缺详情", p["blockers"][0])
        # 没有一本能判「一章不丢」时，连推荐值都没有 → 更早的 no_safe_task 闸
        inherit["recommended_first"] = ""
        p2 = qr.plan_retry_one(_state(reaped=True), inherit,
                               confirm=True, window_reached=True)
        self.assertFalse(p2["allow"])
        self.assertEqual(p2["gate"], "no_safe_task")
        print("合成边界 2 条（单列标注、不计真样本）：unknown / 无安全本子一律拒")

    def test_dry_run_reports_pending_preconditions(self):
        p = qr.plan_retry_one(_state(), _inherit(), confirm=True,
                              window_reached=False, dry_run=True)
        self.assertTrue(p["allow"], "dry-run 只是预演，不能真写")
        self.assertTrue(any("恢复窗口" in n for n in p["notes"]))
        self.assertTrue(any("收尸" in n for n in p["notes"]))

    def test_post_write_review_catches_four_books_racing(self):
        before = _state(reaped=True)
        extra = [{"id": "r-new-0001", "task_id": EXPECTED_RECOMMENDED, "status": "queued"},
                 {"id": "r-new-0002", "task_id": REGRESS_TASKS[0], "status": "queued"}]
        after = _state(reaped=True, extra_runs=extra)
        chk = qr.judge_retry_post_write(before, after, EXPECTED_RECOMMENDED, "r-new-0001")
        self.assertFalse(chk["ok"])
        self.assertTrue(any("四本齐跑" in p for p in chk["problems"]))
        good = _state(reaped=True, extra_runs=extra[:1])
        self.assertTrue(qr.judge_retry_post_write(before, good,
                                                  EXPECTED_RECOMMENDED,
                                                  "r-new-0001")["ok"])
        print("合成写后状态 2 条（单列标注、不计真样本）：四本齐跑必须报复核失败")


class RetryOneWritePathTest(unittest.TestCase):
    """CLI 全链路：所有 HTTP 都 mock，绝不碰 8765。"""

    def test_missing_confirm_refuses_with_zero_io(self):
        api = FakeAPI(_state())
        rc, text = _run_cli(["--retry-one"], api)
        self.assertEqual(rc, 2, text)
        self.assertIn("缺 --confirm-quota-restored", text)
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])

    def test_before_window_refuses_with_zero_io(self):
        api = FakeAPI(_state())
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored"],
                            api, now=BEFORE)
        self.assertEqual(rc, 2, text)
        self.assertIn("恢复窗口未到", text)
        self.assertIn("距额度恢复窗口", text)
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])

    def test_not_reaped_refuses_with_zero_post(self):
        api = FakeAPI(_state())
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored"], api)
        self.assertEqual(rc, 2, text)
        self.assertIn("重启收尸未完成", text)
        self.assertIn("真被判过的真实 run 样本数 20/20", text)
        self.assertEqual(api.posts, [])

    def test_wrong_book_refuses_with_zero_post(self):
        api = FakeAPI(_state(reaped=True))
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored",
                             "--task-id", REGRESS_TASKS[0]], api)
        self.assertEqual(rc, 2, text)
        self.assertIn("会倒退 4 章", text)
        self.assertEqual(api.posts, [])

    def test_dry_run_previews_with_zero_post(self):
        api = FakeAPI(_state())
        rc, text = _run_cli(["--retry-one", "--dry-run",
                             "--confirm-quota-restored"], api, now=BEFORE)
        self.assertEqual(rc, 0, text)
        self.assertIn("真被判过的真实 run 样本数 20/20", text)
        self.assertIn(EXPECTED_RECOMMENDED, text)
        self.assertIn("dry-run 提醒", text)
        self.assertIn("生产未改动", text)
        self.assertEqual(api.posts, [])
        self.assertGreater(len(api.gets), 1, "预演要真读生产判据")

    def test_success_path_posts_exactly_once_and_reviews(self):
        new_run = "r-new-0001"
        before = _state(reaped=True)
        after = _state(reaped=True, extra_runs=[
            {"id": new_run, "task_id": EXPECTED_RECOMMENDED, "status": "queued"}])
        api = FakeAPI(before, state_after=after,
                      post_result={"ok": True, "run_id": new_run})
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored"], api)
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(api.posts), 1, "只准 POST 一次")
        self.assertEqual(api.posts[0][0],
                         "/api/tasks/%s/retry" % EXPECTED_RECOMMENDED)
        self.assertIn("复核通过", text)
        self.assertIn("--verify-run " + new_run, text)

    def test_failed_post_is_not_reported_as_success(self):
        api = FakeAPI(_state(reaped=True),
                      post_result={"ok": False, "error": "任务仍在运行中"})
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored"], api)
        self.assertEqual(rc, 1, text)
        self.assertIn("写入失败", text)
        self.assertNotIn("复核通过", text)

    def test_post_write_review_failure_is_visible(self):
        """合成边界：写后其余本子也起跑了，必须显式报失败，不能假装成功。"""
        before = _state(reaped=True)
        after = _state(reaped=True, extra_runs=[
            {"id": "r-new-0001", "task_id": EXPECTED_RECOMMENDED, "status": "queued"},
            {"id": "r-new-0002", "task_id": REGRESS_TASKS[0], "status": "queued"}])
        api = FakeAPI(before, state_after=after)
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored"], api)
        self.assertEqual(rc, 1, text)
        self.assertIn("复核失败", text)
        self.assertIn("四本齐跑", text)
        self.assertEqual(len(api.posts), 1, "POST 已发生、不能回滚，但要报错")

    def test_task_id_is_routed_to_retry_one(self):
        with mock.patch.object(qr, "cmd_retry_one", return_value=0) as cmd:
            rc = qr.main(["--retry-one", "--task-id", EXPECTED_RECOMMENDED,
                          "--confirm-quota-restored"])
        self.assertEqual(rc, 0)
        self.assertEqual(cmd.call_args[0][1], EXPECTED_RECOMMENDED)


class ConsumerAndSourceAnchorTest(unittest.TestCase):
    """消费点与源码锚点：别让护栏或入口被回滚成手敲 curl。"""

    def test_check_and_resume_point_at_retry_one(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("真要 retry 用 --retry-one", src)
        self.assertIn("--retry-one --dry-run --confirm-quota-restored", src)

    def test_four_gates_and_single_post_are_not_rolled_back(self):
        src = SCRIPT.read_text(encoding="utf-8")
        anchors = (
            'if not dry_run and window_reached is not True:',
            'if row.get("verdict") != "ok":',
            'if not dry_run and not readiness["retry_ready"]:',
            'res = api_post(base, "/api/tasks/%s/retry" % plan["target"], {})',
            'chk = judge_retry_post_write(state, after, plan["target"], new_run_id)',
            '"--retry-one"',
            'if args.retry_one:',
        )
        for anchor in anchors:
            self.assertIn(anchor, src, anchor)


if __name__ == "__main__":
    unittest.main()
