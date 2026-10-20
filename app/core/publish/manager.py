# -*- coding: utf-8 -*-
"""发布管理：平台会话（attach-or-launch）、登录状态机、建书/发章后台线程。

状态机（data/publish/state.json 的 platforms.<id>.status）：
  none           从未连接
  waiting_login  平台要求登录或验证；保留页面，后续自动复核登录状态
  connected      登录检测通过
  busy           发布动作进行中（结束回 connected / error）
  error          最后一次动作失败（error 字段带人话原因，可重试）

浏览器生命周期：每平台一个持久化 profile（~/.codebee/publish_profiles/<id>，
仓库外——GB 级浏览器运行时数据不进仓库目录），登录态落在 profile 里。
服务重启后按 state.json 记住的调试端口 attach 旧实例；实例已死才重新
launch——用户登录一次，之后无感。

与 bookmeta.generate_async 同款线程纪律：动作起后台线程即返回，前端靠
view()（SSE/轮询）看进度；线程内任何异常都落终态，绝不悬挂。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit

from .. import operations, paths, quality_gate
from . import fanqie, flow, ledger, qimao
from .browser import Browser, BrowserError, Page

PLATFORMS = {"fanqie": fanqie, "qimao": qimao}
LOGIN_WAIT_S = 15 * 60          # 扫码等待窗口
STATUS_FILE = paths.PUBLISH_DIR / "state.json"
# A login/challenge can outlive the interactive connect request (or the
# service can be restarted while the tab is still open).  Keep a small,
# passive watchdog so a completed verification is noticed without requiring
# another click on “连接平台”.  It never navigates a non-empty challenge tab
# and never attempts to solve credentials or CAPTCHA controls.
AUTH_WATCH_INTERVAL_S = 30.0
# Optional, already-issued browser auth state for unattended deployments.
# The default directory is outside the repository and is intentionally not
# created by the app.  A state file is a secret-bearing artifact: callers
# must provision it explicitly (or point CODEBEE_PUBLISH_AUTH_STATE at it).
AUTH_STATE_DIR_ENV = "CODEBEE_PUBLISH_AUTH_DIR"
AUTH_STATE_FILE_ENV = "CODEBEE_PUBLISH_AUTH_STATE"
AUTH_STATE_DEFAULT_DIR = Path.home() / ".codebee" / "publish_auth"

LOCK = threading.RLock()
AUTOMATIC_RELEASE_REASON = (
    "用户已启用全自动发布；系统按平台校准流程自动提交，"
    "质量门禁放行已写入发布台账"
)


def automatic_release_kwargs(auto_submit, force=False, force_confirmed=False,
                             force_reason="", as_draft=False):
    """Return explicit release override flags without granting auto bypasses.

    An unattended schedule requests the workflow; it does not replace task
    contract approval or prove that content passed review. A forced release
    remains an explicit operator action and must carry its audited confirmation
    and reason through unchanged.
    """
    return {"force": bool(force),
            "force_confirmed": bool(force_confirmed),
            "force_reason": str(force_reason or "")}
_state = {"platforms": {}}      # {plat: {status, port, at, error, last_login, last_action}}
_browsers = {}                  # plat → Browser（进程内缓存，alive 校验兜底）
_AUTH_WATCH_LOCK = threading.Lock()
_AUTH_WATCH_STOP = threading.Event()
_AUTH_WATCH_THREAD = None
_LOGIN_WAITERS_GUARD = threading.Lock()
_LOGIN_WAITERS = set()
_LOGIN_CANCEL_EVENTS = {}
_CREATE_BOOK_RECOVERY_GUARD = threading.Lock()
_CREATE_BOOK_RECOVERY = set()
_UPLOAD_RECOVERY_GUARD = threading.Lock()
_UPLOAD_RECOVERY = set()
_RECOVERY_CANCEL_EPOCH = {}
_AUTH_STATE_CACHE = {}
_AUTH_STATE_CACHE_LOCK = threading.Lock()
_AUTH_PROBE_CONNECTIONS = {}
_AUTH_PROBE_CONNECTIONS_LOCK = threading.Lock()

# A platform account slot owns one browser profile.  The persisted ``busy``
# flag is useful for the UI, but it is not an atomic reservation: two HTTP
# requests can both observe ``connected`` before either worker sets ``busy``.
# Keep an in-process single-flight lock per slot so create/upload/probe/sync
# cannot navigate the same profile concurrently, while different accounts
# retain independent locks and can run in parallel.
_ACTION_LOCKS = {}
_ACTION_LOCKS_GUARD = threading.Lock()
_LOGIN_CHALLENGE_MARKS = (
    "安全验证", "安全校验", "人机验证", "验证码", "请完成验证",
    "访问过于频繁", "异常请求", "拖动滑块", "captcha", "verify you are human",
)
# Some providers render the actual challenge in a cross-origin iframe. In
# that case the top-level document can expose neither the iframe text nor a
# useful title, while its URL still identifies the verification route.
_LOGIN_CHALLENGE_URL_MARKS = (
    "captcha", "challenge", "verify", "verification", "slider", "human-check",
    "security-check", "secverify", "geetest", "hcaptcha", "recaptcha",
)


def _auth_state_path(slot):
    """Return the optional auth-state path for a platform account slot.

    ``CODEBEE_PUBLISH_AUTH_STATE`` may name one JSON file or a directory.  A
    ``{slot}`` placeholder is expanded for multi-account deployments.  The
    default is ``~/.codebee/publish_auth/<slot>.json`` so browser cookies and
    storage state never enter the repository or its backups.
    """
    raw = str(os.environ.get(AUTH_STATE_FILE_ENV) or "").strip()
    if raw:
        raw = raw.replace("{slot}", str(slot))
        p = Path(raw).expanduser()
        if p.is_dir() or raw.endswith(("/", "\\")):
            return p / (str(slot) + ".json")
        return p
    base = str(os.environ.get(AUTH_STATE_DIR_ENV) or "").strip()
    return (Path(base).expanduser() if base else AUTH_STATE_DEFAULT_DIR) / (
        str(slot) + ".json")


def _platform_host(plat):
    try:
        host = (urlsplit(str(PLATFORMS[plat].CONFIG.get("home") or ""))
                .hostname or "")
    except Exception:
        host = ""
    return host.lower().lstrip(".")


def _platform_origin(plat):
    host = _platform_host(plat)
    return ("https://" + host) if host else ""


def _host_allowed_for_platform(plat, host):
    base = _platform_host(plat)
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
            cookie["domain"] = _platform_host(plat)
            host = cookie["domain"]
        if not _host_allowed_for_platform(plat, host):
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
                parsed.password or not _host_allowed_for_platform(plat, host)):
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
    path = _auth_state_path(slot)
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
    with _AUTH_STATE_CACHE_LOCK:
        cached = _AUTH_STATE_CACHE.get(slot)
    raw = (cached or {}).get("scripts") if isinstance(cached, dict) else None
    if not isinstance(raw, dict):
        raw = _st(slot).get("auth_scripts")
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
                     _st(slot).get("auth_script_target_id") or "")
        script_id = str((cached or {}).get("script_id") or
                        _st(slot).get("auth_script_id") or "")
        if target and script_id:
            entries[target] = {
                "script_id": script_id,
                "storage_keys": ((cached or {}).get("storage_keys") or
                                  _st(slot).get("auth_storage_keys") or {}),
            }
    return entries


def _auth_page_for_target(slot, target, current_page):
    if str(getattr(current_page, "_target_id", "") or "") == str(target):
        return current_page
    browser = _browsers.get(slot)
    finder = getattr(browser, "page_for_target", None) if browser else None
    if callable(finder):
        try:
            return finder(target)
        except Exception:
            return None
    return None


def _remove_auth_scripts(slot, page, entries):
    """Remove imported scripts and keys from every still-live target."""
    for target, item in (entries or {}).items():
        target_page = _auth_page_for_target(slot, target, page)
        if target_page is None:
            continue
        try:
            target_page.remove_init_script(item.get("script_id") or "")
        except Exception:
            pass
        keys = item.get("storage_keys") or {}
        if keys:
            try:
                target_page.evaluate(_auth_storage_script({}, keys))
            except Exception:
                pass


def _seed_auth_state(plat, slot, page):
    """Inject a provisioned auth state into this page, if one is configured.

    Only cookies/localStorage belonging to the platform's own host (including
    its subdomains) are accepted.  Invalid or expired state is harmless: the
    regular login detector records ``waiting_login`` and keeps its recovery
    behavior.  Cookie values are never logged.  A target->script map is kept
    so rotating state while a challenge uses multiple tabs cannot leave an
    old init script behind on a tab that is revisited later.
    """
    state, path, fingerprint = _read_auth_state(slot)
    target = str(getattr(page, "_target_id", "") or "")
    entries = _auth_script_entries(slot)
    if state is None or fingerprint is None:
        _remove_auth_scripts(slot, page, entries)
        with _AUTH_STATE_CACHE_LOCK:
            _AUTH_STATE_CACHE.pop(slot, None)
        _set(slot, auth_script_id="", auth_script_target_id="",
             auth_storage_keys={}, auth_scripts={}, auth_state_fingerprint="")
        return False
    cache_key = (str(path), fingerprint, target)
    with _AUTH_STATE_CACHE_LOCK:
        cached = _AUTH_STATE_CACHE.get(slot)
    if isinstance(cached, dict) and cached.get("cache_key") == cache_key:
        # Older state files did not persist the fingerprint.  The in-process
        # cache still proves this exact payload was imported; backfill the
        # durable marker once so the auth watchdog does not retry forever.
        if (_st(slot).get("auth_state_fingerprint") != list(fingerprint)
                or _st(slot).get("auth_scripts") != entries):
            current = entries.get(target) or {}
            _set(slot, auth_script_id=current.get("script_id", ""),
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
    _remove_auth_scripts(slot, page, entries)
    try:
        cookies = _auth_cookie_items(plat, state)
        setter = getattr(page, "set_cookies", None)
        if cookies and callable(setter):
            setter(cookies)
        origins = _auth_storage_origins(plat, state)
        # A supplied state with cookies but no localStorage must also clear a
        # previously imported token when an old init script survives a
        # browser/page restart.  The current platform origin is the only
        # fallback target; unrelated site storage is never touched.
        if not origins and isinstance(state, dict) and state.get("cookies"):
            origin = _platform_origin(plat)
            if origin:
                origins = {origin: {}}
        storage_keys = {origin: list(values.keys())
                        for origin, values in origins.items()}
        script_id = ""
        if origins:
            script = _auth_storage_script(origins, old_keys)
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
                    page.evaluate(_auth_storage_script(
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
    with _AUTH_STATE_CACHE_LOCK:
        _AUTH_STATE_CACHE[slot] = {"cache_key": cache_key,
                                   "scripts": new_entries}
    _set(slot, auth_script_id=script_id,
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
    _state_data, _path, fingerprint = _read_auth_state(slot)
    if fingerprint is None:
        return False
    previous = _st(slot).get("auth_state_fingerprint")
    try:
        previous = tuple(int(value) for value in previous)
    except (TypeError, ValueError):
        previous = ()
    return tuple(fingerprint) != previous


def _action_lock(slot):
    key = str(slot)
    with _ACTION_LOCKS_GUARD:
        return _ACTION_LOCKS.setdefault(key, threading.Lock())


def _try_action_lock(slot):
    lock = _action_lock(slot)
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
    with LOCK:
        try:
            d = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("platforms"), dict):
                _state = d
                return
        except Exception:
            pass
        _state = {"platforms": {}}


def _save():
    with LOCK:
        try:
            paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(STATUS_FILE)
        except Exception:
            pass


def _set(plat, **kv):
    with LOCK:
        ent = _state["platforms"].setdefault(plat, {})
        ent.update(kv)
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save()


def _st(plat):
    return (_state.get("platforms") or {}).get(plat) or {}


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
        platform_version=ledger.book_for(task.get("id"), plat) or {},
        force=force, force_confirmed=force_confirmed, force_reason=force_reason,
        serial=bool(task.get("serial")) or
        str(task.get("type") or "").lower() == "serial_novel",
        current_manuscript_fingerprint=current_fingerprint)
    decision["version_snapshot"] = version_snapshot
    decision["platform_snapshot"] = ledger.book_for(task.get("id"), plat) or {}
    if verdict:
        decision["reviewed_through"] = verdict.get("end_chapter") or 0
        decision["quality_status"] = verdict.get("quality_status") or "continue_only"
        decision["review_run_id"] = run.get("id")
    return decision


def view():
    """前端状态视图：平台状态 + 是否找到浏览器 + 作品登记 + 账号列表。"""
    from .browser import find_browser
    out = {}
    for pid, mod in PLATFORMS.items():
        s = _st(pid)
        out[pid] = {"label": mod.CONFIG["label"], "status": s.get("status") or "none",
                    "at": s.get("at") or "", "error": s.get("error") or "",
                    "last_action": s.get("last_action") or "",
                    "profile": str(_profile_dir(pid)),
                    "accounts": []}
    # 多账号：账号条目带各自状态槽（slot=fanqie@a2）；老前端忽略此字段不受影响
    for a in load_accounts():
        pid = a.get("platform")
        if pid not in out:
            continue
        slot = "%s@%s" % (pid, a.get("id"))
        st = _st(slot)
        out[pid]["accounts"].append({
            "id": a.get("id"), "label": a.get("label") or "",
            "status": st.get("status") or "none",
            "at": st.get("at") or "", "error": st.get("error") or "",
            "last_action": st.get("last_action") or "",
            "profile": str(_profile_dir(slot))})
    return {"platforms": out, "browser_found": bool(find_browser())}


def recover_orphans():
    """启动收尸：busy 的工作线程随重启中断，改判 error。

    waiting_login 保留：验证页面仍可能在浏览器中，启动会话恢复与持久批次
    会被动复核它；改成 error 会让恢复代码重新导航并打断已有验证。

    升级自愈：旧版「等扫码窗口一关就冤判超时」留下的 error（error 文案带
    「等待登录超时」）由 resume_saved_sessions 统一重开并复核。不要在这里
    单独起复核线程，否则它会与启动恢复同时导航同一账号的浏览器。"""
    _load()
    n = 0
    # 多账号：状态槽含 fanqie@a2 形态，全量扫（基础平台也在其中）
    for slot in list((_state.get("platforms") or {}).keys()) + list(PLATFORMS):
        s = _st(slot)
        if s.get("status") == "busy":
            _set(slot, status="error", error="上次操作随服务重启中断，请重试")
            n += 1
    return n


def saved_session_slots():
    """Return publishing profiles whose persisted login may be reusable.

    A configured book is enough to resume its profile even when an earlier
    process died before the platform login state was written.  Existing CDP
    ports and last-login timestamps also recover standalone platform sessions.
    """
    slots = set()
    for slot, state in (_state.get("platforms") or {}).items():
        base = str(slot).split("@", 1)[0]
        if base in PLATFORMS and (state.get("port") or state.get("last_login")):
            slots.add(str(slot))
    try:
        books = ledger.load_books() or {}
    except Exception:
        books = {}
    for platform_books in books.values():
        if not isinstance(platform_books, dict):
            continue
        for plat, book in platform_books.items():
            if plat not in PLATFORMS or not isinstance(book, dict):
                continue
            try:
                slots.add(slot_of(plat, book.get("account") or ""))
            except ValueError:
                continue
    return sorted(slots, key=lambda value: (
        value.split("@", 1)[0], value.split("@", 1)[1] if "@" in value else ""))


def resume_saved_sessions():
    """Reopen known publishing profiles and detect reusable login cookies.

    This removes the manual reconnect step after a service restart.  A fresh
    login, QR scan, or platform challenge still follows the normal waiting
    login state and cannot be bypassed by this recovery path.
    """
    slots = saved_session_slots()
    for slot in slots:
        plat, sep, account = slot.partition("@")
        thread = threading.Thread(
            target=connect, kwargs={"plat": plat,
                                    "account": account if sep else "",
                                    "unattended": True},
            daemon=True, name="pub-resume-%s" % slot)
        thread.start()
    return len(slots)


def _auth_watch_once():
    """Passively recover waiting login slots that became valid.

    The interactive ``connect`` waiter is intentionally finite so a request
    thread cannot live forever.  A platform challenge, however, may remain
    open for hours.  This one-shot pass is safe to call from a daemon loop:
    each profile has the same action lock as publishing, and
    ``_connect_unattended`` preserves active challenge tabs.
    """
    _load()
    _resume_pending_create_recoveries()
    _resume_pending_upload_recoveries()
    slots = []
    for slot, state in (_state.get("platforms") or {}).items():
        if not isinstance(state, dict) or base_plat(slot) not in PLATFORMS:
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
              and (_authentication_required(base_plat(slot), state.get("error"))
                   or _auth_state_changed(str(slot)))):
            slots.append(str(slot))
    recovered = 0
    for slot in slots:
        # All paths acquire the per-slot action lock before inspecting or
        # registering a login waiter. This keeps the lock order stable and
        # prevents an interactive connect from racing this probe.
        lock = _try_action_lock(slot)
        if lock is None:
            continue
        try:
            with _LOGIN_WAITERS_GUARD:
                if slot in _LOGIN_WAITERS:
                    # The interactive connect request is already passively
                    # checking this profile; do not run a second probe.
                    continue
            plat, sep, account = slot.partition("@")
            ok, _error = _connect_unattended(
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
    _load()
    slots = [str(slot) for slot, state in
             (_state.get("platforms") or {}).items()
             if isinstance(state, dict)
             and state.get("status") == "connected"
             and base_plat(slot) in PLATFORMS]
    changed = 0
    for slot in slots:
        lock = _try_action_lock(slot)
        if lock is None:
            continue
        try:
            with _LOGIN_WAITERS_GUARD:
                if slot in _LOGIN_WAITERS:
                    continue
            browser = _browsers.get(slot)
            if browser is None or not browser.alive():
                continue
            target_id = str(_st(slot).get("auth_target_id") or "").strip()
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
            ok, detail, _checked_page = _probe_login_pages(
                slot, browser, page)
            if (not ok and _authentication_required(plat, detail)
                    and _challenge_page_is_relevant(plat, _checked_page)):
                _set(
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
    while not _AUTH_WATCH_STOP.is_set():
        try:
            _auth_watch_once()
            _connected_session_probe_once()
        except Exception:
            pass
        _AUTH_WATCH_STOP.wait(AUTH_WATCH_INTERVAL_S)


def start_auth_watchdog():
    """Start the process-wide passive login recovery loop once.

    This closes the gap after the 15-minute interactive waiter expires: once
    the user or platform finishes an already-open verification, pending
    durable batches resume on their own and idle sessions become ``connected``
    without a second UI action.
    """
    global _AUTH_WATCH_THREAD
    with _AUTH_WATCH_LOCK:
        if (_AUTH_WATCH_THREAD is not None and
                _AUTH_WATCH_THREAD.is_alive()):
            return False
        _AUTH_WATCH_STOP.clear()
        _AUTH_WATCH_THREAD = threading.Thread(
            target=_auth_watch_loop, daemon=True, name="pub-auth-watchdog")
        _AUTH_WATCH_THREAD.start()
        return True


def stop_auth_watchdog():
    """Stop the passive watchdog (primarily for clean shutdown and tests)."""
    global _AUTH_WATCH_THREAD
    with _AUTH_WATCH_LOCK:
        _AUTH_WATCH_STOP.set()
        thread = _AUTH_WATCH_THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=max(1.0, AUTH_WATCH_INTERVAL_S + 1.0))
    with _AUTH_WATCH_LOCK:
        # Keep the reference while a slow probe is still unwinding.  A new
        # start then cannot clear the stop event and create a second watcher.
        if _AUTH_WATCH_THREAD is thread and (thread is None or
                                             not thread.is_alive()):
            _AUTH_WATCH_THREAD = None


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
    legacy = _P(paths.PUBLISH_DIR) / "profiles"
    if not legacy.is_dir():
        return
    base = _profiles_base()
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
    return _profiles_base() / plat


# ---------------------------------------------------------------- 多账号
# 账号=独立浏览器 profile（~/.codebee/publish_profiles/<plat>@<acc>）+ 独立
# 状态槽（state.json platforms 键 = slot）。默认账号槽名就是平台名（老
# 安装零迁移）；书在哪台账号建的（books.json entry.account），发章/校准
# 就自动路由到那台——同平台双账号可并行操作（不同 profile 不同浏览器）。
ACCOUNTS_FILE = paths.PUBLISH_DIR / "accounts.json"


def base_plat(slot):
    """槽名（fanqie@a2）→ 基础平台名（fanqie）。"""
    return str(slot).split("@", 1)[0]


def slot_of(plat, account=""):
    """基础平台 + 账号 → 槽名。空账号=默认槽（=平台名，老数据零迁移）。"""
    acc = str(account or "").strip()
    if not acc:
        return str(plat)
    if plat not in PLATFORMS:
        raise ValueError("未知平台：%s" % plat)
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_-]{1,16}$", acc):
        raise ValueError("账号 ID 只能是字母/数字/下划线/连字符，长度 1-16")
    return "%s@%s" % (plat, acc)


def load_accounts():
    try:
        d = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        items = d.get("accounts") if isinstance(d, dict) else d
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _save_accounts(items):
    with LOCK:
        paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ACCOUNTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"accounts": items}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(ACCOUNTS_FILE)


def list_accounts(plat=None):
    out = [dict(a) for a in load_accounts()
           if not plat or a.get("platform") == plat]
    out.sort(key=lambda a: (a.get("platform") or "", a.get("id") or ""))
    return out


def add_account(plat, acc_id, label=""):
    if plat not in PLATFORMS:
        return None, "未知平台：%s" % plat
    try:
        slot_of(plat, acc_id)
    except ValueError as e:
        return None, str(e)
    with LOCK:
        items = load_accounts()
        if any(a.get("platform") == plat and a.get("id") == acc_id for a in items):
            return None, "该账号已存在"
        items.append({"platform": plat, "id": acc_id,
                      "label": str(label or "")[:30],
                      "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        _save_accounts(items)
    return {"platform": plat, "id": acc_id, "label": str(label or "")[:30]}, ""


def remove_account(plat, acc_id):
    """删账号登记（不动 profile 目录与线上作品）。有书绑在该账号时拒绝——
    先解绑/换绑，防发章路由悬空。"""
    with LOCK:
        items = load_accounts()
        hit = next((a for a in items
                    if a.get("platform") == plat and a.get("id") == acc_id), None)
        if not hit:
            return False, "账号不存在"
        books = ledger.load_books()
        for _tid, plats in books.items():
            ent = (plats or {}).get(plat)
            if ent and str(ent.get("account") or "") == acc_id:
                return False, ("仍有作品绑定在该账号（%s），请先解绑"
                               % (ent.get("title") or ""))
        _save_accounts([a for a in items if a is not hit])
        slot = "%s@%s" % (plat, acc_id)
        _state["platforms"].pop(slot, None)
        _browsers.pop(slot, None)
        _save()
    return True, ""


def _ensure_browser(plat):
    """attach-or-launch：进程内缓存 → 记住的端口 → 全新 launch。"""
    with LOCK:
        b = _browsers.get(plat)
        if b and b.alive():
            return b
        port = _st(plat).get("port") or 0
        if port:
            try:
                b = Browser.attach(int(port))
                _browsers[plat] = b
                return b
            except BrowserError:
                pass                        # 旧实例已死：走 launch
        b = Browser(_profile_dir(plat))     # 有窗口：用户要扫码/人工确认
        _browsers[plat] = b
        _set(plat, port=b.port)
        return b


def _open_page(plat, preserve_tabs=False):
    b = _ensure_browser(plat)
    if preserve_tabs:
        # A platform challenge may live in a tab other than the first one.
        # Reuse the persisted target when it is still alive; falling back to
        # the first page keeps compatibility with old state files and mocks.
        target_id = str(_st(plat).get("auth_target_id") or "").strip()
        page_for_target = getattr(b, "page_for_target", None)
        if target_id and callable(page_for_target):
            try:
                page = page_for_target(target_id)
                if page is not None:
                    _seed_auth_state(base_plat(plat), plat, page)
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
            if (_st(plat).get("auth_script_target_id") and
                    _st(plat).get("auth_script_target_id") != retained_target):
                with _AUTH_STATE_CACHE_LOCK:
                    _AUTH_STATE_CACHE.pop(plat, None)
                _set(plat, auth_script_id="", auth_script_target_id="",
                     auth_storage_keys={})
        except Exception:
            pass
    page = b.first_page(create=True)
    _seed_auth_state(base_plat(plat), plat, page)
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
        _set(plat, auth_target_id=target_id)


def _clear_auth_page(plat):
    """Forget a challenge tab after the saved session becomes valid."""
    if _st(plat).get("auth_target_id"):
        _set(plat, auth_target_id="")


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
            pages.append(Page(ws_url))
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
    with _AUTH_PROBE_CONNECTIONS_LOCK:
        previous = _AUTH_PROBE_CONNECTIONS.pop(str(plat), [])
    for old_page in previous:
        closer = getattr(old_page, "close_connection", None)
        if callable(closer):
            closer()
        else:
            try:
                old_page.ws.close()
            except Exception:
                pass
    pages = _live_pages(browser, primary)
    temporary = [candidate for candidate in pages if candidate is not primary]
    with _AUTH_PROBE_CONNECTIONS_LOCK:
        _AUTH_PROBE_CONNECTIONS[str(plat)] = temporary
    successes = []
    last_detail = ""
    for page in pages:
        try:
            ok, detail = _check_login(plat, page, navigate=False)
        except Exception as exc:
            ok, detail = False, str(exc)
        last_detail = detail or last_detail
        if ok:
            successes.append((page, detail))
        elif _authentication_required(plat, detail):
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
    mod = PLATFORMS[base_plat(plat)]
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
    body_challenges = [mark for mark in _LOGIN_CHALLENGE_MARKS
                       if mark.lower() in body_text]
    route_hint_for_body = any(mark.lower() in route_for_body
                              for mark in _LOGIN_CHALLENGE_URL_MARKS)
    strong_overlay_hint = any(phrase in body_text for phrase in (
        "请完成安全验证", "请完成人机验证", "拖动滑块",
        "verify you are human"))
    challenge = body_challenges[0] if body_challenges else ""
    if challenge and (not same_platform_for_body or route_hint_for_body
                      or len(body_challenges) >= 2 or strong_overlay_hint):
        _remember_auth_page(plat, page)
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
                             for mark in _LOGIN_CHALLENGE_URL_MARKS))
    except ValueError:
        challenge_url = False
    if challenge_url:
        _remember_auth_page(plat, page)
        return False, "平台要求安全验证：挑战页 URL"
    if url.startswith("chrome-error://") or url.startswith("about:"):
        return False, url                      # 页面没打开：网络/域名问题，不是登录态
    if any(m in url for m in mod.CONFIG["login_url_marks"]):
        _remember_auth_page(plat, page)
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
    _clear_auth_page(plat)
    return True, url


def _authentication_required(plat, detail):
    text = str(detail or "").lower()
    return any(mark.lower() in text for mark in (
        *PLATFORMS[base_plat(plat)].CONFIG["login_url_marks"],
        *_LOGIN_CHALLENGE_MARKS))


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
        if any(mark.lower() in current for mark in _LOGIN_CHALLENGE_URL_MARKS):
            return True
        home = urlsplit(PLATFORMS[base_plat(plat)].CONFIG["home"])
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
    for fp in (paths.PUBLISH_DIR / ("flows-%s.json" % plat),
               Path(__file__).with_name("flows-%s-calibrated.json" % plat)):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            # A previous Windows install wrote the user override after a
            # UTF-8/GBK conversion and persisted U+FFFD (�) inside every
            # selector.  The JSON still parses and passed the structural
            # contract check, but the browser could never find labels such as
            # “新建小说” or “章节名称”.  Treat that file as corrupt and fall
            # through to the packaged, real-device-calibrated baseline.
            if not _flow_text_clean(data):
                continue
            if isinstance(data, dict) and isinstance(data.get(action), list):
                steps = data[action]
                # A stale user override must not silently downgrade a
                # calibrated safety flow to "fill and pretend".  Keep the
                # override precedence only when it contains the actions that
                # prove the remote operation was actually accepted.
                if _flow_contract_ok(plat, action, steps):
                    return steps
        except Exception:
            pass
    # A timed-publish flow changes a consequential platform state.  Never
    # synthesize one from the immediate-publish flow: selector guesses can
    # silently fall through optional steps and be recorded as queued.  A
    # platform must ship an explicit, calibrated upload_chapter_scheduled
    # table (or a user override) before this recovery path is available.
    return PLATFORMS[plat].FLOWS[action]


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
    if plat not in PLATFORMS:
        return False, "未知平台"
    try:
        slot = slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    if unattended:
        action_lock = _try_action_lock(slot)
        if action_lock is None:
            return False, "该平台账号浏览器正在执行其他操作，自动恢复稍后重试"
        try:
            return _connect_unattended(plat, account, slot)
        finally:
            action_lock.release()

    # Register before touching the browser so the passive watchdog cannot
    # begin a second probe in the small window between navigation and waiter
    # thread creation. Acquire the action lock first to keep the lock order
    # identical to both watchdog probes.
    registration_lock = _try_action_lock(slot)
    if registration_lock is None:
        return False, "该平台账号浏览器正在执行其他操作，请稍后重试"
    cancel_event = threading.Event()
    waiter_started = False
    try:
        with _LOGIN_WAITERS_GUARD:
            if slot in _LOGIN_WAITERS:
                return True, ""
            _LOGIN_WAITERS.add(slot)
            _LOGIN_CANCEL_EVENTS[slot] = cancel_event
        waiting = _st(slot).get("status") == "waiting_login"
        # Keep the action lock through browser setup and waiter registration.
        # Without this, a concurrent disconnect/upload can close or navigate
        # the profile between _open_page() and the durable waiting_login mark.
        b, page = _open_page(slot, preserve_tabs=waiting)
        if cancel_event.is_set():
            return False, "登录连接已取消"
        if not waiting or _page_is_empty(page):
            page.navigate(PLATFORMS[plat].CONFIG["home"])
        if cancel_event.is_set():
            return False, "登录连接已取消"
        _set(slot, status="waiting_login", error="")
        acc = str(account or "").strip()

        def wait_login():
            try:
                deadline = time.time() + LOGIN_WAIT_S
                while time.time() < deadline:
                    if cancel_event.is_set():
                        return
                    try:
                        ok, _u, checked_page = _probe_login_pages(slot, b, page)
                        if ok and not cancel_event.is_set():
                            _clear_auth_page(slot)
                            _set(slot, status="connected",
                                 last_login=time.strftime("%m-%d %H:%M"))
                            ledger.record(plat, "connect", ok=True, account=acc)
                            return
                    except (BrowserError, Exception):
                        pass                # 页面被用户关掉等：继续等到超时
                    time.sleep(5)
                if not cancel_event.is_set():
                    _finalize_login_wait(slot, plat, acc,
                                         cancel_event=cancel_event)
            finally:
                with _LOGIN_WAITERS_GUARD:
                    if _LOGIN_CANCEL_EVENTS.get(slot) is cancel_event:
                        _LOGIN_CANCEL_EVENTS.pop(slot, None)
                        _LOGIN_WAITERS.discard(slot)

        threading.Thread(target=wait_login, daemon=True,
                         name="pub-login-%s" % slot).start()
        waiter_started = True
        return True, ""
    except BrowserError as e:
        _set(slot, status="error", error=str(e))
        return False, str(e)
    except Exception as exc:
        error = "无法打开平台作者后台：%s" % str(exc)[:160]
        _set(slot, status="error", error=error)
        ledger.record(plat, "connect", ok=False, account=str(account or "").strip(),
                      error=error[:300])
        return False, error
    finally:
        if not waiter_started:
            with _LOGIN_WAITERS_GUARD:
                if _LOGIN_CANCEL_EVENTS.get(slot) is cancel_event:
                    _LOGIN_CANCEL_EVENTS.pop(slot, None)
                    _LOGIN_WAITERS.discard(slot)
        registration_lock.release()


def _connect_unattended(plat, account, slot):
    """Verify a saved session while holding the profile's action lock."""
    acc = str(account or "").strip()
    try:
        _b, page = _open_page(slot, preserve_tabs=True)
        # A login/challenge can span several retries. Observe its current page
        # without refreshing it, so recovery never invalidates an in-progress
        # verification. Once it clears, the normal batch reconciles and resumes.
        status = _st(slot).get("status")
        ok, url, checked_page = _probe_login_pages(slot, _b, page)
        auth_required = (not ok and _authentication_required(plat, url))
        if (not ok and (not auth_required or _page_is_empty(page)
                        or status in (None, ""))
                and (status != "waiting_login" or _page_is_empty(page))
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
            ok, url = _check_login(slot, page, navigate=True)
            auth_required = (not ok and _authentication_required(plat, url))
        elif not ok and not auth_required and status != "waiting_login":
            ok, url = _check_login(slot, page, navigate=True)
    except Exception as exc:
        ok, url = False, str(exc)
    if ok:
        _clear_auth_page(slot)
        _set(slot, status="connected",
             last_login=time.strftime("%m-%d %H:%M"), error="")
        ledger.record(plat, "connect", ok=True, account=acc)
        return True, ""
    waiting = _authentication_required(plat, url)
    error = ("平台等待登录或安全验证，验证完成后自动重试：%s" if waiting else
             "保存的平台登录态不可用，自动发布未等待人工登录：%s") % str(url or "unknown")[:160]
    _set(slot, status="waiting_login" if waiting else "error", error=error)
    ledger.record(plat, "connect", ok=False, account=acc, error=error[:300])
    return False, error


def _finalize_login_wait(slot, plat=None, acc="", cancel_event=None):
    """等扫码超时后的终审：复用 profile 复核，验证未完成时保留页面。

    waiting_login 不再强制导航或改判超时；持久批次会继续被动检查。
    返回 True 表示复核通过（已判 connected）。
    slot 兼容旧调用（只传 plat）：平台名即默认槽。"""
    if cancel_event is not None and cancel_event.is_set():
        return False
    if plat is None:
        plat = base_plat(slot)
    login_state = ""
    probe_error = ""
    try:
        _b, page = _open_page(slot, preserve_tabs=True)
        ok, login_state, checked_page = _probe_login_pages(slot, _b, page)
    except Exception as exc:
        ok = False
        probe_error = str(exc)[:160]
    if cancel_event is not None and cancel_event.is_set():
        return False
    if ok:
        _clear_auth_page(slot)
        _set(slot, status="connected", last_login=time.strftime("%m-%d %H:%M"))
        ledger.record(plat, "connect", ok=True, account=acc)
    elif _authentication_required(plat, login_state):
        _set(slot, status="waiting_login",
             error="平台等待登录或安全验证，验证完成后自动重试：%s" % str(login_state)[:160])
    elif probe_error and (_st(slot).get("auth_target_id") or
                          _st(slot).get("status") == "waiting_login"):
        _set(slot, status="waiting_login",
             error="登录页面暂时不可用，认证看门狗将自动重试：%s" % probe_error)
    else:
        _set(slot, status="error", error="等待登录超时（15 分钟），请重新点连接")
    return ok


def disconnect(plat, account=""):
    try:
        slot = slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False
    try:
        with _LOGIN_WAITERS_GUARD:
            cancel_event = _LOGIN_CANCEL_EVENTS.get(slot)
            if cancel_event is not None:
                cancel_event.set()
                _LOGIN_CANCEL_EVENTS.pop(slot, None)
                _LOGIN_WAITERS.discard(slot)
        b = _browsers.pop(slot, None)
        if b:
            b.close()
        with _AUTH_STATE_CACHE_LOCK:
            _AUTH_STATE_CACHE.pop(slot, None)
        # An explicit disconnect is a user cancellation. Do not let the
        # watchdog interpret stale durable intents as permission to reopen the
        # browser and resume publishing after the user has signed out.
        with LOCK:
            state = _state["platforms"].get(slot)
            _RECOVERY_CANCEL_EPOCH[slot] = int(
                _RECOVERY_CANCEL_EPOCH.get(slot, 0)) + 1
            if isinstance(state, dict):
                state.pop("pending_creates", None)
                state.pop("pending_create", None)
                state.pop("pending_uploads", None)
                state["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _save()
        with _CREATE_BOOK_RECOVERY_GUARD:
            _CREATE_BOOK_RECOVERY.difference_update(
                key for key in _CREATE_BOOK_RECOVERY if key[1] == slot)
        with _UPLOAD_RECOVERY_GUARD:
            _UPLOAD_RECOVERY.difference_update(
                key for key in _UPLOAD_RECOVERY if key[0] == str(slot))
        _set(slot, status="none", error="", auth_script_id="",
             auth_script_target_id="")
        return True
    finally:
        action_lock.release()


# ---------------------------------------------------------------- 动作：探测
def probe_form_async(plat, account=""):
    """「探测表单」：跑 probe 流程把页面真实可交互元素 dump 进台账 log。"""
    if plat not in PLATFORMS:
        return False, "未知平台"
    try:
        slot = slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if _st(slot).get("status") not in ("connected", "error"):
        action_lock.release()
        return False, "请先连接并登录平台"
    _set(slot, status="busy", last_action="probe", error="")

    logs = []

    def run():
        try:
            b, page = _open_page(slot)
            flow.run_flow(page, load_flow(plat, "probe_form"),
                          values={}, config=PLATFORMS[plat].CONFIG,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, "probe", n)),
                          log=logs.append)
            _set(slot, status="connected")
            ledger.record(plat, "probe", ok=True, account=str(account or "").strip(),
                          error="\n".join(logs)[:2000])
        except Exception as e:
            _set(slot, status="error", error="探测失败：%s" % e)
            ledger.record(plat, "probe", ok=False, error=str(e)[:300],
                          account=str(account or "").strip())
        finally:
            _save()
            action_lock.release()

    threading.Thread(target=run, daemon=True, name="pub-probe-%s" % plat).start()
    return True, ""


