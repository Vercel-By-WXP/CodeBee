# -*- coding: utf-8 -*-
"""七猫短故事「整篇发布」链回归（纯逻辑面；浏览器链路由真机首跑校准）。

锁定：流程表形状与契约、确认链助手（声明必设/真实鼠标确认/合规框/序号
提示/超时）、verify any_of 凭据语义、format_block optional 跳过、
manager 对 qimao 短篇发布的放行与取值注入。
"""
import unittest
from unittest import mock

from app.core.publish import flow_qimao, manager, qimao
from app.core.publish.flow_helpers import FlowError
from app.core.publish.flow_qimao import run_qimao_short_publish
from app.core.publish.publish_browser import _flow_contract_ok


class FakePage:
    def __init__(self, call=None, real_click=None):
        self._call = call or (lambda js, *a, **kw: None)
        self._real_click = real_click or (lambda *a, **kw: {"ok": True})
        self.real_clicks = []

    def neutralize_dialogs(self):
        return None

    def call(self, js, *args, **kwargs):
        return self._call(js, *args, **kwargs)

    def real_click_text(self, text, scope="", contains=True, **kwargs):
        self.real_clicks.append(text)
        return self._real_click(text, scope=scope, contains=contains)


class QimaoShortPublishFlowTableTests(unittest.TestCase):
    def setUp(self):
        self.steps = qimao.FLOWS["publish_short_story"]
        self.does = [s.get("do") for s in self.steps]

    def test_registered_and_satisfies_contract(self):
        self.assertTrue(self.steps)
        self.assertTrue(_flow_contract_ok("qimao", "publish_short_story",
                                          self.steps))

    def test_editor_and_body_steps(self):
        self.assertEqual(self.steps[0].get("url"), "{editor_url}")
        body = next(s for s in self.steps if s.get("key") == "chapter_body")
        self.assertIn("q-contenteditable", body.get("sel"))
        blocks = [s for s in self.steps if s.get("do") == "format_block"]
        self.assertGreaterEqual(len(blocks), 3)          # 一/二 必格，其余按需
        self.assertTrue(all(b.get("block") == "h3" for b in blocks))
        self.assertTrue(all(b.get("optional") for b in blocks[2:]))

    def test_publish_chain_steps(self):
        self.assertTrue(any(s.get("do") == "click_real"
                            and s.get("text") == "立即发布"
                            for s in self.steps))
        publish = next(s for s in self.steps if s.get("do") == "qimao_short_publish")
        self.assertGreaterEqual(int(publish.get("timeout") or 0), 60)
        verify = next(s for s in self.steps if s.get("do") == "verify")
        self.assertIn("short_book_manage", str(verify.get("url")))
        self.assertIn("{book_name}", verify.get("any") or [])
        self.assertEqual(verify.get("any_of"),
                         ["待审核", "审核中", "已发布"])


