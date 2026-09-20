# -*- coding: utf-8 -*-
"""Token 成本优化 T2/T3 测试：预算闸（T2.1）/ 精确缓存（T2.2）/ FrugalGPT 级联（T3.1）。
设计稿：docs/migration/07-token-cost.md。
"""
from __future__ import annotations

import json
import os
import time
from base import BaseTest


class BudgetBase(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import settings_schema as ss
        ss.init(data_dir=str(self.data_dir))
        ss.register_default_namespaces()
        from app.core.token_meter import token_meter
        token_meter.reset("r-bud")


class TestBudgetNamespace(BudgetBase):

    def test_default_unlimited(self):
        from app.core.settings_schema import get as ss_get
        self.assertEqual(int(ss_get("budget", "max_tokens_per_run") or 0), 0)

    def test_set_budget(self):
        from app.core import settings_schema as ss
        rev = ss.revision("budget")
        ss.mutate("budget", [{"op": "set", "path": "max_tokens_per_run", "value": 5000}],
                  expected_revision=rev)
        self.assertEqual(ss.get("budget", "max_tokens_per_run"), 5000)

    def test_env_overrides_settings(self):
        """TUTTI_BUDGET_MAX_TOKENS 优先于 settings（运维快捷钳制）。"""
        from app.core import settings_schema as ss, pipeline
        ss.mutate("budget", [{"op": "set", "path": "max_tokens_per_run", "value": 5000}])
        old = os.environ.get("TUTTI_BUDGET_MAX_TOKENS")
        try:
            os.environ["TUTTI_BUDGET_MAX_TOKENS"] = "123"
            self.assertEqual(pipeline._budget_max_tokens(), 123)
            os.environ["TUTTI_BUDGET_MAX_TOKENS"] = "0"
            self.assertEqual(pipeline._budget_max_tokens(), 0)
        finally:
            if old is None:
                os.environ.pop("TUTTI_BUDGET_MAX_TOKENS", None)
            else:
                os.environ["TUTTI_BUDGET_MAX_TOKENS"] = old


class TestBudgetGate(BudgetBase):
    """_spawn_step 的预算闸：超额 → ENV_BLOCK；未超额 → 正常调用。"""

    def _cap(self, n):
        from app.core import settings_schema as ss
        ss.mutate("budget", [{"op": "set", "path": "max_tokens_per_run", "value": n}])

    def test_over_budget_blocks_without_spawn(self):
        from app.core.token_meter import token_meter
        token_meter.accumulate("r-bud", {"input": 60000})
        self._cap(50000)
        from app.core import pipeline
        res = pipeline._spawn_step(
            "r-bud", "draft", {"kind": "generic", "mode": "real"}, "p",
            None, True, None, 10, None, {"n": 1}, None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "ENV_BLOCK")
        self.assertIn("预算", res["error"])

    def test_under_budget_spawns(self):
        from app.core.token_meter import token_meter
        token_meter.accumulate("r-bud", {"input": 10})
        self._cap(5000000)
        from app.core import pipeline, runner
        calls = []
        orig = runner.run_agent
        runner.run_agent = lambda agent, p, **kw: calls.append(p) or {
            "ok": True, "text": "done", "json": None, "cost_usd": 0.0,
            "tokens": 1, "usage": None, "error": "", "error_code": "",
            "sid": "", "raw": {"exit_code": 0}, "kind": "generic", "model": None}
        try:
            res = pipeline._spawn_step(
                "r-bud", "draft", {"kind": "generic", "mode": "real"}, "p",
                None, True, None, 10, None, {"n": 1}, None)
        finally:
            runner.run_agent = orig
        self.assertTrue(res["ok"])
        self.assertEqual(len(calls), 1)

    def test_zero_cap_means_unlimited(self):
        self._cap(0)
        from app.core import pipeline, runner
        calls = []
        orig = runner.run_agent
        runner.run_agent = lambda agent, p, **kw: calls.append(p) or {
            "ok": True, "text": "done", "json": None, "cost_usd": 0.0,
            "tokens": 1, "usage": None, "error": "", "error_code": "",
            "sid": "", "raw": {"exit_code": 0}, "kind": "generic", "model": None}
        try:
            res = pipeline._spawn_step(
                "r-bud", "draft", {"kind": "generic", "mode": "real"}, "p",
                None, True, None, 10, None, {"n": 1}, None)
        finally:
            runner.run_agent = orig
        self.assertTrue(res["ok"])
        self.assertEqual(len(calls), 1)

    def test_real_first_step_usage_blocks_second_step_on_direct_paths(self):
        """默认 compaction=false 与 resume 路径都必须累计真实 runner usage。"""
        from app.core import pipeline, runner
        from app.core.token_meter import token_meter

        self._cap(50)
        original_run = runner.run_agent
        original_compaction = pipeline._compaction_enabled
        calls = []

        def fake_run(agent, prompt, **kwargs):
            calls.append((prompt, kwargs.get("resume")))
            return {
                "ok": True, "text": "done", "json": None, "cost_usd": 0.0,
                "tokens": 60, "usage": {"input": 40, "output": 20},
                "error": "", "error_code": "", "sid": "s1",
                "raw": {"exit_code": 0}, "kind": "generic", "model": "m1",
            }

        try:
            runner.run_agent = fake_run
            pipeline._compaction_enabled = lambda: False
            for resume in (None, "existing-session"):
                run_id = "r-direct-%s" % ("resume" if resume else "fresh")
                token_meter.reset(run_id)
                first = pipeline._spawn_step(
                    run_id, "draft", {"kind": "generic", "mode": "real"}, "p1",
                    None, True, None, 10, resume, {"n": 1}, None)
                second = pipeline._spawn_step(
                    run_id, "review", {"kind": "generic", "mode": "real"}, "p2",
                    None, True, None, 10, resume, {"n": 2}, None)
                self.assertTrue(first["ok"])
                self.assertEqual(token_meter.used(run_id), 60)
                self.assertFalse(second["ok"])
                self.assertEqual(second["error_code"], "ENV_BLOCK")
        finally:
            runner.run_agent = original_run
            pipeline._compaction_enabled = original_compaction

        self.assertEqual([item[0] for item in calls], ["p1", "p1"])

    def test_compaction_path_records_each_real_call_once(self):
        """压缩执行器内的首次调用和重试各记一次，外层不得重复累计。"""
        from app.core import pipeline, runner, step_runner
        from app.core.token_meter import token_meter

        original_run = runner.run_agent
        original_compaction = pipeline._compaction_enabled
        original_execute = step_runner.execute_step

        def fake_run(agent, prompt, **kwargs):
            return {
                "ok": True, "text": "done", "json": None, "cost_usd": 0.0,
                "tokens": 60, "usage": {"input": 40, "output": 20},
                "error": "", "error_code": "", "sid": "s1",
                "raw": {"exit_code": 0}, "kind": "generic", "model": "m1",
            }

        try:
            runner.run_agent = fake_run
            pipeline._compaction_enabled = lambda: True
            for retries, expected in ((0, 60), (1, 120)):
                def fake_execute(session, run_agent_fn, prompt, **kwargs):
                    result = run_agent_fn(prompt)
                    for _ in range(retries):
                        result = run_agent_fn(prompt)
                    return result, bool(retries)

                step_runner.execute_step = fake_execute
                run_id = "r-compaction-%d" % retries
                token_meter.reset(run_id)
                result = pipeline._spawn_step(
                    run_id, "draft", {"kind": "generic", "mode": "real"}, "p1",
                    None, True, None, 10, None, {"n": 1}, None)
                self.assertTrue(result["ok"])
                self.assertEqual(token_meter.used(run_id), expected)
        finally:
            runner.run_agent = original_run
            pipeline._compaction_enabled = original_compaction
            step_runner.execute_step = original_execute


class TestChatExactCache(BudgetBase):
    """modelhub.chat 精确缓存：cache_ttl>0 命中不发网；创作调用不开缓存。"""

    def setUp(self):
        super().setUp()
        from app.core import modelhub, paths
        modelhub._FILE = paths.DATA_DIR / "models.json"
        modelhub._FILE.parent.mkdir(parents=True, exist_ok=True)
        modelhub._FILE.write_text(json.dumps({
            "providers": [{"id": "prov-test", "name": "T", "protocol": "openai",
                           "base_url": "http://127.0.0.1:1", "api_key": "k",
                           "enabled": True}],
            "bindings": {},
        }, ensure_ascii=False), encoding="utf-8")
        self._calls = []
        self._orig_post = modelhub._post_json_http

        def fake_post(url, headers, body, allow_private, timeout=20):
            self._calls.append(url)
            return 200, {"choices": [{"message": {"content": "收到"}}]}, None
        modelhub._post_json_http = fake_post

    def tearDown(self):
        from app.core import modelhub
        modelhub._post_json_http = self._orig_post
        super().tearDown()

    def test_cache_hit_no_second_network(self):
        from app.core import modelhub
        r1 = modelhub.chat("prov-test", "m1", "请只回复两个字：收到",
                           max_tokens=64, cache_ttl=3600)
        self.assertTrue(r1["ok"])
        r2 = modelhub.chat("prov-test", "m1", "请只回复两个字：收到",
                           max_tokens=64, cache_ttl=3600)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["text"], "收到")
        self.assertEqual(len(self._calls), 1, "第二次应命中缓存，不再发网")

    def test_different_prompt_misses(self):
        from app.core import modelhub
        modelhub.chat("prov-test", "m1", "A", max_tokens=64, cache_ttl=3600)
        modelhub.chat("prov-test", "m1", "B", max_tokens=64, cache_ttl=3600)
        self.assertEqual(len(self._calls), 2)

    def test_no_cache_ttl_always_network(self):
        from app.core import modelhub
        modelhub.chat("prov-test", "m1", "同prompt", max_tokens=64)
        modelhub.chat("prov-test", "m1", "同prompt", max_tokens=64)
        self.assertEqual(len(self._calls), 2, "未开 cache_ttl 不缓存（创作调用安全默认）")

    def test_ttl_expiry(self):
        from app.core import modelhub
        modelhub.chat("prov-test", "m1", "p", max_tokens=64, cache_ttl=3600)
        # 回写 mtime 到 2 小时前 → 过期
        from app.core.modelhub import _chat_cache_path
        cp = _chat_cache_path("prov-test", "m1", "p", 64)
        old = time.time() - 7200
        os.utime(cp, (old, old))
        modelhub.chat("prov-test", "m1", "p", max_tokens=64, cache_ttl=3600)
        self.assertEqual(len(self._calls), 2, "过期缓存不应命中")


