import json
import os
import tempfile
import unittest
from pathlib import Path

from app.core import paths, remote


class RemoteSecurityTests(unittest.TestCase):
    def test_same_origin_normalizes_default_ports_and_rejects_cross_site(self):
        self.assertTrue(remote.same_origin("http://127.0.0.1", "127.0.0.1:80"))
        self.assertTrue(remote.same_origin("https://codebee.example:443",
                                          "codebee.example:443"))
        self.assertFalse(remote.same_origin("https://evil.example", "127.0.0.1:8765"))
        self.assertFalse(remote.same_origin("null", "127.0.0.1:8765"))
        self.assertFalse(remote.same_origin("javascript:alert(1)", "127.0.0.1:8765"))
        self.assertTrue(remote.same_origin("", "127.0.0.1:8765"))

    def test_new_token_is_high_entropy_and_written_atomically(self):
        old_data_dir = paths.DATA_DIR
        old_token = remote._TOKEN
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                remote._TOKEN = ""
                value = remote.token()
                self.assertGreaterEqual(len(value), 40)
                persisted = json.loads((Path(tmp) / "remote.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["token"], value)
                self.assertTrue((Path(tmp) / "remote.json").is_file())
                if os.name != "nt":
                    self.assertEqual((Path(tmp) / "remote.json").stat().st_mode & 0o777, 0o600)
        finally:
            paths.DATA_DIR = old_data_dir
            remote._TOKEN = old_token


if __name__ == "__main__":
    unittest.main()
