# -*- coding: utf-8 -*-
"""run_process kill-tree 后排空流测试。
设计稿：docs/migration/01-defense-patterns.md §5B。
"""
from __future__ import annotations

import sys
import threading
import time
from base import BaseTest


class TestDrainStreamsHelper(BaseTest):
    """_drain_streams 单元测试：纯线程逻辑。"""

    def test_drain_returns_immediately_for_finished_thread(self):
        from app.core.runner import _drain_streams
        def _done():
            pass
        t = threading.Thread(target=_done, daemon=True)
        t.start()
        t.join()
        start = time.time()
        _drain_streams(None, t, t, timeout=10)
        self.assertLess(time.time() - start, 0.5)

    def test_drain_waits_for_slow_thread(self):
        """线程 sleep 2s，drain 应等至少 1.5s。"""
        from app.core.runner import _drain_streams
        captured = []
        def slow():
            time.sleep(2.0)
            captured.append("done")
        t = threading.Thread(target=slow, daemon=True)
        t.start()
        start = time.time()
        _drain_streams(None, t, t, timeout=5)
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 1.5)
        self.assertLess(elapsed, 4.0)
        self.assertEqual(captured, ["done"])

    def test_drain_truncates_at_timeout(self):
        """线程 sleep 10s 但 timeout=2：drain 应 ~2s 返回。"""
        from app.core.runner import _drain_streams
        def slow():
            time.sleep(10.0)
        t = threading.Thread(target=slow, daemon=True)
        t.start()
        start = time.time()
        _drain_streams(None, t, t, timeout=2)
        elapsed = time.time() - start
        self.assertLess(elapsed, 4.0)
        # daemon=True 线程未完成会随进程退出被丢


class TestRunProcessDrainsOnCancel(BaseTest):
    """spawn 长输出子进程 → cancel → drain 后 stdout 包含已输出内容。"""

    def test_cancel_drains_stream(self):
        from app.core.runner import run_process
        py = sys.executable
        # 每秒 print 一次，共 10 次；首行立即 flush
        child_script = (
            "import time, sys\n"
            "for i in range(10):\n"
            "    print(f'line{i}', flush=True)\n"
            "    time.sleep(1)\n"
        )
        cancel = threading.Event()
        def _cancel_after():
            time.sleep(0.5)
            cancel.set()
        threading.Thread(target=_cancel_after, daemon=True).start()

        res = run_process(
            argv=[py, "-c", child_script],
            timeout=60,
            cancel_event=cancel,
        )
        self.assertTrue(res["cancelled"])
        # cancel 发生在 0.5s，应该至少读到 line0
        self.assertIn("line0", res["stdout"],
                      f"stdout was: {res['stdout'][:200]!r}")


class TestRunProcessDrainsOnTimeout(BaseTest):
    """spawn 长输出子进程 → 超时 → drain 后 stdout 包含已输出内容。"""

    def test_timeout_drains_stream(self):
        from app.core.runner import run_process
        py = sys.executable
        # 每秒 print 一次，永久循环
        child_script = (
            "import time\n"
            "i = 0\n"
            "while True:\n"
            "    print(f'line{i}', flush=True)\n"
            "    i += 1\n"
            "    time.sleep(1)\n"
        )
        res = run_process(
            argv=[py, "-c", child_script],
            timeout=2,  # 2 秒超时
        )
        self.assertTrue(res["timed_out"])
        # 2 秒内能读到多行
        self.assertIn("line0", res["stdout"],
                      f"stdout was: {res['stdout'][:200]!r}")