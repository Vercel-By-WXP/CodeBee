import subprocess
import unittest
from unittest import mock

from scripts import release_gate


class ReleaseGateTestSummaryTests(unittest.TestCase):
    def _run_with_output(self, stdout="", stderr=""):
        completed = subprocess.CompletedProcess(
            args=["test-suite"], returncode=0, stdout=stdout, stderr=stderr)
        with mock.patch.object(release_gate.subprocess, "run", return_value=completed):
            release_gate.run_tests()

    def test_exit_zero_without_unittest_summary_is_rejected(self):
        for output in ("", "suite stopped early", "Ran 0 tests in 0.01s\nOK\n"):
            with self.subTest(output=output), self.assertRaises(release_gate.GateError):
                self._run_with_output(output)

    def test_nonzero_test_run_with_success_looking_summary_is_rejected(self):
        completed = subprocess.CompletedProcess(
            args=["test-suite"], returncode=2,
            stdout="Ran 4 tests in 0.1s\nOK\n", stderr="interrupted")
        with mock.patch.object(release_gate.subprocess, "run", return_value=completed):
            with self.assertRaises(release_gate.GateError):
                release_gate.run_tests()

    def test_stdout_lookalike_cannot_authorize_release(self):
        with self.assertRaises(release_gate.GateError):
            self._run_with_output(stdout="Ran 12 tests in 0.5s\n\nOK\n")

    def test_nonzero_ran_count_and_ok_summary_on_runner_stream_is_accepted(self):
        self._run_with_output(stderr="Ran 12 tests in 0.5s\n\nOK\n")

    def test_success_must_be_terminal_summary(self):
        with self.assertRaises(release_gate.GateError):
            self._run_with_output(stderr=(
                "Ran 12 tests in 0.5s\n\nOK\n"
                "unexpected trailing output\n"))


if __name__ == "__main__":
    unittest.main()
