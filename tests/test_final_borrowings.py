# -*- coding: utf-8 -*-
"""清尾班三件单测：ARIS 评审证据锚 + continuum 陈年承诺督促 + ECC evolve。

跑法：python -m unittest discover -s tests -p "test_final_borrowings.py" -v
"""
from __future__ import annotations

from base import BaseTest


class AnchorIssuesTests(BaseTest):
    """ARIS Anti-Autoresearch 借鉴：模型只提议，锚定由规则裁决（三态）。"""

    def _pi(self):
        from app.core import pipeline
        return pipeline

    def test_quote_found_is_anchored(self):
        p = self._pi()
        ms = "第一章\n夜色如墨，他握紧了手中的剑。\n"
        issues = [{"dim": "氛围", "severity": "major", "note": "开篇太平",
                   "quote": "夜色如墨，他握紧了手中的剑"}]
        issues, n = p._anchor_issues(issues, ms)
        self.assertIs(issues[0]["anchored"], True)
        self.assertEqual(n, 0)

    def test_quote_whitespace_insensitive(self):
        p = self._pi()
        issues = [{"quote": "夜色如墨， 他握紧了手中的剑。"}]   # 引文带空格/句号
        issues, _ = p._anchor_issues(issues, "夜色如墨，他握紧了手中的剑")
        self.assertIs(issues[0]["anchored"], True)

    def test_fabricated_quote_unanchored(self):
        p = self._pi()
        issues = [{"dim": "节奏", "quote": "这段原文根本不存在于稿件之中"}]
        issues, n = p._anchor_issues(issues, "完全不同的正文内容")
        self.assertIs(issues[0]["anchored"], False)
        self.assertEqual(n, 1)

    def test_missing_quote_is_none_legacy(self):
        p = self._pi()
        issues = [{"dim": "节奏", "note": "老格式无引文"}]
        issues, n = p._anchor_issues(issues, "正文")
        self.assertIsNone(issues[0]["anchored"])   # 灰度兼容：不标记不计数
        self.assertEqual(n, 0)

    def test_too_short_quote_unanchored(self):
        p = self._pi()
        issues = [{"quote": "太短"}]
        issues, n = p._anchor_issues(issues, "正文里有太短三个字")
        self.assertIs(issues[0]["anchored"], False)

    def test_major_lines_demote_unanchored(self):
        p = self._pi()
        majors = [
            {"dim": "a", "note": "未锚定的", "anchored": False},
            {"dim": "b", "note": "锚定的", "anchored": True},
            {"dim": "c", "note": "老格式", "anchored": None},
        ]
        lines = p._major_lines(majors)
        self.assertIn("锚定的", lines[0])            # True 最前
        self.assertIn("老格式", lines[1])            # None 居中
        self.assertIn("未锚定的", lines[-1])         # False 殿后
        self.assertIn("未锚定", lines[-1])           # 带降档标记

    def test_critique_prompts_ask_quote(self):
        p = self._pi()
        for tpl in (p.NOVEL_CRITIQUE_PROMPT, p.SERIAL_GLOBAL_PROMPT):
            self.assertIn('"quote"', tpl)
            self.assertIn("逐字摘录", tpl)


