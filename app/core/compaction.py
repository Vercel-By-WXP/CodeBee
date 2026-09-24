# -*- coding: utf-8 -*-
"""三段式上下文压缩：工具结果剪枝 → LLM 摘要 → surface replace。

设计稿：docs/migration/02-context-compaction.md §1B。
参考 dsh：
  packages/compaction/compaction-basic/src/region.ts:117-155（selectCompactableRange：
    跳过 system 头、保留尾预算、不切断 tool call/result 配对）
  packages/compaction/compaction-tool-result-pruner/src/index.ts:83-122（头尾剪枝）
  packages/compaction/compaction-basic/src/region.ts:173-275（压缩事务四件事件：
    compaction/start → summary → user/message(replace) → end；失败仍写 end 保证日志可见）

触发：maybe_compact() 检查 token_meter 压力比 ≥ 阈值才动手。
"""
from __future__ import annotations

import logging

from .token_meter import token_meter, DEFAULT_PRESSURE_THRESHOLD

log = logging.getLogger(__name__)

# 工具结果剪枝参数（字符数；dsh 默认 8192/4096，单行 marker）
PRUNE_HEAD = 8192
PRUNE_TAIL = 4096
PRUNE_MARKER = "\n…[中间内容已剪枝，原文见会话日志]…\n"

# 尾部保留预算（估算 token）
RETAIN_TAIL_TOKENS = 8000
# 单次压缩区域上限（事件数，防一次吞太多）
MAX_REGION_EVENTS = 50


