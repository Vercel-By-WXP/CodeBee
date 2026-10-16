# -*- coding: utf-8 -*-
"""历史空链 provider_id 迁移回归。"""
from __future__ import annotations

from app.core import modelhub
from base import BaseTest


FAKE_KEY = "sk-test-" + "a" * 20


class TestStaleProviderOnlyMigration(BaseTest):
    def _provider(self, protocol="anthropic", name="厂商A"):
        modelhub.upsert_provider({
            "name": name, "protocol": protocol,
            "base_url": "https://a.test/v1", "api_key": FAKE_KEY,
        })
        return next(p["id"] for p in modelhub.providers() if p["name"] == name)

    def test_incompatible_empty_legacy_binding_is_cleared_and_idempotent(self):
        pid = self._provider("anthropic")
        data = modelhub._load()
        data["bindings"] = {
            "codex-cli": {"provider_id": pid, "chain": [], "models": [],
                          "model": "", "difficulty_routing": True},
        }
        modelhub._save(data)

        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 1)
        binding = modelhub.bindings()["codex-cli"]
        self.assertEqual(binding["provider_id"], "")
        self.assertFalse(modelhub.bind_agent({
            "id": "codex-cli", "kind": "codex", "mode": "real",
        }).get("binding_configured"))
        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 0)

    def test_real_model_binding_and_compatible_provider_only_are_preserved(self):
        pa = self._provider("anthropic")
        po = self._provider("openai", "厂商B")
        data = modelhub._load()
        data["bindings"] = {
            "codex-cli": {"provider_id": pa, "chain": [], "models": [],
                          "model": "gpt-x"},
            "deepseek-harness": {"provider_id": po, "chain": [],
                                 "models": [], "difficulty_routing": True},
            "claude-code": {"provider_id": pa, "chain": [], "models": [],
                            "difficulty_routing": True},
        }
        modelhub._save(data)

        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 0)
        bindings = modelhub.bindings()
        self.assertEqual(bindings["codex-cli"]["provider_id"], pa)
        self.assertEqual(bindings["deepseek-harness"]["provider_id"], po)
        self.assertEqual(bindings["claude-code"]["provider_id"], pa)

    def test_all_keys_cooling_is_treated_as_unavailable(self):
        pid = self._provider("anthropic")
        data = modelhub._load()
        provider = next(p for p in data["providers"] if p["id"] == pid)
        provider["keys"] = [{"id": "k1", "key": FAKE_KEY, "enabled": True,
                              "cooling": True, "cool_until": 9999999999}]
        provider["api_key"] = FAKE_KEY
        data["bindings"] = {
            "codex-cli": {"provider_id": pid, "chain": [], "models": [],
                          "model": ""},
        }
        modelhub._save(data)

        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 1)
        self.assertEqual(modelhub.bindings()["codex-cli"]["provider_id"], "")

    def test_native_cli_provider_without_api_key_is_preserved(self):
        data = modelhub._load()
        pid = "prov-native"
        data["providers"] = [{"id": pid, "name": "Codex OAuth",
                              "protocol": "openai",
                              "base_url": "https://chatgpt.com/backend-api",
                              "credential_mode": "cli", "native_cli": "codex-cli",
                              "native_models": ["gpt-6.1-sol"], "api_key": "",
                              "enabled": True}]
        data["bindings"] = {"codex-cli": {"provider_id": pid, "chain": [],
                                             "models": [], "model": ""}}
        modelhub._save(data)
        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 0)
        self.assertEqual(modelhub.bindings()["codex-cli"]["provider_id"], pid)

    def test_native_cli_without_injectable_protocol_is_preserved(self):
        """Native OAuth remains valid even when the CLI has no wire protocol."""
        data = modelhub._load()
        pid = "prov-gemini-native"
        data["providers"] = [{"id": pid, "name": "Gemini OAuth",
                              "protocol": "google",
                              "base_url": "https://generativelanguage.googleapis.com",
                              "credential_mode": "cli", "native_cli": "gemini-cli",
                              "native_models": ["gemini-2.5-pro"], "api_key": "",
                              "enabled": True}]
        data["bindings"] = {"gemini-cli": {"provider_id": pid, "chain": [],
                                             "models": [], "model": ""}}
        modelhub._save(data)
        self.assertEqual(modelhub.bindable_protocols("gemini-cli"), ())
        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 0)
        self.assertEqual(modelhub.bindings()["gemini-cli"]["provider_id"], pid)

    def test_migration_marker_does_not_skip_recreated_stale_binding(self):
        pid = self._provider("anthropic")
        data = modelhub._load()
        data["_binding_migrations"] = {"stale_provider_only_v1": True}
        data["bindings"] = {"codex-cli": {"provider_id": pid, "chain": [],
                                             "models": [], "model": ""}}
        modelhub._save(data)
        self.assertEqual(modelhub.migrate_stale_provider_only_bindings(), 1)
        self.assertEqual(modelhub.bindings()["codex-cli"]["provider_id"], "")


if __name__ == "__main__":
    import unittest
    unittest.main()