# ---------------------------------------------------------------- 动作：建书
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
        v = str(v or "").strip()[:_TITLE_MAX]
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
                % (base, _ALT_COUNT + 2, str(data.get("summary") or "")[:300]))
            res = modelhub.chat(prov["id"], model, prompt,
                                max_tokens=600, timeout=_ALT_TIMEOUT)
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
                        t = str(x or "").strip().replace("\n", " ")[:_TITLE_MAX]
                        if len(t) >= 2 and t != base and t not in out:
                            out.append(t)
            if out:
                logs.append("候选书名（编排者 %s）：%s"
                            % (model, "、".join(out[:5])))
    except Exception as e:
        logs.append("候选书名生成降级（%s）" % str(e)[:100])
    for v in _mech_titles(base):
        if v not in out:
            out.append(v)
    return out[:_ALT_COUNT + 2]


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
    slot = slot_of(plat, account)
    kwargs = dict(kwargs or {})
    current_epoch = int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))
    expected_epoch = kwargs.get("_recovery_epoch")
    if (expected_epoch is not None and
            int(expected_epoch) != current_epoch):
        return False
    key = (str(task_id), str(slot))
    with _CREATE_BOOK_RECOVERY_GUARD:
        if key in _CREATE_BOOK_RECOVERY:
            # The original request is already waiting for the same profile;
            # treat a duplicate click as accepted instead of making the user
            # retry manually.
            return True
        _CREATE_BOOK_RECOVERY.add(key)

    options = dict(kwargs or {})
    options.pop("_recovery_epoch", None)
    pending = {
        "task_id": str(task_id),
        "account": str(account or ""),
        "force": bool(options.get("force")),
        "force_confirmed": bool(options.get("force_confirmed")),
        "force_reason": str(options.get("force_reason") or ""),
    }
    _remember_pending_create(slot, pending)

    def recover():
        def latest_create_error():
            """Return a failed receipt for this create attempt, if any."""
            state = _st(slot)
            if state.get("error"):
                return str(state.get("error"))
            try:
                for receipt in ledger.recent(task_id=task_id, platform=plat,
                                             limit=20):
                    if (receipt.get("action") == "create_book" and
                            receipt.get("ok") is False):
                        return str(receipt.get("error") or "建书失败")
            except Exception:
                pass
            return "建书失败"

        def retryable_create_error(error):
            text = str(error or "").lower()
            if _authentication_required(plat, error):
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
            deadline = time.monotonic() + LOGIN_WAIT_S
            waiting_for_auth = False
            while time.monotonic() < deadline:
                if int(_RECOVERY_CANCEL_EPOCH.get(slot, 0)) != current_epoch:
                    return
                # ``disconnect`` removes this marker to cancel a live waiter.
                # Check it before reconnecting so an explicit sign-out cannot
                # be undone by the recovery thread already in flight.
                with LOCK:
                    state_marker = (_state.get("platforms") or {}).get(slot) or {}
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
                state = _st(slot)
                if state.get("status") == "connected":
                    waiting_for_auth = False
                    ok, error = create_book_async(
                        task_id, plat, auto_submit=True, account=account,
                        _recovery=True, _recovery_epoch=current_epoch,
                        **options)
                    if not ok:
                        if not retryable_create_error(error):
                            _forget_pending_create(slot, task_id)
                            return
                    else:
                        # create_book_async starts a worker and returns before
                        # the platform receipt exists. Keep pending_create
                        # until the ledger confirms a book ID, otherwise a
                        # later browser failure would strand this request.
                        while time.monotonic() < deadline:
                            try:
                                book = ledger.book_for(task_id, plat)
                            except Exception:
                                book = None
                            if isinstance(book, dict) and str(
                                    book.get("book_id") or "").strip():
                                _forget_pending_create(slot, task_id)
                                return
                            current = _st(slot)
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
                            _forget_pending_create(slot, task_id)
                            return
                    if not retryable_create_error(error):
                        _forget_pending_create(slot, task_id)
                        return
                elif waiting_for_auth:
                    # The existing browser page is waiting for the user or
                    # platform challenge to finish. Reconnecting here can
                    # reopen the same login flow on every poll.
                    time.sleep(5.0)
                    continue
                else:
                    ok, error = connect(plat, account=account, unattended=True)
                    if ok:
                        continue
                    # A challenge/login state is expected to remain open. Any
                    # other terminal browser/configuration error should be
                    # surfaced by the normal status state instead of looping.
                    current = _st(slot)
                    if not (_authentication_required(plat, error) or
                            current.get("status") == "waiting_login" or
                            retryable_create_error(error)):
                        _forget_pending_create(slot, task_id)
                        return
                    waiting_for_auth = (
                        _authentication_required(plat, error) or
                        current.get("status") == "waiting_login")
                time.sleep(5.0)
        finally:
            with _CREATE_BOOK_RECOVERY_GUARD:
                _CREATE_BOOK_RECOVERY.discard(key)

    try:
        threading.Thread(target=recover, daemon=True,
                         name="pub-create-recover-%s" % slot).start()
    except Exception:
        # Keep the durable request for the watchdog/startup pass, but do not
        # let a failed thread creation permanently suppress a later retry.
        with _CREATE_BOOK_RECOVERY_GUARD:
            _CREATE_BOOK_RECOVERY.discard(key)
        raise
    return True


