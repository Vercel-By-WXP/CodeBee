# -*- coding: utf-8 -*-
"""定时发布的到点判定、触发与重试状态。

从 publish.auto 拆出（单文件行数治理），原模块再导出保持旧调用面；
跨模块引用经 _auto() 解析，保持运行时状态与测试打桩语义。
"""
from __future__ import annotations


def _auto():
    from . import auto
    return auto


def _scheduled_error_is_persistent(error):
    """Return whether a scheduled batch may keep retrying after its budget.

    Scheduled publishing is unattended, so errors that can clear while the
    service remains alive must not turn into a permanent ``exhausted`` state.
    Content/configuration failures stay finite: retrying those forever would
    only repeat the same rejected submission and hide a real operator fix.
    Keep this classifier deliberately text based because manager/driver
    errors are platform-specific and are persisted across service restarts.
    """
    text = str(error or "").lower()
    if not text:
        return False
    if _auto().retryable_publish_start(auto_submit=True, as_draft=False,
                               error=error):
        return True
    return any(mark in text for mark in (
        # A later review can replace a failing verdict. Retain the accepted
        # unattended request and re-check the gate with capped backoff; never
        # bypass it or submit while the same quality blockers remain.
        "质量门禁拦截", "最近一次质量评审未达标", "全局一致性评审未通过",
        "未形成共识", "评审人数不足", "评分未达到有效发布门槛",
        "存在未达标章节", "存在 major 问题",
        # The configured daily cap is a temporary stop, not a rejected batch.
        # Keep the durable request and let the next-day retry continue once
        # ledger.today_count rolls over; otherwise the user must restart it.
        "今日已发", "今日已发布", "达到每日发布上限",
        # Fanqie reports its account-level submission quota from the editor
        # as a chapter-content banner rather than the local daily-cap text.
        # Treat all wording variants as persistent so unattended retries wait
        # for a later quota window instead of exhausting after five attempts.
        "提交字数超出每日上限", "提交字数超过每日上限",
        "投稿字数超出每日上限", "投稿字数超过每日上限",
        "未登录", "登录态", "重新登录", "登录过期", "session expired",
        "无法确认平台登录页面",
        "连接页面失败", "无法解析调试地址",
        "未完成加载", "调试端口", "没有活着的浏览器",
        "浏览器启动失败", "隐藏的后台实例占着档案",
        "端口探测不到",
        # Platform rate limits and temporary anti-abuse throttles can clear
        # after a cooldown. Keep the batch durable and let the normal backoff
        # retry; this does not attempt to dismiss or bypass a challenge.
        "访问过于频繁", "操作过于频繁", "请求过于频繁", "频繁操作",
        "异常请求", "rate limit", "too many requests",
        "提交结果不确定", "对账失败",
        # A completed browser flow without a local receipt is ambiguous, not
        # a confirmed rejection. Keep the durable batch so its next entry
        # reconciles the platform before deciding whether another submit is
        # needed; never discard the only recovery path here.
        "流程结束，但未确认平台发布成功",
        "待发章节清单读取异常",
        # A legacy title-only binding may resolve after the platform list
        # finishes loading. Retain the request so a later reconcile can recover
        # the canonical ID; do not abandon it for a manual re-registration.
        "未按作品名找到远端作品", "平台作品列表按书名未找到",
        "安全验证", "安全校验", "人机验证", "验证码", "captcha",
        "verify you are human", "账号浏览器正在执行",
        "浏览器正在执行另一项发布操作", "正在回收", "发布批次进行中",
        "另一批自动发布", "另一项发布操作", "已有自动发布进行中",
        "旧发布批次", "该账号有操作正在进行中", "该平台有操作正在进行中",
        "请等本轮结束", "请稍后重试", "自动恢复稍后重试",
        "browser busy", "浏览器忙", "浏览器未运行", "浏览器不可用", "浏览器未连接",
        "无法连接浏览器", "无法打开平台作者后台", "域名打不开",
        # Keep preparation and chapter-wait timeouts in the durable recovery
        # loop too. These often mean the browser worker is still completing an
        # action; ending the batch here otherwise requires a manual restart.
        "网络错误", "超时", "timed out", "timeout",
        "cdp", "devtools", "target closed", "connection reset",
        # Antivirus/indexer/sync clients can briefly lock a chapter file
        # between queue enumeration and the upload worker's actual read.
        # Retry only OS-level sharing/permission contention; a missing file or
        # invalid chapter remains a finite content error.
        "winerror 32", "errno 13", "errno 16", "sharing violation",
        "permission denied", "resource busy",
        # OS thread-pool/resource pressure is transient. A worker was already
        # durably recorded before start(), so let the scheduler retry it.
        "线程启动失败", "can't start new thread", "unable to start new thread",
        "connection refused", "connection aborted", "连接断开",
        "连接被关闭", "等响应超时", "目标已关闭",
    ))


