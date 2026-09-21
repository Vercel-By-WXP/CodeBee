# -*- coding: utf-8 -*-
"""教训 outcome 加权（tradememory 借鉴）单测：注入登记 → run 结局回写胜负 →
排序按 karma 提权。"""
from __future__ import annotations

from base import BaseTest


class OutcomeKarmaTests(BaseTest):
    def _skills(self):
        import app.core.skills as sk
        sk._FILE = self.data_dir / "skills.json"
        sk._INJECTED.clear()
        return sk

    def test_block_for_registers_and_outcome_writes(self):
        sk = self._skills()
        a = sk.upsert_lesson("code", "好教训", "能防住问题的具体做法" * 3)
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-x")
        # 登记在案
        self.assertIn("run-x", sk._INJECTED)
        # 过审 → won+1；幂等：第二次消费不重复记
        sk.note_outcome("run-x", True)
        les = next(l for l in sk.list_lessons("code") if l["id"] == a["id"])
        self.assertEqual(les.get("won"), 1)
        self.assertNotIn("run-x", sk._INJECTED)
        sk.note_outcome("run-x", True)
        les = next(l for l in sk.list_lessons("code") if l["id"] == a["id"])
        self.assertEqual(les.get("won"), 1)   # 已消费不再记

    def test_failed_run_marks_lost(self):
        sk = self._skills()
        b = sk.upsert_lesson("code", "没用教训", "防不住问题的空泛说法" * 3)
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-y")
        sk.note_outcome("run-y", False)
        les = next(l for l in sk.list_lessons("code") if l["id"] == b["id"])
        self.assertEqual(les.get("lost"), 1)

    def test_learn_from_run_consumes_outcome(self):
        """run 收尾真实路径：learn_from_run 头部按 verdict.pass 记胜负。"""
        from app.core import store, pipeline
        sk = self._skills()
        c = sk.upsert_lesson("code", "链路教训", "走真实收尾链验证" * 3)
        task = store.create_task({"type": "code", "title": "outcome", "goal": "g",
                                  "workdir": str(self.workdir), "mode": "auto",
                                  "verify_command": "exit 0"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        sk.block_for({"type": "code", "goal": "g"}, run_id=run["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        r = store.get_run(run["id"])
        self.assertEqual("done", r["status"])
        self.assertTrue((r.get("verdict") or {}).get("pass"))
        les = next(l for l in sk.list_lessons("code") if l["id"] == c["id"])
        self.assertEqual(les.get("won") or 0, 1)   # 过审 → won 落账
        self.assertNotIn(run["id"], sk._INJECTED)  # 登记已消费

    def test_ranking_prefers_positive_karma(self):
        """同相关性下：正 karma 教训排前、负 karma 垫底（tradememory 核心）。"""
        sk = self._skills()
        good = sk.upsert_lesson("code", "共享标题甲", "具体做法甲" * 3)
        bad = sk.upsert_lesson("code", "共享标题乙", "具体做法乙" * 3)
        mid = sk.upsert_lesson("code", "共享标题丙", "具体做法丙" * 3)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == good["id"]:
                    it["won"] = 5
                elif it["id"] == bad["id"]:
                    it["lost"] = 4
            sk._save(data)
        top = sk.relevance_top(
            sk.list_lessons("code", only_enabled=True),
            {"type": "code", "goal": "共享标题"}, 2)   # limit<总数才触发排序
        ids = [x["id"] for x in top]
        self.assertEqual(ids[0], good["id"])          # 好教训第一
        self.assertNotIn(bad["id"], ids)              # 坏教训被正 karma 挤出 top

    def test_injected_map_bounded(self):
        sk = self._skills()
        for i in range(205):
            sk.block_for({"type": "code", "goal": "x%d" % i}, run_id="r%d" % i)
        self.assertLess(len(sk._INJECTED), 205)       # 上限触发整体作废


if __name__ == "__main__":
    import unittest
    unittest.main()
