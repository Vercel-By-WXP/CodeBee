# -*- coding: utf-8 -*-
"""内存守卫：定期检查后端进程 RSS，超阈值时安全退出本进程。

外部服务管理器（桌面端 / Windows 服务 / 自动化脚本）检测到进程退出后
自动重启，续跑链无缝恢复——章文件全在盘上，零损失。

用法：main.py 启动时调 start_memory_guard(threshold_mb)。
每 300 秒采样一次，RSS > 阈值（默认 1.2 GB）时 os._exit 释放内存，
由外部看门狗重启。比 OOM kill 更优雅（有日志、可预测）。
"""
import json
import os
import sys
import threading
import time

_INTERVAL_SEC = 300  # 5 分钟采样一次
_threshold_mb = 1228  # 1.2 GB
_triggered = False
_lock = threading.Lock()


def _record_trigger(rss_mb):
    """触发证据落盘到数据目录 memory-guard.jsonl。stdout 在 --no-browser
    无控制台模式下会被丢弃——不落盘则死因永远只能靠猜（2026-10-14 实案：
    CLI 裸跑服务凌晨死亡，三本书停摆一晚且无任何触发痕迹可查）。
    失败静默：守卫关键路径不能被日志 IO 拖死。"""
    try:
        from . import paths
        os.makedirs(str(paths.DATA_DIR), exist_ok=True)
        with open(str(paths.DATA_DIR / "memory-guard.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pid": os.getpid(),
                "rss_mb": round(float(rss_mb), 1),
                "threshold_mb": _threshold_mb,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


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

        psapi = ctypes.WinDLL("psapi")
        k32 = ctypes.windll.kernel32
        pmc = _PMC()
        handle = k32.GetCurrentProcess()
        # cb 必须先设为结构体大小（GetProcessMemoryInfo 的入参，err=122 即此）
        pmc.cb = ctypes.sizeof(_PMC)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
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
                    _record_trigger(rss)
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
