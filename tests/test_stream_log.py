# -*- coding: utf-8 -*-
"""子进程输出流式落盘的回归测试。

曾经用 BufferedReader.read(65536)：它会阻塞到凑满 64KB 或 EOF，所以在长命令
（npm 安装等）上等于「进程结束才一次性返回」，管理页日志全程空白——用户看到
的就是「安装日志一直无输出」。改用 read1() 后必须能边跑边看到内容。

断言刻意做成「进程还活着时就能读到已产出的内容」：只比较日志大小会在旧的
read() 实现下也偶然通过（最后一次采样恰好落在进程结束时），无法区分两种实现。
"""
from __future__ import annotations

import sys
import threading
import time

from base import BaseTest

# 打印第一行后静默较久：给「边跑边看」留出充裕的判定窗口
CHILD = ("import sys, time\n"
         "print('tick 0'); sys.stdout.flush()\n"
         "time.sleep(2.5)\n"
         "print('tick 1'); sys.stdout.flush()\n")


class TestStreamingLog(BaseTest):
    def runTest(self):
        from app.core import runner

        log_path = self.tmp / "stream.log"
        result = {}
        worker = threading.Thread(
            target=lambda: result.update(runner.run_process(
                argv=[sys.executable, "-c", CHILD], timeout=60, log_path=str(log_path))),
            daemon=True)
        worker.start()

        # 进程运行期间轮询：只要读到 tick 0 就说明输出是流式落盘的
        saw_before_exit = False
        started = time.time()
        while worker.is_alive() and time.time() - started < 30:
            if log_path.exists():
                try:
                    body = log_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    body = ""
                if "tick 0" in body:
                    saw_before_exit = worker.is_alive()
                    break
            time.sleep(0.2)
        worker.join(timeout=30)

        self.assertTrue(result.get("ok"), result.get("stderr"))
        self.assertTrue(saw_before_exit,
                        "进程结束前日志里读不到任何输出：说明输出没有流式落盘"
                        "（read(65536) 会阻塞到 EOF）")
        body = log_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("tick 0", body)
        self.assertIn("tick 1", body)

class TestNoLogPathStillCollects(BaseTest):
    """不传 log_path 时仍完整收集输出（纯内存路径不受流式改动影响）。"""

    def runTest(self):
        from app.core import runner
        res = runner.run_process(
            argv=[sys.executable, "-c", "print('hello'); print('world')"], timeout=60)
        self.assertTrue(res["ok"])
        self.assertIn("hello", res["stdout"])
        self.assertIn("world", res["stdout"])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
