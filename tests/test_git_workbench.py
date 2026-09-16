# -*- coding: utf-8 -*-
"""GIT 工作台回归：workbench_status 聚合 + workbench_op 白名单写操作。

锁定工作台的关键不变量：
1. status 三组分类正确（暂存/未暂存/未跟踪），±统计与 ahead/behind 不虚报；
2. 暂存/取消暂存/丢弃/删除/提交往返，路径参数越界（..、绝对路径、- 开头）全拒；
3. _attachments/ 永不进提交也永不被 stash（与隔离链同一口径）；
4. 切分支：脏工作区显式拒绝；干净时本地直切、远程分支建跟踪分支；
5. fetch/pull/push：无远程显式报错；有本地 bare 远程时推拉闭环；
6. stash 只认 tutti-stash-* 条目，用户自己的 stash 工作台不碰；
7. 丢弃/删除类不可恢复操作必须 confirm=true。
"""
from __future__ import annotations

from base import BaseTest


class TestGitWorkbench(BaseTest):

    def _git_repo(self):
        """临时 git 仓库 + 执行器；返回 (repo_path, run_git_fn)。"""
        from app.core import runner
        repo = self.tmp / "repo"
        repo.mkdir()

        def g(*args):
            return runner.run_process(argv=["git", *args], cwd=str(repo), timeout=60)

        g("init")
        g("config", "user.name", "Tester")
        g("config", "user.email", "tester@local")
        (repo / "README.md").write_text("baseline\n", encoding="utf-8")
        g("add", "-A")
        g("commit", "-m", "baseline")
        return repo, g

    def _commit_all(self, g, msg):
        g("add", "-A")
        g("commit", "-m", msg)

    # ---------------------------------------------------------- status 聚合

    def test_status_groups_and_counts(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "mod.txt").write_text("改了\n", encoding="utf-8")          # 未暂存 M
        (repo / "new.txt").write_text("新文件\n", encoding="utf-8")        # 未跟踪
        g("add", "mod.txt")                                                # 暂存 M
        st = gitmod.workbench_status(str(repo))
        self.assertTrue(st["repo"])
        self.assertEqual([f["path"] for f in st["staged"]], ["mod.txt"])
        self.assertEqual([f["path"] for f in st["untracked"]], ["new.txt"])
        self.assertEqual(st["unstaged"], [])       # mod.txt 只剩暂存改动
        self.assertEqual(st["staged"][0]["add"], 1)
        self.assertEqual(st["branch"], "master")
        self.assertEqual(st["head"], g("rev-parse", "--short", "HEAD")["stdout"].strip())
        self.assertEqual(st["stashes"], [])
        self.assertTrue(st["recent"])

    def test_status_attachments_excluded(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        att = repo / "_attachments"
        att.mkdir()
        (att / "a.png").write_bytes(b"\x89PNG")
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(st["untracked"], [])      # 附件目录不算变更

    def test_status_not_a_repo(self):
        from app.core import gitmod
        self.assertEqual(gitmod.workbench_status(str(self.workdir)), {"repo": False})

    def test_status_ahead_behind_with_bare_remote(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        bare = self.tmp / "remote.git"
        g("clone", "--bare", "--quiet", str(repo), str(bare))
        g("remote", "add", "origin", str(bare))
        g("fetch", "--quiet", "origin")
        g("branch", "--set-upstream-to=origin/master")
        (repo / "ahead.txt").write_text("领先提交\n", encoding="utf-8")
        self._commit_all(g, "local ahead")
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(st["ahead"], 1)
        self.assertEqual(st["behind"], 0)
        self.assertTrue(st["remotes"])
        self.assertEqual(st["upstream"], "origin/master")

    # ---------------------------------------------------------- stage/unstage

    def test_stage_unstage_roundtrip(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "mod.txt").write_text("改了\n", encoding="utf-8")
        ok, err, _ = gitmod.workbench_op(str(repo), "stage", {"path": "mod.txt"})
        self.assertTrue(ok, err)
        st = gitmod.workbench_status(str(repo))
        self.assertEqual([f["path"] for f in st["staged"]], ["mod.txt"])
        self.assertEqual(st["unstaged"], [])
        ok, err, _ = gitmod.workbench_op(str(repo), "unstage", {"path": "mod.txt"})
        self.assertTrue(ok, err)
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(st["staged"], [])
        # 新加文件撤出 index 回到未跟踪；已跟踪文件的撤出才落「未暂存」组
        self.assertEqual(sorted(f["path"] for f in st["untracked"]), ["mod.txt"])
        (repo / "README.md").write_text("改了\n", encoding="utf-8")
        gitmod.workbench_op(str(repo), "stage", {"path": "README.md"})
        gitmod.workbench_op(str(repo), "unstage", {"path": "README.md"})
        st = gitmod.workbench_status(str(repo))
        self.assertEqual([f["path"] for f in st["unstaged"]], ["README.md"])
        self.assertEqual(st["staged"], [])

    def test_stage_untracked_new_file(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "new.txt").write_text("新\n", encoding="utf-8")
        ok, err, _ = gitmod.workbench_op(str(repo), "stage", {"path": "new.txt"})
        self.assertTrue(ok, err)
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(st["untracked"], [])
        self.assertEqual(st["staged"][0]["status"], "A")

    def test_stage_refuses_attachments_and_bad_paths(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        att = repo / "_attachments"
        att.mkdir()
        (att / "a.txt").write_text("素材\n", encoding="utf-8")
        for bad in ("_attachments/a.txt", "../escape.txt", "C:/boot", "-A", "", "a/../../b"):
            ok, err, _ = gitmod.workbench_op(str(repo), "stage", {"path": bad})
            self.assertFalse(ok, "应拒绝 %r" % bad)

    # ---------------------------------------------------------- discard/delete

    def test_discard_tracked_needs_confirm_then_restores(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "README.md").write_text("改坏了\n", encoding="utf-8")
        ok, err, _ = gitmod.workbench_op(str(repo), "discard", {"path": "README.md"})
        self.assertFalse(ok)                       # 无 confirm 拒绝
        self.assertIn("confirm", err)
        ok, err, _ = gitmod.workbench_op(str(repo), "discard", {"path": "README.md", "confirm": True})
        self.assertTrue(ok, err)
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "baseline\n")
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(), "")

    def test_discard_staged_change_resets_index_too(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "README.md").write_text("改了\n", encoding="utf-8")
        g("add", "README.md")
        ok, err, _ = gitmod.workbench_op(str(repo), "discard", {"path": "README.md", "confirm": True})
        self.assertTrue(ok, err)
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(), "")

    def test_delete_untracked_file(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "junk.txt").write_text("垃圾\n", encoding="utf-8")
        ok, err, _ = gitmod.workbench_op(str(repo), "delete", {"path": "junk.txt"})
        self.assertFalse(ok)                       # 无 confirm 拒绝
        ok, err, _ = gitmod.workbench_op(str(repo), "delete", {"path": "junk.txt", "confirm": True})
        self.assertTrue(ok, err)
        self.assertFalse((repo / "junk.txt").exists())
        # 已跟踪文件不走 delete（那是 discard 的活）
        ok, err, _ = gitmod.workbench_op(str(repo), "delete", {"path": "README.md", "confirm": True})
        self.assertFalse(ok)

    # ---------------------------------------------------------- commit

    def test_commit_staged_files(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        ok, err, _ = gitmod.workbench_op(str(repo), "commit", {"message": "空提交"})
        self.assertFalse(ok)                       # 暂存区为空
        ok, err, _ = gitmod.workbench_op(str(repo), "commit", {"message": "  "})
        self.assertFalse(ok)                       # 空信息
        (repo / "feat.py").write_text("print(1)\n", encoding="utf-8")
        gitmod.workbench_op(str(repo), "stage", {"path": "feat.py"})
        ok, err, data = gitmod.workbench_op(str(repo), "commit", {"message": "加个功能"})
        self.assertTrue(ok, err)
        self.assertEqual(data.get("files"), 1)
        self.assertTrue(data.get("commit"))
        log = g("log", "-1", "--format=%s")["stdout"].strip()
        self.assertEqual(log, "加个功能")

    def test_commit_never_touches_attachments(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
        att = repo / "_attachments"
        att.mkdir()
        (att / "a.txt").write_text("素材\n", encoding="utf-8")
        gitmod.workbench_op(str(repo), "stage_all")
        ok, err, data = gitmod.workbench_op(str(repo), "commit", {"message": "只有代码"})
        self.assertTrue(ok, err)
        ls = g("ls-files")["stdout"].split()
        self.assertIn("code.py", ls)
        self.assertNotIn("_attachments/a.txt", ls)   # 附件保持未跟踪

    # ---------------------------------------------------------- checkout

    def test_checkout_refuses_dirty_worktree(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        g("branch", "feature")
        (repo / "README.md").write_text("脏\n", encoding="utf-8")
        ok, err, _ = gitmod.workbench_op(str(repo), "checkout", {"branch": "feature"})
        self.assertFalse(ok)
        self.assertIn("未提交", err)

    def test_checkout_local_and_remote_branch(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        bare = self.tmp / "remote.git"
        g("clone", "--bare", "--quiet", str(repo), str(bare))
        g("remote", "add", "origin", str(bare))
        g("fetch", "--quiet", "origin")
        g("branch", "feature")
        ok, err, _ = gitmod.workbench_op(str(repo), "checkout", {"branch": "feature"})
        self.assertTrue(ok, err)
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), "feature")
        ok, err, _ = gitmod.workbench_op(str(repo), "checkout", {"branch": "origin/master"})
        self.assertTrue(ok, err)
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), "master")
        ok, err, _ = gitmod.workbench_op(str(repo), "checkout", {"branch": "no-such-branch"})
        self.assertFalse(ok)

    # ---------------------------------------------------------- fetch/pull/push

    def test_network_ops_refuse_without_remote(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        for act in ("fetch", "pull", "push"):
            ok, err, _ = gitmod.workbench_op(str(repo), act, {})
            self.assertFalse(ok, act)
            self.assertIn("远程", err)

    def test_push_then_pull_roundtrip(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        bare = self.tmp / "remote.git"
        g("clone", "--bare", "--quiet", str(repo), str(bare))
        g("remote", "add", "origin", str(bare))
        g("fetch", "--quiet", "origin")
        # 推送：无上游 → 自动 -u 建跟踪
        ok, err, _ = gitmod.workbench_op(str(repo), "push", {})
        self.assertTrue(ok, err)
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(st["upstream"], "origin/master")
        # 另一个 clone 改远程，本地 pull 拿到
        g("commit", "--allow-empty", "-m", "占位")
        ok, err, _ = gitmod.workbench_op(str(repo), "push", {})
        self.assertTrue(ok, err)
        clone = self.tmp / "clone"
        g2 = lambda *a: runner_git(a, str(clone))
        r = runner_git(["clone", "--quiet", str(bare), str(clone)], self.tmp)
        (clone / "from-other.md").write_text("别的机器\n", encoding="utf-8")
        runner_git(["add", "-A"], str(clone))
        runner_git(["commit", "-m", "远端改动"], str(clone))
        runner_git(["push", "--quiet", "origin", "master"], str(clone))
        ok, err, _ = gitmod.workbench_op(str(repo), "pull", {})
        self.assertTrue(ok, err)
        self.assertTrue((repo / "from-other.md").exists())

    # ---------------------------------------------------------- stash

    def test_stash_push_pop_excludes_attachments(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "wip.txt").write_text("半成品\n", encoding="utf-8")
        att = repo / "_attachments"
        att.mkdir()
        (att / "a.txt").write_text("素材\n", encoding="utf-8")
        ok, err, _ = gitmod.workbench_op(str(repo), "stash_push", {})
        self.assertTrue(ok, err)
        self.assertFalse((repo / "wip.txt").exists())
        self.assertTrue((att / "a.txt").exists())          # 附件不收
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(len(st["stashes"]), 1)
        self.assertIn("tutti-stash-", st["stashes"][0]["subject"])
        ok, err, _ = gitmod.workbench_op(str(repo), "stash_pop", {"ref": st["stashes"][0]["ref"]})
        self.assertTrue(ok, err)
        self.assertTrue((repo / "wip.txt").exists())

    def test_stash_pop_refuses_foreign_entries(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "x.txt").write_text("用户自己的\n", encoding="utf-8")
        g("stash", "push", "-u", "-m", "my-own")
        st = gitmod.workbench_status(str(repo))
        self.assertEqual(len(st["stashes"]), 1)
        ok, err, _ = gitmod.workbench_op(str(repo), "stash_pop", {"ref": st["stashes"][0]["ref"]})
        self.assertFalse(ok)
        self.assertIn("tutti-stash", err)

    def test_stash_drop_needs_confirm(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "x.txt").write_text("x\n", encoding="utf-8")
        gitmod.workbench_op(str(repo), "stash_push", {})
        ref = gitmod.workbench_status(str(repo))["stashes"][0]["ref"]
        ok, _, _ = gitmod.workbench_op(str(repo), "stash_drop", {"ref": ref})
        self.assertFalse(ok)
        ok, err, _ = gitmod.workbench_op(str(repo), "stash_drop", {"ref": ref, "confirm": True})
        self.assertTrue(ok, err)
        self.assertEqual(gitmod.workbench_status(str(repo))["stashes"], [])

    # ---------------------------------------------------------- file_diff

    def test_file_diff_tracked_untracked_and_escape(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        (repo / "README.md").write_text("baseline\n追加一行\n", encoding="utf-8")
        d = gitmod.file_diff(str(repo), "README.md")
        self.assertFalse(d.get("error"))
        self.assertIn("+追加一行", d["diff"])
        (repo / "brand.md").write_text("全新文件\n", encoding="utf-8")
        d = gitmod.file_diff(str(repo), "brand.md")
        self.assertTrue(d["untracked"])
        self.assertIn("new file mode", d["diff"])
        self.assertIn("+全新文件", d["diff"])
        self.assertTrue(gitmod.file_diff(str(repo), "../outside").get("error"))
        self.assertTrue(gitmod.file_diff(str(repo), "-u").get("error"))

    def test_file_diff_clean_tracked_file_empty(self):
        repo, g = self._git_repo()
        from app.core import gitmod
        d = gitmod.file_diff(str(repo), "README.md")
        self.assertEqual(d.get("diff", ""), "")    # 干净文件无 diff（弹窗给「无变更」）

    # ---------------------------------------------------------- 白名单与降级

    # ---------------------------------------------------------- 路由层守卫

    def test_running_task_write_guard_409(self):
        """任务 queued/running 时写接口必须 409（守卫在 main._api_git_wb_op，
        handler 直调覆盖——load_all 会把无运行的 running 任务改成 failed，
        HTTP 层造不出稳定的运行中状态）。注意 main.py 以 `from core import …`
        引包，其 store 是顶层 core.store（与 app.core.store 是两个模块实例），
        补丁要打在 main 自己的那份上。"""
        import sys
        repo, g = self._git_repo()
        from base import ROOT
        app_dir = str(ROOT / "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        import main as main_mod
        h = main_mod.Handler.__new__(main_mod.Handler)
        captured = {}
        h._body = lambda: {"action": "stage", "path": "README.md"}
        h._json = lambda code, obj: captured.update(code=code, obj=obj)
        task = {"id": "t-guard", "workdir": str(repo), "status": "running"}
        orig = main_mod.store.get_task
        main_mod.store.get_task = lambda tid: task if tid == "t-guard" else orig(tid)
        try:
            h._api_git_wb_op("t-guard")
        finally:
            main_mod.store.get_task = orig
        self.assertEqual(captured.get("code"), 409, captured)
        # 只读 status 不受运行态限制
        captured.clear()
        h._json = lambda code, obj: captured.update(code=code, obj=obj)
        main_mod.store.get_task = lambda tid: task if tid == "t-guard" else orig(tid)
        try:
            h._api_git_wb("t-guard")
        finally:
            main_mod.store.get_task = orig
        self.assertEqual(captured.get("code"), 200, captured)
        self.assertTrue(captured.get("obj", {}).get("repo"))

    def test_unknown_action_and_non_repo(self):
        from app.core import gitmod
        ok, err, _ = gitmod.workbench_op(str(self.workdir), "stage", {"path": "x"})
        self.assertFalse(ok)
        repo, g = self._git_repo()
        ok, err, _ = gitmod.workbench_op(str(repo), "rebase --onto", {})
        self.assertFalse(ok)
        ok, err, _ = gitmod.workbench_op(str(repo), "checkout", {"branch": "-evil"})
        self.assertFalse(ok)


def runner_git(args, cwd):
    from app.core import runner
    return runner.run_process(argv=["git", *args], cwd=str(cwd), timeout=60)
