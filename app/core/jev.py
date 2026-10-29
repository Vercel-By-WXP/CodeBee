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


__all__ = ["BRAND_SLOTS", "is_enabled", "assess", "note_review_gate"]
