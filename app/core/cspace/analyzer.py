# -*- coding: utf-8 -*-
"""大文件与重复文件分析：三阶段确认，只读优先，替换留后路。

重复文件判定绝不能靠「文件名像」或「尺寸一样」：先用精确字节数筛掉唯一
尺寸，再用头尾各 8KB 采样哈希压候选，最后对候选做全量 SHA-256。只有全文
一致才成组——错判一组，用户点一下就是真数据没了。

唯一写动作 ``replace_with_hardlink`` 默认只给计划；确认后也是「先把副本改名
留备份 → 建硬链接 → 成功才删备份 → 失败立刻把备份改回来」，任何时刻都不
存在原件被 unlink 而无处找回的窗口。
"""
from __future__ import annotations

import hashlib
import os

from . import risk, scanner

_SAMPLE = 8192
_CHUNK = 1024 * 1024

# 扩展名 → 用户能一眼看懂的类别（与上游规则库的分类口径一致）
_CATEGORIES = (
    (("虚拟机镜像",), (".vhdx", ".vmdk", ".vdi", ".qcow2", ".iso", ".vhd")),
    (("模型权重",), (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".onnx", ".ckpt")),
    (("音视频",), (".mp4", ".mkv", ".avi", ".mov", ".flac", ".wav", ".m4a", ".mp3")),
    (("压缩包",), (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz")),
    (("数据库与备份",), (".bak", ".sql", ".db", ".sqlite", ".sqlite3", ".mdf", ".dmp")),
)


def _category(path):
    ext = os.path.splitext(str(path))[1].lower()
    for label, exts in _CATEGORIES:
        if ext in exts:
            return label[0]
    return "其他"


def _sha256_file(path, chunk=_CHUNK):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def _sample_hash(path):
    """头 8KB + 尾 8KB 的采样指纹；文件比两段还短时就是全文哈希。"""
    h = hashlib.sha256()
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(_SAMPLE)
            h.update(head)
            if size > _SAMPLE * 2:
                f.seek(-_SAMPLE, os.SEEK_END)
                h.update(f.read(_SAMPLE))
    except OSError:
        return None
    return h.hexdigest()


def _iter_files(roots, cancel=None):
    for root in roots or []:
        if not root or not os.path.exists(root):
            continue
        for record in scanner.iter_entries(root, cancel=cancel):
            if cancel is not None and cancel():
                return
            if record["is_dir"] or record["reparse"]:
                continue
            path = record["path"]
            if risk.is_forbidden(path):
                continue
            yield path, int(record["size"]), float(record["mtime"])


def big_files(roots, min_bytes=100 * 1024 * 1024, cancel=None, limit=500) -> list[dict]:
    """扫大文件，只返回元数据，不读内容、不改动任何文件。"""
    out = []
    for path, size, mtime in _iter_files(roots, cancel=cancel):
        if size < min_bytes:
            continue
        out.append({"path": path, "size": size,
                    "size_human": scanner.human(size), "mtime": mtime,
                    "category": _category(path)})
        if len(out) >= limit * 4:
            break
    out.sort(key=lambda x: -x["size"])
    return out[:limit]


def duplicate_groups(roots, min_bytes=1024 * 1024, cancel=None) -> list[dict]:
    """三阶段：尺寸 → 头尾采样 → 全量 SHA-256。只有全文一致的才成组。"""
    by_size = {}
    for path, size, _mtime in _iter_files(roots, cancel=cancel):
        if size < min_bytes:
            continue
        by_size.setdefault(size, []).append(path)

    by_sample = {}
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            digest = _sample_hash(path)
            if digest:
                by_sample.setdefault((size, digest), []).append(path)

    by_full = {}
    for (size, _sample), paths in by_sample.items():
        if len(paths) < 2:
            continue
        for path in paths:
            digest = _sha256_file(path)
            if digest:
                by_full.setdefault((size, digest), []).append(path)

    groups = []
    for (size, digest), paths in by_full.items():
        paths = sorted(set(paths))
        if len(paths) < 2:
            continue
        wasted = size * (len(paths) - 1)
        groups.append({"digest": digest, "size": size,
                       "size_human": scanner.human(size),
                       "files": paths, "wasted": wasted,
                       "wasted_human": scanner.human(wasted)})
    groups.sort(key=lambda x: -x["wasted"])
    return groups


def replace_with_hardlink(keep, duplicate, confirm=False) -> dict:
    """把冗余副本换成指向 keep 的硬链接：路径不变、内容不变、立刻省一份空间。

    confirm=False 只返回计划。confirm=True 时再次比对全文哈希，不一致直接拒；
    全程用「改名备份」而不是先删，任一异常都把备份改回原位。
    """
    keep = os.fspath(keep)
    duplicate = os.fspath(duplicate)
    plan = {"dry_run": True, "would": [{"keep": keep, "replace": duplicate}]}
    if confirm is not True:
        return plan
    for path in (keep, duplicate):
        risk.assert_action_allowed(path, "clean")
    if not os.path.isfile(keep) or not os.path.isfile(duplicate):
        return {"ok": False, "error": "两个路径都必须是存在的文件"}
    if os.path.normcase(os.path.abspath(keep)) == os.path.normcase(os.path.abspath(duplicate)):
        return {"ok": False, "error": "不能拿同一个文件替换自己"}
    if _sha256_file(keep) != _sha256_file(duplicate):
        return {"ok": False, "error": "全文哈希不一致，拒绝替换"}

    size = os.path.getsize(duplicate)
    backup = duplicate + ".codebee-dup-bak"
    if os.path.exists(backup):
        return {"ok": False, "error": "备份名已被占用，请先清理旧备份"}
    try:
        os.replace(duplicate, backup)
    except OSError as exc:
        return {"ok": False, "error": "副本改名失败，未改动任何文件",
                "detail": getattr(exc, "strerror", None) or type(exc).__name__}
    try:
        os.link(keep, duplicate)
    except OSError as exc:
        try:
            os.replace(backup, duplicate)
        except OSError:
            pass
        return {"ok": False, "error": "建硬链接失败，副本已原样恢复",
                "detail": getattr(exc, "strerror", None) or type(exc).__name__}
    try:
        os.unlink(backup)  # 只删自己刚建的临时备份，原件从未被 unlink
    except OSError:
        pass
    return {"ok": True, "freed": size, "freed_human": scanner.human(size),
            "keep": keep, "linked": duplicate, "errors": []}
