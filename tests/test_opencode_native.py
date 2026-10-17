# -*- coding: utf-8 -*-
"""OpenCode first-party free models use the CLI's own auth/session."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest import mock

from base import BaseTest


@contextlib.contextmanager
def _stack(patches):
    """同时进入一组 patch，退出时按反序还原。"""
    with contextlib.ExitStack() as es:
        for p in patches:
            es.enter_context(p)
        yield


class TestOpenCodeNative(BaseTest):
    def _native(self, model="opencode/deepseek-v4.1-flash-free"):
        from app.core import modelhub
        data = {"providers": [{
            "id": "prov-1", "name": "OpenCode 原生免费模型",
            "protocol": "openai", "base_url": "https://opencode.ai",
            "api_key": "", "model": model,
            "models": [{"name": model, "enabled": True, "priority": 1}],
            "credential_mode": "cli", "native_cli": "opencode",
            "native_models": True,
        }], "bindings": {}}
        modelhub._FILE.write_text(json.dumps(data), encoding="utf-8")
        return modelhub

    def test_discovery_marks_free_models_as_native_cli(self):
        from app.core import modelhub
        with mock.patch.object(modelhub.shutil, "which", return_value="opencode"), \
             mock.patch.object(modelhub.subprocess, "run",
                               return_value=mock.Mock(stdout=(
                                   "opencode/deepseek-v4.1-flash-free\n"
                                   "opencode/glm-5-free\n"
                                   "openai/gpt-4o\n"))):
            got = modelhub._opencode_native_models()
        self.assertEqual(got, ["opencode/deepseek-v4.1-flash-free",
                               "opencode/glm-5-free"])

    def test_native_binding_is_credential_free_and_cli_scoped(self):
        modelhub = self._native()
        modelhub.set_binding("opencode", chain=[
            {"provider_id": "prov-1", "model": "opencode/deepseek-v4.1-flash-free"}])
        got = modelhub.resolve_binding("opencode")
        self.assertIsNotNone(got)
        self.assertEqual(got["model"], "opencode/deepseek-v4.1-flash-free")
        self.assertEqual(got["env"], {})
        self.assertTrue(got["call_chain"][0]["no_cred"])
        self.assertIsNone(modelhub.resolve_binding("claude-code"))
        pick, note = modelhub.launch_pick("opencode", ("anthropic", "openai"))
        self.assertIsNone(note)
        self.assertEqual(pick["model"], "opencode/deepseek-v4.1-flash-free")

    def test_native_provider_enters_automatic_opencode_recommendation(self):
        modelhub = self._native()
        rec = modelhub.recommend_binding("opencode")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["model"], "opencode/deepseek-v4.1-flash-free")
        self.assertEqual(rec["env"], {})
        self.assertTrue(rec["call_chain"][0]["no_cred"])
        self.assertIsNone(modelhub.recommend_binding("codex-cli"))

    def test_runner_keeps_complete_provider_model_id(self):
        from app.core import runner
        argv, _stdin, _prompt, _tmp = runner._build_call(
            {"command": "opencode"}, "opencode", "", False,
            "opencode/deepseek-v4.1-flash-free", "hello")
        self.assertEqual(argv[-2:], ["--model", "opencode/deepseek-v4.1-flash-free"])

    def test_native_sync_preserves_model_and_does_not_create_orch(self):
        from app.core import manager
        home = self.tmp / "home"
        config = home / ".config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"provider": {"orch": {"options": {"apiKey": "old"}},
                                      "github": {"x": 1}},
                                      "model": "old/model"}), encoding="utf-8")
        old_home = mock.patch.object(manager.os.path, "expanduser",
                                      side_effect=lambda p: str(home) if p == "~" else
                                      (str(home / p[2:]) if p.startswith("~/") else p))
        prov = {"name": "OpenCode 原生免费模型", "protocol": "openai",
                "base_url": "https://opencode.ai", "api_key": "",
                "credential_mode": "cli", "native_cli": "opencode",
                "native_models": True}
        with old_home:
            err = manager._sync_opencode_settings(
                {"id": "opencode", "config": {"path": "~/.config/opencode/opencode.json"}},
                "opencode/deepseek-v4.1-flash-free", prov)
        self.assertIsNone(err, err)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "opencode/deepseek-v4.1-flash-free")
        self.assertNotIn("orch", data.get("provider", {}))
        self.assertEqual(data["provider"]["github"], {"x": 1})

    def test_gemini_oauth_discovery_is_separate_from_api_key(self):
        from app.core import modelhub
        with mock.patch.object(modelhub, "GEMINI_OAUTH", str(self.tmp / "oauth_creds.json")), \
             mock.patch.object(modelhub, "GEMINI_DIR", str(self.tmp / "gemini")), \
             mock.patch.object(modelhub, "_read_json", side_effect=lambda p: {
                 str(self.tmp / "oauth_creds.json"): {"refresh_token": "r1"},
                 str(self.tmp / "gemini" / "settings.json"): {"model": "flash"},
             }.get(str(p))):
            Path(modelhub.GEMINI_OAUTH).write_text("{}", encoding="utf-8")
            Path(modelhub.GEMINI_DIR).mkdir(exist_ok=True)
            got = modelhub._src_gemini()
        self.assertEqual(len(got["providers"]), 1)
        p = got["providers"][0]
        self.assertEqual(p["native_cli"], "gemini-cli")
        self.assertEqual(p["api_key"], "")
        self.assertIn("flash", [m["name"] for m in p["models"]])

    def test_kimi_oauth_discovery_reads_managed_aliases(self):
        from app.core import modelhub
        cred = self.tmp / "credentials"
        cred.mkdir()
        cfg = self.tmp / "config.toml"
        (cred / "account.json").write_text(json.dumps({"access_token": "a1"}), encoding="utf-8")
        cfg.write_text('default_model = "kimi-free"\n\n[models."kimi-free"]\nprovider = "managed:kimi-code"\nmodel = "kimi-free"\n', encoding="utf-8")
        with mock.patch.object(modelhub, "KIMI_CREDENTIALS", str(cred)), \
             mock.patch.object(modelhub, "KIMI_CONFIG", str(cfg)):
            got = modelhub._src_kimi()
        self.assertEqual(len(got["providers"]), 1)
        p = got["providers"][0]
        self.assertEqual(p["native_cli"], "kimi-code")
        self.assertEqual(p["api_key"], "")
        self.assertEqual(p["model"], "kimi-free")

    def test_qwen_oauth_discovery_is_cli_owned_and_not_free(self):
        from app.core import modelhub
        oauth = self.tmp / "oauth_creds.json"
        oauth.write_text(json.dumps({
            "access_token": "access",
            "refresh_token": "refresh",
            "token_type": "Bearer",
            "expiry_date": 4102444800000,
        }), encoding="utf-8")
        settings = self.tmp / "settings.json"
        settings.write_text(json.dumps({"modelProviders": {}}), encoding="utf-8")
        with mock.patch.object(modelhub, "QWEN_OAUTH", str(oauth)), \
             mock.patch.object(modelhub, "QWEN_SETTINGS", str(settings)):
            got = modelhub._src_qwen()
        self.assertEqual(len(got["providers"]), 1)
        p = got["providers"][0]
        self.assertEqual(p["native_cli"], "qwencode")
        self.assertEqual(p["model"], "coder-model")
        self.assertEqual(p["api_key"], "")
        self.assertNotIn("free_tier", p)
        self.assertIn("免费额度已停用", got["note"])

    def test_qwen_native_binding_uses_cli_session(self):
        from app.core import modelhub
        self._native_qwen = modelhub
        data = {"providers": [{
            "id": "qwen-native", "name": "Qwen Code 原生 OAuth",
            "protocol": "openai", "base_url": "https://chat.qwen.ai",
            "api_key": "", "model": "coder-model",
            "credential_mode": "cli", "native_cli": "qwencode",
            "native_models": True,
            "models": [{"name": "coder-model", "enabled": True, "priority": 1}],
        }], "bindings": {}}
        modelhub._FILE.write_text(json.dumps(data), encoding="utf-8")
        modelhub.set_binding("qwencode", chain=[
            {"provider_id": "qwen-native", "model": "coder-model"}])
        got = modelhub.resolve_binding("qwencode")
        self.assertIsNotNone(got)
        self.assertEqual(got["model"], "coder-model")
        self.assertEqual(got["env"], {})
        self.assertTrue(got["call_chain"][0]["no_cred"])

    def test_native_cli_runner_adds_model_flag(self):
        from app.core import runner
        argv, _stdin, _prompt, _tmp = runner._build_call(
            {"id": "gemini-cli", "command": "gemini", "argv_template": ["-p", "{prompt}"]},
            "generic", "", False, "flash", "hello")
        self.assertIn("-m", argv)
        self.assertEqual(argv[-2:], ["-p", "hello"])

    def test_grok_oauth_discovery_is_cli_scoped(self):
        from app.core import modelhub
        auth = self.tmp / "auth.json"
        cfg = self.tmp / "config.toml"
        auth.write_text(json.dumps({"access_token": "g1"}), encoding="utf-8")
        cfg.write_text('[models]\ndefault = "grok-4.6"\n\n'
                       '[model.first-party]\nmodel = "grok-4.7"\n', encoding="utf-8")
        with mock.patch.object(modelhub, "GROK_AUTH", str(auth)), \
             mock.patch.object(modelhub, "GROK_CONFIG", str(cfg)):
            got = modelhub._src_grok()
        self.assertEqual(len(got["providers"]), 1)
        p = got["providers"][0]
        self.assertEqual(p["native_cli"], "grok-build")
        self.assertEqual(p["api_key"], "")
        self.assertEqual(p["model"], "grok-4.6")
        self.assertEqual([m["name"] for m in p["models"]],
                         ["grok-4.6", "grok-4.7", "grok-4.5"])

    def test_grok_auth_json_documented_issuer_key_shape_is_detected(self):
        from app.core import modelhub
        auth = self.tmp / "auth-key.json"
        auth.write_text(json.dumps({
            "https://accounts.x.ai/sign-in": {"key": "session-token"}
        }), encoding="utf-8")
        with mock.patch.object(modelhub, "GROK_AUTH", str(auth)), \
             mock.patch.object(modelhub, "GROK_CONFIG", str(self.tmp / "missing.toml")):
            got = modelhub._src_grok()
        self.assertEqual(len(got["providers"]), 1)
        self.assertEqual(got["providers"][0]["native_cli"], "grok-build")

    def test_grok_native_sync_does_not_inject_empty_gateway_credentials(self):
        from app.core import manager
        entry = {"id": "grok-build", "name": "Grok Build"}
        binding = {"env": {}, "provider": {"api_key": "",
                                              "credential_mode": "cli",
                                              "native_cli": "grok-build",
                                              "native_models": True}}
        note = manager._sync_agent_injection(entry, binding)
        self.assertIn("CLI 自带登录", note)
        self.assertNotIn("apiKey", note)
        self.assertNotIn("orch", note)

    def test_grok_runner_adds_model_flag(self):
        from app.core import runner
        argv, _stdin, _prompt, _tmp = runner._build_call(
            {"id": "grok-build", "command": "grok", "argv_template": ["-p", "{prompt}"]},
            "generic", "", False, "grok-4.6", "hello")
        self.assertEqual(argv[-2:], ["-p", "hello"])
        self.assertIn("-m", argv)

    def test_grok_native_provider_is_only_usable_by_grok(self):
        from app.core import modelhub
        data = {"providers": [{
            "id": "grok-1", "name": "Grok Build 原生 OAuth",
            "protocol": "openai", "base_url": "https://grok.com", "api_key": "",
            "model": "grok-4.6",
            "models": [{"name": "grok-4.6", "enabled": True, "priority": 1}],
            "credential_mode": "cli", "native_cli": "grok-build", "native_models": True,
        }], "bindings": {}}
        modelhub._FILE.write_text(json.dumps(data), encoding="utf-8")
        modelhub.set_binding("grok-build", chain=[{"provider_id": "grok-1", "model": "grok-4.6"}])
        self.assertIsNotNone(modelhub.resolve_binding("grok-build"))
        self.assertIsNone(modelhub.resolve_binding("opencode"))

    def test_pi_oauth_discovery_is_provider_scoped_and_not_free(self):
        from app.core import modelhub
        auth = self.tmp / "pi-auth.json"
        auth.write_text(json.dumps({
            "openai-codex": {"type": "oauth", "refresh": "r1", "access": "a1",
                              "expires": 4102444800000},
            "deepseek": {"type": "api_key", "key": "sk-test"},
        }), encoding="utf-8")
        with mock.patch.object(modelhub, "PI_AUTH", str(auth)), \
             mock.patch.object(modelhub.shutil, "which", return_value=None):
            got = modelhub._src_pi()
        self.assertEqual(len(got["providers"]), 1)
        p = got["providers"][0]
        self.assertEqual(p["native_cli"], "pi")
        self.assertEqual(p["native_provider"], "openai-codex")
        self.assertEqual(p["api_key"], "")
        self.assertFalse(any("free" in m["name"].lower() for m in p["models"]))

    def test_pi_runner_passes_native_provider_and_local_model(self):
        from app.core import runner
        argv, _stdin, _prompt, _tmp = runner._build_call(
            {"id": "pi", "command": "pi", "argv_template": ["-p", "{prompt}"],
             "provider": {"native_provider": "openai-codex"}},
            "generic", "", False, "openai-codex/gpt-6.1-sol", "hello")
        i = argv.index("--provider")
        self.assertEqual(argv[i:i + 4], ["--provider", "openai-codex", "--model", "gpt-6.1-sol"])

    def test_pi_native_sync_only_selects_provider_without_gateway_block(self):
        from app.core import manager
        home = self.tmp / "home"
        settings = home / ".pi" / "agent" / "settings.json"
        with mock.patch.object(manager.os.path, "expanduser",
                               side_effect=lambda p: str(home) if p == "~" else
                               (str(home / p[2:]) if p.startswith("~/") else p)), \
             mock.patch.object(manager, "tmp_data_no_home_write", return_value=False):
            err = manager._sync_pi_settings(
                {"id": "pi", "config": {"path": "~/.pi/agent/settings.json"}},
                "openai-codex/gpt-6.1-sol",
                {"credential_mode": "cli", "native_cli": "pi",
                 "native_provider": "openai-codex", "native_models": True})
        self.assertIsNone(err, err)
        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(data, {"defaultProvider": "openai-codex", "defaultModel": "gpt-6.1-sol"})

    def test_pi_successful_empty_model_listing_does_not_use_fallback(self):
        from app.core import modelhub
        with mock.patch.object(modelhub.shutil, "which", return_value="pi"), \
             mock.patch.object(modelhub.subprocess, "run",
                               return_value=mock.Mock(returncode=0,
                                                       stdout="No models available.\n")):
            self.assertEqual(modelhub._pi_native_models("openai-codex"), [])

    def test_pi_rejects_incomplete_or_expired_access_only_oauth(self):
        from app.core import modelhub
        self.assertFalse(modelhub._pi_oauth_usable({"type": "oauth", "access": "a1"}))
        self.assertFalse(modelhub._pi_oauth_usable({"type": "oauth", "access": "a1",
                                                    "refresh": "", "expires": 4102444800000}))
        self.assertTrue(modelhub._pi_oauth_usable({"type": "oauth", "access": "a1",
                                                   "refresh": "r1", "expires": 1}))
        self.assertTrue(modelhub._pi_oauth_usable({"type": "oauth", "access": "a1",
                                                   "refresh": "r1", "expires": 1}))

    def test_native_generic_flags_follow_windows_cmd_wrapper(self):
        from app.core import runner
        with mock.patch.object(runner, "resolve_command",
                               return_value=["cmd", "/c", r"C:\\npm\\pi.cmd"]):
            argv, _stdin, _prompt, _tmp = runner._build_call(
                {"id": "pi", "command": "pi", "argv_template": ["-p", "{prompt}"],
                 "provider": {"native_provider": "openai-codex"}},
                "generic", "", False, "openai-codex/gpt-6.1-sol", "hello")
        self.assertEqual(argv[:7], ["cmd", "/c", r"C:\\npm\\pi.cmd",
                                    "--provider", "openai-codex", "--model", "gpt-6.1-sol"])

    def test_codex_chatgpt_oauth_is_native_and_keyless(self):
        from app.core import modelhub
        cfg = self.tmp / "codex.toml"
        auth = self.tmp / "auth.json"
        cfg.write_text('model = "gpt-5.5"\n', encoding="utf-8")
        auth.write_text(json.dumps({"auth_mode": "chatgpt", "access_token": "a",
                                    "refresh_token": "r"}), encoding="utf-8")
        with mock.patch.object(modelhub, "CODEX_DIR", str(self.tmp)):
            got = modelhub._src_codex()
        p = next(p for p in got["providers"] if p.get("native_cli") == "codex-cli")
        self.assertEqual(p["api_key"], "")
        self.assertTrue(p["native_models"])

    def test_codex_chatgpt_oauth_is_found_without_config_toml(self):
        from app.core import modelhub
        auth = self.tmp / "auth.json"
        auth.write_text(json.dumps({"auth_mode": "chatgpt", "access_token": "a",
                                    "refresh_token": "r"}), encoding="utf-8")
        with mock.patch.object(modelhub, "CODEX_DIR", str(self.tmp)):
            got = modelhub._src_codex()
        self.assertTrue(got["found"])
        self.assertEqual(got["providers"][0]["native_cli"], "codex-cli")

    def test_claude_native_binding_matches_anthropic_only(self):
        from app.core import modelhub
        data = {"providers": [{
            "id": "claude-oauth", "name": "Claude Code 原生 OAuth",
            "protocol": "anthropic", "base_url": "https://api.anthropic.com",
            "api_key": "", "model": "sonnet",
            "models": [{"name": "sonnet", "enabled": True, "priority": 1}],
            "credential_mode": "cli", "native_cli": "claude-code",
            "native_models": True,
        }], "bindings": {}}
        modelhub._FILE.write_text(json.dumps(data), encoding="utf-8")
        modelhub.set_binding("claude-code", chain=[
            {"provider_id": "claude-oauth", "model": "sonnet"}])
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNotNone(pick)
        self.assertIsNone(note)
        self.assertEqual(pick["model"], "sonnet")
        self.assertIsNone(modelhub.launch_pick("codex-cli", ("openai",))[0])

    def test_claude_subscription_account_is_native_and_keyless(self):
        from app.core import modelhub
        settings = self.tmp / "settings.json"
        global_cfg = self.tmp / "claude.json"
        settings.write_text("{}", encoding="utf-8")
        global_cfg.write_text(json.dumps({"oauthAccount": {"accountUuid": "a1"}}), encoding="utf-8")
        with mock.patch.object(modelhub, "CLAUDE_SETTINGS", str(settings)), \
             mock.patch.object(modelhub, "CLAUDE_GLOBAL", str(global_cfg)):
            got = modelhub._src_claude()
        p = next(p for p in got["providers"] if p.get("native_cli") == "claude-code")
        self.assertEqual(p["api_key"], "")

    def test_mimo_auto_discovery_is_native_limited_free_channel(self):
        from app.core import modelhub
        with mock.patch.object(modelhub, "MIMO_CONFIG", str(self.tmp / "mimocode.jsonc")), \
             mock.patch.object(modelhub.shutil, "which", return_value="mimo"):
            got = modelhub._src_mimo()
        self.assertEqual(len(got["providers"]), 1)
        p = got["providers"][0]
        self.assertEqual(p["native_cli"], "mimo-code")
        self.assertEqual(p["model"], "mimo/mimo-auto")
        self.assertEqual(p["api_key"], "")
        self.assertEqual(p["free_tier"], "limited")

    def test_mimo_native_binding_is_cli_scoped_and_selects_model(self):
        from app.core import modelhub, runner
        data = {"providers": [{
            "id": "mimo-1", "name": "MiMo Code 原生免费通道",
            "protocol": "openai", "base_url": "https://mimo.xiaomi.com", "api_key": "",
            "model": "mimo/mimo-auto",
            "models": [{"name": "mimo/mimo-auto", "enabled": True, "priority": 1}],
            "credential_mode": "cli", "native_cli": "mimo-code", "native_models": True,
        }], "bindings": {}}
        modelhub._FILE.write_text(json.dumps(data), encoding="utf-8")
        modelhub.set_binding("mimo-code", chain=[{"provider_id": "mimo-1", "model": "mimo/mimo-auto"}])
        self.assertIsNotNone(modelhub.resolve_binding("mimo-code"))
        self.assertIsNone(modelhub.resolve_binding("opencode"))
        argv, _stdin, _prompt, _tmp = runner._build_call(
            {"id": "mimo-code", "command": "mimo", "argv_template": ["run", "{prompt}"]},
            "generic", "", False, "mimo/mimo-auto", "hello")
        self.assertIn("-m", argv)

    def test_mimo_native_sync_selects_model_without_codebee_block(self):
        from app.core import manager
        home = self.tmp / "home"
        config = home / ".config" / "mimocode" / "mimocode.jsonc"
        config.parent.mkdir(parents=True)
        config.write_text('{"provider": {"codebee": {"options": {"apiKey": "old"}}}, "model": "old/model"}', encoding="utf-8")
        with mock.patch.object(manager.os.path, "expanduser",
                               side_effect=lambda p: str(home) if p == "~" else
                               (str(home / p[2:]) if p.startswith("~/") else p)), \
             mock.patch.object(manager, "tmp_data_no_home_write", return_value=False):
            err = manager._sync_mimo_settings(
                {"id": "mimo-code", "config": {"path": "~/.config/mimocode/mimocode.jsonc"}},
                "mimo/mimo-auto", {"credential_mode": "cli", "native_cli": "mimo-code",
                                    "native_models": True})
        self.assertIsNone(err, err)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "mimo/mimo-auto")
        self.assertNotIn("codebee", data.get("provider", {}))


class TestNativeChannelAutoWire(BaseTest):
    """原生 CLI 免费通道自动接线：探测可用即导入+绑空槽，不覆盖人工绑定。"""

    def _stubs(self, modelhub, free_models=None):
        """把两个通道的 CLI/配置探测指到临时目录，返回 patch 列表。"""
        free_models = free_models if free_models is not None else [
            "opencode/deepseek-v4.1-flash-free", "opencode/glm-5.3-flash-free"]
        return [
            mock.patch.object(modelhub, "OPENCODE_CONFIG", str(self.tmp / "no-oc.json")),
            mock.patch.object(modelhub, "OPENCODE_AUTH", str(self.tmp / "no-auth.json")),
            mock.patch.object(modelhub, "MIMO_CONFIG", str(self.tmp / "no-mimo.jsonc")),
            mock.patch.object(modelhub.shutil, "which",
                              side_effect=lambda n: n if n in ("opencode", "mimo") else None),
            mock.patch.object(modelhub, "_opencode_native_models",
                              return_value=list(free_models)),
        ]

    def test_fallback_probe_finds_cli_in_nvm_symlink_dir(self):
        from app.core import runner
        link = self.tmp / "nodejs"
        link.mkdir()
        exe = link / "opencode.cmd"
        exe.write_text("@echo off", encoding="utf-8")
        env = {"CODEBEE_RELEASE_TEST_PLATFORM": "win32",
               "NVM_SYMLINK": str(link), "LOCALAPPDATA": str(self.tmp / "lal"),
               "APPDATA": str(self.tmp / "ad")}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(runner.shutil, "which", return_value=None):
            self.assertEqual(runner._fallback_probe_cli("opencode"), str(exe))
            self.assertIsNone(runner._fallback_probe_cli("definitely-not-here"))

    def test_native_models_probe_survives_missing_path_snapshot(self):
        from app.core import modelhub, runner
        fake = r"D:\nvm4w\nodejs\opencode.cmd"
        with mock.patch.object(modelhub.shutil, "which", return_value=None), \
             mock.patch.object(runner, "_fallback_probe_cli", return_value=fake), \
             mock.patch.object(runner, "resolve_command",
                               return_value=["cmd", "/c", fake]) as rc, \
             mock.patch.object(modelhub.subprocess, "run",
                               return_value=mock.Mock(stdout="opencode/glm-5.3-flash-free\n")) as sp:
            got = modelhub._opencode_native_models()
        self.assertEqual(got, ["opencode/glm-5.3-flash-free"])
        self.assertEqual(sp.call_args[0][0][:3], ["cmd", "/c", fake])
        rc.assert_called_once_with("opencode")

    def test_native_models_returns_empty_without_resolvable_cli(self):
        from app.core import modelhub, runner
        with mock.patch.object(modelhub.shutil, "which", return_value=None), \
             mock.patch.object(runner, "_fallback_probe_cli", return_value=None), \
             mock.patch.object(modelhub.subprocess, "run") as sp:
            self.assertEqual(modelhub._opencode_native_models(), [])
        sp.assert_not_called()

    def test_auto_wire_imports_and_binds_empty_slots(self):
        from app.core import modelhub
        patches = self._stubs(modelhub)
        with _stack(patches):
            out = modelhub.auto_wire_native_channels()
        self.assertIn("导入并绑定", out["opencode"])
        self.assertIn("导入并绑定", out["mimo-code"])
        data = json.loads(modelhub._FILE.read_text(encoding="utf-8"))
        oc = next(p for p in data["providers"] if p.get("source_id") == "opencode:native-free")
        self.assertEqual([m["name"] for m in oc["models"]],
                         ["opencode/deepseek-v4.1-flash-free", "opencode/glm-5.3-flash-free"])
        b = data["bindings"]["opencode"]
        self.assertEqual(b["provider_id"], oc["id"])
        self.assertEqual(b["model"], "opencode/deepseek-v4.1-flash-free")
        self.assertEqual(b["chain"][0]["model"], "opencode/deepseek-v4.1-flash-free")
        mi = next(p for p in data["providers"] if p.get("source_id") == "mimo:native-auto")
        self.assertEqual(data["bindings"]["mimo-code"]["model"], "mimo/mimo-auto")
        self.assertEqual(data["bindings"]["mimo-code"]["provider_id"], mi["id"])

    def test_auto_wire_preserves_manual_binding_and_is_idempotent(self):
        from app.core import modelhub
        data = {"providers": [{
            "id": "prov-manual", "name": "人工网关", "protocol": "openai",
            "base_url": "https://gw.example", "api_key": "k1",
            "models": [{"name": "paid-x", "enabled": True, "priority": 1}],
        }], "bindings": {"opencode": {"provider_id": "prov-manual", "model": "paid-x",
                                       "chain": [{"provider_id": "prov-manual",
                                                  "model": "paid-x"}],
                                       "models": [], "difficulty_routing": True}}}
        modelhub._FILE.write_text(json.dumps(data), encoding="utf-8")
        patches = self._stubs(modelhub)
        with _stack(patches):
            out1 = modelhub.auto_wire_native_channels()
            self.assertEqual(out1["opencode"], "已有人工绑定，保持不动")
            out2 = modelhub.auto_wire_native_channels()
        data2 = json.loads(modelhub._FILE.read_text(encoding="utf-8"))
        b = data2["bindings"]["opencode"]
        self.assertEqual((b["provider_id"], b["model"]), ("prov-manual", "paid-x"))
        self.assertEqual(len([p for p in data2["providers"]
                              if p.get("source_id") == "opencode:native-free"]), 1)
        self.assertEqual(len([p for p in data2["providers"]
                              if p.get("source_id") == "mimo:native-auto"]), 1)
        self.assertIn("保持不动", out2["mimo-code"])

    def test_auto_wire_skips_channels_without_cli(self):
        from app.core import modelhub
        with mock.patch.object(modelhub, "MIMO_CONFIG", str(self.tmp / "none.jsonc")), \
             mock.patch.object(modelhub.shutil, "which", return_value=None), \
             mock.patch.object(modelhub, "_opencode_native_models", return_value=[]):
            out = modelhub.auto_wire_native_channels()
        self.assertIn("跳过", out["opencode"])
        self.assertIn("跳过", out["mimo-code"])
        self.assertFalse(modelhub._FILE.exists())
