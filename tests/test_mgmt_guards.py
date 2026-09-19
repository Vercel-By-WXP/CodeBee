# -*- coding: utf-8 -*-
"""目录页管理操作防线回归（2026-09-18 dsh/codex/aider 三案定版）：

① 升级/安装成功作废更新检查缓存并后台复检——防「假新版本」徽章诱导同版本
   重装（重装撞 EBUSY 文件锁，dsh 案诱因）。
② 同条目去重闸数据源 active_mgmt_run + queued mgmt 启动收尸——防同名全局
   npm 双开互锁成双僵尸（codex 案）。
③ EBUSY/EPERM 文件锁失败识别——给人话结论并跳过 AI 修复（修复智能体面对
   文件锁只会给白名单必拒的 taskkill 危险命令）。
④ AI 修复白名单含 uv tool install，提示词前缀清单与白名单同源不漂移。
⑤ aider 渠道迁移 uv tool：存量 pip 形态幂等修正 + uv 卸载命令推导。
"""
from __future__ import annotations

import threading
import time

from base import BaseTest


class TestRepairWhitelist(BaseTest):

    def test_uv_prefix_allowed(self):
        """uv tool install 前缀放行（aider 迁 uv 后修复建议的合法形态）。"""
        from app.core import jobs
        self.assertTrue(jobs._repair_command_allowed(
            "uv tool install --python 3.12 aider-chat"))

    def test_compound_command_rejected(self):
        """&& 复合命令拒绝（防注入；2026-09-18 aider 修复建议实拒案例）。"""
        from app.core import jobs
        self.assertFalse(jobs._repair_command_allowed(
            "py -3.13 -m pip install -U pip setuptools wheel"
            " && py -3.13 -m pip install -U aider-chat"))

    def test_taskkill_rejected(self):
        """taskkill 开头拒绝——全杀 node.exe 会连坐 CodeBee 自身服务。"""
        from app.core import jobs
        self.assertFalse(jobs._repair_command_allowed(
            "taskkill /F /IM node.exe 2>nul & npm install -g x"))

    def test_prompt_allowlist_in_sync(self):
        """提示词里的前缀约束与白名单同源生成，永不漂移。"""
        from app.core import jobs
        self.assertIn("__ALLOW__", jobs.AI_REPAIR_PROMPT)
        rendered = jobs.AI_REPAIR_PROMPT.replace("__ALLOW__", " / ".join(
            x.strip() for x in jobs.AI_REPAIR_ALLOW))
        for p in jobs.AI_REPAIR_ALLOW:
            self.assertIn(p.strip(), rendered)

    def test_platform_allowlists(self):
        """白名单按平台取渠道：Mac 放行 brew/python3、拒 winget/py 启动器。"""
        from app.core import jobs
        self.assertTrue(jobs._repair_command_allowed(
            "brew install node", platform="darwin"))
        self.assertTrue(jobs._repair_command_allowed(
            "python3 -m pip install -U x", platform="darwin"))
        self.assertFalse(jobs._repair_command_allowed(
            "winget install x", platform="darwin"))
        self.assertFalse(jobs._repair_command_allowed(
            "py -3.13 -m pip install x", platform="darwin"))
        # Windows 侧维持原表
        self.assertTrue(jobs._repair_command_allowed(
            "winget install x", platform="win32"))
        self.assertTrue(jobs._repair_command_allowed(
            "py -3.13 -m pip install x", platform="win32"))

    def test_prompt_os_placeholder(self):
        """提示词平台占位符按平台填充（不再写死 Windows）。"""
        from app.core import jobs
        self.assertIn("__OS__", jobs.AI_REPAIR_PROMPT)
        self.assertEqual(jobs._repair_os_label("win32"), "Windows")
        self.assertEqual(jobs._repair_os_label("darwin"), "macOS")


