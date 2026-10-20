# -*- coding: utf-8 -*-
"""Deterministic quality checks for signing-oriented Chinese web fiction.

The LLM remains responsible for literary judgment.  This module supplies
stable rubric defaults and small text signals so reviewers can cite evidence
for the common rejection reasons instead of relying on vague impressions.
"""
from __future__ import annotations

import re
import hashlib
import math
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Mapping, Optional


SIGNING_RUBRIC = [
    "开篇吸引力",
    "情节推进",
    "文风统一",
    "情感细腻度",
    "节奏控制",
]

OPENING_DIM = "开篇吸引力"
SIGNING_CHECKPOINT_WORDS = 20000   # 项目检查点默认值；不是通用平台规则，调用方应核对当前后台
_FEEDBACK_ID_INLINE_LIMIT = 200


def feedback_record_key(value: Any) -> str:
    """Return a stable prompt-safe key while retaining exact-ID compatibility."""
    text = str(value or "")
    if len(text) <= _FEEDBACK_ID_INLINE_LIMIT:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return "feedback-id:" + digest


def _positive_chapter_number(value: Any) -> Optional[int]:
    """Parse a chapter number without silently truncating floats or booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        try:
            number = int(value.strip())
        except (ValueError, OverflowError):
            return None
        return number if number > 0 else None
    return None


def _chapter_text_map(chapters: Optional[Mapping[int, str]]) -> dict[int, str]:
    """Normalize chapter mappings restored from JSON/checkpoint storage."""
    if not isinstance(chapters, Mapping):
        return {}
    normalized = {}
    for key, text in chapters.items():
        chapter = _positive_chapter_number(key)
        # Persisted chapter maps are untrusted JSON.  Coercing a list/dict to
        # ``str`` could let a fabricated representation satisfy quote checks.
        if (chapter is not None and chapter not in normalized and
                isinstance(text, str)):
            normalized[chapter] = text
    return normalized


def _feedback_has_unresolved_conclusion(analysis: str) -> bool:
    """Catch explicit current defects without diagnosing free-form literary truth.

    Preserve clause boundaries: a repair can describe the old defect, negate
    it, or leave a character's question unanswered without contradicting the
    review status. Only an explicit current review conclusion is actionable.
    """
    patterns = (
        r"(?:主要|核心|原|该)?问题(?:仍然|依旧|仍|尚|还)?"
        r"(?:尚未|仍未|未能|未被|没有|未)"
        r"(?:(?:被)|(?:得到)|(?:获得)|(?:予以))?"
        r"(?:解决|处理|改善|改变)",
        r"(?:主要|核心|原|该)?问题(?:仍然|依旧)(?:存在)?$",
        r"(?:主要|核心|原|该)?问题(?:仍然|依旧|仍|尚|还)?"
        r"(?:悬而未决|没有着落|未有定论)",
        r"(?:主要|核心|原|该)?问题(?:仍然|依旧|仍|尚|还)?"
        r"(?:只|仅)解决了?一半",
        r"^(?:(?:现稿|新版|修订后|修改后|当前|目前|本章|正文|该段)"
        r"(?:中|里)?(?:仍然|依旧|仍|尚|还)?|(?:仍然|依旧|仍|尚|还))?"
        r"(?:缺少|没有|未(?:形成|兑现|说明|给出|补足|解决))"
        r"(?:明确|具体)?(?:结果|后果|行动|回报|变化|线索|目标|答案|问题|"
        r"代价|动机|依据|铺垫|冲突|解释|信息|细节)",
        r"^(?:(?:现稿|新版|修订后|修改后|当前|目前|本章|正文|该段)"
        r"(?:中|里)?(?:仍然|依旧|仍|尚|还)?|(?:仍然|依旧|仍|尚|还))"
        r"(?:需|需要)(?:补出|补充|补足)(?:明确|具体)?"
        r"(?:结果|后果|行动|回报|变化|线索|目标|答案|问题|代价|动机|"
        r"依据|铺垫|冲突|解释|信息|细节)",
        r"^(?:现稿|正文|当前|目前)?(?:仍然|依旧|仍)存在问题",
        r"^(?:现稿|正文|当前|目前)?(?:尚未|仍未|没有|未)"
        r"(?:解决|处理|改善)(?:主要|核心)?问题",
        r"^(?:(?:现稿|新版|修订后|修改后|当前|目前|本章|正文|该段)"
        r"(?:中|里)?|)(?:人物(?:设定|动机|行为)|角色(?:设定|动机|行为)|主角行为|"
        r"情节(?:走向|安排)|逻辑(?:链条)?|剧情逻辑(?:链条)?|句式|用词|行文(?:用词)?|表达|修辞(?:手法)?|"
        r"节奏|文风|开篇(?:切入点)?|看点|爽点|主线|信息(?:交代)?|开头)"
        r"(?:到目前为止|目前|至今)?(?:仍然|仍旧|依然|依旧|仍|尚|还|还是|迟迟|一直|始终)?"
        r"(?:不自洽|不一致|冲突|混乱|前后矛盾|前后对不上|不合理|不合逻辑|不通|"
        r"重复雷同|过于重复|重复|单调|缺乏变化|缺乏层次|缺乏(?:足够)?吸引力|"
        r"没有(?:足够的?)?吸引力|吸引力不足|不够吸引|不够抓人|抓不住读者|"
        r"没(?:有)?(?:讲清楚|交代清楚)|没(?:有)?推进|未推进|没有兑现|未兑现|"
        r"交代不足|交代不清|缺少动机|缺乏动机|没有层次|单一|有问题|不足|不够|平淡)",
        r"^(?:故事)?(?:开篇(?:的)?(?:切入点)?|开头)(?:仍然|依然|依旧|仍|尚|还)?"
        r"(?:缺乏|缺少|没有|没|不够|不足|抓不住|抓不牢)"
        r"(?:足够(?:的)?吸引力|吸引力|吸引读者|读者|爽点|看点|抓人(?:的)?切入点)"
        r"|^(?:爽点|看点)(?:有些)?(?:不够|不足)"
        r"|^(?:(?:现稿|新稿|新版|新版本|当前|目前|开篇|故事开头)(?:中|里)?(?:的)?(?:开篇|开头|切入点)?(?:仍然|仍旧|依然|依旧|仍|尚|还)?)?"
        r"(?:看不出吸引力|不具备吸引力|不足以吸引读者|没能吸引读者)"
        r"|^(?:(?:现稿|新版|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:句式|用词|修辞(?:手法)?|行文|表达)"
        r"(?:(?:、|与|和)(?:句式|用词|修辞(?:手法)?|行文|表达))*"
        r"(?:仍然|依然|依旧|仍|尚|还|还是)?(?:重复雷同|过于重复|重复|单调|缺乏层次|缺乏变化)"
        r"|^(?:(?:人物设定|情节走向|情节安排|逻辑|前文|上文|表述)"
        r"(?:(?:、|与|和)[^，,。；;：:！？!?]{1,24})?"
        r"(?:仍然|依然|依旧|仍|尚|还)?(?:相互)?(?:冲突|不一致|矛盾))",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:到目前为止|目前|至今)?(?:仍然|仍旧|依然|依旧|仍|尚|还)?"
        r"(?:存在)?(?:前后矛盾|前后对不上|不自洽|逻辑不通)",
        r"^(?:仍然|依旧|仍|尚|还)?与(?:上文|前文|设定|表述|自身设定)"
        r"[^，,。；;：:！？!?]{0,24}(?:冲突|不一致|矛盾)",
        r"^(?:(?:现稿|新版|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:没有|没|尚未|未能|未)讲清楚(?:具体)?(?:原因|动机|信息|来由|缘由)?",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:仍然|依然|依旧|仍|尚|还|还是)?(?:缺乏层次(?:与变化)?|缺乏变化|重复雷同|"
        r"看点(?:有些)?不足|爽点(?:有些)?不足|吸引力不足|不够吸引|不够抓人)",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:角色|人物)(?:动机|行为|设定)?(?:仍然|依然|依旧|仍|尚|还)?"
        r"(?:牵强|生硬|突兀|不可信|站不住脚|前后不一|不合理)",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:剧情|情节)(?:前后)?(?:衔接不上|接不上|断裂|跳脱|转折突兀|缺少因果|因果不通)",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:叙事)?视角(?:混乱|跳脱|不稳|来回跳|频繁切换)",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:行文|句式|表达)(?:仍然|依然|依旧|仍|尚|还)?"
        r"(?:机械|像(?:同一个|一个)?模子(?:刻出来)?|一个模子|套路化)",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:节奏)(?:仍然|依然|依旧|仍|尚|还)?(?:拖沓|迟缓|失衡|过慢)",
        r"^(?:(?:现稿|新版|新版本|新稿|当前|目前|本章|正文|该段)(?:中|里)?)?"
        r"(?:开篇|开头)(?:仍然|依然|依旧|仍|尚|还)?"
        r"(?:平淡|乏味|铺垫过长|缺少(?:钩子|悬念|冲突|看点|即时回报))",
    )
    # Commas often enumerate several defects under one subject (especially
    # "旧稿……，缺乏层次……"). Keep that scope until an explicit current-draft
    # subject appears; otherwise a repaired historical defect can be mistaken
    # for a defect in the revision.
    current_scope = re.compile(
        r"(?:现稿|新版|新版本|新稿|修订后|修改后|修改之后|改写后|改完后|改完之后|改稿后|当前|目前|"
        r"本次修改后|本章|正文|该段|这版|现在|如今)")
    # Semicolons commonly continue an enumerated diagnosis, so preserve the
    # historical scope across them but begin a fresh scope at sentence ends.
    major_clauses = re.split(r"[。！？!?\n]|(?:不过|但是|然而|但|却)", analysis)
    for major_clause in major_clauses:
        historical_scope = bool(re.match(
            r"\s*(?:(?:针对|关于|对于|回看|对照)\s*)?(?:原稿|旧稿|原版|修改前|改写前|此前|原先|原本)",
            major_clause))
        for clause in re.split(r"[，,、；;]", major_clause):
            clause = re.sub(r"\s+", "", clause)
            if current_scope.search(clause):
                historical_scope = False
            if historical_scope:
                continue
            if re.search(r"(?:吗|么|呢)$", clause):
                continue
            if re.match(r"(?:(?:针对|关于|对于|回看|对照))?(?:原稿|旧稿|原版|修改前|改写前|此前|原先|原本)", clause):
                historical_scope = True
                continue
            if re.match(r"(?:如果|假如|假设|倘若|一旦|若)", clause):
                continue
            for pattern in patterns:
                for match in re.finditer(pattern, clause):
                    prefix = clause[:match.start()]
                    # The matched defect can be the object of a completed repair
                    # or an explicit negation. Check its local context rather
                    # than enumerating all possible reviewer subjects.
                    if re.search(r"(?:不是|并非|并不是|并没有|并未)$|"
                                 r"不再(?:存在|有)?$|已(?:经)?(?:消除|解决|修正|处理)了?$|"
                                 r"(?:消除|解决|修正|处理)了$", prefix):
                        continue
                    if re.match(r"的(?:旧稿|原稿|原版|缺陷)", clause[match.end():]):
                        continue
                    return True
    return False


def _feedback_analysis_is_substantive(status: str, analysis: str) -> bool:
    """Reject placeholder or repeated feedback analyses without dictating prose."""
    compact = re.sub(r"[\s\u3000，,。；;：:！？!?、（）()\[\]{}\"'‘’“”]", "",
                     str(analysis or "")).casefold()
    if len(compact) < 8:
        return False
    # This workflow evaluates Chinese manuscript evidence.  A numeric token,
    # identifier, or bare English placeholder can meet the length check while
    # saying nothing about the cited passage.
    if not re.search(r"[\u4e00-\u9fff]", compact):
        return False
    # A repeated token can satisfy a character count without explaining the
    # manuscript.  Catch both exact periodic filler and common status-only text.
    for width in range(1, len(compact) // 2 + 1):
        if len(compact) % width == 0:
            unit = compact[:width]
            if unit * (len(compact) // width) == compact:
                return False
    repeated_placeholders = (
        "已处理", "已完成", "不适用", "不相关", "仍未解决", "正文证据充分",
        "没有问题", "已核验", "done", "notapplicable", "irrelevant",
    )
    if any(compact.count(token) >= 2 for token in repeated_placeholders):
        return False
    # Reject a status word followed only by a generic disclaimer, while still
    # allowing natural explanations with concrete nouns and actions.
    if re.search(r"(?:已处理|已完成|不适用|不相关|仍未解决|已核验)"
                 r"(?:但|因为|且|，|,)?"
                 r"(?:细节(?:内容)?尚未说明|具体细节未知|情况不明|暂无说明|不清楚原因)$",
                 compact):
        return False
    generic_status_tails = (
        "相关情况需要后续核对", "正文部分仍需继续观察", "后续再看",
        "具体原因需要进一步核对", "暂时无法判断", "相关内容需要后续核对",
        "具体情况需核对", "暂无进一步说明", "等待后续处理",
        "和本段无关", "内容没有变化", "后续核对", "继续观察", "进一步核对",
    )
    status_prefixes = ("已处理", "已完成", "处理完成", "仍未解决", "问题依旧",
                       "不相关", "不适用")
    if any(compact.startswith(prefix) and compact[len(prefix):].lstrip("因为但且") in tails
           for prefix in status_prefixes for tails in generic_status_tails):
        return False
    # Catch natural-language variants whose tail is still only a promise to
    # inspect later, rather than a manuscript-grounded explanation.
    generic_analysis_patterns = (
        r"(?:已处理|已完成|处理完成)(?:但|因为|且)?(?:具体情况(?:需|需要)(?:进一步)?(?:核对|了解)|"
        r"后面再补充|(?:细节|具体内容)(?:尚需|需要|尚未)?(?:确认|说明|待定)|"
        r"正文(?:部分)?仍需继续观察|暂无进一步说明|内容没有变化|此处暂不展开)",
        r"(?:已处理|已完成|处理完成)(?:相关|对应|本项|该问题)?"
        r"(?:处理|调整|修改|改动|工作)(?:(?:已)?完成)?",
        r"(?:仍未解决|问题依旧)(?:等待后续处理|稍后再处理|后续再看|需要继续跟进)",
        r"(?:不适用|不相关)(?:因为)?(?:暂时无法判断|原因暂不明确|"
        r"和(?:本段|这条反馈)没有关系|当前段落不涉及|相关内容需要后续核对)",
        r"正文有变化但此处暂不展开",
        r"(?:这个|该|相关)?问题(?:仍|尚)?未解决",
        r"(?:仍然|依旧)存在问题|(?:暂时|目前)没有结果|(?:还|仍需|需要)后续(?:处理|跟进)",
        r"(?:这条(?:反馈|意见)|该(?:反馈|意见))(?:与|和)(?:本段|当前内容|这段|当前段落)(?:没有关系|无关)",
        r"(?:当前|本段|此处)(?:不涉及|与(?:当前内容|本段)无关)(?:该问题|相关(?:内容|问题)|这条反馈)?",
        r"(?:这条(?:反馈|意见))(?:不适用于|不适合)(?:本章|当前段落|此处)",
    )
    if any(re.fullmatch(pattern, compact) for pattern in generic_analysis_patterns):
        return False
    # The status is a claim about the current manuscript, so reject explicit
    # explanations that say the opposite. Keep this deliberately narrow: a
    # completed repair to an old draft can coexist with an unresolved issue in
    # the current draft.
    clauses = re.split(r"[，,；;。！？!?]", str(analysis or ""))
    conclusion_clauses = re.split(r"[。！？!?]|不过|但是|然而|但|却",
                                 str(analysis or ""))
    historical_clause = re.compile(
        r"^(?:(?:针对|关于|对于|回看|对照)\s*)?(?:原稿|旧稿|原版|修改前|改写前|此前|原先|原本)"
    )
    current_scope = re.compile(r"现稿|当前稿|修改后|改写后|本次修改|这次改稿")
    has_separate_unresolved_claim = False
    unresolved_scope_is_historical = False
    for conclusion_clause in conclusion_clauses:
        conclusion_clause = re.sub(r"\s+", "", conclusion_clause)
        if current_scope.search(conclusion_clause):
            unresolved_scope_is_historical = False
        elif historical_clause.search(conclusion_clause):
            unresolved_scope_is_historical = True
        if unresolved_scope_is_historical or re.match(
                r"(?:如果|假如|假设|倘若|一旦|若)", conclusion_clause):
            continue
        if re.search(r"(?:问题|缺陷|不足|动机|冲突|结果|后果|钩子|爽点)?"
                     r"(?:已经|已)(?:彻底|完全)?(?:解决|修复|补足|消除|改好)|"
                     r"(?:没有问题|不存在(?:逻辑)?冲突|前后保持一致)",
                     conclusion_clause):
            continue
        if (_feedback_has_unresolved_conclusion(conclusion_clause) or
                re.search(r"(?:仍然|依然|依旧|仍|尚|还)(?:与[^，,。；;！？!?]{0,12})?"
                          r"(?:冲突|不一致|不自洽|牵强|突兀|不合理|缺少|缺乏|不足)",
                          conclusion_clause)):
            has_separate_unresolved_claim = True
            break
    historical_scope = False
    for clause in clauses:
        clause = re.sub(r"\s+", "", clause)
        if not clause:
            continue
        if current_scope.search(clause):
            historical_scope = False
        elif historical_clause.search(clause):
            historical_scope = True
        if historical_scope:
            continue
        if status == "not_addressed" and re.search(
                r"(?<!未)(?<!尚未)(?<!没有)(?<!并未)(?<!不再)"
                r"(?:问题|缺陷|不足|动机|冲突|结果|后果|钩子|爽点)"
                r"(?:已经|已)(?:彻底|完全)?(?:解决|修复|补足|消除|改好)|"
                r"(?:已经|已)(?:彻底|完全)?(?:解决|修复|补足|消除|改好)(?:了)?"
                r"(?:该|这个|上述)?(?:问题|缺陷|不足|冲突|结果|后果)|"
                r"(?:没有问题|不存在(?:逻辑)?冲突|前后保持一致|动机已经补足)", clause) \
                and not has_separate_unresolved_claim:
            return False
        if status == "not_applicable" and re.search(
                r"(?<!不)(?<!非)(?:这条|该|本项)?(?:反馈|意见|问题)"
                r"(?:明确)?(?:适用于|适合于|涉及|针对)(?:本章|当前(?:段落|内容|情节)?|此处|本段)|"
                r"(?<!不)(?<!非)适用于(?:本章|当前(?:段落|内容|情节)?|此处|本段)", clause):
            return False
    if (str(status or "").strip() == "addressed" and
            _feedback_has_unresolved_conclusion(analysis)):
        return False
    # Status alone is not evidence.  Require at least one word that names the
    # claimed change, gap, or scope boundary, while leaving the actual prose
    # free-form for the reviewer.
    status_markers = {
        "addressed": r"改写|重写|改动|修改|改成|补写|补足|补充|补出|删除|删去|"
                     r"压缩|前置|调整|精简|拆分|合并|强化|改变|形成|呈现|产生|兑现|"
                     r"增加|完成|承担|付出|行动|选择|结果|后果|回报|线索|冲突|导致|使得",
        "not_addressed": r"缺少|缺乏|没有|未|仍|不足|停在|看不到|未形成|未兑现|"
                         r"未说明|未给|问题|尚未|无法|不成立",
        "not_applicable": r"不涉及|无关|不适用|不相关|本段|此处|当前段落|题材|"
                           r"背景|能力|场景|对象|并非|不符合",
    }
    marker_pattern = status_markers.get(str(status or "").strip())
    if marker_pattern and not re.search(marker_pattern, compact):
        return False
    # Three or more adjacent copies of a short word are another common way to
    # pad an otherwise empty analysis ("内容内容内容").
    for width in range(1, 5):
        if re.search(r"([\u4e00-\u9fff]{%d})\1\1" % width, compact):
            return False
    return True


def feedback_check_errors(review: Any, chapters: Mapping[int, str], records: list[dict],
                          require_all: bool = True) -> list[str]:
    """Require an explicit, manuscript-anchored response to each reused feedback case.

    This closes the gap between injecting a rejection record and actually testing
    the proposed fix. ``not_applicable`` is allowed only with a real quote and
    explanation; ``not_addressed`` is valid evidence of a defect, handled as a
    blocking issue by the caller so that revision can still run. Resumed
    checkpoints may set ``require_all=False`` when the original feedback
    chapter is outside the submitted scope.
    """
    if records is None:
        return []
    if not isinstance(records, list):
        return ["拒稿复盘记录格式无效"]
    if not records:
        return []
    chapters = _chapter_text_map(chapters)
    rows = review.get("feedback_check") if isinstance(review, dict) else None
    errors = []
    raw_record_ids = {item["id"] for item in records
                      if isinstance(item, dict)
                      and isinstance(item.get("id"), str)
                      and item["id"].strip()}
    for record in records:
        if not isinstance(record, dict):
            errors.append("拒稿复盘记录格式无效")
            continue
        raw_id = record.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append("拒稿复盘记录 id 无效")
            continue
        rid = raw_id.strip()
        chapter_error_added = False
        alias = feedback_record_key(rid)
        accepted_ids = {rid}
        # An alias must never shadow a real short ID.  In that rare collision
        # case callers can still submit the long original ID, while an alias
        # response fails closed as missing instead of being misattributed.
        if alias != rid and alias not in raw_record_ids:
            accepted_ids.add(alias)
        matching = [x for x in rows if isinstance(x, dict)
                    and isinstance(x.get("id"), str)
                    and x["id"].strip() in accepted_ids] if isinstance(rows, list) else []
        if not matching:
            if not require_all:
                continue
            errors.append("拒稿复盘 %s 缺少逐条处理结论" % rid)
            continue
        scoped = []
        invalid_chapter = False
        for candidate in matching:
            candidate_chapter = _positive_chapter_number(candidate.get("chapter"))
            if candidate_chapter is None:
                invalid_chapter = True
                continue
            if candidate_chapter in chapters:
                scoped.append(candidate)
        if invalid_chapter:
            errors.append("拒稿复盘 %s 的 chapter 无效" % rid)
            chapter_error_added = True
        if not scoped and not require_all and not invalid_chapter:
            continue
        if scoped:
            matching = scoped
        if len(matching) > 1:
            errors.append("拒稿复盘 %s 出现重复处理结论" % rid)
        row = matching[-1]
        status = str(row.get("status") or "").strip()
        if status not in ("addressed", "not_addressed", "not_applicable"):
            errors.append("拒稿复盘 %s 的 status 无效" % rid)
        chapter = _positive_chapter_number(row.get("chapter"))
        if chapter is None:
            if not chapter_error_added:
                errors.append("拒稿复盘 %s 的 chapter 无效" % rid)
            continue
        # A resumed checkpoint may not include the chapter where the original
        # feedback applies.  The current review cannot verify an unseen quote;
        # leave that record for the opening/full-scope review instead of
        # turning an out-of-scope row into a model failure.
        if chapter not in chapters and not require_all:
            continue
        text = chapters.get(chapter, "")
        text = "\n".join(line for line in text.splitlines()
                         if not re.match(r"^\s{0,3}#{1,6}\s", line))
        raw_quote = row.get("quote")
        raw_analysis = row.get("analysis")
        if not isinstance(raw_quote, str) or not isinstance(raw_analysis, str):
            errors.append("拒稿复盘 %s 的 quote/analysis 必须是文本" % rid)
            continue
        quote = raw_quote.strip()
        analysis = raw_analysis.strip()
        if len(re.sub(r"\s+", "", quote)) < 8 or quote not in text or not analysis:
            errors.append("拒稿复盘 %s 缺少可定位正文引文与处理分析" % rid)
            continue
        # A copied manuscript sentence plus a placeholder such as "已处理" or
        # "不适用" is not evidence that the rejection feedback was actually
        # checked. Require a short but substantive explanation for every
        # in-scope status; the prompt asks what changed, what remains limited,
        # or why the observation does not apply to the cited passage.
        if not _feedback_analysis_is_substantive(status, analysis):
            errors.append("拒稿复盘 %s 的处理分析过于简略或与 status 不匹配，须说明正文变化、未解决点或不适用理由" % rid)
    return errors


def feedback_check_issues(review: Any, records: list[dict],
                          chapters: Optional[Mapping[int, str]] = None) -> list[dict]:
    """Translate validated unresolved checks into normal, anchored repair work."""
    chapters = _chapter_text_map(chapters) if chapters is not None else None
    records = records if isinstance(records, list) else []
    exact_by_id = {r["id"].strip(): r for r in records
                   if isinstance(r, dict) and isinstance(r.get("id"), str)
                   and r["id"].strip()}
    alias_by_id = {}
    for record in records:
        if (not isinstance(record, dict) or
                not isinstance(record.get("id"), str) or
                not record["id"].strip()):
            continue
        record_id = record["id"].strip()
        alias = feedback_record_key(record_id)
        if alias != record_id and alias not in exact_by_id:
            alias_by_id.setdefault(alias, record)
    out = []
    raw_rows = review.get("feedback_check") if isinstance(review, dict) else None
    rows = raw_rows if isinstance(raw_rows, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str):
            continue
        row_id = row_id.strip()
        record = exact_by_id.get(row_id) or alias_by_id.get(row_id)
        if record is None:
            continue
        if str(row.get("status") or "").strip() != "not_addressed":
            continue
        chapter = _positive_chapter_number(row.get("chapter"))
        if chapters is not None and (chapter is None or chapter not in chapters):
            continue
        quote = row.get("quote")
        analysis = row.get("analysis")
        if (chapter is None or not isinstance(quote, str)
                or not isinstance(analysis, str)
                or not quote.strip() or not analysis.strip()):
            continue  # feedback_check_errors owns malformed-row diagnostics
        direction = str(record.get("experiment") or "").strip()
        if not direction:
            direction = str(record.get("recheck_question") or "").strip()
        out.append({"dim": "拒稿复盘", "severity": "major", "chapter": chapter,
                    "quote": quote,
                    "revision_direction": direction,
                    "_feedback_issue": True,
                    "note": "[%s] 原问题仍未处理：%s" % (row["id"], analysis)})
    return out


def merge_feedback_checks(rows: Any) -> list[dict]:
    """Merge multi-reviewer feedback checks conservatively and deterministically.

    A single ``addressed`` verdict must not hide another reviewer's
    ``not_addressed`` finding.  Prefer unresolved evidence, then preserve the
    first complete row for stable report output.
    """
    priority = {"not_addressed": 0, "addressed": 1, "not_applicable": 2}

    def evidence_quality(row):
        """Prefer a usable manuscript anchor when statuses are equivalent."""
        chapter_ok = _positive_chapter_number(row.get("chapter")) is not None
        quote = str(row.get("quote") or "").strip()
        analysis_ok = bool(str(row.get("analysis") or "").strip())
        quote_ok = len(re.sub(r"\s+", "", quote)) >= 8
        return int(chapter_ok) + int(quote_ok) + int(analysis_ok)

    merged: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict) or not row.get("id"):
            continue
        rid = str(row["id"])
        status = str(row.get("status") or "").strip()
        current = merged.get(rid)
        if current is None:
            merged[rid] = row
            continue
        old_status = str(current.get("status") or "").strip()
        if (priority.get(status, 99) < priority.get(old_status, 99)
                or (priority.get(status, 99) == priority.get(old_status, 99)
                    and evidence_quality(row) > evidence_quality(current))):
            merged[rid] = row
    return list(merged.values())


def review_evidence_errors(review: Any, chapters: Mapping[int, str]) -> list[str]:
    """Require a real chapter quote and an explanation for each scored dimension.

    This validates provenance only, never the literary truth of the judgement.
    ``chapters`` must contain exactly the excerpts sent to the reviewer.
    """
    review = review if isinstance(review, dict) else {}
    chapters = _chapter_text_map(chapters)
    rows = review.get("evidence")
    rows = rows if isinstance(rows, list) else []
    supported = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("dim") not in SIGNING_RUBRIC:
            continue
        chapter = _positive_chapter_number(row.get("chapter"))
        if chapter is None:
            continue
        text = chapters.get(chapter, "")
        text = "\n".join(line for line in text.splitlines()
                         if not re.match(r"^\s{0,3}#{1,6}\s", line))
        raw_quote = row.get("quote")
        raw_analysis = row.get("analysis")
        if not isinstance(raw_quote, str) or not isinstance(raw_analysis, str):
            continue
        quote = raw_quote.strip()
        if (len(re.sub(r"\s+", "", quote)) >= 8 and quote in text
                and raw_analysis.strip()):
            supported.add(row["dim"])
    errors = ["%s缺少可定位正文引文与分析" % dim
              for dim in SIGNING_RUBRIC if dim not in supported]

    # A low opening score should help the author find the broken beat, not just
    # label the chapter.  The prompt asks reviewers scoring below 7 to identify
    # where the promise→event→choice→result→expectation chain breaks.  Enforce
    # the minimum actionable shape here so a terse score-only review cannot
    # silently pass the gate.  High scores remain free of mandatory criticism.
    scores = review.get("scores") if isinstance(review.get("scores"), dict) else {}
    score_value = scores.get(OPENING_DIM)
    try:
        opening_score = float(score_value) if score_value is not None else None
    except (TypeError, ValueError, OverflowError):
        opening_score = None
    if opening_score is not None and opening_score < 7 and 1 in chapters:
        opening_text = "\n".join(
            line for line in chapters.get(1, "").splitlines()
            if not re.match(r"^\s{0,3}#{1,6}\s", line)
        )
        actionable = False
        issues = review.get("issues")
        for row in issues if isinstance(issues, list) else []:
            if not isinstance(row, dict) or row.get("dim") != OPENING_DIM:
                continue
            quote_value = row.get("quote")
            note_value = row.get("note")
            quote = quote_value.strip() if isinstance(quote_value, str) else ""
            note = note_value.strip() if isinstance(note_value, str) else ""
            has_chain_stage = bool(re.search(
                r"承诺|触发|事件|选择|人物|主角|行动|追问|目标|"
                r"阶段结果|可见后果|后果|结果|变化|线索|信息|悬念|冲突|"
                r"阅读|期待|回报|兑现|吸引力|因果|动机|场景",
                note,
            ))
            has_diagnosis = bool(re.search(
                r"没有|未能|未形成|缺少|尚未|停在|断在|未见|未给|不足|未兑现|"
                r"没(?:有|去|能|能再|再)|无(?:法|从|任何)|不(?:足|明|清|成立|匹配)|"
                r"落空|脱节|看不到|难以|未解释|未说明",
                note,
            ))
            direction_value = row.get("revision_direction")
            direction = (direction_value.strip()
                         if isinstance(direction_value, str) else "")
            has_revision_lever = bool(re.search(
                r"(?:(?:前置|压缩|补写|补出|补足|删去|合并|重写|改写|重排|展开|强化|具体化)"
                r".{0,24}(?:铺垫|事件|选择|目标|阻碍|代价|后果|回报|结果|线索|悬念|"
                r"冲突|行动|能力|设定|对手|动机|场景|信息))|"
                r"(?:让(?:主角|她|他|人物).{0,12}(?:选择|追查|拒绝|答应|承担|付出|"
                r"失去|获得|改变|揭露|阻止|交换|冒险|行动|对抗|做出))|"
                r"(?:让|使).{0,16}(?:能力|AI|情报|系统|线索).{0,12}"
                r"(?:改变|影响|推动|改写|左右|解决|扭转).{0,16}"
                r"(?:解法|行动|选择|局面|交易|结果|筹码|处境)|"
                r"(?:形成|建立|明确).{0,20}(?:新目标|具体目标|下一步行动|阶段结果|"
                r"新期待|阅读期待|明确阻碍)",
                direction,
            ))
            if (len(re.sub(r"\s+", "", quote)) >= 8 and quote in opening_text
                    and has_chain_stage and has_diagnosis and has_revision_lever):
                actionable = True
                break
        if not actionable:
            errors.append("开篇吸引力低于7分，缺少含断点、原因和改稿方向的可定位问题说明")
    return errors

_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_DIALOGUE_RE = re.compile(r"[“‘\"「『](.*?)[”’\"」』]", re.S)
_EMOTION_RE = re.compile(r"哭|泪|笑|怒|怕|颤|抖|咬|攥|握紧|心跳|呼吸|喉咙|指甲|沉默")

# 逐字重复检测阈值（2026-10-11 连载实案校准）：整段 ≥40 字重复两遍是 113/118 章
# 实际事故形态；完整对白复现（≥6 字）同理——有意复诵必有动作/归属铺垫，评审员
# 对 113 章「这杆子歪着」的判词就是「若有意让角色复诵咀嚼，缺少铺垫」。叙述性
# 短句复沓（「早上探，往里。」5 字）是白描节奏手段，落在阈值之下不误伤。
_DUP_MIN_PARA_CHARS = 40
_DUP_MIN_SENT_CHARS = 12
_DUP_MIN_DIALOGUE_CHARS = 6
_DUP_MAX_NOTES = 4

# Cross-chapter repetition is a different failure mode from accidental
# copy/paste.  A sentence can be unique inside one chapter and still make the
# whole book feel templated when the same sentence, opening, or stock action
# returns chapter after chapter.  Keep the detector deliberately conservative:
# it emits evidence for the reviewer and only treats a pattern as major after
# it appears in at least three chapters.
_REPEAT_SENTENCE_MIN = 16
_NEAR_REPEAT_SENTENCE_MIN = 28
_NEAR_REPEAT_SENTENCE_MAX = 512
_NEAR_REPEAT_RATIO = 0.78
_NEAR_REPEAT_CANDIDATES = 24
_NEAR_REPEAT_GRAMS = 24
_NEAR_REPEAT_POSTINGS_PER_GRAM = 64
_NEAR_METAPHOR_RATIO = 0.75
_NEAR_METAPHOR_CANDIDATES = 24
_NEAR_METAPHOR_POSTINGS_PER_GRAM = 48
_REPEAT_OPENING_MIN = 14
_REPEAT_CLAUSE_MIN = 12
_REPEAT_CLAUSE_MAX = 64
_REPEAT_MAX_NOTES = 12
_METAPHOR_RE = re.compile(
    r"(?:像是|像|仿佛|如同|宛如|犹如|好似|恰似)[^，,。！？!?；;\n]{4,24}"
)
_STOCK_PHRASES = (
    "目光一冷", "嘴角勾起", "心头一震", "呼吸一滞", "瞳孔一缩",
    "脸色微变", "攥紧拳头", "深吸一口气", "身形一闪", "不由得一愣",
    "仿佛一道", "像是被", "不是……而是", "不是...而是",
)
_OPENING_TRIGGER_RE = re.compile(
    # Trigger vocabulary must describe an event or action, not merely name
    # the premise. Otherwise exposition such as "她研究AI系统十年" falsely
    # makes the opening look active even when nothing happens on the page.
    r"(?:醒来|睁眼|重生|穿越|回到|发现|看见|听见|冲进|闯入|敲门|爆炸|着火|"
    r"流血|死亡|杀|逃|追|坠落|失踪|响起|来不及|交易|威胁|"
    r"按下|打开|拔刀|转身|闯进|打断|宣布|拒绝|答应|承认|"
    r"离开|抵达|赶到|遇到|收到|递来|砸开|推倒|倒下|闯入)"
)
_OPENING_CHOICE_RE = re.compile(
    r"(?:决定|选择|答应|拒绝|咬牙|按下|打开|拔刀|转身|开口|承认|隐瞒|"
    r"签下|撕掉|交出|带走|留下|逃走|追上|宣布|冲出|推开|接过|夺走)"
)
_OPENING_CONSEQUENCE_RE = re.compile(
    r"(?:于是|随即|立刻|下一秒|当场|门开(?:了)?|灯灭(?:了)?|火光|"
    r"警报(?:响起)?|到账|获得|失去|倒下|响起|暴露|崩塌|被发现|"
    r"被拦|被追|被杀|拦住|挡在|门后|出现|站着|追兵|冲进|闯入|"
    r"逼近|扑来|倒地|断裂|碎裂|被锁|关上|系统提示|任务完成|传来|传出)"
)
_OPENING_CAUSAL_CONTINUATION_RE = re.compile(r"^\s*(?:于是|因此|随即|立刻|下一秒|当场|结果)")
_NON_CAUSAL_REASONS = {
    "保持", "未变化", "无", "剧情需要", "为了推进剧情", "推动剧情",
    "设定如此", "作者安排", "方便剧情", "情节需要",
    "未说明", "随后", "之后", "接着", "突然", "剧情发展", "自然发展",
    "afterward", "afterwards", "later", "later on", "then", "suddenly",
    "same", "unchanged", "no change", "none",
}
_GENERIC_CAUSAL_FILLERS = (
    "因为某种原因", "由于某种原因", "不知为何", "不明原因", "莫名其妙",
    "情况有变", "情况突然变化", "发生变故", "出了意外", "发生意外",
    "不知怎么回事", "不清楚原因", "事情变了", "局势变化",
    "for some reason", "for unknown reasons", "for reasons unknown",
    "somehow", "circumstances changed", "things changed", "something happened",
    "an accident happened", "an unexplained change",
    "the plot required it", "the story required it", "for plot purposes",
    "for the sake of the plot", "plot convenience", "natural progression",
    "author's choice", "the author decided",
)


def _has_causal_reason(reason):
    """Reject empty/timing-only explanations; a cause needs concrete content."""
    normalized = re.sub(r"[\s\u3000，,。；;：:！？!?]", "", str(reason or "")).casefold()
    non_causal = {
        re.sub(r"\s+", "", phrase).casefold() for phrase in _NON_CAUSAL_REASONS
    }
    if not normalized or normalized in non_causal:
        return False
    vague_phrases = _GENERIC_CAUSAL_FILLERS + (
        "为了推进剧情", "剧情需要", "剧情发展", "作者安排", "方便剧情", "自然发展",
    )
    return not any(re.sub(r"\s+", "", token).casefold() in normalized
                   for token in vague_phrases)


def _opening_source_compact(text):
    """Return scan characters with their original manuscript offsets.

    Markdown headings are excluded from the scan, but the offsets continue to
    refer to the unmodified source so diagnostics can locate the actual text.
    """
    chars, offsets = [], []
    source_offset = 0
    for line in str(text or "").splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if not re.match(r"^\s{0,3}#{1,6}\s", content):
            for index, char in enumerate(line):
                if not char.isspace():
                    chars.append(char)
                    offsets.append(source_offset + index)
        source_offset += len(line)
    return "".join(chars), offsets


def _opening_source_end(offsets, count, source_length):
    """Map a compact-text character limit to an exclusive source offset."""
    return offsets[count - 1] + 1 if len(offsets) >= count else source_length


def _continuity_quote_key(value):
    """Normalize manuscript quotes and supplied evidence identically.

    Reviewer quotes often differ only by Chinese book quotes, em dashes,
    ellipses, compatibility-width punctuation, or mixed punctuation copied
    from a markdown renderer. Apply canonical Unicode normalization, then
    remove common prose/Markdown punctuation and whitespace while preserving
    symbols and name punctuation such as ``+1``, ``A-B``, ``A/B`` and ``甲·乙``.
    This remains an anchored containment test rather than fuzzy paraphrase.
    """
    value = unicodedata.normalize("NFC", str(value or ""))
    semantic_punctuation = {"-", "/", "／", "·", "‐", "‑"}
    return "".join(
        char for char in value
        if not char.isspace() and char not in ("\u200b", "\ufeff")
        and (not unicodedata.category(char).startswith("P")
             or char in semantic_punctuation)
    )


def _continuity_states_compatible(left, right):
    """Treat an unopposed textual refinement as the same continuity state.

    Reviewers often describe a short state as "在客栈" and another as
    "在客栈等人". Exact-string comparison called those a plot jump even though
    the second only adds detail. Containment is accepted only when neither
    state carries an explicit negation, so "活着" and "并非活着" remain
    conflicting. This narrow rule avoids pretending to solve general semantics.
    """
    a = _continuity_quote_key(left).casefold()
    b = _continuity_quote_key(right).casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        negation = re.compile(
            r"不|未|无|没|非|\b(?:not|no|never|without|cannot|can't|"
            r"isn't|aren't|wasn't|weren't)\b", re.I)
        return not (negation.search(str(left).casefold())
                    or negation.search(str(right).casefold()))
    return False


def opening_signal_notes(text, trigger_chars=600, payoff_chars=1200):
    """Return conservative, manuscript-anchored opening risk signals.

    These checks explain a weak opening for revision; they do not assert a
    platform rule or require a particular genre device.  A trigger is any
    concrete event/action marker, while an immediate payoff is a character
    choice followed by a visible consequence.  The caller may combine these
    observations with literary review scores.
    """
    source = str(text or "")
    body = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", source, flags=re.M)
    body = body.strip()
    if not body:
        return []
    compact, source_offsets = _opening_source_compact(source)
    try:
        trigger_chars = max(120, int(trigger_chars or 600))
    except (TypeError, ValueError, OverflowError):
        trigger_chars = 600
    try:
        payoff_chars = max(trigger_chars, int(payoff_chars or 1200))
    except (TypeError, ValueError, OverflowError):
        payoff_chars = max(trigger_chars, 1200)
    trigger_window = compact[:trigger_chars]
    payoff_window = compact[:payoff_chars]
    quote = body[:100].strip()
    notes = []
    choice_matches = list(_OPENING_CHOICE_RE.finditer(payoff_window))
    consequence_matches = list(_OPENING_CONSEQUENCE_RE.finditer(payoff_window))
    has_trigger = bool(_OPENING_TRIGGER_RE.search(trigger_window)
                       or _OPENING_CHOICE_RE.search(trigger_window))
    has_choice = bool(choice_matches)
    # A consequence hundreds of characters later may belong to a different
    # beat. Require it to follow a choice closely, with at most one sentence
    # boundary, and inspect every choice so an early idle verb cannot mask a
    # later meaningful decision. This stays advisory because lexical causality
    # cannot be proven by a deterministic text check.
    def follows_choice(choice, consequence):
        if consequence.start() < choice.end() or consequence.start() - choice.end() > 180:
            return False
        gap = payoff_window[choice.end():consequence.start()]
        boundaries = list(re.finditer(r"[。！？!?；;\n]", gap))
        if len(boundaries) > 1:
            return False
        if boundaries:
            # A consequence in the next sentence is only attributable to the
            # choice when the prose explicitly carries the causal transition.
            continuation = gap[boundaries[0].end():]
            if not _OPENING_CAUSAL_CONTINUATION_RE.match(continuation):
                return False
        return True

    has_consequence = any(follows_choice(choice, consequence)
                          for choice in choice_matches
                          for consequence in consequence_matches)
    if len(compact) >= trigger_chars and not has_trigger:
        notes.append({
            "dim": OPENING_DIM, "severity": "minor", "chapter": 1,
            "note": "开篇切入偏慢：前 %d 字未出现可定位的具体事件或行动，故事承诺尚未落地——"
                    "请把正在发生的异常、冲突或任务前置，并保留人物视角"
                    % trigger_chars,
            "quote": quote,
            "evidence_type": "absence_window",
            "scan_window": {"start_char": 0,
                            "end_char": _opening_source_end(
                                source_offsets, trigger_chars, len(source))},
            "advisory": True,
            "kind": "开篇缺少具体触发事件",
        })
    if len(compact) >= payoff_chars and not (has_choice and has_consequence):
        missing = []
        if not has_choice:
            missing.append("人物选择")
        if not has_consequence:
            missing.append("可见后果")
        notes.append({
            "dim": OPENING_DIM, "severity": "minor", "chapter": 1,
            "note": "开篇即时兑现不足：前 %d 字缺少%s——"
                    "让主角做出有代价的选择，并让局面产生可见变化"
                    % (payoff_chars, "与".join(missing)),
            "quote": (body[:160] if body else quote),
            "evidence_type": "absence_window",
            "scan_window": {"start_char": 0,
                            "end_char": _opening_source_end(
                                source_offsets, payoff_chars, len(source))},
            "advisory": True,
            "kind": "开篇缺少人物选择或即时后果",
        })
    return notes


def _prose_sentences(text):
    """Return normalized prose sentences, excluding Markdown headings."""
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", str(text or ""), flags=re.M)
    out = []
    for sentence in _SENTENCE_RE.findall(text):
        normalized = re.sub(r"[\s\u3000]+", "", sentence)
        normalized = normalized.strip("。！？!?；;，,、:：\"'“”‘’「」『』")
        # Synthetic fixtures and intentional sound effects can be long while
        # carrying one repeated character; they are not a reusable sentence
        # template and would create noisy release blockers.
        if len(normalized) >= _REPEAT_SENTENCE_MIN and len(set(normalized)) >= 8:
            out.append(normalized)
    return out


def _prose_clauses(text):
    """Return substantive comma/semicolon clauses for cross-chapter checks."""
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", str(text or ""), flags=re.M)
    out = []
    for clause in re.split(r"[，,。！？!?；;：:\n]+", text):
        normalized = re.sub(r"[\s\u3000]+", "", clause)
        normalized = normalized.strip("\"'“”‘’「」『』（）()[]【】")
        if (_REPEAT_CLAUSE_MIN <= len(normalized) <= _REPEAT_CLAUSE_MAX
                and len(set(normalized)) >= 6):
            out.append(normalized)
    return out


def _chapter_list_text(chapters):
    return "、".join("第 %d 章" % int(chapter) for chapter in chapters)


def _stock_phrase_notes(texts):
    """Summarize repeated stock actions/rhetoric across chapter texts."""
    notes = []
    for phrase in _STOCK_PHRASES:
        hits = []
        total = 0
        for chapter, text in sorted(texts.items()):
            count = str(text).count(phrase)
            if count:
                hits.append(chapter)
                total += count
        if len(hits) >= 3 or total >= 4:
            notes.append({
                "dim": "文风统一", "severity": "major" if len(hits) >= 3 else "minor",
                "chapter": None,
                "note": "惯用修辞/动作重复：‘%s’在%s共出现 %d 次——"
                        "请改用人物专属反应、环境细节或具体选择承载情绪"
                        % (phrase, _chapter_list_text(hits), total),
                "quote": phrase,
                "kind": "惯用修辞重复",
            })
    return notes


def _metaphor_hits(texts):
    """Collect normalized simile fragments and the chapters containing them."""
    hits = {}
    for chapter, text in sorted(texts.items()):
        for match in sorted(set(_METAPHOR_RE.findall(str(text)))):
            phrase = re.sub(r"[\s\u3000]+", "", match).strip("\"'“”‘’「」『』")
            if len(phrase) >= 8 and len(set(phrase)) >= 5:
                chapters = hits.setdefault(phrase, [])
                if chapter not in chapters:
                    chapters.append(chapter)
    return hits


def _near_duplicate_sentence_groups(texts):
    """Group long cross-chapter sentences that reuse nearly the same frame."""
    sentences = []
    for chapter, text in sorted(texts.items()):
        for sentence in sorted(set(_prose_sentences(text))):
            if _NEAR_REPEAT_SENTENCE_MIN <= len(sentence) <= _NEAR_REPEAT_SENTENCE_MAX:
                sentences.append((chapter, sentence))
    if len(sentences) < 2:
        return []

    parents = list(range(len(sentences)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    postings = {}
    for index, (_chapter, sentence) in enumerate(sentences):
        grams = set(sentence[offset:offset + 3]
                    for offset in range(len(sentence) - 2))
        overlaps = {}
        # Rare shingles carry more discriminating information.  Ignore very
        # common shingles and cap each posting scan so long manuscripts retain
        # bounded candidate-generation work per sentence.
        informative = sorted(
            ((len(postings.get(gram, ())), gram) for gram in grams
             if postings.get(gram)))
        informative = [(count, gram) for count, gram in informative
                       if count <= _NEAR_REPEAT_POSTINGS_PER_GRAM]
        for _count, gram in informative[:_NEAR_REPEAT_GRAMS]:
            for previous in postings[gram][-_NEAR_REPEAT_POSTINGS_PER_GRAM:]:
                if sentences[previous][0] == _chapter:
                    continue
                overlaps[previous] = overlaps.get(previous, 0) + 1
        min_overlap = min(_NEAR_REPEAT_GRAMS, max(5, int(len(grams) * 0.18)))
        candidates = sorted(
            ((count, previous) for previous, count in overlaps.items()
             if count >= min_overlap), reverse=True)[:_NEAR_REPEAT_CANDIDATES]
        for _count, previous in candidates:
            ratio = SequenceMatcher(None, sentence, sentences[previous][1],
                                    autojunk=False).ratio()
            if ratio >= _NEAR_REPEAT_RATIO:
                union(index, previous)
        for gram in grams:
            postings.setdefault(gram, []).append(index)

    groups = {}
    for index, row in enumerate(sentences):
        groups.setdefault(find(index), []).append(row)
    result = []
    for rows in groups.values():
        chapters = sorted({chapter for chapter, _sentence in rows})
        variants = list(dict.fromkeys(sentence for _chapter, sentence in rows))
        if len(chapters) >= 2 and len(variants) >= 2:
            chapter_quotes = {}
            for chapter, sentence in sorted(rows):
                chapter_quotes.setdefault(chapter, sentence[:80])
            result.append((chapters, variants, chapter_quotes))
    return sorted(result, key=lambda group: (-len(group[0]), group[0], group[1][0]))


def _near_duplicate_metaphor_groups(metaphors):
    """Group lightly varied simile images without comparing every pair.

    Exact phrase reuse is reported separately. This catches near copies such
    as changing one modifier in an otherwise repeated image, using rare
    three-character shingles to keep candidate work bounded on long books.
    """
    rows = [(phrase, chapters) for phrase, chapters in sorted(metaphors.items())
            if chapters and len(phrase) >= 8]
    if len(rows) < 2:
        return []
    parents = list(range(len(rows)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    postings = {}
    for index, (phrase, _chapters) in enumerate(rows):
        grams = set(phrase[offset:offset + 3] for offset in range(len(phrase) - 2))
        informative = sorted((len(postings.get(gram, ())), gram)
                             for gram in grams if postings.get(gram))
        candidates = {}
        for _count, gram in informative[:24]:
            for previous in postings[gram][- _NEAR_METAPHOR_POSTINGS_PER_GRAM:]:
                candidates[previous] = candidates.get(previous, 0) + 1
        likely = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[
            :_NEAR_METAPHOR_CANDIDATES]
        for previous, overlap in likely:
            minimum = min(4, max(2, int(min(len(phrase), len(rows[previous][0])) * 0.12)))
            if overlap < minimum:
                continue
            if SequenceMatcher(None, phrase, rows[previous][0], autojunk=False).ratio() \
                    >= _NEAR_METAPHOR_RATIO:
                left, right = find(index), find(previous)
                if left != right:
                    parents[right] = left
        for gram in grams:
            postings.setdefault(gram, []).append(index)

    groups = {}
    for index, (phrase, chapters) in enumerate(rows):
        groups.setdefault(find(index), []).extend((phrase, chapter) for chapter in chapters)
    results = []
    for group in groups.values():
        chapter_numbers = sorted({chapter for _phrase, chapter in group})
        variants = list(dict.fromkeys(phrase for phrase, _chapter in group))
        if len(chapter_numbers) >= 2 and len(variants) >= 2:
            chapter_quotes = {}
            for phrase, chapter in sorted(group, key=lambda row: (row[1], row[0])):
                chapter_quotes.setdefault(str(chapter), phrase)
            results.append((chapter_numbers, variants, chapter_quotes))
    return sorted(results, key=lambda group: (-len(group[0]), group[0], group[1][0]))


def repetition_notes(chapters):
    """Find book-level sentence/template repetition with manuscript evidence.

    ``chapters`` accepts ``{chapter_no: text}`` or an iterable of pairs.  The
    result uses the normal issue shape so callers can feed it into the same
    repair and publish gates as model findings.  Two chapters produce a
    review warning; three or more chapters produce a major issue.  Very short
    phrases are intentionally ignored because names, titles, and genre terms
    are expected to recur.
    """
    if isinstance(chapters, dict):
        pairs = list(chapters.items())
    else:
        try:
            pairs = list(chapters or [])
        except TypeError:
            pairs = []
    texts = {}
    for pair in pairs:
        try:
            chapter, text = pair
        except (TypeError, ValueError):
            continue
        number = _positive_chapter_number(chapter)
        if number is None:
            continue
        if not isinstance(text, str):
            continue
        body = text.strip()
        if body:
            if number in texts:
                texts[number] = texts[number] + "\n" + body
            else:
                texts[number] = body
    if len(texts) < 2:
        return []

    stock_notes = _stock_phrase_notes(texts)
    metaphor_hits = _metaphor_hits(texts)
    near_metaphor_groups = _near_duplicate_metaphor_groups(metaphor_hits)
    near_group_phrases = {
        phrase for _chapters, variants, _chapter_quotes in near_metaphor_groups
        for phrase in variants
    }
    near_metaphor_notes = []
    for chapters_seen, variants, chapter_quotes in near_metaphor_groups:
        near_metaphor_notes.append({
            "dim": "文风统一",
            "severity": "major" if len(chapters_seen) >= 3 else "minor",
            "chapter": None,
            "note": "跨章近似比喻重复：%s反复使用相近意象，虽替换少量修饰词仍沿用同一比喻骨架——"
                    "请确认是否为有意母题，否则改换意象来源或感官通道"
                    % _chapter_list_text(chapters_seen),
            "quote": variants[0],
            "chapter_quotes": chapter_quotes,
            "kind": "跨章近似比喻重复",
        })
    reserved_notes = stock_notes[:1] + near_metaphor_notes[:1]

    def finish_notes(current):
        # Reserve report slots for repeated stock phrases and near-reused
        # imagery. Otherwise many exact sentence findings can fill the cap
        # before these more specific style patterns reach the output.
        if reserved_notes:
            return current[:max(0, _REPEAT_MAX_NOTES - len(reserved_notes))] + reserved_notes
        return current[:_REPEAT_MAX_NOTES]

    notes = []
    sentence_hits = {}
    sentence_samples = {}
    sentence_chapter_samples = {}
    for chapter, text in sorted(texts.items()):
        for sentence in sorted(set(_prose_sentences(text))):
            key = _continuity_quote_key(sentence)
            if not key:
                continue
            chapters = sentence_hits.setdefault(key, [])
            if chapter not in chapters:
                chapters.append(chapter)
            sentence_samples.setdefault(key, sentence)
            sentence_chapter_samples.setdefault(key, {})[str(chapter)] = sentence
    for sentence_key, chapters_seen in sorted(
            sentence_hits.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(chapters_seen) < 2:
            continue
        severity = "major" if len(chapters_seen) >= 3 else "minor"
        sentence = sentence_samples[sentence_key]
        notes.append({
            "dim": "文风统一", "severity": severity,
            "chapter": None,
            "note": "跨章整句复用：%s出现同一完整句式（共 %d 章）——"
                    "请确认是否为必要的固定表达，否则改换叙述角度、动作载体和信息量"
                    % (_chapter_list_text(chapters_seen), len(chapters_seen)),
            "quote": sentence[:80],
            "chapter_quotes": {
                str(chapter): sentence_chapter_samples[sentence_key][str(chapter)][:80]
                for chapter in chapters_seen
            },
            "kind": "跨章整句重复",
        })
        if len(notes) >= _REPEAT_MAX_NOTES:
            return finish_notes(notes)

    for chapters_seen, variants, chapter_quotes in _near_duplicate_sentence_groups(texts):
        severity = "major" if len(chapters_seen) >= 3 else "minor"
        notes.append({
            "dim": "文风统一", "severity": severity, "chapter": None,
            "note": "跨章近似句式重复：%s多次沿用相同长句骨架——"
                    "请改变叙述主语、动作顺序和信息落点"
                    % _chapter_list_text(chapters_seen),
            "quote": variants[0][:80],
            "chapter_quotes": {str(chapter): quote
                               for chapter, quote in chapter_quotes.items()},
            "kind": "跨章近似句式重复",
        })
        if len(notes) >= _REPEAT_MAX_NOTES:
            return finish_notes(notes)

    # Repeated clauses catch a more subtle template pattern: each full sentence
    # differs, while the same substantial action or reaction is reused inside
    # it.  Short stock phrases remain the responsibility of the dedicated list
    # below, so a recurring name or beat-length fragment cannot block a book.
    clause_hits = {}
    for chapter, text in sorted(texts.items()):
        for clause in sorted(set(_prose_clauses(text))):
            clause_hits.setdefault(clause, []).append(chapter)
    full_sentence_clauses = set()
    for sentence_key, chapters_seen in sentence_hits.items():
        if len(chapters_seen) >= 2:
            full_sentence_clauses.update(_prose_clauses(sentence_samples[sentence_key]))
    for clause, chapters_seen in sorted(
            clause_hits.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(chapters_seen) < 2:
            continue
        if clause in full_sentence_clauses:
            continue
        chapter_quotes = {str(chapter): clause for chapter in chapters_seen}
        notes.append({
            "dim": "文风统一",
            "severity": "major" if len(chapters_seen) >= 3 else "minor",
            "chapter": None,
            "note": "跨章分句复用：%s重复使用同一较长动作/描写分句（共 %d 章）——"
                    "保留情节信息，改换动作载体、观察角度或句子节奏"
                    % (_chapter_list_text(chapters_seen), len(chapters_seen)),
            "quote": clause,
            "chapter_quotes": chapter_quotes,
            "kind": "跨章分句重复",
        })
        if len(notes) >= _REPEAT_MAX_NOTES:
            return finish_notes(notes)

    repeated_full_metaphors = {
        phrase for phrase, chapters_seen in metaphor_hits.items()
        if any(phrase in sentence_samples[sentence_key]
               for sentence_key, sentence_chapters in sentence_hits.items()
               if len(sentence_chapters) >= 2)
    }
    for phrase, chapters_seen in sorted(
            metaphor_hits.items(), key=lambda item: (-len(item[1]), item[0])):
        if (len(chapters_seen) < 2 or phrase in repeated_full_metaphors
                or phrase in near_group_phrases):
            continue
        notes.append({
            "dim": "文风统一",
            "severity": "major" if len(chapters_seen) >= 3 else "minor",
            "chapter": None,
            "note": "跨章比喻意象重复：%s反复使用相同的比喻片段（共 %d 章）——"
                    "改换意象来源、感官通道或叙述视角，避免只替换主语"
                    % (_chapter_list_text(chapters_seen), len(chapters_seen)),
            "quote": phrase,
            "chapter_quotes": {str(chapter): phrase for chapter in chapters_seen},
            "kind": "跨章比喻重复",
        })
        if len(notes) >= _REPEAT_MAX_NOTES:
            return finish_notes(notes)

    for near_metaphor_note in near_metaphor_notes:
        notes.append(near_metaphor_note)
        if len(notes) >= _REPEAT_MAX_NOTES:
            return finish_notes(notes)

    # Compare the first and last sentence of each chapter.  Matching a long
    # edge is a useful proxy for repeated chapter openings/closings while
    # avoiding false positives from recurring character names alone.
    for edge_name, index in (("开笔", 0), ("收束", -1)):
        edge_hits = {}
        for chapter, text in sorted(texts.items()):
            sentences = _prose_sentences(text)
            if not sentences:
                continue
            edge = sentences[index]
            key = edge[:_REPEAT_OPENING_MIN] if index == 0 else edge[-_REPEAT_OPENING_MIN:]
            if len(key) >= _REPEAT_OPENING_MIN:
                edge_hits.setdefault(key, []).append((chapter, edge))
        for key, rows in sorted(edge_hits.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(rows) < 2:
                continue
            chapters_seen = [row[0] for row in rows]
            notes.append({
                # Edge fragments are weak evidence by themselves: genre prose
                # often reuses a short scene-setting lead-in.  Keep this as a
                # review warning; full/near-full sentence matches own blocking.
                "dim": "文风统一", "severity": "minor", "chapter": None,
                "note": "跨章%s句式同款：%s共享相同的 %d 字起止结构——"
                        "必须改变切入视角、动作顺序或信息落点"
                        % (edge_name, _chapter_list_text(chapters_seen), len(key)),
                "quote": rows[0][1][:80] if index == 0 else rows[0][1][-80:],
                "chapter_quotes": {
                    str(chapter): (edge[:80] if index == 0 else edge[-80:])
                    for chapter, edge in rows
                },
                "kind": "跨章%s句式重复" % edge_name,
            })
            if len(notes) >= _REPEAT_MAX_NOTES:
                return finish_notes(notes)

    # Stock rhetoric was computed before the capped scans so it could retain a
    # slot when exact/near-repeat findings saturate the report.
    for stock_note in stock_notes:
        notes.append(stock_note)
        if len(notes) >= _REPEAT_MAX_NOTES:
            break
    return notes[:_REPEAT_MAX_NOTES]


def parse_continuity_lines(lines, chapter=None):
    """Parse reviewer ``<continuity>`` rows into a validated ledger shape.

    Required format is ``实体|属性|本章状态|变化原因|正文证据``.  Bad rows are
    ignored instead of becoming false facts; the reviewer still has to cite
    the actual manuscript in its normal ``issues`` output.
    """
    result = []
    for raw in lines if isinstance(lines, list) else []:
        parts = [str(x).strip() for x in str(raw or "").split("|")]
        if len(parts) < 5:
            continue
        entity, attr, state, reason, evidence = parts[:5]
        if not entity or not attr or not state or not evidence:
            continue
        result.append({
            "chapter": chapter,
            "entity": entity[:60], "attribute": attr[:80],
            "state": state[:160], "reason": reason[:160],
            "evidence": evidence[:240],
        })
    return result


def validate_continuity_rows(rows, manuscript):
    """Keep only continuity rows whose evidence is an anchored manuscript quote.

    Reviewer ledgers are state carried into future prompts, so an invented or
    paraphrased evidence string must not be allowed to become a fact.  Whitespace
    differences are ignored, while headings and short filler quotes remain
    invalid.
    """
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", str(manuscript or ""), flags=re.M)
    text = _continuity_quote_key(text)
    valid = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        evidence = _continuity_quote_key(row.get("evidence"))
        if len(evidence) < 8 or evidence not in text:
            continue
        reason = str(row.get("reason") or "").strip()
        if _has_causal_reason(reason):
            anchored_reason = _continuity_quote_key(reason)
            if len(anchored_reason) < 8 or anchored_reason not in text:
                continue
        valid.append(row)
    return valid


def continuity_conflict_notes(rows):
    """Turn contradictory reviewer ledger rows into anchored major issues.

    The latest review round for a chapter is expected to replace an earlier
    round.  Within that round, two reviewers disagreeing about the same
    entity/attribute is a conflict; across chapters, a changed state must carry
    an explicit transition reason.  This is a deterministic guardrail around
    semantic model judgement, not a claim that arbitrary prose can be fully
    understood by regex.
    """
    valid = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)
             and row.get("entity") and row.get("attribute") and row.get("state")]
    if not valid:
        return []
    # Keep only the latest submitted row set per chapter.  Rows from the first
    # failed review round must not fight with the final repaired chapter.
    by_chapter = {}
    for row in valid:
        chapter = _positive_chapter_number(row.get("chapter"))
        if chapter is None:
            continue
        by_chapter.setdefault(chapter, []).append(row)
    notes = []
    history = {}
    for chapter in sorted(by_chapter):
        current = by_chapter[chapter]
        grouped = {}
        for row in current:
            key = (_continuity_quote_key(row["entity"]),
                   _continuity_quote_key(row["attribute"]))
            grouped.setdefault(key, []).append(row)
        for (entity_key, attr_key), group in grouped.items():
            entity = str(group[0]["entity"]).strip()
            attr = str(group[0]["attribute"]).strip()
            state_by_key = {}
            for row in group:
                state_by_key.setdefault(_continuity_quote_key(row["state"]),
                                        str(row["state"]).strip())
            states = list(state_by_key.values())
            mutually_compatible = all(
                _continuity_states_compatible(left, right)
                for index, left in enumerate(states)
                for right in states[index + 1:])
            if len(states) > 1 and not mutually_compatible:
                notes.append({
                    "dim": "情节逻辑", "severity": "major", "chapter": chapter,
                    "note": "连续性账本冲突：第 %d 章对‘%s·%s’给出互斥状态：%s——"
                            "先统一人物/设定事实，再继续写作"
                            % (chapter, entity, attr, "、".join(states[:4])),
                    "quote": next((row["evidence"] for row in group if row.get("evidence")), ""),
                    "kind": "连续性账本冲突",
                })
                continue
            # Preserve the most informative compatible description for the
            # cross-chapter baseline without treating added detail as a change.
            state = max(states, key=lambda value: len(_continuity_quote_key(value)))
            state_key = _continuity_quote_key(state)
            previous = history.get((entity_key, attr_key))
            if previous and not _continuity_states_compatible(previous["state"], state):
                reasons = [str(row.get("reason") or "").strip() for row in group]
                has_causal_bridge = any(_has_causal_reason(reason) for reason in reasons)
                if not has_causal_bridge:
                    notes.append({
                        "dim": "情节逻辑", "severity": "major", "chapter": chapter,
                        "note": "人物/设定状态无因果变化：‘%s·%s’从第 %d 章的‘%s’变为‘%s’，"
                                "但没有说明触发事件或代价"
                                % (entity, attr, previous["chapter"], previous["state"], state),
                        "quote": group[0].get("evidence") or "",
                        "kind": "连续性状态跳变",
                    })
            history[(entity_key, attr_key)] = {
                "chapter": chapter, "state": state, "state_key": state_key,
            }
    return notes


def duplicate_notes(text):
    """章内逐字重复检测（确定性，成稿事故级）。

    抓三类：整段逐字重复、整句（≥12 字去空白）逐字重复、完整对白（≥6 字去空白）
    逐字重复。返回结构化证据列表 [{kind, count, quote}]，供落盘验收打回重起草与
    评审提示词引用；短句复沓等有意的节奏手段不在阈值之内，不构成证据。
    """
    text = str(text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    notes = []
    # ① 整段逐字重复（去空白比对；间隔段落/连排都算）
    para_counts = {}
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"\s+", "", p)
        if len(p) >= _DUP_MIN_PARA_CHARS:
            para_counts[p] = para_counts.get(p, 0) + 1
    dup_paras = [p for p, c in sorted(para_counts.items(), key=lambda kv: -kv[1])
                 if c >= 2]
    for p in dup_paras[:2]:
        notes.append({"kind": "整段重复", "count": para_counts[p], "quote": p[:40]})
    # ② 整句逐字重复（已被整段重复覆盖的句子不再单列，避免一条事故报一串）
    sent_counts = {}
    for s in _SENTENCE_RE.findall(text):
        s = re.sub(r"\s+", "", s)
        if len(s) >= _DUP_MIN_SENT_CHARS:
            sent_counts[s] = sent_counts.get(s, 0) + 1
    for s, c in sorted(sent_counts.items(), key=lambda kv: -kv[1]):
        if c < 2 or len(notes) >= _DUP_MAX_NOTES:
            continue
        if any(s in p for p in dup_paras):
            continue
        notes.append({"kind": "整句重复", "count": c, "quote": s[:40]})
    # ③ 完整对白逐字重复（阈值更低：成稿事故最常以「对白整句连排两遍」出现）
    seen_dialogue = {n["quote"] for n in notes}
    dial_counts = {}
    for d in _DIALOGUE_RE.findall(text):
        d = re.sub(r"\s+", "", d)
        if len(d) >= _DUP_MIN_DIALOGUE_CHARS:
            dial_counts[d] = dial_counts.get(d, 0) + 1
    for d, c in sorted(dial_counts.items(), key=lambda kv: -kv[1]):
        if c < 2 or len(notes) >= _DUP_MAX_NOTES:
            continue
        if any(d in q for q in seen_dialogue):
            continue
        notes.append({"kind": "对白重复", "count": c, "quote": d[:40]})
    return notes


def format_duplicate_notes(notes):
    """Render duplicate_notes evidence as human-readable lines."""
    lines = []
    for n in notes if isinstance(notes, list) else []:
        try:
            lines.append("%s %d 处：「%s」" % (n.get("kind", "重复"),
                                              int(n.get("count") or 0),
                                              str(n.get("quote") or "")[:40]))
        except (TypeError, ValueError):
            continue
    return lines


_STYLE_NAMES = {"「」": "直角引号「」", "“”": "弯引号“”", '"': "ASCII 直引号"}
_STYLE_OPEN_CLOSE = {"「」": ("「", "」"), "“”": ("“", "”"), '"': ('"', '"')}
_STYLE_MIN_QUOTES = 3     # 单章某制式 ≥3 对才认定「该章用了这种制式」


def _quote_style_counts(text):
    """按制式统计引号对数：直角「」、弯引号“”、ASCII 直引号 "。"""
    t = str(text or "")
    return {"「」": t.count("「"),
            "“”": min(t.count("\u201c"), t.count("\u201d")),
            '"': t.count('"') // 2}


def _style_snippet(text, marker, closer):
    """取制式标记处的一段原文做证据（≤30 字），找不到标记回退章首。"""
    t = str(text or "")
    try:
        i = t.index(marker)
        j = t.index(closer, i) + 1 if closer in t[i + 1:i + 60] else i + 20
        return t[i:min(j, i + 30)].replace("\n", " ")
    except ValueError:
        return t[:30].replace("\n", " ")


def style_consistency_notes(chapters):
    """跨章/章内体例一致性检测（确定性，签约红线级；2026-10-15 七猫双拒实案）。

    引号制式混用和 BOM 是可定位的内部交付问题，按项目体例要求出 major。
    这不能证明它们是编辑所说「风格上不够统一」的原因；语域、视角距离、
    人物声音仍需文学评审独立判断。
    chapters 接受 {章号: 正文} 或 [(章号, 正文), ...]。三类红线：①引号制式
    跨章混用（多数章制式为基准，偏离章在 note 点名）；②章内两种制式并存；
    ③UTF-8 BOM 残留。返回 issue 列表 [{dim, severity, chapter, note, quote}]——
    跨章账 chapter=None + note 点名病灶章（修复链按字段∪文案选章/撤账）。
    """
    if isinstance(chapters, dict):
        pairs = list(chapters.items())
    else:
        try:
            pairs = list(chapters or [])
        except TypeError:
            pairs = []
    raw = {}
    for pair in pairs:
        try:
            chapter, text = pair
        except (TypeError, ValueError):
            continue
        chapter = _positive_chapter_number(chapter)
        if chapter is not None and isinstance(text, str) and text.strip():
            raw[chapter] = text
    if not raw:
        return []
    texts = {c: t.replace("\r\n", "\n").strip() for c, t in raw.items()}
    notes = []
    dominant = {}
    for c, t in texts.items():
        counts = _quote_style_counts(t)
        best = max(counts, key=lambda k: counts[k])
        if counts[best] >= _STYLE_MIN_QUOTES:
            dominant[c] = best
    votes = {}
    for style in dominant.values():
        votes[style] = votes.get(style, 0) + 1
    if votes:
        base = max(votes, key=lambda k: votes[k])
        stray = [c for c, s in sorted(dominant.items()) if s != base]
        if stray and votes[base] >= 2:
            notes.append({
                "dim": "文风统一", "severity": "major", "chapter": None,
                "note": "引号制式跨章混用：第 %s 章用%s、其余章以%s为主——签约稿须"
                        "按项目交付要求统一体例；此项不代表文学文风或平台判断"
                        % ("、".join(str(c) for c in stray),
                           _STYLE_NAMES[dominant[stray[0]]], _STYLE_NAMES[base]),
                "quote": _style_snippet(texts[stray[0]],
                                        *_STYLE_OPEN_CLOSE[dominant[stray[0]]])})
    for c, t in sorted(texts.items()):
        counts = _quote_style_counts(t)
        used = sorted(k for k, v in counts.items() if v >= _STYLE_MIN_QUOTES)
        if len(used) >= 2:
            notes.append({
                "dim": "文风统一", "severity": "major", "chapter": c,
                "note": "第 %d 章引号制式章内混用（%s并存）——同章两制是编辑肉眼"
                        "可辨的体例伤，统一为本稿主流制式后才能提交"
                        % (c, "、".join(_STYLE_NAMES[k] for k in used)),
                "quote": _style_snippet(t, *_STYLE_OPEN_CLOSE[used[0]])})
    for c, t in raw.items():
        if t.startswith("\ufeff"):
            notes.append({
                "dim": "文风统一", "severity": "major", "chapter": c,
                "note": "第 %d 章文件带 UTF-8 BOM 残留——发布管线原样搬运时首行"
                        "会多出不可见字符，交稿前须清除" % c,
                "quote": t.lstrip("\ufeff")[:30].replace("\n", " ")})
    return notes


def rubric_for(task):
    """Return the configured rubric, or the signing rubric for serial novels."""
    task = task if isinstance(task, dict) else {}
    raw = task.get("rubric")
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        values = [str(x).strip() for x in raw if str(x).strip()]
        if values:
            return values[:8]
    if str(task.get("type") or "").lower() == "serial_novel":
        return list(SIGNING_RUBRIC)
    return []


def opening_requirements(chapter, target_words=None):
    """Return concise, chapter-aware acceptance requirements for prompts."""
    chapter = _positive_chapter_number(chapter) or 1
    if chapter == 1:
        return ("首章观察点（非硬门槛）：记录读者何时能辨认视角人物、故事承诺与正在发生的事；"
                "核心卖点是否通过具体场景兑现：区分只提到能力/身份/目标，与它实际改变行动并带来阶段结果；"
                "检查读者是否得到与题材相称的首个回报或新看点，可为爽感、信息发现、悬念推进、关系变化、惊险、幽默或氛围，"
                "不要求固定爽点或打脸；同时判断这个回报是否具体、有力度且具辨识度，不能把有事件/有结果本身等同于足够好看；"
                "铺垫是否符合题材且有阅读价值；结尾是否形成自然的继续阅读理由。"
                "根据文本效果和作者目标评估，不因未在固定字数内出现冲突/卖点/钩子而自动判失败。")
    return ("本章观察点：核对与前文的承接、局面/人物/信息/情绪的变化及本章叙事功能；"
            "结尾是否提供符合本章节奏的收束或继续阅读理由。不强制每章反转或悬念断章。")


def signal_summary(text, chapter=None):
    """Extract explainable text signals; no claim is made that they are scores."""
    text = str(text or "").replace("\r\n", "\n").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    sentence_lengths = [len(re.sub(r"\s+", "", s)) for s in sentences]
    dialogue_chars = sum(len(x) for x in _DIALOGUE_RE.findall(text))
    emotion_hits = len(_EMOTION_RE.findall(text))
    chars = len(re.sub(r"\s+", "", text))
    avg = round(sum(sentence_lengths) / len(sentence_lengths), 1) if sentence_lengths else 0.0
    long_sentences = sum(1 for n in sentence_lengths if n >= 55)
    hints = []
    if avg >= 35 or long_sentences:
        hints.append("存在偏长句，复查长定语、重复说明与动作拆分")
    if len(paragraphs) >= 4 and chars / len(paragraphs) > 220:
        hints.append("段落偏长，复查手机阅读下的节奏与信息密度")
    # 逐字重复（整段/整句/完整对白）是成稿事故而非文风选择，直接点名证据；
    # 起草验收闸（pipeline._chapter_state）用同一判定打回，评审面据此复核
    for line in format_duplicate_notes(duplicate_notes(text)):
        hints.append("逐字重复（成稿事故，须删并保留信息量最高的一处）：" + line)
    opening_hints = []
    if chapter == 1:
        opening_hints = [
            "开篇观察（非硬门槛，需结合文学判断）：" + str(item.get("note") or "")
            for item in opening_signal_notes(text)
        ]
    # Dialogue/emotion-action counts are descriptive signals only. Their absence
    # is not a quality defect: narrative voice, genre, and scene purpose vary.
    return {
        "chapter": chapter,
        "chars": chars,
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "avg_sentence_chars": avg,
        "long_sentences": long_sentences,
        "dialogue_chars": dialogue_chars,
        "emotion_action_hits": emotion_hits,
        # Opening observations must survive a saturated generic signal list;
        # otherwise the exact rejection pattern can be detected but omitted
        # from the revision prompt. Reserve space while preserving the cap.
        "hints": (hints[:max(0, 6 - len(opening_hints))] + opening_hints)[:6],
    }


def format_signal_summary(summary):
    """Render signal_summary output as a compact prompt block."""
    if not isinstance(summary, dict):
        return ""
    parts = [
        "文本信号（仅作证据，不替代文学判断）：约 %s 字；%s 段；%s 句；平均句长 %s 字；长句 %s；对白 %s 字；情绪动作线索 %s 处。"
        % (summary.get("chars", 0), summary.get("paragraphs", 0),
           summary.get("sentences", 0), summary.get("avg_sentence_chars", 0),
           summary.get("long_sentences", 0), summary.get("dialogue_chars", 0),
           summary.get("emotion_action_hits", 0))]
    hints = summary.get("hints") or []
    if hints:
        parts.append("确定性复查提示：" + "；".join(str(x) for x in hints))
    return "\n".join(parts)


def style_anchor_block(anchor):
    """Render the book-level style anchor as a prompt block; empty when absent."""
    anchor = str(anchor or "").strip()
    if not anchor:
        return ""
    return ("## 全书文风锚（大纲裁定，所有章节起草/修订/评审必须遵守；"
            "与其冲突处以本锚为准）\n%s\n" % anchor[:400])


def reuse_guard_block(samples):
    """Render prior chapters' opening/closing excerpts as a negative-example block.

    samples: [{"chapter": int, "head": str, "tail": str}] — 起草面据此避开同款
    开笔句式与同型收束（跨章复用红线：同一动作句式至多两次，第二次必须换载体
    或升级信息量）。无样例（首批第 1 章等）返回空串，不占提示词字节。
    """
    rows = []
    for s in samples if isinstance(samples, list) else []:
        if not isinstance(s, dict):
            continue
        ch = _positive_chapter_number(s.get("chapter"))
        if ch is None:
            continue
        head = str(s.get("head") or "").strip()
        tail = str(s.get("tail") or "").strip()
        if head:
            rows.append("- 第 %d 章开笔：「%s」" % (ch, head[:60]))
        if tail:
            rows.append("- 第 %d 章收束：「…%s」" % (ch, tail[-70:]))
    if not rows:
        return ""
    return ("## 跨章复用红线（起草前必读）\n"
            "此前各章已用的开笔/收束形态如下——本章不得再以同款句式开笔或同型"
            "句式收束；同一动作句式全书至多两次，第二次必须换载体或升级信息量，"
            "连读时章尾节奏不得趋同：\n" + "\n".join(rows) + "\n")


def fingerprint(summary):
    """Normalize a signal_summary into per-1000-char style metrics."""
    if not isinstance(summary, dict):
        return {}
    try:
        chars = max(1, int(summary.get("chars") or 0))
    except (TypeError, ValueError, OverflowError):
        chars = 1
    def number(value):
        try:
            result = float(value or 0.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return result if math.isfinite(result) else 0.0
    return {
        "avg_sentence_chars": number(summary.get("avg_sentence_chars")),
        "dialogue_per_k": round(1000.0 * number(summary.get("dialogue_chars")) / chars, 1),
        "emotion_per_k": round(1000.0 * number(summary.get("emotion_action_hits")) / chars, 1),
    }


def _median(values):
    xs = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else round((xs[mid - 1] + xs[mid]) / 2.0, 1)


def style_deviation_notes(baselines, summary, min_baseline=2):
    """Cross-chapter style drift warnings from deterministic fingerprints.

    baselines: signal_summary dicts of the book's previous chapters.  Needs at
    least ``min_baseline`` chapters before speaking (开篇几章没有稳定基线)；
    all notes are evidence hints for reviewers/revisers, never scores.
    """
    notes = []
    if not isinstance(baselines, (list, tuple)):
        return notes
    if isinstance(min_baseline, bool) or not isinstance(min_baseline, int) or min_baseline < 0:
        return notes
    base = [fingerprint(b) for b in baselines if isinstance(b, dict)]
    base = [b for b in base if b]
    if len(base) < min_baseline:
        return notes
    cur = fingerprint(summary)
    if not cur:
        return notes
    avg_base = _median([b["avg_sentence_chars"] for b in base])
    if avg_base >= 12 and cur["avg_sentence_chars"] > avg_base * 1.45:
        notes.append("本章平均句长 %s 字，此前各章中位 %s 字——句式明显变长，"
                     "复查长定语、说明性插叙与节奏拖沓"
                     % (cur["avg_sentence_chars"], avg_base))
    elif avg_base >= 12 and cur["avg_sentence_chars"] < avg_base * 0.62:
        notes.append("本章平均句长 %s 字，此前各章中位 %s 字——句式明显变碎，"
                     "复查是否为凑字数拆散动作与因果"
                     % (cur["avg_sentence_chars"], avg_base))
    dial_base = _median([b["dialogue_per_k"] for b in base])
    if dial_base >= 40 and cur["dialogue_per_k"] < dial_base * 0.45:
        notes.append("对白占比骤降（本章 %s/千字，此前中位 %s/千字）——"
                     "复查是否换成了大段叙述，文风与推进方式是否漂移"
                     % (cur["dialogue_per_k"], dial_base))
    emo_base = _median([b["emotion_per_k"] for b in base])
    if emo_base >= 1.5 and cur["emotion_per_k"] < emo_base * 0.4:
        notes.append("情绪动作密度骤降（本章 %s/千字，此前中位 %s/千字）——"
                     "复查复杂情感是否退回「很伤心/很愤怒」式概括"
                     % (cur["emotion_per_k"], emo_base))
    return notes


def opening_below(means, threshold):
    """True when the signing opening dimension is scored below the line."""
    if not isinstance(means, dict):
        return False
    try:
        value = float(means.get(OPENING_DIM))
    except (TypeError, ValueError, OverflowError):
        return False
    try:
        line = float(threshold)
    except (TypeError, ValueError, OverflowError):
        return False
    return value < line
