# -*- coding: utf-8 -*-
"""11-09 九步流程第 ⑨ 步：验收回执「身份核对」判据守测（只读、零写入）。

夹具 `tests/fixtures/quota_recovery_verify_target.json` 是 2026-11-07 13:5x
对生产库 `%APPDATA%/CodeBee/runs/*/run.json` **1217 条落盘 run 全量只读扫描**
的结论冻结 + 11 条真实身份样本（3 条假绿反例 / 4 条 09:48 重启前 active
锚点 / 4 条同 task 见底期旧 run）。身份字段 `task_id` / `status` /
`created_at` 是落盘真原文、未改写；`schema` / `recovery_ok` /
`successful_model_steps` 是用完整 run 经 `judge_run` + `judge_recovery`
实算后冻结的结论，不重存 120~180KB 的 `messages` / `agent_context` /
`outline` / `output` 长转录——裁字符串会造出本来不存在的假反例。
`derived` 里的样本均为合成，**单列标注、不计真样本数**。

为什么需要这条判据（11-07 复核）
--------------------------------
九步 ⑧ 打印新 run id、⑨ 靠人手复制 `$RID`。而 `judge_recovery` 只看
verdict / steps，**完全不看 `status`，也不核对这条 run 是不是本次续跑产生的**。
生产 1217 条落盘 run 实算有 **3 条历史 doc run**（10-25 / 10-26，其中 1 条
`status=failed`）会被判「恢复判据：通过」——贴错 `$RID` 就是验收假绿，
紧接着四本齐跑抢见底池；贴成 4 个待续跑 task 下的 **92 条见底期旧 run**
则全是 `schema=unknown` → 又会误导「额度还没恢复」。两个方向都是真误判。

`--as-recovery`（默认关闭）用纯函数 `judge_verify_target` 核对三件事：
`task_id ∈ 4 个待续跑 task`、`created_at` 严格晚于恢复窗口、
`status=done`。判不了一律 `unknown`、不上绿标；非 `ok` 时不再输出恢复
结论、打印「验收不可用」并返回 2。默认诊断路径逐字不变。

守几件事
--------
1. 夹具是真数据：报「真被判过样本数」，3 条反例确实 `recovery_ok=True`
   且含 `status=failed`（证明原判据不看 status），8 条待续跑旧 run
   确实全 `schema=unknown`。
2. 判据八场景：派生新 run `ok`；3 条真实反例 / 4 锚点 / 4 同 task 旧 run
   `blocked`；缺字段与格式异常 `unknown`；active / 未收尾 status `blocked`；
   `created_at` 窗口边界（14:09:00 拦、14:09:01 放）；恢复时刻解析不了
   `unknown`。`unknown` 绝不上绿标（决策 18）。
3. CLI：默认路径对假绿反例仍打印「恢复判据：通过」（**证明缺口真实且默认
   行为不变**）；`--as-recovery` 拦住它、返回 2、不输出恢复结论；派生新 run
   正常放行；详情读不到友好报错返回 2；全程零 `api_post`。
4. 源码锚点：`cmd_verify_run` 真调 `judge_verify_target` 且按 `as_recovery`
   降级、`main()` 把开关与 `--recovery-at` 传进来（防死代码）。
5. 消费点：交接单九步命令块 ⑨ 与「每步验收」真的带 `--as-recovery`。

dev / prod
----------
纯函数 + mock 掉的 HTTP，零网络、零写盘、零额度；dev 直接
`py -3.13 -m pytest tests/test_quota_recovery_verify_target.py -q`，
prod 由 `scripts/run_protected_suite.py` 的 unittest discover 收集。
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
_spec = importlib.util.spec_from_file_location("quota_recovery_verify_target", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)


def _fx():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _real(category=None):
    rows = _fx()["real_runs"]
    return [r for r in rows if category is None or r["category"] == category]


def _derived():
    """合成：11-09 新建且 verdict 达标的连载 run，不计真样本数。"""
    return copy.deepcopy(_fx()["derived"]["new_recovery_run"])


def _wrong_identity():
    """合成：`judge_recovery` 会给绿灯、但身份是历史书 —— 贴错 $RID 的等价场景。"""
    d = _derived()
    g = _real("false_green_historical")[0]
    d["id"] = "r-derived-wrong-identity"
    d["task_id"] = g["task_id"]
    d["created_at"] = g["created_at"]
    d["status"] = "done"
    return d


def _identity(row):
    """只取身份判据消费的三个真原文字段。"""
    return {"task_id": row["task_id"], "status": row["status"],
            "created_at": row["created_at"]}


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
            return node
    raise AssertionError("脚本里找不到函数 %s" % name)


class FixtureTruthTest(unittest.TestCase):
    """夹具必须来自真实落盘，别拿合成数据冒充（决策 18）。"""

    def test_scan_summary_and_real_sample_count(self):
        fx = _fx()
        s = fx["scan_summary"]
        self.assertEqual(s["total_run_json_judged"], 1217)
        self.assertEqual(s["recovery_ok_count"], 3)
        self.assertEqual(len(s["recovery_ok_ids"]), 3)
        self.assertEqual(s["resume_task_run_count"], 92)
        self.assertEqual(s["resume_task_recovery_ok_count"], 0)
        self.assertEqual(s["resume_task_schema_counts"], {"unknown": 92})
        self.assertEqual(len(_real()), 11)
        self.assertEqual(len(_real("false_green_historical")), 3)
        self.assertEqual(len(_real("anchor_active_before_restart")), 4)
        self.assertEqual(len(_real("same_task_old")), 4)
        self.assertEqual(fx["recovery_window"], qr.QUOTA_RECOVERY_AT)
        self.assertEqual(tuple(fx["resume_task_ids"]), qr.RESUME_TASK_IDS)
        print("真被判过的落盘 run 样本数 %d；夹具收录真实身份样本 %d 条"
              % (s["total_run_json_judged"], len(_real())))

    def test_false_green_rows_really_pass_the_old_recovery_judge(self):
        rows = _real("false_green_historical")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["recovery_ok"] is True for r in rows))
        self.assertIn("failed", {r["status"] for r in rows},
                      "必须含 status=failed 的样本，才证明旧判据不看 status")
        self.assertTrue(all(r["schema"] == "doc" for r in rows))
        self.assertTrue(all(r["successful_model_steps"] >= 1 for r in rows))
        print("真实假绿反例 3/3 会被 judge_recovery 判通过，status=%s"
              % sorted({r["status"] for r in rows}))

    def test_all_real_rows_carry_verbatim_identity_fields(self):
        judged = 0
        for r in _real():
            for key in ("id", "task_id", "status", "created_at"):
                self.assertIsInstance(r[key], str, (r["id"], key))
                self.assertTrue(r[key], (r["id"], key))
            self.assertIsNotNone(qr._parse_run_created_at(r["created_at"]), r["id"])
            judged += 1
        self.assertEqual(judged, 11)
        print("真被判过身份字段的真实 run 样本数 %d/11" % judged)

    def test_resume_task_real_rows_are_all_schema_unknown(self):
        rows = (_real("anchor_active_before_restart") + _real("same_task_old"))
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r["schema"] == "unknown" for r in rows))
        self.assertTrue(all(r["recovery_ok"] is False for r in rows))
        self.assertTrue(all(r["task_id"] in qr.RESUME_TASK_IDS for r in rows))


class JudgeVerifyTargetTest(unittest.TestCase):
    AT = "2026-11-09 14:09"

    def test_derived_new_recovery_run_is_ok(self):
        d = _derived()
        r = qr.judge_verify_target(d, self.AT)
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["judged_fields"], 3)
        self.assertTrue(qr.judge_recovery(qr.judge_run(d))["recovery_ok"])

    def test_all_three_real_false_green_runs_are_blocked(self):
        judged = 0
        for row in _real("false_green_historical"):
            r = qr.judge_verify_target(_identity(row), self.AT)
            self.assertEqual(r["verdict"], "blocked", row["id"])
            self.assertTrue(any("不在 4 个待续跑 task" in b for b in r["blockers"]))
            self.assertTrue(any("不晚于恢复窗口" in b for b in r["blockers"]))
            judged += 1
        self.assertEqual(judged, 3)
        print("真被判过的真实假绿反例样本数 %d/3，全部被身份判据拦住" % judged)

    def test_anchor_and_same_task_old_runs_are_blocked(self):
        judged = 0
        for row in (_real("anchor_active_before_restart") + _real("same_task_old")):
            r = qr.judge_verify_target(_identity(row), self.AT)
            self.assertEqual(r["verdict"], "blocked", row["id"])
            judged += 1
        self.assertEqual(judged, 8)
        print("真被判过的待续跑 task 落盘 run 样本数 %d/8，全部被拦住" % judged)

    def test_missing_or_malformed_fields_are_unknown_never_ok(self):
        cases = [
            {"status": "done", "created_at": "2026-11-09 14:20:01"},
            {"task_id": qr.RESUME_TASK_IDS[0], "status": "done"},
            {"task_id": qr.RESUME_TASK_IDS[0], "status": "done",
             "created_at": "2026-11-09 14:20"},
            {"task_id": qr.RESUME_TASK_IDS[0], "status": "done",
             "created_at": "2026-13-45 99:99:99"},
            {"task_id": qr.RESUME_TASK_IDS[0],
             "created_at": "2026-11-09 14:20:01"},
        ]
        for obj in cases:
            r = qr.judge_verify_target(obj, self.AT)
            self.assertEqual(r["verdict"], "unknown", obj)
            self.assertNotEqual(r["verdict"], "ok")
            self.assertTrue(r["blockers"])

    def test_active_and_unfinished_statuses_are_blocked(self):
        # 两类必须分开报：active 是「等它跑完再验」，未收尾是「这次跑挂了」，
        # 操作建议完全不同；只断言 blocked 会让两条分支塌成一条也静默通过。
        for status in ("running", "queued"):
            obj = _derived()
            obj["status"] = status
            r = qr.judge_verify_target(obj, self.AT)
            self.assertEqual(r["verdict"], "blocked", status)
            self.assertTrue(any("还在跑" in b for b in r["blockers"]), status)
            self.assertFalse(any("流程没收尾" in b for b in r["blockers"]), status)
        for status in ("failed", "cancelled", "timeout"):
            obj = _derived()
            obj["status"] = status
            r = qr.judge_verify_target(obj, self.AT)
            self.assertEqual(r["verdict"], "blocked", status)
            self.assertTrue(any("流程没收尾" in b for b in r["blockers"]), status)
        # 4 条 09:48 锚点此刻就是 running
        for row in _real("anchor_active_before_restart"):
            self.assertEqual(row["status"], "running")

    def test_created_at_window_boundary(self):
        for at in ("2026-11-09 14:08:59", "2026-11-09 14:09:00"):
            obj = _derived()
            obj["created_at"] = at
            r = qr.judge_verify_target(obj, self.AT)
            self.assertEqual(r["verdict"], "blocked", at)
            self.assertTrue(any("不晚于恢复窗口" in b for b in r["blockers"]))
        obj = _derived()
        obj["created_at"] = "2026-11-09 14:09:01"
        self.assertEqual(qr.judge_verify_target(obj, self.AT)["verdict"], "ok")

    def test_unparseable_recovery_at_is_fail_closed(self):
        r = qr.judge_verify_target(_derived(), "解析不了的时刻")
        self.assertEqual(r["verdict"], "unknown")
        self.assertEqual(r["judged_fields"], 0)
        self.assertIn("恢复时刻解析不了", r["blockers"][0])

    def test_recovery_at_override_is_honoured(self):
        obj = _derived()   # created_at = 2026-11-09 14:20:01
        self.assertEqual(qr.judge_verify_target(obj, "2026-11-09 15:00")["verdict"],
                         "blocked", "窗口推后后，14:20 的 run 就该算旧 run")
        self.assertEqual(qr.judge_verify_target(obj, "2026-11-09 14:00")["verdict"],
                         "ok")


class VerifyRunCliTest(unittest.TestCase):

    def test_default_path_unchanged_and_still_false_green(self):
        """不加开关时行为逐字不变；同时也证明假绿缺口是真实存在的。"""
        obj = _wrong_identity()
        rc, out, post = _cli(["--verify-run", obj["id"]], obj)
        self.assertEqual(rc, 0)
        self.assertIn("恢复判据：通过", out)
        self.assertNotIn("验收不可用", out)
        self.assertNotIn("本次续跑身份核对", out)
        post.assert_not_called()

    def test_as_recovery_blocks_false_green_without_conclusion(self):
        obj = _wrong_identity()
        rc, out, post = _cli(["--verify-run", obj["id"], "--as-recovery"], obj)
        self.assertEqual(rc, 2)
        self.assertNotIn("恢复判据：通过", out)
        self.assertNotIn("恢复判据：未通过", out)
        self.assertIn("验收不可用", out)
        self.assertIn("本次续跑身份核对：blocked", out)
        self.assertIn("不在 4 个待续跑 task", out)
        self.assertIn("不晚于恢复窗口", out)
        post.assert_not_called()

    def test_as_recovery_allows_genuine_new_run(self):
        obj = _derived()
        rc, out, post = _cli(["--verify-run", obj["id"], "--as-recovery"], obj)
        self.assertEqual(rc, 0)
        self.assertIn("本次续跑身份核对：ok", out)
        self.assertIn("恢复判据：通过", out)
        self.assertNotIn("验收不可用", out)
        post.assert_not_called()

    def test_all_three_real_false_green_rows_blocked_at_cli(self):
        """把 3 条真样本的身份装进会给绿灯的最小 run，逐条走 CLI。"""
        judged = 0
        for row in _real("false_green_historical"):
            obj = _wrong_identity()
            obj.update(_identity(row))
            obj["id"] = row["id"]
            rc, out, post = _cli(["--verify-run", row["id"], "--as-recovery"], obj)
            self.assertEqual(rc, 2, row["id"])
            self.assertNotIn("恢复判据：通过", out)
            self.assertIn("验收不可用", out)
            post.assert_not_called()
            judged += 1
        self.assertEqual(judged, 3)
        print("真被判过并拦下的真实假绿反例 %d/3（CLI 层）" % judged)

    def test_unreadable_run_detail_returns_2_friendly(self):
        buf = io.StringIO()
        with mock.patch.object(qr, "api_get", side_effect=RuntimeError("404")), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--verify-run", "r-not-exist", "--as-recovery"])
        self.assertEqual(rc, 2)
        self.assertIn("run 详情读不到", buf.getvalue())
        self.assertNotIn("Traceback", buf.getvalue())
        post.assert_not_called()


class SourceAndHandoverTest(unittest.TestCase):
    """判据必须有消费点：代码接线 + 九步命令块，防死代码。"""

    def test_cmd_verify_run_calls_judge_and_degrades(self):
        src = ast.get_source_segment(SCRIPT.read_text(encoding="utf-8"),
                                     _func("cmd_verify_run")) or ""
        self.assertIn("judge_verify_target(", src)
        self.assertIn("if as_recovery:", src)
        self.assertIn("验收不可用", src)
        self.assertIn("return 2", src)
        self.assertIn("print(human_recovery(judge_recovery(d)))", src)

    def test_main_wires_switch_and_recovery_at(self):
        src = ast.get_source_segment(SCRIPT.read_text(encoding="utf-8"),
                                     _func("main")) or ""
        self.assertIn("as_recovery=args.as_recovery", src)
        self.assertIn("at_text=args.recovery_at", src)
        self.assertIn('"--as-recovery"', src)

    def test_handover_step9_has_consumption_point(self):
        text = HANDOVER.read_text(encoding="utf-8")
        self.assertIn('--verify-run "$RID" --as-recovery', text)
        self.assertIn("先打印「本次续跑身份核对：ok」", text)

    def test_script_line_endings_stay_lf(self):
        raw = SCRIPT.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0, "仓库脚本必须保持 LF")


if __name__ == "__main__":
    unittest.main()