def _remember_pending_create(slot, pending):
    """Persist one pending create without overwriting sibling tasks."""
    task_id = str((pending or {}).get("task_id") or "")
    if not task_id:
        return
    with LOCK:
        ent = _state["platforms"].setdefault(slot, {})
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
        _save()


def _forget_pending_create(slot, task_id):
    """Remove only one task's durable create marker."""
    task_id = str(task_id or "")
    with LOCK:
        ent = _state["platforms"].get(slot)
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
        _save()


def _upload_retryable_error(plat, error):
    """Whether a failed one-off upload can safely wait for session recovery."""
    text = str(error or "").lower()
    if _authentication_required(plat, error):
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
    key = _upload_pending_key(pending)
    if not key or not str((pending or {}).get("task_id") or ""):
        return ""
    with LOCK:
        ent = _state["platforms"].setdefault(slot, {})
        entries = ent.get("pending_uploads")
        if not isinstance(entries, dict):
            entries = {}
        entries[key] = dict(pending)
        ent["pending_uploads"] = entries
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save()
    return key


def _forget_pending_upload(slot, key):
    """Remove only the completed direct chapter request."""
    key = str(key or "")
    if not key:
        return
    with LOCK:
        ent = _state["platforms"].get(slot)
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
            _save()


def _upload_receipt_exists(pending):
    """Check local receipts before retrying an ambiguous browser failure."""
    task_id = str((pending or {}).get("task_id") or "")
    plat = str((pending or {}).get("platform") or "")
    chapter_no = int((pending or {}).get("chapter_no") or 0)
    if not task_id or not plat or chapter_no <= 0:
        return False
    readers = [ledger.published_chapters, ledger.scheduled_chapters,
               ledger.review_chapters]
    if pending.get("as_draft"):
        readers.append(ledger.drafted_chapters)
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
        ok, error = sync_published(task_id, plat, manual=False)
    except Exception as exc:
        ok, error = False, str(exc)
    if ok:
        if _upload_receipt_exists(pending):
            return True
        # Chapter lists can be eventually consistent immediately after a
        # submit. Require two empty snapshots before deciding it is safe to
        # click submit again.
        time.sleep(1.0)
        try:
            ok_again, error_again = sync_published(task_id, plat, manual=False)
        except Exception as exc:
            ok_again, error_again = False, str(exc)
        if ok_again:
            return not _upload_receipt_exists(pending)
        error = error_again
    if _upload_retryable_error(plat, error):
        return None
    return False


