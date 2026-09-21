# -*- coding: utf-8 -*-
"""任务编译器：把用户任务和预置流程编译成统一、可审计的运行规格。"""
from __future__ import annotations

from collections.abc import Mapping

from . import dispatch, flows

SCHEMA_VERSION = 1
DIFFICULTIES = ("easy", "default", "hard")


def _difficulty(task, flow):
    explicit = str((task or {}).get("difficulty") or "").strip().lower()
    if explicit in DIFFICULTIES:
        return explicit, "用户指定"
    try:
        threshold = float((task or {}).get("threshold")
                          if (task or {}).get("threshold") is not None
                          else (flow or {}).get("threshold") or 7.0)
    except (TypeError, ValueError):
        threshold = 7.0
    if threshold >= 8.5:
        return "hard", "高质量门槛"
    if threshold <= 6.0:
        return "easy", "快速交付门槛"
    if str((task or {}).get("type") or "").lower() in (
            "research", "tech_proposal", "rank_scan"):
        return "hard", "研究/方案类默认需要深度"
    return "default", "标准难度"


def _capabilities(task, dimension, engine):
    caps = [dimension]
    if engine == "code" or (task or {}).get("verify_command"):
        caps.extend(["filesystem", "verification"])
    if (task or {}).get("serial"):
        caps.extend(["long_context", "continuity"])
    if (task or {}).get("attachments"):
        caps.append("attachments")
    return list(dict.fromkeys(caps))


def compile_task(task):
    """返回统一任务规格；输入缺失或字段异常时保持可编排的安全兜底。"""
    raw = dict(task) if isinstance(task, Mapping) else {}
    ttype = str(raw.get("type") or "direct").strip().lower()
    flow = flows.get_flow(ttype) or {}
    engine = str(raw.get("engine") or flow.get("engine") or
                 ("code" if ttype == "code" else "review")).strip()
    if engine not in flows.ENGINES:
        engine = flow.get("engine") or ("code" if ttype == "code" else "review")
    difficulty, difficulty_reason = _difficulty(raw, flow)
    dimension = dispatch.task_dimension(ttype, raw.get("role") or "")
    rubric = raw.get("rubric") or flow.get("rubric") or []
    if isinstance(rubric, str):
        rubric = [x.strip() for x in rubric.replace("，", ",").split(",") if x.strip()]
    elif not isinstance(rubric, (list, tuple)):
        rubric = flow.get("rubric") or []
    rubric = [str(x).strip() for x in rubric if str(x).strip()][:8]
    deliverable = str(raw.get("manuscript") or flow.get("manuscript") or "").strip()
    serial = raw.get("serial") if isinstance(raw.get("serial"), dict) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "type": ttype,
        "title": str(raw.get("title") or "")[:120],
        "goal": str(raw.get("goal") or "")[:2000],
        "engine": engine,
        "dimension": dimension,
        "difficulty": difficulty,
        "difficulty_reason": difficulty_reason,
        "thinking": (str(raw.get("thinking") or "standard").lower()
                     if str(raw.get("thinking") or "standard").lower()
                     in ("auto", "low", "standard", "high") else "auto"),
        "capabilities": _capabilities(raw, dimension, engine),
        "deliverable": deliverable,
        "quality_dimensions": rubric,
        "serial": serial,
        "explicit_agent": str(raw.get("implementer") or "").strip(),
        "has_verification": bool(raw.get("verify_command")),
    }


def summary(spec):
    """给运行日志/UI 的短说明，不包含用户正文。"""
    spec = spec or {}
    return "%s/%s/%s（能力：%s）" % (
        spec.get("type") or "direct", spec.get("engine") or "direct",
        spec.get("difficulty") or "default",
        "、".join(spec.get("capabilities") or []) or "通用")


