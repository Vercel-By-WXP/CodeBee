# -*- coding: utf-8 -*-
"""pipeline.py 的 content 子模块。

从 pipeline.py 拆出（单文件行数治理）；模块级状态与辅助函数仍在 pipeline.py，全部经 _pl() 解析以保持测试对 pipeline 属性的打桩语义；旧调用面由 pipeline 再导出。
"""
from __future__ import annotations

import os
import re


def _pl():
    from . import pipeline
    return pipeline


def _content_role(task):
    """返回内置类型的专业角色；自定义 review 流程使用中性角色。"""
    spec = _pl().CONTENT_DELIVERY_CONTRACTS.get(str(task.get("type") or ""))
    return spec[0] if spec else "内容交付专家"


def _content_contract(task):
    """把类型成品约束渲染为稳定提示块；无内置契约时不额外注入。"""
    spec = _pl().CONTENT_DELIVERY_CONTRACTS.get(str(task.get("type") or ""))
    if not spec:
        return ""
    block = "\n\n## 本类型交付约束\n" + "\n".join("- " + item for item in spec[1])
    if task.get("type") == "short_drama" and isinstance(task.get("drama"), dict):
        drama = task["drama"]
        constraints = []
        if drama.get("episodes"):
            constraints.append("共 %d 集" % int(drama["episodes"]))
        if drama.get("seconds_per_episode"):
            constraints.append("每集约 %d 秒" % int(drama["seconds_per_episode"]))
        if constraints:
            block += "\n- 本任务规格：" + "，".join(constraints) + "；必须按此规格交付。"
    if task.get("type") == "short_story":
        spec = story_spec(task)
        if spec:
            block += ("\n- 本任务规格：全文约 %d 字（下限 %d 字、上限 %d 字），"
                      "必须按此规格交付；结尾必须兑现开篇承诺。"
                      % (spec["words"], spec["min_words"], spec["max_words"]))
    if task.get("type") == "rank_scan":
        from . import selection
        block += "\n" + selection.CONTRACT
    return block

# 调研报告类稿件的追加要求（借鉴 gpt-researcher 迭代深研）：有网络/读文件工具时
# 多源交叉验证，单源结论降权——调研的可信度来自证据链而非文采。
# 来源冲突裁决借鉴 deepresearch-agent 记忆层矛盾检测（Majority/Source-Weight 消解）；
# 子问题缺口补查借鉴 dzhng/deep-research 迭代循环；
# 来源可信度审查与主动找反例借鉴对抗降噪（2026-09-28：Cornell Tech 深研 agent
# 可被 UGC 误导内容操纵 / MisKnow-Agent 误导暴露即致错 / ARGUS 误信息注入防御 /
# deepresearch-agent Red-Blue 两队互搏——检索降噪多源独立验证后收窄的落地切片）
RESEARCH_APPENDIX = """

## 调研要求（证据链）
- 有联网/检索工具就先搜集资料再写：同一关键结论至少两个独立来源交叉验证，
  单源信息要标注「仅单一来源」。
- 引用来源在文中用行内链接或脚注标明（域名即可，不编造 URL）。
- 区分「事实」与「观点」：数据/时间/版本号给来源，预测/评价标明是分析。
- 来源冲突显式裁决：同一事实多个来源说法不一致时，写明分歧点与各方依据，给出
  倾向判断和理由（多数来源一致取多数，权威差异按来源可信度加权）——不各说一半，
  也不悄悄只取其一。
- 来源先审后用：营销软文、SEO 内容农场、利益相关方的自我背书一律降权，引用时
  点明其立场；同一说法优先追溯到更原始的出处（转述会走样）。
- 结论要经得起反例：关键结论不仅找支持证据，也主动找反对证据；找不到才可写强
  结论，写完自问「什么证据能推翻它」并在风险与局限里如实回答。
- 缺口驱动补查：起草前先列出报告要回答的子问题清单，逐个核对证据是否到位；
  没找到答案的子问题明确标注「证据不足」，不用泛泛而谈填空。
- 结构硬性要求：报告第一段必须是「**核心结论**」三行以内的要点摘要（结论先行），
  之后才展开分层论证 → 风险与局限（说明哪些结论证据不足）。"""

# 翻译类稿件的追加要求（借鉴 andrewyng/translation-agent 三步法 translate→reflect→improve
# 的 glossary 机制）：动笔前先提炼术语表、全篇译名以表为准——术语一致性此前只靠
# 评审 rubric「术语一致性」事后抓，起草侧无约定，长文译名漂移只能靠修订轮返工。
# 翻译腔自查条借鉴 yomiyasu（翻译腔检查器）：欧化句式/逐词直译前置到起草侧自查，
# 不再只靠评审 rubric「流畅度」事后抓。TransAgents（多智能体文学翻译）的出版社
# 角色分工不借鉴：评审链已有对应物。
TRANSLATION_APPENDIX = """

## 翻译要求（术语表先行）
- 动笔前先通读全文，提炼术语表放在稿件开头（`## 术语表`）：专有名词（人名/地名/
  作品名/产品名/机构名）与反复出现的领域术语，逐条给出「源文 → 译名」。
- 全篇译名以术语表为准：同一源文术语从头到尾一个译法，不随上下文漂移；业界已有
  通用译名的从通用，没有的定一个贴切的并全篇保持一致。
- 无术语可提炼的短文可省略术语表，译名一致性要求不变。
- 交稿前自查翻译腔（欧化句式/逐词直译）：长句拆短、被动改主动，清掉「被……所」
  「……性」「……的存在」这类直译腔标记；译文读感应是「用目标语写出来的」，
  不是「翻出来的」。"""

