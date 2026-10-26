# -*- coding: utf-8 -*-
"""死本地代理自愈回归（2026-10-26 头七实案）。

实案：Windows 系统代理残留指向已退出的 Clash（注册表 ProxyServer=
127.0.0.1:7897，端口无人监听），进程内 urllib 全部 ConnectionRefused(10061)，
Bigmodel 健康探测/评测整夜全灭；子进程 CLI 继承同一代理同样全灭。供应商链
大半陪葬，评审打在未修订稿件上，任务以「评审未达标」收场——真实死因是代理。

自愈契约：
· 判死 → 剥离 os.environ 代理变量 + 接管 urllib.request.getproxies 返回空表
  （自建 opener 的 ProxyHandler 也走它，单点覆盖全部进程内 urllib）；
· 端口复活 → 原样恢复 env 与 getproxies；
· 只碰回环地址代理——非本地代理不探测不剥离；
· strip_dead 只删指向已判死端口的代理赋值键，不碰 no_proxy。
"""
import os
import unittest
import urllib.request

from app.core import netenv


class NetenvDeadProxyTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = {k: os.environ[k] for k in netenv._PROXY_ENV_KEYS
                           if k in os.environ}
        self._orig_proxies = urllib.request.getproxies()
        netenv.reset_for_tests()
        self.addCleanup(self._restore_all)

    def _restore_all(self):
        netenv.reset_for_tests()
        for k in netenv._PROXY_ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(self._saved_env)

    def _fake_sources(self, env=None, reg=None):
        return lambda: (dict(env or {}), dict(reg or {}))

    def test_dead_loopback_proxy_scrubs_env_and_urllib(self):
        netenv._proxy_sources = self._fake_sources(
            env={"https": "http://127.0.0.1:7897"},
            reg={"https": "http://127.0.0.1:7897"})
        netenv._port_alive = lambda *a, **k: False
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

        dead = netenv.ensure(force=True)

        self.assertEqual(dead, (("127.0.0.1", 7897),))
        self.assertNotIn("HTTPS_PROXY", os.environ)
        # getproxies 被接管：任何自建 opener 都不再读注册表/env 代理
        self.assertEqual(urllib.request.getproxies(), {})
        self.assertIn(("127.0.0.1", 7897), netenv.dead_targets())
        self.assertIn("127.0.0.1:7897", netenv.note())

    def test_revived_proxy_restores_env_and_urllib(self):
        netenv._proxy_sources = self._fake_sources(
            reg={"https": "http://127.0.0.1:7897"})
        netenv._port_alive = lambda *a, **k: False
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
        netenv.ensure(force=True)
        self.assertEqual(urllib.request.getproxies(), {})

        netenv._port_alive = lambda *a, **k: True
        netenv.ensure(force=True)

        self.assertEqual(netenv.dead_targets(), ())
        self.assertEqual(os.environ.get("HTTPS_PROXY"),
                         "http://127.0.0.1:7897")
        # 复活后 getproxies 归还原函数：env 代理重新生效（stdlib 语义 env
        # 优先于注册表，这里读到的应是 env 里刚恢复的值而非空表）
        self.assertFalse(netenv._STATE["patched"])
        self.assertTrue(urllib.request.getproxies())

    def test_nonloopback_proxy_is_never_touched(self):
        netenv._proxy_sources = self._fake_sources(
            env={"https": "http://10.0.0.9:8888"})
        netenv._port_alive = lambda *a, **k: False

        dead = netenv.ensure(force=True)

        self.assertEqual(dead, ())
        self.assertFalse(netenv._STATE["patched"])
        self.assertEqual(netenv.dead_targets(), ())

    def test_strip_dead_only_removes_dead_targets(self):
        netenv._proxy_sources = self._fake_sources(
            reg={"https": "http://127.0.0.1:7897"})
        netenv._port_alive = lambda *a, **k: False
        netenv.ensure(force=True)

        env = {"HTTPS_PROXY": "http://127.0.0.1:7897",
               "HTTP_PROXY": "http://127.0.0.1:9999",
               "https_proxy": "http://localhost:7897",
               "NO_PROXY": "a,b"}
        netenv.strip_dead(env)

        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("https_proxy", env)
        # 端口不同/未判死的一律保留；no_proxy 永不删
        self.assertEqual(env.get("HTTP_PROXY"), "http://127.0.0.1:9999")
        self.assertEqual(env.get("NO_PROXY"), "a,b")


if __name__ == "__main__":
    unittest.main()