def code_workflow(task, difficulty, plan=None, mode="auto", planned=True):
    """把难度、验收能力和规划建议收敛成代码任务的实际步骤策略。

    自动模式允许低风险任务走短链；手动模式与高风险任务保留完整质量门禁。
    规划器只能在安全边界内减少步骤，不能让无确定性验证的任务跳过评审。
    """
    task = task if isinstance(task, Mapping) else {}
    rec = (plan or {}).get("workflow") if isinstance(plan, Mapping) else None
    rec = rec if isinstance(rec, Mapping) else {}
    has_verify = bool(str(task.get("verify_command") or "").strip())
    risk_text = " ".join(str(task.get(k) or "") for k in ("title", "goal", "context")).lower()
    high_risk = bool(any(k in risk_text for k in (
        "安全", "权限", "鉴权", "认证", "加密", "密钥", "注入", "迁移", "数据库",
        "并发", "竞态", "支付", "架构", "公共接口", "public api", "公共 api",
        "breaking change", "security", "permission", "auth", "migration",
        "concurrency", "architecture")))
    fast_mode = mode == "fast"
    expert_mode = mode == "expert"
    easy_auto = (mode == "auto" and difficulty == "easy") or fast_mode

    review_required = True
    max_repairs = 2
    allow_switch = True
    reason = "复杂或手动任务使用完整质量门禁"
    if easy_auto:
        review_required = high_risk or not has_verify
        max_repairs = 1
        allow_switch = False
        reason = ("低风险任务以确定性验证代替模型评审"
                  if has_verify else "低风险任务无验证命令，保留一次模型评审")
        if isinstance(rec.get("review_required"), bool):
            review_required = high_risk or rec["review_required"] or not has_verify
        try:
            if "max_repair_rounds" in rec:
                max_repairs = max(1 if not has_verify else 0,
                                  min(1, int(rec["max_repair_rounds"])))
        except (TypeError, ValueError):
            pass
    elif expert_mode or (mode == "auto" and difficulty == "hard"):
        review_required = True
        max_repairs = 2
        allow_switch = True
        try:
            if "max_repair_rounds" in rec:
                max_repairs = max(1, min(2, int(rec["max_repair_rounds"])))
        except (TypeError, ValueError):
            pass
        if isinstance(rec.get("allow_switch"), bool):
            allow_switch = rec["allow_switch"]
        reason = "复杂任务保留规划、验证与模型评审"

    return {
        "planning": "llm" if planned else "inline",
        "implementation_steps": len((plan or {}).get("steps") or []) or 1,
        "verification": has_verify,
        "review_required": review_required,
        "reviewers": 1 if review_required else 0,
        "max_repair_rounds": max_repairs,
        "allow_switch": allow_switch,
        "reason": reason,
    }


def content_workflow(task, difficulty, plan=None, mode="auto"):
    """为非连载内容任务生成动态大纲/评审/修订策略。"""
    task = task if isinstance(task, Mapping) else {}
    ttype = str(task.get("type") or "doc")
    requested_rounds = max(1, min(5, int(task.get("rounds") or 2)))
    rec = (plan or {}).get("workflow") if isinstance(plan, Mapping) else None
    rec = rec if isinstance(rec, Mapping) else {}
    light_types = {"email", "weekly_report", "translation"}
    deep_types = {"novel", "research", "tech_proposal"}

    if mode == "fast":
        return {"outline": False, "reviewers": 1, "review_rounds": 1,
                "reason": "快速模式省略独立大纲，仅保留一次快速评审"}
    if mode == "expert":
        return {"outline": True, "reviewers": 2,
                "review_rounds": min(requested_rounds, 3),
                "reason": "专家模式启用完整大纲、多评审与修订门禁"}
    if mode != "auto":
        return {"outline": True, "reviewers": 0,
                "review_rounds": requested_rounds,
                "reason": "手动模式尊重用户指定的评审组与轮数"}

    if ttype in light_types and difficulty != "hard":
        return {"outline": False, "reviewers": 1, "review_rounds": 1,
                "reason": "轻量内容省略独立大纲，只做一次快速评审"}

    outline = ttype in deep_types or difficulty == "hard"
    reviewers = 2 if outline else 1
    review_rounds = min(requested_rounds, 2 if outline else 1)
    try:
        if "reviewers" in rec:
            reviewers = max(1, min(2, int(rec["reviewers"])))
        if "review_rounds" in rec:
            review_rounds = max(1, min(requested_rounds, 3,
                                       int(rec["review_rounds"])))
    except (TypeError, ValueError):
        pass
    # 深度类型至少保留一轮评审，但允许规划器把双评审降成一名；高门槛强制双评审。
    if float(task.get("threshold") or 7.0) >= 8.5:
        reviewers = 2
    return {"outline": outline, "reviewers": reviewers,
            "review_rounds": review_rounds,
            "reason": ("深度内容保留大纲和多轮质量门禁" if outline
                       else "标准内容使用单评审短链")}
