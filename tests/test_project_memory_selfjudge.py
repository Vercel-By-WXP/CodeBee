# -*- coding: utf-8 -*-
"""项目记忆自判闸回归。

- 门禁未通过的 run 不入账（验证未执行除外）
- 只有目标/流程/时间套话的 run 不入账
- 同一事实的重复 run 去重，且不推翻人工裁决
- 启动回填只回收「自动批准」的存量垃圾，人工裁决原样保留
"""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from app.core import project_memory


def _make_run(run_id, *, goal="正常任务", verdict=None, steps=None,
              manuscript="", ended_at="2026-10-22 10:00:00"):
    spec = {"title": "标题", "goal": goal}
    if manuscript:
        spec["deliverable"] = manuscript
    return {"id": run_id, "task_id": "t1", "kind": "orchestration",
            "status": "done", "verdict": dict(verdict or {}),
            "steps": list(steps or []), "task_spec": spec, "ended_at": ended_at}


class SelfJudgeTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = str(Path(self._tmp.name) / "ws")
        Path(self.workdir).mkdir(parents=True, exist_ok=True)
        self.task = {"id": "t1", "workdir": self.workdir}

    def _seed_row(self, *, entry_id, content, approval_mode="automatic",
                   status="approved", source_run="r_old"):
        row = {"id": entry_id, "source_run": source_run, "title": "标题",
               "content": content,
               "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
               "facts_sha256": project_memory._facts_digest("标题", content),
               "created_at": time.time() - 100,
               "expires_at": time.time() + project_memory.DEFAULT_TTL_DAYS * 86400,
               "status": status, "version": 1,
               "updated_at": time.time() - 100,
               "reviewed_at": time.time() - 100,
               "approval_mode": approval_mode}
        project_memory._append(project_memory._file(self.workdir), row)
        return row

    def _statuses(self):
        return {row["id"]: row["status"]
                for row in project_memory.list_entries(self.workdir)}


class FailedGateTests(SelfJudgeTestBase):
    def test_publish_gate_failure_not_captured(self):
        run = _make_run("r1", verdict={"publishable": False, "pass": True})
        self.assertIsNone(project_memory.capture_run(self.task, run))
        self.assertEqual(self._statuses(), {})

    def test_each_hard_gate_failure_not_captured(self):
        cases = [
            {"pass": False},
            {"review_pass": False},
            {"verify_pass": False, "verify_ran": True},
            {"pass": True, "review_pass": True, "publishable": False},
        ]
        for index, verdict in enumerate(cases):
            with self.subTest(verdict=verdict):
                workdir = str(Path(self._tmp.name) / ("ws%d" % index))
                Path(workdir).mkdir(parents=True, exist_ok=True)
                task = {"id": "t1", "workdir": workdir}
                run = _make_run("r%d" % index, verdict=verdict,
                                manuscript="out.md")
                self.assertIsNone(project_memory.capture_run(task, run))
                self.assertEqual(
                    {row["status"] for row in project_memory.list_entries(workdir)},
                    set())

    def test_verify_not_ran_is_not_a_hard_failure(self):
        run = _make_run("r1", verdict={"verify_pass": False, "verify_ran": False},
                        manuscript="out.md")
        row = project_memory.capture_run(self.task, run)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "approved")
        self.assertIn("验证：未执行独立验证", row["content"])


class BoilerplateOnlyTests(SelfJudgeTestBase):
    def test_goal_and_engine_only_run_not_captured(self):
        run = _make_run("r1", goal="把这个http://127.0.0.1:8765 搞一个演示")
        self.assertIsNone(project_memory.capture_run(self.task, run))
        self.assertEqual(self._statuses(), {})

    def test_substantive_run_still_captured(self):
        run = _make_run("r1", verdict={"pass": True, "review_pass": True},
                        manuscript="chapters/ch21-30.md")
        row = project_memory.capture_run(self.task, run)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "approved")
        self.assertIn("稿件文件：chapters/ch21-30.md", row["content"])


