# -*- coding: utf-8 -*-
"""MCP export/registration is explicit, redacted and reversible."""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


class McpRegistryTests(BaseTest):
    def _servers(self):
        return [{"name": "docs", "command": "node", "args": ["server.js"],
                 "env": {"API_KEY": "secret", "MODE": "test"},
                 "disabled_tools": []}]

    def test_export_redacts_secret_and_translates_target(self):
        from app.core import mcp_registry
        with mock.patch.object(mcp_registry.plugins, "merged_servers_text",
                               return_value=json.dumps(self._servers())):
            out = mcp_registry.export("claude")
        self.assertEqual(out["servers"][0]["env"]["API_KEY"], "<redacted>")
        self.assertIn('"mcpServers"', out["config"])
        self.assertNotIn("secret", json.dumps(out))

    def test_register_defaults_to_dry_run_and_commit_is_atomic(self):
        from app.core import mcp_registry
        target = self.workdir / "claude.json"
        target.write_text(json.dumps({"keep": True}), encoding="utf-8")
        with mock.patch.object(mcp_registry.plugins, "merged_servers_text",
                               return_value=json.dumps(self._servers())):
            preview = mcp_registry.register("claude", str(target))
            self.assertTrue(preview["dry_run"])
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"keep": True})
            done = mcp_registry.register("claude", str(target), dry_run=False)
        self.assertTrue(done["changed"])
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertTrue(data["keep"])
        self.assertEqual(data["mcpServers"]["docs"]["env"]["API_KEY"], "secret")
        self.assertTrue(target.with_name(target.name + ".codebee.bak").exists())

    def test_unknown_target_is_rejected_before_any_write(self):
        from app.core import mcp_registry
        with self.assertRaises(ValueError):
            mcp_registry.register("unknown", str(self.workdir / "outside.json"))

    def test_codex_registration_uses_managed_toml_block(self):
        from app.core import mcp_registry
        target = self.workdir / "config.toml"
        target.write_text('model = "gpt-test"\n', encoding="utf-8")
        with mock.patch.object(mcp_registry.plugins, "merged_servers_text",
                               return_value=json.dumps(self._servers())):
            preview = mcp_registry.register("codex-cli", str(target))
            self.assertEqual(preview["format"], "toml")
            done = mcp_registry.register("codex-cli", str(target), dry_run=False)
        text = target.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-test"', text)
        self.assertIn("[mcp_servers.docs]", text)
        self.assertIn("# codebee:mcp:start", text)
        self.assertTrue(done["changed"])

    def test_dry_run_never_returns_existing_secret(self):
        from app.core import mcp_registry
        target = self.workdir / "claude.json"
        target.write_text(json.dumps({"mcpServers": {"old": {"env": {"TOKEN": "old-secret"}}}}), encoding="utf-8")
        with mock.patch.object(mcp_registry.plugins, "merged_servers_text",
                               return_value=json.dumps(self._servers())):
            preview = mcp_registry.register("claude", str(target))
        self.assertNotIn("old-secret", preview["config"])

    def test_targets_expose_distinct_qwen_and_gemini_profiles(self):
        from app.core import mcp_registry
        targets = mcp_registry.targets()
        self.assertIn("qwen", targets)
        self.assertIn("gemini", targets)
        self.assertEqual(targets["qwen"]["path"], "~/.qwen/settings.json")
        self.assertEqual(targets["gemini"]["path"], "~/.gemini/settings.json")
        self.assertIn("gemini-cli", targets["gemini"]["aliases"])

    def test_opencode_uses_local_command_array(self):
        from app.core import mcp_registry
        with mock.patch.object(mcp_registry.plugins, "merged_servers_text",
                               return_value=json.dumps(self._servers())):
            out = mcp_registry.export("mimo-code")
        data = json.loads(out["config"])
        self.assertEqual(data["mcp"]["docs"]["type"], "local")
        self.assertEqual(data["mcp"]["docs"]["command"], ["node", "server.js"])
