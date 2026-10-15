# -*- coding: utf-8 -*-
"""自更新模块单测：模式判定 / 版本比较 / 发布名一致性 / 升级门控 / 重启参数校验。

不真跑 npm（网络+全局安装副作用），只测纯逻辑与防御分支。
"""
from __future__ import annotations

import json
import os
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

        # 7) 升级走 registry 直传，不调用 npm/node。
        with mock.patch.object(su, "_direct_upgrade", return_value={
                "ok": True, "exit_code": 0, "stdout": "", "stderr": ""}) as direct:
            res = su.run_upgrade("r-test", None)
        self.assertTrue(res["ok"])
        direct.assert_called_once_with(None)
        # 8) relaunch 同理：新实例 cwd 不落包内，且脚本用绝对路径（cwd 已不再是 APP_DIR）
        class _FakePopen:
            def __init__(self, args, **kw):
                popen_kw["args"] = args
                popen_kw["cwd"] = kw.get("cwd")

        popen_kw = {}
        orig_popen = su.subprocess.Popen
        su.subprocess.Popen = _FakePopen
        try:
            # 011023c/防毒闸延伸：测试实例（TUTTI_DATA 在临时目录）拒绝 relaunch——
            # 真实执行过 relaunch(8765)，启动清场把生产服务当旧实例杀掉后鸠占鹊巢
            # （2026-09-23 实案：8765 被测试服务占用，用户真实数据消失）
            self.assertFalse(su.relaunch(8765))
            # 生产形态（清掉 TUTTI_DATA）：闸放行，验证拉起参数
            saved_td = os.environ.pop("TUTTI_DATA", None)
            try:
                self.assertTrue(su.relaunch(8765))
            finally:
                if saved_td is not None:
                    os.environ["TUTTI_DATA"] = saved_td
        finally:
            su.subprocess.Popen = orig_popen
        cwd2 = Path(popen_kw["cwd"]).resolve()
        root = su.paths.ROOT.resolve()
        self.assertFalse(cwd2 == root or root in cwd2.parents)
        self.assertTrue(Path(popen_kw["args"][1]).is_absolute())
        self.assertTrue(str(popen_kw["args"][1]).endswith("main.py"))

    def test_direct_upgrade_failure_is_reported(self):
        """内置 registry 通道失败时保留可读错误。"""
        import app.core.selfupdate as su
        with mock.patch.object(su, "_direct_upgrade", return_value={
                "ok": False, "exit_code": 1, "stdout": "",
                "stderr": "内置升级失败：网络不可用"}):
            result = su.run_upgrade("r-direct-fail", None)
        self.assertFalse(result["ok"])
        self.assertIn("网络不可用", result["error"])

    def test_ebusy_retry_and_friendly_error(self):
        """保留历史 npm 占用错误映射测试，验证兼容辅助函数不回归。"""
        import app.core.selfupdate as su
        ebusy = "npm error EBUSY: resource busy or locked"
        self.assertTrue(su._locked_error({"stderr": ebusy, "stdout": ""}))
        self.assertFalse(su._locked_error({"stderr": "network timeout", "stdout": ""}))
    def test_missing_npm_error_is_actionable(self):
        """Win7 的 npm 缺失时，版本页给安装和重启指引而不是裸 cmd 错误。"""
        import app.core.selfupdate as su
        raw = "'npm' 不是内部或外部命令，也不是可运行的程序或批处理文件。"
        hint = su._npm_missing_error(raw)
        self.assertIn("安装 Node.js", hint)
        self.assertIn("重启 CodeBee", hint)
        self.assertEqual("", su._npm_missing_error("npm error network timeout"))

        with mock.patch.object(su, "_direct_npm_meta",
                                  return_value=("", "", "", "", "offline")):
            latest, notes, error = su._npm_meta()

        self.assertEqual((latest, notes), ("", ""))
        self.assertIn("内置 registry", error)

    def test_missing_npm_uses_builtin_upgrade_channel(self):
        """npm 缺失时升级本身切到内置下载，不要求用户另装 Node.js。"""
        import app.core.selfupdate as su
        from app.core import runner as _runner

        raw = "启动失败：找不到可执行文件「npm」。CLI 未安装或已卸载"
        with mock.patch.object(_runner, "run_process", return_value={
                "ok": False, "exit_code": None, "stdout": "", "stderr": raw,
                "cancelled": False}), \
                mock.patch.object(su, "_direct_upgrade", return_value={
                    "ok": True, "exit_code": 0, "stdout": "", "stderr": ""}) as direct:
            result = su.run_upgrade("r-direct", None)

        self.assertTrue(result["ok"])
        direct.assert_called_once_with(None)

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

    def test_busy_upgrade_explains_concurrency_limit(self):
        import app.core.selfupdate as su
        from app.core import jobs, store

        run = {"id": "m-busy"}
        reason = "当前运行任务已达并发保护上限（6）；本次未排队，请稍后重试"
        with mock.patch.object(su, "install_mode", return_value="npm"), \
             mock.patch.object(store, "create_run", return_value=run), \
             mock.patch.object(jobs, "enqueue", side_effect=jobs.JobsBusyError(reason)), \
             mock.patch.object(store, "update_run") as update_run:
            result = su.apply_upgrade()

        self.assertIn("并发保护上限（6）", result["error"])
        self.assertEqual(update_run.call_args.kwargs["error"], result["error"])

    def test_full_capacity_rejects_upgrade_without_creating_failed_run(self):
        import app.core.selfupdate as su
        from app.core import jobs, store

        with mock.patch.object(su, "install_mode", return_value="npm"), \
             mock.patch.object(jobs, "capacity_status", return_value={
                 "accepting": False, "active": 6, "limit": 6, "restarting": False}), \
             mock.patch.object(store, "create_run") as create_run:
            result = su.apply_upgrade()

        self.assertIn("升级未启动且不会排队", result["error"])
        create_run.assert_not_called()

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


