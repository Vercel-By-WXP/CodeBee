# -*- coding: utf-8 -*-
"""教训注入 karma 标注（批5 对称补齐 09-25）单测：won≥2 的教训在注入
行带「（已验证有效 N 次）」实证标——与知识库［有据］置信标同构；
won<2 零噪音；run 内字节稳定（won 只在 run 收尾回写）。

跑法：python -m unittest discover -s tests -p "test_lesson_karma_mark.py" -v
"""
from __future__ import annotations

from base import BaseTest


class LessonKarmaMarkTests(BaseTest):

    def _mk_lesson(self, title, goal):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        a = skills.upsert_lesson("code", title, "内容" * 4, category="流程规范")
        # 三轮过审 → won=3（block_for 注册 + note_outcome 记账）
        for i in range(3):
            skills.block_for({"type": "code", "goal": goal}, run_id="r-km%d" % i)
            skills.note_outcome("r-km%d" % i, True, reward=[a["id"]])
        return skills, a

    def test_won_marked_in_injection(self):
        """won=3 → 注入行带「已验证有效 3 次」。"""
        sk, a = self._mk_lesson("网关限流先看冷却", "网关限流")
        txt, used = sk.block_for({"type": "code", "goal": "网关限流"})
        self.assertIn("已验证有效 3 次", txt)
        self.assertIn(a["id"], used)

    def test_low_won_no_mark(self):
        """won=1 不标（1 次可能是巧合，噪音为零）。"""
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        a = skills.upsert_lesson("code", "部署前跑冒烟", "内容" * 4, category="流程规范")
        skills.block_for({"type": "code", "goal": "部署冒烟"}, run_id="r-km9")
        skills.note_outcome("r-km9", True, reward=[a["id"]])
        txt, _ = skills.block_for({"type": "code", "goal": "部署冒烟"})
        self.assertNotIn("已验证有效", txt)

    def test_id_and_title_intact(self):
        """编号与标题契约不回归（复盘官点名靠编号）。"""
        sk, a = self._mk_lesson("回归测试必跑", "回归测试")
        txt, _ = sk.block_for({"type": "code", "goal": "回归测试"})
        self.assertIn("[%s] **回归测试必跑**" % a["id"], txt)
        self.assertIn("（已验证有效 3 次）", txt)


if __name__ == "__main__":
    import unittest
    unittest.main()
