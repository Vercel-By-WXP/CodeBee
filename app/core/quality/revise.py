# -*- coding: utf-8 -*-
"""改稿单（revision brief），2026-11-04 P1-A。

吸引力门（`appeal.py`）回答的是「这一章有没有兑现约定」——一个能被事实反驳的
是非题。但它拦下来之后，作者拿到的只有一句「第 1 章没有兑现任何获得」，接下来
仍要自己翻回原稿、找出哪一段能改。这正是《一头猎物，三笔账》被拒后作者花几天
人工做的事：读五章、逐段找「差一个动作」的地方、写成调整方向。

本模块把那一步也机器化：**把 blocker 翻成一份带锚点的改稿单**。

* 锚点精确到「第几章第几段第几句 + 原文摘录」，作者能直接跳过去改；
* 分类说清「差什么」：只提了东西没有动作 / 动作被否定 / 写成未来语气；
* 纯规则、离线、**零额度**：不调模型，几百毫秒出结果；
* 没有锚点就说没有——**宁可只报章级事实，也不编一个具体段落**。

三条纪律（改这个文件前先读）：

1. **只陈述事实，不给「写得好不好」**。可以说「P49 提到了野猪，同句没有放倒
   或抬走」，不能说「这一段缺乏张力」。判据能证伪的才写。
2. **锚点必须可核验**：每一条都带原句摘录与段号句号，作者一眼能对上原文。
   段落编号与 `appeal.paragraphs()` 同口径（空行分隔的块）。
3. **不代替人改**。改稿单是给人（或写手 prompt）的输入，不自动重写章节；
   是否照改、改到什么程度仍由作者决定。
"""
from __future__ import annotations

from . import appeal

# 每章最多列几条锚点。列 20 条等于没列——作者只需要「最省力的那一两处」。
MAX_ANCHORS = 4
# 单条原文摘录上限。够看出是哪句，又不至于把整段搬进提示词。
MAX_QUOTE = 80
# 注入提示词的整块上限（与 constraints 同级；两者一起别把经验库挤爆）。
MAX_BLOCK = 1400

# 「未兑现词」必须**管得到这句里的东西**才算数。
# 真稿实测反例：第 2 章 P1「回到柳河屯时，天还没黑，石头的脚踝已经肿得塞不进
# 棉鞋」——「还没」管的是「天黑」，离「棉鞋」隔了二十多字。不设距离，改稿单就
# 会让作者去把「脚肿了」改成兑现，这种建议比没有建议更糟。
_MARKER_WINDOW = 12

# 两类未兑现词要区别对待：
#   STRONG（拟/打算/待明日/再算…）说的是「这件事还没做」，整句都是计划 →
#          不管隔多远都算未来语气；
#   WEAK（若/要是/如果/还没…）多是条件从句或状态描述 → 只有贴着东西、或这句
#          里本来就写了动作时才算。
# 真稿实测：第 2 章 P12「今天买了纱布」是**过去时**，原先因为「买了」被当成
# 动作就当成了未来语气——那句只是「纱布」不在词表里，判据没覆盖到，不是写法问题。
_STRONG_MARKERS = ("拟", "打算", "计划", "准备", "待确认", "待明日", "待定",
                   "下章", "下一章", "明天再", "下次再", "再决定", "再算",
                   "再谈", "再说", "再分", "再议")
_WEAK_MARKERS = ("将要", "即将", "如果", "假如", "要是", "若", "还没", "尚未",
                 "未能", "不许", "不准")

_MARK_ORDER = {"negated": 0, "pending": 1, "bare": 2}
_MARK_TEXT = {
    "negated": "动作被否定",
    "pending": "写成未来语气",
    "bare": "只提到东西，没有兑现动作",
}


def _classify(sentence):
    """判断一句「差什么才兑现」，返回 (mark, thing, action) 或 None。

    `appeal._find_gain` 已经兑现的句子不进来（那些是证据，不是待改）。
    返回的每个 mark 都要能被原句直接证伪，宁可少报不硬凑。
    """
    if appeal._find_gain(sentence):
        return None
    things, negated, plain = [], [], []
    for gain_id, rule in appeal._GAIN_RULES.items():
        hit_thing = ""
        for thing in rule["things"]:
            index = sentence.find(thing)
            if index >= 0 and (not hit_thing or index < sentence.find(hit_thing)):
                hit_thing = thing
        if hit_thing:
            things.append((gain_id, hit_thing))
    if not things:
        return None
    for gain_id, rule in appeal._GAIN_RULES.items():
        for action in rule["actions"]:
            index = sentence.find(action)
            if index < 0:
                continue
            if appeal._negated(sentence, index):
                negated.append((gain_id, action))
            else:
                plain.append((gain_id, action))
            break
    if negated:
        return "negated", things[0], negated[0]
    marker = appeal._UNREALIZED.search(sentence)
    if marker:
        strong = any(word in sentence for word in _STRONG_MARKERS)
        gap = sentence.find(things[0][1]) - marker.start()
        if strong or plain or 0 <= gap <= _MARKER_WINDOW:
            return "pending", things[0], (plain[0] if plain else None)
    return "bare", things[0], None