def _queue_upload_recovery(task_id, plat, chapter_file, account, options,
                           chapter_no, title, error=""):
    """Durably retry a direct upload after login/browser recovery."""
    slot = slot_of(plat, account)
    options = dict(options or {})
    current_epoch = int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))
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
    key = _upload_pending_key(pending)
    guard_key = (str(slot), key)
    with _UPLOAD_RECOVERY_GUARD:
        already_running = guard_key in _UPLOAD_RECOVERY
        if not already_running:
            _UPLOAD_RECOVERY.add(guard_key)
    _remember_pending_upload(slot, pending)
    if already_running:
        return True

    def recover():
        try:
            deadline = time.monotonic() + LOGIN_WAIT_S
            while time.monotonic() < deadline:
                if int(_RECOVERY_CANCEL_EPOCH.get(slot, 0)) != current_epoch:
                    return
                with LOCK:
                    state_marker = (_state.get("platforms") or {}).get(slot) or {}
                entries_marker = state_marker.get("pending_uploads")
                if (not isinstance(entries_marker, dict) or
                        key not in entries_marker):
                    return
                if _upload_receipt_exists(pending):
                    _forget_pending_upload(slot, key)
                    return
                reconciled = _reconcile_upload_before_retry(pending)
                if reconciled is True:
                    _forget_pending_upload(slot, key)
                    return
                if reconciled is None:
                    time.sleep(5.0)
                    continue
                if int(_RECOVERY_CANCEL_EPOCH.get(slot, 0)) != current_epoch:
                    return
                state = _st(slot)
                if state.get("status") == "busy":
                    time.sleep(2.0)
                    continue
                ok, why = upload_chapter_async(
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
                    if not _upload_retryable_error(plat, why):
                        _forget_pending_upload(slot, key)
                        return
                    time.sleep(5.0)
                    continue
                # The worker is asynchronous. Wait for its receipt or for a
                # recoverable error before starting another attempt.
                for _ in range(60):
                    if _upload_receipt_exists(pending):
                        _forget_pending_upload(slot, key)
                        return
                    current = _st(slot)
                    if current.get("status") == "busy":
                        time.sleep(1.0)
                        continue
                    if current.get("status") == "waiting_login":
                        break
                    if current.get("status") == "error":
                        latest = str(current.get("error") or "")
                        if not _upload_retryable_error(plat, latest):
                            _forget_pending_upload(slot, key)
                            return
                        pending["last_error"] = latest[:300]
                        break
                    time.sleep(1.0)
                time.sleep(3.0)
        finally:
            with _UPLOAD_RECOVERY_GUARD:
                _UPLOAD_RECOVERY.discard(guard_key)

    try:
        threading.Thread(target=recover, daemon=True,
                         name="pub-upload-recover-%s" % slot).start()
    except Exception:
        with _UPLOAD_RECOVERY_GUARD:
            _UPLOAD_RECOVERY.discard(guard_key)
        raise
    return True


def _resume_pending_upload_recoveries():
    """Recreate in-memory waiters for direct uploads after a restart."""
    _load()
    queued = 0
    for slot, state in list((_state.get("platforms") or {}).items()):
        if not isinstance(state, dict):
            continue
        entries = state.get("pending_uploads")
        if not isinstance(entries, dict):
            continue
        plat, sep, account = str(slot).partition("@")
        if plat not in PLATFORMS:
            continue
        for pending in list(entries.values()):
            if not isinstance(pending, dict) or not pending.get("task_id"):
                continue
            pending_account = str(pending.get("account") or
                                  (account if sep else ""))
            try:
                queued += int(bool(_queue_upload_recovery(
                    pending.get("task_id"), plat,
                    pending.get("chapter_file") or "", pending_account,
                    pending, pending.get("chapter_no") or 0,
                    pending.get("title") or "", pending.get("last_error") or "")))
            except Exception:
                pass
    return queued


def _resume_pending_create_recoveries():
    """Recreate in-memory waiters for durable standalone book requests."""
    _load()
    queued = 0
    for slot, state in list((_state.get("platforms") or {}).items()):
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
        if plat not in PLATFORMS:
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
                queued += int(bool(_queue_create_book_auth_recovery(
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
    if plat not in PLATFORMS:
        return False, "未知平台"
    # Creating a platform work is part of the unattended publishing contract.
    # Keep the legacy argument for callers that still send it, but never let a
    # false value restore the old fill-and-wait-for-human-submit flow.
    auto_submit = True
    try:
        slot = slot_of(plat, account)
    except ValueError as e:
        return False, str(e)
    acc = str(account or "").strip()
    request_epoch = (int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))
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
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    existing = ledger.book_for(task_id, plat)
    # A title-only manual registration is usable as a binding. For legacy
    # entries without source metadata, only a recorded failed create-book
    # attempt is safe to retry; otherwise it may be a real manual binding.
    if existing:
        if existing.get("book_id") or existing.get("source") == "manual":
            return False, "该任务已在此平台登记过作品，请直接发章"
        failed_create = any(
            r.get("action") == "create_book" and not r.get("ok")
            for r in ledger.recent(task_id=task_id, platform=plat, limit=50))
        if not failed_create:
            return False, "该任务已有作品登记但未确认，请登记已有作品或先在平台核对"
    why = _create_preflight(plat, meta["data"])
    if why:
        return False, why
    release_flags = automatic_release_kwargs(
        auto_submit, force=force, force_confirmed=force_confirmed,
        force_reason=force_reason)
    quality = _quality_release_guard(task, plat, action="publish",
                                     **release_flags)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    quality_forced = bool(quality.get("forced") or release_flags.get("force"))
    quality_reason = str(release_flags.get("force_reason") or "")
    # Reserve the account profile before the login recheck.  The persisted
    # status check above is only advisory; this lock closes the check-then-set
    # race when two works share one account.
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if (_recovery and
            request_epoch != int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))):
        action_lock.release()
        return False, "平台恢复任务已被主动断开取消"
    if _st(slot).get("status") == "busy":
        action_lock.release()
        return False, "该平台有操作正在进行中"
    ok_login, why = _login_guard(slot)
    if not ok_login:
        if not _batch and _upload_retryable_error(plat, why):
            try:
                queued = _queue_create_book_auth_recovery(
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
    _set(slot, status="busy", last_action="create_book", error="")

    data = meta["data"]
    mod = PLATFORMS[plat]
    logs = []
    book_name = (data.get("book_name") or "").strip()
    try:
        operation_id = operations.begin(
            "publish:%s:create_book" % plat,
            {"task_id": task_id, "title": book_name}, task_id=task_id,
            metadata={"platform": plat, "action": "create_book"})
    except Exception as exc:
        _set(slot, status="error", error="建书操作登记失败：%s" % exc)
        action_lock.release()
        return False, "建书操作登记失败：%s" % exc

    def run():
        started_at = time.monotonic()

        def elapsed_s():
            return round(max(0.0, time.monotonic() - started_at), 3)

        final_name = book_name
        try:
            b, page = _open_page(slot)
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
            reconcile = getattr(mod, "reconcile_book_id", None)
            if not callable(reconcile):
                raise RuntimeError("平台不支持建书前可靠对账，已停止以避免重复建书")
            found, book_id, detail = reconcile(page, {"title": book_name})
            if not found and detail:
                raise RuntimeError("建书前作品对账失败：%s" % detail)
            if book_id:
                logs.append("建书前按作品名找到已有作品，自动复用 book_id=%s" %
                            book_id)
            i = 0
            while (not book_id and i < len(names) and
                   i < 1 + _ALT_COUNT + 2):
                name = names[i]
                i += 1
                final_name = name
                if i > 1:
                    logs.append("第 %d 次尝试，书名《%s》" % (i, name))
                values = mod.values_create_book(dict(data, book_name=name))
                steps = _with_tag_steps(load_flow(plat, "create_book"),
                                        mod.tag_groups(data), values, mod)
                try:
                    flow.run_flow(page, steps, values=values, config=mod.CONFIG,
                                  auto_submit=auto_submit, dup_check=auto_submit,
                                  shot=lambda n: page.screenshot(
                                      ledger.shot_path(plat, task_id, n)),
                                  log=logs.append)
                except flow.TitleDupError as e:
                    # 事前预检命中：平台实时校验已喊重名，点提交必被拒
                    logs.append(str(e))
                    bid = _resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id, final_name = bid, name
                        logs.append("同名书就在本账号下，已复用 book_id=%s" % bid)
                        break
                    if not cands_ready:
                        names = names + _alt_titles(plat, data, task, logs)
                        cands_ready = True
                    if i >= len(names):
                        break               # 候选没准备出来（模型+机械全空）
                    continue
                # book_id：创建成功后平台跳书籍详情/编辑器，从 URL 提取
                # （番茄 book-info/<id>；七猫 information?id=<id>）。url_any 过了
                # 但页面还在跳转链上时再等几轮；提取落空把最终 URL 记进日志，
                # 别再静默登记空 id（发章只能兜底找书，直达编辑器就废了）。
                try:
                    for _try in range(6):
                        m_url = re.search(r"book-info/(\d+)|information\?id=(\d+)",
                                          str(page.url() or ""))
                        if m_url:
                            book_id = m_url.group(1) or m_url.group(2)
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
                    hint = flow.dup_hint(page)
                    if hint:
                        break
                    time.sleep(1.0)
                if hint:
                    logs.append("页面提示：%s" % hint[:120])
                    bid = _resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id, final_name = bid, name
                        logs.append("同名书就在本账号下，已复用 book_id=%s" % bid)
                        break
                    if not cands_ready:
                        names = names + _alt_titles(plat, data, task, logs)
                        cands_ready = True
                    continue
                # 非撞名失败：按书名对账兜底（原有逻辑），找不回就明说未建成
                bid = _resolve_book_id(plat, page, {"title": name})
                if bid:
                    book_id = bid
                    logs.append("book_id 提取落空，已按书名在平台对账找回 %s" % bid)
                break
            if book_id and final_name != book_name:
                # 换名成功（或复用同名旧书）：书名全链路同步到新名
                _sync_renamed_book(task_id, plat, task, data, final_name, logs)
            operation_status = "confirmed" if book_id else "unknown"
            if book_id:
                operations.confirm(operation_id, remote_receipt=book_id,
                                   metadata={"platform": plat, "action": "create_book"})
            else:
                operations.mark_unknown(
                    operation_id,
                    "建书流程走完，但平台作品列表按书名未找到《%s》，书很可能未建成；"
                    "请到平台作品管理确认，若已存在可人工登记" % final_name,
                    metadata={"platform": plat, "action": "create_book"})
            ledger.record(plat, "create_book", task_id=task_id, title=final_name,
                          book_id=book_id, ok=bool(book_id),
                          error=((("" if book_id else
                                   "建书流程走完但未取得 book_id，且平台按书名未找到"
                                   "《%s》，疑似未建成\n" % final_name)
                                  + "\n".join(logs))[:2000] or None),
                          shot=str(ledger.shot_path(plat, task_id, "")),
                          operation_id=operation_id, operation_status=operation_status,
                          remote_receipt=book_id, duration_s=elapsed_s())
            if book_id:
                # 未取得 id 不落台账：空条目会让界面亮「已建书」、校准盲跑，
                # 而发章/下次建书各自有按书名对账的兜底，空条目只剩害处。
                ledger.save_book(task_id, plat, {"book_id": book_id, "title": final_name,
                                                 "source": "create"},
                                  account=acc)
            _set(slot, status="connected", error="")
        except Exception as e:
            operations.finish_exception(operation_id, e)
            operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
            _set(slot, status="error", error="建书失败：%s" % e)
            ledger.record(plat, "create_book", task_id=task_id, title=book_name,
                          ok=False, error=str(e)[:300],
                          shot=str(ledger.shot_path(plat, task_id, "")),
                          operation_id=operation_id, operation_status=operation_status,
                          duration_s=elapsed_s())
            if not _batch and _upload_retryable_error(plat, e):
                try:
                    _queue_create_book_auth_recovery(
                        task_id, plat, acc,
                        {"force": force,
                         "force_confirmed": force_confirmed,
                         "force_reason": force_reason})
                except Exception:
                    pass
        finally:
            _save()
            action_lock.release()

    try:
        threading.Thread(target=run, daemon=True,
                         name="pub-book-%s" % plat).start()
    except Exception as exc:
        error = "建书任务启动失败，可自动重试：%s" % exc
        action_lock.release()
        _set(slot, status="connected", error=error)
        if not _batch and _upload_retryable_error(plat, exc):
            try:
                _queue_create_book_auth_recovery(
                    task_id, plat, acc,
                    {"force": force,
                     "force_confirmed": force_confirmed,
                     "force_reason": force_reason})
            except Exception:
                pass
        return False, error
    return True, ""


# ---------------------------------------------------------------- 动作：登记已有作品
def register_book(task_id, plat, title, book_id="", account=None):
    """人工登记平台已有作品：建书流程没走通、或用户纯手工在平台上建的
    书，补进台账让卡片翻到「已建书·发一章」，避免再点「创建作品」造出
    重复书。只动本地台账不碰浏览器；发章按书名找书，title 必填，
    book_id 选填（有直达 URL 时填）。已登记会覆盖更新（兼纠错口）。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    if account is not None:
        try:
            slot_of(plat, account)
        except ValueError as e:
            return False, str(e)
    title = (title or "").strip()
    if not title:
        return False, "作品名必填（发章按作品名在平台找书）"
    book_id = str(book_id or "").strip()
    valid_id = getattr(PLATFORMS[plat], "book_id_valid", None)
    if book_id and callable(valid_id) and not valid_id(book_id):
        return False, ("作品 ID 格式不对；番茄请填章节管理页 URL 中的完整长 ID，"
                       "不要填短作品号")
    # 换绑另一本书（book_id/title 变了）时旧书的校准数作废，防张冠李戴
    old = ledger.book_for(task_id, plat) or {}
    if old and (old.get("book_id") != str(book_id or "").strip()
                or old.get("title") != title[:120]):
        ledger.update_book(task_id, plat, remote_total=None, remote_published=None,
                           remote_scheduled=None, remote_review=None,
                           remote_rejected=None,
                           remote_synced_at=None, remote_error=None,
                           remote_published_nos=None, remote_scheduled_nos=None,
                           remote_review_nos=None,
                           remote_last_publish_at=None, remote_last_schedule_at=None,
                           confirmed_published_through=None,
                           confirmed_published_at=None)
    ledger.save_book(task_id, plat, {"book_id": book_id,
                                     "title": title[:120], "source": "manual"},
                     account=account)
    return True, ""


def unbind_book(task_id, plat):
    """解除本地作品绑定：删 books.json 里沿链命中的平台登记条目。
    只动本地台账不碰浏览器/平台——线上书还在不在由用户先核实，这里
    一律清登记；清掉后卡片回「创建作品」态，可重建或改绑别的书。
    平台忙时拒改：在飞的建书/发章链收尾会 save_book 写回，边删边写
    会把绑定又立回来。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    book = ledger.book_for(task_id, plat) or {}
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if _st(slot).get("status") == "busy":
        action_lock.release()
        return False, "该平台有操作正在进行中"
    removed = ledger.remove_book(task_id, plat)
    action_lock.release()
    if not removed:
        return False, "该平台没有已登记的作品绑定"
    return True, ""


def _with_tag_steps(steps, groups, values, mod=None):
    """标签走数据驱动：清单进 values["_tags"]（[组名, 标签] 对），由 flow 的
    "tags" 步骤按组切换点选。组显示名映射来自**对应平台模块**的
    TAG_GROUP_LABELS（曾硬编码 qimao——番茄任务 key 撞名被套上七猫组名，
    插桩 text 变 list repr）。兼容旧流程表（无 tags 步骤时插桩）；流程表
    已用 {tag_x} 占位（番茄式 click_real）时不插桩——占位由 values 自答。"""
    flat = []
    labels = getattr(mod, "TAG_GROUP_LABELS", {}) if mod else {}
    for key, tags in groups or []:
        grp = labels.get(key, "")
        flat.extend([grp, str(t)] if grp and str(t).strip() else str(t)
                    for t in tags if str(t).strip())
    if not flat:
        return steps
    if any(st.get("do") == "tags" for st in steps):
        values["_tags"] = flat
        return steps
    if any("{tag_" in str(st.get("text") or "") for st in steps if st.get("text")):
        return steps                  # 流程表自带标签占位（番茄式）：values 已就绪
    tag_steps = [{"do": "click_text", "text": str(t), "contains": False,
                  "scope": "[class*=tag] li,span,label,[class*=label]"}
                 for t in flat]
    out, inserted = list(steps), False
    for i, st in enumerate(out):
        if st.get("do") in ("submit", "shot") and not inserted:
            out[i:i] = tag_steps
            inserted = True
    if not inserted:
        out += tag_steps
    return out


def _login_guard(plat):
    """动作前的登录前置，现场复核浏览器页面，避免 stale connected。"""
    try:
        _b, page = _open_page(plat, preserve_tabs=True)
        status = _st(plat).get("status")
        ok, url, checked_page = _probe_login_pages(plat, _b, page)
        auth_required = (not ok and _authentication_required(plat, url))
        if (not ok and (not auth_required or _page_is_empty(page)
                        or status in (None, ""))
                and (status != "waiting_login" or _page_is_empty(page))
                and checked_page is page):
            # Re-open an empty recovered tab once, including when the prior
            # challenge marker has disappeared. Keep non-empty challenge tabs
            # untouched while waiting for platform verification.
            ok, url = _check_login(plat, page, navigate=True)
            auth_required = (not ok and _authentication_required(plat, url))
        elif not ok and not auth_required and status != "waiting_login":
            ok, url = _check_login(plat, page, navigate=True)
        if ok:
            _set(plat, status="connected")
            return True, ""
        if _authentication_required(plat, url):
            error = "平台等待登录或安全验证，验证完成后自动重试：%s" % url[:160]
            _set(plat, status="waiting_login", error=error)
            return False, error
        return False, "平台未登录（当前页面 %s），请先点「连接平台」扫码" % url[:80]
    except BrowserError as e:
        return False, "浏览器不可用：%s" % e


def _url_values(mod, book):
    """平台模块提供的 URL 占位值（chapter_manage_url/draft_url/editor_url），
    逐键取、缺哪个跳哪个。曾按序连赋 + except AttributeError 兜底：draft_url
    只有七猫有，番茄走到第二行即断，editor_url 永远赋不上——{editor_url}
    残进 navigate，页面停在 about:blank 误报「未登录或改版」（0924 三连败）。"""
    out = {}
    for key in ("chapter_manage_url", "draft_url", "editor_url"):
        fn = getattr(mod, key, None)
        if callable(fn):
            out[key] = fn(book)
    return out


def _resolve_book_id(plat, page, book):
    """登记缺 book_id 时按书名在作家后台找回（平台模块可选提供
    resolve_book_id）。找不回返回空串：流程按无 id 兜底路径继续，让后续
    步骤如实报错，绝不在这里拦死。找回后回写 books.json，下次直达。"""
    fn = getattr(PLATFORMS[plat], "resolve_book_id", None)
    if not callable(fn) or str((book or {}).get("book_id") or "").strip():
        return ""
    try:
        return str(fn(page, book) or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- 动作：发章
# 裸标题行（中文数字章稿常态）：行首「第三十一章 绕开的路线」，无 # 前缀。
# 认不出会把标题行留进正文、标题回退成文件名 chapter-31——番茄编辑器
# 序号/标题分开填，两头全输（2026-10-08 实案）。
_BARE_HEAD_RE = re.compile(
    r"^\s*第\s*[0-9０-９一二三四五六七八九十百千零两]+\s*[章节回]")


def split_chapter_name(title):
    """「第三十一章 绕开的路线」→「绕开的路线」（番茄标题位不带章号前缀，
    序号单独填）。没有章号前缀、或前缀后没有正题 → 原样返回。"""
    title = str(title or "")
    m = ledger._CHAPTER_RE.search(title)
    if not m:
        return title
    rest = title[m.end():].strip(" ：:，,、.·-—－_　")
    return rest or title


def chapter_fill_values(ch_no, title, body, book_name) -> Dict[str, Any]:
    """发章流程的 values 字典。chapter_title=完整标题行（台账留痕/七猫流程
    锚点口径不变）；chapter_no=阿拉伯数字串（番茄「第 _ 章」序号框只认数字，
    2026-10-08 实案：流程有 fill 步骤但 values 从没给过这个键，序号永远
    空着被平台以「章节序号只支持阿拉伯数字」打回）；chapter_name=拆掉章号
    前缀的正题（番茄标题位）。章号解析不出给空串，fill 步骤按空值跳过。"""
    return {"chapter_title": str(title or ""),
            "chapter_scope": str(title or ""),
            "chapter_name": split_chapter_name(title),
            "chapter_no": str(int(ch_no)) if ch_no else "",
            "chapter_body": str(body or ""),
            "book_name": str(book_name or "")}


def read_chapter(fp):
    """读章节文件 → (章号, 标题, 正文, 错误)。UTF-8→GBK 回退（章稿乱码教训）。

    标题识别三档：markdown「# 」标题行 → 裸「第X章 标题」首行 → 文件名。
    标题行不进正文（平台编辑器序号/标题单独填，正文再带一遍读者会看到重复）。
    章号从标题/文件名的「第X章」解析，解析不出记 0（台账不按章号幂等，
    只按标题留痕）。"""
    from pathlib import Path
    from .. import runner
    p = Path(fp)
    if not p.is_file():
        return 0, "", "", "章节文件不存在：%s" % p
    try:
        text = runner.read_text_any_enc(p)
    except Exception as e:
        return 0, "", "", "读章节文件失败：%s" % e
    lines = text.strip().splitlines()
    title = ""
    body_start = 0
    for i, ln in enumerate(lines[:5]):
        s = ln.strip()
        if s.startswith("#"):                 # markdown 标题行
            s = s.lstrip("#").strip()
            if s:
                title, body_start = s, i + 1
                break
    if not title:
        for i, ln in enumerate(lines[:5]):    # 裸「第X章 …」首行
            s = ln.strip()
            if _BARE_HEAD_RE.match(s):
                title, body_start = s, i + 1
                break
    if not title:
        title = p.stem
    body = "\n".join(lines[body_start:]).strip()
    ch_no = ledger.parse_chapter_no(title) or ledger.parse_chapter_no(p.name)
    return ch_no, title, body, ""


def upload_chapter_async(task_id, plat, chapter_file, auto_submit=True,
                         force=False, force_confirmed=False, force_reason="",
                         as_draft=False, scheduled=False, direct_today=False,
                         _recovery=False, _batch=False, _recovery_epoch=None):
    """把一章自动发布到平台，或通过 as_draft 只保存草稿。

    幂等：已成功发布**或已存草稿**的章号拒绝重做——重复发会平台上出双章，
    重复存草稿会攒一箱重复稿。as_draft=True 走「存草稿」流程表
    （upload_chapter_draft）：内容落平台草稿箱不上线，不构成发布动作，
    质量闸不拦（发布时照拦）；存稿动作无需人工确认，适合批量连跑。"""
    from .. import contracts, store
    if plat not in PLATFORMS:
        return False, "未知平台"
    # Direct platform publication is always unattended. Keep accepting the
    # legacy argument so older clients remain compatible, but never let it
    # turn a single-chapter upload into a filled-form/manual-submit workflow.
    if not as_draft:
        auto_submit = True
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    if not as_draft and not contracts.publish_allowed(task_id, unattended=True):
        return False, "任务需要先完成审批，才能发布产物"
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务尚未在此平台建书，请先「创建作品」"
    if not book.get("book_id") and book.get("source") != "manual":
        return False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
    # 多账号路由：书在哪个账号建的（books entry.account），这章就发到
    # 那个账号的浏览器/状态槽——默认账号（无 account 字段）= 平台默认槽。
    acc = str(book.get("account") or "").strip()
    slot = slot_of(plat, acc)
    request_epoch = (int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))
                     if _recovery_epoch is None else int(_recovery_epoch))
    if (_recovery and
            request_epoch != int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))):
        return False, "平台恢复任务已被主动断开取消"
    # 闭包中需要在找回 book_id 后更新书籍引用；使用独立副本避免
    # Python 将 book 误判为 run() 的局部变量，导致前置解析触发
    # UnboundLocalError。
    book_ref = dict(book)
    valid_id = getattr(PLATFORMS[plat], "book_id_valid", None)
    if (book_ref.get("book_id") and callable(valid_id)
            and not valid_id(book_ref.get("book_id"))):
        # Legacy records sometimes stored the short public work number. It
        # opens an empty chapter page, so resolve the canonical id by title.
        book_ref["book_id"] = ""
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    if as_draft:
        # 草稿与别的自动发布批次互斥（2026-10-10 实案：连载发章占着浏览器，
        # 草稿流 fill 被掐断连接）——单章草稿同样不让起
        from . import auto as _auto
        other = _auto.other_batch_running(task_id, plat, slot)
        if other:
            return False, ("检测到另一批自动发布进行中（任务 %s），平台浏览器会"
                           "被互踩；等它跑完再存草稿" % other)
    ch_no, title, body, err = read_chapter(chapter_file)
    if err:
        return False, err
    release_flags = {}
    if as_draft:
        # 草稿不上线：质量闸不适用（evaluate_release action=draft 直通），
        # 人工复核挪到「平台草稿箱 → 用户逐章检查后提交」那一步。
        quality = {"allowed": True}
    else:
        release_flags = automatic_release_kwargs(
            auto_submit, force=force, force_confirmed=force_confirmed,
            force_reason=force_reason)
        quality = _quality_release_guard(
            task, plat, action="publish", target_chapter=ch_no or None,
            **release_flags)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    # Keep the audit fields available to both the worker success path and its
    # exception path.  Previously they were only initialized in create_book,
    # so a failed Qimao upload masked the real flow error with NameError and
    # left the platform state harder to diagnose.
    quality_forced = bool(quality.get("forced") or release_flags.get("force"))
    quality_reason = str(release_flags.get("force_reason") or "")
    n_chars = len(body.replace("\n", "").replace(" ", ""))
    if n_chars < 100:
        return False, "正文过短（%d 字），疑似未完成章节" % n_chars
    # 平台级下限（如七猫编辑器明示「最少 1000 字」：不足时发布被静默拦截）
    min_chars = int(PLATFORMS[plat].CONFIG.get("min_chapter_chars") or 100)
    if n_chars < min_chars:
        return False, ("正文 %d 字未达该平台下限（%d 字），补足后再发"
                       % (n_chars, min_chars))
    if n_chars > 30000:
        return False, "正文超长（%d 字），平台单章上限一般 2 万字" % n_chars
    done = ledger.published_chapters(task_id, plat)
    if ch_no and ch_no in done:
        return False, "第 %d 章已成功发布过（台账幂等拦截）；确需重发请手工处理" % ch_no
    queued = ledger.scheduled_chapters(task_id, plat)
    if ch_no and ch_no in queued:
        return False, ("第 %d 章已进入定时发布队列（台账幂等拦截）；请等待平台发布"
                       % ch_no)
    if as_draft and ch_no and ch_no in ledger.drafted_chapters(task_id, plat):
        return False, ("第 %d 章已存过草稿（台账幂等拦截）；请到平台草稿箱检查后"
                       "提交发布，确需重存请先在平台删旧稿" % ch_no)
    # Reserve the account profile before the login recheck and keep the lock
    # until the worker finishes.  A persisted ``busy`` flag alone is subject
    # to a check-then-set race when two works share one account.
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if _st(slot).get("status") == "busy":
        action_lock.release()
        return False, "该平台有操作正在进行中"
    ok_login, why = _login_guard(slot)
    if not ok_login:
        if not _batch and _upload_retryable_error(plat, why):
            try:
                queued = _queue_upload_recovery(
                    task_id, plat, chapter_file, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason, "as_draft": as_draft,
                     "scheduled": scheduled, "direct_today": direct_today,
                     "_recovery_epoch": request_epoch},
                    ch_no, title, why)
            except Exception as exc:
                action_lock.release()
                return False, "登录态恢复任务启动失败：%s" % str(exc)[:160]
            if queued:
                action_lock.release()
                return True, ""
        action_lock.release()
        return False, why
    flow_key = "upload_chapter_draft" if as_draft else "upload_chapter"
    # A scheduled fallback uses the same editor fill chain unless a platform
    # ships a dedicated calibrated flow.  The action is still recorded as
    # scheduled so idempotency and progress accounting advance correctly.
    flow_action = "upload_chapter_scheduled" if scheduled else flow_key
    if scheduled:
        try:
            load_flow(plat, flow_action)
            flow_key = flow_action
        except Exception:
            action_lock.release()
            return False, "该平台未配置定时发布流程"
    if scheduled:
        auto_submit = True
    _set(slot, status="busy", last_action=flow_key, error="")

    mod = PLATFORMS[plat]
    values = chapter_fill_values(ch_no, title, body,
                                 book_ref.get("title") or "")
    values["publish_date"] = ""
    values["publish_time"] = "00:00"
    # 今天直发：平台弹「建议定时/有定时待发」也点否拒绝（新书首发指令）
    values["force_direct"] = bool(direct_today)
    # When the platform already has a queued chapter, continue its cadence.
    # The flow may still fall back to tomorrow/00:00 when no local slot is
    # known (for example a queue created manually before CodeBee was used).
    try:
        prior_slot = ledger.latest_scheduled_slot(task_id, plat)
    except Exception:
        prior_slot = None
    if prior_slot:
        # The local slot is the last chapter already in the queue.  The next
        # chapter must start on the following day; using the same date makes
        # Fanqie's daily quota reject the submission even when a later slot is
        # available.  Keep the platform prefill as a fallback only when it is
        # newer than this calculated cadence.
        try:
            import datetime as _dt
            _next_day = (_dt.datetime.strptime(prior_slot["date"], "%Y-%m-%d")
                         + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            _next_day = prior_slot["date"]
        values["scheduled_date"] = _next_day
        values["scheduled_time"] = prior_slot["time"]
        values["publish_date"] = _next_day
        values["publish_time"] = prior_slot["time"]
    # Qimao renders the numeric prefix itself in the chapter editor.  Passing
    # the source heading ("第1章 标题") would therefore produce
    # "第1章 第1章 标题" on the platform; keep the full heading for audit
    # records, but fill only the semantic title in this platform form.
    if plat == "qimao" and values.get("chapter_name"):
        values["chapter_title"] = values["chapter_name"]
    # 分卷：按本地卷计划解析目标卷（无计划=空串，volume 步骤按空值跳过，
    # 保持平台默认卷）；_volumes 是平台卷表共享缓存，弹窗新建的卷由流程
    # 步骤追加进来，跑完回写 books.json 供后续章节免建。
    try:
        from . import volumes as _volumes
        values["volume_name"] = _volumes.name_for(task.get("workdir"), ch_no, task=task)
    except Exception:
        values["volume_name"] = ""
    values["_volumes"] = list(book_ref.get("volumes") or [])
    values.update(_url_values(mod, book_ref))   # verify/draft/editor 页 URL（流程占位）
    logs = []
    pending_key = ""
    if not _batch:
        pending_key = _remember_pending_upload(
            slot,
            {"task_id": task_id, "platform": plat, "account": acc,
             "chapter_file": str(chapter_file), "chapter_no": ch_no,
             "title": title, "auto_submit": True, "force": force,
             "force_confirmed": force_confirmed, "force_reason": force_reason,
             "as_draft": as_draft, "scheduled": scheduled,
             "direct_today": direct_today,
             "_recovery_epoch": request_epoch})
    try:
        operation_id = operations.begin(
            "publish:%s:upload_chapter" % plat,
            {"task_id": task_id, "chapter_no": ch_no, "title": title}, task_id=task_id,
            metadata={"platform": plat, "action": flow_key})
    except Exception as exc:
        _set(slot, status="error", error="发章操作登记失败：%s" % exc)
        action_lock.release()
        if not _batch and _upload_retryable_error(plat, exc):
            try:
                _queue_upload_recovery(
                    task_id, plat, chapter_file, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason, "as_draft": as_draft,
                     "scheduled": scheduled, "direct_today": direct_today,
                     "_recovery_epoch": request_epoch},
                    ch_no, title, exc)
            except Exception:
                pass
        return False, "发章操作登记失败：%s" % exc

    def run():
        started_at = time.monotonic()

        def elapsed_s():
            return round(max(0.0, time.monotonic() - started_at), 3)

        try:
            b, page = _open_page(slot)
            bid = _resolve_book_id(plat, page, book_ref)
            if bid:                          # 建书时没提上 id 的书，发章前补账
                book_ref.update(book_id=bid)
                values.update(_url_values(mod, book_ref))
                ledger.save_book(task_id, plat, book_ref)
                logs.append("已按书名找回 book_id=%s 并更新登记" % bid)
            elif not book_ref.get("book_id"):
                raise RuntimeError("未按作品名找到远端作品，未打开章节编辑器；"
                                   "请检查平台作品名或补填作品 ID")
            flow.run_flow(page, load_flow(plat, flow_key), values=values,
                          config=mod.CONFIG, auto_submit=auto_submit,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, task_id, n)),
                          log=logs.append)
            new_vols = values.get("_volumes")
            if isinstance(new_vols, list) and new_vols != (book_ref.get("volumes") or []):
                ledger.update_book(task_id, plat, volumes=new_vols)
            if as_draft:
                # 草稿已落平台草稿箱（流程含验证步，走到这=验证过）。发布确认权
                # 仍在用户：到平台草稿箱逐章检查后手工提交，质量闸在「发布」时
                # 照常把关。
                operations.confirm(operation_id, metadata={"platform": plat,
                                                           "action": "upload_chapter_draft",
                                                           "chapter_no": ch_no})
                ledger.record(plat, "upload_chapter_draft", task_id=task_id,
                              chapter_no=ch_no, book_id=book_ref.get("book_id") or "",
                              title=title, ok=True, operation_id=operation_id,
                              operation_status="confirmed",
                              remote_receipt="platform-draft", account=acc,
                              duration_s=elapsed_s())
            elif auto_submit:
                recorded_action = ("upload_chapter_scheduled"
                                    if scheduled or values.get("_scheduled_by_platform")
                                    else flow_key)
                operations.confirm(operation_id, metadata={"platform": plat,
                                                            "action": recorded_action,
                                                            "chapter_no": ch_no})
                ledger.record(plat, recorded_action, task_id=task_id, chapter_no=ch_no,
                              book_id=book_ref.get("book_id") or "", title=title, ok=True,
                              operation_id=operation_id, operation_status="confirmed",
                              scheduled_date=(str(values.get("scheduled_date") or "")
                                              if recorded_action == "upload_chapter_scheduled" else ""),
                              scheduled_time=(str(values.get("scheduled_time") or "")
                                              if recorded_action == "upload_chapter_scheduled" else ""),
                              quality_forced=quality_forced,
                              quality_reason=quality_reason, account=acc,
                              duration_s=elapsed_s())
            if pending_key:
                _forget_pending_upload(slot, pending_key)
            _set(slot, status="connected", error="")
        except Exception as e:
            operations.finish_exception(operation_id, e)
            operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
            _set(slot, status="error", error="发章失败：%s" % e)
            ledger.record(plat, flow_key, task_id=task_id, chapter_no=ch_no,
                          book_id=book_ref.get("book_id") or "", title=title,
                          ok=False, error=str(e)[:300], operation_id=operation_id,
                          operation_status=operation_status,
                          quality_forced=quality_forced,
                          quality_reason=quality_reason, account=acc,
                          duration_s=elapsed_s())
            if not _batch and _upload_retryable_error(plat, e):
                try:
                    _queue_upload_recovery(
                        task_id, plat, chapter_file, acc,
                        {"force": force, "force_confirmed": force_confirmed,
                         "force_reason": force_reason, "as_draft": as_draft,
                         "scheduled": scheduled, "direct_today": direct_today,
                         "_recovery_epoch": request_epoch},
                        ch_no, title, e)
                except Exception:
                    pass
            elif not _batch:
                _forget_pending_upload(slot, pending_key)
        finally:
            _save()
            action_lock.release()

    try:
        threading.Thread(target=run, daemon=True,
                         name="pub-ch-%s" % plat).start()
    except Exception as exc:
        error = "发章任务启动失败：%s" % str(exc)[:180]
        operations.finish_exception(operation_id, exc)
        operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
        _set(slot, status="connected", error=error)
        ledger.record(plat, flow_key, task_id=task_id, chapter_no=ch_no,
                      book_id=book_ref.get("book_id") or "", title=title,
                      ok=False, error=error[:300], operation_id=operation_id,
                      operation_status=operation_status,
                      quality_forced=quality_forced,
                      quality_reason=quality_reason, account=acc)
        _save()
        action_lock.release()
        if not _batch and _upload_retryable_error(plat, exc):
            try:
                _queue_upload_recovery(
                    task_id, plat, chapter_file, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason, "as_draft": as_draft,
                     "scheduled": scheduled, "direct_today": direct_today,
                     "_recovery_epoch": request_epoch},
                    ch_no, title, exc)
            except Exception:
                pass
        elif not _batch:
            _forget_pending_upload(slot, pending_key)
        return False, error
    return True, ""


