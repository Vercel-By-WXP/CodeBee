"""Rank-scan review gates, immutable handoff, and isolated book creation."""
import json
from unittest.mock import patch

from base import BaseTest


def proposal():
    return {"schema_version": 1, "candidates": [{
        "id": "c1", "title": "借命修城", "platform": "七猫男频（要求待核实）",
        "audience": "喜欢有代价选择的悬疑读者", "genre": "都市悬疑",
        "premise": "修城工以寿命换取城内失踪者的回归", "mechanism": "修补建筑会消耗相关人的记忆",
        "differentiation": "选择救人即会损害对方关于自己的记忆；市场比较待精读",
        "protagonist": "修城工决定牺牲母亲对自己的记忆救同事，追索失踪案",
        "opening_event": "塌楼中先救同事导致母亲认不出自己，必须面对失去亲情的代价",
        "first_three_chapters": ["救同事付出代价", "同事提供矛盾证词", "完成首案但失去修补许可"],
        "milestones": {"20000": "验证选择与情绪回报", "30000": "首案闭环且引出新主线"},
        "long_term_engine": "三单元分别处理亲情、公共利益、制度代价，回收记忆伏笔",
        "story_bible": "# 故事圣经\n修城工第一人称。代价不可撤销。母亲已忘记主角身份。",
        "risks": ["尚未核实平台要求与同类机制，试写前补精读"],
    }]}


def report(data=None):
    return "# 选材报告\n内部立项建议，市场待验证。\n```json\n" + json.dumps(
        data or proposal(), ensure_ascii=False) + "\n```"


