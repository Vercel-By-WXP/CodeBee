import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import mcp_client, plugins, settings


class PluginTests(unittest.TestCase):
    def _plugin(self, root, name="sample-plugin", *, skills=True, mcp=False):
        plugin = Path(root) / name
        (plugin / ".codex-plugin").mkdir(parents=True)
        manifest = {
            "name": name,
            "version": "0.1.0",
            "description": "A test plugin",
            "author": {"name": "Test"},
            "skills": "./skills/",
            "interface": {
                "displayName": "Sample Plugin",
                "shortDescription": "A sample",
                "longDescription": "A sample plugin for tests",
                "developerName": "Test",
                "category": "Productivity",
                "capabilities": ["Write"],
                "defaultPrompt": ["Review this code."],
            },
        }
        if mcp:
            manifest["mcpServers"] = "./.mcp.json"
            (plugin / ".mcp.json").write_text(json.dumps({
                "mcpServers": {
                    "sample": {"command": "python", "args": ["-c", "print(1)"]}
                }
            }), encoding="utf-8")
        (plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        if skills:
            (plugin / "skills").mkdir()
            (plugin / "skills" / "review.md").write_text(
                "---\nname: Review rules\nscopes:\n  - code\nnote: Review\n---\nCheck boundaries.",
                encoding="utf-8")
        return plugin

    def test_manifest_validation_and_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._plugin(root)
            items = plugins.discover(root)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], "sample-plugin")
            self.assertEqual(items[0]["skills"], ["review.md"])

    def test_manifest_rejects_path_escape_and_name_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugin = self._plugin(root, "sample-plugin")
            data = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text())
            data["skills"] = "../outside"
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(plugins.PluginError):
                plugins.load_manifest(plugin)

    def test_install_toggle_uninstall_and_mcp_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugin = self._plugin(root, mcp=True)
            data = Path(td) / "data"
            with patch.object(plugins.paths, "DATA_DIR", data), \
                 patch.object(plugins.market.paths, "DATA_DIR", data):
                result, err = plugins.install("sample-plugin", roots=[root], grants=["Write"])
                self.assertIsNone(err)
                self.assertTrue(result["ok"])
                self.assertEqual(result["skill_count"], 1)
                self.assertEqual(plugins.view(roots=[root])["plugins"][0]["state"], "disabled")
                self.assertIsNone(plugins.toggle("sample-plugin", True))
                self.assertEqual(plugins.view(roots=[root])["plugins"][0]["state"], "enabled")
                self.assertEqual(plugins.toggle("sample-plugin", False), None)
                self.assertEqual(plugins.view(roots=[root])["plugins"][0]["state"], "disabled")
                self.assertEqual(plugins.mcp_servers("sample-plugin", roots=[root])[0]["name"], "sample")
                self.assertIsNone(plugins.remove("sample-plugin", roots=[root]))
                self.assertEqual(plugins.view(roots=[root])["plugins"][0]["state"], "available")

    def test_enabled_plugin_mcp_is_namespaced_in_client_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._plugin(root, mcp=True)
            data = Path(td) / "data"
            # 生产链路里 _SETTINGS_TEXT 由 main() 启动注入（读 settings +
            # plugins 合成）——单测没有 main()，注入同一个真函数才测得着
            inject = lambda: plugins.merged_servers_text(
                settings.load().get("mcp_servers"))
            with patch.object(plugins.paths, "DATA_DIR", data), \
                 patch.object(plugins.market.paths, "DATA_DIR", data), \
                 patch.object(settings, "load", return_value={"mcp_servers": "[]"}), \
                 patch.object(mcp_client, "_SETTINGS_TEXT", inject):
                result, err = plugins.install("sample-plugin", roots=[root], grants=["Write"])
                self.assertIsNone(err)
                self.assertTrue(result["has_mcp"])
                self.assertEqual(json.loads(mcp_client._settings_text()), [])
                self.assertIsNone(plugins.toggle("sample-plugin", True))
                merged = json.loads(mcp_client._settings_text())
                self.assertEqual(len(merged), 1)
                self.assertTrue(merged[0]["name"].startswith("plg_"))
                self.assertIsNone(plugins.toggle("sample-plugin", False))
                self.assertEqual(json.loads(mcp_client._settings_text()), [])

    def test_corrupt_registry_path_is_not_reported_as_installed(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            with patch.object(plugins.paths, "DATA_DIR", data):
                data.mkdir()
                (data / "plugins.json").write_text(
                    json.dumps({"installed": {"ghost": {"enabled": True}}}),
                    encoding="utf-8")
                self.assertFalse(any(x["id"] == "ghost" for x in plugins.view()["plugins"]))

    def test_plugin_scan_failure_preserves_explicit_mcp_settings(self):
        configured = [{"name": "user", "command": "python", "args": []}]
        inject = lambda: plugins.merged_servers_text(
            settings.load().get("mcp_servers"))
        with patch.object(settings, "load", return_value={
            "mcp_servers": json.dumps(configured)
        }), patch.object(plugins, "active_mcp_servers", side_effect=RuntimeError("broken")), \
            patch.object(mcp_client, "_SETTINGS_TEXT", inject):
            self.assertEqual(json.loads(mcp_client._settings_text()), configured)

    def test_codex_capabilities_include_enabled_manifest_with_hooks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codex = root / "codex"
            plugin = root / "demo"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / "skills").mkdir()
            (plugin / "skills" / "workflow.md").write_text(
                "# Workflow\n\nFollow the project checklist.", encoding="utf-8")
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({
                "name": "demo", "version": "1.0.0", "description": "Demo",
                "author": {"name": "Test"}, "hooks": {"hooks": {}},
                "skills": "./skills/"
            }), encoding="utf-8")
            (codex / "config.toml").parent.mkdir(parents=True)
            (codex / "config.toml").write_text(
                '[marketplaces.test]\nsource = "' + str(plugin).replace('\\', '/') +
                '"\n[plugins."demo@test"]\nenabled = true\n', encoding="utf-8")
            with patch.object(plugins, "_codex_home", return_value=codex):
                result = plugins.capabilities_view()
            row = next(x for x in result["plugins"] if x["id"] == "codex:demo@test")
            self.assertEqual(row["state"], "enabled")
            self.assertEqual(row["unsupported"], ["hooks"])
            self.assertIn("Follow the project checklist", row["skill_prompts"][0])

    def test_codex_agent_profile_keeps_developer_instructions_for_selection(self):
        with tempfile.TemporaryDirectory() as td:
            codex = Path(td)
            agents = codex / "agents"
            agents.mkdir()
            (agents / "reviewer.toml").write_text(
                'description = "Review code"\ndeveloper_instructions = "Check tests first."\n',
                encoding="utf-8")
            with patch.object(plugins, "_codex_home", return_value=codex):
                row = plugins.codex_agents_view()[0]
            self.assertEqual(row["display_name"], "reviewer")
            self.assertEqual(row["prompt"], "Check tests first.")

    def test_codex_cache_is_scoped_to_marketplace_and_updates_without_restart(self):
        with tempfile.TemporaryDirectory() as td:
            codex = Path(td)
            cache = codex / "plugins" / "cache"
            self._plugin(cache / "other", name="demo")
            with patch.object(plugins, "_codex_home", return_value=codex):
                self.assertIsNone(plugins._codex_manifest_path("demo", "test", {}))
                target = self._plugin(cache / "test" / "demo", name="1.0.0")
                manifest = target / ".codex-plugin" / "plugin.json"
                data = json.loads(manifest.read_text())
                data["name"] = "demo"
                manifest.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(plugins._codex_manifest_path("demo", "test", {}), target)

    def test_codex_skill_entries_do_not_include_references_or_collapse_declared_roots(self):
        with tempfile.TemporaryDirectory() as td:
            codex = Path(td) / "codex"
            plugin = self._plugin(td, name="demo", skills=False)
            for name in ("first", "second"):
                folder = plugin / "skills" / name
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text("# Instructions " + name, encoding="utf-8")
                (folder / "reference.md").write_text("Not an entry", encoding="utf-8")
            manifest = plugin / ".codex-plugin" / "plugin.json"
            data = json.loads(manifest.read_text())
            data["skills"] = ["skills/first", "skills/second"]
            manifest.write_text(json.dumps(data), encoding="utf-8")
            codex.mkdir()
            (codex / "config.toml").write_text(
                '[marketplaces.test]\nsource = "' + plugin.as_posix() +
                '"\n[plugins."demo@test"]\nenabled = false\n', encoding="utf-8")
            with patch.object(plugins, "_codex_home", return_value=codex):
                rows = [r for r in plugins.codex_skills_view() if r.get("plugin_id")]
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({r["id"] for r in rows}), 2)
            self.assertTrue(all(not r["enabled"] for r in rows))

    def test_config_fallback_comments_and_unrelated_tables(self):
        _, configured = plugins._codex_config_fallback(
            '[plugins."demo@test"]\nenabled = false # disabled\n'
            '[unrelated]\nenabled = true\n')
        self.assertIs(configured["demo@test"]["enabled"], False)


if __name__ == "__main__":
    unittest.main()
