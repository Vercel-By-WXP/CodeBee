# -*- coding: utf-8 -*-
"""CLI/编排者路径的「取消不进惩罚账」守卫（执行标准红线回归）。

docs/execution-standard.md 统一执行算法第 3 条：取消立即终止，且取消产生的杀
进程、断管道和尾部错误不得进入 KEY 冷却或健康惩罚。此前只有内置直连路径按调用
顺序免疫，CLI 侧三本惩罚账都只判 `ok`：

  - `runner._report_key` → `modelhub.note_key_error`：取消错误串形如
    「取消；stderr/stdout: <被杀进程尾部>」，尾部含 429/欠费/无效 KEY 字样时
    整串子串匹配命中，健康 KEY 背上 30 分钟冷却（实测）；
  - `pipeline._record_usage` / `planner._log_usage` → `health.report_failure`：
    一次用户取消被记成供应商失败，并顺带 `evaluation.invalidate_provider`
    作废 24h 保质期内已 `passed` 的评测结论（实测 fresh=True → stale）。

判定一律走 `runner.attempt_cancelled` 的结构化标记，不按错误文案匹配。同批
断言反向半边：真实失败必须照常进账，避免豁免被做成万能挡板。
"""
from __future__ import annotations

import time

from base import BaseTest


def _cancelled_res(error="取消；stderr/stdout: HTTP/1.1 429 Too Many Requests"):
    """runner 在取消路径上产出的真实形状。"""
    return {"ok": False, "error": error, "model": "glm-5.3",
            "provider_id": "prov-x",
            "provider": {"id": "prov-x", "name": "cavoti"},
            "error_code": "CANCELLED",
            "raw": {"cancelled": True, "duration": 1.0}}


