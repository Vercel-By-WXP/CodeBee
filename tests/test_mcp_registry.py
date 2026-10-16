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


class McpCatalogTests(BaseTest):
    def test_catalog_exposes_runtime_readiness_without_installing_packages(self):
        from app.core import mcp_catalog

        def which(command):
            return "C:/bin/%s" % command if command in {"npx", "npm"} else None

        with mock.patch.object(mcp_catalog.shutil, "which", side_effect=which), \
                mock.patch.object(mcp_catalog.mcp_client, "sandbox_backend_available", return_value=True):
            rows = {row["id"]: row for row in mcp_catalog.catalog()}
        self.assertTrue(rows["memory"]["launcher_available"])
        self.assertTrue(rows["memory"]["package_manager_available"])
        self.assertEqual(rows["memory"]["runtime_status"], "ready")
        self.assertEqual(rows["memory"]["install_hint"],
                         "npm exec --yes @modelcontextprotocol/server-memory")
        self.assertFalse(rows["fetch"]["launcher_available"])
        self.assertFalse(rows["fetch"]["package_manager_available"])
        self.assertEqual(rows["fetch"]["runtime_status"], "missing_package_manager")

    def test_catalog_requires_the_same_isolation_backend_as_runtime_dispatch(self):
        from app.core import mcp_catalog, mcp_client
        with mock.patch.object(mcp_catalog.shutil, "which", side_effect=lambda name: "x"), \
                mock.patch.object(mcp_client, "sandbox_backend_available", return_value=False):
            row = next(item for item in mcp_catalog.catalog() if item["id"] == "memory")
        self.assertTrue(row["launcher_available"])
        self.assertFalse(row["sandbox_available"])
        self.assertEqual(row["runtime_status"], "missing_sandbox")

    def test_catalog_install_is_preview_by_default_and_requires_options(self):
        from app.core import mcp_catalog
        with self.assertRaises(ValueError):
            mcp_catalog.install("filesystem")
        preview = mcp_catalog.install(
            "filesystem", options={"workdir": str(self.workdir)})
        self.assertFalse(preview["commit"])
        self.assertTrue(preview["package_install_required"])
        self.assertEqual(preview["server"]["name"], "cb_filesystem")
        self.assertNotEqual(mcp_catalog._configured(), [preview["server"]])

    def test_catalog_commit_replaces_managed_entry_and_redacts_env(self):
        from app.core import mcp_catalog, settings
        first = mcp_catalog.install("github", commit=True)
        self.assertTrue(first["changed"])
        second = mcp_catalog.install(
            "github", options={"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret"}},
            commit=True)
        self.assertTrue(second["changed"])
        configured = mcp_catalog._configured()
        self.assertEqual(len(configured), 1)
        self.assertEqual(configured[0]["name"], "cb_github")
        self.assertEqual(second["server"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "<redacted>")
        self.assertIn("secret", settings.load()["mcp_servers"])

    def test_catalog_remove_is_explicit_and_state_is_audit_safe(self):
        from app.core import mcp_catalog
        mcp_catalog.install("time", commit=True)
        preview = mcp_catalog.remove("cb_time")
        self.assertFalse(preview["commit"])
        self.assertEqual(len(mcp_catalog._configured()), 1)
        done = mcp_catalog.remove("cb_time", commit=True)
        self.assertTrue(done["changed"])
        self.assertEqual(mcp_catalog._configured(), [])
        self.assertTrue(all("secret" not in json.dumps(x) for x in mcp_catalog.state()["configured"]))

    def test_catalog_commit_invalidates_tool_discovery_and_reports_configured_runtime(self):
        from app.core import mcp_catalog, mcp_client
        with mock.patch.object(mcp_client, "invalidate_tools_cache") as invalidate, \
                mock.patch.object(mcp_catalog.shutil, "which", return_value=None), \
                mock.patch.object(mcp_client, "sandbox_backend_available", return_value=False):
            mcp_catalog.install("time", commit=True)
            state = mcp_catalog.state()
            mcp_catalog.remove("cb_time", commit=True)
        self.assertGreaterEqual(invalidate.call_count, 2)
        self.assertEqual(state["configured_runtime"], [{
            "name": "cb_time", "command": "uvx",
            "launcher_available": False, "sandbox_available": False,
            "runtime_status": "missing_launcher",
        }])
