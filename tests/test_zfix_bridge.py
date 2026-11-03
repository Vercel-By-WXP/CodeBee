# -*- coding: utf-8 -*-
"""zfix_bridge 桥接测试：禅道链路 × 知识引擎。

四组：
  1. 引擎加载（含 skills 目录缺失时的降级）；
  2. 桥 API（指纹/根因/历史/规则附加段）；
  3. 与 zentao.py 链路的集成（goal 注入、claim 指纹字段、resolve/failed 落档）；
  4. 节流与知识概况。

纪律复验：引擎任何故障不得影响禅道主链路——降级测试就是这条纪律的守卫。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import zfix_bridge
from app.core import paths


def _engine_here():
    """确保测试进程里引擎指向本仓库 skills 目录。"""
    zfix_bridge._test_reset()
    return zfix_bridge.available()


class EngineLoadTests(unittest.TestCase):
    def tearDown(self):
        zfix_bridge._test_reset()

    def test_engine_loads_from_repo_skills(self):
        self.assertTrue((Path(paths.ROOT) / "skills" / "zentao-autofix" / "zfix"
                         / "knowledge.py").is_file(), "技能包应在仓库内随包分发")
        self.assertTrue(_engine_here())

    def test_knowledge_lands_in_data_dir_not_home(self):
        _engine_here()
        home = zfix_bridge._ENGINE["knowledge"].user_home()
        self.assertIn("zfix-knowledge", str(home))
        self.assertEqual(Path(home).parent, Path(paths.DATA_DIR),
                         "知识必须落数据目录（TUTTI_DATA 重定向跟随），不写 ~/.zfix")

    def test_engine_degrades_when_dir_missing(self):
        with mock.patch.object(zfix_bridge, "engine_dir",
                               lambda: Path(tempfile.gettempdir()) / "no-such-zfix"):
            zfix_bridge._test_reset()
            self.assertFalse(zfix_bridge.available())
            self.assertEqual(zfix_bridge.fingerprint({"id": "1", "title": "x"}), "")
            self.assertEqual(zfix_bridge.goal_extras({"id": "1", "title": "x"}), "")
            self.assertEqual(zfix_bridge.repeat_info("abc"), {"count": 0, "last": "", "bugs": []})
            self.assertFalse(zfix_bridge.knowledge_view()["available"])
            # record_fix / learn 在降级下必须是纯 no-op，不抛异常
            zfix_bridge.record_fix({"id": "1", "title": "x"}, "p", "null_ref", "why")
            self.assertEqual(zfix_bridge.learn_if_due(force=True), 0)


class BridgeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._old_data = paths.DATA_DIR
        paths.DATA_DIR = Path(cls.tmp.name)
        zfix_bridge._test_reset()
        assert zfix_bridge.available()

    @classmethod
    def tearDownClass(cls):
        paths.DATA_DIR = cls._old_data
        zfix_bridge._test_reset()
        cls.tmp.cleanup()

    def test_fingerprint_stable_across_line_numbers(self):
        a = {"id": "1", "title": "保存报错 AttributeError NoneType get",
             "steps": 'File "app/svc.py", line 42, in save<br/>AttributeError'}
        b = {"id": "2", "title": "保存报错 AttributeError NoneType get",
             "steps": 'File "app/svc.py", line 88, in save<br/>AttributeError'}
        self.assertEqual(zfix_bridge.fingerprint(a), zfix_bridge.fingerprint(b))

    def test_classify_cause_reads_chinese(self):
        self.assertEqual(zfix_bridge.classify_cause("读取超时 TimeoutError"), "network")
        self.assertEqual(zfix_bridge.classify_cause("空指针"), "null_ref")
        self.assertEqual(zfix_bridge.cause_label("null_ref"), "空值/未初始化")

    def test_record_and_goal_extras_closed_loop(self):
        bug = {"id": "101", "title": "订单保存报 NoneType has no attribute get",
               "steps": "步骤：不填地址点保存<br/>AttributeError: 'NoneType'"}
        zfix_bridge.record_fix(bug, "zentao", "null_ref",
                               "order 缺 address 时未判空直接取 city", verify_ok=True)
        rep = zfix_bridge.repeat_info(_fp_of(bug))
        self.assertEqual(rep["count"], 1)
        self.assertEqual(rep["bugs"], ["101"])

        extras = zfix_bridge.goal_extras(bug)
        self.assertIn("历史经验", extras)
        self.assertIn("第 2 次出现", extras)
        self.assertIn("#101", extras)
        self.assertIn("order 缺 address", extras)

    def test_goal_extras_empty_when_no_history(self):
        bug = {"id": "999", "title": "完全没见过的奇怪问题 xyzzy",
               "steps": "描述很短"}
        self.assertEqual(zfix_bridge.goal_extras(bug), "")

    def test_learn_if_due_throttles(self):
        first = zfix_bridge.learn_if_due(force=True)
        self.assertIsInstance(first, int)
        # 未到 6h：直接 0，不再触发任何 IO
        with mock.patch.object(zfix_bridge._ENGINE["knowledge"].KnowledgeBase,
                               "learn", side_effect=AssertionError("不该被调用")):
            self.assertEqual(zfix_bridge.learn_if_due(), 0)

    def test_knowledge_view_shape(self):
        # 用例按字母序执行，view 可能先于 record 跑——自己种一条保证非空
        zfix_bridge.record_fix({"id": "v1", "title": "view 桩", "steps": "stub"},
                               "zentao", "ui", "view 形状测试桩")
        view = zfix_bridge.knowledge_view()
        self.assertTrue(view["available"])
        self.assertGreaterEqual(view["fixes"], 1)
        self.assertIn("by_cause", view)
        for row in view["by_cause"]:
            self.assertTrue({"cause", "label", "count"} <= set(row))


def _fp_of(bug):
    return zfix_bridge.fingerprint(bug)


class ZentaoIntegrationTests(unittest.TestCase):
    """桥 × zentao.py 主链路：只测桥的接缝，禅道 HTTP 全部 mock。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._old_data = paths.DATA_DIR
        paths.DATA_DIR = Path(cls.tmp.name)
        zfix_bridge._test_reset()
        from app.core import zentao
        zentao._test_reset()
        zentao._FILE = Path(cls.tmp.name) / "zentao.json"

    @classmethod
    def tearDownClass(cls):
        from app.core import zentao
        zentao._test_reset()
        zentao._FILE = paths.DATA_DIR / "zentao.json"
        paths.DATA_DIR = cls._old_data
        zfix_bridge._test_reset()
        cls.tmp.cleanup()

    def test_goal_text_contains_history_after_archive(self):
        from app.core import zentao
        bug = {"id": "201", "title": "导出超时 TimeoutError",
               "steps": "大数据量导出 504<br/>TimeoutError", "severity": "2"}
        base = zentao._goal_text(bug)
        # 无档案时：种子模式可能已命中（TimeoutError → http-no-timeout 规则），
        # 但不得出现「同指纹第 N 次出现」——#201 自己还没修过
        self.assertNotIn("第 2 次出现", base)
        self.assertNotIn("同指纹已修过", base)
        zfix_bridge.record_fix(bug, "zentao", "network", "导出未分页全量加载超时")
        enriched = zentao._goal_text(bug)
        self.assertIn("历史经验", enriched)
        self.assertIn("#201", enriched)
        self.assertIn("第 2 次出现", enriched)
        self.assertIn("导出未分页", enriched)

    def test_goal_text_survives_engine_missing(self):
        from app.core import zentao
        bug = {"id": "202", "title": "x", "steps": "y"}
        with mock.patch.object(zfix_bridge, "goal_extras",
                               side_effect=RuntimeError("engine exploded")):
            text = zentao._goal_text(bug)   # 不许抛
        self.assertIn("#202", text)
        self.assertNotIn("历史经验", text)

    def test_route_one_stamps_fingerprint_and_repeat(self):
        from app.core import zentao
        bug = {"id": "301", "title": "列表导出 IndexError 越界",
               "steps": "空列表取第 0 项 IndexError", "module": "",
               "status": "active", "severity": "3"}
        zfix_bridge.record_fix(bug, "zentao", "boundary", "空集合未判空取 [0]")
        profile = zentao._norm_profile({"product": 7, "our_sides": ["backend"],
                                        "repos": {"backend": {"workdir": str(Path(self.tmp.name) / "wd"),
                                                              "git_rev": "abc"}}}, 0)
        cfg = dict(zentao._CFG_DEFAULTS, triage_ai=False)
        # not_ours：走转派分支也能先打指纹——指纹在分流之前算好
        with mock.patch.object(zentao, "_transfer", return_value=True), \
             mock.patch.object(zentao, "_notify"), \
             mock.patch.object(zentao, "_save_locked"):
            zentao._route_one(bug, profile, cfg, notify=False)
        claim = zentao._STATE["claims"].get("301")
        self.assertIsNotNone(claim)
        self.assertTrue(claim.get("fingerprint"))
        self.assertEqual(claim.get("repeat_count"), 1)
        self.assertIn("repeat_note", claim)

    def test_finish_failed_archives_failure_knowledge(self):
        from app.core import zentao
        claim = {"bug_id": "401", "product": 7, "title": "支付回调死锁",
                 "triage": {"side": "backend", "reason": "锁顺序", "by": "rule"},
                 "tasks": [{"side": "backend", "task_id": "t1", "run_id": "r1",
                            "state": "failed"}],
                 "attempts": zentao.FIX_MAX_ATTEMPTS, "state": "retry",
                 "note": "", "archived": False}
        run = {"id": "r1", "status": "failed", "error": "deadlock detected"}
        with mock.patch.object(zentao.store, "get_run", return_value=run), \
             mock.patch.object(zentao.store, "task_runs", return_value=[]), \
             mock.patch.object(zentao, "_set_claim"), \
             mock.patch.object(zentao, "_notify"), \
             mock.patch.object(zentao, "_looks_internal_crash", return_value=False):
            zentao._finish_failed(claim, {})
        kb = zfix_bridge._ENGINE["knowledge"].KnowledgeBase()
        archived = [r for r in kb.fixes() if r.bug_id == "401"]
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].tags, ["failed"])
        self.assertFalse(archived[0].verify.get("ok", True))
        self.assertIn("死锁", archived[0].summary)

    def test_view_carries_knowledge(self):
        from app.core import zentao
        v = zentao.view()
        self.assertIn("knowledge", v)
        self.assertTrue(v["knowledge"]["available"])


if __name__ == "__main__":
    unittest.main()
