"""The publishing entry points default to unattended submission."""
import inspect
import sys
import unittest
from unittest import mock
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class UnattendedPublishingDefaultsTests(unittest.TestCase):
    def test_shipped_platform_flows_have_real_unattended_completion_contracts(self):
        import json
        from core.publish import auto, manager

        for platform in ("qimao", "fanqie"):
            path = Path(manager.__file__).with_name(
                "flows-%s-calibrated.json" % platform)
            data = json.loads(path.read_text(encoding="utf-8"))
            for action in ("create_book", "upload_chapter", "upload_chapter_draft"):
                with self.subTest(platform=platform, action=action):
                    self.assertTrue(manager._flow_contract_ok(
                        platform, action, data[action]))
                    self.assertTrue(auto.flow_calibrated(platform, action))

    def test_flow_without_remote_completion_evidence_is_not_calibrated(self):
        from core.publish import manager

        self.assertFalse(manager._flow_contract_ok("fanqie", "upload_chapter", [
            {"do": "submit", "text": "下一步"},
            {"do": "fanqie_publish"},
        ]))
        self.assertFalse(manager._flow_contract_ok("fanqie", "create_book", [
            {"do": "submit", "text": "立即创建"},
        ]))
        self.assertFalse(manager._flow_contract_ok("fanqie", "upload_chapter_draft", [
            {"do": "submit", "text": "存草稿"},
            {"do": "expect_text", "any": ["保存成功"]},
        ]))

    def test_all_publish_entry_points_default_to_auto_submit(self):
        from core.publish import auto, manager

        for fn in (manager.create_book_async, manager.upload_chapter_async,
                   auto.publish_pending_async):
            with self.subTest(function=fn.__name__):
                self.assertIs(inspect.signature(fn).parameters["auto_submit"].default,
                              True)

    def test_scheduled_publish_defaults_to_auto_submit(self):
        from core.publish import auto

        normalized, error = auto.norm_auto_publish({
            "platform": "fanqie", "time": "08:00", "enabled": True,
        })
        self.assertEqual(error, "")
        self.assertIs(normalized["auto_submit"], True)

    def test_legacy_scheduled_fill_only_flag_is_upgraded_to_auto_submit(self):
        from core.publish import auto

        normalized, error = auto.norm_auto_publish({
            "platform": "qimao", "time": "08:00", "enabled": True,
            "auto_submit": False,
        })
        self.assertEqual(error, "")
        self.assertIs(normalized["auto_submit"], True)

    def test_legacy_false_batch_flag_cannot_restore_manual_pause(self):
        from core.publish import auto

        with mock.patch.object(auto, "calibrated", return_value=False):
            ok, error = auto.publish_pending_async(
                "task-1", "fanqie", auto_submit=False)

        self.assertFalse(ok)
        self.assertIn("自动提交模式需要先校准", error)

    def test_daily_publish_cap_zero_really_disables_the_limit(self):
        from core.publish import auto

        with mock.patch.object(auto, "_settings", return_value={
                "publish_daily_cap": 0}):
            self.assertEqual(auto.daily_cap(), 0)
        with mock.patch.object(auto, "_settings", return_value={}):
            self.assertEqual(auto.daily_cap(), 10)
        with mock.patch.object(auto, "_settings", return_value={
                "publish_daily_cap": "invalid"}):
            self.assertEqual(auto.daily_cap(), 10)

    def test_legacy_false_book_flag_cannot_restore_manual_submit(self):
        from core import store
        from core.publish import ledger, manager

        task = {"id": "task-1", "workdir": "C:/tmp/task-1"}
        meta = {"status": "done", "data": {"book_name": "测试作品"}}
        lock = mock.Mock()
        seen = {}

        def release_flags(auto_submit, **kwargs):
            seen["auto_submit"] = auto_submit
            return {"force": True, "force_confirmed": True,
                    "force_reason": "test"}

        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(store, "inherited_book_meta", return_value=meta), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(manager, "_st", return_value={}), \
                mock.patch.object(manager, "_create_preflight", return_value=""), \
                mock.patch.object(manager, "automatic_release_kwargs", side_effect=release_flags), \
                mock.patch.object(manager, "_quality_release_guard", return_value={"allowed": True}), \
                mock.patch.object(manager, "_try_action_lock", return_value=lock), \
                mock.patch.object(manager, "_login_guard", return_value=(False, "not logged in")):
            ok, error = manager.create_book_async(
                "task-1", "fanqie", auto_submit=False)

        self.assertFalse(ok)
        self.assertIn("not logged in", error)
        self.assertTrue(seen["auto_submit"])
        lock.release.assert_called_once_with()

    def test_legacy_false_single_chapter_flag_cannot_restore_manual_submit(self):
        from core import store
        from core.publish import ledger, manager

        task = {"id": "task-1", "workdir": "C:/tmp/task-1"}
        book = {"book_id": "book-1", "title": "测试作品"}
        lock = mock.Mock()
        seen = {}

        def release_flags(auto_submit, **kwargs):
            seen["auto_submit"] = auto_submit
            return {"force": True, "force_confirmed": True,
                    "force_reason": "test"}

        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(ledger, "book_for", return_value=book), \
                mock.patch.object(manager, "_st", return_value={}), \
                mock.patch.object(manager, "read_chapter",
                                  return_value=(1, "测试章节", "字" * 1200, "")), \
                mock.patch.object(manager, "automatic_release_kwargs",
                                  side_effect=release_flags), \
                mock.patch.object(manager, "_quality_release_guard",
                                  return_value={"allowed": True}), \
                mock.patch.object(ledger, "published_chapters", return_value=set()), \
                mock.patch.object(ledger, "scheduled_chapters", return_value=set()), \
                mock.patch.object(manager, "_try_action_lock", return_value=lock), \
                mock.patch.object(manager, "_login_guard",
                                  return_value=(False, "not logged in")), \
                mock.patch.object(manager.threading, "Thread") as thread:
            ok, error = manager.upload_chapter_async(
                "task-1", "fanqie", "C:/tmp/task-1/chapter-1.md",
                auto_submit=False)

        self.assertFalse(ok)
        self.assertIn("not logged in", error)
        self.assertTrue(seen["auto_submit"])
        lock.release.assert_called_once_with()
        thread.assert_not_called()

    def test_single_chapter_worker_start_failure_releases_lock_and_records_retryable_error(self):
        from core import operations, store
        from core.publish import ledger, manager

        class BrokenThread:
            def __init__(self, target, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        task = {"id": "task-1", "workdir": "C:/tmp/task-1"}
        book = {"book_id": "book-1", "title": "测试作品"}
        lock = mock.Mock()
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(ledger, "book_for", return_value=book), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(manager, "read_chapter",
                                  return_value=(1, "测试章节", "字" * 1200, "")), \
                mock.patch.object(manager, "automatic_release_kwargs", return_value={}), \
                mock.patch.object(manager, "_quality_release_guard",
                                  return_value={"allowed": True}), \
                mock.patch.object(ledger, "published_chapters", return_value=set()), \
                mock.patch.object(ledger, "scheduled_chapters", return_value=set()), \
                mock.patch.object(manager, "_try_action_lock", return_value=lock), \
                mock.patch.object(manager, "_login_guard", return_value=(True, "")), \
                mock.patch.object(manager.threading, "Thread", BrokenThread), \
                mock.patch.object(operations, "begin", return_value="op-1"), \
                mock.patch.object(operations, "finish_exception") as finish_exception, \
                mock.patch.object(operations, "get", return_value={"status": "failed"}), \
                mock.patch.object(ledger, "record") as record, \
                mock.patch.object(manager, "_set") as set_status, \
                mock.patch.object(manager, "_save"):
            ok, error = manager.upload_chapter_async(
                "task-1", "fanqie", "C:/tmp/task-1/chapter-1.md")

        self.assertFalse(ok)
        self.assertIn("发章任务启动失败", error)
        lock.release.assert_called_once_with()
        finish_exception.assert_called_once()
        record.assert_called_once()
        self.assertTrue(any(call.kwargs.get("status") == "connected"
                            for call in set_status.call_args_list))


if __name__ == "__main__":
    unittest.main()
