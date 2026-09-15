# -*- coding: utf-8 -*-
"""能力维度（4D）：按任务类型（writing/coding/reasoning/vision）选模型。

设计稿：docs/migration/05-model-seams.md §4D（按 CodeBee 实际架构落地：
不改 modelhub.py——供应商声明可选 `strengths` 字段即可被识别；
选择逻辑独立成模块，零侵入）。

供应商声明示例（data/models.json 的 providers 条目）：
  {"id": "p1", ..., "strengths": ["writing", "reasoning"]}
未声明 strengths 的供应商视为无偏好（只做兜底）。
"""
from __future__ import annotations

import re

# 能力维度（先固化清单，schema 见设计稿）
DIMS = ("writing", "coding", "reasoning", "vision")

# 关键词分类表（覆盖 task type / 目标文本；可按需扩充）
_KEYWORDS = {
    "writing": ("写", "文案", "大纲", "连载", "章节", "小说", "营销", "总结", "润色",
                "novel", "draft", "review"),
    "coding": ("代码", "实现", "修复", "重构", "测试", "bug", "code", "fix",
               "refactor", "test", "编译", "报错"),
    "reasoning": ("推理", "分析", "调研", "对比", "规划", "拆解", "评审",
                  "reason", "analyze", "plan", "orchestrate"),
    "vision": ("图", "图片", "识别", "视觉", "ocr", "image", "vision", "截图"),
}


def classify_task_type(task) -> str:
    """把任务分类到 DIMS 之一。task 显式带 task_type 时优先。"""
    explicit = str((task or {}).get("task_type") or "").strip().lower()
    if explicit in DIMS:
        return explicit
    text = "%s %s" % ((task or {}).get("type") or "",
                      (task or {}).get("goal") or "")
    scores = {}
    for dim, kws in _KEYWORDS.items():
        scores[dim] = sum(1 for kw in kws if kw in text)
    best = max(scores, key=lambda d: scores[d])
    if scores[best] > 0:
        return best
    return "coding"  # 默认兜底（CodeBee 主场景）


def provider_strengths(prov):
    """读供应商声明的能力维度（容错：非法值忽略）。"""
    raw = (prov or {}).get("strengths")
    if not isinstance(raw, list):
        return []
    return [s for s in raw if s in DIMS]


def pick_by_strength(providers, task_type, *, enabled_only=True):
    """按能力维度挑供应商。

    规则：
      1. 声明了该维度的可用供应商优先（保持原有顺序）；
      2. 未声明的排后面（兜底）；
      3. 都没有声明时返回原顺序（行为同现状）。
    返回 (排序后的供应商列表, 分类依据说明)。
    """
    task_type = task_type if task_type in DIMS else classify_task_type({"goal": task_type})
    pool = list(providers or [])
    if enabled_only:
        pool = [p for p in pool if p.get("enabled", True)]
    strong = [p for p in pool if task_type in provider_strengths(p)]
    rest = [p for p in pool if p not in strong]
    ranked = strong + rest
    reason = "%s：优先 %s" % (
        task_type,
        "、".join(p.get("id", "?") for p in strong) if strong else "（无声明，按原序兜底）")
    return ranked, reason


def resolve_binding_by_task(task, providers, bindings):
    """任务 → 绑定解析（能力维度优先，回落 default）。

    bindings 形如 modelhub 的 {"writing": {...}, "coding": {...}, "default": {...}}；
    对应维度的绑定缺失/为空时回落 default。返回 (binding, task_type)。
    """
    tt = classify_task_type(task)
    b = (bindings or {}).get(tt)
    if not b:
        b = (bindings or {}).get("default")
    return b, tt


# ---------------------------------------------------------------- §07 T3.1 级联

TIER_ORDER = {"budget": 0, "standard": 1, "premium": 2}


def cascade_reorder(agent, tier_of):
    """FrugalGPT 式便宜优先：easy 任务把 call_chain 按 tier 升序稳定重排。

    agent 来自 modelhub.bind_agent（可能带 call_chain）；tier_of(chain_entry)
    返回该条目的档位串（"budget"/"standard"/"premium"或 None=standard）。
    稳定排序保证同档内保持用户配置的原顺序。链长 <2 或非链式原样返回。
    """
    chain = (agent or {}).get("call_chain") or []
    if len(chain) < 2:
        return agent

    def key(entry):
        return TIER_ORDER.get(tier_of(entry) or "standard", 1)

    out = dict(agent)
    out["call_chain"] = sorted(chain, key=key)
    return out


def make_tier_lookup(providers):
    """由 providers 列表构造 chain_entry → tier 的查询函数。

    tier 来源（按优先级）：链条目 model 在供应商 models[] 里的 tier 字段 →
    供应商级 tier → None（按 standard 兜底）。缺什么都不抛。
    """
    by_id = {p.get("id"): p for p in (providers or []) if p.get("id")}

    def tier_of(entry):
        p = by_id.get((entry or {}).get("provider_id") or "")
        if not p:
            return None
        for m in (p.get("models") or []):
            if isinstance(m, dict) and m.get("name") == (entry or {}).get("model"):
                return m.get("tier") or p.get("tier")
        return p.get("tier")
    return tier_of