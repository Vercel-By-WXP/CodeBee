# -*- coding: utf-8 -*-
"""思考过程可视化（2026-09-22 用户诉求「把思考过程打印出来」）单元测试。

覆盖：
  1) SSE 增量解析：openai reasoning_content / anthropic thinking_delta /
     google thought 三协议各认各的字段；tool_calls 分片按 index 归并；
  2) run() 流式路径：思考随 on_reason 回调、结果带 reasoning、工具循环正常；
  3) 非流式回落：events=0（网关不理 stream）→ _post_json 重发，思考字段兜底；
  4) store.stream_step 节流与 finish_step 落 thinking、清 live 态；
  5) /timeline 时间线项下发 thinking（收尾）/ stream+activity（运行中）。
传输层统一打桩（_post_sse_stream / _post_json），不发起真实网络。
"""
from __future__ import annotations

from base import BaseTest


class TestSseReasonParse(BaseTest):
    def runTest(self):
        from app.core import builtin_agent as ba
        acc = {"text": "", "reasoning": "", "tools": {}, "usage": {}}
        # openai：思考在 delta.reasoning_content，正文在 delta.content（云知声实测形状）
        self.assertEqual(ba._reason_from_delta("openai", {
            "choices": [{"delta": {"reasoning_content": "用户问"}}]}), "用户问")
        ba._stream_accumulate("openai", {"choices": [{"delta": {"content": "答"}}]}, acc)
        self.assertEqual(acc["text"], "答")
        # tool_calls 分片：name 与 arguments 分帧到，按 index 归并后成完整调用
        ba._stream_accumulate("openai", {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "read_file",
                                                  "arguments": '{"pa'}}]}}]}, acc)
        ba._stream_accumulate("openai", {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'th": "a.md"}'}}]}}]}, acc)
        calls = ba._calls_from_acc(acc)
        self.assertEqual(calls, [{"id": "c1", "name": "read_file", "args": {"path": "a.md"}}])
        # anthropic：thinking_delta 是思考、text_delta 是正文，二者不串。
        # 思考走 _reason_from_delta、正文走 _stream_accumulate（SSE 读取层各管各的）
        acc2 = {"text": "", "reasoning": "", "tools": {}, "usage": {}}
        self.assertEqual(ba._reason_from_delta("anthropic", {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "想一想"}}), "想一想")
        ba._stream_accumulate("anthropic", {"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": "回"}}, acc2)
        self.assertEqual((acc2["text"], acc2["reasoning"]), ("回", ""))
        self.assertEqual(ba._reason_from_delta("anthropic", {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "回"}}), "")   # 正文不得混进思考
        # google：thought=True 的 part 是思考
        self.assertEqual(ba._reason_from_delta("google", {
            "candidates": [{"content": {"parts": [{"thought": True, "text": "寻思"}]}}]}), "寻思")
        # 非流式响应体里的思考字段（回落路径的兜底抽取）
        self.assertEqual(ba._reason_from_message("openai", {
            "choices": [{"message": {"content": "好", "reasoning_content": "推理串"}}]}), "推理串")
        self.assertEqual(ba._reason_from_message("anthropic", {
            "content": [{"type": "thinking", "thinking": "慢想"},
                        {"type": "text", "text": "答"}]}), "慢想")