NOVEL_REVISE_PROMPT = """你是__ROLE__。请根据下方汇总评审意见修订稿件文件：`__FILE__`（直接写入该文件）。文件必须以 UTF-8 编码保存（PowerShell 写文件显式加 -Encoding UTF8，禁止依赖默认编码）。

## 原始写作任务
__GOAL__

## 评审汇总（各维度均分与主要问题）
__CRITIQUE__

## 要求
- 针对性改进所有 major 问题；保持既定风格与设定。
- 完成后用 3 句话说明本轮改了什么。"""

# 评审口径单一真源：评审面提示词（单章/全局）与作者面注入块从这里同源提取，
# 两边永不脱节。此前评审清单/签约专项/全局口径只活在评审面，作者第一遍写完
# 被打回才知道按什么标准被审（2026-10-10 实案：全章过线仍卡全局 major）。
# 作者面刻意不下发评分机制本体（1-10 分/阈值/宁严勿宽）——对写作者零价值，
# 透下去只喂响应试素材；清单条目本身全是质量正向要求，事前下发只有好处。
REVIEW_CHECKLIST_ITEMS = (
    "叙述语域与视角统一",
    "复杂情感落到动作、五感或生理细节",
    "开篇在前 600 字进入具体冲突",
    "每章发生真实的局面变化",
    "无可删的重复铺垫与长段解释",
)
GLOBAL_REVIEW_FOCUS = ("主线一致性、人物弧光、开篇吸引力、文风表现、情感表达、"
                       "情节推进与节奏控制")


def author_review_rubric_block(dims, signing_dims):
    """作者面评审口径块：起草/修订提示词里事前下发「会按什么标准被审」，
    与评审面同源（REVIEW_CHECKLIST_ITEMS / GLOBAL_REVIEW_FOCUS）。静态文本，
    同批各章字节一致，不碎前缀缓存。"""
    lines = [
        "## 评审口径（本书会按以下标准审稿，写作时对照自查）",
        "- 评分维度：" + " / ".join(dims) + "。",
        # 作者只需要知道审稿关注点和提交证据，不需要评审侧的数值量表。
        # Keeping the score range out of this block prevents prompt-gaming and
        # keeps the author/reviewer contracts intentionally separate.
        "- 维度权重与证据要求：" + "；".join(
            "%s（权重 %.2f）：请结合正文证据自查" % (d["name"], d["weight"])
            for d in _pl().review_config._clean_dimensions(dims)
        ) + "。",
        "- 逐项硬清单：" + "；".join(_pl().REVIEW_CHECKLIST_ITEMS) + "。",
        "- 全书级关注：" + _pl().GLOBAL_REVIEW_FOCUS + "。",
        "- 签约专项维度：" + "、".join(signing_dims) + "。",
    ]
    return "\n".join(lines) + "\n"


NOVEL_CRITIQUE_PROMPT = """你是严格的评审（不要使用任何工具、不要修改文件，只依据下方稿件内容评审）。
请输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{
  "scores": {"__DIMKEYS__"},
  "issues": [{"dim": "维度名", "severity": "major|minor", "note": "具体问题", "quote": "支撑该问题的稿件原文连续片段（≥8字，逐字摘录不许改写）"}],
  "summary": "一句话总评"
}
每个维度打 1-10 分（可为小数），宁严勿宽。major 问题必须给 quote（系统会逐条校验引文是否真在稿件中——编造的引文会被降档标记）。
评审逐项核查以下各项是否成立：%s。""" % ("；".join(REVIEW_CHECKLIST_ITEMS)) + """

## 待评审稿件
---
__MANUSCRIPT__
---"""


def _tpl(task, key, default):
    """自定义流程的提示词覆盖：任务上带模板则用之，否则用内置默认。"""
    t = (task.get(key) or "").strip()
    return t if t else default


# ---------------------------------------------------------------- 短篇规格（单稿字数闸）

def story_spec(task):
    """短篇规格：任务 story.words → 字数闸上下限。

    下限 75%（低于即扩写/判未完成）、上限 135%（超出先压缩；超出不挡交付，
    短一点瘪一点都发不出去，长一点平台还能截）。无规格/字数过小返回 None
    （零噪音：老任务与 novel 流程不受影响）。"""
    s = task.get("story")
    if not isinstance(s, dict):
        return None
    try:
        words = int(s.get("words") or 0)
    except Exception:
        return None
    if words < 500:
        return None
    return {"words": words,
            "min_words": max(300, int(words * 0.75)),
            "max_words": int(words * 1.35)}


