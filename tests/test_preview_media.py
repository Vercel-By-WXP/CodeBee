# -*- coding: utf-8 -*-
"""媒体成品预览回归（2026-10-22 handdraw.mp4 案）。

手绘视频这类任务整个工作目录里可能只有一只 mp4——它就是成品本身。此前
list_app 只认 HTML 入口，没有网页就直接 no_html，工作台永远「暂无网页成品」；
成果 chip 点 mp4 也是「二进制文件不预览」。本文件锁定三条线：

1. list_app 无 HTML 时回退媒体入口（视频优先于音频），有 HTML 时网页优先；
2. 媒体单独大体积上限（4MiB 不拦正常成片，64MiB 封顶）；
3. Range 解析与 Handler._send_media 的 206/416/200 三态——视频进度条拖动靠它。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core import preview        # noqa: E402
import main                     # noqa: E402


def _fake_store(workdir, files, task_id="t-1"):
    """把 store 的四个读取口打到内存假数据上（不落真库）。"""
    return mock.patch.multiple(
        preview.store,
        get_run=mock.MagicMock(return_value={"id": "r-1", "task_id": task_id}),
        run_workdir=mock.MagicMock(return_value=workdir),
        run_artifacts=mock.MagicMock(return_value=(workdir, files)),
        task_step_count=mock.MagicMock(return_value=3),
    )


class PickEntryTests(unittest.TestCase):
    """入口挑选：HTML 优先；无 HTML 回退媒体，视频压音频。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _list(self, files):
        with _fake_store(self.tmp.name, files):
            return preview.list_app("r-1", "tok")

    def test_media_fallback_when_no_html(self):
        out = self._list([
            {"name": "report.md", "size": 100, "mtime": 10},
            {"name": "handdraw.mp4", "size": 1800, "mtime": 20},
        ])
        self.assertTrue(out["ok"])
        self.assertEqual(out["entry"], "handdraw.mp4")
        self.assertTrue(out["media"])

    def test_html_takes_precedence_over_media(self):
        out = self._list([
            {"name": "index.html", "size": 100, "mtime": 5},
            {"name": "handdraw.mp4", "size": 1800, "mtime": 20},
        ])
        self.assertTrue(out["ok"])
        self.assertEqual(out["entry"], "index.html")
        self.assertNotIn("media", out)

    def test_video_beats_audio_same_depth(self):
        out = self._list([
            {"name": "voice.mp3", "size": 100, "mtime": 99},
            {"name": "handdraw.mp4", "size": 100, "mtime": 1},
        ])
        self.assertEqual(out["entry"], "handdraw.mp4")

    def test_no_html_no_media_still_no_html(self):
        out = self._list([{"name": "report.md", "size": 100, "mtime": 1}])
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "no_html")

    def test_oversized_media_excluded_from_entry_pick(self):
        # 超过媒体上限（64MiB）的 mp4 不进候选——与文本超 4MiB 同口径
        out = self._list([
            {"name": "huge.mp4", "size": preview.MEDIA_MAX_FILE + 1, "mtime": 1},
        ])
        self.assertFalse(out["ok"])