def schedule_chapter_async(task_id, plat, chapter_file, force=False,
                           force_confirmed=False, force_reason=""):
    """Fill and submit one chapter through the platform's scheduled-publish path.

    This is the recovery path for an automatic immediate publish that did not
    produce a confirmed receipt.  It is deliberately separate from the normal
    upload action so the ledger and idempotency checks know the chapter is
    already queued on the platform.
    """
    # Fail before opening the browser when this platform has no explicit,
    # calibrated timed-publish flow.  This also prevents tests/mocks or a
    # generic immediate flow from being mistaken for a scheduling receipt.
    try:
        load_flow(plat, "upload_chapter_scheduled")
    except (KeyError, TypeError, ValueError, FileNotFoundError):
        return False, "该平台未配置定时发布流程"
    return upload_chapter_async(task_id, plat, chapter_file,
                                auto_submit=True, force=force,
                                force_confirmed=force_confirmed,
                                force_reason=force_reason,
                                scheduled=True)


def confirm_manual_chapter(task_id, plat, chapter_no):
    """Record an explicit user confirmation after submitting in the browser."""
    from .. import store
    from pathlib import Path
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    try:
        chapter_no = int(chapter_no or 0)
    except (TypeError, ValueError):
        chapter_no = 0
    if chapter_no <= 0:
        return False, "章号无效"
    if chapter_no in ledger.published_chapters(task_id, plat):
        return True, ""
    pending = next((r for r in ledger.recent(task_id=task_id, platform=plat, limit=100)
                    if r.get("action") == "upload_chapter_pending"
                    and int(r.get("chapter_no") or 0) == chapter_no), None)
    if not pending:
        # Auto-submit attempts do not leave an upload_chapter_pending row: the
        # worker records a failed upload instead.  If the user then submits
        # the already-filled platform dialog by hand, refusing the
        # confirmation leaves that chapter at the head of the queue forever
        # and the next run tries to publish it again.  Accept an explicit
        # manual confirmation only when the matching local chapter exists, so
        # a stray chapter number cannot be used to manufacture a receipt.
        task = store.get_task(task_id) or {}
        wd = str(task.get("workdir") or "").strip()
        candidate = None
        if wd and Path(wd).is_dir():
            direct = Path(wd) / ("chapter-%d.md" % chapter_no)
            if direct.is_file():
                candidate = direct
            else:
                for fp in Path(wd).rglob("*"):
                    if (fp.is_file() and fp.suffix.lower() in (".md", ".txt")
                            and ledger.parse_chapter_no(fp.name) == chapter_no):
                        candidate = fp
                        break
        if candidate is None:
            return False, "没有找到待确认的填稿记录或对应章节文件，请先重新填入该章"
        _no, title, _body, read_err = read_chapter(str(candidate))
        if read_err or _no != chapter_no:
            return False, "对应章节文件无法校验：%s" % (read_err or "章号不一致")
        book = ledger.book_for(task_id, plat) or {}
        if not book:
            # Older runtime/task snapshots can lose the serial-chain link even
            # though the failed publish rows still carry the authoritative
            # platform book id.  Reuse that id for an explicit user recovery;
            # never invent a binding from a bare chapter number.
            failed = next((r for r in ledger.recent(task_id=task_id,
                                                    platform=plat, limit=100)
                           if int(r.get("chapter_no") or 0) == chapter_no
                           and r.get("book_id")), None)
            if failed:
                book = {"book_id": failed.get("book_id"),
                        "title": failed.get("title") or title}
        if not book:
            return False, "该任务尚未登记此平台作品"
        op_id = operations.begin(
            "publish:%s:manual-confirm" % plat,
            {"task_id": task_id, "chapter_no": chapter_no, "title": title},
            task_id=task_id,
            metadata={"platform": plat, "action": "upload_chapter",
                      "manual": True})
        if not operations.confirm(op_id, remote_receipt="manual-confirmed",
                                  metadata={"platform": plat,
                                            "action": "upload_chapter",
                                            "chapter_no": chapter_no,
                                            "manual": True}):
            return False, "人工确认操作状态已变化，请稍后重试"
        ledger.record(plat, "upload_chapter", task_id=task_id,
                      chapter_no=chapter_no, book_id=book.get("book_id") or "",
                      title=title, ok=True, operation_id=op_id,
                      operation_status="confirmed",
                      remote_receipt="manual-confirmed")
        # There was no queued fill operation to resume.  Do not immediately
        # launch a new batch from this recovery path; the caller can inspect
        # the recalculated queue and explicitly start the next chapter.
        return True, "本章已登记为手工发布，后续章节未自动启动"
    op_id = pending.get("operation_id") or ""
    current = operations.get(op_id) if op_id else None
    if current and current.get("status") == "pending":
        ok = operations.confirm(op_id, remote_receipt="manual-confirmed",
                                metadata={"platform": plat, "action": "upload_chapter"})
    elif current:
        ok = operations.reconcile(op_id, "confirmed", remote_receipt="manual-confirmed",
                                  metadata={"platform": plat, "action": "upload_chapter"})
    else:
        ok = True
    if not ok:
        return False, "待确认操作状态已变化，请重新校准后再试"
    ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=chapter_no,
                  book_id=pending.get("book_id") or "", title=pending.get("title") or "",
                  ok=True, operation_id=op_id, operation_status="confirmed",
                  remote_receipt="manual-confirmed")
    # The browser confirmation is the hand-off point for a manual batch.  A
    # successful receipt must immediately resume the remaining chapters with
    # the fully automatic submit path; otherwise the UI appears stuck after
    # the user clicks "已发布".
    try:
        from . import auto
        started, reason = auto.publish_pending_async(task_id, plat,
                                                     auto_submit=True)
    except Exception as exc:
        started, reason = False, str(exc)
    if not started:
        # The chapter itself is confirmed, so surface a useful warning while
        # keeping the confirmation successful.  The client can retry the
        # batch after the platform becomes idle.
        if "没有待发章节" in str(reason or ""):
            return True, "本章已确认，已无待发章节"
        return True, "本章已确认，后续章节未启动：%s" % (reason or "未知原因")
    return True, ""


