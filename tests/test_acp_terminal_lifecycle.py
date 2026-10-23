# -*- coding: utf-8 -*-
"""ACP 终端命令必须随会话收尾一起取消。

`terminal/create` 会起一个独立线程跑命令；`terminal/release` 只覆盖 agent 主动
释放的情况。此前 `run_agent` 的 finally 只关连接，于是「agent 先结束、命令还在
跑」成了默认结局：run 已标记完成，命令继续在同一工作目录里写文件，把下一个任务
的改动踩掉，也占着 CPU/磁盘直到自己的 deadline。
"""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

from app.core.acp_client import _stop_pending_terminals

ROOT = Path(__file__).resolve().parents[1]


def _terminal(done_event, cancel_event, thread=None):
    terminal = {"output": "", "done": done_event,
                "cancel": cancel_event, "released": False}
    if thread is not None:
        terminal["thread"] = thread
    return terminal


class StopPendingTerminalsTest(unittest.TestCase):
    def test_pending_command_is_cancelled_and_joined(self):
        done, cancel = threading.Event(), threading.Event()

        def worker():
            # 模拟 _exec_tool：轮询 cancel，收到即收手并置 done。
            if cancel.wait(2.0):
                done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        terminals = {"term-1": _terminal(done, cancel, thread)}
        leftovers = _stop_pending_terminals(terminals, budget=2.0)
        self.assertTrue(cancel.is_set())
        self.assertTrue(done.is_set())
        self.assertEqual(leftovers, [])

    def test_finished_command_is_left_alone(self):
        done, cancel = threading.Event(), threading.Event()
        done.set()
        terminals = {"term-1": _terminal(done, cancel)}
        _stop_pending_terminals(terminals, budget=0.1)
        self.assertFalse(cancel.is_set(), "已完成的终端不该被标记取消")

    def test_stubborn_command_is_reported_not_blocking(self):
        done, cancel = threading.Event(), threading.Event()
        stop = threading.Event()

        def worker():
            stop.wait(5.0)      # 不理会 cancel：模拟卡在系统调用里的命令
            done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        started = time.monotonic()
        terminals = {"term-1": _terminal(done, cancel, thread)}
        leftovers = _stop_pending_terminals(terminals, budget=0.2)
        elapsed = time.monotonic() - started
        stop.set()
        self.assertTrue(cancel.is_set())
        self.assertEqual(leftovers, ["term-1"])
        self.assertLess(elapsed, 1.5, "预算必须共享，不能逐条各等一遍")

    def test_run_agent_finally_calls_the_cleanup(self):
        source = (ROOT / "app" / "core" / "acp_client.py").read_text(encoding="utf-8")
        finally_block = source[source.rindex("    finally:"):]
        self.assertIn("_stop_pending_terminals(terminals)", finally_block)


if __name__ == "__main__":
    unittest.main()
