# -*- coding: utf-8 -*-
"""机制闸 HTTP 路由测试（/api/strategy/*）：沿用 evalbench 路由测试的
Handler 桩法（__new__ + _authed/_body/_json/_deny_control 桩），不打真网、
不碰生产数据目录。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
from app.core import strategy_config, strategy_lab  # noqa: F401（对账同源实例，另见 core.* 别名）

APP_DIR = str(REPO / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
import main

# 路由内 `from core import ...` 与 app.core 是两个模块实例（evalbench 路由
# 测试同坑）：打桩必须落在 core.* 实例上，断言也走同一实例。
from core import paths as core_paths
from core import strategy_config as route_config
from core import strategy_lab as route_lab

Handler = main.Handler


def _handler(method, path, body=None):
    h = Handler.__new__(Handler)
    h.path = path
    h.headers = {}
    h._authed = lambda: True
    h._deny_control = lambda: None
    h._body = lambda: body if body is not None else {}
    out = []
    h._json = lambda status, value: out.append((status, value))
    h._out = out
    return h


class StrategyApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(route_config, "_FILE", root / "strategy.json"),
            mock.patch.object(route_config, "_CACHE", {"mtime": None, "doc": {}}),
            mock.patch.object(route_lab, "_FILE", root / "strategy_shadow.jsonl"),
            mock.patch.object(route_lab, "_REPORT_FILE", root / "strategy_lab_report.json"),
            mock.patch.object(core_paths, "DATA_DIR", root),
            mock.patch.object(core_paths, "RUNS_DIR", root / "runs"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_get_config_returns_doc(self):
        h = _handler("GET", "/api/strategy/config")
        Handler._route_get(h)
        status, body = h._out[-1]
        self.assertEqual(status, 200)
        self.assertEqual(len(body["weights"]), 14)
        self.assertIn("ranges", body)
        self.assertIn("challenger_effective", body)

    def test_get_report_returns_aggregates(self):
        route_lab._FILE.write_text(
            '{"ts": "2026-10-22 10:00:00", "run_id": "", "role": "implement",'
            ' "champion_agent": "a", "champion_score": 1.0,'
            ' "challenger_agent": "b", "challenger_score": 2.0, "diverged": true}\n',
            encoding="utf-8")
        h = _handler("GET", "/api/strategy/report?days=30")
        Handler._route_get(h)
        status, body = h._out[-1]
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["diverged"], 1)

    def test_post_config_update_and_reject(self):
        h = _handler("POST", "/api/strategy/config",
                     {"weights": {"sticky_bonus": 6.0}})
        Handler._route_post(h)
        status, body = h._out[-1]
        self.assertEqual(status, 200)
        self.assertEqual(route_config.get("sticky_bonus"), 6.0)

        h = _handler("POST", "/api/strategy/config",
                     {"weights": {"nope": 1}})
        Handler._route_post(h)
        status, body = h._out[-1]
        self.assertEqual(status, 400)
        self.assertIn("未知策略键", body["error"])

    def test_post_config_reset(self):
        route_config.update_weights({"sticky_bonus": 6.0})
        h = _handler("POST", "/api/strategy/config", {"action": "reset"})
        Handler._route_post(h)
        status, _ = h._out[-1]
        self.assertEqual(status, 200)
        self.assertEqual(route_config.raw_weights(), {})

    def test_challenger_set_clear_adopt_cycle(self):
        h = _handler("POST", "/api/strategy/challenger",
                     {"action": "set", "name": "c1",
                      "weights": {"sticky_bonus": 2.0}, "enabled": True})
        Handler._route_post(h)
        status, body = h._out[-1]
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["config"]["challenger"])

        h = _handler("POST", "/api/strategy/challenger", {"action": "adopt"})
        Handler._route_post(h)
        status, body = h._out[-1]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(route_config.get("sticky_bonus"), 2.0)
        self.assertIsNone(route_config.challenger())

        # 再 adopt 无挑战者 → 400
        h = _handler("POST", "/api/strategy/challenger", {"action": "adopt"})
        Handler._route_post(h)
        status, body = h._out[-1]
        self.assertEqual(status, 400)

        # 未知 action → 400
        h = _handler("POST", "/api/strategy/challenger", {"action": "bogus"})
        Handler._route_post(h)
        status, _ = h._out[-1]
        self.assertEqual(status, 400)

    def test_challenger_clear(self):
        route_config.set_challenger("c1", {"sticky_bonus": 2.0})
        h = _handler("POST", "/api/strategy/challenger", {"action": "clear"})
        Handler._route_post(h)
        status, _ = h._out[-1]
        self.assertEqual(status, 200)
        self.assertIsNone(route_config.challenger())


if __name__ == "__main__":
    unittest.main()
