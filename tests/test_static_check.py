import unittest
from unittest import mock


from tools import static_check


class StaticCheckTests(unittest.TestCase):
    def test_relative_file_normalizes_windows_and_posix_absolute_paths(self):
        self.assertEqual(
            static_check._relative_file(
                r"E:\GoOut\MultiAgentOrchestration\app\core\sample.py"
            ),
            "app/core/sample.py",
        )
        self.assertEqual(
            static_check._relative_file(
                "/home/runner/work/codebee/codebee/tools/sample.py"
            ),
            "tools/sample.py",
        )

    def test_run_rejects_fatal_tool_exit_code(self):
        completed = mock.Mock(returncode=2, stdout='{"diagnostics": []}', stderr="bad config")
        with mock.patch.object(static_check.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "exit 2"):
                static_check._run("fake-tool", [])

    def test_baseline_count_must_match_unique_fingerprints(self):
        with self.assertRaisesRegex(RuntimeError, "baseline count"):
            static_check._section_fingerprints(
                {"count": 2, "fingerprints": ["same", "same"]}
            )

    def test_only_diagnostics_missing_from_baseline_are_new(self):
        baseline = {
            static_check.fingerprint({"file": "app/a.py", "code": "F401"})
        }
        current = [
            {"file": "app/a.py", "code": "F401"},
            {"file": "app/b.py", "code": "F821"},
        ]

        self.assertEqual(
            static_check.new_diagnostics(current, baseline),
            [{"file": "app/b.py", "code": "F821"}],
        )

    def test_empty_current_diagnostics_are_clean(self):
        self.assertEqual(
            static_check.new_diagnostics(
                [{"file": "app/a.py", "code": "F401"}],
                {static_check.fingerprint({"file": "app/a.py", "code": "F401"})},
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
