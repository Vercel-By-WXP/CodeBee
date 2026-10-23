# -*- coding: utf-8 -*-
"""「验证未执行」不得被写成「验证通过」（2026-10-23 回归）。

背景：`_run_verify` 在任务未配置验证命令时返回 (True, False)——verify_pass 为真
只表示没有失败证据，不代表跑过。旧报告正文与任务摘要只读 verify_pass，于是真实
运行 `r-20260917-195700-0323` 的 report.md 出现：

    - 结论：**✅ 通过**
    - 验证命令：（未配置） → 通过

本测试锁定三态口径（通过 / 未通过 / 未执行），并同时锁住证据文件里「没有验证
步骤」时的显式说明。全部为纯函数与打桩，不启动服务、不碰生产端口。
"""
import unittest
from pathlib import Path
from unittest import mock

from app.core import pipeline, pipeline_code

ROOT = Path(__file__).resolve().parents[1]


class VerifyTextTests(unittest.TestCase):
    def test_not_run_is_never_reported_as_pass(self):
        self.assertEqual(pipeline_code._verify_text(True, False), "未执行")
        # 未跑时即便 verify_pass 是 False（例如降级路径），也不能冒充「跑挂了」。
        self.assertEqual(pipeline_code._verify_text(False, False), "未执行")

    def test_ran_states_keep_their_verdict(self):
        self.assertEqual(pipeline_code._verify_text(True, True), "通过")
        self.assertEqual(pipeline_code._verify_text(False, True), "未通过")


class VerifyReportLinesTests(unittest.TestCase):
    def test_unconfigured_command_says_not_run_and_never_pass(self):
        lines = pipeline_code._verify_report_lines({"title": "t"}, True, False)
        self.assertEqual(lines[0], "- 验证命令：（未配置） → 未执行")
        self.assertIn("验证未执行", lines[1])
        self.assertIn("本任务未配置验证命令", lines[1])
        joined = "\n".join(lines)
        self.assertNotIn("→ 通过", joined)

    def test_configured_but_unreached_distinguishes_the_reason(self):
        lines = pipeline_code._verify_report_lines(
            {"title": "t", "verify_command": "python -m unittest"}, True, False)
        self.assertEqual(lines[0], "- 验证命令：python -m unittest → 未执行")
        self.assertIn("运行未到达验证步骤", lines[1])

    def test_ran_passes_emit_single_line_without_warning(self):
        lines = pipeline_code._verify_report_lines(
            {"title": "t", "verify_command": "pytest -q"}, True, True)
        self.assertEqual(lines, ["- 验证命令：pytest -q → 通过"])

    def test_ran_failure_emits_single_line_without_warning(self):
        lines = pipeline_code._verify_report_lines(
            {"title": "t", "verify_command": "pytest -q"}, False, True)
        self.assertEqual(lines, ["- 验证命令：pytest -q → 未通过"])


class EvidenceLinesTests(unittest.TestCase):
    def _lines(self, run, task):
        with mock.patch.object(pipeline.store, "get_run", return_value=run):
            return pipeline._evidence_lines_from_run("r-test", task)

    def test_code_run_without_verify_step_is_marked_not_run(self):
        lines = self._lines({"steps": [], "verdict": {"type": "code"}}, {})
        self.assertIn("验证命令 `（未配置）` → 未执行", lines)

    def test_code_run_with_verify_step_keeps_real_status(self):
        run = {"steps": [{"role": "verify", "status": "done", "summary": "验证通过"}],
               "verdict": {"type": "code"}}
        lines = self._lines(run, {"verify_command": "pytest -q"})
        self.assertEqual(lines, ["验证命令 `pytest -q` → done（验证通过）"])

    def test_non_code_run_does_not_gain_verify_noise(self):
        lines = self._lines({"steps": [], "verdict": {"type": "novel"}}, {})
        self.assertEqual(lines, [])


class VerifyCardFrontendTests(unittest.TestCase):
    """工作台验证卡片必须走三态，且不得再用字符串比较 verdict 对象。"""

    def test_card_does_not_compare_verdict_object_to_string(self):
        src = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("//"))
        # `run.verdict === "pass"` 对对象恒为 false：跑通的任务显示「验证未通过」。
        self.assertNotIn('run.verdict === "pass"', code)
        self.assertIn("v.verify_ran !== false", code)

    def test_not_run_label_is_translatable(self):
        src = (ROOT / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('"验证未执行"', src)


if __name__ == "__main__":
    unittest.main()
