# -*- coding: utf-8 -*-
"""额度恢复检查脚本的判据守测：拿真实落盘值冻住字段路径，别再靠人眼比对。

夹具 `tests/fixtures/quota_recovery_runs.json` 是 2026-11-06 15:03 / 15:2x
从生产 `/api/runs/<id>` 只读取的 **3 条真实 run**（2 条 doc 各 11 步、1 条
serial_novel 73 步），只裁掉 command / configuration / output / log 与 verdict
里 route / volumes / chapter_scores / major_issues / signing_checkpoint 等无关
大字段，**没有改写任何字符串**。

守四件事（后两件是 15:2x 新量出来的）：

1. 三件套按**真实字段路径**判：`completed_seats` 在 `review_resources` 里，
   `review_fallbacks` 在真实 run 里根本没有键（不是 `[local-heuristic]`）。
2. 429 从 `summary` / `note` 检出（`error` 常为空），且 request id 里的
   "429" 数字巧合不能误判。
3. `/api/state` 的压缩 verdict 只有白名单 6 键 → 必须报「字段不足」；
   **但 serial_novel 的完整详情没有 `review_resources`、`overall=null`，
   不能被误判成压缩版**——它本来就是详情。
4. `--resume-tasks` 不带人工确认标志时拒绝写生产；dry-run 不产生写调用；
   `--verify-run` 必须真的把恢复判据打到 stdout（判据有消费点）。

决策 18：报出真被判过的真实样本数。合成样本只用于反向覆盖边界，单独标注、
不计入真样本数。
"""
from __future__ import annotations

import copy
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


def _by_schema(schema):
    return [r for r in _runs()
            if qr.detect_schema(r.get("verdict") or {}) == schema]


class JudgeRunTest(unittest.TestCase):
    def test_all_real_runs_are_judged_and_print_sample_count(self):
        judged = 0
        for run in _runs():
            d = qr.judge_run(run)
            self.assertTrue(d["fields_complete"], run["id"])
            self.assertIn(d["schema"], ("doc", "serial_novel"), run["id"])
            self.assertIs(d["publishable"], False, run["id"])
            self.assertTrue(d["verdict_honest"], run["id"])
            text = qr.human_verdict(d)
            self.assertIn("不可发布", text)
            self.assertNotIn("字段不足", text, run["id"])
            judged += 1
            print("  %s [%s] 真被判过：限流 %d 步，真调模型 %d 步" % (
                run["id"], d["schema"], len(d["rate_limited_steps"]),
                d["successful_model_steps"]))
        print("真被判过的真实 run 样本数: %d / %d" % (judged, len(_runs())))
        self.assertGreaterEqual(judged, 3, "真实 run 样本不足，验收不成立")

    def test_doc_runs_use_review_resources_field_path(self):
        docs = _by_schema("doc")
        self.assertEqual(len(docs), 2)
        for run in docs:
            d = qr.judge_run(run)
            self.assertEqual(d["schema"], "doc", run["id"])
            self.assertEqual(d["review_independence"], "no-review", run["id"])
            self.assertEqual(d["completed_seats"], 0, run["id"])
            self.assertEqual(d["required_seats"], 1, run["id"])
            self.assertEqual(d["successful_model_steps"], 1, run["id"])
            self.assertTrue(d["last_step_rate_limited"], run["id"])
            # 14:35 误把守测期望值当落盘值写进交接单；真实 run 没有这个键。
            self.assertEqual(d["review_fallbacks"], [], run["id"])
            self.assertIn("无真实评审席位", qr.human_verdict(d))

    def test_serial_run_is_full_detail_not_a_compact_verdict(self):
        """旧版对连载详情一律报「这是 /api/state 压缩版，请走详情」——误报。"""
        serials = _by_schema("serial_novel")
        self.assertEqual(len(serials), 1)
        run = serials[0]
        d = qr.judge_run(run)
        self.assertEqual(d["schema"], "serial_novel")
        self.assertTrue(d["fields_complete"])
        self.assertIsNone(d["overall"])          # 连载详情里 overall 就是 null
        self.assertIsNone(d["completed_seats"])  # 没有 review_resources
        self.assertIsNone(d["required_seats"])
        self.assertEqual(d["review_status"], "not_evaluated")
        self.assertEqual(d["review_quality"], "degraded")
        self.assertIs(d["review_degraded"], True)
        self.assertEqual(d["reviewer_count"], 2)
        self.assertIs(d["reviewer_consensus"], True)
        self.assertEqual(d["review_independence"], "cross-vendor")
        self.assertEqual(len(d["incomplete_review_reasons"]), 2)
        self.assertTrue(d["verdict_honest"])
        self.assertEqual(d["successful_model_steps"], 6)
        self.assertEqual(len(d["rate_limited_steps"]), 28)
        self.assertFalse(d["last_step_rate_limited"])  # 「末步 429」也不适用于连载
        self.assertEqual(d["review_fallbacks"], [])
        text = qr.human_verdict(d)
        self.assertNotIn("字段不足", text)
        self.assertNotIn("/api/runs/<id>", text)
        self.assertIn("评审证据未判定", text)
        self.assertIn("不是假绿", text)
        self.assertEqual(qr.detect_schema({"publishable": False}), "compact")

    def test_429_lives_in_summary_and_note_not_error(self):
        run = _by_schema("doc")[0]
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
        """/api/state 的 verdict 是 main.py:5034 压缩白名单。"""
        compact = {"id": "r-x", "status": "done", "verdict": {"publishable": False},
                   "steps": []}
        self.assertEqual(qr.detect_schema(compact["verdict"]), "compact")
        d = qr.judge_run(compact)
        self.assertFalse(d["fields_complete"])
        self.assertFalse(d["verdict_honest"])
        self.assertIn("字段不足", qr.human_verdict(d))
        self.assertIn("/api/runs/<id>", qr.human_verdict(d))

    def test_state_whitelist_six_keys_are_still_compact(self):
        verdict = {k: False if k in ("publishable", "verify_pass", "review_pass", "pass")
                   else "continue_only" for k in qr.STATE_VERDICT_KEYS}
        verdict["end_chapter"] = 8
        self.assertEqual(qr.detect_schema(verdict), "compact")
        self.assertFalse(qr.judge_run({"verdict": verdict})["fields_complete"])

    def test_unknown_schema_is_not_mislabelled_as_compact(self):
        d = qr.judge_run({"id": "r-x", "verdict": {"publishable": False, "weird": 1},
                          "steps": []})
        self.assertEqual(d["schema"], "unknown")
        self.assertIn("不认识", qr.human_verdict(d))
        self.assertNotIn("压缩", qr.human_verdict(d))

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


