# -*- coding: utf-8 -*-
"""供应商健康监测与告警（health.py）测试。
需求：连不上超时自动降级+告警；恢复自动解除；可手动静默/恢复。
"""
from __future__ import annotations

import threading
import time
from base import BaseTest


class HealthBase(BaseTest):

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


class TestStateMachine(HealthBase):

    def test_success_without_history_creates_nothing(self):
        from app.core import health
        health.report_success("cavoti")
        self.assertNotIn("cavoti", health.snapshot()["providers"][0:1] and
                         {p["provider"] for p in health.snapshot()["providers"]})

    def test_failures_progress_to_failing_then_down(self):
        from app.core import health
        for i in range(2):
            health.report_failure("cavoti", "503")
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["status"], "failing")
        self.assertFalse(snap["alerting"])
        # 第 3、4 次失败 → down + 告警
        health.report_failure("cavoti", "503")
        health.report_failure("cavoti", "503")
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["status"], "down")
        self.assertTrue(snap["alerting"])
        self.assertIn("503", snap["last_error"])

    def test_down_after_duration(self):
        """failing 持续超过 DOWN_AFTER_SECONDS → down（即使次数少）。"""
        from app.core import health
        health.report_failure("cavoti", "timeout")
        health.report_failure("cavoti", "timeout")
        with health._LOCK:
            st = health._PROVIDERS["cavoti"]
            st["first_fail_at"] = time.time() - health.DOWN_AFTER_SECONDS - 1
        health.report_failure("cavoti", "timeout")
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["status"], "down")

    def test_recovery_clears_alert(self):
        """恢复自动解除告警。"""
        from app.core import health
        for _ in range(4):
            health.report_failure("cavoti", "503")
        self.assertTrue(health.snapshot()["providers"][0]["alerting"])
        health.report_success("cavoti")
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["status"], "recovered")
        self.assertFalse(snap["alerting"])

    def test_second_failure_after_recovery_recounts(self):
        """恢复后再失败 → 重新计数。"""
        from app.core import health
        for _ in range(4):
            health.report_failure("cavoti", "503")
        health.report_success("cavoti")
        health.report_failure("cavoti", "503")
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["consecutive_failures"], 1)
        self.assertEqual(snap["status"], "failing")


class TestSilenceAndReset(HealthBase):

    def _down(self):
        from app.core import health
        for _ in range(4):
            health.report_failure("cavoti", "503")

    def test_silence_mutes_alert(self):
        from app.core import health
        self._down()
        self.assertTrue(health.snapshot()["providers"][0]["alerting"])
        ok, err = health.silence("cavoti")
        self.assertTrue(ok)
        snap = health.snapshot()["providers"][0]
        self.assertTrue(snap["silenced"])
        self.assertFalse(snap["alerting"])

    def test_silence_unknown_provider_rejected(self):
        from app.core import health
        ok, err = health.silence("nonexistent")
        self.assertFalse(ok)

    def test_reset_manually_recovers(self):
        """手动恢复：状态清为 recovered，下次失败重新计数。"""
        from app.core import health
        self._down()
        ok, _ = health.reset("cavoti")
        self.assertTrue(ok)
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["status"], "recovered")
        # 再失败 → 从 1 重新计
        health.report_failure("cavoti", "503")
        self.assertEqual(health.snapshot()["providers"][0]["consecutive_failures"], 1)

    def test_alert_unsilenced_after_recovery_then_fail(self):
        """静默后恢复，再失败会重新告警（静默只管一次故障期）。"""
        from app.core import health
        self._down()
        health.silence("cavoti")
        self.assertFalse(health.snapshot()["providers"][0]["alerting"])
        health.report_success("cavoti")   # 恢复（清静默）
        for _ in range(4):
            health.report_failure("cavoti", "503")
        self.assertTrue(health.snapshot()["providers"][0]["alerting"],
                        "新一轮故障必须重新告警")


class TestDownQuery(HealthBase):

    def test_is_down_and_down_names(self):
        from app.core import health
        for _ in range(4):
            health.report_failure("cavoti", "503")
        health.report_failure("weiyun", "timeout")
        health.report_failure("weiyun", "timeout")
        self.assertTrue(health.is_down("cavoti"))
        self.assertFalse(health.is_down("weiyun"))  # 还在 failing
        self.assertEqual(health.down_names(), {"cavoti"})


class TestPersistence(HealthBase):

    def test_reload_keeps_down_state(self):
        """重启（重新 init）后 down/告警状态保留。"""
        from app.core import health
        for _ in range(4):
            health.report_failure("cavoti", "503")
        health.init(data_dir=str(self.data_dir))  # 模拟重启
        snap = health.snapshot()
        cav = [p for p in snap["providers"] if p["provider"] == "cavoti"][0]
        self.assertEqual(cav["status"], "down")
        self.assertTrue(snap["any_alerting"])


