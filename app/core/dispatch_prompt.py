# -*- coding: utf-8 -*-
"""Compile a stable execution contract before a prompt reaches an agent.

The compiler is deliberately deterministic.  It makes the current phase and
the task boundaries explicit without asking another model to paraphrase the
user's request.  Planning output is the reusable optimization artifact for a
run; dispatch never performs a second hidden prompt-rewrite call.
"""
from __future__ import annotations

import re


VERSION = 1
MARKER = "## CodeBee 下发契约（v1）"
_INTENT_MARKER = "## 用户意图契约（优先级高于默认做法，必须逐项核对）"
_ATTACHMENTS_MARKER = "<!-- codebee-attachments:start -->"
_ORIGINAL_END = "--- 原始步骤提示词结束（下面是契约复核，不是新的用户任务） ---"
# Keep the envelope small enough that long writing/review prompts do not get
# pushed over a CLI context or Windows argv limit merely by adding metadata.
_MAX_ITEM_CHARS = 240
_MAX_ITEMS = 6

_DEEP_TYPES = {
    "novel", "serial_novel", "research", "tech_proposal", "rank_scan",
    "short_drama", "presentation",
}
_PLAN_ROLES = ("plan", "outline", "diagnos", "explor", "research")
_REVIEW_ROLES = (
    "review", "critic", "critique", "qa", "audit", "signing-eval",
    "race-select", "bestof-select", "verify", "score", "评审", "审计",
    "验收", "评分", "签约",
)
_REVISION_ROLES = (
    "fix", "revise", "polish", "rebuild", "redesign", "修订", "打磨",
    "修复", "重设计",
)


def _clean(value, limit=_MAX_ITEM_CHARS):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _items(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        item = _clean(item)
        if item and item not in result:
            result.append(item)
        if len(result) >= _MAX_ITEMS:
            break
    return result


def _contract(task):
    contract = task.get("intent_contract") if isinstance(task, dict) else None
    return contract if isinstance(contract, dict) else {}


def _phase(role):
    value = _clean(role, 80).lower()
    if any(token in value for token in _PLAN_ROLES):
        return "规划/讨论"
    if any(token in value for token in _REVIEW_ROLES):
        return "评审/验收"
    if any(token in value for token in _REVISION_ROLES):
        return "修订/修复"
    return "执行/产出"


def prompt_strategy(task):
    """Return the bounded prompt policy for a task.

    ``deterministic`` means compile the contract only.  The legacy
    ``prompt_preflight`` field is intentionally ignored: it is not a supported
    task API and must never cause a hidden model call.  A planner or outline
    result, when present, is the reusable optimization artifact for the run.
    """
    return {
        "mode": "deterministic",
        "max_extra_model_calls": 0,
        "reason": "使用确定性契约；已有规划/大纲时复用，不重复改写提示词",
    }


def _stage_rules(task, phase):
    task_type = _clean(task.get("type") or "direct", 60).lower()
    rules = [
        "只完成当前阶段；交付完成后停止，不自动开启下一阶段、创建新任务或继续连载。",
        "原始步骤提示词是本阶段的执行依据；本契约只补充边界，不改变其目标、事实或顺序。",
        "无法读取附件、工作区或必要事实时，明确报告阻塞与缺口，不用猜测填补。",
    ]
    if phase == "规划/讨论":
        rules.append("当前只做规划、诊断或讨论；除非原始步骤明确要求，不写正文、不修改成品、不启动发布。")
    elif phase == "评审/验收":
        rules.append("当前以证据评审为主；没有文件/行号/测试等依据的判断标为未验证，不把候选方案当成已批准事实。")
    elif phase == "修订/修复":
        rules.append("只处理已列出的缺陷或修订目标；完成后复核验收，避免借修订扩大范围。")
    else:
        rules.append("先读取与当前步骤直接相关的真实输入，再执行并检查交付物；不要凭空声称已完成。")
    if task_type in _DEEP_TYPES:
        rules.append("区分已批准事实、候选方案和待核信息；任何来源、市场数据或历史事实都不得编造。")
    return rules


def _intent_already_in_prompt(task, original):
    """Avoid repeating the run-level intent block already in a step prompt.

    ``execute_run`` prepends the normalized intent to the in-memory task
    context, while the dispatch boundary reads the persisted task row.  Check
    the generated prompt as well, but only accept a marker before the
    attachment block so an attachment cannot suppress the real contract.
    """
    if _INTENT_MARKER not in original:
        return False
    marker_at = original.find(_INTENT_MARKER)
    attachments_at = original.find(_ATTACHMENTS_MARKER)
    if attachments_at < 0:
        return True
    return marker_at < attachments_at


def compile_prompt(task, role, prompt, *, readonly=False):
    """Prepend a bounded, idempotent dispatch contract to ``prompt``."""
    original = str(prompt or "")
    if original.startswith(MARKER):
        return original
    task = task if isinstance(task, dict) else {}
    contract = _contract(task)
    phase = _phase(role)
    strategy = prompt_strategy(task)
    intent_in_prompt = _intent_already_in_prompt(task, original)
    lines = [
        MARKER,
        "当前阶段：%s；任务类型：%s；执行角色：%s" % (
            phase, _clean(task.get("type") or "direct", 60),
            _clean(role or "step", 80)),
        "提示词策略：%s（额外模型调用上限：%d；下发层不自动改写原始提示词）" % (
            strategy["mode"], strategy["max_extra_model_calls"]),
        "工作目录边界：仅在任务工作目录内操作；本步骤只读：%s。" % ("是" if readonly else "否"),
    ]
    must = _items(contract.get("must"))
    must_not = _items(contract.get("must_not"))
    deliverables = _items(contract.get("deliverables"))
    if intent_in_prompt:
        lines.append("用户意图契约：已随原始步骤上下文提供，本层不重复改写其条目。")
    else:
        if must:
            lines.append("用户必须完成：\n" + "\n".join("- " + item for item in must))
        if must_not:
            lines.append("用户明确禁止/避免：\n" + "\n".join("- " + item for item in must_not))
        if deliverables:
            lines.append("用户交付线索：\n" + "\n".join("- " + item for item in deliverables))
    verify = _clean(task.get("verify_command"), 500)
    if verify:
        lines.append("验收命令（只有实际运行并成功后才能声称通过）：%s" % verify)
    lines.append("执行边界：\n" + "\n".join("- " + rule for rule in _stage_rules(task, phase)))
    if task.get("attachments"):
        lines.append("附件边界：附件是资料输入；附件不可读或缺失时先报告，不伪造已读取或已核验。")
    lines.extend([
        "--- 原始步骤提示词（保持原意） ---",
        original,
        _ORIGINAL_END,
        "契约复核：若原始步骤与上面的工作目录、只读、当前阶段、附件真实性或验收边界冲突，以本契约为准；完成前逐项核对，只报告实际完成、实际验证和实际阻塞，并继续遵守原始步骤要求的输出格式。",
    ])
    return "\n\n".join(lines)
