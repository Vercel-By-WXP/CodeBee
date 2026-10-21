# -*- coding: utf-8 -*-
"""自动发布批次持久化、恢复与取消。

从 publish.auto 拆出（单文件行数治理），原模块再导出保持旧调用面；
跨模块引用经 _auto() 解析，保持运行时状态与测试打桩语义。
"""
from __future__ import annotations


def _auto():
    from . import auto
    return auto


def _batch_slot_lock(slot):
    key = str(slot)
    with _auto()._BATCH_SLOT_LOCKS_GUARD:
        return _auto()._BATCH_SLOT_LOCKS.setdefault(key, _auto().threading.Lock())


def _remember_automatic_batch(task_id, platform, auto_submit, as_draft,
                              only, force, force_confirmed, force_reason,
                              scheduled, auto_prepare_book, only_file):
    """Persist an accepted direct-publish request for crash recovery.

    Draft-only batches are persisted too. They never publish anything, and
    every resumed run reconciles against the live platform before selecting
    remaining chapters, so a service restart or browser interruption does not
    strand an explicitly requested draft batch.
    """
    if not auto_submit and not as_draft:
        return
    try:
        chapters = None if only is None else [int(value) for value in only]
    except (TypeError, ValueError):
        chapters = None
    params = {"auto_submit": bool(auto_submit), "only": chapters,
              "force": bool(force), "force_confirmed": bool(force_confirmed),
              "force_reason": str(force_reason or ""),
              "as_draft": bool(as_draft),
              "auto_prepare_book": bool(auto_prepare_book),
              "scheduled": bool(scheduled), "_only_file": only_file}
    with _auto()._BATCH_JOB_LOCK:
        jobs = _auto()._load_batch_jobs()
        prior = jobs.get(str(task_id))
        try:
            attempt = max(0, int(prior.get("attempt") or 0)) if isinstance(prior, dict) else 0
        except (TypeError, ValueError):
            attempt = 0
        if isinstance(prior, dict) and prior.get("status") == "blocked_verdict":
            # A fresh acceptance is a deliberate human re-arm: restart the
            # per-day verdict budget instead of instant re-blocking.
            attempt = 0
        jobs[str(task_id)] = {"task_id": str(task_id), "platform": str(platform),
                              "params": params, "status": "in_flight",
                              "updated_at": _auto().time.time(), "attempt": attempt}
        _auto()._save_batch_jobs()


def _forget_automatic_batch(task_id):
    with _auto()._BATCH_JOB_LOCK:
        jobs = _auto()._load_batch_jobs()
        if jobs.pop(str(task_id), None) is not None:
            _auto()._save_batch_jobs()


def _automatic_retry_delay(error, attempt):
    """Choose a retry delay without stranding a recovered login.

    Authentication challenges are never solved here.  Once the platform
    accepts the persistent profile again, though, the normal exponential
    backoff could leave a completed verification idle for up to 30 minutes.
    Probe authentication failures frequently while keeping ordinary
    browser/network failures on the conservative backoff schedule.
    """
    try:
        auth_recovery = _auto()._needs_login_recovery(error)
    except (NameError, TypeError, ValueError):
        auth_recovery = False
    if auth_recovery:
        return float(_auto().AUTH_RETRY_DELAY_S)
    return min(_auto().PUBLISH_START_RETRY_MAX_S,
               _auto().PUBLISH_START_RETRY_BASE_S
               * (2 ** min(max(0, int(attempt) - 1), 12)))


