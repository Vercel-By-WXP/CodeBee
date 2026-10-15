# -*- coding: utf-8 -*-
"""Backup extraction must not follow destination symlinks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import backup


class BackupSymlinkGuardTests(unittest.TestCase):
    def test_safe_join_rejects_resolved_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "data"
            outside = Path(td) / "outside"
            root.mkdir()
            outside.mkdir()

            original_resolve = Path.resolve

            def resolve_with_escape(path, strict=False):
                resolved = original_resolve(path, strict=strict)
                if path.name == "escape.txt" and path.parent.name == "linked":
                    return outside / path.name
                return resolved

            with patch.object(Path, "resolve", resolve_with_escape):
                with self.assertRaises(ValueError):
                    backup._safe_join(root, "linked/escape.txt")

    def test_safe_join_keeps_normal_paths_inside_base(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "data"
            root.mkdir()
            target = backup._safe_join(root, "nested/file.txt")
            self.assertEqual(target, (root / "nested" / "file.txt").resolve())


if __name__ == "__main__":
    unittest.main()