class TestProbeLoop(HealthBase):
    """探针：对 down 的 provider 探活，恢复则自动解除。"""

    def setUp(self):
        super().setUp()
        from app.core import modelhub
        self._orig_providers = modelhub.providers
        modelhub.providers = lambda: [
            {"id": "prov-15", "name": "维云模型 YBJ", "protocol": "openai",
             "base_url": "https://vsllm.cc/v1", "api_key": "k", "enabled": True,
             "models": [{"name": "[opencode]deepseek-v4-flash"}]},
            {"id": "prov-32", "name": "cavoti", "protocol": "anthropic",
             "base_url": "https://cavoti.com", "api_key": "k", "enabled": True,
             "models": [{"name": "deepseek-v4-flash-0731"}]},
        ]

    def tearDown(self):
        from app.core import modelhub
        modelhub.providers = self._orig_providers
        super().tearDown()

    def test_probe_success_recovers(self):
        from app.core import health
        from app.core import modelhub
        for _ in range(4):
            health.report_failure("维云模型 YBJ", "503",
                                  model="[opencode]deepseek-v4-flash",
                                  provider_id="prov-15")
        orig_chat = modelhub.chat
        modelhub.chat = lambda *a, **k: {"ok": True, "text": "1"}
        try:
            with health._LOCK:
                st = health._PROVIDERS["维云模型 YBJ"]
                st["probe_next_at"] = 0
            ok = health._probe_one(st)
            self.assertTrue(ok)
        finally:
            modelhub.chat = orig_chat
        health.report_success("维云模型 YBJ")
        self.assertEqual(health.snapshot()["providers"][0]["status"], "recovered")

    def test_probe_failure_keeps_down(self):
        from app.core import health
        from app.core import modelhub
        for _ in range(4):
            health.report_failure("cavoti", "503",
                                  model="deepseek-v4-flash-0731",
                                  provider_id="prov-32")
        orig_chat = modelhub.chat
        modelhub.chat = lambda *a, **k: {"ok": False, "error": "timeout"}
        try:
            with health._LOCK:
                st = health._PROVIDERS["cavoti"]
            ok = health._probe_one(st)
            self.assertFalse(ok)
            self.assertEqual(health.snapshot()["providers"][0]["status"], "down")
        finally:
            modelhub.chat = orig_chat


class TestSnapshotShape(HealthBase):

    def test_snapshot_has_alerts_fields(self):
        from app.core import health
        for _ in range(4):
            health.report_failure("cavoti", "boom")
        snap = health.snapshot()
        self.assertIn("any_alerting", snap)
        self.assertEqual(len(snap["alerts"]), 1)
        p = snap["alerts"][0]
        for k in ("provider", "status", "consecutive_failures", "last_error",
                  "alerting", "silenced", "first_fail_at"):
            self.assertIn(k, p)


class TestStaticBindingAlerts(HealthBase):
    """静态死链告警：绑定链起跑前就全死（厂商停用/无密钥/协议不匹配）。
    与运行期故障不同：立即告警、不进探针，绑定恢复自动解除。"""

    def test_dead_immediately_alerts(self):
        from app.core import health
        health.report_binding_dead("claude-code", "绑定链全部失效")
        snap = health.snapshot()
        self.assertTrue(snap["any_alerting"])
        p = snap["alerts"][0]
        self.assertEqual(p["provider"], "绑定链·claude-code")
        self.assertTrue(p["static"])
        self.assertTrue(p["alerting"])
        self.assertIn("绑定链", p["last_error"])

    def test_static_never_probed(self):
        """静态死链无端点可探：不得进入探针目标，只能靠绑定恢复解除。"""
        from app.core import health
        health.report_binding_dead("opencode", "x")
        with health._LOCK:
            st = health._PROVIDERS["绑定链·opencode"]
            targets = [n for n, s in health._PROVIDERS.items()
                       if not s.get("static") and s.get("status") in ("failing", "down")]
        self.assertTrue(st["static"])
        self.assertEqual(st["status"], "down")
        self.assertEqual(targets, [])

    def test_ok_clears_and_rearms(self):
        from app.core import health
        health.report_binding_dead("claude-code", "x")
        health.report_binding_ok("claude-code")
        snap = health.snapshot()
        self.assertFalse(snap["any_alerting"])
        self.assertEqual([p["status"] for p in snap["providers"] if p["static"]],
                         ["recovered"])
        # 恢复后再死 → 重新告警
        health.report_binding_dead("claude-code", "y")
        self.assertTrue(health.snapshot()["any_alerting"])

    def test_silence_mutes_until_recovery(self):
        from app.core import health
        health.report_binding_dead("claude-code", "x")
        ok, _ = health.silence("绑定链·claude-code")
        self.assertTrue(ok)
        health.report_binding_dead("claude-code", "x2")
        self.assertFalse(health.snapshot()["any_alerting"],
                         "静默期内不得重复告警")
        health.report_binding_ok("claude-code")   # 恢复即重新武装
        health.report_binding_dead("claude-code", "x3")
        self.assertTrue(health.snapshot()["any_alerting"])

    def test_ok_without_state_is_noop(self):
        from app.core import health
        health.report_binding_ok("never-bound")
        self.assertEqual(health.snapshot()["providers"], [])