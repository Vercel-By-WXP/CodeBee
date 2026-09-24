# -*- coding: utf-8 -*-
"""错误码体系：把 runner 失败归一成可枚举的 ErrorCode。

设计稿：docs/migration/01-defense-patterns.md §5D + §2D。
参考 dsh docs/subsystems/workflow.zh.md:108-120 WorkflowErrorCode。
"""
from __future__ import annotations

from enum import Enum
import re


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
    UPSTREAM_SERVER = "UPSTREAM_SERVER"    # 上游 5xx / 服务端异常
    MISSING_CREDENTIAL = "MISSING_CREDENTIAL"  # 当前路由没有可用 API key
    AUTH = "AUTH"                          # 凭据认证失败（401）
    FORBIDDEN = "FORBIDDEN"                # 上游拒绝当前账号/模型/请求（403）
    QUOTA = "QUOTA"                        # 欠费、余额或调用额度耗尽
    RATE_LIMIT = "RATE_LIMIT"              # 短时速率/并发限制（429）
    LOCAL_STATE = "LOCAL_STATE"            # 本地 CLI 状态/锁冲突
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


_MISSING_CREDENTIAL = (
    "no api key", "missing api key", "api key not found", "api key is required",
    "missing environment variable", "missing env var",
)
_AUTH = (
    "invalid api key", "invalid_api_key", "api key is invalid", "incorrect api key",
    "api key not valid", "api key 不可用", "鉴权失败", "认证失败",
    "unauthorized", "unauthorised", "authentication failed", "authentication error",
    "authentication_error", "invalid token", "invalid_token", "http 401",
    "status code 401",
)
_FORBIDDEN = ("forbidden", "request not allowed", "permission denied", "access denied")
_RATE_LIMIT = ("rate limit", "rate_limit", "too many requests", "http 429", "status code 429",
               "限流")
_QUOTA = ("insufficient balance", "insufficient quota", "insufficient credit",
          "quota", "balance is insufficient", "credit balance", "billing", "arrears",
          "payment required", "http 402", "status code 402", "欠费", "余额", "额度",
          "并发", "超过限", "usage limit", "limit exceeded", "quota exhausted")
_SERVER = ("unexpected server error", "internal server error", "server error",
           "overloaded", "temporarily", "unavailable", "service unavailable",
           "internal error", "no available channel", "unknown model")
_NETWORK = ("stream disconnected", "stream closed", "connection reset", "connection aborted",
            "broken pipe", "socket hang up", "econnrefused", "enotfound", "getaddrinfo",
            "name resolution", "network error", "network is unreachable", "reqwest error stream",
            "error sending request for url", "no route to host", "connection error",
            "reset by peer", "channel is closed", "name or service not known",
            "输出停滞")
_LOCAL_STATE = ("database is locked", "database locked", "sqlite busy",
                "unable to open database file")
_TRANSIENT_HINTS = ("stream closed before response.completed", "output stall", "timed out",
                    "timeout", "output stalled", "ENOTFOUND", "Reconnecting...",
                    "initialize", "[1211]")
_REFUSAL = ("can't help with this", "couldn't help with this", '"stop_reason":"refusal"',
            "content policy", "content filtering")


def error_code_value(code):
    """Return the stable wire value for ErrorCode or a string-like code."""
    if isinstance(code, ErrorCode):
        return code.value
    return str(code or "")


