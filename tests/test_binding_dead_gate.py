# -*- coding: utf-8 -*-
"""绑定链闸门回归（2026-09-18 语义收窄）：

· 从没配过链的 CLI：回落本机默认照跑（0.1.6 的「一律判失败」把用本地登录
  的普通用户全挡在门外——真实装机误伤案例，用户拍板回退）；
· 配过链但全死：本步判失败不静默降级（防静默烧本机默认供应商配额的本意保留），
  但只记在步骤错误/备注里，**不再产生全局健康告警胶囊**（「绑定链·xxx」
  红胶囊同批移除；老数据由 health.init 启动即清）。
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

    def _run(self):
        from app.core import store
        task = store.create_task({"type": "code", "title": "死链闸门",
                                  "goal": "验证闸门语义", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        return run["id"]

    @staticmethod
    def _fake_spawn():
        from app.core import pipeline
        orig = pipeline._spawn_step

        def fake(*a, **k):
            return {"ok": True, "text": "done", "json": None, "cost_usd": 0.0,
                    "tokens": 1, "usage": None, "error": "",
                    "raw": {"exit_code": 0}}
        pipeline._spawn_step = fake
        return orig


class TestBindingGate(HealthMixin):

    def test_unconfigured_agent_passes_gate(self):
        """从没配过链：不判死，照常进执行（回落 CLI 本机默认）。"""
        from app.core import pipeline, store
        run_id = self._run()
        agent = {"id": "claude-code", "label": "Claude Code",
                 "kind": "claude", "mode": "real"}   # 无 binding_configured
        orig = self._fake_spawn()
        try:
            res = pipeline._run_step(run_id, "implement", agent, "写点东西",
                                     str(self.workdir), readonly=True, ev=None)
        finally:
            pipeline._spawn_step = orig
        self.assertTrue(res["ok"], "没配链的 CLI 必须照跑，不得判死")
        step = (store.get_run(run_id).get("steps") or [])[0]
        self.assertNotIn("绑定链", step.get("note") or "")

    def test_configured_dead_fails_step_without_pill(self):
        """配过链但全死：本步判失败（保配额），但不产生任何健康告警胶囊。"""
        from app.core import health, pipeline, store
        from app.core.error_codes import ErrorCode
        run_id = self._run()
        agent = {"id": "claude-code", "label": "Claude Code",
                 "kind": "claude", "mode": "real",
                 "binding_configured": True}          # 配过链，但 call_chain 空
        res = pipeline._run_step(run_id, "implement", agent, "写点东西",
                                 str(self.workdir), readonly=True, ev=None)
        self.assertFalse(res["ok"])
        self.assertIn("绑定链", res["error"])
        self.assertIn("anthropic", res["error"])   # 协议提示：claude 只认 anthropic
        self.assertEqual(res["error_code"], ErrorCode.ENV_BLOCK)
        step = (store.get_run(run_id).get("steps") or [])[0]
        self.assertEqual(step["status"], "failed")
        self.assertIn("绑定链", step.get("note") or "")
        # 关键回归：不再有「绑定链·」全局红胶囊
        snap = health.snapshot()
        self.assertFalse(snap["any_alerting"])
        self.assertEqual([p for p in snap["providers"] if str(
            p.get("provider", "")).startswith("绑定链·")], [])

    def test_mock_agent_unaffected(self):
        from app.core import health, pipeline
        run_id = self._run()
        agent = {"id": "mock-a", "label": "MockA", "kind": "mock", "mode": "mock"}
        res = pipeline._run_step(run_id, "implement", agent, "写点东西",
                                 str(self.workdir), readonly=True, ev=None)
        self.assertTrue(res["ok"])
        self.assertEqual(health.snapshot()["providers"], [])


class TestDeadChainSubstitute(HealthMixin):
    """死链补位（2026-09-18 重写任务 3/3 续跑全灭案）：

    原定 CLI 配了链但解析为空（chat-only 供应商挂在只讲 responses 的 codex
    链上必然如此）时，从同池找「配置过且链还活着」的真实 CLI 顶上，整步
    不必判死；全池无活链才维持 ENV_BLOCK——闸门「绝不静默偷跑本机默认」
    的语义不变，补位用的仍是用户显式配好的链。
    """

    _LIVE = {
        "providers": [{"id": "p1", "name": "P1", "protocol": "openai",
                       "base_url": "https://x.example/v1", "api_key": "k",
                       "enabled": True, "models": [{"name": "m1", "enabled": True}]}],
        "bindings": {"kimi-code": {"provider_id": "p1", "model": "m1",
                                   "chain": [{"provider_id": "p1", "model": "m1"}],
                                   "models": ["m1"]}},
    }

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self._orig_pool = pipeline._CURRENT_AGENTS
        pipeline._CURRENT_AGENTS = []

    def tearDown(self):
        from app.core import pipeline
        pipeline._CURRENT_AGENTS = self._orig_pool
        super().tearDown()

    @staticmethod
    def _capturing_spawn():
        from app.core import pipeline
        orig = pipeline._spawn_step
        seen = {}

        def fake(*a, **k):
            seen["agent"] = k.get("agent")
            return {"ok": True, "text": "done", "json": None, "cost_usd": 0.0,
                    "tokens": 1, "usage": None, "error": "",
                    "raw": {"exit_code": 0}}
        pipeline._spawn_step = fake
        return orig, seen

    def test_dead_chain_substitutes_live_agent(self):
        """codex 链全死 + 同池 kimi 有活链 → 补位跑成，步骤备注写明补位。"""
        from app.core import modelhub as MH, pipeline, store
        MH._FILE.write_text(json.dumps(self._LIVE), encoding="utf-8")
        pipeline._CURRENT_AGENTS = [
            {"id": "codex-cli", "label": "Codex CLI", "kind": "codex", "mode": "real"},
            {"id": "kimi-code", "label": "Kimi Code", "kind": "kimi", "mode": "real"},
        ]
        run_id = self._run()
        agent = {"id": "codex-cli", "label": "Codex CLI", "kind": "codex",
                 "mode": "real", "binding_configured": True}   # 链解析为空
        orig, seen = self._capturing_spawn()
        try:
            res = pipeline._run_step(run_id, "draft-c1", agent, "写第 3 章",
                                     str(self.workdir), readonly=True, ev=None)
        finally:
            pipeline._spawn_step = orig
        self.assertTrue(res["ok"], "同池有活链 CLI 时死链步骤应补位而非判死")
        self.assertEqual((seen.get("agent") or {}).get("id"), "kimi-code",
                         "实际执行应换到活链的 kimi")
        step = (store.get_run(run_id).get("steps") or [])[0]
        self.assertEqual(step["agent"], "kimi-code")
        self.assertIn("补位", step.get("note") or "")
        self.assertIn("Codex CLI", step.get("note") or "")

    def test_dead_chain_without_live_substitute_still_fails(self):
        """全池没有活链（其它 CLI 没配过绑定）→ 维持 ENV_BLOCK 判死。"""
        from app.core import pipeline
        from app.core.error_codes import ErrorCode
        pipeline._CURRENT_AGENTS = [
            {"id": "codex-cli", "label": "Codex CLI", "kind": "codex", "mode": "real"},
            {"id": "kimi-code", "label": "Kimi Code", "kind": "kimi", "mode": "real"},
        ]
        run_id = self._run()
        agent = {"id": "codex-cli", "label": "Codex CLI", "kind": "codex",
                 "mode": "real", "binding_configured": True}
        res = pipeline._run_step(run_id, "draft-c1", agent, "写第 3 章",
                                 str(self.workdir), readonly=True, ev=None)
        self.assertFalse(res["ok"], "全池无活链时不得补位，维持死链判失败")
        self.assertEqual(res["error_code"], ErrorCode.ENV_BLOCK)

    def test_resume_pins_agent_no_substitute(self):
        """续会话钉在原 CLI 上：会话跟人走，补位没有意义，维持判死。"""
        from app.core import modelhub as MH, pipeline
        from app.core.error_codes import ErrorCode
        MH._FILE.write_text(json.dumps(self._LIVE), encoding="utf-8")
        pipeline._CURRENT_AGENTS = [
            {"id": "codex-cli", "label": "Codex CLI", "kind": "codex", "mode": "real"},
            {"id": "kimi-code", "label": "Kimi Code", "kind": "kimi", "mode": "real"},
        ]
        run_id = self._run()
        agent = {"id": "codex-cli", "label": "Codex CLI", "kind": "codex",
                 "mode": "real", "binding_configured": True}
        res = pipeline._run_step(run_id, "chat", agent, "接着聊",
                                 str(self.workdir), readonly=True, ev=None,
                                 resume={"agent": "codex-cli", "session": "sess-1"})
        self.assertFalse(res["ok"], "有 resume 会话时不补位")
        self.assertEqual(res["error_code"], ErrorCode.ENV_BLOCK)


class TestLegacyPillPurge(HealthMixin):
    """0.1.6 持久化的「绑定链·」静态条目：init 时必须清掉，别让旧状态常驻。"""

    def test_init_purges_legacy_static_entries(self):
        from app.core import health, modelhub as MH
        # P1 在 models.json 里真实存在：重启清理（幽灵条目随 init 清掉）只清
        # 配置里已不存在的供应商，真实供应商的健康状态要原样保留
        MH._FILE.write_text(json.dumps({
            "providers": [{"id": "p1", "name": "P1", "enabled": True,
                           "models": [{"name": "m1", "enabled": True}]}],
            "bindings": {},
        }), encoding="utf-8")
        f = health._FILE if getattr(health, "_FILE", None) else None
        data = {"providers": {
            "绑定链·codex-cli": {"name": "绑定链·codex-cli", "provider_id": "",
                                 "model": "", "status": "down", "static": True,
                                 "consecutive_failures": 0, "first_fail_at": 0,
                                 "last_fail_at": 0, "last_ok_at": 0,
                                 "last_error": "x", "alerted": True,
                                 "silenced": False, "silence_until": 0,
                                 "probe_next_at": 0, "probe_backoff_idx": 0,
                                 "recovered_at": 0},
            "P1": {"name": "P1", "provider_id": "p1", "model": "m1",
                   "status": "failing", "static": False,
                   "consecutive_failures": 2, "first_fail_at": 0,
                   "last_fail_at": 0, "last_ok_at": 0, "last_error": "y",
                   "alerted": True, "silenced": False, "silence_until": 0,
                   "probe_next_at": 0, "probe_backoff_idx": 0, "recovered_at": 0},
        }}
        import json as _json
        f.write_text(_json.dumps(data), encoding="utf-8")
        health.init(data_dir=str(self.data_dir))   # 重新 init 触发清理
        names = list(health._PROVIDERS.keys())
        self.assertNotIn("绑定链·codex-cli", names, "老静态胶囊条目必须被清掉")
        self.assertIn("P1", names, "真实供应商的健康状态不受影响")


class TestNoPillOnOps(HealthMixin):
    """厂商/模型启停只影响解析结果，不再产生/解除静态告警胶囊。"""

    _MODELS = {
        "providers": [{"id": "p1", "name": "P1", "protocol": "openai",
                       "base_url": "https://x.example/v1", "api_key": "k",
                       "enabled": True, "wire_api": "responses",
                       "models": [{"name": "m1", "enabled": True}]}],
        "bindings": {"opencode": {"provider_id": "p1", "model": "m1",
                                  "chain": [{"provider_id": "p1", "model": "m1"}],
                                  "models": ["m1"]}},
    }

    def test_ops_never_create_pills(self):
        from app.core import health, modelhub as MH
        MH._FILE.write_text(json.dumps(self._MODELS), encoding="utf-8")
        n, err = MH.providers_op(["p1"], "disable")
        self.assertEqual((n, err), (1, ""))
        snap = health.snapshot()
        self.assertFalse(snap["any_alerting"], "停用厂商不得再点亮绑定链胶囊")
        self.assertEqual([p for p in snap["providers"] if str(
            p.get("provider", "")).startswith("绑定链·")], [])
        n, _ = MH.providers_op(["p1"], "enable")
        self.assertEqual(n, 1)
        self.assertFalse(health.snapshot()["any_alerting"])

    def test_bind_agent_marks_configured(self):
        """bind_agent 必须把「配没配过链」带给执行层（闸门的判断依据）。"""
        from app.core import modelhub as MH
        MH._FILE.write_text(json.dumps(self._MODELS), encoding="utf-8")
        a = {"id": "opencode", "kind": "opencode", "mode": "real"}
        out = MH.bind_agent(dict(a))
        self.assertTrue(out.get("binding_configured"), "配过链 → True")
        # 厂商停用 → 链解析为空，但 configured 标记仍在（闸门据此判死）
        MH.providers_op(["p1"], "disable")
        out2 = MH.bind_agent(dict(a))
        self.assertTrue(out2.get("binding_configured"))
        self.assertFalse(out2.get("call_chain"))
        # 没配过链的 CLI → False（闸门放行，回落本机默认）
        out3 = MH.bind_agent({"id": "qwencode", "kind": "generic", "mode": "real"})
        self.assertFalse(out3.get("binding_configured"))
