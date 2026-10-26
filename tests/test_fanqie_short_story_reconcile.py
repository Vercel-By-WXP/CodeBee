# -*- coding: utf-8 -*-
"""番茄短故事建书对账加固回归（2026-10-26 头七案，纯逻辑面）。

实案：一键建 new 物化握手被平台 400 拒 → 编辑器「已保存」是本地假象、
标题永不落库，草稿箱只剩无名空稿残片，DOM 对账全瞎 13 连败。本文件锁定
三道防线：draft_list 接口按名兜底、无名空稿领养闸（allow_ghost + 30 分钟）、
报错带 item_id 可人工登记；外加恢复路径重试计数上限。
"""
import inspect
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core.publish import fanqie, qimao
from app.core.publish import manager, publish_create


def _row(item_id, titles=(), words=0, created=None):
    return {"item_id": str(item_id), "multi_title": list(titles),
            "word_number": words, "create_time": str(created or 0)}


class FakePage(object):
    """只覆盖对账用到的三个接触面：call / navigate / url。"""

    def __init__(self, api_rows=None, dom_row_id="", probe=None, on_origin=True):
        self.api_rows = api_rows          # None=接口取不到；list=接口在册
        self.dom_row_id = dom_row_id
        self.probe = probe or {}
        self.navigate_calls = []
        self.on_origin = on_origin

    def call(self, js, *args, **kw):
        if "draft_list" in js:
            if not self.on_origin:
                return None
            return self.api_rows
        if "titleVisible" in js:
            return dict(self.probe)             # 探针 JS 也含 querySelectorAll，先判
        if "querySelectorAll" in js and args:
            return self.dom_row_id              # 草稿箱行直读
        return None

    def navigate(self, url, timeout=30.0):
        self.navigate_calls.append(url)
        self.on_origin = True               # 导航后即在番茄域

    def url(self):
        return "https://fanqienovel.com/main/writer/short-manage"


class FanqieShortStoryReconcileTests(unittest.TestCase):
    TITLE = {"title": "头七：供桌前的陌生人"}

    def test_api_title_match_adopts_without_dom(self):
        rows = [_row("7700904378462372376", ["头七：供桌前的陌生人"], 0)]
        page = FakePage(api_rows=rows)
        found, bid, detail = fanqie.reconcile_short_story_book_id(page, self.TITLE)
        self.assertTrue(found)
        self.assertEqual(bid, "7700904378462372376")
        self.assertEqual(detail, "")
        self.assertEqual(page.navigate_calls, [])   # 接口命中免导航

    def test_api_named_row_wins_over_ghost(self):
        now = int(time.time())
        rows = [_row("999", [], 0, now - 60),
                _row("7700904378462372376", ["头七：供桌前的陌生人"], 120, now)]
        page = FakePage(api_rows=rows)
        found, bid, _ = fanqie.reconcile_short_story_book_id(page, self.TITLE, True)
        self.assertTrue(found)
        self.assertEqual(bid, "7700904378462372376")

    def test_fresh_ghost_adopted_only_when_allowed(self):
        now = int(time.time())
        rows = [_row("7700904378462372376", [], 0, now - 60)]
        adopted = fanqie.reconcile_short_story_book_id(
            FakePage(api_rows=rows), self.TITLE, True)
        self.assertEqual(adopted[:2], (True, "7700904378462372376"))
        self.assertIn("领养", adopted[2])

        blocked = fanqie.reconcile_short_story_book_id(
            FakePage(api_rows=rows), self.TITLE, False)
        self.assertEqual(blocked[0], False)
        self.assertIn("7700904378462372376", blocked[2])   # 报错带 id 可人工登记

    def test_stale_ghost_reported_never_adopted(self):
        rows = [_row("7700904378462372376", [], 0,
                     int(time.time()) - 7200)]          # 2 小时前：可能是别人的弃稿
        page = FakePage(api_rows=rows, probe={"ready": True, "create": True,
                                              "titleVisible": False})
        found, bid, detail = fanqie.reconcile_short_story_book_id(
            page, self.TITLE, True)
        self.assertFalse(found)
        self.assertEqual(bid, "")
        self.assertIn("7700904378462372376", detail)

        # 建书前路径（allow_ghost=False）陈年空稿不拦流程：退回原探针语义
        pre = fanqie.reconcile_short_story_book_id(
            FakePage(api_rows=rows, probe={"ready": True, "create": True,
                                           "titleVisible": False}),
            self.TITLE, False)
        self.assertEqual(pre, (False, "", ""))

    def test_dom_fallback_when_api_unavailable(self):
        page = FakePage(api_rows=None, dom_row_id="7700904378462372376")
        found, bid, _ = fanqie.reconcile_short_story_book_id(page, self.TITLE)
        self.assertTrue(found)
        self.assertEqual(bid, "7700904378462372376")
        self.assertTrue(page.navigate_calls)            # 走了 DOM 原路导航

    def test_empty_title_rows_ignored_when_words_present(self):
        # 有字数/有名字的行不算幽灵；字数非 0 的无名行也不领养
        now = int(time.time())
        rows = [_row("888", [], 3000, now - 60)]
        page = FakePage(api_rows=rows, probe={"ready": True, "create": True,
                                              "titleVisible": False})
        found, _bid, detail = fanqie.reconcile_short_story_book_id(
            page, self.TITLE, True)
        self.assertFalse(found)
        self.assertNotIn("888", detail)


