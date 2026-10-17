# -*- coding: utf-8 -*-
"""Attachment delivery must fail closed on symlinked destinations."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import attachments


class AttachmentSymlinkGuardTests(unittest.TestCase):
    def test_commit_skips_destination_that_resolves_outside_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td) / "workspace"
            workdir.mkdir()

            original_resolve = Path.resolve

            def resolve_with_escape(path, strict=False):
                resolved = original_resolve(path, strict=strict)
                if path.name == "note.txt" and path.parent.name == "_attachments":
                    return Path(td) / "outside" / path.name
                return resolved

            with patch.object(attachments.paths, "DATA_DIR", Path(td) / "data"), \
                 patch.object(Path, "resolve", resolve_with_escape):
                meta = attachments.save_pending(
                    "note.txt", base64.b64encode(b"secret").decode("ascii"))
                delivered = attachments.commit_to_workdir(str(workdir), [meta["id"]])

            self.assertEqual(delivered, [])
            self.assertFalse((Path(td) / "outside" / "note.txt").exists())

    def test_commit_keeps_normal_attachment_inside_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td) / "workspace"
            workdir.mkdir()
            with patch.object(attachments.paths, "DATA_DIR", Path(td) / "data"):
                meta = attachments.save_pending(
                    "note.txt", base64.b64encode(b"ok").decode("ascii"))
                delivered = attachments.commit_to_workdir(str(workdir), [meta["id"]])

            self.assertEqual(delivered[0]["path"], "_attachments/note.txt")
            self.assertEqual((workdir / "_attachments" / "note.txt").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
