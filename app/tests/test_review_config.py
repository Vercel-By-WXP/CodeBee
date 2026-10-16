import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import adapter_scaffold, review_config, task_templates


class ReviewConfigTest(unittest.TestCase):
    def test_weighted_score_and_degradation(self):
        dims = [{"id": "a", "name": "A", "weight": 2}, {"id": "b", "name": "B", "weight": 1}]
        self.assertEqual(review_config.weighted_score({"a": 8, "b": 5}, dims), 7.0)
        verdict = review_config.assess({"scores": {"a": 5}, "confidence": 0.1},
                                       {"dimensions": dims, "min_score": 7, "min_confidence": 0.5})
        self.assertEqual(verdict["review_quality"], "degraded")
        self.assertTrue(verdict["review_degraded"])

    def test_scaffold_is_safe_and_parseable(self):
        with tempfile.TemporaryDirectory() as root:
            result = adapter_scaffold.generate(root, name="My CLI", executable="my-cli")
            self.assertTrue(Path(result["adapter"]).is_file())
            source = Path(result["adapter"]).read_text(encoding="utf-8")
            compile(source, result["adapter"], "exec")

    def test_builtin_templates_present(self):
        ids = {row["id"] for row in task_templates.list_templates()}
        self.assertTrue({"refactor", "bugfix", "feature", "review", "docs", "tests"} <= ids)


if __name__ == "__main__":
    unittest.main()
