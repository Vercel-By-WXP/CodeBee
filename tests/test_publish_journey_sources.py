# -*- coding: utf-8 -*-
"""真实落盘事件适配器守测（unittest 风格，release_gate 的 discover 必收）。

**夹具口径**：这里写的是 task / outcomes / 发布台账的**结构夹具**，用来验证
映射与 fail-closed 行为；它们不是真稿、不是真实过签/驳回样本，绝不冒充
决策 18 要求的「真被判过样本」。真实数字只由 CLI 对生产库只读跑出来。

守的是五件最容易假绿的事：
1. ``status=done`` 不等于过签，没有平台结论不许生成 ``accepted``；
2. ``quality_forced`` 缺 ``source`` 时不可判定（既不算救火也不算没救火）；
3. 三类人工救火没有埋点 → 有终态证据的书一律 unknown；
4. 坏文件 / 坏行 / 缺文件不静默吞掉，且绝不假绿；
5. 全过程零写入，哨兵 kind 不许被塞进 journey 白名单。
"""
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

from core.publish import (journey, journey_sources, outcome, stats,  # noqa: E402
                          telemetry_contract as contract)

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import journey_report  # noqa: E402


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _task(task_id="t-1", status="done", kind="serial_novel", created="2026-11-01 10:00:00"):
    return {"id": task_id, "type": kind, "title": "结构夹具",
            "created_at": created, "status": status}


def _fixture(root, tasks=None, outcomes=None, ledger_rows=None,
             include_outcomes=True):
    """造一份最小落盘结构；返回 data_dir。"""
    root = Path(root)
    for t in (tasks or []):
        _write_json(root / "tasks" / (t["id"] + ".json"), t)
    if include_outcomes:
        _write_json(root / "publish" / "outcomes.json", outcomes or {})
    if ledger_rows is not None:
        (root / "publish").mkdir(parents=True, exist_ok=True)
        (root / "publish" / "publish-202611.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in ledger_rows),
            encoding="utf-8")
    return str(root)


def _kinds(events, task_id):
    return [e["kind"] for e in events if e.get("task_id") == task_id]


