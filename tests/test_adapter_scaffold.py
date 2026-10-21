import tempfile
import unittest
from pathlib import Path

from app.core import adapter_scaffold


class AdapterScaffoldTests(unittest.TestCase):
    def test_generated_contract_test_is_discoverable_from_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = adapter_scaffold.generate(
                tmp, name="Example CLI", executable="example-cli")
            test_path = Path(result["test"])
            self.assertEqual(test_path.parent, Path(tmp) / "tests")
            self.assertIn("from app.core.adapters import example_cli",
                          test_path.read_text(encoding="utf-8"))
            self.assertTrue(Path(result["adapter"]).is_file())


if __name__ == "__main__":
    unittest.main()
