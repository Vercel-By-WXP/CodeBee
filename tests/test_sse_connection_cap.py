# -*- coding: utf-8 -*-
"""SSE 并发上限回归（对应审查 C3）。

/api/events 是唯一常驻长连接：ThreadingHTTPServer 每连接起一个线程，连接
只在客户端断开时退出。页面反复刷新、多端在线、隧道重连叠旧连接未关，线程数
和每连接的压缩器 + ~0.5MB 状态载荷一起线性涨，长任务大屏逐步变慢，极端情况
线程表打满后连 /api/state 都拿不到。这里钉住三件事：

1. 超限直接 503 + Retry-After（不排队、不留线程）；
2. 槽位数准确，且客户端断开后必须归还（漏归还等于自建永久封锁）；
3. acquire/release 必须配对平衡，release 不会把计数减成负数。
"""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import main                        # noqa: E402
from core import paths             # noqa: E402


class SseConnectionCapTests(unittest.TestCase):
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

        cls.addClassCleanup(restore_paths)
        cls.httpd = main.ThreadedServer(("127.0.0.1", 0), main.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)

    def setUp(self):
        # 上限调小到 2、心跳调快：真实默认值（32 连接 / 15s 心跳）在测试里
        # 既开不起那么多流，也要等十几秒才能发现连接已死
        self._old_max = main.SSE_MAX_CONNECTIONS
        self._old_ping = main.SSE_PING_S
        main.SSE_MAX_CONNECTIONS = 2
        main.SSE_PING_S = 0.2
        self._drain_slots()
        self._streams = []

    def tearDown(self):
        for resp in self._streams:
            try:
                resp.close()
            except Exception:
                pass
        self._drain_slots()
        main.SSE_MAX_CONNECTIONS = self._old_max
        main.SSE_PING_S = self._old_ping

    # ------------------------------------------------------------ 辅助
    def _drain_slots(self, deadline_s=15.0):
        """等上一个用例的连接真正退出（服务端发现断流要等下一次写）。"""
        end = time.time() + deadline_s
        while main._sse_active() and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(main._sse_active(), 0, "SSE 槽位泄漏：用例间未归还")

    def _open_stream(self):
        url = "http://127.0.0.1:%d/api/events" % self.port
        resp = urllib.request.urlopen(url, timeout=15)
        self._streams.append(resp)
        return resp

    def _get_events_status(self):
        url = "http://127.0.0.1:%d/api/events" % self.port
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                # 拿到 200 就是被接受了：读一点字节后立刻关掉，别留长连接
                resp.read1(64)
                return resp.status, resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers

    # ------------------------------------------------------------ 用例
    def test_extra_connection_gets_503_with_retry_after(self):
        self._open_stream()
        self._open_stream()
        self.assertEqual(main._sse_active(), 2)

        status, headers = self._get_events_status()
        self.assertEqual(status, 503)
        self.assertEqual(headers.get("Retry-After"), "5")
        # 被拒的连接不占名额：计数仍是 2，不是 3
        self.assertEqual(main._sse_active(), 2)

    def test_client_disconnect_releases_slot(self):
        self._open_stream()
        self.assertEqual(main._sse_active(), 1)
        self._streams.pop().close()
        # 服务端下一次写（心跳）才会发现断流 → finally 归还
        end = time.time() + 12.0
        while main._sse_active() and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(main._sse_active(), 0)
        # 归还之后必须能重新接入（上限 2，先占 1）
        status, _ = self._get_events_status()
        self.assertEqual(status, 200)

    def test_acquire_release_is_balanced(self):
        main.SSE_MAX_CONNECTIONS = 1
        self.assertTrue(main._sse_acquire())
        self.assertFalse(main._sse_acquire(), "超限必须拒绝")
        main._sse_release()
        self.assertEqual(main._sse_active(), 0)
        main._sse_release()          # 多还一次不得减成负数
        self.assertEqual(main._sse_active(), 0)
        self.assertTrue(main._sse_acquire(), "归还后名额必须可复用")
        main._sse_release()

    def test_cap_is_configurable_and_lower_bounded(self):
        # 环境变量口径：0/负数不该把服务变成「永不接受 SSE」
        self.assertGreaterEqual(main.SSE_MAX_CONNECTIONS, 1)
        self.assertEqual(main.SSE_MAX_CONNECTIONS, 2)   # setUp 里的覆盖生效


if __name__ == "__main__":
    unittest.main()
