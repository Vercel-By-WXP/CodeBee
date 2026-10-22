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
    TOOL_LAUNCH = "TOOL_LAUNCH"            # 本地 CLI 启动失败（找不到可执行文件）
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # 模型未配置、已下线或模型 ID 不存在

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
    # claude CLI 登录闸门：无 OAuth/Key 时 -p 模式只吐一行提示即退出（exit 0/1
    # 都见过），不产生 result 事件——绑定链 env 未注入或本机未登录时必现
    "not logged in", "please run /login",
)
_AUTH = (
    "invalid api key", "invalid_api_key", "api key is invalid", "incorrect api key",
    "api key not valid", "api key 不可用", "鉴权失败", "认证失败",
    "unauthorized", "unauthorised", "authentication failed", "authentication error",
    "authentication_error", "invalid token", "invalid_token", "http 401",
    "status code 401",
)
_FORBIDDEN = ("forbidden", "request not allowed", "permission denied", "access denied")
# 额度耗尽的强信号：部分网关把计费拒绝包成 403 下发（qwen 实案：
# "[API Error: 403 预扣费额度失败, 用户剩余额度: ¥0.006, 需要预扣费额度: ¥0.010]"；
# 2026-10-16 公司网关实案 "[API Error: 403 用户额度不足, 剩余额度: ¥0.000000]"——
# 不带「剩余」二字的裸「额度不足」变体也必须先于 403 正则，否则判成
# FORBIDDEN（权限封禁）而不是 QUOTA）。
# 裸 403 正则会先把它判成 FORBIDDEN（权限封禁）——但两者的处置完全不同：
# QUOTA 是账号级死错（本场该停用该 CLI），FORBIDDEN 只封具体上游。
# 强信号词零歧义（不会出现在权限文案里），先于 401/403 正则判定。
_QUOTA_STRONG = ("预扣费", "剩余额度", "额度不足", "insufficient balance",
                "insufficient quota", "insufficient credit", "quota exhausted",
                "余额不足", "欠费停", "payment required", "billing address")
# 「request rejected」是智谱网关 429 的固定措辞（Request rejected (429)）：
# 裸 (429) 不带 http/status-code 前缀，正则分支匹配不到，只能靠词面
# （2026-09-27 七天上限案：分类不出 → 换将闸门当终态收工，备用 KEY 永不上场）
_RATE_LIMIT = ("rate limit", "rate_limit", "too many requests", "http 429", "status code 429",
               "request rejected",
               "concurrent limit", "concurrency limit", "too many concurrent",
               "并发限制", "并发超限", "限流")
_QUOTA = ("insufficient balance", "insufficient quota", "insufficient credit",
          "quota", "balance is insufficient", "credit balance", "billing", "arrears",
          "payment required", "http 402", "status code 402", "欠费", "余额", "额度",
          "usage limit", "limit exceeded", "quota exhausted",
          # 周期性用量上限（七天/五小时窗口）：部分上游文案不带 429 字样
          "使用上限", "用量上限")
_SERVER = ("unexpected server error", "internal server error", "server error",
           "overloaded", "temporarily", "unavailable", "service unavailable",
           "internal error", "no available channel", "unknown model")
_NETWORK = ("stream disconnected", "stream closed", "connection reset", "connection aborted",
            "broken pipe", "socket hang up", "econnrefused", "enotfound", "getaddrinfo",
            "name resolution", "network error", "network is unreachable", "reqwest error stream",
            "error sending request for url", "no route to host", "connection error",
            "reset by peer", "channel is closed", "name or service not known",
            "transport failed",
            "输出停滞")
_LOCAL_STATE = ("database is locked", "database locked", "sqlite busy",
                "unable to open database file")
