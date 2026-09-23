# -*- coding: utf-8 -*-
"""蜂巢保全回归：服务任何形式退出，蜜蜂 CLI 一起带走（2026-09-23 用户定案）。

三件事：
1. Windows Job Object：蜜蜂挂巢后关巢（=服务进程死亡），蜜蜂被内核收走。
2. 硬退元测试（nt）：辅助进程挂巢后 os._exit(0)——跳过 atexit、句柄随进程
   蒸发，只有内核 Job 能收走蜜蜂。这正是升级重启/被 taskkill 硬杀的场景。
3. run_process 大漏斗：起蜜蜂即挂巢，正常执行不受影响。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
import time

from base import BaseTest

from app.core import beekeeper

APP_DIR = str(pathlib.Path(__file__).resolve().parents[1])
_SLEEP_120 = "import time; time.sleep(120)"


def _spawn_sleeper():
    if os.name == "nt":
        return subprocess.Popen(
            [sys.executable, "-c", _SLEEP_120],
            creationflags=0x08000000)        # CREATE_NO_WINDOW
    return subprocess.Popen(
        [sys.executable, "-c", _SLEEP_120],
        start_new_session=True)              # run_process 同款：独立进程组


def _pid_alive(pid):
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259                 # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _await_dead(pid, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.2)
    return not _pid_alive(pid)


class TestBeekeeper(BaseTest):
    def test_nest_close_kills_child(self):
        """挂巢后关巢（=服务死亡）→ 蜜蜂亡。nt 走内核 Job；POSIX 走 sweep 登记路径。"""
        child = _spawn_sleeper()
        self.addCleanup(lambda: child.poll() is None and child.kill())
        time.sleep(0.3)   # 给子进程起跑时间，防「还没挂巢就退出」竞态
        if os.name == "nt":
            nest = beekeeper.BeeNest()
            nest.adopt(child)
            self.assertGreaterEqual(nest.assigned, 1)
            nest.close()
        else:
            beekeeper.adopt(child)
            beekeeper.sweep()
        self.assertTrue(_await_dead(child.pid),
                        "服务巢关闭后蜜蜂应被收走（pid=%s 仍存活）" % child.pid)

    def test_service_hard_exit_kills_bee(self):
        """硬退元测试（nt）：os._exit 跳过 atexit，蜜蜂仍必须死——内核 Job 的存在意义。"""
        if os.name != "nt":
            self.skipTest("POSIX 无内核级巢；kill -9 是全平台已知边界，优雅路径见上一用例")
        helper = self.tmp / "hard_exit_helper.py"
        helper.write_text(textwrap.dedent("""
            import os, subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", %r],
                                     creationflags=0x08000000)
            sys.path.insert(0, %r)
            from app.core import beekeeper
            beekeeper.adopt(child)
            print(child.pid, flush=True)
            time.sleep(0.5)
            os._exit(0)
        """ % (_SLEEP_120, APP_DIR)).strip() + "\n", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, str(helper)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=0x08000000)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        line = p.stdout.readline().strip()
        p.wait(timeout=15)
        err = p.stderr.read()[-300:]
        p.stdout.close()
        p.stderr.close()
        self.assertTrue(line.isdigit(), "辅助进程未报告蜜蜂 pid：%r / %r" % (line, err))
        self.assertTrue(_await_dead(int(line)),
                        "服务 os._exit 硬退后蜜蜂（pid=%s）应被内核 Job 收走" % line)

    def test_run_process_adopts_and_runs(self):
        """run_process 起蜜蜂即挂巢（assigned 计数增长），执行本身零影响。"""
        from app.core import runner
        before = beekeeper.NEST.assigned
        r = runner.run_process(
            [sys.executable, "-c", "import time; time.sleep(1); print('ok')"],
            timeout=30)
        self.assertTrue(r["ok"], "挂巢不得影响蜜蜂正常执行：%r" % r.get("stderr", "")[-200:])
        self.assertGreaterEqual(beekeeper.NEST.assigned, before + 1,
                                "run_process 应把蜜蜂挂巢")
