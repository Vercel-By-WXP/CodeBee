# -*- coding: utf-8 -*-
"""回执登记覆盖率契约守测（unittest 风格，release_gate 的 discover 必收）。

**夹具口径**：全是 task / outcomes / books.json 的**结构夹具**，不是真稿、
不是真实过签/驳回样本，绝不冒充决策 18 的「真被判过样本」。真实数字只由
CLI 对生产库只读跑出来。

守的是六件最容易「看起来好了」的事：
1. 分母只含 done / 已登记结论的书，在跑与夭折不许稀释覆盖率；
2. 待登记清单区分「完全没回执」与「只到 live」；
3. 孤儿回执只有 books.json 标题**唯一精确命中**才给候选，0/多命中与
   缺 work 一律 unknown，绝不模糊归属；
4. books.json 缺失/损坏不崩、不假绿，孤儿保持 unknown；
5. 这份清单是只读的：derive_events 的事件流与指标口径一字不改，零写盘；
6. CLI --outcome-gaps 能出清单，--events 模式下拒绝（没有回执数据）。
"""
from __future__ import annotations

import contextlib
import hashlib
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

from core.publish import journey, journey_sources  # noqa: E402
import journey_report  # noqa: E402


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _task(tid, status="done", kind="serial_novel"):
    return {"id": tid, "type": kind, "status": status,
            "created_at": "2026-11-01 10:00:00", "title": "结构夹具"}


def _entry(stage, work=None):
    d = {"stage": stage, "source": "manual"}
    if work:
        d["work"] = work
    return {"qimao": d}


def _fixture(root, with_books=True, broken_books=False):
    tasks = [_task("t-a"), _task("t-b"), _task("t-c"),
             _task("t-d", "running"), _task("t-e", "failed"),
             _task("t-f", "done", "doc"), _task("t-g", "queued")]
    for t in tasks:
        _write_json(root / "tasks" / (t["id"] + ".json"), t)
    outcomes = {
        "t-a": _entry("signed"),
        "t-b": _entry("live"),
        "book-甲": _entry("signing_rejected", "甲"),
        "book-乙": _entry("signing_rejected", "乙"),
        "book-丙": _entry("signing_rejected", "丙"),
        "book-无work": _entry("signing_rejected"),
    }
    _write_json(root / "publish" / "outcomes.json", outcomes)
    if broken_books:
        (root / "publish" / "books.json").write_text("{broken", encoding="utf-8")
    elif with_books:
        _write_json(root / "publish" / "books.json", {
            "t-a": {"qimao": {"title": "《甲》"}},
            "t-x": {"qimao": {"title": "丙"}},
            "t-y": {"fanqie": {"title": "丙"}},
        })
    return str(root)