class RecoveryAttemptCapTests(unittest.TestCase):
    def test_cap_constant_locked(self):
        self.assertEqual(publish_create.CREATE_BOOK_RECOVERY_MAX_ATTEMPTS, 3)

    def test_bump_counts_from_durable_marker(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE", Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {}}):
            publish_create._remember_pending_create(
                "fanqie", {"task_id": "t-1", "account": ""})
            self.assertEqual(
                publish_create._bump_pending_create_attempt("fanqie", "t-1"), 1)
            self.assertEqual(
                publish_create._bump_pending_create_attempt("fanqie", "t-1"), 2)
            entries = manager._st("fanqie")["pending_creates"]
            self.assertEqual(entries["t-1"]["attempts"], 2)
            # 标记不在（并发收尾已摘）时计数为 0，不复活标记
            self.assertEqual(
                publish_create._bump_pending_create_attempt("fanqie", "t-x"), 0)
            self.assertEqual(set(manager._st("fanqie")["pending_creates"]),
                             {"t-1"})

    def test_resume_passes_attempts_through_and_kwargs_unchanged(self):
        state = {"platforms": {"fanqie": {"status": "connected",
                                          "pending_creates": {
                                              "t-1": {"task_id": "t-1",
                                                      "account": "",
                                                      "attempts": 2}}}}}
        with mock.patch.object(manager, "_state", {}), \
                mock.patch.object(manager, "_load",
                                  side_effect=lambda: manager._state.update(state)), \
                mock.patch.object(manager, "_queue_create_book_auth_recovery",
                                  return_value=True) as queue:
            self.assertEqual(manager._resume_pending_create_recoveries(), 1)
        queue.assert_called_once_with(
            "t-1", "fanqie", "",
            {"force": False, "force_confirmed": False, "force_reason": ""})

    def test_qimao_reconcile_accepts_ghost_flag_for_contract(self):
        sig = inspect.signature(qimao.reconcile_short_story_book_id)
        self.assertIn("allow_ghost", sig.parameters)
        self.assertIs(sig.parameters["allow_ghost"].default, False)

    def test_ledger_error_payload_serializable(self):
        # 领养/报告两条 detail 文案必须能原样进台账 JSON（含书名号与中文）
        for text in ("领养30分钟内新建的无名空草稿（平台拒挂名的残片）",
                     "番茄草稿箱按名未找到《头七：供桌前的陌生人》；发现无名空草稿 "
                     "item_id=7700904378462372376（创建于 10-26 17:22）"):
            json.dumps({"error": text}, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
