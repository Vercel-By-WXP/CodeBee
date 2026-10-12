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


if __name__ == "__main__":
    unittest.main()
