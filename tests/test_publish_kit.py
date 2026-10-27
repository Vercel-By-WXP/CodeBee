# -*- coding: utf-8 -*-
"""手动发布包（publish.kit）单测。

这是「平台发不出去」时的保命出口，所以测试重点不在字段齐全，而在三件事：

1. **切法与发布链一致**：短篇分节复用 manager 的逻辑，七猫每章 500~1500 是
   发布硬校验——切错作者拿回去也过不了；
2. **不编**：没生成作品信息就如实说没生成，简介不够字数就说差多少；
3. **只读**：不写盘、不连平台（测试自然也不该需要网络或浏览器）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.publish import kit


def _task(workdir, **kw):
    task = {"id": "t-kit", "title": "任务名", "workdir": str(workdir),
            "type": "short_story", "manuscript": "story.md"}
    task.update(kw)
    return task


def _write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def _body(n, para=120):
    """造 n 字正文，按 para 字一行（模拟真实稿件：段落之间没有空行）。"""
    line = "字" * para
    out = []
    while n > 0:
        out.append(line[:min(para, n)])
        n -= para
    return "\n".join(out)


class ShortStoryTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_fanqie_short_splits_into_sections_and_keeps_every_char(self):
        body = _body(6000)
        _write(self.root / "story.md", body)
        got = kit.build(_task(self.root), "fanqie")
        self.assertEqual(got["kind"], "short")
        self.assertEqual(got["platform_label"], "番茄")
        self.assertGreaterEqual(len(got["chapters"]), 2)
        # 分节标记不进正文（平台上标记是单独一行，粘进正文会重复）
        for ch in got["chapters"]:
            self.assertNotIn(ch["text"].strip(), ["一", "二", "三", "四", "五"])
        # 一字不丢：切分不能吃掉正文（字数按去空白口径）
        self.assertEqual(got["stats"]["words"], len(body.replace("\n", "")))

    def test_qimao_short_every_chapter_within_hard_range(self):
        # 单段 4000 字的实心长段：manager 的按行插标记会把它顶成一大节，
        # 必须重切到每章 ≤1500，否则七猫发布直接拦下。
        _write(self.root / "story.md", _body(5000, para=4000))
        got = kit.build(_task(self.root), "qimao")
        self.assertTrue(got["spec"]["hard"])
        self.assertGreaterEqual(len(got["chapters"]), 2)
        for ch in got["chapters"]:
            self.assertLessEqual(ch["words"], 1500, ch)
            self.assertGreaterEqual(ch["words"], 500, ch)
            self.assertTrue(ch["ok"], ch.get("problem"))

    def test_qimao_single_block_is_split_to_two(self):
        # 1600 字只够 manager 切出 2 节，但如果某次切成一节（正文短），
        # 七猫「≥2 章」硬校验会拦——包必须自己补到两章。
        _write(self.root / "story.md", _body(1600, para=1600))
        got = kit.build(_task(self.root), "qimao")
        self.assertGreaterEqual(len(got["chapters"]), 2)

    def test_missing_manuscript_is_reported_not_invented(self):
        got = kit.build(_task(self.root), "fanqie")
        self.assertFalse(got["ready"])
        self.assertTrue(any("story.md" in x for x in got["missing"]), got["missing"])
        self.assertEqual(got["chapters"], [])

    def test_short_story_under_platform_floor_is_flagged(self):
        _write(self.root / "story.md", _body(3000))
        got = kit.build(_task(self.root), "qimao")   # 七猫短故事下限 4000 字
        self.assertFalse(got["ready"])
        self.assertTrue(any("下限" in x for x in got["missing"]), got["missing"])


class BookFieldsTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        _write(self.root / "story.md", _body(5000))

    def tearDown(self):
        self._dir.cleanup()

    def test_no_book_meta_says_not_generated(self):
        got = kit.build(_task(self.root), "fanqie")
        self.assertFalse(got["book"]["state"]["generated"])
        self.assertIn("还没生成", kit.render_text(got))

    def test_fanqie_serial_flags_short_summary(self):
        task = _task(self.root, type="serial_novel", serial={"chapters": 8},
                     book_meta={"fanqie": {"status": "done", "data": {
                         "book_name": "停灵三夜", "summary": "太短了",
                         "category": "悬疑"}}})
        got = kit.build(task, "fanqie")
        self.assertEqual(got["kind"], "serial")
        row = next(r for r in got["book"]["fields"] if r["key"] == "summary")
        self.assertFalse(row["ok"])
        self.assertIn("50-500", row["problem"])
        self.assertFalse(got["ready"])

    def test_fanqie_short_story_ignores_summary_length(self):
        # 番茄短故事建书只锁书名，简介不参与校验——不该谎报「待补齐」
        task = _task(self.root, book_meta={"fanqie": {"status": "done", "data": {
            "book_name": "头七"}}})
        got = kit.build(task, "fanqie")
        self.assertTrue(got["ready"], got["missing"])

    def test_tags_are_joined_for_copy(self):
        task = _task(self.root, type="serial_novel", serial={"chapters": 4},
                     book_meta={"fanqie": {"status": "done", "data": {
                         "book_name": "书", "summary": "简" * 120,
                         "category": "悬疑",
                         "tags_theme": ["复仇", "民俗"],
                         "tags_role": ["反派"], "tags_plot": ["反转"]}}})
        got = kit.build(task, "fanqie")
        row = next(r for r in got["book"]["fields"] if r["key"] == "tags_theme")
        self.assertEqual(row["value"], "复仇、民俗")
        self.assertIn("主题标签：复仇、民俗", kit.render_text(got))


class SerialTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_chapters_come_from_files_in_order(self):
        _write(self.root / "chapter-02.md", "# 第二章 雨夜\n" + _body(2000))
        _write(self.root / "chapter-01.md", "# 第一章 来电\n" + _body(1800))
        task = {"id": "t2", "title": "连载", "workdir": str(self.root),
                "type": "serial_novel", "serial": {"chapters": 2}}
        got = kit.build(task, "qimao")
        self.assertEqual([c["no"] for c in got["chapters"]], [1, 2])
        self.assertEqual(got["chapters"][0]["title"], "第一章 来电")
        self.assertEqual(got["stats"]["chapters"], 2)

    def test_empty_workdir_is_reported(self):
        task = {"id": "t3", "title": "连载", "workdir": str(self.root),
                "type": "serial_novel", "serial": {"chapters": 2}}
        got = kit.build(task, "qimao")
        self.assertFalse(got["ready"])
        self.assertTrue(any("chapter-" in x for x in got["missing"]), got["missing"])


class RenderTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        _write(self.root / "story.md", _body(4000))

    def tearDown(self):
        self._dir.cleanup()

    def test_text_has_all_three_sections_and_steps(self):
        got = kit.build(_task(self.root), "qimao")
        text = kit.render_text(got)
        for mark in ("建书资料", "正文", "手动发布步骤"):
            self.assertIn(mark, text)
        self.assertIn("新建短故事" if got["platform"] == "fanqie" else "章节标题", text)

    def test_filename_is_windows_safe(self):
        got = kit.build(_task(self.root, title="上/下:测试*书"), "fanqie")
        name = kit.filename(got)
        for bad in '\\/:*?"<>|':
            self.assertNotIn(bad, name)
        self.assertTrue(name.endswith(".txt"))

    def test_unknown_platform_falls_back_to_fanqie(self):
        got = kit.build(_task(self.root), "weibo")
        self.assertEqual(got["platform"], "fanqie")


if __name__ == "__main__":
    unittest.main()
