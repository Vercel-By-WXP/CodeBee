# -*- coding: utf-8 -*-
"""git_rev 隔离链回归：检出任务分支 → 产物提交到 codebee/<task-id> → 切回原分支。

锁定 Baton/Codeband 式隔离的关键不变量：
1. run 结束后用户工作区回到原分支且干净，产物只在任务分支上（WIP 也提交）；
2. _attachments/ 永不提交也永不丢（未跟踪随分支切换保留）；
3. 脏工作区：检出/合并前 stash 原样收起、结束后原样还原（开发仓库永远有并行
   改动，硬拒绝等于隔离不可用）；非仓库/坏引用仍显式失败；
4. 重试续用已有任务分支（追加提交，不重置）；
5. 变更快照（含未跟踪新文件、排除附件）落 run 记录，中文路径不乱码。
"""
from __future__ import annotations

from base import BaseTest


class TestGitCheckout(BaseTest):

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

    def _run_code_task(self, repo, git_rev="HEAD"):
        """在 git 仓库上跑一个 mock 代码任务（mock 实现会写 mock-impl.txt）。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "隔离任务", "goal": "改点东西",
            "workdir": str(repo), "git_rev": git_rev,
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        return store.get_run(run["id"]), task

    def test_roundtrip_commits_artifacts_and_restores_branch(self):
        """run done：产物提交到任务分支并切回原分支；用户工作区看不到产物。"""
        repo, g = self._git_repo()
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        run, task = self._run_code_task(repo)

        self.assertEqual(run["status"], "done")
        # 工作区：回到原分支、干净、产物文件不在（隔离语义）
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), origin)
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(), "")
        self.assertFalse((repo / "mock-impl.txt").exists())
        # 任务分支：存在、带产物提交；run.git 记录了收尾结果
        tb = "codebee/" + task["id"]
        self.assertTrue(g("rev-parse", "--verify", "--quiet", "refs/heads/" + tb)["ok"])
        self.assertIn("mock-impl.txt", g("ls-tree", "-r", "--name-only", tb)["stdout"])
        self.assertTrue(run["git"]["restored"])
        self.assertTrue(run["git"]["commit"])
        # 变更快照：mock 产物作为新文件进入 diff
        self.assertIn("mock-impl.txt", run["changes"]["diff"])
        self.assertIn("new file", run["changes"]["diff"])

    def test_retry_reuses_task_branch_without_reset(self):
        """同一任务第二次运行：续用任务分支追加提交，不重置、不另开分支。"""
        from app.core import store
        repo, g = self._git_repo()
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        base_count = int(g("rev-list", "--count", "HEAD")["stdout"].strip() or "0")

        run1, task = self._run_code_task(repo)
        self.assertEqual(run1["status"], "done")
        tb = "codebee/" + task["id"]
        self.assertEqual(int(g("rev-list", "--count", tb)["stdout"].strip()), base_count + 1)

        # 第二次 run：mock 实现向 mock-impl.txt 追加一行 → 新一笔提交
        run2 = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        from app.core import pipeline
        pipeline.execute_run(run2["id"])
        run2 = store.get_run(run2["id"])
        self.assertEqual(run2["status"], "done")
        self.assertEqual(int(g("rev-list", "--count", tb)["stdout"].strip()), base_count + 2)
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), origin)
        # 两轮各追加一行，任务分支上的成品含两次痕迹
        blob = g("show", "%s:mock-impl.txt" % tb)["stdout"]
        self.assertEqual(blob.count("mock 实现："), 2)

    def test_dirty_workdir_stashes_and_restores(self):
        """脏工作区 → 检出前 stash 原样收起，run 正常跑完，收尾切回后原样还原。"""
        repo, g = self._git_repo()
        (repo / "user-wip.txt").write_text("用户未保存的改动", encoding="utf-8")
        (repo / "README.md").write_text("baseline\n用户改的\n", encoding="utf-8")   # 已跟踪改动
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        run, task = self._run_code_task(repo)

        self.assertEqual(run["status"], "done")
        self.assertTrue(run["git"]["stash"])           # 确实收起过
        self.assertTrue(run["git"]["restored"])
        self.assertEqual(run["git"]["restore_error"], "")
        # 回到原分支，用户改动原样还原（未跟踪新文件 + 已跟踪修改都在）
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), origin)
        self.assertTrue((repo / "user-wip.txt").exists())
        self.assertIn("用户改的", (repo / "README.md").read_text(encoding="utf-8"))
        st = g("status", "--porcelain")["stdout"]
        self.assertIn("user-wip.txt", st)
        self.assertIn(" M README.md", st)
        # apply 成功后 stash 条目已清理
        self.assertEqual(g("stash", "list")["stdout"].strip(), "")
        # 产物仍只落在任务分支，不污染用户工作区
        self.assertFalse((repo / "mock-impl.txt").exists())
        self.assertIn("mock-impl.txt",
                      g("ls-tree", "-r", "--name-only", "codebee/" + task["id"])["stdout"])

    def test_non_repo_workdir_fails_loudly(self):
        """非 git 仓库 + git_rev → 显式失败（不静默退回当前 HEAD）。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "无仓库", "goal": "g",
            "workdir": str(self.workdir), "git_rev": "HEAD"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        r = store.get_run(run["id"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("不是 git 仓库", r["error"])

    def test_attachments_never_committed_never_lost(self):
        """附件目录：不进任务分支提交，切回原分支后仍留在工作目录。"""
        repo, g = self._git_repo()
        att = repo / "_attachments"
        att.mkdir()
        (att / "upload.png").write_bytes(b"\x89PNG fake")
        run, task = self._run_code_task(repo)

        self.assertEqual(run["status"], "done")
        self.assertTrue((att / "upload.png").exists())
        tb = "codebee/" + task["id"]
        ls = g("ls-tree", "-r", "--name-only", tb)["stdout"]
        self.assertIn("mock-impl.txt", ls)
        self.assertNotIn("_attachments", ls)
        # 工作区剩下的唯一"脏"项就是附件（未跟踪，符合预期）
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(),
                         "?? _attachments/")

    def test_collect_changes_untracked_and_chinese_paths(self):
        """快照单元：已跟踪修改 + 未跟踪新文件都进 diff，附件排除，中文路径不乱码。"""
        from app.core import gitmod
        repo, g = self._git_repo()
        (repo / "README.md").write_text("baseline\nchanged\n", encoding="utf-8")
        (repo / "第-01章.md").write_text("正文内容", encoding="utf-8")
        att = repo / "_attachments"
        att.mkdir()
        (att / "a.png").write_bytes(b"x")

        ch = gitmod.collect_changes(str(repo))
        paths = [f["path"] for f in ch["files"]]
        self.assertIn("第-01章.md", paths)
        self.assertIn("README.md", paths)
        self.assertFalse(any(p.startswith("_attachments") for p in paths),
                         "附件不应出现在变更列表: %r" % paths)
        self.assertIn("第-01章.md", ch["diff"])
        self.assertIn("+正文内容", ch["diff"])
        self.assertIn("changed", ch["diff"])

    def test_finalize_without_changes_just_switches_back(self):
        """无变更 run（mock 未写盘场景构造）：跳过提交，照样切回原分支。"""
        from app.core import gitmod
        repo, g = self._git_repo()
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        g("checkout", "-q", "-b", "codebee/task-x")
        fin = gitmod.finalize_run(str(repo), {
            "branch": "codebee/task-x", "from_branch": origin,
            "base_commit": g("rev-parse", "--short", origin)["stdout"].strip(),
        }, "tutti r1: 空跑")
        self.assertTrue(fin["restored"])
        self.assertEqual(fin["commit"], "")
        self.assertEqual(fin["restore_error"], "")
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), origin)

    # ------------------------------------------------ 裁决：合并 / 丢弃

    def test_merge_and_discard_roundtrip(self):
        """人审出口：isolated → 合并（产物回原分支，工作区干净）→ 丢弃（分支消失）。"""
        from app.core import gitmod
        repo, g = self._git_repo()
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        run, task = self._run_code_task(repo)
        self.assertEqual(run["status"], "done")
        # pipeline 检出时把任务标记为待裁决
        from app.core import store
        self.assertEqual(store.get_task(task["id"]).get("git_state"), "isolated")
        tb = "codebee/" + task["id"]

        # 合并：mock-impl.txt 回到原分支、工作区干净、状态置 merged
        # （走 API 层语义：gitmod 合并 + store 置终态，与 _api_git_verdict 一致）
        ok, err, info = gitmod.merge_task_branch(str(repo), store.get_task(task["id"]))
        self.assertTrue(ok, err)
        store.set_task_git_state(task["id"], "merged")
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), origin)
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(), "")
        blob = g("show", "%s:mock-impl.txt" % origin)["stdout"]
        self.assertIn("mock 实现：", blob)
        self.assertEqual(store.get_task(task["id"]).get("git_state"), "merged")

        # 已合并的分支再合并/丢弃 → 显式拒绝（分支仍在，但内容已并入）
        ok2, err2, _ = gitmod.merge_task_branch(str(repo), store.get_task(task["id"]))
        self.assertFalse(ok2)   # 任务分支无领先提交（内容已并入）
        self.assertIn("没有领先基线的提交", err2)
        # 新任务验证丢弃：分支删除、原分支不受影响、状态置 discarded
        task2 = store.create_task({
            "type": "code", "title": "丢弃组", "goal": "g",
            "workdir": str(repo), "git_rev": "HEAD"})
        run2 = store.create_run("orchestration", task2["title"], task_id=task2["id"])
        store.update_task_status(task2["id"], "queued")
        from app.core import pipeline
        pipeline.execute_run(run2["id"])
        self.assertEqual(store.get_run(run2["id"])["status"], "done")
        ok3, err3 = gitmod.discard_task_branch(str(repo), store.get_task(task2["id"]))
        self.assertTrue(ok3, err3)
        store.set_task_git_state(task2["id"], "discarded")
        self.assertFalse(g("rev-parse", "--verify", "--quiet",
                            "refs/heads/codebee/" + task2["id"])["ok"])
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(), "")
        self.assertEqual(store.get_task(task2["id"]).get("git_state"), "discarded")

    def test_merge_guards_active_task_and_dirty_workdir(self):
        """守卫：任务运行中拒绝裁决；工作区有未提交改动拒绝合并。"""
        from app.core import gitmod, store
        repo, g = self._git_repo()
        task = store.create_task({
            "type": "code", "title": "守卫组", "goal": "g",
            "workdir": str(repo), "git_rev": "HEAD"})
        # 运行中
        store.update_task_status(task["id"], "running")
        ok, err, _ = gitmod.merge_task_branch(str(repo), store.get_task(task["id"]))
        self.assertFalse(ok)
        self.assertIn("正在运行", err)
        ok, err = gitmod.discard_task_branch(str(repo), store.get_task(task["id"]))
        self.assertFalse(ok)
        self.assertIn("正在运行", err)
        store.update_task_status(task["id"], "done")
        # 无分支
        ok, err, _ = gitmod.merge_task_branch(str(repo), store.get_task(task["id"]))
        self.assertFalse(ok)
        self.assertIn("不存在", err)
        # 手工造一条任务分支（无带检出信息的 run 记录）
        g("checkout", "-q", "-b", "codebee/" + task["id"])
        (repo / "art.txt").write_text("产物", encoding="utf-8")
        g("add", "-A")
        g("-c", "user.name=T", "-c", "user.email=t@l", "commit", "-m", "artifacts")
        g("checkout", "-q", "master" if g("rev-parse", "--verify", "--quiet", "master")["ok"] else "main")
        # 该任务无带检出信息的 run 记录，基线无法确定 → 显式拒绝而非合并进当前分支
        # （脏工作区不再触发拒绝：stash 收起后合并在干净树上进行，
        #   正向路径见 test_merge_with_dirty_workdir_stashes_and_restores）
        g("checkout", "-q", "-b", "elsewhere")
        ok, err, _ = gitmod.merge_task_branch(str(repo), store.get_task(task["id"]))
        self.assertFalse(ok)
        self.assertIn("基线分支", err)

    def test_merge_with_dirty_workdir_stashes_and_restores(self):
        """脏工作区合并：stash 收起用户改动 → 合并在干净树上成功 → 原样还原。"""
        from app.core import gitmod, store
        repo, g = self._git_repo()
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        run, task = self._run_code_task(repo)
        self.assertEqual(run["status"], "done")
        # 待裁决期间用户又在工作区改了东西（并行开发的常态）
        (repo / "user-wip.txt").write_text("未提交", encoding="utf-8")
        (repo / "README.md").write_text("baseline\n用户又改了\n", encoding="utf-8")

        ok, err, info = gitmod.merge_task_branch(str(repo), store.get_task(task["id"]))
        self.assertTrue(ok, err)
        self.assertEqual(info.get("restore_error", ""), "")
        # 用户改动原样回到工作区，stash 清空，产物已并入原分支
        self.assertTrue((repo / "user-wip.txt").exists())
        self.assertIn("用户又改了", (repo / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(g("stash", "list")["stdout"].strip(), "")
        blob = g("show", "%s:mock-impl.txt" % origin)["stdout"]
        self.assertIn("mock 实现：", blob)

    # ---------------------------------------- 丢弃：分支被其它 worktree 占用

    def _holder_scene(self):
        """造「收尾被打断」遗留现场：主工作树停在任务分支上 + 另一个
        linked worktree 作为裁决 workdir（用户实测 mo-so 占分支案的形状）。
        返回 (repo, g, wt, task, origin)。"""
        from app.core import store
        repo, g = self._git_repo()
        origin = g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()
        run, task = self._run_code_task(repo)
        assert run["status"] == "done"
        g("checkout", "-q", "codebee/" + task["id"])   # 模拟 finalize 未跑成
        wt = self.tmp / "wt-inspect"
        g("worktree", "add", "-q", "-b", "inspect-side", str(wt), origin)
        return repo, g, wt, task, origin

    def test_discard_frees_branch_held_by_other_worktree(self):
        """任务分支被别的 worktree 检出时丢弃：干净的工作树自动切回基线，
        分支照删（旧逻辑在此报 cannot delete branch used by worktree）。"""
        from app.core import gitmod, store
        repo, g, wt, task, origin = self._holder_scene()
        tb = "codebee/" + task["id"]

        ok, err = gitmod.discard_task_branch(str(wt), store.get_task(task["id"]))
        self.assertTrue(ok, err)
        self.assertFalse(g("rev-parse", "--verify", "--quiet", "refs/heads/" + tb)["ok"])
        # 被占用的主工作树被送回基线分支且保持干净；裁决用的工作树不受影响
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), origin)
        self.assertEqual(g("status", "--porcelain")["stdout"].strip(), "")
        self.assertEqual(g("-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(),
                         "inspect-side")

    def test_discard_refuses_when_holder_worktree_dirty(self):
        """占用分支的 worktree 有未提交改动 → 显式拒绝，分支与改动原样保留。"""
        from app.core import gitmod, store
        repo, g, wt, task, origin = self._holder_scene()
        tb = "codebee/" + task["id"]
        (repo / "wip.txt").write_text("未提交", encoding="utf-8")

        ok, err = gitmod.discard_task_branch(str(wt), store.get_task(task["id"]))
        self.assertFalse(ok)
        self.assertIn("未提交改动", err)
        self.assertTrue(g("rev-parse", "--verify", "--quiet", "refs/heads/" + tb)["ok"])
        self.assertTrue((repo / "wip.txt").exists())

    # --------------------------------- 游离基线（from_branch="HEAD"）专项

    def test_discard_with_literal_head_from_branch(self):
        """用户实测案：游离基线在老数据里存的是字面量 "HEAD"，而
        git checkout HEAD 原地空转（退出码 0 但不换位）——旧逻辑 checkout
        「成功」后分支仍被占用，branch -D 照样报 used by worktree。
        现在按游离处理回基线提交，且以 rev-parse 复核分支真的腾空。"""
        import json
        from app.core import gitmod, paths
        repo, g = self._git_repo()
        origin_sha = g("rev-parse", "HEAD")["stdout"].strip()
        tid = "t-detach-head-case"
        tb = "codebee/" + tid
        g("checkout", "-q", "-b", tb)
        (repo / "art.txt").write_text("产物", encoding="utf-8")
        g("add", "-A")
        g("-c", "user.name=T", "-c", "user.email=t@l", "commit", "-qm", "art")
        rundir = paths.RUNS_DIR / "r-test-head"
        rundir.mkdir(parents=True)
        (rundir / "run.json").write_text(json.dumps({
            "id": "r-test-head", "task_id": tid,
            "git": {"branch": tb, "from_branch": "HEAD",
                    "base_commit": origin_sha[:9]},
        }), encoding="utf-8")

        ok, err = gitmod.discard_task_branch(str(repo), {"id": tid, "status": "done"})
        self.assertTrue(ok, err)
        self.assertFalse(g("rev-parse", "--verify", "--quiet", "refs/heads/" + tb)["ok"])
        # 主工作树被送回基线提交（游离形态）：abbrev-ref 为 HEAD，提交即基线
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), "HEAD")
        self.assertEqual(g("rev-parse", "HEAD")["stdout"].strip(), origin_sha)

    def test_finalize_from_detached_head_returns_to_base(self):
        """收尾同理：from_branch="HEAD" 不能原样 checkout（空转），
        必须回 base_commit（同样游离）。"""
        from app.core import gitmod
        repo, g = self._git_repo()
        origin_sha = g("rev-parse", "HEAD")["stdout"].strip()
        g("checkout", "-q", "-b", "codebee/task-dh")
        fin = gitmod.finalize_run(str(repo), {
            "branch": "codebee/task-dh", "from_branch": "HEAD",
            "base_commit": origin_sha[:9]}, "r: 空跑")
        self.assertTrue(fin["restored"])
        self.assertEqual(fin["restore_error"], "")
        self.assertEqual(g("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip(), "HEAD")
        self.assertEqual(g("rev-parse", "HEAD")["stdout"].strip(), origin_sha)
