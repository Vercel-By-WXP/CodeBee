import unittest

from app.core.publish.flow import _click_match_js


class ClickMatchSemanticsTests(unittest.TestCase):
    def test_any_keywords_are_alternatives(self):
        script = _click_match_js()
        self.assertIn("keys.some(k=>x.includes(k))", script)
        self.assertNotIn("keys.every(k=>x.includes(k))", script)
        self.assertIn("包含任一关键词", script)


if __name__ == "__main__":
    unittest.main()
