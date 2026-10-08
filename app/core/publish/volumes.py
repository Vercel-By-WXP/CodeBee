# -*- coding: utf-8 -*-
"""分卷计划与平台分卷对齐（2026-10-08 批量自动发布·自动分卷）。

卷计划来源（前者优先）：
1. <workdir>/分卷.json（或 volumes.json）：{"volumes":[{"name":"县里有旧账",
   "from":49,"to":104}, ...]}——手工维护的权威表；
2. <workdir>/大纲/series-outline.md：`## 第一卷：卷名` 标题给顺序与卷名，
   正文里「八卷依次48、56、60…章」给各卷章数（章号全书连续，从头累加）。

平台侧卷以显示名带前缀（「第二卷：县里有旧账」）；匹配一律走 norm_volume_name
去前缀比较，平台是第几卷由平台自己定。没有计划（解析不出/文件不在）= 功能
关闭，发章保持平台默认卷——绝不瞎猜卷名。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 平台卷显示名前缀：第一卷： / 第12卷:
_PREFIX_RE = re.compile(
    r"^第\s*[0-9０-９一二三四五六七八九十百千零两]+\s*卷\s*[：:]\s*")
_HEAD_RE = re.compile(
    r"^##\s*第\s*([0-9０-９一二三四五六七八九十百千零两]+)\s*卷\s*[：:]\s*(.+?)\s*$",
    re.M)
# 「八卷依次48、56、60、64、64、64、64、60章」
_COUNTS_RE = re.compile(r"依次\s*([0-9０-９]+(?:[、，,]\s*[0-9０-９]+)*)\s*章")


def norm_volume_name(s):
    """「第二卷：县里有旧账」→「县里有旧账」（本地计划名与平台显示名对齐）。"""
    return _PREFIX_RE.sub("", str(s or "").strip()).strip()


def load_plan(workdir):
    """工作目录 → 卷计划 [{no, name, from, to}]（from/to 为全书连续章号，
    0 表示不设界）。解析不出返回 []，调用方按「无计划」处理。"""
    wd = Path(workdir or "")
    if not wd.is_dir():
        return []
    for name in ("分卷.json", "volumes.json"):
        fp = wd / name
        if not fp.is_file():
            continue
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        vols = d.get("volumes") if isinstance(d, dict) else d
        out = []
        if isinstance(vols, list):
            for k, v in enumerate(vols, 1):
                if isinstance(v, dict) and str(v.get("name") or "").strip():
                    out.append({"no": int(v.get("no") or k),
                                "name": str(v["name"]).strip(),
                                "from": int(v.get("from") or 0),
                                "to": int(v.get("to") or 0)})
        if out:
            return out
    return _parse_outline(wd / "大纲" / "series-outline.md")


def _parse_outline(fp):
    """series-outline.md → 卷计划。标题给卷名，章数行给范围；章数缺失或与
    标题数对不上时宁可放弃（不猜）。"""
    try:
        text = Path(fp).read_text(encoding="utf-8")
    except Exception:
        return []
    heads = _HEAD_RE.findall(text)
    if not heads:
        return []
    m = _COUNTS_RE.search(text)
    if not m:
        return []
    try:
        counts = [int(x) for x in re.split(r"[、，,]", m.group(1).strip())]
    except ValueError:
        return []
    if len(counts) != len(heads) or any(c <= 0 for c in counts):
        return []
    out, start = [], 1
    for k, ((_cn, name), cnt) in enumerate(zip(heads, counts), 1):
        out.append({"no": k,                  # 卷号以顺序为准，标题汉字号仅展示
                    "name": name.strip(),
                    "from": start,
                    "to": start + cnt - 1})
        start += cnt
    return out


def volume_for(ch_no, plan):
    """章号 → 卷条目（from<=ch<=to；0 边界=不设界）。无匹配返回 None。"""
    if ch_no <= 0:
        return None
    for v in plan or []:
        lo, hi = int(v.get("from") or 0), int(v.get("to") or 0)
        if (lo and ch_no < lo) or (hi and ch_no > hi):
            continue
        if lo or hi:
            return v
    return None


def name_for(workdir, ch_no):
    """章号 → 目标卷名（裸名，如「县里有旧账」）；无计划/无匹配返回空串。"""
    v = volume_for(ch_no, load_plan(workdir))
    return str(v.get("name") or "").strip() if v else ""
