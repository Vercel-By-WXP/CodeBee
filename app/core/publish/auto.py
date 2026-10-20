# -*- coding: utf-8 -*-
"""自动发布：枚举任务待发章节，按护栏顺序发布（P2 护栏层）。

与 manager 的分工：manager 管「一次动作」（连接/建书/发一章），
auto 管「一批章节」——枚举任务成品里的章节文件，减去台账已发章号，
逐章调 manager 发，每章之间隔一段防风控节奏。

护栏（每章发起前复查，发布中途护栏状态变了也拦得住）：
- 每日上限：ledger.today_count >= settings.publish_daily_cap（默认 10）
- 失败反馈：当前章失败即报告实际错误，历史失败不拦截后续发起；
- 幂等：已成功发布的章号跳过（manager 内还有第二道闸）；
- 单飞：同任务同时只有一个自动发布线程。

auto_submit=True 时按校准流程自动提交；质量门禁决策带固定审计原因记录。
"""
from __future__ import annotations

import math
import os
import threading
import time

# Keep the publish ledger available at module scope for callers/tests that
# patch or inspect the reconciliation source directly.
from . import ledger


def _env_float(name, default, minimum=0.0):
    """Read a non-negative timing override without making startup fragile.

    Publishing runs in a long-lived desktop process, so a malformed environment
    variable must fall back to the safe default rather than preventing the
    service from starting.  The constants remain module globals so tests and
    operators can tune a running batch explicitly.
    """
    try:
        raw_value = os.environ.get(name)
        value = float(raw_value if raw_value is not None else default)
    except (TypeError, ValueError):
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    return max(float(minimum), value)


# The old 45-second fixed gap made a ten-chapter batch spend at least 6m45s
# waiting between browser actions.  Keep a conservative anti-abuse gap while
# making it practical for normal serial publishing; operators can restore the
# old pace with TUTTI_PUBLISH_PACE_S=45 when a platform needs it.
PACE_S = _env_float("TUTTI_PUBLISH_PACE_S", 15.0)       # 章间间隔（秒）
IDLE_POLL_S = _env_float("TUTTI_PUBLISH_IDLE_POLL_S", 0.5)
IDLE_TIMEOUT_S = _env_float("TUTTI_PUBLISH_IDLE_TIMEOUT_S", 300.0)
TRANSIENT_RETRY_S = 5        # 连接类瞬断的重试前静默（等浏览器缓过来）
PUBLISH_START_RETRY_BASE_S = 60
PUBLISH_START_RETRY_MAX_S = 30 * 60
PUBLISH_START_RETRY_ATTEMPTS = 3
PUBLISH_READ_RETRY_ATTEMPTS = 3
BOOK_PREP_RETRY_ATTEMPTS = 3
BOOK_PREP_TIMEOUT_S = 900    # 自动补资料/建书最长等待（含模型与平台跳转）
LOGIN_RECOVERY_TIMEOUT_S = 15 * 60  # 等待持久会话恢复或登录态复核失败
RECLAIM_TIMEOUT_S = 8.0      # 重发前等待旧批次收尾的最长时间

_CAP_DEFAULT = 10

_running = {}                # task_id → {platform, at, done, total, status, error}
_cancel_events = {}          # task_id → current batch cancellation event
_LOCK = threading.Lock()
_BATCH_JOB_FILE = None
_BATCH_JOBS = None
_BATCH_JOB_LOCK = threading.RLock()
_BATCH_RESUME_LOCK = threading.Lock()

# A platform account owns one browser profile.  The per-chapter action lock in
# manager.py prevents two individual uploads from entering the browser at the
# same instant, but it is too short lived to protect a whole batch: another
# task can start between chapters and navigate the same page to its own book.
# Reserve the slot for the lifetime of an automatic batch so chapters from
# different tasks cannot interleave or verify against the wrong work.
_BATCH_SLOT_LOCKS = {}
_BATCH_SLOT_LOCKS_GUARD = threading.Lock()


def _batch_slot_lock(slot):
    key = str(slot)
    with _BATCH_SLOT_LOCKS_GUARD:
        return _BATCH_SLOT_LOCKS.setdefault(key, threading.Lock())


def _batch_job_path():
    global _BATCH_JOB_FILE
    if _BATCH_JOB_FILE is None:
        from .. import paths
        _BATCH_JOB_FILE = paths.PUBLISH_DIR / "auto_publish_batches.json"
    return _BATCH_JOB_FILE


def _load_batch_jobs():
    global _BATCH_JOBS
    if _BATCH_JOBS is None:
        import json
        path = _batch_job_path()
        temp_path = path.with_suffix(".tmp")

        def read_state(source):
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("batch state root must be an object")
            return data

        if not path.exists() and not temp_path.exists():
            _BATCH_JOBS = {}
        else:
            primary_error = None
            if path.exists():
                try:
                    _BATCH_JOBS = read_state(path)
                except (OSError, ValueError, TypeError) as exc:
                    primary_error = exc
            if _BATCH_JOBS is None and temp_path.exists():
                try:
                    _BATCH_JOBS = read_state(temp_path)
                    # Restore the recovered atomic-write candidate immediately
                    # so subsequent mutations continue from the same queue.
                    temp_path.replace(path)
                except (OSError, ValueError, TypeError) as exc:
                    raise RuntimeError(
                        "自动发布恢复队列损坏，主文件错误=%s；临时文件错误=%s" %
                        (primary_error, exc))
            if _BATCH_JOBS is None:
                raise RuntimeError(
                    "自动发布恢复队列不可读取：%s" % primary_error)
    return _BATCH_JOBS


def _save_batch_jobs():
    import json
    path = _batch_job_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_load_batch_jobs(), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def _remember_automatic_batch(task_id, platform, auto_submit, as_draft,
                              only, force, force_confirmed, force_reason,
                              scheduled, auto_prepare_book, only_file):
    """Persist an accepted direct-publish request for crash recovery.

    Draft-only batches intentionally stay out: saving a draft is not the same
    authorization as publishing it. Every resumed publish reconciles against
    the live platform before selecting remaining chapters.
    """
    if not auto_submit or as_draft:
        return
    try:
        chapters = None if only is None else [int(value) for value in only]
    except (TypeError, ValueError):
        chapters = None
    params = {"auto_submit": True, "only": chapters,
              "force": bool(force), "force_confirmed": bool(force_confirmed),
              "force_reason": str(force_reason or ""), "as_draft": False,
              "auto_prepare_book": bool(auto_prepare_book),
              "scheduled": bool(scheduled), "_only_file": only_file}
    with _BATCH_JOB_LOCK:
        jobs = _load_batch_jobs()
        prior = jobs.get(str(task_id))
        try:
            attempt = max(0, int(prior.get("attempt") or 0)) if isinstance(prior, dict) else 0
        except (TypeError, ValueError):
            attempt = 0
        jobs[str(task_id)] = {"task_id": str(task_id), "platform": str(platform),
                              "params": params, "status": "in_flight",
                              "updated_at": time.time(), "attempt": attempt}
        _save_batch_jobs()


def _forget_automatic_batch(task_id):
    with _BATCH_JOB_LOCK:
        jobs = _load_batch_jobs()
        if jobs.pop(str(task_id), None) is not None:
            _save_batch_jobs()


