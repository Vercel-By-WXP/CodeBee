# -*- coding: utf-8 -*-
"""发布台账：每次发布动作（连接/建书/传章节）追加一条 JSONL，append-only。

落盘 data/publish/publish-YYYYMM.jsonl（按月分文件，与用量台账同范式）。
幂等键 (task_id, platform, chapter_no)：章节发布成功后重发即跳过——
断点续发（发布到一半挂了，重启后接着发没发完的）靠这一条。

作品登记 books.json：task ↔ 平台作品的绑定（建书成功后写入 book_id/标题），
原子写（tmp + os.replace）。与台账分工：台账只追加不改（审计/历史），
可变状态（当前绑定的作品）进 books.json。

截图存证：data/publish/shots/<platform>/<task_id>/<时间戳>-<步骤名>.png，
每步动作后落一张，出错可回看卡在哪一步；旧截图按任务清理不自动做（量小）。
"""
from __future__ import annotations

import json
import re
import threading
import time

from .. import paths

LOCK = threading.RLock()

FIELDS = ("ts", "day", "platform", "action", "task_id", "chapter_no",
          "book_id", "title", "ok", "error", "shot", "operation_id",
          "operation_status", "remote_receipt")


def _month_file(day):
    return paths.PUBLISH_DIR / ("publish-%s.jsonl" % day[:7].replace("-", ""))


def record(platform, action, task_id="", chapter_no=0, book_id="",
           title="", ok=True, error="", shot="", operation_id="",
           operation_status="", remote_receipt=""):
    """追加一条发布记录。异常全吞——记账失败绝不能影响发布主流程。"""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "day": time.strftime("%Y-%m-%d"),
            "platform": str(platform)[:16],
            "action": str(action)[:24],
            "task_id": str(task_id)[:64],
            "chapter_no": int(chapter_no or 0),
            "book_id": str(book_id)[:80],
            "title": str(title)[:120],
            "ok": bool(ok),
            "error": str(error or "")[:300],
            "shot": str(shot)[:200],
            "operation_id": str(operation_id or "")[:80],
            "operation_status": str(operation_status or "")[:20],
            "remote_receipt": str(remote_receipt or "")[:300],
        }
        with LOCK:
            paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            with open(_month_file(rec["day"]), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _iter_records(days=90):
    """近 N 天的记录（月份文件粒度粗滤，行内再按 day 过滤）。"""
    import datetime
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    for fp in sorted(paths.PUBLISH_DIR.glob("publish-*.jsonl")):
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if str(r.get("day") or "") >= cutoff:
                yield r


def published_chapters(task_id, platform):
    """该任务在该平台已成功发布的章节号集合（幂等跳过依据）。"""
    out = set()
    for r in _iter_records(3650):
        if (r.get("action") == "upload_chapter" and r.get("ok")
                and r.get("task_id") == task_id and r.get("platform") == platform):
            n = r.get("chapter_no") or 0
            if n > 0:
                out.add(int(n))
    return out


def recent(task_id=None, platform=None, limit=50):
    """最近记录（新在前），详情页发布历史用。"""
    out = []
    for r in _iter_records(90):
        if task_id and r.get("task_id") != task_id:
            continue
        if platform and r.get("platform") != platform:
            continue
        out.append(r)
    out.reverse()
    return out[:limit]


# ---------------------------------------------------------------- 作品登记
_BOOKS_FILE = paths.PUBLISH_DIR / "books.json"


def load_books():
    try:
        d = json.loads(_BOOKS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_book(task_id, platform, info):
    """登记/更新任务在某平台的作品绑定。info: {book_id, title, url?}。"""
    with LOCK:
        paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        books = load_books()
        books.setdefault(str(task_id), {})[str(platform)] = {
            "book_id": str(info.get("book_id") or ""),
            "title": str(info.get("title") or "")[:120],
            "url": str(info.get("url") or "")[:300],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = _BOOKS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(books, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_BOOKS_FILE)


def book_for(task_id, platform):
    return ((load_books().get(str(task_id)) or {}).get(str(platform))) or None


def shot_path(platform, task_id, step):
    """截图存证路径（只算路径不落盘，由 browser.Page.screenshot 写）。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    return paths.PUBLISH_DIR / "shots" / str(platform) / str(task_id) / \
        ("%s-%s.png" % (ts, step))


_CHAPTER_RE = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百千零两]+)\s*[章节回]")


def parse_chapter_no(name):
    """从章节标题/文件名解析章号（第12章/第十二章），失败返回 0。"""
    m = _CHAPTER_RE.search(str(name or ""))
    if not m:
        return 0
    s = m.group(1)
    if s.isdigit():
        return int(s)
    cn = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9}
    # 正序解析（十二=12、二十三=23、一百零五=105）：数字暂存 num，
    # 遇单位（十/百/千）把它乘上去并清零；万字内够用（章号没有更大的）
    section, num = 0, 0
    for ch in s:
        if ch in cn:
            num = cn[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "千":
            section += (num or 1) * 1000
            num = 0
    return section + num


# ---------------------------------------------------------------- 护栏统计
def today_count(task_id, platform):
    """该任务在该平台今日已成功发布的章数（每日上限护栏的计数口径）。"""
    day = time.strftime("%Y-%m-%d")
    return sum(1 for r in _iter_records(2)
               if r.get("action") == "upload_chapter" and r.get("ok")
               and r.get("task_id") == task_id and r.get("platform") == platform
               and r.get("day") == day)


def consecutive_failures(platform):
    """该平台最近连续失败的发布动作数（连败退避护栏，跨任务口径）。

    记录时间序旧→新，倒着数到第一条成功为止；连败大概率是风控或改版，
    此时继续自动重试只会火上浇油，应转人工检查。
    """
    n = 0
    for r in reversed(list(_iter_records(7))):
        if r.get("platform") != platform:
            continue
        if r.get("action") not in ("upload_chapter", "create_book"):
            continue
        if r.get("ok"):
            break
        n += 1
    return n
