"""目录透视：流式枚举与大小统计（只读）。

这层解决什么问题
----------------
为渐进式「空间透视」提供可取消、可注入根目录的遍历：逐条产出文件/目录
元数据、计算整棵子树的字节数与文件数、统计单层子项大小排行，以及列出磁盘
容量。全部产出 dict / list[dict]，路径统一 str，可直接 JSON 序列化。

安全红线
--------
* 严格不进入重解析点：先判定 reparse 再决定是否枚举子项；单个目录出错不
  影响其余结果，错误交给 on_error 或静默跳过。
* 只读，绝不写入；磁盘容量查询在 winapi 不可用或报错时降级到标准库，
  不把异常抛给上层。
* 模块导入阶段不做任何磁盘 IO；winapi 采用函数内延迟导入。

归属：规则数据来自上游 MIT 项目，见仓库根 THIRD_PARTY_NOTICES.md。
"""
from __future__ import annotations

import os
import shutil
import stat as _stat

from . import risk
from . import rules


def human(n) -> str:
    """1024 进制人类可读大小：B / KB / MB / GB / TB。供包内其它模块导入。"""
    try:
        value = float(n)
    except (TypeError, ValueError):
        value = 0.0
    if value < 0:
        value = 0.0
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while value >= 1024.0 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return "%d B" % int(value)
    return "%.1f %s" % (value, units[index])


# 契约里提到的内部别名，保留以便其它模块按需引用。
_human = human


def _report(on_error, exc):
    if callable(on_error):
        try:
            on_error(exc)
        except Exception:
            pass


def _walk(path, cancel, on_error):
    """递归产出单棵子树的条目；root 自身也作为第一项产出。"""
    if cancel is not None and cancel():
        return
    try:
        st = os.lstat(path)
        reparse = risk.is_reparse(path)
    except OSError as exc:
        _report(on_error, exc)
        return
    is_dir = _stat.S_ISDIR(st.st_mode)
    yield {
        "path": str(path),
        "name": os.path.basename(path) or str(path),
        "is_dir": is_dir,
        "size": 0 if is_dir else int(st.st_size),
        "mtime": float(st.st_mtime),
        "reparse": bool(reparse),
    }
    if not is_dir or reparse:
        return
    try:
        with os.scandir(path) as iterator:
            children = [entry.path for entry in iterator]
    except OSError as exc:
        _report(on_error, exc)
        return
    for child in children:
        if cancel is not None and cancel():
            return
        for record in _walk(child, cancel, on_error):
            yield record


def iter_entries(root, cancel=None, on_error=None):
    """流式产出 root 及其子树下每个条目的元数据。

    严格不进入重解析点；cancel 返回 True 时提前停止；OSError 交给 on_error
    （可调用对象）或跳过。
    """
    for record in _walk(os.fspath(root), cancel, on_error):
        yield record


def calc_size(root, cancel=None) -> tuple:
    """返回 (bytes, file_count)；复用 iter_entries，跳过重解析点。"""
    total = 0
    files = 0
    for record in iter_entries(root, cancel=cancel):
        if record["reparse"]:
            continue
        if not record["is_dir"]:
            total += record["size"]
            files += 1
    return (total, files)


def scan_level(root, cancel=None) -> list[dict]:
    """只统计 root 下一层子项，按 size 倒序返回，用于渐进式透视。

    每个子目录用 calc_size 汇总整棵子树，文件用自身大小；单个子项失败不
    影响其余。heuristic 字段优先取命中规则的 classify，未收录则用启发式。
    """
    root = os.fspath(root)
    result = []
    try:
        with os.scandir(root) as iterator:
            children = [entry.path for entry in iterator]
    except OSError:
        return result
    for child in children:
        if cancel is not None and cancel():
            break
        try:
            st = os.lstat(child)
            reparse = risk.is_reparse(child)
            is_dir = _stat.S_ISDIR(st.st_mode)
            if is_dir and not reparse:
                size, files = calc_size(child, cancel=cancel)
            elif is_dir:
                size, files = 0, 0
            else:
                size, files = int(st.st_size), (0 if reparse else 1)
            rule = rules.match_rule(child)
            judged = risk.classify(rule) if rule else risk.heuristic(child)
            result.append({
                "path": str(child),
                "name": os.path.basename(child) or str(child),
                "is_dir": is_dir,
                "size": int(size),
                "size_human": human(size),
                "files": int(files),
                "mtime": float(st.st_mtime),
                "reparse": bool(reparse),
                "heuristic": judged,
            })
        except OSError:
            continue
    result.sort(key=lambda item: item["size"], reverse=True)
    return result


def _pick(mapping, keys, default=""):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_drive(item) -> dict | None:
    """把 winapi 返回的条目规整成统一结构；无法识别返回 None。"""
    if isinstance(item, str):
        info = {"letter": item}
    elif isinstance(item, dict):
        info = dict(item)
    else:
        return None
    letter = str(_pick(info, ("letter", "drive", "device", "name"), "") or "").rstrip(":\\/")
    label = str(_pick(info, ("label", "volume", "volume_name"), "") or "")
    fs = str(_pick(info, ("fs", "fstype", "filesystem", "file_system"), "") or "")
    total = _to_int(_pick(info, ("total", "total_bytes", "capacity", "size"), 0))
    free = _to_int(_pick(info, ("free", "free_bytes", "available", "free_space"), 0))
    if total <= 0 and letter:
        disk_root = letter + ":" + os.sep if len(letter) == 1 else letter + os.sep
        try:
            usage = shutil.disk_usage(disk_root)
            total = int(usage.total)
            free = int(usage.free)
        except OSError:
            pass
    return {
        "letter": letter,
        "label": label,
        "total": total,
        "free": free,
        "used": max(total - free, 0),
        "fs": fs,
        "total_human": human(total),
        "free_human": human(free),
    }


def _fallback_drive() -> dict:
    """winapi 不可用时的降级条目：当前盘一份 shutil.disk_usage。"""
    try:
        cwd = os.path.abspath(os.getcwd())
    except OSError:
        cwd = os.path.abspath(os.sep)
    drive = os.path.splitdrive(cwd)[0]
    disk_root = (drive + os.sep) if drive else os.path.abspath(os.sep)
    total = free = 0
    try:
        usage = shutil.disk_usage(disk_root)
        total = int(usage.total)
        free = int(usage.free)
    except OSError:
        pass
    return {
        "letter": drive.rstrip(":\\/") if drive else "",
        "label": "",
        "total": total,
        "free": free,
        "used": max(total - free, 0),
        "fs": "",
        "total_human": human(total),
        "free_human": human(free),
    }


def drives() -> list[dict]:
    """列出磁盘容量；winapi 缺失或报错时降级为当前盘单条目。"""
    raw = None
    try:
        from . import winapi
        raw = winapi.list_drives()
    except Exception:
        raw = None
    out = []
    if raw:
        for item in raw:
            normalized = _normalize_drive(item)
            if normalized is not None:
                out.append(normalized)
    if not out:
        out.append(_fallback_drive())
    return out
