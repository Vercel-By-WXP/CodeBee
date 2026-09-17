# -*- coding: utf-8 -*-
"""续会话端到端（不碰真实 CLI）：用假 CLI 验证子进程的启动目录。

真实约束（2026-09 实测）：opencode/qwen 的会话恢复按 cwd 定位——CLI 必须在
会话所属项目目录下启动，否则报「找不到会话」或直接挂起。因此续会话步骤的
工作目录必须换成会话的 project，而非任务工作目录。

本测试驱动真实的 pipeline.execute_run，把 agent 的 command 换成假 CLI，
断言它拿到的 cwd 是会话目录、argv 带恢复参数、提示词经 stdin 送达。
"""
from __future__ import annotations

import json
import os
import sys

from base import BaseTest

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_fake_cli.py")


class TestResumeE2E(BaseTest):
    def runTest(self):
        from app.core import catalog, jobs, paths, pipeline, registry, store

        # 会话所属项目目录（真实存在，才能被 _resume_workdir 采纳）
        session_dir = self.tmp / "session-project"
        session_dir.mkdir()
        task_wd = self.workdir  # 与会话目录不同的任务工作目录

        # 假 CLI 注册为已安装且未启用编排（续会话应越过启用开关注入）
        paths.CATALOG_FILE.write_text(json.dumps([
            {"id": "fakecli", "name": "Fake CLI", "cli_group": "installable",
             "detect": {"cli": sys.executable},
             "orch": {"kind": "generic", "command": sys.executable,
                      "argv_template": [FAKE, "{prompt}"],
                      "resume_argv_template": [FAKE, "-s", "{session}"],
                      "env": {"TUTTI_TEST_SELFCONFIG": "1"}},  # 自带配置：过死链闸门
             "default_enabled": False},
        ], ensure_ascii=False), encoding="utf-8")
        catalog._CACHE["entries"] = None
        paths.ENABLED_FILE.write_text("{}", encoding="utf-8")

        # 绕过环境探测：直接把该条目视为已安装
        orig_detect = registry.effective_agents.__globals__["load_enabled"]
        try:
            registry.effective_agents.__globals__["load_enabled"] = lambda: {}
            import app.core.manager as mgr
            orig_da = mgr.detect_all
            mgr.detect_all = lambda force=False: {"fakecli": {"installed": True}}
            # pipeline 内部经 manager.detect_all 探测，同样被替换
            task = store.create_task({
                "type": "code", "goal": "续会话 cwd 验证", "workdir": str(task_wd),
                "mode": "manual", "implementer": "fakecli",
                "resume": {"agent": "fakecli", "session": "ses_demo",
                           "project": str(session_dir), "preview": "p"},
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        finally:
            registry.effective_agents.__globals__["load_enabled"] = orig_detect
            mgr.detect_all = orig_da
            catalog._CACHE["entries"] = None

        # 假 CLI 的输出经 runner 原样写入该步骤日志（夹具本身不写文件）
        run_rec = store.get_run(run["id"])
        log_abs = paths.RUNS_DIR / run["id"] / run_rec["steps"][0]["log"]
        self.assertTrue(log_abs.exists(), "假 CLI 未被调用（无步骤日志）")
        body = log_abs.read_text(encoding="utf-8")
        # 子进程 cwd 必须是会话项目目录（不是任务工作目录）
        self.assertIn("cwd=%s" % str(session_dir), body)
        self.assertNotIn("cwd=%s" % str(task_wd), body)
        # 恢复参数与 stdin 提示词
        self.assertIn("-s", body)
        self.assertIn("ses_demo", body)
        self.assertIn("续会话 cwd 验证", body)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
