# -*- coding: utf-8 -*-
"""认证失败只降级到绑定链中的不同凭据，不重放同一把 KEY。"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


def _res(ok, *, stdout="", stderr="", exit_code=0):
    return {"ok": ok, "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": 0.01, "cancelled": False, "timed_out": False,
            "stalled": False}


def _chain_entry(provider_id, key_id, model, host, key):
    return {
        "provider_id": provider_id,
        "key_id": key_id,
        "model": model,
        "env": {"TEST_ROUTE_KEY": key},
        "provider": {"id": provider_id, "name": provider_id,
                     "base_url": "https://%s/v1" % host},
    }


class TestAuthFallback(BaseTest):
    def _run(self, chain, results):
        from app.core import runner
        attempted = []

        def fake_run_process(**kwargs):
            attempted.append(kwargs["env"].get("TEST_ROUTE_KEY"))
            return results[len(attempted) - 1]

        agent = {"id": "cli-x", "kind": "generic", "mode": "real",
                 "command": "echo", "env": {}, "call_chain": chain}
        with mock.patch.object(runner, "run_process", side_effect=fake_run_process):
            result = runner.run_agent(agent, "hi", workdir=str(self.workdir))
        return result, attempted

    def test_auth_failure_skips_same_key_and_tries_other_key_then_provider(self):
        chain = [
            _chain_entry("prov-a", "k1", "m1", "a.test", "a-key-1"),
            _chain_entry("prov-a", "k1", "m2", "a.test", "a-key-1"),
            _chain_entry("prov-a", "k2", "m2", "a.test", "a-key-2"),
            _chain_entry("prov-b", "k1", "m1", "b.test", "b-key-1"),
        ]
        results = [
            _res(False, stderr="Invalid API Key: Please provide valid API Key", exit_code=1),
            _res(False, stderr="HTTP 401 Unauthorized", exit_code=1),
            _res(True, stdout="recovered"),
        ]

        result, attempted = self._run(chain, results)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(attempted, ["a-key-1", "a-key-2", "b-key-1"])
        self.assertEqual(result["provider_id"], "prov-b")
        self.assertTrue(any(item.get("skipped") for item in result["attempts"]))

    def test_auth_failure_fails_fast_without_distinct_credential(self):
        chain = [
            _chain_entry("prov-a", "k1", "m1", "a.test", "a-key-1"),
            _chain_entry("prov-a", "k1", "m2", "a.test", "a-key-1"),
        ]
        results = [_res(False, stderr="401 Unauthorized", exit_code=1)]

        result, attempted = self._run(chain, results)

        self.assertFalse(result["ok"])
        self.assertEqual(attempted, ["a-key-1"])


if __name__ == "__main__":
    import unittest
    unittest.main()
