"""超时杀树：用真实子进程验证，不打桩。

仓库里所有 ``timed_out=True`` 的断言都来自假结果（mock 的 dict 或返回值），
没有任何一条证明「超时之后进程真的没了」。2026-10-13 的 18 个 `opencode
--version` 僵尸堆积就是从这类盲区里长出来的：单测全绿，机器上进程在涨。

本文件只验证 CodeBee 的进程层行为（超时判定、杀树、部分输出保留、停滞看门狗、
正常退出不被误判），**不构成任何 CLI 的兼容性证据** —— 真实 CLI 的任务调用、
超时退出、会话恢复仍需按 CLI_COMPATIBILITY.md 单独验收。
"""
import os
import sys
import tempfile
import time
import unittest

from app.core import runner

# 桩进程：不打桩 CodeBee，而是真起一个会睡觉的子进程（可选再生一个孙进程）。
STUB = '''
import os, subprocess, sys, time

mode = sys.argv[1] if len(sys.argv) > 1 else "silent"
print("SELF=%d" % os.getpid(), flush=True)

if mode == "tree":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print("CHILD=%d" % child.pid, flush=True)
    time.sleep(120)
elif mode == "silent":
    time.sleep(120)
elif mode == "quick":
    print("done", flush=True)
else:
    raise SystemExit("unknown mode: %s" % mode)
'''


def _alive(pid):
    """跨平台存活判定。Windows 复用 runner 自己的 OpenProcess 口径。"""
    if os.name == "nt":
        return runner._win_pids_alive([pid])
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_dead(pid, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


class TimeoutKillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="timeout-kill-")
        self.stub = os.path.join(self.tmp, "stub_cli.py")
        with open(self.stub, "w", encoding="utf-8") as fh:
            fh.write(STUB)

    def _run(self, mode, **kwargs):
        return runner.run_process(argv=[sys.executable, self.stub, mode],
                                  cwd=self.tmp, **kwargs)

    def test_timeout_marks_timed_out_and_keeps_partial_output(self):
        t0 = time.time()
        res = self._run("silent", timeout=2.0)
        elapsed = time.time() - t0
        self.assertTrue(res["timed_out"], "超时未标记：%r" % res)
        self.assertFalse(res["cancelled"], "超时不应记成用户取消")
        # exit_code 取的是 proc.returncode，即杀进程的收尾码（POSIX SIGTERM=15，
        # Windows TerminateProcess 同码），不是 CLI 自己的退出码。要紧的是它不能
        # 是 0 —— 否则调用方会把「被杀」读成「成功退出」。
        self.assertNotEqual(res["exit_code"], 0, "被杀进程报了成功退出码")
        self.assertIn("SELF=", res["stdout"], "超时前已产生的输出被丢了")
        self.assertLess(elapsed, 60, "超时后收口太慢：%.1fs" % elapsed)

    def test_timeout_kills_the_whole_tree(self):
        """根进程 + 孙进程都要真死。这是「单测绿但机器上进程在涨」的唯一防线。"""
        res = self._run("tree", timeout=2.0)
        self.assertTrue(res["timed_out"])
        pids = {}
        for line in res["stdout"].splitlines():
            if line.startswith("SELF=") or line.startswith("CHILD="):
                key, val = line.split("=", 1)
                pids[key] = int(val)
        self.assertEqual(set(pids), {"SELF", "CHILD"}, "桩进程未输出 pid：%r" % res["stdout"])
        self.assertNotIn("quiescence_unknown", res,
                         "杀树未确认清空：%s" % res.get("stderr", "")[-200:])
        self.assertTrue(_wait_dead(pids["SELF"], 5), "根进程仍存活：%s" % pids["SELF"])
        self.assertTrue(_wait_dead(pids["CHILD"], 5), "孙进程成孤儿仍存活：%s" % pids["CHILD"])

    def test_stall_watchdog_fires_before_base_timeout(self):
        """停滞看门狗必须比基础超时先触发，否则流式 CLI 卡死等于干等 20 分钟。"""
        t0 = time.time()
        res = self._run("silent", timeout=300, stall_timeout=1.5)
        elapsed = time.time() - t0
        self.assertTrue(res["timed_out"], "停滞未标记超时：%r" % res)
        self.assertTrue(res["stalled"], "停滞未标记 stalled")
        self.assertLess(elapsed, 60, "看门狗未提前触发：%.1fs" % elapsed)

    def test_normal_exit_is_not_marked_timed_out(self):
        """反向用例：正常退出的进程不能被误判，否则每个任务都会被记成超时。"""
        res = self._run("quick", timeout=60)
        self.assertFalse(res["timed_out"], "正常退出被误判超时：%r" % res)
        self.assertFalse(res["stalled"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("done", res["stdout"])

    def test_missing_executable_is_a_launch_failure_not_a_timeout(self):
        """缺可执行文件要归到启动失败，不能混进超时（否则重试策略会误判）。"""
        res = runner.run_process(argv=[os.path.join(self.tmp, "no-such-cli-xyz")],
                                 cwd=self.tmp, timeout=30)
        self.assertFalse(res["ok"])
        self.assertFalse(res["timed_out"], "启动失败被算成超时：%r" % res)
        self.assertIn("找不到可执行文件", res["stderr"])


if __name__ == "__main__":
    unittest.main()
