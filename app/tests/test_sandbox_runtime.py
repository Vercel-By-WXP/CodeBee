"""Linux integration tests for the actual bubblewrap execution boundary."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import policy


_BWRAP_READY = sys.platform.startswith("linux") and bool(shutil.which("bwrap"))
_REQUIRE_BWRAP = os.environ.get("CODEBEE_REQUIRE_BWRAP") == "1"


class BubblewrapRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _BWRAP_READY:
            message = "requires Linux and an executable bubblewrap backend"
            if _REQUIRE_BWRAP:
                raise RuntimeError(message)
            raise unittest.SkipTest(message)
        probe = subprocess.run(
            [shutil.which("bwrap"), "--die-with-parent", "--new-session",
             "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev",
             "--", "/bin/true"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
        if probe.returncode:
            detail = probe.stderr.decode("utf-8", errors="replace").strip()
            message = "bubblewrap user namespace unavailable: %s" % detail[:200]
            if _REQUIRE_BWRAP:
                raise RuntimeError(message)
            raise unittest.SkipTest(message)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name).resolve()
        self.workdir = self.base / "workspace"
        self.workdir.mkdir()
        self.outside = self.base / "host-secret.txt"
        self.outside.write_text("host-only-secret", encoding="utf-8")
        self.sandbox = policy.normalize_sandbox({
            "allowed_roots": [str(self.workdir)],
            "env_allowlist": [],
            "network": True,
            "timeout_s": 20,
        }, self.workdir)

    def test_shell_and_descendant_are_confined_to_workspace_and_network_namespace(self):
        from core import builtin_agent

        (self.workdir / "inside.txt").write_text("workspace-visible", encoding="utf-8")
        command = (
            "printf 'cwd=%s\\n' \"$PWD\"; "
            "cat inside.txt; "
            "if test -r ../host-secret.txt; then echo outside-visible; "
            "else echo outside-blocked; fi; "
            "sh -c 'if test -r ../host-secret.txt; then echo child-outside-visible; "
            "else echo child-outside-blocked; fi; printf child-created > child.txt'; "
            "if grep -q ' 00000000 ' /proc/net/route; then echo default-route-visible; "
            "else echo network-isolated; fi"
        )

        result = builtin_agent._exec_tool(
            str(self.workdir), "run_command", {"command": command},
            sandbox=self.sandbox)

        self.assertIn("退出码: 0", result)
        self.assertIn("cwd=%s" % self.workdir, result)
        self.assertIn("workspace-visible", result)
        self.assertIn("outside-blocked", result)
        self.assertIn("child-outside-blocked", result)
        self.assertEqual((self.workdir / "child.txt").read_text(encoding="utf-8"),
                         "child-created")
        self.assertEqual(self.outside.read_text(encoding="utf-8"), "host-only-secret")

    def test_network_isolation_when_kernel_allows_network_namespaces(self):
        probe = subprocess.run(
            [shutil.which("bwrap"), "--die-with-parent", "--new-session",
             "--unshare-net", "--", "/bin/true"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
        if probe.returncode:
            detail = probe.stderr.decode("utf-8", errors="replace").strip()
            if _REQUIRE_BWRAP:
                self.fail("network namespace unavailable: %s" % detail[:200])
            self.skipTest("network namespace unavailable: %s" % detail[:200])

        from core import builtin_agent
        sandbox = dict(self.sandbox, network=False)
        result = builtin_agent._exec_tool(
            str(self.workdir), "run_command",
            {"command": "if grep -q ' 00000000 ' /proc/net/route; then "
                       "echo default-route-visible; else echo network-isolated; fi"},
            sandbox=sandbox)

        self.assertIn("退出码: 0", result)
        self.assertIn("network-isolated", result)

    def test_mcp_server_process_runs_inside_the_declared_workspace_boundary(self):
        from core import mcp_client

        python = next((path for path in ("/usr/bin/python3", "/bin/python3")
                       if Path(path).is_file()), None)
        if not python:
            self.skipTest("python3 is required for the MCP runtime fixture")
        script = self.workdir / "sandbox_mcp_server.py"
        script.write_text(
            "import json, os, sys\n"
            "for line in sys.stdin:\n"
            "    req = json.loads(line)\n"
            "    method = req.get('method')\n"
            "    if method == 'notifications/initialized':\n"
            "        continue\n"
            "    if method == 'initialize':\n"
            "        result = {'protocolVersion': '2024-11-05', 'capabilities': {}, "
            "'serverInfo': {'name': 'sandbox-test', 'version': '1'}}\n"
            "    elif method == 'tools/call':\n"
            "        outside = os.path.exists(os.path.join(os.getcwd(), '..', 'host-secret.txt'))\n"
            "        result = {'content': [{'type': 'text', 'text': "
            "'cwd=' + os.getcwd() + ';outside=' + str(outside)}]}\n"
            "    else:\n"
            "        result = {}\n"
            "    if 'id' in req:\n"
            "        print(json.dumps({'jsonrpc': '2.0', 'id': req['id'], "
            "'result': result}), flush=True)\n",
            encoding="utf-8")
        server = {"name": "sandboxprobe", "command": python,
                  "args": [str(script)]}

        result = mcp_client.call_tool(
            server, "probe", {}, timeout_s=10,
            sandbox=self.sandbox, workdir=str(self.workdir))

        self.assertTrue(result["ok"], result.get("error"))
        self.assertIn("cwd=%s" % self.workdir, result["text"])
        self.assertIn("outside=False", result["text"])
        self.assertEqual(self.outside.read_text(encoding="utf-8"), "host-only-secret")


if __name__ == "__main__":
    unittest.main()
