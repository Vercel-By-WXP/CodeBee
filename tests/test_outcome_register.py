"""11-07 18:4x 守测：ⓒ 补登记清单的消费点、四路判据与零写入闸。

真样本来自 `tests/fixtures/outcome_register_rows.json`（只读扫描 2026-11-07
生产导出的 80 行 CSV + 115 个 task 文件）；反例全部在真实 task_id 上派生，
夹具里单列标注、不计真样本。所有 CLI 测试全 mock，绝不碰生产库 / 8765。
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/outcome_register.py"
FIXTURE = ROOT / "tests/fixtures/outcome_register_rows.json"
HANDOVER = ROOT / "迭代计划.md"

spec = importlib.util.spec_from_file_location("outcome_register_guard", SCRIPT)
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)
# 保存真函数：run_cli 会 patch reg.read_csv，回调里必须调原始实现，
# 否则 patch 后 reg.read_csv 指向回调自己 → 无限递归。
REAL_READ_CSV = reg.read_csv

FX = json.loads(FIXTURE.read_text(encoding="utf-8"))
HEADER = FX["header"]
REAL_ROWS = FX["real_rows"]
MISSING = set(FX["missing_ids"])
GAPS = {
    "total": FX["scan_summary"]["outcome_gaps_total"],
    "registered": FX["scan_summary"]["outcome_gaps_registered"],
    "registration_rate": FX["scan_summary"]["outcome_gaps_rate"],
    "missing": [{"task_id": t} for t in FX["missing_ids"]],
}
D = FX["derived"]


def _row(cell="", tid=None):
    """在一条真实行上派生：只改 task_id / 结论列，其余字段保持生产原样。"""
    r = list(REAL_ROWS[0])
    if tid is not None:
        r[0] = tid
    r[5] = cell
    return r


def _valid_rows():
    return [_row(x["cell"], x["task_id"]) for x in D["valid_rows"]]


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = os.path.join(self.tmp.name, "gaps.csv")

    def write_csv(self, rows, header=None):
        with open(self.csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header if header is not None else HEADER)
            w.writerows(rows)
        return self.csv

    def run_cli(self, argv, missing=None, post_side_effect=None,
                get_side_effect=None):
        """全 mock 跑 CLI，返回 (rc, 输出, 调用记录)。"""
        ids = MISSING if missing is None else missing
        calls = {"post": [], "get": [], "load": [], "read": []}

        def _load(data_dir):
            calls["load"].append(data_dir)
            return ids, GAPS

        def _read(path):
            calls["read"].append(path)
            return REAL_READ_CSV(path)

        def _post(base, path, body):
            calls["post"].append((path, body))
            if post_side_effect:
                return post_side_effect(path, body)
            return {"ok": True}

        def _get(base, path):
            calls["get"].append(path)
            if get_side_effect:
                return get_side_effect(path)
            tid = path.split("/")[-2]
            return {"task_id": tid, "platforms": {
                "fanqie": {"stage": ""}, "qimao": {"stage": ""}}}

        buf = io.StringIO()
        with mock.patch.object(reg, "load_missing_ids", _load), \
             mock.patch.object(reg, "read_csv", _read), \
             mock.patch.object(reg, "api_post", _post), \
             mock.patch.object(reg, "api_get", _get), \
             redirect_stdout(buf):
            rc = reg.main(argv + ["--csv", self.csv, "--data-dir", self.tmp.name])
        return rc, buf.getvalue(), calls


class TestFixtureContract(_Case):
    def test_01_real_scan_summary(self):
        """夹具真样本口径：80 行全空结论、12 本带真实书名、115 task / 81 分母。"""
        s = FX["scan_summary"]
        self.assertEqual(s["csv_rows_judged"], 80)
        self.assertEqual(s["csv_columns"], 7)
        self.assertEqual(s["csv_empty_conclusions"], 80)
        self.assertEqual(s["csv_rows_with_real_book_title"], 12)
        self.assertEqual(s["task_files"], 115)
        self.assertEqual(s["outcome_gaps_total"], 81)
        self.assertEqual(s["outcome_gaps_registered"], 1)
        self.assertEqual(s["missing_count"], 80)
        self.assertEqual(len(REAL_ROWS), 12)
        self.assertTrue(all(not r[5].strip() for r in REAL_ROWS))
        self.assertEqual(len(MISSING), 80)

    def test_02_header_is_exact_contract(self):
        self.assertEqual(reg.EXPECTED_HEADER, HEADER)
        self.assertEqual(reg.CONCLUSION_IDX, 5)

    def test_03_enum_comes_from_outcome_module(self):
        """枚举真源只能是 core.publish.outcome，别在脚本里另抄一份。"""
        src = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(src)
        assigns = [n.targets[0].id for n in tree.body
                   if isinstance(n, ast.Assign) and len(n.targets) == 1
                   and isinstance(n.targets[0], ast.Name)]
        self.assertNotIn("STAGES", assigns)
        self.assertNotIn("PLATFORMS", assigns)
        self.assertIn("outcome.STAGES", src)
        self.assertIn("outcome.PLATFORMS", src)
        self.assertIn("outcome.STAGE_ORDER", src)

    def test_04_load_missing_ids_really_uses_derive_events(self):
        """清单真源必须是落盘 derive_events，不是 /api/state 压缩摘要。"""
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("journey_sources.derive_events", src)
        fake = {"coverage": {"outcome_gaps": dict(
            GAPS, missing=[{"task_id": "t-real-1"}, {"task_id": ""}])}}
        with mock.patch.object(reg.journey_sources, "derive_events",
                               return_value=fake) as m:
            ids, gaps = reg.load_missing_ids("some-dir")
        self.assertEqual(ids, {"t-real-1"})
        self.assertEqual(gaps["total"], 81)
        m.assert_called_once_with(data_dir="some-dir")


class TestParseConclusion(_Case):
    def test_05_empty_is_skip_not_blocked(self):
        self.assertEqual(reg.parse_conclusion("")["verdict"], "skip")
        self.assertEqual(reg.parse_conclusion("   ")["verdict"], "skip")

    def test_06_good_stage_ok(self):
        p = reg.parse_conclusion("qimao|signed|")
        self.assertEqual(p["verdict"], "ok")
        self.assertEqual((p["platform"], p["stage"]), ("qimao", "signed"))

    def test_07_wait_stage_two_segments_ok(self):
        p = reg.parse_conclusion("fanqie|in_review")
        self.assertEqual(p["verdict"], "ok")
        self.assertEqual(p["quote"], "")

    def test_08_bad_stage_with_quote_ok(self):
        p = reg.parse_conclusion("fanqie|rejected|缺乏吸引力，建议修改开篇")
        self.assertEqual(p["verdict"], "ok")
        self.assertEqual(p["quote"], "缺乏吸引力，建议修改开篇")

    def test_09_one_segment_blocked(self):
        self.assertEqual(reg.parse_conclusion("过签了")["verdict"], "blocked")

    def test_10_unknown_platform_blocked(self):
        p = reg.parse_conclusion("wechat|signed|")
        self.assertEqual(p["verdict"], "blocked")
        self.assertIn("平台", p["reason"])

    def test_11_unknown_stage_blocked(self):
        p = reg.parse_conclusion("qimao|过签|")
        self.assertEqual(p["verdict"], "blocked")
        self.assertIn("阶段", p["reason"])

    def test_12_bad_stage_without_quote_is_unknown_not_blocked(self):
        p = reg.parse_conclusion("fanqie|rejected|")
        self.assertEqual(p["verdict"], "unknown")
        self.assertIn("判不了", p["reason"])

    def test_13_quote_keeps_pipe_characters(self):
        p = reg.parse_conclusion("qimao|signing_rejected|理由A|理由B")
        self.assertEqual(p["verdict"], "ok")
        self.assertEqual(p["quote"], "理由A|理由B")


class TestJudgeRows(_Case):
    def test_14_all_empty_reports_zero_judged(self):
        r = reg.judge_rows(HEADER, REAL_ROWS, MISSING)
        self.assertEqual(r["total"], 12)
        self.assertEqual(r["judged"], 0)
        self.assertEqual(len(r["skip"]), 12)
        self.assertEqual(r["ok"], [])

    def test_15_valid_batch_and_judged_count(self):
        rows = _valid_rows() + REAL_ROWS[3:6]
        r = reg.judge_rows(HEADER, rows, MISSING)
        self.assertEqual(len(r["ok"]), 3)
        self.assertEqual(r["judged"], 3)
        self.assertEqual(len(r["skip"]), 3)
        self.assertEqual(r["ok"][0]["platform"], "qimao")

    def test_16_header_drift_blocks_whole_batch(self):
        r = reg.judge_rows(HEADER[:-1] + ["多余列"], _valid_rows(), MISSING)
        self.assertEqual(len(r["blocked"]), 1)
        self.assertEqual(r["ok"], [])
        self.assertIn("表头漂移", r["blocked"][0]["reason"])

    def test_17_column_drift_is_unknown(self):
        rows = _valid_rows() + [["t-x", "only-two"]]
        r = reg.judge_rows(HEADER, rows, MISSING)
        self.assertEqual(len(r["unknown"]), 1)
        self.assertIn("列数", r["unknown"][0]["reason"])

    def test_18_empty_task_id_is_unknown(self):
        r = reg.judge_rows(HEADER, [_row("qimao|signed|", "")], MISSING)
        self.assertEqual(len(r["unknown"]), 1)
        self.assertIn("task_id", r["unknown"][0]["reason"])

    def test_19_duplicate_task_blocked(self):
        rows = _valid_rows() + [_row(D["duplicate"]["cell"],
                                     D["duplicate"]["task_id"])]
        r = reg.judge_rows(HEADER, rows, MISSING)
        self.assertTrue(any("重复" in x["reason"] for x in r["blocked"]))

    def test_20_not_in_missing_blocked(self):
        x = D["not_in_missing"]
        r = reg.judge_rows(HEADER, [_row(x["cell"], x["task_id"])], MISSING)
        self.assertTrue(any("不在当前待登记清单" in b["reason"]
                            for b in r["blocked"]))

    def test_21_unknown_quote_separate_from_blocked(self):
        x = D["bad_quote_missing"]
        r = reg.judge_rows(HEADER, [_row(x["cell"], x["task_id"])], MISSING)
        self.assertEqual(len(r["unknown"]), 1)
        self.assertEqual(r["blocked"], [])
        self.assertEqual(r["judged"], 1)

    def test_22_empty_missing_ids_notes_fail_closed(self):
        r = reg.judge_rows(HEADER, _valid_rows(), set())
        self.assertEqual(r["ok"], [])
        self.assertTrue(any("fail-closed" in n for n in r["notes"]))


class TestCliGates(_Case):
    def test_23_check_is_default_and_zero_http(self):
        self.write_csv(REAL_ROWS)
        rc, text, calls = self.run_cli([])
        self.assertEqual(rc, 0, text)
        self.assertEqual(calls["post"], [])
        self.assertEqual(calls["get"], [])
        self.assertIn("真被判过：0/12 行", text)
        self.assertIn("回执登记覆盖：真被判过 1/81", text)
        self.assertIn("清单还没核对", text)

    def test_24_apply_without_flag_is_zero_io(self):
        self.write_csv(_valid_rows())
        rc, text, calls = self.run_cli(["--apply"])
        self.assertEqual(rc, 2, text)
        self.assertEqual(calls["read"], [])
        self.assertEqual(calls["load"], [])
        self.assertEqual(calls["post"], [])
        self.assertEqual(calls["get"], [])
        self.assertIn("拒写", text)

    def test_25_dry_run_walks_all_gates_but_zero_post(self):
        self.write_csv(_valid_rows())
        rc, text, calls = self.run_cli(
            ["--apply", "--dry-run", "--confirm-human-reviewed"])
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(calls["load"]), 2)  # 判据 + 写前复核各取一次真源
        self.assertEqual(calls["post"], [])
        self.assertEqual(calls["get"], [])
        self.assertIn("写前复核通过", text)
        self.assertIn("零 POST / 零 GET", text)
        self.assertIn("POST /api/publish/task/%s/outcome" %
                      D["valid_rows"][0]["task_id"], text)

    def test_26_blocked_row_rejects_whole_batch(self):
        x = D["bad_platform"]
        self.write_csv(_valid_rows() + [_row(x["cell"], x["task_id"])])
        rc, text, calls = self.run_cli(["--apply", "--confirm-human-reviewed"])
        self.assertEqual(rc, 2, text)
        self.assertEqual(calls["post"], [])
        self.assertIn("整批拒写", text)

    def test_27_unknown_row_rejects_whole_batch(self):
        x = D["bad_quote_missing"]
        self.write_csv(_valid_rows() + [_row(x["cell"], x["task_id"])])
        rc, text, calls = self.run_cli(["--apply", "--confirm-human-reviewed"])
        self.assertEqual(rc, 2, text)
        self.assertEqual(calls["post"], [])
        self.assertIn("整批拒写", text)

    def test_28_empty_batch_rejects_without_post(self):
        self.write_csv(REAL_ROWS)
        rc, text, calls = self.run_cli(["--apply", "--confirm-human-reviewed"])
        self.assertEqual(rc, 2, text)
        self.assertEqual(calls["post"], [])
        self.assertIn("没有一行填了结论", text)

    def test_29_stale_precheck_rejects_without_post(self):
        """写前第二次取真源发现书已被登记 → 零 POST。"""
        self.write_csv(_valid_rows())
        ids = MISSING
        calls = {"post": [], "load": []}
        seq = [(ids, GAPS), (set(), GAPS), (ids, GAPS)]

        def _load(data_dir):
            calls["load"].append(data_dir)
            return seq[min(len(calls["load"]) - 1, 2)]

        def _post(base, path, body):
            calls["post"].append(path)
            return {"ok": True}

        buf = io.StringIO()
        with mock.patch.object(reg, "load_missing_ids", _load), \
             mock.patch.object(reg, "api_post", _post), \
             mock.patch.object(reg, "api_get", mock.Mock()), \
             redirect_stdout(buf):
            rc = reg.main(["--apply", "--confirm-human-reviewed",
                           "--csv", self.csv, "--data-dir", self.tmp.name])
        self.assertEqual(rc, 2, buf.getvalue())
        self.assertEqual(calls["post"], [])
        self.assertIn("写前复核时已不在待登记清单", buf.getvalue())


class TestApplyWritePath(_Case):
    def _view(self, stage_by_task):
        def _get(path):
            tid = path.split("/")[-2]
            st = stage_by_task.get(tid, "")
            return {"task_id": tid, "platforms": {
                "fanqie": {"stage": st}, "qimao": {"stage": st}}}
        return _get

    def test_30_all_ok_posts_and_verifies_each(self):
        self.write_csv(_valid_rows())
        want = {x["task_id"]: x["cell"].split("|")[1]
                for x in D["valid_rows"]}
        rc, text, calls = self.run_cli(
            ["--apply", "--confirm-human-reviewed"],
            get_side_effect=self._view(want))
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(calls["post"]), 3)
        self.assertEqual(len(calls["get"]), 3)
        self.assertEqual(calls["post"][0][1]["platform"], "qimao")
        self.assertIn("3 本写后复核通过", text)
        self.assertIn("写后覆盖", text)

    def test_31_verify_mismatch_exit2_and_no_retry(self):
        """POST 已发生不可回滚：失败如实报，绝不重跑这一本。"""
        self.write_csv(_valid_rows())
        rc, text, calls = self.run_cli(
            ["--apply", "--confirm-human-reviewed"],
            get_side_effect=self._view({}))
        self.assertEqual(rc, 2, text)
        self.assertEqual(len(calls["post"]), 3)
        self.assertEqual(len(calls["get"]), 3)
        self.assertIn("写后 stage", text)
        self.assertIn("别重跑", text)

    def test_32_post_exception_exit2_and_no_retry(self):
        self.write_csv(_valid_rows())

        def _boom(path, body):
            raise OSError("connection refused")

        rc, text, calls = self.run_cli(
            ["--apply", "--confirm-human-reviewed"],
            post_side_effect=_boom)
        self.assertEqual(rc, 2, text)
        self.assertEqual(len(calls["post"]), 3)
        self.assertEqual(calls["get"], [])
        self.assertIn("POST 可能已发生", text)

    def test_33_template_zero_io(self):
        buf = io.StringIO()
        with mock.patch.object(reg, "load_missing_ids") as ld, \
             mock.patch.object(reg, "api_post") as post, \
             redirect_stdout(buf):
            rc = reg.main(["--template"])
        self.assertEqual(rc, 0)
        ld.assert_not_called()
        post.assert_not_called()
        self.assertIn("<平台>|<阶段>|<平台原话>", buf.getvalue())
        self.assertIn("fanqie|rejected", buf.getvalue())


class TestConsumptionPoints(_Case):
    def test_34_ast_wiring(self):
        src = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(src)
        apply_fn = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == "cmd_apply")
        body = ast.dump(apply_fn)
        for name in ("judge_rows", "load_missing_ids", "api_post", "api_get"):
            self.assertIn(name, body, "cmd_apply 未消费 %s" % name)
        main_fn = next(n for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name == "main")
        main_src = ast.dump(main_fn)
        self.assertIn("cmd_apply", main_src)
        self.assertIn("cmd_check", main_src)
        self.assertIn("confirmed", main_src)

    def test_35_no_hardcoded_recovery_window_or_quota_logic(self):
        """本工具与 11-09 额度恢复无关，别把两套写入闸搅在一起。"""
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("QUOTA_RECOVERY_AT", src)
        self.assertNotIn("--retry-one", src)
        self.assertNotIn("api/tasks/", src)

    def test_36_handover_documents_the_consumer(self):
        """交接单必须写明这份 CSV 的消费点，否则又是零消费点交付。"""
        text = HANDOVER.read_text(encoding="utf-8")
        self.assertTrue("scripts/outcome_register.py" in text,
                        "交接单未写明 CSV 的消费点脚本")
        self.assertTrue("--confirm-human-reviewed" in text,
                        "交接单未写明人工确认标志")
        self.assertTrue("--template" in text,
                        "交接单未写明填写契约入口")


if __name__ == "__main__":
    unittest.main()
