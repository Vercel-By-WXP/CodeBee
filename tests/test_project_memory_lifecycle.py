# -*- coding: utf-8 -*-
"""Project memory recovery and task-engine integration, using isolated workspaces."""
import json
from unittest.mock import patch

from base import BaseTest


class TestProjectMemoryLifecycle(BaseTest):
    def task_run(self, engine="direct", **verdict):
        task = {"id": "task-1", "title": "Project task", "goal": "Keep project conventions",
                "workdir": str(self.workdir), "engine": engine}
        run = {"id": "run-1", "task_id": task["id"], "kind": "orchestration",
               "status": "done", "verdict": dict(engine=engine, **verdict)}
        return task, run

    def test_completed_engines_create_approved_memory_with_truthful_quality(self):
        from app.core import project_memory

        for engine in ("direct", "code", "review"):
            task, run = self.task_run(engine, publishable=False)
            run["id"] = "run-" + engine
            row = project_memory.capture_run(task, run)
            self.assertEqual(row["status"], "approved")
            self.assertEqual(row["source_run"], run["id"])
            self.assertIn(task["goal"], row["content"])
            if engine == "direct":
                self.assertIn("未执行独立验证或评审", row["content"])
            else:
                self.assertIn("发布门禁：未通过", row["content"])
        self.assertEqual(len(project_memory.list_entries(self.workdir)), 3)
        self.assertIn("Keep project conventions", project_memory.active_text(self.workdir))

    def test_approve_pending_approves_only_valid_unexpired_candidates(self):
        from app.core import project_memory

        valid = dict(project_memory.propose(self.workdir, facts=["VALID"], now=1000), status="pending")
        expired = dict(project_memory.propose(self.workdir, facts=["EXPIRED"], now=1000), status="pending")
        project_memory._append(project_memory._file(self.workdir), valid)
        project_memory._append(project_memory._file(self.workdir),
                               dict(expired, expires_at=1000))
        malformed = dict(valid, id="malformed", content="TAMPERED", version="bad")
        project_memory._append(project_memory._file(self.workdir), malformed)

        result = project_memory.approve_pending(self.workdir, now=1000 + 1)
        self.assertEqual(result["approved_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["invalid_count"], 1)
        statuses = {row["id"]: row["status"]
                    for row in project_memory.list_entries(self.workdir)}
        self.assertEqual(statuses[valid["id"]], "approved")
        self.assertEqual(statuses[expired["id"]], "pending")
        self.assertEqual(statuses["malformed"], "pending")

    def test_backfill_promotes_existing_pending_candidates(self):
        from app.core import project_memory

        task, _ = self.task_run()
        row = project_memory.propose(self.workdir, source_run="legacy-run",
                                     facts=["PROMOTE-ON-STARTUP"])
        project_memory._append(project_memory._file(self.workdir),
                               dict(row, status="pending"))

        result = project_memory.backfill([task], [])

        self.assertEqual(result["errors"], 0)
        current = project_memory.list_entries(self.workdir)[0]
        self.assertEqual(current["status"], "approved")
        self.assertEqual(current["version"], 2)
        self.assertIn("PROMOTE-ON-STARTUP", project_memory.active_text(self.workdir))

    def test_incomplete_or_management_runs_do_not_create_candidates(self):
        from app.core import project_memory

        task, run = self.task_run()
        for status in ("running", "queued", "failed", "cancelled", "timeout"):
            self.assertIsNone(project_memory.capture_run(task, dict(run, status=status)))
        self.assertIsNone(project_memory.capture_run(task, dict(run, verdict={"partial": True})))
        self.assertIsNone(project_memory.capture_run(task, dict(run, kind="management")))
        self.assertIsNone(project_memory.capture_run(dict(task, workdir=""), run))
        self.assertFalse((self.workdir / ".codebee").exists())

    def test_missing_workspace_never_falls_back_to_process_directory(self):
        from app.core import project_memory

        for value in (None, "", "   "):
            with self.assertRaises(ValueError):
                project_memory.list_entries(value)

    def test_corrupt_utf8_record_does_not_hide_neighboring_memories(self):
        from app.core import project_memory

        first = project_memory.propose(self.workdir, facts=["FIRST"])
        with project_memory._file(self.workdir).open("ab") as handle:
            handle.write(b'{"id":"broken","content":"\xff"}\n')
        second = project_memory.propose(self.workdir, facts=["SECOND"])
        self.assertEqual({row["id"] for row in project_memory.list_entries(self.workdir)},
                         {first["id"], second["id"]})

    def test_malformed_latest_record_is_visible_but_never_injected(self):
        import json
        from app.core import project_memory

        first = project_memory.propose(self.workdir, facts=["MUST-NOT-RESURRECT"])
        project_memory._append(project_memory._file(self.workdir),
                               dict(first, status="pending"))
        project_memory.decide(self.workdir, first["id"], "approve")
        valid = project_memory.propose(self.workdir, facts=["STILL-VALID"])
        project_memory._append(project_memory._file(self.workdir),
                               dict(valid, status="pending"))
        project_memory.decide(self.workdir, valid["id"], "approve")
        bad = dict(first, status="approved", content=42, expires_at="bad", created_at=[],
                   feedback_count="bad", feedback_score="bad", version="bad")
        with project_memory._file(self.workdir).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(bad) + "\n")
        entries = project_memory.list_entries(self.workdir)
        self.assertEqual(len(entries), 2)
        malformed = next(row for row in entries if row["id"] == first["id"])
        self.assertFalse(malformed["verified"])
        self.assertTrue(malformed["expired"])
        text = project_memory.active_text(self.workdir)
        self.assertIn("STILL-VALID", text)
        self.assertNotIn("MUST-NOT-RESURRECT", text)

    def test_nonfinite_expiry_cannot_be_approved(self):
        import json
        from app.core import project_memory

        row = project_memory.propose(self.workdir, facts=["Fact"])
        with project_memory._file(self.workdir).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row, expires_at=float("nan"))) + "\n")
        with self.assertRaises(ValueError):
            project_memory.decide(self.workdir, row["id"], "approve")

    def test_historical_workspace_change_does_not_leak_facts_into_new_project(self):
        from app.core import project_memory

        task, run = self.task_run()
        old_workdir = self.tmp / "previous-project"
        old_workdir.mkdir()
        run["execution_snapshot"] = {"workdir": str(old_workdir)}
        self.assertIsNone(project_memory.capture_run(task, run))
        self.assertEqual(project_memory.backfill([task], [run])["runs"], 0)
        self.assertFalse((self.workdir / ".codebee").exists())

    def test_project_index_reads_each_workspace_once_including_empty_ones(self):
        from app.core import project_memory

        project_memory.propose(self.workdir, facts=["Fact"])
        empty = self.tmp / "empty"
        empty.mkdir()
        tasks = [{"id": str(n), "workdir": str(self.workdir if n % 2 else empty)} for n in range(20)]
        with patch.object(project_memory, "list_entries", wraps=project_memory.list_entries) as read:
            projects = project_memory.project_index(tasks)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(len(projects), 1)
        self.assertEqual(len(projects[0]["task_ids"]), 10)

    def test_concurrent_decisions_enforce_version_once(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        from app.core import project_memory

        row = project_memory.propose(self.workdir, facts=["Fact"])
        project_memory._append(project_memory._file(self.workdir),
                               dict(row, status="pending"))
        append = project_memory._append

        def delayed_append(*args):
            time.sleep(.02)
            return append(*args)

        def decide(action):
            try:
                project_memory.decide(self.workdir, row["id"], action, expected_version=1)
                return True
            except ValueError:
                return False

        with patch.object(project_memory, "_append", side_effect=delayed_append):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(decide, ["approve", "reject"]))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(project_memory.list_entries(self.workdir)[0]["version"], 2)

    def test_concurrent_feedback_does_not_lose_votes(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        from app.core import project_memory

        row = project_memory.propose(self.workdir, facts=["Fact"])
        project_memory._append(project_memory._file(self.workdir),
                               dict(row, status="pending"))
        project_memory.decide(self.workdir, row["id"], "approve")
        append = project_memory._append

        def delayed_append(*args):
            time.sleep(.01)
            return append(*args)

        with patch.object(project_memory, "_append", side_effect=delayed_append):
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda _: project_memory.feedback(self.workdir, row["id"], useful=True), range(12)))
        latest = project_memory.list_entries(self.workdir)[0]
        self.assertEqual(latest["feedback_count"], 12)
        self.assertEqual(latest["feedback_score"], 12)
        self.assertEqual(latest["version"], 14)

    def test_backfill_preserves_review_state_and_uses_all_runs(self):
        from app.core import project_memory

        task, run = self.task_run()
        self.assertEqual(project_memory.backfill([task], [run])["runs"], 1)
        row = project_memory.list_entries(self.workdir)[0]
        project_memory._append(project_memory._file(self.workdir),
                               dict(row, status="pending"))
        project_memory.decide(self.workdir, row["id"], "reject", expected_version=1)
        task["goal"] = "An edited goal must not replace an earlier rejection"
        self.assertEqual(project_memory.backfill([task], [run])["runs"], 0)
        self.assertEqual(project_memory.capture_run(task, run)["status"], "rejected")
        self.assertEqual(len(project_memory.list_entries(self.workdir)), 1)
        projects = project_memory.project_index([task])
        self.assertEqual(projects[0]["entries"][0]["status"], "rejected")

    def test_legacy_markdown_migrates_once_with_automatic_approval(self):
        from app.core import project_memory

        legacy = self.workdir / ".codebee" / "project-memory.md"
        legacy.parent.mkdir()
        original = "# 项目记忆\n\n### Old task · 2026-10-01 12:00:00\n- Uses SQLite\n- 验收：通过\n"
        legacy.write_text(original, encoding="utf-8")
        self.assertEqual(project_memory.migrate_legacy(self.workdir), 1)
        self.assertEqual(project_memory.migrate_legacy(self.workdir), 0)
        row = project_memory.list_entries(self.workdir)[0]
        self.assertEqual(row["title"], "Old task")
        self.assertEqual(row["status"], "approved")
        self.assertIn("Uses SQLite", row["content"])
        self.assertIn("Uses SQLite", project_memory.active_text(self.workdir))
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_backfill_preserves_decisions_older_than_5000_journal_lines(self):
        import json
        from app.core import project_memory

        task, run = self.task_run()
        row = project_memory.capture_run(task, run)
        project_memory._append(project_memory._file(self.workdir),
                               dict(row, status="pending"))
        project_memory.decide(self.workdir, row["id"], "reject")
        path = project_memory._file(self.workdir)
        with path.open("a", encoding="utf-8") as handle:
            for n in range(5001):
                handle.write(json.dumps({"id": "other", "source_run": "other", "version": n}) + "\n")
        before = path.stat().st_size
        self.assertEqual(project_memory.backfill([task], [run])["runs"], 0)
        self.assertEqual(project_memory.capture_run(task, run)["status"], "rejected")
        self.assertEqual(path.stat().st_size, before)

    def test_malformed_source_does_not_crash_backfill_or_resurrect_reviewed_ids(self):
        from app.core import project_memory

        task, run = self.task_run()
        path = project_memory._file(self.workdir)
        path.parent.mkdir(exist_ok=True)
        path.with_name("project-memory.md").write_text("### Legacy\n- legacy fact\n", encoding="utf-8")
        project_memory.migrate_legacy(self.workdir)
        captured = project_memory.capture_run(task, run)
        for row in project_memory._read(path).values():
            project_memory._append(path, dict(row, status="pending"))
            rejected = project_memory.decide(self.workdir, row["id"], "reject")
            project_memory._append(path, dict(rejected, source_run=[] if row["id"] == captured["id"] else {}))
        before = path.stat().st_size
        result = project_memory.backfill([task], [run, dict(run, id="new-run")])
        self.assertEqual(result, {"legacy": 0, "runs": 1, "errors": 0})
        self.assertEqual(project_memory.capture_run(task, run)["status"], "rejected")
        entries = project_memory.list_entries(self.workdir)
        self.assertEqual(sum(row["status"] == "rejected" for row in entries), 2)
        self.assertGreater(path.stat().st_size, before)

    def test_invalid_unicode_content_does_not_hide_valid_neighbors(self):
        from app.core import project_memory

        good = project_memory.propose(self.workdir, facts=["valid neighbor"])
        path = project_memory._file(self.workdir)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(good, id="bad-unicode", content="\ud800")) + "\n")
        entries = {row["id"]: row for row in project_memory.list_entries(self.workdir)}
        self.assertTrue(entries[good["id"]]["verified"])
        self.assertFalse(entries["bad-unicode"]["verified"])
        self.assertIn("格式无效", entries["bad-unicode"]["content"])
        task, _run = self.task_run()
        self.assertEqual(project_memory.project_index([task])[0]["counts"]["total"], 2)
        with self.assertRaisesRegex(ValueError, "指纹校验失败"):
            project_memory.decide(self.workdir, "bad-unicode", "approve")
        with self.assertRaisesRegex(ValueError, "指纹校验失败"):
            project_memory.feedback(self.workdir, "bad-unicode", useful=True)

    def test_skipped_verification_is_not_recorded_as_passed(self):
        from app.core import project_memory

        task, run = self.task_run("code", verify_ran=False, verify_pass=True)
        row = project_memory.capture_run(task, run)
        self.assertIn("验证：未执行独立验证", row["content"])
        self.assertNotIn("验证：通过", row["content"])

    def test_historical_capture_uses_pinned_inputs_instead_of_edited_task(self):
        from app.core import project_memory

        task, run = self.task_run("review")
        task.update(goal="EDITED-GOAL", title="EDITED-TITLE", manuscript="edited.md",
                    verify_command="edited-command")
        run["task_spec"] = {"goal": "ORIGINAL-GOAL", "title": "ORIGINAL-TITLE",
                            "deliverable": "original.md"}
        run["execution_snapshot"] = {"verify_command": "original-command"}
        run["steps"] = [{"role": "verify", "status": "done"}]
        row = project_memory.capture_run(task, run)
        self.assertEqual(row["title"], "ORIGINAL-TITLE")
        self.assertIn("ORIGINAL-GOAL", row["content"])
        self.assertIn("original.md", row["content"])
        self.assertIn("original-command", row["content"])
        self.assertNotIn("EDITED", row["content"])
        self.assertNotIn("edited", row["content"])

    def test_unknown_historical_verify_command_does_not_use_current_task_command(self):
        from app.core import project_memory

        task, run = self.task_run("code")
        task["verify_command"] = "NEW-COMMAND"
        run["steps"] = [{"role": "verify", "status": "done"}]
        row = project_memory.capture_run(task, run)
        self.assertIn("历史命令未记录", row["content"])
        self.assertNotIn("NEW-COMMAND", row["content"])

    def test_batch_backfill_reads_journal_once_per_workspace(self):
        from app.core import project_memory

        task, run = self.task_run()
        runs = [dict(run, id="run-%d" % n) for n in range(100)]
        with patch.object(project_memory, "_read", wraps=project_memory._read) as read:
            result = project_memory.backfill([task], runs)
        self.assertEqual(result["runs"], 100)
        self.assertEqual(read.call_count, 1)

    def test_backfill_isolates_unavailable_workspace(self):
        from app.core import project_memory

        task, run = self.task_run()
        missing = dict(task, id="missing", workdir=str(self.workdir / "missing"))
        result = project_memory.backfill([missing, task], [run])
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["runs"], 1)

    def test_legacy_symlink_is_rejected(self):
        from app.core import project_memory

        outside = self.tmp / "outside.md"
        outside.write_text("### outside\n- never import\n", encoding="utf-8")
        legacy = self.workdir / ".codebee" / "project-memory.md"
        legacy.parent.mkdir()
        try:
            legacy.symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks require privileges on this host")
        with self.assertRaises(ValueError):
            project_memory.migrate_legacy(self.workdir)

    def test_historical_age_is_preserved_and_expired_memory_cannot_activate(self):
        from app.core import project_memory

        task, run = self.task_run()
        run["ended_at"] = "2020-01-01 12:00:00"
        project_memory.backfill([task], [run])
        row = project_memory.list_entries(self.workdir)[0]
        self.assertTrue(row["expired"])
        with self.assertRaises(ValueError):
            project_memory.decide(self.workdir, row["id"], "approve")

    def test_full_pipelines_capture_memory_for_code_direct_and_writing(self):
        from app.core import pipeline, project_memory, store

        pipeline._agents = self.mock_agents
        for kind in ("code", "direct", "novel"):
            workdir = self.workdir / kind
            workdir.mkdir()
            task = store.create_task({"type": kind, "title": kind, "goal": "Complete task",
                                      "workdir": str(workdir), "implementer": "mock-a",
                                      "verify_command": "exit 0", "critics": ["mock-b"],
                                      "rounds": 1, "threshold": 1.0})
            run = store.create_run("orchestration", kind, task_id=task["id"])
            with patch.object(pipeline.skills, "learn_async"), \
                    patch.object(pipeline.knowledge, "learn_async"):
                pipeline.execute_run(run["id"])
            self.assertEqual(store.get_run(run["id"])["status"], "done")
            entries = project_memory.list_entries(workdir)
            self.assertEqual(len(entries), 1, kind)
            self.assertEqual(entries[0]["source_run"], run["id"])
            self.assertEqual(entries[0]["status"], "approved")
            self.assertEqual(store.get_run(run["id"])["project_memory_id"], entries[0]["id"])

    def test_noncode_memory_is_loaded_after_selected_git_checkout(self):
        from app.core import gitmod, pipeline, project_memory, store

        previous = project_memory.propose(self.workdir, facts=["OLD-BRANCH-FACT"])
        task = store.create_task({"type": "direct", "title": "Branch task", "goal": "Use selected branch",
                                  "workdir": str(self.workdir), "implementer": "mock-a", "git_rev": "selected"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        seen = {}

        def checkout(*args):
            project_memory.decide(self.workdir, previous["id"], "archive")
            selected = project_memory.propose(self.workdir, facts=["SELECTED-BRANCH-FACT"])
            return True, "", None

        def direct(_run, current_task, *_args):
            seen["context"] = current_task["context"]
            store.update_run(run["id"], status="done", verdict={"engine": "direct", "pass": True})

        pipeline._agents = self.mock_agents
        with patch.object(gitmod, "prepare_checkout", side_effect=checkout), \
                patch.object(pipeline, "_run_direct", side_effect=direct), \
                patch.object(pipeline.skills, "learn_async"), \
                patch.object(pipeline.knowledge, "learn_async"):
            pipeline.execute_run(run["id"])
        self.assertIn("SELECTED-BRANCH-FACT", seen["context"])
        self.assertNotIn("OLD-BRANCH-FACT", seen["context"])

    def test_approved_memory_reaches_direct_prompt_and_held_does_not(self):
        from app.core import pipeline, project_memory, store

        row = project_memory.propose(self.workdir, facts=["APPROVED-CONVENTION"])
        held = project_memory.propose(self.workdir, facts=["HELD-CONVENTION"])
        project_memory._append(project_memory._file(self.workdir),
                               dict(held, status="pending"))
        task = store.create_task({"type": "direct", "title": "Direct", "goal": "Apply convention",
                                  "workdir": str(self.workdir), "implementer": "mock-a"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        prompts = []

        def fake_step(*args, **kwargs):
            prompts.append(args[3])
            return {"ok": True, "text": "Done", "sid": ""}

        pipeline._agents = self.mock_agents
        with patch.object(pipeline, "_run_step", side_effect=fake_step), \
                patch.object(pipeline.skills, "learn_async"), \
                patch.object(pipeline.knowledge, "learn_async"):
            pipeline.execute_run(run["id"])
        self.assertTrue(prompts)
        self.assertIn("APPROVED-CONVENTION", prompts[0])
        self.assertNotIn("HELD-CONVENTION", prompts[0])

    def test_capture_failure_is_visible_without_failing_completed_run(self):
        from app.core import pipeline, project_memory, store

        task = store.create_task({"type": "direct", "title": "Direct", "goal": "Complete task",
                                  "workdir": str(self.workdir), "implementer": "mock-a"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        with patch.object(project_memory, "capture_run", side_effect=OSError("read only")), \
                patch.object(pipeline.skills, "learn_async"), \
                patch.object(pipeline.knowledge, "learn_async"):
            pipeline.execute_run(run["id"])
        final = store.get_run(run["id"])
        self.assertEqual(final["status"], "done")
        self.assertTrue(any("项目记忆保存失败" in warning for warning in final["warnings"]))
