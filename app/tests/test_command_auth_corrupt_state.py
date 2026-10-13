# -*- coding: utf-8 -*-
"""Corrupt permission state must fail closed at the command boundary."""
import json
import tempfile
import unittest
from pathlib import Path

from core import command_auth


class CommandAuthCorruptStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        command_auth.init(self.root)

    def test_malformed_rules_are_ignored_and_command_is_not_allowed(self):
        (self.root / "command-permissions.json").write_text(json.dumps({
            "mode": "restricted",
            "rules": [None, "not-a-rule", {"scope": "global", "match": "bad"}],
            "audit": [],
        }), encoding="utf-8")

        result = command_auth.authorize("echo guarded", timeout_s=0)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "timeout")

    def test_malformed_audit_is_replaced_before_new_denial_is_recorded(self):
        (self.root / "command-permissions.json").write_text(json.dumps({
            "mode": "restricted", "rules": [], "audit": ["bad", None]
        }), encoding="utf-8")

        result = command_auth.authorize("echo guarded", timeout_s=0)
        view = command_auth.view()

        self.assertFalse(result["allowed"])
        self.assertEqual(view["audit"][-1]["action"], "timeout")
        self.assertTrue(all(isinstance(row, dict) for row in view["audit"]))


if __name__ == "__main__":
    unittest.main()
