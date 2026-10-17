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
        health.init(data_dir=str(self.data_dir), start_probe=False)
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
        from app.core import health, modelhub
        orig = modelhub.providers
        modelhub.providers = lambda: [{"id": "prov-32", "name": "cavoti"}]
        try:
            for _ in range(4):
                health.report_failure("cavoti", "503")
            health.init(data_dir=str(self.data_dir), start_probe=False)  # 模拟重启
        finally:
            modelhub.providers = orig
        snap = health.snapshot()
        cav = [p for p in snap["providers"] if p["provider"] == "cavoti"][0]
        self.assertEqual(cav["status"], "down")
        self.assertTrue(snap["any_alerting"])

    def test_reload_drops_deleted_provider(self):
        """供应商已从配置删除：重启（重新 init）时健康条目一并清掉（Z.ai 幽灵案）。"""
        from app.core import health
        health.report_failure("Z.ai - API Key", "429", provider_id="prov-z")
        health.init(data_dir=str(self.data_dir), start_probe=False)  # 模拟重启：models.json 里已无 prov-z
        self.assertNotIn("Z.ai - API Key",
                         {p["provider"] for p in health.snapshot()["providers"]})


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
            ok, gone = health._probe_one(st)
            self.assertTrue(ok)
            self.assertFalse(gone)
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
            ok, gone = health._probe_one(st)
            self.assertFalse(ok)
            self.assertFalse(gone, "供应商还在（探活失败≠已删除）")
            self.assertEqual(health.snapshot()["providers"][0]["status"], "down")
        finally:
            modelhub.chat = orig_chat

    def test_probe_result_does_not_mutate_replaced_state(self):
        """init/测试切换后，旧探针返回不能覆盖当前 provider 状态。"""
        from app.core import health
        health.report_failure("cavoti", "503")
        stale = health._PROVIDERS["cavoti"]
        current = dict(stale, status="failing", last_error="current test")
        with health._LOCK:
            health._PROVIDERS["cavoti"] = current

        health._record_probe_result("cavoti", stale, ok=True, gone=False)
        self.assertIs(health._PROVIDERS["cavoti"], current)
        self.assertEqual(current["status"], "failing")
        self.assertEqual(current["last_error"], "current test")

        health._record_probe_result("cavoti", stale, ok=False, gone=True)
        self.assertIs(health._PROVIDERS["cavoti"], current)

    def test_persistence_failure_does_not_escape_reporting(self):
        """健康文件不可写时保留内存状态，不打断主流程错误处理。"""
        from app.core import health
        blocker = self.tmp / "not-a-directory"
        blocker.write_text("block", encoding="utf-8")
        with health._LOCK:
            health._FILE = blocker / "provider_health.json"

        health.report_failure("cavoti", "503")
        self.assertEqual(health.snapshot()["providers"][0]["provider"], "cavoti")


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

    def test_snapshot_includes_account_health_and_combines_alert_flag(self):
        from app.core import bench, health
        bench.record_failure("qwencode", "QUOTA", "余额不足")
        snap = health.snapshot()
        self.assertEqual(snap["accounts"][0]["agent_id"], "qwencode")
        self.assertEqual(snap["account_alerts"][0]["code"], "QUOTA")
        self.assertTrue(snap["any_alerting"])


class TestGhostCleanup(HealthBase):
    """供应商被删除后，健康记录要跟着清掉。

    幽灵条目探不活也永远不会恢复，还会每轮把 last_fail_at 刷成「刚刚」，
    健康页看起来像它一直在报错（Z.ai 删掉后仍显示 429 的误伤案，2026-09-18）。
    """

    def _seed(self, name="Z.ai - API Key", pid="prov-z"):
        from app.core import health
        health.report_failure(name, "<HTTPError 429: 'Too Many Requests'>",
                              model="glm-4.5", provider_id=pid)
        return name

    def test_prune_drops_deleted_provider(self):
        from app.core import health, modelhub
        name = self._seed()
        self.assertIn(name, health._PROVIDERS)
        orig = modelhub.providers
        modelhub.providers = lambda: [{"id": "prov-43", "name": "云知声"}]
        try:
            health._prune_missing()
        finally:
            modelhub.providers = orig
        self.assertNotIn(name, health._PROVIDERS)

    def test_prune_keeps_live_providers(self):
        """id 或名字任一还能对上号的都保留（改名不清历史）。"""
        from app.core import health, modelhub
        self._seed("cavoti", "prov-32")
        self._seed("renamed-provider", "prov-43")   # 改名但 id 还在 → 保留
        orig = modelhub.providers
        modelhub.providers = lambda: [
            {"id": "prov-32", "name": "cavoti"},
            {"id": "prov-43", "name": "云知声"},
        ]
        try:
            health._prune_missing()
        finally:
            modelhub.providers = orig
        self.assertIn("cavoti", health._PROVIDERS)
        self.assertIn("renamed-provider", health._PROVIDERS)

    def test_probe_one_flags_gone_provider(self):
        from app.core import health, modelhub
        st = {"name": "Z.ai - API Key", "provider_id": "prov-z",
              "model": "glm-4.5", "status": "down"}
        orig = modelhub.providers
        modelhub.providers = lambda: []
        try:
            ok, gone = health._probe_one(st)
        finally:
            modelhub.providers = orig
        self.assertFalse(ok)
        self.assertTrue(gone, "已删除的供应商要标记 gone，让探针循环清掉记录")

    def test_probe_failure_still_stamps_existing_provider(self):
        """供应商还在、探活失败 → 保持原行为（刷新 last_fail_at）。"""
        from app.core import health, modelhub
        self._seed("cavoti", "prov-32")
        orig_providers, orig_chat = modelhub.providers, modelhub.chat
        modelhub.providers = lambda: [{"id": "prov-32", "name": "cavoti",
                                       "api_key": "k", "endpoint": "http://x"}]
        modelhub.chat = lambda *a, **k: {"ok": False}
        try:
            ok, gone = health._probe_one(health._PROVIDERS["cavoti"])
        finally:
            modelhub.providers, modelhub.chat = orig_providers, orig_chat
        self.assertFalse(ok)
        self.assertFalse(gone)


