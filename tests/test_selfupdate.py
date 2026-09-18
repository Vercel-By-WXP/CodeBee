# -*- coding: utf-8 -*-
"""自更新模块单测：模式判定 / 版本比较 / 发布名一致性 / 升级门控 / 重启参数校验。

不真跑 npm（网络+全局安装副作用），只测纯逻辑与防御分支。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from base import BaseTest


class TestSelfUpdate(BaseTest):
    def runTest(self):
        import app.core.selfupdate as su

        # 0) 发布名一致性：_PKG_NAME 必须等于 package.json 的 name（防品牌重命名漏改）
        pkg = json.loads((Path(__file__).resolve().parents[1] / "package.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(su._PKG_NAME, pkg["name"])
        # 版本号合法 semver
        self.assertRegex(pkg["version"], r"^\d+\.\d+\.\d+")

        # 1) 安装模式：本测试环境是开发仓库 → repo（永不自动升级）
        self.assertEqual(su.install_mode(), "repo")
        info = su.check()
        self.assertFalse(info["has_update"])
        self.assertIn("git pull", info["note"])

        # 2) repo 模式拒绝自动升级（门控）
        res = su.apply_upgrade()
        self.assertIn("error", res)

        # 3) 版本比较：semver 元组逐段比（0.1.9 < 0.1.10 < 0.2.0）
        self.assertGreater(su._ver_tuple("0.1.10"), su._ver_tuple("0.1.9"))
        self.assertGreater(su._ver_tuple("0.2.0"), su._ver_tuple("0.1.10"))

        # 4) package_version 能读到真实版本
        self.assertEqual(su.package_version(), pkg["version"])

        # 5) relaunch 端口校验（非法端口拒绝，不拉进程）
        for bad in (0, -1, 70000):
            self.assertFalse(su.relaunch(bad))

        # 6) _port_free：未占用端口为 True（1 端口几乎不可能被我们监听）
        self.assertTrue(su._port_free(1))

        # 7) 升级命令 cwd 必须钉在包外：Windows 上 npm 换版本靠整体改名包目录，
        #    cwd 在包内 = 目录被自身占用，rename 必 EBUSY（真实事故：升级 9 秒即挂）
        from app.core import runner as _runner
        captured = {}

        def _fake_run_process(argv=None, cwd=None, timeout=None,
                              log_path=None, **kw):
            captured["argv"] = argv
            captured["cwd"] = cwd
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "",
                    "timed_out": False, "cancelled": False}

        orig_run = _runner.run_process
        _runner.run_process = _fake_run_process
        try:
            res = su.run_upgrade("r-test", None)
        finally:
            _runner.run_process = orig_run
        self.assertTrue(res["ok"])
        self.assertIn("install", captured["argv"])
        root = su.paths.ROOT.resolve()
        cwd = Path(captured["cwd"]).resolve()
        self.assertFalse(cwd == root or root in cwd.parents,
                         "npm 的 cwd 不得落在包目录内（Windows rename EBUSY）")

        # 8) relaunch 同理：新实例 cwd 不落包内，且脚本用绝对路径（cwd 已不再是 APP_DIR）
        class _FakePopen:
            def __init__(self, args, **kw):
                popen_kw["args"] = args
                popen_kw["cwd"] = kw.get("cwd")

        popen_kw = {}
        orig_popen = su.subprocess.Popen
        su.subprocess.Popen = _FakePopen
        try:
            self.assertTrue(su.relaunch(8765))
        finally:
            su.subprocess.Popen = orig_popen
        cwd2 = Path(popen_kw["cwd"]).resolve()
        self.assertFalse(cwd2 == root or root in cwd2.parents)
        self.assertTrue(Path(popen_kw["args"][1]).is_absolute())
        self.assertTrue(str(popen_kw["args"][1]).endswith("main.py"))

    def test_enqueue_failure_closes_upgrade_run(self):
        import app.core.selfupdate as su
        from app.core import jobs, store

        run = {"id": "m-test"}
        with mock.patch.object(su, "install_mode", return_value="npm"), \
             mock.patch.object(store, "create_run", return_value=run), \
             mock.patch.object(jobs, "enqueue", side_effect=RuntimeError("private worker path")), \
             mock.patch.object(store, "update_run") as update_run:
            result = su.apply_upgrade()

        self.assertEqual(result["run_id"], "m-test")
        self.assertNotIn("private worker path", result["error"])
        update_run.assert_called_once()
        self.assertEqual(update_run.call_args.kwargs["status"], "failed")
