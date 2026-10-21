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

import logging
import math
import os
import threading
import time

# Keep the publish ledger available at module scope for callers/tests that
# patch or inspect the reconciliation source directly.
from .. import atomic
from . import ledger

from .auto_batch import (  # noqa: F401 - 旧调用面再导出
    _automatic_retry_delay, _batch_sleep, _batch_slot_lock, _forget_automatic_batch,
    _queue_automatic_retry, _reclaim_previous, _release_cancel_event, _remember_automatic_batch,
    _retain_automatic_batch_for_retry, _start_worker_or_retry, cancel_pending,
    cancel_user_batch, other_batch_running,
    resume_interrupted_batches,
)
from .auto_chapters import (  # noqa: F401 - 旧调用面再导出
    _DOC_DIR_MARKS, _WALK_PRUNE, _append_selected_unlisted_file, _automatic_retry_status,
    _workdir_chapter_files, chapter_review_states, pending,
)
from .auto_prep import (  # noqa: F401 - 旧调用面再导出
    _book_ready, _isdir, _prepare_book, _wait_idle,
)
from .auto_schedule import (  # noqa: F401 - 旧调用面再导出
    _error_needs_fresh_verdict, _retry_delay, _scheduled_error_is_persistent,
    _scheduled_retry, _set_scheduled_retry,
    due_tasks, fire_due, norm_auto_publish, scheduled_retry_state,
)

from .auto_chapters import status as _chapter_status
status = _chapter_status


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
AUTH_RETRY_DELAY_S = 15
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
_LOGGER = logging.getLogger(__name__)

# A platform account owns one browser profile.  The per-chapter action lock in
# manager.py prevents two individual uploads from entering the browser at the
# same instant, but it is too short lived to protect a whole batch: another
# task can start between chapters and navigate the same page to its own book.
# Reserve the slot for the lifetime of an automatic batch so chapters from
# different tasks cannot interleave or verify against the wrong work.
_BATCH_SLOT_LOCKS = {}
_BATCH_SLOT_LOCKS_GUARD = threading.Lock()


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
                    recovered_jobs = read_state(temp_path)
                except (OSError, ValueError, TypeError) as exc:
                    raise RuntimeError(
                        "自动发布恢复队列损坏，主文件错误=%s；临时文件错误=%s" %
                        (primary_error, exc)) from exc
                # Keep the module cache empty until the recovery copy has been
                # published. If the disk is unavailable, a later call can retry
                # from the legacy candidate instead of trusting volatile state.
                try:
                    atomic.write_json(path, recovered_jobs, mode=0o600,
                                      indent=1)
                except OSError as exc:
                    raise RuntimeError(
                        "自动发布恢复队列写盘失败：%s" % type(exc).__name__) from exc
                _BATCH_JOBS = recovered_jobs
                try:
                    temp_path.unlink()
                except OSError as exc:
                    # The primary copy is already durable. Keep it usable and
                    # let a later startup retry cleanup without misclassifying
                    # the recovered queue as corrupt.
                    _LOGGER.warning(
                        "自动发布恢复队列已写入，旧临时文件清理失败（%s）",
                        type(exc).__name__)
            if _BATCH_JOBS is None:
                raise RuntimeError(
                    "自动发布恢复队列不可读取：%s" % primary_error)
    return _BATCH_JOBS


def _save_batch_jobs():
    path = _batch_job_path()
    atomic.write_json(path, _load_batch_jobs(), mode=0o600, indent=1)


def _cancelled(task_id):
    with _LOCK:
        event = _cancel_events.get(str(task_id))
        return bool(event and event.is_set())


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


# ---------------------------------------------------------------- 待发枚举
# 文档目录里的编号文件不是章节稿：章纲/大纲按章编号（第85章-xxx.md、
# vol-1-ch-16.md），全按章号算会把待发清单撑出一堆「假章」——批量选择
# 发布时一旦选中就会把章纲当正文填进编辑器（2026-10-08 实案：全书树 88
# 个章号，其中 34 个来自 章纲/已成稿/作废稿 目录）。


# ---------------------------------------------------------------- 顺序发布


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
                                   "本次未操作；系统已保留恢复记录，浏览器空闲后会自动续跑"
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
                upload_kwargs = {"auto_submit": auto_submit, "as_draft": as_draft,
                                 "_batch": True}
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
                        st["error"] = ("第 %d 章平台动作等待超时（%.0f 分钟）；"
                                       "已保留自动恢复记录，稍后会自动对账重试"
                                       % (item["chapter_no"], IDLE_TIMEOUT_S / 60))
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
# 质量门禁族错误的重试预算（按天）。评审结论不因重试而改变，到预算即转
# blocked_verdict 安睡到明天；其余可恢复错误保持原有的无限重试循环。
SCHEDULE_RETRY_VERDICT_MAX_ATTEMPTS = 4


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
    with _AP_LOCK:
        fired = _load_fired()
        fired[str(task_id)] = day
        try:
            atomic.write_json(_ap_file(), fired, mode=0o600, indent=1)
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
    with _AP_LOCK:
        try:
            path = _retry_file()
            atomic.write_json(path, _load_retry_state(), mode=0o600, indent=1)
        except Exception:
            pass


