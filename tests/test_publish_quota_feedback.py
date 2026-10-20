"""Fanqie's editor quota feedback must stop without another submission."""
import unittest
from unittest.mock import Mock, patch

from app.core.publish import auto, flow


class FanqieQuotaFeedbackTests(unittest.TestCase):
    def test_editor_quota_stops_before_any_click_and_retains_retry(self):
        for verb in ("提交", "投稿"):
            for exceeds in ("超出", "超过"):
                mark = verb + "字数" + exceeds + "每日上限"
                with self.subTest(mark=mark):
                    page = Mock()
                    page.call.return_value = {
                        "dialogs": [], "body": mark,
                        "url": "https://fanqienovel.com/main/writer/book/publish/chapter",
                    }
                    shot = Mock()
                    with self.assertRaisesRegex(flow.FlowError, "每日上限") as error:
                        flow.run_flow(page, [{"do": "fanqie_publish"}], shot=shot)
                    self.assertTrue(auto._scheduled_error_is_persistent(str(error.exception)))
                    self.assertEqual(page.call.call_count, 1)
                    page.send.assert_not_called()
                    shot.assert_called_once()

    def test_stale_quota_on_management_page_does_not_trigger_editor_rejection(self):
        page = Mock()
        page.call.return_value = {
            "dialogs": [], "body": "提交字数超出每日上限",
            "url": "https://fanqienovel.com/main/writer/book/chapter-manage",
        }
        self.assertEqual(flow.run_flow(page, [{"do": "fanqie_publish"}]), 1)
        self.assertEqual(page.call.call_count, 1)

    def test_active_confirmation_dialog_keeps_its_normal_handling(self):
        page = Mock()
        page.call.side_effect = [
            {"dialogs": ["发布提示 错别字 提交"], "body": "提交字数超出每日上限",
             "url": "https://fanqienovel.com/main/writer/book/publish/chapter"},
            {"ok": True},
            {"dialogs": [], "body": "", "url": "https://fanqienovel.com/chapter-manage"},
        ]
        with patch.object(flow.time, "sleep"):
            self.assertEqual(flow.run_flow(page, [{"do": "fanqie_publish"}]), 1)
        self.assertEqual(page.call.call_args_list[1].args[1:], ("发布提示", "提交"))


if __name__ == "__main__":
    unittest.main()
