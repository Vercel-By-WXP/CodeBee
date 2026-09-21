# -*- coding: utf-8 -*-
"""桌面蜜蜂（app/pet.py）：状态推导纯逻辑 + pet 设置往返 + /api/pet_state 端点形状。

蜜蜂进程的 Tk 渲染没法在无头测试里验，这里只锁三件可机验的事：
差分状态机对不对、设置键存取和校验对不对、喂食端点字段和计数对不对。
"""
from __future__ import annotations

import sys
import threading
import time
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

    def test_vanished_task_is_not_treated_as_success(self):
        # 快照短暂缺行或任务被删除时，没有明确 done 就不能误报完工。
        st, info = pet.derive_state({"t-gone"}, [])
        self.assertEqual(st, "sleep")
        self.assertEqual(info["good"], [])

    def test_timeout_counts_as_bad_and_unknown_is_ignored(self):
        st, info = pet.derive_state({"t-timeout"}, [_task("t-timeout", "timeout")])
        self.assertEqual(st, "alert")
        self.assertEqual([x["id"] for x in info["bad"]], ["t-timeout"])
        st2, info2 = pet.derive_state({"t-odd"}, [_task("t-odd", "mystery")])
        self.assertEqual(st2, "sleep")
        self.assertEqual(info2["good"], [])

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
        dirty = pet.parse_snapshot({"settings": "bad", "workers": [], "digest": "bad",
                                    "tasks": [{"id": "x", "steps_done": "bad",
                                               "steps_total": -3}]})
        self.assertEqual(dirty["workers"], {"running": 0, "queued": 0})
        self.assertEqual(dirty["digest"]["unseen"], 0)
        self.assertEqual(dirty["tasks"][0]["steps_done"], 0)
        self.assertEqual(dirty["tasks"][0]["steps_total"], 0)

    def test_tooltip_lines_active_first_capped(self):
        tasks = [_task("t-%d" % i, "running", done=i, total=10, cur="写第%d章" % i)
                 for i in range(10)]
        rows = pet.tooltip_lines(pet.parse_snapshot({"tasks": tasks}))
        self.assertEqual(len(rows), 6)   # 清单上限 6 行，防撑成一堵墙
        self.assertTrue(rows[0].startswith("● t-0"))
        self.assertIn("写第0章", rows[0])
        self.assertIn("0/10", rows[0])

    def test_tooltip_lines_only_active_and_queued(self):
        # 历史失败项曾把清单刷成一堵墙（用户实测十几条 ✘）——失败由警示
        # 气泡点名，悬停清单只列进行中/排队
        tasks = [_task("t-a", "running", done=1, total=2, cur="写稿"),
                 _task("t-b", "queued"),
                 _task("t-c", "failed"),
                 _task("t-d", "done")]
        rows = pet.tooltip_lines(pet.parse_snapshot({"tasks": tasks}))
        self.assertTrue(rows[0].startswith("● t-a"))
        self.assertTrue(rows[1].startswith("○ t-b"))
        self.assertIn(pet.LANG["zh"]["queued"], rows[1])
        self.assertEqual(len(rows), 2)   # 失败/完成都不进清单

    def test_tooltip_lines_truncate_long_title(self):
        t = _task("t-x", "running", cur="写稿")
        t["title"] = "很长的任务标题很长很长很长很长很长很长很长很长"
        rows = pet.tooltip_lines(pet.parse_snapshot({"tasks": [t]}))
        self.assertTrue(rows[0].startswith("● 很长的任务标题"))
        self.assertIn("…", rows[0])

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

    def test_pet_skin_roundtrip_and_validation(self):
        view, err = settings.save({"pet_skin": "robot"})
        self.assertIsNone(err)
        self.assertEqual(view["pet_skin"], "robot")
        view = settings.load()
        self.assertEqual(view["pet_skin"], "robot")
        cur, err = settings.save({"pet_skin": "bogus"})
        self.assertIsNotNone(err)
        self.assertEqual(cur["pet_skin"], "robot")


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
                         {"pet_enabled": True, "pet_mode": "tasks_only",
                          "pet_skin": "plush"})   # 未配置时兜底默认形象
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
        # 设置缺省也兜底为开+常驻+默认形象
        self.assertEqual(payload["settings"],
                         {"pet_enabled": True, "pet_mode": "always",
                          "pet_skin": "plush"})