class ServeMediaTests(unittest.TestCase):
    """serve() 对媒体的实际读取：MIME、大额上限、超限拒挂。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wd = self.tmp.name
        (Path(self.wd) / "handdraw.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 900)

    def _serve(self, name="handdraw.mp4"):
        with _fake_store(self.wd, []):
            return preview.serve("r-1", name, "tok")

    def test_mp4_served_with_video_mime(self):
        data, ctype, err = self._serve()
        self.assertIsNone(err)
        self.assertEqual(ctype, "video/mp4")
        self.assertTrue(data.startswith(b"\x00\x00\x00\x18ftyp"))

    def test_media_over_web_cap_but_under_media_cap_serves(self):
        big = Path(self.wd) / "big.mp4"
        big.write_bytes(b"m" * (preview.MAX_FILE + 1024))   # >4MiB，<64MiB
        data, ctype, err = self._serve("big.mp4")
        self.assertIsNone(err)
        self.assertEqual(len(data), big.stat().st_size)

    def test_media_over_media_cap_rejected(self):
        # 媒体超限用小文件+收紧上限来测，不真的写 64MiB
        old_cap = preview.MEDIA_MAX_FILE
        preview.MEDIA_MAX_FILE = 8
        try:
            (Path(self.wd) / "huge.mp4").write_bytes(b"m" * 9)
            data, ctype, err = self._serve("huge.mp4")
            self.assertIsNone(data)
            self.assertIn("过大", err)
        finally:
            preview.MEDIA_MAX_FILE = old_cap

    def test_text_over_web_cap_still_rejected(self):
        (Path(self.wd) / "note.txt").write_bytes(b"x")
        old_cap = preview.MAX_FILE
        preview.MAX_FILE = 8
        try:
            (Path(self.wd) / "note.txt").write_bytes(b"x" * 9)
            data, ctype, err = self._serve("note.txt")
            self.assertIsNone(data)
            self.assertIn("过大", err)
        finally:
            preview.MAX_FILE = old_cap


class ParseRangeTests(unittest.TestCase):
    """Range 头解析：无头/标准/尾段/非法/越界五态。"""

    def test_no_header_returns_none(self):
        self.assertIsNone(preview.parse_range("", 100))
        self.assertIsNone(preview.parse_range(None, 100))
        self.assertIsNone(preview.parse_range("bytes=abc", 100))   # 非bytes形态按无Range

    def test_open_end(self):
        self.assertEqual(preview.parse_range("bytes=10-", 100), (10, 99))

    def test_closed_range(self):
        self.assertEqual(preview.parse_range("bytes=0-9", 100), (0, 9))

    def test_suffix_range(self):
        self.assertEqual(preview.parse_range("bytes=-10", 100), (90, 99))

    def test_unsatisfiable_raises(self):
        with self.assertRaises(ValueError):
            preview.parse_range("bytes=100-", 100)   # 起点越界
        with self.assertRaises(ValueError):
            preview.parse_range("bytes=5-3", 100)    # 逆序
        with self.assertRaises(ValueError):
            preview.parse_range("bytes=-0", 100)

    def test_end_clamped_to_total(self):
        self.assertEqual(preview.parse_range("bytes=90-200", 100), (90, 99))

    def test_multi_range_unsupported_falls_back_to_full(self):
        # 多段 Range 直接回整包 200（媒体预览不需要多段，别为它写 multipart）
        self.assertIsNone(preview.parse_range("bytes=0-1,5-9", 100))


class _FakeHandler:
    """把 Handler._send_media 绑到假 self 上，捕获 (code, body, ctype, headers)。"""

    _send_media = main.Handler._send_media

    def __init__(self, range_header):
        self.headers = {"Range": range_header} if range_header else {}
        self.sent = []

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None,
              cache_control=None):
        self.sent.append((code, body, ctype, dict(headers or {})))


class SendMediaTests(unittest.TestCase):
    """_send_media 三态：200 全量+Accept-Ranges / 206 切片+Content-Range / 416。"""

    def test_full_when_no_range(self):
        h = _FakeHandler(None)
        h._send_media(b"0123456789", "video/mp4")
        code, body, ctype, hdrs = h.sent[0]
        self.assertEqual(code, 200)
        self.assertEqual(body, b"0123456789")
        self.assertEqual(hdrs.get("Accept-Ranges"), "bytes")

    def test_partial_response(self):
        h = _FakeHandler("bytes=2-5")
        h._send_media(b"0123456789", "video/mp4")
        code, body, ctype, hdrs = h.sent[0]
        self.assertEqual(code, 206)
        self.assertEqual(body, b"2345")
        self.assertEqual(hdrs.get("Content-Range"), "bytes 2-5/10")
        self.assertEqual(hdrs.get("Accept-Ranges"), "bytes")

    def test_suffix_partial(self):
        h = _FakeHandler("bytes=-4")
        h._send_media(b"0123456789", "video/mp4")
        code, body, _, hdrs = h.sent[0]
        self.assertEqual(code, 206)
        self.assertEqual(body, b"6789")
        self.assertEqual(hdrs.get("Content-Range"), "bytes 6-9/10")

    def test_unsatisfiable_416(self):
        h = _FakeHandler("bytes=99-")
        h._send_media(b"0123456789", "video/mp4")
        code, body, _, hdrs = h.sent[0]
        self.assertEqual(code, 416)
        self.assertEqual(hdrs.get("Content-Range"), "bytes */10")


if __name__ == "__main__":
    unittest.main(verbosity=2)
