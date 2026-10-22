# -*- coding: utf-8 -*-
"""流程席位（Raven 四席借鉴·轻量落位）单测。

覆盖：norm_seats 规范化（bool-only/未知键丢弃/空席位 None）、_EDITABLE 与
digest 纳入（席位变化=语义漂移可见）、任务创建固化与 payload 覆盖、
pipeline 三席接线（aiflavor 三态/global_directives 跳过/fast_path 强制与
禁用）、缺省零行为变化（回归红线）。全部走临时数据目录。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import aiflavor, flows, paths, pipeline, pipeline_code, store


def _seats_dir(case):
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


class NormSeatsTests(unittest.TestCase):
    def test_only_known_bool_keys_survive(self):
        seats = flows.norm_seats({
            "action": {"aiflavor": False, "bogus": True},
            "memory": {"global_directives": False},
            "planning": {"fast_path": True},
            "ghost": {"anything": True},
        })
        self.assertEqual(seats, {"action": {"aiflavor": False},
                                 "memory": {"global_directives": False},
                                 "planning": {"fast_path": True}})

    def test_non_bool_values_dropped_and_empty_returns_none(self):
        self.assertIsNone(flows.norm_seats({"action": {"aiflavor": "yes"}}))
        self.assertIsNone(flows.norm_seats({"action": {}}))
        self.assertIsNone(flows.norm_seats("junk"))
        self.assertIsNone(flows.norm_seats(None))


class FlowSeatsTests(unittest.TestCase):
    def setUp(self):
        _seats_dir(self)

    def tearDown(self):
        _restore(self)

    def test_builtin_override_applies_and_digests(self):
        base = flows.get_flow("novel")
        before = flows.flow_digest(base)
        flow, err = flows.upsert_flow({
            "id": "novel", "name": base["name"],
            "seats": {"action": {"aiflavor": True}, "ghost": {"x": True}},
        })
        self.assertIsNone(err)
        self.assertEqual(flow["seats"], {"action": {"aiflavor": True}})
        self.assertNotEqual(flows.flow_digest(flow), before)   # 席位进 digest
        # 清空席位（恢复默认语义）
        flow, err = flows.upsert_flow({"id": "novel", "name": base["name"],
                                       "seats": {}})
        self.assertIsNone(err)
        self.assertNotIn("seats", flow)

    def test_create_task_fixates_seats_and_payload_overrides(self):
        work = paths.DATA_DIR / "ws"
        work.mkdir()
        flows.upsert_flow({"id": "novel", "name": "小说",
                           "seats": {"memory": {"global_directives": False}}})
        task = store.create_task({"type": "novel", "goal": "写一章",
                                  "workdir": str(work)})
        self.assertEqual(task["seats"], {"memory": {"global_directives": False}})
        # payload 显式覆盖流程默认
        task2 = store.create_task({"type": "novel", "goal": "再写一章",
                                   "workdir": str(work),
                                   "seats": {"action": {"aiflavor": False}}})
        self.assertEqual(task2["seats"], {"action": {"aiflavor": False}})
        # 清掉流程覆写后：无席位流程不落键（回归红线：老行为零变化）
        flows.upsert_flow({"id": "novel", "name": "小说", "seats": {}})
        task3 = store.create_task({"type": "novel", "goal": "第三章",
                                   "workdir": str(work)})
        self.assertNotIn("seats", task3)


class PipelineSeatWiringTests(unittest.TestCase):
    """直接打桩测三个席位的分支语义，不跑真管线。"""

    def test_aiflavor_three_states(self):
        # ≥400 字单段长文：套话词命中措辞层；超长段命中节奏层（仅叙事档下发）
        text = "他心中一动，空气仿佛凝固。她眸中闪过一丝惊讶，缓缓转身走向窗前。" * 15
        tpl = "前言\n\n## 待评审稿件\n%s" % text
        with mock.patch.object(pipeline.skills, "global_directives_block",
                               return_value=""):
            # 缺省（email）：措辞层命中注入，节奏层不下发（现状）
            auto = pipeline._aiflavor_inject({"type": "email"}, tpl, text)
            self.assertIn("[AI味检测]", auto)
            self.assertNotIn("[节奏检测]", auto)
            # 强制（email → 叙事档）：节奏层也下发
            forced = pipeline._aiflavor_inject(
                {"type": "email", "seats": {"action": {"aiflavor": True}}}, tpl, text)
            self.assertIn("[AI味检测]", forced)
            self.assertIn("[节奏检测]", forced)
            # 关闭：叙事文体也一字不注入
            off = pipeline._aiflavor_inject(
                {"type": "novel", "seats": {"action": {"aiflavor": False}}}, tpl, text)
            self.assertEqual(off, tpl)
            # 叙事文体 + 缺省：两层都在（现状保持）
            nar = pipeline._aiflavor_inject({"type": "novel"}, tpl, text)
            self.assertIn("[AI味检测]", nar)
            self.assertIn("[节奏检测]", nar)

    def test_global_directives_seat_skips(self):
        with mock.patch.object(pipeline.skills, "global_directives_block",
                               return_value="## 全局指令\n要点") as blk:
            self.assertEqual(pipeline._global_directives({}), "## 全局指令\n要点")
            self.assertEqual(
                pipeline._global_directives(
                    {"seats": {"memory": {"global_directives": False}}}), "")
            self.assertEqual(blk.call_count, 1)   # 关闭时连读都不读

    def test_fast_path_seat_forces_and_disables(self):
        fp = pipeline_code._fast_path_flag
        # 附件在场 → 启发式不快；席位强制 → 快
        self.assertFalse(fp({"attachments": ["a.csv"]}, "auto", "easy", 0))
        self.assertTrue(fp({"attachments": ["a.csv"],
                            "seats": {"planning": {"fast_path": True}}},
                           "auto", "easy", 0))
        # plan_mode 用户显式 > 席位强制
        self.assertFalse(fp({"plan_mode": True,
                             "seats": {"planning": {"fast_path": True}}},
                            "auto", "easy", 0))
        # 简单任务启发式快；席位禁用 → 不快
        self.assertTrue(fp({}, "auto", "easy", 0))
        self.assertFalse(fp({"seats": {"planning": {"fast_path": False}}},
                            "auto", "easy", 0))


if __name__ == "__main__":
    unittest.main()
