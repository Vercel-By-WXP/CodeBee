# -*- coding: utf-8 -*-
"""注意力面板数据源（attention）回归。

核心语义（2026-11-01，借鉴 dsh-superagent「注意力守恒」）：
1. 只列需要人处理的事：失败 run（近 7 天）/ 暂停任务 / 滞留排队（唤醒过期 >5 分钟）；
2. 每条带标题、时间、原因与动作语义（actions）；
3. 按时间倒序；limit 截断；
4. 内部故障 → 空列表 + degraded=true（绝不抛错连累主流程）。
"""
from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

from core import attention, automation, store
from core.publish import stats as pub_stats


def _run(rid, status="failed", ended="2026-11-01T08:00:00", task_id="t1",
         error=" boom", **kw):
    r = {"id": rid, "status": status, "ended_at": ended, "task_id": task_id,
         "error": error}
    r.update(kw)
    return r


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        patcher = mock.patch.object(store, "LOCK", store.LOCK)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 新增的定时失联扫描会读 automation 存测：统一 mock 成空，
        # 存量用例才不依赖测试环境里的真实自动化数据
        ap = mock.patch.object(automation, "list_tasks", return_value=[])
        ap.start()
        self.addCleanup(ap.stop)
        # 发布成功率告警（第 7 来源）读真实发布台账：统一 mock 成不告警，
        # 存量计数断言才不依赖测试环境里的真实台账
        pp = mock.patch.object(pub_stats, "alert", return_value=None)
        pp.start()
        self.addCleanup(pp.stop)

    def test_failed_run_listed_with_reason_and_actions(self):
        runs = [_run("r1", error="评审未达标")]
        tasks = [{"id": "t1", "title": "书一", "status": "idle"}]
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=tasks):
            res = attention.collect(now=self.now)
        self.assertEqual(len(res["items"]), 1)
        it = res["items"][0]
        self.assertEqual(it["kind"], "failed_run")
        self.assertEqual(it["title"], "书一")
        self.assertEqual(it["reason"], "评审未达标")
        self.assertIn("retry", [a["type"] for a in it["actions"]])

    def test_old_failures_filtered_by_days(self):
        runs = [_run("r1", ended="2020-01-01T00:00:00")]
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=[]):
            res = attention.collect(now=self.now)
        self.assertEqual(res["items"], [])

    def test_paused_run_listed_with_run_id(self):
        # 暂停是 run 级标志（任务状态不变）：paused=True 且 run 仍 running
        runs = [_run("r-p", status="running", paused=True,
                     pause_requested_at="2026-11-01T08:00:00")]
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=[]):
            res = attention.collect(now=self.now)
        it = next(i for i in res["items"] if i["kind"] == "paused_task")
        self.assertEqual(it["run_id"], "r-p")
        self.assertIn("resume", [a["type"] for a in it["actions"]])

    def test_unpaused_or_terminal_runs_not_listed_as_paused(self):
        runs = [_run("r-ok", status="running", paused=False),
                _run("r-done", status="done", paused=True)]
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=[]):
            res = attention.collect(now=self.now)
        kinds = [i["kind"] for i in res["items"]]
        self.assertNotIn("paused_task", kinds)

    def test_stuck_queued_needs_expired_resume(self):
        stale = time.strftime("%Y-%m-%dT%H:%M:%S",
                              time.localtime(self.now - 3600))
        fresh = time.strftime("%Y-%m-%dT%H:%M:%S",
                              time.localtime(self.now - 30))
        runs = [_run("r-old", status="queued", resume_enqueue_at=stale),
                _run("r-new", status="queued", resume_enqueue_at=fresh)]
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=[]):
            res = attention.collect(now=self.now)
        self.assertEqual([i["id"] for i in res["items"]], ["r-old"])
        self.assertEqual(res["items"][0]["kind"], "stuck_queued")

    def test_sorted_desc_and_limit(self):
        # 日期必须相对 self.now 生成：原先写死 2026-10-01..05，而窗口是 now-30d，
        # 到 11-02 时 10-03 08:00 已跌出窗口，用例自己烂掉（只剩 2 条 → 假红）
        runs = [_run("r%d" % i,
                     ended=time.strftime("%Y-%m-%dT%H:%M:%S",
                                         time.localtime(self.now - (i + 1) * 86400)))
                for i in range(5)]
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=[]):
            res = attention.collect(days=30, limit=3, now=self.now)
        self.assertEqual(len(res["items"]), 3)
        epochs = [i["time_epoch"] for i in res["items"]]
        self.assertEqual(epochs, sorted(epochs, reverse=True))

    def test_internal_error_degrades_to_empty(self):
        with mock.patch.object(store, "list_runs", side_effect=OSError("x")):
            res = attention.collect()
        self.assertEqual(res["items"], [])
        self.assertTrue(res["degraded"])

    def test_dead_override_listed(self):
        """第 6 类：契约不过的覆盖表 = 死表，必须上注意力面板。"""
        import json as _json
        import tempfile
        from pathlib import Path
        from core import paths as _paths
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pub = Path(tmp.name) / "publish"
        pub.mkdir()
        bad = {"upload_chapter": [{"do": "fill", "sel": ".x", "key": "t"}]}
        (pub / "flows-fanqie.json").write_text(
            _json.dumps(bad), encoding="utf-8")
        with mock.patch.object(_paths, "PUBLISH_DIR", pub):
            res = attention.collect(now=self.now)
        it = next(i for i in res["items"] if i["kind"] == "dead_override")
        self.assertIn("flows-fanqie.json", it["title"])
        self.assertIn("upload_chapter", it["title"])
        self.assertTrue(it["reason"])

    def test_live_override_not_listed(self):
        import json as _json
        import tempfile
        from pathlib import Path
        from core import paths as _paths
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pub = Path(tmp.name) / "publish"
        pub.mkdir()
        # 契约三件套齐的表不应上面板
        good = {"upload_chapter": [
            {"do": "submit", "text": "发布"},
            {"do": "fanqie_publish"},
            {"do": "verify_chapter_status", "url": "{u}"},
        ]}
        (pub / "flows-fanqie.json").write_text(
            _json.dumps(good), encoding="utf-8")
        with mock.patch.object(_paths, "PUBLISH_DIR", pub):
            res = attention.collect(now=self.now)
        self.assertNotIn(
            "dead_override", [i["kind"] for i in res["items"]])