def _snapshot(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.md5(
                p.read_bytes()).hexdigest()
    return out


class OutcomeGapsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = _fixture(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _gaps(self):
        return journey_sources.derive_events(self.data)["coverage"]["outcome_gaps"]

    # ------------------------------------------------------------ 1. 分母
    def test_denominator_only_includes_done_or_registered(self):
        g = self._gaps()
        self.assertEqual(6, g["novel_tasks"])       # doc 类型被排除
        self.assertEqual(3, g["total"])             # t-a / t-b / t-c
        self.assertEqual(1, g["registered"])
        self.assertEqual(0.3333, g["registration_rate"])
        self.assertEqual({"running": 2, "aborted": 1, "other": 0},
                         g["out_of_scope"])
        self.assertEqual(["t-b", "t-c"],
                         [m["task_id"] for m in g["missing"]])

    def test_missing_reasons_distinguish_progress_from_nothing(self):
        reasons = {m["task_id"]: m["reason"] for m in self._gaps()["missing"]}
        self.assertEqual("stage_not_terminal", reasons["t-b"])
        self.assertEqual("no_entry", reasons["t-c"])

    def test_zero_in_scope_returns_unknown_rate_not_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for t in [_task("r-1", "running"), _task("r-2", "failed")]:
                _write_json(root / "tasks" / (t["id"] + ".json"), t)
            _write_json(root / "publish" / "outcomes.json", {})
            g = journey_sources.derive_events(str(root))["coverage"]["outcome_gaps"]
            self.assertEqual(0, g["total"])
            self.assertIsNone(g["registration_rate"])
            self.assertEqual([], g["missing"])

    # ------------------------------------------------------- 2. 孤儿归属
    def test_orphan_exact_candidate_requires_unique_title_match(self):
        by_key = {o["key"]: o for o in self._gaps()["orphans"]}
        self.assertEqual(4, len(by_key))
        self.assertEqual("exact", by_key["book-甲"]["verdict"])
        self.assertEqual(["t-a"], by_key["book-甲"]["candidate_task_ids"])
        self.assertEqual("unknown", by_key["book-乙"]["verdict"])   # 0 命中
        self.assertEqual("unknown", by_key["book-丙"]["verdict"])   # 2 命中
        self.assertEqual([], by_key["book-丙"]["candidate_task_ids"])

    def test_orphan_without_explicit_work_stays_unknown(self):
        by_key = {o["key"]: o for o in self._gaps()["orphans"]}
        self.assertEqual("", by_key["book-无work"]["work"])
        self.assertEqual("unknown", by_key["book-无work"]["verdict"])

    def test_title_normalisation_is_exact_not_fuzzy(self):
        # 《甲》→ 甲 属规范化；「甲乙」绝不能模糊命中「甲」。
        self.assertEqual("甲", journey_sources._norm_title("《甲》"))
        self.assertEqual("甲乙", journey_sources._norm_title(" 甲乙 "))
        bindings, _, _ = journey_sources.load_book_bindings(self.data)
        self.assertIn("甲", bindings["t-a"])
        self.assertNotIn("甲乙", bindings["t-a"])

    def test_missing_or_broken_books_file_keeps_orphans_unknown(self):
        for label, kw in (("missing", {"with_books": False}),
                          ("broken", {"broken_books": True})):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as td:
                    data = _fixture(Path(td), **kw)
                    d = journey_sources.derive_events(data)
                    g = d["coverage"]["outcome_gaps"]
                    self.assertTrue(all(o["verdict"] == "unknown"
                                        for o in g["orphans"]))
                    self.assertTrue(any("books.json" in p
                                        for p in d["coverage"]["problems"])
                                    or not d["coverage"]["sources"]["book_bindings"]["exists"])

    # --------------------------------------------- 3. 指标口径 / 零写入
    def test_gaps_do_not_touch_events_or_metric(self):
        before = _snapshot(self.root)
        d = journey_sources.derive_events(self.data)
        summary = journey.from_events(d["events"])
        self.assertEqual(before, _snapshot(self.root), "适配器不得写盘")
        self.assertNotIn(journey_sources.UNOBSERVABLE_KIND,
                         journey.VALID_KINDS)
        # 夹具里没有一条能进 journey 分母（全被哨兵/夭折挡住），加 gaps 前后一致。
        self.assertEqual(0, summary["judged"])
        self.assertEqual(d["coverage"]["outcome_gaps"],
                         journey_sources.outcome_gaps(
                             journey_sources.load_tasks(self.data)[0],
                             journey_sources.load_outcomes(self.data)[0],
                             journey_sources.load_book_bindings(self.data)[0]))

    def test_human_coverage_reports_judged_count(self):
        d = journey_sources.derive_events(self.data)
        text = journey_sources.human_coverage(d["coverage"])
        self.assertIn("真被判过 1/3 本", text)
        self.assertIn("≠交付完成率", text)

    # ------------------------------------------------------------- 4. CLI
    def test_cli_outcome_gaps_prints_actionable_list(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = journey_report.main(["--data-dir", self.data,
                                      "--outcome-gaps", "--gap-limit", "0"])
        out = buf.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("回执登记缺口", out)
        self.assertIn("t-b", out)
        self.assertIn("book-甲", out)
        self.assertIn("verdict=exact", out)
        self.assertIn("保持 unknown", out)
        self.assertIn("≠ 交付完成率", out)

    def test_cli_gap_limit_truncates(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            journey_report.main(["--data-dir", self.data, "--outcome-gaps",
                                 "--gap-limit", "1"])
        out = buf.getvalue()
        self.assertIn("其余 1 本", out)

    def test_cli_rejects_gaps_on_events_mode(self):
        p = self.root / "events.jsonl"
        p.write_text('{"task_id":"t-1","kind":"task_created"}\n',
                     encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                journey_report.main(["--events", str(p), "--outcome-gaps"])
        self.assertEqual(2, cm.exception.code)

    def test_cli_json_carries_gaps(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            journey_report.main(["--data-dir", self.data, "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(3, payload["coverage"]["outcome_gaps"]["total"])
        self.assertEqual(4, len(payload["coverage"]["outcome_gaps"]["orphans"]))


if __name__ == "__main__":
    unittest.main()
