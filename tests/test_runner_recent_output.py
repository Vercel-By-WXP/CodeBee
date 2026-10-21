import sys
import tempfile
import threading
import unittest
from pathlib import Path

from app.core import cli_text, runner


class RunnerRecentOutputTests(unittest.TestCase):
    def test_output_helpers_remain_available_from_runner(self):
        self.assertIs(runner.clean_cli_text, cli_text.clean_cli_text)
        self.assertIs(runner.decode_output, cli_text.decode_output)
        self.assertIs(runner.pretty_cli_log, cli_text.pretty_cli_log)

    def test_recent_output_window_keeps_only_the_last_bytes(self):
        window = runner._RecentBytes(max_bytes=8)

        window.append(b"first-")
        window.append(b"last-data")

        self.assertEqual(window.snapshot(), b"ast-data")

    def test_abort_guard_still_detects_repeated_stream_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_reconnecting_cli.py"
            script.write_text(
                "import sys, time\n"
                "for _ in range(3):\n"
                "    sys.stdout.write('Reconnecting...\\n')\n"
                "    sys.stdout.flush()\n"
                "    time.sleep(0.08)\n"
                "time.sleep(5)\n",
                encoding="utf-8",
            )

            result = runner.run_process(
                argv=[sys.executable, str(script)],
                cwd=tmp,
                timeout=10,
                repeat_abort=("Reconnecting...", 2),
            )

        self.assertTrue(result["timed_out"])
        self.assertEqual(result["abort_marker"], "Reconnecting...")
        self.assertIn("Reconnecting...", result["stdout"])

    def test_capture_limit_bounds_each_stream_and_marks_the_result_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_large_output_cli.py"
            script.write_text(
                "import sys\n"
                "sys.stdout.write('O' * 65536)\n"
                "sys.stdout.flush()\n"
                "sys.stderr.write('E' * 65536)\n"
                "sys.stderr.flush()\n",
                encoding="utf-8",
            )
            log_path = Path(tmp) / "full-output.log"

            result = runner.run_process(
                argv=[sys.executable, str(script)],
                cwd=tmp,
                timeout=15,
                log_path=str(log_path),
                max_capture_bytes=4096,
            )

            logged = log_path.read_bytes()

        self.assertFalse(result["ok"])
        self.assertTrue(result["output_truncated"])
        self.assertEqual(runner._classify_failure(result), runner.ErrorCode.PARSE_FAIL)
        self.assertEqual(len(result["stdout"].encode("utf-8")), 4096)
        self.assertTrue(result["stderr"].startswith("E" * 4096))
        self.assertIn("输出超过捕获上限", result["stderr"])
        self.assertGreater(len(logged), 2 * 65536)

    def test_default_capture_limit_applies_to_generic_subprocesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_unbounded_cli.py"
            expected_size = 16 * 1024 * 1024 + 1
            script.write_text(
                "import sys\n"
                "sys.stdout.write('O' * %d)\n" % expected_size,
                encoding="utf-8",
            )

            result = runner.run_process(
                argv=[sys.executable, str(script)], cwd=tmp, timeout=15)
            unlimited = runner.run_process(
                argv=[sys.executable, str(script)], cwd=tmp, timeout=15,
                max_capture_bytes=None)

        self.assertFalse(result["ok"])
        self.assertTrue(result["output_truncated"])
        self.assertEqual(
            len(result["stdout"].encode("utf-8")),
            runner.DEFAULT_PROCESS_CAPTURE_BYTES,
        )
        self.assertTrue(unlimited["ok"])
        self.assertFalse(unlimited["output_truncated"])
        self.assertEqual(len(unlimited["stdout"].encode("utf-8")), expected_size)

    def test_log_limit_bounds_disk_growth_without_truncating_captured_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_large_output_cli.py"
            payload_size = 64 * 1024
            script.write_text(
                "import sys\n"
                "sys.stdout.write('O' * %d)\n"
                "sys.stderr.write('E' * %d)\n" % (payload_size, payload_size),
                encoding="utf-8",
            )
            log_path = Path(tmp) / "bounded.log"

            result = runner.run_process(
                argv=[sys.executable, str(script)], cwd=tmp, timeout=15,
                log_path=str(log_path), max_capture_bytes=None,
                max_log_bytes=2048,
            )

            logged = log_path.read_bytes()

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["stdout"]), payload_size)
        self.assertEqual(len(result["stderr"]), payload_size)
        self.assertTrue(result["log_truncated"])
        self.assertGreater(result["log_dropped_bytes"], 0)
        self.assertLessEqual(len(logged), 2048)
        self.assertIn(b"process log truncated", logged)

    def test_log_limit_includes_existing_append_file_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_cli.py"
            script.write_text("print('new output')\n", encoding="utf-8")
            log_path = Path(tmp) / "bounded.log"
            log_path.write_bytes(b"P" * 900)

            result = runner.run_process(
                argv=[sys.executable, str(script)], cwd=tmp, timeout=15,
                log_path=str(log_path), max_log_bytes=1024,
            )

            logged = log_path.read_bytes()

        self.assertLessEqual(len(logged), 1024)
        self.assertTrue(result["log_truncated"])
        self.assertIn(b"process log truncated", logged)

    def test_log_limit_also_bounds_later_runner_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_cli.py"
            script.write_text("print('process output')\n", encoding="utf-8")
            log_path = Path(tmp) / "bounded.log"

            result = runner.run_process(
                argv=[sys.executable, str(script)], cwd=tmp, timeout=15,
                log_path=str(log_path), max_log_bytes=512,
            )
            runner._log_note(log_path, "N" * 4096, max_bytes=512)

            logged = log_path.read_bytes()

        self.assertTrue(result["ok"])
        self.assertLessEqual(len(logged), 512)
        self.assertIn(b"process log truncated", logged)

    def test_log_limit_trims_an_existing_oversized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_cli.py"
            script.write_text("print('new output')\n", encoding="utf-8")
            log_path = Path(tmp) / "oversized.log"
            log_path.write_bytes(b"old-output-" * 1024)

            result = runner.run_process(
                argv=[sys.executable, str(script)], cwd=tmp, timeout=15,
                log_path=str(log_path), max_log_bytes=1024,
            )

            logged = log_path.read_bytes()

        self.assertLessEqual(len(logged), 1024)
        self.assertTrue(result["log_truncated"])
        self.assertGreater(result["log_dropped_bytes"], 0)
        self.assertIn(b"older log content discarded", logged)

    def test_concurrent_process_writer_and_runner_note_share_log_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "concurrent.log"
            process_writer = runner._open_bounded_log(log_path, 100)
            ready = threading.Barrier(2)
            errors = []

            def write_process_output():
                try:
                    ready.wait(timeout=5)
                    process_writer.write(b"P" * 80)
                except Exception as exc:
                    errors.append(exc)

            def write_runner_note():
                try:
                    ready.wait(timeout=5)
                    runner._append_log_bytes(log_path, b"N" * 80, max_bytes=100)
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=write_process_output),
                       threading.Thread(target=write_runner_note)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            process_writer.close()

            logged = log_path.read_bytes()

        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertLessEqual(len(logged), 100)
        self.assertIn(b"process log truncated", logged)


if __name__ == "__main__":
    unittest.main()
