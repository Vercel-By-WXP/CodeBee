"""triage 校准守测（11-07，C0 阶段③遗留；决策 15 已拍板、决策 16 UI 冻结解冻后落地）。

纪律口径：
- **默认路径逐字不变**：合法书名 / 合法 mode / 合法 time 的返回与 11-01 完全一致；
- **fail-closed**：归一后仍对不上真实清单、归一后多重命中、`schedule` 缺合法时间，
  一律不给草稿（`None` 或空书名），不猜、不补默认时间；
- **真被判过样本数**：设 `CODEBEE_PROD_DIR` 时用生产 `publish/books.json` 的真实
  书名跑全量归一并报 N；不设时只跑契约样本，**契约样本不算真样本**、不上绿标；
- **消费点**：AST 棘轮锁 `_match_book` 真被 `_parse_reply` 调用、锁前端 `def:true`
  锚点，防止「判据写了却没人用」（判据沉默三问）。

mock 口径沿用 `test_intent_triage.py`：`core.*` 与 `app.core.*` 是两个模块对象，
patch 必须打在 `app.core.modelhub` / `app.core.settings`。
"""
from __future__ import annotations

import ast
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from app.core import intent_triage, modelhub, settings

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "core" / "intent_triage.py"
FRONTEND = ROOT / "app" / "ui" / "chat-intent.js"
BOOKS = ["头七结账人", "神瞳鉴宝"]


def _reply(obj):
    return {"ok": True, "text": "分类结果：" + json.dumps(obj, ensure_ascii=False)}


def _obj(**kw):
    base = {"kind": "publish", "book": "", "count": 0, "platform": "",
            "mode": "", "time": ""}
    base.update(kw)
    return base


def _triage(reply, books=None):
    with mock.patch.object(settings, "load", return_value={}), \
         mock.patch.object(modelhub, "resolve_orchestrator",
                           return_value=({"id": "p1"}, "m1")), \
         mock.patch.object(modelhub, "chat", return_value=_reply(reply)):
        return intent_triage.triage("把《头七结账人》发一下", books or BOOKS)


