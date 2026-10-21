# -*- coding: utf-8 -*-
"""反馈闭环与评审证据检查：确定性的「评审是否真正回应了复用反馈」。

从 novel_quality 拆出（单文件行数治理），主模块原样再导出。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Optional

from .novel_quality_common import (
    OPENING_DIM, SIGNING_RUBRIC, _FEEDBACK_ID_INLINE_LIMIT, _positive_chapter_number,
)


def feedback_record_key(value: Any) -> str:
    """Return a stable prompt-safe key while retaining exact-ID compatibility."""
    text = str(value or "")
    if len(text) <= _FEEDBACK_ID_INLINE_LIMIT:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return "feedback-id:" + digest




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


def _is_periodic_text(text: str) -> bool:
    """Return whether text is an exact repetition of a shorter unit in O(n)."""
    if len(text) < 2:
        return False
    prefix = [0] * len(text)
    matched = 0
    for index in range(1, len(text)):
        while matched and text[index] != text[matched]:
            matched = prefix[matched - 1]
        if text[index] == text[matched]:
            matched += 1
        prefix[index] = matched
    period = len(text) - prefix[-1]
    return bool(prefix[-1] and len(text) % period == 0)


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
    # KMP's prefix table detects an exact periodic string in linear time.
    # Trying every possible unit width constructs O(n²) temporary text for a
    # long model response, even though a review normally contains only a few
    # hundred characters.
    if _is_periodic_text(compact):
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
    analysis_dimensions = {}
    valid_analysis_by_dim = {}
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
        analysis = re.sub(r"\s+", "", raw_analysis)
        # A non-empty token such as "好" is not an explanation.  Keep this
        # deliberately structural: literary support and limitations remain
        # human judgements, but each dimension must at least contain enough
        # text to state them rather than pass with a placeholder.
        if (len(re.sub(r"\s+", "", quote)) >= 8 and quote in text
                and len(analysis) >= 16):
            dim = row["dim"]
            # The same generic explanation copied across dimensions does not
            # establish dimension-specific reasoning. Ignore punctuation and
            # whitespace when detecting exact template reuse.
            normalized_analysis = analysis.casefold()
            for rubric_dim in SIGNING_RUBRIC:
                normalized_analysis = normalized_analysis.replace(
                    rubric_dim.casefold(), "")
            analysis_key = re.sub(r"[\W_]+", "", normalized_analysis)
            # Length alone can be padded by repeating one word ("好好好…")
            # or by repeating a short generic sentence. Require some actual
            # non-rubric content and reject exact periodic filler.
            if len(analysis_key) < 8 or _is_periodic_text(analysis_key):
                continue
            analysis_dimensions.setdefault(analysis_key, set()).add(dim)
            valid_analysis_by_dim[dim] = analysis_key
    supported.update(
        dim for dim, key in valid_analysis_by_dim.items()
        if len(analysis_dimensions.get(key, ())) == 1)
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
_CHAPTER_PLACEHOLDER_PHRASES = (
    "看了很久", "看了许久", "没有说话", "没有再说话", "顿了顿",
    "声音很轻", "声音很冷", "一动不动", "没有动", "沉默了几秒",
)
_NUMERIC_BEAT_RE = re.compile(
    r"^(?:[0-9０-９]+(?:[.,，。][0-9０-９]+)?|"
    r"[零一二三四五六七八九十百千万两]+)"
    r"(?:万|千|百)?(?:人|个|名|次|条|万|千|百)?$")
_METRIC_BEAT_RE = re.compile(
    r"(?:人数|播放量|在线峰值|热搜|突破|冲破|爬升|爬|增长|"
    r"[0-9０-９一二三四五六七八九十百千万两]+(?:小时|分钟|天)后)"
    r"[^。！？!?；;\n]{0,18}"
    r"(?:[0-9０-９一二三四五六七八九十百千万两]+(?:万|千|百)?"
    r"(?:人|次|条|万|千|百)?|第一|第二|第三|第四|第五)")
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