def _retain_automatic_batch_for_retry(task_id, error, now=None):
    """Keep a recoverable failed batch durable for a fresh reconcile/retry.

    Re-entry uses the normal publish worker, which reconciles live platform
    state before selecting chapters. This is safe for uncertain submit receipts:
    it never blindly submits again while the platform may still be processing.

    Verdict-blocked batches (quality gate) get a small per-day attempt budget:
    retrying cannot produce a new verdict, so once the budget is spent the
    record sleeps as ``blocked_verdict`` (outside the resume set) instead of
    waking the platform browser every backoff window. The attempt counter is
    per calendar day, mirroring the scheduler's day-keyed retry state.
    """
    if not _auto()._scheduled_error_is_persistent(error):
        return False
    now = _auto().time.time() if now is None else float(now)
    today = _auto().time.strftime("%Y-%m-%d", _auto().time.localtime(now))
    key = str(task_id)
    with _auto()._BATCH_JOB_LOCK:
        record = _auto()._load_batch_jobs().get(key)
        if not isinstance(record, dict):
            return False
        try:
            attempt = max(0, int(record.get("attempt") or 0)) + 1
        except (TypeError, ValueError):
            attempt = 1
        if str(record.get("day") or "") != today:
            attempt = 1
        record.update(status="retry_wait", error=str(error or "")[:240],
                      attempt=attempt, day=today, updated_at=now)
        if (_auto()._error_needs_fresh_verdict(error) and
                attempt >= _auto().SCHEDULE_RETRY_VERDICT_MAX_ATTEMPTS):
            record["status"] = "blocked_verdict"
            record.pop("next_retry_at", None)
        else:
            record["next_retry_at"] = now + _auto()._automatic_retry_delay(
                error, attempt)
        _auto()._save_batch_jobs()
    return True


def _queue_automatic_retry(task_id, platform, error, *, auto_submit=True,
                           as_draft=False, only=None, force=False,
                           force_confirmed=False, force_reason="",
                           scheduled=False, auto_prepare_book=True,
                           only_file=None, now=None):
    """Persist a safely retryable preflight rejection before returning it."""
    if ((not auto_submit and not as_draft) or
            not _auto()._scheduled_error_is_persistent(error)):
        return False
    try:
        _auto()._remember_automatic_batch(
            task_id, platform, auto_submit, as_draft, only, force,
            force_confirmed, force_reason, scheduled, auto_prepare_book,
            str(only_file) if only_file is not None else None)
        return _auto()._retain_automatic_batch_for_retry(task_id, error, now=now)
    except Exception:
        # Preserve the original actionable platform error. The caller logs
        # persistence failures separately when it can add useful context.
        return False


