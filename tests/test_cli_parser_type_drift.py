# -*- coding: utf-8 -*-
"""CLI 输出解析器：字段类型漂移不得让整轮解析抛异常。

`usage` / `item` / `modelUsage` 这类字段只承载统计与展示，不是判定依据。上游
网关换一种写法（`"usage": "n/a"`、`"input_tokens": "1.2k"`、`"modelUsage": []`）
时，旧实现 `u = ev.get("usage") or {}` 只挡 None/假值，随后 `u.get(...)` 抛
AttributeError，异常穿出 run_agent——错误分类与换将白名单全部不生效，用户看到
的是 Python traceback 而不是「网关返回了畸形字段」。
"""
from __future__ import annotations

import unittest

from app.core.cli_parsers import (
    _int_or_zero,
    _parse_claude_json,
    _parse_codex_jsonl,
)


class LenientCoercionTest(unittest.TestCase):
    def test_int_or_zero_absorbs_type_drift(self):
        for value in ("n/a", "1.2k", None, "", [], {}, -5, 7, "12"):
            with self.subTest(value=value):
                self.assertIsInstance(_int_or_zero(value), int)
        self.assertEqual(_int_or_zero("12"), 12)
        self.assertEqual(_int_or_zero("n/a"), 0)
        self.assertEqual(_int_or_zero(-5), 0)


class CodexParserTest(unittest.TestCase):
    def test_string_usage_does_not_raise(self):
        stdout = '{"type":"turn.completed","usage":"n/a"}\n'
        text, usage, sid = _parse_codex_jsonl(stdout)
        self.assertEqual(text, "")
        self.assertEqual(usage["total"], 0)
        self.assertEqual(sid, "")

    def test_scalar_token_counts_are_coerced(self):
        stdout = ('{"type":"turn.completed","usage":'
                  '{"input_tokens":"1.2k","output_tokens":"30","total_tokens":null}}\n')
        _, usage, _ = _parse_codex_jsonl(stdout)
        self.assertEqual(usage["input"], 0)
        self.assertEqual(usage["output"], 30)
        self.assertEqual(usage["total"], 30)

    def test_non_object_item_is_ignored(self):
        stdout = '{"type":"item.completed","item":"boom"}\n'
        text, usage, _ = _parse_codex_jsonl(stdout)
        self.assertEqual(text, "")
        self.assertEqual(usage["input"], 0)

    def test_normal_event_stream_still_parses(self):
        stdout = "\n".join([
            '{"type":"thread.started","thread_id":"t-1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":4,'
            '"cached_input_tokens":2,"reasoning_output_tokens":1,"total_tokens":14}}',
        ])
        text, usage, sid = _parse_codex_jsonl(stdout)
        self.assertEqual(text, "done")
        self.assertEqual(sid, "t-1")
        self.assertEqual(usage, {"input": 10, "output": 4, "cached": 2,
                                 "reasoning": 1, "total": 14})


class ClaudeParserTest(unittest.TestCase):
    def test_string_usage_does_not_raise(self):
        payload = '{"type":"result","result":"ok","usage":7}'
        parsed = _parse_claude_json(payload)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["text"], "ok")
        self.assertEqual(parsed["usage"]["total"], 0)

    def test_list_model_usage_does_not_raise(self):
        payload = '{"type":"result","result":"ok","modelUsage":["a"],"total_cost_usd":"0.5"}'
        parsed = _parse_claude_json(payload)
        self.assertEqual(parsed["served_models"], [])
        self.assertEqual(parsed["cost_usd"], 0.5)

    def test_normal_payload_keeps_numbers(self):
        payload = ('{"type":"result","result":"hi","usage":{"input_tokens":5,'
                   '"output_tokens":3,"cache_read_input_tokens":2},'
                   '"total_cost_usd":0.01,"session_id":"s-1",'
                   '"modelUsage":{"served-x":1}}')
        parsed = _parse_claude_json(payload)
        self.assertEqual(parsed["usage"]["total"], 10)
        self.assertEqual(parsed["sid"], "s-1")
        self.assertEqual(parsed["served_models"], ["served-x"])


if __name__ == "__main__":
    unittest.main()
