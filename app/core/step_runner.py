# -*- coding: utf-8 -*-
"""Step 执行器：压缩触发的重试守门（复用已渲染 prompt，不重复组装）。

设计稿：docs/migration/02-context-compaction.md §1D。
参考 dsh packages/core/agent-loop/src/agent.ts:307-378：
  失败重试只在「前进 surface generation」时返回 {kind:'retry'}，
  复用已渲染 PromptAssembly，不重跑 pre-step / 组装 / 用户消息准入。

Tutti 语境：runner.run_agent 自带 prompt（已渲染）；重试的风险不是重复渲染
（prompt 是同一个字符串），而是「不压缩直接重试」必然再次撑爆。
本模块的守门：仅当 maybe_compact 真正前进了 surface generation 才重试一次。
"""
from __future__ import annotations

import logging

from .compaction import maybe_compact
from .error_codes import ErrorCode

log = logging.getLogger(__name__)

# 触发「压缩后重试」的错误码
_OVERFLOW_CODES = {ErrorCode.CONTEXT_OVERFLOW, ErrorCode.MAX_TOKENS}


def execute_step(session, run_agent_fn, prompt, *, model: str = "",
                 llm_caller=None, retain_tail_tokens=None, **kwargs):
    """执行一次 step；撑爆时压缩并守门重试一次。

    Args:
        session: session_log.Session（记录 replace_generation）
        run_agent_fn: runner.run_agent（或测试替身），签名 (prompt, **kwargs) -> dict
        prompt: 已渲染的提示词（重试时原样复用，不重新渲染）
        model: token_meter 压力估算用模型名
        llm_caller: 压缩摘要 LLM（None = 不压缩，永不重试）
        **kwargs: 透传 run_agent_fn

    Returns:
        (result, retried: bool)
    """
    gen0 = session.replace_generation()
    result = run_agent_fn(prompt, **kwargs)
    code = result.get("error_code") or ""
    if code not in _OVERFLOW_CODES or llm_caller is None:
        return result, False
    # 撑爆 → 尝试压缩
    compact_kwargs = {}
    if retain_tail_tokens is not None:
        compact_kwargs["retain_tail_tokens"] = retain_tail_tokens
    compacted = maybe_compact(session, model=model, llm_caller=llm_caller,
                              run_id=session.run_id, **compact_kwargs)
    gen1 = session.replace_generation()
    if not compacted or gen1 <= gen0:
        # 压缩未发生或未前进 generation：重试必然再次撑爆，不重试
        log.info("overflow but no compaction progress (gen %s->%s), no retry",
                 gen0, gen1)
        return result, False
    # 压缩生效：surface 已折叠，重新派生消息后用同一 prompt 重试一次
    log.info("compaction advanced gen %s->%s, retrying step", gen0, gen1)
    result2 = run_agent_fn(prompt, **kwargs)
    return result2, True