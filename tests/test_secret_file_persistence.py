import json
import os
import tempfile
import unittest
from pathlib import Path

from app.core import backend_profiles, modelhub


class SecretFilePersistenceTests(unittest.TestCase):
    def test_model_provider_secrets_are_atomically_saved_private_on_posix(self):
        old_file = modelhub._FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                modelhub._FILE = Path(tmp) / "models.json"
                modelhub._save({"providers": [{
                    "id": "provider-1", "api_key": "provider-secret",
                }]})

                saved = json.loads(modelhub._FILE.read_text(encoding="utf-8"))

                self.assertEqual(saved["providers"][0]["api_key"], "provider-secret")
                if os.name != "nt":
                    self.assertEqual(modelhub._FILE.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(Path(tmp).iterdir()), [modelhub._FILE])
        finally:
            modelhub._FILE = old_file

    def test_acp_profile_secrets_are_atomically_saved_private_on_posix(self):
        old_file = backend_profiles._FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                backend_profiles.init(tmp)
                path = backend_profiles._file()
                backend_profiles._persist([{
                    "id": "local-agent", "env": {"API_TOKEN": "profile-secret"},
                }])

                saved = json.loads(path.read_text(encoding="utf-8"))

                self.assertEqual(saved["profiles"][0]["env"]["API_TOKEN"],
                                 "profile-secret")
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(Path(tmp).iterdir()), [path])
        finally:
            backend_profiles._FILE = old_file


if __name__ == "__main__":
    unittest.main()
