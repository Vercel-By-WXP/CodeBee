import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import pipeline, settings_schema


class CompactionDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        settings_schema.init(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_install_enables_compaction_by_default(self):
        settings_schema.register_default_namespaces()

        self.assertTrue(settings_schema.get("orchestrator", "compaction.enabled"))

    def test_legacy_default_off_is_migrated_on_but_explicit_off_is_preserved(self):
        path = Path(self.temp_dir.name) / "settings_v2.json"
        path.write_text(json.dumps({
            "values": {"orchestrator": {"compaction": {"enabled": False}}},
            "revisions": {"orchestrator": 1},
        }), encoding="utf-8")
        settings_schema.init(self.temp_dir.name)
        settings_schema.register_default_namespaces()

        self.assertTrue(settings_schema.get("orchestrator", "compaction.enabled"))

        settings_schema.init(self.temp_dir.name)
        settings_schema.register_default_namespaces()
        settings_schema.mutate("orchestrator", [{
            "op": "set", "path": "compaction.enabled", "value": False,
        }])
        settings_schema.init(self.temp_dir.name)
        settings_schema.register_default_namespaces()

        self.assertFalse(settings_schema.get("orchestrator", "compaction.enabled"))

    def test_environment_can_explicitly_disable_or_force_compaction(self):
        settings_schema.register_default_namespaces()

        with patch.dict("os.environ", {"TUTTI_COMPACTION": "0"}):
            self.assertFalse(pipeline._compaction_enabled())
        with patch.dict("os.environ", {"TUTTI_COMPACTION": "1"}):
            self.assertTrue(pipeline._compaction_enabled())


if __name__ == "__main__":
    unittest.main()