class GapTests(unittest.TestCase):
    """缺口巡检两源（2026-11-01 借 Ruflo 的 testgaps：该发生没发生的才要人看）。"""

    def setUp(self):
        self.now = time.time()
        # 与 CollectTests 同款：发布告警来源读真实台账，统一 mock 成不告警
        pp = mock.patch.object(pub_stats, "alert", return_value=None)
        pp.start()
        self.addCleanup(pp.stop)

    def _collect(self, runs, tasks, autos):
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=tasks), \
             mock.patch.object(automation, "list_tasks", return_value=autos):
            return attention.collect(now=self.now)

    def _iso(self, seconds_ago):
        return time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(self.now - seconds_ago))

    def test_automation_late_listed_with_run_now(self):
        autos = [{"id": "a1", "name": "每日巡检", "enabled": True,
                  "next_run": self._iso(7200), "task_id": "t9"}]
        res = self._collect([], [], autos)
        it = next(i for i in res["items"] if i["kind"] == "automation_late")
        self.assertEqual(it["automation_id"], "a1")
        self.assertIn("run_now", [a["type"] for a in it["actions"]])

    def test_fresh_or_disabled_automation_not_listed(self):
        autos = [{"id": "a2", "name": "n", "enabled": True,
                  "next_run": self._iso(60)},
                 {"id": "a3", "name": "n2", "enabled": False,
                  "next_run": self._iso(86400)}]
        res = self._collect([], [], autos)
        self.assertEqual(res["items"], [])

    def test_serial_gap_listed_with_retry(self):
        runs = [_run("r1", status="done", ended=self._iso(72 * 3600),
                     task_id="t1")]
        tasks = [{"id": "t1", "title": "在更书", "serial": True}]
        res = self._collect(runs, tasks, [])
        it = next(i for i in res["items"] if i["kind"] == "serial_gap")
        self.assertEqual(it["task_id"], "t1")
        self.assertIn("retry", [a["type"] for a in it["actions"]])

    def test_recent_active_or_stale_serial_no_gap(self):
        # 刚产出过（2h）不报；30 天没活动（完结/停更老书）不报；还在跑不报
        recent = [_run("r1", status="done", ended=self._iso(2 * 3600),
                       task_id="t1")]
        stale = [_run("r2", status="done", ended=self._iso(40 * 86400),
                      task_id="t2")]
        active = [_run("r3", status="running", ended=self._iso(72 * 3600),
                       task_id="t3")]
        tasks = [{"id": "t1", "title": "新书", "serial": True},
                 {"id": "t2", "title": "老书", "serial": True},
                 {"id": "t3", "title": "在跑", "serial": True}]
        res = self._collect(recent + stale + active, tasks, [])
        kinds = [i["kind"] for i in res["items"]]
        self.assertNotIn("serial_gap", kinds)

    def test_auto_publish_gate_is_stricter(self):
        # 挂自动发布的用 36h 严窗口：40h 报、30h 不报
        ap_task = [{"id": "t1", "title": "定时书", "serial": True,
                    "auto_publish": {"enabled": True}}]
        late = [_run("r1", status="done", ended=self._iso(40 * 3600),
                     task_id="t1")]
        res = self._collect(late, ap_task, [])
        it = next(i for i in res["items"] if i["kind"] == "serial_gap")
        self.assertIn("自动发布", it["reason"])
        soon = [_run("r1", status="done", ended=self._iso(30 * 3600),
                     task_id="t1")]
        res = self._collect(soon, ap_task, [])
        self.assertNotIn("serial_gap", [i["kind"] for i in res["items"]])