def story_length_block(task):
    """起草提示词里的篇幅要求块；无规格返回 ""（零噪音，字节对齐旧版）。"""
    spec = story_spec(task)
    if not spec:
        return ""
    return (
        "\n\n## 篇幅要求\n"
        "- 全文目标约 %d 字（正文字数，不含标题行）；下限 %d 字、上限 %d 字，成稿后按此自查。\n"
        "- 字数不足时把既有冲突写透（具体场景、动作、对话与心理细节），"
        "禁止用摘要、回顾、排比或环境描写注水凑字。\n"
        "- 单篇完整故事：不写「第 X 章」分章标题；结尾必须兑现开篇的核心承诺"
        "（反转、揭示或情绪落点），不以「未完待续」收束。"
        % (spec["words"], spec["min_words"], spec["max_words"]))


# ---------------------------------------------------------------- 故事圣经与评审视角

BIBLE_FILE = "story-bible.md"
_BIBLE_MAX_CHARS = 20000
MODULES_FILE = "plot-modules.md"
_MODULES_MAX_CHARS = 12000

# 评审视角播种（dev-3.0 式 bug hunters）：N 个评审各领一个深挖镜头，
# 避免全员盯着同一处。按评审序号取模分配——同一评审每轮同一镜头，
# 提示词前缀字节稳定，不碎前缀缓存。
CRITIC_LENSES = (
    "情节逻辑与因果链（事件是否成立、动机是否充分、有没有逻辑硬伤）",
    "人物一致性与弧光（言行是否符合人设、成长是否有迹可循）",
    "文笔与节奏（语言质量、场景切换、详略与爽点铺排）",
    "设定与伏笔台账（世界观自洽、伏笔是否按故事圣经埋设与回收）",
)


def _story_bible(workdir):
    """故事圣经（NovelClaw 式结构化记忆）：工作目录里的 story-bible.md
    （人物卡/世界观/伏笔台账），作者手工维护，每章起草与评审前自动注入。
    不存在/为空返回 ""——约定式功能，零配置时不产生任何提示词噪音。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().BIBLE_FILE))
    if not _pl()._inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        txt = _pl()._read_text_any_enc(p)[:_pl()._BIBLE_MAX_CHARS].strip()
    except OSError:
        return ""
    if not txt:
        return ""
    return ("## 故事圣经（story-bible.md：人物/世界观/伏笔台账，本书一切写作与评审以此为准，"
            "与其冲突处以圣经为准）\n\n"
            "**连续性锁**（借鉴 drama-skills）：人物外貌/性格口头禅/物品/能力等需要跨章一致"
            "的设定，写作时原样沿用圣经中的表述（可整句贴入正文），不要同义改写——每章用词"
            "一致读者才不会出戏。\n\n" + txt)


def _plot_modules(workdir):
    """剧情模块库（oh-story 拆文沉淀式）：工作目录里的 plot-modules.md
    （可复用的桥段/冲突/爽点/名场面素材模块），作者手工维护，每章起草与
    评审前自动注入。不存在/为空返回 ""——约定式功能，零配置零噪音。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().MODULES_FILE))
    if not _pl()._inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        txt = _pl()._read_text_any_enc(p)[:_pl()._MODULES_MAX_CHARS].strip()
    except OSError:
        return ""
    if not txt:
        return ""
    return ("## 剧情模块库（plot-modules.md：可复用的桥段/冲突/爽点素材模块，"
            "鼓励化用，不要照抄原句）\n\n" + txt)


def _book_volume_plan(task, outline, upto):
    """本书的卷规划表（卷边界+卷名+卷弧光）。实现见 planner.book_volume_plan
    ——放在 planner 是因为它要沿 serial.continues 链读历史 run 的卷名，属
    「大纲/命名」职责；此处保留薄封装便于连载流程阅读。"""
    return _pl().planner.book_volume_plan(task, outline, upto)


LEDGER_FILE = os.path.join(".codebee", "resource-ledger.md")
_LEDGER_MAX_CHARS = 6000   # 账本注入上限：太老的状态让评审官收敛 recent 优先
CONTINUITY_LEDGER_FILE = os.path.join(".codebee", "continuity-ledger.md")
_CONTINUITY_LEDGER_MAX_CHARS = 8000


def _parse_tagged_lines(text, tag):
    """从模型回复提取 <tag>...</tag> 块的非空行列表（缺失/空块返回 []）。"""
    m = re.search(r"<%s>([\s\S]*?)</%s>" % (tag, tag), text or "")
    if not m:
        return []
    return [ln.strip(" -*") for ln in m.group(1).splitlines() if ln.strip(" -*")]


def _append_ledger(workdir, chapter, lines):
    """资源账本追加：本章评审提炼的道具/伤情/承诺/伏笔增量，供下章起草注入。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().LEDGER_FILE))
    if not _pl()._inside(workdir, p):
        raise ValueError("ledger 路径越界")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write("# 资源账本（每章评审自动追加：道具/伤情/承诺/伏笔的现状，"
                    "写新章前必须核对，防止跨章穿帮）\n\n")
        f.write("## 第 %d 章\n%s\n" % (chapter, "\n".join("- " + x for x in lines)))


def _read_ledger(workdir):
    """读取资源账本注入文本（截至上一章）。不存在返回「（暂无记录）」。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().LEDGER_FILE))
    if not _pl()._inside(workdir, p) or not os.path.isfile(p):
        return "（暂无记录，本章建立的新道具/伤情/承诺/伏笔会被账本自动登记）"
    try:
        txt = _pl()._read_text_any_enc(p)[-_pl()._LEDGER_MAX_CHARS:].strip()
    except OSError:
        return "（暂无记录）"
    return txt or "（暂无记录）"


