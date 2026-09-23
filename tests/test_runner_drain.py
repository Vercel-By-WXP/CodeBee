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


class TestRunProcessRepeatedFatalOutput(BaseTest):
    """有持续错误输出的无限重连也应提前终止，不能因为一直有输出避开静默看门狗。"""

    def test_repeated_network_error_aborts(self):
        from app.core.runner import run_process
        child_script = (
            "import time\n"
            "for i in range(20):\n"
            " print('Reconnecting... waiting for network', flush=True)\n"
            " time.sleep(.1)\n"
        )
        started = time.time()
        res = run_process(
            argv=[sys.executable, "-c", child_script], timeout=30,
            repeat_abort=("Reconnecting... waiting for network", 5))
        self.assertTrue(res["timed_out"])
        self.assertFalse(res["ok"])
        self.assertLess(time.time() - started, 10)
        self.assertIn("同一网络错误重复 5 次", res["stderr"])


class TestRunProcessStallWatchdog(BaseTest):
    """静默挂死（进程活着、输出归零）由 stall_timeout 提前收尸。

    2026-09-22 qwen 全书总评实案：CLI 吐完评审内容后卡在 MCP 收尾死锁，
    stall=0 时只能干等 2400s 总超时。"""

    def test_silent_hang_killed_by_stall(self):
        from app.core.runner import run_process
        child_script = (
            "import time\n"
            "print('boot ok', flush=True)\n"
            "time.sleep(60)\n"
        )
        started = time.time()
        res = run_process(
            argv=[sys.executable, "-c", child_script], timeout=30,
            stall_timeout=2)
        self.assertTrue(res["stalled"])
        self.assertTrue(res["timed_out"])
        self.assertFalse(res["ok"])
        elapsed = time.time() - started
        self.assertGreaterEqual(elapsed, 1.5)      # 确实等了一段静默才杀
        self.assertLess(elapsed, 10)               # 远小于总超时
        self.assertIn("输出停滞 2s", res["stderr"])
        self.assertIn("boot ok", res["stdout"])    # 杀前已输出内容不丢

    def test_qwen_agent_carries_stall_from_catalog(self):
        """qwen 的看门狗配置真源在 catalog orch.stall_timeout_s（runner 侧读取）。"""
        from app.core import catalog
        qw = next(e for e in catalog.DEFAULT_CATALOG if e.get("id") == "qwencode")
        self.assertEqual(int(qw["orch"].get("stall_timeout_s") or 0), 900)
        # 存量老数据形态：补丁补齐为 900
        entries = [{"id": "qwencode",
                    "orch": {"kind": "qwen", "command": "qwen"}}]
        catalog._apply_stall_patch(entries)
        self.assertEqual(entries[0]["orch"]["stall_timeout_s"], 900)


class TestRunProcessActivityWindow(BaseTest):
    """流式 CLI 不能因基础 60 秒窗口到期而误杀。"""

    def test_stream_activity_extends_base_timeout_even_without_stall_watchdog(self):
        from app.core.runner import run_process
        child_script = (
            "import time\n"
            "for i in range(8):\n"
            "    print(f'event{i}', flush=True)\n"
            "    time.sleep(.1)\n"
        )
        started = time.time()
        res = run_process(
            argv=[sys.executable, "-c", child_script], timeout=.25,
            activity_timeout=.8, stall_timeout=0)
        self.assertTrue(res["ok"], msg=res)
        self.assertGreater(time.time() - started, .25)
        self.assertIn("event7", res["stdout"])

    def test_activity_extension_requires_output(self):
        from app.core.runner import run_process
        child_script = "import time; time.sleep(5)"
        started = time.time()
        res = run_process(
            argv=[sys.executable, "-c", child_script], timeout=.25,
            activity_timeout=.8, stall_timeout=0)
        self.assertTrue(res["timed_out"], msg=res)
        self.assertLess(time.time() - started, 1.2)

    def test_deadline_wins_over_activity_extension(self):
        from app.core.runner import run_process
        child_script = (
            "import time\n"
            "while True:\n"
            "    print('event', flush=True)\n"
            "    time.sleep(.05)\n"
        )
        res = run_process(
            argv=[sys.executable, "-c", child_script], timeout=.1,
            activity_timeout=5, stall_timeout=0,
            deadline=time.monotonic() + .45)
        self.assertTrue(res["timed_out"], msg=res)
        self.assertTrue(res["deadline_exceeded"], msg=res)
