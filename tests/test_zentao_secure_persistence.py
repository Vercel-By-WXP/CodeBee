import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import zentao
from app.core import atomic


class ZentaoSecurePersistenceTests(unittest.TestCase):
    def test_saved_credential_state_uses_atomic_private_file(self):
        old_file = zentao._FILE
        old_state = zentao._STATE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "zentao.json"
                zentao._FILE = target
                zentao._STATE = {"config": {"password": "secret"}, "claims": {}}

                with mock.patch.object(atomic, "write_json",
                                       wraps=atomic.write_json) as write_json:
                    zentao._save_locked()

                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["config"]["password"],
                                 "secret")
                self.assertFalse(target.with_suffix(".tmp").exists())
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        finally:
            zentao._FILE = old_file
            zentao._STATE = old_state


if __name__ == "__main__":
    unittest.main()
