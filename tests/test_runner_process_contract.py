import sys
import tempfile
import unittest
from pathlib import Path
import os

from app.core import runner


class RunnerProcessContractTests(unittest.TestCase):
    def test_generic_adapter_template_keeps_prompt_as_one_argv_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_generic_cli.py"
            script.write_text(
                "import sys\n"
                "sys.stdout.write(sys.argv[1])\n",
                encoding="utf-8",
            )
            prompt = "任务：保留空格、引号 '、& 和中文雪☃"
            agent = {
                "command": sys.executable,
                "argv_template": [str(script), "{prompt}"],
            }
            argv, stdin_text, _prompt, temp_files = runner._build_call(
                agent, "generic", "", False, None, prompt, workdir=tmp)
            try:
                self.assertIsNone(stdin_text)
                result = runner.run_process(
                    argv=argv, cwd=tmp,
                    env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    timeout=15)
            finally:
                for path in temp_files:
                    Path(path).unlink(missing_ok=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"], prompt)

    def test_argv_and_stdin_preserve_unicode_and_large_pipes_do_not_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_cli.py"
            script.write_text(
                "import sys\n"
                "payload = sys.stdin.read()\n"
                "sys.stdout.write(sys.argv[1] + payload + 'O' * 131072)\n"
                "sys.stderr.write('E' * 131072)\n",
                encoding="utf-8",
            )
            argument = "参数 with spaces 'quotes' & symbols"
            payload = "stdin 内容：雪だるま ☃\n"
            result = runner.run_process(
                argv=[sys.executable, str(script), argument],
                stdin_text=payload,
                cwd=tmp,
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                timeout=15,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"].replace("\r\n", "\n"),
                         argument + payload + "O" * 131072)
        self.assertEqual(result["stderr"], "E" * 131072)
        self.assertEqual(result["exit_code"], 0)

    def test_missing_executable_returns_a_stable_launch_failure(self):
        result = runner.run_process(
            argv=["codebee-test-command-that-does-not-exist"], timeout=5)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["exit_code"])
        self.assertIn("启动失败", result["stderr"])
        self.assertIn("找不到可执行文件", result["stderr"])

    def test_audit_log_redacts_argv_secrets_and_is_private_on_posix(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_log_cli.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            log_path = Path(tmp) / "step.log"
            result = runner.run_process(
                argv=[sys.executable, str(script), "--api-key", "test-secret-value"],
                cwd=tmp,
                log_path=str(log_path),
                timeout=15,
            )

            self.assertTrue(result["ok"], result)
            contents = log_path.read_text(encoding="utf-8")
            self.assertNotIn("test-secret-value", contents)
            self.assertIn("<redacted>", contents)
            if os.name != "nt":
                self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
