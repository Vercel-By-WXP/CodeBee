# -*- coding: utf-8 -*-
"""已写字数（writing）单测。

这个模块的价值全在「不编造」：目标字数不是已写、没写过不等于写了 0 字、
读不到稿子必须报未知。所以测试的重点不是算术，而是**每个分支的来源标签和
已知/未知判定**。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core import writing


def _tracking_state(chapters):
    """最小可读的 story_tracking 状态（_load 只校验结构，不校验业务）。"""
    return {
        "version": 1, "task_id": "t1", "title": "测试书", "premise": "",
        "state_revision": 1, "current_chapter": max([c[0] for c in chapters] or [0]),
        "facts": [], "characters": [], "timeline": [], "foreshadowing": [],
        "next_promises": [], "author_truth": [], "reader_known": [],
        "chapters": [{"chapter": no, "outline": "细纲", "prose_digest": "d%d" % no,
                      "word_count": words} for no, words in chapters],
        "derived_digests": {},
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.wd = Path(self._dir.name)
        writing.forget()

    def tearDown(self):
        writing.forget()
        self._dir.cleanup()

    def task(self, **kw):
        base = {"id": "t1", "type": "short_story", "workdir": str(self.wd),
                "manuscript": "story.md"}
        base.update(kw)
        return base


class TargetTest(unittest.TestCase):
    def test_short_story_target(self):
        self.assertEqual(writing.summary({"story": {"words": 12000}})["target"], 12000)

    def test_serial_target_is_chapters_times_per_chapter(self):
        got = writing.summary({"serial": {"chapters": 8, "words_per_chapter": 2500}})
        self.assertEqual(got["target"], 20000)

    def test_broken_values_fall_back_to_zero(self):
        self.assertEqual(writing.summary({"story": {"words": "很多"}})["target"], 0)
        self.assertEqual(writing.summary({"serial": {"chapters": None}})["target"], 0)


class ShortStoryTest(_Base):
    def test_manuscript_words_ignore_whitespace(self):
        (self.wd / "story.md").write_text("第一段\n\n第二段  有空格\n", encoding="utf-8")
        got = writing.summary(self.task())
        self.assertEqual(got["words"], len("第一段第二段有空格"))
        self.assertEqual(got["source"], "manuscript")
        self.assertTrue(got["known"])

    def test_missing_manuscript_is_unknown_not_zero_words(self):
        got = writing.summary(self.task())
        self.assertEqual(got["words"], 0)
        self.assertFalse(got["known"])          # 关键：没写过 ≠ 写了 0 字
        self.assertEqual(got["source"], "none")

    def test_blank_manuscript_is_unknown(self):
        (self.wd / "story.md").write_text("   \n\n", encoding="utf-8")
        got = writing.summary(self.task())
        self.assertFalse(got["known"])

    def test_manuscript_name_cannot_escape_workdir(self):
        outside = self.wd.parent / "outside.md"
        outside.write_text("外部文件", encoding="utf-8")
        try:
            got = writing.summary(self.task(manuscript="../outside.md"))
            self.assertEqual(got["words"], 0)
            self.assertFalse(got["known"])
        finally:
            outside.unlink()

    def test_missing_workdir_is_unknown(self):
        got = writing.summary(self.task(workdir=str(self.wd / "not-there")))
        self.assertFalse(got["known"])
        self.assertEqual(got["source"], "none")


class SerialTest(_Base):
    def _tracking(self, chapters):
        d = self.wd / ".codebee"
        d.mkdir(parents=True, exist_ok=True)
        (d / "story_tracking.json").write_text(
            json.dumps(_tracking_state(chapters), ensure_ascii=False), encoding="utf-8")

    def test_tracking_is_the_source_of_truth(self):
        self._tracking([(1, 2000), (2, 2500), (3, 1800)])
        got = writing.summary(self.task(type="serial_novel"))
        self.assertEqual(got["words"], 6300)
        self.assertEqual(got["chapters"], 3)
        self.assertEqual(got["source"], "tracking")
        self.assertEqual([r["no"] for r in got["rows"]], [1, 2, 3])

    def test_tracking_wins_over_chapter_files(self):
        self._tracking([(1, 9000)])
        (self.wd / "chapter-01.md").write_text("x" * 50, encoding="utf-8")
        got = writing.summary(self.task(type="serial_novel"))
        self.assertEqual(got["words"], 9000)     # 归档过的章不能在文件口径里丢字

    def test_chapter_files_are_the_fallback(self):
        (self.wd / "chapter-01.md").write_text("一二三", encoding="utf-8")
        (self.wd / "chapter-02.md").write_text("四五六七", encoding="utf-8")
        (self.wd / "notes.md").write_text("不算章", encoding="utf-8")
        got = writing.summary(self.task(type="serial_novel"))
        self.assertEqual(got["words"], 7)
        self.assertEqual(got["chapters"], 2)
        self.assertEqual(got["source"], "files")

    def test_empty_tracking_with_no_chapters_is_unknown(self):
        self._tracking([])
        got = writing.summary(self.task(type="serial_novel"))
        self.assertFalse(got["known"])

    def test_task_serial_flag_also_means_serial(self):
        (self.wd / "chapter-01.md").write_text("一章", encoding="utf-8")
        got = writing.summary(self.task(type="novel", serial={"chapters": 3}))
        self.assertEqual(got["source"], "files")


class CacheTest(_Base):
    def test_cache_hit_is_marked_and_forget_forces_recompute(self):
        (self.wd / "story.md").write_text("一" * 100, encoding="utf-8")
        first = writing.cached_summary(self.task())
        self.assertEqual(first["words"], 100)
        self.assertNotIn("cached", first)          # 首次是实算

        second = writing.cached_summary(self.task())
        self.assertTrue(second.get("cached"))      # 文件没动 → 命中
        self.assertEqual(second["words"], 100)

        writing.forget("t1")
        third = writing.cached_summary(self.task())
        self.assertNotIn("cached", third)          # forget 之后必须重算
        self.assertEqual(third["words"], 100)

    def test_signature_change_invalidates_cache(self):
        path = self.wd / "story.md"
        path.write_text("一" * 100, encoding="utf-8")
        writing.cached_summary(self.task())
        path.write_text("一" * 250, encoding="utf-8")
        # 改了文件内容（mtime/大小变了）就应当重算，不用等相关方调用 forget
        self.assertEqual(writing.cached_summary(self.task())["words"], 250)

    def test_counts_for_batch(self):
        (self.wd / "story.md").write_text("一二三四五", encoding="utf-8")
        got = writing.counts_for([self.task(id="a"), self.task(id="b"), {}])
        self.assertEqual(set(got), {"a", "b"})
        self.assertEqual(got["a"]["words"], 5)

    def test_counts_for_never_raises(self):
        got = writing.counts_for([{"id": "x", "workdir": None, "type": "serial_novel"}])
        self.assertFalse(got["x"]["known"])


if __name__ == "__main__":
    unittest.main()