class TestFileLockError(BaseTest):

    def test_ebusy_detected(self):
        from app.core import jobs
        self.assertTrue(jobs._file_lock_error(
            {"error": "npm error code EBUSY\nnpm error syscall copyfile 'a.node' -> 'b.node'"}))

    def test_eperm_detected(self):
        from app.core import jobs
        self.assertTrue(jobs._file_lock_error(
            {"error": "EPERM: operation not permitted, unlink 'libvips-42.dll'"}))

    def test_other_errors_not_flagged(self):
        from app.core import jobs
        self.assertFalse(jobs._file_lock_error({"error": "npm error 404 Not Found"}))
        self.assertFalse(jobs._file_lock_error({}))
        self.assertFalse(jobs._file_lock_error(None))


class TestActiveMgmtRun(BaseTest):

    def test_finds_active_then_released(self):
        """queued/running 命中；终态后闸门释放。"""
        from app.core import store
        run = store.create_run("mgmt", "升级 Codex CLI", entry_id="codex-cli", op="upgrade")
        act = store.active_mgmt_run("codex-cli")
        self.assertIsNotNone(act, "queued 的 mgmt run 必须被去重闸看到")
        self.assertEqual(act["id"], run["id"])
        store.update_run(run["id"], status="running")
        self.assertIsNotNone(store.active_mgmt_run("codex-cli"))
        store.update_run(run["id"], status="done", ended_at="t")
        self.assertIsNone(store.active_mgmt_run("codex-cli"), "终态 run 不得再堵闸门")

    def test_ignores_other_entry_and_kind(self):
        from app.core import store
        run = store.create_run("mgmt", "升级 Aider", entry_id="aider", op="upgrade")
        store.update_run(run["id"], status="running")
        self.assertIsNone(store.active_mgmt_run("codex-cli"), "别的条目不串闸")
        store.create_run("orchestration", "连载任务", task_id="t1")
        self.assertIsNone(store.active_mgmt_run("t1"), "编排 run 不是管理操作")


class TestRecoverInterruptedMgmt(BaseTest):

    def test_queued_mgmt_recovered(self):
        """重启后 queued 的 mgmt run 无人认领（job 队列在内存），收尸为 failed。"""
        from app.core import store
        run = store.create_run("mgmt", "升级 X", entry_id="a", op="upgrade")
        self.assertEqual(store.recover_interrupted_mgmt(), 1)
        self.assertEqual(store.get_run(run["id"])["status"], "failed")

    def test_queued_orchestration_untouched(self):
        """通用恢复把 queued 留给连载续跑（test_keeps_queued_run 钉住的行为），
        mgmt 专用收尸不得越界碰编排 run。"""
        from app.core import store
        run = store.create_run("orchestration", "连载", task_id="t1")
        self.assertEqual(store.recover_interrupted_mgmt(), 0)
        self.assertEqual(store.get_run(run["id"])["status"], "queued")


