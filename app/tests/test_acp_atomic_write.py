# -*- coding: utf-8 -*-
"""ACP file writes must preserve a valid destination on interruption."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import acp_client


class ACPAtomicWriteTests(unittest.TestCase):
    def test_write_replaces_destination_without_partial_contents(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "notes.md"
            target.write_text("before\n", encoding="utf-8")

            result = acp_client._fs_request(
                "fs/write_text_file", {"path": str(target), "content": "after\n"},
                str(root), "", False, "")

            self.assertEqual(result, {})
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
            self.assertEqual(list(root.glob(".notes.md-*.tmp")), [])

    def test_failed_replace_keeps_old_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "notes.md"
            target.write_text("before\n", encoding="utf-8")

            with patch.object(acp_client.os, "replace",
                              side_effect=OSError("simulated interruption")):
                with self.assertRaises(OSError):
                    acp_client._fs_request(
                        "fs/write_text_file",
                        {"path": str(target), "content": "after\n"},
                        str(root), "", False, "")

            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(list(root.glob(".notes.md-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
