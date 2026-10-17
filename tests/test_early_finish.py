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

    def test_plan_can_be_updated_and_cancelled(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 7)
        self.assertTrue(ok, err)
        ok, err, updated = store.update_early_finish_task(
            child["id"], target_chapter=9, reason="保留终局伏笔")
        self.assertTrue(ok, err)
        self.assertEqual(updated["serial"]["early_finish"]["target_chapter"], 9)
        self.assertEqual(store.serial_early_finish_plan(task)["target_chapter"], 9)
        self.assertEqual(store.serial_early_finish_plan(child)["reason"], "保留终局伏笔")

        ok, err, cancelled = store.update_early_finish_task(child["id"], cancel=True)
        self.assertTrue(ok, err)
        self.assertTrue(cancelled["serial"].get("early_finish") is None)
        self.assertIsNone(store.serial_early_finish_plan(child))

    def test_plan_actions_from_an_old_batch_follow_latest_batch(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 7)
        self.assertTrue(ok, err)

        ok, err, updated = store.update_early_finish_task(
            task["id"], target_chapter=9, reason="从首批入口调整")
        self.assertTrue(ok, err)
        self.assertEqual(updated["id"], child["id"])
        self.assertEqual(
            store.serial_early_finish_plan(child)["target_chapter"], 9)

        ok, err, cancelled = store.update_early_finish_task(
            task["id"], cancel=True)
        self.assertTrue(ok, err)
        self.assertIsNone(store.serial_early_finish_plan(cancelled))

    def test_persisted_early_finish_targets_keep_the_1_to_500_boundary(self):
        from app.core import store

        valid = store.create_task({
            "type": "serial_novel", "title": "边界计划 1",
            "goal": "写一本连载", "workdir": str(self.workdir),
            "serial": {"chapters": 3, "early_finish": {
                "target_chapter": 1}},
        })
        self.assertEqual(
            (valid.get("serial") or {}).get("early_finish", {}).get("target_chapter"), 1)

        for invalid in (0, 501, 9999):
            task = store.create_task({
                "type": "serial_novel", "title": "非法计划 %s" % invalid,
                "goal": "写一本连载", "workdir": str(self.workdir),
                "serial": {"chapters": 3, "early_finish": {
                    "target_chapter": invalid}},
            })
            self.assertNotIn(
                "early_finish", task.get("serial") or {},
                "非法最终章号 %s 不应被静默改写" % invalid)

    def test_plan_update_rejects_running_and_out_of_range_targets(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 7)
        self.assertTrue(ok, err)
        ok, err, _ = store.update_early_finish_task(child["id"], target_chapter=501)
        self.assertFalse(ok)
        self.assertIn("500", err)

        run = store.create_run("orchestration", child["title"], task_id=child["id"])
        store.update_task_status(child["id"], "queued")
        ok, err, _ = store.update_early_finish_task(child["id"], target_chapter=8)
        self.assertFalse(ok)
        self.assertIn("运行", err)
        store.update_run(run["id"], status="failed", error="test")
        store.update_task_status(child["id"], "failed")

    def test_repeated_same_target_resolves_existing_batch(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 7)
        self.assertTrue(ok, err)
        existing = store.early_finish_existing_task(task["id"], 7)
        self.assertEqual(existing["id"], child["id"])
        self.assertIsNone(store.early_finish_existing_task(task["id"], 8))

    def test_progress_caps_stale_chapters_after_planned_end(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        for i in range(5, 9):
            (self.workdir / ("chapter-%02d.md" % i)).write_text(
                "遗留稿 %d" % i, encoding="utf-8")
        info = store.continue_info(child["id"])
        self.assertEqual(info["last_chapter"], 6)
        self.assertEqual(info["remaining_chapters"], 0)
        self.assertEqual(info["early_finish_state"], "chapters_reached")
        self.assertFalse(info["early_finish_content_complete"])
        self.assertEqual(info["orphaned_chapters"], [7, 8])

    def test_early_finish_requires_latest_successful_review_to_be_complete(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        for i in range(5, 7):
            (self.workdir / ("chapter-%02d.md" % i)).write_text(
                "第 %d 章" % i, encoding="utf-8")

        run = store.create_run("orchestration", child["title"], task_id=child["id"])
        store.update_run(run["id"], status="done", verdict={
            "end_chapter": 6, "publishable": False,
        })
        info = store.continue_info(task["id"])
        self.assertEqual(info["early_finish_state"], "review_failed")
        self.assertTrue(info["early_finish_content_complete"])
        self.assertFalse(info["early_finish_review_passed"])

        store.update_run(run["id"], verdict={
            "end_chapter": 6, "publishable": True,
        })
        info = store.continue_info(task["id"])
        self.assertEqual(info["early_finish_state"], "completed")
        self.assertTrue(info["early_finish_content_complete"])
        self.assertTrue(info["early_finish_review_passed"])

    def test_old_batch_info_reports_latest_batch_state(self):
        from app.core import store

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 7)
        self.assertTrue(ok, err)
        run = store.create_run("orchestration", child["title"], task_id=child["id"])
        store.update_run(run["id"], status="running")

        info = store.continue_info(task["id"])
        self.assertEqual(info["early_finish_latest_task_id"], child["id"])
        self.assertEqual(info["early_finish_latest_task_status"], "running")
        self.assertEqual(info["early_finish_latest_run_status"], "running")
        self.assertEqual(info["early_finish_state"], "running")
        self.assertFalse(info["can"])
        self.assertIn("最新", info["reason"])

    def test_target_cannot_move_before_published_or_scheduled_chapters(self):
        from app.core import store
        from app.core.publish import ledger
        from unittest.mock import patch

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 8)
        self.assertTrue(ok, err)
        with patch.object(ledger, "published_chapters", return_value=set()), \
                patch.object(ledger, "scheduled_chapters", return_value={7}):
            ok, err, _ = store.update_early_finish_task(
                child["id"], target_chapter=6)
        self.assertFalse(ok)
        self.assertIn("排期", err)
        self.assertEqual(store.serial_early_finish_plan(child)["target_chapter"], 8)

    def test_early_finish_publish_progress_is_separate_from_content(self):
        from app.core import store
        from app.core.publish import ledger
        from unittest.mock import patch

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        with patch.object(ledger, "published_chapters", return_value={1, 2}), \
                patch.object(ledger, "scheduled_chapters", return_value={3}), \
                patch.object(ledger, "drafted_chapters", return_value={4}):
            info = store.continue_info(child["id"])
        self.assertFalse(info["early_finish_content_complete"])
        self.assertFalse(info["early_finish_publication_complete"])
        self.assertEqual(info["early_finish_publish_progress"], [{
            "platform": "fanqie", "published": 2, "scheduled": 1,
            "drafted": 1, "outstanding": 2,
        }])

    def test_publish_progress_keeps_bound_platform_after_auto_publish_disabled(self):
        from app.core import store
        from app.core.publish import ledger
        from unittest.mock import patch

        task = self._task()
        ok, err, child = store.early_finish_task(task["id"], 6)
        self.assertTrue(ok, err)
        store.set_auto_publish(task["id"], None)
        with patch.object(ledger, "books_for", return_value={"fanqie": {}}), \
                patch.object(ledger, "published_chapters", return_value={1, 2}), \
                patch.object(ledger, "scheduled_chapters", return_value=set()), \
                patch.object(ledger, "drafted_chapters", return_value=set()):
            info = store.continue_info(child["id"])
        self.assertEqual([row["platform"] for row in info["early_finish_publish_progress"]],
                         ["fanqie"])
        self.assertEqual(info["early_finish_publish_progress"][0]["published"], 2)

    def test_frontend_early_finish_request_is_json_encoded(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("body: JSON.stringify({ target_chapter: target, reason })", source)
        self.assertNotIn("body: { target_chapter: target }", source)
        self.assertIn("earlyFinishContentComplete", source)
        self.assertIn("latestSerialBatch", source)
        self.assertIn("不会自动发布", source)
