# -*- coding: utf-8 -*-
"""探测到即启用种子（registry.resolve_orch_enabled / effective_agents）。

精简化核心：装了但没手动开的 CLI，默认进自动路由；显式关闭永远优先；
default_enabled 仅作无探测信息时的兜底硬开。全部走临时数据目录，不碰用户真实数据。
"""
import unittest
from unittest import mock

from core import registry


def _entry(eid, kind="codex", default_enabled=False):
    return {"id": eid, "name": eid, "orch": {"kind": kind, "command": eid},
            "default_enabled": default_enabled}


class ResolveEnabled(unittest.TestCase):
    def test_no_pref_detected_seed_on(self):
        self.assertTrue(
            registry.resolve_orch_enabled(_entry("x"), {"installed": True}, {}))

    def test_no_pref_not_detected_off(self):
        self.assertFalse(
            registry.resolve_orch_enabled(_entry("x"), {"installed": False}, {}))

    def test_no_pref_no_detect_seed_false_off(self):
        self.assertFalse(
            registry.resolve_orch_enabled(_entry("x"), {}, {}, seed_detected=False))

    def test_explicit_disable_wins(self):
        self.assertFalse(
            registry.resolve_orch_enabled(_entry("x"), {"installed": True},
                                         {"enabled": False}))

    def test_explicit_enable_wins(self):
        self.assertTrue(
            registry.resolve_orch_enabled(_entry("x"), {"installed": True},
                                         {"enabled": True}))

    def test_explicit_enable_not_installed_still_blocked(self):
        # 没装上的 CLI 不能被「启用」拉进路由（路由需要真实可执行文件）。
        self.assertFalse(
            registry.resolve_orch_enabled(_entry("x"), {"installed": False},
                                         {"enabled": True}))

    def test_default_enabled_fallback(self):
        self.assertTrue(
            registry.resolve_orch_enabled(_entry("x", default_enabled=True),
                                         {"installed": True}, {}))

    def test_no_orch_kind_never(self):
        self.assertFalse(
            registry.resolve_orch_enabled({"id": "x", "orch": None},
                                         {"installed": True}, {}))


class EffectiveAgentsSeed(unittest.TestCase):
    def _agents(self, entries, detected, enabled=None, seed=True):
        if enabled is not None:
            with mock.patch.object(registry, "load_enabled", return_value=enabled):
                return registry.effective_agents(entries, detected,
                                                 seed_detected=seed)
        return registry.effective_agents(entries, detected, seed_detected=seed)

    @staticmethod
    def _real_ids(out):
        # effective_agents 总会追加内置 mock 智能体，比较时排除。
        return {a["id"] for a in out if not str(a["id"]).startswith("mock-")}

    def test_detected_enabled_by_seed(self):
        entries = [_entry("a"), _entry("b")]
        detected = {"a": {"installed": True}, "b": {"installed": True}}
        out = self._agents(entries, detected, enabled={})
        self.assertEqual(self._real_ids(out), {"a", "b"})

    def test_undetected_excluded(self):
        entries = [_entry("a"), _entry("b")]
        detected = {"a": {"installed": True}, "b": {"installed": False}}
        out = self._agents(entries, detected, enabled={})
        self.assertEqual(self._real_ids(out), {"a"})

    def test_explicit_disable_respected(self):
        entries = [_entry("a")]
        detected = {"a": {"installed": True}}
        out = self._agents(entries, detected, enabled={"a": {"enabled": False}})
        self.assertEqual(self._real_ids(out), set())

    def test_seed_off_uses_default_enabled(self):
        entries = [_entry("a", default_enabled=True),
                   _entry("b", default_enabled=False)]
        detected = {"a": {"installed": True}, "b": {"installed": True}}
        out = self._agents(entries, detected, enabled={}, seed=False)
        self.assertEqual(self._real_ids(out), {"a"})

    def test_mock_always_present(self):
        out = self._agents([_entry("a")], {"a": {"installed": False}}, enabled={})
        self.assertTrue(any(a["id"].startswith("mock-") for a in out))


if __name__ == "__main__":
    unittest.main()
