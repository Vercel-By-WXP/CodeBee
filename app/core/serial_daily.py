# -*- coding: utf-8 -*-
"""Daily serial-writing and publishing plans with durable deadline tracking."""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime

from . import atomic, paths, store

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_TICK_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "serial_daily.json"
_JOBS = {}
_LOADED = False
_TIME_RE = re.compile(r"^(\d{2}):(\d{2})$")
PUBLISH_RETRY_MAX_ATTEMPTS = 5
PUBLISH_RETRY_BASE_S = 60
PUBLISH_RETRY_MAX_S = 30 * 60


def _ensure_loaded():
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        try:
            raw = json.loads(_FILE.read_text(encoding="utf-8"))
            rows = raw.get("jobs", []) if isinstance(raw, dict) else []
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    _JOBS[str(row["id"])] = row
        except (OSError, ValueError, TypeError):
            pass
        _LOADED = True


def _save_locked():
    atomic.write_json(_FILE, {"version": 1, "jobs": list(_JOBS.values())})


def _copy(job):
    return json.loads(json.dumps(job, ensure_ascii=False))


def list_jobs():
    _ensure_loaded()
    with _LOCK:
        return [_copy(_JOBS[key]) for key in sorted(_JOBS, reverse=True)]


def get(job_id):
    _ensure_loaded()
    with _LOCK:
        job = _JOBS.get(str(job_id))
        return _copy(job) if job else None


def serial_tasks():
    return [task for task in store.list_tasks(limit=10 ** 9, archived=False)
            if task.get("type") in ("serial_novel", "novel-serial")]


def create(payload):
    payload = payload if isinstance(payload, dict) else {}
    _ensure_loaded()
    task_id = str(payload.get("task_id") or "").strip()
    task = store.get_task(task_id)
    if not task or task.get("type") not in ("serial_novel", "novel-serial"):
        raise ValueError("请选择有效的连载小说任务")
    from .publish import manager
    platform = str(payload.get("platform") or "").strip()
    if platform not in manager.PLATFORMS:
        raise ValueError("platform 必须是 fanqie 或 qimao")
    try:
        count = int(payload.get("chapters_per_day") or 0)
    except (TypeError, ValueError):
        count = 0
    if not 1 <= count <= 20:
        raise ValueError("每日章节数必须在 1 到 20 之间")
    publish_time = str(payload.get("publish_time") or "").strip()
    match = _TIME_RE.match(publish_time)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError("发布时间必须是 24 小时制 HH:MM")
    job = {"id": "serial-daily-%s-%04d" % (
        time.strftime("%Y%m%d-%H%M%S"), secrets.randbelow(10000)),
        "task_id": task_id, "task_title": task.get("title") or task.get("goal") or "连载",
        "platform": platform, "chapters_per_day": count,
        "publish_time": publish_time,
        # Platform publishing runs unattended once a serial-daily job is
        # accepted. Preserve draft-only as a separate publish workflow.
        "auto_submit": True,
        "status": "active", "phase": "waiting_generation", "created_at": _now(),
        "day": "", "run_id": "", "run_task_id": "",
        "baseline_chapters": [], "new_chapters": [], "publish_backlog": [],
        "publish_retry": {"attempt": 0, "status": "ready"},
        "last_day_status": "", "last_error": "", "alerted_day": ""}
    with _LOCK:
        _JOBS[job["id"]] = job
        _save_locked()
    return _copy(job)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _mutate(job_id, **values):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        job.update(values)
        _save_locked()
        return _copy(job)


def pause(job_id):
    with _TICK_LOCK:
        current = get(job_id)
        values = {"status": "paused", "updated_at": _now()}
        if current and current.get("day") and current.get("last_day_status") != "done":
            values["last_day_status"] = "paused"
        job = _mutate(job_id, **values)
        if job:
            _cancel_activity(job)
        return job


def resume(job_id):
    job = get(job_id)
    if not job or job.get("status") == "cancelled":
        return None
    values = {"status": "active", "updated_at": _now()}
    if job.get("last_day_status") == "paused":
        values["last_day_status"] = "running"
    return _mutate(job_id, **values)