# run_process 对 Popen 异常的统一包裹前缀（"启动失败: %r"）。供应商侧任何
# 报错都不会长这个样子，所以单靠前缀即可零误判地识别「本地工具链起不来」。
# 2026-09-30 claude 垫片被清案：这类失败被记成供应商健康故障，把健康的
# Bigmodel 评测作废、整链判死——必须先于一切上游分类识别。
# A CLI can start successfully and still fail before making an API request
# (missing Python module, shell command not found, or a missing executable
# referenced by a wrapper). Treat these as local tool-chain failures so the
# run-level bench disables that agent immediately instead of replaying the same
# broken command for every chapter/reviewer.
_TOOL_LAUNCH = (
    "启动失败", "modulenotfounderror", "no module named", "command not found",
    "not recognized as an internal or external command", "no such file or directory",
    "cannot find the path specified",
)
_TOOL_CONFIG = (
    "invalid configuration in", "invalid configuration for",
    "expected object, received string",
)
_MODEL_UNAVAILABLE = (
    "model is not configured", "model not configured", "not configured for model",
    "unknown model id", "unknown model", "invalid model", "model not found",
    "model does not exist", "no such model", "model unavailable",
    "free tier can only be used from within opencode",
    # 智谱网关 429 套餐权限拒绝（2026-10-22 实案：[1311][当前订阅套餐暂未开放
    # GLM-5.3-FlashX权限]）：是「该订阅不含此模型」的模型级死错，供应商的其他
    # 模型照常可用。必须先于 _RATE_LIMIT 的 "request rejected" 判成
    # MODEL_UNAVAILABLE，走模型级熔断+换下一个模型，而不是把 RATE_LIMIT 连坐
    # 到整个供应商把 10 个活模型一起熔断。
    "套餐暂未开放",
    # claude CLI 对不存在/无权限模型的报错原文（2026-10-22 起草实案："There's
    # an issue with the selected model (glm-5.3). It may not exist or you may
    # not have access to it."）——不含任何已知关键字的措辞，漏分类会按供应商
    # 级记账连坐整条链；模型名提取见 _MODEL_IN_ERROR_PATTERNS。
    "issue with the selected model",
    "may not exist or you may not have access",
    # mimo CLI 免费服务停摆横幅（2026-10-22 短篇实案）：exit 0 + 该横幅 =
    # 免费通道已死，不是成功（runner 侧按假成功拦截，见 _GENERIC_DEAD_BANNERS）。
    "free api service has ended",
)
_CONTEXT_OVERFLOW = (
    "input exceeds the maximum length", "maximum context length",
    "context length exceeded", "context window exceeded",
    "prompt is too long", "request too large",
)
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


# grep/编译器「路径:行号(:列号):内容」命中行里的行号会被误认成 HTTP 状态码
# （2026-10-21 实案：TODO 扫描 grep 命中 types.gen.d.ts:429:export type Todo
# 被归成限流/上游故障，健康的免费通道连坐进 down 告警）。分类前先剥掉源码
# 位置令牌；真实的「HTTP 429」「status: 503」等措辞不受影响。
_SOURCE_LOCATION_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[./~])?[^\s:]+\.[A-Za-z0-9]{1,10}(?::\d{1,6}){1,2}")


def classify_error_text(error):
    """Map a vendor/CLI error message to the shared failure taxonomy.

    Return ``None`` when text does not provide a reliable signal. Cancellation and
    timeout flags still take precedence in the caller because text may be noisy.
    More specific credential/permission/quota signals are checked before generic
    server and transport wording.
    """
    text = _SOURCE_LOCATION_RE.sub(" ", str(error or "")).lower()
    if not text:
        return None
    config_file_hint = any(marker in text for marker in
                           ("settings.json", "config.json", "configuration file"))
    if (any(marker in text for marker in _TOOL_LAUNCH)
            or (config_file_hint and any(marker in text for marker in _TOOL_CONFIG))):
        return ErrorCode.TOOL_LAUNCH
    if (any(marker in text for marker in _MODEL_UNAVAILABLE)
            or re.search(r"\bmodel(?:\s+[^:\n]{1,80})?\s+is\s+not\s+configured\b", text)):
        return ErrorCode.MODEL_UNAVAILABLE
    if any(marker in text for marker in _CONTEXT_OVERFLOW):
        return ErrorCode.CONTEXT_OVERFLOW
    # 计费拒绝的强信号先于 401/403 正则：网关用 403 包装「预扣费失败」时，
    # 必须归类为 QUOTA（账号级）而不是 FORBIDDEN（权限级）——下游的停用与
    # 降级策略依赖这个区分（2026-10-15 qwen 欠费连撞 31 次案）。
    if any(marker in text for marker in _QUOTA_STRONG):
        return ErrorCode.QUOTA
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


