# -*- coding: utf-8 -*-
"""OpenCode first-party free models use the CLI's own auth/session."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from base import BaseTest


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
