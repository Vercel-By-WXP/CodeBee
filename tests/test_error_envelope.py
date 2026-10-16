# -*- coding: utf-8 -*-
"""CLI 多行 JSON 错误信封回归（2026-10-16 公司网关 403 实案）。

opencode 把上游错误打成 ANSI 彩色的「Error: 」+ 缩进 JSON 体，错误行探针
按行切只留首行 `Error: {`——健康告警只剩无效文本、classify 全盲（run
r-20261016-131626-6494 第 19 章起草判死，告警里两次都是「Error: {"）。

锁定行为：
1. 多行信封吞行到花括号配平，抽出 message 拼回
   `Error: <name>: <message>`（opencode UnknownError 实案形态）；
2. 同样的修复作用于两条错误路径：`_failure_output_detail`（stderr/stdout 段）
   与 `_augment_error_from_log`（日志错误行段）；
3. 信封里带配额文案时分类跟上：classify_error_text → QUOTA；
4. 「403 用户额度不足」（无「剩余」二字的 one-api 变体）判 QUOTA 而非
   FORBIDDEN——额度强信号先于 403 正则；真权限 403 仍判 FORBIDDEN；
5. 保守边界：JSON 配不平（尾部截断）或解析不动 → 只留首行不吞行；
   NDJSON 事件行（claude 流）原行为不动。
"""
from __future__ import annotations

import unittest

from base import BaseTest

_OPENCODE_ENVELOPE = (
    "\x1b[91m\x1b[1mError: \x1b[0m{\n"
    '  "name": "UnknownError",\n'
    '  "data": {\n'
    '    "message": "Unexpected server error. Check server logs for details.",\n'
    '    "ref": "err_8e6901ed"\n'
    "  }\n"
    "}\n"
)


class TestErrorEnvelope(BaseTest):

    def test_multiline_envelope_absorbed(self):
        from app.core import runner as R
        hits = R._cli_error_lines("前文输出\n" + _OPENCODE_ENVELOPE)
        self.assertEqual(len(hits), 1)
        self.assertIn("UnknownError", hits[0])
        self.assertIn("Unexpected server error", hits[0])
        self.assertTrue(hits[0].startswith("Error:"))
        self.assertNotIn("{", hits[0])

    def test_failure_output_detail_carries_message(self):
        from app.core import runner as R
        detail = R._failure_output_detail({"stderr": "", "stdout": _OPENCODE_ENVELOPE})
        self.assertIn("UnknownError", detail)
        self.assertNotEqual(detail.strip(), "Error: {")

    def test_augment_error_from_log_absorbs_envelope(self):
        from app.core import runner as R
        log = self.workdir / "step.log"
        log.write_text("--- 输出 ---\n" + _OPENCODE_ENVELOPE, encoding="utf-8")
        out = {"error": "退出码 1；stderr/stdout: Error: {"}
        R._augment_error_from_log(out, str(log))
        self.assertIn("UnknownError", out["error"])
        self.assertIn("日志错误行", out["error"])

    def test_envelope_quota_text_classifies_quota(self):
        from app.core import runner as R
        from app.core.error_codes import ErrorCode, classify_error_text
        raw = ('Error: {"error":{"message":"[API Error: 403 用户额度不足, '
               '剩余额度: ¥0.000000 (request id: 20261016082500)]"}}\n')
        hits = R._cli_error_lines(raw)
        self.assertTrue(hits and "用户额度不足" in hits[0])
        self.assertEqual(classify_error_text(hits[0]), ErrorCode.QUOTA)

    def test_bare_403_quota_wording_is_quota_not_forbidden(self):
        from app.core.error_codes import ErrorCode, classify_error_text
        self.assertEqual(
            classify_error_text("[API Error: 403 用户额度不足]"), ErrorCode.QUOTA)
        # 当日实案全文（带「剩余额度」）本来就该 QUOTA，防止回归
        self.assertEqual(
            classify_error_text("[API Error: 403 用户额度不足, 剩余额度: ¥0.000000]"),
            ErrorCode.QUOTA)
        # 真权限 403 不受影响
        self.assertEqual(
            classify_error_text("HTTP 403 Forbidden: model not allowed for this key"),
            ErrorCode.FORBIDDEN)

    def test_truncated_envelope_stays_single_line(self):
        from app.core import runner as R
        hits = R._cli_error_lines('Error: {\n  "name": "UnknownError",\n')
        self.assertEqual(hits, ["Error: {"])

    def test_non_json_brace_block_not_swallowed(self):
        from app.core import runner as R
        text = "Error: broken {\nnot json }\n普通行\n退出码 1 quota exhausted\n"
        hits = R._cli_error_lines(text)
        self.assertEqual(hits, ["Error: broken {", "退出码 1 quota exhausted"])

    def test_messageless_envelope_flattened(self):
        from app.core import runner as R
        hits = R._cli_error_lines('Error: {\n  "ref": "err_123"\n}\n')
        self.assertEqual(hits, ["Error: { \"ref\": \"err_123\" }"])

    def test_ndjson_error_event_unchanged(self):
        from app.core import runner as R
        hits = R._cli_error_lines('{"type":"error","error":{"message":"boom"}}\n')
        self.assertEqual(hits, ["boom"])


if __name__ == "__main__":
    unittest.main()
