# -*- coding: utf-8 -*-
"""形态注册表与专家表（agents）单测。

要点：全部在临时目录里跑，不读也不写用户真实数据目录，
否则「本地有一份 data/profiles/x.json」就会让测试结果随机变化。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core import agents, profile


class _TempData(unittest.TestCase):
    """把 profile/agents 的数据目录重定向到临时目录。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self._saved_env = os.environ.pop(profile.ENV_VAR, None)
        self._saved = (profile.spec_dir, profile.state_file,
                       agents._agents_dir, profile._REG_CACHE.copy(),
                       agents._CACHE.copy())
        profile.spec_dir = lambda: self.root / "profiles"
        profile.state_file = lambda: self.root / "profile_state.json"
        agents._agents_dir = lambda: self.root / "agents"
        profile._REG_CACHE.update({"sig": None, "data": None})
        agents._CACHE.update({"sig": None, "data": None})
        profile.reset()

    def tearDown(self):
        (profile.spec_dir, profile.state_file,
         agents._agents_dir, reg, cache) = self._saved
        profile._REG_CACHE.update(reg)
        agents._CACHE.update(cache)
        profile.reset()
        os.environ.pop(profile.ENV_VAR, None)
        if self._saved_env is not None:
            os.environ[profile.ENV_VAR] = self._saved_env
        self._dir.cleanup()

    def write_spec(self, name, payload):
        d = self.root / "profiles"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % name)).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class RegistryTest(_TempData):
    def test_builtin_profiles_present(self):
        self.assertIn("default", profile.names())
        self.assertIn("novel", profile.names())
        self.assertIn("hive", profile.names())

    def test_user_spec_is_discovered(self):
        self.write_spec("bid", {"name": "标书版", "flows": ["bid_doc", "doc"]})
        spec = profile.spec("bid")
        self.assertEqual(spec["name"], "标书版")
        self.assertEqual(spec["flows"], ["bid_doc", "doc"])
        self.assertFalse(spec["builtin"])

    def test_user_spec_can_override_builtin(self):
        self.write_spec("novel", {"name": "我的小说版", "flows": ["short_story"]})
        self.assertEqual(profile.spec("novel")["name"], "我的小说版")
        self.assertEqual(profile.spec("novel")["flows"], ["short_story"])

    def test_unknown_flow_is_dropped_not_fatal(self):
        self.write_spec("x", {"flows": ["novel", "no_such_flow"]})
        self.assertEqual(profile.spec("x")["flows"], ["novel"])

    def test_broken_json_is_skipped(self):
        d = self.root / "profiles"
        d.mkdir(parents=True, exist_ok=True)
        (d / "broken.json").write_text("{not json", encoding="utf-8")
        self.assertNotIn("broken", profile.names())
        self.assertIn("novel", profile.names())  # 坏文件不影响其它形态

    def test_extends_inherits_unknown_fields_from_base(self):
        self.write_spec("mynovel", {"extends": "novel", "name": "我的小说线",
                                    "flows": ["short_story"]})
        spec = profile.spec("mynovel")
        self.assertEqual(spec["name"], "我的小说线")
        self.assertEqual(spec["flows"], ["short_story"])
        # features 从 novel 继承：代码任务入口仍是关的
        self.assertFalse(spec["features"]["code_tasks"])

    def test_missing_ui_falls_back_to_core_ui(self):
        self.write_spec("ghost", {"ui": "nope/index.html"})
        self.assertEqual(profile.spec("ghost")["ui"], "")


