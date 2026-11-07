# -*- coding: utf-8 -*-
"""11-09 九步 ⑧→⑨ 交接闭环守测：`--expect-task` 让 ⑨ 真正消费 ⑧ 的对账结论。

为什么需要这一轮（11-07 14:3x 倒查）
------------------------------------
第 ⑧ 步 `--retry-one` 在 14:22 已经把 `$RID` 与写后 `/api/state` 对过账，
打印「只 <target> 新增 1 条 active run，其余 3 本未动」。但第 ⑨ 步
`--verify-run --as-recovery`（14:00 落地）的 `judge_verify_target` 只要求
`task_id ∈ 4 本白名单` + `created_at` 晚于窗口 + `status=done`，**完全没消费
⑧ 的 target**：⑧ 明明只 retry 了 A，⑨ 贴成 B 照样给绿灯。

这不是假想：九步第 ③ 步会把 4 个定时任务开回来，11-09 14:09 之后同批次
**别本书**也可能跑出一条满足前三道的 done run；贴错 `$RID` 拿到它就是
「验收假绿」——拿一本根本没验的书宣布额度恢复，紧接着四本齐跑抢见底池。

三处改动，默认路径逐字不变
--------------------------
1. `judge_verify_target` 新增可选 `expected_task_id`（第 ④ 道）：只对传入
   非空的情形收窄，传错（不在 4 本白名单）一律 `unknown` fail-closed；
2. `cmd_verify_run` 增详情 `run.id` 与请求 `$RID` 的交叉核对，防「详情
   被换成另一条对象还照样给绿灯」；
3. ⑧ 打印一整条带 `$RID` 与 `$TID` 的完整 ⑨ 命令，人整行复制、不手敲。

夹具口径（决策 18）
------------------
复用 `tests/fixtures/quota_recovery_verify_target.json`：它是生产库
`%APPDATA%/CodeBee/runs/*/run.json` **1217 条落盘 run 全量只读扫描**的结论
冻结 + 11 条真实身份样本，`resume_task_ids` 是 4 个真实待续跑 task。
「同一批次另一本书」的反例由**真实 task_id** 派生字段、单列标注、
**不计真样本数**；窗口后的 run 现在（11-07）根本不存在，不伪造它。

dev / prod
----------
纯函数 + mock 掉的 HTTP，零网络、零写盘、零额度；`unittest.TestCase`
让 pytest 与 `scripts/run_protected_suite.py` 的 unittest discover 双收集。
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_verify_target.json"
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
HANDOVER = ROOT / "迭代计划.md"
_spec = importlib.util.spec_from_file_location("quota_recovery_verify_handoff", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

AT = "2026-11-09 14:09"
TARGET = "t-20261104-170153-4815"   # ⑧ --check-inherit 推荐、一章不丢的那本
OTHER = "t-20261022-104907-1000"    # 真实落盘的同一批次另一本


def _fx():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _derived():
    """合成：11-09 新建且 verdict 达标的连载 run，不计真样本数。"""
    return copy.deepcopy(_fx()["derived"]["new_recovery_run"])


def _other_book(task_id=OTHER):
    """合成：贴错 `$RID` 到同一批次另一本书的等价场景，不计真样本数。

    `task_id` 一律取夹具里 4 个真实待续跑 task 之一，身份字段不虚构。
    """
    d = _derived()
    d["id"] = "r-derived-other-book-" + task_id[-4:]
    d["task_id"] = task_id
    return d


def _cli(argv, obj):
    buf = io.StringIO()
    with mock.patch.object(qr, "api_get", return_value={"run": obj}), \
         mock.patch.object(qr, "api_post") as post, \
         redirect_stdout(buf):
        rc = qr.main(argv)
    return rc, buf.getvalue(), post


def _func(name):
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SCRIPT.read_text(encoding="utf-8"), node) or ""
    raise AssertionError("脚本里找不到函数 %s" % name)


class FixtureTruthTest(unittest.TestCase):
    """夹具必须来自真实落盘，别拿合成数据冒充（决策 18）。"""

    def test_real_task_ids_and_scanned_sample_count(self):
        fx = _fx()
        s = fx["scan_summary"]
        self.assertEqual(s["total_run_json_judged"], 1217)
        self.assertEqual(len(fx["resume_task_ids"]), 4)
        self.assertEqual(TARGET, fx["derived"]["new_recovery_run"]["task_id"],
                         "⑧ 的目标不能硬编码，必须与夹具派生 run 对齐")
        self.assertIn(OTHER, fx["resume_task_ids"])
        real_tasks = {r["task_id"] for r in fx["real_runs"]}
        judged = sum(1 for tid in fx["resume_task_ids"] if tid in real_tasks)
        self.assertGreaterEqual(judged, 3)
        print("真被判过的落盘 run 样本数 %d；真实待续跑 task %d/4；"
              "真实身份样本 %d 条" % (
                  s["total_run_json_judged"], judged, len(fx["real_runs"])))

    def test_other_book_reverse_example_is_derived_and_labelled(self):
        d = _other_book()
        self.assertIn(d["task_id"], _fx()["resume_task_ids"])
        self.assertNotEqual(d["id"], _derived()["id"])
        self.assertTrue(qr.judge_recovery(qr.judge_run(d))["recovery_ok"],
                        "必须是 judge_recovery 会给绿灯的对象，才证明缺口真实")
        self.assertEqual(qr.judge_verify_target(d, AT)["verdict"], "ok",
                         "不带 --expect-task 时旧判据确实放行（假绿实证）")


class JudgeHandoffTest(unittest.TestCase):

    def test_correct_target_is_ok_and_field_count_unchanged(self):
        r = qr.judge_verify_target(_derived(), AT, expected_task_id=TARGET)
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["judged_fields"], 3,
                         "第 ④ 道是同一字段的第二条约束，别虚报判过的字段数")
        self.assertFalse(r["blockers"])

    def test_without_expect_task_matches_1400_behaviour(self):
        old = qr.judge_verify_target(_derived(), AT)
        new = qr.judge_verify_target(_derived(), AT, expected_task_id=TARGET)
        self.assertEqual(old["verdict"], new["verdict"])
        self.assertEqual(old["judged_fields"], new["judged_fields"])

    def test_same_batch_other_book_is_blocked_with_distinct_reason(self):
        d = _other_book()
        r = qr.judge_verify_target(d, AT, expected_task_id=TARGET)
        self.assertEqual(r["verdict"], "blocked")
        self.assertTrue(any("不是 ⑧ retry 的目标" in b for b in r["blockers"]))
        self.assertFalse(any("不在 4 个待续跑 task" in b for b in r["blockers"]),
                         "两类 blocker 必须分开：塌成一条会静默通过（14:00 的教训）")

    def test_all_three_other_real_books_are_blocked(self):
        fx = _fx()
        others = [t for t in fx["resume_task_ids"] if t != TARGET]
        self.assertEqual(len(others), 3)
        judged = 0
        for tid in others:
            r = qr.judge_verify_target(_other_book(tid), AT,
                                       expected_task_id=TARGET)
            self.assertEqual(r["verdict"], "blocked", tid)
            self.assertTrue(any("不是 ⑧ retry 的目标" in b for b in r["blockers"]), tid)
            judged += 1
        print("真被判过并拦下的「同一批次另一本书」真实 task %d/3" % judged)

    def test_expect_task_outside_whitelist_is_unknown_fail_closed(self):
        r = qr.judge_verify_target(_derived(), AT,
                                   expected_task_id="t-20261024-205325-3934")
        self.assertEqual(r["verdict"], "unknown")
        self.assertEqual(r["judged_fields"], 0)
        self.assertIn("--expect-task", r["blockers"][0])
        self.assertIn("不在 4 个待续跑 task", r["blockers"][0])

    def test_empty_expect_task_does_not_narrow_or_fake_green(self):
        for val in (None, "", "   "):
            self.assertEqual(
                qr.judge_verify_target(_derived(), AT,
                                       expected_task_id=val)["verdict"], "ok")
        self.assertEqual(
            qr.judge_verify_target(_other_book(), AT,
                                   expected_task_id="")["verdict"], "ok",
            "不收窄就是 14:00 的旧行为，别偷偷加严破坏兼容")


class CliHandoffTest(unittest.TestCase):

    def test_as_recovery_with_correct_target_ok_and_no_warning(self):
        obj = _derived()
        rc, out, post = _cli(["--verify-run", obj["id"], "--as-recovery",
                              "--expect-task", TARGET], obj)
        self.assertEqual(rc, 0)
        self.assertIn("本次续跑身份核对：ok", out)
        self.assertIn("恢复判据：通过", out)
        self.assertNotIn("验收不可用", out)
        self.assertNotIn("未带 --expect-task", out)
        post.assert_not_called()

    def test_as_recovery_with_wrong_target_blocks_conclusion(self):
        obj = _other_book()
        rc, out, post = _cli(["--verify-run", obj["id"], "--as-recovery",
                              "--expect-task", TARGET], obj)
        self.assertEqual(rc, 2)
        self.assertIn("本次续跑身份核对：blocked", out)
        self.assertIn("不是 ⑧ retry 的目标", out)
        self.assertIn("验收不可用", out)
        self.assertNotIn("恢复判据：通过", out)
        self.assertNotIn("恢复判据：未通过", out)
        post.assert_not_called()

    def test_detail_id_mismatch_is_fail_closed(self):
        obj = _derived()
        obj["id"] = "r-derived-detail-mismatch"
        rc, out, post = _cli(["--verify-run", "r-expected-by-8", "--as-recovery",
                              "--expect-task", TARGET], obj)
        self.assertEqual(rc, 2)
        self.assertIn("验收不可用", out)
        self.assertIn("不一致", out)
        self.assertNotIn("恢复判据：通过", out)
        post.assert_not_called()

    def test_without_expect_task_keeps_1400_path_and_warns(self):
        obj = _derived()
        rc, out, post = _cli(["--verify-run", obj["id"], "--as-recovery"], obj)
        self.assertEqual(rc, 0, "普通诊断路径逐字不变")
        self.assertIn("本次续跑身份核对：ok", out)
        self.assertIn("恢复判据：通过", out)
        self.assertIn("未带 --expect-task", out)
        self.assertIn("没消费 ⑧ 的目标 task", out)
        post.assert_not_called()

    def test_invalid_expect_task_is_unknown_not_ok(self):
        obj = _derived()
        rc, out, post = _cli(["--verify-run", obj["id"], "--as-recovery",
                              "--expect-task", "t-not-a-real-task"], obj)
        self.assertEqual(rc, 2)
        self.assertIn("本次续跑身份核对：unknown", out)
        self.assertIn("验收不可用", out)
        post.assert_not_called()

    def test_default_diagnostic_path_untouched(self):
        obj = _other_book()
        rc, out, post = _cli(["--verify-run", obj["id"]], obj)
        self.assertEqual(rc, 0)
        self.assertIn("恢复判据：通过", out)
        self.assertNotIn("本次续跑身份核对", out)
        self.assertNotIn("--expect-task", out)
        post.assert_not_called()

    def test_main_wires_expect_task_into_verify(self):
        src = _func("main")
        self.assertIn('"--expect-task"', src)
        self.assertIn("expect_task=args.expect_task", src)
        vsrc = _func("cmd_verify_run")
        self.assertIn("expected_task_id=expect_task", vsrc)
        self.assertIn("detail_id != run_id", vsrc)
        self.assertIn("judge_verify_target(", vsrc)


class ConsumptionPointTest(unittest.TestCase):
    """判据沉默三问：产出有没有被下游消费，防死代码。"""

    def test_step8_prints_full_step9_command_with_both_values(self):
        src = _func("cmd_retry_one")
        self.assertIn("--expect-task", src)
        self.assertIn("⑨ 完整命令", src)
        self.assertIn("--as-recovery", src)
        self.assertIn('plan["target"]', src)
        self.assertIn("new_run_id", src)

    def test_handover_step9_and_acceptance_consume_tid(self):
        text = HANDOVER.read_text(encoding="utf-8")
        self.assertIn('--verify-run "$RID" --as-recovery --expect-task "$TID"', text)
        self.assertIn("$TID 取 ⑧ 打印的", text)

    def test_script_line_endings_stay_lf(self):
        raw = SCRIPT.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0, "仓库脚本必须保持 LF")


if __name__ == "__main__":
    unittest.main()
