# -*- coding: utf-8 -*-
"""额度恢复检查脚本的判据守测：拿真实落盘值冻住字段路径，别再靠人眼比对。

夹具 `tests/fixtures/quota_recovery_runs.json` 是 2026-11-06 15:00 从生产
`/api/runs/<id>` 只读取的两条真实定时 run（11 步 + verdict），只裁掉
command / configuration / output 等无关大字段，**没有改写任何字符串**。

守四件事：

1. 三件套按**真实字段路径**判：`completed_seats` 在 `review_resources` 里，
   `review_fallbacks` 在真实 run 里根本没有键（不是 `[local-heuristic]`）。
2. 429 从 `summary` / `note` 检出（`error` 常为空），且 request id 里的
   "429" 数字巧合不能误判。
3. `/api/state` 的压缩 verdict 只有 `publishable` → 必须报「字段不足」，
   不许拿它下质量结论。
4. `--resume-tasks` 不带人工确认标志时拒绝写生产；dry-run 不产生写调用。
"""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_runs.json"
_spec = importlib.util.spec_from_file_location(
    "quota_recovery", ROOT / "scripts" / "quota_recovery.py")
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)


def _runs():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["runs"]


class JudgeRunTest(unittest.TestCase):
    def test_real_runs_are_judged_by_real_field_paths(self):
        """决策 18：报出真被判过的样本数，别拿零样本当绿灯。"""
        judged = 0
        for run in _runs():
            d = qr.judge_run(run)
            self.assertTrue(d["fields_complete"], run["id"])
            self.assertIs(d["publishable"], False, run["id"])
            self.assertEqual(d["review_independence"], "no-review", run["id"])
            self.assertEqual(d["completed_seats"], 0, run["id"])
            self.assertEqual(d["required_seats"], 1, run["id"])
            self.assertTrue(d["verdict_honest"], run["id"])
            self.assertEqual(d["successful_model_steps"], 1, run["id"])
            self.assertTrue(d["last_step_rate_limited"], run["id"])
            # 14:35 误把守测期望值当落盘值写进交接单；真实 run 没有这个键。
            self.assertEqual(d["review_fallbacks"], [], run["id"])
            self.assertIn("不可发布", qr.human_verdict(d))
            judged += 1
            print("  %s 真被判过：限流步 %s，真调模型 %d 步" % (
                run["id"], d["rate_limited_steps"], d["successful_model_steps"]))
        print("真被判过的真实 run 样本数: %d / %d" % (judged, len(_runs())))
        self.assertGreaterEqual(judged, 2, "真实 run 样本不足，验收不成立")

    def test_429_lives_in_summary_and_note_not_error(self):
        run = _runs()[0]
        steps = run["steps"]
        self.assertTrue(any("429 Too Many Requests" in str(s.get("summary") or "")
                            for s in steps))
        self.assertTrue(any("429 Too Many Requests" in str(s.get("note") or "")
                            for s in steps))
        # 末步 error 字段是空：只看 error 会漏掉所有限流。
        self.assertFalse(str(steps[-1].get("error") or "").strip())
        d = qr.judge_run(run)
        self.assertGreaterEqual(len(d["rate_limited_steps"]), 5)

    def test_request_id_digits_are_not_rate_limit(self):
        """n=10 的 request id 里巧合含 429，裸 grep 会多算一步。"""
        self.assertFalse(qr._step_rate_limited(
            {"summary": "request id: 2026110516490068394296004dff52a0zCklXBH"}))
        self.assertTrue(qr._step_rate_limited(
            {"summary": "exceeded retry limit, last status: 429 Too Many Requests"}))
        self.assertTrue(qr._step_rate_limited(
            {"note": "失败：codex: exceeded retry limit, last status: 429 Too Many Requests"}))

    def test_state_compact_verdict_cannot_judge_quality(self):
        """/api/state 的 verdict 是 _state_run_summary 压缩白名单。"""
        compact = {"id": "r-x", "status": "done", "verdict": {"publishable": False},
                   "steps": []}
        d = qr.judge_run(compact)
        self.assertFalse(d["fields_complete"])
        self.assertFalse(d["verdict_honest"])
        self.assertIn("字段不足", qr.human_verdict(d))
        self.assertIn("/api/runs/<id>", qr.human_verdict(d))

    def test_zero_cost_reused_steps_do_not_count_as_model_calls(self):
        run = {"id": "r-x", "status": "done",
               "verdict": {"publishable": False, "overall": 6.0, "threshold": 7.0,
                           "review_independence": "no-review",
                           "review_resources": {"completed_seats": 0,
                                                "required_seats": 1}},
               "steps": [{"n": 1, "status": "done", "cost_usd": 0.0, "model": ""},
                         {"n": 2, "status": "done", "cost_usd": 1.2,
                          "model": "glm-5.3-flash"}]}
        d = qr.judge_run(run)
        self.assertEqual(d["successful_model_steps"], 1)
        self.assertEqual(d["rate_limited_steps"], [])


class ResumeSafetyTest(unittest.TestCase):
    def test_task_ids_are_the_four_actually_disabled(self):
        self.assertEqual(len(qr.TASK_IDS), 4)
        self.assertTrue(all(t.startswith("auto-") for t in qr.TASK_IDS))
        self.assertEqual(len(set(qr.TASK_IDS)), 4)

    def test_resume_without_confirmation_is_refused_without_any_write(self):
        with mock.patch.object(qr, "api_post") as post:
            rc = qr.main(["--resume-tasks"])
        self.assertEqual(rc, 2)
        post.assert_not_called()

    def test_dry_run_never_writes(self):
        with mock.patch.object(qr, "api_post") as post, \
             mock.patch.object(qr, "api_get") as get:
            rc = qr.main(["--resume-tasks", "--dry-run",
                          "--confirm-quota-restored"])
        self.assertEqual(rc, 0)
        post.assert_not_called()
        get.assert_not_called()

    def test_check_reports_suppressed_state_without_writing(self):
        state = {"pause_control": {"active": 4, "paused": 4},
                 "runs": [{"id": "r-20261106-094827-3523", "status": "running",
                           "pause_phase": "paused"}]}
        tasks = {"tasks": [{"id": t, "name": "n", "enabled": False, "next_run": ""}
                           for t in qr.TASK_IDS]}

        def fake_get(_base, path):
            return tasks if path == "/api/automation" else state

        buf = io.StringIO()
        with mock.patch.object(qr, "api_get", side_effect=fake_get), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--check"])
        self.assertEqual(rc, 0)
        self.assertIn("抑制态", buf.getvalue())
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
