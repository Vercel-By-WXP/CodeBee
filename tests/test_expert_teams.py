# -*- coding: utf-8 -*-
"""专家团（2026-11-03）单测：内置团完整性、校验红线、存删、任务固化与提示词注入。

全部走临时数据目录，不碰真实 data/。
"""
import tempfile
import unittest
from pathlib import Path

from app.core import expert_teams, flows, paths, store


def _tmp_dir(case):
    case.tmp = tempfile.TemporaryDirectory()
    root = Path(case.tmp.name)
    case.old = {"file": flows._FILE, "data_dir": paths.DATA_DIR,
                "tasks_dir": paths.TASKS_DIR}
    flows._FILE = root / "flows.json"
    paths.DATA_DIR = root
    paths.TASKS_DIR = root / "tasks"
    paths.TASKS_DIR.mkdir(parents=True, exist_ok=True)
    return root


def _restore(case):
    flows._FILE = case.old["file"]
    paths.DATA_DIR = case.old["data_dir"]
    paths.TASKS_DIR = case.old["tasks_dir"]
    case.tmp.cleanup()


class BuiltinTeamsTests(unittest.TestCase):
    def test_builtin_teams_are_complete(self):
        teams = expert_teams.list_teams()
        self.assertEqual(6, len([t for t in teams if t.get("builtin")]))
        for team in teams:
            self.assertTrue(team["id"].startswith("team-"))
            self.assertTrue(team["name"])
            self.assertGreaterEqual(len(team["members"]), 2)
            self.assertEqual(len(team["members"]), team["member_count"])
            # 每位成员的流程必须真实存在，且主责可解析
            for member in team["members"]:
                self.assertIsNotNone(flows.get_flow(member["flow_id"]),
                                     "%s 的成员 %s 流程不存在" % (team["id"], member["flow_id"]))
            self.assertEqual(team["members"][0]["flow_id"], expert_teams.primary_flow(team))

    def test_snapshot_pins_names_and_focus(self):
        snap = expert_teams.snapshot(expert_teams.get_team("team-long-fiction"))
        self.assertEqual("novel", snap["primary_flow"])
        names = [m["name"] for m in snap["members"]]
        # 专家名走 agents.decorate（人设名），不是流程名
        self.assertIn("小说家", names)
        self.assertTrue(all(m.get("role") for m in snap["members"]))
        self.assertIn("captured_at", snap)

    def test_context_block_lists_roles(self):
        block = expert_teams.context_block(
            expert_teams.snapshot(expert_teams.get_team("team-code")))
        self.assertIn("专家团分工", block)
        self.assertIn("主责", block)
        self.assertIn("复盘", block)
        self.assertIn("协作", block)
        # 单成员不放团队块（团才有意义）
        self.assertEqual("", expert_teams.context_block({"name": "x", "members": [{"flow_id": "code"}]}))
        self.assertEqual("", expert_teams.context_block(None))


class ValidateAndStoreTests(unittest.TestCase):
    def setUp(self):
        _tmp_dir(self)

    def tearDown(self):
        _restore(self)

    def test_rejects_bad_input(self):
        bad = [
            {"name": "", "members": ["code", "article"]},
            {"name": "只有一位", "members": ["code"]},
            {"name": "重复", "members": ["code", "code"]},
            {"name": "未知流程", "members": ["code", "nope_flow"]},
            {"name": "七位", "members": ["code"] * 7},
        ]
        for payload in bad:
            with self.assertRaises(ValueError, msg=str(payload)):
                expert_teams.save_team(payload)

    def test_builtin_cannot_be_overwritten_or_deleted(self):
        with self.assertRaises(ValueError):
            expert_teams.save_team({"id": "team-code", "name": "改内置", "members": ["code", "article"]})
        with self.assertRaises(ValueError):
            expert_teams.delete_team("team-code")

    def test_custom_team_roundtrip(self):
        saved = expert_teams.save_team({
            "name": "我的两人组",
            "desc": "先写后改",
            "members": [{"flow_id": "article", "role": "主笔"}, {"flow_id": "doc", "role": "校对"}],
        })
        self.assertTrue(saved["id"].startswith("team-user-"))
        ids = [t["id"] for t in expert_teams.list_teams()]
        self.assertIn(saved["id"], ids)
        # 自定义团排在内置团之后
        self.assertGreater(ids.index(saved["id"]), ids.index("team-research"))
        self.assertIsNotNone(expert_teams.get_team(saved["id"]))
        expert_teams.delete_team(saved["id"])
        self.assertIsNone(expert_teams.get_team(saved["id"]))

    def test_task_pins_team_snapshot(self):
        root = Path(self.tmp.name)
        task = store.create_task({
            "type": "novel", "goal": "写一个长篇小说", "workdir": str(root),
            "team_id": "team-long-fiction",
        })
        self.assertEqual("team-long-fiction", task["team_id"])
        snap = task["team_snapshot"]
        self.assertEqual("novel", snap["primary_flow"])
        self.assertEqual(3, len(snap["members"]))

    def test_task_rejects_team_type_mismatch(self):
        root = Path(self.tmp.name)
        with self.assertRaises(ValueError):
            store.create_task({
                "type": "code", "goal": "修个 bug", "workdir": str(root),
                "team_id": "team-long-fiction",
            })
        with self.assertRaises(ValueError):
            store.create_task({
                "type": "novel", "goal": "写个小说", "workdir": str(root),
                "team_id": "team-not-exist",
            })

    def test_task_without_team_is_unchanged(self):
        root = Path(self.tmp.name)
        task = store.create_task({"type": "code", "goal": "修个 bug", "workdir": str(root)})
        self.assertNotIn("team_snapshot", task)
        self.assertNotIn("team_id", task)


if __name__ == "__main__":
    unittest.main()