class TestUsageCacheRate(BaseTest):
    """/api/usage 聚合暴露 cache_rate（§07 验收指标的观测面）。"""

    def _seed(self):
        from app.core import usage
        usage.record(source="pipeline", run_id="r1", role="draft", model="m-a",
                     ok=True, usage={"input": 800, "output": 100, "cached": 0})
        usage.record(source="pipeline", run_id="r1", role="draft", model="m-a",
                     ok=True, usage={"input": 200, "output": 100, "cached": 800})

    def test_totals_and_day_cache_rate(self):
        self._seed()
        from app.core import usage
        s = usage.summary(days=7)
        # 总命中率 = 800 / (1000 + 800) ≈ 44.4%
        self.assertEqual(s["totals"]["cached"], 800)
        self.assertAlmostEqual(s["totals"]["cache_rate"], 44.4, places=1)
        day = [d for d in s["by_day"] if d["tokens"] > 0][0]
        self.assertAlmostEqual(day["cache_rate"], 44.4, places=1)
        self.assertEqual(day["cached"], 800)

    def test_by_model_cache_rate(self):
        self._seed()
        from app.core import usage
        s = usage.summary(days=7)
        rows = {r["key"]: r for r in s["by_model"]}
        self.assertAlmostEqual(rows["m-a"]["cache_rate"], 44.4, places=1)


