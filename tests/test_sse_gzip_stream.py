# -*- coding: utf-8 -*-
"""SSE /api/events 流式 gzip 回归（2026-10-22 实案）。

d59afab 给 SSE 事件流加流式 gzip 时把 zlib 的 compressobj 误写成 gzip
模块的——gzip 从无 compressobj 属性。curl 不带 Accept-Encoding: gzip，
探针/测试全绿；真实浏览器必带 gzip，SSE 每连必炸 AttributeError → 断流
重连风暴（日志刷「GET /api/events 处理异常（AttributeError）」），侧栏
实时推送全灭。本文件用带 gzip 头的真连接把压缩/明文两条路都钉死。
"""
import sys
import tempfile
import time
import unittest
import urllib.request
import zlib
from pathlib import Path

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import main                        # noqa: E402
from core import paths             # noqa: E402


def _read_head(resp, want_bytes, deadline_s=15.0):
    """SSE 是长连接：只读到断言所需字节就收手，不等流结束。"""
    buf = b""
    deadline = time.time() + deadline_s
    while len(buf) < want_bytes and time.time() < deadline:
        chunk = resp.read1(4096)
        if not chunk:
            break
        buf += chunk
    return buf


class SseGzipStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        old = (paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR)
        paths.DATA_DIR = root
        paths.TASKS_DIR = root / "tasks"
        paths.RUNS_DIR = root / "runs"

        def restore_paths():
            paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR = old

        # addClassCleanup 是 LIFO：paths 先注册（后恢复），服务先收尾再还原
        cls.addClassCleanup(restore_paths)
        cls.httpd = main.ThreadedServer(("127.0.0.1", 0), main.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = __import__("threading").Thread(
            target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.httpd.daemon_threads = True
        cls.addClassCleanup(cls.thread.join)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def test_gzip_stream_emits_decodable_data_events(self):
        req = urllib.request.Request(self.url("/api/events"))
        req.add_header("Accept-Encoding", "gzip")
        with urllib.request.urlopen(req, timeout=15) as resp:
            self.assertEqual(resp.headers.get("Content-Encoding"), "gzip")
            buf = _read_head(resp, 200)
        self.assertTrue(buf, "SSE 未推出任何字节")
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        text = d.decompress(buf).decode("utf-8", "replace")
        # 修复前这里炸 AttributeError，一个事件都出不来
        self.assertTrue(text.startswith("data: "), text[:120])
        self.assertIn('"v":', text)

    def test_plain_stream_untouched_without_gzip_header(self):
        with urllib.request.urlopen(self.url("/api/events"), timeout=15) as resp:
            self.assertIsNone(resp.headers.get("Content-Encoding"))
            buf = _read_head(resp, 32)
        self.assertTrue(buf.startswith(b"data: "),
                        buf[:64].decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
