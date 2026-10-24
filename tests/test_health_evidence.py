# -*- coding: utf-8 -*-
"""健康归因证据分级回归（LayerTrace 借鉴，2026-10-22）。

纪律：观测级（runner 结构化码 / 探针实返回）才够格动手（熔断/首次短路）；
推断级（错误文本分类）只告警送检；未知级（探不了/异常）不刷时间戳。
注意分路：模型错（MODEL_UNAVAILABLE/FORBIDDEN）走 model_circuits 早退路径，
不进供应商计数；供应商级闸门用 QUOTA 文本（非模型码）验证。
"""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from app.core import health

_QUOTA_TEXT = "Error: insufficient balance"   # 分类器 _QUOTA_STRONG 稳定命中


class HealthEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        health.init(self.tmp.name, start_probe=False)

    def tearDown(self):
        health.init(self.tmp.name, start_probe=False)
        self.tmp.cleanup()

    def _row(self, provider):
        return health._PROVIDERS.get(provider) or {}

    def test_supplied_structured_code_is_observed_and_short_circuits(self):
        health.report_failure(
            "API provider", "HTTP 403 quota exhausted", model="m1",
            provider_id="p1", error_code="QUOTA")

        row = self._row("API provider")
        self.assertEqual(row["evidence"], health.EVIDENCE_OBSERVED)
        self.assertEqual(row["status"], "down")
        self.assertGreater(float(row["circuit_open_until"] or 0), 0)
        self.assertEqual(row["permanent_error_code"], "QUOTA")

    def test_text_only_permanent_code_is_inferred_without_short_circuit(self):
        """文本分类出的永久码（quota）是推断级：记失败、进探测，但不做
        首次观测短路（不熔断、不落永久码）——先送探针拿一手观测。"""
        health.report_failure(
            "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")

        row = self._row("CLI provider")
        self.assertEqual(row["evidence"], health.EVIDENCE_INFERRED)
        self.assertEqual(row["last_error_code"], "QUOTA")
        self.assertEqual(row["status"], "failing")
        self.assertEqual(float(row.get("circuit_open_until") or 0), 0)
        self.assertNotIn("permanent_error_code", row)
        self.assertEqual(row["consecutive_failures"], 1)

    def test_inferred_failures_reach_down_via_threshold(self):
        """推断级走连续次数阈值的正常程序：3 次连败才 down+有界冷却。"""
        for _ in range(3):
            health.report_failure(
                "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")

        row = self._row("CLI provider")
        self.assertEqual(row["status"], "down")
        self.assertGreater(float(row["circuit_open_until"] or 0), 0)
        self.assertNotIn("permanent_error_code", row)

    def test_text_model_error_takes_model_circuit_path_without_disposal(self):
        """「unknown model」文案匹配是推断级：模型熔断只记计数不落
        open_until（不剔除）；供应商级计数不被模型错污染。"""
        health.report_failure(
            "CLI provider", "Error: unknown model id fake-model",
            model="fake-model", provider_id="p2")

        row = self._row("CLI provider")
        self.assertEqual(row["evidence"], health.EVIDENCE_INFERRED)
        self.assertEqual(
            float(row["model_circuits"]["fake-model"].get("open_until") or 0), 0)
        self.assertFalse(health.is_model_down("CLI provider", "fake-model"))
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["status"], "ok")

    def test_model_circuit_opens_on_observed_evidence(self):
        health.report_failure(
            "API provider", "HTTP 404 model not found", model="ghost",
            provider_id="p3", error_code="MODEL_UNAVAILABLE")
        row = self._row("API provider")

        self.assertEqual(row["evidence"], health.EVIDENCE_OBSERVED)
        self.assertGreater(
            float(row["model_circuits"]["ghost"]["open_until"] or 0), 0)
        self.assertTrue(health.is_model_down("API provider", "ghost"))

    def test_empty_answer_opens_model_circuit_not_provider(self):
        """恒空答模型（2026-10-24 stepaudio 实案：HTTP 200、input 照扣、
        output 恒 0）首次观测 EMPTY 即模型级熔断 300s；不进供应商计数，
        同供应商其它健康模型不连坐；真实成功调用即清。"""
        health.report_failure(
            "幻城", "claude 只读步骤连续返回空输出",
            model="stepaudio-2.5-chat", provider_id="p9",
            error_code="EMPTY")
        row = self._row("幻城")

        self.assertTrue(
            health.is_model_down("幻城", "stepaudio-2.5-chat"))
        self.assertGreater(
            float(row["model_circuits"]["stepaudio-2.5-chat"]
                  .get("open_until") or 0), 0)
        # 模型级死错不污染供应商级计数
        self.assertEqual(row.get("consecutive_failures") or 0, 0)
        self.assertNotIn("down", (row.get("status") or ""))

        # 同供应商其它模型不受影响
        self.assertFalse(health.is_model_down("幻城", "glm-5.3"))

        # 真实成功调用清模型熔断
        health.report_success("幻城", model="stepaudio-2.5-chat")
        self.assertFalse(
            health.is_model_down("幻城", "stepaudio-2.5-chat"))

    def test_transient_timeout_benches_model_but_keeps_provider_counting(self):
        """中转站按模型分快慢（2026-10-24 拍板）：超时/网络类失败给该模型
        开熔断让兄弟模型顶位；同时双记账继续供应商级计数，供应商整体断流
        时供应商熔断照常开闸（维云 claude-opus-5-5 五连撞无记账实案）。"""
        health.report_failure(
            "维云模型开放平台",
            "超时；stderr/stdout: Reconnecting... waiting for network "
            "(Connection failed: error sending request)",
            model="claude-opus-5-5", provider_id="p0",
            error_code="TIMEOUT")
        row = self._row("维云模型开放平台")

        self.assertTrue(
            health.is_model_down("维云模型开放平台", "claude-opus-5-5"))
        # 双记账：供应商级计数也在涨
        self.assertEqual(row.get("consecutive_failures") or 0, 1)
        # 兄弟模型不受影响，绑定解析可换它顶位
        self.assertFalse(
            health.is_model_down("维云模型开放平台", "gpt-6.1-sol"))
        # 成功即清
        health.report_success("维云模型开放平台", model="claude-opus-5-5")
        self.assertFalse(
            health.is_model_down("维云模型开放平台", "claude-opus-5-5"))

    def test_alert_log_carries_evidence_label(self):
        with self.assertLogs("app.core.health", level="WARNING") as captured:
            for _ in range(3):
                health.report_failure(
                    "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")
        self.assertTrue(any("证据=推断" in line for line in captured.output))

    def test_probe_wire_failure_upgrades_evidence_and_disposes_down_row(self):
        for _ in range(3):
            health.report_failure(
                "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")
        st = health._PROVIDERS["CLI provider"]
        st["circuit_open_until"] = 0   # 摘掉阈值冷却，验证探针证实后补落

        health._record_probe_result("CLI provider", st, False, False, wire=True)

        self.assertEqual(st["evidence"], health.EVIDENCE_OBSERVED)
        self.assertGreater(float(st["circuit_open_until"] or 0), 0)

    def test_probe_wire_failure_on_failing_row_upgrades_but_does_not_dispose(self):
        health.report_failure(
            "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")
        st = health._PROVIDERS["CLI provider"]
        self.assertEqual(st["status"], "failing")

        health._record_probe_result("CLI provider", st, False, False, wire=True)

        self.assertEqual(st["evidence"], health.EVIDENCE_OBSERVED)
        self.assertEqual(float(st.get("circuit_open_until") or 0), 0)

    def test_probe_without_wire_keeps_unknown_discipline(self):
        health.report_failure(
            "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")
        st = health._PROVIDERS["CLI provider"]
        st["unprobeable"] = True
        st["evidence"] = health.EVIDENCE_INFERRED
        stale = st["last_fail_at"]

        health._record_probe_result("CLI provider", st, False, False, wire=False)

        self.assertEqual(st["last_fail_at"], stale)
        self.assertEqual(st["evidence"], health.EVIDENCE_INFERRED)

    def test_recovery_records_observed_evidence(self):
        health.report_failure(
            "CLI provider", _QUOTA_TEXT, model="m1", provider_id="p2")
        health.report_success("CLI provider", model="m1")

        self.assertEqual(self._row("CLI provider")["evidence"],
                         health.EVIDENCE_OBSERVED)

    def test_snapshot_exposes_evidence_fields(self):
        health.report_failure(
            "API provider", "HTTP 403 quota exhausted", model="m1",
            provider_id="p1", error_code="QUOTA")
        row = health.snapshot()["providers"][0]

        self.assertEqual(row["evidence"], "observed")
        self.assertEqual(row["evidence_label"], "观测")

    def test_unprobeable_row_keeps_bounded_text_disposal(self):
        """探不了的通道（无密钥 CLI 原生通道）文本证据是唯一证据形态：
        沿用有界冷却处置，不做观测级独占（1017 死锁案的放行设计）。"""
        health.report_failure(
            "Free CLI", _QUOTA_TEXT, model="m1", provider_id="p4")
        st = health._PROVIDERS["Free CLI"]
        st["unprobeable"] = True

        health.report_failure("Free CLI", _QUOTA_TEXT, model="m1", provider_id="p4")

        self.assertEqual(st["status"], "down")
        self.assertGreater(float(st["circuit_open_until"] or 0), 0)
        self.assertEqual(st["permanent_error_code"], "QUOTA")


if __name__ == "__main__":
    unittest.main()
