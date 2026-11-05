# -*- coding: utf-8 -*-
"""Windows 系统能力适配：盘符、提权、占用探测、系统回收站。

这一层不依赖包内其他模块，是唯一碰 Win32 的地方。设计准则：**任何调用失败
都必须优雅降级，且绝不把「失败」偷偷变成硬删除**——回收站失败就返回 False，
由 cleaner 自己决定归档到数据目录；非 Windows 平台全部走降级路径，import
本身不做任何系统调用。

占用检测没有绑定 Restart Manager（那要维护一整套会话结构体，收益是「列得出
占用进程名」，代价是几百行易错的 FFI）。改用「改名再改回」实测：能不能动是
文件系统说了算，比进程名单更准；进程名只作提示，由 blocking_processes 尽力
解析 tasklist。
"""
from __future__ import annotations

import csv
import ctypes
import io
import os
import shutil
import subprocess

_CREATE_NO_WINDOW = 0x08000000

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def _fallback_drives():
    try:
        root = os.path.abspath(os.sep)
        usage = shutil.disk_usage(root)
        return [{"letter": root[:3] if len(root) >= 3 else root,
                 "label": "", "total": int(usage.total),
                 "free": int(usage.free), "used": int(usage.used), "fs": ""}]
    except OSError:
        return []


def list_drives() -> list[dict]:
    """列出本机逻辑盘及容量；任何一步失败退回当前盘的 shutil 结果。"""
    if os.name != "nt":
        return _fallback_drives()
    try:
        kernel32 = ctypes.windll.kernel32
        mask = int(kernel32.GetLogicalDrives())
        out = []
        for index in range(26):
            if not (mask & (1 << index)):
                continue
            letter = "%s:\\" % chr(ord("A") + index)
            free = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            kernel32.GetDiskFreeSpaceExW.argtypes = [
                ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulonglong),
                ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]
            kernel32.GetDiskFreeSpaceExW.restype = ctypes.c_int
            if not kernel32.GetDiskFreeSpaceExW(
                    letter, ctypes.byref(free), ctypes.byref(total), None):
                continue
            label = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            try:
                kernel32.GetVolumeInformationW.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(ctypes.c_uint), ctypes.c_wchar_p, ctypes.c_uint]
                kernel32.GetVolumeInformationW(
                    letter, label, len(label), None, None, None, fs, len(fs))
            except (AttributeError, OSError):
                pass
            total_v = int(total.value)
            free_v = int(free.value)
            out.append({"letter": letter, "label": label.value,
                        "total": total_v, "free": free_v,
                        "used": max(0, total_v - free_v), "fs": fs.value})
        return out or _fallback_drives()
    except (AttributeError, OSError, ValueError):
        return _fallback_drives()


def is_elevated() -> bool:
    """是否管理员权限。目录联接不需要提权，符号链接才需要，故只作展示。"""
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    try:
        return os.geteuid() == 0
    except (AttributeError, OSError):
        return False


def probe_lock(path) -> dict:
    """改名再改回，实测目标是否被占用；无论成功失败都不留改名残留。"""
    path = os.fspath(path)
    if not os.path.exists(path):
        return {"locked": False, "reason": "路径不存在"}
    probe = path + ".codebee-probe"
    moved = False
    try:
        os.rename(path, probe)
        moved = True
        os.rename(probe, path)
        moved = False
        return {"locked": False, "reason": ""}
    except OSError as exc:
        return {"locked": True,
                "reason": getattr(exc, "strerror", None) or "目标被占用或不可改名"}
    finally:
        if moved:
            try:
                os.rename(probe, path)
            except OSError:
                pass


def blocking_processes(names) -> list[dict]:
    """尽力列出正在运行的同名进程；不支持或解析失败一律返回空列表。"""
    wanted = {str(x).lower() for x in (names or []) if x}
    if not wanted or os.name != "nt":
        return []
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
            timeout=6, creationflags=_CREATE_NO_WINDOW)
        text = (proc.stdout or b"").decode("utf-8", errors="ignore") or \
               (proc.stdout or b"").decode("gbk", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    try:
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 5 or row[0].lower() not in wanted:
                continue
            mem = "".join(ch for ch in row[4] if ch.isdigit())
            out.append({"name": row[0], "pid": row[1], "mem": mem})
    except (csv.Error, UnicodeError):
        return []
    return out


def recycle(path) -> bool:
    """送进系统回收站（可还原）。失败返回 False，绝不退化成硬删。"""
    if os.name != "nt":
        return False
    path = os.path.abspath(os.fspath(path))
    if not os.path.exists(path):
        return False
    try:
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        shell32 = ctypes.windll.shell32
        shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
        shell32.SHFileOperationW.restype = ctypes.c_int
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        # pFrom 是双 NUL 结尾的多字符串，第二个 \0 表示列表结束
        op.pFrom = path + "\x00\x00"
        op.pTo = None
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION |
                     FOF_SILENT | FOF_NOERRORUI)
        op.fAnyOperationsAborted = False
        op.hNameMappings = None
        op.lpszProgressTitle = None
        result = shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted and not os.path.exists(path)
    except (AttributeError, OSError, ValueError, TypeError):
        return False