def _snapshot(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


class JourneySourcesContractTest(unittest.TestCase):

    def test_constants_match_upstream_truth(self):
        # 口径真源在 outcome / stats，这里不许自己漂移。
        self.assertEqual(journey_sources.NOVEL_TYPES, set(outcome._NOVEL_TYPES))
        self.assertEqual(journey_sources.LIVE_ACTIONS, set(stats._LIVE_ACTIONS))

    def test_telemetry_contract_is_a_three_way_partition(self):
        # 一旦哨兵被加进白名单，「没埋点」就会被当成一次正常事件 → 假绿。
        self.assertNotIn(journey_sources.UNOBSERVABLE_KIND, journey.VALID_KINDS)
        # 四类救火必须恰好分成「有埋点 / 无产品入口 / 不可观测」三路，无遗漏无重叠。
        self.assertEqual(
            frozenset(journey.RESCUE_KINDS),
            contract.OBSERVED_RESCUE_KINDS
            | contract.NOT_APPLICABLE_RESCUE_KINDS
            | contract.UNOBSERVED_RESCUE_KINDS)
        for a, b in (("observed", "not_applicable"),
                     ("observed", "unobserved"),
                     ("not_applicable", "unobserved")):
            left = getattr(contract, "%s_RESCUE_KINDS" % a.upper())
            right = getattr(contract, "%s_RESCUE_KINDS" % b.upper())
            self.assertEqual(set(), set(left) & set(right), (a, b))
        self.assertEqual(frozenset(), contract.UNOBSERVED_RESCUE_KINDS)
        self.assertEqual(frozenset(("manual_revise",)),
                         contract.NOT_APPLICABLE_RESCUE_KINDS)
        self.assertEqual(3, len(contract.OBSERVED_RESCUE_KINDS))

    def test_done_without_outcome_never_maps_to_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task(status="done")], {})
            d = journey_sources.derive_events(directory)
            kinds = _kinds(d["events"], "t-1")
            self.assertNotIn("accepted", kinds)
            self.assertIn(journey_sources.UNOBSERVABLE_KIND, kinds)
            self.assertEqual(1, d["coverage"]["counts"]["done_without_outcome"])
            summary = journey.from_events(d["events"])
            self.assertEqual(0, summary["judged"])
            self.assertIsNone(summary["clean_rate"])
            self.assertEqual(1, summary["counts"]["unknown"])

    def test_signed_maps_accepted_but_unobserved_blocks_denominator(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(
                td, [_task(status="done")],
                {"t-1": {"qimao": {"stage": "signed", "at": "2026-11-02 10:00:00"}}})
            d = journey_sources.derive_events(directory)
            kinds = _kinds(d["events"], "t-1")
            self.assertIn("accepted", kinds)
            self.assertIn("outcome_recorded", kinds)
            # 埋点不全，仍不许进分母。
            self.assertEqual(0, journey.from_events(d["events"])["judged"])

    def test_rejection_stages_map_to_rejected(self):
        for stage in ("rejected", "signing_rejected"):
            with tempfile.TemporaryDirectory() as td:
                directory = _fixture(
                    td, [_task(status="done")],
                    {"t-1": {"fanqie": {"stage": stage}}})
                kinds = _kinds(journey_sources.derive_events(directory)["events"], "t-1")
                self.assertIn("rejected", kinds)
                self.assertNotIn("accepted", kinds)

    def test_progress_only_and_wait_stages_are_not_terminals(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task(status="done"), _task("t-2", "done")], {
                "t-1": {"fanqie": {"progress": {"live": 43, "total": 60}}},
                "t-2": {"qimao": {"stage": "in_review"}},
            })
            d = journey_sources.derive_events(directory)
            for tid in ("t-1", "t-2"):
                self.assertNotIn("accepted", _kinds(d["events"], tid))
            self.assertEqual(2, d["coverage"]["counts"]["done_without_outcome"])

    def test_cancelled_and_failed_are_aborted_not_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "cancelled"),
                                      _task("t-2", "failed")], {})
            d = journey_sources.derive_events(directory)
            self.assertIn("cancelled", _kinds(d["events"], "t-1"))
            self.assertIn("failed", _kinds(d["events"], "t-2"))
            self.assertEqual(0, d["coverage"]["counts"]["terminal_evidence"])
            self.assertEqual(2, d["coverage"]["counts"]["aborted"])
            # aborted 不需要救火埋点也能判，不许被哨兵误报成 unknown。
            self.assertEqual(0, d["coverage"]["counts"]["sentinel_unobserved"])
            summary = journey.from_events(d["events"])
            self.assertEqual(2, summary["counts"]["aborted"])
            self.assertEqual(0, summary["judged"])
            self.assertEqual(0, summary["counts"]["unknown"])

    def test_coverage_aborted_matches_report_even_with_unknown_forced_source(self):
        """coverage 的 aborted 数必须等于指标行的 aborted 数，不许对不上。"""
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(
                td, [_task("t-1", "cancelled"), _task("t-2", "failed")], {},
                [{"task_id": "t-1", "action": "upload_chapter", "ok": True,
                  "quality_forced": True}])  # 老数据：无 quality_forced_source
            d = journey_sources.derive_events(directory)
            cov = d["coverage"]["counts"]["aborted"]
            report = journey.from_events(d["events"])["counts"]["aborted"]
            self.assertEqual(2, cov)
            self.assertEqual(cov, report)
            self.assertEqual(1, d["coverage"]["counts"]["forced_unknown_source_tasks"])

    def test_outcome_conclusion_takes_priority_over_task_status(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "cancelled")],
                                 {"t-1": {"qimao": {"stage": "signed"}}})
            d = journey_sources.derive_events(directory)
            kinds = _kinds(d["events"], "t-1")
            self.assertIn("accepted", kinds)
            self.assertNotIn("cancelled", kinds)
            self.assertEqual(1, d["coverage"]["counts"]["terminal_evidence"])

    def test_running_stays_incomplete_not_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "running")], {})
            d = journey_sources.derive_events(directory)
            summary = journey.from_events(d["events"])
            self.assertEqual(1, summary["counts"]["incomplete"])
            self.assertEqual(0, summary["counts"]["unknown"])

    def test_forced_manual_is_rescue_auto_is_neutral_unknown_is_sentinel(self):
        rows = [
            {"task_id": "t-1", "action": "upload_chapter", "ok": True,
             "quality_forced": True, "quality_forced_source": "manual"},
            {"task_id": "t-2", "action": "upload_chapter", "ok": True,
             "quality_forced": True, "quality_forced_source": "auto"},
            {"task_id": "t-3", "action": "upload_chapter", "ok": True,
             "quality_forced": True},  # 老数据：source 缺失
        ]
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(
                td, [_task("t-1", "done"), _task("t-2", "done"), _task("t-3", "done")],
                {"t-1": {"qimao": {"stage": "signed"}},
                 "t-2": {"qimao": {"stage": "signed"}},
                 "t-3": {"qimao": {"stage": "signed"}}}, rows)
            d = journey_sources.derive_events(directory)
            self.assertIn("force_publish", _kinds(d["events"], "t-1"))
            self.assertNotIn("force_publish", _kinds(d["events"], "t-2"))
            self.assertIn(journey_sources.UNOBSERVABLE_KIND, _kinds(d["events"], "t-3"))
            c = d["coverage"]["sources"]["publish_ledger"]
            self.assertEqual((3, 1, 1, 1), (c["forced"], c["forced_manual"],
                                            c["forced_auto"], c["forced_unknown_source"]))
            self.assertIn("published", _kinds(d["events"], "t-1"))

    def test_non_novel_tasks_do_not_enter_denominator(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1"), _task("t-2", kind="doc")], {})
            d = journey_sources.derive_events(directory)
            self.assertEqual(1, d["coverage"]["counts"]["skipped_non_novel"])
            self.assertEqual([], _kinds(d["events"], "t-2"))

    def test_missing_outcomes_file_is_reported_and_never_green(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1")], None, include_outcomes=False)
            d = journey_sources.derive_events(directory)
            self.assertFalse(d["coverage"]["sources"]["outcomes"]["exists"])
            self.assertEqual(0, journey.from_events(d["events"])["judged"])

    def test_missing_data_dir_is_reported_never_silently_empty(self):
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "nope")
            d = journey_sources.derive_events(missing)
            self.assertFalse(d["coverage"]["sources"]["tasks"]["exists"])
            self.assertTrue(any("目录不存在" in p for p in d["coverage"]["problems"]))

    def test_bad_task_json_is_reported_and_blocks_green(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "done")], {})
            (Path(directory) / "tasks" / "t-broken.json").write_text(
                "{not json", encoding="utf-8")
            d = journey_sources.derive_events(directory)
            self.assertEqual(1, d["coverage"]["sources"]["tasks"]["bad_files"])
            self.assertIn(journey_sources.UNOBSERVABLE_KIND,
                          _kinds(d["events"], "t-broken"))

    def test_bad_jsonl_line_is_counted_not_swallowed(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1")], {}, [])
            p = Path(directory) / "publish" / "publish-202611.jsonl"
            p.write_text(p.read_text(encoding="utf-8") + "\n{bad line\n", encoding="utf-8")
            d = journey_sources.derive_events(directory)
            self.assertEqual(1, d["coverage"]["sources"]["publish_ledger"]["bad_lines"])
            self.assertTrue(any("无法解析" in x for x in d["coverage"]["problems"]))

    def test_orphan_outcome_is_reported_not_attached_to_a_task(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1")],
                                 {"book-忘名簿": {"qimao": {"stage": "signing_rejected"}}})
            d = journey_sources.derive_events(directory)
            self.assertEqual(1, d["coverage"]["sources"]["outcomes"]["orphan_keys"])
            self.assertTrue(any("book-忘名簿" in x for x in d["coverage"]["problems"]))
            self.assertEqual([], _kinds(d["events"], "book-忘名簿"))

    def test_derive_events_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "done")],
                                 {"t-1": {"qimao": {"stage": "signed"}}},
                                 [{"task_id": "t-1", "action": "upload_chapter",
                                   "ok": True, "quality_forced": True,
                                   "quality_forced_source": "manual"}])
            before = _snapshot(directory)
            journey_sources.derive_events(directory)
            self.assertEqual(before, _snapshot(directory))

    def test_human_coverage_reports_telemetry_gap(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "done")], {})
            d = journey_sources.derive_events(directory)
            text = journey_sources.human_coverage(d["coverage"])
            self.assertIn("未观测", text)
            self.assertIn("不上绿标", text)
            self.assertIn("done ≠ 过签", text)
            self.assertIn("只读", text)

    def test_cli_data_dir_prints_judged_sample_count(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "running")], {})
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = journey_report.main(["--data-dir", directory])
            self.assertEqual(0, rc)
            self.assertIn("真被判过的样本数", buf.getvalue())
            self.assertIn("数据来源", buf.getvalue())

    def test_cli_json_and_source_exclusivity(self):
        with tempfile.TemporaryDirectory() as td:
            directory = _fixture(td, [_task("t-1", "running")], {})
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                journey_report.main(["--data-dir", directory, "--json"])
            payload = json.loads(buf.getvalue())
            self.assertEqual("data-dir", payload["source"])
            self.assertIn("coverage", payload)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    journey_report.main(["--data-dir", directory, "--prod"])

    def test_cli_events_mode_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_text("\n".join(json.dumps({"task_id": "t-1", "kind": k})
                                   for k in ("task_created", "published", "accepted")),
                         encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                journey_report.main(["--events", str(p)])
            self.assertIn("100.0%", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
