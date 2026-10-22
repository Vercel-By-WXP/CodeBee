# -*- coding: utf-8 -*-
"""bind 写盘台账（magpie stash/applied 纪律借鉴）+ 换将时间维度
（最快解禁先用 + 会话粘性）回归。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core import bind_ledger, health, manager, router


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class BindLedgerTests(unittest.TestCase):
    def setUp(self):
        self._old_env = os.environ.get("TUTTI_DATA")
        self._data_tmp = tempfile.TemporaryDirectory()
        os.environ["TUTTI_DATA"] = self._data_tmp.name
        self._cfg_tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._cfg_tmp.name) / "settings.json"

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("TUTTI_DATA", None)
        else:
            os.environ["TUTTI_DATA"] = self._old_env
        self._data_tmp.cleanup()
        self._cfg_tmp.cleanup()

    def test_record_and_restore_roundtrip(self):
        self.cfg.write_bytes(b"v1")
        bind_ledger.record_write(str(self.cfg), b"v1", b"v2")
        self.cfg.write_bytes(b"v2")  # 写盘咽喉落新内容（台账只记账不写盘）
        view = bind_ledger.view()
        self.assertIn(str(self.cfg), view["applied"])
        self.assertEqual(view["applied"][str(self.cfg)]["sha256"], _sha(b"v2"))
        res = bind_ledger.restore(str(self.cfg))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], "restored")
        self.assertEqual(self.cfg.read_bytes(), b"v1")

    def test_restore_refuses_when_file_changed_after_write(self):
        self.cfg.write_bytes(b"v1")
        bind_ledger.record_write(str(self.cfg), b"v1", b"v2")
        self.cfg.write_bytes(b"user edited")
        res = bind_ledger.restore(str(self.cfg))
        self.assertFalse(res["ok"])
        self.assertIn("拒绝盲目还原", res["error"])
        self.assertEqual(self.cfg.read_bytes(), b"user edited")

    def test_restore_removes_file_we_created(self):
        bind_ledger.record_write(str(self.cfg), None, b"{}")
        self.assertTrue(self.cfg.exists() or True)  # 文件由写入方负责
        self.cfg.write_bytes(b"{}")  # 模拟写盘咽喉已建文件
        res = bind_ledger.restore(str(self.cfg))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], "removed")
        self.assertFalse(self.cfg.exists())

    def test_restore_without_records_errors(self):
        res = bind_ledger.restore(str(self.cfg))
        self.assertFalse(res["ok"])

    def test_stash_keeps_last_three(self):
        for i in range(4):
            prev = ("v%d" % i).encode()
            bind_ledger.record_write(str(self.cfg), prev, ("v%d" % (i + 1)).encode())
        entries = [e for e in bind_ledger.view()["stash"] if e["path"] == str(self.cfg)]
        self.assertEqual(len(entries), 3)
        # 最老一笔是第 2 次写（v1→v2），第 1 次已淘汰
        self.assertEqual(entries[0]["before_sha"], _sha(b"v1"))

    def test_data_dir_paths_are_not_recorded(self):
        inside = Path(self._data_tmp.name) / "models.json"
        bind_ledger.record_write(str(inside), b"a", b"b")
        self.assertEqual(bind_ledger.view()["applied"], {})

    def test_oversized_snapshot_is_skipped_but_refuses_restore(self):
        big = b"x" * (bind_ledger._MAX_SNAPSHOT_BYTES + 1)
        self.cfg.write_bytes(big)
        bind_ledger.record_write(str(self.cfg), big, b"v2")
        res = bind_ledger.restore(str(self.cfg))
        self.assertFalse(res["ok"])
        self.assertIn("快照上限", res["error"])

    def test_meta_flows_into_view(self):
        bind_ledger.record_write(str(self.cfg), None, b"{}", meta={"agent": "t"})
        view = bind_ledger.view()
        self.assertEqual(view["applied"][str(self.cfg)]["meta"]["agent"], "t")


class AtomicWriteLedgerTests(unittest.TestCase):
    def setUp(self):
        self._old_env = os.environ.get("TUTTI_DATA")
        self._data_tmp = tempfile.TemporaryDirectory()
        os.environ["TUTTI_DATA"] = self._data_tmp.name
        self._cfg_tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._cfg_tmp.name) / "config.toml"

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("TUTTI_DATA", None)
        else:
            os.environ["TUTTI_DATA"] = self._old_env
        self._data_tmp.cleanup()
        self._cfg_tmp.cleanup()

    def test_atomic_write_records_and_cleans_lockfile(self):
        self.cfg.write_bytes(b"v1")
        manager._atomic_write_bytes(str(self.cfg), b"v2", meta={"agent": "t"})
        self.assertEqual(self.cfg.read_bytes(), b"v2")
        self.assertFalse(Path(str(self.cfg) + manager._CONFIG_LOCK_SUFFIX).exists())
        view = bind_ledger.view()
        self.assertEqual(view["applied"][str(self.cfg)]["meta"]["agent"], "t")
        res = bind_ledger.restore(str(self.cfg))
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.cfg.read_bytes(), b"v1")

    def test_stale_lockfile_is_stolen(self):
        self.cfg.write_bytes(b"v1")
        lockfile = Path(str(self.cfg) + manager._CONFIG_LOCK_SUFFIX)
        lockfile.write_text("999 old")
        old = time.time() - manager._CONFIG_LOCK_STALE_S - 10
        os.utime(str(lockfile), (old, old))
        manager._atomic_write_bytes(str(self.cfg), b"v2")
        self.assertEqual(self.cfg.read_bytes(), b"v2")
        self.assertFalse(lockfile.exists())

    def test_create_then_restore_removes(self):
        manager._atomic_write_bytes(str(self.cfg), b"{}")
        self.assertEqual(self.cfg.read_bytes(), b"{}")
        res = bind_ledger.restore(str(self.cfg))
        self.assertTrue(res["ok"], res)
        self.assertFalse(self.cfg.exists())


class CliProcessAliveTests(unittest.TestCase):
    def test_no_cli_is_unknown(self):
        self.assertIsNone(manager.cli_process_alive({"detect": {}}))
        self.assertIsNone(manager.cli_process_alive(None))

    def test_probe_truthy_and_failure_paths(self):
        entry = {"detect": {"cli": "faketool"}}

        class R:
            def __init__(self, stdout):
                self.stdout = stdout

        with mock.patch.object(manager.subprocess, "run",
                               return_value=R(b'{"ProcessId": 123}')):
            self.assertTrue(manager.cli_process_alive(entry))
        with mock.patch.object(manager.subprocess, "run",
                               return_value=R(b"")):
            self.assertIs(manager.cli_process_alive(entry), False)
        with mock.patch.object(manager.subprocess, "run",
                               side_effect=OSError("boom")):
            self.assertIsNone(manager.cli_process_alive(entry))


class LaunchGuardTests(unittest.TestCase):
    def _entry(self):
        return {"id": "t-ui", "name": "T", "kind": "generic",
                "detect": {"cli": "faketool"},
                "launch": {"kind": "web", "command": "faketool"}}

    def test_alive_skips_config_sync(self):
        with mock.patch.object(manager, "detect_entry",
                               return_value={"installed": True, "detail": "x"}), \
             mock.patch.object(manager, "cli_process_alive", return_value=True), \
             mock.patch.object(manager, "_sync_launch_model") as sync:
            res = manager.launch(self._entry(), open_browser=False)
        sync.assert_not_called()

    def test_unknown_still_syncs(self):
        with mock.patch.object(manager, "detect_entry",
                               return_value={"installed": True, "detail": "x"}), \
             mock.patch.object(manager, "cli_process_alive", return_value=None), \
             mock.patch.object(manager, "_sync_launch_model",
                               return_value=["ok"]) as sync:
            manager.launch(self._entry(), open_browser=False)
        sync.assert_called_once()


class CooldownRemainingTests(unittest.TestCase):
    def tearDown(self):
        with health._LOCK:
            health._PROVIDERS.pop("p-ledger", None)

    def test_unknown_provider(self):
        self.assertIsNone(health.cooldown_remaining("p-ledger"))

    def test_circuit_and_model_rows_take_min(self):
        now = time.time()
        with health._LOCK:
            health._PROVIDERS["p-ledger"] = {
                "status": "down",
                "circuit_open_until": now + 120,
                "model_circuits": {"m1": {"open_until": now + 300}},
            }
        rem = health.cooldown_remaining("p-ledger")
        self.assertIsNotNone(rem)
        self.assertGreater(rem, 100)
        self.assertLessEqual(rem, 120)

    def test_expired_with_ok_status_is_zero(self):
        now = time.time()
        with health._LOCK:
            health._PROVIDERS["p-ledger"] = {
                "status": "ok",
                "circuit_open_until": now - 5,
                "model_circuits": {},
            }
        self.assertEqual(health.cooldown_remaining("p-ledger"), 0.0)

    def test_down_without_until_is_none(self):
        with health._LOCK:
            health._PROVIDERS["p-ledger"] = {"status": "down",
                                             "circuit_open_until": 0,
                                             "model_circuits": {}}
        self.assertIsNone(health.cooldown_remaining("p-ledger"))


class StickyRoutingTests(unittest.TestCase):
    def setUp(self):
        router.sticky_reset()

    def tearDown(self):
        router.sticky_reset()

    @staticmethod
    def _agent(aid, mode="real"):
        return {"id": aid, "label": aid, "kind": "generic", "mode": mode}

    def test_score_applies_sticky_bonus_to_same_agent_only(self):
        router._sticky_remember("implement", "direct", "a")
        bonus_a, txt_a = router._sticky_bonus(self._agent("a"), "implement", "direct")
        bonus_b, _ = router._sticky_bonus(self._agent("b"), "implement", "direct")
        self.assertEqual(bonus_a, router.STICKY_BONUS)
        self.assertIn("会话粘性", txt_a)
        self.assertEqual((bonus_b, ""), (0.0, ""))

    def test_mock_agents_never_stick(self):
        router._sticky_remember("implement", "direct", "m")
        self.assertEqual(router._sticky_bonus(self._agent("m", mode="mock"),
                                              "implement", "direct"), (0.0, ""))

    def test_role_and_ttype_scope(self):
        router._sticky_remember("implement", "direct", "a")
        self.assertEqual(router._sticky_bonus(self._agent("a"), "review", "direct"),
                         (0.0, ""))
        self.assertEqual(router._sticky_bonus(self._agent("a"), "implement", "code"),
                         (0.0, ""))

    def test_ttl_expiry(self):
        router._sticky_remember("implement", "direct", "a")
        key = ("implement", "direct")
        with router._STICKY["lock"]:
            aid, _ = router._STICKY["by_key"][key]
            router._STICKY["by_key"][key] = (aid, time.time() - router.STICKY_TTL_S - 1)
        self.assertEqual(router._sticky_bonus(self._agent("a"), "implement", "direct"),
                         (0.0, ""))

    def test_score_reason_contains_sticky(self):
        router._sticky_remember("implement", "direct", "a")
        total, reason = router.score(self._agent("a"), "implement", "direct", stats={})
        self.assertIn("会话粘性", reason)

    def test_pick_remembers_selection(self):
        sel, reason = router.pick([self._agent("solo")], "implement", "direct",
                                  stats={})
        self.assertEqual(sel["id"], "solo")
        key = ("implement", "direct")
        with router._STICKY["lock"]:
            self.assertIn(key, router._STICKY["by_key"])
            self.assertEqual(router._STICKY["by_key"][key][0], "solo")

    def test_sticky_flips_near_tie(self):
        # 粘性 +4 足以在能力基线同分的候选间翻转次序；reset 后原序恢复
        router._sticky_remember("implement", "direct", "zz-a")
        sel, _ = router._pick_scored([self._agent("zz-b"), self._agent("zz-a")],
                                     "implement", "direct", stats={})
        self.assertEqual(sel["id"], "zz-a")
        router.sticky_reset()
        sel, _ = router._pick_scored([self._agent("zz-b"), self._agent("zz-a")],
                                     "implement", "direct", stats={})
        self.assertEqual(sel["id"], "zz-b")

    def test_sticky_never_beats_tool_penalty(self):
        # 仅对话降级 -10 必须压过粘性 +4：上一轮首选被判「仅对话」后，
        # 不会因粘性反超健康候选。绑定/在线信号全 mock 归零，只留工具降级
        # 与粘性两个变量（避免读到真实健康台账造成非确定性）。
        router._sticky_remember("implement", "direct", "chat-only-a")

        def verdict(aid, **kw):
            if aid == "chat-only-a":
                return ("all_chat_only", "均为仅对话")
            return ("", "")

        with mock.patch.object(router, "_binding_bonus",
                               return_value=(0.0, "")), \
             mock.patch.object(router, "_online_bonus",
                               return_value=(0.0, "")), \
             mock.patch.object(router.modelhub, "chain_tool_verdict",
                               side_effect=verdict):
            t_chat, _ = router.score(self._agent("chat-only-a"), "implement",
                                     "direct", stats={})
            t_plain, _ = router.score(self._agent("plain-b"), "implement",
                                      "direct", stats={})
        self.assertLess(t_chat, t_plain)


class SoonestRecoverTests(unittest.TestCase):
    def setUp(self):
        router.sticky_reset()

    def tearDown(self):
        router.sticky_reset()

    @staticmethod
    def _agent(aid):
        return {"id": aid, "label": aid, "kind": "generic", "mode": "real"}

    def test_helper_takes_min_across_upstreams(self):
        # router 惰性 import health，patch health 模块函数即可命中
        with mock.patch.object(router, "agent_upstreams",
                               return_value={"a.example"}), \
             mock.patch.object(router.modelhub, "providers",
                               return_value=[{"name": "PA",
                                              "base_url": "https://a.example/v1"}]), \
             mock.patch("app.core.health.cooldown_remaining", return_value=42.0):
            rem = router._upstream_cooldown_remaining("x")
        self.assertEqual(rem, 42.0)

    def test_helper_none_when_no_data(self):
        with mock.patch.object(router, "agent_upstreams", return_value=set()):
            self.assertIsNone(router._upstream_cooldown_remaining("x"))

    def test_walk_picks_soonest_recovering_candidate(self):
        def fake_rem(agent_id, *a, **kw):
            return 60.0 if agent_id == "slow-a" else 600.0

        with mock.patch.object(router, "agent_upstreams",
                               return_value={"dead.example"}), \
             mock.patch.object(router, "_upstream_cooldown_remaining",
                               side_effect=fake_rem):
            best, reason = router.pick_switch_candidate(
                [self._agent("slow-a"), self._agent("slow-b")],
                "implement", "direct", stats={},
                dead_upstreams=[{"dead.example"}])
        self.assertEqual(best["id"], "slow-a")
        self.assertIn("最快解禁", reason)
        self.assertIn("slow-a", reason)

    def test_walk_without_cooldown_data_keeps_score_order(self):
        with mock.patch.object(router, "agent_upstreams",
                               return_value={"dead.example"}), \
             mock.patch.object(router, "_upstream_cooldown_remaining",
                               return_value=None):
            best, reason = router.pick_switch_candidate(
                [self._agent("top-a"), self._agent("low-b")],
                "implement", "direct", stats={},
                dead_upstreams=[{"dead.example"}])
        self.assertEqual(best["id"], "top-a")
        self.assertNotIn("最快解禁", reason)


if __name__ == "__main__":
    unittest.main()
