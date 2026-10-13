"""Static contracts for the narrow-screen running-task chat affordance."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MobileChatUiTests(unittest.TestCase):
    def test_chat_input_reenters_visual_viewport_after_mobile_keyboard_resize(self):
        js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.visualViewport.addEventListener(\"resize\", keepChatInputVisible)", js)
        self.assertIn("ta.scrollIntoView({ block: \"nearest\", inline: \"nearest\" })", js)

    def test_mobile_chat_bar_respects_safe_area_and_history_padding(self):
        css = (ROOT / "app" / "ui" / "style.css").read_text(encoding="utf-8")
        self.assertIn("#run-detail.chat-mode .rd-chat-bar", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("scroll-padding-bottom: 132px", css)

    def test_running_chat_surfaces_goal_controls_and_cli_process_output(self):
        js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="rd-chat-run-status"', html)
        self.assertIn("data-chat-run-pause", html)
        self.assertIn("data-chat-run-cancel", html)
        self.assertIn("chatLoadLog(it.run, it.log)", js)
        self.assertIn('class="chat-cli-log"', js)
        self.assertIn("it.acp_events", js)
        self.assertLess(js.index("chatLoadLog(it.run, it.log)"),
                        js.index("if (liveSig === chatLiveSig"),
                        "running CLI logs must refresh before the timeline signature guard")

    def test_chat_workspace_editor_has_tree_read_save_and_responsive_layout(self):
        js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "app" / "ui" / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="rd-chat-workspace"', html)
        self.assertIn('data-chat-editor-save', html)
        self.assertIn("/api/dir/scan?path=", js)
        self.assertIn("/api/dir/file?dir=", js)
        self.assertIn("/api/dir/save", js)
        self.assertIn("chat-workspace-open", css)
        self.assertIn("@media (max-width: 900px)", css)


if __name__ == "__main__":
    unittest.main()