class QimaoShortPublishChainTests(unittest.TestCase):
    """run_qimao_short_publish 的假页推进测试。"""

    @staticmethod
    def _page(dialogs_seq, body_seq=None, declaration_results=None,
             real_click_texts=None):
        state = {"dialogs": list(dialogs_seq), "body": list(body_seq or []),
                 "decl": list(declaration_results or []),
                 "decl_calls": []}

        def call(js, *args, **kwargs):
            if args and "el-dialog" in str(args[0]):
                if len(args) >= 2:
                    state["decl_calls"].append(args[1])
                    return (state["decl"].pop(0)
                            if state["decl"] else {"ok": True, "picked": ""})
                return state["dialogs"].pop(0) if state["dialogs"] else []
            return state["body"].pop(0) if state["body"] else ""

        def real_click(text, scope="", contains=True, **kw):
            if real_click_texts is not None:
                return ({"ok": text in real_click_texts})
            return {"ok": True}

        return FakePage(call=call, real_click=real_click), state

    def test_declaration_set_then_confirm_via_real_click(self):
        page, state = self._page(
            dialogs_seq=[["确认发布 内容声明"], []],
            body_seq=["", "发布成功"],
            declaration_results=[{"ok": True, "picked": "含AI生成内容"}])
        with mock.patch.object(flow_qimao.time, "sleep"):
            run_qimao_short_publish(page, {"timeout": 30}, 0,
                                    lambda i, s: None,
                                    {"content_declaration": "含AI生成内容"})
        self.assertIn("确认发布", page.real_clicks)
        self.assertEqual(state["decl_calls"], ["含AI生成内容"])

    def test_dialog_without_declaration_select_aborts_before_confirm(self):
        page, state = self._page(
            dialogs_seq=[["确认发布"], []],
            body_seq=[""],
            declaration_results=[{"ok": False, "err": "no-select"}])
        with mock.patch.object(flow_qimao.time, "sleep"):
            with self.assertRaises(FlowError):
                run_qimao_short_publish(page, {"timeout": 30}, 0,
                                        lambda i, s: None, {})
        self.assertNotIn("确认发布", page.real_clicks)
        self.assertEqual(state["decl_calls"], ["含AI生成内容"])

    def test_declaration_failure_aborts_before_confirm(self):
        page, _ = self._page(
            dialogs_seq=[["确认发布 内容声明"] * 20],
            declaration_results=[{"ok": False, "err": "no-option"}])
        with mock.patch.object(flow_qimao.time, "sleep"):
            with self.assertRaises(FlowError):
                run_qimao_short_publish(page, {"timeout": 30}, 0,
                                        lambda i, s: None, {})
        self.assertNotIn("确认发布", page.real_clicks)

    def test_compliance_dialog_is_cleared_first(self):
        page, _ = self._page(
            dialogs_seq=[["重要提醒 我已阅读并知晓"], ["确认发布"], []],
            body_seq=["", "", "发布成功"],
            declaration_results=[{"ok": True}])
        with mock.patch.object(flow_qimao.time, "sleep"):
            run_qimao_short_publish(page, {"timeout": 30}, 0,
                                    lambda i, s: None, {})
        self.assertEqual(page.real_clicks[0], "我已阅读并知晓")

    def test_closed_dialog_after_confirm_yields_to_verify(self):
        page, _ = self._page(
            dialogs_seq=[["确认发布 内容声明"], []], body_seq=["", ""],
            declaration_results=[{"ok": True, "already": True}])
        with mock.patch.object(flow_qimao.time, "sleep"):
            run_qimao_short_publish(page, {"timeout": 30}, 0,
                                    lambda i, s: None, {})
        self.assertIn("确认发布", page.real_clicks)

    def test_stuck_confirm_dialog_times_out(self):
        page, _ = self._page(dialogs_seq=[["确认发布"]] * 20,
                             declaration_results=[{"ok": True}] * 20)
        with mock.patch.object(flow_qimao.time, "sleep"):
            with self.assertRaises(FlowError):
                run_qimao_short_publish(page, {"timeout": 30}, 0,
                                        lambda i, s: None, {})

    def test_nothing_clicked_never_fakes_success(self):
        page, _ = self._page(dialogs_seq=[[]] * 20, body_seq=[""] * 20)
        with mock.patch.object(flow_qimao.time, "sleep"):
            with self.assertRaises(FlowError):
                run_qimao_short_publish(page, {"timeout": 0.05}, 0,
                                        lambda i, s: None, {})
        self.assertEqual(page.real_clicks, [])


class VerifyAnyOfTests(unittest.TestCase):
    """verify 的 any_of：any 全部在场 + any_of 至少一个在场。"""

    @staticmethod
    def _page(body_text):
        class VPage(FakePage):
            def open_new_tab(self, url):
                return self

            def close_tab(self):
                return None

        page = VPage(call=lambda js, *a, **kw: body_text)
        return page

    def _run(self, page, step):
        from app.core.publish.flow import run_flow
        with mock.patch("app.core.publish.flow.time.sleep"):
            run_flow(page, [step], values={"book_name": "镜中七秒"})

    def test_any_of_hit_passes(self):
        page = self._page("镜中七秒 待审核")
        self._run(page, {"do": "verify", "url": "http://x", "new_tab": True,
                         "any": ["{book_name}"],
                         "any_of": ["待审核", "审核中", "已发布"]})

    def test_any_of_all_missing_raises(self):
        page = self._page("镜中七秒 草稿")
        with self.assertRaises(FlowError):
            self._run(page, {"do": "verify", "url": "http://x", "new_tab": True,
                             "any": ["{book_name}"],
                             "any_of": ["待审核", "审核中", "已发布"]})

    def test_any_marks_still_all_required(self):
        page = self._page("待审核")
        with self.assertRaises(FlowError):
            self._run(page, {"do": "verify", "url": "http://x", "new_tab": True,
                             "any": ["{book_name}"], "any_of": ["待审核"]})


