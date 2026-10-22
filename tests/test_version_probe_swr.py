import json
import sys
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core import manager          # noqa: E402
import main                        # noqa: E402


def _entry(eid="t-cli"):
    return {"id": eid, "detect": {"cli": eid}}


class VersionProbeSWRTests(unittest.TestCase):
    """版本探测 SWR 化回归（2026-10-22 开页慢案）：`cli --version` 是秒级子进程
    探测，10 条串行 ≈16s；/api/catalog 热路径绝不能同步背它。"""

    def setUp(self):
        manager._STATE["versions"] = {}
        manager._STATE["ver_sweep"] = False

    def tearDown(self):
        manager._STATE["versions"] = {}
        manager._STATE["ver_sweep"] = False

    def test_cold_returns_placeholder_and_kicks_background(self):
        with mock.patch.object(manager, "_probe_version", return_value="1.0") as p, \
             mock.patch.object(manager.threading, "Thread") as th:
            v = manager.version_of(_entry())
            self.assertEqual(v, "-")           # 冷缓存吐占位，不阻塞请求
            p.assert_not_called()              # 没有同步探测
            th.assert_called_once()            # 补探踢到后台
            self.assertTrue(manager._STATE["ver_sweep"])

    def test_stale_returns_old_value_without_blocking(self):
        manager._STATE["versions"]["t-cli"] = (0.0, "0.9")   # 远早于 TTL
        with mock.patch.object(manager, "_probe_version", return_value="1.0") as p, \
             mock.patch.object(manager.threading, "Thread"):
            v = manager.version_of(_entry())
            self.assertEqual(v, "0.9")         # 立即拿旧值
            p.assert_not_called()

    def test_fresh_cache_hits_without_kick(self):
        import time as _t
        manager._STATE["versions"]["t-cli"] = (_t.time(), "1.2.3")
        with mock.patch.object(manager.threading, "Thread") as th:
            self.assertEqual(manager.version_of(_entry()), "1.2.3")
            th.assert_not_called()
            self.assertFalse(manager._STATE["ver_sweep"])

    def test_wait_true_probes_synchronously(self):
        with mock.patch.object(manager, "_probe_version", return_value="2.0") as p:
            self.assertEqual(manager.version_of(_entry(), wait=True), "2.0")
            p.assert_called_once()

    def test_probe_version_populates_cache_once(self):
        with mock.patch.object(manager, "detect_all",
                               return_value={"t-cli": {"installed": True,
                                                       "detail": "x"}}), \
             mock.patch.object(manager, "_cli_version_output",
                               return_value="3.1.0") as probe:
            self.assertEqual(manager._probe_version(_entry()), "3.1.0")
            self.assertEqual(manager._probe_version(_entry()), "3.1.0")
            probe.assert_called_once()         # 第二次吃 TTL 缓存

    def test_sweep_resets_inflight_flag_even_on_error(self):
        manager._STATE["ver_sweep"] = True
        with mock.patch.object(manager.catalog, "load",
                               side_effect=RuntimeError("boom")):
            manager._sweep_versions()
        self.assertFalse(manager._STATE["ver_sweep"])


class StatePayloadJsonCacheTests(unittest.TestCase):
    """状态载荷按版本缓存序列化回归：字符串必须是单层 JSON 对象（曾把串再
    dumps 一遍变成双重编码，前端 applyState 收到字符串直接丢弃）。"""

    def test_payload_json_is_single_encoded_object(self):
        raw = main._state_payload_json("swr-test-client")
        d = json.loads(raw)
        self.assertIsInstance(d, dict)
        self.assertIn("control", d)
        self.assertIn("tasks", d)
        self.assertIn("task_latest", d)

    def test_cache_reuses_body_for_same_version(self):
        # control 视图按端不同：注入可辨认的假视图验证 body 共享 + control 拼回
        views = {"client-a": {"mode": "held", "by": "a"},
                 "client-b": {"mode": "held", "by": "b"}}
        with mock.patch.object(main.remote, "control_view",
                               side_effect=lambda cid: views.get(cid, {})):
            a = main._state_payload_json("client-a")
            b = main._state_payload_json("client-b")
        # 同版本共享大 body：去掉 control 尾巴后两者逐字相同
        self.assertEqual(a[: a.rfind('"control"')], b[: b.rfind('"control"')])
        self.assertIn('"by": "a"', a)
        self.assertIn('"by": "b"', b)


if __name__ == "__main__":
    unittest.main()
