# -*- coding: utf-8 -*-
"""题材承诺兑现（「吸引力门」）判据，2026-11-04 P0-B。

要判的不是「文笔好不好」，而是一个**可以用事实反驳**的问题：
**这一章里，读者想看的那个「获得」到底有没有在章内发生？**

平台驳回《一头猎物，三笔账》的原话是「综合质量一般、整体内容比较普通且缺乏
吸引力」。同一份稿子过了内部 R1–R7（查的是重复段、元叙述、口径冲突、开篇错位
这类**硬伤**），说明硬伤门与过签之间还差一维：类型承诺有没有兑现。诊断文档
（`创作成果/一头猎物三笔账_签约驳回分析与新作调整方向_v1.md` §1、§3）把这一维
写成了四条承诺——快速进山 / 收获有来路 / 家中生活具体变好 / 每几章一个小闭环——
其中后三条落在同一件事上：**每章至少落地一次「获得」**（猎获、现金、家中改善
三选一，必须章内兑现，禁止「下章再算」）。

本模块就是把这个「至少一次」变成可执行判据：

* 纯规则、纯函数、**零网络零额度**，几百毫秒内跑完三章；
* 证据落到「第几章第几段第几句」，作者能直接翻到那一句改；
* 判据只在**章内零获得**时出 blocker，节奏类问题（开篇见猎、章节字数）先记
  warning——第一版宁可漏报，不可误拦；
* 模型只做兜底，默认关闭；开启后抛错/超时一律退回规则判定，不崩、不放行。

三条纪律（改这个文件前先读）：

1. **不认识题材就不判**。承诺清单是题材级的，把「打猎文」的承诺套到鉴宝文上
   只会制造假拦截。`check_task()` 找不到题材画像时返回 `enabled=False`。
2. **判据给的是事实，不是品味**。「第 3 章第 27 段第 2 句写了『货拿到了』」是
   事实；「这句话写得不好」不是本模块的结论。
3. **不代替人**。blocker 进 `quality_gate.evaluate_release` 的 `blockers`，仍然
   走既有的强制放行通道（带理由 + 二次确认 + 台账留痕），本模块不自己拦发布。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# 门禁只看前三章：过签评估（平台侧）就是拿开头几章做的决定，第四五章写得再好
# 也换不回读者在第 1 章划走。
DEFAULT_CHAPTERS = 3
# 但「每几章一个小闭环」这条承诺不是「每章」：连载里单章过渡是常态，连续两章
# 拿不到东西才是读者划走的点。所以门禁**外**（第 4 章起）只判连续空窗，不判
# 单章空窗。2026-11-04 在真实原稿上跑出来的缺口：第 4 章 1406 字零获得、
# 第 5 章零获得，而旧版 `analyze` 只读前三章 —— 门禁等于只查了投稿五章的 60%。
DEFAULT_SCAN = 6
MAX_EMPTY_GAP = 2
DEFAULT_MIN_WORDS = 2000

# ------------------------------------------------------------------ 获得判据
# 每类「获得」= 东西（things）+ 兑现动作（actions）出现在**同一句**里。
# 同句约束是刻意的：跨句搭配会把「打算换木料」和「真换了木料」混为一谈，
# 而这两件事在《一头猎物》里恰好一章之隔（第 2 章拟换、第 3 章才真拿到货）。
_GAIN_RULES = {
    "hunt": {
        "label": "猎获",
        "things": ("猎物", "野猪", "目标", "货", "一头", "獠牙", "毛皮"),
        "actions": ("打到", "猎到", "猎获", "放倒", "撂倒", "抬上爬犁", "装上爬犁",
                    "固定在爬犁", "查验", "过秤", "称重", "装车", "扛回", "拖回"),
    },
    "cash": {
        "label": "现金",
        "things": ("钱", "现金", "元", "块", "票子"),
        "actions": ("拿到", "到手", "收了", "领到", "结了", "结清", "揣进",
                    "点清", "净余", "结回", "打进"),
    },
    "home": {
        "label": "家中改善",
        "things": ("药", "煤", "木料", "檩条", "屋顶", "房顶", "学费", "报名费",
                   "肉", "粮", "棉鞋", "棉衣", "煤票", "白面",
                   # 「欠项冲清」也是家里变好——而且是《一头猎物》第 5 章唯一
                   # 真正落地的那次获得（原话「欠项清了，接下来领药买煤得重新付」）。
                   # 这一类是拿真稿对读出来的，不是从诊断文档抄的：只按文档措辞
                   # 写动作词表（买回 / 换了 / 结清），就会把「旧欠清了」这种减法
                   # 式获得读成「零获得」。假阴性比假阳性危险——放过烂稿没人察觉。
                   "欠", "旧欠", "欠项", "欠条", "债务", "饥荒"),
        "actions": ("买回", "买了", "换了", "换回", "拉回", "修好", "补好", "钉上",
                    "交上", "搬回", "分到", "端上桌", "塞满", "下锅", "进家", "到账",
                    "清了", "冲清", "冲掉", "还清", "抵掉"),
    },
}

# 整句就是「还没发生」的语气时直接跳过。刻意不含裸「将」「等」：
# 「等周围确认安全，才上前查验目标」是**已经发生**的事，把「等」当未来时
# 会漏掉第 3 章唯一那条兑现证据（真踩过）。
_UNREALIZED = re.compile(
    r"(拟|打算|计划|准备|将要|即将|如果|假如|要是|若|还没|尚未|未能|"
    r"待确认|待明日|待定|下章|下一章|明天再|下次再|不许|不准|"
    r"再决定|再算|再谈|再说|再分|再议)")

# 紧贴在动作前面的否定词才是真否定：「没有立刻走近，才上前查验」里的「没有」
# 离动作隔了半个句子，不能算。
_NEGATION = re.compile(r"[不没未别勿]")
_NEGATION_WINDOW = 5

_HEADING = re.compile(
    r"^#{1,6}\s*第([0-9零一二三四五六七八九十百千]{1,6})章[\s:：、.]*(.*)$")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…])")
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(text):
    """把「一」「十二」「二十一」解析成整数；解析不了返回 None。"""
    text = str(text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, current = 0, 0
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "千":
            total += (current or 1) * 1000
            current = 0
        else:
            return None
    return total + current


def _positive_int(value, default):
    """配置项来自用户 JSON：拿不到正整数就用默认值，绝不因为脏配置崩掉。"""
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if number > 0 else default


def split_chapters(text):
    """按「第 N 章」小标题切章；没有标题就整体当一章。"""
    lines = str(text or "").splitlines()
    chapters, current = [], None
    for line in lines:
        match = _HEADING.match(line.strip())
        if match:
            if current is not None:
                chapters.append(current)
            number = _cn_number(match.group(1))
            current = {"chapter": number if number is not None else len(chapters) + 1,
                       "title": match.group(2).strip(), "lines": []}
            continue
        if current is not None:
            current["lines"].append(line)
    if current is not None:
        chapters.append(current)
    if not chapters:
        return [{"chapter": 1, "title": "", "text": str(text or "")}]
    out = []
    for index, item in enumerate(chapters, 1):
        number = item["chapter"] if item["chapter"] else index
        out.append({"chapter": number, "title": item["title"],
                    "text": "\n".join(item["lines"]).strip()})
    return out


def paragraphs(text):
    """段落 = 空行分隔的块。字数统计与「第几段」都以它为准。"""
    blocks = re.split(r"\n\s*\n", str(text or ""))
    out = []
    for block in blocks:
        cleaned = block.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        out.append(re.sub(r"\s*\n\s*", "", cleaned))
    return out


def sentences(paragraph):
    """按中文句末标点断句，保留引号里的内容。"""
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(str(paragraph or ""))]
    return [part for part in parts if part]


def words(text):
    """字数＝去空白后的字符数，与 `quality_gate.local_version` 同口径。"""
    return len(re.sub(r"\s", "", str(text or "")))


def _negated(sentence, index):
    """动作前面紧挨着否定词才算否定。"""
    head = sentence[max(0, index - _NEGATION_WINDOW):index]
    return bool(_NEGATION.search(head))


def _find_gain(sentence):
    """在一句里找「东西 + 兑现动作」，返回 (gain_id, action) 或 None。"""
    if _UNREALIZED.search(sentence):
        return None
    for gain_id, rule in _GAIN_RULES.items():
        if not any(thing in sentence for thing in rule["things"]):
            continue
        for action in rule["actions"]:
            index = sentence.find(action)
            if index < 0:
                continue
            if _negated(sentence, index):
                continue
            return gain_id, action
    return None


def gains_of(chapter_text):
    """逐段逐句找获得，返回带段号句号的事实清单。"""
    found = []
    for p_index, paragraph in enumerate(paragraphs(chapter_text), 1):
        for s_index, sentence in enumerate(sentences(paragraph), 1):
            hit = _find_gain(sentence)
            if not hit:
                continue
            gain_id, action = hit
            found.append({
                "type": gain_id,
                "label": _GAIN_RULES[gain_id]["label"],
                "paragraph": p_index,
                "sentence": s_index,
                "action": action,
                "text": sentence[:80],
            })
    return found


# ------------------------------------------------------------------ 题材画像
_HUNT_PROFILE = {
    "id": "hunt_1980s",
    "name": "年代打猎文",
    "detect": ("野猪", "狩猎证", "枪证", "界桩", "猎捕", "林场", "进山", "猎户"),
    "chapters": DEFAULT_CHAPTERS,
    "scan_chapters": DEFAULT_SCAN,
    "min_words": DEFAULT_MIN_WORDS,
    "promises": (
        {"id": "enter_fast", "text": "快速进山", "kind": "pace"},
        {"id": "gain_traceable", "text": "收获有来路", "kind": "gain", "gain": "hunt"},
        {"id": "home_better", "text": "家中生活具体变好", "kind": "gain", "gain": "home"},
        {"id": "chapter_loop", "text": "每几章一个小闭环", "kind": "gain", "gain": "any"},
    ),
}

# 内置一份「年代打猎文」：零数据目录也能跑（npm 安装、隔离实例、单测）。
_BUILTIN_PROFILES = {_HUNT_PROFILE["id"]: _HUNT_PROFILE}


def _profile_dir():
    from .. import paths
    return Path(paths.DATA_DIR) / "novel" / "appeal"


def profiles():
    """内置画像 + `data/novel/appeal/*.json` 覆盖（同 id 后写覆盖先写）。"""
    out = {key: dict(value) for key, value in _BUILTIN_PROFILES.items()}
    root = _profile_dir()
    try:
        files = sorted(root.glob("*.json")) if root.is_dir() else []
    except OSError:
        files = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        out[str(data["id"])] = data
    return out


def profile_for(genre):
    """按 id 或中文名找画像；找不到返回 None（**不猜**）。"""
    key = str(genre or "").strip()
    if not key:
        return None
    for profile in profiles().values():
        if key in (str(profile.get("id") or ""), str(profile.get("name") or "")):
            return profile
    return None


def detect_genre(text):
    """按关键词投票猜题材；不够明显就返回 None，宁可漏判不误判。"""
    sample = str(text or "")
    if not sample.strip():
        return None
    best, best_score = None, 0
    for profile in profiles().values():
        hits = sum(1 for word in profile.get("detect") or () if word in sample)
        if hits > best_score:
            best, best_score = profile, hits
    # 单靠一个词不算数：一份鉴宝文里也可能出现一次「野猪」。
    return best["id"] if best is not None and best_score >= 2 else None


# ------------------------------------------------------------------ 判据主体
def _chapter_blockers(chapter_no, gains, chapter_words, min_words, gate=True,
                      previous_gap=None):
    """逐章判：门禁内零获得即 blocker，门禁外只认「连续空窗」。

    ``previous_gap`` 是上一个零获得章号（没有则 None）。放在这里而不是
    调用方，是为了让「第 4、5 章连续」这句话在同一个地方生成——拆两处写，
    早晚会出现「两章都单独报、但连续那条不报」的不一致。
    """
    blockers, warnings = [], []
    if not gains:
        if gate:
            blockers.append(
                "第 %d 章没有兑现任何「获得」（猎获 / 现金 / 家中改善全缺），"
                "读者这一章拿不到东西" % chapter_no)
        elif previous_gap:
            blockers.append(
                "第 %d、%d 章连续两章没有兑现任何「获得」（猎获 / 现金 / "
                "家中改善全缺），这是读者划走的位置" % (previous_gap, chapter_no))
        else:
            # 门禁外单章空窗：连载里的过渡章是正常写法，记 warning 不拦。
            warnings.append("第 %d 章没有兑现「获得」（单章空窗，未达连续线）"
                            % chapter_no)
    if min_words and chapter_words and chapter_words < min_words:
        warnings.append("第 %d 章仅 %d 字（低于 %d 字）" %
                        (chapter_no, chapter_words, min_words))
    return blockers, warnings


def _promise_table(profile, chapter_results):
    """把承诺对到「哪一章的哪句兑现了」；不受判据覆盖的显式标出来。

    `judged=False` 的行是**没有**机器判据的承诺（例如「快速进山」这种节奏类
    承诺）。列出来而不是悄悄跳过，是为了让作者看到「这一条没人替你查」——
    用关键词判「500 字内见猎」会分不清「见迹」和「见猎」，误报几次之后
    就再没人看 warning 了，宁可不判。
    """
    rows = []
    for promise in profile.get("promises") or ():
        if not isinstance(promise, dict):
            continue
        kind = str(promise.get("kind") or "gain")
        want = str(promise.get("gain") or "any")
        covered = []
        if kind == "gain":
            for result in chapter_results:
                hit_gains = [g for g in result["gains"]
                             if want == "any" or g["type"] == want]
                if hit_gains:
                    first = hit_gains[0]
                    covered.append({"chapter": result["chapter"],
                                    "paragraph": first["paragraph"],
                                    "sentence": first["sentence"]})
        rows.append({"id": str(promise.get("id") or ""),
                     "text": str(promise.get("text") or ""),
                     "judged": kind == "gain",
                     "covered": covered})
    return rows


def analyze(chapters, genre=None, profile=None, use_model=False, model=None,
            gate=None, scan=None):
    """判类型承诺兑现。

    ``chapters`` 可以是「第 N 章」标题齐全的整篇字符串，也可以是
    ``[{"chapter": 1, "text": "..."}]``。返回证据与 blockers，不抛异常。

    ``gate`` 是「每章必须有获得」的章数（默认 3，过签评估看的就是开头），
    ``scan`` 是总扫描章数（默认 6）：第 4 章起只判连续空窗。两个数分开，
    是因为「前三章每章都得给东西」与「整卷不许连着两章没东西」是两条不同的
    承诺，用同一个数字表达会让第二条永远落不了地。

    不传 ``genre`` / ``profile`` 时按内置「年代打猎文」判——**只**为方便直接
    调用；生产入口是 `check_task()`，它认不出题材就不判。
    """
    profile = profile or profile_for(genre) or _BUILTIN_PROFILES[_HUNT_PROFILE["id"]]
    if isinstance(chapters, str):
        parsed = split_chapters(chapters)
    elif isinstance(chapters, (list, tuple)):
        parsed = []
        for index, item in enumerate(chapters):
            if not isinstance(item, dict):
                continue
            number = _cn_number(item.get("chapter"))
            if number is None:
                number = _positive_int(item.get("chapter"), index + 1)
            parsed.append({"chapter": number,
                           "title": str(item.get("title") or ""),
                           "text": str(item.get("text") or "")})
    else:
        parsed = []
    gate_count = _positive_int(
        gate if gate is not None else profile.get("chapters"), DEFAULT_CHAPTERS)
    scan_count = max(gate_count, _positive_int(
        scan if scan is not None else profile.get("scan_chapters"), DEFAULT_SCAN))
    parsed = parsed[:scan_count]
    min_words = _positive_int(profile.get("min_words"), DEFAULT_MIN_WORDS)
    model_gains, model_note = {}, ""
    if use_model and callable(model):
        model_gains, model_note = _model_gains(parsed, profile, model)

    results, blockers, warnings = [], [], []
    previous_gap = None
    for index, item in enumerate(parsed):
        chapter_gains = gains_of(item["text"])
        for extra in model_gains.get(item["chapter"]) or []:
            if any(g["type"] == extra["type"] and g["paragraph"] == extra["paragraph"]
                   for g in chapter_gains):
                continue
            chapter_gains.append(extra)
        chapter_words = words(item["text"])
        chapter_blockers, chapter_warnings = _chapter_blockers(
            item["chapter"], chapter_gains, chapter_words, min_words,
            gate=index < gate_count, previous_gap=previous_gap)
        previous_gap = item["chapter"] if not chapter_gains else None
        results.append({"chapter": item["chapter"], "title": item["title"],
                        "words": chapter_words, "gains": chapter_gains,
                        "gated": index < gate_count,
                        "blockers": chapter_blockers, "warnings": chapter_warnings})
        blockers.extend(chapter_blockers)
        warnings.extend(chapter_warnings)
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    return {
        "enabled": True,
        "genre": str(profile.get("id") or ""),
        "genre_name": str(profile.get("name") or ""),
        "chapter_count": len(results),
        "chapters": results,
        "promises": _promise_table(profile, results),
        "blockers": blockers,
        "warnings": warnings,
        "model_used": bool(model_note and not model_note.startswith("error:")),
        "model_note": model_note,
    }


def _model_gains(parsed, profile, model):
    """模型兜底：只接受**能在原文里核对到**的补充证据。

    模型返回 ``{"chapters": [{"chapter": 1, "gains": [{"type": "cash",
    "paragraph": 3, "quote": "……"}]}]}``。每一条都要求 `quote`（≥6 字）真的
    出现在该段正文里，且 `type` 是已知获得类目——模型不能凭空造一条获得把
    闸门顶开，也不能改写原稿（判据读的必须是作者真正发出去的正文）。任何
    异常、超时、格式不对，一律退回规则结果，不抛。
    """
    try:
        answer = model([{"chapter": item["chapter"], "text": item["text"]}
                        for item in parsed], profile)
    except Exception as exc:  # noqa: BLE001 - 兜底不能把发布链带崩
        return {}, "error: %s" % exc
    if not isinstance(answer, dict):
        return {}, "error: 模型返回格式无效"
    patches = answer.get("chapters")
    if not isinstance(patches, list):
        return {}, "error: 模型返回缺少 chapters"
    by_chapter, accepted, rejected = {}, 0, 0
    for item in parsed:
        para_list = paragraphs(item["text"])
        entries = next((p.get("gains") for p in patches if isinstance(p, dict)
                        and p.get("chapter") == item["chapter"]), None)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                rejected += 1
                continue
            gain_id = str(entry.get("type") or "").strip()
            quote = str(entry.get("quote") or "").strip()
            paragraph = _nonnegative(entry.get("paragraph"))
            if (gain_id not in _GAIN_RULES or len(quote) < 6 or
                    paragraph is None or paragraph < 1 or paragraph > len(para_list) or
                    quote not in para_list[paragraph - 1]):
                rejected += 1
                continue
            by_chapter.setdefault(item["chapter"], []).append({
                "type": gain_id,
                "label": _GAIN_RULES[gain_id]["label"],
                "paragraph": paragraph,
                "sentence": _sentence_index(para_list[paragraph - 1], quote),
                "action": "model",
                "text": quote[:80],
            })
            accepted += 1
    return by_chapter, "ok: 采纳 %d 条，丢弃 %d 条" % (accepted, rejected)


def _nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _sentence_index(paragraph, quote):
    """把核对过的引文定位到句号；定位不到就记第 1 句。"""
    for index, sentence in enumerate(sentences(paragraph), 1):
        if quote[:12] in sentence:
            return index
    return 1


def check_task(task, chapters=None, genre=None, use_model=None, model=None,
               scan=None):
    """从任务读章节正文并判定；不适用时返回 ``enabled=False`` 及其原因。

    不判的四种情况（都返回 enabled=False，绝不出 blocker）：
    非长篇/连载任务、没有 workdir、目录里没有 chapter-NN.md、题材画像认不出。

    读多少章：``chapters``（门禁章数，默认 3）与 ``scan``（总扫描章数，默认 6）
    取大者。多读的章不参与「每章必须有获得」，只用来判连续空窗与字数——
    这与平台看的是「已发布的前几章」一致，而不是只看第一次投稿的那三章。
    """
    task = task if isinstance(task, dict) else {}
    kind = str(task.get("type") or "").lower()
    # 只认长篇/连载。**不要**放宽到 `engine == "review"`：文档、文章、调研
    # 报告也走 review 引擎，它们的 workdir 里若碰巧有 chapter-NN.md、正文又提到
    # 两次「野猪」，就会被套上打猎文的承诺清单——2026-11-04 复核时这条比
    # 文档写的边界宽了三类任务。
    if kind not in ("novel", "serial_novel"):
        return {"enabled": False, "reason": "not_serial"}
    root = Path(str(task.get("workdir") or ""))
    if not str(task.get("workdir") or "").strip() or not root.is_dir():
        return {"enabled": False, "reason": "no_workdir"}
    gate = _positive_int(chapters, DEFAULT_CHAPTERS)
    limit = max(gate, _positive_int(scan, DEFAULT_SCAN))
    texts = []
    for number in range(1, limit + 1):
        path = root / ("chapter-%02d.md" % number)
        try:
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            body = ""
        if body:
            texts.append({"chapter": number, "text": body})
    if not texts:
        return {"enabled": False, "reason": "no_text"}
    profile = profile_for(genre or task.get("appeal_genre")) or \
        profile_for(_auto_genre(task, texts)) or None
    if profile is None:
        return {"enabled": False, "reason": "unknown_genre"}
    want_model = use_model if use_model is not None else (
        task.get("appeal_model") is True or
        str(os.environ.get("TUTTI_APPEAL_MODEL") or "").strip() in ("1", "true", "on"))
    hook = model if callable(model) else _MODEL_HOOK
    result = analyze(texts, profile=profile, use_model=bool(want_model), model=hook,
                     gate=gate, scan=limit)
    result["workdir"] = str(root)
    return result


def _auto_genre(task, texts):
    """先看书目元数据里的类型/题材，猜不出来再看正文。"""
    from .. import store
    book = {}
    try:
        book = store.get_task(task.get("id")) or {}
    except Exception:  # noqa: BLE001 - 元数据读不到不算错
        book = {}
    for key in ("appeal_genre", "genre", "类型", "题材"):
        value = (task.get(key) or book.get(key) or "")
        if str(value).strip():
            return str(value).strip()
    return detect_genre("\n".join(item["text"] for item in texts))


_MODEL_HOOK = None


def set_model_hook(hook):
    """注册模型兜底实现（可选）。传 None 关闭。"""
    global _MODEL_HOOK
    _MODEL_HOOK = hook if callable(hook) else None
    return _MODEL_HOOK
