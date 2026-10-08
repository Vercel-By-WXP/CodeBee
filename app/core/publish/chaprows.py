# -*- coding: utf-8 -*-
"""章节管理页的行收集与分桶（校准的数据面，manager 只做编排）。

从 manager.py 拆出（manager 超 1200 行行数基线，2026-10-08）：收集器只吃
page + 平台 CONFIG，不依赖任何内部模块——放 L0 层无依赖。
状态关键词口径与台账一致：已发布 / 待审核·审核中·排队 / 未通过·驳回。
"""
from __future__ import annotations

import re
import time

_STATUS_PUB = "已发布"
_STATUS_REVIEW = ("待审核", "审核中", "排队")
_STATUS_BAD = ("未通过", "驳回")

_CH_NO_RE = re.compile(r"第\s*(\d+)\s*章")


def bucket_chapter_rows(rows):
    """章节管理页的行文本 → {total, published, review, rejected, published_nos}。

    行里含状态关键词才计入对应桶；total=识别出的章节数据行数。
    published_nos=已发布行里解析出的章号（第N章，阿拉伯数字）——本地台账
    只记得 CodeBee 自己发的章，用户手工补交的只有平台知道，校准把它带
    回来供幂等/待发清单对账。"""
    out = {"total": 0, "published": 0, "review": 0, "rejected": 0,
           "published_nos": []}
    nos = set()
    for t in rows or []:
        t = str(t)
        if not t:
            continue
        if "章节名称" in t:                     # 表头兜底（正常到不了这）
            continue
        out["total"] += 1
        if any(k in t for k in _STATUS_BAD):
            out["rejected"] += 1
        elif _STATUS_PUB in t:
            out["published"] += 1
            m = _CH_NO_RE.search(t)
            if m:
                nos.add(int(m.group(1)))
        elif any(k in t for k in _STATUS_REVIEW):
            out["review"] += 1
    out["published_nos"] = sorted(nos)
    return out


def collect_manage_rows(page, mod):
    """读章节管理页的全部行：跨页翻页收齐（番茄表每页 15 行，只数当前页
    会把 30 章已发报成 15——2026-10-08 校准少计实案）。平台没配翻页 JS
    （七猫）或翻页无进展时按单页收。行文本去重保序，重复页不会翻倍。"""
    js_rows = mod.CONFIG.get("count_rows_js")
    next_js = mod.CONFIG.get("pager_next_js")
    settle = float(mod.CONFIG.get("count_page_settle") or 1.2)
    rows, seen = [], set()
    for _ in range(int(mod.CONFIG.get("count_max_pages") or 20)):
        fresh = 0
        for t in (page.call(js_rows) or []):
            t = str(t)
            if t and t not in seen:
                seen.add(t)
                rows.append(t)
                fresh += 1
        if not next_js or fresh == 0:
            break
        r = page.call(next_js)
        if not isinstance(r, dict) or r.get("done"):
            break
        time.sleep(settle)
    return rows


def collect_all_volumes(page, mod):
    """跨分卷收行：章节管理页带分卷筛选（番茄「第一卷：…」下拉，无「全部」
    项），列表只显示选中卷的章节——卷二发布后只数卷一会继续少计。弹层在
    click 后下一帧才渲染，open→读/点之间必须隔 sleep（同步 JS 一口气查必
    落空，2026-10-08 真机实案）。收完恢复进入时的卷；一卷都没选成时按原
    筛选兜底收一次，不让分卷自动化失败拖死整次校准。

    返回 (rows, vols)：vols=读到的平台分卷显示名清单（读不到为 None），
    供 books.json 缓存平台卷表。"""
    vj = mod.CONFIG.get("volume_js") or {}
    if not all(vj.get(k) for k in ("open", "options", "pick")):
        return collect_manage_rows(page, mod), None
    settle = float(mod.CONFIG.get("count_page_settle") or 1.2)

    def open_popup():
        try:
            page.call(vj["open"])
        except Exception:
            pass
        time.sleep(0.8)

    orig = ""
    try:
        orig = str(page.call(vj.get("current")) or "").strip()
    except Exception:
        pass
    open_popup()
    try:
        vols = page.call(vj["options"]) or []
    except Exception:
        vols = []
    if not isinstance(vols, list) or len(vols) < 2:
        return collect_manage_rows(page, mod), None   # 单卷：当前筛选就是全量
    rows, seen, picked_any, misses = [], set(), False, 0
    for v in vols:
        v = str(v or "").strip()
        if not v:
            continue
        open_popup()
        try:
            r = page.call(vj["pick"], v)
        except Exception:
            r = None
        if not isinstance(r, dict) or not r.get("ok"):
            misses += 1
            if misses >= 3:
                break                   # 弹层压根点不动：别把选项清单烧完
            continue
        misses = 0
        picked_any = True
        time.sleep(settle)
        for t in collect_manage_rows(page, mod):
            if t not in seen:
                seen.add(t)
                rows.append(t)
    if orig:                                    # 还原进入时的卷筛选
        open_popup()
        try:
            page.call(vj["pick"], orig)
        except Exception:
            pass
    if not picked_any:
        return collect_manage_rows(page, mod), list(vols)
    return rows, [str(v) for v in vols if str(v or "").strip()]
