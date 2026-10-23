# -*- coding: utf-8 -*-
"""凭据缺失/未登录的措辞覆盖（2026-10-23 实案）。

一次真实长跑里，同一个死号被每个评审维度、每一轮反复重撞，根因不是
"没有换通道"逻辑缺失，而是这些真实错误文本被分类成 None：

- mistral-vibe：``Error: Missing MISTRAL_API_KEY environment variable for
  mistral provider.`` —— marker 表只有 "missing api key" 连写形式，变量名
  内嵌（MISTRAL_API_KEY）匹配不到，单轮重撞 22 次。
- codebuddy：``Authentication required. Please use /login command to sign
  in to your account`` —— 只有 "please run /login"，"please use /login"
  漏网。

分类为 None 时既不进本场停用台账，也不进换将白名单，于是"换另一个"
永远不会发生。这些用例锁住该行为。
"""
from __future__ import annotations

import unittest

from app.core import routing_strategy
from app.core.error_codes import (
    ErrorCode, classify_error_text, error_code_value, is_account_dead_error,
)


class CredentialMissingWordingTests(unittest.TestCase):
    def test_missing_api_key_variable_forms(self):
        samples = (
            'Error: Missing MISTRAL_API_KEY environment variable for '
            'mistral provider. Set the environment variable (e.g. in '
            '~/.vibe/.env or your shell), or run `vibe --setup` once '
            'interactively.',
            "Missing MISTRAL_API_KEY",
            "MISTRAL_API_KEY is not set",
            "GEMINI_API_KEY environment variable not set",
            "Missing environment variable: DEEPSEEK_API_KEY",
            "Error: Missing API key",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    error_code_value(classify_error_text(text)),
                    ErrorCode.MISSING_CREDENTIAL.value)

    def test_login_required_wordings(self):
        samples = (
            "Authentication required. Please use /login command to sign in "
            "to your account",
            "Please sign in to continue",
            "not authenticated",
            "You are not logged in",
            "please run /login",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    error_code_value(classify_error_text(text)),
                    ErrorCode.MISSING_CREDENTIAL.value)

    def test_credential_dead_errors_still_fall_back_to_another_cli(self):
        strategy = routing_strategy.get()
        for text in ("Missing MISTRAL_API_KEY",
                     "Authentication required. Please use /login command"):
            with self.subTest(text=text):
                code = error_code_value(classify_error_text(text))
                self.assertTrue(is_account_dead_error(code))
                self.assertTrue(strategy.should_fallback(
                    {"error_code": code, "error": text}))

    def test_unrelated_text_is_not_misread_as_credential(self):
        for text in ("model not found", "no such model",
                     "TypeError: cannot read properties of undefined",
                     "process exited with code 3221226505"):
            with self.subTest(text=text):
                self.assertNotEqual(
                    error_code_value(classify_error_text(text)),
                    ErrorCode.MISSING_CREDENTIAL.value)


if __name__ == "__main__":
    unittest.main()
