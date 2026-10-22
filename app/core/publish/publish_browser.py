# -*- coding: utf-8 -*-
"""publish/manager.py 的 browser 子模块。

从 publish/manager.py 拆出（单文件行数治理）；模块级状态与辅助函数仍在
manager.py，全部经 _mgr() 解析以保持测试对 manager 属性的打桩语义；
旧调用面由 manager 再导出。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional, Tuple, cast
from urllib.parse import urlsplit

from .browser import Browser, BrowserError, Page


def _mgr():
    from . import manager
    return manager


def _ensure_browser(plat: str) -> Browser:
    """attach-or-launch：进程内缓存 → 记住的端口 → 全新 launch。"""
    with _mgr().LOCK:
        b = _mgr()._browsers.get(plat)
        if b and b.alive():
            return b
        port = _mgr()._st(plat).get("port") or 0
        if port:
            try:
                b = _mgr().Browser.attach(int(port))
                _mgr()._browsers[plat] = b
                return b
            except BrowserError:
                pass                        # 旧实例已死：走 launch
        b = _mgr().Browser(_mgr()._profile_dir(plat))     # 有窗口：用户要扫码/人工确认
        _mgr()._browsers[plat] = b
        _mgr()._set(plat, port=b.port)
        return b


def _open_page(plat: str, preserve_tabs: bool = False) -> Tuple[Browser, Page]:
    b = _mgr()._ensure_browser(plat)
    if preserve_tabs:
        # A platform challenge may live in a tab other than the first one.
        # Reuse the persisted target when it is still alive; falling back to
        # the first page keeps compatibility with old state files and mocks.
        target_id = str(_mgr()._st(plat).get("auth_target_id") or "").strip()
        page_for_target = getattr(b, "page_for_target", None)
        if target_id and callable(page_for_target):
            try:
                page = page_for_target(target_id)
                page = cast(Optional[Page], page)
                if page is not None:
                    _mgr()._seed_auth_state(_mgr().base_plat(plat), plat, page)
                    return b, page
            except Exception:
                pass
    # 单标签纪律：平台点击（如「上传章节」）会开新 tab，流程引擎的 page
    # 对象可能留在旧 tab 上填错页面（0 字草稿案的乱源）。每次动作前收拢
    # 到一个 tab——发布是串行作业，多 tab 只会串台。
    if not preserve_tabs:
        try:
            browser_pages = b.pages()
            retained_target = str((browser_pages[0] if browser_pages else {}).get(
                "id") or "")
            for t in browser_pages[1:]:
                try:
                    from .browser import _http_json
                    _http_json("http://127.0.0.1:%d/json/close/%s" % (b.port, t["id"]))
                except Exception:
                    pass
            # Any persisted auth init script attached to a tab we just closed
            # can no longer be removed through CDP. Drop its bookkeeping so a
            # later seed cannot mistake that stale target for the live page.
            if (_mgr()._st(plat).get("auth_script_target_id") and
                    _mgr()._st(plat).get("auth_script_target_id") != retained_target):
                with _mgr()._AUTH_STATE_CACHE_LOCK:
                    _mgr()._AUTH_STATE_CACHE.pop(plat, None)
                _mgr()._set(plat, auth_script_id="", auth_script_target_id="",
                     auth_storage_keys={})
        except Exception:
            pass
    page = b.first_page(create=True)
    if page is None:
        raise BrowserError("浏览器没有可用页面")
    _mgr()._seed_auth_state(_mgr().base_plat(plat), plat, page)
    return b, page


def _page_is_empty(page):
    """Whether a recovered tab has no page worth preserving.

    A blank tab can be safely initialized after a service restart. Login pages
    and challenge pages must stay where they are so a human or platform flow
    can finish without being interrupted by another navigation.
    """
    try:
        url = str(page.url() or "").strip().lower()
    except Exception:
        return True
    return not url or url.startswith("about:blank") or url.startswith("chrome-error://")


def _remember_auth_page(plat, page):
    """Persist the tab that currently contains a login/challenge page."""
    target_id = str(getattr(page, "_target_id", "") or "").strip()
    if target_id:
        _mgr()._set(plat, auth_target_id=target_id)


def _clear_auth_page(plat):
    """Forget a challenge tab after the saved session becomes valid."""
    if _mgr()._st(plat).get("auth_target_id"):
        _mgr()._set(plat, auth_target_id="")


def _live_pages(browser, primary):
    """Return the current browser tabs, keeping *primary* as a fallback.

    A login provider may open a separate tab for a challenge.  The normal
    publish path intentionally collapses tabs, but authentication recovery
    must inspect every live page before it decides that the session is valid.
    """
    pages = [primary] if primary is not None else []
    seen = {str(getattr(primary, "_target_id", "") or "")} if primary else set()
    try:
        targets = browser.pages()
    except Exception:
        return pages
    if not isinstance(targets, (list, tuple)):
        return pages
    for target in targets or []:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id") or "").strip()
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url or target_id in seen:
            continue
        try:
            pages.append(_mgr().Page(ws_url))
            seen.add(target_id)
        except Exception:
            continue
    return pages


def _probe_login_pages(plat, browser, primary):
    """Passively inspect every tab and prioritize any auth challenge.

    Returns ``(ok, detail, page)``.  A challenge takes precedence over an
    otherwise valid author-console tab so an untouched second tab can never
    be mistaken for a completed login.
    """
    # Release wrappers created by the previous pass before opening new CDP
    # sockets.  This closes transports only; the underlying tabs remain open
    # for the user/platform challenge.
    with _mgr()._AUTH_PROBE_CONNECTIONS_LOCK:
        previous = _mgr()._AUTH_PROBE_CONNECTIONS.pop(str(plat), [])
    for old_page in previous:
        closer = getattr(old_page, "close_connection", None)
        if callable(closer):
            closer()
        else:
            try:
                old_page.ws.close()
            except Exception:
                pass
    pages = _mgr()._live_pages(browser, primary)
    temporary = [candidate for candidate in pages if candidate is not primary]
    with _mgr()._AUTH_PROBE_CONNECTIONS_LOCK:
        _mgr()._AUTH_PROBE_CONNECTIONS[str(plat)] = temporary
    successes = []
    last_detail = ""
    for page in pages:
        try:
            ok, detail = _mgr()._check_login(plat, page, navigate=False)
        except Exception as exc:
            ok, detail = False, str(exc)
        last_detail = detail or last_detail
        if ok:
            successes.append((page, detail))
        elif _mgr()._authentication_required(plat, detail):
            return False, detail, page
    if successes:
        page, detail = successes[0]
        return True, detail, page
    return False, last_detail, primary


def _check_login(plat, page, navigate=True):
    """打开后台首页看 URL 是否被踢到登录页。返回 (ok, 当前url)。

    plat 可以是槽名（fanqie@a2）：状态写槽、平台配置取基础平台。
    导航失败页（chrome-error://）不含登录标记，曾把「域名打不开」误判成
    已登录——假 connected 的来源，先排除。navigate=False 只读当前页面，
    用于等待验证期间的被动检查。"""
    mod = _mgr().PLATFORMS[_mgr().base_plat(plat)]
    try:
        if navigate:
            page.navigate(mod.CONFIG["home"], timeout=30)
        url = str(page.url() or "")
    except BrowserError as e:
        return False, str(e)
    # CAPTCHA providers sometimes host the challenge on a third-party origin.
    # Inspect visible text before the platform-host check so that tab is
    # retained and the batch waits instead of accepting the author tab beside it.
    try:
        body_text = str(page.call(
            "()=>document.body?(document.body.innerText||'').slice(0,5000):''",
            timeout=8) or "").lower()
    except BrowserError as e:
        return False, "无法确认平台登录页面：%s" % str(e)[:120]
    # A normal author page may mention a single word such as “验证码” in a
    # help link or form description.  Treat body text as a challenge only when
    # the tab is cross-origin, the URL identifies an auth/challenge route, or
    # multiple independent challenge markers agree.  This keeps passive
    # probes from logging out a healthy platform page while retaining the
    # cross-origin CAPTCHA-provider path.
    try:
        parsed_for_body = urlsplit(url)
        home_host_for_body = urlsplit(mod.CONFIG["home"]).hostname or ""
        labels_for_body = home_host_for_body.lower().rstrip(".").split(".")
        root_for_body = (".".join(labels_for_body[-2:])
                         if len(labels_for_body) >= 2 else home_host_for_body)
        host_for_body = (parsed_for_body.hostname or "").lower().rstrip(".")
        same_platform_for_body = bool(
            host_for_body and (host_for_body == root_for_body or
                               host_for_body.endswith("." + root_for_body)))
        route_for_body = ((parsed_for_body.path or "") + "?" +
                          (parsed_for_body.query or "")).lower()
    except (TypeError, ValueError):
        same_platform_for_body = False
        route_for_body = ""
    body_challenges = [mark for mark in _mgr()._LOGIN_CHALLENGE_MARKS
                       if mark.lower() in body_text]
    route_hint_for_body = any(mark.lower() in route_for_body
                              for mark in _mgr()._LOGIN_CHALLENGE_URL_MARKS)
    strong_overlay_hint = any(phrase in body_text for phrase in (
        "请完成安全验证", "请完成人机验证", "拖动滑块",
        "verify you are human"))
    challenge = body_challenges[0] if body_challenges else ""
    if challenge and (not same_platform_for_body or route_hint_for_body
                      or len(body_challenges) >= 2 or strong_overlay_hint):
        _mgr()._remember_auth_page(plat, page)
        return False, "平台要求安全验证：%s" % challenge
    # Some providers render the challenge inside a cross-origin iframe and
    # leave the top-level body empty. Treat a challenge-looking URL as an
    # auth gate only when it is outside the platform's own origin; ordinary
    # author-console URLs containing ``verify`` remain subject to host checks.
    try:
        parsed = urlsplit(url)
        home_host = urlsplit(mod.CONFIG["home"]).hostname or ""
        labels = home_host.lower().rstrip(".").split(".")
        platform_root = ".".join(labels[-2:]) if len(labels) >= 2 else home_host
        current_host = (parsed.hostname or "").lower().rstrip(".")
        same_platform = (current_host == platform_root or
                         current_host.endswith("." + platform_root))
        challenge_url = ((not same_platform) and
                         any(mark in (current_host + parsed.path).lower()
                             for mark in _mgr()._LOGIN_CHALLENGE_URL_MARKS))
    except ValueError:
        challenge_url = False
    if challenge_url:
        _mgr()._remember_auth_page(plat, page)
        return False, "平台要求安全验证：挑战页 URL"
    if url.startswith("chrome-error://") or url.startswith("about:"):
        return False, url                      # 页面没打开：网络/域名问题，不是登录态
    if any(m in url for m in mod.CONFIG["login_url_marks"]):
        _mgr()._remember_auth_page(plat, page)
        return False, url
    # Passive checks must never accept an unrelated tab or a redirect outside
    # the author site merely because its URL lacks a login marker.
    try:
        parsed = urlsplit(url)
        home_host = urlsplit(mod.CONFIG["home"]).hostname or ""
        labels = home_host.lower().rstrip(".").split(".")
        platform_root = ".".join(labels[-2:]) if len(labels) >= 2 else home_host
        current_host = (parsed.hostname or "").lower().rstrip(".")
        same_platform = (current_host == platform_root or
                         current_host.endswith("." + platform_root))
        if (parsed.scheme != "https" or not same_platform or
                parsed.port not in (None, 443) or parsed.username or parsed.password):
            return False, url
    except ValueError:
        return False, url
    _mgr()._clear_auth_page(plat)
    return True, url


def _authentication_required(plat, detail):
    text = str(detail or "").lower()
    return any(mark.lower() in text for mark in (
        *_mgr().PLATFORMS[_mgr().base_plat(plat)].CONFIG["login_url_marks"],
        *_mgr()._LOGIN_CHALLENGE_MARKS))


def _challenge_page_is_relevant(plat, page):
    """Whether a challenge-looking tab belongs to platform auth recovery.

    The general login probe intentionally inspects cross-origin challenge tabs.
    A connected-session monitor is more conservative because authors may keep
    unrelated pages open in the same profile; a help page mentioning
    “验证码” must not log the publishing account out.  Platform-host pages or
    URLs carrying a known challenge marker remain eligible.
    """
    try:
        url = str(page.url() or "")
        parsed = urlsplit(url)
        current = ((parsed.hostname or "") + parsed.path + "?" +
                   (parsed.query or "")).lower()
        if any(mark.lower() in current for mark in _mgr()._LOGIN_CHALLENGE_URL_MARKS):
            return True
        home = urlsplit(_mgr().PLATFORMS[_mgr().base_plat(plat)].CONFIG["home"])
        home_host = (home.hostname or "").lower().rstrip(".")
        labels = home_host.split(".")
        root = ".".join(labels[-2:]) if len(labels) >= 2 else home_host
        host = (parsed.hostname or "").lower().rstrip(".")
        return bool(host and (host == root or host.endswith("." + root)))
    except Exception:
        return False


# ---------------------------------------------------------------- 流程加载
def load_flow(plat, action):
    """流程表三层：data/publish/flows-<plat>.json（用户校准）→ 仓库校准模板
    flows-<plat>-calibrated.json（真机验证过的基线，随代码分发）→ 平台模块
    内置默认（待校准推测）。"""
    from pathlib import Path
    for fp in (_mgr().paths.PUBLISH_DIR / ("flows-%s.json" % plat),
               Path(__file__).with_name("flows-%s-calibrated.json" % plat)):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            # A previous Windows install wrote the user override after a
            # UTF-8/GBK conversion and persisted U+FFFD (�) inside every
            # selector.  The JSON still parses and passed the structural
            # contract check, but the browser could never find labels such as
            # “新建小说” or “章节名称”.  Treat that file as corrupt and fall
            # through to the packaged, real-device-calibrated baseline.
            if not _mgr()._flow_text_clean(data):
                continue
            if isinstance(data, dict) and isinstance(data.get(action), list):
                steps = data[action]
                # A stale user override must not silently downgrade a
                # calibrated safety flow to "fill and pretend".  Keep the
                # override precedence only when it contains the actions that
                # prove the remote operation was actually accepted.
                if _mgr()._flow_contract_ok(plat, action, steps):
                    return steps
        except Exception:
            pass
    # A timed-publish flow changes a consequential platform state.  Never
    # synthesize one from the immediate-publish flow: selector guesses can
    # silently fall through optional steps and be recorded as queued.  A
    # platform must ship an explicit, calibrated upload_chapter_scheduled
    # table (or a user override) before this recovery path is available.
    return _mgr().PLATFORMS[plat].FLOWS[action]


def _flow_text_clean(value):
    """Return False when a persisted flow contains replacement characters."""
    if isinstance(value, str):
        return "\ufffd" not in value
    if isinstance(value, dict):
        return all(_flow_text_clean(k) and _flow_text_clean(v)
                   for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_flow_text_clean(v) for v in value)
    return True


def _flow_contract_ok(plat, action, steps):
    """Return whether a flow has a real completion contract.

    These checks intentionally cover only the calibrated publishing actions;
    exploratory/probe flows may remain permissive.  Invalid runtime files
    fall through to the packaged calibrated baseline.
    """
    if not isinstance(steps, list):
        return False
    def has(do=None, text=None):
        return any(isinstance(s, dict) and (do is None or s.get("do") == do)
                   and (text is None or s.get("text") == text)
                   for s in steps)
    if plat == "fanqie":
        if action == "create_book":
            return (has("submit") and any(
                isinstance(s, dict) and s.get("do") == "url_any"
                and any(mark in (s.get("any") or [])
                        for mark in ("book-info", "chapter-manage"))
                for s in steps))
        if action == "upload_chapter_draft":
            return (has("submit") and has("expect_text") and any(
                isinstance(s, dict) and s.get("do") == "verify"
                and "chapter_manage_url" in str(s.get("url") or "")
                for s in steps))
        if action == "upload_chapter":
            return (has("submit") and has("fanqie_publish")
                    and has("verify_chapter_status"))
        if action == "upload_chapter_scheduled":
            return (any(isinstance(s, dict)
                        and s.get("do") == "fanqie_publish"
                        and s.get("force_schedule") is True for s in steps)
                    and has("verify_chapter_status"))
        return True
    if plat != "qimao":
        return True
    if action == "create_book":
        return has("submit", "确认创建")
    if action == "upload_chapter_draft":
        return has("click_real", "存草稿") and any(
            isinstance(s, dict) and s.get("do") == "verify"
            and "draft_url" in str(s.get("url") or "") for s in steps)
    if action == "upload_chapter":
        # A generic toast or title visible in a draft list is not proof of
        # publication. The calibrated Qimao path must initiate the real
        # immediate-publish action and finish with a chapter-management row
        # carrying a live review/published status. Confirmation dialogs vary
        # by chapter state, so their follow-up click is optional; the status
        # row is the mandatory receipt.
        return (has("click_real", "立即发布")
                and has("verify_chapter_status"))
    return True


# ---------------------------------------------------------------- 动作：连接
def connect(plat, account="", unattended=False):
    """Open the saved platform profile and verify its login session.

    Interactive callers wait for a user login/QR scan. Unattended callers only
    reuse an already valid saved session and fail immediately when the platform
    requires a new login or challenge.
    """
    if plat not in _mgr().PLATFORMS:
        return False, "未知平台"
    try:
        slot = _mgr().slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    if unattended:
        action_lock = _mgr()._try_action_lock(slot)
        if action_lock is None:
            return False, "该平台账号浏览器正在执行其他操作，自动恢复稍后重试"
        try:
            return _mgr()._connect_unattended(plat, account, slot)
        finally:
            action_lock.release()

    # Register before touching the browser so the passive watchdog cannot
    # begin a second probe in the small window between navigation and waiter
    # thread creation. Acquire the action lock first to keep the lock order
    # identical to both watchdog probes.
    registration_lock = _mgr()._try_action_lock(slot)
    if registration_lock is None:
        return False, "该平台账号浏览器正在执行其他操作，请稍后重试"
    cancel_event = threading.Event()
    waiter_started = False
    try:
        with _mgr()._LOGIN_WAITERS_GUARD:
            if slot in _mgr()._LOGIN_WAITERS:
                return True, ""
            _mgr()._LOGIN_WAITERS.add(slot)
            _mgr()._LOGIN_CANCEL_EVENTS[slot] = cancel_event
        waiting = _mgr()._st(slot).get("status") == "waiting_login"
        # Keep the action lock through browser setup and waiter registration.
        # Without this, a concurrent disconnect/upload can close or navigate
        # the profile between _open_page() and the durable waiting_login mark.
        b, page = _mgr()._open_page(slot, preserve_tabs=waiting)
        if cancel_event.is_set():
            return False, "登录连接已取消"
        if not waiting or _mgr()._page_is_empty(page):
            page.navigate(_mgr().PLATFORMS[plat].CONFIG["home"])
        if cancel_event.is_set():
            return False, "登录连接已取消"
        _mgr()._set(slot, status="waiting_login", error="")
        acc = str(account or "").strip()

        def wait_login():
            try:
                deadline = time.time() + _mgr().LOGIN_WAIT_S
                while time.time() < deadline:
                    if cancel_event.is_set():
                        return
                    try:
                        ok, _u, checked_page = _mgr()._probe_login_pages(slot, b, page)
                        if ok and not cancel_event.is_set():
                            _mgr()._clear_auth_page(slot)
                            _mgr()._set(slot, status="connected",
                                 last_login=time.strftime("%m-%d %H:%M"))
                            _mgr().ledger.record(plat, "connect", ok=True, account=acc)
                            return
                    except (BrowserError, Exception):
                        pass                # 页面被用户关掉等：继续等到超时
                    time.sleep(5)
                if not cancel_event.is_set():
                    _mgr()._finalize_login_wait(slot, plat, acc,
                                         cancel_event=cancel_event)
            finally:
                with _mgr()._LOGIN_WAITERS_GUARD:
                    if _mgr()._LOGIN_CANCEL_EVENTS.get(slot) is cancel_event:
                        _mgr()._LOGIN_CANCEL_EVENTS.pop(slot, None)
                        _mgr()._LOGIN_WAITERS.discard(slot)

        threading.Thread(target=wait_login, daemon=True,
                         name="pub-login-%s" % slot).start()
        waiter_started = True
        return True, ""
    except BrowserError as e:
        _mgr()._set(slot, status="error", error=str(e))
        return False, str(e)
    except Exception as exc:
        error = "无法打开平台作者后台：%s" % str(exc)[:160]
        _mgr()._set(slot, status="error", error=error)
        _mgr().ledger.record(plat, "connect", ok=False, account=str(account or "").strip(),
                      error=error[:300])
        return False, error
    finally:
        if not waiter_started:
            with _mgr()._LOGIN_WAITERS_GUARD:
                if _mgr()._LOGIN_CANCEL_EVENTS.get(slot) is cancel_event:
                    _mgr()._LOGIN_CANCEL_EVENTS.pop(slot, None)
                    _mgr()._LOGIN_WAITERS.discard(slot)
        registration_lock.release()


def browser_running(slot):
    """Whether a live controlled browser exists for the slot (never launches)."""
    browser = _mgr()._browsers.get(slot)
    return bool(browser and browser.alive())


def _unattended_attach_gate(slot):
    """无人值守动作的浏览器闸：只许 attach 已在跑的实例，绝不 launch。

    2026-10-22 拍板：登录/扫码只发生在用户手动点「连接平台」的时候；
    定时发布、批次重试、run 续跑、启动扫查遇到浏览器没开一律挂起并
    给出「连接平台」指引，用户一连上，下一轮重试自动续跑。
    返回 (ok, error)。
    """
    if browser_running(slot):
        return True, ""
    state = _mgr()._st(slot)
    if str(state.get("status") or "") == "waiting_login":
        return False, str(state.get("error")
                          or "平台等待登录或安全验证，验证完成后自动重试")
    return False, ("平台浏览器未运行：为避免无人值守时强拉登录窗，"
                   "请在发布台点「连接平台」，连接后即可发布并自动续跑")


def _connect_unattended(plat, account, slot):
    """Verify a saved session while holding the profile's action lock."""
    acc = str(account or "").strip()
    ok_gate, error = _mgr()._unattended_attach_gate(slot)
    if not ok_gate:
        return False, error
    try:
        _b, page = _mgr()._open_page(slot, preserve_tabs=True)
        # A login/challenge can span several retries. Observe its current page
        # without refreshing it, so recovery never invalidates an in-progress
        # verification. Once it clears, the normal batch reconciles and resumes.
        status = _mgr()._st(slot).get("status")
        ok, url, checked_page = _mgr()._probe_login_pages(slot, _b, page)
        auth_required = (not ok and _mgr()._authentication_required(plat, url))
        if (not ok and (not auth_required or _mgr()._page_is_empty(page)
                        or status in (None, ""))
                and (status != "waiting_login" or _mgr()._page_is_empty(page))
                and checked_page is page):
            # A fresh/previously connected session gets one active probe so a
            # blank or stale page is initialized. A waiting profile may also
            # have lost its challenge tab during a browser restart; when the
            # recovered page is truly empty, re-open the platform home to
            # verify whether the persistent cookie already completed login.
            # A brand-new slot has no persisted verification state, so perform
            # the same one-time initialization even if the provider already
            # rendered its challenge at the configured home URL. Once the slot
            # is marked waiting_login, later probes remain passive.
            # Non-empty challenge pages remain passive so a human/platform
            # verification in progress is never navigated away.
            ok, url = _mgr()._check_login(slot, page, navigate=True)
            auth_required = (not ok and _mgr()._authentication_required(plat, url))
        elif not ok and not auth_required and status != "waiting_login":
            ok, url = _mgr()._check_login(slot, page, navigate=True)
    except Exception as exc:
        ok, url = False, str(exc)
    if ok:
        _mgr()._clear_auth_page(slot)
        _mgr()._set(slot, status="connected",
             last_login=time.strftime("%m-%d %H:%M"), error="")
        _mgr().ledger.record(plat, "connect", ok=True, account=acc)
        return True, ""
    waiting = _mgr()._authentication_required(plat, url)
    error = ("平台等待登录或安全验证，验证完成后自动重试：%s" if waiting else
             "保存的平台登录态不可用，自动发布未等待人工登录：%s") % str(url or "unknown")[:160]
    _mgr()._set(slot, status="waiting_login" if waiting else "error", error=error)
    _mgr().ledger.record(plat, "connect", ok=False, account=acc, error=error[:300])
    return False, error


