# -*- coding: utf-8 -*-
"""直连流式单元测试：modelhub._sse_parse 三协议解析 + planner._log_streamer 节流落盘。

背景（2026-09-15）：编排者大纲走 modelhub.chat 直连 HTTP，不经 run_process，
生成全程日志只有一行标题，用户盯着它几分钟以为卡死。chat(on_delta=...) 走
SSE 流式，planner._log_streamer 把增量节流写进步骤日志。"""
from __future__ import annotations

from base import BaseTest


class TestSseParse(BaseTest):
    def runTest(self):
        from app.core import modelhub
        # openai：增量在 choices[].delta.content；usage 在收尾 chunk（include_usage）
        delta, usage = modelhub._sse_parse(
            "openai", {"choices": [{"delta": {"content": "你好"}}]})
        self.assertEqual(delta, "你好")
        self.assertIsNone(usage)
        delta, usage = modelhub._sse_parse("openai", {
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                      "total_tokens": 12,
                      "prompt_tokens_details": {"cached_tokens": 4}}})
        self.assertEqual(delta, "")
        self.assertEqual(usage["total"], 12)
        self.assertEqual(usage["cached"], 4)
        # openai：role 首帧无内容
        self.assertEqual(modelhub._sse_parse(
            "openai", {"choices": [{"delta": {"role": "assistant"}}]})[0], "")

        # anthropic：content_block_delta / message_start / message_delta
        delta, _ = modelhub._sse_parse(
            "anthropic", {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": "章"}})
        self.assertEqual(delta, "章")
        _, usage = modelhub._sse_parse(
            "anthropic", {"type": "message_start",
                          "message": {"usage": {"input_tokens": 8,
                                                "cache_read_input_tokens": 3}}})
        self.assertEqual(usage["input"], 8)
        self.assertEqual(usage["cached"], 3)
        _, usage = modelhub._sse_parse(
            "anthropic", {"type": "message_delta", "usage": {"output_tokens": 5}})
        self.assertEqual(usage["output"], 5)
        self.assertEqual(modelhub._sse_parse("anthropic", {"type": "ping"})[0], "")

        # google：candidates[].content.parts[].text + usageMetadata
        delta, usage = modelhub._sse_parse("google", {
            "candidates": [{"content": {"parts": [{"text": "大纲"}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 1,
                              "totalTokenCount": 8}})
        self.assertEqual(delta, "大纲")
        self.assertEqual(usage["total"], 8)

        # 坏输入一律返回空，不抛
        self.assertEqual(modelhub._sse_parse("openai", "garbage")[0], "")
        self.assertEqual(modelhub._sse_parse("openai", None)[0], "")


class TestLogStreamer(BaseTest):
    def runTest(self):
        from app.core import planner
        p = self.tmp / "stream.log"
        cb = planner._log_streamer(str(p), min_chars=10, min_secs=3600)
        cb("abc")                       # 3 < 10 不写
        self.assertFalse(p.exists())
        for ch in "defghij":
            cb(ch)                      # 凑满 10 触发首次落盘
        self.assertTrue(p.exists())
        self.assertEqual(p.read_text(encoding="utf-8"), "abcdefghij")
        cb("尾段")                      # 不足阈值，挂着
        cb.flush()                      # flush 补上尾段
        self.assertEqual(p.read_text(encoding="utf-8"), "abcdefghij尾段")
        # 原样拼接：不得出现 _append_log 式的额外换行
        self.assertNotIn("\n", p.read_text(encoding="utf-8"))
        # 无 log_path → 空操作不炸
        cb2 = planner._log_streamer(None)
        cb2("x")
        cb2.flush()
