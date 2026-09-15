# -*- coding: utf-8 -*-
"""继续连载回归：在旧任务基础上新建任务接着写下一批章节。

锁定四个关键行为：
1. continue_task 链式建任务：start_chapter 接续、continues 指向上批、标题 ·续/·续2；
2. 进度以工作目录 chapter-*.md 为准（续写批次共用目录，天然含全部历史章）；
3. 连载流水线全局章号：新批次写 chapter-11+.md，不碰旧章，成书合并仍是完整一本；
4. 状态守卫：非连载任务/运行中/还没写成任何一章时拒绝续写。
"""
from __future__ import annotations

from base import BaseTest


class TestContinueSerial(BaseTest):

    def _run_serial(self, task):
        from app.core import pipeline, store
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        pipeline._agents = self.mock_agents  # 只用 mock，不碰真实 CLI
        pipeline.execute_run(run["id"])
        return store.get_run(run["id"])

    def test_continue_task_chain_and_title(self):
        """store 层：start_chapter 接续 + continues 指向上批 + 标题按代数标 ·续。"""
        from app.core import store
        prev = store.create_task({
            "type": "serial_novel", "title": "测试书", "goal": "写一本连载",
            "workdir": str(self.workdir),
            "serial": {"chapters": 8, "words_per_chapter": 2000},
        })
        for i in range(1, 11):  # 前 10 章已成稿（跨 8+2 两批也照样数对）
            (self.workdir / ("chapter-%02d.md" % i)).write_text("第 %d 章" % i, encoding="utf-8")
        ok, err, nxt = store.continue_task(prev["id"])
        self.assertTrue(ok, err)
        self.assertEqual(nxt["serial"]["start_chapter"], 11)
        self.assertEqual(nxt["serial"]["continues"], prev["id"])
        self.assertEqual(nxt["title"], "测试书·续")
        self.assertEqual(nxt["workdir"], prev["workdir"])
        self.assertEqual(nxt["manuscript"], prev["manuscript"])
        self.assertEqual(nxt["goal"], prev["goal"])
        # 第二代：链上两代 → ·续2；进度仍以文件为准
        (self.workdir / "chapter-11.md").write_text("第 11 章", encoding="utf-8")
        (self.workdir / "chapter-12.md").write_text("第 12 章", encoding="utf-8")
        ok2, err2, nxt2 = store.continue_task(nxt["id"], chapters=1)
        self.assertTrue(ok2, err2)
        self.assertEqual(nxt2["title"], "测试书·续2")
        self.assertEqual(nxt2["serial"]["start_chapter"], 13)
        self.assertEqual(nxt2["serial"]["continues"], nxt["id"])
        self.assertEqual(nxt2["serial"]["chapters"], 1)  # 续 1 章也允许

    def test_continue_info_guards(self):
        """非连载/运行中/无成稿 → 拒绝续写并给出原因。"""
        from app.core import store
        novel = store.create_task({
            "type": "novel", "title": "单稿件", "goal": "g", "workdir": str(self.workdir)})
        info = store.continue_info(novel["id"])
        self.assertFalse(info["can"])
        self.assertIn("连载", info["reason"])

        serial = store.create_task({
            "type": "serial_novel", "title": "连载", "goal": "g", "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 800}})
        info = store.continue_info(serial["id"])
        self.assertFalse(info["can"])
        self.assertIn("chapter-", info["reason"])  # 还没写成任何一章

        r = store.create_run("orchestration", serial["title"], task_id=serial["id"])
        store.update_run(r["id"], status="running")
        store.update_task_status(serial["id"], "running")
        (self.workdir / "chapter-01.md").write_text("x", encoding="utf-8")
        info = store.continue_info(serial["id"])
        self.assertFalse(info["can"])
        self.assertIn("运行", info["reason"])

    def test_continue_pipeline_global_numbering_and_merge(self):
        """流水线端到端（mock）：第 1 批写 1–2 章 → 续写任务写 3–4 章，
        旧章不动，manuscript.md 合并成完整一本（1–4 章）。"""
        from app.core import pipeline, store
        prev = store.create_task({
            "type": "serial_novel", "title": "衔接书", "goal": "写一本连载",
            "workdir": str(self.workdir), "implementer": "mock-a",
            "critics": ["mock-a", "mock-b"], "threshold": 7.0,
            "serial": {"chapters": 2, "words_per_chapter": 800},
        })
        run1 = self._run_serial(prev)
        self.assertEqual(run1["status"], "done", run1.get("error"))
        self.assertTrue((self.workdir / "chapter-01.md").is_file())
        self.assertTrue((self.workdir / "chapter-02.md").is_file())
        ch1_before = (self.workdir / "chapter-01.md").read_text(encoding="utf-8")

        ok, err, nxt = store.continue_task(prev["id"], chapters=2)
        self.assertTrue(ok, err)
        run2 = self._run_serial(nxt)
        self.assertEqual(run2["status"], "done", run2.get("error"))
        v = run2.get("verdict") or {}
        self.assertEqual(v.get("start_chapter"), 3)
        self.assertEqual(v.get("end_chapter"), 4)
        self.assertEqual([c["chapter"] for c in (v.get("chapter_scores") or [])], [3, 4])
        # 新章落盘、旧章原封不动
        self.assertTrue((self.workdir / "chapter-03.md").is_file())
        self.assertTrue((self.workdir / "chapter-04.md").is_file())
        self.assertEqual(
            (self.workdir / "chapter-01.md").read_text(encoding="utf-8"), ch1_before)
        # 成书合并是完整一本：含第 1 批与第 2 批内容
        ms = (self.workdir / "manuscript.md").read_text(encoding="utf-8")
        self.assertIn("第 1 章", ms)
        self.assertIn("第 3 章", ms)
        # 步骤角色用全书章号（draft-c3，而不是 draft-c1）
        roles = [s["role"] for s in run2["steps"]]
        self.assertIn("draft-c3", roles)
        # 报告标注续写范围
        report = self._paths.RUNS_DIR / run2["id"] / "report.md"
        self.assertIn("续写第 3–4 章", report.read_text(encoding="utf-8"))

    def test_outline_inherit_uses_global_numbers(self):
        """续写任务断点续跑：继承大纲后复用章数按全书章号统计（不再恒为 0）。"""
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "续跑继承", "goal": "g",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 800,
                       "start_chapter": 11, "continues": "t-20260101-0000-9999"},
        })
        # continues 指向不存在任务也应允许创建（仅作前情线索）；这里只验证 retry 继承
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(r1["id"], outline={
            "book_title": "书", "source": "template", "start_chapter": 11,
            "chapters": [{"title": "第 11 章", "beats": "空", "hook": ""},
                         {"title": "第 12 章", "beats": "空", "hook": ""}]})
        s, _ = store.add_step(r1["id"], "draft-c11", "mock", "mock")
        store.finish_step(r1["id"], s["n"], "done", summary="x", duration_s=0.1)
        store.update_run(r1["id"], status="failed", ended_at="2026-09-13 00:00:00")
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertTrue(r2.get("inherit"))
        self.assertEqual(r2["inherit"]["done_chapters"], [11])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
