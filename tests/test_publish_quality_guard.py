"""Publishing must use the newest completed quality verdict."""
from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core import chaptersafe
from app.core import quality_gate, store
from app.core.publish import ledger, manager


class PublishQualityGuardTests(unittest.TestCase):
    def test_chapter_storage_and_publish_read_strip_internal_review_blocks(self):
        with TemporaryDirectory() as root:
            chaptersafe.atomic_write_chapter(
                root, 35,
                "# 第35章\n正文第一段。\n\n"
                "<event_check>\n剧情要点|已完成\n</event_check>\n\n"
                "正文第二段。\n\n<ledger>\n道具|账册|已出现\n</ledger>\n\n"
                "<continuity>\n闻川|伤情|保持\n</continuity>\n\n"
                "引文中的 <span>普通尖括号</span> 应保留。")
            path = Path(root) / "chapter-35.md"
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("<event_check>", raw)
            self.assertNotIn("<ledger>", raw)
            self.assertNotIn("<continuity>", raw)
            self.assertIn("<span>普通尖括号</span>", raw)

            chapter_no, title, body, error = manager.read_chapter(path)
            self.assertEqual((chapter_no, title, error), (35, "第35章", ""))
            self.assertIn("正文第一段。", body)
            self.assertIn("正文第二段。", body)
            self.assertNotIn("event_check", body)
            self.assertNotIn("ledger", body)
            self.assertNotIn("continuity", body)
            self.assertIn("<span>普通尖括号</span>", body)
            values = manager.chapter_fill_values(chapter_no, title, body, "测试书")
            self.assertEqual(values["chapter_body"], body)

            raw_values = manager.chapter_fill_values(
                35, title, "正文。<ledger>道具|账册|出现</ledger>", "测试书")
            self.assertEqual(raw_values["chapter_body"], "正文。")

    def test_publish_read_is_last_line_of_defense_for_direct_cli_writes(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "chapter-7.md"
            path.write_text(
                "第7章 归档\n正文。\n<ledger>道具|钥匙|出现</ledger>\n",
                encoding="utf-8")
            chapter_no, title, body, error = manager.read_chapter(path)
            self.assertEqual((chapter_no, title, error), (7, "第7章 归档", ""))
            self.assertEqual(body, "正文。")

    def test_release_guard_uses_store_chronology_not_random_run_id_suffix(self):
        older = {
            "id": "r-20261019-120000-9999", "created_at_ns": 100,
            "status": "done", "op": "run",
            "verdict": {"publishable": True, "revision": "older"},
        }
        newer = {
            "id": "r-20261019-120000-0001", "created_at_ns": 200,
            "status": "done", "op": "run",
            "verdict": {"publishable": True, "revision": "newer"},
        }
        with patch.object(store, "task_runs", return_value=[newer, older]), \
                patch.object(ledger, "book_for", return_value={}), \
                patch.object(quality_gate, "local_version",
                             return_value={"fingerprint": "current"}), \
                patch.object(quality_gate, "evaluate_release",
                             return_value={"allowed": True, "blockers": []}) as evaluate:
            decision = manager._quality_release_guard(
                {"id": "task-1", "type": "serial_novel"}, "qimao")

        self.assertTrue(decision["allowed"])
        self.assertEqual(evaluate.call_args.args[0]["revision"], "newer")
        self.assertEqual(
            evaluate.call_args.kwargs["current_manuscript_fingerprint"], "current")


if __name__ == "__main__":
    unittest.main()
