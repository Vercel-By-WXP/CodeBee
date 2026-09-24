# -*- coding: utf-8 -*-
"""错误码体系测试（§2D）+ runner 中 _classify_failure 辅助函数测试（§5D）。
设计稿：docs/migration/01-defense-patterns.md §5D + §2D。
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
from base import BaseTest


FIXTURES = Path(__file__).parent


class TestErrorCodeEnum(BaseTest):
    """ErrorCode 枚举 + 分类函数。"""

    def test_enum_values_are_strings(self):
        from app.core.error_codes import ErrorCode
        self.assertEqual(ErrorCode.VENDOR_ERROR.value, "VENDOR_ERROR")
        self.assertEqual(ErrorCode.CANCELLED.value, "CANCELLED")
        self.assertEqual(ErrorCode.EMPTY.value, "EMPTY")

    def test_execution_standard_matrix_matches_enum(self):
        """The markdown matrix is a checked interface, not a second memory."""
        from app.core.error_codes import ErrorCode

        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "execution-standard.md").read_text(encoding="utf-8")
        section = text.split("## 统一错误分类与换路矩阵", 1)[1]
        section = section.split("错误文本分类只用来", 1)[0]
        first_cells = []
        for line in section.splitlines():
            if not line.startswith("|") or line.startswith("| 错误类") or line.startswith("|---"):
                continue
            first_cells.append(line.split("|", 2)[1])
        documented = [code for cell in first_cells
                      for code in re.findall(r"`([A-Z][A-Z0-9_]*)`", cell)]
        expected = set(ErrorCode.__members__)
        self.assertEqual(set(documented), expected)
        self.assertEqual(sorted(documented), sorted(expected),
                         msg="每个错误码应在矩阵中恰好出现一次")
        self.assertFalse(set(documented) - expected)

    def test_execution_standard_covers_multimodal_contract(self):
        """Keep the image capability boundary in the executable standard."""
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "execution-standard.md").read_text(encoding="utf-8")
        self.assertIn("| 多模态能力 |", text)
        section = text.split("## 多模态评测规则", 1)[1]
        section = section.split("## 统一错误分类与换路矩阵", 1)[0]
        for marker in (
            "image_in",
            "image_out",
            "HTTP 200 + 错误信封",
            "图像输入",
            "图像输出",
            "可解码",
            "image part",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, section)

    def test_execution_standard_pins_evaluation_expiry(self):
        """A passed probe must remain evidence with a documented clock."""
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "execution-standard.md").read_text(encoding="utf-8")
        for marker in ("evaluated_at", "expires_at", "fresh",
                       "TUTTI_EVALUATION_TTL_S", "不能复活旧的 `passed`"):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_is_fatal(self):
        from app.core.error_codes import is_fatal, ErrorCode
        self.assertTrue(is_fatal(ErrorCode.VENDOR_ERROR))
        self.assertTrue(is_fatal(ErrorCode.VENDOR_REFUSAL))
        self.assertTrue(is_fatal(ErrorCode.PARSE_FAIL))
        self.assertTrue(is_fatal(ErrorCode.ENV_BLOCK))
        self.assertTrue(is_fatal(ErrorCode.SIGNAL_IGNORED))
        self.assertTrue(is_fatal("VENDOR_ERROR"))  # 字符串也可
        self.assertTrue(is_fatal("VENDOR_REFUSAL"))
        self.assertFalse(is_fatal(ErrorCode.TIMEOUT))
        self.assertFalse(is_fatal(ErrorCode.CANCELLED))
        self.assertFalse(is_fatal(ErrorCode.EMPTY))
        self.assertFalse(is_fatal(""))
        self.assertFalse(is_fatal(None))
        self.assertFalse(is_fatal("UNKNOWN_CODE"))

    def test_is_retryable(self):
        from app.core.error_codes import is_retryable, ErrorCode
        self.assertTrue(is_retryable(ErrorCode.TIMEOUT))
        self.assertTrue(is_retryable(ErrorCode.EMPTY))
        self.assertTrue(is_retryable(ErrorCode.MAX_TOKENS))
        self.assertTrue(is_retryable(ErrorCode.NETWORK))
        self.assertFalse(is_retryable(ErrorCode.VENDOR_ERROR))
        self.assertFalse(is_retryable(ErrorCode.VENDOR_REFUSAL))
        self.assertFalse(is_retryable(ErrorCode.CANCELLED))  # 用户显式取消不自动重试
        self.assertFalse(is_retryable(""))


class TestSharedFailureClassification(BaseTest):
    """Transport/vendor failures share one stable vocabulary across layers."""

    def test_shared_classification(self):
        from app.core.error_codes import (
            ErrorCode, classify_error_text, error_code_value, is_auth_error,
        )

        cases = (
            ("No API key found for the selected model", ErrorCode.MISSING_CREDENTIAL),
            ("HTTP 401: invalid_api_key", ErrorCode.AUTH),
            ("HTTP 403 Forbidden: request not allowed", ErrorCode.FORBIDDEN),
            ("HTTP 403: invalid API key for this model", ErrorCode.FORBIDDEN),
            ("HTTP 401 invalid API key for model foo-403beta", ErrorCode.AUTH),
            ("invalid API key for model foo-403beta", ErrorCode.AUTH),
            ("HTTP 429 rate limit exceeded", ErrorCode.RATE_LIMIT),
            ("concurrent limit exceeded", ErrorCode.RATE_LIMIT),
            ("并发超限，请稍后重试", ErrorCode.RATE_LIMIT),
            ("unexpected status 403 forbidden", ErrorCode.FORBIDDEN),
            ("unexpected status 401", ErrorCode.AUTH),
            ("unexpected status 429", ErrorCode.RATE_LIMIT),
            ("insufficient balance / quota exhausted", ErrorCode.QUOTA),
            ("Unexpected server error", ErrorCode.UPSTREAM_SERVER),
            ("stream disconnected before response.completed", ErrorCode.NETWORK),
            ("deadline exceeded", ErrorCode.TIMEOUT),
            ("Unexpected error database is locked", ErrorCode.LOCAL_STATE),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(classify_error_text(message), expected)
                self.assertEqual(error_code_value(expected), expected.value)
        self.assertIsNone(classify_error_text("some unclassified vendor output"))
        self.assertIsNone(classify_error_text("vendor failed for model foo-403beta"))
        self.assertFalse(is_auth_error("HTTP 403: invalid API key for this model"))
        self.assertTrue(classify_error_text("Error: failed to run prompt: provider.connection_error: Connection error"))
        self.assertIsNone(classify_error_text("record 402 is missing from the report"))
        self.assertEqual(classify_error_text("HTTP 402 payment required"), ErrorCode.QUOTA)

    def test_runner_error_code_uses_shared_taxonomy(self):
        from app.core import runner
        from app.core.error_codes import ErrorCode

        for message, expected in (
            ("Unexpected server error", ErrorCode.UPSTREAM_SERVER),
            ("stream disconnected", ErrorCode.NETWORK),
            ("No API key found", ErrorCode.MISSING_CREDENTIAL),
            ("HTTP 403 Forbidden", ErrorCode.FORBIDDEN),
            ("HTTP 403: invalid API key for this model", ErrorCode.FORBIDDEN),
        ):
            with self.subTest(message=message):
                self.assertEqual(runner._classify_failure({
                    "ok": False, "exit_code": 1, "stdout": message, "stderr": "",
                }), expected)


class TestClassifyFailureHelper(BaseTest):
    """runner._classify_failure 单测：所有分支。"""

    def _ok_res(self):
        return {"ok": True, "cancelled": False, "timed_out": False,
                "exit_code": 0, "stdout": "", "stderr": ""}

    def _cancelled_res(self):
        return {"ok": False, "cancelled": True, "timed_out": False,
                "exit_code": None, "stdout": "", "stderr": ""}

    def _timed_out_res(self):
        return {"ok": False, "cancelled": False, "timed_out": True,
                "exit_code": None, "stdout": "", "stderr": ""}

    def _exit_nonzero_res(self):
        return {"ok": False, "cancelled": False, "timed_out": False,
                "exit_code": 2, "stdout": "", "stderr": "boom"}

    def test_success_returns_empty(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        self.assertEqual(_classify_failure(self._ok_res()), "")
        self.assertEqual(_classify_failure(self._ok_res(), kind="claude",
                                           parsed={"text": "ok"}), "")

    def test_cancelled(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        self.assertEqual(_classify_failure(self._cancelled_res()),
                         ErrorCode.CANCELLED)
        self.assertEqual(_classify_failure(self._cancelled_res(), kind="claude"),
                         ErrorCode.CANCELLED)

    def test_timeout(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        self.assertEqual(_classify_failure(self._timed_out_res()),
                         ErrorCode.TIMEOUT)
        self.assertEqual(_classify_failure(self._timed_out_res(), kind="claude"),
                         ErrorCode.TIMEOUT)

    def test_claude_parse_fail(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        # 进程 ok=True 但 parsed=None（claude 输出非 JSON）
        self.assertEqual(_classify_failure(self._ok_res(), kind="claude", parsed=None),
                         ErrorCode.PARSE_FAIL)

    def test_claude_is_error_classifies_upstream_status(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        self.assertEqual(_classify_failure(
            self._ok_res(), kind="claude",
            parsed={"is_error": True, "text": "API Error 503"}),
            ErrorCode.UPSTREAM_SERVER)

    def test_claude_content_refusal(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        self.assertEqual(_classify_failure(
            self._ok_res(), kind="claude",
            parsed={"is_error": True, "text": "I can't help with this request."}),
            ErrorCode.VENDOR_REFUSAL)

    def test_claude_empty_after_retry(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        # attempt_done=True 且 text 为空
        self.assertEqual(_classify_failure(
            self._ok_res(), kind="claude",
            parsed={"is_error": False, "text": ""}, attempt_done=True),
            ErrorCode.EMPTY)

    def test_claude_empty_first_attempt_no_classify(self):
        """首次空响应不归 EMPTY，等重试。"""
        from app.core.runner import _classify_failure
        self.assertEqual(_classify_failure(
            self._ok_res(), kind="claude",
            parsed={"is_error": False, "text": ""}, attempt_done=False),
            "")

    def test_exit_nonzero(self):
        from app.core.runner import _classify_failure
        from app.core.error_codes import ErrorCode
        self.assertEqual(_classify_failure(self._exit_nonzero_res()),
                         ErrorCode.VENDOR_ERROR)
        # 非 claude kind + 解析失败走 VENDOR_ERROR
        self.assertEqual(_classify_failure(self._exit_nonzero_res(), kind="codex"),
                         ErrorCode.VENDOR_ERROR)


class TestRunAgentErrorCodeField(BaseTest):
    """runner.run_agent 返回 dict 必含 error_code 字段。"""

    def test_dict_has_error_code_on_success(self):
        """fake role_cli（网文主编）→ ok=True → error_code 应为空字符串。"""
        from app.core import runner
        agent = {"kind": "generic", "command": sys.executable,
                 "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
                 "mode": "real"}
        out = runner.run_agent(agent, "网文主编 出大纲", readonly=True, timeout=30)
        self.assertTrue(out["ok"], msg=f"unexpected: {out.get('error')}")
        self.assertIn("error_code", out)
        self.assertEqual(out["error_code"], "")

    def test_dict_has_error_code_on_failure(self):
        """bad_cli（始终非 JSON 退出 0，但 generic 不在意），改用 generic 空输出 cli。"""
        # 写一个立即退出非 0 的临时 fake
        import tempfile
        import os
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write("import sys\n")
            f.write("sys.exit(7)\n")
            tmp = f.name
        try:
            from app.core import runner
            agent = {"kind": "generic", "command": sys.executable,
                     "argv_template": [tmp, "{prompt}"], "mode": "real"}
            out = runner.run_agent(agent, "hi", readonly=True, timeout=10)
            self.assertFalse(out["ok"])
            self.assertIn("error_code", out)
            self.assertEqual(out["error_code"], "VENDOR_ERROR")
        finally:
            os.unlink(tmp)

    def test_generic_empty_output_marks_empty(self):
        """generic cli 输出空 → error_code = EMPTY。"""
        import tempfile
        import os
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write("import sys\n")
            # 不写任何东西到 stdout，exit 0
            tmp = f.name
        try:
            from app.core import runner
            agent = {"kind": "generic", "command": sys.executable,
                     "argv_template": [tmp, "{prompt}"], "mode": "real"}
            out = runner.run_agent(agent, "hi", readonly=True, timeout=10)
            self.assertIn("error_code", out)
            self.assertEqual(out["error_code"], "EMPTY")
        finally:
            os.unlink(tmp)
