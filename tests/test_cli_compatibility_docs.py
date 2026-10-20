import re
import unittest
from pathlib import Path

from app.core.catalog import DEFAULT_CATALOG


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _cli_rows():
    section = README.split("## CLI 接入范围", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = re.fullmatch(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if match and match.group(1).strip() not in ("CLI", "---"):
            name, status, evidence = (cell.strip() for cell in match.groups())
            if name in rows:
                raise AssertionError("duplicate CLI compatibility row: %s" % name)
            rows[name] = (status, evidence)
    return rows


class CliCompatibilityDocsTests(unittest.TestCase):
    def test_readme_lists_each_catalog_cli_once(self):
        rows = _cli_rows()
        expected = {entry["name"] for entry in DEFAULT_CATALOG}
        self.assertEqual(set(rows), expected)

    def test_readme_distinguishes_dispatch_from_detection_only(self):
        rows = _cli_rows()
        for entry in DEFAULT_CATALOG:
            status, _ = rows[entry["name"]]
            dispatchable = bool((entry.get("orch") or {}).get("kind"))
            if dispatchable:
                self.assertIn("自动编排", status, entry["name"])
            else:
                self.assertIn("仅检测", status, entry["name"])

    def test_readme_requires_version_and_platform_evidence(self):
        rows = _cli_rows()
        for entry in DEFAULT_CATALOG:
            _, evidence = rows[entry["name"]]
            self.assertTrue(evidence, entry["name"])
        section = README.split("## CLI 接入范围", 1)[1].split("\n## ", 1)[0]
        self.assertIn("版本", section)
        self.assertIn("系统", section)
        self.assertIn("验收", section)

    def test_readme_links_the_release_evidence_template(self):
        self.assertIn("[CLI_COMPATIBILITY.md](CLI_COMPATIBILITY.md)", README)
        self.assertTrue((ROOT / "CLI_COMPATIBILITY.md").is_file())


if __name__ == "__main__":
    unittest.main()