class RerunDedupTests(SelfJudgeTestBase):
    def test_identical_rerun_collapses_into_first_entry(self):
        first = project_memory.capture_run(
            self.task, _make_run("r1", verdict={"pass": True},
                                 manuscript="out.md"))
        second = project_memory.capture_run(
            self.task, _make_run("r2", verdict={"pass": True},
                                 manuscript="out.md",
                                 ended_at="2026-10-22 11:00:00"))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self._statuses()), 1)

    def test_new_facts_still_create_entries(self):
        project_memory.capture_run(
            self.task, _make_run("r1", verdict={"pass": True},
                                 manuscript="out.md"))
        project_memory.capture_run(
            self.task, _make_run("r2", verdict={"pass": True,
                                                "review_pass": True},
                                 manuscript="out.md"))
        self.assertEqual(len(self._statuses()), 2)

    def test_archived_facts_are_not_reingested(self):
        first = project_memory.capture_run(
            self.task, _make_run("r1", verdict={"pass": True},
                                 manuscript="out.md"))
        project_memory.decide(self.workdir, first["id"], "archive")
        again = project_memory.capture_run(
            self.task, _make_run("r2", verdict={"pass": True},
                                 manuscript="out.md",
                                 ended_at="2026-10-22 11:00:00"))
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["status"], "archived")
        self.assertEqual(len(self._statuses()), 1)


class BackfillSweepTests(SelfJudgeTestBase):
    def test_automatic_junk_demoted_manual_decision_untouched(self):
        junk = ("- 任务目标：把这个http://127.0.0.1:8765 搞一个演示\n"
                "- 任务流程：review；执行已完成\n"
                "- 发布门禁：未通过\n"
                "- 完成时间：2026-10-21 17:09:33")
        boiler = ("- 任务目标：随便聊聊\n"
                  "- 任务流程：direct；执行已完成\n"
                  "- 直连任务未执行独立验证或评审，执行完成不代表质量验收通过\n"
                  "- 完成时间：2026-10-21 18:00:00")
        good = ("- 任务目标：批量起草\n"
                "- 任务流程：serial；执行已完成\n"
                "- 质量门禁：通过\n"
                "- 本次章节范围：21–30\n"
                "- 完成时间：2026-10-21 19:00:00")
        self._seed_row(entry_id="junk_auto", content=junk)
        self._seed_row(entry_id="junk_manual", content=boiler,
                       approval_mode="manual")
        self._seed_row(entry_id="good_auto", content=good)
        result = project_memory.backfill([self.task], [])
        self.assertEqual(result["demoted"], 1)
        statuses = self._statuses()
        self.assertEqual(statuses["junk_auto"], "archived")
        self.assertEqual(statuses["junk_manual"], "approved")
        self.assertEqual(statuses["good_auto"], "approved")
        rows = {row["id"]: row for row in
                project_memory.list_entries(self.workdir)}
        self.assertIn("自判", rows["junk_auto"].get("demoted_reason", ""))

    def test_demoted_entry_stops_injection_but_restorable(self):
        junk = ("- 任务目标：演示\n- 任务流程：review；执行已完成\n"
                "- 发布门禁：未通过\n- 完成时间：2026-10-21 17:09:33")
        row = self._seed_row(entry_id="junk_auto", content=junk)
        project_memory.backfill([self.task], [])
        self.assertEqual(
            project_memory.active_text(self.workdir, cap=4000), "")
        restored = project_memory.decide(self.workdir, row["id"], "restore")
        self.assertEqual(restored["status"], "approved")

    def test_backfill_dedups_identical_facts_across_runs(self):
        run_a = _make_run("r1", verdict={"pass": True}, manuscript="out.md")
        run_b = _make_run("r2", verdict={"pass": True}, manuscript="out.md",
                          ended_at="2026-10-22 11:00:00")
        result = project_memory.backfill([self.task], [run_a, run_b])
        self.assertEqual(result["runs"], 1)
        self.assertEqual(len(self._statuses()), 1)


if __name__ == "__main__":
    unittest.main()
