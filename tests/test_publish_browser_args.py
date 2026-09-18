# -*- coding: utf-8 -*-
"""Browser 启动参数与进程树收尾回归（mock，不碰真浏览器；真链路见 test_publish_browser.py）。

钉两件事：
- 启动参数不得再出现 --restore-last-session（Chromium 无值开关，=false 反而激活会话恢复）；
- POSIX spawn 必带 start_new_session（独立进程组供 killpg 杀树），POSIX close 走 killpg。

跑法：python tests/test_publish_browser_args.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="pub-args-")).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core.publish import browser  # noqa: E402

FAILS = []


def check(name, fn):
    try:
        fn()
        print("  ok   %s" % name)
    except Exception as e:
        FAILS.append(name)
        print("  FAIL %s: %r" % (name, e))


def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expect failed")


def test_launch_args():
    with mock.patch.object(browser.subprocess, "Popen") as P, \
         mock.patch.object(browser.Browser, "_wait_ready", return_value=True):
        browser.Browser(str(_TMP / "prof"))
        args, kwargs = P.call_args
    argv = args[0]
    expect(not any(a.startswith("--restore-last-session") for a in argv),
           "不得带 --restore-last-session（=false 反而激活会话恢复）：%s" % argv)
    expect("--hide-crash-restore-bubble" in argv, "崩溃恢复气泡压掉：%s" % argv)
    expect(kwargs.get("start_new_session") == (os.name != "nt"),
           "POSIX 独立进程组（killpg 前提）/Windows 忽略：%r" % kwargs.get("start_new_session"))


def test_close_posix_killpg():
    """POSIX 上杀树走 killpg（pgid==pid），不再 SIGTERM 单杀主进程。"""
    b = browser.Browser.__new__(browser.Browser)
    b.proc = mock.Mock()
    b.proc.pid = 4321
    with mock.patch.object(browser.sys, "platform", "darwin"), \
         mock.patch.object(browser.os, "killpg", create=True) as kp, \
         mock.patch.object(browser.signal, "SIGKILL", 9, create=True):
        b.close()
    expect(kp.call_args and tuple(kp.call_args[0]) == (4321, 9),
           "killpg(4321, SIGKILL)：%r" % (kp.call_args,))


def test_close_attach_noop():
    """attach 接管（proc=None）时 close 不许炸：不是自己起的进程不归自己杀。"""
    b = browser.Browser.__new__(browser.Browser)
    b.proc = None
    b.close()  # 不抛即过


if __name__ == "__main__":
    # 直跑才是脚本；被 unittest discover 导入时零副作用（仓库全量回归惯例）
    for t in (test_launch_args, test_close_posix_killpg, test_close_attach_noop):
        check(t.__name__, t)

    print("FAILS:", FAILS if FAILS else "none")
    sys.exit(1 if FAILS else 0)
