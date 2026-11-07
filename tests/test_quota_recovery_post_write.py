# -*- coding: utf-8 -*-
"""11-09 九步第 ⑧ 步写后复核守测：`--retry-one` 打印的 `$RID` 必须可信。

14:1x 实案：⑨ 刚补了「验收回执身份核对」，可 ⑧ 交给它的 run id 本身
没有经过任何对账。`judge_retry_post_write` 的 `new_run_id` 是**死参数**
（只原样塞回返回值、零消费），且 `target_delta=0` 不报问题 —— 生产
26 条真实 run 派生的写后态实证「响应说建了 run、state 里没新增」与
「state 新增的是另一条 run」都会判「复核通过」。同时 `cmd_retry_one`
只认 `res.get("ok") is False`，而 `main.py:2607` 的 503 入队失败
（`store.retry_task` 已建 run 落盘、`_enqueue_run` 失败）返回
`{"error":..., "run_id":...}` **没有 ok 键**，会被当成成功；真实 503 则
被 `api_post` 抛成 HTTPError 直接 traceback，POST 已发生、run 不可回滚
却不给处置建议。

真样本：`tests/fixtures/quota_recovery_post_write.json` 里生产 `/api/state`
只读导出的 26 条真实 run 身份字段，加上 `app/main.py:2599-2614` 源码取证的
三种真实响应形状。派生的写后边界态单列标注、不计真被判过样本数。

决策 18：打印真被判过的真实样本数。绝不碰 8765：所有 HTTP 都是 mock，
CLI 写路径复用同族守测的 FakeAPI / 真实 inherit 夹具，不另造合成状态。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_post_write.json"
MAIN = ROOT / "app" / "main.py"
HANDOFF = ROOT / "迭代计划.md"

_spec = importlib.util.spec_from_file_location("quota_recovery_post_write", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

# CLI 写路径复用同族守测的真实夹具与 FakeAPI（不另造合成 inherit 状态）。
# unittest discover -s tests 会把 tests/ 加进 sys.path，pytest 则不一定，故显式补。
sys.path.insert(0, str(ROOT / "tests"))
from test_quota_recovery_retry_one import (  # noqa: E402
    FakeAPI, _run_cli, _state, EXPECTED_RECOMMENDED, AFTER,
)

_fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
REAL_RUNS = _fx["state_before"]["runs"]
TARGET = qr.RESUME_TASK_IDS[2]
OTHER = qr.RESUME_TASK_IDS[0]
NEW = "r-new-0001"

# 写前态：26 条真实 run 派生为「重启收尸后」（单列标注、不计真样本）
def _before():
    s = copy.deepcopy(_fx["state_before"])
    for r in s["runs"]:
        if r["status"] in ("queued", "running"):
            r["status"] = "failed"
    return s


def _after_with(extra):
    s = _before()
    s["runs"] = s["runs"] + list(extra)
    return s


def _target_run(rid=NEW, task_id=None, status="queued"):
    return {"id": rid, "task_id": task_id or TARGET, "status": status}


class RealFixtureTest(unittest.TestCase):
    """夹具是真取证，不是合成稿。"""

    def test_real_run_sample_count_is_26(self):
        self.assertEqual(len(REAL_RUNS), 26)
        self.assertEqual(_fx["scan_summary"]["real_run_samples"], len(REAL_RUNS))
        print("真被判过的真实 run 样本数 %d" % len(REAL_RUNS))

    def test_every_real_run_has_identity_fields(self):
        for r in REAL_RUNS:
            self.assertTrue(r["id"], r)
            self.assertTrue(r["task_id"], r)
            self.assertIn(r["status"], ("queued", "running", "failed",
                                        "timeout", "done", "cancelled"), r)
        self.assertEqual(len(_fx["scan_summary"]["active_run_ids"]), 4)

    def test_scan_summary_matches_real_runs(self):
        from collections import Counter
        self.assertEqual(_fx["scan_summary"]["status_counts"],
                         dict(Counter(r["status"] for r in REAL_RUNS)))

    def test_response_shapes_match_current_main_py(self):
        """源码取证的形状必须与当前 main.py 逐字一致，防响应形状漂移。"""
        src = MAIN.read_text(encoding="utf-8")
        shapes = _fx["response_shapes"]
        self.assertIn(shapes["200_success"]["code"].strip(), src)
        self.assertIn(shapes["400_retry_rejected"]["code"].strip(), src)
        self.assertIn(shapes["503_enqueue_failed"]["code"].strip(), src)
        self.assertIn('"ok": True, "run_id": run["id"]', src)
        self.assertIn('"error": qerr, "run_id": run["id"]', src)

    def test_503_shape_has_no_ok_key(self):
        body = _fx["response_shapes"]["503_enqueue_failed"]["body"]
        self.assertNotIn("ok", body)
        self.assertIn("run_id", body)
        self.assertIn("error", body)

    def test_derived_states_are_marked_synthetic(self):
        self.assertIn("合成", _fx["derived"]["note"])
        self.assertIn("不计", _fx["derived"]["note"])


class RetryResponseTest(unittest.TestCase):
    """写入响应三路判据：判不了不上绿标。"""

    def test_200_success(self):
        r = qr.judge_retry_response({"ok": True, "run_id": NEW})
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["run_id"], NEW)

    def test_400_retry_rejected_is_blocked(self):
        r = qr.judge_retry_response({"error": "任务仍在运行中"})
        self.assertEqual(r["verdict"], "blocked")
        self.assertEqual(r["run_id"], "")

    def test_503_enqueue_failed_is_blocked_not_success(self):
        # main.py:2607：store.retry_task 已建 run 落盘，入队失败才 503。
        r = qr.judge_retry_response({"error": "queue full", "run_id": NEW})
        self.assertEqual(r["verdict"], "blocked")
        self.assertEqual(r["run_id"], "")
        self.assertIn("写入失败", "；".join(r["blockers"]))

    def test_missing_ok_with_run_id_is_unknown(self):
        r = qr.judge_retry_response({"run_id": NEW})
        self.assertEqual(r["verdict"], "unknown")
        self.assertEqual(r["run_id"], "")
        self.assertIn("503", r["blockers"][0])

    def test_non_boolean_ok_is_unknown(self):
        self.assertEqual(qr.judge_retry_response(
            {"ok": "yes", "run_id": NEW})["verdict"], "unknown")

    def test_ok_true_without_run_id_is_unknown(self):
        self.assertEqual(qr.judge_retry_response(
            {"ok": True})["verdict"], "unknown")

    def test_explicit_ok_false_with_run_id_is_blocked(self):
        self.assertEqual(qr.judge_retry_response(
            {"ok": False, "error": "x", "run_id": NEW})["verdict"], "blocked")

    def test_non_dict_response_is_unknown(self):
        for bad in (None, [], "ok", 1):
            self.assertEqual(qr.judge_retry_response(bad)["verdict"], "unknown")

    def test_ok_true_with_error_is_blocked(self):
        self.assertEqual(qr.judge_retry_response(
            {"ok": True, "run_id": NEW, "error": "late"})["verdict"], "blocked")


class PostWriteReviewTest(unittest.TestCase):
    """写后复核真正消费 new_run_id；unknown 与 blocked 分开报。"""

    def _judge(self, extra, rid=NEW):
        return qr.judge_retry_post_write(
            _before(), _after_with(extra), TARGET, rid)

    def test_normal_path_still_green(self):
        r = self._judge([_target_run()])
        self.assertEqual(r["verdict"], "ok")
        self.assertTrue(r["ok"])
        self.assertEqual(r["problems"], [])

    def test_no_new_active_run_is_unknown_not_green(self):
        r = self._judge([])
        self.assertEqual(r["verdict"], "unknown")
        self.assertFalse(r["ok"])
        self.assertTrue(any("delta=0" in n for n in r["notes"]))

    def test_state_new_run_id_mismatch_is_blocked(self):
        r = self._judge([_target_run(rid="r-other-9999")])
        self.assertEqual(r["verdict"], "blocked")
        self.assertFalse(r["ok"])
        self.assertTrue(any("id 不匹配" in b for b in r["blockers"]))
        self.assertTrue(any("不是响应声明的" in b for b in r["blockers"]))

    def test_four_books_racing_still_blocked(self):
        r = self._judge([_target_run(), _target_run(rid="r-other-2", task_id=OTHER)])
        self.assertEqual(r["verdict"], "blocked")
        self.assertTrue(any("四本齐跑" in b for b in r["blockers"]))

    def test_two_new_runs_for_target_still_blocked(self):
        r = self._judge([_target_run(), _target_run(rid="r-new-0002")])
        self.assertEqual(r["verdict"], "blocked")
        self.assertTrue(any("应恰好 1 条" in b for b in r["blockers"]))

    def test_run_id_pointing_at_other_task_is_blocked(self):
        extra = [_target_run(task_id=OTHER), _target_run(rid="r-ok-2")]
        r = self._judge(extra)
        self.assertEqual(r["verdict"], "blocked")
        self.assertTrue(any("写串了本子" in b for b in r["blockers"]))

    def test_new_run_not_active_is_unknown(self):
        r = self._judge([_target_run(status="done")])
        self.assertEqual(r["verdict"], "unknown")
        self.assertFalse(r["ok"])

    def test_empty_or_non_string_run_id_is_unknown(self):
        for rid in ("", None, 123, "   "):
            r = self._judge([_target_run()], rid=rid)
            self.assertEqual(r["verdict"], "unknown", rid)
            self.assertFalse(r["ok"], rid)

    def test_compat_keys_preserved(self):
        r = self._judge([_target_run()])
        for k in ("ok", "problems", "new_run_id", "active_before", "active_after"):
            self.assertIn(k, r)
        self.assertEqual(r["new_run_id"], NEW)


class WritePathCliTest(unittest.TestCase):
    """CLI 全链路：所有 HTTP 都是 mock，绝不碰 8765。"""

    def _run(self, api, expect_rc, must, must_not=()):
        rc, text = _run_cli(["--retry-one", "--confirm-quota-restored"],
                            api, now=AFTER)
        self.assertEqual(rc, expect_rc, text)
        for s in must:
            self.assertIn(s, text)
        for s in must_not:
            self.assertNotIn(s, text)
        return text

    def test_success_path_posts_once_and_prints_verified_rid(self):
        api = FakeAPI(_state(reaped=True),
                      _state(reaped=True, extra_runs=[
                          {"id": NEW, "task_id": EXPECTED_RECOMMENDED,
                           "status": "queued"}]),
                      post_result={"ok": True, "run_id": NEW})
        self._run(api, 0, ["复核通过：", "--verify-run " + NEW,
                           "--as-recovery", "$RID 已与 /api/state 对账"])
        self.assertEqual(len(api.posts), 1, "只准 POST 一次")
        self.assertGreaterEqual(api.gets.count("/api/state"), 2,
                                "写后必须真拉 /api/state 做对账")

    def test_503_shape_body_is_not_reported_as_success(self):
        api = FakeAPI(_state(reaped=True), _state(reaped=True),
                      post_result={"error": "queue full", "run_id": NEW})
        self._run(api, 1, ["写入失败/不可用"], ["复核通过："])
        self.assertEqual(len(api.posts), 1)

    def test_missing_ok_is_not_reported_as_success(self):
        api = FakeAPI(_state(reaped=True), _state(reaped=True),
                      post_result={"run_id": NEW})
        self._run(api, 1, ["写入失败/不可用", "unknown"], ["复核通过："])

    def test_explicit_failure_keeps_old_message(self):
        api = FakeAPI(_state(reaped=True), _state(reaped=True),
                      post_result={"ok": False, "error": "任务仍在运行中"})
        self._run(api, 1, ["写入失败"], ["复核通过："])

    def test_api_post_exception_is_friendly_and_never_retried(self):
        class BoomAPI(FakeAPI):
            def post(self, base, path, body):
                self.posts.append((path, body))
                raise RuntimeError("HTTP Error 503: Service Unavailable")

        api = BoomAPI(_state(reaped=True))
        self._run(api, 1, ["写入请求异常", "别重跑 ⑧", "/api/state"],
                  ["Traceback", "复核通过："])
        self.assertEqual(len(api.posts), 1, "绝不重试")

    def test_no_new_run_in_state_is_not_green(self):
        api = FakeAPI(_state(reaped=True), _state(reaped=True),
                      post_result={"ok": True, "run_id": NEW})
        self._run(api, 1, ["复核不可用", "判不了", "不上绿标"], ["复核通过："])

    def test_response_id_mismatch_is_not_green(self):
        api = FakeAPI(_state(reaped=True),
                      _state(reaped=True, extra_runs=[
                          {"id": "r-other-9999", "task_id": EXPECTED_RECOMMENDED,
                           "status": "queued"}]),
                      post_result={"ok": True, "run_id": NEW})
        self._run(api, 1, ["复核失败：", "id 不匹配"], ["复核通过："])

    def test_four_books_racing_still_blocked_in_cli(self):
        api = FakeAPI(_state(reaped=True),
                      _state(reaped=True, extra_runs=[
                          {"id": NEW, "task_id": EXPECTED_RECOMMENDED,
                           "status": "queued"},
                          {"id": "r-other-2", "task_id": "t-20261022-104907-1000",
                           "status": "queued"}]),
                      post_result={"ok": True, "run_id": NEW})
        self._run(api, 1, ["复核失败：", "四本齐跑"], ["复核通过："])


class SourceAnchorTest(unittest.TestCase):
    """锁住接线与消费点，别让判据变成死代码。"""

    def setUp(self):
        self.src = SCRIPT.read_text(encoding="utf-8")

    def test_response_judge_is_consumed_by_cmd_retry_one(self):
        self.assertIn("resp = judge_retry_response(res)", self.src)
        self.assertIn('if resp["verdict"] != "ok":', self.src)

    def test_post_write_judge_consumes_run_id(self):
        # new_run_id 不再只是塞回返回值：必须有 id 对账分支。
        self.assertIn("if new_ids and new_ids != {rid}:", self.src)
        self.assertIn('str(r.get("id") or "") == rid', self.src)
        self.assertIn("if target_delta == 0:", self.src)

    def test_api_post_exception_is_caught_not_swallowed_as_success(self):
        self.assertIn("except Exception as exc:", self.src)
        self.assertIn("POST 可能已发生、不能回滚", self.src)
        self.assertIn("别重跑 ⑧", self.src)

    def test_handoff_step_8_has_consumer(self):
        text = HANDOFF.read_bytes().decode("utf-8")
        self.assertIn("$RID` 已与 `/api/state` 对账", text)
        self.assertIn("写入请求异常立刻停手", text)

    def test_handoff_step_9_unchanged(self):
        text = HANDOFF.read_bytes().decode("utf-8")
        self.assertIn('--verify-run "$RID" --as-recovery', text)


if __name__ == "__main__":
    unittest.main()
