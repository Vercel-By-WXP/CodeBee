# -*- coding: utf-8 -*-
"""weekly_report git log 素材通道（pipeline._gitlog_brief）单元测试：
正常（临时 git 仓真实提交如实取材）/ 边界（非 git 目录/空仓/命令异常/
ok=False）/ 回归（取材参数锚定：7 天窗口、40 条封顶、空白裁剪）。"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


class TestGitlogBrief(BaseTest):
    def runTest(self):
        from app.core import gitmod
        from app.core import pipeline

        wd = self.workdir

        # ---- 边界：非 git 目录 → 空串（常态路径，绝不阻塞起草）
        self.assertEqual(pipeline._gitlog_brief(wd), "")

        # ---- 正常：临时 git 仓两笔提交 → 摘要行如实取材，封顶 40 行内
        gitmod._git(wd, "init")
        for msg in ("feat: 报表导出", "fix: 登录超时"):
            gitmod._git(wd, "-c", "user.email=t@example.com",
                        "-c", "user.name=t",
                        "commit", "--allow-empty", "-m", msg)
        brief = pipeline._gitlog_brief(wd)
        self.assertIn("feat: 报表导出", brief)
        self.assertIn("fix: 登录超时", brief)
        self.assertLessEqual(len(brief.splitlines()), 40)

        # ---- 边界：空仓无提交（git log 报错）→ 空串
        empty = self.tmp / "empty_repo"
        empty.mkdir()
        gitmod._git(empty, "init")
        self.assertEqual(pipeline._gitlog_brief(empty), "")

        # ---- 边界：git 命令抛异常 → 空串，绝不冒泡
        with mock.patch.object(gitmod, "_git", side_effect=RuntimeError("boom")):
            self.assertEqual(pipeline._gitlog_brief(wd), "")

        # ---- 回归：ok=False → 空串（不把 stderr 当素材）
        with mock.patch.object(gitmod, "_git",
                               return_value={"ok": False, "stdout": "x"}):
            self.assertEqual(pipeline._gitlog_brief(wd), "")

        # ---- 回归：取材参数锚定（--since 7 天/-n 40 封顶）+ 空白裁剪
        with mock.patch.object(
                gitmod, "_git",
                return_value={"ok": True, "stdout": "  abc1 2026-10-01 修闸门  \n"}) as mg:
            self.assertEqual(pipeline._gitlog_brief(wd), "abc1 2026-10-01 修闸门")
        args = mg.call_args[0]
        self.assertEqual(args[0], wd)
        self.assertIn("--since=7 days ago", args)
        self.assertIn("--pretty=%h %ad %s", args)
        self.assertEqual(args[args.index("-n") + 1], "40")
