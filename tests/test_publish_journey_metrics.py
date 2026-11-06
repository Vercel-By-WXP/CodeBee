# -*- coding: utf-8 -*-
"""端到端交付指标守测（unittest 风格，确保 release_gate 的 discover 真能收到）。

守的是三件最容易假绿的事：
1. 「作者正常登记审核结论」不是救火（outcome.py 的刻意设计不能被指标惩罚）；
2. 在跑 / 判不了的样本不进分母，且完成率在无样本时是 None 而不是 100%；
3. 四类人工接管必须被判成 rescued，且报告要打「真被判过的样本数」。
"""
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core.publish import journey  # noqa: E402


def ev(kind, task_id="t-1"):
    return {"task_id": task_id, "kind": kind}


def full_chain(*extra):
    base = [ev("task_created"), ev("draft_done"), ev("review_done"),
            ev("published"), ev("outcome_recorded"), ev("accepted")]
    return base + list(extra)


class JourneyMetricsTest(unittest.TestCase):

    def test_clean_chain_has_no_rescue(self):
        got = journey.classify_journey(full_chain())
        self.assertEqual("clean", got["status"])
        self.assertTrue(got["judged"])
        self.assertEqual([], got["reasons"])

    def test_normal_outcome_registration_is_not_rescue(self):
        """outcome.py 刻意要求审核结论只认作者登记，不能因此把书判成救火。"""
        self.assertIn("outcome_recorded", journey.NEUTRAL_KINDS)
        self.assertNotIn("outcome_recorded", journey.RESCUE_KINDS)
        self.assertEqual("clean", journey.classify_journey(full_chain())["status"])

    def test_each_rescue_kind_is_counted(self):
        for kind in sorted(journey.RESCUE_KINDS):
            got = journey.classify_journey(full_chain(ev(kind)))
            self.assertEqual("rescued", got["status"], kind)
            self.assertIn(kind, got["reasons"])

    def test_incomplete_does_not_enter_denominator(self):
        running = [ev("task_created", "t-2"), ev("draft_done", "t-2")]
        self.assertEqual("incomplete", journey.classify_journey(running)["status"])
        summary = journey.from_events(running + full_chain())
        self.assertEqual(1, summary["judged"])
        self.assertEqual(1, summary["counts"]["incomplete"])

    def test_unknown_inputs_are_not_green(self):
        self.assertEqual("unknown", journey.classify_journey([])["status"])
        self.assertEqual("unknown", journey.classify_journey([None])["status"])
        self.assertEqual("unknown", journey.classify_journey(
            [{"kind": "something_new"}])["status"])
        self.assertEqual("unknown", journey.classify_journey(
            [ev("draft_done"), ev("accepted")])["status"])  # 缺 task_created

    def test_no_judged_sample_reports_none_rate(self):
        summary = journey.from_events([ev("task_created"), ev("draft_done")])
        self.assertIsNone(summary["clean_rate"])
        self.assertIn("unknown", journey.human_report(summary))

    def test_rate_and_counts(self):
        events = full_chain()
        events += [ev("task_created", "t-2"), ev("draft_done", "t-2"),
                   ev("published", "t-2"), ev("force_publish", "t-2"),
                   ev("accepted", "t-2")]
        summary = journey.from_events(events)
        self.assertEqual(2, summary["judged"])
        self.assertEqual({"clean": 1, "rescued": 1, "incomplete": 0, "unknown": 0},
                         summary["counts"])
        self.assertEqual(0.5, summary["clean_rate"])
        self.assertEqual(1, summary["rescue_reasons"]["force_publish"])

    def test_bad_lines_keep_unknown_not_dropped(self):
        summary = journey.from_events([{"kind": "__bad_line__"}])
        self.assertEqual(1, summary["counts"]["unknown"])
        self.assertEqual(0, summary["judged"])

    def test_human_report_reports_judged_sample_count(self):
        report = journey.human_report(journey.from_events(full_chain()))
        self.assertIn("真被判过的样本数 1/1", report)
        self.assertIn("100.0%", report)

    def test_rescue_whitelist_is_explicit(self):
        """契约锁：口径扩大必须显式改这里 + 改守测，不许悄悄加事件。"""
        self.assertEqual(
            frozenset(("force_publish", "manual_retry",
                       "manual_pause_resume", "manual_revise")),
            journey.RESCUE_KINDS)


if __name__ == "__main__":
    unittest.main()
