# -*- coding: utf-8 -*-
"""番茄短故事「整篇发布」链回归（2026-10-23 编辑器实抓的纯逻辑面）。

编辑器实抓（CDP 59415）：设置区 `.publish-short-config`、封面抽屉
`.byte-drawer-content`（本地上传 tab → 隐藏 file input → 确认上传 → 确定）、
AI 单选 `.publish_short_config_use_ai`、分类自绘下拉
`.publish-short-category-select` / `-item`、试读比例 `.publish-short-config-pay`、
协议 `.publish-short-config-signLicense`，发布链 下一步 → 继续发布 → 确定 →
「发布成功／已提交，预计1天内审核完成」。

本文件锁流程表形状、分类映射、封面定位、manager 路由与新增步骤类型；
浏览器本身由真机首跑校准，不在这里跑。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.publish import fanqie, flow_fanqie, manager
from app.core.publish.flow import run_flow
from app.core.publish.flow_fanqie import (run_fanqie_short_publish,
                                          run_fanqie_short_trial)
from app.core.publish.flow_helpers import FlowError
from app.core.publish.publish_browser import _flow_contract_ok


class FakePage:
    """Run-flow 步骤测试用的最小页面桩。"""

    def __init__(self, call=None, real_click=None):
        self._call = call or (lambda js, *a, **kw: None)
        self._real_click = real_click or (lambda *a, **kw: {"ok": True})
        self.sent = []
        self.files = []
        self.waited = []
        self.clicked = []

    def neutralize_dialogs(self):
        return None

    def call(self, js, *args, **kwargs):
        return self._call(js, *args, **kwargs)

    def send(self, method, params=None, timeout=None):
        self.sent.append((method, dict(params or {})))
        return {}

    def real_click_text(self, text, scope=None, contains=True):
        return self._real_click(text, scope=scope, contains=contains)

    def wait_for(self, sel, timeout=None):
        self.waited.append((sel, timeout))
        return True

    def set_file(self, sel, path):
        self.files.append((sel, path))
        return True

    def exists(self, sel, timeout=None):
        return False

    def click(self, sel, **kwargs):
        self.clicked.append(sel)
        return True


class FanqieShortPublishFlowTableTests(unittest.TestCase):
    def setUp(self):
        self.steps = fanqie.FLOWS["publish_short_story"]
        self.does = [s.get("do") for s in self.steps]

    def test_registered_and_satisfies_contract(self):
        self.assertIsInstance(self.steps, list) and self.assertTrue(self.steps)
        self.assertTrue(_flow_contract_ok("fanqie", "publish_short_story",
                                          self.steps))

    def test_editor_base_steps(self):
        self.assertEqual(self.steps[0].get("url"), "{short_editor_url}")
        self.assertTrue(any(s.get("do") == "wait"
                            and "ProseMirror" in str(s.get("sel"))
                            for s in self.steps))
        self.assertIn("dismiss_stale", self.does)
        keys = {s.get("key") for s in self.steps}
        self.assertIn("chapter_title", keys)
        self.assertIn("chapter_body", keys)

    def test_cover_steps_are_gated_by_cover_path(self):
        cover = [s for s in self.steps
                 if "cover" in str(s.get("sel") or "")
                 or s.get("do") == "set_file"]
        self.assertTrue(cover)
        for s in cover:
            self.assertEqual(s.get("if_key"), "_cover_path", s)
        set_file = next(s for s in self.steps if s.get("do") == "set_file")
        self.assertIn("input[type=file]", set_file.get("sel"))
        self.assertEqual(set_file.get("key"), "_cover_path")

    def test_settings_area_steps(self):
        radio = next(s for s in self.steps if s.get("do") == "radio")
        self.assertEqual(radio.get("key"), "ai_use")
        self.assertEqual(radio.get("scope_sel"), ".publish_short_config_use_ai")
        self.assertEqual(radio.get("map"), {"是": "1", "否": "2"})
        pick = next(s for s in self.steps if s.get("do") == "select_item")
        self.assertEqual(pick.get("open_sel"), ".publish-short-category-select")
        self.assertEqual(pick.get("item_sel"),
                         ".publish-short-category-select-item")
        self.assertEqual(pick.get("any_key"), "_categories")
        self.assertIn("fanqie_short_trial", self.does)
        check = next(s for s in self.steps if s.get("do") == "check")
        self.assertIn("signLicense", check.get("sel"))
        self.assertIn("arco-checkbox-checked", check.get("checked_sel"))
        self.assertEqual(check.get("confirm"), "我已阅读并同意")

    def test_publish_chain_and_verification(self):
        self.assertTrue(any(s.get("do") == "js_click"
                            and s.get("text") == "下一步" for s in self.steps))
        publish = next(s for s in self.steps if s.get("do") == "fanqie_short_publish")
        self.assertGreaterEqual(int(publish.get("timeout") or 0), 120)
        verify = next(s for s in self.steps if s.get("do") == "verify")
        self.assertEqual(verify.get("url"), "{short_manage_url}")
        self.assertIn("审核中", verify.get("any") or [])


class ShortStoryCategoryTests(unittest.TestCase):
    def test_maps_platform_category_to_main_category(self):
        got = fanqie.short_story_categories({"category": "女频悬疑"})
        self.assertEqual(got[0], "悬疑惊悚")
        self.assertEqual(got[-1], "其他")

    def test_exact_name_wins_and_dedupes(self):
        got = fanqie.short_story_categories({"short_category": "婚姻家庭"})
        self.assertEqual(got[:2], ["婚姻家庭", "虐心婚恋"])
        self.assertEqual(len(got), len(set(got)))
        self.assertEqual(got[-1], "其他")

    def test_xianyanaya_mapping(self):
        self.assertEqual(fanqie.short_story_categories({"category": "现代言情"})[0],
                         "现言甜宠")

    def test_unknown_and_empty_fall_back_to_other(self):
        self.assertEqual(fanqie.short_story_categories({"category": "外星文明"}),
                         ["其他"])
        self.assertEqual(fanqie.short_story_categories(None), ["其他"])

    def test_list_fields_are_merged(self):
        got = fanqie.short_story_categories(
            {"tags_theme": ["宫斗"], "tags_plot": ["逆袭"]})
        self.assertIn("宫斗宅斗", got)
        self.assertIn("女性成长", got)

    def test_every_name_is_a_real_platform_option(self):
        self.assertEqual(len(fanqie._SHORT_CATEGORY_NAMES), 24)
        for _, opts in fanqie._SHORT_CATEGORY_RULES:
            for name in opts:
                self.assertIn(name, fanqie._SHORT_CATEGORY_NAMES)


class ShortCoverPathTests(unittest.TestCase):
    def test_prefers_cover_gen_run_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            cover = run_dir / "cover.png"
            cover.write_bytes(b"png")
            old = manager.paths.RUNS_DIR
            manager.paths.RUNS_DIR = root / "runs"
            try:
                task = {"cover_gen": {"status": "done", "run_id": "run-1",
                                      "file": "cover.png"},
                        "workdir": str(root / "work")}
                self.assertEqual(manager.short_cover_path(task), str(cover))
            finally:
                manager.paths.RUNS_DIR = old

    def test_falls_back_to_workdir_and_rejects_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "cover.jpg").write_bytes(b"jpg")
            self.assertEqual(manager.short_cover_path({"workdir": str(wd)}),
                             str(wd / "cover.jpg"))
            self.assertEqual(manager.short_cover_path(
                {"cover_gen": {"status": "done", "run_id": "nope",
                               "file": "cover.png"}}), "")

    def test_incomplete_cover_gen_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "cover.png").write_bytes(b"png")
            task = {"cover_gen": {"status": "running", "run_id": "r",
                                  "file": "cover.png"},
                    "workdir": str(wd)}
            self.assertEqual(manager.short_cover_path(task),
                             str(wd / "cover.png"))


class FlowStepTypeTests(unittest.TestCase):
    def test_missing_if_key_skips_step_entirely(self):
        calls = mock.Mock()
        page = FakePage(call=calls)
        run_flow(page, [{"do": "fill", "sel": "#a", "key": "chapter_body",
                         "if_key": "_cover_path"}], values={})
        calls.assert_not_called()

    def test_set_file_without_path_raises_unless_optional(self):
        page = FakePage()
        with self.assertRaises(FlowError):
            run_flow(page, [{"do": "set_file", "sel": "#f", "key": "_cover_path"}],
                     values={})
        run_flow(page, [{"do": "set_file", "sel": "#f", "key": "_cover_path",
                         "optional": True}], values={})
        self.assertEqual(page.files, [])

    def test_set_file_resolves_value_and_mounts(self):
        page = FakePage()
        with mock.patch("app.core.publish.flow.time.sleep"):
            run_flow(page, [{"do": "set_file", "sel": "#f", "key": "_cover_path"}],
                     values={"_cover_path": "C:/tmp/cover.png"})
        self.assertEqual(page.waited[0][0], "#f")
        self.assertEqual(page.files, [("#f", "C:/tmp/cover.png")])

    def test_select_item_clicks_candidate_and_closes_popup(self):
        state = {"picked": False}

        def call(js, *args, **kwargs):
            if args and args[0] == ".pub-cat":
                if "getBoundingClientRect" in js:
                    return {"x": 5, "y": 6}
                return "悬疑惊悚" if state["picked"] else "作品分类"
            if args and args[0] == ".pub-cat-item":
                state["picked"] = True
                return {"ok": True, "x": 12, "y": 34, "t": "悬疑惊悚"}
            return {"x": 5, "y": 6}

        page = FakePage(call=call)
        with mock.patch("app.core.publish.flow.time.sleep"):
            run_flow(page, [{"do": "select_item", "name": "作品分类",
                             "open_sel": ".pub-cat", "item_sel": ".pub-cat-item",
                             "any_key": "_categories"}],
                     values={"_categories": ["悬疑惊悚"]})
        buttons = [p for m, p in page.sent if m == "Input.dispatchMouseEvent"]
        self.assertTrue(any(p.get("x") == 34 or p.get("x") == 12 for p in buttons))
        keys = [p.get("key") for m, p in page.sent
                if m == "Input.dispatchKeyEvent"]
        self.assertIn("Escape", keys)

    def test_select_item_unmatched_candidates_fail(self):
        def call(js, *args, **kwargs):
            if args and args[0] == ".pub-cat":
                if "getBoundingClientRect" in js:
                    return {"x": 1, "y": 2}
                return "作品分类"
            if args and args[0] == ".pub-cat-item":
                return {"ok": False, "err": "no-match", "ts": ["其他"]}
            return {"x": 1, "y": 2}

        page = FakePage(call=call)
        with mock.patch("app.core.publish.flow.time.sleep"):
            with self.assertRaises(FlowError):
                run_flow(page, [{"do": "select_item",
                                 "open_sel": ".pub-cat",
                                 "item_sel": ".pub-cat-item",
                                 "any_key": "_categories"}],
                         values={"_categories": ["悬疑惊悚"]})

    def test_check_clicks_mask_then_verifies(self):
        state = {"checked": False}

        def call(js, *args, **kwargs):
            if args and args[0] == ".cb-checked":
                return state["checked"]
            if args and args[0] == ".cb":
                return {"ok": True, "x": 20, "y": 30}
            return None

        def send(method, params=None, timeout=None):
            if method == "Input.dispatchMouseEvent":
                state["checked"] = True
            return {}

        page = FakePage(call=call)
        page.send = send
        with mock.patch("app.core.publish.flow.time.sleep"):
            run_flow(page, [{"do": "check", "sel": ".cb",
                             "checked_sel": ".cb-checked",
                             "confirm": "我已阅读并同意"}], values={})
        self.assertTrue(state["checked"])

    def test_check_failure_raises_after_retries(self):
        def call(js, *args, **kwargs):
            if args and args[0] == ".cb-checked":
                return False
            return {"ok": False, "err": "nf"}

        page = FakePage(call=call)
        with mock.patch("app.core.publish.flow.time.sleep"):
            with self.assertRaises(FlowError):
                run_flow(page, [{"do": "check", "sel": ".cb",
                                 "checked_sel": ".cb-checked", "tries": 2}],
                         values={})

    def test_dismiss_stale_absent_is_silent(self):
        page = FakePage(call=lambda *a, **k: {"ok": False, "absent": True})
        with mock.patch("app.core.publish.flow.time.sleep"):
            run_flow(page, [{"do": "dismiss_stale"}], values={})
        self.assertEqual(page.sent, [])


class FanqieShortChainTests(unittest.TestCase):
    def test_trial_skips_when_ratio_already_set(self):
        page = FakePage(call=lambda *a, **k: "30%")
        notes = []
        with mock.patch.object(flow_fanqie.time, "sleep"):
            run_fanqie_short_trial(page, {}, 0,
                                   lambda i, s: notes.append(s), {})
        self.assertIn("已设置", notes[0])

    def test_trial_click_then_ratio_appears(self):
        state = {"text": "去设置"}

        def call(js, *a, **k):
            return state["text"]

        def real_click(text, scope=None, contains=True):
            if text == "去设置":
                state["text"] = "30%"
            return {"ok": True}

        page = FakePage(call=call, real_click=real_click)
        with mock.patch.object(flow_fanqie.time, "sleep"):
            run_fanqie_short_trial(page, {}, 0, lambda i, s: None, {})
        self.assertEqual(state["text"], "30%")

    def test_trial_failure_refuses_to_click_next(self):
        page = FakePage(call=lambda *a, **k: "去设置",
                        real_click=lambda *a, **k: {"ok": False})
        with mock.patch.object(flow_fanqie.time, "sleep"):
            with self.assertRaises(FlowError):
                run_fanqie_short_trial(page, {}, 0, lambda i, s: None, {})

    def test_publish_chain_continues_then_confirms_then_succeeds(self):
        dialogs = [["发布提示\n继续发布"], ["提交确认\n确定"],
                   ["发布成功\n已提交，预计1天内审核完成"]]
        clicked = []

        def call(js, *args, **kwargs):
            if args and isinstance(args[0], str) and "modal-wrapper" in args[0]:
                return dialogs.pop(0) if dialogs else []
            return ""

        def real_click(text, scope=None, contains=True):
            clicked.append(text)
            return {"ok": True}

        page = FakePage(call=call, real_click=real_click)
        with mock.patch.object(flow_fanqie.time, "sleep"):
            run_fanqie_short_publish(page, {"timeout": 30}, 0,
                                     lambda i, s: None, {})
        self.assertEqual(clicked, ["继续发布", "确定"])

    def test_publish_chain_timeout_raises(self):
        page = FakePage(call=lambda *a, **k: [] if a else "")
        with mock.patch.object(flow_fanqie.time, "sleep"):
            with self.assertRaises(FlowError):
                run_fanqie_short_publish(page, {"timeout": 0.05}, 0,
                                         lambda i, s: None, {})

    def test_publish_chain_reports_trial_ratio_block(self):
        def call(js, *args, **kwargs):
            return "未设置试读比例，请设置后再提交发布"

        page = FakePage(call=call)
        with mock.patch.object(flow_fanqie.time, "sleep"):
            with self.assertRaises(FlowError):
                run_fanqie_short_publish(page, {"timeout": 30}, 0,
                                         lambda i, s: None, {})


class ManagerShortPublishRoutingTests(unittest.TestCase):
    def _upload(self, plat, **kwargs):
        from app.core import contracts, store
        from app.core.publish import ledger
        task = {"id": "task-1", "type": "short_story",
                "workdir": "C:/tmp/task-1"}
        book = {"book_id": "book-1", "title": "测试短篇"}
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=book), \
                mock.patch.object(ledger, "recent", return_value=[]), \
                mock.patch.object(manager, "read_chapter",
                                  return_value=(0, "测试短篇", "字" * 1200, "")), \
                mock.patch.object(manager, "_try_action_lock",
                                  return_value=None):
            return manager.upload_chapter_async(
                "task-1", plat, "C:/tmp/task-1/story.md",
                as_draft=kwargs.get("as_draft", False), short_story=True)

    def test_qimao_publish_is_refused_without_flow(self):
        ok, error = self._upload("qimao")
        self.assertFalse(ok)
        self.assertIn("未配置短故事发布流程", error)

    def test_fanqie_publish_passes_flow_gate(self):
        # 过了流程表存在性检查后才读到正文；用假正文错误当路标，
        # 证明没有在「未配置发布流程」处被拦。
        ok, error = self._upload("fanqie")
        self.assertFalse(ok)
        self.assertNotIn("未配置短故事发布流程", error)

    def test_short_story_scheduled_is_refused(self):
        from app.core import contracts, store
        from app.core.publish import ledger
        task = {"id": "task-1", "type": "short_story", "workdir": "C:/tmp"}
        book = {"book_id": "book-1", "title": "测试短篇"}
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=book):
            ok, error = manager.upload_chapter_async(
                "task-1", "fanqie", "C:/tmp/story.md",
                as_draft=False, scheduled=True, short_story=True)
        self.assertFalse(ok)
        self.assertIn("短故事不支持定时发布", error)

    def test_publish_values_carry_cover_categories_and_ai(self):
        from app.core import contracts, operations, store
        from app.core.publish import flow, ledger, volumes

        task = {"id": "task-1", "type": "short_story",
                "workdir": "C:/tmp/task-1"}
        book = {"book_id": "book-1", "title": "测试短篇"}
        seen = {}

        class SyncThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        def fake_run(page, steps, values=None, **kwargs):
            seen["values"] = dict(values or {})
            seen["steps"] = steps
            return len(steps or [])

        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "cover.png").write_bytes(b"png")
            meta = {"status": "done",
                    "data": {"category": "女频悬疑", "ai_use": "否"}}
            lock = mock.Mock()
            remember = mock.Mock(return_value="")
            patches = [
                mock.patch.object(store, "get_task", return_value=task),
                mock.patch.object(contracts, "publish_allowed",
                                  return_value=True),
                mock.patch.object(store, "inherited_book_meta",
                                  return_value=meta),
                mock.patch.object(ledger, "book_for", return_value=book),
                mock.patch.object(ledger, "recent", return_value=[]),
                mock.patch.object(ledger, "published_chapters",
                                  return_value=set()),
                mock.patch.object(ledger, "scheduled_chapters",
                                  return_value=set()),
                mock.patch.object(ledger, "drafted_chapters",
                                  return_value=set()),
                mock.patch.object(ledger, "latest_scheduled_slot",
                                  return_value=None),
                mock.patch.object(ledger, "record"),
                mock.patch.object(ledger, "save_book"),
                mock.patch.object(manager, "read_chapter",
                                  return_value=(0, "测试短篇", "字" * 1200, "")),
                mock.patch.object(manager, "_quality_release_guard",
                                  return_value={"allowed": True}),
                mock.patch.object(manager, "_try_action_lock",
                                  return_value=lock),
                mock.patch.object(manager, "_login_guard",
                                  return_value=(True, "")),
                mock.patch.object(manager, "_st", return_value={}),
                mock.patch.object(manager, "_set"),
                mock.patch.object(manager, "_save"),
                mock.patch.object(manager, "_remember_pending_upload", remember),
                mock.patch.object(manager, "_open_page",
                                  return_value=(None, FakePage())),
                mock.patch.object(manager, "_resolve_book_id",
                                  return_value="book-1"),
                mock.patch.object(manager.threading, "Thread", SyncThread),
                mock.patch.object(operations, "begin", return_value="op-1"),
                mock.patch.object(operations, "confirm"),
                mock.patch.object(volumes, "name_for", return_value=""),
                mock.patch.object(manager, "short_cover_path",
                                  return_value=str(wd / "cover.png")),
                mock.patch.object(flow, "run_flow", side_effect=fake_run),
            ]
            from contextlib import ExitStack
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                ok, error = manager.upload_chapter_async(
                    "task-1", "fanqie", "C:/tmp/task-1/story.md",
                    as_draft=False, short_story=True)
            cover = str(wd / "cover.png")
        self.assertEqual(error, "")
        self.assertTrue(ok)
        vals = seen["values"]
        self.assertEqual(vals["_cover_path"], cover)
        self.assertEqual(vals["ai_use"], "否")
        self.assertEqual(vals["_categories"][0], "悬疑惊悚")
        self.assertEqual(vals["short_manage_url"], fanqie.CONFIG["short_manage"])
        self.assertIn("{short_manage_url}", str(seen["steps"]))
        remember.assert_not_called()   # 重启恢复不按连载口径重放短篇
        lock.release.assert_called_once_with()

    def test_ledger_idempotency_blocks_second_short_publish(self):
        from app.core import contracts, store
        from app.core.publish import ledger
        task = {"id": "task-1", "type": "short_story", "workdir": "C:/tmp"}
        book = {"book_id": "book-1", "title": "测试短篇"}
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=book), \
                mock.patch.object(ledger, "recent",
                                  return_value=[{"action": "publish_short_story",
                                                 "ok": True}]), \
                mock.patch.object(manager, "read_chapter",
                                  return_value=(0, "测试短篇", "字" * 1200, "")):
            ok, error = manager.upload_chapter_async(
                "task-1", "fanqie", "C:/tmp/story.md",
                as_draft=False, short_story=True)
        self.assertFalse(ok)
        self.assertIn("台账幂等拦截", error)

    def test_recovery_is_not_queued_for_short_story(self):
        from app.core import contracts, store
        from app.core.publish import ledger
        task = {"id": "task-1", "type": "short_story", "workdir": "C:/tmp"}
        book = {"book_id": "book-1", "title": "测试短篇"}
        lock = mock.Mock()
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed", return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=book), \
                mock.patch.object(ledger, "recent", return_value=[]), \
                mock.patch.object(manager, "read_chapter",
                                  return_value=(0, "测试短篇", "字" * 1200, "")), \
                mock.patch.object(manager, "_quality_release_guard",
                                  return_value={"allowed": True}), \
                mock.patch.object(manager, "_try_action_lock",
                                  return_value=lock), \
                mock.patch.object(manager, "_st", return_value={}), \
                mock.patch.object(manager, "_login_guard",
                                  return_value=(False, "登录已失效")), \
                mock.patch.object(manager, "_queue_upload_recovery") as queue:
            ok, error = manager.upload_chapter_async(
                "task-1", "fanqie", "C:/tmp/story.md",
                as_draft=False, short_story=True)
        self.assertFalse(ok)
        self.assertIn("登录已失效", error)
        queue.assert_not_called()
        lock.release.assert_called_once_with()


class ShortStoryApiHandlerTests(unittest.TestCase):
    def test_draft_and_publish_dispatch_flags(self):
        app_dir = Path(__file__).resolve().parents[1] / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        import main_handlers as handlers
        import core.publish.manager as pub_manager

        calls = []

        def fake_upload(task_id, platform, path, **kwargs):
            calls.append((task_id, platform, path, kwargs))
            return True, ""

        fake_main = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "story.md").write_text("正文", encoding="utf-8")
            fake_main.store.get_task.return_value = {
                "id": "t-1", "workdir": str(wd), "manuscript": "story.md"}
            handler = handlers.HandlerPublishApi.__new__(handlers.HandlerPublishApi)
            handler._json = lambda code, payload: {"code": code,
                                                   "payload": payload}
            handler._body = lambda: {"platform": "fanqie"}
            with mock.patch.object(handlers, "_main", return_value=fake_main), \
                    mock.patch.object(pub_manager, "upload_chapter_async",
                                      side_effect=fake_upload):
                draft = handler._api_publish_short_story_draft("t-1")
                live = handler._api_publish_short_story_publish("t-1")
        self.assertEqual(draft["code"], 200)
        self.assertEqual(live["code"], 200)
        self.assertTrue(calls[0][3]["as_draft"])
        self.assertFalse(calls[1][3]["as_draft"])
        self.assertEqual(calls[1][2], str(wd / "story.md"))
        for _, _, _, kwargs in calls:
            self.assertTrue(kwargs["short_story"])


if __name__ == "__main__":
    unittest.main()
