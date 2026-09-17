# -*- coding: utf-8 -*-
"""绑定链死链闸门回归（2026-09-17）：绑定解析为空时不再静默回落 CLI 本机默认，
改为「health 静态告警 + 本步直接判失败」，绑定恢复自动解除告警。

背景：claude-code/codex-cli 的绑定链还指向已停用厂商时，旧语义照跑 CLI 本机
默认配置——2026-09-16 实测会静默烧本机默认供应商的配额。宁可失败不静默降级；
auto 流程实现步的既有换将负责接手健康 CLI。
"""
from __future__ import annotations

import json

from base import BaseTest


class HealthMixin(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import health
        health.init(data_dir=str(self.data_dir))
        with health._LOCK:
            health._PROVIDERS.clear()

    def tearDown(self):
        from app.core import health
        with health._LOCK:
            health._PROVIDERS.clear()
        super().tearDown()


class TestBindingDeadGate(HealthMixin):

    def _run(self):
        from app.core import store
        task = store.create_task({"type": "code", "title": "死链闸门",
                                  "goal": "验证死链判失败", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        return run["id"]

    def test_real_agent_without_chain_fails_step(self):
        from app.core import health, pipeline, store
        from app.core.error_codes import ErrorCode
        run_id = self._run()
        agent = {"id": "claude-code", "label": "Claude Code",
                 "kind": "claude", "mode": "real"}
        res = pipeline._run_step(run_id, "implement", agent, "写点东西",
                                 str(self.workdir), readonly=True, ev=None)
        self.assertFalse(res["ok"])
        self.assertIn("绑定链", res["error"])
        self.assertIn("anthropic", res["error"])   # 协议提示：claude 只认 anthropic
        self.assertEqual(res["error_code"], ErrorCode.ENV_BLOCK)
        # 步骤落库为 failed，备注带 ⚠ 说明
        step = (store.get_run(run_id).get("steps") or [])[0]
        self.assertEqual(step["status"], "failed")
        self.assertIn("绑定链", step.get("note") or "")
        # health 出现静态告警（UI 横幅数据源）
        snap = health.snapshot()
        self.assertTrue(snap["any_alerting"])
        self.assertEqual(snap["alerts"][0]["provider"], "绑定链·claude-code")

    def test_binding_ok_clears_stale_alert(self):
        from app.core import health, pipeline
        run_id = self._run()
        health.report_binding_dead("opencode", "历史死链")
        agent = {"id": "opencode", "label": "OpenCode", "kind": "opencode",
                 "mode": "real",
                 "call_chain": [{"model": "any-model", "env": {}, "provider": None}]}
        orig = pipeline._spawn_step

        def fake_spawn(*a, **k):
            return {"ok": True, "text": "done", "json": None, "cost_usd": 0.0,
                    "tokens": 1, "usage": None, "error": "",
                    "raw": {"exit_code": 0}}
        pipeline._spawn_step = fake_spawn
        try:
            res = pipeline._run_step(run_id, "implement", agent, "写点东西",
                                     str(self.workdir), readonly=True, ev=None)
        finally:
            pipeline._spawn_step = orig
        self.assertTrue(res["ok"])
        snap = health.snapshot()
        self.assertFalse(snap["any_alerting"])
        self.assertEqual([p["status"] for p in snap["providers"] if p["static"]],
                         ["recovered"])

    def test_mock_agent_unaffected(self):
        from app.core import health, pipeline
        run_id = self._run()
        agent = {"id": "mock-a", "label": "MockA", "kind": "mock", "mode": "mock"}
        res = pipeline._run_step(run_id, "implement", agent, "写点东西",
                                 str(self.workdir), readonly=True, ev=None)
        self.assertTrue(res["ok"])
        self.assertEqual(health.snapshot()["providers"], [])


class TestAlertSyncOnOps(HealthMixin):
    """厂商/模型启停后即刻重评估静态告警：恢复当场解除，新死当场亮起。"""

    _MODELS = {
        "providers": [{"id": "p1", "name": "P1", "protocol": "openai",
                       "base_url": "https://x.example/v1", "api_key": "k",
                       "enabled": True, "wire_api": "responses",
                       "models": [{"name": "m1", "enabled": True}]}],
        "bindings": {"opencode": {"provider_id": "p1", "model": "m1",
                                  "chain": [{"provider_id": "p1", "model": "m1"}],
                                  "models": ["m1"]}},
    }

    def _seed(self):
        from app.core import modelhub as MH
        MH._FILE.write_text(json.dumps(self._MODELS), encoding="utf-8")

    def test_provider_disable_raises_reenable_clears(self):
        from app.core import health, modelhub as MH
        self._seed()
        MH.sync_binding_alerts()
        self.assertFalse(health.snapshot()["any_alerting"])   # 链健康：无告警
        n, err = MH.providers_op(["p1"], "disable")
        self.assertEqual((n, err), (1, ""))
        self.assertTrue(health.snapshot()["any_alerting"], "停用厂商必须当场告警")
        self.assertIn("已停用", health.snapshot()["alerts"][0]["last_error"])
        n, _ = MH.providers_op(["p1"], "enable")
        self.assertEqual(n, 1)
        self.assertFalse(health.snapshot()["any_alerting"], "重新启用必须当场解除告警")

    def test_model_disable_raises_reenable_clears(self):
        from app.core import health, modelhub as MH
        self._seed()
        n, err = MH.model_ops("p1", ["m1"], "disable")
        self.assertEqual((n, err), (1, ""))
        self.assertTrue(health.snapshot()["any_alerting"], "停用链上模型必须当场告警")
        n, _ = MH.model_ops("p1", ["m1"], "enable")
        self.assertEqual(n, 1)
        self.assertFalse(health.snapshot()["any_alerting"])

    def test_unbound_cli_never_alerts(self):
        from app.core import health, modelhub as MH
        models = json.loads(json.dumps(self._MODELS))
        models["bindings"]["qwencode"] = {"models": []}   # 没配过链
        MH._FILE.write_text(json.dumps(models), encoding="utf-8")
        MH.providers_op(["p1"], "disable")
        names = [p["provider"] for p in health.snapshot()["providers"]]
        self.assertIn("绑定链·opencode", names)
        self.assertNotIn("绑定链·qwencode", names, "没配链的 CLI 不归静态告警管")