def cancel(job_id):
    with _TICK_LOCK:
        job = _mutate(job_id, status="cancelled", phase="cancelled", updated_at=_now())
        if job:
            _cancel_activity(job)
        return job


def _cancel_activity(job):
    try:
        from .publish import auto
        auto.cancel_pending(job.get("run_task_id") or job["task_id"])
    except Exception:
        log.exception("serial daily: publish cancellation failed job=%s", job.get("id"))
    if not job.get("run_id"):
        return
    try:
        from . import jobs
        run = store.get_run(job["run_id"])
        if run and run.get("status") in ("queued", "running"):
            jobs.cancel(job["run_id"])
    except Exception:
        log.exception("serial daily: run cancellation failed job=%s", job.get("id"))


def _chapter_numbers(task_id):
    from .publish import auto, ledger
    task = store.get_task(task_id) or {}
    wd = str(task.get("workdir") or "")
    return {n for item in auto._workdir_chapter_files(wd)
            if (n := ledger.parse_chapter_no(item.get("name") or "")) > 0}


def _start_generation(job, day, chapters=None):
    from . import jobs
    task = store.get_task(job["task_id"])
    if not task:
        raise ValueError("连载任务已删除")
    baseline = sorted(_chapter_numbers(job["task_id"]))
    start_chapter = max(baseline, default=0) + 1
    chapters = int(chapters or job["chapters_per_day"])
    goal = ("今天必须完成 %d 章连载并写入工作目录。先读取故事圣经和现有章节，"
            "从当前故事进度续写，确保产出恰好 %d 个新的完整章节文件；不要覆盖已有章节。"
            "发布计划在 %s，完成写作与质量检查后等待发布时刻。" % (
                chapters, chapters, job["publish_time"]))
    daily_task = store.create_task({
        "type": "serial_novel", "title": "%s 日更 %s" % (job["task_title"], day),
        "goal": goal, "workdir": task.get("workdir"),
        "serial": {"chapters": chapters, "start_chapter": start_chapter,
                   "continues": task["id"]}})
    run = store.create_run("orchestration", daily_task["title"],
                           task_id=daily_task["id"])
    store.update_task_status(daily_task["id"], "queued")
    try:
        jobs.enqueue({"kind": "orchestration", "run_id": run["id"],
                      "task_id": daily_task["id"]})
    except Exception:
        store.update_run(run["id"], status="failed", error="日更运行排队失败",
                         ended_at=_now())
        raise
    return run["id"], baseline, daily_task["id"]


def _publish(job, only):
    from .publish import auto
    return auto.publish_pending_async(
        job.get("run_task_id") or job["task_id"], job["platform"],
        auto_submit=True, auto_prepare_book=True,
        only=only, scheduled=False)


def _unique_chapters(*groups):
    out = []
    seen = set()
    for group in groups:
        for value in group or []:
            try:
                chapter_no = int(value)
            except (TypeError, ValueError):
                continue
            if chapter_no > 0 and chapter_no not in seen:
                seen.add(chapter_no)
                out.append(chapter_no)
    return out