def _retry_delay(attempt):
    exponent = max(0, int(attempt or 1) - 1)
    return min(_auto().SCHEDULE_RETRY_MAX_S,
               _auto().SCHEDULE_RETRY_BASE_S * (2 ** min(exponent, 12)))


def scheduled_retry_state(prior, run_status, now=None, day=None, error=None):
    """Advance one scheduled batch state and say whether it may be started.

    Missing in-memory run state while a persisted attempt is in flight means
    the service restarted. Wait with backoff, then let the normal platform
    reconciliation/idempotency path safely resume the remaining chapters.
    """
    now = _auto().time.time() if now is None else float(now)
    day = day or _auto().time.strftime("%Y-%m-%d")
    if run_status in ("running", "preparing", "reclaiming"):
        state = dict(prior) if isinstance(prior, dict) else {}
        try:
            attempt = max(0, int(state.get("attempt") or 0))
        except (TypeError, ValueError):
            attempt = 0
        # Carry an active batch across the date boundary.  Without this
        # marker, the next tick would reset yesterday's in-flight state to
        # ``ready``; after the batch completed it could launch a second batch
        # for the same daily schedule.  Also adopt batches started through a
        # manual/API entry point so the scheduler doesn't race them.
        state.update(day=day, attempt=attempt, status="in_flight")
        return state, False
    if not isinstance(prior, dict) or prior.get("day") != day:
        if run_status == "done":
            return {"day": day, "attempt": 1, "status": "succeeded"}, False
        if run_status == "cancelled":
            return {"day": day, "attempt": 1, "status": "cancelled"}, False
        return {"day": day, "attempt": 0, "status": "ready"}, True
    state = dict(prior)
    # A worker can finish asynchronously after the scheduler tick.  Carry
    # the latest error through the durable state so a restart or a later tick
    # can apply the same recoverable/permanent classification.
    if error:
        state["error"] = str(error)[:200]
    effective_error = error or state.get("error") or ""
    try:
        attempt = max(0, int(state.get("attempt") or 0))
    except (TypeError, ValueError):
        attempt = 0
    status = state.get("status") or "ready"
    # The in-memory batch is authoritative even if its persisted retry state
    # was stale (for example, an older process wrote retry_wait before its
    # completion callback became visible to the scheduler).
    if run_status == "done":
        state["status"] = "succeeded"
        state.pop("next_retry_at", None)
        state.pop("error", None)
        return state, False
    if run_status == "cancelled":
        state["status"] = "cancelled"
        state.pop("next_retry_at", None)
        return state, False
    if status == "in_flight":
        # Error or a lost in-memory run after restart: reconcile then retry.
        if (attempt >= _auto().SCHEDULE_RETRY_MAX_ATTEMPTS and
                not _auto()._scheduled_error_is_persistent(effective_error)):
            state["status"] = "exhausted"
            state.pop("next_retry_at", None)
            return state, False
        state["status"] = "retry_wait"
        state["next_retry_at"] = now + _auto()._retry_delay(attempt)
        return state, False
    if status == "exhausted":
        # Older releases exhausted every error after five attempts.  If the
        # durable record still carries a known recoverable reason, migrate it
        # back into the unattended retry loop instead of leaving it stranded.
        if _auto()._scheduled_error_is_persistent(effective_error):
            state["status"] = "retry_wait"
            state["next_retry_at"] = now + _auto()._retry_delay(attempt)
            return state, False
        return state, False
    if status in ("succeeded", "cancelled"):
        return state, False
    if status == "retry_wait":
        if (attempt >= _auto().SCHEDULE_RETRY_MAX_ATTEMPTS and
                not _auto()._scheduled_error_is_persistent(effective_error)):
            state["status"] = "exhausted"
            state.pop("next_retry_at", None)
            return state, False
        return state, now >= float(state.get("next_retry_at") or 0)
    return state, True


def _scheduled_retry(task_id, day, run_status=None, now=None, error=None):
    with _auto()._AP_LOCK:
        states = _auto()._load_retry_state()
        key = str(task_id)
        next_state, ready = _auto().scheduled_retry_state(
            states.get(key), run_status, now=now, day=day, error=error)
        if states.get(key) != next_state:
            states[key] = next_state
            _auto()._save_retry_state()
        return dict(next_state), ready


def _set_scheduled_retry(task_id, state):
    with _auto()._AP_LOCK:
        _auto()._load_retry_state()[str(task_id)] = dict(state)
        _auto()._save_retry_state()


