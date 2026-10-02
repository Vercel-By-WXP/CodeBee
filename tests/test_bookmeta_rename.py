# -*- coding: utf-8 -*-
"""作品信息换名单测：全站书名排除名单收集 / 轻模型换名链（成功、撞名单重试、
模型失败与解析失败不产出新名）。不碰真实 data/（TUTTI_DATA 独立临时目录，
同 test_bookmeta 口径）；模型一律 mock，不真调编排者。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-bmr-"))
sys.path.insert(0, str(ROOT / "app"))

from core import bookmeta, store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tutti-bmr-fixture-"))


def _mktask(title, goal="男频军事文，主角陈砚戍边杀伐。"):
    wd = TMP / title
    wd.mkdir(parents=True, exist_ok=True)   # create_task 校验工作目录在场
    return store.create_task({"type": "serial_novel", "title": title,
                              "goal": goal, "workdir": str(wd)})


def _chat_return(text, ok=True, error=""):
    return {"ok": ok, "text": text, "error": error}


class TestUsedBookNames(unittest.TestCase):
    def test_collects_done_and_ledger(self):
        t = _mktask("bmr-collect")
        other = _mktask("bmr-collect2")
        store.set_book_meta(t["id"], "fanqie", {
            "status": "done", "data": {"book_name": "戍边行"},
            "at": "2026-10-02 00:00:00"})
        store.set_book_meta(other["id"], "qimao", {
            "status": "done", "data": {"book_name": "金枝折"},
            "at": "2026-10-02 00:00:01"})
        store.set_book_meta(other["id"], "fanqie", {
            "status": "failed", "error": "x", "at": "2026-10-02 00:00:02"})
        names = bookmeta.used_book_names()
        # 共享 TUTTI_DATA 下别的测试模块也会种书名，只断言本用例的三条边界，
        # 不数总数；failed 态没有 data 不该混进名单（「x」是 error 文本）
        self.assertIn("戍边行", names)
        self.assertIn("金枝折", names)
        self.assertNotIn("x", names)

    def test_ledger_titles_included(self):
        from core.publish import ledger
        t = _mktask("bmr-ledger")
        ledger.save_book(t["id"], "fanqie", {"book_id": "777", "title": "登记书名"})
        self.assertIn("登记书名", bookmeta.used_book_names())


class TestRenameBook(unittest.TestCase):
    def setUp(self):
        self.task = _mktask("bmr-rename")
        store.set_book_meta(self.task["id"], "fanqie", {
            "status": "done",
            "data": {"book_name": "戍边骑奴", "summary": "少年戍边，以军功改命。"},
            "at": "2026-10-02 00:00:00"})

    def _patch_orch(self):
        orch = ({"id": "p1", "name": "假供应商"}, "glm-x")
        return (mock.patch("core.modelhub.resolve_orchestrator",
                           return_value=orch), orch)

    def test_success(self):
        p, orch = self._patch_orch()
        with p, mock.patch("core.modelhub.chat",
                           return_value=_chat_return('```json\n{"book_name": "铁衣寒"}\n```')):
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴",
                                                 bookmeta.used_book_names())
        self.assertEqual((new_name, err), ("铁衣寒", ""))

    def test_prompt_carries_old_and_exclude(self):
        p, _ = self._patch_orch()
        with p, mock.patch("core.modelhub.chat",
                           return_value=_chat_return('{"book_name": "新名"}')) as mchat:
            bookmeta.rename_book(self.task, "fanqie", "戍边骑奴",
                                 {"别的书", "另一本"})
        prompt = mchat.call_args[0][2]
        self.assertIn("《戍边骑奴》", prompt)          # 旧名在案（题材延续的锚）
        self.assertIn("- 别的书", prompt)              # 排除名单逐条注入
        self.assertIn("- 另一本", prompt)
        self.assertIn("- 戍边骑奴", prompt)            # 旧名自身也在名单里
        # 素材注入：goal 首行进了提示词
        self.assertIn("男频军事文", prompt)

    def test_dup_retry_then_success(self):
        p, _ = self._patch_orch()
        rets = [_chat_return('{"book_name": "别的书"}'),     # 第一轮撞排除名单
                _chat_return('{"book_name": "烽燧月"}')]
        with p, mock.patch("core.modelhub.chat", side_effect=rets) as mchat:
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴",
                                                 {"别的书"})
        self.assertEqual((new_name, err), ("烽燧月", ""))
        self.assertEqual(mchat.call_count, 2)
        # 第二轮提示词把刚撞的名字补进了排除名单
        self.assertIn("- 别的书", mchat.call_args[0][2])

    def test_always_dup_reports(self):
        p, _ = self._patch_orch()
        with p, mock.patch("core.modelhub.chat",
                           return_value=_chat_return('{"book_name": "别的书"}')):
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴",
                                                 {"别的书"})
        self.assertEqual(new_name, "")
        self.assertIn("仍与本站已有作品重名", err)

    def test_model_error(self):
        p, _ = self._patch_orch()
        with p, mock.patch("core.modelhub.chat",
                           return_value=_chat_return("", ok=False, error="超时")):
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴")
        self.assertEqual((new_name, err), ("", "超时"))

    def test_unparsable(self):
        p, _ = self._patch_orch()
        with p, mock.patch("core.modelhub.chat",
                           return_value=_chat_return("我觉得《烽火连城》不错")):
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴")
        self.assertEqual((new_name, err), ("", "返回内容无法解析出新书名"))

    def test_no_orchestrator(self):
        with mock.patch("core.modelhub.resolve_orchestrator",
                        side_effect=RuntimeError("没配链")):
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴")
        self.assertEqual(new_name, "")
        self.assertIn("编排者模型不可用", err)

    def test_long_name_clamped(self):
        p, _ = self._patch_orch()
        with p, mock.patch("core.modelhub.chat",
                           return_value=_chat_return('{"book_name": "这是一个超过十五个字上限的书名需要被截断"}')):
            new_name, err = bookmeta.rename_book(self.task, "fanqie", "戍边骑奴")
        self.assertEqual(err, "")
        self.assertLessEqual(len(new_name), 15)


if __name__ == "__main__":
    unittest.main(verbosity=1)
