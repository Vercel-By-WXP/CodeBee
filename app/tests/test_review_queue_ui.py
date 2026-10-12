"""Run functional queue UI regressions without browser or third-party libraries."""
import shutil
import subprocess
import unittest
from pathlib import Path


class ReviewQueueUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for UI logic tests")
    def test_queue_forms_validation_errors_and_concurrent_requests(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [shutil.which("node"), "--test", "tests/review_queue_logic.test.mjs"],
            cwd=root, capture_output=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