def anchors_of(chapter_text, limit=MAX_ANCHORS):
    """逐段逐句找「离兑现最近的几处」，返回 (锚点列表, 提到东西的句子总数)。

    总数单独回，是因为「全章提了 14 次野猪、一次都没落地」本身就是要给作者看
    的事实——只列 4 条锚点会让这件事看起来像「只有 4 处可改」。
    """
    rows = []
    for p_index, paragraph in enumerate(appeal.paragraphs(chapter_text), 1):
        for s_index, sentence in enumerate(appeal.sentences(paragraph), 1):
            hit = _classify(sentence)
            if not hit:
                continue
            marker, (gain_id, thing), action_pair = hit
            rows.append({
                "paragraph": p_index,
                "sentence": s_index,
                "mark": marker,
                "mark_text": _MARK_TEXT[marker],
                "gain": gain_id,
                "gain_label": appeal._GAIN_RULES[gain_id]["label"],
                "thing": thing,
                "action": action_pair[1] if action_pair else "",
                "text": sentence[:MAX_QUOTE],
            })
    total = len(rows)
    rows.sort(key=lambda row: (_MARK_ORDER.get(row["mark"], 9),
                               row["paragraph"], row["sentence"]))
    # 有「一句话就能改」的锚点（否定/未来语气）时只列它们：这时再把
    # 「只提到东西」的对话铺陈摆出来，作者会以为那也是待改点。
    # 一处都没有时才退回到 bare，且只列 2 条——列多了就是让人重写全章。
    cheap = [row for row in rows if row["mark"] != "bare"]
    if cheap:
        picked = cheap[:limit] if limit else cheap
    else:
        picked = rows[:min(2, limit or 2)]
    return picked, total


def _closing(chapter_text):
    """章末是什么状态：还在「未兑现」语气里的话值得单独点出来。

    读者划走的判断发生在章末——「下章再算」这种收尾把兑现推到下一章，
    正是《一头猎物》前两章的做法。
    """
    paragraphs = appeal.paragraphs(chapter_text)
    if not paragraphs:
        return {}
    last = paragraphs[-1]
    tail = appeal.sentences(last)[-1] if appeal.sentences(last) else ""
    marker = appeal._UNREALIZED.search(tail) if tail else None
    return {"paragraph": len(paragraphs), "text": tail[:MAX_QUOTE],
            "pending": bool(marker), "marker": marker.group(0) if marker else ""}


def _advice(chapter, min_words, gate):
    """按事实给改法方向：只说「往哪个方向补」，不替作者定句子。"""
    out = []
    if not chapter.get("located"):
        # 没有正文就不给具体改法：给出来的只会是猜的。
        out.append("没有读到这一章正文，未做锚点定位——按上面章级判据判断即可。")
        return out
    anchors = chapter.get("anchors") or []
    bare = [a for a in anchors if a["mark"] == "bare"]
    cheap = [a for a in anchors if a["mark"] in ("negated", "pending")]
    if cheap:
        first = cheap[0]
        if first["mark"] == "negated":
            out.append("最省力的一处：P%d 的「%s」把动作否掉了——改成章内已经发生"
                       "（%s）。" % (first["paragraph"], first["text"], first["gain_label"]))
        else:
            out.append("最省力的一处：P%d 的「%s」写的是计划而不是结果——把这件事"
                       "挪到本章内做完（%s）。" % (first["paragraph"], first["text"],
                                                 first["gain_label"]))
    elif bare:
        first = bare[0]
        out.append(
            "最近的一处：P%d 提到了「%s」，同句没有兑现动作——就地补一次"
            "（放倒 / 抬走 / 换到手 / 买回家），让它在这一章落地。"
            % (first["paragraph"], first["thing"]))
    else:
        out.append(
            "全章没有一处围绕「获得」写——不是缺一个动作，是这一章没写这件事；"
            "要么把上一章或下一章的兑现挪进来，要么把这一章并进相邻章。")
    closing = chapter.get("closing") or {}
    if closing.get("pending"):
        out.append("章末 P%d 仍停在「%s」上——把兑现挪到这一章之内，别留到下一章。"
                   % (closing.get("paragraph") or 0, closing.get("marker") or "未兑现"))
    if min_words and chapter.get("words", 0) < min_words:
        out.append("本章 %d 字，低于 %d 字：补的篇幅要落在兑现上，别只加过渡。"
                   % (chapter.get("words", 0), min_words))
    if gate:
        out.append("这一章在前三章门禁内：过签评估看的就是开头几章，单章空窗直接拦。")
    return out


def _chapter_texts(task):
    """按章号读正文：`chapter-01.md` 起，读不到就空。

    注意 `appeal.analyze()` 的结果里**不带正文**（它只回证据与判据，正文
    一旦塞进返回值就会顺着闸门 decision 进 API 响应）。锚点定位需要原文，
    所以由这里按章号现读——读法与 `appeal.check_task()` 完全一致。
    """
    from pathlib import Path
    root = Path(str((task or {}).get("workdir") or ""))
    if not str((task or {}).get("workdir") or "").strip() or not root.is_dir():
        return {}
    out = {}
    for path in sorted(root.glob("chapter-*.md")):
        try:
            number = int(path.stem.split("-")[-1])
        except (ValueError, IndexError):
            continue
        try:
            out[number] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


