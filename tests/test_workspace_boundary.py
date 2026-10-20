import tempfile
import unittest
from pathlib import Path

from app.core.workspace_boundary import WorkspaceBoundary, WorkspaceBoundaryError


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_rejects_absolute_and_escape_paths(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = WorkspaceBoundary(root)
            for path in ("../outside.txt", "C:/outside.txt", "\\\\server\\share"):
                with self.assertRaises(WorkspaceBoundaryError):
                    workspace.resolve(path)

    def test_atomic_write_and_read_limit(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = WorkspaceBoundary(root, max_write_bytes=8, max_read_bytes=8)
            workspace.write("out.txt", "hello")
            self.assertEqual(workspace.read("out.txt"), "hello")
            with self.assertRaises(WorkspaceBoundaryError):
                workspace.write("too-large.txt", "123456789")

    def test_format_lock_requires_declared_channel(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = WorkspaceBoundary(
                root, format_locks=[{"extensions": [".pdf"],
                                    "channel": "report.export",
                                    "reason": "报告统一导出"}])
            with self.assertRaises(WorkspaceBoundaryError):
                workspace.write("report.pdf", "x")
            workspace.write("report.pdf", "x", channel="report.export")
            self.assertEqual(Path(root, "report.pdf").read_text(encoding="utf-8"), "x")


if __name__ == "__main__":
    unittest.main()