class TestPetHttp(unittest.TestCase):
    def test_user_close_disables_pet_and_destroys_window(self):
        app = object.__new__(pet.PetApp)
        app._post_settings = mock.Mock()
        app._save_cfg = mock.Mock()
        app.root = mock.Mock()
        app.root.winfo_x.return_value = 10
        app.root.winfo_y.return_value = 20
        with mock.patch.object(pet, "global_lock_path",
                               return_value=Path("missing-pet.lock")):
            app._bye(write_setting=True)
        app._post_settings.assert_called_once_with({"pet_enabled": False})
        app.root.destroy.assert_called_once_with()

    def test_drag_uses_screen_delta_and_coalesced_geometry(self):
        app = object.__new__(pet.PetApp)
        app._press = None
        app._moved = False
        app._drag_to = None
        app._drag_pending = False
        app.root = mock.Mock()
        app.root.winfo_x.return_value = 100
        app.root.winfo_y.return_value = 200
        app.root.after.side_effect = lambda _delay, callback: callback()
        app._on_press(mock.Mock(x_root=10, y_root=20))
        app._on_motion(mock.Mock(x_root=35, y_root=50))
        app.root.after.assert_called_once()
        app.root.geometry.assert_called_once_with("+125+230")
        self.assertTrue(app._moved)

    def test_non_2xx_response_raises(self):
        response = mock.Mock(status=500)
        response.read.return_value = b'{"ok": false}'
        conn = mock.Mock()
        conn.getresponse.return_value = response
        with mock.patch.object(pet.http.client, "HTTPConnection", return_value=conn):
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                pet._http_json(8765, "/api/pet_state")
        conn.close.assert_called_once()

    def test_settings_writer_is_serial_and_latest_value_wins(self):
        app = object.__new__(pet.PetApp)
        app.port = 8765
        app._settings_lock = threading.Lock()
        app._settings_pending = {}
        app._settings_desired = {}
        app._settings_confirmed = {}
        app._settings_seq = 0
        app._settings_worker_running = False
        first_started = threading.Event()
        release_first = threading.Event()
        second_done = threading.Event()
        calls = []

        def fake_http(_port, _path, body=None):
            calls.append(dict(body or {}))
            if len(calls) == 1:
                first_started.set()
                release_first.wait(2)
            else:
                second_done.set()
            return {"ok": True}

        with mock.patch.object(pet, "_http_json", side_effect=fake_http):
            app._post_settings({"pet_mode": "always"})
            self.assertTrue(first_started.wait(1))
            app._post_settings({"pet_mode": "tasks_only"})
            release_first.set()
            self.assertTrue(second_done.wait(2))

        self.assertEqual(calls, [{"pet_mode": "always"},
                                 {"pet_mode": "tasks_only"}])
        deadline = time.time() + 1
        while app._settings_worker_running and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(app._settings_worker_running)
        # 写入确认前发出的旧轮询不能把最后一次点击覆盖掉。
        stale = pet.parse_snapshot({"settings": {"pet_mode": "always"}})
        app._reconcile_desired_settings(stale, {})
        self.assertEqual(stale["settings"]["pet_mode"], "tasks_only")
        # 写入确认后新发起的轮询恢复服务端权威，允许网页设置页再次改值。
        external = pet.parse_snapshot({"settings": {"pet_mode": "always"}})
        app._reconcile_desired_settings(external, dict(app._settings_confirmed))
        self.assertEqual(external["settings"]["pet_mode"], "always")
        self.assertEqual(app._settings_desired, {})


if __name__ == "__main__":
    unittest.main()
