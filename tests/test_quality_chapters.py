# -*- coding: utf-8 -*-
"""稿子发现（P2-E）：一章在哪儿、怎么切，只认一处答案。

守四件事：

1. **章文件认得出**：`chapter-01.md` / `chapter-1.md` / `chapter-001.md` 都算，
   同章号并存只取一份（否则判据会随目录顺序漂）。
2. **合稿能补前几章**：真实那本 200 章的书，根目录只有 `chapter-21..200`，前二十章
   在 `已成稿/前二十章.md` 里、标题写作「第一章 忙完这阵」（**没有 `#`**）。旧读法
   因此对一本写完的书说「还没有章节稿」—— 假阴性找到了、判据却没跑。
3. **合稿的准入门槛不许放松**：章纲/大纲/索引/小文件不当正文，正文里以「第 N 章」
   开头的长句也不当标题。假阳性（拿章纲判「没给读者东西」）比漏判贵得多。
4. **找不到要说清是哪种找不到**：一章没有 = `no_text`；有章但不是从第 1 章起 =
   `no_first_chapter` 且报出实际起始章号。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.quality import appeal, chapters

# 够过 MERGED_MIN_CHARS 的正文素材（一章 ~500 字）。
BODY = "他把门闩插好，回身看见灶台上的灯还亮着。" * 25


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _merged_text(first, last, prefix=""):
    """`prefix + 第一章 标题\n正文…` 形式的合稿。"""
    out = []
    for number in range(first, last + 1):
        out.append("第%d章 第%d回\n\n%s%s" % (number, number, prefix, BODY))
    return "\n\n".join(out)


class ChapterFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_digit_chapters(self):
        for number in range(1, 4):
            _write(self.root / ("chapter-%02d.md" % number), BODY)
        rows, info = chapters.discover(self.root, 6)
        self.assertEqual([r["chapter"] for r in rows], [1, 2, 3])
        self.assertEqual(info["file_start"], 1)
        self.assertEqual(info["file_end"], 3)
        self.assertEqual(info["sources"], ["chapter-01.md", "chapter-02.md", "chapter-03.md"])

    def test_three_digit_and_one_digit_names(self):
        _write(self.root / "chapter-001.md", BODY)
        _write(self.root / "chapter-002.md", BODY)
        rows, _info = chapters.discover(self.root, 6)
        self.assertEqual([r["chapter"] for r in rows], [1, 2])

    def test_same_chapter_keeps_the_standard_name(self):
        # `chapter-1.md` 比 `chapter-01.md` 短，但标准写法优先——挑哪份要可预测。
        _write(self.root / "chapter-1.md", "旧的一份" * 100)
        _write(self.root / "chapter-01.md", BODY)
        rows, _info = chapters.discover(self.root, 6)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "chapter-01.md")
        self.assertIn("门闩", rows[0]["text"])

    def test_gaps_are_reported_not_filled(self):
        for number in (1, 2, 4):
            _write(self.root / ("chapter-%02d.md" % number), BODY)
        rows, info = chapters.discover(self.root, 6)
        self.assertEqual([r["chapter"] for r in rows], [1, 2, 4])
        self.assertEqual(info["missing"], [3])
        self.assertEqual(info["gaps"], [3])

    def test_starts_at_21_is_no_first_chapter(self):
        for number in range(21, 24):
            _write(self.root / ("chapter-%02d.md" % number), BODY)
        task = {"type": "serial_novel", "workdir": str(self.root)}
        result = appeal.check_task(task)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "no_first_chapter")
        self.assertEqual(result["file_start"], 21)
        self.assertEqual(result["file_count"], 3)

    def test_empty_workdir_is_no_text(self):
        task = {"type": "serial_novel", "workdir": str(self.root)}
        result = appeal.check_task(task)
        self.assertEqual(result["reason"], "no_text")

    def test_blank_workdir_never_scans_the_cwd(self):
        # `Path("")` 是当前目录（`.`）：空 workdir 若不拦住，就会把仓库自己的
        # md 当成这位作者的稿子（2026-11-04：没设 workdir 的任务因此被套上了
        # 打猎文的开工约束）。
        for blank in ("", "  ", ".", "./"):
            rows, info = chapters.discover(blank, 6)
            self.assertEqual(rows, [], blank)
            self.assertEqual(info["sources"], [], blank)


class MergedManuscriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_merged_file_fills_the_prefix(self):
        # 真实那本书的样子：根目录第 21 章起，前二十章并进「已成稿」。
        for number in range(21, 24):
            _write(self.root / ("chapter-%02d.md" % number), BODY)
        _write(self.root / "已成稿" / "不周山守村人-前二十章.md",
               _merged_text(1, 20))
        rows, info = chapters.discover(self.root, 6)
        self.assertEqual([r["chapter"] for r in rows], [1, 2, 3, 4, 5, 6])
        self.assertEqual({r["from"] for r in rows[:6]}, {"merged"})
        self.assertEqual(info["merged"][0]["file"], "已成稿/不周山守村人-前二十章.md")
        self.assertIn("已成稿/不周山守村人-前二十章.md", info["sources"])

    def test_manuscript_md_fills_the_prefix(self):
        _write(self.root / "chapter-05.md", BODY)
        _write(self.root / "manuscript.md", _merged_text(1, 4))
        rows, _info = chapters.discover(self.root, 6)
        self.assertEqual([r["chapter"] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual(rows[4]["from"], "file")

    def test_chapter_file_wins_over_merged_text(self):
        _write(self.root / "chapter-01.md", BODY)
        _write(self.root / "manuscript.md", _merged_text(1, 3, prefix="合稿里的："))
        rows, _info = chapters.discover(self.root, 6)
        self.assertEqual(rows[0]["from"], "file")
        self.assertNotIn("合稿里的", rows[0]["text"])

    def test_outline_directories_are_never_read(self):
        for number in range(21, 24):
            _write(self.root / ("chapter-%02d.md" % number), BODY)
        _write(self.root / "大纲" / "volume-1.md", _merged_text(1, 10))
        _write(self.root / "作废稿-20261003" / "old.md", _merged_text(1, 10))
        rows, info = chapters.discover(self.root, 6)
        self.assertEqual(rows, [])
        self.assertEqual(info["merged"], [])

    def test_index_and_small_files_are_skipped(self):
        for number in range(21, 24):
            _write(self.root / ("chapter-%02d.md" % number), BODY)
        # 索引：名字里有「目录」，即便切得出「第 1 章」也不读
        _write(self.root / "已成稿" / "前二十章阅读目录.md", _merged_text(1, 5))
        # 太短：不够 MERGED_MIN_CHARS，多半是章纲
        _write(self.root / "manuscript.md", "第1章 甲\n\n短\n\n第2章 乙\n\n也短\n")
        rows, _info = chapters.discover(self.root, 6)
        self.assertEqual(rows, [])

    def test_merged_must_start_at_chapter_one(self):
        # 从中间某章起头的文件是某一卷的草稿，不能拿来补「前几章」——它里面的
        # 第 3–5 章刚好落在 limit 内，缺了这条守卫就会被当成书的前几章。
        _write(self.root / "chapter-21.md", BODY)
        _write(self.root / "manuscript.md", _merged_text(3, 5))
        rows, info = chapters.discover(self.root, 6)
        self.assertEqual(rows, [])
        self.assertEqual(info["merged"], [])


class SplitChaptersTest(unittest.TestCase):
    def test_bare_heading_only_when_allowed(self):
        text = "第一章 忙完这阵\n\n%s\n\n第二章 送到医院\n\n%s" % (BODY, BODY)
        self.assertEqual(len(chapters.split_chapters(text)), 1)
        self.assertEqual(len(chapters.split_chapters(text, allow_bare=True)), 2)

    def test_long_sentence_starting_with_chapter_word_is_not_a_heading(self):
        # 「第三章程序改了这行代码」这类正文句子不能被当成标题（这就是要求整行
        # ≤ BARE_MAX 且章号后必须跟分隔符的原因）。
        text = ("第一章 甲\n\n%s\n\n第三章程序改了这行代码之后整个流程都跑通了，"
                "他这才敢把灯关掉。\n\n%s" % (BODY, BODY))
        items = chapters.split_chapters(text, allow_bare=True)
        self.assertEqual([i["chapter"] for i in items], [1])
        self.assertIn("第三章程序改了", items[0]["text"])

    def test_hashed_heading_still_wins(self):
        text = "## 第3章 夜行\n\n%s" % BODY
        items = chapters.split_chapters(text)
        self.assertEqual(items[0]["chapter"], 3)
        self.assertEqual(items[0]["title"], "夜行")


if __name__ == "__main__":
    unittest.main()
