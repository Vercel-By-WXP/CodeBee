# -*- coding: utf-8 -*-
"""链尾限流宽限重试测试（2026-09-22 四连败案）。

claude ENOTFOUND 换将 codex 后仍与同一上游撞 429：链上无第三条路时，
旧逻辑在链尾第一次失败就判死整步。现在链尾限流等待一个窗口原地重试一次；
欠费/取消不宽限；链中段瞬态仍照旧逐条降级。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import BaseTest


def _res(ok, stdout="", stderr="", exit_code=0):
    return {"ok": ok, "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": 0.01, "cancelled": False, "timed_out": False, "stalled": False}


_AGENT = {"id": "cli-x", "name": "X", "kind": "generic", "mode": "real",
          "command": "echo", "env": {}, "model": "m1", "model_fallbacks": []}


class TestRateLimitGrace(BaseTest):
    def _run(self, agent, results, cancel_event=None, log_path=None):
        from app.core import runner
        calls = []

        def fake_run_process(**kw):
            calls.append(kw)
            return results[min(len(calls) - 1, len(results) - 1)]

        with mock.patch.object(runner, "run_process", side_effect=fake_run_process), \
                mock.patch.object(runner, "RATE_LIMIT_GRACE_S", 0.3):
            out = runner.run_agent(agent, "hi", workdir=str(self.workdir),
                                   cancel_event=cancel_event, log_path=log_path)
        return out, calls

    def test_grace_retries_once_on_tail_429(self):
        """链尾撞 429：等一个宽限窗口原地重试，第二次成功 → 步成功。"""
        from app.core import runner
        log = self.tmp / "step.log"
        results = [_res(False, stderr="exceeded retry limit, last status: "
                                        "429 Too Many Requests", exit_code=1),
                   _res(True, stdout="ok-after-grace")]
        out, calls = self._run(dict(_AGENT), results, log_path=str(log))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["text"], "ok-after-grace")
        self.assertEqual(len(calls), 2)
        self.assertIn("[限流宽限]", log.read_text(encoding="utf-8"))

    def test_grace_exhausts_then_fails(self):
        """宽限只有一次：重试仍 429 → 照常判失败，不再无限等。"""
        from app.core import runner
        results = [_res(False, stderr="429 Too Many Requests", exit_code=1)]
        out, calls = self._run(dict(_AGENT), results)
        self.assertFalse(out["ok"])
        self.assertIn("429", out["error"])
        self.assertEqual(len(calls), 2)  # 首发 + 1 次宽限

    def test_no_grace_on_quota(self):
        """欠费/配额耗尽等一个窗口救不回来：不宽限，一次即返。"""
        results = [_res(False, stderr="insufficient balance", exit_code=1)]
        out, calls = self._run(dict(_AGENT), results)
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 1)

    def test_no_grace_when_cancelled(self):
        """已取消的运行不做宽限等待，直接收场。"""
        import threading
        cancel = threading.Event()
        cancel.set()
        results = [_res(False, stderr="429 Too Many Requests", exit_code=1)]
        out, calls = self._run(dict(_AGENT), results, cancel_event=cancel)
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 1)

    def test_mid_chain_transient_still_walks_chain(self):
        """链中段瞬态（503）照旧降级下一条，不消耗宽限预算。"""
        agent = dict(_AGENT, call_chain=[
            {"model": "m1", "env": {}, "provider": {}, "from_chain": True,
             "own_cp": False, "codex_provider": None, "provider_id": "", "key_id": ""},
            {"model": "m2", "env": {}, "provider": {}, "from_chain": True,
             "own_cp": False, "codex_provider": None, "provider_id": "", "key_id": ""},
        ], model_fallbacks=[])
        results = [_res(False, stderr="503 Service Unavailable", exit_code=1),
                   _res(True, stdout="from-m2")]
        out, calls = self._run(agent, results)
        self.assertTrue(out["ok"])
        self.assertEqual(len(calls), 2)

    def test_rate_limited_matcher(self):
        from app.core import runner
        self.assertTrue(runner._rate_limited(
            "codex: exceeded retry limit, last status: 429 Too Many Requests"))
        self.assertTrue(runner._rate_limited("Error: rate limit exceeded"))
        self.assertFalse(runner._rate_limited("insufficient balance"))
        self.assertFalse(runner._rate_limited(""))


class TestLaunchPickProviderFallback(BaseTest):
    """launch_pick 忽略「只锁供应商没排链」的绑定 → 自愈永远跳过（a.test 残留案）。"""

    def test_provider_only_binding_picks_default_model(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        # 真实场景（prov-23）：models 列表已刷新、顶层 model 为空、绑定只有 provider_id
        modelhub.upsert_provider({"name": "厂商A", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1", "api_key": "sk-a"})
        pid = modelhub.providers()[0]["id"]
        data = modelhub._load()
        data["providers"][0]["models"] = [{"name": "glm-x", "enabled": True}]
        modelhub._save(data)
        modelhub.set_binding("claude-code", provider_id=pid)  # 无链无模型
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNotNone(pick, note)
        self.assertEqual(pick["model"], "glm-x")
        self.assertEqual(pick["provider"]["id"], pid)

    def test_provider_only_binding_uses_provider_model_field(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "厂商B", "protocol": "anthropic",
                                  "base_url": "https://b.test/v1", "api_key": "sk-b",
                                  "model": "glm-y"})
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("claude-code", provider_id=pid)
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNotNone(pick, note)
        self.assertEqual(pick["model"], "glm-y")


if __name__ == "__main__":
    unittest.main()
