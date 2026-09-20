# -*- coding: utf-8 -*-
"""桌面蜜蜂（app/pet.py）：状态推导纯逻辑 + pet 设置往返 + /api/pet_state 端点形状。

蜜蜂进程的 Tk 渲染没法在无头测试里验，这里只锁三件可机验的事：
差分状态机对不对、设置键存取和校验对不对、喂食端点字段和计数对不对。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from base import BaseTest  # noqa: E402  （base 会把数据目录重定向到临时目录）

from app import pet  # noqa: E402  （顶层不 import tkinter，可安全导入）
from app.core import settings  # noqa: E402

APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
import main as main_module  # noqa: E402


def _task(tid, status, done=0, total=0, cur="", title=None):
    return {"id": tid, "title": title or tid, "type": "doc",
            "run_status": status, "steps_done": done, "steps_total": total,
            "step_current": cur, "error": ""}


class TestPetLogic(unittest.TestCase):
    """差分状态机：蜜蜂的表情全靠它，错了就会常驻庆祝或永远举警示牌。"""

    def test_empty_is_sleep(self):
        st, info = pet.derive_state(set(), [])
        self.assertEqual(st, "sleep")
        self.assertEqual(info["active"], [])

    def test_running_is_work_regardless_of_history(self):
        st, info = pet.derive_state({"t-old"}, [_task("t-1", "running")])
        self.assertEqual(st, "work")
        self.assertEqual(len(info["active"]), 1)

    def test_queued_counts_as_work(self):
        st, _ = pet.derive_state(set(), [_task("t-1", "queued")])
        self.assertEqual(st, "work")

    def test_finish_all_is_cheer(self):
        st, info = pet.derive_state({"t-1", "t-2"},
                                    [_task("t-1", "done"), _task("t-2", "done")])
        self.assertEqual(st, "cheer")
        self.assertEqual({t["id"] for t in info["good"]}, {"t-1", "t-2"})

    def test_any_failure_is_alert(self):
        st, info = pet.derive_state(
            {"t-1", "t-2"},
            [_task("t-1", "failed"), _task("t-2", "done")])
        self.assertEqual(st, "alert")
        self.assertEqual([t["id"] for t in info["bad"]], ["t-1"])
        self.assertEqual([t["id"] for t in info["good"]], ["t-2"])

    def test_cancelled_counts_as_bad(self):
        st, info = pet.derive_state({"t-1"}, [_task("t-1", "cancelled")])
        self.assertEqual(st, "alert")
        self.assertEqual(len(info["bad"]), 1)

    def test_vanished_task_treated_as_good(self):
        # 任务被删除（快照里没了）不应让蜜蜂永远举牌
        st, info = pet.derive_state({"t-gone"}, [])
        self.assertEqual(st, "cheer")
        self.assertEqual(info["good"][0]["id"], "t-gone")

    def test_ancient_failure_never_alerts(self):
        # 上轮就没在跑的失败任务与差分无关——三年前的失败不举牌
        st, _ = pet.derive_state(set(), [_task("t-1", "failed")])
        self.assertEqual(st, "sleep")

    def test_parse_snapshot_tolerates_junk(self):
        snap = pet.parse_snapshot(None)
        self.assertFalse(snap["settings"]["pet_enabled"] is False)  # 默认开
        self.assertEqual(snap["tasks"], [])
        snap = pet.parse_snapshot({"settings": {"pet_mode": 123},
                                   "workers": {"running": "2"},
                                   "tasks": [{"id": "a", "run_status": 1},
                                             "junk", 42]})
        self.assertEqual(snap["settings"]["pet_mode"], "123")
        self.assertEqual(snap["workers"]["running"], 2)
        self.assertEqual(len(snap["tasks"]), 1)
        self.assertEqual(snap["tasks"][0]["run_status"], "1")

    def test_tooltip_lines_active_first_capped(self):
        tasks = [_task("t-%d" % i, "running", done=i, total=10, cur="写第%d章" % i)
                 for i in range(10)]
        rows = pet.tooltip_lines(pet.parse_snapshot({"tasks": tasks}))
        self.assertEqual(len(rows), 8)
        self.assertTrue(rows[0].startswith("● t-0"))
        self.assertIn("写第0章", rows[0])
        self.assertIn("0/10", rows[0])

    def test_tooltip_lines_queued_between_running_and_failed(self):
        tasks = [_task("t-a", "running", done=1, total=2, cur="写稿"),
                 _task("t-b", "queued"),
                 _task("t-c", "failed")]
        rows = pet.tooltip_lines(pet.parse_snapshot({"tasks": tasks}))
        self.assertTrue(rows[0].startswith("● t-a"))
        self.assertTrue(rows[1].startswith("○ t-b"))
        self.assertIn(pet.LANG["zh"]["queued"], rows[1])
        self.assertTrue(rows[2].startswith("✘ t-c"))

    def test_tooltip_lines_empty_shows_all_clear(self):
        rows = pet.tooltip_lines(pet.parse_snapshot({}))
        self.assertEqual(rows, [pet.LANG["zh"]["all_clear"]])


class TestPetSettings(BaseTest):
    """pet_enabled / pet_mode 的默认值、往返与非法值拒绝。"""

    def test_defaults_on_and_always(self):
        view = settings.load()
        self.assertTrue(view["pet_enabled"])
        self.assertEqual(view["pet_mode"], "always")

    def test_roundtrip_and_validation(self):
        view, err = settings.save({"pet_enabled": False,
                                   "pet_mode": "tasks_only"})
        self.assertIsNone(err)
        self.assertFalse(view["pet_enabled"])
        self.assertEqual(view["pet_mode"], "tasks_only")
        view = settings.load()   # 落盘重读
        self.assertFalse(view["pet_enabled"])
        self.assertEqual(view["pet_mode"], "tasks_only")
        cur, err = settings.save({"pet_mode": "bogus"})
        self.assertIsNotNone(err)
        self.assertEqual(cur["pet_mode"], "tasks_only")   # 非法值不改现状


class TestPetEndpoint(unittest.TestCase):
    """/api/pet_state：字段形状、活跃计数、按 id 倒序裁剪。"""

    def _handler(self):
        handler = object.__new__(main_module.Handler)
        handler.responses = []
        handler._json = (lambda code, payload:
                         handler.responses.append((code, payload)) or payload)
        return handler

    def test_shape_and_counts(self):
        runs = {
            "t-1": {"id": "r-1", "status": "running", "error": "",
                    "steps": [{"status": "done"}, {"status": "done"},
                              {"status": "running", "summary": "起草第3章",
                               "agent_label": "写手"}]},
            "t-2": {"id": "r-2", "status": "queued", "error": "", "steps": []},
            "t-3": {"id": "r-3", "status": "failed", "error": "供应商超时",
                    "steps": []},
        }
        handler = self._handler()
        with mock.patch.object(main_module.store, "latest_run_by_task",
                               return_value=runs), \
             mock.patch.object(main_module.store, "get_task",
                               side_effect=lambda tid: {"title": "任务" + tid,
                                                        "type": "doc"}), \
             mock.patch.object(main_module.settings, "load",
                               return_value={"pet_enabled": True,
                                             "pet_mode": "tasks_only"}):
            handler._api_pet_state()
        code, payload = handler.responses[0]
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["port"], main_module.PORT)
        self.assertEqual(payload["settings"],
                         {"pet_enabled": True, "pet_mode": "tasks_only"})
        self.assertEqual(payload["workers"], {"running": 1, "queued": 1})
        ids = [t["id"] for t in payload["tasks"]]
        self.assertEqual(ids, ["t-3", "t-2", "t-1"])   # id 含时间戳，倒序=新在前
        running = payload["tasks"][2]
        self.assertEqual(running["title"], "任务t-1")
        self.assertEqual(running["steps_done"], 2)
        self.assertEqual(running["steps_total"], 3)
        self.assertEqual(running["step_current"], "起草第3章")
        self.assertEqual(payload["tasks"][0]["error"], "供应商超时")

    def test_missing_task_row_falls_back_to_id_title(self):
        runs = {"t-9": {"id": "r-9", "status": "done", "steps": []}}
        handler = self._handler()
        with mock.patch.object(main_module.store, "latest_run_by_task",
                               return_value=runs), \
             mock.patch.object(main_module.store, "get_task",
                               return_value=None), \
             mock.patch.object(main_module.settings, "load",
                               return_value={}):
            handler._api_pet_state()
        _, payload = handler.responses[0]
        self.assertEqual(payload["tasks"][0]["title"], "t-9")
        # 设置缺省也兜底为开+常驻
        self.assertEqual(payload["settings"],
                         {"pet_enabled": True, "pet_mode": "always"})


if __name__ == "__main__":
    unittest.main()
