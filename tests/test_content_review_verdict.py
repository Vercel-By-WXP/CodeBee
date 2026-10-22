"""终判死因文案 + 单评审短链两条闸的回归（r-20261022-102912-8178 实案）。

实案：article 任务阈值 8.0，唯一评审官给「开头吸引力」7.5（其余四维 ≥8.0，
综合 8.2 过线），被「每维 ≥ 阈值」闸判死；终判文案却喊「综合 8.2 < 阈值 8.0」，
且单评审短链压成 1 轮，修订循环从未运行。
"""
import unittest

from app.core import task_compile
from app.core.pipeline import _verdict_fail_reason

REAL_CASE_MEANS = {"选题与标题": 8.0, "开头吸引力": 7.5, "结构节奏": 8.5,
                   "平台适配": 9.0, "传播性": 8.0}


class VerdictFailReasonTests(unittest.TestCase):
    def test_composite_pass_single_dim_fail_names_dim(self):
        reason = _verdict_fail_reason(8.2, 8.0, {"means": REAL_CASE_MEANS, "issues": []},
                                      is_selection=False)
        self.assertIn("维度未过线：开头吸引力 7.5（线 8.0）", reason)
        self.assertNotIn("综合", reason)
        self.assertNotIn("8.2 < 8.0", reason)

    def test_composite_below_reports_both_layers(self):
        reason = _verdict_fail_reason(6.5, 7.0,
                                      {"means": {"全面性": 6.0, "深度": 7.0}, "issues": []},
                                      is_selection=False)
        self.assertIn("综合 6.5 < 阈值 7.0", reason)
        self.assertIn("维度未过线：全面性 6.0（线 7.0）", reason)

    def test_selection_major_veto_reported(self):
        last = {"means": {"证据可靠": 8.0},
                "issues": [{"severity": "major", "note": "缺榜单出处"}]}
        reason = _verdict_fail_reason(8.0, 8.0, last, is_selection=True)
        self.assertIn("1 条 major 未解决", reason)

    def test_minor_issues_never_reported_as_blocking(self):
        last = {"means": {"全面性": 8.0},
                "issues": [{"severity": "minor", "note": "措辞"}]}
        reason = _verdict_fail_reason(8.0, 8.0, last, is_selection=True)
        self.assertNotIn("major", reason)

    def test_no_low_dims_falls_back_to_generic(self):
        reason = _verdict_fail_reason(8.2, 8.0, {}, is_selection=False)
        self.assertIn("综合 8.2 / 阈值 8.0", reason)


class ContentWorkflowGateTests(unittest.TestCase):
    def test_standard_content_keeps_revision_rounds(self):
        wf = task_compile.content_workflow(
            {"type": "article", "rounds": 2}, "default", mode="auto")
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["review_rounds"], 2)
        self.assertIn("回炉", wf["reason"])

    def test_threshold_8_forces_dual_reviewers(self):
        wf = task_compile.content_workflow(
            {"type": "article", "rounds": 2, "threshold": 8.0}, "default", mode="auto")
        self.assertEqual(wf["reviewers"], 2)

    def test_threshold_below_8_keeps_single_reviewer(self):
        wf = task_compile.content_workflow(
            {"type": "article", "rounds": 2, "threshold": 7.9}, "default", mode="auto")
        self.assertEqual(wf["reviewers"], 1)

    def test_high_threshold_beats_planner_single_reviewer_rec(self):
        wf = task_compile.content_workflow(
            {"type": "article", "rounds": 2, "threshold": 8.0}, "default",
            mode="auto", plan={"workflow": {"reviewers": 1}})
        self.assertEqual(wf["reviewers"], 2)


if __name__ == "__main__":
    unittest.main()