class TriageCalibrationTests(unittest.TestCase):
    # ---------------- 书名归一：真书名不该被当成幻觉 ----------------
    def test_01_wrapped_book_normalized(self):
        it = _triage(_obj(book="《头七结账人》", count=2))
        self.assertEqual(it["book"], "头七结账人", "带书名号的真书名要回填清单原值")

    def test_02_numbered_book_normalized(self):
        it = _triage(_obj(book="1. 神瞳鉴宝"))
        self.assertEqual(it["book"], "神瞳鉴宝", "prompt 书单就是编号展示的")

    def test_03_fullwidth_space_and_quotes_normalized(self):
        it = _triage(_obj(book="\u3000「头七结账人」\u3000"))
        # 「」不在归一白名单里 → 判不了就清空，不能含糊放过去
        self.assertEqual(it["book"], "", "未纳入白名单的包装一律 fail-closed")

    def test_04_book_brackets_only_normalized(self):
        it = _triage(_obj(book="  《神瞳鉴宝》  "))
        self.assertEqual(it["book"], "神瞳鉴宝")

    def test_05_unknown_book_still_empty(self):
        it = _triage(_obj(kind="query", book="不存在的书"))
        self.assertEqual(it["book"], "", "归一后不在清单仍必须清空")

    def test_06_ambiguous_normalized_match_stays_empty(self):
        # 清单里同时有「A」与「《A》」→ 归一后多重命中，判不了就空，不许挑第一个。
        books = ["头七结账人", "《头七结账人》"]
        self.assertEqual(intent_triage._match_book("头七结账人", books), "")
        self.assertEqual(intent_triage._match_book("不存在的书", books), "")

    def test_07_norm_book_is_deterministic(self):
        self.assertEqual(intent_triage._norm_book("1. 《头七结账人》"),
                         intent_triage._norm_book("头七结账人"))

    # ---------------- 定时发布：不替用户编时间 ----------------
    def test_08_schedule_without_time_discarded(self):
        self.assertIsNone(_triage(_obj(mode="schedule", time="")),
                          "没说时间的定时草稿必须作废，不能让前端补默认 10:10")

    def test_09_schedule_with_bad_time_discarded(self):
        for bad in ("25:99", "9点", "明早", "10:00左右"):
            self.assertIsNone(_triage(_obj(mode="schedule", time=bad)),
                              "非 HH:MM 一律判不了：%s" % bad)

    def test_10_schedule_with_valid_time_kept(self):
        it = _triage(_obj(mode="schedule", time="09:30"))
        self.assertEqual(it["mode"], "schedule")
        self.assertEqual(it["time"], "09:30")

    def test_11_publish_default_mode_unchanged(self):
        it = _triage(_obj(book="头七结账人"))
        self.assertEqual(it, {"kind": "publish", "book": "头七结账人", "count": 0,
                              "platform": "", "mode": "direct", "time": ""},
                         "11-01 默认路径逐字不变")

    def test_12_control_kind_fields_still_stripped(self):
        it = _triage(_obj(kind="pause", book="《头七结账人》", count=5,
                          platform="fanqie", mode="draft", time="10:00"))
        self.assertEqual(it, {"kind": "pause", "book": "头七结账人", "count": 0,
                              "platform": "", "mode": "", "time": ""})

    def test_13_none_kind_explicit(self):
        self.assertIsNone(_triage({"kind": "none"}),
                          "prompt 契约里的「都不是」必须显式收口为 None")

    # ---------------- 消费点 / 零回归 ----------------
    def test_14_source_consumption_points(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("book = _match_book(j.get(\"book\"), books)", src,
                      "归一判据必须真被 _parse_reply 消费")
        self.assertIn("if kind == \"none\":", src)
        self.assertIn('and it["mode"] == "schedule" and not it["time"]:', src)
        tree = ast.parse(src)
        fns = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertTrue({"_norm_book", "_match_book", "_parse_reply"} <= fns)

    def test_15_frontend_default_time_anchor(self):
        """后端为什么拒写：前端确实会把缺时间补成默认 10:10。锚点没了要重新评估。"""
        src = FRONTEND.read_text(encoding="utf-8")
        self.assertIn("def: true", src)
        self.assertIn('out.mode === "schedule"', src)

    def test_16_one_model_call_at_most(self):
        with mock.patch.object(settings, "load", return_value={}), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_reply(_obj(mode="schedule"))) as chat:
            intent_triage.triage("明早发一下", BOOKS)
        self.assertEqual(chat.call_count, 1, "校准不得增加模型调用次数")

    # ---------------- 真实书单（可选，报真被判过样本数） ----------------
    def test_17_real_book_titles_if_prod_declared(self):
        prod = os.environ.get("CODEBEE_PROD_DIR")
        if not prod:
            self.skipTest("未声明 CODEBEE_PROD_DIR：本条不报真样本数（契约样本不算）")
        from app.core.publish import journey_sources as js
        bindings, _stat, _problems = js.load_book_bindings(prod)
        titles = sorted({str(t) for names in bindings.values()
                         for t in names if str(t).strip()})
        if not titles:
            self.skipTest("生产 publish/books.json 无真实书名：unknown，不上绿标")
        ambiguous = [t for t in titles
                     if len([b for b in titles
                             if intent_triage._norm_book(b)
                             == intent_triage._norm_book(t)]) > 1]
        clean = [t for t in titles if t not in ambiguous]
        ok = 0
        for t in clean:
            for noisy in ("《%s》" % t, "1. %s" % t, "  %s  " % t):
                if intent_triage._match_book(noisy, titles) == t:
                    ok += 1
        print("真被判过书名归一样本 %d/%d（真实书名 %d 本，归一多重命中 %d 本保持 unknown）"
              % (ok, len(clean) * 3, len(titles), len(ambiguous)))
        self.assertEqual(ok, len(clean) * 3)


if __name__ == "__main__":
    unittest.main()
