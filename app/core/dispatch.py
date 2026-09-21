# -*- coding: utf-8 -*-
"""统一调度评分：任务画像、CLI 亲和度与模型链排序。

调用方先完成协议、启停、健康、密钥等硬约束过滤，再把可用候选交给这里评分；
静态能力与近期用量台账只提供软信号，相同分数保持用户原顺序。
"""
from __future__ import annotations

import math


TYPE_DIMENSIONS = {
    "direct": "reasoning",
    "code": "coding",
    "novel": "writing",
    "serial_novel": "writing",
    "article": "writing",
    "video_script": "writing",
    "doc": "writing",
    "translation": "writing",
    "rank_scan": "reasoning",
    "research": "reasoning",
    "speech": "writing",
    "weekly_report": "writing",
    "email": "writing",
    "tech_proposal": "reasoning",
    "resume": "writing",
    "bid_doc": "writing",   # 标书：正文应答写作量大（合规判断由契约规则约束）
    "zentao": "coding",
}

_KIND_AFFINITY = {
    "coding": {"codex": 10, "aider": 8, "opencode": 7, "qwen": 6,
               "claude": 5, "generic": 2},
    "writing": {"claude": 10, "qwen": 7, "generic": 5, "codex": 3,
                "opencode": 2, "aider": 0},
    "reasoning": {"claude": 9, "codex": 8, "qwen": 6, "opencode": 4,
                  "generic": 3, "aider": 1},
    "vision": {"codex": 10, "claude": 8, "qwen": 5, "opencode": 2,
               "generic": 1, "aider": 0},
}

_TIER_SCORE = {
    "easy": {"budget": 18.0, "standard": 8.0, "premium": -6.0},
    "default": {"budget": 2.0, "standard": 6.0, "premium": 5.0},
    "hard": {"budget": -8.0, "standard": 4.0, "premium": 12.0},
}


def task_dimension(task_type, role=""):
    """把预置任务与步骤角色归一为 writing/coding/reasoning/vision。"""
    ttype = str(task_type or "").strip().lower()
    role = str(role or "").strip().lower()
    if ttype in ("writing", "coding", "reasoning", "vision"):
        return ttype
    if role in ("plan", "review", "critique", "selector", "qa") \
            or "review" in role or "critique" in role:
        return "reasoning"
    return TYPE_DIMENSIONS.get(ttype, "reasoning")


def agent_affinity(kind, task_type, role=""):
    """CLI 类型对任务维度的温和偏好；只作加分，不覆盖健康与历史信号。"""
    dim = task_dimension(task_type, role)
    score = float((_KIND_AFFINITY.get(dim) or {}).get(kind, 0))
    if role == "review" or "critique" in str(role or ""):
        score += {"claude": 4, "codex": 3, "qwen": 2}.get(kind, 0)
    return score, "%s 匹配 %+.1f" % (dim, score)


def _model_meta(provider, model):
    for item in (provider or {}).get("models") or []:
        if isinstance(item, dict) and item.get("name") == model:
            return item
    return {}


def _price_score(pricing, model, difficulty):
    price = (pricing or {}).get(model) or {}
    try:
        # 输出通常比输入贵且更影响整步成本，按 2 倍权重估算。
        blended = max(0.0, float(price.get("in") or 0.0)
                      + 2.0 * float(price.get("out") or 0.0))
    except (TypeError, ValueError):
        return 0.0, "价格未知"
    if blended <= 0:
        return 0.0, "价格未知"
    magnitude = max(0.0, math.log10(blended + 1.0))
    score = -min(18.0, magnitude * (7.0 if difficulty == "easy" else 2.0))
    return score, "估算价 %.3g（%+.1f）" % (blended, score)