# 模型级错误文本里点名的模型 id 提取（2026-10-22 两实案）：
# · grok CLI 本地校验拒绝：Couldn't set model 'glm-5.3': unknown model id
# · 智谱套餐权限拒绝：[当前订阅套餐暂未开放GLM-5.3-FlashX权限]
# · claude CLI 不存在/无权限模型：…the selected model (glm-5.3). It may not exist…
# 调用方（health 模型级熔断）据此把无 model 位上报的错误归到正确模型上。
_MODEL_IN_ERROR_PATTERNS = (
    re.compile(r"couldn't set model ['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"暂未开放\s*([A-Za-z0-9._\-]+)\s*权限"),
    re.compile(r"the selected model \(([A-Za-z0-9._\-]+)\)", re.IGNORECASE),
)


def model_name_from_error(error) -> str:
    """模型级错误文本中点名的模型 id；没有则返回空串。"""
    text = str(error or "")
    for pattern in _MODEL_IN_ERROR_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


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


# 账号/候选级死错：换请求/换提示词也不会好（余额耗尽/凭据缺失/权限封禁、
# 模型不可用或 CLI 起不来）。
# 与 transient（限流/超时/网络抖动）相对——那些只值得退避重试，不配让 CLI 下场。
# 供本场停用台账（bench）判定。
ACCOUNT_DEAD_CODES = frozenset({
    ErrorCode.QUOTA.value,
    ErrorCode.AUTH.value,
    ErrorCode.MISSING_CREDENTIAL.value,
    ErrorCode.FORBIDDEN.value,
    ErrorCode.TOOL_LAUNCH.value,
})


def is_account_dead_error(code) -> bool:
    """True 当错误码属于账号级死错（本场应停用该 CLI，不再重试）。

    code 可以是 ErrorCode 或字符串（如来自 res.get('error_code')）。
    """
    return error_code_value(code) in ACCOUNT_DEAD_CODES


# 判决轴（借鉴 tester-army/e2e 的 passed / failed / blocked）：
# blocked 表示环境、凭据、配额、时钟或自动化能力挡住了这次尝试，还没有
# 对产出本身做出判断；它不是产品失败，不该计入「候选链连续失败」，也
# 不该驱逐账号/缓存。VENDOR_ERROR 是未归类的兜底值，按产品失败保守处理。
BLOCKED_CODES = frozenset({
    ErrorCode.ENV_BLOCK.value,
    ErrorCode.CANCELLED.value,
    ErrorCode.TIMEOUT.value,
    ErrorCode.NETWORK.value,
    ErrorCode.UPSTREAM_SERVER.value,
    ErrorCode.MISSING_CREDENTIAL.value,
    ErrorCode.AUTH.value,
    ErrorCode.FORBIDDEN.value,
    ErrorCode.QUOTA.value,
    ErrorCode.RATE_LIMIT.value,
    ErrorCode.LOCAL_STATE.value,
    ErrorCode.MODEL_UNAVAILABLE.value,
    ErrorCode.TOOL_LAUNCH.value,
    ErrorCode.SIGNAL_IGNORED.value,
})


def is_blocked_code(code) -> bool:
    """True 当错误码表示「没拿到产物判断」而不是产品失败。"""
    return error_code_value(code) in BLOCKED_CODES


def attempt_verdict(ok, code="") -> str:
    """单次尝试的判决：passed / failed / blocked。"""
    if ok:
        return "passed"
    return "blocked" if is_blocked_code(code) else "failed"


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
    ErrorCode.MISSING_CREDENTIAL: "未找到可用凭据（CLI 未登录或未配置 API Key）",
    ErrorCode.AUTH: "API 凭据认证失败",
    ErrorCode.FORBIDDEN: "上游拒绝账号、模型或请求权限",
    ErrorCode.QUOTA: "上游余额或调用额度不足",
    ErrorCode.RATE_LIMIT: "上游触发速率或并发限制",
    ErrorCode.LOCAL_STATE: "CLI 本地状态或数据库锁冲突",
    ErrorCode.CONTEXT_OVERFLOW: "输入上下文超出模型容量",
    ErrorCode.TOOL_LAUNCH: "本地 CLI 启动失败（可执行文件不存在，与供应商无关）",
    ErrorCode.MODEL_UNAVAILABLE: "模型未配置、已下线或模型 ID 不存在",
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
