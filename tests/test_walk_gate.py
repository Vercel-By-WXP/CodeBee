# -*- coding: utf-8 -*-
"""换将闸门回归（2026-09-23 夜班 doc 起草双连败案）。

两个 run 的完整尸检结论（%APPDATA%/CodeBee/runs/r-20260923-003001-4058 与
r-20260923-030005-8314）：
- 超时能否换将全赌输出尾部碰巧含 "timeout" 字样（当时是步骤里读到的代码
  `timeout=900` 撞了 _TRANSIENT 表）——同一慢上游（公司网关 gemini）连烧
  3×20 分钟全超时，零产出整 run 判死；
- claude 撞上游内容拒答（refusal："can't help with this"）被归为非瞬态，
  整链早死，链上异上游候选（云知声 u2-flash）从未被尝试。

固化三条新语义：
1. refusal 可换将（换异上游候选常常就活，判死则整 run 白烧）；
2. 超时显式可换将（看 run_process 的 timed_out 标志位，不再赌尾部字样）；
3. 超时同因连撞止损：连续两次同签名（头部=超时/输出停滞）就收手，
   最坏情况从 N×步超时压到 2×——链上候选绑同一上游时换将=换壳不换命。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


def _claude_result(text="OK", is_error=False, model_usage=None, session="sess-1"):
    data = {"type": "result", "is_error": is_error, "result": text,
            "session_id": session, "total_cost_usd": 0.01,
            "usage": {"input_tokens": 10, "output_tokens": 5}}
    if model_usage:
        data["modelUsage"] = model_usage
    return {"ok": True, "exit_code": 0, "stdout": json.dumps(data), "stderr": "",
            "duration": 1.0, "cancelled": False, "timed_out": False, "stalled": False}


def _refusal(model="gemini-3.8-flash"):
    return _claude_result(
        text="API Error: %s can't help with this. Start a new session to "
             "continue.\n\nLearn more: https://www.anthropic.com/legal/aup" % model,
        is_error=True, model_usage={model: {"canonicalModel": model}})


def _timeout():
    return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
            "duration": 1200.0, "cancelled": False, "timed_out": True,
            "stalled": False}


def _fail(stdout="boom", exit_code=1):
    return {"ok": False, "exit_code": exit_code, "stdout": stdout, "stderr": "",
            "duration": 1.0, "cancelled": False, "timed_out": False,
            "stalled": False}


def _cancelled():
    # 尾段带瞬态字样（被杀时刻正读到 503）：取消也绝不降级
    return {"ok": False, "exit_code": None, "stdout": "HTTP 503 unavailable", "stderr": "",
            "duration": 5.0, "cancelled": True, "timed_out": False,
            "stalled": False}


def _agent(models=("m1", "m2", "m3")):
    return {"id": "claude-code", "kind": "claude", "mode": "real", "command": "claude",
            "call_chain": [{"model": m, "env": {}, "provider": {},
                            "provider_id": "p-%s" % m} for m in models]}


def _run(agent, results):
    """mock run_process 逐次返回 results；返回 (out, 调用到的模型序列)。"""
    import app.core.runner as R
    calls = []

    def fake(argv=None, **kw):
        argv = list(argv or [])
        calls.append(argv[argv.index("--model") + 1])
        return results[len(calls) - 1]

    with mock.patch.object(R, "run_process", side_effect=fake):
        out = R.run_agent(agent, "hi", readonly=True, timeout=60)
    return out, calls


class TestRefusalWalk(BaseTest):
    def runTest(self):
        # 1) refusal 换将：第一条被上游拒答 → 第二条成功
        out, calls = _run(_agent(("gemini-3.8-flash", "u2-flash")),
                          [_refusal(), _claude_result("CHAIN2-OK")])
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["text"], "CHAIN2-OK")
        self.assertEqual(calls, ["gemini-3.8-flash", "u2-flash"])

        # 2) refusal 是链尾候选 → 照常判失败（不无限烧）
        out, calls = _run(_agent(("only-one",)), [_refusal()])
        self.assertFalse(out["ok"])
        self.assertEqual(calls, ["only-one"])
        self.assertIn("can't help with this", out["error"])

        # 3) 全链都拒答 → 链长内全试（各家病根不同，都值得一次机会）
        out, calls = _run(_agent(("a", "b", "c")),
                          [_refusal("a"), _refusal("b"), _refusal("c")])
        self.assertFalse(out["ok"])
        self.assertEqual(calls, ["a", "b", "c"])

        # 4) 拒答 + 网关模型漂移 → 错误串带漂移注记（排查看对病根）
        out, _ = _run(_agent(("deepseek-v4.1-flash", "u2-flash")),
                      [_refusal(), _claude_result("CHAIN2-OK")])
        self.assertTrue(out["ok"], out.get("error"))

        out2, _ = _run(_agent(("deepseek-v4.1-flash",)),
                       [_refusal("gemini-3.8-flash")])
        self.assertIn("[模型漂移] 请求 deepseek-v4.1-flash，上游实服 gemini-3.8-flash",
                      out2.get("error") or "")


class TestTimeoutWalk(BaseTest):
    def runTest(self):
        # 1) 超时显式换将：尾部干净（无任何瞬态字样）也换——不再赌运气
        out, calls = _run(_agent(("slow", "fast")), [_timeout(), _claude_result("OK")])
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(calls, ["slow", "fast"])

        # 2) 同因连撞止损：三条链全超时 → 第 3 条不再烧（2 次收手）
        same_vendor = _agent(("m1", "m2", "m3"))
        for item in same_vendor["call_chain"]:
            item["provider_id"] = "same-provider"
        out, calls = _run(same_vendor,
                          [_timeout(), _timeout(), _timeout()])
        self.assertFalse(out["ok"])
        self.assertEqual(calls, ["m1", "m2"])

        # 3) 止损只压超时：超时→成功正常救回；超时→瞬态文本照样继续走链
        out, calls = _run(_agent(("m1", "m2", "m3")),
                          [_timeout(), _fail("HTTP 503 unavailable"),
                           _claude_result("OK3")])
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(calls, ["m1", "m2", "m3"])

        # 4) 真终态不降级：非零退出且无任何可换将特征 → 1 次收手
        out, calls = _run(_agent(("m1", "m2")), [_fail("plain crash")])
        self.assertFalse(out["ok"])
        self.assertEqual(calls, ["m1"])


class TestAttemptBudget(BaseTest):
    def test_exhausted_task_deadline_does_not_start_next_model_process(self):
        import app.core.runner as R
        agent = _agent(("slow-a", "fast-b"))
        agent["orch"] = {"timeout_ms": 2400000}
        for item, provider_id in zip(agent["call_chain"], ("p-a", "p-b")):
            item["provider_id"] = provider_id
            item["provider"] = {"id": provider_id,
                                 "base_url": "https://%s.example" % provider_id}

        clock = [0.0]
        calls = []

        def fake(argv=None, **kwargs):
            argv = list(argv or [])
            calls.append(argv[argv.index("--model") + 1])
            clock[0] = 100.0
            return _timeout()

        with mock.patch.object(R.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(R, "run_process", side_effect=fake):
            out = R.run_agent(agent, "hi", readonly=True, timeout=2400,
                              deadline=100.0)

        self.assertFalse(out["ok"])
        self.assertEqual(calls, ["slow-a"])
        self.assertTrue(out["raw"]["deadline_exceeded"])
        self.assertIn("任务总时限已到", out["error"])

    def test_long_attempts_respect_task_deadline_and_switch_after_same_upstream_timeouts(self):
        import app.core.runner as R
        agent = _agent(("slow-a", "slow-b", "fast-c"))
        agent["orch"] = {"timeout_ms": 2400000}
        for item, provider_id in zip(agent["call_chain"], ("p-a", "p-a", "p-b")):
            item["provider_id"] = provider_id
            item["provider"] = {"id": provider_id,
                                 "base_url": "https://%s.example" % provider_id}

        clock = [0.0]
        calls = []
        results = [_timeout(), _timeout(), _claude_result("FAST-C-OK")]

        def fake(argv=None, **kwargs):
            argv = list(argv or [])
            calls.append({"model": argv[argv.index("--model") + 1],
                          "timeout": kwargs["timeout"],
                          "deadline": kwargs["deadline"]})
            clock[0] += 30
            return results[len(calls) - 1]

        with mock.patch.object(R.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(R, "run_process", side_effect=fake):
            out = R.run_agent(agent, "hi", readonly=True, timeout=2400,
                              deadline=100.0)

        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual([call["model"] for call in calls],
                         ["slow-a", "slow-b", "fast-c"])
        self.assertEqual([call["timeout"] for call in calls], [100, 70, 40])
        self.assertEqual([call["deadline"] for call in calls], [100.0] * 3)

    def test_configured_attempt_budget_reaches_each_model_and_different_upstreams_are_tried(self):
        import app.core.runner as R
        agent = _agent(("slow-a", "slow-b", "fast-c"))
        agent["orch"] = {"timeout_ms": 2400000}
        for item, provider_id in zip(agent["call_chain"], ("p-a", "p-b", "p-c")):
            item["provider_id"] = provider_id
            item["provider"] = {"id": provider_id, "base_url": "https://%s.example" % provider_id}
        calls = []
        results = [_timeout(), _timeout(), _claude_result("FAST-C-OK")]

        def fake(argv=None, **kwargs):
            argv = list(argv or [])
            calls.append({"model": argv[argv.index("--model") + 1],
                          "timeout": kwargs.get("timeout")})
            return results[len(calls) - 1]

        with mock.patch.object(R, "run_process", side_effect=fake):
            out = R.run_agent(agent, "hi", readonly=True, timeout=2400)

        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual([c["model"] for c in calls], ["slow-a", "slow-b", "fast-c"])
        for call in calls:
            self.assertAlmostEqual(call["timeout"], 2400)

    def test_same_upstream_timeout_skips_remaining_models_then_tries_another_vendor(self):
        agent = _agent(("slow-a", "slow-b", "slow-c", "fast-d"))
        for item, provider_id in zip(agent["call_chain"], ("p-a", "p-a", "p-a", "p-b")):
            item["provider_id"] = provider_id
            item["provider"] = {"id": provider_id, "base_url": "https://%s.example" % provider_id}
        stalled = dict(_timeout(), stalled=True)
        out, calls = _run(agent, [_timeout(), stalled,
                                  _claude_result("FAST-D-OK")])
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(calls, ["slow-a", "slow-b", "fast-d"])

        # 5) 取消不降级：即使尾段带 503 字样（被杀时刻的输出快照）
        out, calls = _run(_agent(("m1", "m2")), [_cancelled()])
        self.assertFalse(out["ok"])
        self.assertEqual(calls, ["m1"])

    def test_claude_empty_response_retry_uses_remaining_model_budget(self):
        import app.core.runner as R
        agent = _agent(("empty", "next"))
        clock = [0.0]
        timeouts = []
        results = [_claude_result(""), _timeout(), _claude_result("NEXT-OK")]

        def now():
            return clock[0]

        def fake(argv=None, **kwargs):
            timeouts.append(kwargs["timeout"])
            clock[0] += 55
            return results[len(timeouts) - 1]

        with mock.patch.object(R.time, "monotonic", side_effect=now), \
                mock.patch.object(R, "run_process", side_effect=fake):
            out = R.run_agent(agent, "hi", readonly=True, timeout=2400)

        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(timeouts, [2400, 2345, 2400])

    def test_codex_repeated_stream_disconnect_aborts_early_then_falls_back(self):
        import json
        import app.core.runner as R

        agent = {"id": "codex-cli", "kind": "codex", "mode": "real",
                 "command": "codex", "call_chain": [
                     {"model": "broken-stream", "provider_id": "p-a", "env": {}},
                     {"model": "healthy", "provider_id": "p-b", "env": {}},
                 ]}
        reconnect = json.dumps({
            "type": "error",
            "message": "Reconnecting... 1/5 (stream disconnected before completion: "
                       "stream closed before response.completed)",
        })
        aborted = {"ok": False, "exit_code": None,
                   "stdout": reconnect + "\n" + reconnect,
                   "stderr": "[same network error repeated 2 times]",
                   "duration": 3, "cancelled": False, "timed_out": True,
                   "stalled": False}
        success = {"ok": True, "exit_code": 0,
                   "stdout": "\n".join([
                       json.dumps({"type": "item.completed", "item": {
                           "type": "agent_message", "text": "fallback response"}}),
                       json.dumps({"type": "turn.completed", "usage": {}}),
                   ]),
                   "stderr": "", "duration": 1, "cancelled": False,
                   "timed_out": False, "stalled": False}
        built_models, process_calls = [], []

        def build(_agent, _kind, _sid, _readonly, model, _prompt, **_kwargs):
            built_models.append(model)
            return (["codex"], "prompt", "prompt", [])

        def process(**kwargs):
            process_calls.append(kwargs)
            return aborted if len(process_calls) == 1 else success

        with mock.patch.object(R, "_build_call", side_effect=build), \
                mock.patch.object(R, "run_process", side_effect=process):
            out = R.run_agent(agent, "hi", readonly=True, timeout=600)

        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(built_models, ["broken-stream", "healthy"])
        self.assertEqual(process_calls[0]["repeat_abort"], ("Reconnecting...", 2))


if __name__ == "__main__":
    import unittest as _u
    _u.main()
