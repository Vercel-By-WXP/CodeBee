# -*- coding: utf-8 -*-
"""升级自动重启 + 启动端口清场单测（用户拍板 2026-09-21）。

- 升级成功且版本真变 → 自动 relaunch+quit；守卫：无端口/版本未变/有任务在跑则跳过
- 启动清场：自家旧实例（main.py 完整路径/npm 打包路径）杀树；别人的进程
  只报告不关闭；系统/自身进程拒绝清理。

不真跑 npm、不真杀进程，全部 mock。
"""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from base import BaseTest


class AutoRelaunchTests(BaseTest):
    def _su(self):
        import app.core.selfupdate as su
        return su

    def test_skip_when_no_port(self):
        su = self._su()
        su._PENDING_PORT = None
        with mock.patch.object(su, "relaunch") as m_rel:
            su._maybe_auto_relaunch("0.1.22", None)
        m_rel.assert_not_called()

    def test_skip_when_version_unchanged(self):
        su = self._su()
        su._PENDING_PORT = 8765
        with mock.patch.object(su, "package_version", return_value="0.1.22"), \
             mock.patch.object(su, "relaunch") as m_rel:
            su._maybe_auto_relaunch("0.1.22", None)   # 装完还是同版本
        m_rel.assert_not_called()

    def test_skip_when_user_tasks_running(self):
        su = self._su()
        su._PENDING_PORT = 8765
        with mock.patch.object(su, "package_version", return_value="0.1.23"), \
             mock.patch("app.core.jobs._alive", 3), \
             mock.patch.object(su, "relaunch") as m_rel:
            su._maybe_auto_relaunch("0.1.22", None)   # 还有别的任务在跑
        m_rel.assert_not_called()

    def test_fires_relaunch_when_free(self):
        su = self._su()
        su._PENDING_PORT = 8765
        with mock.patch.object(su, "package_version", return_value="0.1.23"), \
             mock.patch("app.core.jobs._alive", 0), \
             mock.patch.object(su.time, "sleep"), \
             mock.patch.object(su, "relaunch") as m_rel, \
             mock.patch.object(su, "self_quit") as m_quit:
            su._maybe_auto_relaunch("0.1.22", None)
            import time as _t
            _t.sleep(0.5)   # 等 daemon 线程跑完（sleep 已被 mock 不等待）
        m_rel.assert_called_once_with(8765)
        m_quit.assert_called_once()

    def test_apply_upgrade_records_port(self):
        su = self._su()
        # 开发仓 install_mode=repo 会先拒绝升级，但端口记账在其之前已生效
        su.apply_upgrade(port=8765)
        self.assertEqual(su._PENDING_PORT, 8765)
        su.apply_upgrade(port=None)
        self.assertIsNone(su._PENDING_PORT)

    def test_restart_drain_rejects_new_job_without_queue(self):
        from app.core import jobs, store

        run = store.create_run("orchestration", "重启窗口")
        jobs.cancel_restart_drain()
        self.assertTrue(jobs.begin_restart_drain())
        try:
            with self.assertRaises(jobs.JobsBusyError):
                jobs.enqueue({"kind": "orchestration", "run_id": run["id"]})
            self.assertEqual(store.get_run(run["id"])["status"], "failed")
            self.assertEqual(jobs._QUEUE.qsize(), 0)
        finally:
            jobs.cancel_restart_drain()

    def test_restart_drain_does_not_interrupt_running_job(self):
        from app.core import jobs, pipeline, store

        run = store.create_run("orchestration", "运行中")
        entered = threading.Event()
        release = threading.Event()

        def execute(_run_id):
            entered.set()
            release.wait(2)

        jobs.cancel_restart_drain()
        with mock.patch.object(pipeline, "execute_run", side_effect=execute):
            jobs.enqueue({"kind": "orchestration", "run_id": run["id"]})
            self.assertTrue(entered.wait(1))
            self.assertFalse(jobs.begin_restart_drain())
            self.assertEqual(store.get_run(run["id"])["status"], "running")
            release.set()
            self.assertTrue(jobs.wait_for_idle(2))