class SelectTest(_TempData):
    def test_explicit_known_profile(self):
        name, why = profile.select("novel", allow_prompt=False)
        self.assertEqual((name, why), ("novel", "cli"))
        # v2 的独立页面（novel/index.html 等）已整体删除，统一回落到同一个工作台，
        # 所以这里不再有「形态专属入口页」——空串就是「走主工作台」。
        self.assertEqual(profile.ui_entry(), "")

    def test_explicit_unknown_profile_raises(self):
        with self.assertRaises(profile.ProfileError) as ctx:
            profile.select("nope", allow_prompt=False)
        self.assertIn("novel", str(ctx.exception))  # 报错里要列出可选

    def test_env_is_lenient(self):
        os.environ[profile.ENV_VAR] = "nope-not-real"
        name, why = profile.select("", allow_prompt=False)
        self.assertEqual(name, profile.DEFAULT)
        self.assertEqual(why, "default")

    def test_last_choice_is_reused(self):
        profile.remember("novel")
        self.assertEqual(profile.last_choice(), "novel")
        name, why = profile.select("", allow_prompt=False)
        self.assertEqual((name, why), ("novel", "last"))

    def test_stale_last_choice_is_ignored(self):
        (self.root / "profile_state.json").write_text(
            json.dumps({"last": "deleted-profile"}), encoding="utf-8")
        self.assertEqual(profile.last_choice(), "")
        self.assertEqual(profile.select("", allow_prompt=False)[0], profile.DEFAULT)


class ScaffoldTest(_TempData):
    def test_scaffold_writes_loadable_spec(self):
        path = profile.scaffold("bid", name="标书版", flows=["bid_doc", "doc"],
                                tagline="少一个废标项")
        self.assertTrue(path.is_file())
        spec = profile.spec("bid")
        self.assertEqual(spec["name"], "标书版")
        self.assertEqual(spec["flows"], ["bid_doc", "doc"])
        # 新形态立即可用：不重启、不清缓存
        self.assertEqual(profile.set_active("bid"), "bid")

    def test_scaffold_rejects_duplicate(self):
        with self.assertRaises(profile.ProfileError):
            profile.scaffold("novel")

    def test_scaffold_rejects_unknown_flow(self):
        with self.assertRaises(profile.ProfileError):
            profile.scaffold("bad", flows=["no_such_flow"])

    def test_scaffold_rejects_bad_id(self):
        with self.assertRaises(profile.ProfileError):
            profile.scaffold("has space")


class AgentsTest(_TempData):
    def test_every_flow_has_an_agent(self):
        from core import flows
        missing = [f["id"] for f in flows.list_flows() if not agents.get(f["id"])]
        self.assertEqual(missing, [], "这些流程没有登记专家：%s" % missing)

    def test_archetype_is_known(self):
        for spec in agents.registry().values():
            self.assertIn(spec["archetype"], agents.ARCHETYPES)

    def test_gates_are_readable_text(self):
        for spec in agents.registry().values():
            for gate in spec["gates"]:
                self.assertIsInstance(gate, str)
                self.assertTrue(gate.strip())

    def test_decorate_attaches_agent_without_mutating(self):
        rows = [{"id": "novel", "name": "小说"}]
        out = agents.decorate(rows)
        self.assertEqual(out[0]["agent"]["archetype"], "craft")
        self.assertNotIn("agent", rows[0])  # 入参不许被改

    def test_user_agent_override_wins(self):
        d = self.root / "agents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "novel.json").write_text(json.dumps({
            "name": "我的小说家", "archetype": "craft", "focus": "只看钩子",
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(agents.get("novel")["name"], "我的小说家")
        self.assertEqual(agents.get("novel")["focus"], "只看钩子")

    def test_unknown_archetype_falls_back_to_studio(self):
        d = self.root / "agents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "novel.json").write_text(json.dumps({"archetype": "nope"}),
                                      encoding="utf-8")
        self.assertEqual(agents.get("novel")["archetype"], "studio")