def resume_interrupted_batches(now=None):
    """Resume interrupted automatic batches from the durable publish queue.

    This is registered on the normal automation tick, so a transient startup
    race with session restoration or another account action is retried without
    requiring a person to press Publish again.
    """
    now = _auto().time.time() if now is None else float(now)
    if not _auto()._BATCH_RESUME_LOCK.acquire(False):
        return 0
    started = 0
    try:
        with _auto()._BATCH_JOB_LOCK:
            jobs = _auto()._load_batch_jobs()
            pending_jobs = [(key, dict(value)) for key, value in jobs.items()
                            if isinstance(value, dict) and value.get("status") in
                            ("in_flight", "resuming", "retry_wait")]
        for key, record in pending_jobs:
            with _auto()._LOCK:
                live = _auto()._running.get(key) or {}
            if live.get("status") in ("running", "preparing", "reclaiming"):
                continue
            # Archiving is a stop signal for scheduled publishing.  The
            # regular scheduler already scans only archived=False tasks, but
            # a crash-recovery record can outlive the task's archive/delete
            # transition.  Never revive that record into a browser action.
            try:
                from .. import store
                task = store.get_task(key, strict=True)
            except Exception:
                # A transient store read failure is not evidence that the task
                # was deleted; retain the record and let the next tick retry.
                continue
            if not task or task.get("archived"):
                _auto()._forget_automatic_batch(key)
                continue
            # A disabled schedule is a stop signal too: an ``enabled=false``
            # auto_publish flag must not keep waking the platform browser
            # through a durable crash-recovery record (mirrors the archived
            # check above). Manual batches on tasks that never carried the
            # schedule are unaffected (no auto_publish key to read).
            ap_flag = task.get("auto_publish")
            if isinstance(ap_flag, dict) and not ap_flag.get("enabled"):
                _auto()._forget_automatic_batch(key)
                continue
            try:
                retry_at = float(record.get("next_retry_at") or 0)
            except (TypeError, ValueError):
                retry_at = 0
            if retry_at > now:
                continue
            params = record.get("params")
            platform = str(record.get("platform") or "")
            if not isinstance(params, dict) or platform not in ("qimao", "fanqie"):
                _auto()._forget_automatic_batch(key)
                continue
            # Migrate records written before unattended publishing became the
            # only release mode. Draft recovery remains draft-only even if an
            # old or hand-edited record carried a stale auto_submit flag.
            params = dict(params)
            if bool(params.get("as_draft")):
                params["auto_submit"] = False
            else:
                params["auto_submit"] = True
            record["params"] = params
            record["status"] = "resuming"
            try:
                record["attempt"] = max(0, int(record.get("attempt") or 0)) + 1
            except (TypeError, ValueError):
                record["attempt"] = 1
            record["updated_at"] = now
            with _auto()._BATCH_JOB_LOCK:
                _auto()._load_batch_jobs()[key] = record
                _auto()._save_batch_jobs()
            try:
                ok, error = _auto().publish_pending_async(key, platform, **params)
            except Exception as exc:
                ok, error = False, "自动发布恢复启动异常：%s" % str(exc)[:200]
            if ok:
                started += 1
                continue
            if _auto()._scheduled_error_is_persistent(error):
                record["status"] = "retry_wait"
                record["error"] = str(error or "")[:240]
                attempt = max(1, int(record.get("attempt") or 1))
                delay = _auto()._automatic_retry_delay(error, attempt)
                record["next_retry_at"] = now + delay
                record["updated_at"] = now
                with _auto()._BATCH_JOB_LOCK:
                    _auto()._load_batch_jobs()[key] = record
                    _auto()._save_batch_jobs()
            else:
                _auto()._forget_automatic_batch(key)
    finally:
        _auto()._BATCH_RESUME_LOCK.release()
    return started


def cancel_pending(task_id):
    """Stop an active batch after its current platform action settles."""
    with _auto()._LOCK:
        event = _auto()._cancel_events.get(str(task_id))
        if not event:
            return False
        event.set()
        return True


def cancel_user_batch(task_id):
    """人工停批的完整语义：掐在跑批次 + 丢弃持久记录 + 终结当日重试。

    cancel_pending 只发取消事件，管不到 retry_wait/blocked_verdict 这类
    没有活线程的持久记录；本函数一并清除恢复队列里的记录，并把当天的
    调度重试状态置为 cancelled（明天按任务上的 auto_publish 配置重来）。
    返回是否有在跑批次被掐断。
    """
    key = str(task_id)
    stopped = bool(cancel_pending(key))
    with _auto()._LOCK:
        cur = _auto()._running.get(key) or {}
        if cur.get("status") in ("running", "preparing", "reclaiming"):
            stopped = True
    _auto()._forget_automatic_batch(key)
    _auto()._set_scheduled_retry(key, {
        "day": _auto().time.strftime("%Y-%m-%d"),
        "attempt": 0, "status": "cancelled"})
    return stopped


def _release_cancel_event(task_id, event):
    """Drop a finished batch's cancellation handle without touching a newer run."""
    with _auto()._LOCK:
        if _auto()._cancel_events.get(str(task_id)) is event:
            _auto()._cancel_events.pop(str(task_id), None)


def _start_worker_or_retry(thread, task_id, status, event, slot_lock,
                           owns_slot_lock=True):
    """Start a pre-persisted batch worker or return its request to retry_wait."""
    try:
        thread.start()
        return True, ""
    except Exception as exc:
        error = "自动发布线程启动失败：%s" % str(exc)[:180]
        status["status"] = "error"
        status["error"] = error
        _auto()._release_cancel_event(task_id, event)
        if owns_slot_lock:
            try:
                slot_lock.release()
            except Exception:
                pass
        if not _auto()._retain_automatic_batch_for_retry(task_id, error,
                                                 now=_auto().time.time()):
            _auto()._forget_automatic_batch(task_id)
        return False, error


