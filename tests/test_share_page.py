# -*- coding: utf-8 -*-
"""可分享报告页（share_page，借鉴 agency-orchestrator ao report）单测。

跑法：python -m unittest discover -s tests -p "test_share_page.py" -v
"""
from __future__ import annotations

from base import BaseTest


class SharePageTests(BaseTest):

    def _render(self, report="## 评分\n- 情节 9.0"):
        from app.core import share_page
        run = {"id": "r-s", "title": "夜更两章", "status": "done",
               "verdict": {"overall": 8.8, "publishable": True,
                           "scores": {"情节": 9.0}, "threshold": 7.0},
               "steps": [{"n": 1, "role": "draft", "agent_label": "Kimi",
                          "summary": "初稿完成", "status": "done"}]}
        task = {"title": "测试书", "goal": "写两章"}
        return share_page.render_share_html(run, task, report)

    def test_selfcontained_html(self):
        html = self._render()
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("测试书", html)
        self.assertIn("已完成", html)
        self.assertIn("8.8", html)
        self.assertIn("情节", html)
        self.assertIn("Kimi", html)
        self.assertIn("初稿完成", html)
        self.assertIn("评分", html)          # 报告正文注入
        self.assertNotIn("<script", html.lower())   # 零 JS 自包含

    def test_escapes_user_content(self):
        html = self._render("<script>alert(1)</script>")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_empty_report_fallback(self):
        html = self._render("")
        self.assertIn("暂无报告", html)


if __name__ == "__main__":
    unittest.main()
