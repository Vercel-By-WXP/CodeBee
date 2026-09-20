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