class TestStreamRunCapturesReasoning(BaseTest):
    def runTest(self):
        """流式路径：思考逐段回调、结果带 reasoning、工具循环照常走完。"""
        from app.core import builtin_agent as ba
        seen_reason, seen_text, acts = [], [], []
        state = {"n": 0}

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            state["n"] += 1
            self.assertTrue(body.get("stream"))          # 流式请求必须带 stream
            if proto == "openai":
                self.assertTrue((body.get("stream_options") or {}).get("include_usage"))
            for chunk in ("先想", "一下"):
                on_reason(chunk)
            if state["n"] == 1:                          # 首轮：思考+工具调用
                on_text("")
                return {"status": 200, "text": "我看看", "reasoning": "先想一下",
                        "usage": {"input": 9, "output": 4, "total": 13},
                        "calls": [{"id": "c1", "name": "list_files", "args": {"path": ""}}],
                        "events": 5, "error": ""}
            on_text("目录是空的")                          # 次轮：思考+最终正文
            return {"status": 200, "text": "目录是空的", "reasoning": "先想一下",
                    "usage": {"input": 20, "output": 6, "total": 26},
                    "calls": [], "events": 6, "error": ""}

        orig = ba._post_sse_stream
        ba._post_sse_stream = fake_sse
        try:
            bi = {"prov": {"id": "p", "name": "P", "protocol": "openai",
                           "base_url": "http://gw.test/v1", "api_key": "k",
                           "enabled": True, "model": "m"},
                  "model": "m", "provider_id": "p", "provider_name": "P"}
            res = ba.run(bi, "看看", str(self.workdir),
                         on_reason=seen_reason.append, on_stream=seen_text.append,
                         on_activity=acts.append)
        finally:
            ba._post_sse_stream = orig
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["text"], "目录是空的")
        self.assertEqual(res["reasoning"], "先想一下\n\n先想一下")   # 两轮思考按序拼接
        self.assertEqual(res["usage"]["total"], 39)                  # 两轮累计
        self.assertEqual("".join(seen_reason), "先想一下先想一下")    # 思考逐段实时回调
        self.assertEqual([s for s in seen_text if s], ["目录是空的"])  # 正文累计值只在末轮
        self.assertTrue(any("list_files" in a for a in acts))        # 工具活动可见
        self.assertTrue(any("请求工具" in a for a in acts))


