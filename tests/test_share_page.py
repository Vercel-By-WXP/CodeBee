"""HTML share reports preserve escaped placeholder-like user text."""
import unittest

from app.core.share_page import render_share_html


class SharePageTests(unittest.TestCase):
    def test_report_tokens_are_not_consumed_as_later_template_fields(self):
        page = render_share_html(
            {"status": "done", "steps": []},
            {"title": "标题 __META__", "goal": "目标 __REPORT__"},
            "报告含有 __TITLE__ 和 __STEPS__",
        )

        self.assertIn("标题 __META__", page)
        self.assertIn("目标 __REPORT__", page)
        self.assertIn("报告含有 __TITLE__ 和 __STEPS__", page)
        self.assertIn("<title>标题 __META__ · CodeBee 任务报告</title>", page)


if __name__ == "__main__":
    unittest.main()