class PublishRateTests(unittest.TestCase):
    """第 7 来源：发布成功率告警（2026-11-01 KPI 装牙）。

    判定本体在 publish.stats.alert 的守测里（test_publish_stats.AlertTest）；
    这里锁的是接线：告警非 None 才上面板、条目字段齐、stats 故障不连累
    其它来源。
    """

    def setUp(self):
        self.now = time.time()

    def _collect(self, alert_value, summary_value=None, alert_raises=False):
        runs = [_run("r1", error="评审未达标")]
        tasks = [{"id": "t1", "title": "书一"}]
        # 注意：告警值必须走 return_value（side_effect=None 会回落到
        # MagicMock 自动真值——第一版就栽在这里）；crash 用例才用 side_effect
        with mock.patch.object(store, "list_runs", return_value=runs), \
             mock.patch.object(store, "list_tasks", return_value=tasks), \
             mock.patch.object(automation, "list_tasks", return_value=[]), \
             mock.patch.object(pub_stats, "summary", return_value=summary_value or {}), \
             mock.patch.object(pub_stats, "alert",
                               return_value=alert_value or None,
                               side_effect=OSError("x") if alert_raises else None):
            return attention.collect(now=self.now)

    def test_alert_listed_with_reason(self):
        res = self._collect({"rate": 0.25, "goal": 0.95, "days": 7,
                             "attempts": 4, "failed": 3},
                            summary_value={"failures": [
                                {"title": "书一", "platform": "fanqie",
                                 "error": "登录已失效"}]})
        it = next(i for i in res["items"] if i["kind"] == "publish_rate")
        self.assertEqual(it["title"], "发布成功率跌破目标线")
        self.assertIn("25%", it["reason"])
        self.assertIn("3 失败", it["reason"])
        self.assertIn("登录已失效", it["reason"])

    def test_quiet_when_no_alert(self):
        res = self._collect(None)
        self.assertNotIn("publish_rate", [i["kind"] for i in res["items"]])
        # 失败 run 条目仍在：告警安静不吞其它来源
        self.assertIn("failed_run", [i["kind"] for i in res["items"]])

    def test_stats_crash_does_not_harm_other_sources(self):
        res = self._collect(None, alert_raises=True)
        self.assertNotIn("publish_rate", [i["kind"] for i in res["items"]])
        self.assertIn("failed_run", [i["kind"] for i in res["items"]])
        self.assertFalse(res.get("degraded"))


class UiWiringTest(unittest.TestCase):
    """前端接线契约（静态）：注意力条认识 publish_rate，标签/色槽/翻译在位。

    后端条目渲染成什么标签是 borrow-ui 的事；三处缺一处，告警条就会
    以裸 kind 名/无色/无英文出现——静态断言锁死，免起浏览器。"""

    def _read(self, *parts):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1].joinpath(*parts)
                .read_text(encoding="utf-8"))

    def test_attention_bar_knows_publish_rate(self):
        js = self._read("app", "ui", "borrow-ui.js")
        self.assertIn('publish_rate: _t("发布告警")', js,
                      "borrow-ui.js 注意力条缺 publish_rate 标签")
        css = self._read("app", "ui", "borrow-ui.css")
        self.assertIn(".ba-publish_rate", css,
                      "borrow-ui.css 缺 publish_rate 色槽")
        i18n = self._read("app", "ui", "i18n.js")
        self.assertIn('"发布告警": "Publish alert"', i18n,
                      "i18n.js 缺发布告警英文翻译")


if __name__ == "__main__":
    unittest.main()
