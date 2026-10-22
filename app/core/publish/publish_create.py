# -*- coding: utf-8 -*-
"""publish/manager.py 的 create 子模块。

从 publish/manager.py 拆出（单文件行数治理）；模块级状态与辅助函数仍在
manager.py，全部经 _mgr() 解析以保持测试对 manager 属性的打桩语义；
旧调用面由 manager 再导出。
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Tuple, cast


def _mgr():
    from . import manager
    return manager


_ALT_COUNT = 3       # 撞名后自动换名最多试 3 个候选
_ALT_TIMEOUT = 90    # 候选书名一次轻调用的墙钟（发布线程内，不能久等）
_TITLE_MAX = 15      # 两平台书名统一截 15 字（bookmeta._norm 同口径）


def _mech_titles(base):
    """机械变体兜底（模型不可用时的最后手段）：最小改动、不重构词——
    同音字（了→啦）、去「我的」前缀、加「纪事」尾。丑但能建成书，
    烧在发布线程里的几万字稿费比书名门面贵得多。"""
    out, seen = [], {base}
    for v in (base.replace("了", "啦", 1),
              base[2:] if base.startswith("我的") else "",
              (base + "纪事") if len(base) <= 13 else ""):
        v = str(v or "").strip()[:_mgr()._TITLE_MAX]
        if len(v) >= 4 and v not in seen:
            out.append(v)
            seen.add(v)
    return out


def _alt_titles(plat, data, task, logs):
    """撞名候选队列：编排者基于原名+简介一次轻调用重出书名，失败静默
    降级机械变体——候选生成绝不能阻断建书主流程。"""
    base = str(data.get("book_name") or "").strip()
    if not base:
        return []
    out = []
    try:
        from .. import modelhub, planner, runner
        orch = modelhub.resolve_orchestrator()
        if orch:
            prov, model = orch
            prompt = (
                "你是网文编辑。原书名《%s》在平台重名被拒，请为同一本书重起"
                " %d 个新书名：每个 15 字内；保持原题材气质与钩子感；避开原名"
                "的核心词组合与「我的XX藏不住」式高频套路组合；书名里带一个本"
                "书独有的专名（人名/金手指/设定词）最不易再撞。只输出一个 "
                "```json 字符串数组，如 [\"书名1\",\"书名2\"]，不要输出其它内容。"
                "\n\n## 简介（截选）\n%s"
                % (base, _mgr()._ALT_COUNT + 2, str(data.get("summary") or "")[:300]))
            res = modelhub.chat(prov["id"], model, prompt,
                                max_tokens=600, timeout=_mgr()._ALT_TIMEOUT)
            planner._log_usage("publish", "create_book", task or {"id": ""}, res,
                               model=model,
                               provider=prov.get("name", prov.get("id", "")),
                               provider_id=prov.get("id", ""))
            if res["ok"]:
                arr = runner.extract_json(res.get("text") or "")
                if isinstance(arr, dict):
                    arr = arr.get("titles") or arr.get("candidates") or []
                if isinstance(arr, list):
                    for x in arr:
                        t = str(x or "").strip().replace("\n", " ")[:_mgr()._TITLE_MAX]
                        if len(t) >= 2 and t != base and t not in out:
                            out.append(t)
            if out:
                logs.append("候选书名（编排者 %s）：%s"
                            % (model, "、".join(out[:5])))
    except Exception as e:
        logs.append("候选书名生成降级（%s）" % str(e)[:100])
    for v in _mgr()._mech_titles(base):
        if v not in out:
            out.append(v)
    return out[:_mgr()._ALT_COUNT + 2]


def _sync_renamed_book(task_id, plat, task, data, new_name, logs):
    """建书撞名自动换名后的全链路同步。

    只换建书表单里的字、bookmeta/大纲/归档还留旧名的话：下轮发章按旧书名
    对账、用户点「重生成作品信息」又把旧名改回去——马甲案同款错位复发。
    这里把 bookmeta data、归档 Markdown、最新批次大纲的 book_title 统一到
    新名（台账 title 由调用方 save_book 落）。任一环节失败只记日志，
    不拦建书成功主流程。"""
    try:
        from pathlib import Path as _Path
        from .. import bookmeta as _bm
        from .. import store as _store
        data["book_name"] = new_name
        cur = _store.get_task(task_id)
        prev = ((cur or {}).get("book_meta") or {}).get(plat) or {}
        if isinstance(prev.get("data"), dict):
            payload = dict(prev)
            payload["data"] = dict(prev["data"], book_name=new_name)
            _store.set_book_meta(task_id, plat, payload)
            fp = _Path(task.get("workdir") or "") / _bm.PLATFORMS[plat]["file"]
            if str(fp.parent) and fp.parent.is_dir():
                fp.write_text(_bm.render_markdown(cur, plat, payload["data"]),
                              encoding="utf-8")
            logs.append("作品信息与归档已同步新书名《%s》" % new_name)
        # 大纲书名：续写链继承与「一键重生成」素材都读最新批次大纲，
        # 不同步会让下次重生成把书名改回旧名
        for r in _store.task_runs(task_id):
            o = r.get("outline")
            if isinstance(o, dict) and o.get("chapters") and o.get("book_title"):
                _store.update_run(r["id"], outline=dict(o, book_title=new_name))
                logs.append("大纲书名已同步为《%s》" % new_name)
                break
    except Exception as e:
        logs.append("书名同步部分失败（不拦建书）：%s" % str(e)[:120])


def _create_preflight(plat, data):
    """建书前置闸：平台表单对必填字段有硬校验（番茄简介 50-500 字），过不了
    校验时页面不跳转、流程只能误报「未登录或改版」——开浏览器之前先拦下，
    把人话原因还给用户。返回错误文案，空串=放行。"""
    placeholders = {"待补充", "（待补充）", "(待补充)"}
    title = str(data.get("book_name") or "").strip()
    if not title or title in placeholders:
        return "作品名称为空，请先补书名再点「创建作品」"
    summary = str(data.get("summary") or "").strip()
    if plat == "fanqie":
        if len(summary) < 50 or len(summary) > 500:
            return ("作品简介 %d 字，番茄建书表单要求 50-500 字；"
                    "请先补简介再点「创建作品」" % len(summary))
        if not str(data.get("category") or "").strip():
            return "作品分类为空，请先重生成作品信息再建书"
        for key in ("tags_theme", "tags_role", "tags_plot"):
            if not (data.get(key) or []):
                return "作品标签（%s）为空，请先重生成作品信息再建书" % key
    elif plat == "qimao":
        if not summary or summary in placeholders:
            return "作品简介为空，请先补简介再点「创建作品」"
        for key in ("category_main", "category_sub"):
            if not str(data.get(key) or "").strip():
                return "作品分类为空，请先重生成作品信息再建书"
        for key in ("tags_style", "tags_role", "tags_plot", "tags_bg"):
            if not (data.get(key) or []):
                return "作品标签（%s）为空，请先重生成作品信息再建书" % key
    return ""


def _queue_create_book_auth_recovery(task_id, plat, account, kwargs):
    """Keep a direct create-book request alive across a login challenge.

    The automatic batch path already persists its request for retry.  The
    standalone ``创建作品`` endpoint used to return an auth error immediately,
    forcing a second click after the user or platform restored the session.
    Queue one lightweight waiter per task/account so a valid persistent
    profile can resume the same request without duplicating the form submit.
    This waiter only observes authentication state; it never interacts with a
    credential or CAPTCHA control.
    """
    slot = _mgr().slot_of(plat, account)
    kwargs = dict(kwargs or {})
    current_epoch = int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0))
    expected_epoch = kwargs.get("_recovery_epoch")
    if (expected_epoch is not None and
            int(expected_epoch) != current_epoch):
        return False
    key = (str(task_id), str(slot))
    with _mgr()._CREATE_BOOK_RECOVERY_GUARD:
        if key in _mgr()._CREATE_BOOK_RECOVERY:
            # The original request is already waiting for the same profile;
            # treat a duplicate click as accepted instead of making the user
            # retry manually.
            return True
        _mgr()._CREATE_BOOK_RECOVERY.add(key)

    options = dict(kwargs or {})
    options.pop("_recovery_epoch", None)
    pending = {
        "task_id": str(task_id),
        "account": str(account or ""),
        "force": bool(options.get("force")),
        "force_confirmed": bool(options.get("force_confirmed")),
        "force_reason": str(options.get("force_reason") or ""),
    }
    _mgr()._remember_pending_create(slot, pending)

    def recover():
        def latest_create_error():
            """Return a failed receipt for this create attempt, if any."""
            state = _mgr()._st(slot)
            if state.get("error"):
                return str(state.get("error"))
            try:
                for receipt in _mgr().ledger.recent(task_id=task_id, platform=plat,
                                             limit=20):
                    if (receipt.get("action") == "create_book" and
                            receipt.get("ok") is False):
                        return str(receipt.get("error") or "建书失败")
            except Exception:
                pass
            return "建书失败"

        def retryable_create_error(error):
            text = str(error or "").lower()
            if _mgr()._authentication_required(plat, error):
                return True
            return any(mark in text for mark in (
                "浏览器", "连接", "超时", "timeout", "cdp", "devtools",
                "target closed", "connection reset", "connection refused",
                "安全验证", "安全校验", "人机验证", "验证码", "captcha",
                "verify you are human", "访问过于频繁", "操作过于频繁",
                "请求过于频繁", "建书前作品对账失败", "未取得 book_id",
                "平台作品列表按书名未找到", "流程走完但未取得",
                "操作正在进行中", "浏览器忙", "浏览器正在执行",
                "账号有操作正在进行中", "平台有操作正在进行中",
            ))

        try:
            deadline = time.monotonic() + _mgr().LOGIN_WAIT_S
            waiting_for_auth = False
            while time.monotonic() < deadline:
                if int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0)) != current_epoch:
                    return
                # ``disconnect`` removes this marker to cancel a live waiter.
                # Check it before reconnecting so an explicit sign-out cannot
                # be undone by the recovery thread already in flight.
                with _mgr().LOCK:
                    state_marker = (_mgr()._state.get("platforms") or {}).get(slot) or {}
                entries_marker = state_marker.get("pending_creates")
                legacy_marker = state_marker.get("pending_create")
                if (isinstance(entries_marker, dict) and
                        str(task_id) in entries_marker):
                    pass
                elif (isinstance(legacy_marker, dict) and
                      str(legacy_marker.get("task_id") or "") == str(task_id)):
                    pass
                else:
                    return
                state = _mgr()._st(slot)
                if state.get("status") == "connected":
                    waiting_for_auth = False
                    ok, error = _mgr().create_book_async(
                        task_id, plat, auto_submit=True, account=account,
                        _recovery=True, _recovery_epoch=current_epoch,
                        **options)
                    if not ok:
                        if not retryable_create_error(error):
                            _mgr()._forget_pending_create(slot, task_id)
                            return
                    else:
                        # create_book_async starts a worker and returns before
                        # the platform receipt exists. Keep pending_create
                        # until the ledger confirms a book ID, otherwise a
                        # later browser failure would strand this request.
                        while time.monotonic() < deadline:
                            try:
                                book = _mgr().ledger.book_for(task_id, plat)
                            except Exception:
                                book = None
                            if isinstance(book, dict) and str(
                                    book.get("book_id") or "").strip():
                                _mgr()._forget_pending_create(slot, task_id)
                                return
                            current = _mgr()._st(slot)
                            if current.get("status") == "waiting_login":
                                waiting_for_auth = True
                                break
                            if current.get("status") == "error":
                                error = latest_create_error()
                                break
                            time.sleep(1.0)
                        else:
                            # Leave the durable marker for the next watchdog
                            # tick; the worker may still be settling or the
                            # browser may have disappeared during the wait.
                            return
                        if waiting_for_auth:
                            # A watchdog probe may observe the challenge while
                            # the create worker is still settling. Keep this
                            # task durable and let the next pass resume it.
                            continue
                        if not waiting_for_auth and error and not retryable_create_error(error):
                            _mgr()._forget_pending_create(slot, task_id)
                            return
                    if not retryable_create_error(error):
                        _mgr()._forget_pending_create(slot, task_id)
                        return
                elif waiting_for_auth:
                    # The existing browser page is waiting for the user or
                    # platform challenge to finish. Reconnecting here can
                    # reopen the same login flow on every poll.
                    time.sleep(5.0)
                    continue
                else:
                    ok, error = _mgr().connect(plat, account=account, unattended=True)
                    if ok:
                        continue
                    # A challenge/login state is expected to remain open. Any
                    # other terminal browser/configuration error should be
                    # surfaced by the normal status state instead of looping.
                    current = _mgr()._st(slot)
                    if not (_mgr()._authentication_required(plat, error) or
                            current.get("status") == "waiting_login" or
                            retryable_create_error(error)):
                        _mgr()._forget_pending_create(slot, task_id)
                        return
                    waiting_for_auth = (
                        _mgr()._authentication_required(plat, error) or
                        current.get("status") == "waiting_login")
                time.sleep(5.0)
        finally:
            with _mgr()._CREATE_BOOK_RECOVERY_GUARD:
                _mgr()._CREATE_BOOK_RECOVERY.discard(key)

    try:
        threading.Thread(target=recover, daemon=True,
                         name="pub-create-recover-%s" % slot).start()
    except Exception:
        # Keep the durable request for the watchdog/startup pass, but do not
        # let a failed thread creation permanently suppress a later retry.
        with _mgr()._CREATE_BOOK_RECOVERY_GUARD:
            _mgr()._CREATE_BOOK_RECOVERY.discard(key)
        raise
    return True


def _remember_pending_create(slot, pending):
    """Persist one pending create without overwriting sibling tasks."""
    task_id = str((pending or {}).get("task_id") or "")
    if not task_id:
        return
    with _mgr().LOCK:
        ent = _mgr()._state["platforms"].setdefault(slot, {})
        entries = ent.get("pending_creates")
        if not isinstance(entries, dict):
            entries = {}
        entries[task_id] = dict(pending)
        ent["pending_creates"] = entries
        # Keep the legacy single-entry field as a compatibility projection for
        # older status readers; the keyed map is the durable source of truth.
        legacy = ent.get("pending_create")
        if (not isinstance(legacy, dict) or
                not legacy.get("task_id") or
                str(legacy.get("task_id")) == task_id):
            ent["pending_create"] = dict(pending)
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _mgr()._save()


def _forget_pending_create(slot, task_id):
    """Remove only one task's durable create marker."""
    task_id = str(task_id or "")
    with _mgr().LOCK:
        ent = _mgr()._state["platforms"].get(slot)
        if not isinstance(ent, dict):
            return
        entries = ent.get("pending_creates")
        if isinstance(entries, dict):
            entries.pop(task_id, None)
            if entries:
                ent["pending_creates"] = entries
                ent["pending_create"] = dict(next(iter(entries.values())))
            else:
                ent.pop("pending_creates", None)
                legacy = ent.get("pending_create")
                if (isinstance(legacy, dict) and
                        str(legacy.get("task_id") or "") == task_id):
                    ent["pending_create"] = {}
        else:
            legacy = ent.get("pending_create")
            if (isinstance(legacy, dict) and
                    str(legacy.get("task_id") or "") == task_id):
                ent["pending_create"] = {}
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _mgr()._save()