def _read_ledger_raw(workdir):
    """账本原文（缺文件/读失败返回空串）——供 _ledger_watchlist 解析，
    不带注入面的兜底文案。"""
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().LEDGER_FILE))
    if not _pl()._inside(workdir, p) or not os.path.isfile(p):
        return ""
    try:
        return _pl()._read_text_any_enc(p)
    except OSError:
        return ""


def _read_continuity_ledger(workdir, before_chapter=None):
    """Read ledger state, optionally excluding this and all later chapters.

    A resumed run may have persisted ledger rows from later chapters.  They
    are useful for the final audit but must never be shown while drafting or
    revising an earlier chapter, where they would leak future knowledge.
    """
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().CONTINUITY_LEDGER_FILE))
    if not _pl()._inside(workdir, p) or not os.path.isfile(p):
        return "（暂无连续性账本；本章必须先核对故事圣经与前情，再登记状态变化）"
    try:
        txt = _pl()._read_text_any_enc(p).strip()
    except OSError:
        return "（暂无连续性账本）"
    if before_chapter is not None and txt:
        try:
            cutoff = int(before_chapter)
        except (TypeError, ValueError, OverflowError):
            cutoff = None
        if cutoff is not None:
            prefix, kept, chapter, block = [], [], None, []
            for line in txt.splitlines():
                match = re.match(r"^##\s*第\s*(\d+)\s*章", line.strip())
                if match:
                    if chapter is not None and chapter < cutoff:
                        kept.extend(block)
                    chapter = int(match.group(1))
                    block = [line]
                elif chapter is None:
                    prefix.append(line)
                else:
                    block.append(line)
            if chapter is not None and chapter < cutoff:
                kept.extend(block)
            txt = "\n".join(prefix + kept).strip()
    txt = txt[-_pl()._CONTINUITY_LEDGER_MAX_CHARS:].strip()
    return txt or "（暂无连续性账本）"


def _append_continuity_ledger(workdir, chapter, rows):
    """Persist the latest validated continuity rows for one chapter.

    Re-running a chapter must replace its previous ledger block.  Keeping both
    review rounds would make a repaired state look like a reviewer conflict to
    the next chapter.
    """
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().CONTINUITY_LEDGER_FILE))
    if not _pl()._inside(workdir, p):
        raise ValueError("continuity ledger 路径越界")
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    if not rows and not os.path.isfile(p):
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        existing = _pl()._read_text_any_enc(p) if os.path.isfile(p) else ""
    except OSError:
        existing = ""
    heading_re = re.compile(r"^##\s*第\s*(\d+)\s*章\s*$")
    old_lines = existing.splitlines()
    headings = [(idx, int(m.group(1))) for idx, line in enumerate(old_lines)
                if (m := heading_re.match(line.strip()))]
    prefix = old_lines[:headings[0][0]] if headings else old_lines
    blocks = {}
    order = []
    for pos, (start, number) in enumerate(headings):
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(old_lines)
        if number not in order:
            order.append(number)
        # Last block wins when older versions of the append-only ledger are
        # being compacted during the first rewrite.
        blocks[number] = old_lines[start:end]
    chapter = int(chapter)
    if rows:
        new_block = ["## 第 %d 章" % chapter] + [
            "- %s｜%s｜%s｜%s｜%s" % (
                row.get("entity", ""), row.get("attribute", ""),
                row.get("state", ""), row.get("reason", ""),
                row.get("evidence", "")) for row in rows[:40]]
        if chapter not in order:
            order.append(chapter)
        blocks[chapter] = new_block
    elif chapter in blocks:
        # A re-reviewed chapter with no validated rows has no trusted facts to
        # carry forward; remove its previous block instead of retaining stale
        # reviewer claims.
        del blocks[chapter]
        order.remove(chapter)
    out = list(prefix)
    if not headings and not out:
        out = ["# 连续性账本（每章终评登记：人物/设定/事件状态；变化必须有因果依据）"]
    for number in order:
        if out and out[-1].strip():
            out.append("")
        out.extend(blocks[number])
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")


def _continuity_rows_from_ledger(workdir):
    """Parse prior ledger rows so a later chapter can be checked for jumps."""
    p = os.path.abspath(os.path.join(str(workdir or ""), _pl().CONTINUITY_LEDGER_FILE))
    if not _pl()._inside(workdir, p) or not os.path.isfile(p):
        return []
    try:
        text = _pl()._read_text_any_enc(p)
    except OSError:
        return []
    chapter = None
    rows = []
    for line in text.splitlines():
        heading = re.match(r"^##\s*第\s*(\d+)\s*章", line.strip())
        if heading:
            chapter = int(heading.group(1))
            continue
        if not line.lstrip().startswith("-") or chapter is None:
            continue
        raw = line.lstrip()[1:].strip().replace("｜", "|")
        rows.extend(_pl().novel_quality.parse_continuity_lines([raw], chapter))
    return rows


def _prior_continuity_rows(workdir, chapter):
    """Return only ledger assertions from chapters preceding ``chapter``."""
    try:
        current = int(chapter)
    except (TypeError, ValueError, OverflowError):
        return []
    return [row for row in _pl()._continuity_rows_from_ledger(workdir)
            if int(row.get("chapter") or 0) < current]


