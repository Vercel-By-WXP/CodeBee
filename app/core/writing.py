# -*- coding: utf-8 -*-
"""已写字数：把「目标字数」和「真的写了多少」分开。

作者端最在意的一行是「已经写了多少字」。此前界面显示的是任务的**目标**字数
（``task.story.words`` / ``task.serial.chapters × 每章字数``），看起来像进度，
其实是承诺——写得多了少了它都不会变。这个模块只回答一个事实问题：磁盘上现在
有多少字。

三个真源，按可靠性从高到低：

1. ``story_tracking``（连载）：逐章登记的 ``word_count``，是全书口径，
   前缀被归档进「已成稿/」也不会丢；
2. 章文件（连载兜底）：``chapter-NN.md`` 逐章计字，只覆盖还在工作目录里的章；
3. 稿件文件（短篇/长篇）：``task.manuscript``（默认 ``story.md``）整篇计字。

``source`` 如实回传用了哪一个真源——界面据此决定要不要写「估算」，**不编造**。
没写过任何字时 ``words=0`` 且 ``known=False``，界面显示「—」而不是「0 字」，
因为「没开始写」和「写了 0 字」对作者是两件事。
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

_SPACE_RE = re.compile(r"\s")
_CACHE_TTL = 5.0                     # 秒；作者端 3 秒轮询，5 秒缓存足够且不积压
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def count_text(text):
    """近似字数：去掉空白后的字符数（中文场景够用，与 pipeline 同口径）。"""
    return len(_SPACE_RE.sub("", text or ""))


def _read_text(path):
    """按 UTF-8 → GBK 顺序读文本，读不到返回 ""。

    章稿可能由外部 CLI 以 GBK 落盘（历史上踩过），这里不做编码猜测以外的
    任何处理：字数统计读失败只该让这个数字不可用，不该让接口报错。
    """
    for enc in ("utf-8", "gbk"):
        try:
            return Path(path).read_text(encoding=enc)
        except (OSError, UnicodeDecodeError):
            continue
    return ""


def _target(task):
    """目标字数。纯承诺值，拿不到就是 0（界面显示「未设定」）。"""
    story = task.get("story") if isinstance(task.get("story"), dict) else {}
    if story.get("words"):
        try:
            return max(0, int(story["words"]))
        except (TypeError, ValueError):
            pass
    serial = task.get("serial") if isinstance(task.get("serial"), dict) else {}
    try:
        chapters = int(serial.get("chapters") or 0)
        per = int(serial.get("words_per_chapter") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, chapters * per)


def _from_tracking(workdir):
    """连载：逐章登记的字数。返回 (words, chapters, rows) 或 None。"""
    from . import story_tracking
    try:
        state = story_tracking.load(workdir)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    rows, total = [], 0
    for item in state.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("chapter") or 0)
            words = int(item.get("word_count") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        rows.append({"no": number, "words": max(0, words)})
        total += max(0, words)
    if not rows:
        return None                          # 登记表存在但还没提交过章
    rows.sort(key=lambda r: r["no"])
    return total, len(rows), rows


def _from_chapter_files(workdir):
    """连载兜底：数工作目录里现存的 chapter-NN.md。返回 (words, chapters, rows)。"""
    try:
        names = os.listdir(workdir)
    except OSError:
        return 0, 0, []
    rows = []
    for name in names:
        m = re.match(r"^chapter-(\d+)\.md$", name, re.I)
        if not m:
            continue
        number = int(m.group(1))
        text = _read_text(os.path.join(workdir, name))
        if not text.strip():
            continue
        rows.append({"no": number, "words": count_text(text)})
    rows.sort(key=lambda r: r["no"])
    return sum(r["words"] for r in rows), len(rows), rows


def _manuscript_path(task, workdir):
    name = str(task.get("manuscript") or "").strip() or "story.md"
    # 只接受工作目录内的文件名：稿件名来自任务数据，不该能指向任意路径。
    if os.path.basename(name) != name:
        name = os.path.basename(name)
    return os.path.join(workdir, name)


def summary(task):
    """一本书的已写字数。未开始写时 words=0 且 known=False。"""
    task = task or {}
    workdir = str(task.get("workdir") or "")
    target = _target(task)
    out = {"target": target, "words": 0, "chapters": 0,
           "known": False, "source": "none", "rows": []}
    if not workdir or not os.path.isdir(workdir):
        return out

    serial = bool(task.get("serial")) or str(task.get("type") or "") == "serial_novel"
    if serial:
        got = _from_tracking(workdir)
        if got:
            words, chapters, rows = got
            out.update(words=words, chapters=chapters, rows=rows[-400:],
                       known=words > 0, source="tracking")
            return out
        words, chapters, rows = _from_chapter_files(workdir)
        if chapters:
            out.update(words=words, chapters=chapters, rows=rows[-400:],
                       known=words > 0, source="files")
        return out

    path = _manuscript_path(task, workdir)
    if os.path.isfile(path):
        words = count_text(_read_text(path))
        if words:
            out.update(words=words, chapters=1, known=True, source="manuscript",
                       rows=[{"no": 1, "words": words}])
    return out


def _sig(task):
    """缓存键的签名：工作目录 + 稿件 + 追踪状态 + 章文件的 mtime/大小。

    只看目录 mtime 会漏：章文件改写时目录 mtime 不变，字数却变了。
    """
    workdir = str(task.get("workdir") or "")
    sig = [workdir]
    for rel in (".codebee/story_tracking.json",):
        try:
            st = os.stat(os.path.join(workdir, rel))
            sig.append((rel, int(st.st_mtime), st.st_size))
        except OSError:
            sig.append((rel, 0, 0))
    try:
        for name in sorted(os.listdir(workdir)):
            if name.lower().endswith(".md"):
                try:
                    st = os.stat(os.path.join(workdir, name))
                    sig.append((name, int(st.st_mtime), st.st_size))
                except OSError:
                    continue
    except OSError:
        pass
    return tuple(sig)


def cached_summary(task):
    """带 5 秒缓存的 summary。作者端 3 秒轮询一本书时不会反复读全稿。"""
    key = str((task or {}).get("id") or "")
    if not key:
        return summary(task)
    sig = _sig(task)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and hit[0] == sig and now - hit[1] < _CACHE_TTL:
        return dict(hit[2], cached=True)
    result = summary(task)
    with _CACHE_LOCK:
        _CACHE[key] = (sig, now, result)
    return result


def counts_for(tasks):
    """批量字数（书架用）。返回 {task_id: summary}。"""
    out = {}
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        try:
            out[tid] = cached_summary(task)
        except Exception:
            out[tid] = {"target": _target(task), "words": 0, "chapters": 0,
                        "known": False, "source": "none", "rows": []}
    return out


def forget(task_id=""):
    """清缓存（任务被删除/重建时用）。task_id 为空清全量。"""
    with _CACHE_LOCK:
        if task_id:
            _CACHE.pop(str(task_id), None)
        else:
            _CACHE.clear()
