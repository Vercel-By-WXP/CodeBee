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
    # 注意：类里一旦存在任何 test_ 方法，unittest discovery 就不再收集 runTest
    # （loadTestsFromTestCase 只在 test_ 名单为空时才回退到 runTest）——核心守卫
    # 必须挂在 test_ 前缀方法下，否则整组断言静默失跑。
    def test_core_guards(self):
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

    def test_ebusy_retry_and_friendly_error(self):
        """EBUSY/EPERM（包目录被占用）自动重试 + 人话错误映射；其他错误不重试。"""
        import tempfile
        import app.core.selfupdate as su
        from app.core import runner as _runner

        ebusy = ("npm error code EBUSY\nnpm error syscall rename\n"
                 "npm error EBUSY: resource busy or locked, rename "
                 "'D:\\x\\node_modules\\codebee' -> 'D:\\x\\node_modules\\.cb-1'\n")
        calls, sleeps = [], []

        def _fake(argv=None, cwd=None, timeout=None, log_path=None, **kw):
            calls.append(log_path)
            if log_path:
                with open(log_path, "ab") as fh:
                    fh.write(ebusy.encode("utf-8"))
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": ebusy,
                    "timed_out": False, "cancelled": False}

        def _ok_after_retries(argv=None, cwd=None, timeout=None, log_path=None, **kw):
            calls.append(log_path)
            done = len(calls) >= 3
            return {"ok": done, "exit_code": 0,
                    "stdout": "", "stderr": "" if done else ebusy,
                    "timed_out": False, "cancelled": False}

        with mock.patch.object(su.time, "sleep", side_effect=sleeps.append):
            # a) 前两次 EBUSY、第三次成功：共 3 次 npm，两次退避 5s/15s，日志有重试说明
            log1 = Path(tempfile.mkdtemp(dir=str(self.tmp))) / "a.log"
            calls.clear()
            with mock.patch.object(_runner, "run_process", side_effect=_ok_after_retries):
                su._CHECK_CACHE["result"] = {"stale": True}
                res = su.run_upgrade("r1", str(log1))
            self.assertTrue(res["ok"])
            self.assertEqual(len(calls), 3)
            self.assertEqual(sleeps, [5, 15])
            self.assertIsNone(su._CHECK_CACHE["result"])  # 成功后查新缓存已过期
            log_text = log1.read_text(encoding="utf-8")
            self.assertIn("第 1/2 次", log_text)
            self.assertIn("第 2/2 次", log_text)

            # b) 三连 EBUSY：不 ok，错误是人话结论（300 字内可见），且非裸 npm 输出
            log2 = Path(tempfile.mkdtemp(dir=str(self.tmp))) / "b.log"
            calls.clear()
            with mock.patch.object(_runner, "run_process", side_effect=_fake):
                res = su.run_upgrade("r2", str(log2))
            self.assertFalse(res["ok"])
            self.assertEqual(len(calls), 3)
            self.assertIn("被其他程序占用", res["error"])
            self.assertIn("已自动重试 2 次", res["error"])
            self.assertNotIn("npm error", res["error"][:300])  # 裸输出不进摘要头部

            # c) 非占用类失败（如网络）：不重试，错误透传原始输出
            net_err = "npm error network request to https://registry.npmjs.org failed"
            calls.clear()

            def _net_fail(argv=None, cwd=None, timeout=None, log_path=None, **kw):
                calls.append(log_path)
                return {"ok": False, "exit_code": 1, "stdout": "", "stderr": net_err,
                        "timed_out": False, "cancelled": False}

            with mock.patch.object(_runner, "run_process", side_effect=_net_fail):
                res = su.run_upgrade("r3", None)
            self.assertFalse(res["ok"])
            self.assertEqual(len(calls), 1)
            self.assertIn("network", res["error"])
            self.assertNotIn("被其他程序占用", res["error"])

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

    def test_auto_relaunch_requires_changed_version_and_idle_workers(self):
        import app.core.selfupdate as su
        from app.core import jobs

        su._PENDING_PORT = 8765
        with mock.patch.object(su, "package_version", return_value="0.1.24"), \
             mock.patch.object(jobs, "_alive", 2), \
             mock.patch.object(su, "relaunch") as relaunch:
            su._maybe_auto_relaunch("0.1.23", None)
        relaunch.assert_not_called()

        started = []

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                started.append(True)
                self.target()

        with mock.patch.object(su, "package_version", return_value="0.1.24"), \
             mock.patch.object(jobs, "_alive", 0), \
             mock.patch("threading.Thread", ImmediateThread), \
             mock.patch.object(su.time, "sleep"), \
             mock.patch.object(su, "relaunch", return_value=True) as relaunch, \
             mock.patch.object(su, "self_quit") as self_quit:
            su._maybe_auto_relaunch("0.1.23", None)
        self.assertEqual(started, [True])
        relaunch.assert_called_once_with(8765)
        self_quit.assert_called_once_with()
        su._PENDING_PORT = None
