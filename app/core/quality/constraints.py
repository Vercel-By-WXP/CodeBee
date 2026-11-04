# -*- coding: utf-8 -*-
"""驳回约束：把平台驳回原文 + 真稿对读的结论变成下次开工的硬约束（2026-11-04 P0-C）。

为什么不塞进 `publish/outcome.py`
--------------------------------
那个台账记的是**平台事实**（谁、哪本、什么结果、平台原话），而且 `PLATFORMS` 写死
番茄 / 七猫。实案《一头猎物，三笔账》是被**纵横中文网**驳回的——第三个平台，
`record()` 对它会直接 `ValueError`。硬塞进去只会污染数据模型，还会让「有驳回记录」
变成一件看运气的事。

而约束本身是**题材级**的：`年代打猎文` 的开篇节奏要求搬到鉴宝文上就是错的。
所以落 `data/novel/constraints-<genre>.json`，与平台解耦，靠题材匹配。

三条纪律（改这个文件前先读）
--------------------------
1. **平台事实与人工判断分栏**（决策 11）。`source.quote` 是平台原话，原样引用；
   `constraints[].rule` 是人工从原文与真稿对读得出的判断，块头必须写明
   「不是平台规则」，否则模型会把作者的自省当成平台禁令，越写越窄。
2. **匹配不到题材就不注入**。宁可这一轮没有约束，也不能把打猎文的要求发给鉴宝文。
3. **没有文件就不注入空段**。不许出现「（无）」式占位（P0-C 验收原文）。
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

MAX_BLOCK_CHARS = 1100
MAX_CONSTRAINTS = 6
MAX_RULE_CHARS = 240
# 题材投票的最低命中数：一个词不算（一份鉴宝文的简介里也可能出现一次「林场」）。
MIN_DETECT_HITS = 2

_HEADER = ("### 【驳回约束（人工判断：来自平台驳回原文与真稿对读，**不是**平台规则，"
           "也不外推到其他题材或其他平台）】")


def _dir():
    from .. import paths
    return Path(paths.DATA_DIR) / "novel"


def _clean(value, limit=0):
    """平台原文是不可信输入：转义标记 + 压掉控制字符 + 截断（与 outcome._clean 同纪律）。"""
    text = str(value or "").strip()
    if limit:
        text = text[:limit]
    text = html.escape(text, quote=True)
    return re.sub(r"[\x00-\x1f\x7f]",
                  lambda m: "\\u%04x" % ord(m.group(0)), text)


def _load_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not str(data.get("genre") or "").strip():
        return None
    if not isinstance(data.get("constraints"), list) or not data["constraints"]:
        return None
    return data


def all_profiles():
    """`data/novel/constraints-*.json` 全部可用约束（坏文件跳过，不抛）。"""
    root = _dir()
    try:
        files = sorted(root.glob("constraints-*.json")) if root.is_dir() else []
    except OSError:
        return []
    out = []
    for path in files:
        data = _load_file(path)
        if data:
            out.append(data)
    return out


def load(genre):
    """按 id 或中文名取一份约束；取不到返回 None。"""
    key = str(genre or "").strip()
    if not key:
        return None
    for data in all_profiles():
        if key in (str(data.get("genre") or ""), str(data.get("genre_name") or "")):
            return data
    return None


def detect(text):
    """按 `detect` 词在任务描述上投票猜题材；不够明显返回 None。"""
    sample = str(text or "")
    if not sample.strip():
        return None
    best, best_hits = None, 0
    for data in all_profiles():
        words = data.get("detect") or ()
        hits = sum(1 for word in words if word and word in sample)
        if hits > best_hits:
            best, best_hits = data, hits
    if best is not None and best_hits >= MIN_DETECT_HITS:
        return str(best.get("genre"))
    return None


def resolve(task):
    """给任务找到适用的约束：显式字段 → 任务描述里的词 → 与判据同一份题材识别。

    第三档是 2026-11-04 补的（P2-D）：前两档只看标题 / 目标 / 背景，而作者在界面上
    新建一本打猎文时，标题里常常一个词表词都没有（《一头猎物，三笔账》只命中
    「猎物」，词表收的是「猎获」），目标里写「年代打猎文」也才 1 命中、够不到
    2 票的门槛。于是判据按正文认出了题材、开工约束却没下发——同一份驳回教训
    只走了一半，作者照着改也只改到一半。兜底去问 `appeal.genre_of()`：
    两处认出同一个题材比两处各认各的更重要。

    认不出仍然返回 None：绝不把别的题材的约束套到这本书上。**显式点名了题材
    却认不出时也不兜底正文**——`appeal_genre` 是「这本书是什么题材」的明示，
    与 `genre`（平台/产品分类，可能是「都市」「男频」这类画像之外的词）不是一回事。
    同一本被判「unknown_genre」的书，不该在提示词里收到打猎约束。
    """
    task = task if isinstance(task, dict) else {}
    explicit = str(task.get("appeal_genre") or "").strip()
    if explicit:
        return load(explicit)
    for key in ("genre", "类型", "题材"):
        got = load(task.get(key))
        if got:
            return got
    text = " ".join(str(task.get(k) or "") for k in ("title", "goal", "context"))
    genre = detect(text)
    if genre:
        return load(genre)
    try:
        from . import appeal as quality_appeal
        return load(quality_appeal.genre_of(task))
    except Exception:  # noqa: BLE001 - 识别失败只损失这一块，不能拖垮提示词拼装
        return None


def _source_line(data):
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    platform = str(source.get("platform_label") or source.get("platform") or "").strip()
    stage = str(source.get("stage_label") or "").strip()
    at = str(source.get("at") or "").strip()[:20]
    work = str(source.get("work") or "").strip()
    quote = _clean(source.get("quote"), 200)

    bits = [x for x in (platform, stage, at) if x]
    # 用中点而不是逗号：stage_label 自带括号（「签约评估未通过（不可再次申请）」），
    # 逗号拼会套成「（平台，未通过（…），日期）」两层括号，读起来像数据错误。
    line = "平台原话（%s）" % " · ".join(bits) if bits else "平台原话"
    if quote:
        line += "：「%s」" % quote
    if work:
        line += "\n来源作品：《%s》" % _clean(work, 40)
    evidence = str(source.get("evidence") or "").strip()
    if evidence:
        line += "（%s）" % _clean(evidence, 160)
    return line


def block_for(task):
    """给提示词用的「驳回约束」块；不适用时返回空串（绝不返回占位段）。"""
    if str((task or {}).get("type") or "") not in ("novel", "serial_novel"):
        return ""
    try:
        data = resolve(task)
    except Exception:  # noqa: BLE001 - 台账坏了只损失这一块，不能拖垮提示词拼装
        return ""
    if not data:
        return ""
    lines = [_HEADER, _source_line(data)]
    count = 0
    for item in data.get("constraints") or []:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "").strip()
        if not rule:
            continue
        label = str(item.get("kind") or "约束").strip()[:8]
        fact = str(item.get("fact") or "").strip()
        one = "%d. [%s] %s" % (count + 1, label, _clean(rule, MAX_RULE_CHARS))
        if fact:
            one += "\n   依据：%s" % _clean(fact, 120)
        rendered = "\n".join(lines)
        if len(rendered) + len(one) + 40 > MAX_BLOCK_CHARS:
            break
        lines.append(one)
        count += 1
        if count >= MAX_CONSTRAINTS:
            break
    if not count:
        return ""
    lines.append("（以上是给这本书的开工约束。它们来自一次真实的签约驳回，"
                 "用于自查同类问题；不是平台规则，也不代表这样写就能过签。）")
    text = "\n".join(lines)
    return text if len(text) <= MAX_BLOCK_CHARS else text[:MAX_BLOCK_CHARS] + "\n…（已截断）"
