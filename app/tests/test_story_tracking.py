import tempfile
import unittest
from pathlib import Path

from core import story_tracking


class StoryTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_init_and_commit_render_deterministic_views(self):
        state = story_tracking.init(self.root, "t-1", "测试书", premise="寻找真相")
        self.assertEqual(state["state_revision"], 0)
        result = story_tracking.commit_chapter(
            self.root, 1,
            outline="冲突：主角收到匿名信\n钩子：信中出现已故父亲的签名",
            prose="他拆开信封，里面只有一张泛黄的照片。",
            facts=["主角住在临江市"],
            foreshadowing=[{"id": "F001", "text": "父亲留下匿名信", "status": "active"}],
            next_promises=["查清匿名信来源"],
            author_truth=["父亲并未死亡"], reader_known=["主角收到匿名信"],
        )
        self.assertEqual(result["state_revision"], 1)
        self.assertEqual((self.root / ".codebee" / "derived" / "context.md").read_text(encoding="utf-8").count("F001"), 1)
        self.assertTrue(story_tracking.check(self.root)["ok"])

    def test_check_detects_manual_edit_of_derived_view(self):
        story_tracking.init(self.root, "t-1", "测试书")
        story_tracking.commit_chapter(self.root, 1, "开端", "正文")
        p = self.root / ".codebee" / "derived" / "context.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n手改", encoding="utf-8")
        result = story_tracking.check(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("context.md", result["errors"][0])

    def test_invalid_chapter_and_duplicate_commit_are_safe(self):
        story_tracking.init(self.root, "t-1", "测试书")
        with self.assertRaises(ValueError):
            story_tracking.commit_chapter(self.root, 0, "", "正文")
        first = story_tracking.commit_chapter(self.root, 1, "纲", "正文")
        second = story_tracking.commit_chapter(self.root, 1, "纲", "正文")
        self.assertEqual(first["state_revision"], second["state_revision"])

    def test_corrupt_state_is_not_treated_as_missing(self):
        p = self.root / ".codebee"
        p.mkdir()
        (p / "story_tracking.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            story_tracking.load(self.root)

    def test_malformed_state_schema_is_rejected(self):
        p = self.root / ".codebee"
        p.mkdir()
        (p / "story_tracking.json").write_text(
            '{"version":1,"facts":{},"characters":[],"timeline":[],'
            '"foreshadowing":[],"next_promises":[],"author_truth":[],'
            '"reader_known":[],"chapters":[],"derived_digests":{}}',
            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "facts"):
            story_tracking.load(self.root)


if __name__ == "__main__":
    unittest.main()
