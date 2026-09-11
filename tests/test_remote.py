# -*- coding: utf-8 -*-
"""remote 模块单元测试：访问令牌与多端控制权锁（纯内存逻辑，不起服务）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# 必须在导入 core 前把数据目录指到临时位置，避免碰真实 data/
_TMP = tempfile.mkdtemp(prefix="tutti-remote-test-")
os.environ["TUTTI_DATA"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core import remote  # noqa: E402

# discover 混跑时其他测试可能先导入了 core.paths（已绑定真实 data/），
# 直接改 remote 所见的数据目录，双保险
remote.paths.DATA_DIR = Path(_TMP)


class TokenTest(unittest.TestCase):
    def setUp(self):
        remote._TOKEN = ""  # 各用例独立加载

    def test_token_generated_and_persisted(self):
        tok = remote.token()
        self.assertTrue(tok)
        p = Path(_TMP) / "remote.json"
        self.assertTrue(p.is_file())
        self.assertEqual(remote.token(), tok)  # 二次读取同一令牌

    def test_loopback_exempt_remote_needs_token(self):
        tok = remote.token()
        self.assertTrue(remote.request_authed("127.0.0.1", "", ""))
        self.assertTrue(remote.request_authed("::1", "", ""))
        self.assertFalse(remote.request_authed("192.168.3.9", "", ""))
        self.assertFalse(remote.request_authed("192.168.3.9", "wrong", ""))
        self.assertTrue(remote.request_authed("192.168.3.9", tok, ""))
        self.assertTrue(remote.request_authed("192.168.3.9", "", tok))


class ControlLockTest(unittest.TestCase):
    def setUp(self):
        with remote._CTRL_LOCK:
            remote._CTRL.update({"client_id": "", "name": "", "expires_at": 0.0})

    def test_free_then_acquire(self):
        self.assertEqual(remote.control_view("a")["mode"], "free")
        ok, view = remote.acquire("a", "设备A")
        self.assertTrue(ok)
        self.assertTrue(view["mine"])
        self.assertEqual(view["holder"], "设备A")

    def test_second_device_denied_without_force(self):
        remote.acquire("a", "设备A")
        ok, view = remote.acquire("b", "iPhone")
        self.assertFalse(ok)
        self.assertFalse(view["mine"])
        self.assertEqual(view["holder"], "设备A")
        ok, _ = remote.acquire("b", "iPhone", force=True)  # 强抢
        self.assertTrue(ok)
        ok, view = remote.acquire("a", "设备A")  # 原持有者反被拒
        self.assertFalse(ok)

    def test_reacquire_refreshes_ttl(self):
        ok1, v1 = remote.acquire("a", "设备A")
        with remote._CTRL_LOCK:
            before = remote._CTRL["expires_at"]
        import time as _t
        _t.sleep(0.01)
        ok2, v2 = remote.acquire("a", "设备A")
        self.assertTrue(ok1 and ok2)
        with remote._CTRL_LOCK:
            self.assertGreater(remote._CTRL["expires_at"], before)

    def test_heartbeat_only_holds_for_holder(self):
        remote.acquire("a", "设备A")
        ok, view = remote.heartbeat("b")
        self.assertFalse(ok)
        ok, view = remote.heartbeat("a")
        self.assertTrue(ok)
        self.assertTrue(view["mine"])

    def test_release_and_lazy_expiry(self):
        remote.acquire("a", "设备A")
        view = remote.release("b")  # 非持有者释放无效
        self.assertEqual(view["mode"], "held")
        view = remote.release("a")
        self.assertEqual(view["mode"], "free")
        # 惰性过期：把过期时间拨到过去后视为空闲
        remote.acquire("a", "设备A")
        with remote._CTRL_LOCK:
            remote._CTRL["expires_at"] = 0.0
        self.assertEqual(remote.control_view("a")["mode"], "free")
        ok, view = remote.acquire("b", "iPhone")  # 过期后可直接接管
        self.assertTrue(ok)

    def test_anonymous_gets_stable_id_per_call(self):
        ok, _ = remote.acquire("", "")  # 无身份请求：自动补 anon id
        self.assertTrue(ok)
        self.assertTrue(remote._CTRL["client_id"].startswith("anon-"))


class ConnectUrlsTest(unittest.TestCase):
    def setUp(self):
        remote._TS_CACHE.update({"ip": "", "ts": 0.0})
        self._lan, self._ts = remote.lan_ip, remote.tailscale_ip

    def tearDown(self):
        remote.lan_ip, remote.tailscale_ip = self._lan, self._ts
        remote._TS_CACHE.update({"ip": "", "ts": 0.0})

    def test_lan_only(self):
        remote.lan_ip = lambda: "192.168.3.206"
        remote.tailscale_ip = lambda *a, **k: ""
        urls = remote.build_connect_urls(8765)
        self.assertEqual(len(urls), 1)
        self.assertIn("192.168.3.206:8765/?token=", urls[0]["url"])
        self.assertEqual(urls[0]["label"], remote.build_connect_urls(8765)[0]["label"])

    def test_tailscale_first(self):
        remote.lan_ip = lambda: "192.168.3.206"
        remote.tailscale_ip = lambda *a, **k: "100.64.0.1"
        urls = remote.build_connect_urls(8765)
        self.assertEqual(len(urls), 2)
        self.assertIn("Tailscale", urls[0]["label"])  # 外网地址排前
        self.assertTrue(urls[0]["url"].startswith("http://100.64.0.1:8765/"))

    def test_no_network(self):
        remote.lan_ip = lambda: ""
        remote.tailscale_ip = lambda *a, **k: ""
        self.assertEqual(remote.build_connect_urls(8765), [])


if __name__ == "__main__":
    unittest.main()