def _upload_retryable_error(plat, error):
    """Whether a failed one-off upload can safely wait for session recovery."""
    text = str(error or "").lower()
    if _mgr()._authentication_required(plat, error):
        return True
    return any(mark in text for mark in (
        "浏览器", "连接", "超时", "timeout", "cdp", "devtools",
        "target closed", "connection reset", "connection refused",
        "安全验证", "安全校验", "人机验证", "验证码", "captcha",
        "verify you are human", "访问过于频繁", "操作过于频繁",
        "请求过于频繁", "rate limit", "browser busy", "浏览器忙",
        "平台有操作正在进行中", "账号有操作正在进行中",
        "目标已关闭", "连接断开", "连接被关闭", "等响应超时",
        "平台作品列表按书名未找到", "未按作品名找到远端作品",
    ))


def _upload_pending_key(pending):
    """Build a stable key so sibling one-off uploads do not overwrite each other."""
    item = pending or {}
    task_id = str(item.get("task_id") or "")
    chapter_no = str(item.get("chapter_no") or "0")
    chapter_file = str(item.get("chapter_file") or "")
    as_draft = "draft" if item.get("as_draft") else "publish"
    scheduled = "scheduled" if item.get("scheduled") else "direct"
    return "%s|%s|%s|%s|%s" % (
        task_id, chapter_no, chapter_file, as_draft, scheduled)