def norm_auto_publish(ap):
    """校验并归一 auto_publish 配置。非法返回 (None, 人话原因)。"""
    from . import manager
    if not isinstance(ap, dict):
        return None, "auto_publish 必须是对象"
    platform = str(ap.get("platform") or "").strip()
    if platform not in manager.PLATFORMS:
        return None, "platform 必须是 fanqie 或 qimao"
    hhmm = str(ap.get("time") or "").strip()
    import re
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
    if not m:
        return None, "time 必须是 24 小时制 HH:MM"
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None, "time 超出 0-23:00-59"
    account = str(ap.get("account") or "").strip()
    if account and not re.match(r"^[A-Za-z0-9_-]{1,16}$", account):
        return None, "account 只能是字母/数字/下划线/连字符（1-16）"
    return {"enabled": bool(ap.get("enabled")),
            "platform": platform,
            "time": "%02d:%02d" % (h, mi),
            # Scheduled publishing is always a release action; draft-only is
            # configured through the separate draft workflow, never as a
            # scheduled form that waits for a person to submit it.
            "auto_submit": True,
            "account": account,
            "at": _auto().time.strftime("%Y-%m-%d %H:%M:%S")}, ""


def due_tasks(now=None):
    """到期待触发的定时发布任务清单：[(task, auto_publish)]。

    条件：enabled + 存在待发章节 + 今日未触发 + 当前时间已过当日 time。
    尚未建书的任务也可进入批次；publish_pending_async 会自动生成作品资料并
    建书，只有真实取得平台作品 ID 后才继续发章。"""
    from .. import store
    now = now or _auto().time.localtime()
    today = _auto().time.strftime("%Y-%m-%d", now)
    hhmm_now = _auto().time.strftime("%H:%M", now)
    fired = _auto()._load_fired()
    out = []
    # Archived tasks retain their historical auto_publish payload for audit and
    # UI display, but must never be eligible for a live scheduler tick.  The
    # old all-tasks scan let archived test books (for example t-pubtest) fire
    # at midnight, hijack the shared platform browser, and leave the real book
    # looking as if publishing had never started.
    for task in store.list_tasks(limit=10 ** 9, archived=False):
        ap = task.get("auto_publish")
        if not isinstance(ap, dict) or not ap.get("enabled"):
            continue
        # A continuation batch inherits the book-level schedule.  Only the
        # newest chain member that still carries a schedule may own the timer;
        # otherwise root and child batches can fire the same book concurrently.
        try:
            chain = store.serial_chain_ids(task["id"])
            configured_latest = next(
                (tid for tid in reversed(chain)
                 if isinstance((store.get_task(tid) or {}).get("auto_publish"), dict)),
                task["id"],
            )
            if configured_latest != task["id"]:
                continue
        except Exception:
            # Scheduling must remain available for legacy/non-serial tasks if
            # an old malformed chain cannot be traversed.
            pass
        from .. import contracts
        if not contracts.publish_allowed(task["id"], unattended=True):
            continue
        if str(ap.get("time") or "") <= hhmm_now and fired.get(task["id"]) != today:
            with _auto()._LOCK:
                running = _auto()._running.get(str(task["id"])) or {}
            retry, retry_ready = _auto()._scheduled_retry(
                task["id"], today, run_status=running.get("status"),
                error=running.get("error"), now=_auto().time.time())
            if retry.get("status") == "succeeded":
                _auto()._mark_fired(task["id"], today)
                continue
            if not retry_ready:
                continue
            plat = ap.get("platform")
            chapters, chapter_error = _auto().pending(task["id"], plat) if plat else ([], "")
            if plat and not chapter_error and chapters:
                out.append((task, ap))
    return out


def fire_due(now=None):
    """tick 入口：把所有到期的定时发布触发一轮。返回触发条数；单条异常不拖累其余。"""
    now = now or _auto().time.localtime()       # 且被双层 except 双重静默成幽灵 0
    today = _auto().time.strftime("%Y-%m-%d", now)
    n = 0
    for task, ap in _auto().due_tasks(now=now):
        retry, _ready = _auto()._scheduled_retry(task["id"], today, now=_auto().time.time())
        retry["attempt"] = int(retry.get("attempt") or 0) + 1
        retry["status"] = "in_flight"
        retry["started_at"] = _auto().time.strftime("%Y-%m-%d %H:%M:%S")
        retry.pop("next_retry_at", None)
        _auto()._set_scheduled_retry(task["id"], retry)
        try:
            ok, err = _auto().publish_pending_async(
                task["id"], ap.get("platform"),
                auto_submit=True, auto_prepare_book=True)
            if ok:
                _auto().ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              title="定时触发 %s" % (ap.get("time") or ""), ok=True)
                n += 1
            else:
                retry, _ready = _auto().scheduled_retry_state(
                    retry, "error", now=_auto().time.time(), day=today,
                    error=err or "自动发布启动失败")
                _auto()._set_scheduled_retry(task["id"], retry)
                _auto().ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              title="定时触发 %s" % (ap.get("time") or ""),
                              ok=False, error=str(err)[:200])
        except Exception as e:
            retry, _ready = _auto().scheduled_retry_state(
                retry, "error", now=_auto().time.time(), day=today, error=e)
            _auto()._set_scheduled_retry(task["id"], retry)
            try:
                _auto().ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              ok=False, error=str(e)[:200])
            except Exception:
                pass
    return n