class SelectionHandoffTests(BaseTest):
    def source(self):
        from app.core import selection, selection_launch as handoff, store
        task = store.create_task({"type": "rank_scan", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        ready = selection.finalize(run["id"], task, report(), True, store.run_dir(run["id"]))
        store.update_run(run["id"], status="done", verdict=ready)
        store.update_task_status(task["id"], "done")
        return task, run

    def test_review_major_triggers_revision_even_with_high_scores(self):
        from app.core import mocks, pipeline, store
        task = store.create_task({"type": "rank_scan", "goal": "都市悬疑", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents

        def critique(agent_id, round_no, dims, threshold):
            return {"scores": {d: 9 for d in dims}, "issues": ([
                {"severity": "major", "dim": dims[0], "note": "应更改事件后果"}
            ] if round_no == 1 else []), "summary": "复核"}

        with patch.object(mocks, "draft_manuscript", return_value=report()), \
                patch.object(mocks, "critique", side_effect=critique):
            pipeline.execute_run(run["id"])
        result = store.get_run(run["id"])
        self.assertTrue(result["verdict"]["selection_ready"])
        self.assertEqual(result["verdict"]["rounds_used"], 2)
        self.assertTrue(any(s["role"] == "revise-r1" for s in result["steps"]))

    def test_malformed_package_cannot_pass_high_scoring_review(self):
        from app.core import mocks, pipeline, store
        task = store.create_task({"type": "rank_scan", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        with patch.object(mocks, "draft_manuscript", return_value="只写了热门题材"), \
                patch.object(mocks, "critique", side_effect=lambda a, r, ds, t: {
                    "scores": {d: 10 for d in ds}, "issues": [], "summary": "高分但交接缺项"}):
            pipeline.execute_run(run["id"])
        result = store.get_run(run["id"])
        self.assertIsNotNone(result.get("verdict"), result.get("error"))
        self.assertFalse(result["verdict"]["publishable"])
        self.assertFalse(result["verdict"]["selection_ready"])

    def test_invalid_review_scores_cannot_open_a_book(self):
        from app.core import mocks, pipeline, store
        task = store.create_task({"type": "rank_scan", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        with patch.object(mocks, "draft_manuscript", return_value=report()), \
                patch.object(mocks, "critique", side_effect=lambda a, r, ds, t: {
                    "scores": {d: 11 for d in ds}, "issues": [], "summary": "越界评分"}):
            pipeline.execute_run(run["id"])
        self.assertFalse(store.get_run(run["id"])["verdict"]["selection_ready"])

    def test_launch_uses_reviewed_snapshot_and_book_title_folder(self):
        from app.core import selection, selection_launch as handoff, store
        task, run = self.source()
        (self.workdir / "rank-scan.md").write_text("未审核的最新改稿", encoding="utf-8")
        seen = []

        def start(payload):
            seen.append(payload)
            child = store.create_task(payload)
            child_run = store.create_run("orchestration", child["title"], task_id=child["id"])
            return 200, {"task_id": child["id"], "run_id": child_run["id"]}

        body = {"run_id": run["id"], "candidate_id": "c1", "chapters": 10, "words_per_chapter": 3000}
        status, result = handoff.launch_serial(task["id"], body, start)
        self.assertEqual(status, 200)
        target = self.workdir / "借命修城"
        self.assertEqual(result["workdir"], str(target.resolve()))
        self.assertTrue((target / "selection-brief.md").is_file())
        self.assertEqual((target / "story-bible.md").read_text("utf-8").strip(),
                         proposal()["candidates"][0]["story_bible"])
        self.assertEqual(seen[0]["serial"], {"chapters": 10, "words_per_chapter": 3000})
        self.assertIn("牺牲母亲", seen[0]["context"])
        self.assertNotIn("未审核的最新改稿", seen[0]["context"])
        _, again = handoff.launch_serial(task["id"], body, start)
        self.assertTrue(again["reused"])
        self.assertEqual(len(seen), 1)

    def test_existing_directory_and_invalid_parameters_do_not_write(self):
        from app.core import selection, selection_launch as handoff
        task, run = self.source()
        body = {"run_id": run["id"], "candidate_id": "c1"}
        for extras in ({"chapters": 21}, {"chapters": 2.5}, {"chapters": True},
                       {"words_per_chapter": 0}, {"candidate_id": "unknown"}):
            with self.assertRaises(ValueError):
                handoff.launch_serial(task["id"], dict(body, **extras), lambda p: self.fail("created"))
        target = self.workdir / "借命修城"
        target.mkdir()
        sentinel = target / "manuscript.md"
        sentinel.write_text("已有作品", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "同名书目录"):
            handoff.launch_serial(task["id"], body, lambda p: self.fail("created"))
        self.assertEqual(sentinel.read_text("utf-8"), "已有作品")

    def test_unsafe_titles_are_rejected(self):
        from app.core import selection, selection_launch as handoff
        for title in ("../旧书", "A/B", "CON", "nul.txt", "书名.", "C:\\书名", "..", "a\x00b"):
            data = proposal()
            data["candidates"][0]["title"] = title
            self.assertTrue(selection.validate_package(data), title)

    def test_old_unreviewed_scan_cannot_launch(self):
        from app.core import selection, selection_launch as handoff, store
        task = store.create_task({"type": "rank_scan", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="done", verdict={"pass": True, "engine": "direct"})
        with self.assertRaisesRegex(ValueError, "新版选材"):
            handoff.selection_info(task["id"])

    def test_retry_of_legacy_direct_scan_runs_review_without_rewriting_task(self):
        from app.core import pipeline, store
        task = store.create_task({"type": "rank_scan", "workdir": str(self.workdir)})
        task["engine"] = "direct"
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        seen = []

        def capture(current, normalized, *args):
            seen.append(normalized)
            store.update_run(current["id"], status="done")

        with patch.object(pipeline, "_run_content_review", side_effect=capture), \
                patch.object(pipeline, "_run_direct") as direct:
            pipeline.execute_run(run["id"])
        direct.assert_not_called()
        self.assertEqual(seen[0]["engine"], "review")
        self.assertEqual(seen[0]["manuscript"], "rank-scan.md")
        self.assertEqual(task["engine"], "direct")

    def test_package_tampering_rejected(self):
        from app.core import selection, selection_launch as handoff, store
        task, run = self.source()
        target = store.run_dir(run["id"]) / selection.PACKAGE_FILE
        data = json.loads(target.read_text("utf-8"))
        data["candidates"][0]["premise"] = "替换未审核主线"
        target.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "审核快照"):
            handoff.selection_info(task["id"])

    def test_fast_mode_keeps_revision_and_review(self):
        from app.core import task_compile
        wf = task_compile.content_workflow({"type": "rank_scan", "rounds": 1}, "easy", mode="fast")
        self.assertGreaterEqual(wf["review_rounds"], 2)
        self.assertEqual(wf["reviewers"], 2)

    def test_failed_queue_keeps_recoverable_child_and_never_creates_a_duplicate(self):
        from app.core import selection, selection_launch as handoff, store
        task, run = self.source()
        calls = []

        def fail_queue(payload):
            child = store.create_task(payload)
            calls.append(child)
            store.update_task_status(child["id"], "failed")
            return 503, {"error": "queue busy", "task_id": child["id"]}

        body = {"run_id": run["id"], "candidate_id": "c1"}
        status, result = handoff.launch_serial(task["id"], body, fail_queue)
        self.assertEqual(status, 503)
        self.assertIn("已保留新书任务", result["error"])
        again_status, again = handoff.launch_serial(task["id"], body, fail_queue)
        self.assertEqual(again_status, 503)
        self.assertEqual(again["task_id"], result["task_id"])
        self.assertEqual(len(calls), 1)

    def test_newest_failed_review_does_not_silently_launch_old_proposal(self):
        from app.core import selection, selection_launch as handoff, store
        task, run = self.source()
        newer = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(newer["id"], status="done", verdict={"selection_ready": False})
        with self.assertRaisesRegex(ValueError, "尚未通过审核"):
            handoff.selection_info(task["id"])
        self.assertEqual(handoff.selection_info(task["id"], run["id"])["run_id"], run["id"])
