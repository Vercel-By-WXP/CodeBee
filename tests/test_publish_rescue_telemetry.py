# -*- coding: utf-8 -*-
"""人工救火台账 / 遥测契约 / API 接入棘轮守测（unittest 风格，闸门必收）。

**夹具口径**：这里写的是 task / 回执 / 台账的**结构夹具**，不冒充真稿或
真实过签样本。真实数字只由 CLI 对生产库只读跑出来（决策 18）。

守四件最容易出事的地方：
1. 记账失败绝不能拖挂主流程（人工点了「继续任务」就必须能继续）；
2. 坏 kind / 缺 task_id / 缺 source 一律拒写，脏数据不许污染指标；
3. 「埋点启用时刻」边界：历史书查不到人工痕迹 ≠ 没救火，不许进分母；
4. 契约棘轮：``manual_revise`` 若真被加了产品入口，必须立刻红，逼人同步契约。
"""
import ast
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from core.publish import (journey, journey_sources, rescue_telemetry,  # noqa: E402
                          telemetry_contract as contract)
import journey_report  # noqa: E402

SINCE = "2026-11-06 23:30"


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _fixture(root, created="2026-11-07 09:00:00", rescue_rows=None):
    root = Path(root)
    _write(root / "tasks" / "t-1.json", {
        "id": "t-1", "type": "serial_novel", "title": "结构夹具",
        "created_at": created, "status": "done"})
    _write(root / "publish" / "outcomes.json",
           {"t-1": {"qimao": {"stage": "signed"}}})
    rows = rescue_rows if rescue_rows is not None else [
        {"ts": "2026-11-07 10:00:00", "day": "2026-11-07",
         "kind": "manual_retry", "task_id": "t-1",
         "source": "api:task_retry", "run_id": "r-1", "note": ""}]
    (root / "publish" / "rescue-202611.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return str(root)


class RescueTelemetryWriteTest(unittest.TestCase):

    def test_record_writes_expected_fields(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(rescue_telemetry.record(
                "manual_retry", "t-1", "api:task_retry", run_id="r-9",
                note="继续任务", data_dir=td))
            rows, bad, exists = rescue_telemetry.iter_records(td)
            self.assertTrue(exists)
            self.assertEqual(0, bad)
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("manual_retry", row["kind"])
            self.assertEqual("t-1", row["task_id"])
            self.assertEqual("api:task_retry", row["source"])
            self.assertEqual("r-9", row["run_id"])
            self.assertEqual("继续任务", row["note"])
            self.assertEqual(set(rescue_telemetry.FIELDS), set(row))

    def test_bad_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            for kind, tid, src in (("not_a_kind", "t-1", "s"),
                                   ("manual_revise", "t-1", "s"),
                                   ("manual_retry", "", "s"),
                                   ("manual_retry", "t-1", "")):
                self.assertFalse(rescue_telemetry.record(
                    kind, tid, src, data_dir=td), (kind, tid, src))
            rows, _, _ = rescue_telemetry.iter_records(td)
            self.assertEqual([], rows)

    def test_idempotent_within_same_second(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(rescue_telemetry.record(
                "manual_pause_resume", "t-1", "api:run_pause", data_dir=td))
            self.assertTrue(rescue_telemetry.record(
                "manual_pause_resume", "t-1", "api:run_pause", data_dir=td))
            rows, _, _ = rescue_telemetry.iter_records(td)
            self.assertEqual(1, len(rows))

    def test_record_never_raises_when_disk_write_fails(self):
        """目录被同名文件占住 → mkdir 抛错，必须被吞掉且不影响主流程。"""
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocked"
            blocker.write_text("i am a file", encoding="utf-8")
            self.assertFalse(rescue_telemetry.record(
                "manual_retry", "t-1", "api:task_retry", data_dir=str(blocker)))

    def test_iter_records_counts_bad_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "publish" / "rescue-202611.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"kind":"manual_retry","task_id":"t-1","source":"s"}\n'
                         '{bad json\n', encoding="utf-8")
            rows, bad, exists = rescue_telemetry.iter_records(td)
            self.assertTrue(exists)
            self.assertEqual(1, bad)
            self.assertEqual(1, len(rows))


class TelemetryContractTest(unittest.TestCase):

    def test_normalize_since_fail_closed(self):
        self.assertIsNone(contract.normalize_since(None))
        self.assertIsNone(contract.normalize_since(""))
        self.assertIsNone(contract.normalize_since("昨天"))
        self.assertIsNone(contract.normalize_since("2026-11-06"))
        self.assertEqual("2026-11-06 23:30:00",
                         contract.normalize_since("2026-11-06 23:30"))

    def test_post_telemetry_boundary(self):
        self.assertFalse(contract.is_post_telemetry("2026-11-07 09:00:00", None))
        self.assertFalse(contract.is_post_telemetry("", SINCE))
        self.assertFalse(contract.is_post_telemetry("坏时间", SINCE))
        self.assertFalse(contract.is_post_telemetry("2026-11-06 23:29:59", SINCE))
        self.assertTrue(contract.is_post_telemetry("2026-11-06 23:30:00", SINCE))
        self.assertTrue(contract.is_post_telemetry("2026-11-07 09:00:00", SINCE))

    def test_rescue_log_kinds_match_contract(self):
        self.assertEqual(contract.OBSERVED_RESCUE_KINDS
                         - frozenset(("force_publish",)),
                         contract.RESCUE_LOG_KINDS)
        self.assertEqual(set(), contract.RESCUE_LOG_KINDS
                         & contract.NOT_APPLICABLE_RESCUE_KINDS)
        self.assertEqual(frozenset(journey.RESCUE_KINDS), contract.RESCUE_KINDS)

    def test_manual_revise_has_no_product_entrypoint(self):
        """AST 棘轮：一旦有人给 manual_revise 加了写入口，这里必须红。

        扫的是「调用参数里出现 manual_revise」，docstring 里的说明不算入口。
        红了就把该 kind 从 NOT_APPLICABLE 挪进 OBSERVED 并补台账。
        """
        allowed = {"telemetry_contract.py", "journey.py",
                   "rescue_telemetry.py", "journey_sources.py"}
        hits = []
        for p in sorted((ROOT / "app").rglob("*.py")):
            if p.name in allowed:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.Constant) and arg.value == "manual_revise":
                        hits.append(str(p))
        self.assertEqual([], hits)

    def test_api_has_both_telemetry_call_sites(self):
        """main.py 两处最小接入不许被后续合并/重构悄悄删掉。"""
        src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertEqual(2, src.count("from core.publish import rescue_telemetry"))
        self.assertIn('"manual_retry", m.group(1), "api:task_retry",', src)
        self.assertIn('"manual_pause_resume", (run.get("task_id") or ""),', src)


class JourneyRescueConsumptionTest(unittest.TestCase):

    def test_history_book_never_enters_denominator_without_since(self):
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td)
            derived = journey_sources.derive_events(d)
            summary = journey.from_events(derived["events"])
            self.assertEqual(0, summary["judged"])
            self.assertEqual(1, summary["counts"]["unknown"])
            self.assertEqual(1, derived["coverage"]["counts"]["sentinel_legacy"])
            self.assertIsNone(derived["coverage"]["telemetry_since"])

    def test_post_telemetry_rescue_book_is_judged(self):
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td)
            derived = journey_sources.derive_events(d, telemetry_since=SINCE)
            summary = journey.from_events(derived["events"])
            self.assertEqual(1, summary["judged"])
            self.assertEqual(1, summary["counts"]["rescued"])
            self.assertEqual(1, summary["rescue_reasons"]["manual_retry"])
            self.assertEqual(0, summary["counts"]["unknown"])
            self.assertEqual(1, derived["coverage"]["counts"]["post_telemetry"])
            self.assertEqual(0, derived["coverage"]["counts"]["sentinel_legacy"])

    def test_book_created_before_since_stays_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td, created="2026-11-06 23:29:00")
            derived = journey_sources.derive_events(d, telemetry_since=SINCE)
            self.assertEqual(0, journey.from_events(derived["events"])["judged"])
            self.assertEqual(1, derived["coverage"]["counts"]["sentinel_legacy"])

    def test_invalid_rescue_row_is_reported_not_counted(self):
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td, rescue_rows=[
                {"kind": "unknown_kind", "task_id": "t-1", "source": "s"},
                {"kind": "manual_retry", "task_id": "t-1", "source": ""}])
            derived = journey_sources.derive_events(d, telemetry_since=SINCE)
            cov = derived["coverage"]
            self.assertEqual(2, cov["sources"]["rescue_ledger"]["invalid_rows"])
            self.assertEqual(0, cov["counts"]["rescue_events"])
            self.assertEqual(1, journey.from_events(derived["events"])["judged"])

    def test_cli_telemetry_since_is_passed_through(self):
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = journey_report.main(
                    ["--data-dir", d, "--telemetry-since", SINCE])
            self.assertEqual(0, rc)
            text = buf.getvalue()
            self.assertIn("启用时刻 2026-11-06 23:30:00", text)
            self.assertIn("真被判过的样本数 1/1", text)
            self.assertIn("manual_retry 1 次", text)

    def test_human_coverage_never_drops_no_green_discipline(self):
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td)
            text = journey_sources.human_coverage(
                journey_sources.derive_events(d)["coverage"])
            self.assertIn("不上绿标", text)
            self.assertIn("启用时刻未声明", text)
            self.assertIn("未观测 [无]", text)


if __name__ == "__main__":
    unittest.main()
