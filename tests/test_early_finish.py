# -*- coding: utf-8 -*-
"""提前完结：缩短后续规划，但仍按连载批次完成和发布。"""
from __future__ import annotations

from base import BaseTest


class TestEarlyFinish(BaseTest):

    def _task(self):
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "提前收束", "goal": "写一本连载",
            "workdir": str(self.workdir),
            "serial": {"chapters": 8, "words_per_chapter": 800},
        })
        for i in range(1, 5):
            (self.workdir / ("chapter-%02d.md" % i)).write_text(
                "第 %d 章" % i, encoding="utf-8")
        store.set_auto_publish(task["id"], {
            "enabled": True, "platform": "fanqie", "time": "08:00",
            "auto_submit": True,
        })
        return store.get_task(task["id"])

    def test_early_finish_caps_chain_and_keeps_publish_policy(self):
        from app.core import store

        task = self._task()
        ok, err, first = store.early_finish_task(task["id"], 7)
        self.assertTrue(ok, err)
        self.assertEqual(first["serial"]["start_chapter"], 5)
        self.assertEqual(first["serial"]["chapters"], 3)
        self.assertEqual(first["serial"]["early_finish"]["target_chapter"], 7)
        self.assertEqual(store.get_task(task["id"])["serial"]["early_finish"]["target_chapter"], 7)
        self.assertEqual(store.get_task(first["id"])["auto_publish"]["platform"], "fanqie")

        # The old batch is no longer a valid continuation entry once a child
        # exists; this prevents duplicate branches from bypassing the plan.
        info_old = store.continue_info(task["id"])
        self.assertFalse(info_old["can"])
        self.assertIn("最新", info_old["reason"])

        for i in range(5, 8):
            (self.workdir / ("chapter-%02d.md" % i)).write_text(
                "第 %d 章" % i, encoding="utf-8")
        info = store.continue_info(first["id"])
        self.assertFalse(info["can"])
        self.assertEqual(info["planned_end_chapter"], 7)
        self.assertIn("最终章", info["reason"])

    def test_early_finish_requires_new_target_and_is_single_plan(self):
        from app.core import store

        task = self._task()
        ok, err, _ = store.early_finish_task(task["id"], 4)
        self.assertFalse(ok)
        self.assertIn("大于", err)
        ok, err, first = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        ok2, err2, _ = store.early_finish_task(first["id"], 8)
        self.assertFalse(ok2)
        self.assertIn("已经有", err2)

    def test_auto_publish_ignores_chapters_after_planned_end(self):
        from app.core import store
        from app.core.publish import auto

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        (self.workdir / "chapter-05.md").write_text("第 5 章", encoding="utf-8")
        (self.workdir / "chapter-06.md").write_text("第 6 章", encoding="utf-8")
        (self.workdir / "chapter-07.md").write_text("遗留草稿，不应发布", encoding="utf-8")
        pending, perr = auto.pending(child["id"], "fanqie")
        self.assertEqual(perr, "")
        self.assertEqual([item["chapter_no"] for item in pending], [1, 2, 3, 4, 5, 6])

    def test_due_schedule_is_owned_by_latest_serial_batch(self):
        import time
        from app.core import store
        from app.core.publish import auto, ledger

        task = self._task()
        ledger.save_book(task["id"], "fanqie", {"book_id": "book-early", "title": "提前收束"})
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        late = time.strptime(time.strftime("%Y-%m-%d") + " 23:59", "%Y-%m-%d %H:%M")
        due = auto.due_tasks(now=late)
        self.assertEqual([item[0]["id"] for item in due], [child["id"]])

    def test_volume_plan_is_truncated_at_new_final_chapter(self):
        from app.core import planner, store
        from app.core.publish import volumes as publish_volumes

        task = store.create_task({
            "type": "serial_novel", "title": "卷表收束", "goal": "写一本连载",
            "workdir": str(self.workdir),
            "serial": {"chapters": 8, "words_per_chapter": 800,
                        "volume_chapters": 4},
        })
        for i in range(1, 4):
            (self.workdir / ("chapter-%02d.md" % i)).write_text(
                "第 %d 章" % i, encoding="utf-8")
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        story_plan = planner.book_volume_plan(child, {}, 10)
        self.assertEqual([(x["first"], x["last"]) for x in story_plan], [(1, 4), (5, 6)])
        publish_plan = publish_volumes.plan_for_task(child, upto=10)
        self.assertEqual([(x["from"], x["to"]) for x in publish_plan], [(1, 4), (5, 6)])

    def test_retry_inherited_outline_cannot_escape_final_chapter(self):
        from app.core import pipeline, store

        task = store.create_task({
            "type": "serial_novel", "title": "重试收束", "goal": "写一本连载",
            "workdir": str(self.workdir), "implementer": "mock-a",
            "critics": ["mock-a", "mock-b"],
            "serial": {"chapters": 8, "words_per_chapter": 800,
                        "early_finish": {"target_chapter": 7}},
        })
        for i in range(1, 5):
            (self.workdir / ("chapter-%02d.md" % i)).write_text(
                "第 %d 章" % i, encoding="utf-8")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        old_outline = {"book_title": "重试收束", "source": "mock",
                       "chapters": [{"title": "第 %d 章" % i,
                                     "beats": "推进", "hook": ""}
                                    for i in range(1, 9)]}
        store.update_run(run["id"], inherit={"outline": old_outline,
                                               "done_chapters": [1, 2, 3, 4]})
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        result = store.get_run(run["id"])
        self.assertEqual(result["status"], "done", result.get("error"))
        self.assertEqual((result.get("verdict") or {}).get("end_chapter"), 7)
        self.assertFalse((self.workdir / "chapter-08.md").exists())
