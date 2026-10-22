# -*- coding: utf-8 -*-
"""publish/manager.py 的 auth 子模块。

从 publish/manager.py 拆出（单文件行数治理）；模块级状态与辅助函数仍在
manager.py，全部经 _mgr() 解析以保持测试对 manager 属性的打桩语义；
旧调用面由 manager 再导出。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import quality_gate


def _mgr():
    from . import manager
    return manager


def _auth_state_path(slot):
    """Return the optional auth-state path for a platform account slot.

    ``CODEBEE_PUBLISH_AUTH_STATE`` may name one JSON file or a directory.  A
    ``{slot}`` placeholder is expanded for multi-account deployments.  The
    default is ``~/.codebee/publish_auth/<slot>.json`` so browser cookies and
    storage state never enter the repository or its backups.
    """
    raw = str(os.environ.get(_mgr().AUTH_STATE_FILE_ENV) or "").strip()
    if raw:
        raw = raw.replace("{slot}", str(slot))
        p = Path(raw).expanduser()
        if p.is_dir() or raw.endswith(("/", "\\")):
            return p / (str(slot) + ".json")
        return p
    base = str(os.environ.get(_mgr().AUTH_STATE_DIR_ENV) or "").strip()
    return (Path(base).expanduser() if base else _mgr().AUTH_STATE_DEFAULT_DIR) / (
        str(slot) + ".json")


def _platform_host(plat):
    try:
        host = (urlsplit(str(_mgr().PLATFORMS[plat].CONFIG.get("home") or ""))
                .hostname or "")
    except Exception:
        host = ""
    return host.lower().lstrip(".")


def _platform_origin(plat):
    host = _mgr()._platform_host(plat)
    return ("https://" + host) if host else ""


def _host_allowed_for_platform(plat, host):
    base = _mgr()._platform_host(plat)
    value = str(host or "").lower().split(":", 1)[0].lstrip(".")
    # Exporters commonly store a parent cookie domain (``.qimao.com``)
    # while the configured home is a writer subdomain.  Permit that parent
    # by at most one label; this covers qimao.com while avoiding broad
    # public-suffix-like values such as ``com`` or ``co.uk``.
    base_labels = base.split(".") if base else []
    value_labels = value.split(".") if value else []
    parent = (len(value_labels) >= 2 and len(base_labels) - len(value_labels) == 1
              and base.endswith("." + value))
    return bool(base and value and
                (value == base or value.endswith("." + base) or parent))


def _auth_cookie_items(plat, state):
    """Normalize Playwright/Chromium cookie exports for CDP setCookies."""
    raw = state if isinstance(state, list) else (state or {}).get("cookies")
    if not isinstance(raw, list):
        return []
    out = []
    allowed = {"name", "value", "url", "domain", "path", "secure",
               "httpOnly", "sameSite", "expires", "priority",
               "sourceScheme", "sourcePort", "partitionKey"}
    same_site = {"no_restriction": "None", "unspecified": "None",
                 "lax": "Lax", "strict": "Strict", "none": "None"}
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("name") or ""):
            continue
        cookie = {k: v for k, v in item.items() if k in allowed}
        url = str(cookie.get("url") or "")
        domain = str(cookie.get("domain") or "")
        host = domain or (urlsplit(url).hostname if url else "")
        if not host and not url:
            cookie["domain"] = _mgr()._platform_host(plat)
            host = cookie["domain"]
        if not _mgr()._host_allowed_for_platform(plat, host):
            continue
        if "expirationDate" in item and "expires" not in cookie:
            try:
                cookie["expires"] = float(item["expirationDate"])
            except (TypeError, ValueError):
                pass
        if "sameSite" in cookie:
            value = str(cookie["sameSite"] or "").lower()
            cookie["sameSite"] = same_site.get(value, cookie["sameSite"])
        out.append(cookie)
    return out


def _auth_storage_origins(plat, state):
    """Return safe ``origin -> localStorage`` entries from a storage export."""
    raw = (state or {}).get("origins") if isinstance(state, dict) else None
    if not isinstance(raw, list):
        return {}
    out = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        origin = str(item.get("origin") or "").strip().rstrip("/")
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        if (not origin or parsed.scheme.lower() != "https" or
                not parsed.netloc or parsed.path not in ("", "/") or
                parsed.query or parsed.fragment or parsed.username or
                parsed.password or not _mgr()._host_allowed_for_platform(plat, host)):
            continue
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            continue
        netloc = hostname
        if port is not None and not (scheme == "https" and port == 443):
            netloc += ":%d" % port
        canonical = "%s://%s" % (scheme, netloc)
        entries = item.get("localStorage")
        if not isinstance(entries, list):
            continue
        values = {}
        for pair in entries:
            if not isinstance(pair, dict) or "name" not in pair:
                continue
            values[str(pair["name"])] = str(pair.get("value") or "")
        if values:
            out[canonical] = values
    return out


def _read_auth_state(slot):
    path = _mgr()._auth_state_path(slot)
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > 8 * 1024 * 1024:
            return None, path, None
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, (dict, list)):
            return None, path, None
        fingerprint = (int(stat.st_mtime_ns), int(stat.st_size))
        return state, path, fingerprint
    except (OSError, ValueError, TypeError):
        return None, path, None


def _auth_storage_script(origins, remove_keys=None):
    payload = json.dumps(origins, ensure_ascii=False, separators=(",", ":"))
    removals = json.dumps(remove_keys or {}, ensure_ascii=False,
                          separators=(",", ":"))
    return ("(() => { const stores = %s, old = %s; "
            "const origin = location.origin, values = stores[origin]; "
            "try { (old[origin] || []).forEach(k => localStorage.removeItem(k)); "
            "if (values !== undefined) Object.keys(values).forEach(k => "
            "localStorage.setItem(k, String(values[k]))); } catch (_) {} })();"
            % (payload, removals))


def _auth_script_entries(slot):
    """Return target -> imported init-script metadata, including legacy state."""
    with _mgr()._AUTH_STATE_CACHE_LOCK:
        cached = _mgr()._AUTH_STATE_CACHE.get(slot)
    raw = (cached or {}).get("scripts") if isinstance(cached, dict) else None
    if not isinstance(raw, dict):
        raw = _mgr()._st(slot).get("auth_scripts")
    entries = {}
    if isinstance(raw, dict):
        for target, item in raw.items():
            if not isinstance(item, dict):
                continue
            script_id = str(item.get("script_id") or "")
            if script_id:
                entries[str(target)] = {
                    "script_id": script_id,
                    "storage_keys": item.get("storage_keys") or {},
                }
    if not entries:
        target = str((cached or {}).get("target_id") or
                     _mgr()._st(slot).get("auth_script_target_id") or "")
        script_id = str((cached or {}).get("script_id") or
                        _mgr()._st(slot).get("auth_script_id") or "")
        if target and script_id:
            entries[target] = {
                "script_id": script_id,
                "storage_keys": ((cached or {}).get("storage_keys") or
                                  _mgr()._st(slot).get("auth_storage_keys") or {}),
            }
    return entries


def _auth_page_for_target(slot: str, target: str, current_page: Any) -> Any:
    if str(getattr(current_page, "_target_id", "") or "") == str(target):
        return current_page
    browser = _mgr()._browsers.get(slot)
    finder = getattr(browser, "page_for_target", None) if browser else None
    if callable(finder):
        try:
            return finder(target)
        except Exception:
            return None
    return None


def _remove_auth_scripts(slot: str, page: Any, entries: Any) -> None:
    """Remove imported scripts and keys from every still-live target."""
    for target, item in (entries or {}).items():
        target_page = _mgr()._auth_page_for_target(slot, target, page)
        if target_page is None:
            continue
        try:
            target_page.remove_init_script(item.get("script_id") or "")
        except Exception:
            pass
        keys = item.get("storage_keys") or {}
        if keys:
            try:
                target_page.evaluate(_mgr()._auth_storage_script({}, keys))
            except Exception:
                pass


def _seed_auth_state(plat: str, slot: str, page: Any) -> bool:
    """Inject a provisioned auth state into this page, if one is configured.

    Only cookies/localStorage belonging to the platform's own host (including
    its subdomains) are accepted.  Invalid or expired state is harmless: the
    regular login detector records ``waiting_login`` and keeps its recovery
    behavior.  Cookie values are never logged.  A target->script map is kept
    so rotating state while a challenge uses multiple tabs cannot leave an
    old init script behind on a tab that is revisited later.
    """
    state, path, fingerprint = _mgr()._read_auth_state(slot)
    target = str(getattr(page, "_target_id", "") or "")
    entries = _mgr()._auth_script_entries(slot)
    if state is None or fingerprint is None:
        _mgr()._remove_auth_scripts(slot, page, entries)
        with _mgr()._AUTH_STATE_CACHE_LOCK:
            _mgr()._AUTH_STATE_CACHE.pop(slot, None)
        _mgr()._set(slot, auth_script_id="", auth_script_target_id="",
             auth_storage_keys={}, auth_scripts={}, auth_state_fingerprint="")
        return False
    cache_key = (str(path), fingerprint, target)
    with _mgr()._AUTH_STATE_CACHE_LOCK:
        cached = _mgr()._AUTH_STATE_CACHE.get(slot)
    if isinstance(cached, dict) and cached.get("cache_key") == cache_key:
        # Older state files did not persist the fingerprint.  The in-process
        # cache still proves this exact payload was imported; backfill the
        # durable marker once so the auth watchdog does not retry forever.
        if (_mgr()._st(slot).get("auth_state_fingerprint") != list(fingerprint)
                or _mgr()._st(slot).get("auth_scripts") != entries):
            current = entries.get(target) or {}
            _mgr()._set(slot, auth_script_id=current.get("script_id", ""),
                 auth_script_target_id=target if current else "",
                 auth_storage_keys=current.get("storage_keys", {}),
                 auth_scripts=entries,
                 auth_state_fingerprint=list(fingerprint))
        return True
    # A new state payload (or a new target) invalidates every old script.  Do
    # this before installing the replacement so removed localStorage keys are
    # not reintroduced by a stale script on another tab.
    old_keys = {}
    for item in entries.values():
        for origin, names in (item.get("storage_keys") or {}).items():
            merged = old_keys.setdefault(origin, [])
            for name in names or []:
                if name not in merged:
                    merged.append(name)
    _mgr()._remove_auth_scripts(slot, page, entries)
    try:
        cookies = _mgr()._auth_cookie_items(plat, state)
        setter = getattr(page, "set_cookies", None)
        if cookies and callable(setter):
            setter(cookies)
        origins = _mgr()._auth_storage_origins(plat, state)
        # A supplied state with cookies but no localStorage must also clear a
        # previously imported token when an old init script survives a
        # browser/page restart.  The current platform origin is the only
        # fallback target; unrelated site storage is never touched.
        if not origins and isinstance(state, dict) and state.get("cookies"):
            origin = _mgr()._platform_origin(plat)
            if origin:
                origins = {origin: {}}
        storage_keys = {origin: list(values.keys())
                        for origin, values in origins.items()}
        script_id = ""
        if origins:
            script = _mgr()._auth_storage_script(origins, old_keys)
            add_script = getattr(page, "add_init_script", None)
            if callable(add_script):
                script_id = str(add_script(script) or "")
            # Apply to an already-loaded first-party page as well.  The init
            # script covers the next navigation (including about:blank ->
            # home), while this path avoids an unnecessary refresh.
            try:
                current_url = str(page.url() or "")
                parsed = urlsplit(current_url)
                current = ("%s://%s" % (parsed.scheme.lower(), parsed.netloc.lower())
                           if parsed.scheme and parsed.netloc else
                           current_url.rstrip("/"))
                values = origins.get(current)
                if values is not None:
                    page.evaluate(_mgr()._auth_storage_script(
                        {current: values}, {current: old_keys.get(current, [])}))
            except Exception:
                pass
    except Exception:
        # A stale browser/CDP session must still go through the normal auth
        # detector; importing state is an optimization, never a hard failure.
        return False
    new_entries = ({target: {"script_id": script_id,
                             "storage_keys": storage_keys}}
                   if target and script_id else {})
    with _mgr()._AUTH_STATE_CACHE_LOCK:
        _mgr()._AUTH_STATE_CACHE[slot] = {"cache_key": cache_key,
                                   "scripts": new_entries}
    _mgr()._set(slot, auth_script_id=script_id,
         auth_script_target_id=target if script_id else "",
         auth_storage_keys=storage_keys,
         auth_scripts=new_entries,
         auth_state_fingerprint=list(fingerprint))
    return True


def _auth_state_changed(slot):
    """Return whether a newly provisioned auth-state file needs importing.

    A deployment may refresh a Playwright/Chromium state file out of process
    (for example, after an operator completes a platform re-authentication in
    a separate browser).  Keep the publishing worker passive, but let the
    watchdog notice that durable input changed even when the account was left
    in ``error`` after the previous session expired.
    """
    _state_data, _path, fingerprint = _mgr()._read_auth_state(slot)
    if fingerprint is None:
        return False
    previous = _mgr()._st(slot).get("auth_state_fingerprint")
    try:
        previous = tuple(int(value) for value in (previous or ()))
    except (TypeError, ValueError):
        previous = ()
    return tuple(fingerprint) != previous


def _action_lock(slot):
    key = str(slot)
    with _mgr()._ACTION_LOCKS_GUARD:
        return _mgr()._ACTION_LOCKS.setdefault(key, threading.Lock())


def _try_action_lock(slot):
    lock = _mgr()._action_lock(slot)
    return lock if lock.acquire(blocking=False) else None


def _wait_chapter_rows_ready(page, mod, timeout=4.0):
    """Wait until a chapter table has rendered, without paying a fixed delay.

    Chapter management pages are SPAs: a fixed sleep is long on a warm page
    and still insufficient on a cold one. Poll the platform's own row probe at
    a short cadence, returning as soon as rows (or an explicit empty-state
    marker) are visible. The caller still performs the full collection and
    validation afterwards, so this only removes unnecessary idle time.
    """
    rows_js = mod.CONFIG.get("count_rows_js")
    if not rows_js:
        return False
    markers = [str(x) for x in (mod.CONFIG.get("empty_chapter_markers") or [])
               if str(x)]
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        try:
            rows = page.call(rows_js) or []
            if rows:
                return True
            if markers:
                body = str(page.evaluate(
                    "document.body && document.body.innerText || ''") or "")
                if any(marker in body for marker in markers):
                    return True
        except Exception:
            # Rendering can briefly detach the table; let the normal collector
            # surface a useful platform-specific error if it never recovers.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))


# ---------------------------------------------------------------- 状态
def _load():
    global _state
    with _mgr().LOCK:
        try:
            d = json.loads(_mgr().STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("platforms"), dict):
                _mgr()._state = d
                return
        except Exception:
            pass
        _mgr()._state = {"platforms": {}}


def _save():
    with _mgr().LOCK:
        try:
            _mgr().atomic.write_json(_mgr().STATUS_FILE, _mgr()._state, mode=0o600, indent=1)
        except Exception:
            pass


def _set(plat, **kv):
    with _mgr().LOCK:
        ent = _mgr()._state["platforms"].setdefault(plat, {})
        ent.update(kv)
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _mgr()._save()


def _st(plat):
    return (_mgr()._state.get("platforms") or {}).get(plat) or {}


def _quality_release_guard(task, plat, action="publish", target_chapter=None,
                           force=False, force_confirmed=False, force_reason=""):
    """Return a release decision backed by the latest completed review.

    Review tasks require a quality verdict. Serial tasks additionally require
    global/chapter evidence; single manuscripts use their content-review result.
    """
    if str(task.get("type") or "").lower() not in ("novel", "serial_novel") \
            and (task.get("engine") or "") != "review":
        return {"allowed": True, "status": "not_applicable", "blockers": [],
                "warnings": [], "forced": False}
    from .. import store
    verdict = None
    # store.task_runs already returns newest-first by created_at_ns (with
    # legacy timestamp/id fallbacks). Run IDs include a random suffix, so
    # sorting them lexically can select an older verdict created in the same
    # second and publish against stale review evidence.
    for run in store.task_runs(task.get("id")) or []:
        candidate = run.get("verdict")
        # 答疑、通知等 run 也可能是 done，但它们没有质量结论，不能
        # 覆盖最近一条 publishable verdict，避免发布时误报“未评审”。
        if (run.get("status") == "done" and isinstance(candidate, dict)
                and "publishable" in candidate and not candidate.get("qa")
                and run.get("op") != "qa"):
            verdict = candidate
            break
    version_args = {}
    if (isinstance(verdict, dict) and verdict.get("serial") is True and
            isinstance(verdict.get("start_chapter"), int) and
            not isinstance(verdict.get("start_chapter"), bool) and
            isinstance(verdict.get("end_chapter"), int) and
            not isinstance(verdict.get("end_chapter"), bool)):
        version_args = {
            "start_chapter": verdict["start_chapter"],
            "end_chapter": verdict["end_chapter"],
            "include_archive": (verdict["start_chapter"] > 1 and
                                not quality_gate.has_complete_local_prefix(
                                    task.get("workdir") or "",
                                    verdict["start_chapter"])),
        }
    manuscript = (None if isinstance(verdict, dict) and verdict.get("serial") is True
                  else task.get("manuscript"))
    version_snapshot = quality_gate.local_version(
        task.get("workdir") or "", manuscript, **version_args)
    current_fingerprint = version_snapshot.get("fingerprint")
    if version_args:
        from .. import pipeline
        current_fingerprint = pipeline._serial_review_fingerprint(
            task.get("workdir") or "", version_args["start_chapter"],
            version_args["end_chapter"])
    decision = quality_gate.evaluate_release(
        verdict or {}, action=action, target_chapter=target_chapter,
        platform_version=_mgr().ledger.book_for(task.get("id"), plat) or {},
        force=force, force_confirmed=force_confirmed, force_reason=force_reason,
        serial=bool(task.get("serial")) or
        str(task.get("type") or "").lower() == "serial_novel",
        current_manuscript_fingerprint=current_fingerprint)
    decision["version_snapshot"] = version_snapshot
    decision["platform_snapshot"] = _mgr().ledger.book_for(task.get("id"), plat) or {}
    if verdict:
        decision["reviewed_through"] = verdict.get("end_chapter") or 0
        decision["quality_status"] = verdict.get("quality_status") or "continue_only"
        decision["review_run_id"] = run.get("id")
    return decision


def view():
    """前端状态视图：平台状态 + 是否找到浏览器 + 作品登记 + 账号列表。"""
    from .browser import find_browser
    out = {}
    for pid, mod in _mgr().PLATFORMS.items():
        s = _mgr()._st(pid)
        out[pid] = {"label": mod.CONFIG["label"], "status": s.get("status") or "none",
                    "at": s.get("at") or "", "error": s.get("error") or "",
                    "last_action": s.get("last_action") or "",
                    "profile": str(_mgr()._profile_dir(pid)),
                    "accounts": []}
    # 多账号：账号条目带各自状态槽（slot=fanqie@a2）；老前端忽略此字段不受影响
    for a in _mgr().load_accounts():
        pid = a.get("platform")
        if pid not in out:
            continue
        slot = "%s@%s" % (pid, a.get("id"))
        st = _mgr()._st(slot)
        out[pid]["accounts"].append({
            "id": a.get("id"), "label": a.get("label") or "",
            "status": st.get("status") or "none",
            "at": st.get("at") or "", "error": st.get("error") or "",
            "last_action": st.get("last_action") or "",
            "profile": str(_mgr()._profile_dir(slot))})
    return {"platforms": out, "browser_found": bool(find_browser())}


def recover_orphans():
    """启动收尸：busy 的工作线程随重启中断，改判 error。

    waiting_login 保留：验证页面仍可能在浏览器中，启动会话恢复与持久批次
    会被动复核它；改成 error 会让恢复代码重新导航并打断已有验证。

    升级自愈：旧版「等扫码窗口一关就冤判超时」留下的 error（error 文案带
    「等待登录超时」）由 resume_saved_sessions 统一重开并复核。不要在这里
    单独起复核线程，否则它会与启动恢复同时导航同一账号的浏览器。"""
    _mgr()._load()
    n = 0
    # 多账号：状态槽含 fanqie@a2 形态，全量扫（基础平台也在其中）
    for slot in list((_mgr()._state.get("platforms") or {}).keys()) + list(_mgr().PLATFORMS):
        s = _mgr()._st(slot)
        if s.get("status") == "busy":
            _mgr()._set(slot, status="error", error="上次操作随服务重启中断，请重试")
            n += 1
    return n


def saved_session_slots():
    """Return publishing profiles whose persisted login may be reusable.

    A configured book is enough to resume its profile even when an earlier
    process died before the platform login state was written.  Existing CDP
    ports and last-login timestamps also recover standalone platform sessions.
    """
    slots = set()
    for slot, state in (_mgr()._state.get("platforms") or {}).items():
        base = str(slot).split("@", 1)[0]
        if base in _mgr().PLATFORMS and (state.get("port") or state.get("last_login")):
            slots.add(str(slot))
    try:
        books = _mgr().ledger.load_books() or {}
    except Exception:
        books = {}
    for platform_books in books.values():
        if not isinstance(platform_books, dict):
            continue
        for plat, book in platform_books.items():
            if plat not in _mgr().PLATFORMS or not isinstance(book, dict):
                continue
            try:
                slots.add(_mgr().slot_of(plat, book.get("account") or ""))
            except ValueError:
                continue
    return sorted(slots, key=lambda value: (
        value.split("@", 1)[0], value.split("@", 1)[1] if "@" in value else ""))


def resume_saved_sessions():
    """Reopen known publishing profiles and detect reusable login cookies.

    This removes the manual reconnect step after a service restart.  A fresh
    login, QR scan, or platform challenge still follows the normal waiting
    login state and cannot be bypassed by this recovery path.

    A slot verified recently (shared ``launch_probe_at`` stamp) is skipped:
    a cookie cannot change while no browser runs, so re-launching every
    profile on every restart only made the login/writer windows pop back up
    each boot (2026-10-21/22 实案). The next real publish preflight still
    connects on demand, so correctness moves to first-use, not boot time.
    """
    now = time.time()
    started = 0
    for slot in _mgr().saved_session_slots():
        state = _mgr()._st(slot)
        try:
            last_probe = float(state.get("launch_probe_at") or 0)
        except (TypeError, ValueError):
            last_probe = 0.0
        if now - last_probe < _mgr().LOGIN_REPROBE_MIN_S:
            continue
        plat, sep, account = slot.partition("@")
        thread = threading.Thread(
            target=_mgr().connect, kwargs={"plat": plat,
                                    "account": account if sep else "",
                                    "unattended": True},
            daemon=True, name="pub-resume-%s" % slot)
        thread.start()
        started += 1
    return started


def _auth_watch_once():
    """Passively recover waiting login slots that became valid.

    The interactive ``connect`` waiter is intentionally finite so a request
    thread cannot live forever.  A platform challenge, however, may remain
    open for hours.  This one-shot pass is safe to call from a daemon loop:
    each profile has the same action lock as publishing, and
    ``_connect_unattended`` preserves active challenge tabs.
    """
    _mgr()._load()
    _mgr()._resume_pending_create_recoveries()
    _mgr()._resume_pending_upload_recoveries()
    slots = []
    for slot, state in (_mgr()._state.get("platforms") or {}).items():
        if not isinstance(state, dict) or _mgr().base_plat(slot) not in _mgr().PLATFORMS:
            continue
        status = state.get("status")
        # waiting_login is the normal passive recovery state.  An expired
        # session can also be left in error by an older worker; if the
        # provisioned auth-state file has since changed, retry that slot
        # without waiting for another publish request.  Other error states
        # remain untouched so content/configuration failures are not turned
        # into browser churn.
        if status == "waiting_login":
            slots.append(str(slot))
        elif (status == "error"
              and (_mgr()._authentication_required(_mgr().base_plat(slot), state.get("error"))
                   or _mgr()._auth_state_changed(str(slot)))):
            slots.append(str(slot))
    recovered = 0
    for slot in slots:
        # All paths acquire the per-slot action lock before inspecting or
        # registering a login waiter. This keeps the lock order stable and
        # prevents an interactive connect from racing this probe.
        lock = _mgr()._try_action_lock(slot)
        if lock is None:
            continue
        try:
            browser = _mgr()._browsers.get(slot)
            if browser is None or not browser.alive():
                # The operator closed the browser window. Relaunching it from
                # this 30-second watchdog turns "waiting for a login" into a
                # visible window popping back up forever. Leave the durable
                # waiting_login state alone; the next real publish request
                # attaches or launches on demand and rechecks the session.
                continue
            with _mgr()._LOGIN_WAITERS_GUARD:
                if slot in _mgr()._LOGIN_WAITERS:
                    # The interactive connect request is already passively
                    # checking this profile; do not run a second probe.
                    continue
            plat, sep, account = slot.partition("@")
            ok, _error = _mgr()._connect_unattended(
                plat, account if sep else "", slot)
            recovered += int(bool(ok))
        except Exception:
            # A closed browser or a transient CDP failure is retried on the
            # next tick; publishing state remains durable in ``waiting_login``.
            pass
        finally:
            lock.release()
    return recovered


def _connected_session_probe_once():
    """Passively detect an expired session before the next publish.

    Only already-running browser objects are inspected, so this probe never
    launches a new window or navigates an author's active page.  If the
    platform has redirected an existing tab to login or a security challenge,
    the slot enters the same durable ``waiting_login`` recovery path used by
    publishing.  A later successful probe then returns it to ``connected``.
    """
    _mgr()._load()
    slots = [str(slot) for slot, state in
             (_mgr()._state.get("platforms") or {}).items()
             if isinstance(state, dict)
             and state.get("status") == "connected"
             and _mgr().base_plat(slot) in _mgr().PLATFORMS]
    changed = 0
    for slot in slots:
        lock = _mgr()._try_action_lock(slot)
        if lock is None:
            continue
        try:
            with _mgr()._LOGIN_WAITERS_GUARD:
                if slot in _mgr()._LOGIN_WAITERS:
                    continue
            browser = _mgr()._browsers.get(slot)
            if browser is None or not browser.alive():
                continue
            target_id = str(_mgr()._st(slot).get("auth_target_id") or "").strip()
            page = None
            if target_id:
                page_for_target = getattr(browser, "page_for_target", None)
                if callable(page_for_target):
                    page = page_for_target(target_id)
            if page is None:
                page = browser.first_page(create=False)
            if page is None:
                continue
            plat, sep, account = slot.partition("@")
            ok, detail, _checked_page = _mgr()._probe_login_pages(
                slot, browser, page)
            if (not ok and _mgr()._authentication_required(plat, detail)
                    and _mgr()._challenge_page_is_relevant(plat, _checked_page)):
                _mgr()._set(
                    slot,
                    status="waiting_login",
                    error=("平台等待登录或安全验证，验证完成后自动重试：%s"
                           % str(detail or "登录态失效")[:160]),
                )
                changed += 1
        except Exception:
            # A transient CDP failure is left for the next tick and for
            # the normal publish preflight to classify more precisely.
            pass
        finally:
            lock.release()
    return changed


def _auth_watch_loop():
    while not _mgr()._AUTH_WATCH_STOP.is_set():
        try:
            _mgr()._auth_watch_once()
            _mgr()._connected_session_probe_once()
        except Exception:
            pass
        _mgr()._AUTH_WATCH_STOP.wait(_mgr().AUTH_WATCH_INTERVAL_S)


def start_auth_watchdog():
    """Start the process-wide passive login recovery loop once.

    This closes the gap after the 15-minute interactive waiter expires: once
    the user or platform finishes an already-open verification, pending
    durable batches resume on their own and idle sessions become ``connected``
    without a second UI action.
    """
    with _mgr()._AUTH_WATCH_LOCK:
        cur = _mgr()._AUTH_WATCH_THREAD
        if cur is not None and cur.is_alive():
            return False
        _mgr()._AUTH_WATCH_STOP.clear()
        thread = threading.Thread(
            target=_mgr()._auth_watch_loop, daemon=True, name="pub-auth-watchdog")
        _mgr()._AUTH_WATCH_THREAD = thread
        thread.start()
        return True


def stop_auth_watchdog():
    """Stop the passive watchdog (primarily for clean shutdown and tests)."""
    global _AUTH_WATCH_THREAD
    with _mgr()._AUTH_WATCH_LOCK:
        _mgr()._AUTH_WATCH_STOP.set()
        thread = _mgr()._AUTH_WATCH_THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=max(1.0, _mgr().AUTH_WATCH_INTERVAL_S + 1.0))
    with _mgr()._AUTH_WATCH_LOCK:
        # Keep the reference while a slow probe is still unwinding.  A new
        # start then cannot clear the stop event and create a second watcher.
        if _mgr()._AUTH_WATCH_THREAD is thread and (thread is None or
                                             not thread.is_alive()):
            _mgr()._AUTH_WATCH_THREAD = None


# ---------------------------------------------------------------- 浏览器会话
def _profiles_base():
    """profile 存放根：用户主目录 ~/.codebee/publish_profiles（仓库外）。

    浏览器 profile 是 GB 级运行时数据（缓存/扩展/字体），放仓库 data/ 里
    会拖垮静态扫描/备份（639M→986M 实测淹没整仓安全扫描），且登录态
    cookie 没必要进任何仓库周边流程。"""
    from pathlib import Path as _P
    return _P.home() / ".codebee" / "publish_profiles"


def _migrate_legacy_profiles():
    """一次性迁移：老位置 data/publish/profiles/<plat> 整体搬到新根（保登录态）。

    新位置已有同名平台目录时跳过（老数据视为已废弃）；搬家失败静默——
    大不了用户重扫一次码。"""
    from pathlib import Path as _P
    legacy = _P(_mgr().paths.PUBLISH_DIR) / "profiles"
    if not legacy.is_dir():
        return
    base = _mgr()._profiles_base()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for plat_dir in legacy.iterdir():
        if not plat_dir.is_dir():
            continue
        dst = base / plat_dir.name
        if dst.exists():
            continue
        try:
            import shutil
            shutil.move(str(plat_dir), str(dst))   # 跨盘（仓库盘→系统盘）也能搬
        except OSError:
            continue
    try:
        legacy.rmdir()                  # 空了才删得掉；还有残留就留给下次
    except OSError:
        pass


def _profile_dir(plat):
    return _mgr()._profiles_base() / plat
