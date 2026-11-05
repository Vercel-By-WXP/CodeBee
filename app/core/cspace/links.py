# -*- coding: utf-8 -*-
"""目录联接 / 符号链接 / 硬链接的创建与拆除。

Windows 的目录联接（Junction）不需要管理员权限，符号链接才需要，所以搬家
默认走 Junction：把数据搬到别的盘，原路径留下一个重解析点，软件继续按老
路径读写，新产生的缓存自然落到新盘。

拆除链接是全流程最危险的一步：``remove_link`` 必须先确认目标确实是链接，
才用 os.rmdir（只摘重解析点，不碰目标盘的实际文件）或 os.unlink（只删文件
软链）。任何一步拿不准都拒绝执行，绝不退化成删目录。
"""
from __future__ import annotations

import os
import stat
import subprocess

from . import risk, winapi

_CREATE_NO_WINDOW = 0x08000000
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def is_link(path) -> bool:
    """真的链接才返回 True；普通目录、不存在的路径一律 False。"""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    if os.name == "nt":
        attr = int(getattr(st, "st_file_attributes", 0) or 0)
        return bool(attr & _REPARSE_ATTR)
    return False


_NT_PREFIXES = ("\\??\\", "\\\\?\\")


def _strip_nt(text):
    """剥掉 Windows 重解析点的 NT 路径前缀，留给调用方的是普通盘符路径。"""
    text = (text or "").strip()
    for prefix in _NT_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def link_target(path) -> str | None:
    """尽力读出链接指向；读不到返回 None，不猜、不编。"""
    if not is_link(path):
        return None
    try:
        return _strip_nt(os.readlink(path)) or None
    except (OSError, ValueError):
        pass
    if os.name != "nt":
        return None
    try:
        proc = subprocess.run(
            ["fsutil", "reparsepoint", "query", os.fspath(path)],
            capture_output=True, timeout=6, creationflags=_CREATE_NO_WINDOW)
        text = (proc.stdout or b"").decode("utf-8", errors="ignore")
        for line in text.splitlines():
            head, sep, tail = line.partition(":")
            key = head.lower()
            # 系统输出里这个标签常被拼成 Substitue（少一个 t），两种都认。
            if sep and ("substitute name" in key or "substitue name" in key
                        or "替代名称" in head):
                # 只切标签后的第一个冒号，tail 里的 "C:\..." 必须完整保留。
                return _strip_nt(tail.strip()) or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _dry(kind, link, target):
    return {"dry_run": True, "ok": False, "kind": kind,
            "link": str(link), "target": str(target),
            "would": ["检查 link 不存在 / target 存在 / 非安全红线",
                      "创建 %s" % kind]}


def _precheck(link, target):
    link = os.path.normpath(os.fspath(link))
    target = os.path.normpath(os.fspath(target))
    if os.path.lexists(link):
        raise ValueError("链接位置已存在，拒绝覆盖：%s" % link)
    if not os.path.exists(target):
        raise ValueError("目标不存在：%s" % target)
    risk.assert_action_allowed(link, "link")
    return link, target


def create_junction(link, target, confirm=False) -> dict:
    """目录联接。Windows 用 mklink /J，其他平台降级 os.symlink。"""
    if confirm is not True:
        return _dry("junction", link, target)
    try:
        link, target = _precheck(link, target)
        if not os.path.isdir(target):
            raise ValueError("目录联接的目标必须是文件夹")
        if os.name == "nt":
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", link, target],
                capture_output=True, timeout=20, creationflags=_CREATE_NO_WINDOW)
            if proc.returncode != 0 or not is_link(link):
                raise OSError("mklink 没能建立目录联接")
        else:
            os.symlink(target, link, target_is_directory=True)
    except (OSError, ValueError, subprocess.SubprocessError, risk.UnsafePath) as exc:
        return {"ok": False, "error": "创建目录联接失败，未改动原目录",
                "detail": str(exc)}
    return {"ok": True, "kind": "junction", "link": link,
            "target": target, "elevated": winapi.is_elevated()}


def create_symlink(link, target, is_dir=True, confirm=False) -> dict:
    """目录/文件符号链接；Windows 通常需要开发者模式或管理员权限。"""
    if confirm is not True:
        return _dry("symlink", link, target)
    try:
        link, target = _precheck(link, target)
        os.symlink(target, link, target_is_directory=bool(is_dir))
    except (OSError, ValueError, NotImplementedError, risk.UnsafePath) as exc:
        return {"ok": False, "error": "创建符号链接失败（可能需要管理员权限）",
                "detail": str(exc)}
    return {"ok": True, "kind": "symlink", "link": link, "target": target}


def create_hardlink(link, target, confirm=False) -> dict:
    if confirm is not True:
        return _dry("hardlink", link, target)
    try:
        link, target = _precheck(link, target)
        if not os.path.isfile(target):
            raise ValueError("硬链接只能指向文件")
        os.link(target, link)
    except (OSError, ValueError, risk.UnsafePath) as exc:
        return {"ok": False, "error": "创建硬链接失败", "detail": str(exc)}
    return {"ok": True, "kind": "hardlink", "link": link, "target": target}


def remove_link(path, confirm=False) -> dict:
    """只摘链接本身；不是链接就拒绝，绝不删真实目录。"""
    path = os.path.normpath(os.fspath(path))
    if confirm is not True:
        return {"dry_run": True, "ok": False, "link": path}
    if not is_link(path):
        return {"ok": False, "error": "目标不是链接，拒绝拆除（防止误删真实目录）"}
    try:
        st = os.lstat(path)
        if stat.S_ISDIR(st.st_mode):
            os.rmdir(path)      # Junction：只删重解析点，不动目标盘数据
        else:
            os.unlink(path)     # 文件软链：只删链接
    except OSError as exc:
        return {"ok": False, "error": "拆除链接失败", "detail": str(exc)}
    return {"ok": True, "link": path, "removed": True}


def inspect(entry) -> dict:
    """给账本条目补状态：链接还在吗、目标还在吗、是不是断链。"""
    source = (entry or {}).get("source") or ""
    target = (entry or {}).get("target") or ""
    exists = bool(source) and os.path.lexists(source)
    target_exists = bool(target) and os.path.exists(target)
    return {"exists": exists, "target_exists": target_exists,
            "broken": (not exists) or (not target_exists)}