def classify_error_text(error):
    """Map a vendor/CLI error message to the shared failure taxonomy.

    Return ``None`` when text does not provide a reliable signal. Cancellation and
    timeout flags still take precedence in the caller because text may be noisy.
    More specific credential/permission/quota signals are checked before generic
    server and transport wording.
    """
    text = str(error or "").lower()
    if not text:
        return None
    if re.search(r"(?:http(?:/\d(?:\.\d)?)?\s*|status(?:\s+code)?\s*[:=]?\s*)401\b", text):
        return ErrorCode.AUTH
    # An explicit HTTP 403 is authoritative even when the provider describes the
    # permission problem with credential wording (for example, "invalid API key
    # for this model"). Do not cool down the key as an authentication failure.
    if (re.search(r"(?:http(?:/\d(?:\.\d)?)?\s*|status(?:\s+code)?\s*[:=]?\s*|"
                  r"code\s*[:=]?\s*)403\b", text)
            or re.search(r"(?:^|:\s*)403(?=$|[\s:{])", text)):
        return ErrorCode.FORBIDDEN
    if any(marker in text for marker in _MISSING_CREDENTIAL):
        return ErrorCode.MISSING_CREDENTIAL
    if (any(marker in text for marker in _AUTH)
            or re.search(r"(?:http(?:/\d(?:\.\d)?)?\s*|status(?:\s+code)?\s*[:=]?\s*)401\b", text)):
        return ErrorCode.AUTH
    if any(marker in text for marker in _FORBIDDEN):
        return ErrorCode.FORBIDDEN
    if (re.search(r"(?:http(?:/\d(?:\.\d)?)?\s*|status(?:\s+code)?\s*[:=]?\s*)429\b", text)
            or any(marker in text for marker in _RATE_LIMIT)):
        return ErrorCode.RATE_LIMIT
    if (re.search(r"(?:http(?:/\d(?:\.\d)?)?\s*|status(?:\s+code)?\s*[:=]?\s*)402\b", text)
            or any(marker in text for marker in _QUOTA)):
        return ErrorCode.QUOTA
    if any(marker in text for marker in _LOCAL_STATE):
        return ErrorCode.LOCAL_STATE
    if (re.search(r"(?<!\d)5\d\d(?!\d)", text)
            or any(marker in text for marker in _SERVER)):
        return ErrorCode.UPSTREAM_SERVER
    if ("timed out" in text or "timeout" in text or "deadline exceeded" in text
            or re.search(r"(?<!\d)408(?!\d)", text)):
        return ErrorCode.TIMEOUT
    if any(marker in text for marker in _NETWORK):
        return ErrorCode.NETWORK
    if any(marker in text for marker in _REFUSAL):
        return ErrorCode.VENDOR_REFUSAL
    return None


def is_auth_error(error):
    return classify_error_text(error) == ErrorCode.AUTH


def is_forbidden_error(error):
    return classify_error_text(error) == ErrorCode.FORBIDDEN


def is_quota_error(error):
    return classify_error_text(error) in (ErrorCode.QUOTA, ErrorCode.RATE_LIMIT)


def is_transient_error(error):
    text = str(error or "").lower()
    return classify_error_text(text) in (
        ErrorCode.NETWORK, ErrorCode.UPSTREAM_SERVER, ErrorCode.TIMEOUT,
        ErrorCode.QUOTA, ErrorCode.RATE_LIMIT, ErrorCode.LOCAL_STATE,
    ) or any(marker.lower() in text for marker in _TRANSIENT_HINTS)


def is_rate_limited_error(error):
    return classify_error_text(error) == ErrorCode.RATE_LIMIT


_SAFE_ERROR_SUMMARIES = {
    ErrorCode.VENDOR_ERROR: "供应商或 CLI 调用失败（未识别具体原因）",
    ErrorCode.VENDOR_REFUSAL: "上游拒绝了本次请求",
    ErrorCode.PARSE_FAIL: "模型响应无法解析",
    ErrorCode.ENV_BLOCK: "本机环境或执行策略阻断",
    ErrorCode.SIGNAL_IGNORED: "子进程未响应终止信号",
    ErrorCode.TIMEOUT: "调用超时",
    ErrorCode.CANCELLED: "调用已取消",
    ErrorCode.MAX_TOKENS: "模型输出达到 token 上限",
    ErrorCode.EMPTY: "模型未返回内容",
    ErrorCode.NETWORK: "网络连接或数据流中断",
    ErrorCode.UPSTREAM_SERVER: "上游服务端异常",
    ErrorCode.MISSING_CREDENTIAL: "所选路由未找到可用 API 凭据",
    ErrorCode.AUTH: "API 凭据认证失败",
    ErrorCode.FORBIDDEN: "上游拒绝账号、模型或请求权限",
    ErrorCode.QUOTA: "上游余额或调用额度不足",
    ErrorCode.RATE_LIMIT: "上游触发速率或并发限制",
    ErrorCode.LOCAL_STATE: "CLI 本地状态或数据库锁冲突",
    ErrorCode.CONTEXT_OVERFLOW: "输入上下文超出模型容量",
}


def safe_error_summary(code):
    """A content-free summary suitable for durable diagnostics and telemetry."""
    try:
        value = ErrorCode(code)
    except (ValueError, TypeError):
        value = ErrorCode.VENDOR_ERROR
    return _SAFE_ERROR_SUMMARIES.get(value, _SAFE_ERROR_SUMMARIES[ErrorCode.VENDOR_ERROR])


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
