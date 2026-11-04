# -*- coding: utf-8 -*-
"""等待人工接管的状态机：退避、落账节流、原因分类。

## 为什么需要它

2026-11-04 实测台账：`connect` 动作共 535 条，失败 511 条，占全部台账
（711 条）的 72%；其中番茄 485 条失败全部是同一句「平台要求安全验证：
验证码」，从 2026-10-20 一直刷到 11-04，跨 16 天。这不是判据错，是没有
任何东西阻止它继续试：

1. `ledger.consecutive_failures()` 只数 `upload_chapter` / `create_book` /
   `upload_chapter_draft`，**connect 不在连败口径里**——所以连败退避护栏
   对它永不触发，`_auth_watch_once` 每 30 秒照探一次。
2. 每次探测失败都写一条台账。真实故障（七猫 10 次登录态失效、9 次内部
   异常）被 485 条同因噪音埋掉，事后来看「全是验证码」，看不出先后。
3. 前端只有一个「等登录/验证…」的小灰字。没有人知道它已经刷了 485 次、
   从哪天开始、需要谁做什么。

## 本模块的边界

只算三个数：**多久再试**（退避）、**什么时候值得记一笔**（节流）、
**跟人怎么说**（分类 + 文案）。不碰浏览器、不写台账、不读磁盘——
调用方把 state 字典递进来，拿到一组要 `_set` 的字段和一个「要不要落账」
的布尔。纯函数，可单测，零副作用。

## 一条纪律

验证码是**人机验证**，程序不可能自己完成。所以这里的每个分支都以
「等人」为前提：退避是为了别再打平台（打得越勤越容易触发风控），
节流是为了别把台账写满，分类是为了让人一眼看出该做什么、而不是
被一句「登录态不可用」骗去重新扫码。
"""
from __future__ import annotations

import time

# 退避阶梯（秒）：30s、1m、2m、5m、10m、15m 封顶。
# 前两次快一点，是因为「用户刚好在浏览器前扫完码」是最常见的恢复场景；
# 之后拉长，因为人不在的时候探得再勤也没用，只会持续触发平台风控。
BACKOFF_S = (30.0, 60.0, 120.0, 300.0, 600.0, 900.0)

# 落账节流：首条必记，之后每累计这么多次尝试再记一条汇总。
# 实测同因失败会持续数天，20 次约等于「每天一两条」，够复盘又不淹台账。
LOG_EVERY = 20

CAUSE_CAPTCHA = "captcha"
CAUSE_LOGIN = "login"
CAUSE_INTERNAL = "internal"

# 顺序有意义：验证码排在最前，因为它同时含「登录/验证」字样，
# 先判 captcha 才不会被泛化的登录标记吃掉。
_CAPTCHA_MARKS = ("验证码", "安全验证", "人机验证", "滑块", "captcha",
                  "verify you are human", "拖动")
_LOGIN_MARKS = ("登录", "扫码", "login", "signin", "sign-in", "未登录")
_INTERNAL_MARKS = (
    "unpack", "attributeerror", "typeerror", "keyerror", "indexerror",
    "traceback", "nonetype", "not enough values", "object has no attribute",
    "connection refused", "webscoketerror", "websocketerror",
    "连接被关闭", "对端发送关闭帧", "内部异常",
)

# 给人看的一句话：他该做什么。
_ADVICE = {
    CAUSE_CAPTCHA: "平台要求过人机验证，程序无法自己完成——请在发布台点「过一次验证码」，"
                   "在弹出的窗口里完成验证后不要关闭窗口",
    CAUSE_LOGIN: "保存的登录态已失效——请在发布台点「重新登录」，扫码或输账号登录一次",
    CAUSE_INTERNAL: "这是发布链内部异常、不是登录问题——不用重新扫码，请把这条错误发给开发",
}


def classify(error) -> str:
    """把一句错误文案归类成 captcha / login / internal。

    判定顺序固定为「内部异常 → 验证码 → 登录」：
    内部异常的文案里常常也带「登录/验证」（例如
    ``保存的平台登录态不可用……not enough values to unpack``），
    先认内部异常才不会把程序 bug 说成登录问题、骗人去重新扫码。
    """
    text = str(error or "").lower()
    if not text:
        return CAUSE_LOGIN
    if any(mark in text for mark in _INTERNAL_MARKS):
        return CAUSE_INTERNAL
    if any(mark in text for mark in _CAPTCHA_MARKS):
        return CAUSE_CAPTCHA
    if any(mark in text for mark in _LOGIN_MARKS):
        return CAUSE_LOGIN
    return CAUSE_LOGIN


