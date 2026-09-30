# -*- coding: utf-8 -*-
"""resolve_command 兜底探测 + 启动失败人话报错（2026-09-30 claude winget 迁移实案）：
CLI 装在服务起跑之后，服务进程 PATH 快照过旧 → which 落空 → 裸名直传
Popen 直接 FileNotFoundError(filename=None)，报错只有一句「系统找不到指定的文件」。
修 = which 落空后探测 winget Links/Packages、~/.local/bin、npm 全局垫片；
仍找不到时错误带目标名与「重启服务让 PATH 生效」指引。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from base import BaseTest
from app.core import runner


class TestResolveCommandFallback(BaseTest):

    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("PATH", "LOCALAPPDATA", "APPDATA", "USERPROFILE")}
        self.tmp = Path(tempfile.mkdtemp(prefix="tutti_resolve_"))
        # PATH 剥空逼 which 落空；三个家目录全指临时盘，探测只看得到夹具
        os.environ["PATH"] = ""
        for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
            os.environ[key] = str(self.tmp)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _fake_bin(self, rel, name):
        p = self.tmp / rel / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"stub")
        return p

    def test_fallback_winget_links_exe(self):
        self._fake_bin("Microsoft/WinGet/Links", "probecli.exe")
        argv = runner.resolve_command("probecli")
        self.assertEqual(len(argv), 1)
        self.assertEqual(argv[0].lower(), str(self.tmp / "Microsoft/WinGet/Links/probecli.exe").lower())

    def test_fallback_local_bin_cmd_shim_wrapped(self):
        self._fake_bin(".local/bin", "probecli.cmd")
        argv = runner.resolve_command("probecli")
        self.assertEqual(argv[0], "cmd")
        self.assertEqual(argv[1], "/c")
        self.assertEqual(argv[2].lower(), str(self.tmp / ".local/bin/probecli.cmd").lower())

    def test_fallback_npm_bat(self):
        # 探测拼的是 %APPDATA%\npm；夹具直接放 <tmp>\npm（APPDATA 已指 <tmp>）
        self._fake_bin("npm", "probecli.bat")
        argv = runner.resolve_command("probecli")
        self.assertEqual(argv[0], "cmd")
        self.assertEqual(argv[2].lower(), str(self.tmp / "npm/probecli.bat").lower())

    def test_fallback_winget_packages_one_level_glob(self):
        self._fake_bin("Microsoft/WinGet/Packages/Vendor.Probe_XYZ", "probecli.EXE")
        argv = runner.resolve_command("probecli")
        self.assertEqual(len(argv), 1)
        self.assertTrue(argv[0].lower().endswith("probecli.exe"))

    def test_no_hit_keeps_bare_name_contract(self):
        self.assertEqual(runner.resolve_command("probecli"), ["probecli"])

    def test_pathed_name_skips_probe(self):
        argv = runner.resolve_command("some/dir/probecli.exe")
        self.assertEqual(argv, ["some/dir/probecli.exe"])

    def test_which_hit_beats_fallback(self):
        # PATH 上能找到时行为与旧契约完全一致（.cmd 包 cmd /c）
        shim = self._fake_bin("pathdir", "probecli.cmd")
        os.environ["PATH"] = str(self.tmp / "pathdir")
        argv = runner.resolve_command("probecli")
        self.assertEqual(argv[0], "cmd")
        self.assertEqual(argv[2].lower(), str(shim).lower())


class TestLaunchFailureMessage(BaseTest):

    def test_file_not_found_friendly(self):
        res = runner.run_process(
            argv=[r"Z:\tutti_no_such_dir_9x7\tutti_missing_cli.exe", "--version"])
        self.assertFalse(res["ok"])
        self.assertIsNone(res["exit_code"])
        self.assertIn("找不到可执行文件", res["stderr"])
        self.assertIn("tutti_missing_cli.exe", res["stderr"])
        self.assertIn("重启", res["stderr"])


if __name__ == "__main__":
    unittest.main()