def _remember_pending_upload(slot, pending):
    """Persist one direct chapter request before its worker starts."""
    key = _mgr()._upload_pending_key(pending)
    if not key or not str((pending or {}).get("task_id") or ""):
        return ""
    with _mgr().LOCK:
        ent = _mgr()._state["platforms"].setdefault(slot, {})
        entries = ent.get("pending_uploads")
        if not isinstance(entries, dict):
            entries = {}
        entries[key] = dict(pending)
        ent["pending_uploads"] = entries
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _mgr()._save()
    return key


def _forget_pending_upload(slot, key):
    """Remove only the completed direct chapter request."""
    key = str(key or "")
    if not key:
        return
    with _mgr().LOCK:
        ent = _mgr()._state["platforms"].get(slot)
        if not isinstance(ent, dict):
            return
        entries = ent.get("pending_uploads")
        if isinstance(entries, dict):
            entries.pop(key, None)
            if entries:
                ent["pending_uploads"] = entries
            else:
                ent.pop("pending_uploads", None)
            ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _mgr()._save()


def _upload_receipt_exists(pending):
    """Check local receipts before retrying an ambiguous browser failure."""
    task_id = str((pending or {}).get("task_id") or "")
    plat = str((pending or {}).get("platform") or "")
    chapter_no = int((pending or {}).get("chapter_no") or 0)
    if not task_id or not plat or chapter_no <= 0:
        return False
    readers = [_mgr().ledger.published_chapters, _mgr().ledger.scheduled_chapters,
               _mgr().ledger.review_chapters]
    if pending.get("as_draft"):
        readers.append(_mgr().ledger.drafted_chapters)
    for reader in readers:
        try:
            if chapter_no in reader(task_id, plat):
                return True
        except Exception:
            pass
    return False


