# -*- coding: utf-8 -*-
"""GBK 章稿乱码防线回归测试（2026-09-16 修复）。

背景：CLI 子代理在中文 Windows 上用 PowerShell Set-Content 写章稿缺省落成
GBK/ANSI，而工作区链路全按 UTF-8 消费——errors="replace" 会把整章中文变成
U+FFFD 并沿前情提要/评审/合并稿扩散。防线：读取层 UTF-8→GBK 回退，预览出口
转码，提示词强制 -Encoding UTF8。
"""
from base import BaseTest


class TestReadFallback(BaseTest):
    def _write_gbk(self, name, text):
        (self.workdir / name).write_bytes(text.encode("gbk"))

    def test_read_chapter_utf8(self):
        from app.core import pipeline
        (self.workdir / "chapter-01.md").write_text("第一章：普通内容。", encoding="utf-8")
        self.assertEqual(pipeline._read_chapter(str(self.workdir), 1),
                         "第一章：普通内容。")

    def test_read_chapter_gbk_fallback(self):
        from app.core import pipeline
        self._write_gbk("chapter-02.md", "第二章：GBK 落盘的中文内容。")
        self.assertEqual(pipeline._read_chapter(str(self.workdir), 2),
                         "第二章：GBK 落盘的中文内容。")

    def test_read_chapter_neither_enc_replaces(self):
        from app.core import pipeline
        (self.workdir / "chapter-03.md").write_bytes(b"\xff\xfe\x00broken")
        txt = pipeline._read_chapter(str(self.workdir), 3)
        self.assertIn("\ufffd", txt)          # 兜底不抛异常

    def test_read_variant_gbk(self):
        from app.core import pipeline
        self._write_gbk("chapter-05-v2.md", "变体二：GBK 稿。")
        self.assertEqual(pipeline._read_variant(str(self.workdir), 5, 2),
                         "变体二：GBK 稿。")
        self.assertEqual(pipeline._read_variant(str(self.workdir), 5, 9), "")

    def test_story_bible_gbk(self):
        from app.core import pipeline
        self._write_gbk("story-bible.md", "主角：陆宴迟。")
        self.assertIn("主角：陆宴迟。", pipeline._story_bible(str(self.workdir)))

    def test_write_chapter_is_utf8(self):
        from app.core import pipeline
        pipeline._write_chapter(str(self.workdir), 4, "第四章。")
        raw = (self.workdir / "chapter-04.md").read_bytes()
        self.assertEqual(raw.decode("utf-8"), "第四章。")


class TestUtf8Bytes(BaseTest):
    def _import_fn(self):
        # main.py 用 `from core import ...`，导入 app.main 前需把 app/ 加入 sys.path
        import sys
        from pathlib import Path
        app_dir = str(Path(__file__).resolve().parents[1] / "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        import main
        return main._utf8_bytes

    def test_plain_utf8_passthrough(self):
        _utf8_bytes = self._import_fn()
        b = "中文正常。".encode("utf-8")
        self.assertEqual(_utf8_bytes(b), b)

    def test_gbk_transcoded(self):
        _utf8_bytes = self._import_fn()
        src = "这是 GBK 落盘的章节。"
        self.assertEqual(_utf8_bytes(src.encode("gbk")), src.encode("utf-8"))

    def test_binary_garbage_replaces(self):
        _utf8_bytes = self._import_fn()
        out = _utf8_bytes(b"\xff\xfe\x00bad")
        out.decode("utf-8")               # 永远产出合法 UTF-8
        self.assertIn("\ufffd", out.decode("utf-8"))


if __name__ == "__main__":
    import unittest as _u
    _u.main()