def build(result, task=None):
    """把 `appeal.analyze()` 的结果翻成改稿单结构；没有 blocker 返回空单。

    ``task`` 用来取正文做锚点定位。**没有正文时不猜**：该章 `located=False`，
    渲染时只说章级判据，绝不写「全章没有一处提到东西」——那是需要通读全文
    才能下的结论，不能拿「我手上没文本」冒充。
    """
    result = result if isinstance(result, dict) else {}
    chapters = [c for c in (result.get("chapters") or []) if isinstance(c, dict)]
    if not result.get("blockers") or not chapters:
        return {"enabled": False, "reason": "no_blocker", "chapters": []}
    texts = _chapter_texts(task)
    profile = appeal.profile_for(result.get("genre")) or {}
    min_words = appeal._positive_int(profile.get("min_words"), appeal.DEFAULT_MIN_WORDS)
    gate_count = appeal._positive_int(profile.get("chapters"), appeal.DEFAULT_CHAPTERS)
    want = []
    for chapter in chapters:
        # 只给**被拦**的章出锚点：没被拦的章给建议等于让作者重写已经过关的东西。
        if not chapter.get("blockers"):
            continue
        number = chapter.get("chapter") or 0
        body = str(chapter.get("text") or texts.get(number) or "")
        located = bool(body.strip())
        anchor_rows, anchor_total = anchors_of(body) if located else ([], 0)
        want.append({
            "chapter": number,
            "title": str(chapter.get("title") or ""),
            "words": chapter.get("words") or 0,
            "paragraphs": len(appeal.paragraphs(body)),
            "located": located,
            "blockers": list(chapter.get("blockers") or []),
            "anchors": anchor_rows,
            "anchor_total": anchor_total,
            "closing": _closing(body) if located else {},
            "gate": number <= gate_count,
        })
    for chapter in want:
        chapter["advice"] = _advice(chapter, min_words, chapter["gate"])
    return {"enabled": True, "genre": str(result.get("genre") or ""),
            "genre_name": str(profile.get("name") or ""),
            "chapters": want, "blockers": list(result.get("blockers") or [])}


def headline(brief):
    """一句话结论：拿去当弹窗标题/提示首行。"""
    chapters = brief.get("chapters") or []
    if not chapters:
        return ""
    numbers = "、".join(str(c["chapter"]) for c in chapters)
    return "第 %s 章整章没有兑现任何「获得」——按下面这几处改最省力" % numbers


def render(brief, max_chars=MAX_BLOCK):
    """渲染成给人和给提示词共用的 markdown；空单返回空串（**不注入空段**）。"""
    chapters = brief.get("chapters") or []
    if not chapters:
        return ""
    lines = ["## 改稿单（当前稿子被拦的章，按事实给的改法，不是文风意见）", ""]
    for chapter in chapters:
        head = "第 %d 章" % chapter["chapter"]
        if chapter.get("title"):
            head += " %s" % chapter["title"]
        lines.append("### %s（%d 字 / %d 段）" % (head, chapter["words"],
                                                 chapter["paragraphs"]))
        anchors = chapter.get("anchors") or []
        if not chapter.get("located"):
            lines.append("（未读到本章正文，只报章级判据，不做锚点定位）")
        elif anchors:
            total = chapter.get("anchor_total") or len(anchors)
            lines.append("全章提到「获得」相关的东西 %d 处，没有一处兑现。离兑现最近的几处"
                         "（段号/句号与原稿一致）：" % total)
            for anchor in anchors:
                detail = "P%d S%d「%s」——%s" % (
                    anchor["paragraph"], anchor["sentence"], anchor["text"],
                    anchor["mark_text"])
                if anchor.get("action"):
                    detail += "（动作：%s）" % anchor["action"]
                lines.append("- " + detail)
        else:
            lines.append("全章没有一处提到该写的东西——不是缺动作，是这一章没写这件事。")
        for advice in chapter.get("advice") or []:
            lines.append("- 改法：" + advice)
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        # 截断也要截在行边界，别把一句话砍成半句让人猜。
        cut = text[:max_chars]
        cut = cut[:cut.rfind("\n")] if "\n" in cut else cut
        text = cut.rstrip() + "\n（改稿单已截断，完整内容见任务详情）"
    return text


def block_for(task, max_chars=MAX_BLOCK):
    """任务级入口：现算判据 → 有 blocker 才出改稿单，否则返回空串。

    给提示词回灌用（`skills.block_for`）。**不读缓存、不落盘**：每次调用都按
    当前正文现算，作者改完稿子再跑，结论立刻跟着变——存一份快照反而会骗人。
    """
    try:
        result = appeal.check_task(task, use_model=False)
    except Exception:
        return ""
    if not result.get("enabled"):
        return ""
    return render(build(result, task), max_chars=max_chars)