class TestDesktopUpdateCheck(BaseTest):
    """桌面模式「关于与更新」：查新照常、检出即给下载页，apply/restart 恒拒。"""

    def test_desktop_check_detects_update_and_reports_download_url(self):
        import app.core.selfupdate as su
        cases = [("9.9.9", "notes-body", True), ("0.0.1", "", False)]
        for latest, notes, expected in cases:
            with mock.patch.dict(os.environ, {"CODEBEE_DESKTOP": "1"}), \
                    mock.patch.object(su, "_npm_meta",
                                      return_value=(latest, notes, "")), \
                    mock.patch.object(su, "_CHECK_CACHE",
                                      {"result": None, "ts": 0.0}):
                info = su.check()
            self.assertEqual(info["mode"], "desktop")
            self.assertIs(info["has_update"], expected)
            self.assertEqual(info["latest"], latest)
            self.assertIn("/actions/workflows/desktop-preview.yml", info["download_url"])
            if expected:
                self.assertIn("下载页", info["note"])
            else:
                self.assertEqual(info["note"], "")
        # 查到新版也不给自动升级/重启：桌面壳独占引擎生命周期。
        with mock.patch.dict(os.environ, {"CODEBEE_DESKTOP": "1"}):
            self.assertIn("error", su.apply_upgrade())
            self.assertFalse(su.relaunch(8765))

    def test_desktop_check_offline_gate_reports_error_note(self):
        import app.core.selfupdate as su
        with mock.patch.dict(os.environ, {"CODEBEE_DESKTOP": "1",
                                          "TUTTI_SELFUPDATE_OFFLINE": "1"}), \
                mock.patch.object(su, "_CHECK_CACHE", {"result": None, "ts": 0.0}):
            info = su.check()
        self.assertEqual(info["mode"], "desktop")
        self.assertFalse(info["has_update"])
        self.assertIn("/actions/workflows/desktop-preview.yml", info["download_url"])
        self.assertIn("查询新版本失败", info["note"])