def _retain_automatic_batch_for_retry(task_id, error, now=None):
    """Keep a recoverable failed batch durable for a fresh reconcile/retry.

    Re-entry uses the normal publish worker, which reconciles live platform
    state before selecting chapters. This is safe for uncertain submit receipts:
    it never blindly submits again while the platform may still be processing.
    """
    if not _scheduled_error_is_persistent(error):
        return False
    now = time.time() if now is None else float(now)
    key = str(task_id)
    with _BATCH_JOB_LOCK:
        record = _load_batch_jobs().get(key)
        if not isinstance(record, dict):
            return False
        try:
            attempt = max(0, int(record.get("attempt") or 0)) + 1
        except (TypeError, ValueError):
            attempt = 1
        delay = min(PUBLISH_START_RETRY_MAX_S,
                    PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
        record.update(status="retry_wait", error=str(error or "")[:240],
                      attempt=attempt, next_retry_at=now + delay,
                      updated_at=now)
        _save_batch_jobs()
    return True


def _queue_automatic_retry(task_id, platform, error, *, auto_submit=True,
                           as_draft=False, only=None, force=False,
                           force_confirmed=False, force_reason="",
                           scheduled=False, auto_prepare_book=True,
                           only_file=None, now=None):
    """Persist a safely retryable preflight rejection before returning it."""
    if (not auto_submit or as_draft or
            not _scheduled_error_is_persistent(error)):
        return False
    try:
        _remember_automatic_batch(
            task_id, platform, auto_submit, as_draft, only, force,
            force_confirmed, force_reason, scheduled, auto_prepare_book,
            str(only_file) if only_file is not None else None)
        return _retain_automatic_batch_for_retry(task_id, error, now=now)
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
    now = time.time() if now is None else float(now)
    if not _BATCH_RESUME_LOCK.acquire(False):
        return 0
    started = 0
    try:
        with _BATCH_JOB_LOCK:
            jobs = _load_batch_jobs()
            pending_jobs = [(key, dict(value)) for key, value in jobs.items()
                            if isinstance(value, dict) and value.get("status") in
                            ("in_flight", "resuming", "retry_wait")]
        for key, record in pending_jobs:
            with _LOCK:
                live = _running.get(key) or {}
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
                _forget_automatic_batch(key)
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
                _forget_automatic_batch(key)
                continue
            # Migrate records written before unattended publishing became the
            # only release mode. Draft records are never persisted here, so a
            # non-draft recovery must always carry the automatic flag.
            params = dict(params)
            if not bool(params.get("as_draft")):
                params["auto_submit"] = True
                record["params"] = params
            record["status"] = "resuming"
            try:
                record["attempt"] = max(0, int(record.get("attempt") or 0)) + 1
            except (TypeError, ValueError):
                record["attempt"] = 1
            record["updated_at"] = now
            with _BATCH_JOB_LOCK:
                _load_batch_jobs()[key] = record
                _save_batch_jobs()
            try:
                ok, error = publish_pending_async(key, platform, **params)
            except Exception as exc:
                ok, error = False, "自动发布恢复启动异常：%s" % str(exc)[:200]
            if ok:
                started += 1
                continue
            if _scheduled_error_is_persistent(error):
                record["status"] = "retry_wait"
                record["error"] = str(error or "")[:240]
                attempt = max(1, int(record.get("attempt") or 1))
                delay = min(PUBLISH_START_RETRY_MAX_S,
                            PUBLISH_START_RETRY_BASE_S
                            * (2 ** min(attempt - 1, 12)))
                record["next_retry_at"] = now + delay
                record["updated_at"] = now
                with _BATCH_JOB_LOCK:
                    _load_batch_jobs()[key] = record
                    _save_batch_jobs()
            else:
                _forget_automatic_batch(key)
    finally:
        _BATCH_RESUME_LOCK.release()
    return started


def cancel_pending(task_id):
    """Stop an active batch after its current platform action settles."""
    with _LOCK:
        event = _cancel_events.get(str(task_id))
        if not event:
            return False
        event.set()
        return True


def _cancelled(task_id):
    with _LOCK:
        event = _cancel_events.get(str(task_id))
        return bool(event and event.is_set())


def _release_cancel_event(task_id, event):
    """Drop a finished batch's cancellation handle without touching a newer run."""
    with _LOCK:
        if _cancel_events.get(str(task_id)) is event:
            _cancel_events.pop(str(task_id), None)


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
        _release_cancel_event(task_id, event)
        if owns_slot_lock:
            try:
                slot_lock.release()
            except Exception:
                pass
        if not _retain_automatic_batch_for_retry(task_id, error,
                                                 now=time.time()):
            _forget_automatic_batch(task_id)
        return False, error


def _batch_sleep(task_id, seconds):
    """Sleep while allowing a re-publish request to reclaim this batch."""
    with _LOCK:
        event = _cancel_events.get(str(task_id))
    if event:
        return not event.wait(max(0.0, float(seconds)))
    time.sleep(max(0.0, float(seconds)))
    return True


def _reclaim_previous(task_id, platform, slot):
    """回收同一任务/平台账号上的旧无人值守批次。

    浏览器动作不能被硬切，否则旧线程可能在新批次开始后继续提交上一章。
    先发取消信号，再等旧线程落终态；超时则保留旧批次并拒绝新批次，交给
    用户等平台动作真正结束后重试。
    """
    tid = str(task_id)
    with _LOCK:
        cur = _running.get(tid)
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
        event = _cancel_events.get(tid)
        if event:
            event.set()
        # manual_pause has no worker left to observe the event; reclaim it
        # immediately so a confirmed/restarted automatic batch can proceed.
        if cur.get("status") == "manual_pause":
            cur["status"] = "reclaimed"
            cur["message"] = "重新发布前已回收上一批次"
            return True, ""
        cur["status"] = "reclaiming"

    deadline = time.monotonic() + max(0.0, float(RECLAIM_TIMEOUT_S))
    while time.monotonic() < deadline:
        with _LOCK:
            current = _running.get(tid)
            if current is None or current is not cur or current.get("status") not in (
                    "reclaiming", "running", "preparing"):
                return True, ""
        time.sleep(min(IDLE_POLL_S, 0.1))
    with _LOCK:
        if _running.get(tid) is cur and cur.get("status") == "reclaiming":
            cur["status"] = "error"
            cur["error"] = "旧发布批次仍在收尾，暂未启动新批次"
    return False, "上一批发布仍在收尾，请稍后再点发布"


def _isdir(p):
    try:
        return os.path.isdir(p)
    except (OSError, ValueError):
        return False


def _book_ready(info):
    return bool(info and (info.get("book_id") or info.get("source") == "manual"))


def _prepare_book(task_id, platform):
    """无人值守发布前补齐平台资料并建书，返回 (ok, reason)。

    续写任务通常没有自己的 ``book_meta``，但它沿连载链共享同一本书。
    过去批量发布在 ``ledger.book_for`` 处直接失败，七猫因此从未发起。
    这里沿用正式的作品资料生成与建书流程，绝不伪造 book_id；只有平台真实
    返回作品 ID 后才允许后续章节发布。
    """
    from .. import bookmeta, store
    from . import manager

    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    existing = ledger.book_for(task_id, platform)
    # A legacy manual binding without its canonical ID is not ready for an
    # unattended batch; still pass it through the safe title lookup below.
    if existing and str(existing.get("book_id") or "").strip():
        return True, ""

    # A preparation run has no confirmed book yet, so route it through the
    # account selected for the task.  If an older/partial binding already
    # exists, its account is authoritative; otherwise use auto_publish.account.
    acc = str((existing or {}).get("account") or
              ((task.get("auto_publish") or {}).get("account") or "")).strip()
    try:
        slot = manager.slot_of(platform, acc)
    except (TypeError, ValueError) as exc:
        return False, str(exc)

    # Legacy bindings can contain a title without the canonical platform ID.
    # Resolve that binding from the already authenticated author console before
    # considering book creation: creating a second work with the same title is
    # unsafe, while returning an instruction to register it manually breaks
    # unattended recovery. This lookup is read-only and never bypasses login or
    # platform security checks.
    if existing and not str(existing.get("book_id") or "").strip():
        title = str(existing.get("title") or "").strip()
        if title:
            attempt = 0
            while True:
                if _cancelled(task_id):
                    return False, "自动找回平台作品 ID 已取消"
                try:
                    _browser, page = manager._open_page(slot, preserve_tabs=True)
                    logged_in, login_state = manager._check_login(
                        slot, page,
                        navigate=(
                            manager._st(slot).get("status") != "waiting_login" or
                            manager._page_is_empty(page)))
                    if logged_in:
                        book_id = manager._resolve_book_id(
                            platform, page, existing)
                        if book_id:
                            existing = dict(existing, book_id=book_id)
                            ledger.save_book(task_id, platform, existing)
                            return True, ""
                        failure = "平台作品列表按书名未找到《%s》，暂不重复建书" % title
                    else:
                        if manager._authentication_required(platform, login_state):
                            manager._set(slot, status="waiting_login",
                                         error=str(login_state)[:160])
                        failure = "平台未登录或需要安全验证：%s" % str(
                            login_state or "无法确认登录态")[:160]
                except Exception as exc:
                    failure = "自动查找已登记平台作品失败：%s" % str(exc)[:160]

                attempt += 1
                if attempt >= BOOK_PREP_RETRY_ATTEMPTS:
                    return False, (
                        "自动找回平台作品 ID 重试次数已达上限：%s" % failure)
                delay = min(PUBLISH_START_RETRY_MAX_S,
                            PUBLISH_START_RETRY_BASE_S
                            * (2 ** min(attempt - 1, 12)))
                status = _running.get(str(task_id))
                if isinstance(status, dict):
                    status["message"] = (
                        "正在自动核对已登记作品《%s》，%d 秒后重试（第 %d 次）；"
                        "为避免重复建书，核对成功前不会创建新作品" %
                        (title[:40], delay, attempt))
                if _needs_login_recovery(failure):
                    try:
                        manager.connect(platform, account=acc, unattended=True)
                    except Exception:
                        pass
                if not _batch_sleep(task_id, delay):
                    return False, "自动找回平台作品 ID 重试期间任务已取消"

    chain = store.serial_chain_ids(task_id)
    root = (store.get_task(chain[0]) if chain else None) or task
    root_id = str(root.get("id") or task_id)
    meta = store.inherited_book_meta(task, platform)
    if meta.get("status") != "done" or not meta.get("data"):
        # generate_async 是唯一会正确落盘归档、记录来源和失败状态的入口；
        # 直接调用同步 make_book_meta 会丢失这些状态，重启后还会再次卡死。
        attempt = 0
        while True:
            # A previous batch may have timed out while its metadata worker is
            # still running. Reuse that worker's durable state instead of
            # launching a second thread that can race to overwrite its result.
            if meta.get("status") != "running":
                store.set_book_meta(root_id, platform, {
                    "status": "running",
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                bookmeta.generate_async(root_id, platform,
                                        author_agent=bookmeta._resolve_author(root))
            deadline = time.time() + BOOK_PREP_TIMEOUT_S
            failure = ""
            while time.time() < deadline:
                if _cancelled(task_id):
                    return False, "自动准备作品已取消"
                task = store.get_task(task_id) or task
                meta = store.inherited_book_meta(task, platform)
                if meta.get("status") == "done" and meta.get("data"):
                    failure = ""
                    break
                if meta.get("status") == "failed":
                    failure = str(meta.get("error") or "未知错误")
                    break
                time.sleep(1.0)
            else:
                # The generation worker may still be running after the wait
                # window. Do not launch a duplicate generation on an ambiguous
                # timeout; the durable publication batch will reconcile on its
                # next restart/retry.
                return False, "自动生成%s作品信息超时" % platform
            if meta.get("status") == "done" and meta.get("data"):
                break
            if not _scheduled_error_is_persistent(failure):
                return False, "自动生成%s作品信息失败：%s" % (
                    manager.PLATFORMS[platform].CONFIG.get("label", platform),
                    failure)
            attempt += 1
            if attempt >= BOOK_PREP_RETRY_ATTEMPTS:
                return False, "自动生成%s作品信息重试次数已达上限：%s" % (
                    platform, failure)
            delay = min(PUBLISH_START_RETRY_MAX_S,
                        PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
            status = _running.get(str(task_id))
            if isinstance(status, dict):
                status["message"] = (
                    "作品资料生成遇到可恢复错误，%d 秒后自动重试（第 %d 次）" %
                    (delay, attempt))
            if not _batch_sleep(task_id, delay):
                return False, "自动生成作品资料重试等待期间任务已取消"

    attempt = 0
    while True:
        if _cancelled(task_id):
            return False, "自动准备作品已取消"
        ok, err = manager.create_book_async(task_id, platform,
                                            auto_submit=True, account=acc)
        if not ok:
            failure = str(err or "自动创建作品失败")
        else:
            deadline = time.time() + BOOK_PREP_TIMEOUT_S
            failure = ""
            while time.time() < deadline:
                if _cancelled(task_id):
                    return False, "自动准备作品已取消"
                found = ledger.book_for(task_id, platform)
                if _book_ready(found):
                    return True, ""
                state = manager._st(slot)
                if state.get("status") == "error":
                    failure = str(state.get("error") or "自动创建作品失败")
                    break
                time.sleep(1.0)
            else:
                # A timeout does not prove that the platform rejected the
                # create action. Do not start another create while its result
                # could still be pending.
                return False, "自动创建%s作品超时" % platform

        if not _scheduled_error_is_persistent(failure):
            return False, failure

        attempt += 1
        if attempt >= BOOK_PREP_RETRY_ATTEMPTS:
            return False, "自动建书重试次数已达上限：%s" % failure
        delay = min(PUBLISH_START_RETRY_MAX_S,
                    PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
        status = _running.get(str(task_id))
        if isinstance(status, dict):
            status["message"] = (
                "自动建书遇到可恢复的平台连接/会话状态，%d 秒后重试（第 %d 次）；"
                "安全验证仍由平台控制" % (delay, attempt))

        # A failed preflight or a worker that ended on a transient browser
        # error may leave the saved profile disconnected. Reopen that profile;
        # this never solves a login challenge or platform security check.
        if _needs_login_recovery(failure):
            try:
                connected, connect_error = manager.connect(
                    platform, account=acc, unattended=True)
            except Exception as exc:
                connected, connect_error = False, str(exc)
            if connected:
                deadline = time.time() + LOGIN_RECOVERY_TIMEOUT_S
                while time.time() < deadline:
                    if _cancelled(task_id):
                        return False, "自动准备作品已取消"
                    state = manager._st(slot)
                    if state.get("status") == "connected":
                        break
                    if state.get("status") == "error":
                        connect_error = state.get("error") or connect_error
                        break
                    time.sleep(1.0)

        if not _batch_sleep(task_id, delay):
            return False, "自动建书重试等待期间任务已取消"


# ---------------------------------------------------------------- 护栏配置
def _settings():
    from .. import settings
    try:
        return settings.load() or {}
    except Exception:
        return {}


def daily_cap():
    try:
        configured = _settings().get("publish_daily_cap", _CAP_DEFAULT)
        # Zero explicitly disables the daily cap; truthiness fallback would
        # silently turn it back into the default limit and stop unattended
        # batches after ten chapters despite the user's setting.
        if configured is None or configured == "":
            configured = _CAP_DEFAULT
        v = int(configured)
    except (TypeError, ValueError):
        v = _CAP_DEFAULT
    return max(0, min(50, v))


def guards(task_id, platform):
    """每日上限护栏。历史发布失败仅记账，不阻止用户再次发起。

    publish_daily_cap=0 视为不限制（2026-10-08 用户要求关闭每日上限）。"""
    cap = daily_cap()
    if cap > 0:
        used = ledger.today_count(task_id, platform)
        if used >= cap:
            return False, ("今日已发 %d 章（上限 %d），为防风控明天再发；"
                           "确需多发请在设置调 publish_daily_cap（0=不限制）"
                           % (used, cap))
    return True, ""


def _chapter_failure(task_id, platform, chapter_no, as_draft=False):
    """从本任务、本章、本动作的最新台账取错误，避免只报「未确认」。"""
    action = "upload_chapter_draft" if as_draft else "upload_chapter"
    for rec in ledger.recent(task_id=task_id, platform=platform, limit=100):
        if rec.get("action") == action and rec.get("chapter_no") == chapter_no:
            return str(rec.get("error") or "") if rec.get("ok") is False else ""
    return ""


# 连接类瞬断标记（2026-10-10 存草稿实案：45 章 fill 时「连接被关闭」整链
# 停摆）——命中即对该章重试一次再弃，不因浏览器抖动废掉整批
_TRANSIENT_MARKS = ("连接断开", "连接被关闭", "10053", "10054", "等响应超时",
                    "已中止", "目标已关闭")
# 幂等拒起标记（manager 层台账/实况对账的拒绝话术）——命中即跳过该章，
# 不是失败
_IDEMPOTENT_MARKS = ("已成功发布过", "已存过草稿")


def _is_transient(err):
    e = str(err or "")
    return any(m in e for m in _TRANSIENT_MARKS)


def _needs_login_recovery(error):
    """Whether a failed preflight can be safely retried after reconnecting.

    This is intentionally narrower than the scheduler's long-lived retry
    classifier: a reconnect is safe before an upload starts, but not for an
    ambiguous response after a submit click.
    """
    text = str(error or "").lower()
    return any(mark in text for mark in (
        "未登录", "登录态", "登录过期", "平台登录态", "保存的平台登录态",
        "浏览器不可用", "浏览器未连接", "无法连接浏览器", "chrome-error",
        # Login checks may fail transiently while the page is still loading or
        # the CDP target is recovering. Reopen the saved profile and verify
        # again before any upload; do not treat an unverified session as logged in.
        "无法确认平台登录页面",
        # These browser errors happen before the publish flow can submit. A
        # fresh connection to the same saved profile is safe and can recover
        # from a stale DevTools endpoint without user intervention.
        "连接页面失败", "无法解析调试地址",
        "未完成加载", "调试端口", "没有活着的浏览器",
        "浏览器启动失败", "隐藏的后台实例占着档案",
        "端口探测不到",
        "devtools", "cdp", "target closed", "安全验证", "安全校验",
        "人机验证", "验证码", "captcha", "verify you are human",
    ))


def retryable_publish_start(auto_submit, as_draft, error):
    """Whether a failed start is a known transient platform/CDP disconnect."""
    return bool((auto_submit or as_draft) and _is_transient(error))


def _upload_start_retryable(error):
    """Classify errors that are safe to retry before a chapter submit starts.

    The scheduler keeps some quality and quota failures durable so a later
    review/day can make them eligible again.  That broad durable classifier
    must not be used inside the worker's tight start loop: a synchronous
    quality rejection would otherwise hold the account slot forever.  Only
    session/browser contention and known rate-limit/connection failures get a
    bounded in-worker retry; all other errors return to the batch finalizer.
    """
    text = str(error or "").lower()
    if _needs_login_recovery(error) or _is_transient(error):
        return True
    return any(mark in text for mark in (
        "浏览器忙", "browser busy", "账号有操作正在进行中",
        "平台有操作正在进行中", "另一项发布操作", "请稍后重试",
        "访问过于频繁", "操作过于频繁", "请求过于频繁", "rate limit",
        "too many requests", "connection refused", "connection aborted",
        "连接被关闭", "连接断开", "目标已关闭", "等响应超时",
    ))


def _is_idempotent_reject(err):
    e = str(err or "")
    return any(m in e for m in _IDEMPOTENT_MARKS)


def other_batch_running(task_id, platform=None, slot=None):
    """返回同一浏览器槽上正在运行的另一批次。

    不同作品只要绑定了不同账号/profile，就可以并行发布；旧实现只按
    ``task_id`` 全局互斥，导致一个作品的草稿批次把另一作品也拦住。未传
    platform/slot 时保留旧的全局查询语义，兼容历史调用和测试桩。
    """
    with _LOCK:
        for tid, ent in _running.items():
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


# ---------------------------------------------------------------- 待发枚举
# 文档目录里的编号文件不是章节稿：章纲/大纲按章编号（第85章-xxx.md、
# vol-1-ch-16.md），全按章号算会把待发清单撑出一堆「假章」——批量选择
# 发布时一旦选中就会把章纲当正文填进编辑器（2026-10-08 实案：全书树 88
# 个章号，其中 34 个来自 章纲/已成稿/作废稿 目录）。
_DOC_DIR_MARKS = ("章纲/", "大纲/", "设定/", "参考资料/", "已成稿/",
                  "作废稿", "docs/", "outline/")
_WALK_PRUNE = {"node_modules", "target", "build", "dist", "__pycache__"}


def _workdir_chapter_files(wd):
    """任务工作目录的**全量**章节稿（不看时间窗，2026-10-08 实案）。

    run 成品口径的时间窗起点是「各自任务的首跑」——续写链上每个批次任务
    只能看到自己出生以后新写的章节：今天的批次任务待发只剩 73-78，昨天
    的 65-78，根任务才有全量 31-78。发布待发的正确口径是「工作目录里
    还没发过的章节稿」，与哪个任务/何时跑无关，所以这里直接全树扫。"""
    import os as _os
    out = []
    def fail_walk(error):
        raise error

    for dirpath, dirnames, filenames in _os.walk(wd, onerror=fail_walk):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in _WALK_PRUNE]
        for name in filenames:
            if not name.lower().endswith((".md", ".txt")):
                continue
            fp = _os.path.join(dirpath, name)
            rel = _os.path.relpath(fp, wd).replace("\\", "/")
            if rel.startswith(_DOC_DIR_MARKS):
                continue
            try:
                out.append({"name": rel, "size": _os.path.getsize(fp)})
            except OSError:
                # Do not silently turn a partial filesystem read into an empty
                # or incomplete publish queue; the durable batch can retry.
                raise
    return out


def pending(task_id, platform):
    """待发章节清单：任务工作目录里的章节文件 − 已发章号，按章号升序。

    已发口径连载链合并（含校准所得平台实况），所以任何一个批次任务上发
    起发布，看到的都是整本书的待发清单。已存草稿的章同样剔除（内容已在
    平台草稿箱，再跑只会造重复稿；提交发布走平台草稿箱由用户手工完成）。
    返回 (list, err)；list 项
    {chapter_no, file, size}，file 为工作目录相对路径。章号解析不出的
    文件不进自动发布（防同章多文件误发），API 单章发（manager 直调）
    不受此限。
    """
    from .. import store
    task = store.get_task(task_id)
    if not task:
        return [], "任务不存在"
    wd = str(task.get("workdir") or "").strip()
    files = _workdir_chapter_files(wd) if wd and _isdir(wd) else []
    if not files:
        # workdir 缺失/异常：退回 run 成品口径（最新 run 优先）
        for r in store.task_runs(task_id):
            _wd, fs = store.run_artifacts(r.get("id") or "", limit=800)
            if fs:
                files = fs           # 任一 run 的成品口径都从任务首跑起，取到即够
                break
    # A scheduled chapter has been accepted by the platform queue, but is not
    # published yet.  Keep it out of the next batch so a retry cannot enqueue
    # a duplicate while preserving the published count for the UI and limits.
    done = (ledger.published_chapters(task_id, platform)
            | ledger.drafted_chapters(task_id, platform)
            | ledger.scheduled_chapters(task_id, platform)
            | ledger.review_chapters(task_id, platform))
    # 分卷标注（2026-10-08 用户需求：待发清单按卷展示）：卷计划解析不出时
    # volume 为空串，前端不分组平铺。
    plan = []
    try:
        from . import volumes as _volumes
        max_chapter = max((ledger.parse_chapter_no(str(f.get("name") or ""))
                           for f in files), default=0)
        plan = _volumes.plan_for_task(task, upto=max_chapter)
    except Exception:
        plan = []
    early_target = None
    try:
        early = store.serial_early_finish_plan(task)
        if early:
            early_target = int(early.get("target_chapter") or 0) or None
    except (AttributeError, TypeError, ValueError):
        early_target = None
    out, seen = [], set()
    for f in files:
        name = str(f.get("name") or "")
        if not name.lower().endswith((".md", ".txt")):
            continue
        if name.startswith(_DOC_DIR_MARKS):
            continue
        n = ledger.parse_chapter_no(name)
        if n <= 0 or (early_target is not None and n > early_target) or n in done or n in seen:
            continue
        seen.add(n)
        vol = _volumes.volume_for(n, plan) if plan else None
        out.append({"chapter_no": n, "file": name, "size": f.get("size") or 0,
                    "volume": (vol.get("name") or "") if vol else ""})
    out.sort(key=lambda x: x["chapter_no"])
    return out, ""


def _append_selected_unlisted_file(items, task, platform, chapter_no, filename):
    """Add one explicitly selected chapter whose number is only in its heading.

    The regular batch manifest intentionally requires a chapter number in the
    filename to avoid guessing among duplicate chapters. A one-file API request
    is unambiguous, so it may use the validated document heading and still go
    through the same reconciliation, retry, and idempotency path as every
    other automatic upload.
    """
    from pathlib import Path
    from . import manager

    if not filename or not chapter_no:
        return items
    filename = str(filename).replace("\\", "/")
    if any(str(item.get("file") or "").replace("\\", "/") == filename
           for item in items):
        return items
    # A parseable filename that is absent from pending was excluded for a
    # reason (already published, duplicate number, or release boundary).
    if ledger.parse_chapter_no(Path(filename).name) > 0:
        return items

    wd = str((task or {}).get("workdir") or "").strip()
    if not wd:
        return items
    root = Path(wd).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return items
    if not path.is_file() or path.suffix.lower() not in (".md", ".txt"):
        return items
    parsed_no, _title, _body, error = manager.read_chapter(str(path))
    if error or int(parsed_no or 0) != int(chapter_no):
        return items

    try:
        from .. import store
        early = store.serial_early_finish_plan(task)
        early_target = int((early or {}).get("target_chapter") or 0)
    except (AttributeError, TypeError, ValueError):
        early_target = 0
    if early_target and int(chapter_no) > early_target:
        return items

    done = (ledger.published_chapters(task.get("id"), platform)
            | ledger.drafted_chapters(task.get("id"), platform)
            | ledger.scheduled_chapters(task.get("id"), platform)
            | ledger.review_chapters(task.get("id"), platform))
    if int(chapter_no) in done:
        return items
    rel = path.relative_to(root).as_posix()
    items = list(items)
    items.append({"chapter_no": int(chapter_no), "file": rel,
                  "size": path.stat().st_size, "volume": ""})
    items.sort(key=lambda item: int(item.get("chapter_no") or 0))
    return items


def chapter_review_states(task, items, quality, hard_ok, hard_reason="", direct_ok=True):
    """逐章最新评审证据与实际发布门禁同时展示，不把单章通过当作放行。"""
    from .. import store, quality_gate
    try:
        reviewed_through = int(quality.get("reviewed_through") or 0)
    except (ValueError, TypeError):
        reviewed_through = 0
    runs = []
    for tid in store.serial_chain_ids(task.get("id")):
        runs.extend(store.task_runs(tid) or [])
    evidence = {}
    for run in sorted(runs, key=lambda r: str(r.get("id") or ""), reverse=True):
        verdict = run.get("verdict") if run.get("status") == "done" else None
        if not isinstance(verdict, dict):
            continue
        for score in verdict.get("chapter_scores") or []:
            if not isinstance(score, dict):
                continue
            try:
                n = int(score.get("chapter") or 0)
            except (ValueError, TypeError):
                continue
            if n > 0 and n not in evidence:
                evidence[n] = (score, run)
    for item in items:
        score, run = evidence.get(item["chapter_no"], ({}, {}))
        state, reason = "pending", "尚无有效单章评审"
        try:
            valid = (score.get("passed") is True
                     and int(score.get("reviewer_count") or 0) >= quality_gate.MIN_REVIEWERS
                     and score.get("reviewer_consensus") is True)
        except (ValueError, TypeError):
            valid = False
        if score.get("passed") is False:
            state, reason = "failed", "单章质量评审未通过"
        elif score and not valid:
            reason = "有效评审人数不足或未形成共识"
        elif valid:
            state, reason = "passed_blocked", "单章已过审，发布门禁未过"
            if not hard_ok:
                reason = hard_reason
            elif not quality.get("allowed"):
                reason = "；".join(quality.get("blockers") or []) or reason
            elif item["chapter_no"] > reviewed_through:
                reason = "目标章节超出当前发布评审版本"
            elif not direct_ok:
                reason = "平台发布流程尚未校准，可人工确认发布，暂不可自动直发"
            else:
                state, reason = "ready", "单章及发布门禁均已通过"
            try:
                reviewed_at = time.mktime(time.strptime(run.get("ended_at") or "", "%Y-%m-%d %H:%M:%S"))
                if os.path.getmtime(os.path.join(task.get("workdir") or "", item["file"])) > reviewed_at + 1:
                    state, reason = "stale", "正文在评审后修改，需要重新评审"
            except (OSError, ValueError, TypeError):
                pass
        item.update(review_status=state, review_reason=reason,
                    review_passed=valid and state != "stale",
                    publish_ready=state == "ready", review_run_id=run.get("id") or "")
    return items


def _automatic_retry_status(task_id):
    """Return the small, UI-safe projection of a durable batch retry."""
    with _BATCH_JOB_LOCK:
        record = _load_batch_jobs().get(str(task_id))
        if not isinstance(record, dict) or record.get("status") != "retry_wait":
            return None
        try:
            attempt = max(0, int(record.get("attempt") or 0))
        except (TypeError, ValueError):
            attempt = 0
        try:
            retry_at = float(record.get("next_retry_at") or 0)
        except (TypeError, ValueError):
            retry_at = 0
        return {"platform": str(record.get("platform") or ""),
                "status": "retry_wait", "error": str(record.get("error") or ""),
                "attempt": attempt, "next_retry_at": retry_at}


def status(task_id):
    """前端视图：待发清单 + 护栏状态 + 自动发布进度。

    护栏分两层给前端：guard_ok/guard_reason=自动发布路径的发布判定（硬护栏+
    自动质量决策），
    管「发布」类按钮；draft_ok/draft_reason=硬护栏（每日上限/建书
    确认），管「存草稿」按钮；自动质量决策产生的警告单独返回，避免把其
    显示成仍会阻断自动发布的错误。"""
    from .. import store
    from . import manager
    ent = ledger.books_for(str(task_id))   # 沿连载链继承：续写批次同书同账
    task = store.get_task(task_id)
    books = []
    for plat, info in ent.items():
        pend, err = pending(task_id, plat)
        hard_ok, hard_why = guards(task_id, plat)
        if not _book_ready(info):
            hard_ok, hard_why = False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
        blockers = []
        quality_warnings = []
        quality = {"allowed": False, "blockers": []}
        if task and _book_ready(info):
            release_flags = manager.automatic_release_kwargs(
                auto_submit=True, as_draft=False)
            quality = manager._quality_release_guard(
                task, plat, action="publish", **release_flags)
            if not quality.get("allowed"):
                blockers = [str(b) for b in (quality.get("blockers") or [])]
            quality_warnings = [str(w) for w in (quality.get("warnings") or [])]
        ok, why = hard_ok, hard_why
        if ok and blockers:
            ok, why = False, "质量门禁拦截：%s" % "；".join(blockers)
        direct_ok = calibrated(plat)
        chapter_review_states(task or {}, pend, quality, hard_ok, hard_why, direct_ok)
        published = ledger.published_chapters(task_id, plat)
        drafted = ledger.drafted_chapters(task_id, plat) - published
        scheduled = ledger.scheduled_chapters(task_id, plat) - published
        books.append({"platform": plat, "bound": True,
                      "title": info.get("title") or "",
                      "pending": len(pend),
                      "items": pend,
                      "published_latest": max(published, default=0),
                      "next_chapter": pend[0]["chapter_no"] if pend else None,
                      "ready_count": sum(p["publish_ready"] for p in pend),
                      "review_passed_count": sum(p["review_passed"] for p in pend),
                      "drafted_nos": sorted(drafted),
                      "scheduled_nos": sorted(scheduled),
                      "last_publish_at": info.get("remote_last_publish_at") or "",
                      "last_schedule_at": info.get("remote_last_schedule_at") or "",
                      "guard_ok": ok, "guard_reason": why,
                      "draft_ok": hard_ok, "draft_reason": hard_why,
                      "quality_blockers": blockers,
                      "quality_warnings": quality_warnings,
                      "quality_forced": bool(quality.get("forced")),
                      "drafted": len(drafted),
                      "scheduled": len(scheduled),
                      "calibrated": direct_ok})
    run = _running.get(str(task_id)) or None
    if run:
        run = dict(run)
    active = run and run.get("status") in ("running", "preparing", "reclaiming")
    retry = None if active else _automatic_retry_status(task_id)
    return {"books": books, "running": run, "retry": retry}


# ---------------------------------------------------------------- 顺序发布
def _wait_idle(platform, account="", slot=None, timeout=None):
    """Wait for the manager's platform action to finish (busy → other state)."""
    from . import manager
    if timeout is None:
        timeout = IDLE_TIMEOUT_S
    if slot is None:
        try:
            slot = manager.slot_of(platform, account)
        except (TypeError, ValueError):
            slot = platform
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            st = manager._st(slot)
            if st.get("status") != "busy":
                return True
        except Exception:
            return False               # manager 异常：别傻等
        time.sleep(IDLE_POLL_S)
    return False


def _start_upload_with_login_recovery(manager, task_id, platform, chapter_path,
                                      account, slot, upload_kwargs):
    """Retry a chapter start after reopening the saved account profile.

    Expired cookies may still require an external login challenge; this helper
    only resumes sessions the platform accepts without user interaction.
    """
    try:
        ok, err = manager.upload_chapter_async(
            task_id, platform, chapter_path, **upload_kwargs)
    except Exception as exc:
        ok, err = False, "自动上传启动异常：%s" % str(exc)[:200]
    if ok or not _needs_login_recovery(err):
        return ok, err
    try:
        connected, connect_error = manager.connect(
            platform, account=account, unattended=True)
    except Exception as exc:
        return False, "登录态自动恢复失败：%s" % str(exc)[:160]
    if not connected:
        return False, "登录态自动恢复失败：%s" % str(
            connect_error or "无法启动平台浏览器会话")[:160]
    deadline = time.monotonic() + LOGIN_RECOVERY_TIMEOUT_S
    while time.monotonic() < deadline:
        state = manager._st(slot)
        if state.get("status") == "connected":
            # Run the regular action preflight again. It rechecks the platform
            # session and idempotency before allowing the chapter operation.
            try:
                return manager.upload_chapter_async(
                    task_id, platform, chapter_path, **upload_kwargs)
            except Exception as exc:
                return False, "自动上传重试启动异常：%s" % str(exc)[:200]
        if state.get("status") == "error":
            return False, state.get("error") or err
        if _cancelled(task_id):
            return False, "登录态恢复等待期间发布批次已取消"
        time.sleep(1.0)
    return False, "等待平台登录态自动恢复超时：%s" % str(err or "未登录")[:120]


def _start_upload_with_retry(manager, task_id, platform, chapter_path,
                             account, slot, upload_kwargs, status=None):
    """Keep an unattended batch alive through recoverable pre-submit failures.

    This path is also used by one-off UI-triggered automatic batches, which do
    not have the durable scheduler around them to restart a failed batch. A
    platform security challenge remains a hard platform gate; retrying only
    checks whether the saved session becomes usable later and never interacts
    with or solves the challenge.
    """
    attempt = 0
    while True:
        ok, error = _start_upload_with_login_recovery(
            manager, task_id, platform, chapter_path, account, slot,
            upload_kwargs)
        if ok or not _upload_start_retryable(error):
            return ok, error
        attempt += 1
        if attempt >= PUBLISH_START_RETRY_ATTEMPTS:
            return False, "自动上传启动重试次数已达上限（%d）：%s" % (
                PUBLISH_START_RETRY_ATTEMPTS, str(error or "未知错误")[:180])
        delay = min(PUBLISH_START_RETRY_MAX_S,
                    PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
        if isinstance(status, dict):
            status["message"] = (
                "当前章节遇到可恢复的平台连接/会话状态，%d 秒后自动重试（第 %d 次）；"
                "安全验证仍由平台控制" % (delay, attempt))
        if not _batch_sleep(task_id, delay):
            return False, "自动重试等待期间发布批次已取消"


def _sync_with_session_recovery(manager, task_id, platform, account,
                                status=None):
    """Keep anti-duplicate platform reconciliation self-healing.

    Automatic releases must reconcile before submission. If the saved browser
    is temporarily unavailable after a restart, reopen that profile and retry
    the read-only reconciliation instead of ending a one-off batch that needs
    a person to press Publish again. Exhausted local attempts return the error
    to the batch finalizer, which persists backoff and releases the slot.
    """
    attempt = 0
    while True:
        try:
            ok, error = manager.sync_published(task_id, platform, manual=False)
        except Exception as exc:
            ok, error = False, "平台对账启动异常：%s" % str(exc)[:200]
        if ok or "未配置章节计数" in str(error or ""):
            return ok, error
        message = str(error or "")
        lower = message.lower()
        retryable = (
            _needs_login_recovery(message) or _is_transient(message)
            or any(mark in lower for mark in (
                "账号有操作正在进行中", "平台有操作正在进行中",
                "该账号有操作正在进行中", "browser busy", "timeout",
                "超时", "连接断开", "连接被关闭",
            )))
        if not retryable:
            return False, error
        attempt += 1
        if attempt >= PUBLISH_READ_RETRY_ATTEMPTS:
            return False, "平台对账重试次数已达上限：%s" % message
        if _needs_login_recovery(message):
            try:
                connected, connect_error = manager.connect(
                    platform, account=account, unattended=True)
            except Exception as exc:
                connected, connect_error = False, str(exc)
            if not connected and isinstance(status, dict):
                status["message"] = "平台会话自动恢复中：%s" % str(
                    connect_error or message)[:180]
        delay = min(PUBLISH_START_RETRY_MAX_S,
                    PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
        if isinstance(status, dict):
            status["message"] = "平台对账暂不可用，%d 秒后自动恢复重试（第 %d 次）" % (
                delay, attempt)
        if not _batch_sleep(task_id, delay):
            return False, "平台对账自动恢复期间发布批次已取消"


def _pending_with_recovery(task_id, platform, status=None):
    """Retry a transient chapter-list failure instead of treating it as empty.

    A failed post-reconciliation read must not turn a non-empty durable batch
    into a false success; the caller will clear its recovery record on success.
    """
    attempt = 0
    while True:
        try:
            chapters, error = pending(task_id, platform)
        except Exception as exc:
            chapters, error = [], "待发章节清单读取异常：%s" % str(exc)[:180]
        if not error or not _scheduled_error_is_persistent(error):
            return chapters, error
        attempt += 1
        if attempt >= PUBLISH_READ_RETRY_ATTEMPTS:
            return [], "待发章节清单重试次数已达上限：%s" % str(error)
        delay = min(PUBLISH_START_RETRY_MAX_S,
                    PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
        if isinstance(status, dict):
            status["message"] = (
                "待发章节清单暂不可用，%d 秒后自动重试（第 %d 次）" %
                (delay, attempt))
        if not _batch_sleep(task_id, delay):
            return [], "待发章节清单自动恢复期间发布批次已取消"


def _chapter_accepted(task_id, platform, chapter_no):
    """A confirmed live, scheduled, or review receipt completes submission."""
    return any(chapter_no in read(task_id, platform) for read in (
        ledger.published_chapters, ledger.scheduled_chapters,
        ledger.review_chapters))


def _reconcile_chapter_before_retry(manager, task_id, platform, chapter_no,
                                    account=""):
    """Resolve an accepted upload with a lost response before retrying it.

    A transient CDP failure after the submit click is ambiguous: the platform
    may have accepted the chapter even though the local worker failed before
    recording its receipt.  Reconcile first to avoid creating a duplicate.
    """
    try:
        ok, error = manager.sync_published(task_id, platform, manual=False)
    except Exception as exc:
        ok, error = False, "平台对账启动异常：%s" % str(exc)[:200]
    if (not ok and hasattr(manager, "connect") and
            (_needs_login_recovery(error) or _is_transient(error) or
             _scheduled_error_is_persistent(error))):
        # This is a read-only reconciliation. It is safe to wait for the
        # saved session/browser to recover before deciding whether a submit
        # click was accepted, and prevents an avoidable manual restart.
        ok, error = _sync_with_session_recovery(
            manager, task_id, platform, account, status=None)
    if not ok:
        return False, False, "重试前平台对账失败：%s" % (error or "未知错误")
    if int(chapter_no) in ledger.published_chapters(task_id, platform):
        return True, True, "平台已确认该章发布成功"
    if int(chapter_no) in ledger.scheduled_chapters(task_id, platform):
        return True, True, "平台已确认该章进入定时队列"
    if int(chapter_no) in ledger.review_chapters(task_id, platform):
        return True, True, "平台已确认该章进入审核队列"
    return True, False, ""


def calibrated(platform):
    """该平台是否有可用于无人值守直发的真机校准流程。

    用户目录覆盖表优先；仓库内带 ``-calibrated`` 后缀的流程也是已经
    真机验证过的基线，干净安装不能因为没有用户覆盖文件而把自动提交
    锁死。平台模块里的推测默认表仍不算校准。"""
    return flow_calibrated(platform, "upload_chapter")


def flow_calibrated(plat, action):
    """某动作的步骤表是否有真机校准来源（用户校准文件或仓库校准模板里
    有该动作的表）。内置推测表不算——存草稿虽不上线，批量乱点照样惹
    风控/攒一箱废稿，无人值守跑之前必须有验证过的步骤表。"""
    import json as _json
    from pathlib import Path as _Path
    from .. import paths as _paths
    from . import manager
    for fp in (_paths.PUBLISH_DIR / ("flows-%s.json" % plat),
               _Path(__file__).with_name("flows-%s-calibrated.json" % plat)):
        try:
            data = _json.loads(fp.read_text(encoding="utf-8"))
            steps = data.get(action) if isinstance(data, dict) else None
            if isinstance(steps, list) and manager._flow_contract_ok(
                    plat, action, steps):
                return True
        except Exception:
            pass
    return False


def publish_pending_async(task_id, platform, auto_submit=True, only=None,
                          force=False, force_confirmed=False, force_reason="",
                          as_draft=False, auto_prepare_book=False, scheduled=False,
                          _reserved_batch_lock=None, _only_file=None):
    """把任务的待发章节按章号顺序发出（后台线程）。返回 (ok, err)。

    only=章号清单（批量选择发布）：从待发清单里挑出所选章号，保持章号
    升序；所选全都不在待发清单里（已发过/解析不出章号）时报错不空跑。
    auto_submit=True（直发）逐章提交走完全程。非草稿发布统一归一为自动提交，
    即使旧客户端传入 ``auto_submit=False`` 也不会停在 manual_pause；这样不会
    导航离开未提交的编辑器，也不会把正常发布交给人工确认。
    as_draft=True（全部发草稿，2026-10-09）：逐章自动填稿并点「存草稿」，
    不上线、不受质量闸拦、无需人工确认，一章接一章连跑到清空待发清单
    （章间仍按 PACE_S 防风控节奏）；提交发布由用户到平台草稿箱手工完成，
    届时质量闸照常把关。与 auto_submit 互斥（草稿模式忽略 auto_submit）。
    草稿链三条专属护栏（2026-10-10 假成功案）：起跑前先 attach-only 对账
    平台实况并重算待发（已发布/已存稿剔除，对不了账就拒起）；跑批期间
    检测到另一批自动发布即让位停止（平台浏览器互斥）。"""
    from .. import store
    from . import manager
    if platform not in manager.PLATFORMS:
        return False, "未知平台"
    if as_draft:
        auto_submit = False                # 草稿模式没有「提交」语义
        if not flow_calibrated(platform, "upload_chapter_draft"):
            return False, ("存草稿模式需要该平台有 upload_chapter_draft 步骤表"
                           "（仓库校准模板或 data/publish/flows-%s.json）；"
                           "未校准不许无人值守批量动表单" % platform)
    else:
        # The public publishing contract is unattended. Keep the legacy
        # parameter for old callers, but never let it re-enable the former
        # fill-and-pause/manual-confirmation branch.
        auto_submit = True
        # Direct release means the task is asking for a completed publish
        # action. If platform setup is missing, prepare the metadata/work
        # automatically instead of returning a "create the book first" prompt.
        auto_prepare_book = True
    if auto_submit and not as_draft and not calibrated(platform):
        return False, ("自动提交模式需要先校准该平台发布流程：用「探测」按钮 dump "
                       "表单后把真实步骤写进 data/publish/flows-%s.json（缺省选择器"
                       "只是推测，未校准不许无人值守直发）" % platform)
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    from .. import contracts
    if not as_draft and not contracts.publish_allowed(task_id, unattended=bool(auto_submit)):
        return False, "任务需要先完成审批，才能发布产物"
    book = ledger.book_for(task_id, platform)
    account = str((book or {}).get("account") or
                  ((task.get("auto_publish") or {}).get("account") or "")).strip()
    try:
        slot = manager.slot_of(platform, account)
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    with _LOCK:
        cur = _running.get(task_id) or {}
        active = cur.get("status") in ("running", "preparing", "reclaiming")
        paused = cur.get("status") == "manual_pause"
    if (active or paused) and (auto_submit or as_draft):
        reclaimed, reclaim_reason = _reclaim_previous(task_id, platform, slot)
        if not reclaimed:
            _queue_automatic_retry(
                task_id, platform, reclaim_reason, auto_submit=auto_submit,
                as_draft=as_draft, only=only, force=force,
                force_confirmed=force_confirmed, force_reason=force_reason,
                scheduled=scheduled, auto_prepare_book=auto_prepare_book,
                only_file=_only_file, now=time.time())
            return False, reclaim_reason
    elif active:
        return False, "该任务已有自动发布进行中，请等本轮结束"
    if as_draft:
        other = other_batch_running(task_id, platform, slot)
        if other:
            return False, ("检测到同一平台账号的另一批自动发布/存草稿进行中（任务 %s）——"
                           "该账号浏览器会互踩，请等它跑完再存草稿" % other)
    needs_book_id = not str((book or {}).get("book_id") or "").strip()
    if auto_prepare_book and (not _book_ready(book) or needs_book_id):
        # Preparation is itself a single-flight batch.  Keep the HTTP request
        # responsive while metadata/model work and platform navigation run in
        # the same observable status slot; once the real book id is confirmed,
        # re-enter this function with preparation disabled.
        prep_lock = _reserved_batch_lock or _batch_slot_lock(slot)
        owns_prep_lock = _reserved_batch_lock is None
        if owns_prep_lock and not prep_lock.acquire(blocking=False):
            other = other_batch_running(task_id, platform, slot)
            reason = ("同一平台账号已有另一批自动发布进行中（任务 %s），"
                      "为避免串书已拒绝本批次" % other if other else
                      "该平台账号浏览器正在执行另一项发布操作，请稍后重试")
            _queue_automatic_retry(
                task_id, platform, reason, auto_submit=auto_submit,
                as_draft=as_draft, only=only, force=force,
                force_confirmed=force_confirmed, force_reason=force_reason,
                scheduled=scheduled, auto_prepare_book=auto_prepare_book,
                only_file=_only_file, now=time.time())
            return False, reason
        with _LOCK:
            cur = _running.get(task_id) or {}
            if cur.get("status") in ("running", "preparing"):
                if owns_prep_lock:
                    prep_lock.release()
                return False, "该任务已有自动发布进行中，请等本轮结束"
            prep = {"platform": platform, "account": account, "slot": slot,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "done": 0, "total": 0, "status": "preparing",
                    "error": "", "auto_submit": bool(auto_submit),
                    "as_draft": bool(as_draft), "last_chapter": 0,
                    "skipped": 0, "notes": []}
            _running[task_id] = prep
            prep_event = threading.Event()
            _cancel_events[task_id] = prep_event
        try:
            _remember_automatic_batch(
                task_id, platform, auto_submit, as_draft, only, force,
                force_confirmed, force_reason, scheduled, auto_prepare_book,
                str(_only_file) if _only_file is not None else None)
        except Exception as exc:
            with _LOCK:
                if _running.get(task_id) is prep:
                    prep["status"] = "error"
                    prep["error"] = "自动发布恢复记录保存失败：%s" % exc
            _release_cancel_event(task_id, prep_event)
            if owns_prep_lock:
                prep_lock.release()
            return False, prep["error"]

        def prepare_then_publish():
            transferred = False
            try:
                ok_p, why_p = _prepare_book(task_id, platform)
                if not ok_p:
                    prep["status"] = "error"
                    prep["error"] = why_p
                    return
                if _cancelled(task_id):
                    prep["status"] = "cancelled"
                    return
                ok_next, why_next = publish_pending_async(
                    task_id, platform, auto_submit=auto_submit, only=only,
                    force=force, force_confirmed=force_confirmed,
                    force_reason=force_reason, as_draft=as_draft,
                    auto_prepare_book=False,
                    _reserved_batch_lock=prep_lock, _only_file=_only_file)
                if not ok_next:
                    prep["status"] = "error"
                    prep["error"] = why_next
                else:
                    transferred = True
            except Exception as exc:
                prep["status"] = "error"
                prep["error"] = "自动准备作品失败：%s" % exc
            finally:
                _release_cancel_event(task_id, prep_event)
                if owns_prep_lock and not transferred:
                    prep_lock.release()
                    if not _retain_automatic_batch_for_retry(
                            task_id, prep.get("error"), now=time.time()):
                        _forget_automatic_batch(task_id)

        started, start_error = _start_worker_or_retry(
            threading.Thread(target=prepare_then_publish, daemon=True,
                             name="pub-prepare-%s" % platform),
            task_id, prep, prep_event, prep_lock, owns_prep_lock)
        if not started:
            return False, start_error
        return True, ""
    if not book:
        return False, "该任务尚未在此平台建书，请先「创建作品」"
    if not _book_ready(book):
        return False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
    if as_draft:
        # 草稿不上线：质量闸不适用（同 manager 口径），硬护栏照查
        quality = {"allowed": True, "status": "draft_only"}
    else:
        release_flags = manager.automatic_release_kwargs(
            auto_submit, force=force, force_confirmed=force_confirmed,
            force_reason=force_reason, as_draft=as_draft)
        quality = manager._quality_release_guard(
            task, platform, action="publish", **release_flags)
    if not quality.get("allowed"):
        error = "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
        # A quality decision can change when a later review run completes.
        # Keep an unattended request durable so a passing verdict resumes the
        # original batch without asking the user to click Publish again. Every
        # retry evaluates the latest verdict and still stops at this gate.
        with _BATCH_JOB_LOCK:
            prior = _load_batch_jobs().get(str(task_id))
            already_resuming = (isinstance(prior, dict) and
                                prior.get("status") == "resuming")
        if auto_submit and not as_draft and not already_resuming:
            try:
                _remember_automatic_batch(
                    task_id, platform, auto_submit, as_draft, only, force,
                    force_confirmed, force_reason, scheduled,
                    auto_prepare_book,
                    str(_only_file) if _only_file is not None else None)
                _retain_automatic_batch_for_retry(task_id, error, now=time.time())
            except Exception as exc:
                error += "；自动恢复记录保存失败：%s" % str(exc)[:120]
        return False, error
    ok, why = guards(task_id, platform)
    if not ok:
        # A direct unattended batch can be started after the platform's daily
        # cap has already been consumed (including by chapters uploaded
        # outside this app). Preserve that accepted request so the scheduler
        # can reconcile and continue after the cap rolls over, without asking
        # the user to press Publish again tomorrow.
        _queue_automatic_retry(
            task_id, platform, why, auto_submit=auto_submit,
            as_draft=as_draft, only=only, force=force,
            force_confirmed=force_confirmed, force_reason=force_reason,
            scheduled=scheduled, auto_prepare_book=auto_prepare_book,
            only_file=_only_file, now=time.time())
        return False, why
    try:
        # Keep request preflight bounded. A durable retry record is safer than
        # holding the API caller indefinitely when the workdir is unavailable.
        pend, err = pending(task_id, platform)
    except Exception as exc:
        pend, err = [], "待发章节清单读取异常：%s" % str(exc)[:180]
    if err:
        _queue_automatic_retry(
            task_id, platform, err, auto_submit=auto_submit,
            as_draft=as_draft, only=only, force=force,
            force_confirmed=force_confirmed, force_reason=force_reason,
            scheduled=scheduled, auto_prepare_book=auto_prepare_book,
            only_file=_only_file, now=time.time())
        return False, err
    want = None
    if only is not None:
        try:
            want = {int(n) for n in (only or []) if n}
        except (TypeError, ValueError):
            return False, "chapters 必须是章号数组"
        pend = [p for p in pend if p["chapter_no"] in want]
    if _only_file is not None and want and len(want) == 1:
        pend = _append_selected_unlisted_file(
            pend, task, platform, next(iter(want)), _only_file)
    if only is not None:
        # Keep the one-file fallback constrained to the explicitly requested
        # chapter even if a caller supplied a broader file path.
        pend = [p for p in pend if p["chapter_no"] in want]
        if not pend:
            return False, ("所选章节都不在待发清单里（已发过，或成品里没有"
                           "对应章节文件）；可先点「校准」对齐平台实况")
    if _only_file is not None:
        requested_file = str(_only_file)
        pend = [p for p in pend if str(p.get("file") or "") == requested_file]
        if not pend:
            return False, "所选章节文件已不在待发清单中，未发布其他同章号文件"
    if not pend:
        return False, "没有待发章节（全部已发布，或成品里没有可识别的章节文件）"

    # Hold the account/browser slot for the whole batch.  The per-chapter
    # manager lock only covers one upload and otherwise lets another task
    # navigate the shared profile between chapters, causing cross-book pages
    # and false verification results.
    batch_slot_lock = _reserved_batch_lock or _batch_slot_lock(slot)
    owns_batch_lock = _reserved_batch_lock is None
    if owns_batch_lock and not batch_slot_lock.acquire(blocking=False):
        other = other_batch_running(task_id, platform, slot)
        if other:
            reason = ("同一平台账号已有另一批自动发布进行中（任务 %s），"
                      "为避免串书已拒绝本批次" % other)
        else:
            reason = "该平台账号浏览器正在执行另一项发布操作，请稍后重试"
        _queue_automatic_retry(
            task_id, platform, reason, auto_submit=auto_submit,
            as_draft=as_draft, only=only, force=force,
            force_confirmed=force_confirmed, force_reason=force_reason,
            scheduled=scheduled, auto_prepare_book=auto_prepare_book,
            only_file=_only_file, now=time.time())
        return False, reason

    from pathlib import Path
    wd = task.get("workdir") or ""
    st = {"platform": platform, "account": account, "slot": slot,
          "at": time.strftime("%Y-%m-%d %H:%M:%S"),
          "done": 0, "total": len(pend), "status": "running", "error": "",
          "auto_submit": bool(auto_submit), "as_draft": bool(as_draft),
          "last_chapter": 0, "skipped": 0, "notes": []}
    with _LOCK:
        cur = _running.get(task_id) or {}
        allowed_preparation_handoff = (
            _reserved_batch_lock is not None and cur.get("status") == "preparing")
        if cur.get("status") in ("running", "preparing", "reclaiming") \
                and not allowed_preparation_handoff:
            if owns_batch_lock:
                batch_slot_lock.release()
            return False, "该任务已有自动发布进行中，请等本轮结束"
        _running[task_id] = st
        batch_event = threading.Event()
        _cancel_events[task_id] = batch_event
    try:
        _remember_automatic_batch(
            task_id, platform, auto_submit, as_draft, only, force,
            force_confirmed, force_reason, scheduled, auto_prepare_book,
            str(_only_file) if _only_file is not None else None)
    except Exception as exc:
        with _LOCK:
            st["status"] = "error"
            st["error"] = "自动发布恢复记录保存失败：%s" % exc
        _release_cancel_event(task_id, batch_event)
        batch_slot_lock.release()
        return False, st["error"]

    def run():
        try:
            if _cancelled(task_id):
                st["status"] = "cancelled"
                return
            items = pend
            if as_draft or auto_submit:
                # 自动操作前对账：台账只记得自己发的章，
                # 手工补交的只有平台知道——attach-only 刷一次校准再重算待发，
                # 已发布/已存稿章就地剔除，不去撞序号重复。对不了账（浏览器
                # 未连接/忙）整个拒起：自动操作本来就需要平台浏览器，宁等空窗
                # 不盲跑；平台没配章节计数时跳过对账维持旧路径。
                ok_r, why_r = _sync_with_session_recovery(
                    manager, task_id, platform, account, status=st)
                if not ok_r and "未配置章节计数" not in str(why_r or ""):
                    st["status"] = "error"
                    if "自动恢复期间发布批次已取消" in str(why_r):
                        st["status"] = "cancelled"
                        st["message"] = "已取消发布批次"
                        return
                    action_label = "存草稿" if as_draft else "自动发布"
                    st["error"] = ("%s前对账失败：%s。为防重复提交已存在章节，"
                                   "本次未操作；请连接平台浏览器并等它空闲后重试"
                                   % (action_label, why_r))
                    return
                fresh, fresh_error = _pending_with_recovery(
                    task_id, platform, status=st)
                if fresh_error:
                    st["status"] = "cancelled" if "已取消" in fresh_error else "error"
                    st["error"] = fresh_error
                    return
                if want is not None:
                    fresh = [p for p in fresh if p["chapter_no"] in want]
                if _only_file is not None and want and len(want) == 1:
                    fresh = _append_selected_unlisted_file(
                        fresh, task, platform, next(iter(want)), _only_file)
                if _only_file is not None:
                    fresh = [p for p in fresh
                             if str(p.get("file") or "") == str(_only_file)]
                if not fresh:
                    st["status"] = "done"
                    if as_draft:
                        st["message"] = ("对账后没有可存草稿的章节（所选均已发布或"
                                         "已存稿）；确需重存请先在平台删旧稿")
                    else:
                        st["message"] = "对账后没有可发布的章节（所选均已在平台存在）"
                    return
                if len(fresh) != st["total"]:
                    verb = "实存" if as_draft else "实发"
                    st["notes"].append("对账剔除 %d 章已发布/已存稿，%s %d 章"
                                       % (st["total"] - len(fresh), verb, len(fresh)))
                st["total"] = len(fresh)
                items = fresh
            for item in items:
                if _cancelled(task_id):
                    st["status"] = "cancelled"
                    st["message"] = "已暂停后续章节发布"
                    return
                if as_draft:
                    other = other_batch_running(task_id, platform, slot)
                    if other:
                        st["status"] = "error"
                        st["error"] = ("检测到另一批自动发布已启动（任务 %s），"
                                       "存草稿让位停止；已存 %d 章落平台草稿箱，"
                                       "等对方跑完再续" % (other, st["done"]))
                        return
                g_ok, why = guards(task_id, platform)   # 每章前复查（中途也能拦）
                if not g_ok:
                    st["status"] = "error"
                    st["error"] = "第 %d 章前护栏拦截：%s" % (item["chapter_no"], why)
                    return
                upload_kwargs = {"auto_submit": auto_submit, "as_draft": as_draft}
                if scheduled:
                    # 强制定时（2026-10-17）：当日字数上限被账户级占用时平台
                    # 仍接受「定时到明日」——直发/定时开关消失只是直发被禁，
                    # 显式走 upload_chapter_scheduled 校准流程。
                    upload_kwargs["scheduled"] = True
                else:
                    # 直发优先（2026-10-19 用户指令：当天写的当天发完）：账号
                    # 定时队列有积压时平台弹「建议本章定时发布」横幅并隐藏定时
                    # 开关，但直发本身可用——force_direct 让流程点否拒绝横幅、
                    # 直接确认发布；超直发额度时由上限弹窗分支自动转定时。
                    upload_kwargs["direct_today"] = True
                upload_kwargs.update(manager.automatic_release_kwargs(
                    auto_submit, force=force, force_confirmed=force_confirmed,
                    force_reason=force_reason, as_draft=as_draft))
                chapter_path = str(Path(wd) / item["file"])
                ok2, err2 = _start_upload_with_retry(
                    manager, task_id, platform, chapter_path, account, slot,
                    upload_kwargs, status=st)
                if not ok2 and as_draft and _is_idempotent_reject(err2):
                    # 幂等拒起＝这章已发布/已存过稿：跳过，不是失败——
                    # 整链不该因「没活干」停摆
                    st["skipped"] += 1
                    st["notes"].append("第 %d 章跳过：%s"
                                       % (item["chapter_no"], str(err2)[:60]))
                    continue
                if not ok2:
                    if "自动重试等待期间发布批次已取消" in str(err2):
                        st["status"] = "cancelled"
                        st["message"] = "已取消发布批次"
                        return
                    st["status"] = "error"
                    st["error"] = "第 %d 章发起失败：%s" % (item["chapter_no"], err2)
                    return
                if not _wait_idle(platform, account=account, slot=slot):
                    st["status"] = "error"
                    if as_draft:
                        st["error"] = ("第 %d 章存草稿等待超时（%.0f 分钟），后续"
                                       "章节未存" % (item["chapter_no"],
                                                     IDLE_TIMEOUT_S / 60))
                    else:
                        st["error"] = ("第 %d 章发布等待超时（%.0f 分钟）；若在等人工提交，"
                                       "请提交后重跑剩余章节" % (item["chapter_no"],
                                                                 IDLE_TIMEOUT_S / 60))
                    return
                # A browser/CDP disconnect can be reported asynchronously
                # after upload_chapter_async accepted the job. Retry only that
                # known transient class once; never enqueue through the stale
                # timed-publish flow.
                if (auto_submit and not as_draft and
                        not _chapter_accepted(task_id, platform,
                                              item["chapter_no"])):
                    latest = next((
                        r for r in ledger.recent(task_id=task_id,
                                                 platform=platform, limit=20)
                        if int(r.get("chapter_no") or 0) == item["chapter_no"]
                        and r.get("ok") is False), None)
                    if latest and _is_transient(latest.get("error")):
                        reconciled, already_submitted, reconcile_error = (
                            _reconcile_chapter_before_retry(
                                manager, task_id, platform, item["chapter_no"],
                                account=account))
                        if not reconciled:
                            st["status"] = "error"
                            st["error"] = "第 %d 章提交结果不确定，%s；为避免重复提交已停止" % (
                                item["chapter_no"], reconcile_error)
                            return
                        if already_submitted:
                            st["done"] += 1
                            st["last_chapter"] = item["chapter_no"]
                            st["notes"].append("第 %d 章已由平台对账确认，无需重发" %
                                               item["chapter_no"])
                            if st["done"] < st["total"] and not _batch_sleep(
                                    task_id, PACE_S):
                                st["status"] = "cancelled"
                                st["message"] = "已回收旧发布批次"
                                return
                            continue
                        st["notes"].append("第 %d 章连接中断，%d 秒后自动重试"
                                           % (item["chapter_no"],
                                              TRANSIENT_RETRY_S))
                        if not _batch_sleep(task_id, TRANSIENT_RETRY_S):
                            st["status"] = "cancelled"
                            st["message"] = "已回收旧发布批次"
                            return
                        ok_retry, err_retry = _start_upload_with_retry(
                            manager, task_id, platform, chapter_path, account,
                            slot, upload_kwargs, status=st)
                        if not ok_retry:
                            if "自动重试等待期间发布批次已取消" in str(err_retry):
                                st["status"] = "cancelled"
                                st["message"] = "已取消发布批次"
                                return
                            st["status"] = "error"
                            st["error"] = "第 %d 章重试发起失败：%s" % (
                                item["chapter_no"], err_retry)
                            return
                        if not _wait_idle(platform, account=account, slot=slot):
                            st["status"] = "error"
                            st["error"] = "第 %d 章重试等待超时" % item["chapter_no"]
                            return
                if as_draft:
                    # manager 走完草稿流程（含平台侧验证）才返回；台账落了
                    # upload_chapter_draft 才算数，防「流程跑完但平台没存上」。
                    if item["chapter_no"] not in ledger.drafted_chapters(
                            task_id, platform):
                        st["status"] = "error"
                        st["error"] = ("第 %d 章存草稿未确认（台账未落草稿账），"
                                       "后续章节未存；截图存证见发布台账，若实际"
                                       "已保存成功，把页面反馈文案照截图校准进"
                                       "流程表 expect_text 步骤"
                                       % item["chapter_no"])
                        detail = _chapter_failure(task_id, platform, item["chapter_no"],
                                                  as_draft=True)
                        if detail:
                            st["error"] = "第 %d 章存草稿失败：%s" % (item["chapter_no"], detail)
                        return
                    st["done"] += 1
                    st["last_chapter"] = item["chapter_no"]
                    if st["done"] + st["skipped"] < st["total"]:
                        if not _batch_sleep(task_id, PACE_S):
                            st["status"] = "cancelled"
                            st["message"] = "已回收旧发布批次"
                            return
                    continue
                # The platform may switch a direct request to scheduling or
                # review. Either confirmed receipt permits the next chapter;
                # neither changes the ledger's public-release semantics.
                if not _chapter_accepted(task_id, platform, item["chapter_no"]):
                    st["status"] = "error"
                    st["error"] = ("第 %d 章流程结束，但未确认平台发布成功；"
                                   "后续章节已停止，避免重复或跳章" % item["chapter_no"])
                    detail = _chapter_failure(task_id, platform, item["chapter_no"])
                    if detail:
                        st["error"] = "第 %d 章发布失败：%s" % (item["chapter_no"], detail)
                    return
                st["done"] += 1
                st["last_chapter"] = item["chapter_no"]
                if st["done"] < st["total"]:
                    if not _batch_sleep(task_id, PACE_S):
                        st["status"] = "cancelled"
                        st["message"] = "已回收旧发布批次"
                        return
            st["status"] = "done"
            if as_draft:
                st["message"] = ("已存草稿 %d 章%s（未发布，不受质量闸拦不代表可"
                                 "直发）；请到平台章节管理/草稿箱逐章检查后提交"
                                 "发布"
                                 % (st["done"], ("、跳过 %d 章" % st["skipped"])
                                    if st["skipped"] else ""))
        except Exception as e:                 # 线程内绝不能悬挂无终态
            st["status"] = "error"
            st["error"] = "自动发布异常：%s" % e
        finally:
            st["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _release_cancel_event(task_id, batch_event)
            batch_slot_lock.release()
            if not _retain_automatic_batch_for_retry(
                    task_id, st.get("error"), now=time.time()):
                _forget_automatic_batch(task_id)

    started, start_error = _start_worker_or_retry(
        threading.Thread(target=run, daemon=True,
                         name="pub-auto-%s" % platform),
        task_id, st, batch_event, batch_slot_lock, owns_batch_lock)
    if not started:
        return False, start_error
    return True, ""


# ---------------------------------------------------------------- 定时联动（P2.5）
# 任务级标记 task.auto_publish = {enabled, platform, time:"HH:MM", auto_submit}
# automation._tick 每 25s 调 fire_due()：到点且今日未触发 → publish_pending_async。
# 「今日已触发」记在 data/publish/auto_publish.json（task_id → YYYY-MM-DD）：
# 定时任务每天触发一次，失败可由用户立即重新发起，不受历史连败限制。
_AP_FILE = None            # paths.PUBLISH_DIR / "auto_publish.json"（导入惰性定）
_AP_FIRED = None           # 内存缓存 {task_id: "YYYY-MM-DD"}
_AP_LOCK = threading.RLock()   # 可重入：_mark_fired 持锁内调 _load_fired 再进同锁
_AP_RETRY_FILE = None      # durable lifecycle for scheduled unattended batches
_AP_RETRY = None
SCHEDULE_RETRY_MAX_ATTEMPTS = 5
SCHEDULE_RETRY_BASE_S = 60
SCHEDULE_RETRY_MAX_S = 30 * 60


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
    if retryable_publish_start(auto_submit=True, as_draft=False,
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


def _ap_file():
    global _AP_FILE
    if _AP_FILE is None:
        from .. import paths
        _AP_FILE = paths.PUBLISH_DIR / "auto_publish.json"
    return _AP_FILE


def _load_fired():
    global _AP_FIRED
    with _AP_LOCK:
        if _AP_FIRED is None:
            try:
                import json
                d = json.loads(_ap_file().read_text(encoding="utf-8"))
                _AP_FIRED = d if isinstance(d, dict) else {}
            except Exception:
                _AP_FIRED = {}
        return _AP_FIRED


def _mark_fired(task_id, day):
    import json
    with _AP_LOCK:
        fired = _load_fired()
        fired[str(task_id)] = day
        try:
            _ap_file().parent.mkdir(parents=True, exist_ok=True)
            tmp = _ap_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(fired, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(_ap_file())
        except Exception:
            pass                            # 记录失败：最坏是当日重复触发，护栏会拦


def _retry_file():
    global _AP_RETRY_FILE
    if _AP_RETRY_FILE is None:
        from .. import paths
        _AP_RETRY_FILE = paths.PUBLISH_DIR / "auto_publish_retry.json"
    return _AP_RETRY_FILE


def _load_retry_state():
    global _AP_RETRY
    with _AP_LOCK:
        if _AP_RETRY is None:
            try:
                import json
                data = json.loads(_retry_file().read_text(encoding="utf-8"))
                _AP_RETRY = data if isinstance(data, dict) else {}
            except Exception:
                _AP_RETRY = {}
        return _AP_RETRY


def _save_retry_state():
    import json
    with _AP_LOCK:
        try:
            path = _retry_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(_load_retry_state(), ensure_ascii=False,
                                      indent=1), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass


def _retry_delay(attempt):
    exponent = max(0, int(attempt or 1) - 1)
    return min(SCHEDULE_RETRY_MAX_S,
               SCHEDULE_RETRY_BASE_S * (2 ** min(exponent, 12)))


def scheduled_retry_state(prior, run_status, now=None, day=None, error=None):
    """Advance one scheduled batch state and say whether it may be started.

    Missing in-memory run state while a persisted attempt is in flight means
    the service restarted. Wait with backoff, then let the normal platform
    reconciliation/idempotency path safely resume the remaining chapters.
    """
    now = time.time() if now is None else float(now)
    day = day or time.strftime("%Y-%m-%d")
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
        if (attempt >= SCHEDULE_RETRY_MAX_ATTEMPTS and
                not _scheduled_error_is_persistent(effective_error)):
            state["status"] = "exhausted"
            state.pop("next_retry_at", None)
            return state, False
        state["status"] = "retry_wait"
        state["next_retry_at"] = now + _retry_delay(attempt)
        return state, False
    if status == "exhausted":
        # Older releases exhausted every error after five attempts.  If the
        # durable record still carries a known recoverable reason, migrate it
        # back into the unattended retry loop instead of leaving it stranded.
        if _scheduled_error_is_persistent(effective_error):
            state["status"] = "retry_wait"
            state["next_retry_at"] = now + _retry_delay(attempt)
            return state, False
        return state, False
    if status in ("succeeded", "cancelled"):
        return state, False
    if status == "retry_wait":
        if (attempt >= SCHEDULE_RETRY_MAX_ATTEMPTS and
                not _scheduled_error_is_persistent(effective_error)):
            state["status"] = "exhausted"
            state.pop("next_retry_at", None)
            return state, False
        return state, now >= float(state.get("next_retry_at") or 0)
    return state, True


def _scheduled_retry(task_id, day, run_status=None, now=None, error=None):
    with _AP_LOCK:
        states = _load_retry_state()
        key = str(task_id)
        next_state, ready = scheduled_retry_state(
            states.get(key), run_status, now=now, day=day, error=error)
        if states.get(key) != next_state:
            states[key] = next_state
            _save_retry_state()
        return dict(next_state), ready


def _set_scheduled_retry(task_id, state):
    with _AP_LOCK:
        _load_retry_state()[str(task_id)] = dict(state)
        _save_retry_state()


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
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}, ""


def due_tasks(now=None):
    """到期待触发的定时发布任务清单：[(task, auto_publish)]。

    条件：enabled + 存在待发章节 + 今日未触发 + 当前时间已过当日 time。
    尚未建书的任务也可进入批次；publish_pending_async 会自动生成作品资料并
    建书，只有真实取得平台作品 ID 后才继续发章。"""
    from .. import store
    now = now or time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    hhmm_now = time.strftime("%H:%M", now)
    fired = _load_fired()
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
            with _LOCK:
                running = _running.get(str(task["id"])) or {}
            retry, retry_ready = _scheduled_retry(
                task["id"], today, run_status=running.get("status"),
                error=running.get("error"), now=time.time())
            if retry.get("status") == "succeeded":
                _mark_fired(task["id"], today)
                continue
            if not retry_ready:
                continue
            plat = ap.get("platform")
            chapters, chapter_error = pending(task["id"], plat) if plat else ([], "")
            if plat and not chapter_error and chapters:
                out.append((task, ap))
    return out


def fire_due(now=None):
    """tick 入口：把所有到期的定时发布触发一轮。返回触发条数；单条异常不拖累其余。"""
    now = now or time.localtime()       # 且被双层 except 双重静默成幽灵 0
    today = time.strftime("%Y-%m-%d", now)
    n = 0
    for task, ap in due_tasks(now=now):
        retry, _ready = _scheduled_retry(task["id"], today, now=time.time())
        retry["attempt"] = int(retry.get("attempt") or 0) + 1
        retry["status"] = "in_flight"
        retry["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        retry.pop("next_retry_at", None)
        _set_scheduled_retry(task["id"], retry)
        try:
            ok, err = publish_pending_async(
                task["id"], ap.get("platform"),
                auto_submit=True, auto_prepare_book=True)
            if ok:
                ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              title="定时触发 %s" % (ap.get("time") or ""), ok=True)
                n += 1
            else:
                retry, _ready = scheduled_retry_state(
                    retry, "error", now=time.time(), day=today,
                    error=err or "自动发布启动失败")
                _set_scheduled_retry(task["id"], retry)
                ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              title="定时触发 %s" % (ap.get("time") or ""),
                              ok=False, error=str(err)[:200])
        except Exception as e:
            retry, _ready = scheduled_retry_state(
                retry, "error", now=time.time(), day=today, error=e)
            _set_scheduled_retry(task["id"], retry)
            try:
                ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              ok=False, error=str(e)[:200])
            except Exception:
                pass
    return n
