"""Automatic publishing keeps the task contract approval gate."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class PublishApprovalAutonomyTests(unittest.TestCase):
    def test_pending_list_error_after_reconciliation_is_retried(self):
        from core.publish import auto

        expected = ([{"chapter_no": 9, "file": "第9章.md", "size": 1}], "")
        with mock.patch.object(auto, "pending", side_effect=(
                ([], "章节清单读取超时"), expected)), \
                mock.patch.object(auto, "_batch_sleep", return_value=True) as wait:
            chapters, error = auto._pending_with_recovery(
                "task-1", "fanqie", status={})

        self.assertEqual(chapters, expected[0])
        self.assertEqual(error, "")
        wait.assert_called_once_with("task-1", 60)

    def test_partial_workdir_scan_is_not_silently_reported_as_empty(self):
        from core.publish import auto

        with mock.patch("os.walk", side_effect=PermissionError("sharing violation")):
            with self.assertRaises(PermissionError):
                auto._workdir_chapter_files("workdir")

    def test_corrupt_batch_state_recovers_valid_atomic_temp_copy(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batches.json"
            path.write_text("{broken", encoding="utf-8")
            path.with_suffix(".tmp").write_text(
                '{"task-1":{"status":"retry_wait"}}', encoding="utf-8")
            with mock.patch.object(auto, "_BATCH_JOB_FILE", path), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                self.assertEqual(auto._load_batch_jobs(), {
                    "task-1": {"status": "retry_wait"}})

    def test_legacy_fixed_temp_batch_state_is_recovered_atomically(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batches.json"
            candidate = {"task-1": {"status": "in_flight"}}
            path.with_suffix(".tmp").write_text(
                '{"task-1":{"status":"in_flight"}}', encoding="utf-8")
            with mock.patch.object(auto, "_BATCH_JOB_FILE", path), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                loaded = auto._load_batch_jobs()

            self.assertEqual(loaded, candidate)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), candidate)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_corrupt_batch_state_fails_closed_instead_of_being_overwritten(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batches.json"
            path.write_text("{broken", encoding="utf-8")
            path.with_suffix(".tmp").write_text("also broken", encoding="utf-8")
            with mock.patch.object(auto, "_BATCH_JOB_FILE", path), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                with self.assertRaises(RuntimeError):
                    auto._load_batch_jobs()

    def test_worker_thread_start_failure_releases_slot_and_persists_retry(self):
        from core.publish import auto

        class BrokenThread:
            def start(self):
                raise RuntimeError("can't start new thread")

        class Lock:
            released = 0

            def release(self):
                self.released += 1

        slot_lock = Lock()
        status = {"status": "running"}
        event = mock.Mock()
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(auto, "_BATCH_JOB_FILE",
                                  Path(temp_dir, "batches.json")), \
                mock.patch.object(auto, "_BATCH_JOBS", None):
            auto._remember_automatic_batch(
                "task-1", "fanqie", True, False, None, False, False,
                "", False, True, None)
            started, error = auto._start_worker_or_retry(
                BrokenThread(), "task-1", status, event, slot_lock)
            record = auto._load_batch_jobs()["task-1"]

        self.assertFalse(started)
        self.assertIn("线程启动失败", error)
        self.assertEqual(status["status"], "error")
        self.assertEqual(slot_lock.released, 1)
        event.set.assert_not_called()
        self.assertEqual(record["status"], "retry_wait")
        self.assertIn("线程启动失败", record["error"])

    def test_transient_browser_preflight_errors_trigger_safe_session_recovery(self):
        from core.publish import auto

        errors = (
            "无法确认平台登录页面：CDP target 暂时无响应",
            "连接页面失败：Connection reset by peer",
            "无法解析调试地址：ws endpoint unavailable",
            "页面 30 秒未完成加载：https://fanqienovel.com/author",
            "浏览器调试端口 15 秒内没就绪",
            "端口 1234 上没有活着的浏览器",
            "浏览器启动失败：权限不足",
            "浏览器把启动请求转给了已在运行的会话（该平台浏览器有隐藏的后台实例占着档案）",
            "浏览器调试端口没就绪——端口探测不到",
        )
        for error in errors:
            with self.subTest(error=error):
                self.assertTrue(auto._needs_login_recovery(error))
                self.assertTrue(auto._scheduled_error_is_persistent(error))
        self.assertFalse(auto._needs_login_recovery("质量门禁拦截：正文质量不足"))
        self.assertTrue(auto._scheduled_error_is_persistent(
            "质量门禁拦截：最近一次质量评审未达标"))
        self.assertTrue(auto._scheduled_error_is_persistent(
            "第 12 章发起失败：读章节文件失败：[WinError 32] 文件正由另一进程使用"))
        self.assertTrue(auto._scheduled_error_is_persistent(
            "第 12 章发起失败：读章节文件失败：[Errno 13] Permission denied"))
        self.assertTrue(auto._scheduled_error_is_persistent(
            "自动发布线程启动失败：can't start new thread"))
        self.assertFalse(auto._scheduled_error_is_persistent(
            "第 12 章发起失败：章节文件不存在"))
        self.assertTrue(auto._scheduled_error_is_persistent(
            "未按作品名找到远端作品，未打开章节编辑器"))
        self.assertFalse(auto._scheduled_error_is_persistent("正文过短（80 字）"))

    def test_quality_blocked_automatic_batch_is_durable_and_retries_after_backoff(self):
        from core import contracts, store
        from core.publish import auto
        from core.publish import ledger, manager

        auto._running.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "auto_publish_batches.json"
            with mock.patch.object(auto, "_BATCH_JOB_FILE", state_file), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "calibrated", return_value=True), \
                    mock.patch.object(store, "get_task", return_value={
                        "id": "task-quality", "type": "serial_novel"}), \
                    mock.patch.object(contracts, "publish_allowed", return_value=True), \
                    mock.patch.object(ledger, "book_for", return_value={
                        "book_id": "book-1"}), \
                    mock.patch.object(manager, "slot_of", return_value="qimao"), \
                    mock.patch.object(manager, "automatic_release_kwargs", return_value={
                        "force": False, "force_confirmed": False,
                        "force_reason": ""}), \
                    mock.patch.object(manager, "_quality_release_guard", return_value={
                        "allowed": False,
                        "blockers": ["最近一次质量评审未达标"]}), \
                    mock.patch.object(auto.time, "time", return_value=1000):
                ok, error = auto.publish_pending_async(
                    "task-quality", "qimao", auto_submit=True,
                    only=[12], auto_prepare_book=False)
                record = auto._load_batch_jobs()["task-quality"]

        self.assertFalse(ok)
        self.assertIn("质量门禁拦截", error)
        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["next_retry_at"], 1060)
        self.assertTrue(record["params"]["auto_submit"])
        auto._running.clear()

    def test_direct_publish_automatically_prepares_missing_platform_book(self):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        lock = mock.Mock()
        lock.acquire.return_value = True
        thread = mock.Mock()
        with mock.patch.object(auto, "calibrated", return_value=True), \
                mock.patch.object(store, "get_task", return_value={"id": "task-new"}), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(manager, "slot_of", return_value="qimao"), \
                mock.patch.object(auto, "_batch_slot_lock", return_value=lock), \
                mock.patch.object(auto, "_remember_automatic_batch") as remember, \
                mock.patch.object(auto.threading, "Thread", return_value=thread) as thread_type, \
                mock.patch.dict(auto._running, {}, clear=True):
            ok, error = auto.publish_pending_async(
                "task-new", "qimao", auto_submit=True, auto_prepare_book=False)

        self.assertTrue(ok, error)
        self.assertEqual(error, "")
        self.assertTrue(remember.call_args.args[2])
        self.assertTrue(remember.call_args.args[9])
        thread_type.assert_called_once()
        self.assertIn("pub-prepare-qimao", thread_type.call_args.kwargs["name"])

    def test_daily_cap_keeps_one_off_automatic_batch_for_next_retry(self):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch.object(auto, "calibrated", return_value=True), \
                    mock.patch.object(store, "get_task", return_value={"id": "task-cap"}), \
                    mock.patch.object(contracts, "publish_allowed", return_value=True), \
                    mock.patch.object(ledger, "book_for", return_value={
                        "book_id": "book-1", "title": "作品"}), \
                    mock.patch.object(manager, "slot_of", return_value="qimao"), \
                    mock.patch.object(manager, "automatic_release_kwargs", return_value={
                        "force": False, "force_confirmed": False,
                        "force_reason": ""}), \
                    mock.patch.object(manager, "_quality_release_guard", return_value={
                        "allowed": True, "blockers": []}), \
                    mock.patch.object(auto, "guards", return_value=(
                        False, "今日已发 10 章（上限 10），明天再发")), \
                    mock.patch.object(auto.time, "time", return_value=1000):
                ok, error = auto.publish_pending_async(
                    "task-cap", "qimao", auto_submit=True, only=[11])
                record = auto._load_batch_jobs()["task-cap"]

        self.assertFalse(ok)
        self.assertIn("今日已发", error)
        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["next_retry_at"], 1060)
        self.assertEqual(record["params"]["only"], [11])
        self.assertTrue(record["params"]["auto_prepare_book"])

    def test_browser_slot_busy_keeps_one_off_automatic_batch(self):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        slot_lock = mock.Mock()
        slot_lock.acquire.return_value = False
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch.object(auto, "calibrated", return_value=True), \
                    mock.patch.object(store, "get_task", return_value={"id": "task-busy"}), \
                    mock.patch.object(contracts, "publish_allowed", return_value=True), \
                    mock.patch.object(ledger, "book_for", return_value={
                        "book_id": "book-1", "title": "作品"}), \
                    mock.patch.object(manager, "slot_of", return_value="qimao"), \
                    mock.patch.object(manager, "automatic_release_kwargs", return_value={
                        "force": False, "force_confirmed": False,
                        "force_reason": ""}), \
                    mock.patch.object(manager, "_quality_release_guard", return_value={
                        "allowed": True, "blockers": []}), \
                    mock.patch.object(auto, "guards", return_value=(True, "")), \
                    mock.patch.object(auto, "pending", return_value=([{
                        "chapter_no": 11, "file": "chapter-11.md"}], "")), \
                    mock.patch.object(auto, "_batch_slot_lock", return_value=slot_lock), \
                    mock.patch.object(auto, "other_batch_running", return_value="other-task"), \
                    mock.patch.object(auto.time, "time", return_value=1000):
                ok, error = auto.publish_pending_async(
                    "task-busy", "qimao", auto_submit=True, only=[11])
                record = auto._load_batch_jobs()["task-busy"]

        self.assertFalse(ok)
        self.assertIn("另一批自动发布进行中", error)
        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["next_retry_at"], 1060)

    def test_selected_chapter_with_heading_only_number_joins_batch_manifest(self):
        from core.publish import auto
        from core.publish import ledger

        with tempfile.TemporaryDirectory() as workdir:
            chapter = Path(workdir, "draft from editor.md")
            chapter.write_text("# 第12章 暗门\n正文内容", encoding="utf-8")
            task = {"id": "task-1", "workdir": workdir}
            with mock.patch.object(ledger, "published_chapters", return_value=set()), \
                    mock.patch.object(ledger, "drafted_chapters", return_value=set()), \
                    mock.patch.object(ledger, "scheduled_chapters", return_value=set()), \
                    mock.patch.object(ledger, "review_chapters", return_value=set()):
                items = auto._append_selected_unlisted_file(
                    [], task, "qimao", 12, "draft from editor.md")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["chapter_no"], 12)
        self.assertEqual(items[0]["file"], "draft from editor.md")

    def test_selected_chapter_fallback_rejects_mismatched_or_completed_file(self):
        from core.publish import auto
        from core.publish import ledger

        with tempfile.TemporaryDirectory() as workdir:
            chapter = Path(workdir, "draft from editor.md")
            chapter.write_text("# 第12章 暗门\n正文内容", encoding="utf-8")
            task = {"id": "task-1", "workdir": workdir}
            patches = [
                mock.patch.object(ledger, "published_chapters", return_value={12}),
                mock.patch.object(ledger, "drafted_chapters", return_value=set()),
                mock.patch.object(ledger, "scheduled_chapters", return_value=set()),
                mock.patch.object(ledger, "review_chapters", return_value=set()),
            ]
            with patches[0], patches[1], patches[2], patches[3]:
                completed = auto._append_selected_unlisted_file(
                    [], task, "qimao", 12, "draft from editor.md")
            mismatch = auto._append_selected_unlisted_file(
                [], task, "qimao", 13, "draft from editor.md")

        self.assertEqual(completed, [])
        self.assertEqual(mismatch, [])

    def test_interrupted_automatic_batch_is_durably_resumed_once(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir, "batches.json")
            with mock.patch.object(auto, "_BATCH_JOB_FILE", state_file), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch("core.store.get_task", return_value={"id": "task-1"}):
                auto._remember_automatic_batch(
                    "task-1", "qimao", True, False, [12], False, False,
                    "", False, True, "chapter-12.md")
                launch = mock.Mock(return_value=(True, ""))
                with mock.patch.object(auto, "publish_pending_async", launch):
                    self.assertEqual(auto.resume_interrupted_batches(now=1000), 1)
                    auto._running["task-1"] = {"status": "running"}
                    self.assertEqual(auto.resume_interrupted_batches(now=1001), 0)

        launch.assert_called_once_with(
            "task-1", "qimao", auto_submit=True, only=[12], force=False,
            force_confirmed=False, force_reason="", as_draft=False,
            auto_prepare_book=True, scheduled=False,
            _only_file="chapter-12.md")

    def test_legacy_interrupted_batch_is_migrated_to_automatic_submit(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir, "batches.json")
            with mock.patch.object(auto, "_BATCH_JOB_FILE", state_file), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch("core.store.get_task", return_value={"id": "task-1"}):
                auto._remember_automatic_batch(
                    "task-1", "qimao", True, False, [3], False, False,
                    "", False, True, None)
                auto._load_batch_jobs()["task-1"]["params"]["auto_submit"] = False
                auto._save_batch_jobs()
                launch = mock.Mock(return_value=(True, ""))
                with mock.patch.object(auto, "publish_pending_async", launch):
                    self.assertEqual(auto.resume_interrupted_batches(now=1000), 1)

        self.assertTrue(launch.call_args.kwargs["auto_submit"])

    def test_interrupted_batch_start_retries_transient_busy_state(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch("core.store.get_task", return_value={"id": "task-1"}), \
                    mock.patch.object(auto, "publish_pending_async",
                                      return_value=(False, "browser busy")) as launch:
                auto._remember_automatic_batch(
                    "task-1", "fanqie", True, False, None, False, False,
                    "", False, True, None)
                self.assertEqual(auto.resume_interrupted_batches(now=1000), 0)
                self.assertEqual(
                    auto._load_batch_jobs()["task-1"]["next_retry_at"], 1060)
                self.assertEqual(auto.resume_interrupted_batches(now=1059), 0)
                self.assertEqual(auto.resume_interrupted_batches(now=1060), 0)
                self.assertEqual(
                    auto._load_batch_jobs()["task-1"]["next_retry_at"], 1180)
                self.assertEqual(auto.resume_interrupted_batches(now=1179), 0)
                self.assertEqual(auto.resume_interrupted_batches(now=1180), 0)
                self.assertEqual(
                    auto._load_batch_jobs()["task-1"]["next_retry_at"], 1420)

        self.assertEqual(launch.call_count, 3)

    def test_interrupted_batch_start_exception_is_persisted_with_backoff(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch("core.store.get_task", return_value={"id": "task-1"}), \
                    mock.patch.object(auto, "publish_pending_async",
                                      side_effect=RuntimeError("browser busy")) as launch:
                auto._remember_automatic_batch(
                    "task-1", "qimao", True, False, None, False, False,
                    "", False, True, None)
                self.assertEqual(auto.resume_interrupted_batches(now=1000), 0)
                record = auto._load_batch_jobs()["task-1"]
                self.assertEqual(record["status"], "retry_wait")
                self.assertEqual(record["next_retry_at"], 1060)
                self.assertEqual(auto.resume_interrupted_batches(now=1059), 0)

        self.assertEqual(launch.call_count, 1)

    def test_interrupted_batch_rate_limit_remains_durable_until_cooldown(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch("core.store.get_task", return_value={"id": "task-1"}), \
                    mock.patch.object(auto, "publish_pending_async",
                                      return_value=(False,
                                          "HTTP 429: Too Many Requests")) as launch:
                auto._remember_automatic_batch(
                    "task-1", "fanqie", True, False, [9], False, False,
                    "", False, True, None)
                self.assertEqual(auto.resume_interrupted_batches(now=1000), 0)
                record = auto._load_batch_jobs()["task-1"]
                self.assertEqual(record["status"], "retry_wait")
                self.assertEqual(record["error"], "HTTP 429: Too Many Requests")
                self.assertEqual(record["next_retry_at"], 1060)
                self.assertEqual(auto.resume_interrupted_batches(now=1059), 0)
                self.assertEqual(launch.call_count, 1)
                self.assertEqual(auto.resume_interrupted_batches(now=1060), 0)
                record = auto._load_batch_jobs()["task-1"]

        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["next_retry_at"], 1180)
        self.assertEqual(launch.call_count, 2)

    def test_ambiguous_remote_receipt_is_persisted_for_reconciliation_retry(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                auto._remember_automatic_batch(
                    "task-1", "qimao", True, False, [12], False, False,
                    "", False, True, "chapter-12.md")
                self.assertTrue(auto._retain_automatic_batch_for_retry(
                    "task-1", "第 12 章提交结果不确定，暂无法完成平台对账", now=1000))
                record = auto._load_batch_jobs()["task-1"]

        self.assertEqual(record["status"], "retry_wait")
        self.assertEqual(record["next_retry_at"], 1060)
        self.assertIn("提交结果不确定", record["error"])

    def test_retry_wait_is_projected_for_status_without_exposing_batch_params(self):
        from core import store
        from core.publish import auto
        from core.publish import ledger

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                auto._remember_automatic_batch(
                    "task-1", "fanqie", True, False, [12], False, False,
                    "", False, True, "chapter-12.md")
                auto._retain_automatic_batch_for_retry(
                    "task-1", "browser busy", now=1000)

                retry = auto._automatic_retry_status("task-1")
                other = auto._automatic_retry_status("task-2")
                with mock.patch.object(ledger, "books_for", return_value={}), \
                        mock.patch.object(store, "get_task", return_value=None), \
                        mock.patch.object(auto, "_running", {
                            "task-1": {"status": "error", "error": "browser busy"}}):
                    view = auto.status("task-1")

        self.assertEqual(retry, {"platform": "fanqie", "status": "retry_wait",
                                 "error": "browser busy", "attempt": 1,
                                 "next_retry_at": 1060.0})
        self.assertIsNone(other)
        self.assertNotIn("params", retry)
        self.assertEqual(view["retry"], retry)
        self.assertEqual(view["running"]["status"], "error")

    def test_status_keeps_quality_blockers_for_automatic_release(self):
        from core import store
        from core.publish import auto, ledger, manager

        blocked = {"allowed": False, "forced": False,
                   "blockers": ["最近一次质量评审未达标"], "warnings": []}
        with mock.patch.object(ledger, "books_for", return_value={
                "qimao": {"book_id": "book-1", "title": "测试作品"}}), \
                mock.patch.object(store, "get_task", return_value={
                    "id": "task-1", "type": "serial_novel"}), \
                mock.patch.object(auto, "pending", return_value=([], "")), \
                mock.patch.object(auto, "guards", return_value=(True, "")), \
                mock.patch.object(auto, "calibrated", return_value=True), \
                mock.patch.object(auto, "_running", {}), \
                mock.patch.object(ledger, "published_chapters", return_value=set()), \
                mock.patch.object(ledger, "drafted_chapters", return_value=set()), \
                mock.patch.object(ledger, "scheduled_chapters", return_value=set()), \
                mock.patch.object(ledger, "review_chapters", return_value=set()), \
                mock.patch.object(manager, "automatic_release_kwargs", return_value={
                    "force": False, "force_confirmed": False,
                    "force_reason": ""}) as flags, \
                mock.patch.object(manager, "_quality_release_guard",
                                  return_value=blocked) as quality_guard:
            view = auto.status("task-1")

        flags.assert_called_once_with(auto_submit=True, as_draft=False)
        quality_guard.assert_called_once_with(
            {"id": "task-1", "type": "serial_novel"}, "qimao",
            action="publish", force=False, force_confirmed=False,
            force_reason="")
        book = view["books"][0]
        self.assertFalse(book["guard_ok"])
        self.assertEqual(book["quality_blockers"], ["最近一次质量评审未达标"])
        self.assertEqual(book["quality_warnings"], [])
        self.assertFalse(book["quality_forced"])

    def test_automatic_release_does_not_manufacture_force_confirmation(self):
        from core.publish import manager

        self.assertEqual(manager.automatic_release_kwargs(
            auto_submit=True, as_draft=False), {
                "force": False, "force_confirmed": False, "force_reason": ""})
        explicit = manager.automatic_release_kwargs(
            auto_submit=True, force=True, force_confirmed=True,
            force_reason="人工复核后放行", as_draft=False)
        self.assertEqual(explicit, {
            "force": True, "force_confirmed": True,
            "force_reason": "人工复核后放行"})

    def test_metadata_and_chapter_timeouts_are_persisted_for_auto_retry(self):
        from core.publish import auto

        errors = (
            "自动生成qimao作品信息超时",
            "自动创建fanqie作品超时",
            "第 12 章发布等待超时",
            "第 12 章流程结束，但未确认平台发布成功；后续章节已停止",
            "今日已发 10 章（上限 10），为防风控明天再发",
        )
        for index, error in enumerate(errors):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp_dir:
                with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                       Path(temp_dir, "batches.json")), \
                        mock.patch.object(auto, "_BATCH_JOBS", None):
                    auto._remember_automatic_batch(
                        "task-%d" % index, "qimao", True, False, [12],
                        False, False, "", False, True, "chapter-12.md")
                    self.assertTrue(auto._retain_automatic_batch_for_retry(
                        "task-%d" % index, error, now=1000))
                    record = auto._load_batch_jobs()["task-%d" % index]

                self.assertEqual(record["status"], "retry_wait")
                self.assertEqual(record["next_retry_at"], 1060)
                self.assertEqual(record["error"], error)

    def test_direct_publish_timeout_message_has_no_manual_submit_handoff(self):
        from core.publish import auto

        error = "第 12 章平台动作等待超时（5 分钟）；已保留自动恢复记录，稍后会自动对账重试"
        self.assertTrue(auto._scheduled_error_is_persistent(error))
        self.assertNotIn("人工提交", error)

    def test_draft_only_batches_are_durable_and_never_resumed_as_publications(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(auto, "_BATCH_JOB_FILE",
                                   Path(temp_dir, "batches.json")), \
                    mock.patch.object(auto, "_BATCH_JOBS", None):
                auto._remember_automatic_batch(
                    "task-1", "qimao", False, True, None, False, False,
                    "", False, False, None)
                record = auto._load_batch_jobs()["task-1"]
                self.assertFalse(record["params"]["auto_submit"])
                self.assertTrue(record["params"]["as_draft"])

    def test_interrupted_draft_batch_resumes_as_draft_only(self):
        from core.publish import auto

        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir, "batches.json")
            with mock.patch.object(auto, "_BATCH_JOB_FILE", state_file), \
                    mock.patch.object(auto, "_BATCH_JOBS", None), \
                    mock.patch.object(auto, "_running", {}), \
                    mock.patch("core.store.get_task", return_value={"id": "task-1"}):
                auto._remember_automatic_batch(
                    "task-1", "qimao", False, True, [12], False, False,
                    "", False, False, "chapter-12.md")
                # A stale flag from an older/manual edit must not turn a
                # resumed draft batch into a publication.
                auto._load_batch_jobs()["task-1"]["params"]["auto_submit"] = True
                auto._save_batch_jobs()
                launch = mock.Mock(return_value=(True, ""))
                with mock.patch.object(auto, "publish_pending_async", launch):
                    self.assertEqual(auto.resume_interrupted_batches(now=1000), 1)
                self.assertFalse(auto._load_batch_jobs()["task-1"]["params"]["auto_submit"])

        launch.assert_called_once_with(
            "task-1", "qimao", auto_submit=False, only=[12], force=False,
            force_confirmed=False, force_reason="", as_draft=True,
            auto_prepare_book=False, scheduled=False,
            _only_file="chapter-12.md")

    def test_pending_excludes_chapters_already_waiting_for_review(self):
        from core import store
        from core.publish import auto, ledger

        with tempfile.TemporaryDirectory() as workdir:
            Path(workdir, "第4章 已提交.md").write_text("正文", encoding="utf-8")
            task = {"id": "task-1", "workdir": workdir}
            with mock.patch.object(store, "get_task", return_value=task), \
                    mock.patch.object(store, "task_runs", return_value=[]), \
                    mock.patch.object(ledger, "published_chapters", return_value=set()), \
                    mock.patch.object(ledger, "drafted_chapters", return_value=set()), \
                    mock.patch.object(ledger, "scheduled_chapters", return_value=set()), \
                    mock.patch.object(ledger, "review_chapters", return_value={4}), \
                    mock.patch("core.publish.volumes.plan_for_task", return_value=[]):
                items, error = auto.pending("task-1", "qimao")

        self.assertEqual(error, "")
        self.assertEqual(items, [])

    def test_unattended_platform_release_requires_contract_approval(self):
        from core import contracts

        with mock.patch.object(contracts, "release_allowed", return_value=False):
            self.assertFalse(contracts.publish_allowed("task-1", unattended=True))

        with mock.patch.object(contracts, "release_allowed", return_value=True):
            self.assertTrue(contracts.publish_allowed("task-1", unattended=True))

    def test_automatic_batch_stops_before_side_effect_when_approval_is_missing(self):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        with mock.patch.object(auto, "calibrated", return_value=True), \
                mock.patch.object(store, "get_task", return_value={"id": "task-approval"}), \
                mock.patch.object(contracts, "publish_allowed", return_value=False), \
                mock.patch.object(ledger, "book_for") as book_for, \
                mock.patch.object(manager, "slot_of") as slot_of:
            ok, error = auto.publish_pending_async(
                "task-approval", "qimao", auto_submit=True,
                auto_prepare_book=False)

        self.assertFalse(ok)
        self.assertIn("先完成审批", error)
        book_for.assert_not_called()
        slot_of.assert_not_called()

    def test_direct_create_book_checks_contract_before_metadata_or_browser(self):
        from core import contracts, store
        from core.publish import ledger, manager

        with mock.patch.object(store, "get_task", return_value={"id": "task-approval"}), \
                mock.patch.object(contracts, "publish_allowed", return_value=False), \
                mock.patch.object(store, "inherited_book_meta") as inherited, \
                mock.patch.object(ledger, "book_for") as book_for:
            ok, error = manager.create_book_async("task-approval", "qimao")

        self.assertFalse(ok)
        self.assertIn("先完成审批", error)
        inherited.assert_not_called()
        book_for.assert_not_called()

    def test_direct_chapter_release_checks_contract(self):
        from core import contracts, store
        from core.publish import ledger, manager

        with mock.patch.object(store, "get_task", return_value={"id": "task-approval"}), \
                mock.patch.object(contracts, "publish_allowed", return_value=False), \
                mock.patch.object(ledger, "book_for") as book_for:
            ok, error = manager.upload_chapter_async(
                "task-approval", "qimao", "missing.md")

        self.assertFalse(ok)
        self.assertIn("先完成审批", error)
        book_for.assert_not_called()

    def test_non_automatic_release_keeps_contract_approval_gate(self):
        from core import contracts

        with mock.patch.object(contracts, "release_allowed", return_value=False):
            self.assertFalse(contracts.publish_allowed("task-1", unattended=False))

    def test_transient_start_failures_are_retryable_for_publication(self):
        from core.publish import auto

        self.assertTrue(auto.retryable_publish_start(True, False, "连接被关闭"))
        self.assertTrue(auto.retryable_publish_start(False, True, "连接被关闭"))
        self.assertFalse(auto.retryable_publish_start(True, False, "章节正文过短"))
        self.assertFalse(auto.retryable_publish_start(False, False, "连接被关闭"))

    def test_chapter_start_reopens_saved_profile_and_rechecks_before_retry(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.calls = 0
                self.connected = False

            def upload_chapter_async(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return False, "平台未登录（当前页面 /login），请先点连接平台"
                return True, ""

            def connect(self, platform, account="", unattended=False):
                self.connected = True
                return True, ""

            def _st(self, slot):
                return {"status": "connected" if self.connected else "waiting_login"}

        manager = Manager()
        with mock.patch.object(auto, "_cancelled", return_value=False), \
                mock.patch.object(auto.time, "sleep"):
            ok, error = auto._start_upload_with_login_recovery(
                manager, "task-1", "fanqie", "chapter-1.txt", "", "fanqie", {})

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(manager.calls, 2)

    def test_browser_startup_and_page_load_errors_reopen_saved_profile(self):
        from core.publish import auto

        class Manager:
            def __init__(self, first_error):
                self.calls = 0
                self.first_error = first_error
                self.connected = False
                self.connect_calls = 0

            def upload_chapter_async(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return False, self.first_error
                return True, ""

            def connect(self, platform, account="", unattended=False):
                self.connect_calls += 1
                self.connected = True
                return True, ""

            def _st(self, slot):
                return {"status": "connected" if self.connected else "waiting_login"}

        errors = (
            "页面 30 秒未完成加载：https://fanqienovel.com/author",
            "浏览器调试端口 15 秒内没就绪",
            "端口 1234 上没有活着的浏览器",
            "浏览器启动失败：权限不足",
        )
        for error in errors:
            with self.subTest(error=error):
                manager = Manager(error)
                with mock.patch.object(auto, "_cancelled", return_value=False), \
                        mock.patch.object(auto.time, "sleep"):
                    ok, reason = auto._start_upload_with_login_recovery(
                        manager, "task-1", "fanqie", "chapter-1.txt", "",
                        "fanqie", {})
                self.assertTrue(ok)
                self.assertEqual(reason, "")
                self.assertEqual(manager.connect_calls, 1)
                self.assertEqual(manager.calls, 2)

    def test_persistent_unattended_start_failure_retries_until_session_recovers(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.calls = 0

            def upload_chapter_async(self, *args, **kwargs):
                self.calls += 1
                if self.calls < 3:
                    return False, "无法连接浏览器：CDP target closed"
                return True, ""

        manager = Manager()
        waits = []
        with mock.patch.object(auto, "_batch_sleep",
                               side_effect=lambda _task, seconds: waits.append(seconds) or True):
            ok, error = auto._start_upload_with_retry(
                manager, "task-1", "fanqie", "chapter-1.txt", "", "fanqie",
                {}, status={})

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(manager.calls, 3)
        self.assertEqual(waits, [60, 120])

    def test_auto_book_preparation_retries_transient_start_failure(self):
        from core import store
        from core.publish import auto, ledger, manager

        task = {"id": "task-1", "auto_publish": {"account": ""}}
        books = [None, None, {"book_id": "book-1", "source": "create"}]
        create_results = [(False, "无法连接浏览器：CDP target closed"),
                          (True, "")]
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(store, "serial_chain_ids", return_value=["task-1"]), \
                mock.patch.object(store, "inherited_book_meta", return_value={
                    "status": "done", "data": {"book_name": "作品"}}), \
                mock.patch.object(ledger, "book_for", side_effect=books), \
                mock.patch.object(manager, "slot_of", return_value="qimao"), \
                mock.patch.object(manager, "create_book_async",
                                  side_effect=create_results) as create, \
                mock.patch.object(manager, "connect", return_value=(True, "")), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(auto, "_batch_sleep", return_value=True), \
                mock.patch.object(auto.time, "sleep"):
            ok, error = auto._prepare_book("task-1", "qimao")

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(create.call_count, 2)

    def test_auto_book_preparation_recovers_existing_title_only_binding(self):
        from core import store
        from core.publish import auto, ledger, manager

        task = {"id": "task-legacy", "auto_publish": {"account": "writer"}}
        existing = {"title": "已登记作品", "source": "manual",
                    "account": "writer"}
        page = object()
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(ledger, "book_for", return_value=existing), \
                mock.patch.object(manager, "slot_of", return_value="qimao@writer"), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager, "_check_login", return_value=(True, "url")), \
                mock.patch.object(manager, "_resolve_book_id", return_value="book-42") as resolve, \
                mock.patch.object(ledger, "save_book") as save, \
                mock.patch.object(manager, "create_book_async") as create:
            ok, error = auto._prepare_book("task-legacy", "qimao")

        self.assertTrue(ok, error)
        self.assertEqual(error, "")
        resolve.assert_called_once_with("qimao", page, existing)
        save.assert_called_once_with("task-legacy", "qimao", {
            "title": "已登记作品", "source": "manual", "account": "writer",
            "book_id": "book-42"})
        create.assert_not_called()

    def test_create_book_reconciles_remote_title_before_submitting_new_form(self):
        from core import operations, store
        from core.publish import ledger, manager

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        lock = mock.Mock()
        page = object()
        with mock.patch.object(store, "get_task", return_value={"id": "task-1"}), \
                mock.patch.object(store, "inherited_book_meta", return_value={
                    "status": "done", "data": {"book_name": "已有作品"}}), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(ledger, "recent", return_value=[]), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(manager, "_create_preflight", return_value=""), \
                mock.patch.object(manager, "automatic_release_kwargs", return_value={}), \
                mock.patch.object(manager, "_quality_release_guard", return_value={
                    "allowed": True}), \
                mock.patch.object(manager, "_try_action_lock", return_value=lock), \
                mock.patch.object(manager, "_login_guard", return_value=(True, "")), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager.PLATFORMS["qimao"], "reconcile_book_id",
                                  return_value=(True, "book-existing", "")) as resolve, \
                mock.patch.object(manager.threading, "Thread", ImmediateThread), \
                mock.patch.object(operations, "begin", return_value="op-1"), \
                mock.patch.object(operations, "confirm") as confirm, \
                mock.patch.object(manager.flow, "run_flow") as run_flow, \
                mock.patch.object(ledger, "record") as record, \
                mock.patch.object(ledger, "save_book") as save_book, \
                mock.patch.object(manager, "_set"), \
                mock.patch.object(manager, "_save"):
            ok, error = manager.create_book_async("task-1", "qimao")

        self.assertTrue(ok, error)
        self.assertEqual(error, "")
        resolve.assert_called_once_with(page, {"title": "已有作品"})
        run_flow.assert_not_called()
        confirm.assert_called_once_with("op-1", remote_receipt="book-existing",
                                        metadata={"platform": "qimao",
                                                  "action": "create_book"})
        save_book.assert_called_once_with("task-1", "qimao", {
            "book_id": "book-existing", "title": "已有作品", "source": "create"},
            account="")
        record.assert_called_once()

    def test_create_book_does_not_submit_when_remote_reconciliation_is_inconclusive(self):
        from core import operations, store
        from core.publish import ledger, manager

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        page = object()
        with mock.patch.object(store, "get_task", return_value={"id": "task-1"}), \
                mock.patch.object(store, "inherited_book_meta", return_value={
                    "status": "done", "data": {"book_name": "已有作品"}}), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(ledger, "recent", return_value=[]), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(manager, "_create_preflight", return_value=""), \
                mock.patch.object(manager, "automatic_release_kwargs", return_value={}), \
                mock.patch.object(manager, "_quality_release_guard", return_value={"allowed": True}), \
                mock.patch.object(manager, "_try_action_lock", return_value=mock.Mock()), \
                mock.patch.object(manager, "_login_guard", return_value=(True, "")), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager.PLATFORMS["qimao"], "reconcile_book_id",
                                  return_value=(False, "", "作品列表未完整加载")), \
                mock.patch.object(manager.threading, "Thread", ImmediateThread), \
                mock.patch.object(operations, "begin", return_value="op-1"), \
                mock.patch.object(operations, "finish_exception") as finish_exception, \
                mock.patch.object(operations, "get", return_value={"status": "failed"}), \
                mock.patch.object(ledger, "record"), \
                mock.patch.object(manager, "_set"), \
                mock.patch.object(manager, "_save"), \
                mock.patch.object(manager.flow, "run_flow") as run_flow:
            ok, error = manager.create_book_async("task-1", "qimao")

        self.assertTrue(ok, error)
        run_flow.assert_not_called()
        finish_exception.assert_called_once()

    def test_create_book_releases_action_lock_when_worker_cannot_start(self):
        from core import operations, store
        from core.publish import ledger, manager

        class BrokenThread:
            def __init__(self, target, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        lock = mock.Mock()
        with mock.patch.object(store, "get_task", return_value={"id": "task-1"}), \
                mock.patch.object(store, "inherited_book_meta", return_value={
                    "status": "done", "data": {"book_name": "作品"}}), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(manager, "_create_preflight", return_value=""), \
                mock.patch.object(manager, "automatic_release_kwargs", return_value={}), \
                mock.patch.object(manager, "_quality_release_guard", return_value={"allowed": True}), \
                mock.patch.object(manager, "_try_action_lock", return_value=lock), \
                mock.patch.object(manager, "_login_guard", return_value=(True, "")), \
                mock.patch.object(manager, "_open_page") as open_page, \
                mock.patch.object(operations, "begin", return_value="op-1"), \
                mock.patch.object(manager.threading, "Thread", BrokenThread), \
                mock.patch.object(manager, "_set") as set_status, \
                mock.patch.object(manager, "_save"):
            ok, error = manager.create_book_async("task-1", "qimao")

        self.assertFalse(ok)
        self.assertIn("任务启动失败", error)
        lock.release.assert_called_once()
        open_page.assert_not_called()
        self.assertTrue(any(call.kwargs.get("status") == "connected"
                            for call in set_status.call_args_list))

    def test_platform_reconciliation_only_confirms_miss_on_loaded_works_list(self):
        from core.publish import fanqie, qimao

        class FakePage:
            def __init__(self, probe):
                self.probe = probe

            def navigate(self, *_args, **_kwargs):
                return None

            def real_click_text(self, *_args, **_kwargs):
                return {"ok": False, "err": "not found"}

            def call(self, *_args, **_kwargs):
                return self.probe

        with mock.patch("time.sleep"):
            self.assertEqual(qimao.reconcile_book_id(
                FakePage({"ready": True, "create": True, "loading": False}),
                {"title": "新书"}), (False, "", ""))
            self.assertIn("列表未完整加载", qimao.reconcile_book_id(
                FakePage({"ready": False, "create": False, "loading": True}),
                {"title": "新书"})[2])
            self.assertIn("已显示同名作品", qimao.reconcile_book_id(
                FakePage({"ready": True, "create": True, "loading": False,
                          "titleVisible": True}), {"title": "新书"})[2])
            self.assertEqual(fanqie.reconcile_book_id(
                FakePage({"ready": True, "create": True, "loading": False}),
                {"title": "新书"}), (False, "", ""))
            self.assertIn("列表未完整加载", fanqie.reconcile_book_id(
                FakePage({"ready": False, "create": False, "loading": True}),
                {"title": "新书"})[2])
            self.assertIn("已显示同名作品", fanqie.reconcile_book_id(
                FakePage({"ready": True, "create": True, "loading": False,
                          "titleVisible": True}), {"title": "新书"})[2])

    def test_auto_book_preparation_does_not_retry_content_errors(self):
        from core import store
        from core.publish import auto, ledger, manager

        task = {"id": "task-1", "auto_publish": {"account": ""}}
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(store, "serial_chain_ids", return_value=["task-1"]), \
                mock.patch.object(store, "inherited_book_meta", return_value={
                    "status": "done", "data": {"book_name": "作品"}}), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(manager, "slot_of", return_value="qimao"), \
                mock.patch.object(manager, "create_book_async",
                                  return_value=(False, "作品名不符合平台要求")) as create, \
                mock.patch.object(auto, "_batch_sleep") as wait:
            ok, error = auto._prepare_book("task-1", "qimao")

        self.assertFalse(ok)
        self.assertEqual(error, "作品名不符合平台要求")
        create.assert_called_once()
        wait.assert_not_called()

    def test_auto_book_preparation_retries_transient_metadata_failure(self):
        from core import bookmeta, store
        from core.publish import auto, ledger, manager

        task = {"id": "task-1", "auto_publish": {"account": ""}}
        states = [
            {"status": "missing"},
            {"status": "failed", "error": "网络错误：模型服务暂时不可用"},
            {"status": "done", "data": {"book_name": "作品"}},
        ]
        generate = mock.Mock()
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(store, "serial_chain_ids", return_value=["task-1"]), \
                mock.patch.object(store, "inherited_book_meta",
                                  side_effect=lambda *_args: states.pop(0)), \
                mock.patch.object(store, "set_book_meta") as set_meta, \
                mock.patch.object(ledger, "book_for", side_effect=[None, {
                    "book_id": "book-1", "source": "create"}]), \
                mock.patch.object(manager, "slot_of", return_value="qimao"), \
                mock.patch.object(manager, "create_book_async", return_value=(True, "")), \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(bookmeta, "generate_async", generate), \
                mock.patch.object(bookmeta, "_resolve_author", return_value="author"), \
                mock.patch.object(auto, "_batch_sleep", return_value=True) as wait, \
                mock.patch.object(auto.time, "sleep"):
            ok, error = auto._prepare_book("task-1", "qimao")

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(set_meta.call_count, 2)
        wait.assert_called_once_with("task-1", 60)

    def test_auto_book_preparation_resume_waits_for_existing_metadata_worker(self):
        from core import bookmeta, store
        from core.publish import auto, ledger, manager

        task = {"id": "task-1", "auto_publish": {"account": ""}}
        states = [
            {"status": "running"},  # first batch's initial check
            {"status": "running"},  # timed-out batch's only poll
            {"status": "running"},  # resumed batch's initial check
            {"status": "done", "data": {"book_name": "作品"}},
        ]
        generate = mock.Mock()
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(store, "serial_chain_ids", return_value=["task-1"]), \
                mock.patch.object(store, "inherited_book_meta",
                                  side_effect=lambda *_args: states.pop(0)), \
                mock.patch.object(store, "set_book_meta") as set_meta, \
                mock.patch.object(ledger, "book_for", side_effect=[None, None, {
                    "book_id": "book-1", "source": "create"}]), \
                mock.patch.object(manager, "slot_of", return_value="qimao"), \
                mock.patch.object(manager, "create_book_async", return_value=(True, "")) as create, \
                mock.patch.object(manager, "_st", return_value={"status": "connected"}), \
                mock.patch.object(bookmeta, "generate_async", generate), \
                mock.patch.object(bookmeta, "_resolve_author", return_value="author"), \
                mock.patch.object(auto.time, "sleep"):
            with mock.patch.object(auto, "BOOK_PREP_TIMEOUT_S", 0):
                first_ok, first_error = auto._prepare_book("task-1", "qimao")
            self.assertFalse(first_ok)
            self.assertIn("超时", first_error)
            generate.assert_not_called()
            set_meta.assert_not_called()
            create.assert_not_called()

            with mock.patch.object(auto, "BOOK_PREP_TIMEOUT_S", 10):
                ok, error = auto._prepare_book("task-1", "qimao")

        self.assertTrue(ok, error)
        self.assertEqual(error, "")
        generate.assert_not_called()
        set_meta.assert_not_called()
        create.assert_called_once()

    def test_unattended_start_retry_stops_on_nonrecoverable_content_error(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.calls = 0

            def upload_chapter_async(self, *args, **kwargs):
                self.calls += 1
                return False, "章节正文过短"

        manager = Manager()
        with mock.patch.object(auto, "_batch_sleep") as wait:
            ok, error = auto._start_upload_with_retry(
                manager, "task-1", "qimao", "chapter-1.txt", "", "qimao", {})

        self.assertFalse(ok)
        self.assertEqual(error, "章节正文过短")
        self.assertEqual(manager.calls, 1)
        wait.assert_not_called()

    def test_quality_gate_start_rejection_returns_to_batch_finalizer(self):
        from core.publish import auto

        class Manager:
            def upload_chapter_async(self, *args, **kwargs):
                return False, "质量门禁拦截：目标章节超出最近评审版本"

        with mock.patch.object(auto, "_batch_sleep") as wait:
            ok, error = auto._start_upload_with_retry(
                Manager(), "task-1", "qimao", "chapter-1.txt", "", "qimao", {})

        self.assertFalse(ok)
        self.assertIn("质量门禁拦截", error)
        wait.assert_not_called()

    def test_unattended_start_retry_has_a_finite_worker_budget(self):
        from core.publish import auto

        class Manager:
            def upload_chapter_async(self, *args, **kwargs):
                return False, "browser busy"

        waits = []

        def wait(_task, seconds):
            if len(waits) >= 2:
                raise AssertionError("worker retry loop did not stop")
            waits.append(seconds)
            return True

        with mock.patch.object(auto, "_batch_sleep", side_effect=wait):
            ok, error = auto._start_upload_with_retry(
                Manager(), "task-1", "fanqie", "chapter-1.txt", "", "fanqie", {})

        self.assertFalse(ok)
        self.assertIn("重试次数已达上限", error)
        self.assertEqual(waits, [60, 120])

    def test_unattended_start_retry_honors_batch_cancellation(self):
        from core.publish import auto

        class Manager:
            def upload_chapter_async(self, *args, **kwargs):
                return False, "无法连接浏览器：CDP target closed"

        with mock.patch.object(auto, "_batch_sleep", return_value=False):
            ok, error = auto._start_upload_with_retry(
                Manager(), "task-1", "fanqie", "chapter-1.txt", "", "fanqie", {})

        self.assertFalse(ok)
        self.assertIn("已取消", error)

    def test_platform_reconciliation_reopens_saved_session_and_retries(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.sync_calls = 0
                self.connect_calls = []

            def sync_published(self, task_id, platform, manual=False):
                self.sync_calls += 1
                if self.sync_calls == 1:
                    return False, "浏览器未连接，点连接平台后再校准"
                return True, ""

            def connect(self, platform, account="", unattended=False):
                self.connect_calls.append((platform, account, unattended))
                return True, ""

        manager = Manager()
        waits = []
        with mock.patch.object(auto, "_batch_sleep",
                               side_effect=lambda _task, seconds: waits.append(seconds) or True):
            ok, error = auto._sync_with_session_recovery(
                manager, "task-1", "fanqie", "writer", status={})

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(manager.sync_calls, 2)
        self.assertEqual(manager.connect_calls,
                         [("fanqie", "writer", True)])
        self.assertEqual(waits, [60])

    def test_platform_reconciliation_stops_on_configuration_error(self):
        from core.publish import auto

        class Manager:
            def sync_published(self, task_id, platform, manual=False):
                return False, "章节列表结构无法解析"

            def connect(self, *args, **kwargs):
                raise AssertionError("configuration failures must not relaunch a browser")

        with mock.patch.object(auto, "_batch_sleep") as wait:
            ok, error = auto._sync_with_session_recovery(
                Manager(), "task-1", "qimao", "", status={})

        self.assertFalse(ok)
        self.assertEqual(error, "章节列表结构无法解析")
        wait.assert_not_called()

    def test_platform_reconciliation_retry_honors_batch_cancellation(self):
        from core.publish import auto

        class Manager:
            def sync_published(self, task_id, platform, manual=False):
                return False, "浏览器未连接"

            def connect(self, platform, account="", unattended=False):
                return True, ""

        with mock.patch.object(auto, "_batch_sleep", return_value=False):
            ok, error = auto._sync_with_session_recovery(
                Manager(), "task-1", "fanqie", "", status={})

        self.assertFalse(ok)
        self.assertIn("已取消", error)

    def test_chapter_login_recovery_stops_on_terminal_profile_error(self):
        from core.publish import auto

        class Manager:
            def upload_chapter_async(self, *args, **kwargs):
                return False, "平台未登录"

            def connect(self, platform, account="", unattended=False):
                return True, ""

            def _st(self, slot):
                return {"status": "error", "error": "登录态已失效"}

        with mock.patch.object(auto, "_cancelled", return_value=False):
            ok, error = auto._start_upload_with_login_recovery(
                Manager(), "task-1", "qimao", "chapter-1.txt", "", "qimao", {})

        self.assertFalse(ok)
        self.assertEqual(error, "登录态已失效")

    def test_chapter_login_recovery_returns_immediately_when_browser_cannot_start(self):
        from core.publish import auto

        class Manager:
            def upload_chapter_async(self, *args, **kwargs):
                return False, "平台未登录"

            def connect(self, platform, account="", unattended=False):
                return False, "Chromium 启动失败"

            def _st(self, slot):
                raise AssertionError("a failed connect must not enter the wait loop")

        with mock.patch.object(auto, "LOGIN_RECOVERY_TIMEOUT_S", 900):
            ok, error = auto._start_upload_with_login_recovery(
                Manager(), "task-1", "qimao", "chapter-1.txt", "", "qimao", {})

        self.assertFalse(ok)
        self.assertIn("Chromium 启动失败", error)

    def test_browser_preflight_failure_reopens_saved_profile(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.calls = 0
                self.connected = False

            def upload_chapter_async(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return False, "浏览器不可用：CDP 目标已关闭"
                return True, ""

            def connect(self, platform, account="", unattended=False):
                self.connected = True
                return True, ""

            def _st(self, slot):
                return {"status": "connected" if self.connected else "waiting_login"}

        manager = Manager()
        with mock.patch.object(auto, "_cancelled", return_value=False), \
                mock.patch.object(auto.time, "sleep"):
            ok, error = auto._start_upload_with_login_recovery(
                manager, "task-1", "fanqie", "chapter-1.txt", "", "fanqie", {})

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(manager.calls, 2)

    def test_upload_start_exception_reopens_saved_profile(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.calls = 0
                self.connected = False

            def upload_chapter_async(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("CDP target closed")
                return True, ""

            def connect(self, platform, account="", unattended=False):
                self.connected = True
                return True, ""

            def _st(self, slot):
                return {"status": "connected" if self.connected else "waiting_login"}

        manager = Manager()
        with mock.patch.object(auto, "_cancelled", return_value=False), \
                mock.patch.object(auto.time, "sleep"):
            ok, error = auto._start_upload_with_login_recovery(
                manager, "task-1", "fanqie", "chapter-1.txt", "", "fanqie", {})

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(manager.calls, 2)

    def test_platform_reconciliation_exception_uses_backoff(self):
        from core.publish import auto

        class Manager:
            def __init__(self):
                self.calls = 0

            def sync_published(self, task_id, platform, manual=False):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("browser busy")
                return True, ""

        manager = Manager()
        waits = []
        with mock.patch.object(auto, "_batch_sleep",
                               side_effect=lambda _task, seconds: waits.append(seconds) or True):
            ok, error = auto._sync_with_session_recovery(
                manager, "task-1", "fanqie", "writer", status={})

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(manager.calls, 2)
        self.assertEqual(waits, [60])

    def test_ambiguous_chapter_result_is_reconciled_before_retry(self):
        from core.publish import auto

        class Manager:
            def __init__(self, result):
                self.result = result
                self.calls = []

            def sync_published(self, task_id, platform, manual=False):
                self.calls.append((task_id, platform, manual))
                return self.result

        manager = Manager((True, ""))
        from core.publish import ledger
        with mock.patch.object(ledger, "published_chapters",
                               return_value=[7]), \
                mock.patch.object(ledger, "scheduled_chapters",
                                  return_value=[]):
            safe, present, error = auto._reconcile_chapter_before_retry(
                manager, "task-1", "fanqie", 7)

        self.assertTrue(safe)
        self.assertTrue(present)
        self.assertEqual(error, "平台已确认该章发布成功")
        self.assertEqual(manager.calls, [("task-1", "fanqie", False)])

    def test_failed_chapter_reconciliation_blocks_duplicate_retry(self):
        from core.publish import auto

        class Manager:
            def sync_published(self, task_id, platform, manual=False):
                return False, "浏览器未连接"

        from core.publish import ledger
        with mock.patch.object(ledger, "published_chapters",
                               return_value=[]), \
                mock.patch.object(ledger, "scheduled_chapters",
                                  return_value=[]):
            safe, present, error = auto._reconcile_chapter_before_retry(
                Manager(), "task-1", "qimao", 3)

        self.assertFalse(safe)
        self.assertFalse(present)
        self.assertIn("浏览器未连接", error)

    def test_ambiguous_reconciliation_recovers_saved_session(self):
        from core.publish import auto
        from core.publish import ledger

        class Manager:
            def __init__(self):
                self.sync_calls = 0
                self.connect_calls = []

            def sync_published(self, task_id, platform, manual=False):
                self.sync_calls += 1
                if self.sync_calls <= 2:
                    return False, "浏览器未连接"
                return True, ""

            def connect(self, platform, account="", unattended=False):
                self.connect_calls.append((platform, account, unattended))
                return True, ""

        manager = Manager()
        with mock.patch.object(auto, "_batch_sleep", return_value=True) as wait, \
                mock.patch.object(ledger, "published_chapters", return_value=set()), \
                mock.patch.object(ledger, "scheduled_chapters", return_value=set()), \
                mock.patch.object(ledger, "review_chapters", return_value={7}):
            safe, present, error = auto._reconcile_chapter_before_retry(
                manager, "task-1", "fanqie", 7, account="writer")

        self.assertTrue(safe)
        self.assertTrue(present)
        self.assertEqual(error, "平台已确认该章进入审核队列")
        self.assertEqual(manager.connect_calls, [("fanqie", "writer", True)])
        wait.assert_called_once_with("task-1", 60)

    def test_unavailable_remote_counter_blocks_ambiguous_retry(self):
        from core.publish import auto, ledger

        class Manager:
            def sync_published(self, task_id, platform, manual=False):
                return False, "该平台未配置章节计数"

        with mock.patch.object(ledger, "published_chapters", return_value=[]), \
                mock.patch.object(ledger, "scheduled_chapters", return_value=[]):
            safe, present, error = auto._reconcile_chapter_before_retry(
                Manager(), "task-1", "qimao", 4)

        self.assertFalse(safe)
        self.assertFalse(present)
        self.assertIn("未配置章节计数", error)

    def test_review_queue_receipt_prevents_ambiguous_resubmission(self):
        from core.publish import auto, ledger
        from core.publish.chaprows import bucket_chapter_rows

        rows = bucket_chapter_rows(["第4章 测试 0 待审核"])
        self.assertEqual(rows["review_nos"], [4])

        class Manager:
            def sync_published(self, task_id, platform, manual=False):
                return True, ""

        with mock.patch.object(ledger, "published_chapters", return_value=[]), \
                mock.patch.object(ledger, "scheduled_chapters", return_value=[]), \
                mock.patch.object(ledger, "review_chapters", return_value={4}):
            safe, present, message = auto._reconcile_chapter_before_retry(
                Manager(), "task-1", "qimao", 4)

        self.assertTrue(safe)
        self.assertTrue(present)
        self.assertIn("审核队列", message)

    def test_draft_queue_is_a_remote_receipt_for_recovery(self):
        from core.publish import ledger, manager
        from core.publish.chaprows import bucket_chapter_rows

        rows = bucket_chapter_rows(["第4章 测试 0 草稿"])
        self.assertEqual(rows["draft_nos"], [4])
        self.assertEqual(rows["draft"], 1)

        pending = {"task_id": "task-1", "platform": "qimao",
                   "chapter_no": 4, "as_draft": True}
        with mock.patch.object(manager, "sync_published",
                               return_value=(True, "")), \
                mock.patch.object(ledger, "drafted_chapters",
                               return_value={4}), \
                mock.patch.object(ledger, "published_chapters",
                               return_value=set()), \
                mock.patch.object(ledger, "scheduled_chapters",
                               return_value=set()), \
                mock.patch.object(ledger, "review_chapters",
                               return_value=set()):
            self.assertTrue(manager._upload_receipt_exists(pending))

    def test_draft_ledger_includes_calibrated_remote_draft_numbers(self):
        from core import store
        from core.publish import ledger

        with mock.patch.object(ledger, "_iter_records", return_value=[]), \
                mock.patch.object(store, "serial_chain_ids",
                                   return_value={"task-1"}), \
                mock.patch.object(ledger, "book_for",
                                   return_value={"remote_draft_nos": [7]}):
            self.assertEqual(ledger.drafted_chapters("task-1", "qimao"), {7})

    def test_book_preparation_reserves_account_slot_for_other_tasks(self):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        class DeferredThread:
            starts = 0

            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                type(self).starts += 1

        auto._running.clear()
        auto._BATCH_SLOT_LOCKS.clear()
        task = {"id": "task-1", "auto_publish": {"account": "writer"}}
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(auto, "_BATCH_JOB_FILE",
                                  Path(temp_dir) / "batches.json"), \
                mock.patch.object(auto, "_BATCH_JOBS", None), \
                mock.patch.object(auto, "_cancel_events", {}), \
                mock.patch.object(auto, "calibrated", return_value=True), \
                mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=None), \
                mock.patch.object(manager, "slot_of", return_value="fanqie@writer"), \
                mock.patch.object(manager, "load_accounts", return_value=[]), \
                mock.patch.object(auto.threading, "Thread", DeferredThread):
            first = auto.publish_pending_async(
                "task-1", "fanqie", auto_prepare_book=True)
            second_task = dict(task, id="task-2")
            with mock.patch.object(store, "get_task", return_value=second_task):
                second = auto.publish_pending_async(
                    "task-2", "fanqie", auto_prepare_book=True)

        self.assertEqual(first, (True, ""))
        self.assertFalse(second[0])
        self.assertIn("另一批自动发布", second[1])
        self.assertEqual(DeferredThread.starts, 1)
        auto._batch_slot_lock("fanqie@writer").release()
        auto._running.clear()
        auto._BATCH_SLOT_LOCKS.clear()

    def test_unattended_connection_fails_without_starting_login_waiter(self):
        from core.publish import manager as publisher

        class Page:
            def navigate(self, url):
                self.url_value = url

            def url(self):
                return "https://passport.example/login"

        page = Page()
        with mock.patch.object(publisher, "_open_page", return_value=(object(), page)), \
                mock.patch.object(publisher, "_check_login",
                                  return_value=(False, page.url())), \
                mock.patch.object(publisher, "_set") as set_state, \
                mock.patch.object(publisher.ledger, "record") as record, \
                mock.patch.object(publisher.threading, "Thread") as thread:
            ok, error = publisher.connect("fanqie", unattended=True)

        self.assertFalse(ok)
        self.assertIn("验证完成后自动重试", error)
        set_state.assert_called_once()
        record.assert_called_once()
        thread.assert_not_called()

    def test_unattended_connection_reuses_a_valid_saved_session(self):
        from core.publish import manager as publisher

        class Page:
            def navigate(self, url):
                self.url_value = url

        with mock.patch.object(publisher, "_state", {"platforms": {}}), \
                mock.patch.object(publisher, "_open_page", return_value=(object(), Page())), \
                mock.patch.object(publisher, "_check_login",
                                  return_value=(True, "https://writer.example/home")), \
                mock.patch.object(publisher, "_set") as set_state, \
                mock.patch.object(publisher.ledger, "record") as record, \
                mock.patch.object(publisher.threading, "Thread") as thread:
            ok, error = publisher.connect("qimao", account="writer2",
                                          unattended=True)

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(set_state.call_args.kwargs["status"], "connected")
        self.assertTrue(record.call_args.kwargs["ok"])
        thread.assert_not_called()

    def test_login_probe_rejects_platform_security_challenge_page(self):
        from core.publish import manager as publisher

        class Page:
            def navigate(self, url, timeout=30):
                self.url_value = url

            def url(self):
                return "https://fanqienovel.com/main/writer/"

            def call(self, function, timeout=8):
                return "请完成安全验证后继续"

        ok, reason = publisher._check_login("fanqie", Page())

        self.assertFalse(ok)
        self.assertIn("平台要求安全验证", reason)

    def test_connection_navigation_failure_is_reported_as_terminal(self):
        from core.publish import manager as publisher

        class Page:
            def navigate(self, url, timeout=30):
                raise RuntimeError("network unavailable")

            def url(self):
                return "about:blank"

            def call(self, function, timeout=8):
                return ""

        with mock.patch.object(publisher, "_state", {"platforms": {}}), \
                mock.patch.object(publisher, "_open_page", return_value=(object(), Page())), \
                mock.patch.object(publisher, "_set") as set_state, \
                mock.patch.object(publisher.ledger, "record") as record, \
                mock.patch.object(publisher.threading, "Thread") as thread:
            ok, error = publisher.connect("fanqie", unattended=True)

        self.assertFalse(ok)
        self.assertIn("network unavailable", error)
        self.assertEqual(set_state.call_args.kwargs["status"], "error")
        self.assertFalse(record.call_args.kwargs["ok"])
        thread.assert_not_called()

    def test_unattended_connection_does_not_navigate_during_an_active_publish(self):
        from core.publish import manager as publisher

        slot_lock = publisher._action_lock("fanqie")
        slot_lock.acquire()
        try:
            with mock.patch.object(publisher, "_open_page") as open_page:
                ok, error = publisher.connect("fanqie", unattended=True)
        finally:
            slot_lock.release()

        self.assertFalse(ok)
        self.assertIn("正在执行其他操作", error)
        open_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