class HiddenTest(_TempData):
    """草稿与首发：不给参数就只看到首发名单，点名或 all 才放草稿。"""

    def flows_of(self, **kw):
        return [a["flow"] for a in agents.describe_all(**kw)["agents"]]

    def write_agent(self, flow, payload):
        d = self.root / "agents"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % flow)).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_default_view_is_launch_list(self):
        self.assertEqual(sorted(self.flows_of()), sorted(agents.LAUNCH))

    def test_include_hidden_returns_every_registered_agent(self):
        self.assertEqual(len(self.flows_of(include_hidden=True)),
                         len(agents.registry()))

    def test_only_promotes_a_draft_but_still_marks_it(self):
        rows = agents.describe_all(only=["resume"])["agents"]
        self.assertEqual([a["flow"] for a in rows], ["resume"])
        self.assertTrue(rows[0]["hidden"], "点名放出来也要如实标记是草稿")

    def test_only_wins_over_hidden(self):
        self.assertEqual(self.flows_of(only=["resume", "novel"]),
                         ["novel", "resume"])

    def test_override_inherits_builtin_draft_state(self):
        # 只改名字，不该顺手把草稿放出去
        self.write_agent("resume", {"name": "改个名的简历师"})
        self.assertTrue(agents.get("resume")["hidden"])
        self.assertNotIn("resume", self.flows_of())

    def test_override_can_promote_a_draft(self):
        self.write_agent("resume", {"hidden": False})
        self.assertIn("resume", self.flows_of())

    def test_override_can_demote_a_launch_agent(self):
        self.write_agent("novel", {"hidden": True})
        self.assertNotIn("novel", self.flows_of())
        self.assertIn("novel", self.flows_of(include_hidden=True))

    def test_brand_new_agent_is_visible(self):
        # 用户自己新建的专家：他明确写了配置，不该再被藏起来
        self.write_agent("my_flow", {"name": "我的专家", "archetype": "studio"})
        self.assertFalse(agents.get("my_flow")["hidden"])
        self.assertIn("my_flow", self.flows_of())

    def test_decorate_reports_hidden_for_frontend(self):
        rows = agents.decorate([{"id": "novel"}, {"id": "resume"}])
        self.assertFalse(rows[0]["agent"]["hidden"])
        self.assertTrue(rows[1]["agent"]["hidden"])


class AgentPolicyTest(_TempData):
    """形态怎么决定放哪些专家：flows 管能不能建，agents 管出不出现。"""

    def test_default_profile_has_no_pointing(self):
        policy = profile.agent_policy()
        self.assertIsNone(policy["only"])
        self.assertFalse(policy["drafts"])

    def test_novel_points_at_its_four(self):
        profile.set_active("novel")
        policy = profile.agent_policy()
        self.assertEqual(policy["only"],
                         ["novel", "short_story", "serial_novel", "rank_scan"])
        self.assertFalse(policy["drafts"])

    def test_hive_shows_drafts(self):
        profile.set_active("hive")
        self.assertTrue(profile.agent_policy()["drafts"])

    def test_user_spec_can_point_at_agents(self):
        self.write_spec("bid", {"flows": ["bid_doc"], "agents": ["bid_doc", "doc"]})
        profile.set_active("bid")
        self.assertEqual(profile.agent_policy()["only"], ["bid_doc", "doc"])

    def test_unknown_agent_id_is_dropped(self):
        self.write_spec("x", {"agents": ["resume", "no_such_agent"]})
        self.assertEqual(profile.spec("x")["agents"], ["resume"])

    def test_agents_must_be_a_list(self):
        self.write_spec("x", {"agents": "resume"})
        self.assertIsNone(profile.spec("x")["agents"])

    def test_describe_exposes_agent_policy(self):
        profile.set_active("novel")
        payload = profile.describe()
        self.assertEqual(payload["agents"],
                         ["novel", "short_story", "serial_novel", "rank_scan"])
        self.assertFalse(payload["drafts"])

    def test_scaffold_names_the_experts_it_picks(self):
        # 新建版本不该给用户一个空荡荡的专家栏（草稿也点名放开）
        profile.scaffold("bid2", flows=["bid_doc", "doc"])
        self.assertEqual(profile.spec("bid2")["agents"], ["bid_doc", "doc"])
        profile.set_active("bid2")
        self.assertEqual(profile.agent_policy()["only"], ["bid_doc", "doc"])


if __name__ == "__main__":
    unittest.main()
