# -*- coding: utf-8 -*-
"""11-09 九步第 ⑨ 步：把「额度恢复了吗」变成可消费的验收结论（15:2x）。

为什么需要这一轮（倒查到九步终点）
------------------------------------
13:20 / 14:00 / 14:22 / 14:37 四轮把「这条 run 是不是本次续跑」判得很严：
重启收尸、身份核对、⑧ 写后 `$RID` 对账、⑧→⑨ 的 `$TID` 交接。但 ⑨
最后一行始终是 `print(human_recovery(judge_recovery(d)))` 后无条件
`return 0` —— **`recovery_ok` 全程零消费点**：

    身份 ok + 恢复判据「通过」          → exit 0、无阻断文案
    身份 ok + 仍有 1 步真实 [1310] 原话 → exit 0、无阻断文案（输出末尾一样）

九步 ⑨ 是终点，人/脚本看到 exit 0 就按交接单继续开其余 3 本；额度见底时
这就是直接烧额度。交接单「每步验收」对 ⑨ 也只写了 `$RID/$TID` 从哪来，
**没写怎么判额度恢复、没写未通过时的动作**。

另一半是口径：`judge_recovery` 把「schema 不认识 / 字段不足」也塞进
blockers 说成「恢复判据：未通过」，把**判不了**说成**不通过**。生产库
1217 条落盘 run 里 1004 条是 `schema=unknown`（`/api/state` 压缩 verdict
只留 publishable），拿到压缩版该 fail-closed 报 unknown。

三处改动，默认诊断路径逐字不变
--------------------------------
1. 新增纯函数 `judge_recovery_acceptance(d)`：ok / blocked / unknown 三路；
2. `cmd_verify_run` 只在 `--as-recovery` 且身份 ok 后消费它，打印
   「⑨ 验收结论」+ 明确动作 + 退出码 0/2；
3. 交接单每步验收补 ⑨ 的结论契约与「blocked/unknown 别开其余 3 本」。

夹具口径（决策 18）
--------------------
`quota_recovery_acceptance.json` = 生产库 1217 条 `run.json` / 25182 个 step
**全量只读扫描**的统计冻结 + 3 条 `[1310]` 真实 `step.summary` 原文（完整
未截断、不造合成反例）。窗口后 run 在 11-07 根本不存在，守测复用
`quota_recovery_verify_target.json` 的 `derived.new_recovery_run`（已明确
标注合成、**不计真样本数**），只把真实原话装进它的 steps 字段。

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
FIXTURE_ACC = ROOT / "tests" / "fixtures" / "quota_recovery_acceptance.json"
FIXTURE_IDENT = ROOT / "tests" / "fixtures" / "quota_recovery_verify_target.json"
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
HANDOVER = ROOT / "迭代计划.md"
_spec = importlib.util.spec_from_file_location("quota_recovery_acceptance", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

AT = "2026-11-09 14:09"


def _acc():
    return json.loads(FIXTURE_ACC.read_text(encoding="utf-8"))


def _ident():
    return json.loads(FIXTURE_IDENT.read_text(encoding="utf-8"))


def _derived():
    """合成：11-09 新建且 verdict 达标的连载 run，不计真样本数。"""
    return copy.deepcopy(_ident()["derived"]["new_recovery_run"])


def TARGET():
    """⑧ 的 target 不硬编码，一律从夹具派生 run 取（14:37 的教训）。"""
    return _derived()["task_id"]


def _quota_run(row=None):
    """派生：把生产落盘的**真实** [1310] step 原文装进窗口后 run。

    身份 / verdict 字段全部沿用夹具派生 run，只有 steps 换成真原文；
    单列标注为派生、不计真样本数。
    """
    d = _derived()
    step = row or _acc()["real_quota_steps"][0]
    d["id"] = "r-derived-quota-not-restored"
    d["steps"] = [{"n": step["step_n"], "status": "done",
                   "cost_usd": 1.2, "summary": step["summary"]}]
    return d


def _unjudgeable(verdict):
    """派生：verdict 字段不足（压缩版 / 不认识的 schema），不计真样本数。"""
    d = _derived()
    d["id"] = "r-derived-unjudgeable"
    d["verdict"] = verdict
    return d


def _cli(argv, obj):
    buf = io.StringIO()
    with mock.patch.object(qr, "api_get", return_value={"run": obj}), \
         mock.patch.object(qr, "api_post") as post, \
         redirect_stdout(buf):
        rc = qr.main(argv)
    return rc, buf.getvalue(), post


def _func(name):
    src_text = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src_text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src_text, node) or ""
    raise AssertionError("脚本里找不到函数 %s" % name)


class FixtureTruthTest(unittest.TestCase):
    """夹具必须来自真实落盘，别拿合成数据冒充（决策 18）。"""

    def test_scan_summary_and_frozen_real_steps(self):
        fx = _acc()
        s = fx["scan_summary"]
        self.assertEqual(s["total_run_json_judged"], 1217)
        self.assertEqual(s["real_quota_steps_frozen"], 3)
        self.assertGreaterEqual(s["total_steps_judged"], 25000)
        self.assertGreater(s["runs_with_1310_step"], 0)
        self.assertEqual(len(fx["real_quota_steps"]), 3)
        self.assertEqual(len(_ident()["resume_task_ids"]), 4)
        self.assertEqual(len(_ident()["real_runs"]), 11)
        print("真被判过的落盘 run 样本数 %d / step %d；真实 [1310] run %d；"
              "冻结真实原话 %d 条；真实身份样本 %d 条" % (
                  s["total_run_json_judged"], s["total_steps_judged"],
                  s["runs_with_1310_step"], len(fx["real_quota_steps"]),
                  len(_ident()["real_runs"])))

    def test_frozen_steps_are_real_originals_not_truncated(self):
        for row in _acc()["real_quota_steps"]:
            self.assertTrue(str(row["run_id"]).startswith("r-"), row)
            self.assertIn("[1310]", row["summary"])
            self.assertIn("后可继续使用", row["summary"])
            self.assertNotIn("…", row["summary"][-20:],
                             "裁字符串会造出本来不存在的假反例")
            self.assertEqual(str(row["source_run_json"]), "run.json")

    def test_quota_words_hit_but_429_literal_regex_does_not(self):
        """429 ≠ 限速：Request rejected (429) 不带 429 Too Many Requests 字面。"""
        judged = 0
        for row in _acc()["real_quota_steps"]:
            step = {"summary": row["summary"]}
            self.assertTrue(qr._step_quota_blocked(step), row["run_id"])
            self.assertFalse(qr._step_rate_limited(step), row["run_id"])
            judged += 1
        self.assertEqual(judged, 3)
        print("真被判过的非 429 字面额度原话 %d/3" % judged)

    def test_baseline_green_and_real_quota_variant_is_red(self):
        self.assertTrue(qr.judge_recovery(qr.judge_run(_derived()))["recovery_ok"])
        rec = qr.judge_recovery(qr.judge_run(_quota_run()))
        self.assertFalse(rec["recovery_ok"])
        self.assertTrue(any("额度耗尽原话" in b for b in rec["blockers"]))
        self.assertEqual(
            qr.judge_verify_target(_quota_run(), AT,
                                   expected_task_id=TARGET())["verdict"], "ok",
            "身份必须全 ok，才能证明缺口出在恢复判据而不是贴错 $RID")


class JudgeAcceptanceTest(unittest.TestCase):
    """三路结论：ok / blocked / unknown，判不了别冒充不通过。"""

    def test_genuine_recovery_is_ok(self):
        r = qr.judge_recovery_acceptance(qr.judge_run(_derived()))
        self.assertEqual(r["verdict"], "ok")
        self.assertTrue(r["recovery_ok"])
        self.assertEqual(r["blockers"], [])
        self.assertEqual(r["unknown_reasons"], [])

    def test_real_quota_step_is_blocked_not_ok(self):
        r = qr.judge_recovery_acceptance(qr.judge_run(_quota_run()))
        self.assertEqual(r["verdict"], "blocked")
        self.assertFalse(r["recovery_ok"])
        self.assertTrue(any("额度耗尽原话" in b for b in r["blockers"]))
        self.assertEqual(r["unknown_reasons"], [],
                         "明确不达标 ≠ 判不了，别混成 unknown")

    def test_all_three_real_quota_steps_are_blocked(self):
        judged = 0
        for row in _acc()["real_quota_steps"]:
            r = qr.judge_recovery_acceptance(qr.judge_run(_quota_run(row)))
            self.assertEqual(r["verdict"], "blocked", row["run_id"])
            judged += 1
        self.assertEqual(judged, 3)
        print("真被判过并拦下的真实额度原话 %d/3（判据层）" % judged)

    def test_compact_verdict_is_unknown_not_blocked(self):
        r = qr.judge_recovery_acceptance(
            qr.judge_run(_unjudgeable({"publishable": True})))
        self.assertEqual(r["verdict"], "unknown")
        self.assertFalse(r["recovery_ok"])
        self.assertTrue(r["unknown_reasons"])
        self.assertIn("压缩版", r["unknown_reasons"][0])

    def test_unrecognized_schema_is_unknown_not_blocked(self):
        r = qr.judge_recovery_acceptance(
            qr.judge_run(_unjudgeable({"something_else": 1})))
        self.assertEqual(r["verdict"], "unknown")
        self.assertTrue(r["unknown_reasons"])

    def test_incomplete_doc_verdict_is_unknown(self):
        r = qr.judge_recovery_acceptance(
            qr.judge_run(_unjudgeable({"type": "doc", "publishable": False})))
        self.assertEqual(r["verdict"], "unknown")
        self.assertFalse(r["recovery_ok"], "字段不足绝不能上绿标")

    def test_unknown_keeps_blockers_for_human_review(self):
        r = qr.judge_recovery_acceptance(
            qr.judge_run(_unjudgeable({"publishable": True})))
        self.assertTrue(r["blockers"], "判不了也要把字段缺口列给人看")


class CliAcceptanceTest(unittest.TestCase):
    """⑨ 完整命令：退出码与文案必须能区分「额度恢复」与「没恢复」。"""

    def _argv(self, obj):
        return ["--verify-run", obj["id"], "--as-recovery",
                "--expect-task", TARGET()]

    def test_genuine_recovery_exits_0_with_action(self):
        obj = _derived()
        rc, out, post = _cli(self._argv(obj), obj)
        self.assertEqual(rc, 0)
        self.assertIn("本次续跑身份核对：ok", out)
        self.assertIn("⑨ 验收结论：ok", out)
        self.assertIn("验收通过：可以进粘性 A/B", out)
        self.assertIn("其余 3 本仍要串行", out)
        self.assertNotIn("验收不可用", out)
        post.assert_not_called()

    def test_quota_not_restored_exits_2_with_blocking_action(self):
        obj = _quota_run()
        rc, out, post = _cli(self._argv(obj), obj)
        self.assertEqual(rc, 2, "额度没恢复却 exit 0 = 九步终点假绿")
        self.assertIn("恢复判据：未通过", out)
        self.assertIn("⑨ 验收结论：blocked", out)
        self.assertIn("[恢复缺口]", out)
        self.assertIn("额度耗尽原话", out)
        self.assertIn("验收不可用：别按「额度已恢复」宣布", out)
        self.assertIn("别开其余 3 本", out)
        self.assertIn("别进粘性 A/B", out)
        post.assert_not_called()

    def test_all_three_real_quota_steps_block_at_cli(self):
        judged = 0
        for row in _acc()["real_quota_steps"]:
            obj = _quota_run(row)
            rc, out, post = _cli(self._argv(obj), obj)
            self.assertEqual(rc, 2, row["run_id"])
            self.assertIn("⑨ 验收结论：blocked", out, row["run_id"])
            post.assert_not_called()
            judged += 1
        self.assertEqual(judged, 3)
        print("真被判过并拦下的真实额度原话 %d/3（CLI 层）" % judged)

    def test_unjudgeable_exits_2_as_unknown(self):
        obj = _unjudgeable({"publishable": True})
        rc, out, post = _cli(self._argv(obj), obj)
        self.assertEqual(rc, 2)
        self.assertIn("⑨ 验收结论：unknown", out)
        self.assertIn("[判不了]", out)
        self.assertIn("别开其余 3 本", out)
        post.assert_not_called()

    def test_default_diagnostic_path_untouched(self):
        """不带 --as-recovery 仍是 14:00 的普通诊断，逐字不变。"""
        obj = _quota_run()
        rc, out, post = _cli(["--verify-run", obj["id"]], obj)
        self.assertEqual(rc, 0)
        self.assertIn("恢复判据：未通过", out)
        self.assertNotIn("⑨ 验收结论", out)
        self.assertNotIn("验收不可用", out)
        self.assertNotIn("本次续跑身份核对", out)
        post.assert_not_called()

    def test_failed_identity_still_returns_2_without_new_conclusion(self):
        """身份不合格时提前 return 2，14:00/14:37 的输出逐字不变。"""
        obj = _quota_run()
        obj["task_id"] = "t-20261022-104907-1000"
        rc, out, post = _cli(self._argv(obj), obj)
        self.assertEqual(rc, 2)
        self.assertIn("不是 ⑧ retry 的目标", out)
        self.assertNotIn("⑨ 验收结论", out)
        self.assertNotIn("恢复判据：未通过", out)
        post.assert_not_called()


class ConsumptionPointTest(unittest.TestCase):
    """判据沉默三问：产出有没有被下游消费，防死代码。"""

    def test_judge_consumes_recovery_ok_and_separates_unknown(self):
        src = _func("judge_recovery_acceptance")
        self.assertIn('r["recovery_ok"]', src)
        self.assertIn("unknown_reasons", src)
        self.assertIn('d["fields_complete"]', src)
        self.assertIn('"unknown"', src)

    def test_cmd_verify_run_consumes_acceptance_and_returns_2(self):
        src = _func("cmd_verify_run")
        self.assertIn("judge_recovery_acceptance(", src)
        self.assertIn('acc["verdict"] != "ok"', src)
        self.assertIn("return 2", src)
        self.assertIn("print(human_recovery(judge_recovery(d)))", src,
                      "14:00 默认诊断路径的打印行必须逐字保留")
        self.assertIn("⑨ 验收结论", src)

    def test_handover_step9_acceptance_contract(self):
        text = HANDOVER.read_text(encoding="utf-8")
        self.assertIn('--verify-run "$RID" --as-recovery --expect-task "$TID"', text)
        self.assertIn("⑨ 验收结论：ok", text)
        self.assertIn("别开其余 3 本", text)

    def test_script_and_fixture_line_endings_stay_lf(self):
        for path in (SCRIPT, FIXTURE_ACC):
            self.assertEqual(path.read_bytes().count(b"\r\n"), 0,
                             "%s 必须保持 LF" % path.name)


if __name__ == "__main__":
    unittest.main()