class FormatBlockOptionalTests(unittest.TestCase):
    def _run(self, step):
        from app.core.publish.flow import run_flow
        page = FakePage(call=lambda js, *a, **kw: {"ok": False,
                                                   "err": "no-para"})
        with mock.patch("app.core.publish.flow.time.sleep"):
            run_flow(page, [step], values={})

    def test_missing_marker_skips_when_optional(self):
        self._run({"do": "format_block", "text": "三", "block": "h3",
                   "optional": True})

    def test_missing_marker_raises_without_optional(self):
        with self.assertRaises(FlowError):
            self._run({"do": "format_block", "text": "三", "block": "h3"})


class ManagerQimaoShortPublishTests(unittest.TestCase):
    def _upload(self, **kwargs):
        from app.core import contracts, store
        from app.core.publish import ledger
        task = {"id": "task-1", "type": "short_story",
                "workdir": "C:/tmp/task-1"}
        book = {"book_id": "13860001", "title": "测试短篇"}
        with mock.patch.object(store, "get_task", return_value=task), \
                mock.patch.object(contracts, "publish_allowed",
                                  return_value=True), \
                mock.patch.object(ledger, "book_for", return_value=book), \
                mock.patch.object(ledger, "recent", return_value=[]), \
                mock.patch.object(manager, "read_chapter",
                                  return_value=(0, "测试短篇", "字" * 4200, "")), \
                mock.patch.object(manager, "_try_action_lock",
                                  return_value=None):
            return manager.upload_chapter_async(
                "task-1", "qimao", "C:/tmp/task-1/story.md",
                as_draft=kwargs.get("as_draft", False), short_story=True)

    def test_qimao_publish_passes_flow_gate(self):
        ok, error = self._upload()
        self.assertFalse(ok)
        # 走到账号锁（None→「有操作正在进行中」）说明流程表放行了
        self.assertNotIn("未配置短故事发布流程", error)
        self.assertIn("有操作正在进行中", error)

    def test_qimao_draft_still_works(self):
        ok, error = self._upload(as_draft=True)
        self.assertFalse(ok)
        self.assertNotIn("未配置短故事发布流程", error)

    def test_publish_values_carry_content_declaration(self):
        from app.core import contracts, operations, store
        from app.core.publish import flow, ledger, volumes
        task = {"id": "task-1", "type": "short_story",
                "workdir": "C:/tmp/task-1"}
        book = {"book_id": "13860001", "title": "测试短篇"}
        seen = {}

        class SyncThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        def fake_run(page, steps, values=None, **kwargs):
            seen["values"] = dict(values or {})
            return len(steps or [])

        lock = mock.Mock()
        patches = [
            mock.patch.object(store, "get_task", return_value=task),
            mock.patch.object(contracts, "publish_allowed", return_value=True),
            mock.patch.object(store, "inherited_book_meta",
                              return_value={"status": "done", "data": {}}),
            mock.patch.object(ledger, "book_for", return_value=book),
            mock.patch.object(ledger, "recent", return_value=[]),
            mock.patch.object(ledger, "published_chapters", return_value=set()),
            mock.patch.object(ledger, "scheduled_chapters", return_value=set()),
            mock.patch.object(ledger, "drafted_chapters", return_value=set()),
            mock.patch.object(ledger, "latest_scheduled_slot",
                              return_value=None),
            mock.patch.object(ledger, "record"),
            mock.patch.object(ledger, "save_book"),
            mock.patch.object(manager, "read_chapter",
                              return_value=(0, "测试短篇", "字" * 4200, "")),
            mock.patch.object(manager, "_quality_release_guard",
                              return_value={"allowed": True}),
            mock.patch.object(manager, "_try_action_lock", return_value=lock),
            mock.patch.object(manager, "_login_guard", return_value=(True, "")),
            mock.patch.object(manager, "_st", return_value={}),
            mock.patch.object(manager, "_set"),
            mock.patch.object(manager, "_save"),
            mock.patch.object(manager, "_remember_pending_upload",
                              return_value=""),
            mock.patch.object(manager, "_open_page", return_value=(None, None)),
            mock.patch.object(manager, "_resolve_book_id",
                              return_value="13860001"),
            mock.patch.object(manager.threading, "Thread", SyncThread),
            mock.patch.object(operations, "begin", return_value="op-1"),
            mock.patch.object(operations, "confirm"),
            mock.patch.object(volumes, "name_for", return_value=""),
            mock.patch.object(flow, "run_flow", side_effect=fake_run),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            ok, error = manager.upload_chapter_async(
                "task-1", "qimao", "C:/tmp/task-1/story.md",
                as_draft=False, short_story=True)
        self.assertEqual(error, "")
        self.assertTrue(ok)
        self.assertEqual(seen["values"]["content_declaration"], "含AI生成内容")
        lock.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