def _finalize_login_wait(slot, plat=None, acc="", cancel_event=None):
    """等扫码超时后的终审：复用 profile 复核，验证未完成时保留页面。

    waiting_login 不再强制导航或改判超时；持久批次会继续被动检查。
    返回 True 表示复核通过（已判 connected）。
    slot 兼容旧调用（只传 plat）：平台名即默认槽。"""
    if cancel_event is not None and cancel_event.is_set():
        return False
    if plat is None:
        plat = _mgr().base_plat(slot)
    login_state = ""
    probe_error = ""
    try:
        _b, page = _mgr()._open_page(slot, preserve_tabs=True)
        ok, login_state, checked_page = _mgr()._probe_login_pages(slot, _b, page)
    except Exception as exc:
        ok = False
        probe_error = str(exc)[:160]
    if cancel_event is not None and cancel_event.is_set():
        return False
    if ok:
        _mgr()._clear_auth_page(slot)
        _mgr()._set(slot, status="connected", last_login=time.strftime("%m-%d %H:%M"))
        _mgr().ledger.record(plat, "connect", ok=True, account=acc)
    elif _mgr()._authentication_required(plat, login_state):
        _mgr()._set(slot, status="waiting_login",
             error="平台等待登录或安全验证，验证完成后自动重试：%s" % str(login_state)[:160])
    elif probe_error and (_mgr()._st(slot).get("auth_target_id") or
                          _mgr()._st(slot).get("status") == "waiting_login"):
        _mgr()._set(slot, status="waiting_login",
             error="登录页面暂时不可用，认证看门狗将自动重试：%s" % probe_error)
    else:
        _mgr()._set(slot, status="error", error="等待登录超时（15 分钟），请重新点连接")
    return ok


