# -*- coding: utf-8 -*-
"""直连流式单元测试：modelhub._sse_parse 三协议解析 + planner._log_streamer 节流落盘。

背景（2026-09-15）：编排者大纲走 modelhub.chat 直连 HTTP，不经 run_process，
生成全程日志只有一行标题，用户盯着它几分钟以为卡死。chat(on_delta=...) 走
SSE 流式，planner._log_streamer 把增量节流写进步骤日志。"""
from __future__ import annotations

import json

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


class TestChatEmptyStreamFallback(BaseTest):
    def runTest(self):
        """实测暴露：网关对大请求回 200 空流 → 必须退回非流式，不能当成功返回空文本。"""
        from app.core import modelhub
        # 先在 providers 里放一个可用供应商（chat 需要 api_key 才走请求路径）
        modelhub._FILE.write_text(json.dumps({
            "providers": [{"id": "prov-x", "name": "X", "protocol": "openai",
                           "base_url": "https://gw.example/v1", "api_key": "k",
                           "enabled": True}],
        }, ensure_ascii=False), encoding="utf-8")

        calls = {"sse": 0, "json": 0}

        def fake_sse(url, headers, body, allow_private, timeout, proto, on_delta):
            calls["sse"] += 1
            return 200, "", {}, ""          # 空流：200 但零事件

        def fake_json(url, headers, body, allow_private, timeout=20):
            calls["json"] += 1
            self.assertNotIn("stream", body)   # 回退请求必须不带 stream
            return 200, {"choices": [{"message": {"content": "非流式结果"}}],
                         "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                                   "total_tokens": 5}}, ""

        modelhub._post_sse_http, orig_sse = fake_sse, modelhub._post_sse_http
        modelhub._post_json_http, orig_json = fake_json, modelhub._post_json_http
        try:
            res = modelhub.chat("prov-x", "m", "hi", on_delta=lambda d: None)
        finally:
            modelhub._post_sse_http = orig_sse
            modelhub._post_json_http = orig_json
        self.assertEqual(calls["sse"], 1)
        self.assertEqual(calls["json"], 1)
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "非流式结果")
        self.assertEqual(res["tokens"], 5)
