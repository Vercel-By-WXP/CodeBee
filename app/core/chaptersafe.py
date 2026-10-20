# -*- coding: utf-8 -*-
"""章节安全落盘（tmp+原子改名，inkos 安全章节工作区借鉴）。

写一半崩溃/磁盘满不会留下半章正文冒充成稿——断点续跑按「文件够长」
判定，半文件会被误当合法稿复用；.tmp 写完 os.replace 原子落位，
失败清理残渣后原样抛出。路径守卫用 pathlib resolve+parents 惯用法
（同 _ms_io/_inside 口径）。pipeline._write_chapter 委托此函数。"""
from __future__ import annotations

import re
from pathlib import Path


# These tags are part of the reviewer protocol in pipeline.py. They are
# persisted separately under .codebee and must never cross into chapter text.
_INTERNAL_BLOCK_RE = re.compile(
    r"(?is)<(?P<tag>event_check|ledger|continuity)\b[^>]*>"
    r".*?</(?P=tag)\s*>"
)


def strip_internal_blocks(text):
    """Remove reviewer-only tagged blocks while preserving ordinary prose."""
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source:
        return ""
    cleaned = _INTERNAL_BLOCK_RE.sub("", source)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def atomic_write_chapter(workdir, i, text):
    """第 i 章安全落盘：tmp 写入 → 原子改名。返回最终路径。"""
    root = Path(workdir).resolve()
    p = (root / ("chapter-%02d.md" % int(i))).resolve()
    # 守卫：目标必须在 workdir 之内且不得是 workdir 本身（".." 作 workdir
    # 时 resolve 到父目录，p 仍是其中一章——但那不是调用方的工作目录，
    # 一并拒绝：workdir 必须是已存在的目录）
    if root not in p.parents or not root.is_dir():
        raise ValueError("章节路径越界: chapter-%02d.md" % int(i))
    tmp = Path(str(p) + ".tmp")
    try:
        tmp.write_text(strip_internal_blocks(text), encoding="utf-8", errors="replace")
        tmp.replace(p)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return p