class RecoveryJudgeTest(unittest.TestCase):
    def test_all_real_runs_fail_recovery_with_concrete_blockers(self):
        judged = 0
        for run in _runs():
            r = qr.judge_recovery(qr.judge_run(run))
            self.assertFalse(r["recovery_ok"], run["id"])
            self.assertTrue(any("429" in b for b in r["blockers"]), run["id"])
            judged += 1
        print("恢复判据真被判过的真实 run 样本数: %d / %d" % (judged, len(_runs())))
        self.assertGreaterEqual(judged, 3)
        doc = qr.judge_recovery(qr.judge_run(_by_schema("doc")[0]))
        self.assertTrue(any("评审席位" in b for b in doc["blockers"]))
        serial = qr.judge_recovery(qr.judge_run(_by_schema("serial_novel")[0]))
        self.assertTrue(any("review_status" in b for b in serial["blockers"]))
        self.assertTrue(any("降级理由" in b for b in serial["blockers"]))

    def test_two_reviewer_seats_do_not_mean_review_completed(self):
        """反直觉真样本：2 个评审席位、cross-vendor，仍 review_status=not_evaluated。"""
        d = qr.judge_run(_by_schema("serial_novel")[0])
        r = qr.judge_recovery(d)
        self.assertEqual(d["reviewer_count"], 2)
        self.assertFalse(r["recovery_ok"])
        self.assertTrue(any("只说明排了席位" in n for n in r["notes"]))

    def test_synthetic_recovered_runs_pass_and_publishable_is_not_a_quota_signal(self):
        """合成边界样本 2 条，仅用于反向覆盖，不计入真样本数。"""
        doc = {"id": "r-synth-doc", "status": "done",
               "verdict": {"type": "doc", "publishable": False, "overall": 6.5,
                           "threshold": 7.0, "review_independence": "cross-vendor",
                           "review_resources": {"completed_seats": 1, "required_seats": 1}},
               "steps": [{"n": 1, "status": "done", "cost_usd": 1.2, "summary": "修订完成"}]}
        serial = {"id": "r-synth-serial", "status": "done",
                  "verdict": {"type": "serial_novel", "publishable": False,
                              "threshold": 7.0, "review_independence": "cross-vendor",
                              "review_status": "evaluated", "review_degraded": True,
                              "review_quality": "degraded",
                              "review_degradation_reasons": ["评分低于最低门槛"],
                              "global_reviewer_count": 2},
                  "steps": [{"n": 1, "status": "done", "cost_usd": 1.2,
                             "summary": "评审完成"}]}
        for run in (doc, serial):
            d = qr.judge_run(run)
            r = qr.judge_recovery(d)
            self.assertTrue(d["fields_complete"], run["id"])
            self.assertIs(d["publishable"], False, run["id"])
            self.assertTrue(r["recovery_ok"], "%s: %s" % (run["id"], r["blockers"]))
        # review_degraded=True 只表示评分没过线，不该被当成「评审没跑」。
        self.assertEqual(qr.judge_run(serial)["incomplete_review_reasons"], [])
        print("合成边界样本 2 条（反向覆盖用，不计入真样本数）")

    def test_mutation_review_status_flips_recovery_conclusion(self):
        """变异：改掉见底信号，恢复判据必须翻面，否则它是恒 False 的死判据。"""
        run = copy.deepcopy(_by_schema("serial_novel")[0])
        self.assertFalse(qr.judge_recovery(qr.judge_run(run))["recovery_ok"])
        verdict = run["verdict"]
        verdict["review_status"] = "evaluated"
        verdict["review_degradation_reasons"] = ["评分低于最低门槛"]
        for step in run["steps"]:
            step["summary"] = "done"
            step["note"] = ""
        d = qr.judge_run(run)
        self.assertEqual(d["rate_limited_steps"], [])
        self.assertEqual(d["successful_model_steps"], 6)
        self.assertTrue(qr.judge_recovery(d)["recovery_ok"],
                        "改完仍判未恢复 = 判据没有真在判")
        verdict["review_status"] = "not_evaluated"
        self.assertFalse(qr.judge_recovery(qr.judge_run(run))["recovery_ok"])


class VerifyRunCliTest(unittest.TestCase):
    def test_verify_run_prints_recovery_conclusion(self):
        """判据得有消费点：CLI 必须把恢复结论打到 stdout。"""
        run = _by_schema("serial_novel")[0]
        buf = io.StringIO()
        with mock.patch.object(qr, "api_get", return_value={"run": run}), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--verify-run", run["id"]])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("恢复判据：未通过", text)
        self.assertIn("review_status", text)
        self.assertNotIn("字段不足", text)
        post.assert_not_called()


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
