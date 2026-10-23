# -*- coding: utf-8 -*-
"""「路径:行号:内容」命中行不得伪装成供应商错误（2026-10-21 实案）。

grep -n 输出 types.gen.d.ts:429:export type Todo = { 里的行号 429 曾被
_LOG_ERR_HINT 当成限流信号、被 classify_error_text 归成瞬态故障，把健康的
OpenCode 免费通道推进 down 告警。本文件钉住两层防线：
  1. error_codes.classify_error_text 分类前剥离源码位置令牌；
  2. runner._cli_error_lines 直接跳过 grep 命中行。
真实错误措辞（HTTP 429 / 502 / 403 / transport failed）必须照常识别。
"""
from __future__ import annotations

import unittest

from app.core import error_codes, runner


class SourceLocationNoiseTests(unittest.TestCase):
    def test_classify_ignores_line_numbers_inside_source_locations(self):
        noise = ("退出码 1；stderr/stdout: ./.mimocode/node_modules/@mimo-ai/sdk"
                 "/dist/gen/types.gen.d.ts:429:export type Todo = {")
        self.assertIsNone(error_codes.classify_error_text(noise))

    def test_classify_still_reads_real_status_wording(self):
        cases = {
            "HTTP 429 Too Many Requests": error_codes.ErrorCode.RATE_LIMIT,
            "exit 1; stderr/stdout: HTTP 502 bad gateway":
                error_codes.ErrorCode.UPSTREAM_SERVER,
            "unexpected status 403 Forbidden":
                error_codes.ErrorCode.FORBIDDEN,
            "dsh: TRANSPORT: DeepSeek Messages transport failed":
                error_codes.ErrorCode.NETWORK,
            "Error: ripgrep execution failed":
                error_codes.ErrorCode.TOOL_LAUNCH,
        }
        for text, expected in cases.items():
            self.assertEqual(error_codes.classify_error_text(text), expected,
                             msg=text)

    def test_error_line_extraction_skips_grep_hits_but_keeps_real_errors(self):
        blob = "\n".join([
            "./.mimocode/node_modules/@mimo-ai/sdk/dist/gen/types.gen.d.ts:429:"
            "export type Todo = {",
            "src/util.py:12: raise ValueError('quota')",
            "Error: Missing MISTRAL_API_KEY environment variable",
        ])
        self.assertEqual(
            runner._cli_error_lines(blob),
            ["Error: Missing MISTRAL_API_KEY environment variable"],
        )

    def test_windows_absolute_path_hits_are_skipped_too(self):
        blob = "D:\\repo\\src\\app.ts:1204:const retry = 429"
        self.assertEqual(runner._cli_error_lines(blob), [])

    def test_failure_detail_prefers_real_error_over_grep_noise(self):
        res = {
            "stderr": "",
            "stdout": ("=====TODO_POOL=====\n"
                       "./.mimocode/sdk/dist/gen/types.gen.d.ts:429:"
                       "export type Todo = {\n"
                       "Error: upstream reset the connection\n"),
        }
        detail = runner._failure_output_detail(res)
        self.assertIn("upstream reset the connection", detail)
        self.assertNotIn("types.gen.d.ts", detail)


if __name__ == "__main__":
    unittest.main()
