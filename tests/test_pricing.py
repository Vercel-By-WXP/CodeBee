# -*- coding: utf-8 -*-
"""金额记账闭环（模型单价标定 + usage 自动计价 + 花费预警）单测。

口径：单价 = ¥/百万 tokens；手标（指定供应商 > 任意供应商）优先，回落
CCSwitch 导入价表；未标价 = 不记钱、预算不计入——宁缺毋滥。

跑法：python -m unittest discover -s tests -p "test_pricing.py" -v
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from base import BaseTest

from app.core import modelhub, settings as settings_mod, usage


def _seed_provider(tmp_provider_id="p1", pricing=None):
    """直接写 modelhub 数据：一个供应商两个模型 + 可选导入价表。"""
    data = {"providers": [{"id": tmp_provider_id, "name": "P1", "protocol": "openai",
                           "models": [{"name": "glm-x", "enabled": True, "priority": 1},
                                      {"name": "glm-y", "enabled": True, "priority": 2}]}]}
    if pricing:
        data["pricing"] = pricing
    modelhub._save(data)


class TestModelPrice(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import paths
        modelhub._FILE = paths.DATA_DIR / "models.json"
        modelhub._load.cache_clear() if hasattr(modelhub._load, "cache_clear") else None

    def test_unpriced_returns_none(self):
        _seed_provider()
        self.assertIsNone(modelhub.model_price("glm-x", provider="p1"))

    def test_manual_overrides_pricemap(self):
        _seed_provider(pricing={"glm-x": {"in": 2.0, "out": 8.0}})
        self.assertIsNone(modelhub.set_model_price("p1", "glm-x", 1.0, 4.0))
        pr = modelhub.model_price("glm-x", provider="p1")
        self.assertEqual(pr, {"in": 1.0, "out": 4.0})

    def test_pricemap_fallback(self):
        _seed_provider(pricing={"glm-y": {"in": 2.0, "out": 8.0}})
        self.assertEqual(modelhub.model_price("glm-y"), {"in": 2.0, "out": 8.0})

    def test_provider_specific_manual_wins(self):
        _seed_provider()
        data = modelhub._load()
        data["providers"].append({"id": "p2", "name": "P2", "protocol": "openai",
                                  "models": [{"name": "glm-x", "price_in": 9.0, "price_out": 9.0}]})
        modelhub._save(data)
        self.assertIsNone(modelhub.model_price("glm-x", provider="p1"))  # p1 未标
        self.assertEqual(modelhub.model_price("glm-x", provider="p2"),
                         {"in": 9.0, "out": 9.0})                        # p2 手标

    def test_set_price_validation(self):
        _seed_provider()
        self.assertIn("数字", modelhub.set_model_price("p1", "glm-x", "abc", "1"))
        self.assertIn("负", modelhub.set_model_price("p1", "glm-x", -1, 1))
        self.assertIn("不存在", modelhub.set_model_price("p1", "nope", 1, 1))
        # 清除标定：字段移除
        modelhub.set_model_price("p1", "glm-x", 2.0, 3.0)
        modelhub.set_model_price("p1", "glm-x", "", "")
        self.assertIsNone(modelhub.model_price("glm-x", provider="p1"))


class TestUsageAutoPricing(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import paths
        modelhub._FILE = paths.DATA_DIR / "models.json"
        _seed_provider(pricing={"glm-x": {"in": 1.0, "out": 3.0}})

    def test_record_priced_from_pricemap(self):
        usage.record(source="pipeline", run_id="r1", task_id="t1", task_type="code",
                     role="implement", step=1, agent="x", model="glm-x",
                     provider="p1", ok=True, cost_usd=0.0,
                     usage={"input": 1_000_000, "output": 500_000})
        rows = [r for r in usage._iter_records(1) if r.get("model") == "glm-x"]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["cost_usd"], 1.0 * 1 + 3.0 * 0.5, places=3)  # ¥2.5

    def test_explicit_cost_wins(self):
        usage.record(source="pipeline", run_id="r1", task_id="t1", task_type="code",
                     role="implement", step=1, agent="x", model="glm-x",
                     provider="p1", ok=True, cost_usd=0.77,
                     usage={"input": 1_000_000, "output": 0})
        rows = [r for r in usage._iter_records(1) if r.get("model") == "glm-x"]
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.77, places=3)

    def test_unpriced_model_records_zero(self):
        usage.record(source="pipeline", run_id="r1", task_id="t1", task_type="code",
                     role="implement", step=1, agent="x", model="unknown-m",
                     provider="p1", ok=True, cost_usd=0.0,
                     usage={"input": 1000, "output": 1000})
        rows = [r for r in usage._iter_records(1) if r.get("model") == "unknown-m"]
        self.assertEqual(rows[0]["cost_usd"], 0.0)

    def test_gate_message_uses_yuan(self):
        from app.core import pipeline
        with mock.patch.object(pipeline, "_budget_cost_caps", return_value=(5.0, 0.0)), \
             mock.patch("app.core.usage.cost_snapshot", return_value=(5.0, 9.0)):
            msg = pipeline._cost_gate_block()
        self.assertIn("¥5.00", msg)


class TestBudgetAlert(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import paths
        modelhub._FILE = paths.DATA_DIR / "models.json"
        _seed_provider(pricing={"glm-x": {"in": 1.0, "out": 0.0}})
        from app.core import settings_schema as ss
        ss.init(data_dir=str(paths.DATA_DIR))
        self._ss = ss

    def tearDown(self):
        with self._ss._LOCK:
            self._ss._NAMESPACES.clear()
            self._ss._VALUES.clear()
            self._ss._REVISIONS.clear()
        super().tearDown()

    def _caps(self, daily=10.0):
        self._ss.register_default_namespaces()
        self._ss.mutate("budget", [
            {"op": "set", "path": "daily_cost_yuan", "value": daily},
            {"op": "set", "path": "monthly_cost_yuan", "value": 0}])

    def test_alert_at_thresholds_deduped(self):
        from app.core import notify
        self._caps(daily=10.0)
        with mock.patch.object(notify, "push_text", return_value=True) as mpush:
            # 一笔 ¥6（60%，超 50% 阈值）——预警在后台线程，轮询等它落地
            usage.record(source="pipeline", run_id="r", task_id="t", task_type="code",
                         role="implement", step=1, model="glm-x", provider="p1",
                         ok=True, usage={"input": 6_000_000, "output": 0})
            for _ in range(100):
                if mpush.call_count:
                    break
                time.sleep(0.02)
            self.assertEqual(mpush.call_count, 1)
            text = mpush.call_args.args[0]
            self.assertIn("花费预警", text)
            self.assertIn("50%", text)
            # 再来一笔同日不变 → 不重复推
            usage.record(source="pipeline", run_id="r", task_id="t", task_type="code",
                         role="implement", step=2, model="glm-x", provider="p1",
                         ok=True, usage={"input": 100, "output": 0})
            time.sleep(0.3)
            self.assertEqual(mpush.call_count, 1)

    def test_no_alert_without_caps(self):
        from app.core import notify
        with mock.patch.object(notify, "push_text", return_value=True) as mpush:
            usage.record(source="pipeline", run_id="r", task_id="t", task_type="code",
                         role="implement", step=1, model="glm-x", provider="p1",
                         ok=True, usage={"input": 6_000_000, "output": 0})
            self.assertFalse(mpush.called)


if __name__ == "__main__":
    unittest.main()
