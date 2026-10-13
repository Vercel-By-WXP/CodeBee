# -*- coding: utf-8 -*-
"""内存守卫：定期检查后端进程 RSS，超阈值时安全退出本进程。

外部服务管理器（桌面端 / Windows 服务 / 自动化脚本）检测到进程退出后
自动重启，续跑链无缝恢复——章文件全在盘上，零损失。

用法：main.py 启动时调 start_memory_guard(threshold_mb)。
每 300 秒采样一次，RSS > 阈值（默认 1.2 GB）时 os._exit 释放内存，
由外部看门狗重启。比 OOM kill 更优雅（有日志、可预测）。
"""
import os
import sys
import threading
import time

_INTERVAL_SEC = 300  # 5 分钟采样一次
_threshold_mb = 1228  # 1.2 GB
_triggered = False
_lock = threading.Lock()


def _get_rss_mb():
    """返回当前进程 RSS（MB）。psutil 不可用时逐层回退。"""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = _PMC()
        k32 = ctypes.windll.kernel32
        handle = k32.GetCurrentProcess()
        if k32.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024 * 1024)
        return 0.0
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def start_memory_guard(threshold_mb: int = 1228):
    """启动内存守卫守护线程。超阈值时安全退出本进程。"""
    global _threshold_mb
    _threshold_mb = threshold_mb

    def _loop():
        global _triggered
        while True:
            time.sleep(_INTERVAL_SEC)
            if _triggered:
                continue
            with _lock:
                if _triggered:
                    continue
                rss = _get_rss_mb()
                if rss > _threshold_mb:
                    _triggered = True
                    print(f"[CodeBee] 内存守卫：RSS {rss:.0f} MB 超过 "
                          f"{_threshold_mb} MB 阈值，安全退出以释放内存"
                          "（外部看门狗将自动重启）", flush=True)
                    # 给 stdout 2 秒刷出，然后安全退出
                    sys.stdout.flush()
                    sys.stderr.flush()
                    time.sleep(2)
                    os._exit(0)

    th = threading.Thread(target=_loop, daemon=True, name="memory-guard")
    th.start()
