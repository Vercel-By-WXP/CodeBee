# -*- coding: utf-8 -*-
"""任务级串行闸门守测（unittest 风格，release_gate 的 discover 能收到）。

三件事：默认关必须与现状逐字等价；开启后四本只放一本；坏输入 fail-closed
不静默放行。另锁「本模块尚未接调度」的诚实边界，防止有人把判据当上线。
"""
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core.publish import serial_gate  # noqa: E402

ON = {"enabled": True, "max_active": 1}
OFF = {"enabled": False, "max_active": 1}


def cand(tid, created=""):
    return {"task_id": tid, "created_at": created}


def running(tid):
    return {"task_id": tid, "status": "running"}


class SerialGateTest(unittest.TestCase):

    def test_default_policy_is_off(self):
        self.assertFalse(serial_gate.policy_from_env({})["enabled"])
        self.assertEqual(1, serial_gate.policy_from_env({})["max_active"])

    def test_bad_env_values_do_not_enable(self):
        self.assertFalse(serial_gate.policy_from_env(
            {serial_gate.ENV_ENABLED: "maybe"})["enabled"])
        pol = serial_gate.policy_from_env(
            {serial_gate.ENV_ENABLED: "1", serial_gate.ENV_MAX_ACTIVE: "nonsense"})
        self.assertTrue(pol["enabled"])
        self.assertEqual(1, pol["max_active"])

    def test_max_active_is_clamped(self):
        self.assertEqual(16, serial_gate.policy_from_env(
            {serial_gate.ENV_MAX_ACTIVE: "999"})["max_active"])
        self.assertEqual(1, serial_gate.policy_from_env(
            {serial_gate.ENV_MAX_ACTIVE: "-3"})["max_active"])

    def test_off_passes_everything_through_unchanged(self):
        cands = [cand("t-1", "1"), cand("t-2", "2")]
        plan = serial_gate.plan_dispatch(cands, [running("t-9")], policy=OFF,
                                         quota_ok=False)
        self.assertFalse(plan["enabled"])
        self.assertEqual(cands, plan["allow"])
        self.assertEqual([], plan["defer"])
        self.assertIn("关闭", serial_gate.human_report(plan))

    def test_four_books_only_one_starts(self):
        cands = [cand("t-%d" % i, "2026-11-0%d" % i) for i in range(1, 5)]
        plan = serial_gate.plan_dispatch(cands, [], policy=ON)
        self.assertEqual(1, len(plan["allow"]))
        self.assertEqual("t-1", plan["allow"][0]["task_id"])
        self.assertEqual(3, len(plan["defer"]))
        self.assertTrue(all(d["reason"] == "pool_full" for d in plan["defer"]))

    def test_same_task_already_active_is_deferred(self):
        plan = serial_gate.plan_dispatch([cand("t-1")], [running("t-1")], policy=ON)
        self.assertEqual([], plan["allow"])
        self.assertEqual("task_already_active", plan["defer"][0]["reason"])

    def test_max_active_two_allows_two(self):
        pol = {"enabled": True, "max_active": 2}
        plan = serial_gate.plan_dispatch(
            [cand("t-1", "1"), cand("t-2", "2"), cand("t-3", "3")], [], policy=pol)
        self.assertEqual(2, len(plan["allow"]))

    def test_quota_not_restored_blocks_all(self):
        plan = serial_gate.plan_dispatch([cand("t-1")], [], policy=ON,
                                         quota_ok=False)
        self.assertEqual([], plan["allow"])
        self.assertEqual("quota_not_restored", plan["defer"][0]["reason"])

    def test_bad_input_is_fail_closed(self):
        for cands, runs in [("not-a-list", []), ([None], []),
                            ([{"no_task": 1}], []), ([cand("t-1")], "bad")]:
            plan = serial_gate.plan_dispatch(cands, runs, policy=ON)
            self.assertEqual([], plan["allow"], (cands, runs))
            self.assertTrue(any("bad_input" in r for r in plan["reasons"]))

    def test_non_active_runs_do_not_occupy_slots(self):
        done = [{"task_id": "t-9", "status": "failed"},
                {"task_id": "t-8", "status": "done"}]
        plan = serial_gate.plan_dispatch([cand("t-1")], done, policy=ON)
        self.assertEqual(1, len(plan["allow"]))
        self.assertEqual([], plan["active_tasks"])

    def test_duplicate_candidate_uses_one_slot(self):
        pol = {"enabled": True, "max_active": 2}
        plan = serial_gate.plan_dispatch(
            [cand("t-1", "1"), cand("t-1", "2"), cand("t-2", "3")], [], policy=pol)
        self.assertEqual(2, len(plan["allow"]))
        self.assertEqual("duplicate_candidate", plan["defer"][0]["reason"])

    def test_honest_boundary_not_wired_into_scheduler(self):
        """源码锚点：模块不得自称已接调度；接了以后此测要同步更新。"""
        src = (Path(APP) / "core" / "publish" / "serial_gate.py").read_text(
            encoding="utf-8")
        self.assertIn("尚未接入调度消费点", src)
        self.assertIn("默认关闭", src)


if __name__ == "__main__":
    unittest.main()
