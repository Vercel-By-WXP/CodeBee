# -*- coding: utf-8 -*-
"""思考预算治理（2026-09-22 glm-5.3-flash 47 分钟 19 万 token 零正文案）回归。

背景：推理模型的思考 token 计入 max_tokens（此前写死 8000），GLM 规划
「给教程补逐步示例图」时思考把预算烧光 → 流正常收、正文 0 字、工具 0 个；
旧逻辑把这个空结果当普通失败静默重试 16 轮 → 47 分钟 / 192,566 token 后
报误导性的「模型未返回文本」。

覆盖：
  1) 零正文 + 思考超长（或输出 token 打满）→ 同轮提额 8000→16000 重试并成功；
  2) 持续占满 → 3 次请求内止损，错误文案点明「思考占满输出预算」，绝不 16 轮盲重试；
  3) Anthropic 面「思考程度」偏好生效：low → thinking.budget_tokens；
     网关 400 不认 thinking → 摘除同 KEY 重发，KEY 不进冷却；
  4) 短思考零正文（模型真没说话）→ 可见化重试，连续 3 轮止损；
  5) _build_request 三协议 max_tokens 参数落位。
传输层统一打桩（_post_sse_stream / _post_json），不发起真实网络。
"""
from __future__ import annotations

from base import BaseTest


def _bi(protocol="openai", effort=None):
    bi = {"prov": {"id": "p", "name": "P", "protocol": protocol,
                   "base_url": "http://gw.test/v1", "api_key": "k",
                   "enabled": True, "model": "m"},
          "model": "m", "provider_id": "p", "provider_name": "P"}
    if effort:
        bi["reasoning_effort"] = effort
    return bi


LONG_THINK = "规划示例图的坐标与配色" * 300      # 3300 字 ≥ THINK_EXHAUST_MIN_CHARS


class TestEscalateOnThinkingExhaustion(BaseTest):
    def runTest(self):
        """首轮思考烧穿 8000 预算 → 同轮提到 16000 重试并成功。"""
        from app.core import builtin_agent as ba
        seen = {"max_tokens": [], "n": 0}

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            seen["n"] += 1
            seen["max_tokens"].append(body.get("max_tokens"))
            on_reason(LONG_THINK)
            if seen["n"] == 1:                  # 首轮：思考占满，零正文零工具
                return {"status": 200, "text": "", "reasoning": LONG_THINK,
                        "usage": {"input": 9000, "output": 7950, "total": 16950},
                        "calls": [], "events": 40, "error": ""}
            if on_text:
                on_text("好的，已补上示例图")    # 提额后：正常产出
            return {"status": 200, "text": "好的，已补上示例图",
                    "reasoning": LONG_THINK,
                    "usage": {"input": 9000, "output": 200, "total": 9200},
                    "calls": [], "events": 41, "error": ""}

        orig = ba._post_sse_stream
        ba._post_sse_stream = fake_sse
        try:
            res = ba.run(_bi(), "补示例图", str(self.workdir))
        finally:
            ba._post_sse_stream = orig
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["text"], "好的，已补上示例图")
        self.assertEqual(seen["max_tokens"], [8000, 16000])   # 同轮提额重试
        self.assertEqual(seen["n"], 2)
        self.assertEqual(res["iterations"], 1)                # 不烧迭代数


class TestPersistentExhaustionFailsFast(BaseTest):
    def runTest(self):
        """持续思考烧穿：3 次请求内止损，错误点明「思考占满输出预算」。"""
        from app.core import builtin_agent as ba
        seen = {"n": 0, "max_tokens": []}
        logs = []

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            seen["n"] += 1
            seen["max_tokens"].append(body.get("max_tokens"))
            on_reason(LONG_THINK)
            return {"status": 200, "text": "", "reasoning": LONG_THINK,
                    "usage": {"input": 9000, "output": 15900, "total": 24900},
                    "calls": [], "events": 40, "error": ""}

        orig = ba._post_sse_stream
        ba._post_sse_stream = fake_sse
        try:
            res = ba.run(_bi(), "补示例图", str(self.workdir), log=logs.append)
        finally:
            ba._post_sse_stream = orig
        self.assertFalse(res["ok"])
        self.assertIn("思考", res["error"])
        self.assertIn("输出预算", res["error"])
        self.assertEqual(seen["max_tokens"], [8000, 16000, 16000])  # 提额后续轮沿用
        self.assertLessEqual(seen["n"], 3)                          # 绝不 16 轮盲重试
        self.assertTrue(any("max_tokens" in l for l in logs))       # 提额过程可见


