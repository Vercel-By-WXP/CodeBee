import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")


class HiveSceneContractTests(unittest.TestCase):
    def test_hive_scene_has_viewport_and_accessible_controls(self):
        self.assertIn('id="rd-hive-viewport"', INDEX)
        self.assertIn('data-hive-view="3d"', INDEX)
        self.assertIn('data-hive-view="2d"', INDEX)
        self.assertIn('data-hive-scene-action="reset"', INDEX)
        self.assertIn('data-hive-scene-action="zoom-in"', INDEX)
        self.assertIn('data-hive-scene-action="zoom-out"', INDEX)

    def test_hive_scene_has_pointer_pan_and_wheel_zoom(self):
        self.assertIn("function setupHiveSceneControls", APP)
        self.assertIn('addEventListener("pointerdown"', APP)
        self.assertIn('addEventListener("pointermove"', APP)
        self.assertIn('addEventListener("wheel"', APP)
        self.assertIn("hiveSceneTransform", APP)

    def test_hive_scene_styles_include_3d_and_flat_fallback(self):
        self.assertIn("transform-style: preserve-3d", STYLE)
        self.assertIn("perspective:", STYLE)
        self.assertIn("#rd-hive-cells.hive-2d", STYLE)
        self.assertIn("@media (prefers-reduced-motion: reduce)", STYLE)


if __name__ == "__main__":
    unittest.main()