def _final_deterministic_quality_issues(workdir, start, end):
    """Recompute deterministic findings from the final persisted manuscript.

    Chapter-level review findings are intentionally replaced after polishing.
    The final publish gate therefore must rebuild both continuity and
    repetition findings from current chapter files/ledger state; carrying only
    the in-memory chapter findings can either retain stale evidence or lose a
    continuity conflict when a chapter's model verdict was refreshed.
    """
    chapter_texts = _pl()._quality_chapter_texts(workdir, start, end)
    rows = _pl()._validated_continuity_rows(
        _pl()._continuity_rows_from_ledger(workdir), chapter_texts, through=end)
    continuity = _pl().novel_quality.continuity_conflict_notes(rows)
    repetition = _pl().novel_quality.repetition_notes(chapter_texts)
    craft = []
    for chapter, text in sorted(chapter_texts.items()):
        craft.extend(_pl().novel_quality.chapter_craft_notes(text, chapter=chapter))
    opening = []
    if start <= 1 <= end and chapter_texts.get(1):
        opening = _pl().novel_quality.opening_signal_notes(chapter_texts[1])
    return continuity + repetition + craft + opening


def _shrink_context_block(sk_block, bible, budget=12000):
    """分层上下文降级（长提示词在容量受限通道上会挂起/秒拒，2026-09-17 讯飞实测）。

    四层优先级：故事圣经（最高，设定冲突以它为准）> 剧情模块库 > 经验库 > 大纲/前情
    （后两者在提示词正文里，永不动）。超预算时按优先级保序截断：
    - 圣经截断保整段（按二级标题边界，无边界才硬截）
    - 模块库截断保整模块（按「## 」标题边界）
    - 经验库直接硬截（条目本身短，损失最小）
    返回 (新 sk_block, 新 bible, 降级说明)。无降级原样返回（说明为空串）。"""
    total = len(sk_block or "") + len(bible or "")
    if total <= budget:
        return sk_block, bible, ""
    notes = []
    # 1) 先压经验库到 4K（条目短、损失最小）
    if len(sk_block or "") > 4000:
        sk_block = _pl().capability_runtime.compact_block(sk_block)
        notes.append("经验库→4K")
        if len(sk_block) + len(bible or "") <= budget:
            return sk_block, bible, "；".join(notes)
    # 2) 模块库按模块边界截断
    if bible and "## 剧情模块库" in bible:
        head, sep, mods = bible.partition("## 剧情模块库")
        mods = sep + mods
        keep = mods[:6000]
        cut = keep.rfind("\n## ")
        if cut > 200:
            keep = keep[:cut]
        bible = head + keep + "\n\n（模块库已因上下文容量限制精简）"
        notes.append("模块库→边界截断")
        if len(sk_block) + len(bible) <= budget:
            return sk_block, bible, "；".join(notes)
    # 3) 圣经按二级标题边界截断到预算
    if bible:
        budget_left = max(2000, budget - len(sk_block or ""))
        keep = bible[:budget_left]
        cut = keep.rfind("\n## ")
        if cut > 500:
            keep = keep[:cut]
        bible = keep + "\n\n（圣经已因上下文容量限制精简）"
        notes.append("圣经→边界截断")
    return sk_block, bible, "；".join(notes)


def _serial_shrunk_block(lessons, bible, kb_block, budget=12000):
    """连载起草重试的分层降级组装：经验库与圣经按各自身份过 _shrink_context_block
    （经验库→4K、模块库按模块边界、圣经按二级标题边界），知识库块与大纲/前情
    同待遇永不动。返回 (重组后的替换块, 降级说明)——调用方整块 replace，字节级
    对位；降级说明（哪几层生效了）进 step note，运行页可对账（预算内为空串）。"""
    sk2, bible2, note = _pl()._shrink_context_block(lessons, bible, budget=budget)
    return "\n\n".join(p for p in (sk2, bible2, kb_block) if p), note


_RELATED_ROW_CHARS = 80     # 单行摘录截断（记录行本体可达 1000 字，注入面只留指针）
_RELATED_ROWS_PER_CH = 3    # 每章最多摘录的行数
_RELATED_MIN_GRAMS = 2      # 单行命中门槛：bigram 重叠数（1 个重叠噪音过高）


