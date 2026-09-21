# -*- coding: utf-8 -*-
"""portscan 单测：解析/项目归属/去重/温和关闭守卫。

跑法：python -m unittest discover -s tests -p "test_portscan.py" -v
"""
from __future__ import annotations

import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
