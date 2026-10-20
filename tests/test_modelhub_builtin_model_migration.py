"""Regression tests for stale Our Free Model model IDs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import modelhub


class BuiltinModelMigrationTests(unittest.TestCase):
    def test_old_short_id_is_replaced_in_provider_and_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            path.write_text(json.dumps({
                "providers": [{
                    "id": "prov-49",
                    "name": "Our Free Model",
                    "source": modelhub.OUR_FREE_MODEL_SOURCE,
                    "enabled": True,
                    "api_key": "key",
                    "models": [
                        {"name": "nvidia/nemotron-3.5-super-vl:free",
                         "enabled": True, "hidden": False},
                    ],
                }],
                "bindings": {
                    "opencode": {
                        "provider_id": "prov-49",
                        "model": "nemotron-3.5-super-vl-free",
                        "models": ["nemotron-3.5-super-vl-free"],
                        "chain": [{"provider_id": "prov-49",
                                   "model": "nemotron-3.5-super-vl-free"}],
                    },
                },
            }), encoding="utf-8")

            with mock.patch.object(modelhub, "_FILE", path):
                self.assertEqual(modelhub.migrate_builtin_free_model_ids(), 1)
                data = json.loads(path.read_text(encoding="utf-8"))

        binding = data["bindings"]["opencode"]
        expected = "nvidia/nemotron-3.5-super-vl:free"
        self.assertEqual(binding["model"], expected)
        self.assertEqual(binding["models"], [expected])
        self.assertEqual(binding["chain"][0]["model"], expected)

    def test_stale_model_is_removed_from_binding_when_no_current_alias_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            path.write_text(json.dumps({
                "providers": [{
                    "id": "prov-49", "source": modelhub.OUR_FREE_MODEL_SOURCE,
                    "models": [{"name": "beam-free", "enabled": True}],
                }],
                "bindings": {"opencode": {
                    "chain": [{"provider_id": "prov-49",
                               "model": "nemotron-3.5-super-vl-free"}],
                }},
            }), encoding="utf-8")
            with mock.patch.object(modelhub, "_FILE", path):
                self.assertEqual(modelhub.migrate_builtin_free_model_ids(), 1)
                data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["bindings"]["opencode"]["chain"], [])
        self.assertEqual(data["bindings"]["opencode"]["model"], "")


if __name__ == "__main__":
    unittest.main()