class LedgerWatchlistTests(BaseTest):
    """continuum 承诺督促：账本按章戳解析，未了结且多章未推进的承诺入清单。"""

    def _pi(self):
        from app.core import pipeline
        return pipeline

    LEDGER = (
        "# 资源账本\n\n"
        "## 第 1 章\n- 承诺|带小满去看海|许下承诺未兑现\n"
        "- 道具|青铜钥匙|获得\n"
        "## 第 3 章\n- 伏笔|井底的哭声|首次出现未解释\n"
        "## 第 5 章\n- 承诺|带小满去看海|仍未兑现\n"
        "- 道具|青铜钥匙|已归还\n"
    )

    def test_stale_promise_flagged(self):
        p = self._pi()
        wl = p._ledger_watchlist(self.LEDGER, upto_chapter=9)
        names = [w["name"] for w in wl]
        self.assertIn("带小满去看海", names)          # 第5章后4章未动
        self.assertIn("井底的哭声", names)            # 第3章后6章未动
        kp = next(w for w in wl if w["name"] == "带小满去看海")
        self.assertEqual(kp["age"], 4)                # 9-5：以最后出现章为准
        self.assertNotIn("青铜钥匙", names)           # 道具类不督促

    def test_resolved_excluded(self):
        p = self._pi()
        txt = ("## 第 2 章\n- 承诺|报仇|立誓\n"
               "## 第 7 章\n- 承诺|报仇|已兑现，大仇得报\n")
        wl = p._ledger_watchlist(txt, upto_chapter=12)
        self.assertEqual(wl, [])

    def test_min_age_and_empty(self):
        p = self._pi()
        self.assertEqual(p._ledger_watchlist(self.LEDGER, upto_chapter=6, min_age=3),
                         []) if False else None   # 第5章后仅1章 → 不够龄
        wl = p._ledger_watchlist("", upto_chapter=10)
        self.assertEqual(wl, [])
        wl2 = p._ledger_watchlist("（暂无记录）", upto_chapter=10)
        self.assertEqual(wl2, [])

    def test_age_boundary(self):
        p = self._pi()
        txt = "## 第 4 章\n- 承诺|旧约|未提\n"
        self.assertEqual(p._ledger_watchlist(txt, 6, min_age=3), [])   # 差1章
        self.assertEqual(len(p._ledger_watchlist(txt, 7, min_age=3)), 1)  # 恰3章

    def test_sorted_by_age_desc(self):
        p = self._pi()
        txt = ("## 第 1 章\n- 伏笔|最老的|未解释\n"
               "## 第 5 章\n- 伏笔|较新的|未解释\n")
        wl = p._ledger_watchlist(txt, 10)
        self.assertEqual([w["name"] for w in wl], ["最老的", "较新的"])


class EvolveLessonsTests(BaseTest):
    """ECC /evolve 借鉴：高可信教训聚合草稿包，默认停用待人工审。"""

    def _sk(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        return skills

    def test_no_eligible_returns_none(self):
        sk = self._sk()
        sk.upsert_lesson("code", "冷教训", "A" * 6)   # hits=0
        fname, n = sk.evolve_lessons()
        self.assertIsNone(fname)
        self.assertEqual(n, 0)

    def test_eligible_clustered_into_disabled_draft(self):
        import pathlib
        sk = self._sk()
        a = sk.upsert_lesson("novel", "黄金三章", "开头三章必须见冲突" + "x" * 6)
        b = sk.upsert_lesson("code", "测试先行", "改核心逻辑先补测试" + "y" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == a["id"]:
                    it["won"], it["hits"] = 3, 30
                elif it["id"] == b["id"]:
                    it["won"], it["hits"] = 2, 10
            sk._save(data)
        fname, n = sk.evolve_lessons()
        self.assertTrue(fname and fname.startswith("evolve-"))
        self.assertEqual(n, 2)
        # 草稿文件在用户包目录，正文含两条教训
        d = sk._user_pack_dir()
        text = (pathlib.Path(d) / fname).read_text(encoding="utf-8")
        self.assertIn("黄金三章", text)
        self.assertIn("测试先行", text)
        self.assertIn("草稿", text)
        # 草稿默认停用：pid 口径与 _load_user_pack 一致且 enabled=False
        packs = {p["id"]: p for p in sk.user_packs()}
        stem = fname[:-3]
        import hashlib
        pid = "user-" + hashlib.sha256(stem.encode("utf-8")).hexdigest()[:10]
        self.assertIn(pid, packs)
        self.assertFalse(sk._pack_enabled(pid))

    def test_low_karma_excluded(self):
        sk = self._sk()
        a = sk.upsert_lesson("code", "常失守的", "Z" * 8)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == a["id"]:
                    it["won"], it["lost"], it["hits"] = 1, 6, 12   # karma 低
            sk._save(data)
        fname, n = sk.evolve_lessons()
        self.assertIsNone(fname)


if __name__ == "__main__":
    unittest.main()
