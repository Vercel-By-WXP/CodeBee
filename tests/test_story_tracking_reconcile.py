# -*- coding: utf-8 -*-
"""开场漂移自愈回归（2026-10-22 神瞳 0049 连环拒跑实案）。

章稿在验收提交后被实现端 CLI / 体例归一器直接改写 .codebee 副本时，状态
摘要停在旧稿，开场闸此前直接判死整个工作目录（连修订任务自己都起不来）。
verify = check + 按磁盘稿回登记（磁盘是真源）；细纲漂移/缺文件/派生文件
手改等其他漂移类不自愈照拦。
"""
import tempfile
import unittest
from pathlib import Path

from app.core import story_tracking


class ReconcileProseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name).resolve()
        story_tracking.init(self.workdir, "task-1", "测试故事")
        story_tracking.commit_chapter(self.workdir, 1, "第一章大纲",
                                      "第一章正文甲\n第一章正文乙")
        story_tracking.commit_chapter(self.workdir, 2, "第二章大纲",
                                      "第二章正文甲\n第二章正文乙")

    def tearDown(self):
        self._tmp.cleanup()

    def _prose_path(self, n):
        return (self.workdir / ".codebee" / "chapters"
                / ("%04d" % n) / "prose.md")

    def test_out_of_band_rewrite_is_healed(self):
        self._prose_path(1).write_text("第一章正文甲\n改写后的正文\n", encoding="utf-8")
        self.assertFalse(story_tracking.check(self.workdir).get("ok"))
        result = story_tracking.verify(self.workdir)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("healed"), [1])
        # 复查幂等：无漂移时不再登记
        self.assertEqual(story_tracking.reconcile_prose(self.workdir), [])

    def test_outline_mismatch_is_not_healed(self):
        ch1 = self.workdir / ".codebee" / "chapters" / "0001" / "outline.md"
        ch1.write_text("被换掉的细纲", encoding="utf-8")
        result = story_tracking.verify(self.workdir)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("healed"), [])
        self.assertTrue(any("细纲" in e for e in result.get("errors") or []))

    def test_missing_chapter_file_is_not_healed(self):
        self._prose_path(2).unlink()
        result = story_tracking.verify(self.workdir)
        self.assertFalse(result.get("ok"))
        self.assertTrue(any("缺失" in e for e in result.get("errors") or []))

    def test_empty_rewrite_not_healed(self):
        self._prose_path(1).write_text("   \n", encoding="utf-8")
        result = story_tracking.verify(self.workdir)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("healed"), [])

    def test_clean_state_passes_without_healing(self):
        result = story_tracking.verify(self.workdir)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("healed"), [])


if __name__ == "__main__":
    unittest.main()