# ------------------------------------------------ 已发章数校准（平台实况对账）
# 台账只记得 CodeBee 自己发成功的章：用户在平台窗口里手工补交/平台驳回/
# 台账重复记录都会让「已发 N」漂移（2026-09-28 实案：番茄平台 8 章台账 7）。
# 校准 = 打开章节管理页数行（跨页+跨卷），按状态关键词分桶后写回 books.json
# 的 remote_*。行收集/分桶在 chaprows（本文件超行数基线拆出，2026-10-08）。
from .chaprows import bucket_chapter_rows, collect_all_volumes  # noqa: E402


def sync_published(task_id, plat, manual=False):
    """Run calibration under the same per-account browser lock as publishing."""
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    try:
        return _sync_published_impl(task_id, plat, manual=manual)
    finally:
        action_lock.release()


def _sync_published_impl(task_id, plat, manual=False):
    """同步执行一次校准（manager.sync_published_async 的线程体）。

    manual=False（打开作品页自动触发）只 attach 在跑的浏览器实例，绝不
    静默 launch 新窗口吓人；manual=True（用户点「校准」按钮）才允许
    attach-or-launch。行收集走 _collect_all_volumes（跨页+跨卷）；
    识别不到章节行（改版/未登录）时只记 note 不覆写 remote_*——宁可
    显示旧数也不把 0 当真相。"""
    from .browser import Browser
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    mod = PLATFORMS[plat]
    js = mod.CONFIG.get("count_rows_js")
    if not js:
        return False, "该平台未配置章节计数"
    try:
        if manual:
            b, page = _open_page(slot, preserve_tabs=True)
        else:
            port = _st(slot).get("port") or 0
            b = Browser.attach(int(port))       # 实例已死 → 异常 → 只记原因
            page = b.first_page(create=True)
    except Exception as e:
        return False, "浏览器未连接，点「连接平台」后再校准"
    waiting_login = _st(slot).get("status") == "waiting_login"
    # While a challenge is already being shown, inspect all tabs passively and
    # leave the verification page in place. A connected/stale session follows
    # the normal reconciliation navigation once, then probes every tab so a
    # second-tab challenge cannot be hidden by the author page.
    if waiting_login:
        ok_login, login_state, checked_page = _probe_login_pages(slot, b, page)
        if not ok_login:
            if _authentication_required(plat, login_state):
                _set(slot, status="waiting_login", error=str(login_state)[:160])
            return False, "平台登录态等待恢复，验证完成后自动重试：%s" % str(login_state)[:160]
        if checked_page is not None:
            page = checked_page
    _set(slot, status="connected", error="")
    if not str(book.get("book_id") or "").strip():
        # book_id 空的登记（人工登记/未验证建书结果）别拿去拼 URL 盲跑：
        # 空 id 会落到作品列表页，数章节必然 0 行，报一句误导人的「可能改版」。
        # 先按书名对账，找回补账再校准；找不回就明说书可能没建成。
        bid = _resolve_book_id(plat, page, book)
        if bid:
            book = dict(book, book_id=bid)
            ledger.save_book(task_id, plat, book)   # 合并语义：保留 remote_*
        else:
            return False, ("平台作品列表按书名未找到《%s》，这本书可能尚未建成；"
                           "请到平台作品管理确认，若已存在可人工登记"
                           % (book.get("title") or ""))
    try:
        page.navigate(mod.chapter_manage_url(book), timeout=40)
        ok_login, login_state, checked_page = _probe_login_pages(slot, b, page)
        if not ok_login:
            if _authentication_required(plat, login_state):
                _set(slot, status="waiting_login", error=str(login_state)[:160])
            return False, "平台登录态待恢复，验证完成后自动重试：%s" % str(login_state)[:160]
        if checked_page is not None:
            page = checked_page
        _wait_chapter_rows_ready(page, mod, timeout=4.0)
        rows, vols = collect_all_volumes(page, mod)
        st = bucket_chapter_rows(rows)
        if st["total"] <= 0:
            # A newly-created work has no table rows yet.  Accept only an
            # explicit platform empty-state marker; a blank page still means
            # the page changed or the session is not ready.
            markers = mod.CONFIG.get("empty_chapter_markers") or []
            body_text = ""
            evaluate = getattr(page, "evaluate", None)
            if callable(evaluate):
                try:
                    body_text = str(evaluate("document.body && document.body.innerText || ''") or "")
                except Exception:
                    body_text = ""
            if not any(str(marker) and str(marker) in body_text for marker in markers):
                return False, "章节管理页未识别到章节列表（可能改版）"
        extra = {"volumes": vols} if vols else {}
        ledger.update_book(task_id, plat,
                           remote_total=st["total"],
                           remote_published=st["published"],
                           remote_scheduled=st["scheduled"],
                           remote_review=st["review"],
                           remote_draft=st.get("draft", 0),
                           remote_rejected=st["rejected"],
                           remote_published_nos=st["published_nos"],
                           remote_scheduled_nos=st["scheduled_nos"],
                           remote_review_nos=st["review_nos"],
                           remote_draft_nos=st.get("draft_nos", []),
                           remote_last_publish_at=st["last_publish_at"],
                           remote_last_schedule_at=st["last_schedule_at"],
                           remote_synced_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                           **extra)
        return True, ""
    except Exception as e:
        return False, "校准失败：%s" % str(e)[:160]


