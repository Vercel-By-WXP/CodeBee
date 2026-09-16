# -*- coding: utf-8 -*-
"""任务检查器（右缘停靠列）后端回归：
1. collect_changes 的行级统计：修改/新增文件的 +/- 数、附件排除、向后兼容；
2. GET /api/tasks/<id>/side 聚合契约：运行中实时 numstat / 结束后快照双路径。
"""
from __future__ import annotations

import json

from base import BaseTest


class TestSidePanel(BaseTest):

    def _git_repo(self):
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

    # ------------------------------------------------ numstat 行级统计

    def test_collect_changes_line_stats(self):
        """修改文件走 numstat、未跟踪新文件按行计数，附件排除，合计正确。"""
        from app.core import gitmod
        repo, g = self._git_repo()
        # 先建一个已跟踪的 del.txt 作为「删除行」样本（单独提交，别把 README 的改动卷进去）
        (repo / "del.txt").write_text("x\ny\n", encoding="utf-8")
        g("add", "del.txt")
        g("commit", "-m", "seed")
        (repo / "README.md").write_text("baseline\none\ntwo\n", encoding="utf-8")   # +2 -0
        (repo / "del.txt").write_text("x\n", encoding="utf-8")                       # +0 -1
        (repo / "新章节.md").write_text("第一行\n第二行\n第三行\n", encoding="utf-8")  # +3 -0（未跟踪）
        att = repo / "_attachments"
        att.mkdir()
        (att / "a.png").write_bytes(b"x")

        ch = gitmod.collect_changes(str(repo))
        by = {f["path"]: f for f in ch["files"]}
        self.assertEqual(by["README.md"]["add"], 2)
        self.assertEqual(by["README.md"]["del"], 0)
        self.assertEqual(by["del.txt"]["del"], 1)
        self.assertEqual(by["新章节.md"]["add"], 3)
        # 状态码归一成单字符（前端 GIT_STATUS_LABEL 的键），未跟踪不再透传 "??"
        self.assertEqual(by["README.md"]["status"], "M")
        self.assertEqual(by["新章节.md"]["status"], "?")
        self.assertFalse(any(f["path"].startswith("_attachments") for f in ch["files"]))
        self.assertEqual(ch["add_total"], 5)
        self.assertEqual(ch["del_total"], 1)

    def test_status_code_normalization(self):
        """porcelain XY 组合态收拢为单字符：??→?、含 D 取 D、MM/AM 取首列。"""
        from app.core import gitmod
        cases = {" M": "M", "M ": "M", "MM": "M", "A ": "A", "AM": "A",
                 "D ": "D", " D": "D", "MD": "D", "??": "?", "R ": "R",
                 "": "M", None: "M"}
        for xy, want in cases.items():
            self.assertEqual(gitmod._status_code(xy), want, "xy=%r" % xy)

    def test_collect_changes_backward_compat(self):
        """旧调用方契约不破：files 仍是 status/path 两个字段起步，diff 仍是文本。"""
        from app.core import gitmod
        repo, g = self._git_repo()
        (repo / "README.md").write_text("baseline\nchanged\n", encoding="utf-8")
        ch = gitmod.collect_changes(str(repo))
        self.assertEqual(ch["files"][0]["path"], "README.md")
        self.assertTrue(ch["files"][0]["status"])
        self.assertIn("changed", ch["diff"])
        # 非 git 目录：空集合 + 零值，绝不抛错
        empty = gitmod.collect_changes(str(self.workdir))
        self.assertEqual(empty["files"], [])
        self.assertEqual(empty["add_total"], 0)
        self.assertEqual(empty["del_total"], 0)

    # ------------------------------------------------ /api/tasks/<id>/side

    def _side(self, task_id):
        """直测聚合逻辑（HTTP 层只是 404 转换薄壳，无可测分支）。"""
        from app.core import store
        d = store.task_side(task_id)
        return (404, None) if d is None else (200, d)

    def test_side_endpoint_after_run_uses_snapshot(self):
        """结束后：changes 取 run 快照（工作区已干净，实时恒 0），git 字段齐。"""
        repo, g = self._git_repo()
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "检查器", "goal": "改点东西",
            "workdir": str(repo), "git_rev": "HEAD"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        self.assertEqual(store.get_run(run["id"])["status"], "done")

        code, d = self._side(task["id"])
        self.assertEqual(code, 200)
        self.assertEqual(d["task"]["id"], task["id"])
        self.assertEqual(d["task"]["git_state"], "isolated")
        self.assertEqual(d["run"]["id"], run["id"])
        self.assertEqual(d["git"]["branch"], "codebee/" + task["id"])
        self.assertEqual(d["git"]["state"], "isolated")
        self.assertTrue(d["git"]["restored"])
        # 结束后走快照：mock 产物在快照里，统计数值来自落盘 changes
        self.assertEqual(d["changes"]["count"], 1)
        self.assertGreaterEqual(d["changes"]["add_total"], 1)
        self.assertIn("mock-impl.txt", d["changes"]["files"][0]["path"])
        # 进度与统计
        self.assertGreaterEqual(d["progress"]["total"], 1)
        self.assertEqual(d["stats"]["runs"], 1)
        self.assertTrue(d["workdir"])
        self.assertTrue(d["steps"])
        # 不带 diff 文本：面板轮询保持 KB 级
        self.assertNotIn("diff", json.dumps(d["changes"]))

    def test_side_endpoint_running_uses_live_numstat(self):
        """运行中：实时统计工作区（造一个未提交改动验证数值）。"""
        repo, g = self._git_repo()
        from app.core import store
        task = store.create_task({
            "type": "code", "title": "运行中", "goal": "g",
            "workdir": str(repo), "git_rev": "HEAD"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running", git={
            "rev": "HEAD", "branch": "codebee/" + task["id"],
            "commit": "abc1234", "from_branch": "master", "base_commit": "abc1234"})
        store.update_task_status(task["id"], "running")
        (repo / "live.txt").write_text("l1\nl2\n", encoding="utf-8")

        code, d = self._side(task["id"])
        self.assertEqual(code, 200)
        self.assertEqual(d["task"]["status"], "running")
        self.assertEqual(d["run"]["status"], "running")
        self.assertEqual(d["changes"]["count"], 1)
        self.assertEqual(d["changes"]["add_total"], 2)
        # 运行中没有裁决按钮的依据，但分支信息齐全
        self.assertEqual(d["git"]["branch"], "codebee/" + task["id"])

    def test_side_endpoint_errors_and_minimal(self):
        """404 与「未启用隔离」任务的最小返回（git.branch 空 → 前端只给说明）。"""
        code, d = self._side("no-such-task")
        self.assertEqual(code, 404)
        repo, g = self._git_repo()
        from app.core import store
        task = store.create_task({
            "type": "code", "title": "无版本", "goal": "g",
            "workdir": str(repo)})   # 不带 git_rev
        code, d = self._side(task["id"])
        self.assertEqual(code, 200)
        self.assertIsNone(d["run"])
        self.assertEqual(d["git"].get("branch", ""), "")
        self.assertEqual(d["changes"]["count"], 0)
        self.assertEqual(d["progress"]["total"], 0)
