"""对话意图 triage 兜底守测（app/core/intent_triage.py，C0 阶段③）。

纪律口径（迭代计划 C0 阶段③ + 决策 15）：
- triage 只出**意图草稿**，绝不执行——执行仍走前端确认卡；本模块的可证明面
  是「解析契约」：严格 JSON、书名必须来自真实清单（防幻觉）、非法字段整单
  作废、控制类只带 kind+book、publish 缺 mode 补 direct；
- fail-open：无编排者 / 模型报错 / 开关关 / 超长文本 → 一律 None；
- 成本纪律：只调一次模型，prompt 必须带书单与原话。

mock 口径（2026-11-01 实案）：core.* 与 app.core.* 是两个模块对象，
patch 必须打在 app.core.modelhub / app.core.settings。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from app.core import intent_triage, modelhub, settings

BOOKS = ["头七结账人", "神瞳鉴宝"]


def _reply(obj):
    """模型回复形状（带废话包裹，验证宽松抠取）。"""
    return {"ok": True, "text": "好的，分类如下：" + json.dumps(obj, ensure_ascii=False)}


class IntentTriageTests(unittest.TestCase):
    def test_valid_publish_parsed(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_reply({"kind": "publish",
                                                    "book": "头七结账人", "count": 3,
                                                    "platform": "fanqie",
                                                    "mode": "draft", "time": ""})):
            it = intent_triage.triage("把《头七结账人》最新几章存草稿", BOOKS)
        self.assertEqual(it, {"kind": "publish", "book": "头七结账人", "count": 3,
                              "platform": "fanqie", "mode": "draft", "time": ""})

    def test_publish_without_mode_defaults_direct(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_reply({"kind": "publish",
                                                    "book": "神瞳鉴宝", "count": 0,
                                                    "platform": "", "mode": "",
                                                    "time": ""})):
            it = intent_triage.triage("把《神瞳鉴宝》发出去", BOOKS)
        self.assertEqual(it["mode"], "direct")
        self.assertEqual(it["count"], 0)

    def test_hallucinated_book_cleared(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_reply({"kind": "query",
                                                    "book": "不存在的书", "count": 0,
                                                    "platform": "", "mode": "",
                                                    "time": ""})):
            it = intent_triage.triage("查一下《不存在的书》", BOOKS)
        self.assertEqual(it["kind"], "query")
        self.assertEqual(it["book"], "", "书名必须来自真实清单（防幻觉）")

    def test_control_kind_stripped(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_reply({"kind": "pause",
                                                    "book": "头七结账人", "count": 5,
                                                    "platform": "fanqie",
                                                    "mode": "draft",
                                                    "time": "10:00"})):
            it = intent_triage.triage("暂停《头七结账人》", BOOKS)
        self.assertEqual(it, {"kind": "pause", "book": "头七结账人", "count": 0,
                              "platform": "", "mode": "", "time": ""},
                         "控制类只带 kind+book，其余字段不带（防下游误读）")

    def test_bad_inputs_fail_open(self):
        cases = [
            {"kind": "explode", "book": "", "count": 0, "platform": "",
             "mode": "", "time": ""},                      # 非法 kind
            {"kind": "none", "book": "", "count": 0, "platform": "",
             "mode": "", "time": ""},                      # 模型判 none
            "不是JSON",                                    # 答非所问
            {"kind": "publish", "book": "头七结账人", "count": 500},  # count 越界→钳 0
        ]
        for bad in cases:
            with mock.patch.object(settings, "load", return_value={}), \
                 mock.patch.object(modelhub, "resolve_orchestrator",
                                   return_value=({"id": "p1"}, "m1")), \
                 mock.patch.object(modelhub, "chat",
                                   return_value={"ok": True, "text": json.dumps(bad)
                                                if isinstance(bad, dict) else bad}):
                it = intent_triage.triage("把《头七结账人》发一下", BOOKS)
            self.assertTrue(it is None or it.get("count", 0) == 0,
                            "非法输入必须作废或钳位：%r" % bad)

    def test_no_orchestrator_fail_open(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator", return_value=None), \
             mock.patch.object(modelhub, "chat") as mch:
            self.assertIsNone(intent_triage.triage("把《头七结账人》发一下", BOOKS))
        mch.assert_not_called(), "无编排者不得发起调用"

    def test_disabled_fail_open(self):
        with mock.patch.object(settings, "load",
                               return_value={"chat_triage_enabled": False}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat") as mch:
            self.assertIsNone(intent_triage.triage("把《头七结账人》发一下", BOOKS))
        mch.assert_not_called(), "开关关不得发起调用"

    def test_model_error_fail_open(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat", side_effect=RuntimeError("boom")):
            self.assertIsNone(intent_triage.triage("把《头七结账人》发一下", BOOKS))

    def test_prompt_carries_books_and_text(self):
        captured = []

        def fake_chat(pid, model, prompt, **kw):
            captured.append(prompt)
            return {"ok": True, "text": json.dumps({"kind": "none"})}

        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat", side_effect=fake_chat):
            intent_triage.triage("把《头七结账人》发一下", BOOKS)
        self.assertEqual(len(captured), 1, "每次至多一次模型调用")
        self.assertIn("头七结账人", captured[0])
        self.assertIn("神瞳鉴宝", captured[0])
        self.assertIn("把《头七结账人》发一下", captured[0])

    def test_empty_and_oversize_text(self):
        with mock.patch.object(settings, "load", return_value={}):
            self.assertIsNone(intent_triage.triage("", BOOKS))
            self.assertIsNone(intent_triage.triage("x" * 201, BOOKS))


if __name__ == "__main__":
    unittest.main()
