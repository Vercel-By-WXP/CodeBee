import re
from pathlib import Path
import unittest


APP_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js"


class RunDetailLoadingTests(unittest.TestCase):
    def test_run_detail_defers_heavy_panels_until_tab_is_ready(self):
        source = APP_JS.read_text(encoding="utf-8")
        detail = re.search(
            r"async function renderRunDetail\(\) \{(?P<body>.*?)\n\}\n\n/\* 代码版本",
            source,
            re.S,
        )
        self.assertIsNotNone(detail)
        body = detail.group("body")
        self.assertNotIn("loadArtifacts(id)", body)
        self.assertNotIn("await fetch(\"/api/runs/\" + encodeURIComponent(id) + \"/report\")", body)
        self.assertNotIn("await loadArtifacts(id)", body)
        self.assertIn("rdEnsureActiveTabData", source)
        self.assertIn("rdScheduleNonCritical", source)

    def test_run_detail_steps_are_rendered_in_batches(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function renderRunDetailSteps", source)
        self.assertRegex(source, r"function renderRunDetailSteps[\s\S]{0,3000}requestAnimationFrame")


if __name__ == "__main__":
    unittest.main()