def _batch_sleep(task_id, seconds):
    """Sleep while allowing a re-publish request to reclaim this batch."""
    with _auto()._LOCK:
        event = _auto()._cancel_events.get(str(task_id))
    if event:
        return not event.wait(max(0.0, float(seconds)))
    _auto().time.sleep(max(0.0, float(seconds)))
    return True


def _reclaim_previous(task_id, platform, slot):
    """回收同一任务/平台账号上的旧无人值守批次。

    浏览器动作不能被硬切，否则旧线程可能在新批次开始后继续提交上一章。
    先发取消信号，再等旧线程落终态；超时则保留旧批次并拒绝新批次，交给
    用户等平台动作真正结束后重试。
    """
    tid = str(task_id)
    with _auto()._LOCK:
        cur = _auto()._running.get(tid)
        if not cur or cur.get("status") not in (
                "running", "preparing", "manual_pause", "reclaiming"):
            return True, ""
        if cur.get("status") == "reclaiming":
            return False, "上一批发布正在回收，请稍后再点发布"
        old_platform = str(cur.get("platform") or "")
        old_slot = str(cur.get("slot") or old_platform)
        if old_platform and old_platform != str(platform):
            return False, "该任务已有其他平台发布批次进行中，请等本轮结束"
        if old_slot != str(slot):
            return False, "该任务已有另一账号发布批次进行中，请等本轮结束"
        event = _auto()._cancel_events.get(tid)
        if event:
            event.set()
        # manual_pause has no worker left to observe the event; reclaim it
        # immediately so a confirmed/restarted automatic batch can proceed.
        if cur.get("status") == "manual_pause":
            cur["status"] = "reclaimed"
            cur["message"] = "重新发布前已回收上一批次"
            return True, ""
        cur["status"] = "reclaiming"

    deadline = _auto().time.monotonic() + max(0.0, float(_auto().RECLAIM_TIMEOUT_S))
    while _auto().time.monotonic() < deadline:
        with _auto()._LOCK:
            current = _auto()._running.get(tid)
            if current is None or current is not cur or current.get("status") not in (
                    "reclaiming", "running", "preparing"):
                return True, ""
        _auto().time.sleep(min(_auto().IDLE_POLL_S, 0.1))
    with _auto()._LOCK:
        if _auto()._running.get(tid) is cur and cur.get("status") == "reclaiming":
            cur["status"] = "error"
            cur["error"] = "旧发布批次仍在收尾，暂未启动新批次"
    return False, "上一批发布仍在收尾，请稍后再点发布"


def other_batch_running(task_id, platform=None, slot=None):
    """返回同一浏览器槽上正在运行的另一批次。

    不同作品只要绑定了不同账号/profile，就可以并行发布；旧实现只按
    ``task_id`` 全局互斥，导致一个作品的草稿批次把另一作品也拦住。未传
    platform/slot 时保留旧的全局查询语义，兼容历史调用和测试桩。
    """
    with _auto()._LOCK:
        for tid, ent in _auto()._running.items():
            if tid == str(task_id) or ent.get("status") not in (
                    "running", "preparing", "manual_pause", "reclaiming"):
                continue
            if platform and ent.get("platform") != str(platform):
                continue
            if slot:
                ent_slot = ent.get("slot")
                # Legacy running entries predate per-account slot metadata;
                # treat them as the default platform slot only. They must not
                # block a named account's independent browser.
                if ent_slot and ent_slot != str(slot):
                    continue
                if not ent_slot and str(slot) != str(platform or ""):
                    continue
            return str(tid)
    return ""