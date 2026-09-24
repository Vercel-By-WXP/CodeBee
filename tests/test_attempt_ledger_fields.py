# -*- coding: utf-8 -*-
"""换将尝试记录的计量字段回归（执行算法第 4 条的行为面）。

`test_cost_budget_standard` 只锁文档文本；2026-09-24 复查发现
`attempt_history` 实际只有 duration/error_code，token 分项与 key_id/协议
是后补的。本测试用假 run_process 走真实换将循环，锁住：
1. 每次尝试带 key_id/protocol/tokens/usage/cost_usd/duration；
2. 认证跳过的条目也保留身份（key_id/protocol），不丢账；
3. 路由在线理由带样本层（fallback 标签，成本与时延预算第 5 条）。
认证跳过顺序本身由 test_auth_fallback 覆盖，这里不重复。
"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


def _res(ok, *, stdout="", stderr="", exit_code=0):
    return {"ok": ok, "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": 1.25, "cancelled": False, "timed_out": False,
            "stalled": False}


def _entry(model, key_id, protocol="openai", host="a.test"):
    return {
        "provider_id": "prov-%s" % host.split(".")[0], "key_id": key_id,
        "model": model, "env": {"TEST_ROUTE_KEY": key_id},
        "provider": {"id": "prov-%s" % host.split(".")[0], "name": host,
                     "protocol": protocol, "base_url": "https://%s/v1" % host},
    }


class TestAttemptLedgerFields(BaseTest):
    def _run(self, chain, results):
        from app.core import runner
        attempted = []

        def fake_run_process(**kwargs):
            attempted.append(kwargs["env"].get("TEST_ROUTE_KEY"))
            return results[len(attempted) - 1]

        agent = {"id": "cli-x", "kind": "generic", "mode": "real",
                 "command": "echo", "env": {}, "call_chain": chain}
        with mock.patch.object(runner, "run_process", side_effect=fake_run_process):
            return runner.run_agent(agent, "hi", workdir=str(self.workdir))

    def test_attempt_carries_key_protocol_and_usage(self):
        result = self._run(
            [_entry("m1", "k1"), _entry("m2", "k2", protocol="anthropic",
                                        host="b.test")],
            [_res(False, stderr="HTTP 500 Unexpected server error", exit_code=1),
             _res(True, stdout="done")])
        self.assertTrue(result["ok"])
        first, second = result["attempts"]
        self.assertEqual(first["key_id"], "k1")
        self.assertEqual(first["protocol"], "openai")
        self.assertEqual(second["key_id"], "k2")
        self.assertEqual(second["protocol"], "anthropic")
        for att in (first, second):
            for field in ("tokens", "usage", "cost_usd", "duration",
                          "error_code", "upstream"):
                self.assertIn(field, att)
        # generic CLI 拿不到用量：tokens=0/usage=None 是诚实的"取不到"，
        # 不得估算填充
        self.assertEqual(second["tokens"], 0)
        self.assertIsNone(second["usage"])

    def test_auth_skipped_attempt_keeps_identity(self):
        # 同凭据的第二条目才会被跳过（不同 KEY 各自独立尝试）
        result = self._run(
            [_entry("m1", "k1"), _entry("m2", "k1"), _entry("m2", "k2")],
            [_res(False, stderr="Invalid API Key: Please provide valid API Key",
                  exit_code=1),
             _res(True, stdout="recovered")])
        self.assertTrue(result["ok"])
        skipped = result["attempts"][1]
        self.assertTrue(skipped["skipped"])
        self.assertEqual(skipped["key_id"], "k1")
        self.assertEqual(skipped["protocol"], "openai")


class TestOnlineReasonCarriesSampleLayer(BaseTest):
    def test_reason_ends_with_fallback_label(self):
        from app.core import router

        metrics = {"samples": 4, "successes": 4, "success_rate": 0.9,
                   "p95_duration_s": 40.0, "avg_cost_usd": 0.02,
                   "fallback": "task-role"}
        with mock.patch("app.core.usage.routing_stats",
                        return_value=metrics):
            total, reason = router._online_bonus(
                {"id": "cli-x"}, "implement", "code")
        self.assertEqual(total, round(
            max(-8.0, min(8.0, (0.9 - 0.75) * 24.0))
            - min(18.0, (40.0 - 30.0) / 7.0)
            - min(4.0, (0.02 - 0.01) / 0.01), 1))
        self.assertIn("样本层 task-role", reason)


if __name__ == "__main__":
    import unittest
    unittest.main()
