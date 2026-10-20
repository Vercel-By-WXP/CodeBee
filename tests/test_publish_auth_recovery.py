"""Keep authentication pages stable while durable publishing waits to resume."""
import json
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class AuthPage:
    def __init__(self, url, text=""):
        self.current_url = url
        self.text = text
        self.navigations = []

    def navigate(self, url, timeout=30):
        self.navigations.append(url)
        self.current_url = url

    def url(self):
        return self.current_url

    def call(self, function, timeout=8):
        return self.text


class PublishAuthRecoveryTests(unittest.TestCase):
    def test_waiting_login_reopens_only_an_empty_tab_after_restart(self):
        from core.publish import manager

        page = AuthPage("about:blank", "请完成安全验证后继续")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "waiting_login"}}}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager.ledger, "record"):
            ok, error = manager.connect("fanqie", unattended=True)
        self.assertFalse(ok)
        self.assertIn("验证完成后自动重试", error)
        self.assertEqual(page.navigations, [manager.fanqie.CONFIG["home"]])

    def test_durable_batch_resumes_after_authentication_without_a_new_publish_request(self):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        page = AuthPage(manager.fanqie.CONFIG["home"], "请完成安全验证后继续")
        accepted = set()

        def upload(*args, **kwargs):
            accepted.add(1)
            return True, ""

        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            for patch in (
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"),
                mock.patch.object(manager, "_state", {"platforms": {}}),
                mock.patch.object(manager, "_ACTION_LOCKS", {}),
                mock.patch.object(manager, "_open_page", return_value=(object(), page)),
                mock.patch.object(manager, "sync_published",
                                  side_effect=lambda task, platform, **kw: manager._login_guard(platform)),
                mock.patch.object(manager, "_quality_release_guard", return_value={"allowed": True}),
                mock.patch.object(auto, "_BATCH_JOB_FILE", Path(temp) / "batches.json"),
                mock.patch.object(auto, "_BATCH_JOBS", None),
                mock.patch.object(auto, "_running", {}),
                mock.patch.object(auto, "_cancel_events", {}),
                mock.patch.object(auto, "_BATCH_SLOT_LOCKS", {}),
                mock.patch.object(auto, "_BATCH_RESUME_LOCK", threading.Lock()),
                mock.patch.object(auto, "calibrated", return_value=True),
                mock.patch.object(auto, "guards", return_value=(True, "")),
                mock.patch.object(auto, "pending", return_value=([
                    {"chapter_no": 1, "file": "第1章.txt"}], "")),
                mock.patch.object(auto, "_batch_sleep", return_value=True),
                mock.patch.object(auto, "_wait_idle", return_value=True),
                mock.patch.object(auto.threading, "Thread", ImmediateThread),
                mock.patch.object(store, "get_task", return_value={"id": "task-auth", "workdir": temp}),
                mock.patch.object(contracts, "publish_allowed", return_value=True),
                mock.patch.object(ledger, "book_for", return_value={"book_id": "123", "title": "测试"}),
                mock.patch.object(ledger, "published_chapters", side_effect=lambda *args: accepted),
                mock.patch.object(ledger, "scheduled_chapters", return_value=set()),
                mock.patch.object(ledger, "review_chapters", return_value=set()),
                mock.patch.object(ledger, "record"),
            ):
                stack.enter_context(patch)
            start_upload = stack.enter_context(mock.patch.object(
                manager, "upload_chapter_async", side_effect=upload))
            self.assertEqual(auto.publish_pending_async("task-auth", "fanqie"), (True, ""))
            self.assertEqual(auto._running["task-auth"]["status"], "error")
            self.assertEqual(manager._st("fanqie")["status"], "waiting_login")
            job = dict(auto._load_batch_jobs()["task-auth"])
            self.assertEqual(job["status"], "retry_wait")
            self.assertFalse(auto._batch_slot_lock("fanqie").locked())
            self.assertEqual(len(page.navigations), 1)
            start_upload.assert_not_called()

            # A platform-approved login/verification completes externally.
            # The ordinary recovery tick, without a new publish request,
            # must notice it and continue the original durable batch.
            page.text = "作品管理"
            auto._BATCH_JOBS = None
            self.assertEqual(auto.resume_interrupted_batches(now=job["next_retry_at"]), 1)
            self.assertEqual(auto._running["task-auth"]["status"], "done")
            self.assertEqual(auto._running["task-auth"]["done"], 1)
            self.assertEqual(auto._load_batch_jobs(), {})
            self.assertEqual(len(page.navigations), 1)
            start_upload.assert_called_once()

    def test_auth_retry_backoff_stays_short_after_verification_completes(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp) / "batches.json"), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                auto._remember_automatic_batch(
                    "task-auth", "fanqie", True, False, None,
                    False, False, "", False, True, None)
                self.assertTrue(auto._retain_automatic_batch_for_retry(
                    "task-auth", "平台等待登录或安全验证，验证完成后自动重试",
                    now=1000))
                record = auto._load_batch_jobs()["task-auth"]

        # A completed login must not wait behind the normal 60s→30m backoff;
        # the watchdog/profile probe can safely recheck it shortly.
        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["next_retry_at"], 1015.0)

    def test_standalone_create_book_waits_for_auth_then_retries_once(self):
        from core.publish import manager

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        states = iter((
            {"status": "waiting_login"},
            {"status": "waiting_login"},
            {"status": "connected"},
        ))
        with mock.patch.object(manager, "_CREATE_BOOK_RECOVERY", set()), \
                mock.patch.object(manager, "_st", side_effect=lambda _slot: next(states)), \
                mock.patch.object(manager, "connect",
                                   return_value=(False, "平台等待登录或安全验证")) as connect, \
                mock.patch.object(manager, "create_book_async",
                                   return_value=(True, "")) as create, \
                mock.patch.object(manager.ledger, "book_for",
                                   return_value={"book_id": "book-1"}), \
                mock.patch.object(manager.threading, "Thread", ImmediateThread), \
                mock.patch.object(manager.time, "monotonic",
                                   side_effect=(0, 0, 1, 2, 2)), \
                mock.patch.object(manager.time, "sleep"):
            self.assertTrue(manager._queue_create_book_auth_recovery(
                "task-auth", "fanqie", "", {
                    "force": False, "force_confirmed": False,
                    "force_reason": ""}))

        self.assertGreaterEqual(connect.call_count, 1)
        self.assertTrue(all(call.args == ("fanqie",) and
                             call.kwargs == {"account": "", "unattended": True}
                             for call in connect.call_args_list))
        create.assert_called_once()
        create_args, create_kwargs = create.call_args
        self.assertEqual(create_args, ("task-auth", "fanqie"))
        self.assertEqual(create_kwargs["auto_submit"], True)
        self.assertEqual(create_kwargs["account"], "")
        self.assertEqual(create_kwargs["force"], False)
        self.assertEqual(create_kwargs["force_confirmed"], False)
        self.assertEqual(create_kwargs["force_reason"], "")
        self.assertEqual(create_kwargs["_recovery"], True)
        self.assertEqual(create_kwargs["_recovery_epoch"], 0)

    def test_standalone_create_book_keeps_recovery_after_worker_failure(self):
        from core.publish import manager

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        states = iter((
            {"status": "waiting_login"},
            {"status": "waiting_login"},
            {"status": "connected"},
            {"status": "error", "error": "建书失败：连接断开"},
            {"status": "error", "error": "建书失败：连接断开"},
            {"status": "connected"},
        ))
        book_results = iter((None, {"book_id": "book-1"}))
        with mock.patch.object(manager, "_CREATE_BOOK_RECOVERY", set()), \
                mock.patch.object(manager, "_st", side_effect=lambda _slot: next(states)), \
                mock.patch.object(manager, "_set"), \
                mock.patch.object(manager, "connect",
                                   return_value=(False, "平台等待登录或安全验证")), \
                mock.patch.object(manager, "create_book_async",
                                   return_value=(True, "")) as create, \
                mock.patch.object(manager.ledger, "book_for",
                                   side_effect=lambda *_args: next(book_results)), \
                mock.patch.object(manager.ledger, "recent", return_value=[{
                    "action": "create_book", "ok": False,
                    "error": "建书失败：连接断开"}]), \
                mock.patch.object(manager.threading, "Thread", ImmediateThread), \
                mock.patch.object(manager.time, "monotonic", return_value=0), \
                mock.patch.object(manager.time, "sleep"):
            self.assertTrue(manager._queue_create_book_auth_recovery(
                "task-auth", "fanqie", "", {
                    "force": False, "force_confirmed": False,
                    "force_reason": ""}))

        self.assertEqual(create.call_count, 2)

    def test_standalone_create_book_retries_when_account_is_temporarily_busy(self):
        from core.publish import manager

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        states = iter((
            {"status": "waiting_login"},
            {"status": "connected"},
            {"status": "connected"},
            {"status": "connected"},
        ))
        results = iter(((False, "该账号有操作正在进行中"), (True, "")))
        with mock.patch.object(manager, "_CREATE_BOOK_RECOVERY", set()), \
                mock.patch.object(manager, "_st", side_effect=lambda _slot: next(states)), \
                mock.patch.object(manager, "_set"), \
                mock.patch.object(manager, "connect",
                                   return_value=(False, "平台等待登录或安全验证")), \
                mock.patch.object(manager, "create_book_async",
                                   side_effect=lambda *args, **kwargs: next(results)) as create, \
                mock.patch.object(manager.ledger, "book_for",
                                   return_value={"book_id": "book-1"}), \
                mock.patch.object(manager.threading, "Thread", ImmediateThread), \
                mock.patch.object(manager.time, "monotonic", return_value=0), \
                mock.patch.object(manager.time, "sleep"):
            self.assertTrue(manager._queue_create_book_auth_recovery(
                "task-auth", "fanqie", "", {
                    "force": False, "force_confirmed": False,
                    "force_reason": ""}))

        self.assertEqual(create.call_count, 2)

    def test_create_book_accepts_auth_recovery_without_second_click(self):
        from core import contracts, store
        from core.publish import manager

        lock = mock.Mock()
        with mock.patch.object(store, "get_task", return_value={"id": "task-auth"}), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(store, "inherited_book_meta", return_value={
                    "status": "done", "data": {"book_name": "测试作品"}}), \
                mock.patch.object(manager.ledger, "book_for", return_value=None), \
                mock.patch.object(manager.ledger, "recent", return_value=[]), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(manager, "_create_preflight", return_value=""), \
                mock.patch.object(manager, "automatic_release_kwargs", return_value={}), \
                mock.patch.object(manager, "_quality_release_guard",
                                   return_value={"allowed": True}), \
                mock.patch.object(manager, "_try_action_lock", return_value=lock), \
                mock.patch.object(manager, "_login_guard",
                                   return_value=(False, "平台等待登录或安全验证")), \
                mock.patch.object(manager, "_queue_create_book_auth_recovery",
                                   return_value=True) as queue:
            ok, error = manager.create_book_async("task-auth", "fanqie")

        self.assertTrue(ok, error)
        self.assertEqual(error, "")
        lock.release.assert_called_once()
        queue.assert_called_once_with(
            "task-auth", "fanqie", "",
            {"force": False, "force_confirmed": False, "force_reason": ""})

    def test_pending_create_recovery_is_recreated_after_restart(self):
        from core.publish import manager

        state = {
            "platforms": {
                "qimao@author": {
                    "status": "waiting_login",
                    "pending_create": {
                        "task_id": "task-auth",
                        "account": "author",
                        "force": True,
                        "force_confirmed": True,
                        "force_reason": "operator approval",
                    },
                },
            },
        }
        with mock.patch.object(manager, "_state", {}), \
                mock.patch.object(manager, "_load", side_effect=lambda: manager._state.update(state)), \
                mock.patch.object(manager, "_queue_create_book_auth_recovery",
                                   return_value=True) as queue:
            self.assertEqual(manager._resume_pending_create_recoveries(), 1)

        queue.assert_called_once_with(
            "task-auth", "qimao", "author",
            {"force": True, "force_confirmed": True,
             "force_reason": "operator approval"})

    def test_pending_create_markers_are_independent_per_task(self):
        from core.publish import manager

        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {}}):
            first = {"task_id": "task-a", "account": ""}
            second = {"task_id": "task-b", "account": ""}
            manager._remember_pending_create("fanqie", first)
            manager._remember_pending_create("fanqie", second)
            entries = manager._st("fanqie")["pending_creates"]
            self.assertEqual(set(entries), {"task-a", "task-b"})

            manager._forget_pending_create("fanqie", "task-a")
            state = manager._st("fanqie")
            self.assertEqual(set(state["pending_creates"]), {"task-b"})
            self.assertEqual(state["pending_create"]["task_id"], "task-b")

            manager._forget_pending_create("fanqie", "task-b")
            self.assertEqual(manager._st("fanqie").get("pending_create"), {})

    def test_restart_resumes_all_keyed_pending_create_tasks(self):
        from core.publish import manager

        state = {
            "platforms": {
                "fanqie": {
                    "pending_creates": {
                        "task-a": {"task_id": "task-a", "account": ""},
                        "task-b": {"task_id": "task-b", "account": ""},
                    },
                    "pending_create": {
                        "task_id": "task-a", "account": "",
                    },
                },
            },
        }
        with mock.patch.object(manager, "_state", {}), \
                mock.patch.object(manager, "_load", side_effect=lambda: manager._state.update(state)), \
                mock.patch.object(manager, "_queue_create_book_auth_recovery",
                                   return_value=True) as queue:
            self.assertEqual(manager._resume_pending_create_recoveries(), 2)

        self.assertEqual({call.args[0] for call in queue.call_args_list},
                         {"task-a", "task-b"})

    def test_waiting_login_or_captcha_keeps_page_then_recovers_without_a_waiter(self):
        from core.publish import manager

        for platform in ("fanqie", "qimao"):
            for kind in ("login", "captcha"):
                with self.subTest(platform=platform, kind=kind), \
                        tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                    home = manager.PLATFORMS[platform].CONFIG["home"]
                    page = AuthPage(home + "login" if kind == "login" else home,
                                    "请完成安全验证后继续" if kind == "captcha" else "")
                    stack.enter_context(mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"))
                    stack.enter_context(mock.patch.object(manager, "_state", {
                        "platforms": {platform: {"status": "waiting_login"}}}))
                    stack.enter_context(mock.patch.object(manager, "_ACTION_LOCKS", {}))
                    open_page = stack.enter_context(mock.patch.object(
                        manager, "_open_page", return_value=(object(), page)))
                    record = stack.enter_context(mock.patch.object(manager.ledger, "record"))
                    thread = stack.enter_context(mock.patch.object(manager.threading, "Thread"))

                    for _ in range(2):
                        ok, error = manager.connect(platform, unattended=True)
                        self.assertFalse(ok)
                        self.assertEqual(manager._st(platform)["status"], "waiting_login")
                        self.assertIn("验证完成后自动重试", error)
                        self.assertEqual(page.navigations, [])
                    guard_ok, _ = manager._login_guard(platform)
                    self.assertFalse(guard_ok)
                    self.assertEqual(page.navigations, [])

                    page.current_url, page.text = home, "作品管理"
                    self.assertEqual(manager.connect(platform, unattended=True), (True, ""))
                    self.assertEqual(manager._st(platform)["status"], "connected")
                    self.assertEqual(page.navigations, [])
                    self.assertTrue(record.call_args.kwargs["ok"])
                    thread.assert_not_called()
                    self.assertTrue(all(call.kwargs.get("preserve_tabs")
                                        for call in open_page.call_args_list))

    def test_waiting_login_rechecks_cookie_when_challenge_tab_is_gone(self):
        from core.publish import manager

        page = AuthPage("about:blank", "")
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                manager, "STATUS_FILE", Path(temp) / "state.json"))
            stack.enter_context(mock.patch.object(manager, "_state", {
                "platforms": {"fanqie": {"status": "waiting_login"}}}))
            stack.enter_context(mock.patch.object(manager, "_ACTION_LOCKS", {}))
            stack.enter_context(mock.patch.object(
                manager, "_open_page", return_value=(object(), page)))
            stack.enter_context(mock.patch.object(manager.ledger, "record"))

            self.assertEqual(manager.connect("fanqie", unattended=True),
                             (True, ""))
            self.assertEqual(manager._st("fanqie")["status"], "connected")

        self.assertEqual(page.navigations, [manager.fanqie.CONFIG["home"]])

    def test_fresh_session_probe_navigates_once_and_retains_new_challenge(self):
        from core.publish import manager

        page = AuthPage("about:blank", "请完成安全验证后继续")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {}}), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager.ledger, "record"):
            for _ in range(2):
                self.assertFalse(manager.connect("fanqie", unattended=True)[0])
            self.assertEqual(page.navigations, [manager.fanqie.CONFIG["home"]])
            self.assertEqual(manager._st("fanqie")["status"], "waiting_login")

    def test_passive_check_cannot_treat_an_unrelated_page_as_logged_in(self):
        from core.publish import manager

        page = AuthPage("https://unrelated.example/", "作品管理")
        ok, _ = manager._check_login("fanqie", page, navigate=False)
        self.assertFalse(ok)
        self.assertEqual(page.navigations, [])

    def test_passive_check_accepts_a_platform_subdomain(self):
        from core.publish import manager

        page = AuthPage("https://writer.fanqienovel.com/author/home", "作品管理")
        ok, _ = manager._check_login("fanqie", page, navigate=False)
        self.assertTrue(ok)

    def test_auth_recovery_preserves_all_existing_tabs(self):
        from core.publish import manager

        browser = mock.Mock()
        browser.pages.return_value = [{"id": "writer"}, {"id": "verification"}]
        with mock.patch.object(manager, "_ensure_browser", return_value=browser), \
                mock.patch("core.publish.browser._http_json") as request:
            self.assertEqual(manager._open_page("fanqie", preserve_tabs=True),
                             (browser, browser.first_page.return_value))
        request.assert_not_called()
        browser.pages.assert_not_called()

    def test_auth_recovery_reuses_persisted_challenge_tab(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"] + "login",
                        "请完成安全验证后继续")
        browser = mock.Mock()
        browser.page_for_target.return_value = page
        with mock.patch.object(manager, "_state", {
                "platforms": {"fanqie": {"status": "waiting_login",
                                           "auth_target_id": "challenge"}}}), \
                mock.patch.object(manager, "_ensure_browser", return_value=browser):
            found_browser, found_page = manager._open_page(
                "fanqie", preserve_tabs=True)
        self.assertIs(found_browser, browser)
        self.assertIs(found_page, page)
        browser.page_for_target.assert_called_once_with("challenge")

    def test_login_probe_records_challenge_tab_and_clears_it_after_recovery(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"], "请完成安全验证后继续")
        page._target_id = "challenge"
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "waiting_login"}}}):
            ok, _ = manager._check_login("fanqie", page, navigate=False)
            self.assertFalse(ok)
            self.assertEqual(manager._st("fanqie")["auth_target_id"], "challenge")
            page.text = "作品管理"
            ok, _ = manager._check_login("fanqie", page, navigate=False)
            self.assertTrue(ok)
            self.assertEqual(manager._st("fanqie").get("auth_target_id"), "")

    def test_login_probe_prefers_a_challenge_in_a_second_tab(self):
        from core.publish import manager

        home = manager.fanqie.CONFIG["home"]
        primary = AuthPage(home, "作品管理")
        primary._target_id = "home"
        challenge = AuthPage(home + "login", "")
        challenge._target_id = "challenge"
        browser = mock.Mock()
        browser.pages.return_value = [{
            "id": "home", "webSocketDebuggerUrl": "ws://home/page"}, {
            "id": "challenge", "webSocketDebuggerUrl": "ws://challenge/page"}]
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {"fanqie": {}}}), \
                mock.patch.object(manager, "Page", return_value=challenge):
            ok, detail, checked = manager._probe_login_pages(
                "fanqie", browser, primary)
            auth_target = manager._st("fanqie").get("auth_target_id")
        self.assertFalse(ok)
        self.assertIn("login", detail)
        self.assertIs(checked, challenge)
        self.assertEqual(auth_target, "challenge")

    def test_cross_domain_challenge_text_is_retained_before_host_rejection(self):
        from core.publish import manager

        page = AuthPage("https://verify.example/challenge", "请完成安全验证后继续")
        page._target_id = "third-party-challenge"
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {"fanqie": {}}}):
            ok, detail = manager._check_login("fanqie", page, navigate=False)
            auth_target = manager._st("fanqie").get("auth_target_id")
        self.assertFalse(ok)
        self.assertIn("安全验证", detail)
        self.assertEqual(auth_target, "third-party-challenge")

    def test_cross_domain_challenge_url_is_retained_when_body_is_empty(self):
        from core.publish import manager

        page = AuthPage("https://verify.example/challenge", "")
        page._target_id = "third-party-url-challenge"
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {"fanqie": {}}}):
            ok, detail = manager._check_login("fanqie", page, navigate=False)
            auth_target = manager._st("fanqie").get("auth_target_id")
        self.assertFalse(ok)
        self.assertIn("安全验证", detail)
        self.assertEqual(auth_target, "third-party-url-challenge")

    def test_retrying_connection_preserves_existing_verification_tabs(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"] + "login")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "waiting_login"}}}), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)) as opened, \
                mock.patch.object(manager.threading, "Thread"):
            self.assertEqual(manager.connect("fanqie"), (True, ""))
        self.assertTrue(opened.call_args.kwargs["preserve_tabs"])
        self.assertEqual(page.navigations, [])

    def test_reconciliation_waits_without_navigating_away_from_verification(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"], "请完成安全验证后继续")
        browser = mock.Mock()
        browser.first_page.return_value = page
        with mock.patch.object(manager, "_state", {
                "platforms": {"fanqie": {"status": "waiting_login", "port": 19222}}}), \
                mock.patch.object(manager.ledger, "book_for", return_value={"book_id": "123"}), \
                mock.patch.object(manager.Browser, "attach", return_value=browser), \
                mock.patch.object(manager, "collect_all_volumes") as collect:
            ok, error = manager._sync_published_impl("task-auth", "fanqie")
        self.assertFalse(ok)
        self.assertIn("登录态", error)
        self.assertEqual(page.navigations, [])
        collect.assert_not_called()

    def test_legacy_work_lookup_preserves_verification_then_resumes(self):
        from core import store
        from core.publish import auto, ledger, manager

        page = AuthPage(manager.qimao.CONFIG["home"], "请完成安全验证后继续")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"qimao": {"status": "waiting_login"}}}), \
                mock.patch.object(store, "get_task", return_value={"id": "task-auth"}), \
                mock.patch.object(ledger, "book_for", return_value={"title": "测试"}), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager, "_resolve_book_id", return_value="42") as resolve, \
                mock.patch.object(ledger, "save_book"), \
                mock.patch.object(ledger, "record"), \
                mock.patch.object(auto, "_batch_sleep", return_value=True):
            self.assertFalse(auto._prepare_book("task-auth", "qimao")[0])
            self.assertEqual(page.navigations, [])
            resolve.assert_not_called()
            page.text = "作品管理"
            self.assertEqual(auto._prepare_book("task-auth", "qimao"), (True, ""))
            self.assertEqual(page.navigations, [])
            resolve.assert_called_once()

    def test_new_challenge_during_reconciliation_enters_passive_wait(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"], "请完成安全验证后继续")
        browser = mock.Mock()
        browser.first_page.return_value = page
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "connected", "port": 19222}}}), \
                mock.patch.object(manager.ledger, "book_for", return_value={"book_id": "123"}), \
                mock.patch.object(manager.Browser, "attach", return_value=browser), \
                mock.patch.object(manager, "collect_all_volumes") as collect:
            for _ in range(2):
                ok, error = manager._sync_published_impl("task-auth", "fanqie")
                self.assertFalse(ok)
                self.assertIn("登录态", error)
            self.assertEqual(manager._st("fanqie")["status"], "waiting_login")
            self.assertEqual(len(page.navigations), 1)
            collect.assert_not_called()

    def test_restart_preserves_pending_verification_instead_of_forcing_navigation(self):
        from core.publish import manager

        with mock.patch.object(manager, "_load"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "waiting_login", "port": 19222}}}), \
                mock.patch.object(manager, "_set") as set_state:
            manager.recover_orphans()
            self.assertEqual(manager._st("fanqie")["status"], "waiting_login")
            set_state.assert_not_called()

    def test_existing_login_waiter_does_not_refresh_an_active_challenge(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"], "请完成安全验证后继续")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {}}), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager.threading, "Thread") as thread, \
                mock.patch.object(manager, "_finalize_login_wait"):
            self.assertEqual(manager.connect("fanqie"), (True, ""))
            waiter = thread.call_args.kwargs["target"]
            with mock.patch.object(manager, "LOGIN_WAIT_S", 10), \
                    mock.patch.object(manager.time, "time", side_effect=[0, 0, 11]), \
                    mock.patch.object(manager.time, "sleep"):
                waiter()
            self.assertEqual(page.navigations, [manager.fanqie.CONFIG["home"]])

    def test_login_wait_timeout_keeps_an_unfinished_verification_page(self):
        from core.publish import manager

        page = AuthPage(manager.qimao.CONFIG["home"], "请完成安全验证后继续")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"qimao": {"status": "waiting_login"}}}), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)):
            self.assertFalse(manager._finalize_login_wait("qimao"))
            self.assertEqual(manager._st("qimao")["status"], "waiting_login")
            self.assertEqual(page.navigations, [])

    def test_auth_watchdog_recovers_after_interactive_waiter_expires(self):
        from core.publish import manager

        with mock.patch.object(manager, "_load"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {
                        "fanqie": {"status": "waiting_login"},
                        "qimao@alt": {"status": "connected"},
                    }}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_connect_unattended",
                                  return_value=(True, "")) as connect:
            self.assertEqual(manager._auth_watch_once(), 1)
        connect.assert_called_once_with("fanqie", "", "fanqie")

    def test_auth_watchdog_does_not_duplicate_an_interactive_waiter(self):
        from core.publish import manager

        with mock.patch.object(manager, "_load"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "waiting_login"}}}), \
                mock.patch.object(manager, "_LOGIN_WAITERS", {"fanqie"}), \
                mock.patch.object(manager, "_connect_unattended") as connect:
            self.assertEqual(manager._auth_watch_once(), 0)
        connect.assert_not_called()

    def test_auth_watchdog_retries_error_slot_after_auth_state_rotation(self):
        from core.publish import manager

        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "fanqie.json"
            state_path.write_text('{"cookies":[{"name":"sid","value":"new"}]}',
                                  encoding="utf-8")
            with mock.patch.dict(manager.os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(state_path)}, clear=False), \
                    mock.patch.object(manager, "_load"), \
                    mock.patch.object(manager, "_state", {
                        "platforms": {
                            "fanqie": {
                                "status": "error",
                                "error": "保存的平台登录态不可用：登录态失效",
                            }}}), \
                    mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                    mock.patch.object(manager, "_LOGIN_WAITERS", set()), \
                    mock.patch.object(manager, "_connect_unattended",
                                      return_value=(True, "")) as connect:
                self.assertEqual(manager._auth_watch_once(), 1)
            connect.assert_called_once_with("fanqie", "", "fanqie")

    def test_connected_session_probe_queues_a_new_platform_challenge(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"] + "login")
        browser = mock.Mock()
        browser.alive.return_value = True
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_load"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "connected"}}}), \
                mock.patch.object(manager, "_browsers", {"fanqie": browser}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_open_page", return_value=(browser, page)) as opened, \
                mock.patch.object(manager, "_probe_login_pages",
                                  return_value=(False, "平台要求安全验证：验证码", page)):
            self.assertEqual(manager._connected_session_probe_once(), 1)
            self.assertEqual(manager._st("fanqie")["status"], "waiting_login")
            persisted = json.loads((Path(temp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["platforms"]["fanqie"]["status"], "waiting_login")
        opened.assert_not_called()

    def test_login_guard_keeps_second_tab_challenge_over_primary_session(self):
        from core.publish import manager

        primary = AuthPage(manager.fanqie.CONFIG["home"], "作品管理")
        challenge = AuthPage("https://verify.example/challenge", "")
        with mock.patch.object(manager, "_open_page",
                               return_value=(object(), primary)), \
                mock.patch.object(manager, "_st",
                                   return_value={"status": "connected"}), \
                mock.patch.object(manager, "_probe_login_pages",
                                   return_value=(False, "平台要求安全验证：验证码",
                                                  challenge)), \
                mock.patch.object(manager, "_check_login") as check:
            ok, error = manager._login_guard("fanqie")
        self.assertFalse(ok)
        self.assertIn("验证完成后自动重试", error)
        check.assert_not_called()
        self.assertEqual(primary.navigations, [])

    def test_unattended_connect_keeps_second_tab_challenge_over_primary_session(self):
        from core.publish import manager

        primary = AuthPage(manager.fanqie.CONFIG["home"], "作品管理")
        challenge = AuthPage("https://verify.example/challenge", "")
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "connected"}}}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_open_page",
                                   return_value=(object(), primary)), \
                mock.patch.object(manager, "_probe_login_pages",
                                   return_value=(False, "平台要求安全验证：验证码",
                                                  challenge)), \
                mock.patch.object(manager, "_check_login") as check:
            ok, error = manager.connect("fanqie", unattended=True)
        self.assertFalse(ok)
        self.assertIn("验证完成后自动重试", error)
        check.assert_not_called()
        self.assertEqual(primary.navigations, [])

    def test_platform_help_text_with_one_captcha_word_is_not_a_challenge(self):
        from core.publish import manager

        page = AuthPage(manager.fanqie.CONFIG["home"], "帮助：验证码说明")
        ok, detail = manager._check_login("fanqie", page, navigate=False)
        self.assertTrue(ok)
        self.assertEqual(detail, manager.fanqie.CONFIG["home"])

    def test_connected_session_probe_ignores_unrelated_verification_text(self):
        from core.publish import manager

        page = AuthPage("https://example.com/help", "验证码说明")
        browser = mock.Mock()
        browser.alive.return_value = True
        state = {"platforms": {"fanqie": {"status": "connected"}}}
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_load"), \
                mock.patch.object(manager, "_state", state), \
                mock.patch.object(manager, "_browsers", {"fanqie": browser}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_probe_login_pages",
                                  return_value=(False, "平台要求安全验证：验证码", page)):
            self.assertEqual(manager._connected_session_probe_once(), 0)
            self.assertEqual(state["platforms"]["fanqie"]["status"], "connected")

    def test_disconnect_yields_to_an_active_account_probe(self):
        from core.publish import manager

        with mock.patch.object(manager, "_ACTION_LOCKS", {}):
            lock = manager._action_lock("fanqie")
            self.assertTrue(lock.acquire(blocking=False))
            try:
                self.assertFalse(manager.disconnect("fanqie"))
            finally:
                lock.release()


if __name__ == "__main__":
    unittest.main()
