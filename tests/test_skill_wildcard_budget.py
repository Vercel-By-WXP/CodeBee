# -*- coding: utf-8 -*-
"""wildcard 包预算与教训保底（skills.block_for 预算纪律）单测。

跑法：python -m unittest discover -s tests -p "test_skill_wildcard_budget.py" -v

背景（2026-09-21 巡检实锤）：39 个 wildcard 通配包全文注入先把 9000 字全局
上限吃光，项目教训排在末尾被整段截掉——教训是本机真实运行沉淀的最重要
上下文，必须保底。
"""
from __future__ import annotations

from base import BaseTest


def _write_user_pack(self, filename, content):
    d = self.data_dir / "skillpacks"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(content, encoding="utf-8")
    from app.core import skills
    with skills._LOCK:
        skills._user_dir_mtime["ts"] = 0.0
        skills._user_dir_mtime["ids"] = None


class WildcardBudgetTests(BaseTest):
    def _skills(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        return skills

    def test_lessons_survive_giant_wildcard_packs(self):
        """教训保底：巨型 wildcard 包吃满预算后，项目教训仍完整注入。"""
        skills = self._skills()
        body = "通用规则条目。" * 4000          # ~2.4 万字/包
        for i in range(3):
            _write_user_pack(self, "giant%d.md" % i,
                             "---\nname: 巨型通配包%d\nscopes:\n  - \"*\"\n---\n%s" % (i, body))
        skills.upsert_lesson("code", "节奏教训", "每章必须有钩子" * 8)
        text, used = skills.block_for({"type": "code"})
        self.assertIn("节奏教训", text)                    # 教训活着
        self.assertIn("每章必须有钩子", text)               # 教训内容完整
        self.assertLessEqual(len(text), skills.MAX_INJECT_CHARS + 30)
        self.assertTrue(any(u.startswith("user-") for u in used))   # 用户包确实参与注入

    def test_wildcard_pack_truncated_with_marker(self):
        """wildcard 单包超限被截断并带人话标记。"""
        skills = self._skills()
        _write_user_pack(self, "giant_solo.md",
                         "---\nname: 巨型通配包solo\nscopes:\n  - \"*\"\n---\n"
                         + "通用规则条目。" * 4000)
        text, _ = skills.block_for({"type": "code"})
        self.assertIn("超出通配注入预算已截断", text)
        self.assertLess(len(text), 6000)                   # 截断真实生效

    def test_scoped_pack_not_capped(self):
        """定向命中的包不受单包上限（番茄/七猫这类定向规范全文注入）。"""
        skills = self._skills()
        _write_user_pack(self, "doc_big.md",
                         "---\nname: 定向大包\nscopes:\n  - doc\n---\n" + "分三段。" * 1500)
        text, _ = skills.block_for({"type": "doc"})
        self.assertNotIn("超出通配注入预算已截断", text)
        self.assertIn("定向大包", text)

    def test_lesson_hits_still_counted(self):
        """预算改造后教训热度记账不回归。"""
        skills = self._skills()
        a = skills.upsert_lesson("code", "计数教训", "别写崩人设" * 5)
        before = next(l.get("hits", 0) for l in skills.list_lessons("code")
                      if l["id"] == a["id"])
        skills.block_for({"type": "code"})
        after = next(l.get("hits", 0) for l in skills.list_lessons("code")
                     if l["id"] == a["id"])
        self.assertEqual(after, before + 1)

    def test_empty_state_returns_empty(self):
        """无匹配包无教训仍返回空串（老契约不变）。"""
        skills = self._skills()
        for p in skills.BUILTIN_PACKS:
            skills.pack_op(p["id"], "disable")
        try:
            text, used = skills.block_for({"type": "novel"})
        finally:
            for p in skills.BUILTIN_PACKS:
                skills.pack_op(p["id"], "enable")
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
