"""C-P4（三轮审查）回归：deadline=None 时 run_process 必须有进程寿命绝对帽。

复现场景：子进程持续 stdout 输出（每 0.05s 一行），且 activity_timeout>0，
使「elapsed>timeout」的杀点因 active=True 而失效、无法靠活动窗口滑动收口。
此时若没有绝对帽，进程会无限期存活。本测试把 PROCESS_ABSOLUTE_CAP_SECS
压到 2s，断言进程在 ~2s 内被收口（timed_out=True，且 deadline_exceeded=False
因为调用方没传 deadline）。
"""

import sys
import time
import unittest

import app.core.runner as runner


_CHILD = (
    "import sys,time\n"
    "while True:\n"
    "    print('tick')\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.05)\n"
)


class RunProcessHardCapTest(unittest.TestCase):
    def test_hard_cap_reaps_output_loop_when_deadline_none(self):
        # 压短绝对帽；同时给一个极小 timeout + 大 activity 窗口，构造「活动窗口
        # 无限滑动」场景——只有 hard_cap 能收口。
        saved = runner.PROCESS_ABSOLUTE_CAP_SECS
        runner.PROCESS_ABSOLUTE_CAP_SECS = 2.0
        try:
            argv = [sys.executable, "-c", _CHILD]
            t0 = time.monotonic()
            res = runner.run_process(
                argv=argv,
                timeout=0.1,            # 很快越过单模型 timeout
                activity_timeout=1.0,   # 但持续输出使其保持 active，elapsed 杀点失效
                stall_timeout=0,
                deadline=None,          # 关键：无任务级 deadline
            )
            dur = time.monotonic() - t0
        finally:
            runner.PROCESS_ABSOLUTE_CAP_SECS = saved

        # 必须在合理时间内收口（硬帽 2s + timeout 0.1s + 排空余量）
        self.assertLess(dur, 6.0, f"进程未在硬帽内收口，耗时 {dur:.1f}s")
        self.assertTrue(res.get("timed_out"), "应输出超时被收口")
        # deadline 为 None 时不应误标 deadline_exceeded（那是任务级上限的语义）
        self.assertFalse(res.get("deadline_exceeded"),
                         "无 deadline 直调不应标 deadline_exceeded")
        # 子进程树应已被杀（returncode 非 None，或 quiescence_unknown 标记）
        self.assertIsNotNone(res.get("exit_code"),
                             "子进程未被确认终止，可能成为孤儿进程")

    def test_hard_cap_disabled_when_real_deadline_passed(self):
        # 对照：传了真实 deadline 时，到的是 deadline 而非合成帽，
        # 且 deadline_exceeded=True（确保合成帽没有悄悄改写 deadline 语义）。
        argv = [sys.executable, "-c", _CHILD]
        deadline = time.monotonic() + 0.3
        res = runner.run_process(
            argv=argv,
            timeout=0.1,
            activity_timeout=1.0,
            stall_timeout=0,
            deadline=deadline,
        )
        self.assertTrue(res.get("timed_out"))
        self.assertTrue(res.get("deadline_exceeded"),
                        "传了 deadline 应标 deadline_exceeded")


if __name__ == "__main__":
    unittest.main()
