# -*- coding: utf-8 -*-
"""0930 假死链案的四处加固回归：

1. 本地 CLI 启动失败（"启动失败: FileNotFoundError…"）分类为 TOOL_LAUNCH，
   不计入供应商健康故障、不作废评测结论（此前把健康的 Bigmodel 拖进 down）；
2. 新鲜失败（fresh+failed）的评测条目按 15 分钟退避重探，不再硬卡 24h TTL
   （公司OpenAI 案：一条失败探针锁死 codex 链到次日）；
3. resolve_binding 判空链时逐条记录死因，binding_dead_msg 带出真实死因
   （协议不匹配/健康作废/探针失败），而非只列配置类猜测。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest

LAUNCH_ERR = ("启动失败: FileNotFoundError(2, '系统找不到指定的文件。', "
              "None, 2, None)")
# 测试夹具假密钥（同 test_binding_chain：仅供本地临时数据目录，非真实凭据）
FAKE_KEY = "sk-test-" + "a" * 20


class TestToolLaunchNotProviderFailure(BaseTest):
    def test_classify_launch_failure(self):
        from app.core.error_codes import ErrorCode, classify_error_text
        self.assertEqual(classify_error_text(LAUNCH_ERR), ErrorCode.TOOL_LAUNCH)
        # 供应商侧报错不受新分支影响
        self.assertEqual(classify_error_text("HTTP 401 unauthorized"),
                         ErrorCode.AUTH)
        self.assertEqual(classify_error_text("HTTP 429 request rejected"),
                         ErrorCode.RATE_LIMIT)

    def test_report_failure_ignores_launch_but_keeps_real_failures(self):
        from app.core import evaluation, health
        evaluation.record("model", "prov-x", "m1",
                          {"ok": True, "status": "passed"})
        # 启动失败：不建健康条目、评测结论不作废
        health.report_failure("厂商X", LAUNCH_ERR, model="m1",
                              provider_id="prov-x")
        names = [p["provider"] for p in health.snapshot()["providers"]]
        self.assertNotIn("厂商X", names)
        entry = evaluation.get("model", "prov-x", "m1")
        self.assertTrue(entry["fresh"])
        self.assertTrue(entry["ok"])
        # 真供应商失败：照旧作废
        health.report_failure("厂商X", "HTTP 500 internal server error",
                              model="m1", provider_id="prov-x")
        entry = evaluation.get("model", "prov-x", "m1")
        self.assertFalse(entry["fresh"])


class TestFreshFailedReprobe(BaseTest):
    def _record_failed(self, evaluation, age_s):
        evaluation.record("model", "prov-y", "m-y",
                          {"ok": False, "status": "failed",
                           "error": "HTTP 503 service unavailable"})
        # 把 evaluated_at 拨回 age_s 秒前，模拟退避窗口内外两种状态
        path = evaluation._path()
        data = json.loads(path.read_text(encoding="utf-8"))
        for v in (data.get("entries") or {}).values():
            v["evaluated_at"] -= age_s
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def test_old_failure_reprobes_and_recovers(self):
        from app.core import evaluation, modelhub
        self._record_failed(evaluation, 20 * 60)   # 已过 15 分钟退避
        with mock.patch.object(
                modelhub, "test_model",
                return_value={"ok": True, "fresh": True,
                              "status": "passed"}) as tm:
            self.assertTrue(modelhub._evaluation_is_usable("prov-y", "m-y"))
        tm.assert_called_once_with("prov-y", "m-y")

    def test_recent_failure_blocks_without_probe(self):
        from app.core import evaluation, modelhub
        self._record_failed(evaluation, 60)        # 退避窗口内
        with mock.patch.object(modelhub, "test_model") as tm:
            self.assertFalse(modelhub._evaluation_is_usable("prov-y", "m-y"))
        tm.assert_not_called()

    def test_passing_entry_never_probes(self):
        from app.core import evaluation, modelhub
        evaluation.record("model", "prov-y", "m-y",
                          {"ok": True, "status": "passed"})
        with mock.patch.object(modelhub, "test_model") as tm:
            self.assertTrue(modelhub._evaluation_is_usable("prov-y", "m-y"))
        tm.assert_not_called()


class TestDeadReasons(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import modelhub
        modelhub._DEAD_REASONS.clear()

    def _anthropic_provider(self):
        from app.core import modelhub
        modelhub.upsert_provider({"name": "厂商A", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1",
                                  "api_key": FAKE_KEY})
        return {p["name"]: p["id"] for p in modelhub.providers()}["厂商A"]

    def test_protocol_mismatch_reason_in_msg(self):
        from app.core import modelhub
        pid = self._anthropic_provider()
        # qwencode 只吃 openai，anthropic 面挂上去=结构性死链（0930 qwencode 案）
        modelhub.set_binding("qwencode", chain=[{"provider_id": pid,
                                                 "model": "glm-x"}])
        self.assertIsNone(modelhub.resolve_binding("qwencode"))
        msg = modelhub.binding_dead_msg("qwencode")
        self.assertIn("协议不匹配", msg)
        self.assertIn("厂商A", msg)
        self.assertIn("openai", msg)
        # 同一供应商对 claude-code 可解析：不留死因
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid,
                                                    "model": "glm-x"}])
        self.assertIsNotNone(modelhub.resolve_binding("claude-code"))
        self.assertEqual(modelhub.last_dead_reasons("claude-code"), [])

    def test_disabled_provider_reason(self):
        from app.core import modelhub
        pid = self._anthropic_provider()
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["enabled"] = False
        modelhub._save(data)
        modelhub.set_binding("opencode", chain=[{"provider_id": pid,
                                                 "model": "glm-x"}])
        self.assertIsNone(modelhub.resolve_binding("opencode"))
        self.assertIn("供应商已停用", modelhub.binding_dead_msg("opencode"))

    def test_single_provider_path_reason(self):
        from app.core import modelhub
        pid = self._anthropic_provider()
        # 无链、单 provider_id 形态：codex 只吃 openai，anthropic 面判死
        modelhub.set_binding("codex-cli", provider_id=pid, model="glm-x")
        self.assertIsNone(modelhub.resolve_binding("codex-cli"))
        self.assertIn("协议不匹配", modelhub.binding_dead_msg("codex-cli"))
