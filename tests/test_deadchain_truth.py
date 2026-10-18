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
        # deepseek-harness 只吃 openai，anthropic 面挂上去=结构性死链
        # （原用 qwencode——它在第三批接入时划入本地凭据 CLI，resolve_binding
        # 顶部即早退，走不到链分支的协议检查，2026-10-16 存量红对账后更换）
        modelhub.set_binding("deepseek-harness", chain=[{"provider_id": pid,
                                                         "model": "glm-x"}])
        self.assertIsNone(modelhub.resolve_binding("deepseek-harness"))
        msg = modelhub.binding_dead_msg("deepseek-harness")
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

    def test_evaluation_gate_death_records_reason(self):
        """legacy 分支最后一道评测闸判死也要留死因（2026-10-16 起草全灭案）。

        供应商本体全绿、只挂在模型的评测结论上时，此前静默返回 None，
        binding_dead_msg 只剩泛泛的「绑定链全部失效」而没有死因段。"""
        from unittest import mock
        from app.core import modelhub
        pid = self._anthropic_provider()
        modelhub.set_binding("opencode", provider_id=pid, model="glm-x")
        with mock.patch.object(modelhub, "_evaluation_is_usable",
                               return_value=False):
            self.assertIsNone(modelhub.resolve_binding("opencode"))
        msg = modelhub.binding_dead_msg("opencode")
        self.assertIn("最近一次解析死因", msg)
        self.assertIn("评测结论不可用", msg)


class TestLegacyLocalCredBinding(BaseTest):
    """本地凭据 CLI 的遗留 provider_id 绑定不算「配过链」（2026-10-16 案）。

    trae/pi 等只吃本机登录态的 CLI 被模型调度页历史误配了供应商后，死链闸
    把它们整场判死（报错还让用户「勿配链」，写入侧却没拦）。现在 bind_agent
    对无 native 桥的本地凭据 CLI 强制 configured=False，回落本机默认照跑。"""

    def test_local_cred_cli_legacy_binding_falls_back_to_cli_default(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "厂商A", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1",
                                  "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("trae-agent", provider_id=pid, model="glm-x")
        out = modelhub.bind_agent({"id": "trae-agent", "kind": "trae-agent",
                                   "mode": "real"})
        self.assertFalse(out.get("binding_configured"),
                         "遗留条目不得把本地凭据 CLI 判成配过链")
        self.assertEqual(out.get("binding_mode"), "cli_default")

    def test_bindable_cli_provider_only_binding_still_configured(self):
        # 可注入协议的 CLI 不受影响：provider_id 绑定照旧算配过链
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "厂商A", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1",
                                  "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("opencode", provider_id=pid)
        out = modelhub.bind_agent({"id": "opencode", "kind": "opencode",
                                   "mode": "real"})
        self.assertTrue(out.get("binding_configured"))


class TestUnclassifiedFailureNotProviderFault(BaseTest):
    """无供应商侧信号的失败不进健康台账（2026-10-16 起草全灭案）。

    CLI 本地崩溃输出（「退出码 N；stderr/stdout: > build · …」全文无 HTTP
    状态/凭据/限流字样）此前被记成供应商健康故障并作废评测结论，把健康的
    Bigmodel 拖进连锁死链。stderr 里带 HTTP 5xx 字样的崩溃仍照旧记录。"""

    BANNER_CRASH = ("退出码 1；stderr/stdout: > build · glm-5.3；仅覆盖 Git "
                    "可见文件改动；忽略文件、shell/MCP 与外部服务副作用不保证可回滚。")

    def test_unclassified_cli_crash_does_not_invalidate(self):
        from app.core import evaluation, health
        evaluation.record("model", "prov-z", "m-z",
                          {"ok": True, "status": "passed"})
        health.report_failure("厂商Z", self.BANNER_CRASH,
                              model="m-z", provider_id="prov-z")
        self.assertEqual([p["provider"] for p in health.snapshot()["providers"]],
                         [], "无信号崩溃不得建健康条目")
        self.assertTrue(evaluation.get("model", "prov-z", "m-z")["fresh"],
                        "无信号崩溃不得作废评测结论")

    def test_classified_vendor_crash_still_records(self):
        from app.core import evaluation, health
        evaluation.record("model", "prov-z", "m-z",
                          {"ok": True, "status": "passed"})
        health.report_failure("厂商Z",
                              "退出码 1；stderr/stdout: HTTP 502 bad gateway",
                              model="m-z", provider_id="prov-z")
        names = [p["provider"] for p in health.snapshot()["providers"]]
        self.assertIn("厂商Z", names)
        self.assertFalse(evaluation.get("model", "prov-z", "m-z")["fresh"])

    def test_unclassified_crash_warning_is_rate_limited(self):
        from app.core import health
        with health._LOCK:
            health._UNSOURCED_LOGGED.clear()
        with mock.patch.object(health.log, "warning") as warning:
            for _ in range(4):
                health.report_failure("厂商Z", self.BANNER_CRASH,
                                      model="m-z", provider_id="prov-z")
        self.assertEqual(warning.call_count, 1)