class TestStreamFallbackToNonStream(BaseTest):
    def runTest(self):
        """网关不理 stream（零事件）或流式这步挂掉：同 wire 非流式重发，不判死。"""
        from app.core import builtin_agent as ba

        def fake_sse_dead(url, headers, body, allow_private, timeout, proto,
                          on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            raise RuntimeError("boom")

        def fake_json(url, headers, body, allow_private, timeout):
            self.assertNotIn("stream", body)             # 回落请求不带 stream
            return 200, {"choices": [{"message": {
                "content": "非流式回答",
                "reasoning_content": "推理链条"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}, ""

        orig_sse, orig_json = ba._post_sse_stream, ba._post_json
        ba._post_sse_stream = fake_sse_dead
        ba._post_json = fake_json
        try:
            bi = {"prov": {"id": "p", "name": "P", "protocol": "openai",
                           "base_url": "http://gw.test/v1", "api_key": "k",
                           "enabled": True, "model": "m"},
                  "model": "m", "provider_id": "p", "provider_name": "P"}
            res = ba.run(bi, "在吗", str(self.workdir))
        finally:
            ba._post_sse_stream, ba._post_json = orig_sse, orig_json
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["text"], "非流式回答")
        self.assertEqual(res["reasoning"], "推理链条")   # 非流式兜底也抓得到思考
        self.assertEqual(res["usage"]["total"], 6)

    def test_events_zero_falls_back(self):
        from app.core import builtin_agent as ba
        calls = {"json": 0}

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            self.assertTrue(body.get("stream"))
            return {"status": 200, "text": "", "reasoning": "", "usage": {},
                    "calls": [], "events": 0, "error": ""}   # 200 零事件=网关不理 stream

        def fake_json(url, headers, body, allow_private, timeout):
            calls["json"] += 1
            return 200, {"choices": [{"message": {"content": "ok"}}]}, ""

        orig_sse, orig_json = ba._post_sse_stream, ba._post_json
        ba._post_sse_stream, ba._post_json = fake_sse, fake_json
        try:
            bi = {"prov": {"id": "p", "name": "P", "protocol": "openai",
                           "base_url": "http://gw.test/v1", "api_key": "k",
                           "enabled": True, "model": "m"},
                  "model": "m", "provider_id": "p", "provider_name": "P"}
            res = ba.run(bi, "在吗", str(self.workdir))
        finally:
            ba._post_sse_stream, ba._post_json = orig_sse, orig_json
        self.assertTrue(res["ok"])
        self.assertEqual(calls["json"], 1)


class TestStoreStreamStep(BaseTest):
    def runTest(self):
        """stream_step 节流落盘（thinking/stream/activity/live 计数），
        finish_step 落最终 thinking、清运行中态。"""
        from app.core import store
        task = store.create_task({"type": "direct", "title": "流式步骤",
                                  "goal": "想一下", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        step, _ = store.add_step(run["id"], "direct", "builtin", "CodeBee")
        n = step["n"]
        self.assertTrue(store.stream_step(run["id"], n, thinking="想想",
                                          text="部分回答", activity="请求工具: list_files"))
        s = store.get_run(run["id"])["steps"][0]
        self.assertEqual(s["thinking"], "想想")
        self.assertEqual(s["stream"], "部分回答")
        self.assertEqual(s["activity"], ["请求工具: list_files"])
        self.assertGreaterEqual(s["live"], 1)
        # 节流窗口内重复写被拒（返回 False，内容保持上一次落盘值；管道传的是
        # 全量累计值，下个窗口自然追上，不会丢内容）
        self.assertFalse(store.stream_step(run["id"], n, thinking="想想2", min_secs=60))
        self.assertEqual(store.get_run(run["id"])["steps"][0]["thinking"], "想想")
        # 越过节流窗口（min_secs=0 强制）继续落
        self.assertTrue(store.stream_step(run["id"], n, thinking="想想2续", min_secs=0))
        self.assertEqual(store.get_run(run["id"])["steps"][0]["thinking"], "想想2续")
        # 收尾：thinking 定格为最终值，stream/activity/live 过程态清场
        store.finish_step(run["id"], n, "done", summary="答", output="答完",
                          thinking="最终思考")
        s = store.get_run(run["id"])["steps"][0]
        self.assertEqual(s["thinking"], "最终思考")
        self.assertNotIn("stream", s)
        self.assertNotIn("activity", s)
        self.assertNotIn("live", s)
        self.assertEqual(s["status"], "done")


class TestTimelineExposesThinking(BaseTest):
    def runTest(self):
        """/api/runs/<id>/timeline 的 agent 项：收尾带 thinking，运行中带 stream。"""
        from unittest.mock import patch
        from app.core import builtin_agent, modelhub, store
        import importlib

        modelhub._save({
            "providers": [{"id": "prov-th", "name": "思考网关", "protocol": "openai",
                           "base_url": "http://gw.test/v1", "api_key": "sk-test",
                           "enabled": True, "model": "th-model"}],
            "bindings": {},
            "orchestrator": {"provider_id": "prov-th", "model": "th-model",
                             "enabled": True},
        })

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            on_reason("这是思考")
            on_text("这是回答")
            return {"status": 200, "text": "这是回答", "reasoning": "这是思考",
                    "usage": {"input": 3, "output": 2, "total": 5},
                    "calls": [], "events": 4, "error": ""}

        orig = builtin_agent._post_sse_stream
        builtin_agent._post_sse_stream = fake_sse
        try:
            task = store.create_task({"type": "direct", "title": "时间线思考",
                                      "goal": "1+1=?", "workdir": str(self.workdir)})
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            import app.core.pipeline as pipeline
            pipeline._agents = self.mock_agents
            pipeline.execute_run(run["id"])
        finally:
            builtin_agent._post_sse_stream = orig

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual(run["steps"][0]["thinking"], "这是思考")

        # main.py 以 app 目录为运行根（与 test_main_hardening 同款导入方式）。
        # ⚠ main 那边 import 的是 core.store（另一份模块对象，读不到本用例种的
        # 内存态）：把它的 store 引用指到测试这份，跑完还原。
        import sys as _sys
        from pathlib import Path as _Path
        app_root = str(_Path(__file__).resolve().parents[1] / "app")
        if app_root not in _sys.path:
            _sys.path.insert(0, app_root)
        import main as main_mod
        from app.core import store as test_store
        orig_store = main_mod.store
        main_mod.store = test_store
        try:
            handler = main_mod.Handler.__new__(main_mod.Handler)
            holder = {}
            handler._json = lambda code, obj: holder.update(data=obj) or obj
            handler._api_run_timeline(run["id"])
        finally:
            main_mod.store = orig_store
        payload = holder.get("data") or {}
        agent_items = [it for it in payload["items"] if it["kind"] == "agent"]
        self.assertTrue(agent_items)
        done_item = agent_items[0]
        self.assertEqual(done_item["thinking"], "这是思考")   # 收尾：思维链随项下发
        self.assertEqual(done_item["stream"], "")             # 非运行中无实时正文
        self.assertEqual(done_item["text"], "这是回答")