def _sync_preflight(task_id, plat, manual):
    """校准前置检查（同步、不碰浏览器页面）：登记在案/没在忙/平台支持；
    自动触发（manual=False）时还要求浏览器实例活着——绝不静默 launch。"""
    from .browser import Browser
    if plat not in PLATFORMS:
        return False, "未知平台"
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    # The persisted status can still be ``connected`` while a calibration
    # worker owns the in-process reservation.  Check the same reservation used
    # by publishing so an async calibration request fails immediately instead
    # of opening a competing browser page.
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    action_lock.release()
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    if not PLATFORMS[plat].CONFIG.get("count_rows_js"):
        return False, "该平台未配置章节计数"
    if not manual:
        port = _st(slot).get("port") or 0
        try:
            Browser.attach(int(port))
        except Exception:
            return False, "浏览器未连接，点「连接平台」后再校准"
    return True, ""


def sync_published_async(task_id, plat, manual=False):
    """校准进后台线程（浏览器操作绝不能堵 HTTP 线程）。

    返回 (ok, err)：预检失败时线程都不起，接口即时回报 skipped 原因。"""
    ok, err = _sync_preflight(task_id, plat, manual)
    if not ok:
        return False, err

    def run():
        ok2, err2 = sync_published(task_id, plat, manual=manual)
        if not ok2:
            ledger.update_book(task_id, plat, remote_error=err2,
                               remote_synced_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    threading.Thread(target=run, daemon=True,
                     name="pub-sync-%s" % plat).start()
    return True, ""


def published_local(task_id, plat):
    """本地台账口径的已发章数（去重后的章号数，记录数会因重试虚高）。"""
    return len(ledger.published_chapters(task_id, plat))


def history(task_id=None, plat=None, limit=50):
    return ledger.recent(task_id=task_id, platform=plat, limit=limit)


_load()
_migrate_legacy_profiles()