def _reconcile_upload_before_retry(pending):
    """Return True/False when reconciliation is conclusive, None when unknown."""
    task_id = str((pending or {}).get("task_id") or "")
    plat = str((pending or {}).get("platform") or "")
    if not task_id or not plat:
        return False
    try:
        ok, error = _mgr().sync_published(task_id, plat, manual=False)
    except Exception as exc:
        ok, error = False, str(exc)
    if ok:
        if _mgr()._upload_receipt_exists(pending):
            return True
        # Chapter lists can be eventually consistent immediately after a
        # submit. Require two empty snapshots before deciding it is safe to
        # click submit again.
        time.sleep(1.0)
        try:
            ok_again, error_again = _mgr().sync_published(task_id, plat, manual=False)
        except Exception as exc:
            ok_again, error_again = False, str(exc)
        if ok_again:
            return not _mgr()._upload_receipt_exists(pending)
        error = error_again
    if _mgr()._upload_retryable_error(plat, error):
        return None
    return False


def _queue_upload_recovery(task_id, plat, chapter_file, account, options,
                           chapter_no, title, error: object = ""):
    """Durably retry a direct upload after login/browser recovery."""
    slot = _mgr().slot_of(plat, account)
    options = dict(options or {})
    current_epoch = int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0))
    expected_epoch = options.get("_recovery_epoch")
    if (expected_epoch is not None and
            int(expected_epoch) != current_epoch):
        return False
    pending = {
        "task_id": str(task_id),
        "platform": str(plat),
        "account": str(account or ""),
        "chapter_file": str(chapter_file),
        "chapter_no": int(chapter_no or 0),
        "title": str(title or ""),
        "auto_submit": True,
        "force": bool((options or {}).get("force")),
        "force_confirmed": bool((options or {}).get("force_confirmed")),
        "force_reason": str((options or {}).get("force_reason") or ""),
        "as_draft": bool((options or {}).get("as_draft")),
        "scheduled": bool((options or {}).get("scheduled")),
        "direct_today": bool((options or {}).get("direct_today")),
        "last_error": str(error or "")[:300],
        "recovery_epoch": current_epoch,
    }
    key = _mgr()._upload_pending_key(pending)
    guard_key = (str(slot), key)
    with _mgr()._UPLOAD_RECOVERY_GUARD:
        already_running = guard_key in _mgr()._UPLOAD_RECOVERY
        if not already_running:
            _mgr()._UPLOAD_RECOVERY.add(guard_key)
    _mgr()._remember_pending_upload(slot, pending)
    if already_running:
        return True

    def recover():
        try:
            deadline = time.monotonic() + _mgr().LOGIN_WAIT_S
            while time.monotonic() < deadline:
                if int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0)) != current_epoch:
                    return
                with _mgr().LOCK:
                    state_marker = (_mgr()._state.get("platforms") or {}).get(slot) or {}
                entries_marker = state_marker.get("pending_uploads")
                if (not isinstance(entries_marker, dict) or
                        key not in entries_marker):
                    return
                if _mgr()._upload_receipt_exists(pending):
                    _mgr()._forget_pending_upload(slot, key)
                    return
                reconciled = _mgr()._reconcile_upload_before_retry(pending)
                if reconciled is True:
                    _mgr()._forget_pending_upload(slot, key)
                    return
                if reconciled is None:
                    time.sleep(5.0)
                    continue
                if int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0)) != current_epoch:
                    return
                state = _mgr()._st(slot)
                if state.get("status") == "busy":
                    time.sleep(2.0)
                    continue
                ok, why = _mgr().upload_chapter_async(
                    task_id, plat, chapter_file, auto_submit=True,
                    force=pending["force"],
                    force_confirmed=pending["force_confirmed"],
                    force_reason=pending["force_reason"],
                    as_draft=pending["as_draft"],
                    scheduled=pending["scheduled"],
                    direct_today=pending["direct_today"],
                    _recovery=True,
                    _recovery_epoch=current_epoch)
                if not ok:
                    pending["last_error"] = str(why or "")[:300]
                    if not _mgr()._upload_retryable_error(plat, why):
                        _mgr()._forget_pending_upload(slot, key)
                        return
                    time.sleep(5.0)
                    continue
                # The worker is asynchronous. Wait for its receipt or for a
                # recoverable error before starting another attempt.
                for _ in range(60):
                    if _mgr()._upload_receipt_exists(pending):
                        _mgr()._forget_pending_upload(slot, key)
                        return
                    current = _mgr()._st(slot)
                    if current.get("status") == "busy":
                        time.sleep(1.0)
                        continue
                    if current.get("status") == "waiting_login":
                        break
                    if current.get("status") == "error":
                        latest = str(current.get("error") or "")
                        if not _mgr()._upload_retryable_error(plat, latest):
                            _mgr()._forget_pending_upload(slot, key)
                            return
                        pending["last_error"] = latest[:300]
                        break
                    time.sleep(1.0)
                time.sleep(3.0)
        finally:
            with _mgr()._UPLOAD_RECOVERY_GUARD:
                _mgr()._UPLOAD_RECOVERY.discard(guard_key)

    try:
        threading.Thread(target=recover, daemon=True,
                         name="pub-upload-recover-%s" % slot).start()
    except Exception:
        with _mgr()._UPLOAD_RECOVERY_GUARD:
            _mgr()._UPLOAD_RECOVERY.discard(guard_key)
        raise
    return True