def _online_model_bonus(entry, task_type, role):
    """把真实运行指标作为模型链软信号，硬约束仍由调用方负责。"""
    try:
        from . import usage
        metrics = usage.routing_stats(
            task_type=task_type, role=role,
            provider=entry.get("provider_id") or "",
            model=entry.get("model") or "")
        samples = int(metrics.get("samples") or 0)
        if not samples:
            return 0.0, ""
        rate = float(metrics.get("success_rate") or 0.0)
        success_score = max(-6.0, min(6.0, (rate - 0.75) * 18.0))
        p95 = max(0.0, float(metrics.get("p95_duration_s") or 0.0))
        # 模型链也必须让真实延迟影响排序，不能让 P95 数分钟的链靠静态质量分
        # 长期压在十几秒的健康链前面。
        latency_score = -min(12.0, max(0.0, (p95 - 30.0) / 8.0))
        cost = max(0.0, float(metrics.get("avg_cost_usd") or 0.0))
        cost_score = -min(3.0, max(0.0, (cost - 0.01) / 0.01))
        total = round(success_score + latency_score + cost_score, 2)
        success_samples = int(metrics.get("success_samples") or samples)
        reason = ("在线 %d/%d 验收成功（%+.1f），P95 %.1fs（%+.1f），均价 $%.4f（%+.1f）"
                  % (int(metrics.get("successes") or 0), success_samples,
                     success_score, p95, latency_score, cost, cost_score))
        return total, reason
    except Exception:
        return 0.0, ""


def score_model_entry(entry, providers, pricing, difficulty, task_type="", role=""):
    """给已通过硬约束的模型链条目评分，返回 (score, explanation)。"""
    provider = (providers or {}).get(entry.get("provider_id")) \
        or entry.get("provider") or {}
    model = entry.get("model") or ""
    meta = _model_meta(provider, model)
    try:
        priority = max(1, int(meta.get("priority") or 99))
    except (TypeError, ValueError):
        priority = 99
    quality = 0.0 if priority == 99 else max(-8.0, 20.0 - (priority - 1) * 4.0)
    tier = meta.get("tier") or provider.get("tier") or "standard"
    tier_score = (_TIER_SCORE.get(difficulty) or {}).get(tier, 0.0)
    price_score, price_reason = _price_score(pricing, model, difficulty)
    dim = task_dimension(task_type, role)
    strengths = provider.get("strengths") if isinstance(provider.get("strengths"), list) else []
    strength_score = 10.0 if dim in strengths else 0.0
    if difficulty == "easy":
        quality *= 0.25
    elif difficulty != "hard":
        # 普通任务兼顾质量、成本与能力匹配；是否真的重排由调用方决定，
        # 因而手工链仍可保持原顺序，自动推荐则能使用这组平衡分。
        quality *= 0.65
    vision_score = 0.0
    if dim == "vision":
        vision_score = 24.0 if meta.get("image_in") else -24.0
    online_score, online_reason = _online_model_bonus(entry, task_type, role)
    total = quality + tier_score + price_score + strength_score + vision_score + online_score
    reason = ("质量 %+.1f，档位 %s %+.1f，%s，能力 %s %+.1f%s%s"
              % (quality, tier, tier_score, price_reason, dim,
                 strength_score + vision_score,
                 "，" if online_reason else "", online_reason))
    return round(total, 2), reason


def rank_model_entries(entries, providers, pricing, difficulty,
                       task_type="", role="", force=False):
    """稳定排序模型链并返回脱敏决策明细；default 难度保持人工顺序。"""
    rows = []
    for index, entry in enumerate(entries or []):
        score, reason = score_model_entry(
            entry, providers, pricing, difficulty, task_type, role)
        rows.append((score, index, entry, reason))
    if difficulty in ("easy", "hard") or force:
        rows.sort(key=lambda row: (-row[0], row[1]))
    ranked = [row[2] for row in rows]
    decisions = [{"provider_id": row[2].get("provider_id") or "",
                  "provider": (row[2].get("provider") or {}).get("name") or "",
                  "model": row[2].get("model") or "",
                  "score": row[0], "reason": row[3]}
                 for row in rows]
    return ranked, decisions
