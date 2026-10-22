# -*- coding: utf-8 -*-
"""四类供应商误伤/盲重试的分诊回归（2026-10-22 健康面板实案）。

· 维云 vsllm 超时包裹 403 额度¥0：死因（QUOTA）优先于死法（TIMEOUT），
  不再当瞬态盲重试死号；
· Bigmodel 429 [1311] 套餐暂未开放模型权限：模型级死错走模型级熔断+换
  下一个模型，不连坐供应商的其他 10 个活模型；
· GLM包月 grok 不认 glm 模型 id：无 model 位的上报按错误文本点名的模型
  记账，不再 2 次打穿供应商熔断；
· opencode 退出码 3221226505（NTSTATUS 0xC0000409）硬崩溃：本地故障不
  污染供应商台账，且换将闸门放行。
"""
import tempfile
import unittest
from unittest import mock

from app.core import health, runner
from app.core.error_codes import (
    ErrorCode, classify_error_text, model_name_from_error,
)

VSLLM_TIMEOUT_QUOTA = (
    "超时；stderr/stdout: Reconnecting... 2/5 (unexpected status 403 Forbidden: "
    "用户额度不足, 剩余额度: ¥0.000000 (request id: 202610210840537015782328268d9d6), "
    "url: https://vsllm.cc/v1/responses, cf-ray: a4def5b8df63c8e2-NRT)")

BIGMODEL_PLAN_429 = (
    "claude 返回 is_error: API Error: Request rejected (429) · "
    "[1311][当前订阅套餐暂未开放GLM-5.3-FlashX权限]")

GROK_UNKNOWN_MODEL = (
    "退出码 1；stderr/stdout: Error: Couldn't set model 'glm-5.3': "
    "Invalid params: \"unknown model id\". Run 'grok models' to see available models.")


def _res(**over):
    base = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "",
            "duration": 10.0, "cancelled": False, "timed_out": False,
            "stalled": False, "deadline_exceeded": False,
            "output_truncated": False, "abort_marker": None}
    base.update(over)
    return base


class ClassifierTriageTests(unittest.TestCase):
    def test_subscription_plan_rejection_is_model_scoped(self):
        self.assertEqual(classify_error_text(BIGMODEL_PLAN_429),
                         ErrorCode.MODEL_UNAVAILABLE)

    def test_plain_request_rejected_stays_rate_limit(self):
        self.assertEqual(classify_error_text(
            "API Error: Request rejected (429)"), ErrorCode.RATE_LIMIT)

    def test_grok_unknown_model_id_is_model_scoped(self):
        self.assertEqual(classify_error_text(GROK_UNKNOWN_MODEL),
                         ErrorCode.MODEL_UNAVAILABLE)

    def test_model_name_extraction(self):
        self.assertEqual(model_name_from_error(GROK_UNKNOWN_MODEL), "glm-5.3")
        self.assertEqual(model_name_from_error(BIGMODEL_PLAN_429),
                         "GLM-5.3-FlashX")
        self.assertEqual(model_name_from_error(VSLLM_TIMEOUT_QUOTA), "")


