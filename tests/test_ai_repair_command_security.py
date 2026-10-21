import unittest
from unittest import mock

from app.core import jobs, manager


class AiRepairCommandSecurityTests(unittest.TestCase):
    def test_repair_parser_accepts_supported_install_tokens(self):
        cases = (
            ("npm install @scope/package@1.2.3", "linux"),
            ("brew install sample-tool", "darwin"),
            ("python3 -m pip install sample_pkg[extra]", "linux"),
            ("py -3.13 -m pip install sample_pkg==1.2", "win32"),
            ("winget install -e --id Example.Tool", "win32"),
            ("uv tool install sample-tool", "linux"),
        )
        for command, platform in cases:
            with self.subTest(command=command):
                self.assertTrue(jobs._repair_command_allowed(command, platform))

    def test_repair_parser_rejects_shell_syntax_and_incomplete_commands(self):
        cases = (
            ("npm install demo; whoami", "linux"),
            ("npm install demo\nwhoami", "linux"),
            ("npm install $(whoami)", "linux"),
            ("npm install `whoami`", "linux"),
            ("npm install demo && whoami", "linux"),
            ("npm install demo > result.txt", "linux"),
            ("npm install demo | cat", "linux"),
            ("npm install", "linux"),
            ("winget installer Example.Tool", "win32"),
        )
        for command, platform in cases:
            with self.subTest(command=command):
                self.assertIsNone(jobs._repair_command_argv(command, platform))

    def test_ai_repair_runner_uses_argv_without_shell_command(self):
        expected = {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
        with mock.patch.object(manager.runner, "resolve_command", return_value=["npm"]), \
                mock.patch.object(manager.runner, "run_process", return_value=expected) as run:
            result = manager.run_install_argv(["npm", "install", "demo-package"])

        self.assertTrue(result["ok"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["argv"], ["npm", "install", "demo-package"])
        self.assertNotIn("shell_cmd", kwargs)


if __name__ == "__main__":
    unittest.main()
