# -*- coding: utf-8 -*-
"""机制闸影子对账（Raven Evolver 借鉴）单测。

覆盖：score overrides 穿透（挑战者权重翻盘排序）、pick 落影子账（分歧/
无分歧/无挑战者）、影子链路异常绝不连累选将、jsonl 轮转、report 结局
关联聚合、strategy_lab.adopt 留档。全部走临时数据目录。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import paths, router, strategy_config, strategy_lab

TTYPE = "code"


def _agent(aid, kind):
    return {"id": aid, "kind": kind, "mode": "real",
            "_dispatch_task_type": TTYPE}


def _losing_stats(aid, streak=4, runs=6, wins=0):
    return {aid: {TTYPE: {"runs": runs, "wins": wins, "streak": streak}}}


def _isolated_score(agent, stats, overrides=None):
    """屏蔽绑定/健康/在线等外设信号，只看评分主干（对齐连败冷却测试）。"""
    with mock.patch.object(router.modelhub, "_binding_for",
                           return_value={}), \
         mock.patch.object(router.modelhub, "providers", return_value=[]), \
         mock.patch("app.core.health.down_names", return_value=set()), \
         mock.patch.object(router, "agent_upstreams", return_value=set()), \
         mock.patch("app.core.usage.routing_stats", return_value={}), \
         mock.patch.object(router.modelhub, "chain_tool_verdict",
                           return_value=(None, "")):
        return router.score(agent, "implement", TTYPE, stats, overrides=overrides)


class ShadowIsolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {
            "cfg_file": strategy_config._FILE,
            "cfg_cache": dict(strategy_config._CACHE),
            "shadow_file": strategy_lab._FILE,
            "report_file": strategy_lab._REPORT_FILE,
            "data_dir": paths.DATA_DIR,
            "runs_dir": paths.RUNS_DIR,
        }
        strategy_config._FILE = root / "strategy.json"
        strategy_config._CACHE = {"mtime": None, "doc": {}}
        strategy_lab._FILE = root / "strategy_shadow.jsonl"
        strategy_lab._REPORT_FILE = root / "strategy_lab_report.json"
        paths.DATA_DIR = root
        paths.RUNS_DIR = root / "runs"
        router.sticky_reset()

    def tearDown(self):
        strategy_config._FILE = self.old["cfg_file"]
        strategy_config._CACHE = self.old["cfg_cache"]
        strategy_lab._FILE = self.old["shadow_file"]
        strategy_lab._REPORT_FILE = self.old["report_file"]
        paths.DATA_DIR = self.old["data_dir"]
        paths.RUNS_DIR = self.old["runs_dir"]
        self.tmp.cleanup()
        router.sticky_reset()

    def _pick(self, stats, context=None):
        agents = [_agent("badcli", "codex"), _agent("okcli", "mimo")]
        with mock.patch.object(router.modelhub, "_binding_for",
                               return_value={}), \
             mock.patch.object(router.modelhub, "providers", return_value=[]), \
             mock.patch("app.core.health.down_names", return_value=set()), \
             mock.patch.object(router, "agent_upstreams", return_value=set()), \
             mock.patch("app.core.usage.routing_stats", return_value={}), \
             mock.patch.object(router.modelhub, "chain_tool_verdict",
                               return_value=(None, "")):
            return router.pick(agents, "implement", TTYPE, stats,
                               context=context)

    def _shadow_lines(self):
        try:
            return [json.loads(ln) for ln
                    in strategy_lab._FILE.read_text(encoding="utf-8").splitlines()
                    if ln.strip()]
        except OSError:
            return []

    def test_overrides_flip_ranking(self):
        bad, ok = _agent("badcli", "codex"), _agent("okcli", "mimo")
        stats = _losing_stats("badcli")
        # 默认权重：连败冷却沉底
        bad_default, _ = _isolated_score(bad, stats)
        ok_default, _ = _isolated_score(ok, stats)
        self.assertLess(bad_default, ok_default)
        # 挑战者权重：冷却轻、历史罚轻 → 高基线翻盘
        overrides = {"streak_bench_penalty": -5.0, "history_loss_cap": 2.0}
        bad_ov, _ = _isolated_score(bad, stats, overrides=overrides)
        ok_ov, _ = _isolated_score(ok, stats, overrides=overrides)
        self.assertGreater(bad_ov, ok_ov)

    def test_pick_writes_shadow_on_divergence(self):
        strategy_config.set_challenger(
            "gentle", {"streak_bench_penalty": -5.0, "history_loss_cap": 2.0})
        sel, _ = self._pick(_losing_stats("badcli"),
                            context={"run_id": "r-1", "task_id": "t-1"})
        self.assertEqual(sel.get("id"), "okcli")     # 冠军仍是健康者
        lines = self._shadow_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertTrue(rec["diverged"])
        self.assertEqual(rec["champion_agent"], "okcli")
        self.assertEqual(rec["challenger_agent"], "badcli")
        self.assertEqual(rec["run_id"], "r-1")
        self.assertEqual(rec["role"], "implement")
        self.assertIsInstance(rec["champion_score"], float)
        self.assertIsInstance(rec["challenger_score"], float)

    def test_no_shadow_without_challenger(self):
        sel, _ = self._pick(_losing_stats("badcli"))
        self.assertEqual(sel.get("id"), "okcli")
        self.assertEqual(self._shadow_lines(), [])

    def test_shadow_failure_never_breaks_pick(self):
        strategy_config.set_challenger(
            "gentle", {"streak_bench_penalty": -5.0})
        with mock.patch.object(strategy_lab, "append_shadow",
                               side_effect=OSError("disk full")):
            sel, _ = self._pick(_losing_stats("badcli"))
        self.assertEqual(sel.get("id"), "okcli")

    def test_report_joins_run_outcomes(self):
        run_dir = paths.RUNS_DIR / "r-1"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": "r-1", "status": "failed",
            "steps": [{"n": 1, "role": "implement", "agent": "okcli",
                       "status": "failed"}],
        }), encoding="utf-8")
        run2 = paths.RUNS_DIR / "r-2"
        run2.mkdir(parents=True)
        (run2 / "run.json").write_text(json.dumps({
            "id": "r-2", "status": "done",
            "steps": [{"n": 1, "role": "implement", "agent": "okcli",
                       "status": "done"}],
        }), encoding="utf-8")
        recs = [
            {"ts": "2026-10-22 10:00:00", "run_id": "r-1", "role": "implement",
             "ttype": TTYPE, "champion_agent": "okcli", "champion_score": 5.0,
             "challenger_agent": "badcli", "challenger_score": 10.0,
             "diverged": True},
            {"ts": "2026-10-22 10:05:00", "run_id": "r-2", "role": "implement",
             "ttype": TTYPE, "champion_agent": "okcli", "champion_score": 3.0,
             "challenger_agent": "badcli", "challenger_score": 8.0,
             "diverged": True},
            {"ts": "2026-10-22 10:10:00", "run_id": "", "role": "implement",
             "ttype": TTYPE, "champion_agent": "okcli", "champion_score": 6.0,
             "challenger_agent": "okcli", "challenger_score": 6.0,
             "diverged": False},
        ]
        strategy_lab._FILE.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
            encoding="utf-8")
        rep = strategy_lab.report(days=30)
        self.assertEqual(rep["total"], 3)
        self.assertEqual(rep["diverged"], 2)
        self.assertEqual(rep["potential_saves"], 1)   # r-1 冠军失败
        self.assertEqual(rep["diverged_champion_ok"], 1)
        self.assertEqual(rep["score_delta"]["avg"], 5.0)
        self.assertEqual(rep["by_role"]["implement"]["diverged"], 2)
        # 报告缓存落盘
        self.assertTrue(strategy_lab._REPORT_FILE.exists())
        self.assertEqual(strategy_lab.latest_report().get("total"), 3)

    def test_lab_adopt_records_summary(self):
        strategy_config.set_challenger(
            "gentle", {"sticky_bonus": 1.5, "history_loss_cap": 10.0})
        res = strategy_lab.adopt()
        self.assertTrue(res["ok"], res)
        self.assertEqual(strategy_config.get("sticky_bonus"), 1.5)
        self.assertEqual(strategy_config.get("history_loss_cap"), 10.0)
        hist = strategy_config.adopt_history()
        self.assertEqual(len(hist), 1)
        self.assertIn("total", hist[0]["summary"])
        self.assertIsNone(strategy_config.challenger())

    def test_rotation_keeps_recent_lines(self):
        base = [{"ts": "2026-10-22 10:00:0%d" % i, "diverged": False} for i in range(3)]
        strategy_lab._FILE.write_text(
            "\n".join(json.dumps(r) for r in base) + "\n", encoding="utf-8")
        with mock.patch.object(strategy_lab, "_MAX_LINES", 2), \
             mock.patch.object(strategy_lab, "_ROTATE_BYTES", 1):
            strategy_lab.append_shadow({"ts": "2026-10-22 11:00:00",
                                        "diverged": True})
        lines = self._shadow_lines()
        self.assertEqual(len(lines), 3)          # 轮转留 2 + 新追加 1
        self.assertTrue(lines[-1]["diverged"])
        self.assertEqual(lines[0]["ts"], "2026-10-22 10:00:01")   # 最老的被裁


if __name__ == "__main__":
    unittest.main()