def advice_for(cause) -> str:
    return _ADVICE.get(str(cause or ""), _ADVICE[CAUSE_LOGIN])


def backoff_seconds(attempts) -> float:
    """第 N 次失败后，隔多少秒才允许再探一次。"""
    try:
        n = int(attempts or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 0.0
    return BACKOFF_S[min(n, len(BACKOFF_S)) - 1]


def attempts_of(state) -> int:
    try:
        return max(0, int((state or {}).get("human_wait_attempts") or 0))
    except (TypeError, ValueError):
        return 0


def should_probe(state, now=None) -> bool:
    """现在是否允许再探一次该槽位。

    看门狗每 30 秒过一轮，这里按退避拦下大部分轮次——**不获取动作锁、
    不打开浏览器、不写台账**，代价只有一次字典读。
    """
    state = state or {}
    if state.get("status") != "waiting_login" and not state.get("needs_human"):
        return True                      # 其它状态不归本闸管
    try:
        next_at = float(state.get("human_wait_next_at") or 0)
    except (TypeError, ValueError):
        return True
    if next_at <= 0:
        return True
    return (time.time() if now is None else float(now)) >= next_at


def note_failure(state, error="", cause="", now=None):
    """记一次「等待人工」的失败，返回 ``(fields, log_now)``。

    ``fields`` 交给调用方 ``_set(**fields)``；``log_now`` 为真时调用方才写
    一条台账。节流规则：首次必记，之后每累计 ``LOG_EVERY`` 次尝试记一条
    汇总——这样同一次故障在台账上留下「开始 + 每 20 次一条」，而不是
    485 行一模一样的话。
    """
    now = time.time() if now is None else float(now)
    state = state or {}
    attempts = attempts_of(state) + 1
    since = str(state.get("human_wait_since") or "").strip()
    if not since:
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    resolved = str(cause or "").strip() or classify(error)
    try:
        logged = max(0, int(state.get("human_wait_logged") or 0))
    except (TypeError, ValueError):
        logged = 0
    log_now = attempts == 1 or (attempts - logged) >= LOG_EVERY
    fields = {
        "needs_human": True,
        "human_wait_cause": resolved,
        "human_wait_since": since,
        "human_wait_attempts": attempts,
        "human_wait_next_at": now + backoff_seconds(attempts),
        "human_wait_advice": advice_for(resolved),
    }
    if log_now:
        fields["human_wait_logged"] = attempts
    return fields, log_now


def note_success():
    """连接成功要清掉的字段（``_set`` 是 update 语义，必须显式写零值）。"""
    return {
        "needs_human": False,
        "human_wait_cause": "",
        "human_wait_since": "",
        "human_wait_attempts": 0,
        "human_wait_next_at": 0,
        "human_wait_logged": 0,
        "human_wait_advice": "",
    }


def summary(state) -> str:
    """给人看的一行摘要；没在等人时返回空串（前端据此不显示）。"""
    state = state or {}
    attempts = attempts_of(state)
    if not attempts:
        return ""
    since = str(state.get("human_wait_since") or "").strip()
    cause = str(state.get("human_wait_cause") or "").strip()
    head = "已尝试 %d 次" % attempts
    if since:
        # "2026-11-04 17:20:33" -> "11-04 17:20"，只留到分钟，一眼能认日期
        head += "，从 %s 起" % (since[5:16] if len(since) >= 16 else since)
    if cause == CAUSE_CAPTCHA:
        head += "（平台一直在要求验证码）"
    elif cause == CAUSE_INTERNAL:
        head += "（内部异常，不是登录问题）"
    return head


def ledger_error_text(state, error="") -> str:
    """写进台账的汇总文案：带累计次数，事后一眼看出这不是偶发一次。"""
    state = state or {}
    attempts = attempts_of(state)
    cause = str(state.get("human_wait_cause") or "").strip()
    label = {CAUSE_CAPTCHA: "验证码", CAUSE_LOGIN: "登录",
             CAUSE_INTERNAL: "内部异常"}.get(cause, "未知")
    head = "连续 %d 次等待人工（%s）" % (attempts, label) if attempts > 1 \
        else "等待人工（%s）" % label
    detail = str(error or "").strip()
    return ("%s：%s" % (head, detail[:200])) if detail else head