def _resume_pending_upload_recoveries():
    """Recreate in-memory waiters for direct uploads after a restart."""
    _mgr()._load()
    queued = 0
    for slot, state in list((_mgr()._state.get("platforms") or {}).items()):
        if not isinstance(state, dict):
            continue
        entries = state.get("pending_uploads")
        if not isinstance(entries, dict):
            continue
        plat, sep, account = str(slot).partition("@")
        if plat not in _mgr().PLATFORMS:
            continue
        for pending in list(entries.values()):
            if not isinstance(pending, dict) or not pending.get("task_id"):
                continue
            pending_account = str(pending.get("account") or
                                  (account if sep else ""))
            try:
                queued += int(bool(_mgr()._queue_upload_recovery(
                    pending.get("task_id"), plat,
                    pending.get("chapter_file") or "", pending_account,
                    pending, pending.get("chapter_no") or 0,
                    pending.get("title") or "", pending.get("last_error") or "")))
            except Exception:
                pass
    return queued


def _resume_pending_create_recoveries():
    """Recreate in-memory waiters for durable standalone book requests."""
    _mgr()._load()
    queued = 0
    for slot, state in list((_mgr()._state.get("platforms") or {}).items()):
        if not isinstance(state, dict):
            continue
        pending_items = {}
        entries = state.get("pending_creates")
        if isinstance(entries, dict):
            for key, value in entries.items():
                if isinstance(value, dict) and value.get("task_id"):
                    pending_items[str(key)] = value
        pending = state.get("pending_create")
        if isinstance(pending, dict) and pending.get("task_id"):
            pending_items.setdefault(str(pending.get("task_id")), pending)
        elif isinstance(pending, list):
            for value in pending:
                if isinstance(value, dict) and value.get("task_id"):
                    pending_items.setdefault(str(value.get("task_id")), value)
        if not pending_items:
            continue
        plat, sep, account = str(slot).partition("@")
        if plat not in _mgr().PLATFORMS:
            continue
        for pending in pending_items.values():
            kwargs = {
                "force": bool(pending.get("force")),
                "force_confirmed": bool(pending.get("force_confirmed")),
                "force_reason": str(pending.get("force_reason") or ""),
            }
            pending_account = str(pending.get("account") or
                                  (account if sep else ""))
            try:
                queued += int(bool(_mgr()._queue_create_book_auth_recovery(
                    pending.get("task_id"), plat, pending_account, kwargs)))
            except Exception:
                # Keep the durable marker; the next watchdog/startup pass can
                # retry after a transient state or browser initialization error.
                pass
    return queued


