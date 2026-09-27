# -*- coding: utf-8 -*-
"""流程版本历史（flows 快照/清单/恢复/按 digest 战绩）单测。

覆盖：覆盖前自动快照、noop 不产噪音、来源标记（manual/import/restore）、
恢复回路（目标条目消费+当前版入史）、历史上限、预置流程 overrides 入史、
按 flow_revision 对账的实测战绩。

跑法：python -m unittest discover -s tests -p "test_flow_versions.py" -v
"""
from __future__ import annotations

import unittest

from base import BaseTest

from app.core import flows


def _upsert_podcast(threshold=7.5, **kw):
    payload = {"id": "podcast", "name": "播客脚本", "engine": "review",
               "manuscript": "script.md", "rubric": ["选题", "结构"],
               "threshold": threshold, "rounds": 2}
    payload.update(kw)
    flow, err = flows.upsert_flow(payload)
    assert err is None, err
    return flow


class TestFlowHistory(BaseTest):

    def test_overwrite_snapshots_previous(self):
        _upsert_podcast(threshold=7.5)
        self.assertEqual(len(flows.flow_versions("podcast")), 1)   # 首建无历史
        _upsert_podcast(threshold=9.0)
        versions = flows.flow_versions("podcast")
        self.assertEqual(len(versions), 2)
        self.assertTrue(versions[0]["current"])
        self.assertFalse(versions[1]["current"])
        self.assertEqual(versions[0]["source"], "current")
        self.assertEqual(versions[1]["source"], "manual")
        # 历史 digest 与当前不同（语义确实变了）
        self.assertNotEqual(versions[0]["digest"], versions[1]["digest"])

    def test_noop_skips_history(self):
        _upsert_podcast(threshold=7.5)
        _upsert_podcast(threshold=7.5)                     # 语义相同
        self.assertEqual(len(flows.flow_versions("podcast")), 1)

    def test_import_source_tagged(self):
        _upsert_podcast(threshold=7.5)
        code, _ = flows.export_flow_code("podcast")
        _upsert_podcast(threshold=9.0)
        res, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        versions = flows.flow_versions("podcast")
        self.assertEqual(versions[1]["source"], "import")  # 被导入顶下的 v9.0
        self.assertEqual(versions[0]["digest"], versions[2]["digest"])  # 回到 v7.5 语义

    def test_restore_roundtrip(self):
        _upsert_podcast(threshold=7.5)
        _upsert_podcast(threshold=9.0)
        old_ts = flows.flow_versions("podcast")[1]["ts"]
        flow, err = flows.restore_flow_version("podcast", old_ts)
        self.assertIsNone(err, err)
        self.assertEqual(flow["threshold"], 7.5)
        versions = flows.flow_versions("podcast")
        self.assertEqual(flows.get_flow("podcast")["threshold"], 7.5)
        # 被顶下来的 v9.0 入史（source=restore），v7.5 条目已消费
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[1]["source"], "restore")
        self.assertNotEqual(versions[0]["digest"], versions[1]["digest"])

    def test_restore_missing_ts(self):
        _upsert_podcast()
        flow, err = flows.restore_flow_version("podcast", "2099-01-01 00:00:00")
        self.assertIsNone(flow)
        self.assertIn("不存在", err)

    def test_history_cap(self):
        # threshold 有 1-10 钳制会撞顶产生 noop，用 name 循环保证每版语义不同
        for i in range(flows._HISTORY_MAX + 3):
            _upsert_podcast(name="播客脚本V%d" % i)
        self.assertEqual(len(flows.flow_versions("podcast")), flows._HISTORY_MAX + 1)

    def test_builtin_override_history_and_restore(self):
        flows.upsert_flow({"id": "novel", "threshold": 8.8, "rubric": ["情节"]})
        versions = flows.flow_versions("novel")
        self.assertTrue(versions[0]["current"])
        # 预置流程的默认定义即「旧版」：首次 override 也应把默认版留档
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[1]["source"], "manual")
        old_ts = versions[1]["ts"]
        flows.restore_flow_version("novel", old_ts)
        self.assertNotEqual(flows.get_flow("novel")["threshold"], 8.8)


class TestFlowStats(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import flows, store
        flows.set_runs_provider(store.list_runs)   # 生产由 main.py 启动注入

    def test_stats_reconciled_by_digest(self):
        flow = _upsert_podcast(threshold=7.5)
        digest = flows.flow_digest(flow)[:16]
        from app.core import store
        for verdict in ({"overall": 8.0, "publishable": True},
                        {"overall": 9.0, "publishable": True},
                        {"overall": 5.0, "publishable": False},
                        None):
            task = store.create_task({"type": "podcast", "title": "t", "goal": "g",
                                      "workdir": str(self.workdir)})
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            if verdict:
                store.update_run(run["id"], status="done", verdict=verdict)
            else:
                store.update_run(run["id"], status="failed")
        versions = {v["current"]: v for v in flows.flow_versions("podcast")}
        st = versions[True]["stats"]
        self.assertIsNotNone(st)
        self.assertEqual(st["runs"], 4)
        self.assertEqual(st["pass_rate"], 0.5)
        self.assertEqual(st["avg_overall"], 7.3)           # (8+9+5)/3，失败 run 无分不计
        # 别的 digest 不串账
        _upsert_podcast(threshold=9.0)
        versions = {v["current"]: v for v in flows.flow_versions("podcast")}
        self.assertIsNone(versions[True]["stats"])
        self.assertEqual(versions[False]["stats"]["runs"], 4)
        self.assertEqual(versions[False]["digest"], digest)


if __name__ == "__main__":
    unittest.main()
