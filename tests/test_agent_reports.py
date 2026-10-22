# -*- coding: utf-8 -*-
"""Agent 反向通道（2026-10-22 multica 落地）回归：汇报存取/解除链 + 标记解析
+ attention 投影 + 三个写入面（HTTP 由路由片段覆盖、MCP stdio、内置工具）+
看板页静态接线守卫。

隔离方式：全程 mock.patch 换掉 core.store（_TASKS/_RUNS 是模块级缓存，
进程内换数据目录会互相污染）；通知打桩防真实推送。
"""
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1] / "app"
REPO = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

# mock.patch 需要目标属性已存在：先触发子模块加载（只定义函数，无落盘副作用）
from core import notify as _notify_mod  # noqa: F401,E402
from core import store as _store_mod  # noqa: F401,E402


class _FakePaths:
    RUNS_DIR = Path("./runs-fake")


class _FakeStore:
    """只覆盖 store_reports / store_messages 走到的面。"""

    TERMINAL_STATUSES = ("done", "failed", "cancelled", "timeout")

    def __init__(self, runs=None):
        self.LOCK = threading.RLock()
        self.time = time
        self.paths = _FakePaths()
        self.saved = {}
        self.bumped = 0
        self._RUNS = {rid: dict(r) for rid, r in (runs or {}).items()}

    def bump_state(self):
        self.bumped += 1

    def _save_json(self, path, obj):
        self.saved[str(path)] = json.dumps(obj, ensure_ascii=False)

    def get_run(self, run_id):
        return self._RUNS.get(run_id)

    def _ensure_messages(self, run):
        if not isinstance(run.get("messages"), list):
            run["messages"] = []
        return run["messages"]

    def get_task(self, task_id):
        return None

    def latest_run_by_task(self):
        return {}


def _running_run(run_id="r1", task_id="t1"):
    return {"id": run_id, "task_id": task_id, "status": "running",
            "title": "反向通道验证", "messages": []}