def create_book_async(task_id, plat, auto_submit=True, force=False,
                      force_confirmed=False, force_reason="", account="",
                      _batch=False, _recovery=False, _recovery_epoch=None):
    """按任务 book_meta 的资料在平台建书。返回 (ok, err)。

    account 非空=用该账号的独立 profile/状态槽建书（多账号），建成的
    绑定记 account 字段，发章自动路由到同一账号。"""
    from .. import contracts, store
    if plat not in _mgr().PLATFORMS:
        return False, "未知平台"
    # Creating a platform work is part of the unattended publishing contract.
    # Keep the legacy argument for callers that still send it, but never let a
    # false value restore the old fill-and-wait-for-human-submit flow.
    auto_submit = True
    try:
        slot = _mgr().slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    acc = str(account or "").strip()
    request_epoch = (int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0))
                     if _recovery_epoch is None else int(_recovery_epoch))
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    if not contracts.publish_allowed(task_id, unattended=True):
        return False, "任务需要先完成审批，才能发布产物"
    # 连载链沿链继承（都是用第一个）：续写批次从自己身上建书也用首批生成的资料
    meta = store.inherited_book_meta(task, plat)
    if meta.get("status") != "done" or not meta.get("data"):
        return False, "请先生成该平台的作品信息"
    if _mgr()._st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    existing = _mgr().ledger.book_for(task_id, plat)
    # A title-only manual registration is usable as a binding. For legacy
    # entries without source metadata, only a recorded failed create-book
    # attempt is safe to retry; otherwise it may be a real manual binding.
    if existing:
        if existing.get("book_id") or existing.get("source") == "manual":
            return False, "该任务已在此平台登记过作品，请直接发章"
        failed_create = any(
            r.get("action") == "create_book" and not r.get("ok")
            for r in _mgr().ledger.recent(task_id=task_id, platform=plat, limit=50))
        if not failed_create:
            return False, "该任务已有作品登记但未确认，请登记已有作品或先在平台核对"
    why = _mgr()._create_preflight(plat, meta["data"])
    if why:
        return False, why
    release_flags = _mgr().automatic_release_kwargs(
        auto_submit, force=force, force_confirmed=force_confirmed,
        force_reason=force_reason)
    quality = _mgr()._quality_release_guard(task, plat, action="publish",
                                     **release_flags)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    # Reserve the account profile before the login recheck.  The persisted
    # status check above is only advisory; this lock closes the check-then-set
    # race when two works share one account.
    action_lock = _mgr()._try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if (_recovery and
            request_epoch != int(_mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0))):
        action_lock.release()
        return False, "平台恢复任务已被主动断开取消"
    if _mgr()._st(slot).get("status") == "busy":
        action_lock.release()
        return False, "该平台有操作正在进行中"
    ok_login, why = _mgr()._login_guard(slot)
    if not ok_login:
        if not _batch and _mgr()._upload_retryable_error(plat, why):
            try:
                queued = _mgr()._queue_create_book_auth_recovery(
                    task_id, plat, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason})
            except Exception as exc:
                action_lock.release()
                return False, "登录态恢复任务启动失败：%s" % str(exc)[:160]
            if queued:
                action_lock.release()
                return True, ""
        action_lock.release()
        return False, why
    _mgr()._set(slot, status="busy", last_action="create_book", error="")

    data = meta["data"]
    mod = _mgr().PLATFORMS[plat]
    logs = []
    book_name = (data.get("book_name") or "").strip()
    try:
        operation_id = _mgr().operations.begin(
            "publish:%s:create_book" % plat,
            {"task_id": task_id, "title": book_name}, task_id=task_id,
            metadata={"platform": plat, "action": "create_book"})
    except Exception as exc:
        _mgr()._set(slot, status="error", error="建书操作登记失败：%s" % exc)
        action_lock.release()
        return False, "建书操作登记失败：%s" % exc

    def run():
        started_at = time.monotonic()

        def elapsed_s():
            return round(max(0.0, time.monotonic() - started_at), 3)

        final_name = book_name
        # 短故事（short_story 任务）走独立建书流：频道/表单/编辑器与小说不同
        # （qimao.CREATE_SHORT_STORY，2026-10-22 实机校准），但对账/取值/登记
        # 同一套机制；operation/ledger 动作名保持 create_book（本质都是建书）。
        is_short_story = str((task or {}).get("type") or "") == "short_story"
        try:
            b, page = _mgr()._open_page(slot)
            # 书名全平台唯一（2026-09-30 马甲案）：撞名是建书常见死法，撞了
            # 原样报「疑似未建成」会白烧几万字稿费。试名队列=原名+自动候选
            # （编排者重出→机械变体兜底）；同名书恰在本账号下时对账复用不换
            # 名。自动提交模式会自动换名并对账，避免把撞名问题交给人工。
            names = [book_name]
            cands_ready = False
            # A previous process may have submitted this exact work and died
            # before save_book wrote its receipt. Reconcile the same account's
            # work list before opening a new-book form so a retry adopts the
            # platform-created work instead of creating a duplicate.
            reconcile = getattr(mod, "reconcile_short_story_book_id"
                                if is_short_story else "reconcile_book_id", None)
            if not callable(reconcile):
                raise RuntimeError("平台不支持建书前可靠对账，已停止以避免重复建书")
            reconcile = cast(Callable[..., Tuple[Any, Any, Any]], reconcile)
            found, book_id, detail = reconcile(page, {"title": book_name})
            if not found and detail:
                raise RuntimeError("建书前作品对账失败：%s" % detail)
            if book_id:
                logs.append("建书前按作品名找到已有作品，自动复用 book_id=%s" %
                            book_id)
            i = 0
            while (not book_id and i < len(names) and
                   i < 1 + _mgr()._ALT_COUNT + 2):
                name = names[i]
                i += 1
                final_name = name
                if i > 1:
                    logs.append("第 %d 次尝试，书名《%s》" % (i, name))
                values = (mod.values_create_short_story if is_short_story
                          else mod.values_create_book)(dict(data, book_name=name))
                steps = _mgr()._with_tag_steps(
                    _mgr().load_flow(plat,
                                     "create_short_story" if is_short_story
                                     else "create_book"),
                    mod.tag_groups(data), values, mod)
                try:
                    _mgr().flow.run_flow(page, steps, values=values, config=mod.CONFIG,
                                  auto_submit=auto_submit, dup_check=auto_submit,
                                  shot=lambda n: page.screenshot(
                                      _mgr().ledger.shot_path(plat, task_id, n)),
                                  log=logs.append)
                except _mgr().flow.TitleDupError as e:
                    # 事前预检命中：平台实时校验已喊重名，点提交必被拒
                    logs.append(str(e))
                    bid = _mgr()._resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id, final_name = bid, name
                        logs.append("同名书就在本账号下，已复用 book_id=%s" % bid)
                        break
                    if not cands_ready:
                        names = names + _mgr()._alt_titles(plat, data, task, logs)
                        cands_ready = True
                    if i >= len(names):
                        break               # 候选没准备出来（模型+机械全空）
                    continue
                # book_id：创建成功后平台跳书籍详情/编辑器，从 URL 提取
                # （番茄 book-info/<id>；七猫 information?id=<id>、短故事
                # book-upload?id=<id> 直跳编辑器）。url_any 过了但页面还在
                # 跳转链上时再等几轮；提取落空把最终 URL 记进日志，别再静默
                # 登记空 id（发章只能兜底找书，直达编辑器就废了）。
                book_id_re = getattr(mod, "BOOK_ID_URL_RE",
                                     r"book-info/(\d+)|information\?id=(\d+)")
                try:
                    for _try in range(6):
                        m_url = re.search(book_id_re, str(page.url() or ""))
                        if m_url:
                            book_id = next((g for g in m_url.groups() if g), "")
                            break
                        time.sleep(0.8)
                    if not book_id:
                        logs.append("book_id 提取落空，最终页面：%s"
                                    % str(page.url() or "")[:120])
                except Exception:
                    pass
                if book_id:
                    break
                # 自动提交被平台静默拒绝（不跳转）时，toast 常喊重名——抓
                # 回来判定，别落进「疑似未建成」的糊涂账
                hint = ""
                for _try in range(3):
                    hint = _mgr().flow.dup_hint(page)
                    if hint:
                        break
                    time.sleep(1.0)
                if hint:
                    logs.append("页面提示：%s" % hint[:120])
                    bid = _mgr()._resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id, final_name = bid, name
                        logs.append("同名书就在本账号下，已复用 book_id=%s" % bid)
                        break
                    if not cands_ready:
                        names = names + _mgr()._alt_titles(plat, data, task, logs)
                        cands_ready = True
                    continue
                # 非撞名失败：按书名对账兜底（原有逻辑），找不回就明说未建成
                bid = _mgr()._resolve_book_id(plat, page, {"title": name})
                if bid:
                    book_id = bid
                    logs.append("book_id 提取落空，已按书名在平台对账找回 %s" % bid)
                break
            if book_id and final_name != book_name:
                # 换名成功（或复用同名旧书）：书名全链路同步到新名
                _mgr()._sync_renamed_book(task_id, plat, task, data, final_name, logs)
            operation_status = "confirmed" if book_id else "unknown"
            if book_id:
                _mgr().operations.confirm(operation_id, remote_receipt=book_id,
                                   metadata={"platform": plat, "action": "create_book"})
            else:
                _mgr().operations.mark_unknown(
                    operation_id,
                    "建书流程走完，但平台作品列表按书名未找到《%s》，书很可能未建成；"
                    "请到平台作品管理确认，若已存在可人工登记" % final_name,
                    metadata={"platform": plat, "action": "create_book"})
            _mgr().ledger.record(plat, "create_book", task_id=task_id, title=final_name,
                          book_id=book_id, ok=bool(book_id),
                          error=((("" if book_id else
                                   "建书流程走完但未取得 book_id，且平台按书名未找到"
                                   "《%s》，疑似未建成\n" % final_name)
                                  + "\n".join(logs))[:2000]),
                          shot=str(_mgr().ledger.shot_path(plat, task_id, "")),
                          operation_id=operation_id, operation_status=operation_status,
                          remote_receipt=book_id, duration_s=elapsed_s())
            if book_id:
                # 未取得 id 不落台账：空条目会让界面亮「已建书」、校准盲跑，
                # 而发章/下次建书各自有按书名对账的兜底，空条目只剩害处。
                _mgr().ledger.save_book(task_id, plat, {"book_id": book_id, "title": final_name,
                                                 "source": "create"},
                                  account=acc)
            _mgr()._set(slot, status="connected", error="")
        except Exception as e:
            _mgr().operations.finish_exception(operation_id, e)
            operation_status = (_mgr().operations.get(operation_id) or {}).get("status") or "failed"
            _mgr()._set(slot, status="error", error="建书失败：%s" % e)
            _mgr().ledger.record(plat, "create_book", task_id=task_id, title=book_name,
                          ok=False, error=str(e)[:300],
                          shot=str(_mgr().ledger.shot_path(plat, task_id, "")),
                          operation_id=operation_id, operation_status=operation_status,
                          duration_s=elapsed_s())
            if not _batch and _mgr()._upload_retryable_error(plat, e):
                try:
                    _mgr()._queue_create_book_auth_recovery(
                        task_id, plat, acc,
                        {"force": force,
                         "force_confirmed": force_confirmed,
                         "force_reason": force_reason})
                except Exception:
                    pass
        finally:
            _mgr()._save()
            action_lock.release()

    try:
        threading.Thread(target=run, daemon=True,
                         name="pub-book-%s" % plat).start()
    except Exception as exc:
        error = "建书任务启动失败，可自动重试：%s" % exc
        action_lock.release()
        _mgr()._set(slot, status="connected", error=error)
        if not _batch and _mgr()._upload_retryable_error(plat, exc):
            try:
                _mgr()._queue_create_book_auth_recovery(
                    task_id, plat, acc,
                    {"force": force,
                     "force_confirmed": force_confirmed,
                     "force_reason": force_reason})
            except Exception:
                pass
        return False, error
    return True, ""