class TestNativeCliChannelRecovery(HealthBase):
    """无密钥的 CLI 原生通道（mimo/opencode 免费档）与快照闸误记案。

    2026-10-16/17 实案：嵌套 worktree 快照拒绝被记成供应商故障 → down →
    HTTP 探针对无密钥通道永远探不活 → last_fail_at 永远刷成「刚刚」+ 候选链
    永久踢出（report_success 永远等不到）。三道防线：拒绝不入账、探不了不刷
    last_fail_at、冷却过后放行回候选让真实调用验证生死。"""

    _REFUSAL = "已有改动无法完整快照（.claude/worktrees/agent-x/），未启动外部 CLI"

    def test_snapshot_refusal_not_recorded(self):
        """快照闸起跑前拒绝与供应商无关：不建档、不计数、不告警。"""
        from app.core import health
        for _ in range(4):
            health.report_failure("MiMo Code 原生免费通道", self._REFUSAL,
                                  model="mimo/mimo-auto", provider_id="prov-46")
        self.assertEqual(health.snapshot()["providers"], [])

    def test_init_sweeps_poisoned_records(self):
        """历史误记（last_error=快照闸拒绝族）启动即解除。"""
        import json
        from app.core import health, modelhub
        row = {"name": "MiMo Code 原生免费通道", "provider_id": "prov-46",
               "model": "mimo/mimo-auto", "status": "down",
               "consecutive_failures": 4, "first_fail_at": 1.0,
               "last_fail_at": 2.0, "last_ok_at": 0,
               "last_error": self._REFUSAL, "alerted": True,
               "silenced": False, "silence_until": 0,
               "probe_next_at": 0, "probe_backoff_idx": 0, "recovered_at": 0}
        health._FILE.write_text(json.dumps({"providers": {"mimo": row}},
                                           ensure_ascii=False), encoding="utf-8")
        orig = modelhub.providers
        modelhub.providers = lambda: [
            {"id": "prov-46", "name": "MiMo Code 原生免费通道"}]
        try:
            health.init(data_dir=str(self.data_dir), start_probe=False)
        finally:
            modelhub.providers = orig
        snap = health.snapshot()["providers"][0]
        self.assertEqual(snap["status"], "recovered")
        self.assertFalse(snap["alerting"])

    def test_probe_unprobeable_never_stamps_fail_time(self):
        """无密钥通道：不发起 HTTP 探活、不刷 last_fail_at。"""
        from app.core import health, modelhub
        name = "MiMo Code 原生免费通道"
        for _ in range(4):
            health.report_failure(name, "<HTTPError 500: 'x'>",
                                  model="mimo/mimo-auto", provider_id="prov-46")
        with health._LOCK:
            st = health._PROVIDERS[name]
            stamp = st["last_fail_at"]
        orig_providers, orig_chat = modelhub.providers, modelhub.chat
        modelhub.providers = lambda: [
            {"id": "prov-46", "name": name, "api_key": "", "enabled": True}]

        def _no_chat(*a, **k):
            raise AssertionError("无密钥通道不应发起 HTTP 探活")

        modelhub.chat = _no_chat
        try:
            ok, gone = health._probe_one(st)
        finally:
            modelhub.providers, modelhub.chat = orig_providers, orig_chat
        self.assertFalse(ok)
        self.assertFalse(gone)
        self.assertTrue(st.get("unprobeable"))
        self.assertEqual(st["last_fail_at"], stamp)
        health._record_probe_result(name, st, ok=False, gone=False)
        self.assertEqual(st["last_fail_at"], stamp,
                         "探不了≠探挂了：记录分支同样不刷新最后失败时间")

    def test_down_names_releases_unprobeable_after_cooldown(self):
        """无密钥通道熔断冷却一过放行回候选；可探测通道维持剔除等探针。"""
        from app.core import health
        for _ in range(4):
            health.report_failure("mimo", "<HTTPError 500: 'x'>",
                                  model="mimo/mimo-auto", provider_id="prov-46")
        for _ in range(4):
            health.report_failure("cavoti", "503", provider_id="prov-32")
        with health._LOCK:
            health._PROVIDERS["mimo"]["unprobeable"] = True
        self.assertIn("mimo", health.down_names())
        self.assertIn("cavoti", health.down_names())
        with health._LOCK:
            health._PROVIDERS["mimo"]["circuit_open_until"] = time.time() - 1
            health._PROVIDERS["cavoti"]["circuit_open_until"] = time.time() - 1
        self.assertNotIn("mimo", health.down_names(),
                         "冷却已过的无密钥通道放行，让真实调用验证生死")
        self.assertIn("cavoti", health.down_names(),
                      "可探测通道冷却过了也继续剔除，恢复由探针确认")
