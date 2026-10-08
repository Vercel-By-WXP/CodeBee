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
    """该任务在该平台已成功发布的章节号集合（幂等跳过依据）。

    连载链合并口径：同一本书一条链，任一批次发过的章号整链可见——续写任务
    重发首批已发章节会被这里拦下。
    并入校准所得的平台实况章号（remote_published_nos）：用户在浏览器里手工
    补交的章本地台账没有记录，不并入的话「发布全部待发」会把平台已有的章
    再发一遍（2026-10-08 实案：平台 30 章台账 7，待发清单里全是已发过的）。
    章号以最近一次校准为准——手工发布后重新校准即可刷新。"""
    from .. import store
    try:
        ids = set(store.serial_chain_ids(task_id))
    except Exception:
        ids = {str(task_id)}
    out = set()
    for r in _iter_records(3650):
        if (r.get("action") == "upload_chapter" and r.get("ok")
                and r.get("task_id") in ids and r.get("platform") == platform):
            n = r.get("chapter_no") or 0
            if n > 0:
                out.add(int(n))
    try:
        ent = book_for(task_id, platform) or {}
        for n in (ent.get("remote_published_nos") or []):
            n = int(n)
            if n > 0:
                out.add(n)
    except (TypeError, ValueError):
        pass
    return out


def recent(task_id=None, platform=None, limit=50):
    """最近记录（新在前），详情页发布历史用。task_id 给定时按连载链合并。"""
    from .. import store
    ids = None
    if task_id:
        try:
            ids = set(store.serial_chain_ids(task_id))
        except Exception:
            ids = {str(task_id)}
    out = []
    for r in _iter_records(90):
        if ids is not None and r.get("task_id") not in ids:
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


def _chain_order(task_id):
    """连载链条 id 序列（根在前、自己紧随其后）：书籍绑定沿链共享的遍历序。

    自己优先于祖先：续写任务上补找回的 book_id（写在自身条目）不该被根上
    缺 id 的旧条目盖住。链取不到时回退 [task_id]。"""
    from .. import store
    try:
        chain = [str(t) for t in store.serial_chain_ids(task_id)]
    except Exception:
        chain = []
    chain = chain or [str(task_id)]
    tid = str(task_id)
    return [tid] + [t for t in chain if t != tid]


