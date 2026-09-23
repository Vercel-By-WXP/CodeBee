# -*- coding: utf-8 -*-
"""章节安全落盘（chaptersafe.atomic_write_chapter，inkos 借鉴）单测。

跑法：python -m unittest discover -s tests -p "test_chaptersafe.py" -v
"""
from __future__ import annotations

from pathlib import Path

from base import BaseTest


class ChapterSafeTests(BaseTest):
    def test_atomic_write_and_readback(self):
        from app.core import chaptersafe
        p = chaptersafe.atomic_write_chapter(str(self.workdir), 3, "正文" * 50)
        self.assertTrue(p.is_file())
        self.assertEqual(p.name, "chapter-03.md")
        self.assertIn("正文", p.read_text(encoding="utf-8"))
        self.assertFalse(Path(str(p) + ".tmp").exists())   # tmp 已清

    def test_overwrite_existing(self):
        from app.core import chaptersafe
        chaptersafe.atomic_write_chapter(str(self.workdir), 1, "旧" * 10)
        chaptersafe.atomic_write_chapter(str(self.workdir), 1, "新" * 10)
        self.assertIn("新", (self.workdir / "chapter-01.md").read_text(encoding="utf-8"))

    def test_path_escape_rejected(self):
        """workdir 必须是已存在的目录：不存在目录拒绝。

        注：「..」本身也是存在的目录（resolve 到父目录），守卫按
        「不存在/文件形态拒绝」语义——真正的越界防御是 resolve 后
        p 必须落在 root 之下，用文件形态的 workdir 验证。"""
        from app.core import chaptersafe
        # 不存在的目录 → 拒绝
        with self.assertRaises(ValueError):
            chaptersafe.atomic_write_chapter(
                str(self.workdir / "not-exist-dir"), 1, "x")
        # workdir 是文件不是目录 → 拒绝
        f = self.workdir / "afile"
        f.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            chaptersafe.atomic_write_chapter(str(f), 1, "x")

    def test_write_via_pipeline_helper(self):
        """pipeline._write_chapter 委托生效（老调用点零改动）。"""
        from app.core import pipeline
        pipeline._write_chapter(str(self.workdir), 5, "委托" * 20)
        self.assertIn("委托", (self.workdir / "chapter-05.md").read_text(encoding="utf-8"))

    def test_partial_write_leaves_old_intact(self):
        """写到一半失败（模拟磁盘满）：旧稿完好，无 tmp 残渣、无半新稿。"""
        from app.core import chaptersafe
        chaptersafe.atomic_write_chapter(str(self.workdir), 2, "旧稿完好" * 10)
        target = (self.workdir / "chapter-02.md").resolve()
        tmp = Path(str(target) + ".tmp")

        real_replace = Path.replace

        def boom(self, other):
            raise OSError("disk full")
        Path.replace = boom
        try:
            with self.assertRaises(OSError):
                chaptersafe.atomic_write_chapter(str(self.workdir), 2, "半新稿" * 10)
        finally:
            Path.replace = real_replace
        # 旧稿未被破坏、tmp 已清、目标文件不含半新稿
        self.assertIn("旧稿完好", target.read_text(encoding="utf-8"))
        self.assertFalse(tmp.exists())
        self.assertNotIn("半新稿", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import unittest
    unittest.main()