class TestCascadeReorder(BaseTest):
    """T3.1 cascade_reorder：easy 任务链按 tier 升序稳定重排。"""

    PROVIDERS = [
        {"id": "p-std", "tier": "standard", "models": [{"name": "m-std"}]},
        {"id": "p-cheap", "tier": "budget", "models": [{"name": "m-cheap-1"},
                                                        {"name": "m-cheap-2"}]},
        {"id": "p-rich", "models": [{"name": "m-rich", "tier": "premium"}]},
        {"id": "p-none"},
    ]

    def _agent(self, ids):
        return {"call_chain": [{"provider_id": i, "model": "mm"} for i in ids]}

    def test_budget_first(self):
        from app.core.capability import cascade_reorder, make_tier_lookup
        tier_of = make_tier_lookup(self.PROVIDERS)
        agent = self._agent(["p-std", "p-rich", "p-cheap"])
        out = cascade_reorder(agent, tier_of)
        order = [e["provider_id"] for e in out["call_chain"]]
        self.assertEqual(order, ["p-cheap", "p-std", "p-rich"])

    def test_model_level_tier_wins(self):
        """同一供应商下按 models[].tier 区分（p-rich 的 m-rich 是 premium）。"""
        from app.core.capability import cascade_reorder, make_tier_lookup
        tier_of = make_tier_lookup(self.PROVIDERS)
        self.assertEqual(tier_of({"provider_id": "p-rich", "model": "m-rich"}), "premium")
        self.assertEqual(tier_of({"provider_id": "p-cheap", "model": "m-cheap-2"}), "budget")

    def test_missing_tier_falls_back_standard(self):
        from app.core.capability import cascade_reorder, make_tier_lookup
        tier_of = make_tier_lookup(self.PROVIDERS)
        self.assertIsNone(tier_of({"provider_id": "p-none", "model": "x"}))
        agent = self._agent(["p-none", "p-cheap"])
        out = cascade_reorder(agent, tier_of)
        self.assertEqual([e["provider_id"] for e in out["call_chain"]],
                         ["p-cheap", "p-none"])

    def test_stable_within_tier(self):
        from app.core.capability import cascade_reorder, make_tier_lookup
        tier_of = make_tier_lookup(self.PROVIDERS)
        agent = self._agent(["p-none", "p-std"])
        out = cascade_reorder(agent, tier_of)
        self.assertEqual([e["provider_id"] for e in out["call_chain"]],
                         ["p-none", "p-std"], "同档保持原序（稳定排序）")

    def test_short_chain_unchanged(self):
        from app.core.capability import cascade_reorder, make_tier_lookup
        tier_of = make_tier_lookup(self.PROVIDERS)
        a1 = {"call_chain": [{"provider_id": "p-rich", "model": "m-rich"}]}
        self.assertIs(cascade_reorder(a1, tier_of), a1)
        no_chain = {"ok": True}
        self.assertIs(cascade_reorder(no_chain, tier_of), no_chain)

    def test_unknown_provider_treated_standard(self):
        from app.core.capability import cascade_reorder, make_tier_lookup
        tier_of = make_tier_lookup(self.PROVIDERS)
        agent = self._agent(["p-rich", "p-ghost", "p-cheap"])
        out = cascade_reorder(agent, tier_of)
        order = [e["provider_id"] for e in out["call_chain"]]
        self.assertEqual(order[0], "p-cheap")
        self.assertIn("p-ghost", order[1:])