class RunnerClassifyTests(unittest.TestCase):
    def setUp(self):
        with runner._DSH_LAUNCH_LOCK:
            runner._DSH_LAUNCH_HISTORY.clear()

    def tearDown(self):
        with runner._DSH_LAUNCH_LOCK:
            runner._DSH_LAUNCH_HISTORY.clear()

    def test_timeout_wrapped_quota_is_quota(self):
        code = runner._classify_failure(_res(
            timed_out=True, exit_code=None,
            stderr="Reconnecting... 2/5 (unexpected status 403 Forbidden: "
                   "用户额度不足, 剩余额度: ¥0.000000)"))
        self.assertEqual(code, ErrorCode.QUOTA)

    def test_timeout_without_dead_signal_stays_timeout(self):
        code = runner._classify_failure(_res(
            timed_out=True, exit_code=None, stderr="Reconnecting... 2/5"))
        self.assertEqual(code, ErrorCode.TIMEOUT)

    def test_ntstatus_crash_is_local_state(self):
        self.assertEqual(
            runner._classify_failure(_res(exit_code=3221226505)),
            ErrorCode.LOCAL_STATE)

    def test_small_exit_code_without_signal_stays_vendor_error(self):
        self.assertEqual(
            runner._classify_failure(_res(exit_code=1)),
            ErrorCode.VENDOR_ERROR)

    def _run(self, results, chain):
        with mock.patch.object(runner, "_build_call",
                               return_value=(["dsh"], "prompt", "prompt", [])), \
             mock.patch.object(runner, "run_process",
                               side_effect=results) as run_process:
            agent = {"id": "dsh", "kind": "generic", "mode": "real",
                     "command": "dsh", "call_chain": chain,
                     "orch": {"timeout_ms": 1000}}
            result = runner.run_agent(
                agent, "prompt", timeout=5,
                command_context={"run_id": "run-triage", "role": "draft"},
                checkpoint_run_id="run-triage")
        return result, run_process.call_count

    def test_timeout_wrapped_quota_reports_quota_code(self):
        chain = [{"model": "m1", "provider": {"id": "p1", "name": "P1"},
                  "provider_id": "p1"}]
        result, calls = self._run(
            [_res(timed_out=True, exit_code=None,
                  stderr="unexpected status 403 Forbidden: 用户额度不足, "
                         "剩余额度: ¥0.000000")], chain)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], ErrorCode.QUOTA.value)
        self.assertEqual(calls, 1)

    def test_ntstatus_crash_walks_to_next_chain_candidate(self):
        chain = [
            {"model": "m1", "provider": {"id": "p1", "name": "P1"},
             "provider_id": "p1"},
            {"model": "m2", "provider": {"id": "p2", "name": "P2"},
             "provider_id": "p2"},
        ]
        result, calls = self._run(
            [_res(exit_code=3221226505),
             _res(ok=True, exit_code=0, stdout="done")], chain)
        self.assertEqual(calls, 2)
        self.assertTrue(result["ok"])


class HealthTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        health.init(self.tmp.name, start_probe=False)

    def tearDown(self):
        health.init(self.tmp.name, start_probe=False)
        self.tmp.cleanup()

    def test_bare_exit_code_crash_is_not_provider_failure(self):
        with mock.patch("app.core.modelhub.providers", return_value=[]):
            health.report_failure("OpenCode 原生免费模型", "退出码 3221226505",
                                  model="", provider_id="opencode-native",
                                  error_code="LOCAL_STATE")
            health.report_failure("OpenCode 原生免费模型", "退出码 3221226505")
        self.assertEqual(health.snapshot()["providers"], [])

    def test_plan_rejection_lands_in_model_circuit_not_provider(self):
        with mock.patch("app.core.modelhub.providers", return_value=[]):
            health.report_failure("Bigmodel - API Key", BIGMODEL_PLAN_429,
                                  model="glm-5.3-flashx",
                                  provider_id="bigmodel-key",
                                  error_code="MODEL_UNAVAILABLE")
        row = health.snapshot()["providers"][0]
        self.assertEqual(row["provider"], "Bigmodel - API Key")
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertNotEqual(row["status"], "down")
        self.assertTrue(health.is_model_down("Bigmodel - API Key",
                                             "glm-5.3-flashx"))
        self.assertFalse(health.is_model_down("Bigmodel - API Key", "glm-5.3"))

    def test_model_scoped_failure_without_model_slot_uses_named_model(self):
        providers = [{"id": "glm-monthly", "name": "GLM包月", "enabled": True,
                      "models": [{"name": "glm-5.3", "enabled": True},
                                 {"name": "glm-5.1", "enabled": True}]}]
        with mock.patch("app.core.modelhub.providers", return_value=providers):
            health.report_failure("GLM包月", GROK_UNKNOWN_MODEL,
                                  model="", provider_id="glm-monthly",
                                  error_code="MODEL_UNAVAILABLE")
        row = health.snapshot()["providers"][0]
        self.assertEqual(row["provider"], "GLM包月")
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertNotEqual(row["status"], "down")
        # 大小写不敏感对账回配置表的规范名，路由层才能查回这只熔断
        self.assertTrue(health.is_model_down("GLM包月", "glm-5.3"))
        self.assertFalse(health.is_model_down("GLM包月", "glm-5.1"))

    def test_unresolvable_named_model_falls_back_to_provider_row(self):
        with mock.patch("app.core.modelhub.providers", return_value=[]):
            health.report_failure("GLM包月", GROK_UNKNOWN_MODEL,
                                  model="", provider_id="glm-monthly",
                                  error_code="MODEL_UNAVAILABLE")
        row = health.snapshot()["providers"][0]
        self.assertEqual(row["provider"], "GLM包月")


if __name__ == "__main__":
    unittest.main()