def estimate_tokens(text: str) -> int:
    """极简估算：4 字符/token（中文偏低估，偏保守触发）。"""
    return max(1, len(text or "") // 4)


def _ev_text(ev) -> str:
    return str(ev.data.get("content") or ev.data.get("stdout") or "")


def prune_text(text: str, *, head=PRUNE_HEAD, tail=PRUNE_TAIL, marker=PRUNE_MARKER) -> str:
    """超长文本头尾剪枝（纯无 LLM 调用）。"""
    if text is None:
        return ""
    if len(text) <= head + tail + len(marker):
        return text
    return text[:head] + marker + text[-tail:]


def select_range(session, *, retain_tail_tokens=RETAIN_TAIL_TOKENS,
                 max_region_events=MAX_REGION_EVENTS):
    """选可压缩区域 [start_seq, end_seq]（闭区间）。

    规则（仿 dsh selectCompactableRange）：
      1. 跳过 system 头节点（从第一条 user_message 起）；
      2. 尾部保留 retain_tail_tokens 预算不参与压缩（retain<=0 表示尾部不保留）；
      3. 全部内容都在尾预算内 → 无可压缩区域，返回 (0, 0)；
      4. 区域末端不能落在 tool 配对中间（回退到配对完成处）；
      5. 区域不超过 max_region_events 个事件。
    返回 (start_seq, end_seq)；无可压缩区域返回 (0, 0)。
    """
    events = session.events()
    surface_events = [e for e in events if e.type in
                      ("system_message", "user_message", "assistant_message")]
    if not surface_events:
        return 0, 0
    first_user_seq = next((e.seq for e in surface_events
                           if e.type == "user_message"), 0)
    if not first_user_seq:
        return 0, 0
    # 尾部保留：倒推预算。tail_start_seq = 保留尾段的第一个 seq。
    last_seq = surface_events[-1].seq
    if retain_tail_tokens <= 0:
        tail_start_seq = last_seq + 1  # 尾部不保留：全部可压
    else:
        tail_budget = 0
        tail_start_seq = 0  # 0 表示"全部都在预算内"（无区域）
        for ev in reversed(surface_events):
            tail_budget += estimate_tokens(_ev_text(ev))
            if tail_budget >= retain_tail_tokens:
                tail_start_seq = ev.seq
                break
        if not tail_start_seq:
            return 0, 0  # 预算从未被突破 → 无可压缩区域
    # 候选区域 = [first_user_seq, tail_start_seq)
    all_events = session.events()
    by_seq = {e.seq: e for e in all_events}
    end_seq = tail_start_seq - 1
    # 不切断 tool 配对：末端若是 tool_call/tool_result，回退到最近的非 tool 事件
    while end_seq >= first_user_seq:
        ev = by_seq.get(end_seq)
        if ev is None or ev.type not in ("tool_call", "tool_result"):
            break
        end_seq -= 1
    # 区域事件数上限
    region_events = [e for e in all_events
                     if first_user_seq <= e.seq <= end_seq]
    if len(region_events) > max_region_events:
        end_seq = region_events[max_region_events - 1].seq
        # 再做一次配对回退
        while end_seq >= first_user_seq:
            ev = by_seq.get(end_seq)
            if ev is None or ev.type not in ("tool_call", "tool_result"):
                break
            end_seq -= 1
    if end_seq < first_user_seq:
        return 0, 0
    return first_user_seq, end_seq


_SUMMARY_SYSTEM = (
    "你是会话压缩器。把以下对话历史压缩成简洁摘要，必须保留：\n"
    "1. 关键决策与结论；2. 涉及的文件路径与命令；3. 未完成事项；\n"
    "4. 错误信息原文。直接输出摘要正文，不要客套。"
)


def compact_region(session, start_seq: int, end_seq: int, llm_caller,
                   *, reason: str = "pressure"):
    """执行一次压缩事务（四件事件，失败仍写 compaction_end 保证日志可见）。

    llm_caller(messages: list[dict]) -> str：一次 LLM 调用，返回摘要文本。
    """
    tx = session.append("compaction_start",
                        {"start_seq": start_seq, "end_seq": end_seq, "reason": reason},
                        surface_op="shadow")
    summary = ""
    error = ""
    try:
        region_events = [e for e in session.events()
                         if start_seq <= e.seq <= end_seq]
        # 第一段：工具结果先剪枝（省摘要调用的输入 token）
        parts = []
        for ev in region_events:
            text = _ev_text(ev)
            if ev.type == "tool_result":
                text = prune_text(text)
            if text.strip():
                parts.append(f"[{ev.type}#{ev.seq}] {text}")
        region_text = "\n\n".join(parts)
        raw_tokens = estimate_tokens(region_text)
        summary = llm_caller([
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": region_text},
        ])
        summary_tokens = estimate_tokens(summary)
        session.append("compaction_summary",
                       {"raw_tokens": raw_tokens,
                        "summary_tokens": summary_tokens,
                        "start_seq": start_seq, "end_seq": end_seq},
                       surface_op="shadow")
        # 台账入账（A 专项：省了多少不再是黑箱）——摘要调用本身的真实消耗
        # （读 region、写 summary）此前完全隐形；saved=净省量与消耗并列成账。
        # record 自吞一切异常，统计绝不拖垮压缩事务。
        try:
            from . import usage as _usage
            _usage.record(source="compaction",
                          run_id=str(getattr(session, "run_id", "") or ""),
                          tool="compaction",
                          usage={"input": raw_tokens,
                                 "output": summary_tokens,
                                 "saved": max(0, raw_tokens - summary_tokens)},
                          ok=True)
        except Exception:
            pass
        # surface replace：把区域折叠为一条 user 摘要消息
        session.append("user_message", {
            "content": f"[系统压缩摘要 {start_seq}-{end_seq}，原因 {reason}]\n{summary}",
        }, surface_op={"op": "replace", "start_seq": start_seq, "end_seq": end_seq})
    except Exception as e:
        error = repr(e)
        log.warning("compact_region failed: %s", error)
        session.append("compaction_summary", {"error": error}, surface_op="shadow")
    finally:
        session.append("compaction_end",
                       {"tx_seq": tx.seq, "error": error},
                       surface_op="shadow")
    return not error


def maybe_compact(session, *, model: str = "", llm_caller=None,
                  threshold: float = DEFAULT_PRESSURE_THRESHOLD,
                  run_id: str = None,
                  retain_tail_tokens: int = RETAIN_TAIL_TOKENS) -> bool:
    """压力比 ≥ 阈值时触发一次压缩。返回是否执行了压缩。

    llm_caller 缺省直接跳过（没有摘要能力就不压）。
    """
    if llm_caller is None:
        return False
    rid = run_id or session.run_id
    ratio = token_meter.pressure_ratio(rid, model=model)
    if ratio < threshold:
        return False
    start, end = select_range(session, retain_tail_tokens=retain_tail_tokens)
    if not start:
        return False
    log.info("compaction triggered run=%s ratio=%.2f region=[%d,%d]",
             rid, ratio, start, end)
    return compact_region(session, start, end, llm_caller, reason=f"pressure={ratio:.2f}")
