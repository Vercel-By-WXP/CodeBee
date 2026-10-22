# -*- coding: utf-8 -*-
"""自动发布前的书籍资料准备与空闲等待。

从 publish.auto 拆出（单文件行数治理），原模块再导出保持旧调用面；
跨模块引用经 _auto() 解析，保持运行时状态与测试打桩语义。
"""
from __future__ import annotations


def _auto():
    from . import auto
    return auto


def _isdir(p):
    try:
        return _auto().os.path.isdir(p)
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
    existing = _auto().ledger.book_for(task_id, platform)
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
    # 无人值守闸：作品找回/建书都要开平台页，浏览器没跑就挂起（绝不拉窗）。
    ok_gate, why_gate = manager._unattended_attach_gate(slot)
    if not ok_gate:
        return False, why_gate

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
                if _auto()._cancelled(task_id):
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
                            _auto().ledger.save_book(task_id, platform, existing)
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
                if attempt >= _auto().BOOK_PREP_RETRY_ATTEMPTS:
                    return False, (
                        "自动找回平台作品 ID 重试次数已达上限：%s" % failure)
                delay = min(_auto().PUBLISH_START_RETRY_MAX_S,
                            _auto().PUBLISH_START_RETRY_BASE_S
                            * (2 ** min(attempt - 1, 12)))
                status = _auto()._running.get(str(task_id))
                if isinstance(status, dict):
                    status["message"] = (
                        "正在自动核对已登记作品《%s》，%d 秒后重试（第 %d 次）；"
                        "为避免重复建书，核对成功前不会创建新作品" %
                        (title[:40], delay, attempt))
                if _auto()._needs_login_recovery(failure):
                    try:
                        manager.connect(platform, account=acc, unattended=True)
                    except Exception:
                        pass
                if not _auto()._batch_sleep(task_id, delay):
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
                    "at": _auto().time.strftime("%Y-%m-%d %H:%M:%S")})
                bookmeta.generate_async(root_id, platform,
                                        author_agent=bookmeta._resolve_author(root))
            deadline = _auto().time.time() + _auto().BOOK_PREP_TIMEOUT_S
            failure = ""
            while _auto().time.time() < deadline:
                if _auto()._cancelled(task_id):
                    return False, "自动准备作品已取消"
                task = store.get_task(task_id) or task
                meta = store.inherited_book_meta(task, platform)
                if meta.get("status") == "done" and meta.get("data"):
                    failure = ""
                    break
                if meta.get("status") == "failed":
                    failure = str(meta.get("error") or "未知错误")
                    break
                _auto().time.sleep(1.0)
            else:
                # The generation worker may still be running after the wait
                # window. Do not launch a duplicate generation on an ambiguous
                # timeout; the durable publication batch will reconcile on its
                # next restart/retry.
                return False, "自动生成%s作品信息超时" % platform
            if meta.get("status") == "done" and meta.get("data"):
                break
            if not _auto()._scheduled_error_is_persistent(failure):
                return False, "自动生成%s作品信息失败：%s" % (
                    manager.PLATFORMS[platform].CONFIG.get("label", platform),
                    failure)
            attempt += 1
            if attempt >= _auto().BOOK_PREP_RETRY_ATTEMPTS:
                return False, "自动生成%s作品信息重试次数已达上限：%s" % (
                    platform, failure)
            delay = min(_auto().PUBLISH_START_RETRY_MAX_S,
                        _auto().PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
            status = _auto()._running.get(str(task_id))
            if isinstance(status, dict):
                status["message"] = (
                    "作品资料生成遇到可恢复错误，%d 秒后自动重试（第 %d 次）" %
                    (delay, attempt))
            if not _auto()._batch_sleep(task_id, delay):
                return False, "自动生成作品资料重试等待期间任务已取消"

    attempt = 0
    while True:
        if _auto()._cancelled(task_id):
            return False, "自动准备作品已取消"
        ok, err = manager.create_book_async(task_id, platform,
                                            auto_submit=True, account=acc,
                                            _batch=True)
        if not ok:
            failure = str(err or "自动创建作品失败")
        else:
            deadline = _auto().time.time() + _auto().BOOK_PREP_TIMEOUT_S
            failure = ""
            while _auto().time.time() < deadline:
                if _auto()._cancelled(task_id):
                    return False, "自动准备作品已取消"
                found = _auto().ledger.book_for(task_id, platform)
                if _auto()._book_ready(found):
                    return True, ""
                state = manager._st(slot)
                if state.get("status") == "error":
                    failure = str(state.get("error") or "自动创建作品失败")
                    break
                _auto().time.sleep(1.0)
            else:
                # A timeout does not prove that the platform rejected the
                # create action. Do not start another create while its result
                # could still be pending.
                return False, "自动创建%s作品超时" % platform

        if not _auto()._scheduled_error_is_persistent(failure):
            return False, failure

        attempt += 1
        if attempt >= _auto().BOOK_PREP_RETRY_ATTEMPTS:
            return False, "自动建书重试次数已达上限：%s" % failure
        delay = min(_auto().PUBLISH_START_RETRY_MAX_S,
                    _auto().PUBLISH_START_RETRY_BASE_S * (2 ** min(attempt - 1, 12)))
        status = _auto()._running.get(str(task_id))
        if isinstance(status, dict):
            status["message"] = (
                "自动建书遇到可恢复的平台连接/会话状态，%d 秒后重试（第 %d 次）；"
                "安全验证仍由平台控制" % (delay, attempt))

        # A failed preflight or a worker that ended on a transient browser
        # error may leave the saved profile disconnected. Reopen that profile;
        # this never solves a login challenge or platform security check.
        if _auto()._needs_login_recovery(failure):
            try:
                connected, connect_error = manager.connect(
                    platform, account=acc, unattended=True)
            except Exception as exc:
                connected, connect_error = False, str(exc)
            if connected:
                deadline = _auto().time.time() + _auto().LOGIN_RECOVERY_TIMEOUT_S
                while _auto().time.time() < deadline:
                    if _auto()._cancelled(task_id):
                        return False, "自动准备作品已取消"
                    state = manager._st(slot)
                    if state.get("status") == "connected":
                        break
                    if state.get("status") == "error":
                        connect_error = state.get("error") or connect_error
                        break
                    _auto().time.sleep(1.0)

        if not _auto()._batch_sleep(task_id, delay):
            return False, "自动建书重试等待期间任务已取消"


def _wait_idle(platform, account="", slot=None, timeout=None):
    """Wait for the manager's platform action to finish (busy → other state)."""
    from . import manager
    if timeout is None:
        timeout = _auto().IDLE_TIMEOUT_S
    if slot is None:
        try:
            slot = manager.slot_of(platform, account)
        except (TypeError, ValueError):
            slot = platform
    deadline = _auto().time.monotonic() + max(0.0, float(timeout))
    while _auto().time.monotonic() < deadline:
        try:
            st = manager._st(slot)
            if st.get("status") != "busy":
                return True
        except Exception:
            return False               # manager 异常：别傻等
        _auto().time.sleep(_auto().IDLE_POLL_S)
    return False