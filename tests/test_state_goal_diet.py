import json
import sys
import unittest
from pathlib import Path

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import main                        # noqa: E402


class StateGoalDietTests(unittest.TestCase):
    """state 载荷活动任务 goal 瘦身回归（2026-10-22 开页慢案续）：
    goal 是用户自由文本、实测最大 346KB，随每次推送出海是载荷最大单点。
    截断必须落在浅拷贝上，store 的活字典绝不能被原地改——编排链/详情接口/
    续跑都还在读全量；前端「基于此任务新建」靠 goal_truncated 落 /detail 取原文。"""

    def test_huge_goal_truncated_with_flag(self):
        big = "X" * (main.GOAL_STATE_LIMIT + 100)
        task = {"id": "t-big", "title": "大目标", "goal": big, "status": "done"}
        out = main._state_task_active(task)
        self.assertEqual(len(out["goal"]), main.GOAL_STATE_LIMIT)
        self.assertTrue(out["goal_truncated"])
        self.assertEqual(out["title"], "大目标")   # 其余字段原样保留

    def test_store_dict_not_mutated(self):
        big = "Y" * (main.GOAL_STATE_LIMIT + 1)
        task = {"id": "t-big", "goal": big}
        main._state_task_active(task)
        self.assertEqual(len(task["goal"]), main.GOAL_STATE_LIMIT + 1)
        self.assertNotIn("goal_truncated", task)

    def test_small_goal_passthrough_untouched(self):
        task = {"id": "t-ok", "goal": "短目标"}
        self.assertIs(main._state_task_active(task), task)

    def test_non_string_goal_passthrough(self):
        task = {"id": "t-weird", "goal": None}
        self.assertIs(main._state_task_active(task), task)

    def test_payload_carries_truncated_goal_store_intact(self):
        big = "Z" * (main.GOAL_STATE_LIMIT + 7)
        task = {"id": "t-diet", "title": "载荷集成", "goal": big, "status": "done"}
        main.store._TASKS["t-diet"] = task
        main._STATE_JSON_CACHE["ver"] = None
        try:
            d = json.loads(main._state_payload_json("diet-client"))
            entry = next(t for t in d["tasks"] if t["id"] == "t-diet")
            self.assertEqual(len(entry["goal"]), main.GOAL_STATE_LIMIT)
            self.assertTrue(entry["goal_truncated"])
            self.assertEqual(task["goal"], big)   # store 原字典未被变异
        finally:
            main.store._TASKS.pop("t-diet", None)
            main._STATE_JSON_CACHE["ver"] = None


if __name__ == "__main__":
    unittest.main()