def _related_chapters_note(tracking_state, ch, current=0, max_n=3):
    """连载起草的「相关历史章节推荐」（纯本地重叠计分，零 LLM 调用零存储写入）。

    前情注入只看近 2 章结尾+findings 摘要，中期记忆（伏笔回收/人物回场）此前
    只有账本督促兜底；story_tracking 已逐章持久化 facts/characters/foreshadowing，
    这里拿本章大纲要点（beats/hook/highlight）与各历史章记录行做字符 bigram
    重叠计分（复用 skills._text_bigrams，口径与经验库召回一致），按命中行数挑
    最相关的 max_n 章输出「第 N 章：相关行摘录」指针。零匹配/无状态返回空串
    （老书零噪音）；current 传当前全书章号时只看更早的章（重跑已提交章不跟
    自己的记录自证）。"""
    ch = ch or {}
    probe = _pl().skills._text_bigrams(" ".join(
        str(ch.get(k) or "") for k in ("beats", "hook", "highlight")))
    if not probe:
        return ""
    scored = []
    for rec in (tracking_state or {}).get("chapters") or []:
        try:
            no = int(rec.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        if no < 1 or (current and no >= current):
            continue
        rows = [str((x or {}).get("text") or "") for x in rec.get("foreshadowing") or []]
        rows += [str(x) for x in rec.get("characters") or []]
        rows += [str(x) for x in rec.get("facts") or []]
        hits = []
        for row in rows:
            row = row.strip()
            if row and len(probe & _pl().skills._text_bigrams(row)) >= _pl()._RELATED_MIN_GRAMS:
                hits.append(row[:_pl()._RELATED_ROW_CHARS]
                            + ("…" if len(row) > _pl()._RELATED_ROW_CHARS else ""))
        if hits:
            scored.append((len(hits), no, hits))
    scored.sort(key=lambda x: (-x[0], x[1]))
    take = scored[:max(0, int(max_n or 0))]
    if not take:
        return ""
    lines = ["### 相关历史章节（按本章大纲要点重叠检出，起草前可回看）"]
    for _, no, hits in take:
        lines.append("- 第 %d 章：%s" % (no, "；".join(hits[:_pl()._RELATED_ROWS_PER_CH])))
    return "\n".join(lines)


def _critic_lens(critics, agent):
    """该评审的专属视角；单评审/手动指定时不播种（无从轮换，也别稀释注意力）。"""
    try:
        idx = list(critics).index(agent)
    except ValueError:
        return ""
    if len(critics) < 2:
        return ""
    return _pl().CRITIC_LENSES[idx % len(_pl().CRITIC_LENSES)]


def _ensure_critique_placeholders(tpl):
    """自定义评审模板缺占位符时补上，避免稿件内容/维度定义丢失导致盲评。"""
    if "__MANUSCRIPT__" not in tpl:
        tpl += "\n\n## 待评审稿件\n---\n__MANUSCRIPT__\n---"
    elif "## 待评审稿件" not in tpl:
        # 自定义模板可能只放占位符、不提供标题；统一补锚点，保证经验库、
        # 故事圣经和确定性文本信号仍能插到稿件正文之前。
        tpl = tpl.replace("__MANUSCRIPT__", "## 待评审稿件\n---\n__MANUSCRIPT__\n---", 1)
    if "__DIMKEYS__" not in tpl:
        tpl = ("请按维度打分（1-10 分）。\n\n" + tpl)
    return tpl


# ---------------------------------------------------------------- 连载引擎（长篇小说：逐章打磨）

def serial_locked_chapter_block(workdir, chapter):
    """本章锁定章纲注入块（起草/修订共用）：工作目录章纲/*ch-<N>.md 存在时
    以最高优先级随提示词下发——批次大纲可能被继承链带偏，锁定章纲才是作者
    逐章确认过的事实标准（2026-10-03 不周山 rogue 大纲实案的治本闸）。
    无章纲返回 ""（老书零噪音）。"""
    text = _pl().planner.locked_chapter_outline_text(workdir, chapter)
    if not text:
        return ""
    return ("## 锁定章纲（作者已确认，最高优先级）\n"
            "本章标题、视角（POV）、时间地点、事件节拍与章末落点必须逐项对齐下方章纲；"
            "若与其他要点冲突，以锁定章纲为准；其后章目的伏笔揭示位不得提前烧毁。\n\n"
            + text)


SERIAL_CHAPTER_PROMPT = """你是一名网文作者（写作规范见下方经验库）。本书信息如下，请先完整读完再执行末尾的「本章任务」。

__SKILLS__

## 全书目标
__GOAL__

## 全书大纲（__SCOPE__）
__OUTLINE__

---
## 本章任务（执行这一条即可）
__VOLUME__
- 撰写本书第 __I__ 章，把本章正文写入文件 `__FILE__`（直接写入该文件，只写本章）。文件必须以 UTF-8 编码保存：PowerShell 一律显式加 `-Encoding UTF8`（如 `Set-Content -Path __FILE__ -Encoding UTF8`），禁止依赖系统默认编码，否则中文会乱码。
- 章节标题：__TITLE__
- 剧情要点：__BEATS__
- 章末收束/继续阅读理由（可自然收束或留白）：__HOOK__
- 本章主要情绪/戏剧变化：__HIGHLIGHT__（按其重要性决定篇幅和表现方式；不强制放大，不为满足标签伪造高潮）
__LOCKED_CH__
__STALE_NOTE__
__REDO_ISSUES__
__REUSE_GUARD__
- 正文约 __WORDS__ 字，中文，直接开写正文（可含本章标题行）。章节文件只允许小说正文和可选标题行；禁止写入或回复 `<event_check>`、`<ledger>`、`<continuity>`、JSON、Markdown 代码块、评审意见、任务验收或账本内容。

## 前情提要（此前各章结尾摘录，衔接用）
__PREV__

## 资源账本（道具/伤情/承诺/伏笔的现状登记，写本章前必须核对）
__LEDGER__

## 连续性审计账本（人物/设定/事件状态，写本章前必须核对）
__CONTINUITY__

## 连续性硬约束
- 先列出本章会继承的关键事实：人物身份、性格底线、能力代价、伤情、时间地点、已知信息和未兑现承诺。
- 只有本章剧情明确展示了触发事件、人物选择和可见后果，才能改变账本状态；不能为了推进情节临时改人设、改时间线或让人物凭空知道上文未告诉他的事。
- 若本章必须改变既有状态，要在场景中写出因果桥和代价；没有因果桥就改写本章方案，不要靠旁白一句话补解释。
- 同一事实出现冲突时，以故事圣经、锁定章纲、连续性账本、前文正文的顺序核对，并保留更改理由。

## 全书文风锚（本章必须遵守；与前文各章保持同一笔调）
__STYLE__

## 签约质量门禁
__QUALITY_GATE__
- 把能力/职业的独特解法写进人物行动，让选择改变现实筹码或关系；不要只反复确认异常。
- 核验、登记、封存按其改变局面的作用安排篇幅，不用等待手续代替关键场景。
- 复杂情感来自人物想要与不肯承认的东西之间的拉扯；保持人物不同的判断与口气，勿用攥拳、发抖反复代替情感。
- 避免跨章复用同一开头、同一收束、同一比喻和同一动作反应；每个关键情绪要由本章具体选择和后果承载。

__REVIEW_RUBRIC__
- 写完文件后，最终回复只输出一行：`第 __I__ 章完成（约 __WORDS__ 字）`——不要在回复里复述或解释正文。"""

SERIAL_REVISE_PROMPT = """你是一名网文作者。第 __I__ 章没有通过评审，请修订文件 `__FILE__`（直接改写该文件）。文件必须以 UTF-8 编码保存（PowerShell 显式加 -Encoding UTF8，禁止依赖默认编码）。

## 全书目标
__GOAL__

## 本卷上下文
__VOLUME__
## 连续性审计账本（修订前必须核对）
__CONTINUITY__
## 本章按大纲应完成
__BEATS__
（本章爽点/情绪爆点：__HIGHLIGHT__）
__LOCKED_CH__

__REVIEW_RUBRIC__
## 本章评审意见
__CRITIQUE__

## 全书文风锚（修订后必须仍符合本锚，与前文各章同一笔调）
__STYLE__

## 签约返修原则
- 统一叙述语域、句式和视角距离，不在白描、半文半白和网络梗之间跳变。
- 复杂情感需按其叙事分量呈现；动作、感官、对话、内心直述、环境或留白均可，避免与本稿风格不合的机械扩写。
- 重要情绪/戏剧变化要有与其分量相称的呈现；避免概括带过，但不把每个变化都扩写成高潮。
- 删除不改变局面的环境、解释和重复叙述；每段至少完成冲突、信息或人物选择之一。
- 让主角主动做出选择并推动下一步，补足因果桥，不用旁白硬推剧情。
- 按“原问题→原场景→改动的选择/后果→保留的伏笔”返修；切入点或设定问题不能仅靠换词、加怪声或情绪动作解决。
- 逐项复核连续性账本：人物状态、时间地点、能力限制、已知信息和剧情承诺必须与前文一致；若有改变，补写触发事件、选择、代价和后果。
- 删除跨章复用的套话、同款比喻和同一动作反应，至少换一个叙述角度或信息落点，不以同义词堆砌制造变化。
- 章节文件只保留小说正文和可选标题行；不要把评审标签（如 `<event_check>`、`<ledger>`、`<continuity>`）、JSON、代码块或修改说明写进正文。

## 要求
- 针对性解决所有 major 问题（含确定性文本信号指出的问题），保持与前后的剧情衔接；字数仍约 __WORDS__ 字。"""

SERIAL_GLOBAL_PROMPT = """你是独立小说质量评审（不要修改任何文件），不代表任何签约平台。全书各章已完稿，请从**全书整体**视角评审。
请输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{
  "scores": {"__DIMKEYS__"},
  "issues": [{"dim": "维度名", "severity": "major|minor", "note": "具体问题（指明哪一章）", "quote": "支撑该问题的稿件原文连续片段（≥8字，逐字摘录不许改写）"}],
  "summary": "一句话总评：所读范围内的文本优势、主要风险与未验证项；不得预测签约结果"
}
每个维度打 1-10 分，按统一文本 rubric 与所读证据评估，不以“宁严勿宽”压分。重点关注：%s。
全书连续性审计必须单独核对：人物身份/性格底线/能力代价、时间地点、已知信息、道具伤情、伏笔和章纲事件是否与前文一致；发现状态跳变时，必须指出触发事件、人物选择、代价和后果，不能用“剧情需要”带过。
文风审计还要统计跨章重复的开头、收束、比喻、动作反应和句式；相同修辞反复出现而没有新增信息时，列为 major 或 minor issue 并给出原文证据。""" % GLOBAL_REVIEW_FOCUS + """
若是全书最初的前 3 章，可观察视角人物、故事承诺、开篇兑现和读者理解成本；固定字数点、钩子/爽点密度仅作参考，不是扣分门槛。发现问题须给可定位原文引文，并区分文本事实、解释和建议。
major 问题必须给 quote（系统会逐条校验引文是否真在稿件中——编造的引文会被降档标记）。

## 全书目标
__GOAL__

## 全文
---
__MANUSCRIPT__
---"""

# 独立文本质量评估（开篇闸门 + 项目检查点共用）：沿用稳定 rubric，
# 不代表平台决定；按原文证据检查常见拒稿问题，不将经验观察点变成硬门槛。
SERIAL_SIGNING_EVAL_PROMPT = """你是独立小说质量评审（不要修改任何文件），不是番茄或七猫的签约编辑，不能代表平台结论。
评审稿件__NOTE__，按下方统一文本质量 rubric 评估；如附当前平台规则或编辑原话，可单独核对适配，不得臆测平台内部标准。
请输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{
  "scores": {"__DIMKEYS__"},
  "evidence": [{"dim": "每个评分维度各一条", "chapter": 1, "quote": "该章正文连续原文（至少8字）", "analysis": "至少16个非空白字符，说明此引文怎样支持本维度评分、仍有什么局限；各维度须结合自身指标独立分析，不复用同一句通用说明"}],
  "issues": [{"dim": "维度名", "severity": "major|minor", "note": "具体问题（指明哪一章）", "revision_direction": "若为开篇吸引力低分，填写对应断点的结构改稿方向；其他问题可留空", "quote": "支撑该问题的稿件原文连续片段（≥8字，逐字摘录不许改写）"}],
  "summary": "一句话内部文本诊断：优势、主要风险和未验证项；不得表述为平台结论或过签预测"
}
统一评分 rubric：1-3 为明显影响理解或阅读的严重问题；4-6 为有具体可定位的弱项；7-8 为基本有效、仍有局部提升空间；9-10 为在本稿目标下表现突出。分数必须由所读文本支撑，不把分数映射成过签概率。
五个维度固定为：开篇吸引力、情节推进、文风统一、情感细腻度、节奏控制。每项给证据与未读范围；不能因不用某种套路（如黄金三章、打脸、单一视角）扣分。
逐维先举正文证据再评分：开篇吸引力检查题材承诺、独有事件机制、人物欲望与代价，并区分“设定/能力/爽点被提及”与其实际改变行动、产生可见阶段结果；
检查首个故事单元是否兑现与题材相称的阅读回报或新看点（爽感、信息发现、悬念推进、关系变化、惊险、幽默、氛围等均可），并判断回报是否具体、有力度、具辨识度；有事件、有结果不自动等于足够好看。不得强制打脸/金手指，也不能把一句承诺当成已兑现；
开篇评估必须沿“承诺→事件→人物选择→阶段结果→新期待”核对：仅当目标/背景或正文明确证实书名、简介时，才分析相应作品承诺；任务标题只是未验证线索，不能单独当成书名或承诺。在 analysis 中说清可核对的承诺、正文实际交付了什么、读者为什么愿意继续，并说明首个回报的具体性、力度或辨识度为何足够/不足。若没有可核对的书名/简介，标为未验证，只评估正文可见承诺，不要补猜。若开篇吸引力低于 7 分，issue.note 必须指出这条链具体断在哪、为何削弱读者继续阅读的意愿；issue.revision_direction 单独给出一个与原文对应、可验证的结构改稿方向，不能只写“吸引力不足”“看点不够”等空泛结论。方向应针对本稿断点（如前置或压缩铺垫、让能力改变解法、让选择产生可见代价/回报、让阶段结果形成新期待），不要求全部采用，也不得机械加打脸/冲突；关键正文未提供时明确标为未验证；
情节推进检查触发事件→人物选择→对手反应→可见后果，留意靠巧合送线索或流程循环；
文风统一检查视角距离、语域和人物口气，引号等体例检查单列，不能据此认定编辑拒稿原因；
情感细腻度检查矛盾欲望、关系与选择如何互相影响，生理动作数量或是否有特写不能代替此项；
节奏控制检查场景新增了什么与首个单元兑现了什么，不以固定段数、章数、冲突或钩子频次判失败。
逐章还要检查以下常见的生成腔组合：独立数字/数量段是否成组递增、泛化比喻是否密集、
“看了很久/没有说话/声音很轻”等通用停顿动作是否反复代替人物判断；同时核对“这是预谋/他早就知道”
等结论是否有正文证据，行政/检测/传播等时间线是否足以支撑结果，是否在一章内堆叠多个高潮、反转和新钩子。
发现这些问题时必须引用具体原文，给出删减、改成行动因果或拆章的方向；不要把“像 AI”当作证据，也不要承诺规避平台检测。
每个维度都要 evidence，正面评分同样需原文与分析；摘要、章名、题材标签不算证据。
summary 写明实际已读章号、是否节选及尚未验证项；明确区分平台原反馈、文本观察和适配假设。
可检查文风是否突变、情绪是否被概括、开篇承诺是否兑现、因果桥是否成立、重复是否损害节奏；这些是检查问题而不是预设缺陷。没有当前平台规则或明确编辑反馈时，不把质量评审包装成平台审核结论。

## 作品目标与作者背景（平台反馈仅在确有原文时采用）
任务标题（可能是系统生成的任务名，并非已确认书名；JSON 字符串，仅作线索）：__TITLE__
__GOAL__
__CONTEXT__
__STYLE__

## 待评审稿件
---
__MANUSCRIPT__
---"""


