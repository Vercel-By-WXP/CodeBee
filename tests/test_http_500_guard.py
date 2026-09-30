# -*- coding: utf-8 -*-
"""HTTP 路由顶层兜底回归（2026-09-29 store 局部名遮蔽实案）。

48f9cde 在 do_POST 中段路由里 `from core import store`，函数内 import 把
store 变成整个 do_POST 的局部名：其余所有触碰 store 的写路由（新建任务/
取消/升级全部/单条管理操作…）一律 UnboundLocalError，socketserver 直接
掐断连接，前端只见 "Failed to fetch"，服务端零痕迹。

两道防线：
- 路由链裸抛统一落日志并回 500 JSON（不再静默断连）；
- do_POST 内不得有 store 的函数内 import（本文件的探针路由实测）。

进程内起真实服务（临时数据目录+假 home，同 e2e 约定），端口字面量。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18944

sys.path.insert(0, str(ROOT / "app"))

_tmp = Path(tempfile.mkdtemp(prefix="tutti-500guard-"))
_data = _tmp / "data"
_data.mkdir()
(_data / "catalog.json").write_text(json.dumps([
    {"id": "fakeA", "name": "FakeA", "cli_group": "installable",
     "detect": {"cli": "ping"},
     "install": "ping -n 1 127.0.0.1", "upgrade": "ping -n 3 127.0.0.1",
     "default_enabled": False},
], ensure_ascii=False), encoding="utf-8")
os.environ["TUTTI_DATA"] = str(_data)
# 只加载种盘 catalog：默认合并会把真实 CLI 条目带进来，一键升级类接口
# 会把真机升级真跑一遍（2026-09-26 e2e_upgrade_all 实弹案）
os.environ["TUTTI_TEST_NO_DEFAULT_CATALOG"] = "1"
os.environ["TUTTI_PET_DISABLED"] = "1"
_fake_home = tempfile.mkdtemp(prefix="tutti-home-")
os.environ["USERPROFILE"] = _fake_home
os.environ["HOME"] = _fake_home

import main as app_main  # noqa: E402  进程内起服务（stdin 探针同款已验证）
from core import store   # noqa: E402


def _req(method, path, payload=None, timeout=15):
    import http.client
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(raw or "{}")
        except Exception:
            return resp.status, {"raw": raw[:200]}
    finally:
        conn.close()


def _start_server():
    sys.argv = ["main.py", "--port", str(PORT), "--no-browser",
                "--host", "127.0.0.1"]
    threading.Thread(target=app_main.main, daemon=True).start()
    for _ in range(60):
        try:
            if _req("GET", "/api/state")[0] == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


class HttpRouteGuard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.up = _start_server()

    def test_01_service_up(self):
        self.assertTrue(self.up, "服务未在临时数据目录起盘")

    def test_02_route_crash_returns_500_not_disconnect(self):
        """路由裸抛必须回 500 JSON——不再静默掐断连接（Failed to fetch 根因）。"""
        orig = store.active_mgmt_run
        def boom(entry_id):
            raise RuntimeError("守卫探针-预期爆炸")
        store.active_mgmt_run = boom
        try:
            st, body = _req("POST", "/api/catalog/fakeA/upgrade", {})
        finally:
            store.active_mgmt_run = orig
        self.assertEqual(st, 500, "路由裸抛应回 500，实际 %s %r" % (st, body))
        self.assertTrue(body.get("error"), "500 响应必须带 error 文案")

    def test_03_store_reachable_from_post_routes(self):
        """store 在写路由里必须可用——0.1.70 函数内 import 遮蔽致全灭的回归位。"""
        st, body = _req("POST", "/api/catalog/fakeA/upgrade", {})
        self.assertEqual(st, 200, "单条升级应正常受理，实际 %s %r" % (st, body))
        self.assertTrue(body.get("run_id"), "受理必须返回 run_id")

    def test_04_contract_openapi_health_and_remote_auth_shapes(self):
        st, body = _req("GET", "/api/openapi.json")
        self.assertEqual(st, 200)
        self.assertIn("/api/tasks/{task_id}/contract", body.get("paths", {}))
        self.assertIn("codebeeQueryToken", body.get("components", {}).get("securitySchemes", {}))

        st, body = _req("GET", "/api/health")
        self.assertEqual(st, 200)
        self.assertIsInstance(body, dict)

        import http.client
        # This server only enforces forwarded-client auth when trusted-proxy
        # mode is enabled; enable it for the explicit remote-auth assertion.
        from core import remote
        remote.set_trusted_proxy(True)
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
        try:
            conn.request("GET", "/api/state", headers={"X-Forwarded-For": "203.0.113.9"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 401)
            resp.read()
        finally:
            conn.close()
            remote.set_trusted_proxy(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
