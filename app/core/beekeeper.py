# -*- coding: utf-8 -*-
"""蜂巢保全：服务无论以何种方式退出，本服务拉起的蜜蜂 CLI 一起带走。

背景（2026-09-23 用户定案）：CodeBee 关闭后——无论正常退出、Ctrl+C、崩溃
还是被 taskkill 硬杀——在跑的 CLI/蜜蜂（codex/claude/kimi 等）必须一起杀
掉，否则孤儿进程把内存吃满。当天 16:50 一次服务重启就留过一只孤儿
claude.exe（直到管道断裂才饿死）。

Windows：Job Object（JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）。蜜蜂 Popen 一出生
就 AssignProcessToJobObject 挂进蜂巢，整棵子进程树自动继承巢内身份；服务进
程死亡（哪怕 os._exit 跳过 atexit、被 TerminateProcess 断头）时内核关闭巢柄，
负责把巢内进程全部收走——这是唯一能覆盖「任意死法」的机制。
  - 升级重启的新实例（selfupdate.relaunch）不经 run_process、不挂巢，得以
    存活——否则就地重启等于自杀。桌面蜜蜂、explorer、cmd /k 交互终端同理豁免。
POSIX（含 Mac）：没有 Job Object 也没有 Linux 的 prctl PDEATHSIG，靠登记 +
atexit 清扫兜底：run_process 的子进程本就 start_new_session（自成进程组，
pgid==pid），sweep 用 killpg 连树杀。正常退出 / Ctrl+C / SIGTERM/SIGHUP
（main.py 已把它们转成 SystemExit，atexit 照跑）全覆盖；仅 kill -9 无解
（任何机制都救不了，属已知边界）。

本模块绝不抛异常：挂巢失败只降级为旧行为（取消/超时收尸仍在），绝不影响
蜜蜂正常起跑。不依赖 pywin32，ctypes 直调 kernel32。
"""
from __future__ import annotations

import atexit
import collections
import os
import signal
import weakref

# 近期蜜蜂登记：只保最近若干条防无限增长。Windows 上 Job 是主机制、登记只是
# atexit 兜底的补充；POSIX 上登记是主机制（sweep 全靠它）。
_RECENT = collections.deque(maxlen=256)


# ---------------------------------------------------------------- Windows 巢
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _INFO_JOB_OBJECT_EXTENDED_LIMIT = 9
    _STILL_ACTIVE = 259

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),          # ULONG_PTR
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.windll.kernel32

    class BeeNest:
        """一个 KILL_ON_JOB_CLOSE 巢。生产用模块单例 NEST；测试自建私有巢。"""

        def __init__(self):
            self._handle = None
            self.assigned = 0

        def _ensure_job(self):
            if self._handle:
                return self._handle
            h = _kernel32.CreateJobObjectW(None, None)
            if not h:
                return None
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                    h, _INFO_JOB_OBJECT_EXTENDED_LIMIT,
                    ctypes.byref(info), ctypes.sizeof(info)):
                _kernel32.CloseHandle(h)
                return None
            self._handle = h
            return h

        def adopt(self, proc):
            h = self._ensure_job()
            if not h:
                return
            handle = getattr(proc, "_handle", None)
            if not handle:
                return
            # 子进程树（cmd /c 垫片 → claude.exe → 其孙）自动继承巢内身份，
            # 只需挂直系子进程。Win8+ 支持嵌套 Job，外层有别的 Job 也不碍事。
            if _kernel32.AssignProcessToJobObject(h, int(handle)):
                self.assigned += 1

        def close(self):
            """主动关巢：巢内进程全部被内核收走（等价于服务进程死亡）。"""
            if self._handle:
                try:
                    _kernel32.CloseHandle(self._handle)
                except Exception:
                    pass
                self._handle = None

else:
    class BeeNest:
        """POSIX 占位巢：杀全靠 sweep()（登记 + killpg），close 无事可做。"""

        def __init__(self):
            self.assigned = 0

        def adopt(self, proc):
            self.assigned += 1

        def close(self):
            pass


NEST = BeeNest()


def adopt(proc):
    """把蜜蜂进程挂巢：服务死则蜜蜂亡。绝不抛异常、绝不拖慢起跑。"""
    if proc is None:
        return
    try:
        _RECENT.append((proc.pid, weakref.ref(proc)))
        NEST.adopt(proc)
    except Exception:
        pass


def sweep():
    """退出清扫：登记在册且仍存活的蜜蜂连树杀。

    POSIX 主机制（atexit 在正常退出/Ctrl+C/SIGTERM→SystemExit 时都会跑）；
    Windows 只是 Job 的兜底（Job 建不出来或句柄异常时仍能杀到登记的直系
    进程，孙进程交给既有 taskkill /T 收尸路径）。Windows 端拿不到 Popen
    存活证据的条目不盲杀——防 PID 复用误伤无辜进程（反正有 Job 兜底）。
    """
    while _RECENT:
        pid, ref = _RECENT.popleft()
        proc = ref() if ref else None
        if proc is not None:
            try:
                if proc.poll() is not None:
                    continue          # 已退出，跳过
            except Exception:
                pass
        if os.name == "nt":
            if proc is None:
                continue
            try:
                os.kill(pid, signal.SIGTERM)   # Windows 语义 = TerminateProcess
            except Exception:
                pass
        else:
            # run_process 子进程 start_new_session：pgid==pid，killpg 连树杀
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass


atexit.register(sweep)