def save_book(task_id, platform, info):
    """登记/更新任务在某平台的作品绑定。info: {book_id, title, url?}。

    绑定属于「这本书」而不是某一批章节：写入链条上已有条目的任务（通常根
    任务），链条全空则落到根上——续写批次建书/补账不会在链条上分叉出第二份。

    合并语义：只覆写登记四键，保留条目上的其他字段（如校准所得 remote_*），
    否则发章链里找回 book_id 的一次 save_book 会把校准结果抹掉。"""
    with LOCK:
        paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        books = load_books()
        target = next((t for t in _chain_order(task_id)
                       if (books.get(t) or {}).get(str(platform))), None)
        if target is None:
            from .. import store          # 链条全空：绑定落到根任务上
            try:
                target = str(store.serial_chain_ids(task_id)[0])
            except Exception:
                target = str(task_id)
        entry = books.setdefault(target, {}).setdefault(str(platform), {})
        entry.update({
            "book_id": str(info.get("book_id") or ""),
            "title": str(info.get("title") or "")[:120],
            "url": str(info.get("url") or "")[:300],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        # Distinguish a title-only manual binding from an unverified create-book result.
        if "source" in info:
            entry["source"] = str(info.get("source") or "")[:20]
        tmp = _BOOKS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(books, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_BOOKS_FILE)


def update_book(task_id, platform, **fields):
    """原地合并更新登记条目（如平台校准数 remote_*），不碰 book_id/title 等既有键。

    save_book 是整条覆写语义（建书/找回 id 用），校准字段走这里才不会被
    下一次 save_book 冲掉。连载链沿链找已有条目更新（校准从哪个批次发起
    都落在这本书的账上）；链条上没登记过不凭空造条目。"""
    if not fields:
        return
    with LOCK:
        books = load_books()
        target = next((t for t in _chain_order(task_id)
                       if (books.get(t) or {}).get(str(platform))), None)
        if target is None:
            return                      # 没登记过的书不凭空造条目
        ent = books[target][str(platform)]
        for k, v in fields.items():
            if v is None:
                ent.pop(k, None)        # None=删键（换绑书时作废旧校准数）
            else:
                ent[k] = v
        tmp = _BOOKS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(books, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_BOOKS_FILE)


def book_for(task_id, platform):
    """任务在某平台的作品绑定；连载链沿链继承（自己条目优先，其次祖先根）。"""
    books = load_books()
    for tid in _chain_order(task_id):
        ent = (books.get(tid) or {}).get(str(platform))
        if ent:
            return ent
    return None


def books_for(task_id):
    """任务在两平台的已登记绑定（沿连载链继承，都是用第一个）。

    history / sync-published / pending 等前端视图的统一口径——续写批次
    看到的就是这本书的登记，别处直查 load_books().get(tid) 会跟它们打架。"""
    out = {}
    for p in ("fanqie", "qimao"):
        ent = book_for(task_id, p)
        if ent:
            out[p] = ent
    return out


def shot_path(platform, task_id, step):
    """截图存证路径（只算路径不落盘，由 browser.Page.screenshot 写）。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    return paths.PUBLISH_DIR / "shots" / str(platform) / str(task_id) / \
        ("%s-%s.png" % (ts, step))


_CHAPTER_RE = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百千零两]+)\s*[章节回]")
_CHAPTER_FILE_RE = re.compile(
    r"(?:^|[/\\_-])(?:chapter|chap|ch)[-_ ]*([0-9０-９]+)(?:\D|$)",
    re.IGNORECASE,
)


def parse_chapter_no(name):
    """从章节标题/文件名解析章号（第12章/第十二章/chapter-12），失败返回 0。"""
    raw = str(name or "")
    m = _CHAPTER_RE.search(raw)
    if not m:
        m = _CHAPTER_FILE_RE.search(raw)
        if not m:
            return 0
        try:
            return int(m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        except (TypeError, ValueError):
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


def fail_window_h():
    """连败统计时间窗（小时，1-168，默认 12）。

    「连续失败」只数窗口内的：失败原因若是代码 bug（2026-10-08 番茄序号空
    实案，修好即愈），修好后的新发布不该被 9 天攒下的 17 连败永久卡死。"""
    from .. import settings
    try:
        v = float((settings.load() or {}).get("publish_fail_window_h") or 12)
    except Exception:
        return 12.0
    return max(1.0, min(168.0, v))


def _rec_time(r):
    """台账记录的时间戳（epoch 秒）；解析不了返回 None。"""
    ts = str(r.get("ts") or "")
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError, OverflowError):
        return None


def consecutive_failures(platform):
    """该平台最近连续失败的发布动作数（连败退避护栏，跨任务口径）。

    记录时间序旧→新，倒着数到第一条成功为止；连败大概率是风控或改版，
    此时继续自动重试只会火上浇油，应转人工检查。
    只数时间窗内（fail_window_h，默认 12 小时）的失败：碰到窗口外的记录
    即停——「连续」是时间上的连续，隔天的旧失败不进口径，否则一次代码
    bug 的历史失败会在修好后永久卡死发布（2026-10-08 实案：序号 bug 修好
    后 17 连败仍拦新发布，死锁）。"""
    import datetime
    cutoff = time.time() - fail_window_h() * 3600
    n = 0
    for r in reversed(list(_iter_records(7))):
        if r.get("platform") != platform:
            continue
        if r.get("action") not in ("upload_chapter", "create_book"):
            continue
        t = _rec_time(r)
        if t is None:
            day = str(r.get("day") or "")
            try:
                t = time.mktime(datetime.datetime.strptime(
                    day, "%Y-%m-%d").timetuple()) + 86399
            except ValueError:
                continue                  # 无时间信息的记录不进连败口径
        if t < cutoff:
            break                         # 窗口外：再旧的成功/失败都不相干
        if r.get("ok"):
            break
        n += 1
    return n
