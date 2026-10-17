"""Tests for detecting usable native sandbox support."""
from __future__ import annotations

import subprocess
import os
import unittest
from unittest import mock


class TestNativeSandboxAvailable(unittest.TestCase):
    def setUp(self):
        from app.core import sandbox_runtime
        sandbox_runtime._BWRAP_PROBE_CACHE.clear()

    def test_missing_or_failed_bwrap_is_unavailable(self):
        from app.core import sandbox_runtime
        with mock.patch.object(sandbox_runtime.os, "name", "posix"), \
                mock.patch.object(sandbox_runtime.sys, "platform", "linux"), \
                mock.patch.object(sandbox_runtime.shutil, "which", return_value="/usr/bin/bwrap"), \
                mock.patch.object(sandbox_runtime.subprocess, "run",
                                  side_effect=subprocess.CalledProcessError(1, "bwrap")):
            self.assertFalse(sandbox_runtime.native_sandbox_available())

    def test_success_is_cached_per_executable(self):
        from app.core import sandbox_runtime
        completed = subprocess.CompletedProcess(["bwrap"], 0)
        with mock.patch.object(sandbox_runtime.os, "name", "posix"), \
                mock.patch.object(sandbox_runtime.sys, "platform", "linux"), \
                mock.patch.object(sandbox_runtime.shutil, "which", return_value="/usr/bin/bwrap"), \
                mock.patch.object(sandbox_runtime.subprocess, "run", return_value=completed) as run:
            self.assertTrue(sandbox_runtime.native_sandbox_available())
            self.assertTrue(sandbox_runtime.native_sandbox_available())
            run.assert_called_once()

    def test_installed_bwrap_can_launch_probe(self):
        from app.core import sandbox_runtime
        required = os.environ.get("CODEBEE_REQUIRE_BWRAP") == "1"
        if not sandbox_runtime.shutil.which("bwrap"):
            if required:
                self.fail("CODEBEE_REQUIRE_BWRAP=1 but bubblewrap is not installed")
            self.skipTest("bubblewrap is not installed")
        if sandbox_runtime.os.name != "posix" or sandbox_runtime.sys.platform == "darwin":
            if required:
                self.fail("CODEBEE_REQUIRE_BWRAP=1 requires Linux bubblewrap")
            self.skipTest("native bubblewrap is Linux-only")
        if not sandbox_runtime.native_sandbox_available():
            if required:
                self.fail("bwrap is installed but cannot create a namespace on this host")
            self.skipTest("bubblewrap namespaces are unavailable on this host")


if __name__ == "__main__":
    unittest.main()
