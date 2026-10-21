# -*- coding: utf-8 -*-
"""通道体检聚合（app/core/availability.py）的只读视图测试。

不探测、不写状态：打桩 modelhub/health 的四个数据口，验证
- 厂商行合并健康台账（按 id 优先、按 name 兜底）
- 模型熔断标记来自 health.down_models() 的 (厂商名, 模型名) 对
- CLI 降级链回填厂商名 + down 标记；未进绑定的账号探活单独成行
- 任一数据口抛错只降级为空，不冒 500
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.core import availability, modelhub

SNAP = {
    "providers": [
        {"provider_id": "p1", "provider": "维云", "status": "down",
         "consecutive_failures": 3, "last_fail_at": "2026-10-21 10:00:00",
         "last_ok_at": "2026-10-20 10:00:00", "last_error": "额度不足",
         "alerting": True, "silenced": False},
    ],
    "accounts": [
        {"agent_id": "codex", "status": "auth_failed", "code": "AUTH",
         "summary": "", "last_error": "登录过期"},
    ],
    "any_alerting": True,
}
DOWN = {("维云", "auto-gemini")}
PROVIEWS = [
    {"id": "p1", "name": "维云", "base_url": "https://vsllm.cc/v1",
     "protocol": "anthropic", "enabled": True, "keys_enabled": 1,
     "keys": [{"id": "k1"}], "model": "auto-gemini",
     "models": [{"name": "auto-gemini", "enabled": True},
                {"name": "auto-ghost", "enabled": False, "hidden": True}]},
    {"id": "p2", "name": "健康厂", "base_url": "https://ok.example/v1",
     "protocol": "openai", "enabled": True, "keys_enabled": 2,
     "keys": [{"id": "k1"}, {"id": "k2"}], "model": "m1",
     "models": [{"name": "m1", "enabled": True}]},
]
BINDINGS = {
    "codex": {"chain": [
        {"provider_id": "p1", "model": "auto-gemini"},
        {"provider_id": "p2", "model": "m1"},
    ]},
}


class AvailabilityPayloadTests(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(health_mod(), "snapshot", return_value=SNAP)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.q = mock.patch.object(health_mod(), "down_models", return_value=DOWN)
        self.q.start()
        self.addCleanup(self.q.stop)
        self.r = mock.patch.object(modelhub, "provider_view", return_value=PROVIEWS)
        self.r.start()
        self.addCleanup(self.r.stop)
        self.s = mock.patch.object(modelhub, "bindings", return_value=BINDINGS)
        self.s.start()
        self.addCleanup(self.s.stop)

    def test_provider_health_merged_and_models_marked(self):
        d = availability.payload()
        p1 = next(p for p in d["providers"] if p["id"] == "p1")
        self.assertEqual(p1["health"]["status"], "down")
        self.assertTrue(p1["health"]["alerting"])
        self.assertEqual(p1["keys_enabled"], 1)
        self.assertEqual(p1["keys_total"], 1)
        down = [m for m in p1["models"] if m["down"]]
        self.assertEqual([m["name"] for m in down], ["auto-gemini"])
        hidden = next(m for m in p1["models"] if m["name"] == "auto-ghost")
        self.assertFalse(hidden["enabled"])

    def test_fallback_chain_resolves_provider_name_and_down(self):
        d = availability.payload()
        codex = next(c for c in d["clis"] if c["id"] == "codex")
        self.assertEqual(codex["chain"][0]["provider"], "维云")
        self.assertTrue(codex["chain"][0]["down"])
        self.assertFalse(codex["chain"][1]["down"])
        # 账号探活挂在同一行，不重复成第二行
        self.assertEqual(codex["account"]["status"], "auth_failed")
        self.assertEqual(len(d["clis"]), 1)

    def test_provider_without_health_row_defaults_ok(self):
        d = availability.payload()
        p2 = next(p for p in d["providers"] if p["id"] == "p2")
        self.assertIsNone(p2["health"])
        self.assertEqual(d["any_alerting"], True)

    def test_probe_only_cli_gets_own_row(self):
        snap = dict(SNAP)
        snap["accounts"] = SNAP["accounts"] + [
            {"agent_id": "gemini", "status": "ok", "code": "", "summary": "", "last_error": ""}]
        with mock.patch.object(health_mod(), "snapshot", return_value=snap):
            d = availability.payload()
        ids = [c["id"] for c in d["clis"]]
        self.assertEqual(ids, ["codex", "gemini"])
        gemini = next(c for c in d["clis"] if c["id"] == "gemini")
        self.assertEqual(gemini["chain"], [])
        self.assertEqual(gemini["account"]["status"], "ok")

    def test_data_source_failure_degrades_to_empty(self):
        with mock.patch.object(health_mod(), "snapshot", side_effect=RuntimeError("boom")):
            with mock.patch.object(modelhub, "provider_view", side_effect=RuntimeError("boom")):
                d = availability.payload()
        self.assertEqual(d["providers"], [])
        self.assertIn("codex", [c["id"] for c in d["clis"]])  # 绑定还能给链


def health_mod():
    from app.core import health
    return health


if __name__ == "__main__":
    unittest.main()
