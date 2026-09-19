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

from .compaction import maybe_compact, DEFAULT_PRESSURE_THRESHOLD
from .error_codes import ErrorCode

log = logging.getLogger(__name__)

# 触发「压缩后重试」的错误码
_OVERFLOW_CODES = {ErrorCode.CONTEXT_OVERFLOW, ErrorCode.MAX_TOKENS}

# 事前预检阈值：最近一次调用的上下文锚点已占本步模型容量九成 → 第一个请求
# 大概率被供应商拒。只拦「大概率必死」，不提前压缩平时的高压（used() 是累计
# 口径，比真实上下文偏大；0.8 的响应式阈值语义与此不同，勿混用）。
_PRECHECK_RATIO = 0.9


def _v2_compaction_tuning():
    """settings_v2 orchestrator.compaction 微调 → (threshold|None, retain|None)。

    None = 未配置（走 maybe_compact 的模块默认）；读取/注册失败静默回落。
    retain 允许 0（尾部不保留），与 None（未配置）语义不同，勿合并。"""
    try:
        from .settings_schema import get as ss_get, register_default_namespaces
        register_default_namespaces()
        c = (ss_get("orchestrator") or {}).get("compaction") or {}
        th = c.get("pressure_threshold")
        rt = c.get("retain_tail_tokens")
        return (float(th) if th else None,
                int(rt) if rt is not None else None)
    except Exception:
        return None, None


def execute_step(session, run_agent_fn, prompt, *, model: str = "",
                 llm_caller=None, retain_tail_tokens=None, **kwargs):
    """执行一次 step；撑爆时压缩并守门重试一次。

    事前预检（借鉴 freebuff base-chat 每步容量重估）：换将切到小窗口模型后，
    响应式路径要等供应商报 CONTEXT_OVERFLOW 才压缩——报得不干净就直接卡死。
    先用「最近一次调用上下文 vs 本步模型容量」判一次，超阈先压缩再发。

    Args:
        session: session_log.Session（记录 replace_generation）
        run_agent_fn: runner.run_agent（或测试替身），签名 (prompt, **kwargs) -> dict
        prompt: 已渲染的提示词（重试时原样复用，不重新渲染）
        model: 本步将要使用的模型名（容量与压力估算都按它算）
        llm_caller: 压缩摘要 LLM（None = 不压缩，永不重试）
        **kwargs: 透传 run_agent_fn

    Returns:
        (result, retried: bool)
    """
    v2_th, v2_rt = _v2_compaction_tuning()
    compact_kwargs = {}
    if retain_tail_tokens is not None:
        compact_kwargs["retain_tail_tokens"] = retain_tail_tokens
    elif v2_rt is not None:
        compact_kwargs["retain_tail_tokens"] = v2_rt
    if v2_th is not None:
        compact_kwargs["threshold"] = v2_th
    if llm_caller is not None and model:
        try:
            from .token_meter import token_meter
            ctx = token_meter.last_context(session.run_id)
            cap = token_meter.capacity(model)
            if ctx > 0 and cap > 0 and ctx > cap * _PRECHECK_RATIO:
                gen_p0 = session.replace_generation()
                if maybe_compact(session, model=model, llm_caller=llm_caller,
                                 run_id=session.run_id, **compact_kwargs) \
                        and session.replace_generation() > gen_p0:
                    log.info("precheck: last ctx~%d > %.0f%% of cap(%s)=%d, "
                             "compacted before step", ctx, _PRECHECK_RATIO * 100,
                             model, cap)
        except Exception:
            log.debug("precheck skipped", exc_info=True)
    gen0 = session.replace_generation()
    result = run_agent_fn(prompt, **kwargs)
    code = result.get("error_code") or ""
    if code not in _OVERFLOW_CODES or llm_caller is None:
        return result, False
    # 撑爆 → 尝试压缩
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