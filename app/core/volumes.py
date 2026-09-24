# -*- coding: utf-8 -*-
"""分卷（网文卷结构）纯函数：把「用户给的卷表」或「每卷章数」解析成一张
确定性的卷规划表（plan），再据此回答「某章属于哪一卷、是不是卷末章、本批
覆盖哪几卷」。

为什么卷边界必须确定且可复现：本书是分批写成的（每批 1-20 章，续写批次
共用同一工作目录、按全书章号衔接）。若让模型每批自由切卷，同一卷会在下一
批被重新划分、甚至被劈成两半。本模块把卷归属变成章号的纯函数——同一份
卷表/每卷章数下，任何一批、任何一次续写，卷归属完全一致。模型只负责卷名
与卷弧光（用户已给卷名时原样沿用）。

两种来源（优先级从高到低）：
1. 显式卷表 spec —— 新建任务时用户明确给出的分卷信息（表单字段或目标/
   上下文文字里的「第一卷 少年初入江湖 第1-20章」式描述）。
2. 每卷章数 per —— 没给显式卷表时，按固定章数等长切卷。

两者都没有 → 空 plan = 不分卷（向后兼容：旧任务 serial 里没有这两个字段，
行为与分卷前完全一致）。

纯函数、零依赖，故不参与分层（未分层模块按最高层处理，只被禁不禁人）。
"""
from __future__ import annotations

import re

MAX_PER = 200     # 每卷章数上限：再大就不是分卷而是「一卷到底」
MIN_PER = 2       # 每卷至少 2 章
MAX_VOLS = 200    # 卷数上限：防模型/用户给出病态长表
MAX_TITLE = 60