def disconnect(plat, account=""):
    try:
        slot = _mgr().slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    action_lock = _mgr()._try_action_lock(slot)
    if action_lock is None:
        return False
    try:
        with _mgr()._LOGIN_WAITERS_GUARD:
            cancel_event = _mgr()._LOGIN_CANCEL_EVENTS.get(slot)
            if cancel_event is not None:
                cancel_event.set()
                _mgr()._LOGIN_CANCEL_EVENTS.pop(slot, None)
                _mgr()._LOGIN_WAITERS.discard(slot)
        b = _mgr()._browsers.pop(slot, None)
        if b:
            b.close()
        with _mgr()._AUTH_STATE_CACHE_LOCK:
            _mgr()._AUTH_STATE_CACHE.pop(slot, None)
        # An explicit disconnect is a user cancellation. Do not let the
        # watchdog interpret stale durable intents as permission to reopen the
        # browser and resume publishing after the user has signed out.
        with _mgr().LOCK:
            state = _mgr()._state["platforms"].get(slot)
            _mgr()._RECOVERY_CANCEL_EPOCH[slot] = int(
                _mgr()._RECOVERY_CANCEL_EPOCH.get(slot, 0)) + 1
            if isinstance(state, dict):
                state.pop("pending_creates", None)
                state.pop("pending_create", None)
                state.pop("pending_uploads", None)
                state["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _mgr()._save()
        with _mgr()._CREATE_BOOK_RECOVERY_GUARD:
            _mgr()._CREATE_BOOK_RECOVERY.difference_update(
                key for key in _mgr()._CREATE_BOOK_RECOVERY if key[1] == slot)
        with _mgr()._UPLOAD_RECOVERY_GUARD:
            _mgr()._UPLOAD_RECOVERY.difference_update(
                key for key in _mgr()._UPLOAD_RECOVERY if key[0] == str(slot))
        _mgr()._set(slot, status="none", error="", auth_script_id="",
             auth_script_target_id="")
        return True
    finally:
        action_lock.release()


# ---------------------------------------------------------------- 动作：探测
def probe_form_async(plat, account=""):
    """「探测表单」：跑 probe 流程把页面真实可交互元素 dump 进台账 log。"""
    if plat not in _mgr().PLATFORMS:
        return False, "未知平台"
    try:
        slot = _mgr().slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    action_lock = _mgr()._try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if _mgr()._st(slot).get("status") not in ("connected", "error"):
        action_lock.release()
        return False, "请先连接并登录平台"
    _mgr()._set(slot, status="busy", last_action="probe", error="")

    logs = []

    def run():
        try:
            b, page = _mgr()._open_page(slot)
            _mgr().flow.run_flow(page, _mgr().load_flow(plat, "probe_form"),
                          values={}, config=_mgr().PLATFORMS[plat].CONFIG,
                          shot=lambda n: page.screenshot(_mgr().ledger.shot_path(plat, "probe", n)),
                          log=logs.append)
            _mgr()._set(slot, status="connected")
            _mgr().ledger.record(plat, "probe", ok=True, account=str(account or "").strip(),
                          error="\n".join(logs)[:2000])
        except Exception as e:
            _mgr()._set(slot, status="error", error="探测失败：%s" % e)
            _mgr().ledger.record(plat, "probe", ok=False, error=str(e)[:300],
                          account=str(account or "").strip())
        finally:
            _mgr()._save()
            action_lock.release()

    threading.Thread(target=run, daemon=True, name="pub-probe-%s" % plat).start()
    return True, ""
