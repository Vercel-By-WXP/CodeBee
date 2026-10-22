# -*- coding: utf-8 -*-
"""评审裁定 JSON 摘要化：步骤 summary 单行版 + 对话时间线多行版。

背景（2026-10-22 用户反馈「展示这么一大堆根本没人看」）：评审官的
```json 裁定原文曾整段截 600 字塞进步骤 summary、整段塞进对话气泡。
修复 = 落盘/下发前先摘要（runner_parsing.verdict_brief / verdict_digest，
pipeline._step_summary_text 与 main._emit_step 接线），前端对历史 run 的
原文落盘数据同口径兜底（app.js verdictDigestText）。本文件锁后端行为与
前端接线点。
"""

import io
import os
import unittest

from app.core import pipeline
from app.core.runner_parsing import verdict_brief, verdict_digest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 2026-10-22 真实评审输出形状（r-20261022-102912-8178 步骤4，自媒体文章流）
CONTENT_VERDICT = (
    '```json\n{\n  "scores": {"选题与标题": 8.0, "开头吸引力": 7.5, '
    '"结构节奏": 8.5, "平台适配": 9.0, "传播性": 8.0},\n'
    '  "issues": [\n'
    '    {"dim": "平台适配", "severity": "minor", '
    '"note": "时间轴总长为 61-67 秒，结尾段无明确行动收束时长偏长。"},\n'
    '    {"dim": "开头吸引力", "severity": "minor", '
    '"note": "开篇钩子数字冲击力强，但未呈现具体冲突画面。"}\n'
    '  ],\n'
    '  "summary": "整体完成度高，事实复核扎实、平台适配性强。"\n}\n```'
)

CODE_VERDICT = (
    '{"pass": false, "scores": {"正确性": 6.0, "安全": 7.0}, '
    '"findings": [{"path": "app/x.py", "severity": "major", '
    '"header": "未校验入参", "detail": "空列表会下标越界"}], '
    '"summary": "主干可用但有越界风险"}'
)


class VerdictBriefTests(unittest.TestCase):
    def test_fenced_content_verdict_becomes_one_line_brief(self):
        brief = verdict_brief(CONTENT_VERDICT)
        self.assertIsNotNone(brief)
        self.assertTrue(brief.startswith("均分 8.2"))
        self.assertIn("选题与标题 8.0", brief)
        self.assertIn("整体完成度高", brief)
        self.assertIn("意见 2 条", brief)
        self.assertNotIn("\n", brief)
        self.assertNotIn("```", brief)

    def test_bare_json_object_also_recognized(self):
        brief = verdict_brief('{"scores": {"情节": 9.0}, "summary": "不错"}')
        self.assertIsNotNone(brief)
        self.assertIn("均分 9.0", brief)
        self.assertIn("不错", brief)

    def test_code_verdict_keeps_pass_verdict_and_path(self):
        brief = verdict_brief(CODE_VERDICT)
        self.assertIsNotNone(brief)
        self.assertTrue(brief.startswith("评审未通过，"))
        self.assertIn("app/x.py", brief)
        self.assertIn("意见 1 条", brief)

    def test_plain_text_is_not_a_verdict(self):
        self.assertIsNone(verdict_brief("评分表 scores 应当客观，这是普通回答。"))
        self.assertIsNone(verdict_brief(""))
        self.assertIsNone(verdict_brief(None))

    def test_non_numeric_or_bool_scores_rejected(self):
        self.assertIsNone(verdict_brief('{"scores": {"情节": "好"}}'))
        self.assertIsNone(verdict_brief('{"scores": {"情节": true}}'))
        self.assertIsNone(verdict_brief('{"other": 1}'))

    def test_brief_length_capped(self):
        long_note = {"scores": {"情节": 8.0},
                     "issues": [{"dim": "情节", "severity": "minor",
                                 "note": "啰" * 500}]}
        self.assertLessEqual(len(verdict_brief(long_note)), 260)


class VerdictDigestTests(unittest.TestCase):
    def test_digest_multiline_with_issue_lines(self):
        digest = verdict_digest(CONTENT_VERDICT)
        self.assertIsNotNone(digest)
        lines = digest.splitlines()
        self.assertTrue(lines[0].startswith("评审均分 8.2"))
        self.assertTrue(any(l.startswith("结论：") for l in lines))
        self.assertIn("意见 2 条：", lines)
        minors = [l for l in lines if l.startswith("· [minor] ")]
        self.assertEqual(len(minors), 2)
        self.assertTrue(minors[0].startswith("· [minor] 平台适配：时间轴总长"))

    def test_code_digest_marks_failed_review(self):
        digest = verdict_digest(CODE_VERDICT)
        self.assertIsNotNone(digest)
        self.assertTrue(digest.startswith("评审未通过，评审均分"))
        self.assertIn("· [major] app/x.py：未校验入参", digest)

    def test_digest_caps_at_six_issues(self):
        issues = [{"dim": "d%d" % i, "severity": "minor", "note": "n%d" % i}
                  for i in range(9)]
        digest = verdict_digest({"scores": {"a": 7.0}, "issues": issues})
        self.assertIn("意见 9 条：", digest)
        self.assertIn("…其余 3 条见该步日志", digest)

    def test_plain_text_not_a_verdict(self):
        self.assertIsNone(verdict_digest("普通回答文本"))
        self.assertIsNone(verdict_digest(None))


class StepSummaryWiringTests(unittest.TestCase):
    """_finish_step_result 的摘要来源：裁定走 brief，其余维持原文/报错。"""

    def test_verdict_text_becomes_brief(self):
        res = {"ok": True, "text": CONTENT_VERDICT}
        summary = pipeline._step_summary_text(res)
        self.assertTrue(summary.startswith("均分 8.2"))
        self.assertNotIn("{", summary)

    def test_plain_text_and_error_unchanged(self):
        self.assertEqual(pipeline._step_summary_text(
            {"text": "普通回答"}), "普通回答")
        self.assertEqual(pipeline._step_summary_text(
            {"text": "", "error": "退出码 1；stderr/stdout: x"}), "退出码 1；stderr/stdout: x")

    def test_checkpoint_notice_stripped_of_leading_blank_lines(self):
        summary = pipeline._step_summary_text(
            {"text": "\n\n（检查点范围提示：Git 仓库没有可用 HEAD）"})
        self.assertEqual(summary, "（检查点范围提示：Git 仓库没有可用 HEAD）")

    def test_long_plain_text_truncated_at_600(self):
        summary = pipeline._step_summary_text({"text": "字" * 900})
        self.assertEqual(len(summary), 600)


class FrontendWiringTests(unittest.TestCase):
    """app.js 兜底助手存在且接进全部摘要展示面；时间线/落盘后端已换摘要。"""

    def _read(self, *parts):
        with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_app_js_defines_and_wires_digest_helper(self):
        app = self._read("app", "ui", "app.js")
        self.assertIn("function verdictDigestText(", app)
        # 两处详情步骤行 + 检查器（title/sum）+ 蜂巢两处 + 对话正文 + 复制
        self.assertGreaterEqual(app.count("verdictDigestText("), 9)

    def test_backend_emitters_use_digest(self):
        pipe = self._read("app", "core", "pipeline.py")
        self.assertIn("runner.verdict_brief(text)", pipe)
        main = self._read("app", "main.py")
        self.assertIn("runner.verdict_digest(body)", main)


if __name__ == "__main__":
    unittest.main()
