import json
from unittest.mock import patch

from base import BaseTest
from test_plugins_install import PID, _seed_plugin


class PluginCompatibilityTests(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import plugins, plugin_compat
        self.plugins, self.compat = plugins, plugin_compat
        self.root = _seed_plugin(self.tmp)
        self.manifest = self.root / PID / ".codex-plugin" / "plugin.json"
        p = patch.object(plugin_compat, "host_version", return_value="1.2.3")
        p.start()
        self.addCleanup(p.stop)

    def requirement(self, **fields):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["codebee"] = fields
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def test_missing_requirement_remains_compatible(self):
        row = self.plugins.discover([self.root])[0]
        self.assertTrue(row["compatibility"]["compatible"])
        self.assertEqual(row["compatibility"]["host_version"], "1.2.3")

    def test_preflight_rejects_newer_host_before_copy_or_skill_install(self):
        self.requirement(min_version="1.3.0")
        row = self.plugins.discover([self.root])[0]
        self.assertFalse(row["compatibility"]["compatible"])
        with patch.object(self.plugins, "_copy_plugin") as copy:
            result, error = self.plugins.install(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("1.3.0", error)
        copy.assert_not_called()
        self.assertFalse((self.data_dir / "plugins.json").exists())

    def test_semver_ordering_prerelease_and_invalid_constraints(self):
        from app.core import plugin_compat
        for host, minimum, expected in (("1.2.3", "1.2.3", True),
            ("1.10.0", "1.2.9", True), ("1.2.3-rc.1", "1.2.3", False),
            ("1.2.3-rc.10", "1.2.3-rc.2", True), ("1.2.3+build", "1.2.3", True)):
            with self.subTest(host=host), patch.object(plugin_compat, "host_version", return_value=host):
                self.assertEqual(plugin_compat.check({"codebee": {"min_version": minimum}})["compatible"], expected)
        for minimum in (True, "1.2", ">=1.0.0", "1.02.3", "1.2.3-01", "1.2.3\n", "1.2.3-", "1.2.٣"):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                plugin_compat.check({"codebee": {"min_version": minimum}})

    def test_invalid_constraint_is_blocked_catalog_row(self):
        self.requirement(min_version=True)
        self.assertEqual(self.plugins.discover([self.root])[0]["state"], "blocked")

    def test_enabling_rechecks_current_host_but_disabling_remains_available(self):
        self.requirement(min_version="1.0.0")
        self.assertIsNone(self.plugins.install(PID, roots=[self.root])[1])
        with patch.object(self.compat, "host_version", return_value="0.9.0"):
            error = self.plugins.toggle(PID, True)
            self.assertIn("1.0.0", error)
            self.assertIsNone(self.plugins.toggle(PID, False))
            self.assertEqual(self.plugins.active_mcp_servers(), [])

    def test_upgrade_keeps_disabled_state_and_replaces_skill_content(self):
        self.assertIsNone(self.plugins.install(PID, roots=[self.root])[1])
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["version"] = "0.2.0"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        (self.root / PID / "skills" / "retry.md").write_text(
            "---\nname: retry-429\n---\nUpdated skill body", encoding="utf-8")
        result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(error, error)
        self.assertTrue(result["ok"])
        row = self.plugins.view(roots=[self.root])["plugins"][0]
        self.assertEqual(row["state"], "disabled")
        self.assertEqual(row["version"], "0.2.0")
        self.assertFalse(self.plugins._installed_record(PID)["enabled"])
        self.assertTrue(all(not self.plugins.skills._pack_enabled(p["id"])
                            for p in self.plugins.skills.user_packs()))
        self.assertTrue(any("Updated skill body" in p.read_text(encoding="utf-8")
                            for p in (self.data_dir / "skillpacks").glob("*.md")))

    def test_incompatible_or_failed_upgrade_leaves_old_install_intact(self):
        self.assertIsNone(self.plugins.install(PID, roots=[self.root])[1])
        before = json.loads(json.dumps(self.plugins._load_registry()))
        self.requirement(min_version="2.0.0")
        result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("2.0.0", error)
        self.assertEqual(self.plugins._load_registry(), before)
        self.requirement(min_version="1.0.0")
        with patch.object(self.plugins, "_copy_plugin", side_effect=OSError("copy failed")):
            result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertEqual(self.plugins._load_registry(), before)
        installed = self.plugins.load_manifest(self.plugins._install_root() / PID)
        self.assertEqual(installed["version"], "0.1.0")

    def test_registry_write_failure_rolls_back_files_and_skills(self):
        self.assertIsNone(self.plugins.install(PID, roots=[self.root])[1])
        before = json.loads(json.dumps(self.plugins._load_registry()))
        with patch.object(self.plugins, "_save_registry", side_effect=OSError("disk full")):
            result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("disk full", error)
        self.assertEqual(self.plugins._load_registry(), before)
        self.assertTrue((self.plugins._install_root() / PID / ".codex-plugin" / "plugin.json").exists())
        self.assertEqual(len(self.plugins.skills.user_packs()), 1)

    def test_unavailable_host_version_is_not_assumed_compatible(self):
        self.requirement(min_version="1.0.0")
        with patch.object(self.compat, "host_version", return_value=""):
            result, error = self.plugins.install(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("unknown", error)

    def test_upgrade_preserves_enabled_state_and_grants_and_refuses_new_permissions(self):
        self.assertIsNone(self.plugins.install(PID, roots=[self.root])[1])
        self.assertIsNone(self.plugins.toggle(PID, True))
        result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(error)
        self.assertTrue(result["enabled"])
        self.assertTrue(all(self.plugins.skills._pack_enabled(p["id"])
                            for p in self.plugins.skills.user_packs()))
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["interface"]["capabilities"] = ["process.execute"]
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        before = json.loads(json.dumps(self.plugins._load_registry()))
        result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("process.execute", error)
        self.assertEqual(self.plugins._load_registry(), before)

    def test_update_rejects_registry_path_outside_install_root(self):
        self.assertIsNone(self.plugins.install(PID, roots=[self.root])[1])
        registry = self.plugins._load_registry()
        registry["installed"][PID]["install_dir"] = str(self.root / PID)
        self.plugins._save_registry(registry)
        with patch.object(self.plugins, "_copy_plugin") as copy:
            result, error = self.plugins.upgrade(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("越界", error)
        copy.assert_not_called()
        self.assertTrue(self.manifest.exists())

    def test_install_checks_copied_manifest_before_installing_skills(self):
        copy = self.plugins._copy_plugin
        def changed_copy(source, target):
            copy(source, target)
            path = target / ".codex-plugin" / "plugin.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["codebee"] = {"min_version": "99.0.0"}
            path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(self.plugins, "_copy_plugin", side_effect=changed_copy):
            result, error = self.plugins.install(PID, roots=[self.root])
        self.assertIsNone(result)
        self.assertIn("99.0.0", error)
        self.assertFalse((self.plugins._install_root() / PID).exists())
        self.assertEqual(self.plugins.skills.user_packs(), [])