def _accepted_chapters(task_id, platform):
    """Return chapters the platform has accepted, including pending review/schedule.

    A submission awaiting moderation or queued for a future release is already
    accepted by the platform. Treating it as unpublished after a restart makes
    the daily worker submit it again and can create duplicates. A complete,
    successful platform sync is the current-state authority: append-only local
    schedule receipts older than that sync may have since been cancelled or
    rejected. Preserve successful local receipts made after the snapshot so a
    just-submitted chapter is not retried before the next sync.
    """
    from .publish import ledger
    book = ledger.book_for(task_id, platform) or {}
    synced_at = str(book.get("remote_synced_at") or "")
    snapshot_fields = ("remote_published_nos", "remote_scheduled_nos",
                       "remote_review_nos")
    has_snapshot = bool(synced_at and all(key in book for key in snapshot_fields))
    if not has_snapshot:
        return (ledger.published_chapters(task_id, platform)
                | ledger.scheduled_chapters(task_id, platform)
                | ledger.review_chapters(task_id, platform))

    accepted = set()
    for key in snapshot_fields:
        try:
            accepted.update(int(n) for n in (book.get(key) or []) if int(n) > 0)
        except (TypeError, ValueError):
            # Malformed snapshot data is not a reliable representation of the
            # platform's current state. Fall back to the durable local receipts.
            return (ledger.published_chapters(task_id, platform)
                    | ledger.scheduled_chapters(task_id, platform)
                    | ledger.review_chapters(task_id, platform))

    try:
        chain_ids = {str(t) for t in store.serial_chain_ids(task_id)}
    except Exception:
        chain_ids = {str(task_id)}
    for record in ledger._iter_records(36500):
        if (record.get("platform") != platform
                or str(record.get("task_id") or "") not in chain_ids
                or not record.get("ok")
                or record.get("action") not in
                ("upload_chapter", "upload_chapter_scheduled")
                or str(record.get("ts") or "") <= synced_at):
            continue
        try:
            chapter_no = int(record.get("chapter_no") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_no > 0:
            accepted.add(chapter_no)
    return accepted


def _alert(message):
    from . import notify
    notify.push_text(message)


def _deadline_passed(now):
    return now.strftime("%H:%M") >= "23:59"


def _alert_due(now, publish_time):
    return now.strftime("%H:%M") >= "23:59"


def _publish_retry_delay(attempt):
    exponent = max(0, int(attempt or 1) - 1)
    return min(PUBLISH_RETRY_MAX_S,
               PUBLISH_RETRY_BASE_S * (2 ** min(exponent, 12)))


def _publish_retry_state(job):
    state = job.get("publish_retry")
    return state if isinstance(state, dict) else {"attempt": 0, "status": "ready"}


def _publish_retry_ready(job, now):
    state = _publish_retry_state(job)
    status = state.get("status") or "ready"
    if status in ("exhausted", "succeeded", "cancelled"):
        return False
    if status == "retry_wait":
        try:
            return float(now.timestamp()) >= float(state.get("next_retry_at") or 0)
        except (TypeError, ValueError):
            return True
    return True


def _publish_error_is_persistent(auto, error):
    # Keep daily publishing aligned with the shared unattended scheduler.
    # In particular, an ambiguous submit receipt must keep reconciling against
    # the platform after the normal retry budget; exhausting it here could
    # strand a chapter that the shared publish worker is still recovering.
    classifier = getattr(auto, "_scheduled_error_is_persistent", None)
    if callable(classifier) and classifier(error):
        return True
    if auto.retryable_publish_start(auto_submit=True, as_draft=False,
                                    error=error):
        return True
    # These conditions can clear without replacing the daily job: a saved
    # browser session may be restored, or a platform challenge may disappear
    # after its own normal expiration/verification. The upload guard still
    # blocks submission while the condition is present.
    text = str(error or "").lower()
    return any(mark in text for mark in (
        "未登录", "登录态", "重新登录", "安全验证", "安全校验", "人机验证",
        "验证码", "captcha", "verify you are human", "账号浏览器正在执行",
        "浏览器正在执行另一项发布操作", "正在回收", "发布批次进行中",
        "另一批自动发布", "另一项发布操作", "已有自动发布进行中",
        "请等本轮结束", "browser busy",
    ))


def _set_publish_retry(job_id, job, status, now, error="", *, attempted=False):
    prior = _publish_retry_state(job)
    try:
        attempt = max(0, int(prior.get("attempt") or 0))
    except (TypeError, ValueError):
        attempt = 0
    if attempted:
        attempt += 1
    state = {"attempt": attempt, "status": status}
    if status == "retry_wait":
        from .publish import auto
        persistent = _publish_error_is_persistent(auto, error)
        if attempt >= PUBLISH_RETRY_MAX_ATTEMPTS and not persistent:
            state["status"] = "exhausted"
        else:
            state["next_retry_at"] = now.timestamp() + _publish_retry_delay(attempt)
    _mutate(job_id, publish_retry=state, last_error=str(error or "")[:500])


def _mark_published(job_id, job):
    generated = len(_unique_chapters(job.get("new_chapters")))
    target = int(job.get("chapters_per_day") or 0)
    complete = generated >= target
    _mutate(job_id, phase="done",
            last_day_status="done" if complete else "incomplete",
            last_error="" if complete else "当日章节不足（%d/%d）" % (generated, target),
            publish_backlog=[],
            publish_retry={"attempt": 0, "status": "succeeded"})


def _tick(now=None):
    now = now or datetime.now()
    _ensure_loaded()
    today = now.strftime("%Y-%m-%d")
    with _TICK_LOCK:
        with _LOCK:
            snapshots = [_copy(job) for job in _JOBS.values()
                         if job.get("status") == "active"]
        for job in snapshots:
            try:
                _process_job(job, today, now)
            except Exception as exc:
                log.exception("serial daily tick failed job=%s", job.get("id"))
                _mutate(job["id"], last_error=str(exc)[:500])


def _process_job(job, today, now):
    job_id = job["id"]
    if job.get("day") != today:
        previous_day = job.get("day")
        backlog = list(job.get("publish_backlog") or [])
        if previous_day and job.get("phase") in (
                "generating", "retry_generation", "waiting_publish", "publishing"):
            previous_task_id = job.get("run_task_id") or job["task_id"]
            accepted = _accepted_chapters(previous_task_id, job["platform"])
            generated = list(job.get("new_chapters") or [])
            if job.get("phase") in ("generating", "retry_generation"):
                generated.extend(_chapter_numbers(previous_task_id)
                                 - set(job.get("baseline_chapters") or []))
                # A prior day's writer must not keep mutating the shared work
                # directory after today's baseline is captured.
                run = store.get_run(job.get("run_id") or "")
                if run and run.get("status") in ("queued", "running"):
                    try:
                        from . import jobs as job_runner
                        job_runner.cancel(job["run_id"])
                    except Exception:
                        log.exception("serial daily: prior-day generation cancel failed job=%s",
                                      job_id)
            overdue = [n for n in _unique_chapters(generated) if n not in accepted]
            backlog = _unique_chapters(backlog, overdue)
        if (previous_day and job.get("last_day_status") not in ("done", "paused")
                and job.get("alerted_day") != previous_day):
            _alert("连载日更未完成告警\n%s\n日期：%s\n原因：服务离线或当天目标未收尾" % (
                job["task_title"], previous_day))
        _mutate(job_id, day=today, phase="generating", run_id="", run_task_id="",
                baseline_chapters=[], new_chapters=[], alerted_day="",
                publish_backlog=backlog,
                publish_retry={"attempt": 0, "status": "ready"},
                last_day_status="running", last_error="")
        fresh = get(job_id)
        if fresh is None:
            return
        run_id, baseline, run_task_id = _start_generation(fresh, today)
        _mutate(job_id, run_id=run_id, run_task_id=run_task_id,
                baseline_chapters=baseline,
                phase="generating")
        return

    job = get(job_id)
    if job is None:
        return
    if job.get("phase") == "retry_generation":
        if not _deadline_passed(now):
            missing = max(1, job["chapters_per_day"] - len(job.get("new_chapters") or []))
            run_id, baseline, run_task_id = _start_generation(job, today, missing)
            _mutate(job_id, phase="generating", run_id=run_id,
                    run_task_id=run_task_id, baseline_chapters=baseline)
        return

    if job.get("phase") == "generating":
        run = store.get_run(job.get("run_id") or "")
        if not run:
            _mutate(job_id, phase="retry_generation",
                    last_error=job.get("last_error") or "写作运行未成功启动，正在重试")
        elif run.get("status") in ("done", "failed", "timeout", "cancelled"):
            observed = sorted(_chapter_numbers(job.get("run_task_id") or job["task_id"])
                              - set(job.get("baseline_chapters") or []))
            merged = list(dict.fromkeys((job.get("new_chapters") or []) + observed))
            complete = len(merged) >= job["chapters_per_day"]
            if complete:
                _mutate(job_id, new_chapters=merged[:job["chapters_per_day"]],
                        phase="waiting_publish", last_error="")
            elif not _deadline_passed(now):
                _mutate(job_id, new_chapters=merged, phase="retry_generation",
                        last_error=run.get("error") or "章节不足，正在当天重试")
            else:
                _mutate(job_id, new_chapters=merged, phase="waiting_publish",
                        last_error=run.get("error") or "章节不足，已无当天重试时间")

    job = get(job_id)
    if job is None:
        return
    if (job.get("phase") == "waiting_publish"
            and (job.get("new_chapters") or job.get("publish_backlog"))
            and now.strftime("%H:%M") >= job["publish_time"]):
        publish_task_id = job.get("run_task_id") or job["task_id"]
        published = _accepted_chapters(publish_task_id, job["platform"])
        candidates = _unique_chapters(job.get("publish_backlog"),
                                      job.get("new_chapters"))
        remaining = [n for n in candidates if n not in published]
        if not remaining:
            _mark_published(job_id, job)
        else:
            from .publish import auto
            active = (auto.status(publish_task_id).get("running") or {}).get("status")
            if active in ("running", "preparing"):
                retry = _publish_retry_state(job)
                if retry.get("status") not in ("in_flight",):
                    try:
                        attempt = max(0, int(retry.get("attempt") or 0))
                    except (TypeError, ValueError):
                        attempt = 0
                    if retry.get("status") in ("ready", "retry_wait"):
                        attempt += 1
                    retry = {"attempt": attempt, "status": "in_flight"}
                _mutate(job_id, phase="publishing", publish_retry=retry)
            elif not _publish_retry_ready(job, now):
                pass
            else:
                prior = _publish_retry_state(job)
                try:
                    attempt = max(0, int(prior.get("attempt") or 0)) + 1
                except (TypeError, ValueError):
                    attempt = 1
                _mutate(job_id, publish_retry={"attempt": attempt,
                                                "status": "in_flight"})
                try:
                    ok, err = _publish(job, remaining)
                except Exception as exc:
                    # A synchronous publisher startup failure must take the
                    # same durable retry path as an explicit (False, error)
                    # result. Otherwise the job remains in_flight and the
                    # next 25-second scheduler tick immediately starts it
                    # again without respecting backoff.
                    ok, err = False, "发布启动异常：%s" % str(exc)[:300]
                if ok:
                    _mutate(job_id, phase="publishing", last_error="")
                else:
                    failed = get(job_id)
                    if failed is None:
                        return
                    _set_publish_retry(job_id, failed, "retry_wait", now,
                                       err or "发布未启动")

    job = get(job_id)
    if job is None:
        return
    if job.get("phase") == "publishing":
        publish_task_id = job.get("run_task_id") or job["task_id"]
        published = _accepted_chapters(publish_task_id, job["platform"])
        candidates = _unique_chapters(job.get("publish_backlog"),
                                      job.get("new_chapters"))
        remaining = [n for n in candidates if n not in published]
        if not remaining:
            _mark_published(job_id, job)
        else:
            from .publish import auto
            running = auto.status(publish_task_id).get("running") or {}
            if running.get("status") not in ("running", "preparing"):
                _mutate(job_id, phase="waiting_publish",
                        last_error=running.get("error") or "发布尚未确认")
                failed = get(job_id)
                if failed is None:
                    return
                _set_publish_retry(job_id, failed, "retry_wait", now,
                                   running.get("error") or "发布尚未确认")

    job = get(job_id)
    if job is None:
        return
    if (_alert_due(now, job["publish_time"])
            and job.get("last_day_status") != "done"
            and job.get("alerted_day") != today):
        missing = max(0, job["chapters_per_day"] - len(job.get("new_chapters") or []))
        published = _accepted_chapters(job.get("run_task_id") or job["task_id"],
                                       job["platform"])
        candidates = _unique_chapters(job.get("publish_backlog"),
                                      job.get("new_chapters"))
        unpublished = [n for n in candidates if n not in published]
        reason = job.get("last_error") or "章节产出或平台发布未确认"
        _alert("连载日更未完成告警\n%s\n日期：%s\n未产出：%d 章\n待发布：%s\n原因：%s" % (
            job["task_title"], today, missing,
            ", ".join(map(str, unpublished)) or "无", reason))
        _mutate(job_id, alerted_day=today, last_day_status="alerted",
                last_error=reason)


def fire_due(now=None):
    _tick(now=now)