class TestAnthropicThinkingEffort(BaseTest):
    def runTest(self):
        """Anthropic 面「思考程度」：low 显式预算；high/未选不塞（模型自决）。"""
        from app.core import builtin_agent as ba
        bodies = []

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            self.assertEqual(proto, "anthropic")
            bodies.append(dict(body))
            if on_text:
                on_text("答")
            return {"status": 200, "text": "答", "reasoning": "",
                    "usage": {"input": 5, "output": 1, "total": 6},
                    "calls": [], "events": 2, "error": ""}

        orig = ba._post_sse_stream
        ba._post_sse_stream = fake_sse
        try:
            res = ba.run(_bi("anthropic", "low"), "在吗", str(self.workdir))
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(bodies[-1].get("thinking"),
                             {"type": "enabled", "budget_tokens": 1024})
            ba.run(_bi("anthropic", "medium"), "在吗", str(self.workdir))
            self.assertEqual(bodies[-1].get("thinking"),
                             {"type": "enabled", "budget_tokens": 4096})
            ba.run(_bi("anthropic", "high"), "在吗", str(self.workdir))
            self.assertNotIn("thinking", bodies[-1])     # high=放开想，留默认
            ba.run(_bi("anthropic"), "在吗", str(self.workdir))
            self.assertNotIn("thinking", bodies[-1])     # 未选=不塞
        finally:
            ba._post_sse_stream = orig


class TestGatewayRejectingThinking(BaseTest):
    def runTest(self):
        """网关 400 不认 thinking：摘除同 KEY 重发成功，KEY 不进冷却。"""
        from app.core import builtin_agent as ba
        from app.core import modelhub
        seen = {"json_bodies": [], "cooled": []}

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            # 流式这步就 400（真实网关对不认识的 thinking 对象多半在握手期就拒）
            return {"status": 400, "text": "", "reasoning": "", "usage": {},
                    "calls": [], "events": 0,
                    "error": "HTTP 400 thinking 参数不受支持"}

        def fake_json(url, headers, body, allow_private, timeout):
            seen["json_bodies"].append(dict(body))
            if len(seen["json_bodies"]) == 1:
                return 400, {"error": {"message": "thinking is not supported"}}, ""
            return 200, {"content": [{"type": "text", "text": "答"}],
                         "usage": {"input_tokens": 5, "output_tokens": 1}}, ""

        orig_sse, orig_json = ba._post_sse_stream, ba._post_json
        orig_cool = modelhub.note_key_error
        ba._post_sse_stream, ba._post_json = fake_sse, fake_json
        modelhub.note_key_error = \
            lambda pid, kid, err: seen["cooled"].append((pid, kid, err))
        try:
            res = ba.run(_bi("anthropic", "low"), "在吗", str(self.workdir))
        finally:
            ba._post_sse_stream, ba._post_json = orig_sse, orig_json
            modelhub.note_key_error = orig_cool
        self.assertTrue(res["ok"], res.get("error"))
        self.assertIn("thinking", seen["json_bodies"][0])            # 首发带 thinking
        self.assertNotIn("thinking", seen["json_bodies"][-1])        # 摘除后重发
        self.assertEqual(seen["cooled"], [])                         # KEY 不进冷却


class TestShortEmptyStreak(BaseTest):
    def runTest(self):
        """零思考零正文（模型真没说话）：可见化重试，连续 3 轮止损。"""
        from app.core import builtin_agent as ba
        seen = {"n": 0}
        logs = []

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None):
            seen["n"] += 1
            return {"status": 200, "text": "", "reasoning": "",
                    "usage": {"input": 10, "output": 0, "total": 10},
                    "calls": [], "events": 1, "error": ""}

        orig = ba._post_sse_stream
        ba._post_sse_stream = fake_sse
        try:
            res = ba.run(_bi(), "在吗", str(self.workdir), log=logs.append)
        finally:
            ba._post_sse_stream = orig
        self.assertFalse(res["ok"])
        self.assertEqual(seen["n"], 3)               # 连续 3 轮止损（旧逻辑跑满 16 轮）
        self.assertIn("连续 3 轮零正文零工具", res["error"])
        self.assertTrue(any("零正文" in l for l in logs))   # 重试过程可见


class TestBuildRequestMaxTokens(BaseTest):
    def runTest(self):
        """三协议请求体的输出预算都吃 max_tokens 参数。"""
        from app.core import builtin_agent as ba
        msgs = [{"role": "user", "content": "hi"}]
        for proto in ("openai", "anthropic"):
            _, _, body = ba._build_request(proto, "http://g.test/v1", "k",
                                           "m", "s", msgs, False)
            self.assertEqual(body["max_tokens"], 8000)
            _, _, body = ba._build_request(proto, "http://g.test/v1", "k",
                                           "m", "s", msgs, False, max_tokens=16000)
            self.assertEqual(body["max_tokens"], 16000)
        _, _, body = ba._build_request("google", "http://g.test", "k",
                                       "m", "s", msgs, False, max_tokens=12345)
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 12345)
