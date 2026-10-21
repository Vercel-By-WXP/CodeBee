import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import command_auth


class CommandAuthPersistenceTests(unittest.TestCase):
    def test_security_state_uses_atomic_private_writer(self):
        old_file = command_auth._FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                command_auth.init(tmp)
                with mock.patch.object(command_auth.atomic, "write_json",
                                       wraps=command_auth.atomic.write_json) as writer:
                    command_auth._persist({"mode": "restricted", "rules": [], "audit": []})
                path = Path(tmp) / "command-permissions.json"
                self.assertEqual(writer.call_count, 1)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mode"],
                                 "restricted")
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        finally:
            command_auth._FILE = old_file


if __name__ == "__main__":
    unittest.main()
