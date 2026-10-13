"""Contracts for the CodeBee browser workspace's publisher-session bridge."""
import unittest
from unittest.mock import Mock, patch

from core.publish import bridge


class BrowserBridgeTargetTests(unittest.TestCase):
    def test_platform_urls_are_restricted_to_publisher_domains(self):
        self.assertEqual(
            bridge.platform_for_url("https://fanqienovel.com/main/writer/"),
            "fanqie",
        )
        self.assertEqual(
            bridge.platform_for_url("https://zuozhe.qimao.com/front/book-manage"),
            "qimao",
        )
        self.assertIsNone(bridge.platform_for_url("https://example.com/"))

    def test_platform_urls_reject_credentials_and_non_https_schemes(self):
        self.assertIsNone(
            bridge.platform_for_url("https://user:pass@fanqienovel.com/")
        )
        self.assertIsNone(bridge.platform_for_url("http://fanqienovel.com/"))
        self.assertIsNone(bridge.platform_for_url("javascript:alert(1)"))

    def test_pointer_actions_require_bounded_coordinates(self):
        self.assertEqual(
            bridge.normalize_action({"action": "click", "x": 300, "y": 200}),
            {"action": "click", "x": 300, "y": 200},
        )
        with self.assertRaises(bridge.BridgeError):
            bridge.normalize_action({"action": "click", "x": -1, "y": 200})
        with self.assertRaises(bridge.BridgeError):
            bridge.normalize_action({"action": "click", "x": 9000, "y": 200})

    def test_text_actions_are_bounded_and_unknown_actions_rejected(self):
        self.assertEqual(
            bridge.normalize_action({"action": "text", "text": "标题"}),
            {"action": "text", "text": "标题"},
        )
        with self.assertRaises(bridge.BridgeError):
            bridge.normalize_action({"action": "unknown"})
        with self.assertRaises(bridge.BridgeError):
            bridge.normalize_action({"action": "text", "text": "x" * 5000})

    def test_keyboard_modifiers_are_explicit_cdp_flags(self):
        self.assertEqual(
            bridge.normalize_action({"action": "key", "key": "a", "ctrl": True}),
            {"action": "key", "key": "a", "modifiers": 2},
        )

    def test_navigate_reuses_the_platform_publisher_profile(self):
        page = Mock()
        page.url.return_value = "https://fanqienovel.com/main/writer/"
        page.send.return_value = {"data": "cG5n"}
        browser = Mock()
        browser.first_page.return_value = page
        with patch.object(bridge, "_ensure_session", return_value=browser) as ensure:
            result = bridge.navigate("fanqie", "https://fanqienovel.com/main/writer/")
        ensure.assert_called_once_with("fanqie")
        browser.first_page.assert_called_once_with(create=True)
        page.navigate.assert_called_once_with("https://fanqienovel.com/main/writer/", timeout=30.0)
        self.assertEqual(result["image"], "cG5n")

    def test_publish_busy_state_blocks_workspace_snapshot(self):
        with patch.object(bridge.manager, "_st", return_value={"status": "busy"}):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.snapshot("fanqie")
        self.assertEqual(caught.exception.status, 409)

    def test_snapshot_rejects_a_tab_redirected_outside_the_platform(self):
        page = Mock()
        page.url.return_value = "https://example.com/"
        browser = Mock()
        browser.first_page.return_value = page
        with patch.object(bridge, "_ensure_session", return_value=browser):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.snapshot("fanqie")
        self.assertEqual(caught.exception.status, 403)
        page.send.assert_not_called()

    def test_input_rejects_a_tab_redirected_outside_the_platform(self):
        page = Mock()
        page.url.return_value = "https://example.com/"
        browser = Mock()
        browser.first_page.return_value = page
        with patch.object(bridge, "_ensure_session", return_value=browser):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.input_event("fanqie", {"action": "text", "text": "secret"})
        self.assertEqual(caught.exception.status, 403)
        page.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