class CancelPenaltyBase(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import evaluation, health, usage
        health.init(data_dir=str(self.data_dir), start_probe=False)
        evaluation.reset_for_tests()
        evaluation.record("model", "prov-x", "glm-5.3",
                          {"ok": True, "status": "passed"})
        # `_record_usage` / `_log_usage` 整块包在 `except Exception: pass` 里，
        # 落账口一旦抛错（例如调用方与被调方的关键字不同步），健康账与评测作废
        # 根本走不到，"取消不进惩罚账"会以假绿通过。这里把落账口换成记录器：
        # 既让断言真的走到闸门，也用 self.ledger 断言"这一步确实入了账"。
        self._orig_record = usage.record
        self.ledger = []
        usage.record = lambda **kw: self.ledger.append(kw)
        self.addCleanup(setattr, usage, "record", self._orig_record)

    def assertLedgered(self):
        self.assertTrue(self.ledger,
                        "这次调用没有过落账口，惩罚账断言是假绿")

    def _eval_entry(self):
        from app.core import evaluation
        return evaluation.get("model", "prov-x", "glm-5.3")

    def _health_rows(self):
        from app.core import health
        return [(p.get("provider"), p.get("consecutive_failures"), p.get("status"))
                for p in health.snapshot()["providers"]]


class TestAttemptCancelled(BaseTest):

    def test_structured_flags_only(self):
        from app.core import runner
        self.assertTrue(runner.attempt_cancelled(
            {"raw": {"cancelled": True}}))
        self.assertTrue(runner.attempt_cancelled({"cancelled": True}))
        self.assertTrue(runner.attempt_cancelled({"error_code": "CANCELLED"}))
        self.assertTrue(runner.attempt_cancelled(None) is False)
        # 文案匹配不算数：超时尾部写着 429 也仍是真实失败
        self.assertFalse(runner.attempt_cancelled(
            {"error": "超时；stderr/stdout: HTTP/1.1 429 Too Many Requests"}))

    def test_builtin_cancel_result_carries_flag(self):
        """内置直连的取消终态必须带结构化标记，供惩罚账判定。"""
        from app.core import builtin_agent, runner
        src = __import__("inspect").getsource(builtin_agent)
        self.assertIn("def _cancel_fail", src)
        self.assertIn('out["cancelled"] = True', src)
        self.assertTrue(runner.attempt_cancelled({"cancelled": True}))


class TestPipelineHealth(CancelPenaltyBase):

    def test_cancel_writes_no_health_or_evaluation_penalty(self):
        from app.core import pipeline
        agent = {"id": "opencode", "kind": "opencode", "label": "OC",
                 "provider": {"id": "prov-x", "name": "cavoti"}}
        self.assertTrue(self._eval_entry().get("fresh"))
        pipeline._record_usage("run-1", "implementer", agent,
                               _cancelled_res(), step=1)
        self.assertLedgered()
        self.assertEqual([], self._health_rows())
        entry = self._eval_entry()
        self.assertTrue(entry.get("fresh"),
                        "取消作废了 24h 内的 passed 结论: %s" % entry)

    def test_real_failure_still_penalizes(self):
        """反向半边：非取消的真实失败必须照常进健康与评测账。"""
        from app.core import pipeline
        res = _cancelled_res(error="HTTP/1.1 429 Too Many Requests")
        res["error_code"] = "RATE_LIMIT"
        res["raw"]["cancelled"] = False
        agent = {"id": "opencode", "kind": "opencode", "label": "OC",
                 "provider": {"id": "prov-x", "name": "cavoti"}}
        pipeline._record_usage("run-1", "implementer", agent, res, step=1)
        self.assertLedgered()
        self.assertEqual(1, self._health_rows()[0][1])
        self.assertFalse(self._eval_entry().get("fresh"))


class TestPlannerHealth(CancelPenaltyBase):

    def test_cancel_skips_health_but_real_failure_does_not(self):
        from app.core import planner
        planner._log_usage("plan", "orchestrator", {}, _cancelled_res(),
                           model="glm-5.3", provider="cavoti",
                           provider_id="prov-x")
        self.assertLedgered()
        self.assertEqual([], self._health_rows())
        res = _cancelled_res(error="服务端异常 503")
        res["error_code"] = "UPSTREAM_SERVER"
        res["raw"]["cancelled"] = False
        planner._log_usage("plan", "orchestrator", {}, res,
                           model="glm-5.3", provider="cavoti",
                           provider_id="prov-x")
        self.assertEqual(2, len(self.ledger))
        self.assertEqual(1, self._health_rows()[0][1])


class TestKeyCooldown(BaseTest):

    def setUp(self):
        super().setUp()
        import json
        from app.core import modelhub
        self.models_file = self.data_dir / "models.json"
        self.models_file.write_text(json.dumps({"providers": [{
            "id": "p1", "name": "P", "keys": [
                {"id": "k1", "key": "sk-abcdefgh123456", "enabled": True}]}]},
            ensure_ascii=False), encoding="utf-8")
        modelhub._FILE = self.models_file

    def _cooling(self):
        from app.core import modelhub
        prov = next(p for p in modelhub._load()["providers"] if p["id"] == "p1")
        key = modelhub._provider_keys(prov)[0]
        return bool(key.get("cool_until", 0) and
                    key["cool_until"] > time.time())

    def test_cancel_with_quota_tail_does_not_cool_key(self):
        """取消串尾部恰好带 429/欠费字样时，健康 KEY 不得背上冷却。"""
        from app.core import runner
        runner._report_key({"provider_id": "p1", "key_id": "k1"},
                           _cancelled_res())
        self.assertFalse(self._cooling())

    def test_real_quota_error_still_cools_key(self):
        """反向半边：真实欠费必须照常冷却，否则换将失去依据。"""
        from app.core import runner
        res = _cancelled_res(error="insufficient balance, please top up")
        res["error_code"] = "QUOTA"
        res["raw"]["cancelled"] = False
        runner._report_key({"provider_id": "p1", "key_id": "k1"}, res)
        self.assertTrue(self._cooling())
