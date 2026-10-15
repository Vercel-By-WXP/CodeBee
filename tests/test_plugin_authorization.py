import json
import tempfile
import unittest
from pathlib import Path

from app.core import plugins


class PluginAuthorizationTests(unittest.TestCase):
    def test_capability_policy_requires_explicit_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            (root / ".codex-plugin").mkdir(parents=True)
            (root / "skill.md").write_text("skill", encoding="utf-8")
            (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
                "name": "demo", "version": "1.0.0", "description": "demo",
                "author": {"name": "test"}, "skills": ["skill.md"],
                "interface": {"displayName": "Demo", "shortDescription": "d",
                               "longDescription": "demo", "developerName": "test",
                               "category": "test", "defaultPrompt": ["go"],
                               "capabilities": ["filesystem.write", "network"]},
            }), encoding="utf-8")
            descriptor = plugins.load_manifest(root)
            policy = plugins.authorize(descriptor, grants=["filesystem.read"])
            self.assertFalse(policy["allowed"])
            self.assertIn("filesystem.write", policy["missing"])
            policy = plugins.authorize(descriptor, grants=["filesystem.write", "network"])
            self.assertTrue(policy["allowed"])


if __name__ == "__main__":
    unittest.main()
