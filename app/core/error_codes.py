# -*- coding: utf-8 -*-
"""错误码体系：把 runner 失败归一成可枚举的 ErrorCode。

设计稿：docs/migration/01-defense-patterns.md §5D + §2D。
参考 dsh docs/subsystems/workflow.zh.md:108-120 WorkflowErrorCode。
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """所有 runner 失败的可枚举分类。

    字段值是 JSON 友好的字符串，方便持久化到 store / 跨进程传输。
    """

    # === fatal：脚本/参数错，必须人工修 ===
    VENDOR_ERROR = "VENDOR_ERROR"          # vendor 内部崩溃（exit != 0）
    VENDOR_REFUSAL = "VENDOR_REFUSAL"      # 明确拒绝（claude is_error / qwen refusal）
    PARSE_FAIL = "PARSE_FAIL"              # 输出无法解析为预期 schema
    ENV_BLOCK = "ENV_BLOCK"                # approval 拒 / 策略阻断
    SIGNAL_IGNORED = "SIGNAL_IGNORED"      # 发了 SIGTERM/任务杀不掉

    # === item：可重试 ===
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    MAX_TOKENS = "MAX_TOKENS"              # 模型撑爆
    EMPTY = "EMPTY"                        # 空输出
    NETWORK = "NETWORK"                    # 连接失败
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"  # 触发压缩（与 MAX_TOKENS 不同）

    # === ok ===
    # "" 表示成功，不设枚举值


# 哪些码视为 fatal（job 终止，不进入重试队列）
FATAL_CODES = frozenset({
    ErrorCode.VENDOR_ERROR,
    ErrorCode.VENDOR_REFUSAL,
    ErrorCode.PARSE_FAIL,
    ErrorCode.ENV_BLOCK,
    ErrorCode.SIGNAL_IGNORED,
})


def is_fatal(code) -> bool:
    """code 可以是 ErrorCode 或字符串（如来自 dict.get('error_code')）。"""
    if not code:
        return False
    try:
        return ErrorCode(code) in FATAL_CODES
    except ValueError:
        return False


def is_retryable(code) -> bool:
    """item 类（可重试）。"""
    if not code:
        return False
    try:
        ec = ErrorCode(code)
        return ec not in FATAL_CODES and ec != ErrorCode.CANCELLED
    except ValueError:
        return False