class StoreReportsTests(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore({"r1": _running_run()})
        patcher = mock.patch("core.store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blocker_persists_and_notifies(self):
        from core import store_reports
        with mock.patch("core.notify.push_text") as push:
            rec = store_reports.add_report("r1", "blocker", "需要确认数据库口令")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["kind"], "blocker")
        self.assertFalse(rec["resolved"])
        push.assert_called_once()
        self.assertIn("反向通道验证", push.call_args[0][0])
        run = self.store._RUNS["r1"]
        self.assertEqual(len(store_reports.open_blockers(run)), 1)
        saved_key = list(self.store.saved)[0].replace("\\", "/")
        self.assertTrue(saved_key.endswith("runs-fake/r1/run.json"), saved_key)
        self.assertEqual(self.store.bumped, 1)   # 汇报落库必须 bump：SSE/看板即时感知

    def test_progress_no_notify(self):
        from core import store_reports
        with mock.patch("core.notify.push_text") as push:
            rec = store_reports.add_report("r1", "progress", "已完成 60%")
        self.assertIsNotNone(rec)
        push.assert_not_called()
        self.assertEqual(store_reports.open_blockers(self.store._RUNS["r1"]), [])

    def test_bad_kind_or_empty_text_rejected(self):
        from core import store_reports
        self.assertIsNone(store_reports.add_report("r1", "panic", "x"))
        self.assertIsNone(store_reports.add_report("r1", "blocker", "  "))
        self.assertIsNone(store_reports.add_report("ghost", "blocker", "x"))
        # resolve 允许空正文：解除本身就是完整信息
        self.assertIsNotNone(store_reports.add_report("r1", "resolve", ""))

    def test_user_reply_resolves_blocker(self):
        from core import store_messages, store_reports
        store_reports.add_report("r1", "blocker", "缺第三方 API key")
        msg = store_messages.add_message("r1", "用 env 里的 TEST_KEY")
        self.assertIsNotNone(msg)
        run = self.store._RUNS["r1"]
        self.assertEqual(store_reports.open_blockers(run), [])
        rep = run["agent_reports"][0]
        self.assertTrue(rep["resolved"])
        self.assertTrue(str(rep["resolved_by"]).startswith("user:"))

    def test_agent_self_resolve(self):
        from core import store_reports
        store_reports.add_report("r1", "blocker", "等文件锁")
        rec = store_reports.add_report("r1", "resolve", "锁已释放")
        self.assertEqual(rec.get("resolved_count"), 1)
        rep = self.store._RUNS["r1"]["agent_reports"][0]
        self.assertTrue(rep["resolved"])
        self.assertEqual(rep["resolved_by"], "agent")

    def test_terminal_run_blockers_moot(self):
        from core import store_reports
        store_reports.add_report("r1", "blocker", "悬而未决")
        run = self.store._RUNS["r1"]
        run["status"] = "failed"
        # 终态下 open_blockers 视为全部失效（结果本身已是答案）
        self.assertEqual(store_reports.open_blockers(run), [])

    def test_list_reports_readonly(self):
        from core import store_reports
        store_reports.add_report("r1", "blocker", "b1")
        rows = store_reports.list_reports("r1")
        self.assertEqual(len(rows), 1)
        rows[0]["text"] = "篡改"
        self.assertEqual(self.store._RUNS["r1"]["agent_reports"][0]["text"], "b1")


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore({"r1": _running_run()})
        patcher = mock.patch("core.store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ingest_and_strip(self):
        from core import store_reports
        text = ("前言正常。\n"
                "[[CODEBEE:BLOCKER]] 等待用户确认删除范围\n"
                "中段说明。\n"
                "[[CODEBEE:PROGRESS]] 已备份 3 个文件\n")
        with mock.patch("core.notify.push_text"):
            recs = store_reports.ingest_markers("r1", text, step=2)
        self.assertEqual([r["kind"] for r in recs], ["blocker", "progress"])
        self.assertEqual(recs[0]["text"], "等待用户确认删除范围")
        self.assertEqual(recs[0]["source"], "marker")
        self.assertEqual(recs[0]["step"], 2)
        stripped = store_reports.strip_markers(text)
        self.assertNotIn("[[CODEBEE:", stripped)
        self.assertIn("前言正常。", stripped)
        self.assertIn("中段说明。", stripped)

    def test_lowercase_marker_ignored(self):
        # 只认大写全形键：正常文本里的小写撞形不得误触发
        from core import store_reports
        self.assertEqual(store_reports.ingest_markers("r1", "[[codebee:blocker]] 误触"), [])
        self.assertEqual(
            store_reports.ingest_markers("r1", "提到 [[CODEBEE:BLOCKER]] 在句中不触发"), [])
        self.assertEqual(
            len(store_reports.ingest_markers(
                "r1", "x [[CODEBEE:BLOCKER]] 句中不触发\n[[CODEBEE:BLOCKER]] 行首才触发")), 1)


class AttentionTests(unittest.TestCase):
    def test_blocker_surfaces_in_task_attention(self):
        from core import activity
        run = _running_run()
        run["agent_reports"] = [
            {"id": "000001", "kind": "blocker", "text": "需要拍板：是否清空重建",
             "resolved": False, "at": "2026-10-22 10:00:00", "source": "mcp"}]
        tasks = [{"id": "t1", "title": "重建"}]
        with mock.patch.object(activity.contracts, "get", return_value={}), \
                mock.patch("core.store", _FakeStore()):
            attn = activity.task_attention(tasks, {"t1": run})
        self.assertIn("blocker", attn.get("t1", {}))
        self.assertEqual(attn["t1"]["blocker"]["count"], 1)
        self.assertIn("拍板", attn["t1"]["blocker"]["text"])


class WriteSurfaceTests(unittest.TestCase):
    """三个写入面收敛到同一条 add_report 链。"""

    def setUp(self):
        self.store = _FakeStore({"r1": _running_run()})
        patcher = mock.patch("core.store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mcp_stdio_tool(self):
        import mcp_server
        with mock.patch("core.notify.push_text"):
            out = mcp_server._tool_report_status(
                {"run_id": "r1", "kind": "blocker", "text": "权限不足需人工"})
        self.assertFalse(out.get("isError"), out)
        self.assertEqual(self.store._RUNS["r1"]["agent_reports"][0]["source"], "mcp")

    def test_mcp_stdio_tool_resolves_task_to_latest_run(self):
        import mcp_server
        store = self.store
        store._RUNS["r2"] = {"id": "r2", "task_id": "t9", "status": "running"}
        store.get_task = lambda tid: ({"id": "t9", "title": "T9"} if tid == "t9" else None)
        store.latest_run_by_task = lambda: {"t9": store._RUNS["r2"]}
        with mock.patch("core.notify.push_text"):
            out = mcp_server._tool_report_status(
                {"task_id": "t9", "kind": "progress", "text": "过半"})
        self.assertFalse(out.get("isError"), out)
        self.assertEqual(store._RUNS["r2"]["agent_reports"][0]["kind"], "progress")

    def test_mcp_stdio_tool_rejects_bad_args(self):
        import mcp_server
        out = mcp_server._tool_report_status({"kind": "blocker", "text": "x"})
        self.assertTrue(out.get("isError"))

    def test_builtin_tool_uses_run_binding(self):
        from core import builtin_agent_tools
        with mock.patch("core.notify.push_text"):
            out = builtin_agent_tools._tool_report_status(
                {"kind": "blocker", "text": "需要确认口径"}, checkpoint_run_id="r1")
        self.assertIn("已汇报", out)
        self.assertEqual(self.store._RUNS["r1"]["agent_reports"][0]["source"], "builtin")

    def test_builtin_tool_action_policy_green(self):
        from core import action_policy
        self.assertEqual(action_policy.spec_for("report_status").zone, "green")
        self.assertEqual(action_policy.preflight("report_status", args={}).get("decision"),
                         "autoExecute")

    def test_mcp_harness_tool_registered(self):
        from core import mcp_harness
        self.assertIn("report_status", mcp_harness._TOOLS)


class KanbanStaticGuardTests(unittest.TestCase):
    """看板页接线守卫：入口按钮/子页/渲染函数三件不被顺手删掉。"""

    def test_index_html_wiring(self):
        html = (REPO / "app" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btn-q-kanban"', html)
        self.assertIn('data-page="kanban"', html)
        self.assertIn('id="sub-kanban"', html)
        self.assertIn('id="kanban-board"', html)

    def test_app_js_wiring(self):
        js = (REPO / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderKanban", js)
        self.assertIn('name === "kanban"', js)
        self.assertIn('S.tab === "kanban"', js)

    def test_i18n_keys_present(self):
        i18n = (REPO / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")
        for key in ("看板", "待办", "已完结", "阻塞", "⚠ 阻塞汇报", "进展汇报", "智能体汇报"):
            self.assertIn('"%s":' % key, i18n)


if __name__ == "__main__":
    unittest.main()