class ClearStalePortTests(BaseTest):
    def _pg(self):
        from app.core import portguard
        return portguard

    def test_is_own_instance_match(self):
        pg = self._pg()
        repo_main = r"E:\GoOut\MultiAgentOrchestration\app\main.py"
        # 源码仓实例：cmdline 含完整绝对路径才认
        self.assertTrue(pg.is_own_instance(
            r'"py.exe" -3 "E:\GoOut\MultiAgentOrchestration\app\main.py" --port 8765',
            repo_main))
        # npm 安装包形态
        self.assertTrue(pg.is_own_instance(
            "py -3 D:/nvm4w/nodejs/node_modules/codebee/app/main.py --port 8765",
            repo_main))
        # 别人的进程：路径对不上就不是自家
        self.assertFalse(pg.is_own_instance(
            "node C:/other/server.js --port 8765", repo_main))
        # 同名 main.py 但路径不同（其他项目）→ 不认
        self.assertFalse(pg.is_own_instance(
            "python C:/otherproj/app/main.py --port 8765", repo_main))
        # 相对路径（无法确认归属）→ 不认
        self.assertFalse(pg.is_own_instance(
            "python app/main.py --port 8765", repo_main))
        # 参数中出现近似路径、非 Python 启动器或端口不符都不能误杀。
        self.assertFalse(pg.is_own_instance(
            r"node server.js --fixture E:\GoOut\MultiAgentOrchestration\app\main.py.bak",
            repo_main, port=8765))
        self.assertFalse(pg.is_own_instance(
            r"python E:\GoOut\MultiAgentOrchestration\app\main.py.bak --port 8765",
            repo_main, port=8765))
        self.assertFalse(pg.is_own_instance(
            r"python E:\GoOut\MultiAgentOrchestration\app\main.py --port 9000",
            repo_main, port=8765))
        self.assertTrue(pg.is_own_instance(
            r"python E:\GoOut\MultiAgentOrchestration\app\main.py --port=9000",
            repo_main, port=9000))
        self.assertFalse(pg.is_own_instance(
            r"python E:\GoOut\MultiAgentOrchestration\app\main.py --port=9000",
            repo_main, port=8765))

    def test_clear_own_instance_kills_tree(self):
        pg = self._pg()
        with mock.patch.object(pg.portscan, "listening_ports",
                               return_value=[{"port": 8765, "pid": 4242}]), \
             mock.patch.object(pg, "proc_cmdline",
                               return_value=r"python E:\x\app\main.py --port 8765"), \
             mock.patch.object(pg.runner, "_kill_tree") as m_kill:
            ok, _ = pg.clear_stale_port(8765, r"E:\x\app\main.py")
        self.assertTrue(ok)
        m_kill.assert_called_once_with(4242)

    def test_clear_other_process_never_signals(self):
        pg = self._pg()
        with mock.patch.object(pg.portscan, "listening_ports",
                               return_value=[{"port": 8765, "pid": 4242}]), \
             mock.patch.object(pg, "proc_cmdline", return_value="node server.js"), \
             mock.patch.object(pg.portscan, "close_port") as m_cp, \
             mock.patch.object(pg.runner, "_kill_tree") as m_kill:
            ok, msg = pg.clear_stale_port(8765, r"E:\x\app\main.py")
        self.assertFalse(ok)
        self.assertIn("非 CodeBee", msg)
        m_cp.assert_not_called()
        m_kill.assert_not_called()

    def test_clear_refuses_system_pid(self):
        pg = self._pg()
        with mock.patch.object(pg.portscan, "listening_ports",
                               return_value=[{"port": 8765, "pid": 4}]):
            ok, msg = pg.clear_stale_port(8765, r"E:\x\app\main.py")
        self.assertFalse(ok)
        self.assertIn("拒绝", msg)

    def test_clear_no_holder_ok(self):
        pg = self._pg()
        with mock.patch.object(pg.portscan, "listening_ports", return_value=[]):
            ok, _ = pg.clear_stale_port(8765, r"E:\x\app\main.py")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
