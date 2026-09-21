# -*- coding: utf-8 -*-
"""portscan 单测：解析/项目归属/去重/温和关闭守卫。

跑法：python -m unittest discover -s tests -p "test_portscan.py" -v
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest.mock as mock
from pathlib import Path

from base import BaseTest

from app.core import portscan


class PortscanBase(BaseTest):
    def _proj(self, name):
        p = Path(tempfile.mkdtemp(prefix="orch-ps-")) / name
        p.mkdir(parents=True)
        return p


class TestProjectFromCwd(PortscanBase):
    def test_finds_git_root(self):
        proj = self._proj("myproj")
        sub = proj / "src" / "deep"
        sub.mkdir(parents=True)
        (proj / ".git").mkdir()
        self.assertEqual(portscan._project_from_cwd(str(sub)), "myproj")

    def test_no_marker_returns_empty(self):
        self.assertEqual(portscan._project_from_cwd(str(self.workdir)), "")

    def test_empty_cwd(self):
        self.assertEqual(portscan._project_from_cwd(""), "")
        self.assertEqual(portscan._project_from_cwd(None), "")

    def test_prefers_deepest_marker(self):
        outer = self._proj("monorepo")
        inner = outer / "svc"
        deep = inner / "lib"
        deep.mkdir(parents=True)
        (outer / ".git").mkdir()
        (inner / "package.json").write_text("{}", encoding="utf-8")
        self.assertEqual(portscan._project_from_cwd(str(deep)), "svc")


class TestParseSs(BaseTest):
    def test_parse_ss_output(self):
        out = "\n".join([
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port",
            "LISTEN 0      128        127.0.0.1:8765      0.0.0.0:*",
            "LISTEN 0      128          0.0.0.0:9353        0.0.0.0:*",
        ])
        ports = portscan._parse_ss(out)
        self.assertEqual([p["port"] for p in ports], [8765, 9353])
        self.assertTrue(ports[0]["local_only"])
        self.assertFalse(ports[1]["local_only"])

    def test_dedup_keeps_pid_entry(self):
        out = "\n".join([
            'LISTEN 0 128 0.0.0.0:8765 0.0.0.0:*',
            'LISTEN 0 128 127.0.0.1:8765 0.0.0.0:* users:(("py",pid=42))',
        ])
        ports = portscan._parse_ss(out)
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0]["pid"], 42)


class TestClosePortGuard(BaseTest):
    def test_missing_port_refused(self):
        ok, msg = portscan.close_port(64999)
        self.assertFalse(ok)
        self.assertIn("64999", msg)

    def test_listening_ports_smoke(self):
        # 真机冒烟：不崩、返回 list、字段齐
        ports = portscan.listening_ports()
        self.assertIsInstance(ports, list)
        if ports:
            self.assertIn("port", ports[0])
            self.assertIn("pid", ports[0])
            self.assertIn("local_only", ports[0])


class TestClosePortRealKill(PortscanBase):
    """真行为回归（2026-09-21 用户实测「扫描端口后关不掉」）：

    旧实现 Windows 走 taskkill 不带 /F（只发 WM_CLOSE，控制台进程无效），
    且不检查返回码直接报「已发送」——假成功，端口照样被监听。
    此测试要求 close_port 后端口必须真的释放。"""

    def _spawn_listener(self):
        script = (
            "import socket, sys, time\n"
            "s = socket.socket()\n"
            "s.bind(('127.0.0.1', 0))\n"
            "s.listen(50)\n"
            "sys.stdout.write(str(s.getsockname()[1]))\n"
            "sys.stdout.flush()\n"
            "time.sleep(300)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", script],
                                stdout=subprocess.PIPE)
        port = int(proc.stdout.readline().strip() or 0)
        self.assertTrue(port, "子进程未报告监听端口")
        return proc, port

    def _can_connect(self, port):
        """connect 探针：连得上=端口仍活着（用户视角），拒绝=真释放。"""
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return True
        except OSError:
            return False

    def _wait_connectable(self, port, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if self._can_connect(port):
                return True
            time.sleep(0.25)
        return False

    def test_close_port_releases_real_listener(self):
        proc, port = self._spawn_listener()
        try:
            if not self._wait_connectable(port):
                # 本环境安全层会拦截/清除子进程回环监听（实测 connect 全
                # timeout、子进程提前消失）；逻辑契约由 TestClosePortLogic 保底
                self.skipTest("环境拦截子进程回环监听（安全软件），跳过真实行为测试")
            ok, msg = portscan.close_port(port)
            self.assertTrue(ok, msg)
            # 终裁：端口必须真的连不上了（旧实现假成功，进程未死时此断言必挂）
            end = time.time() + 6
            while self._can_connect(port) and time.time() < end:
                time.sleep(0.25)
            self.assertFalse(self._can_connect(port),
                             "close_port 报成功但端口仍可连接（进程未死，假成功）")
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()


class TestClosePortLogic(PortscanBase):
    """close_port 编排契约（mock 扫描与信号；真机安全软件拦子进程监听，
    逻辑测试保底）。2026-09-21「扫描端口后关不掉」修复：

    温和信号 → 验证端口真释放 → 温和无效升级强杀（杀前重验防 PID 复用）
    → 终裁仍以释放为准，绝不假报成功。"""

    PORT, PID = 54321, 4242

    @staticmethod
    def _rows(port, pid, name="evilapp"):
        return [{"port": port, "pid": pid, "process": name,
                 "project": "", "local_only": True}]

    class _Clock:
        def __init__(self): self.now = 1000.0
        def time(self): return self.now
        def sleep(self, s): self.now += s

    class _Ret:
        returncode = 0

    def _close(self, rows_state, taskkill_side, kill_log):
        """在 mock 下跑 close_port。rows_state["rows"] 为当前扫描结果；
        taskkill_side(cmd) 处理 taskkill 调用；kill_log 收 (pid, sig)。"""
        clock = self._Clock()
        with mock.patch.object(portscan, "listening_ports",
                               side_effect=lambda with_names=True: list(rows_state["rows"])), \
             mock.patch.object(portscan, "time", clock), \
             mock.patch.object(portscan.subprocess, "run", side_effect=taskkill_side), \
             mock.patch.object(portscan.os, "kill",
                               side_effect=lambda pid, sig: kill_log.append((pid, sig))):
            return portscan.close_port(self.PORT)

    def test_gentle_signal_releases(self):
        state = {"rows": self._rows(self.PORT, self.PID)}
        calls = {"gentle": 0, "force": 0}

        def taskkill(cmd, **kw):
            if "/F" in cmd:
                calls["force"] += 1
            else:
                calls["gentle"] += 1
                state["rows"] = []          # 温和信号生效，进程退出
            return self._Ret()

        ok, msg = self._close(state, taskkill, [])
        self.assertTrue(ok, msg)
        self.assertIn("已释放", msg)
        self.assertEqual(calls["gentle"], 1)
        self.assertEqual(calls["force"], 0)

    def test_gentle_ignored_upgrades_to_force(self):
        state = {"rows": self._rows(self.PORT, self.PID)}
        calls = {"gentle": 0, "force": 0}

        def taskkill(cmd, **kw):
            if "/F" in cmd:
                calls["force"] += 1
                state["rows"] = []          # 强杀生效
            else:
                calls["gentle"] += 1        # 温和信号被无视，仍监听
            return self._Ret()

        ok, msg = self._close(state, taskkill, [])
        self.assertTrue(ok, msg)
        self.assertIn("强制结束", msg)
        self.assertEqual(calls["force"], 1)

    def test_stubborn_process_reports_failure_not_fake_success(self):
        state = {"rows": self._rows(self.PORT, self.PID)}   # 杀了也不退

        def taskkill(cmd, **kw):
            return self._Ret()

        ok, msg = self._close(state, taskkill, [])
        self.assertFalse(ok)
        self.assertIn("仍被监听", msg)

    def test_guardrails(self):
        kill_log = []
        state = {"rows": self._rows(self.PORT, 4, name="system")}
        ok, msg = self._close(state, lambda cmd, **kw: self._Ret(), kill_log)
        self.assertFalse(ok)
        self.assertIn("系统进程", msg)
        state = {"rows": self._rows(self.PORT, os.getpid())}
        ok, msg = self._close(state, lambda cmd, **kw: self._Ret(), kill_log)
        self.assertFalse(ok)
        self.assertIn("自身", msg)

if __name__ == "__main__":
    unittest.main()
