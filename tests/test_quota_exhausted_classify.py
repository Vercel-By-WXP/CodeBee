# -*- coding: utf-8 -*-
"""额度见底 ≠ 瞬时限流：九家供应商直连原话的分类回归（2026-11-06）。

背景（11-05 16:00 逐家直连台账）：15:12 之后成片 `RATE_LIMIT` 其实全是
额度见底。人话说「触发速率或并发限制」，把用户引去降并发 / 加退避，
而 Bigmodel 明写「2026-11-09 14:09 后可继续使用」——怎么重试都不会好。

量过才改：`ErrorCode.QUOTA` 与 1310 周期上限的专门分支本就存在，九家里
八家已归 QUOTA、真限流仍是 RATE_LIMIT。真正漏的只有 `[1309] 套餐已到期`
（判 None，既不换通道也不停用）。故本轮**不新造枚举**（新码会漏过
ACCOUNT_DEAD_CODES / BLOCKED_CODES / health / routing 四处白名单），
只补零歧义词表 + 带恢复时刻的人话，并用真原话把两侧边界一起锁死。

决策 18：判据类守测必须报「真被判过的样本数」，零样本的绿灯不算数。
"""
from __future__ import annotations

try:
    from core.error_codes import (  # pytest 守测布局（app/ 在 path）
        ErrorCode, classify_error_text, is_account_dead_error,
        is_rate_limited_error, quota_recovery_at, describe_error_text,
    )
except ImportError:
    from app.core.error_codes import (  # unittest 闸门布局（仓库根在 path）
        ErrorCode, classify_error_text, is_account_dead_error,
        is_rate_limited_error, quota_recovery_at, describe_error_text,
    )

# 逐条来自 11-05 直连 body 原话（见 .workbuddy/memory/2026-11-05.md 16:00 节）
QUOTA_TEXTS = (
    "429 [1310]已达到 7 天使用上限，2026-11-09 14:09:00 后可继续使用",   # prov-36
    "429 [1309]GLM Coding Plan 套餐已到期",                              # prov-37
    "403 当前订阅额度不足或暂不可用",                                      # prov-41
    "402 Insufficient credits. This account never purchased credits",     # prov-53
    "402 Insufficient Balance",                                           # prov-61
    "HTTP 200 exceed quota limit",                                        # prov-38 假 200
    "账户已欠费",                                                          # prov-50
    "exceeded your monthly quota",                                        # prov-54
    "[API Error: 403 用户额度不足, 剩余额度: ¥0.000000]",                   # 公司网关实案
    "GLM Coding Plan 套餐已到期",                                          # 不带 429 前缀的变体
    "当前订阅额度不足",                                                    # 不带状态码前缀的变体
)

# 真·瞬时限流：不能被额度词表吞掉，否则该等窗口的被当死号、该退避的被停用
RATE_LIMIT_TEXTS = (
    "429 Too Many Requests",
    "rate limit exceeded",
    "Request rejected (429)",
    "concurrent limit exceeded",
)

# 负样本：额度词表若过度激进，这些会变成假 QUOTA
NEGATIVE_TEXTS = (
    "503 No available channel",
    "403 model is not available in the current token plan",
    "404 model qwen3.8-max does not exist",
    "Reconnecting... 2/5 (unexpected status 404 Not Found: openai_error)",
    # 既有契约：只有「达到…使用上限」而没写「后可继续使用」= 瞬时限流，
    # 不许外推成周期额度（test_upstream_config_classify.py 同款断言）。
    "Request rejected (429) 达到并发使用上限",
)
NO_SIGNAL_TEXT = "2026-11-06 11:00:00 step 12 done cost 0"


def test_all_quota_texts_are_quota():
    judged = 0
    for text in QUOTA_TEXTS:
        got = classify_error_text(text)
        assert got == ErrorCode.QUOTA, (
            "额度原话被错判为 %s（应为 QUOTA）\n原文：%s"
            % (got.value if got else "None", text))
        judged += 1
    print("真被判过的额度样本数: %d / %d" % (judged, len(QUOTA_TEXTS)))
    assert judged == len(QUOTA_TEXTS) >= 9, "额度样本太少，绿灯不可信"


def test_rate_limit_texts_stay_rate_limit():
    judged = 0
    for text in RATE_LIMIT_TEXTS:
        assert classify_error_text(text) == ErrorCode.RATE_LIMIT, (
            "真限流被额度词表吞掉：%s" % text)
        judged += 1
    print("真被判过的限流样本数: %d" % judged)
    assert judged == len(RATE_LIMIT_TEXTS) >= 3


def test_quota_texts_never_trigger_rate_limit_grace():
    """归 QUOTA 后：不进 runner 的 429 宽限重试，按账号级死错停用该 CLI。"""
    for text in QUOTA_TEXTS:
        code = classify_error_text(text)
        assert not is_rate_limited_error(text), "额度见底仍被当限流：%s" % text
        assert is_account_dead_error(code), "额度见底没进账号级死错：%s" % text


def test_recovery_hint_from_real_text():
    assert quota_recovery_at(QUOTA_TEXTS[0]) == "2026-11-09 14:09"
    for text in QUOTA_TEXTS[1:]:
        assert quota_recovery_at(text) == "", "无恢复时刻却编了一个：%s" % text


def test_human_summary_says_quota_not_limit():
    with_time = describe_error_text(QUOTA_TEXTS[0])
    assert "2026-11-09 14:09" in with_time
    assert "不是限速" in with_time
    assert "降并发" not in with_time and "并发限制" not in with_time
    plain = describe_error_text(QUOTA_TEXTS[1])
    assert "额度" in plain and "不是限速" in plain
    assert "额度" not in describe_error_text(RATE_LIMIT_TEXTS[0])


def test_negative_texts_are_not_swallowed():
    for text in NEGATIVE_TEXTS:
        assert classify_error_text(text) != ErrorCode.QUOTA, (
            "额度词表误吞负样本：%s" % text)
    assert classify_error_text(NO_SIGNAL_TEXT) is None, (
        "纯结构化行被判出信号：正则过度激进")


def test_every_sample_was_actually_judged():
    """决策 18：报出「真被判过的样本数」，别拿零样本当绿灯。"""
    samples = QUOTA_TEXTS + RATE_LIMIT_TEXTS + NEGATIVE_TEXTS
    judged = sum(1 for s in samples if classify_error_text(s) is not None)
    print("真被判过的总样本数: %d / %d" % (judged, len(samples)))
    assert judged >= 15, "有效判据样本不足，验收不成立"