class TestUpdateCacheInvalidation(BaseTest):
    """修1 核心：安装/升级成功 → 缓存作废 + 后台复检；失败不动缓存。"""

    def _entry(self):
        return {"id": "fake-cli", "name": "Fake CLI",
                "install": "python -c 1", "upgrade": "python -c 1"}

    def _patch_runner(self, manager, ok, stderr=""):
        manager.runner.run_process = lambda **kw: {
            "ok": ok, "exit_code": 0 if ok else 1, "stdout": "", "stderr": stderr}
        manager.detect_all = lambda force=False: {}

    def test_success_pops_cache_and_rechecks(self):
        from app.core import manager
        entry = self._entry()
        done = threading.Event()
        with manager._LOCK:
            manager._UPDATE_CACHE["fake-cli"] = (time.time(), {"updatable": True})
        orig_rp, orig_detect, orig_check = (manager.runner.run_process,
                                            manager.detect_all, manager.check_update)
        self._patch_runner(manager, ok=True)
        manager.check_update = lambda e, force=False: done.set()
        try:
            res = manager.run_mgmt_command(entry, "upgrade")
        finally:
            manager.runner.run_process = orig_rp
            manager.detect_all = orig_detect
            manager.check_update = orig_check
        self.assertTrue(res["ok"])
        with manager._LOCK:
            self.assertNotIn("fake-cli", manager._UPDATE_CACHE,
                             "成功后旧「有新版本」结论必须立刻作废")
        self.assertTrue(done.wait(2), "成功后应后台复检一次远端版本")

    def test_failure_keeps_cache(self):
        from app.core import manager
        entry = self._entry()
        with manager._LOCK:
            manager._UPDATE_CACHE["fake-cli"] = (time.time(), {"updatable": True})
        orig_rp, orig_detect, orig_check = (manager.runner.run_process,
                                            manager.detect_all, manager.check_update)
        self._patch_runner(manager, ok=False, stderr="npm error 404 Not Found")
        rechecked = []
        manager.check_update = lambda e, force=False: rechecked.append(e["id"])
        try:
            res = manager.run_mgmt_command(entry, "upgrade")
        finally:
            manager.runner.run_process = orig_rp
            manager.detect_all = orig_detect
            manager.check_update = orig_check
        self.assertFalse(res["ok"])
        self.assertIn("404", res["error"])
        with manager._LOCK:
            self.assertIn("fake-cli", manager._UPDATE_CACHE,
                          "失败不改版本结论，缓存原样保留")
        time.sleep(0.1)
        self.assertEqual(rechecked, [], "失败不触发复检")


class TestAiderChannelPatch(BaseTest):

    def test_pip_form_migrated(self):
        """存量 data/catalog.json 的 pip 形态在 load 时被幂等改写为 uv 渠道。"""
        from app.core import catalog
        entries = [{"id": "aider",
                    "install": "py -3.13 -m pip install -U aider-chat",
                    "upgrade": "py -3.13 -m pip install -U aider-chat"}]
        catalog._apply_install_patch(entries)
        self.assertEqual(entries[0]["install"], "uv tool install --python 3.12 aider-chat")
        self.assertEqual(entries[0]["upgrade"], "uv tool upgrade aider-chat")

    def test_uv_form_untouched(self):
        from app.core import catalog
        entries = [{"id": "aider",
                    "install": "uv tool install --python 3.12 aider-chat",
                    "upgrade": "uv tool upgrade aider-chat"}]
        catalog._apply_install_patch(entries)
        self.assertEqual(entries[0]["install"], "uv tool install --python 3.12 aider-chat")

    def test_user_custom_command_untouched(self):
        """用户自定义的非 pip 安装命令不被迁移。"""
        from app.core import catalog
        entries = [{"id": "aider", "install": "cargo install aider",
                    "upgrade": "cargo install-force aider"}]
        catalog._apply_install_patch(entries)
        self.assertEqual(entries[0]["install"], "cargo install aider")

    def test_other_entries_untouched(self):
        from app.core import catalog
        entries = [{"id": "codex-cli", "install": "npm install -g @openai/codex",
                    "upgrade": "npm install -g @openai/codex@latest"}]
        catalog._apply_install_patch(entries)
        self.assertEqual(entries[0]["install"], "npm install -g @openai/codex")

    def test_derive_uninstall_uv(self):
        """uv tool install 推导卸载：包名在末尾（--python 3.12 的值不带横杠）。"""
        from app.core import catalog
        self.assertEqual(
            catalog.derive_uninstall("uv tool install --python 3.12 aider-chat"),
            "uv tool uninstall aider-chat")

    def test_default_catalog_aider_is_uv(self):
        """内置默认已是 uv 渠道，且检查更新口径不受影响（非 npm → unsupported）。"""
        from app.core import catalog
        entry = catalog.by_id("aider")
        self.assertIsNotNone(entry)
        self.assertTrue(entry["install"].startswith("uv tool install"))
        self.assertIsNone(catalog.npm_pkg_name(entry["install"]))
