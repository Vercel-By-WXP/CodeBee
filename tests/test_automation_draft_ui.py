# -*- coding: utf-8 -*-
"""定时任务表单草稿的前端契约回归。"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestAutomationDraftUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")

    def test_new_form_restores_local_draft_and_binds_all_fields(self):
        src = self.source
        self.assertIn('const AUTO_DRAFT_KEY = "codebee.automationDraft.v1";', src)
        self.assertIn("const restoredDraft = !task && !tpl ? readAutoDraft() : null;", src)
        self.assertIn("if (!task) bindAutoDraftAutosave(formToken);", src)
        for field in ("au-name", "au-prompt", "au-workdir", "au-kind", "au-flow",
                      "au-mode", "au-thinking", "au-fresh", "au-time", "au-runat"):
            self.assertIn('"%s"' % field, src)

    def test_draft_is_cleared_only_after_successful_create(self):
        src = self.source
        self.assertIn("if (!id) clearAutoDraft();", src)
        self.assertIn("function autoDiscardDraft()", src)
        self.assertIn("localStorage.removeItem(AUTO_DRAFT_KEY)", src)
        self.assertIn("if (formToken !== autoDraftFormToken || !$(\"au-name\")) return;", src)
        self.assertIn("const formToken = ++autoDraftFormToken;", src)


if __name__ == "__main__":
    unittest.main()
