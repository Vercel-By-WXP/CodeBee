# -*- coding: utf-8 -*-
"""Jev 评审门禁第二意见（P2-2 PoC）。

Jev 输出结构化概率（choice / score / noul），适合判「软问题」，不适合判硬约束
（已装 / 已启用 / 协议兼容 / 凭据冷却 / 地区）。本模块把它接成「评审门禁」的
**非阻断第二意见**：

- 确定性门禁（`pipeline._content_rounds` 里的硬过滤 + 共识放行）照常裁决，结论是唯一
  事实来源；
- Jev 只在旁边给一个 opinion，并把它连同确定性门禁的结论一起落审计
  （``data/audit/jev_review_gate-YYYY-MM.jsonl``，哈希链）；
- 默认关闭（`JEV_ENABLED != "1"`），无 key 时退回确定性 mock 供离线验证，
  **绝不连累主线**——接入点全程 try/except，任何异常都视为「无意见」。

用法（评审门禁处）：
    from . import jev
    jev.note_review_gate(context)   # context 含本轮评审信号 + 确定性 gate_decision

价值排序（按迭代计划 P2-2）：评审门禁 > 难度判定 > 候选内排序。本 PoC 先只接
评审门禁这一点，用已有拒稿实案做对照（同一批稿，Jev 判的轮数 vs 现在判的轮数、
成本、结论是否一致），跑得通再谈进主链。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from . import audit

# 简单模式三品牌位（前端引导用，后端仅作文档常量）。覆盖 Anthropic / OpenAI /
# GitHub 三大订阅生态；任意两家有凭据即可做跨厂商对抗评审（CodeBee 唯一难以复制的点）。
BRAND_SLOTS = (
    ("claude-code", "Claude Code"),
    ("codex-cli", "Codex CLI"),
    ("github-copilot-cli", "GitHub Copilot CLI"),
)

_ENV_ENABLED = "JEV_ENABLED"
_ENV_ENDPOINT = "JEV_ENDPOINT"
_ENV_API_KEY = "JEV_API_KEY"
_ENV_TIMEOUT = "JEV_TIMEOUT_MS"

# 评审门禁允许的结论（与 pipeline 的确定性门禁语义对齐）。
_CHOICES = ("pass", "more", "rewrite")
_DEFAULT_TIMEOUT_S = 10.0


def is_enabled() -> bool:
    """Jev 第二意见总开关。默认关，避免无 key / 无网络时影响主线。"""
    return os.environ.get(_ENV_ENABLED, "").strip() == "1"


def _provider() -> tuple[Optional[str], Optional[str]]:
    """返回 (endpoint, api_key)；未配置则 (None, None) → 走 mock。"""
    ep = os.environ.get(_ENV_ENDPOINT, "").strip()
    key = os.environ.get(_ENV_API_KEY, "").strip()
    if ep and key:
        return ep, key
    return None, None


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get(_ENV_TIMEOUT, "").strip()) / 1000.0)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _safe_ctx(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """只保留结构化、不可还原原文的字段进审计（对齐 audit.py 不落原文纪律）。"""
    ctx = ctx or {}
    return {
        "round": int(ctx.get("round") or 0),
        "max_rounds": int(ctx.get("max_rounds") or 0),
        "task_type": str(ctx.get("task_type") or "")[:40],
        "avg_score": _num(ctx.get("avg_score")),
        "seat_count": int(ctx.get("seat_count") or 0),
        "issue_count": int(ctx.get("issue_count") or 0),
        "gate_decision": str(ctx.get("gate_decision") or "")[:24],
    }


def _num(v) -> float:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return 0.0


def _clamp_score(v) -> int:
    try:
        s = int(round(float(v)))
    except (TypeError, ValueError):
        s = 6
    return max(2, min(10, s))


def _mock_assess(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """确定性 mock：无 key / 不可达时的离线兜底，结果可复现、可对照。

    规则（仅占位，真实 Jev 概率模型会替换）：分数够高且还有轮次余量时建议继续，
    否则放行；分数过低建议重写。
    """
    avg = _num(ctx.get("avg_score"))
    r = int(ctx.get("round") or 0)
    maxr = int(ctx.get("max_rounds") or 0)
    # avg_score 已是 0-10 评分均值，直接作置信分（钳到 2..10）。
    score = _clamp_score(avg)
    if score >= 8 and (maxr == 0 or r < maxr):
        choice, noul = "more", False
    elif score >= 6:
        choice, noul = "pass", False
    else:
        choice, noul = "rewrite", True
    return {"choice": choice, "score": score, "noul": noul,
            "source": "mock", "raw": None}


def _parse(data: Any) -> Dict[str, Any]:
    """把 Jev 返回解析成 {choice, score, noul}（对嵌套结构做兼容）。"""
    if not isinstance(data, dict):
        data = {}
    # 兼容 {"data": {...}} / {"result": {...}} 包裹
    for key in ("data", "result", "payload"):
        if isinstance(data.get(key), dict):
            data = data[key]
            break
    choice = str(data.get("choice") or "").strip().lower()
    if choice not in _CHOICES:
        # 退化：score 高→pass，低→rewrite
        choice = "pass" if _clamp_score(data.get("score")) >= 6 else "rewrite"
    noul = bool(data.get("noul", data.get("needs_another_round", False)))
    return {"choice": choice, "score": _clamp_score(data.get("score")),
            "noul": noul, "source": "jev", "raw": data}


def assess(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """给定评审门禁上下文，返回 Jev 意见。任何异常都退回 mock，绝不抛出。"""
    ep, key = _provider()
    if not ep:
        return _mock_assess(ctx)
    try:
        return _http_assess(ep, key, ctx)
    except Exception:
        return _mock_assess(ctx)


def _http_assess(ep: str, key: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps({
        "prompt": _build_prompt(ctx),
        "context": _safe_ctx(ctx),
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ep, data=body,
        headers={"Authorization": "Bearer %s" % key,
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        return _parse(json.loads(resp.read().decode("utf-8")))


def _build_prompt(ctx: Dict[str, Any]) -> str:
    """构造给 Jev 的评审门禁问题（结构化，便于审计与复现）。"""
    avg = _num(ctx.get("avg_score"))
    r = int(ctx.get("round") or 0)
    maxr = int(ctx.get("max_rounds") or 0)
    seats = int(ctx.get("seat_count") or 0)
    issues = int(ctx.get("issue_count") or 0)
    return (
        "评审门禁决策：当前第 {r} 轮（上限 {maxr}），{seats} 席评审平均分 {avg}，"
        "遗留问题 {issues} 条。请从 {{pass(放行), more(追加一轮), rewrite(退回重写)}} 中选一个，"
        "并给出 2-10 的置信分与 noul（是否还需要追加一轮）。".format(
            r=r, maxr=maxr, seats=seats, avg=avg, issues=issues)
    )


# ---------------------------------------------------------------- 路由侧：编排者候选 JEV 概率重排
#
# 迭代计划 P2-2「待接」①：路由侧把 Jev 接进候选选择——硬过滤（已启用/凭据/健康/
# 模型可用）照常由调用方负责，这里只在过滤后的候选集上做 Jev 概率重排。与评审门禁
# 的 assess 不同：mock 不是排序器，无 key 时不重排（确定性评分本身就是基线），
# 任何异常一律返回 None，调用方保持原序——绝不连累主线。

def _safe_pair(c) -> str:
    """候选 → "provider_id|model" 键（审计用，不含自由文本）。"""
    if not isinstance(c, dict):
        return ""
    return "%s|%s" % (c.get("provider_id") or "", c.get("model") or "")


def _safe_rank_ctx(candidates: Any) -> Dict[str, Any]:
    """路由候选只保留结构化字段进请求与审计，不落原文。"""
    out = []
    for c in (candidates or [])[:16]:
        out.append({
            "provider_id": str(c.get("provider_id") or "")[:40],
            "model": str(c.get("model") or "")[:80],
            "score": _num(c.get("score")),
            "reason": str(c.get("reason") or "")[:160],
        })
    return {"candidates": out}


def _build_rank_prompt(candidates: Any) -> str:
    """构造给 Jev 的选模问题（结构化，便于审计与复现）。"""
    lines = []
    for i, c in enumerate((candidates or [])[:16], 1):
        lines.append("{i}. {provider} / {model}（确定性分 {score}）".format(
            i=i, provider=c.get("provider_id") or "", model=c.get("model") or "",
            score=_num(c.get("score"))))
    return (
        "为编排者选择最优供应商/模型。以下候选已通过硬过滤（已启用/有凭据/健康/模型可用），"
        "并各有确定性评分。请综合质量、成本、能力匹配与近期实测表现，"
        "返回一个概率加权的排序。\n" + "\n".join(lines)
    )


def _extract_order(data: Any) -> list:
    """从 Jev 响应提取有序 (provider_id, model) 键序列；不可解析返回空列表。

    兼容三种形状：
    - {"ranking": [{"provider_id","model","prob"?}, ...]}   按列表序
    - {"order": ["pid|model", ...]} 或 [{"provider_id","model"}, ...]
    - {"weights": {"pid|model": prob, ...}}                 按 prob 降序
    """
    def to_pair(x):
        if isinstance(x, dict):
            pid = str(x.get("provider_id") or "").strip()
            model = str(x.get("model") or "").strip()
            return (pid, model) if pid and model else None
        if isinstance(x, str) and "|" in x:
            pid, _, model = x.partition("|")
            pid, model = pid.strip(), model.strip()
            return (pid, model) if pid and model else None
        return None

    ranking = data.get("ranking") if isinstance(data, dict) else None
    if isinstance(ranking, list):
        order = []
        for item in ranking:
            p = to_pair(item)
            if p:
                order.append(p)
        if order:
            return order
    raw_order = data.get("order") if isinstance(data, dict) else None
    if isinstance(raw_order, list):
        order = []
        for item in raw_order:
            p = to_pair(item)
            if p:
                order.append(p)
        if order:
            return order
    weights = data.get("weights") if isinstance(data, dict) else None
    if isinstance(weights, dict):
        pairs = []
        for k, v in weights.items():
            p = to_pair(k)
            if p:
                try:
                    pairs.append((float(v), p))
                except (TypeError, ValueError):
                    pairs.append((0.0, p))
        pairs.sort(key=lambda t: -t[0])
        return [p for _, p in pairs]
    return []


def _reorder(candidates: Any, data: Any) -> Optional[list]:
    """按 Jev 返回的排序重排候选；不可解析或结果无效时返回 None。"""
    if not isinstance(data, dict):
        return None
    for key in ("data", "result", "payload"):
        if isinstance(data.get(key), dict):
            data = data[key]
            break
    order = _extract_order(data)
    if not order:
        return None
    keyed = {(c.get("provider_id"), c.get("model")): c for c in (candidates or [])}
    out, seen = [], set()
    for pid, model in order:
        c = keyed.get((pid, model))
        if c is None or (pid, model) in seen:
            continue
        seen.add((pid, model))
        out.append(c)
    # Jev 未提及的候选追加尾部（保持原相对序），绝不丢候选
    for c in (candidates or []):
        k = (c.get("provider_id"), c.get("model"))
        if k not in seen:
            out.append(c)
    if len(out) != len(candidates or []):
        return None
    return out


def _http_rank(ep: str, key: str, candidates: Any) -> Optional[list]:
    body = json.dumps({
        "task": "orchestrator_select",
        "prompt": _build_rank_prompt(candidates),
        "context": _safe_rank_ctx(candidates),
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ep, data=body,
        headers={"Authorization": "Bearer %s" % key,
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        return _reorder(candidates, json.loads(resp.read().decode("utf-8")))


def rank_candidates(candidates: Any) -> Optional[list]:
    """编排者候选的 Jev 概率重排（路由侧）。

    candidates 为已过硬过滤、按确定性评分排序的候选列表，每项含
    provider_id / model / score / reason。返回 Jev 重排后的新列表；
    Jev 未启用、无凭据、调用失败或响应不可解析时返回 None，
    调用方保持原序。绝不抛、绝不连累主线。
    """
    if not is_enabled() or not candidates:
        return None
    ep, key = _provider()
    if not ep:
        return None   # 无 key 的 mock 不是排序器，不用于路由
    try:
        ranked = _http_rank(ep, key, candidates)
    except Exception:
        return None
    if ranked:
        try:
            audit.record("jev_orchestrator_rank", {
                "chosen": _safe_pair(ranked[0]),
                "prev": _safe_pair(candidates[0]),
                "n": len(ranked),
                "source": "jev",
            })
        except Exception:
            pass
    return ranked


def note_review_gate(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """非阻断第二意见 + 审计落盘。禁用或异常时返回 None，不抛。

    调用方应在**确定性门禁已给出结论**后调用，并把 ``gate_decision`` 写进 ctx，
    使审计同时保留「Jev 意见」与「确定性门禁结论」两行，便于事后对照
    （同一批稿，Jev 判的 vs 现在判的，轮数 / 成本 / 结论是否一致）。
    """
    if not is_enabled():
        return None
    ctx = ctx or {}
    try:
        opinion = assess(ctx)
        audit.record("jev_review_gate", {
            "context": _safe_ctx(ctx),
            "jev_choice": opinion.get("choice"),
            "jev_score": opinion.get("score"),
            "jev_noul": bool(opinion.get("noul")),
            "jev_source": opinion.get("source"),
            "gate_decision": str(ctx.get("gate_decision") or "")[:24],
        })
        return opinion
    except Exception:
        return None


__all__ = ["BRAND_SLOTS", "is_enabled", "assess", "rank_candidates", "note_review_gate"]
