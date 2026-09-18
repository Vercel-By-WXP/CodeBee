# -*- coding: utf-8 -*-
"""Git 工作台「创建 PR」op 测试（借鉴 agent-orchestrator 的 planning→merge 闭环）。

真 git 仓库 + 本地裸仓当 origin（离线）；gh 用假替身捕获 argv。
跑法：python -m unittest discover -s tests -p "test_pr_create.py" -v
"""
from __future__ import annotations

import subprocess

from base import BaseTest


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


class PrCreateTests(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import gitmod
        self.gitmod = gitmod
        self._orig_gh = gitmod._gh
        # origin：本地裸仓（离线可 push）
        self.bare = self.tmp / "origin.git"
        _git(self.tmp, "init", "--bare", "-b", "main", str(self.bare))
        # 工作仓：main 建基线提交 → 切任务分支提交一个文件
        _git(self.workdir, "init", "-b", "main")
        _git(self.workdir, "config", "user.email", "t@t")
        _git(self.workdir, "config", "user.name", "t")
        (self.workdir / "base.txt").write_text("base", encoding="utf-8")
        _git(self.workdir, "add", ".")
        _git(self.workdir, "commit", "-m", "base")
        _git(self.workdir, "remote", "add", "origin", str(self.bare))
        _git(self.workdir, "checkout", "-b", "codebee/t-pr")
        (self.workdir / "feat.txt").write_text("feat", encoding="utf-8")
        _git(self.workdir, "add", ".")
        _git(self.workdir, "commit", "-m", "feat")

    def tearDown(self):
        self.gitmod._gh = self._orig_gh
        super().tearDown()

    def _fake_gh(self, ok=True, stdout="", stderr=""):
        calls = []

        def fake(wd, *args, timeout=60):
            calls.append(args)
            if args == ("--version",):
                return {"ok": ok, "stdout": "gh version 2.0" if ok else "", "stderr": stderr}
            return {"ok": ok, "stdout": stdout, "stderr": stderr}
        self.gitmod._gh = fake
        return calls

    def test_pr_create_happy_path(self):
        calls = self._fake_gh(ok=True, stdout="https://github.com/x/y/pull/9\n")
        ok, err, data = self.gitmod.workbench_op(str(self.workdir), "pr_create", {})
        self.assertTrue(ok, err)
        self.assertEqual(data["url"], "https://github.com/x/y/pull/9")
        self.assertEqual(data["branch"], "codebee/t-pr")
        self.assertEqual(data["base"], "main")           # 自动识别基线分支
        self.assertIn("pr", calls[-1])                    # 最后一步是 gh pr create
        # 分支已真实推到裸仓 origin
        out = subprocess.run(["git", "branch"], cwd=str(self.bare),
                             capture_output=True, text=True).stdout
        self.assertIn("codebee/t-pr", out)

    def test_pr_create_gh_missing(self):
        self.gitmod._gh = lambda wd, *a, **k: {"ok": False, "stdout": "", "stderr": ""}
        ok, err, _ = self.gitmod.workbench_op(str(self.workdir), "pr_create", {})
        self.assertFalse(ok)
        self.assertIn("gh CLI", err)

    def test_pr_create_gh_auth_error(self):
        self._fake_gh(ok=False, stderr="gh: To get started with GitHub CLI, please run: gh auth login")
        ok, err, _ = self.gitmod.workbench_op(str(self.workdir), "pr_create", {})
        self.assertFalse(ok)
        self.assertIn("gh auth login", err)

    def test_pr_create_no_remote(self):
        _git(self.workdir, "remote", "remove", "origin")
        self._fake_gh()
        ok, err, _ = self.gitmod.workbench_op(str(self.workdir), "pr_create", {})
        self.assertFalse(ok)
        self.assertIn("远程", err)


if __name__ == "__main__":
    unittest.main()
