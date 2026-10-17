# -*- coding: utf-8 -*-
"""Regression coverage for the additional CLI catalog entries."""

import os
from unittest import mock

from base import BaseTest


class TestCliExpansion(BaseTest):
    def test_every_catalog_cli_has_mac_and_windows_install_path(self):
        from app.core import catalog

        for entry in catalog.DEFAULT_CATALOG:
            if entry.get("cli_group") != "installable":
                continue
            self.assertTrue(catalog.command_for(entry, "install", "macos"),
                            msg=entry.get("id"))
            self.assertTrue(catalog.command_for(entry, "install", "windows"),
                            msg=entry.get("id"))

    def test_minimax_code_is_headless_and_local_credential_only(self):
        from app.core import catalog, modelhub, runner

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "minimax-code")
        self.assertEqual(entry["detect"], {"cli": "mcode"})
        self.assertEqual(entry["orch"]["argv_template"], ["exec", "{prompt}"])
        self.assertEqual(modelhub.bindable_protocols("minimax-code"), ())

        agent = {"id": "minimax-code", "kind": "generic", "mode": "real",
                 "command": "mcode", "argv_template": ["exec", "{prompt}"],
                 "env": {}}
        argv, stdin_text, _, _ = runner._build_call(
            agent, "generic", "", False, "MiniMax-M2", "修复测试")
        self.assertEqual(argv[-2:], ["exec", "修复测试"])
        self.assertIsNone(stdin_text)
        self.assertNotIn("--model", argv)

    def test_platform_specific_management_command(self):
        from app.core import catalog

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "minimax-code")
        self.assertIn("install.ps1", catalog.command_for(entry, "install", "windows"))
        self.assertIn("install.sh", catalog.command_for(entry, "install", "linux"))
        # Release-gate runs this Windows command contract on a POSIX host.
        with mock.patch.dict(os.environ, {"CODEBEE_RELEASE_TEST_PLATFORM": "win32"}), \
             mock.patch.object(catalog.os, "name", "posix"), \
             mock.patch.object(catalog.sys, "platform", "linux"):
            self.assertIn("USERPROFILE", catalog.uninstall_command(dict(entry)))

        goose = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "goose")
        self.assertIn("download_cli.ps1", catalog.command_for(goose, "install", "windows"))
        self.assertIn("download_cli.sh", catalog.command_for(goose, "install", "macos"))
        self.assertIn("download_cli.sh", catalog.command_for(goose, "install", "darwin"))
        self.assertIn("USERPROFILE", catalog.command_for(goose, "uninstall", "windows"))

    def test_goose_is_visible_but_not_routable(self):
        from app.core import catalog, registry

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "goose")
        self.assertIsNone(entry["orch"])
        # A management-only CLI must never become an execution agent even when
        # the executable is present on the host.
        agents = registry.effective_agents([entry], {"goose": {"installed": True}})
        self.assertFalse(any(a.get("id") == "goose" for a in agents))

    def test_mistral_vibe_is_headless_and_local_credential_only(self):
        from app.core import catalog, modelhub, runner

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "mistral-vibe")
        self.assertEqual(entry["detect"], {"cli": "vibe"})
        self.assertEqual(entry["orch"]["argv_template"],
                         ["--prompt", "{prompt}", "--auto-approve", "--output", "text"])
        self.assertEqual(modelhub.bindable_protocols("mistral-vibe"), ())

        agent = {"id": "mistral-vibe", "kind": "generic", "mode": "real",
                 "command": "vibe", "argv_template": entry["orch"]["argv_template"],
                 "env": {}}
        argv, stdin_text, _, _ = runner._build_call(
            agent, "generic", "", False, "mistral-large", "检查这段代码")
        self.assertEqual(argv[-5:], ["--prompt", "检查这段代码", "--auto-approve", "--output", "text"])
        self.assertIsNone(stdin_text)
        self.assertNotIn("--model", argv)

    def test_mistral_vibe_management_and_launch(self):
        from app.core import catalog

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "mistral-vibe")
        self.assertIn("uv tool install mistral-vibe", catalog.command_for(entry, "install"))
        self.assertIn("uv tool upgrade mistral-vibe", catalog.command_for(entry, "upgrade"))
        self.assertEqual(catalog.uninstall_command(entry), "uv tool uninstall mistral-vibe")
        loaded = next(x for x in catalog.load(force=True) if x["id"] == "mistral-vibe")
        self.assertEqual(loaded["launch"], {"kind": "console", "command": "vibe"})

    def test_cline_is_cross_platform_headless(self):
        from app.core import catalog, modelhub, runner

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "cline-cli")
        self.assertEqual(entry["detect"], {"cli": "cline"})
        self.assertEqual(modelhub.bindable_protocols("cline-cli"), ())
        self.assertEqual(modelhub.bindable_protocols("cline"), ())
        agent = {"id": "cline-cli", "kind": "generic", "mode": "real",
                 "command": "cline", "argv_template": ["--yolo", "{prompt}"], "env": {}}
        argv, stdin_text, _, _ = runner._build_call(
            agent, "generic", "", False, "claude-sonnet", "修复失败测试")
        self.assertEqual(argv[-2:], ["--yolo", "修复失败测试"])
        self.assertIsNone(stdin_text)
        self.assertNotIn("--model", argv)

    def test_copilot_cli_is_management_only_on_both_desktops(self):
        from app.core import catalog, registry

        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "github-copilot-cli")
        self.assertIsNone(entry["orch"])
        self.assertIn("copilot-cli", catalog.command_for(entry, "install", "macos"))
        self.assertIn("GitHub.Copilot", catalog.command_for(entry, "install", "windows"))
        self.assertIn("@github/copilot", catalog.command_for(entry, "install", "linux"))
        agents = registry.effective_agents([entry], {"copilot": {"installed": True}})
        self.assertFalse(any(a.get("id") == "github-copilot-cli" for a in agents))


if __name__ == "__main__":
    import unittest
    unittest.main()