def norm_per(value):
    """规范化每卷章数：非数字/<2/非法 → 0（不分卷）；否则夹到 [2, 200]。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    if n < MIN_PER:
        return 0
    return min(MAX_PER, n)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_RANGE_RE = re.compile(r"(\d{1,5})\s*[-~—－到至]\s*(\d{1,5})")
_COUNT_RE = re.compile(r"(\d{1,5})\s*章")
# 章号表达式的完整形态：第1-20章 / 第1章 / 1-20章 / 20章（用于从卷名里剔除）
_CH_EXPR_RE = re.compile(
    r"第\s*\d{1,5}\s*[-~—－到至]\s*\d{1,5}\s*章?"
    r"|第\s*\d{1,5}\s*章"
    r"|\d{1,5}\s*[-~—－到至]\s*\d{1,5}\s*章"
    r"|\d{1,5}\s*章")
# 开放卷：「第21章起 / 21章开始 / 21章以后」= 该卷从 21 章铺到书末
_OPEN_RE = re.compile(r"第?\s*(\d{1,5})\s*章\s*(?:起|开始|以上|以后|往后|及以后)")
_VOL_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零两]+)\s*(?:卷|部|篇)"
                     r"|(?:^|[\s。；;，,：:、])(?:卷|部|篇)\s*([0-9一二三四五六七八九十百零两]+)")
_BRACKETS_RE = re.compile(r"[《》〈〉「」『』\[\]【】()（）\"'“”‘’]")
_TITLE_TRIM = " \t:：,，、。；;!！?？-—~～·/"
# 开放卷的残词：「21章起」被剔除章号后只剩「起」，整词丢弃；卷名里的「起」
# （如「风云再起」）因是词内字符不受影响
_OPEN_WORD_RE = re.compile(r"(?:^|\s)(?:起|开始|以上|以后|往后|及以后)(?=\s|$)")

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}


def _cn2int(text):
    """中文数字 → int（支持「一」「十二」「二十三」「一百」这类常见写法）；
    失败返回 None。仅用于识别卷号，识别不出也不影响正文章节内容。"""
    text = str(text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, section, seen = 0, 0, False
    for ch in text:
        if ch not in _CN_NUM:
            return None
        seen = True
        val = _CN_NUM[ch]
        if val == 10:
            section = (section or 1) * 10
        elif val == 100:
            total += (section or 1) * 100
            section = 0
        else:
            section += val
    if not seen:
        return None
    return total + section


def norm_spec(raw):
    """规范化显式卷表 → [{"title": str, "start": int|None, "end": int|None,
    "chapters": int|None}]。

    接受多种形态（模型/前端/用户手写各有习惯）：
      [{"title": "卷一 少年初入江湖", "chapters": 20}, ...]
      [{"title": "...", "start": 1, "end": 20}, ...]
      [{"title": "...", "range": "1-20"}, ...]
      [{"title": "...", "count": 20}, ...]
      ["卷一 少年初入江湖", ...]（纯字符串：只有卷名，边界靠 per 推）
    无 title 也无边界的条目丢弃；整体不可用返回 []。"""
    if isinstance(raw, dict):
        raw = raw.get("volumes") if isinstance(raw.get("volumes"), list) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_VOLS]:
        if isinstance(item, str):
            title = item.strip()[:MAX_TITLE]
            if title:
                out.append({"title": title, "start": None, "end": None,
                            "chapters": None})
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()[:MAX_TITLE]
        start = _to_int(item.get("start") or item.get("from"))
        end = _to_int(item.get("end") or item.get("to"))
        rng = item.get("range")
        if isinstance(rng, str):
            m = _RANGE_RE.search(rng)
            if m:
                start, end = _to_int(m.group(1)), _to_int(m.group(2))
        chapters = _to_int(item.get("chapters") or item.get("count")
                           or item.get("n"))
        if chapters is not None and chapters < 1:
            chapters = None
        if start is not None and start < 1:
            start = None
        if end is not None and (end < 1 or (start is not None and end < start)):
            end = None
        if start is not None and end is None and chapters:
            end = start + chapters - 1
        if start is None and end is not None:
            start = 1
        if not (title or start or chapters):
            continue
        out.append({"title": title, "start": start, "end": end,
                    "chapters": chapters})
    return out


def _clean_title(seg, m):
    """从卷段里抠出卷名：优先取卷标记之后的文字（「第一卷 少年初入江湖 第1-20章」
    → 少年初入江湖），剔除章号表达式、书名号与修饰标点；标记后为空才回退到
    标记之前（「少年初入江湖 第一卷 1-20章」这类倒装写法）。前后都空返回 ""，
    交给模型/大纲补卷名。"""
    def _tidy(s):
        s = _BRACKETS_RE.sub(" ", s)
        s = _CH_EXPR_RE.sub(" ", s)
        s = _OPEN_WORD_RE.sub(" ", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip(_TITLE_TRIM)
    after = _tidy(seg[m.end():])
    if after:
        return after[:MAX_TITLE]
    return _tidy(seg[:m.start()])[:MAX_TITLE]


def parse_spec_text(text):
    """从目标/上下文文字里识别显式分卷信息（新建任务时用户在描述里直接写
    「第一卷 少年初入江湖 第1-20章；卷二 风云再起 21-40章」）。

    保守策略：必须出现 ≥1 个卷标记（第X卷/卷X/第X部/第X篇）才认为用户在
    分卷；且**每个卷段都必须带边界**（章号范围 / 章数 / 「N章起」开放标记）。
    缺边界的段一律判为「不是分卷信息」——「第一卷要吸引人」这类句子里的
    「第一卷」是修辞不是结构，绝不能据此把全书切成一个卷。正常写法都带边界
    （「第一卷 少年初入江湖 20章」「第1-20章」「21章起」），因此不受影响。

    切段方式：按**卷标记出现位置**切（而非按行/分号），这样「卷一 甲 20章，
    卷二 乙 25章」这种同一行多卷也能正确拆开。"""
    text = str(text or "")
    if not text.strip():
        return []
    marks = list(_VOL_RE.finditer(text))
    if not marks:
        return []
    # 同一卷号被重复提及时只认首次出现；能解析出卷号且全为升序时按卷号排序
    segs, seen = [], set()
    for i, m in enumerate(marks):
        num = _cn2int(m.group(1) or m.group(2))
        key = num if num else len(segs) + 1
        if key in seen:
            continue
        seen.add(key)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        segs.append((text[m.start():end], key))
    out = []
    for seg, _num in segs:
        m = _VOL_RE.match(seg)     # seg 以卷标记起头，相对位置 0
        if not m:
            continue
        body = seg[m.end():]
        op = _OPEN_RE.search(body) or _OPEN_RE.search(seg[:m.start()])
        rng = _RANGE_RE.search(body)
        if op:
            start, end = _to_int(op.group(1)), None      # 开放卷：铺到书末
            chapters = None
        elif rng:
            start, end = _to_int(rng.group(1)), _to_int(rng.group(2))
            chapters = (end - start + 1) if (start and end and end >= start) else None
        else:
            m2 = _COUNT_RE.search(body) or _COUNT_RE.search(seg[:m.start()])
            chapters = _to_int(m2.group(1)) if m2 else None
            start, end = None, None
        if not (chapters or (start and end) or (op and start)):
            # 该卷段没有可用的边界：整段判为不是分卷信息（见 docstring）
            return []
        out.append({"title": _clean_title(seg, m), "start": start,
                    "end": end, "chapters": chapters})
    return out if out else []


def build_plan(spec, per, upto=0):
    """卷规划表：按顺序铺开各卷的章号区间，返回
    [{"vol": 1, "title": str, "first": 1, "last": 20}, ...]（last=None 表示开放
    到书末）。

    spec 优先（显式卷表）；spec 用尽后若 per>0 继续按 per 等长续卷，否则把最后
    一卷视为开放卷（不再新开卷）。upto>0 时保证表至少覆盖到第 upto 章（续写批次
    需要的章号范围），不足则按 per（无 per 用末卷长度）继续补卷。
    不分卷返回 []。"""
    per = norm_per(per)
    spec = norm_spec(spec)
    if not spec and not per:
        return []
    plan, prev_last = [], 0
    def _push(title, first, last):
        plan.append({"vol": len(plan) + 1, "title": title or "",
                     "first": first, "last": last})
    for item in spec:
        if len(plan) >= MAX_VOLS:
            break
        if item.get("start"):
            first = item["start"]
        else:
            first = prev_last + 1
        if item.get("end"):
            last = item["end"]
        elif item.get("chapters"):
            last = first + item["chapters"] - 1
        else:
            last = None
        if last is not None and last < first:
            last = first
        _push(item.get("title"), first, last)
        prev_last = last if last is not None else first
        if last is None:
            # 开放卷：后面不再有卷（用户没给结尾），直接收工
            return plan
    # spec 之外的续卷：per 优先，否则沿用末卷长度（可预测，不会突然变成巨卷）
    if plan:
        fallback = per or (plan[-1]["last"] - plan[-1]["first"] + 1
                           if plan[-1]["last"] is not None else 0)
    else:
        fallback = per
    if fallback and len(plan) < MAX_VOLS:
        cursor = prev_last + 1
        while (upto and cursor <= upto) or (not upto and not plan):
            if len(plan) >= MAX_VOLS:
                break
            _push("", cursor, cursor + fallback - 1)
            cursor += fallback
        # upto 已知时，保证覆盖到 upto；未知时至少一卷
        if upto and plan and plan[-1]["last"] is not None and plan[-1]["last"] < upto:
            _push("", cursor, cursor + fallback - 1)
    return plan


def find(plan, chapter):
    """章号 → 所在卷条目（dict）；不在任何卷内返回 None。"""
    c = _to_int(chapter)
    if c is None or c < 1 or not plan:
        return None
    for ent in plan:
        last = ent.get("last")
        if ent["first"] <= c and (last is None or c <= last):
            return ent
    return None


def plan_volumes(plan, start, n):
    """本批 [start, start+n-1] 覆盖到的卷条目（升序去重）；不分卷返回 []。"""
    s, n = _to_int(start), _to_int(n)
    if s is None or n is None or n <= 0 or not plan:
        return []
    out, seen = [], set()
    for i in range(s, s + n):
        ent = find(plan, i)
        if ent and ent["vol"] not in seen:
            seen.add(ent["vol"])
            out.append(ent)
    return out


def position(plan, chapter):
    """章在本卷内的位置：(卷内第几章, 本卷共几章)。末卷开放时总数返回 0
    （未知）。不分卷返回 (0, 0)。"""
    ent = find(plan, chapter)
    if not ent:
        return (0, 0)
    total = (ent["last"] - ent["first"] + 1) if ent["last"] is not None else 0
    return (_to_int(chapter) - ent["first"] + 1, total)


def is_vol_end(plan, chapter):
    """本章是否为卷末章（卷末要有本卷大高潮与卷末钩子）。开放末卷恒为 False。"""
    ent = find(plan, chapter)
    if not ent or ent["last"] is None:
        return False
    return _to_int(chapter) == ent["last"]


def fmt_plan(plan, upto=None):
    """卷规划表 → 提示词/日志可读文本。upto 限定只列到该章为止的卷。"""
    if not plan:
        return ""
    lines = []
    for ent in plan:
        if upto and ent["first"] > upto:
            break
        last = ent["last"]
        rng = ("第 %d 章起（开放至书末）" % ent["first"]) if last is None \
            else ("第 %d–%d 章" % (ent["first"], last))
        title = ("《%s》" % ent["title"]) if ent.get("title") else "（未命名）"
        lines.append("第 %d 卷 %s：%s" % (ent["vol"], title, rng))
    return "\n".join(lines)


def merge_titles(plan, named):
    """把模型/大纲给出的卷名合并进规划表（named 形如 {"1": {"title","arc"}} 或
    {"1": "卷名"}）。用户显式给的卷名优先，不被模型覆盖。返回新表。"""
    if not isinstance(named, dict) or not plan:
        return plan
    out = []
    for ent in plan:
        e = dict(ent)
        item = named.get(str(e["vol"])) if str(e["vol"]) in named else named.get(e["vol"])
        if isinstance(item, str):
            item = {"title": item}
        if isinstance(item, dict):
            if not e.get("title") and str(item.get("title") or "").strip():
                e["title"] = str(item["title"]).strip()[:MAX_TITLE]
            arc = str(item.get("arc") or "").strip()
            if arc:
                e["arc"] = arc[:400]
        out.append(e)
    return out


def norm_volumes(raw, per, start, n, plan=None):
    """收大纲 JSON 里模型给的卷名/卷弧光，只留本批章号真正覆盖到的卷
    （模型可能多写、乱写卷号，一律按卷规划表过滤；卷边界不采信模型）。

    raw 形如 {"1": {"title","arc"}} 或 {"1": "卷名"}（键为卷号字符串）。
    卷边界优先用调用方现成的 plan（显式卷表时与主链推导完全一致），
    缺省按 per 等长推导。返回 {卷号str: {"title":..., "arc":...}}；
    raw 不合规或本批卷号一个都没命中返回 None（调用方用既有卷名兜底）。"""
    if not isinstance(raw, dict):
        return None
    s, cnt = _to_int(start), _to_int(n)
    if s is None or cnt is None or cnt <= 0:
        return None
    if plan is None:
        plan = build_plan(None, per, upto=s + cnt - 1)
    hits = {e["vol"] for e in plan_volumes(plan, s, cnt)}
    if not hits:
        return None
    out = {}
    for key, item in raw.items():
        vol = _to_int(str(key).strip())
        if vol is None or vol not in hits:
            continue
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:MAX_TITLE]
        arc = str(item.get("arc") or "").strip()[:400]
        if title or arc:
            out[str(vol)] = {"title": title, "arc": arc}
    return out or None
