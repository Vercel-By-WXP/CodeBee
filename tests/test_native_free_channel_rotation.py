"""原生免费通道修复回归：多模型轮换链、small_model 钉扎、opencode 退出码 1 salvage。

2026-10-21 实案：opencode 免费池单模型（deepseek-v4.1-flash-free）独扛全部调用，
上游间歇「Endpoint is unavailable」时整通道 82% 记失败；主回答流式完成后收尾的
会话标题小模型调用失败还会把整个进程拖成退出码 1（opencode#30662 同款），
完整产出被按失败丢弃。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import manager, modelhub, runner


_POOL = [
    {"name": "opencode/deepseek-v4.1-flash-free", "enabled": True, "priority": 1},
    {"name": "opencode/cat-jam-free", "enabled": True, "priority": 2},
    {"name": "opencode/glm-5.3-flash-free", "enabled": True, "priority": 3},
    {"name": "opencode/longcat-2.5-preview-free", "enabled": True, "priority": 4},
]


class AutoWireRotationTests(unittest.TestCase):
    def test_auto_wire_binds_up_to_three_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            path.write_text(json.dumps({
                "providers": [{
                    "id": "prov-48", "name": "OpenCode 原生免费模型",
                    "source": "opencode", "source_id": "opencode:native-free",
                    "credential_mode": "cli", "native_cli": "opencode",
                    "models": _POOL,
                }],
                "bindings": {},
            }), encoding="utf-8")
            with mock.patch.object(modelhub, "_FILE", path), \
                 mock.patch.object(modelhub, "_NATIVE_AUTO_CHANNELS",
                                   (("opencode", "opencode"),)), \
                 mock.patch.object(modelhub, "_native_channel_available",
                                   lambda sid: True):
                out = modelhub.auto_wire_native_channels()

            data = json.loads(path.read_text(encoding="utf-8"))
            chain = data["bindings"]["opencode"]["chain"]
            self.assertEqual(
                [c["model"] for c in chain],
                ["opencode/deepseek-v4.1-flash-free", "opencode/cat-jam-free",
                 "opencode/glm-5.3-flash-free"])
            self.assertTrue(all(c["provider_id"] == "prov-48" for c in chain))
            self.assertIn(" / ", out["opencode"])

    def test_auto_wire_keeps_single_entry_when_pool_has_one_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            path.write_text(json.dumps({
                "providers": [{
                    "id": "prov-46", "name": "MiMo Code 原生免费通道",
                    "source": "mimo", "source_id": "mimo:native-auto",
                    "models": [{"name": "mimo/mimo-auto", "enabled": True}],
                }],
                "bindings": {},
            }), encoding="utf-8")
            with mock.patch.object(modelhub, "_FILE", path), \
                 mock.patch.object(modelhub, "_NATIVE_AUTO_CHANNELS",
                                   (("mimo", "mimo-code"),)), \
                 mock.patch.object(modelhub, "_native_channel_available",
                                   lambda sid: True):
                modelhub.auto_wire_native_channels()

            data = json.loads(path.read_text(encoding="utf-8"))
            chain = data["bindings"]["mimo-code"]["chain"]
            self.assertEqual(len(chain), 1)
            self.assertEqual(chain[0]["model"], "mimo/mimo-auto")


class OpencodeSmallModelTests(unittest.TestCase):
    def test_prefers_non_main_reliable_family(self):
        prov = {"models": _POOL}
        self.assertEqual(
            manager._opencode_small_model("opencode/deepseek-v4.1-flash-free", prov),
            "opencode/glm-5.3-flash-free")
        self.assertEqual(
            manager._opencode_small_model("opencode/glm-5.3-flash-free", prov),
            "opencode/longcat-2.5-preview-free")

    def test_single_model_pool_yields_no_pin(self):
        prov = {"models": [{"name": "mimo/mimo-auto", "enabled": True}]}
        self.assertEqual(manager._opencode_small_model("mimo/mimo-auto", prov), "")

    def test_falls_back_to_first_other_name(self):
        prov = {"models": [{"name": "a-free"}, {"name": "b-free"}]}
        self.assertEqual(manager._opencode_small_model("a-free", prov), "b-free")

    def test_sync_writes_small_model_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "opencode.json"
            cfg.write_text(json.dumps({
                "model": "orch/glm-5.3",
                "provider": {"orch": {"npm": "@ai-sdk/openai-compatible"}},
            }), encoding="utf-8")
            with mock.patch.object(manager, "tmp_data_no_home_write",
                                   return_value=False), \
                 mock.patch.object(manager, "_canonical",
                                   lambda p: str(Path(tmp).resolve())), \
                 mock.patch.object(manager, "_opencode_config_candidates",
                                   lambda entry: [str(cfg)]):
                err = manager._sync_opencode_settings(
                    {}, "opencode/deepseek-v4.1-flash-free",
                    {"credential_mode": "cli", "native_cli": "opencode",
                     "native_models": True, "models": _POOL})
            self.assertEqual(err, None)
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(data["model"], "opencode/deepseek-v4.1-flash-free")
            self.assertEqual(data["small_model"], "opencode/glm-5.3-flash-free")
            self.assertNotIn("orch", data.get("provider", {}))


def _res(**over):
    base = {"ok": False, "exit_code": 1, "stdout": "正" * 400, "stderr": "",
            "duration": 12.0, "cancelled": False, "timed_out": False,
            "stalled": False, "deadline_exceeded": False,
            "output_truncated": False, "abort_marker": None}
    base.update(over)
    return base


class OpencodeSalvageTests(unittest.TestCase):
    def test_complete_run_with_title_failure_is_salvaged(self):
        agent = {"id": "opencode", "kind": "opencode", "mode": "real",
                 "command": "opencode",
                 "model": "opencode/glm-5.3-flash-free",
                 "orch": {"timeout_ms": 1000}}
        with mock.patch.object(runner, "_build_call",
                               return_value=(["opencode"], "p", "p", [])), \
             mock.patch.object(runner, "run_process", return_value=_res()):
            result = runner.run_agent(
                agent, "prompt", timeout=1,
                command_context={"run_id": "r-salvage", "role": "critique-c1"},
                checkpoint_run_id="r-salvage")
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("salvaged"))
        self.assertEqual(result["text"], "正" * 400)

    def test_upstream_error_in_tail_is_not_salvaged(self):
        stdout = "正" * 300 + "\nError: Upstream request failed: Endpoint is unavailable"
        with mock.patch.object(runner, "_build_call",
                               return_value=(["opencode"], "p", "p", [])), \
             mock.patch.object(runner, "run_process",
                               return_value=_res(stdout=stdout)):
            result = runner.run_agent(
                {"id": "opencode", "kind": "opencode", "mode": "real",
                 "command": "opencode",
                 "model": "opencode/deepseek-v4.1-flash-free",
                 "orch": {"timeout_ms": 1000}},
                "prompt", timeout=1,
                command_context={"run_id": "r-salvage2", "role": "draft"},
                checkpoint_run_id="r-salvage2")
        self.assertFalse(result["ok"])
        self.assertNotIn("salvaged", result)

    def test_timeout_and_short_output_are_not_salvaged(self):
        self.assertEqual(runner._opencode_salvage_complete_run(
            _res(timed_out=True)), "")
        self.assertEqual(runner._opencode_salvage_complete_run(
            _res(stdout="短")), "")
        self.assertEqual(runner._opencode_salvage_complete_run(
            _res(exit_code=0)), "")

    def test_stderr_error_signal_blocks_salvage(self):
        self.assertEqual(runner._opencode_salvage_complete_run(
            _res(stderr="Error: stream disconnected")), "")


if __name__ == "__main__":
    unittest.main()
