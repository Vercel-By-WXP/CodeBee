"""Static contracts for the queue-first conversation delivery controls."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "app" / "ui"


class ChatDeliveryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "chat-delivery.css").read_text(encoding="utf-8")

    def test_default_queue_is_visible_and_immediate_option_is_explicit(self):
        self.assertIn('id="rd-chat-send-label"', self.html)
        self.assertIn('class="ico chat-send-chevron"', self.html)
        self.assertNotIn('>⌄</button>', self.html)
        self.assertIn('data-i18n="排队（默认）"', self.html)
        self.assertIn('data-chat-delivery="now"', self.html)
        self.assertIn("立即发送并引导", self.html)

    def test_keyboard_contract_matches_codex_style_split_send(self):
        self.assertIn('e.ctrlKey || e.metaKey ? "now" : "queue"', self.js)
        self.assertIn('"delivery": "now" if active else "sent"', (ROOT.parent / "main.py").read_text(encoding="utf-8"))
        self.assertIn("Ctrl+Enter", self.js)
        self.assertIn("chat-send-menu", self.css)

    def test_queue_has_retract_affordance(self):
        self.assertIn("data-chat-retract", self.js)
        self.assertIn("/messages/retract", self.js)
        self.assertIn("已撤回排队消息", (ROOT / "i18n.js").read_text(encoding="utf-8"))

    def test_control_retry_requires_successful_takeover(self):
        self.assertIn('const acquired = await ctrlAction("acquire", true, true);', self.js)
        self.assertIn('if (acquired) {', self.js)
        self.assertIn('return !!d.control;', self.js)

    def test_stale_conversation_error_moves_to_server_run(self):
        self.assertIn('jumpToRun(error.data.run_id);', self.js)
        self.assertIn('对话已进入新一轮，已切换到最新会话，请重新发送', self.js)


if __name__ == "__main__":
    unittest.main()
