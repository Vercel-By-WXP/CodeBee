# -*- coding: utf-8 -*-
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import gitmod, pipeline, review_findings, review_storage


DIFF = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 import os
+value = load()
 print(value)
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
"""


def _finding(**overrides):
    raw = {"path": "src/app.py", "line_start": 2, "severity": "major",
           "header": "未校验输入", "detail": "load() 的返回值被直接使用"}
    raw.update(overrides)
    return review_findings.normalize(raw)


def _review_text(**overrides):
    body = {"pass": True,
            "scores": {"正确性": 8, "可维护性": 8, "安全": 8},
            "findings": [], "summary": "本轮覆盖完整"}
    body.update(overrides)
    return "```json\n%s\n```" % json.dumps(body, ensure_ascii=False)


def _coverage(**overrides):
    coverage = {"repo": True, "changed_files": 2,
                "reviewed_files": ["src/app.py", "new.py"],
                "omitted_files": [], "ignored_files": [],
                "partial_files": [], "truncated": False,
                "diff_chars": len(DIFF), "diff_limit": 200000,
                "complete": True}
    coverage.update(overrides)
    return coverage


def _changes(coverage=None, diff=DIFF):
    return {"files": [{"path": "src/app.py"}, {"path": "new.py"}],
            "diff": diff, "add_total": 3, "del_total": 0,
            "coverage": coverage or _coverage()}


class FindingSchemaTests(unittest.TestCase):
    def test_normalize_identity_survives_line_drift_and_aliases(self):
        first = _finding(line_start=2, line_end=2)
        second = _finding(line_start=87, line_end=90)
        self.assertEqual(first["finding_id"], second["finding_id"])
        self.assertEqual(first["severity"], "major")
        legacy = review_findings.normalize({
            "path": "src\\app.py", "line": 9, "line_end": 4,
            "severity": "critical", "title": "问题", "message": "细节"})
        self.assertEqual(legacy["path"], "src/app.py")
        self.assertEqual((legacy["line_start"], legacy["line_end"]), (4, 9))
        self.assertEqual(legacy["severity"], "blocker")

    def test_schema_rejects_non_boolean_pass_bad_scores_and_bad_findings(self):
        self.assertFalse(review_findings.validate_review(None)["ok"])
        for body in (
                {"pass": "true", "scores": {"a": 8}, "findings": []},
                {"pass": True, "scores": {"a": 11}, "findings": []},
                {"pass": True, "scores": {"a": 8}, "findings": "none"},
                {"pass": True, "scores": {"a": 8},
                 "findings": [{"path": "a.py", "severity": "major"}]},
                {"pass": True, "scores": {"a": 8},
                 "findings": [{"path": "a.py", "header": "h",
                               "detail": "d"}]},
        ):
            result = review_findings.validate_review(body)
            self.assertFalse(result["ok"], body)
            self.assertTrue(result["errors"], body)

    def test_schema_accepts_aliases_and_reports_truncation(self):
        findings = [{"path": "a.py", "line_start": 1, "severity": "high",
                     "header": "h%d" % i, "detail": "d%d" % i}
                    for i in range(review_findings.MAX_FINDINGS + 2)]
        result = review_findings.validate_review(
            {"pass": True, "scores": {"a": 8}, "findings": findings})
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["findings_truncated"])
        self.assertEqual(result["findings"][0]["severity"], "major")

    def test_location_validation_keeps_findings_but_marks_evidence(self):
        findings = [
            _finding(),
            _finding(line_start=99, line_end=99),
            _finding(path="other.py", line_start=1),
            _finding(line_start=None, line_end=None),
            _finding(path="new.py", line_start=2, line_end=2),
        ]
        review_findings.validate_locations(
            findings, DIFF, changed_paths=["src/app.py", "new.py"])
        self.assertEqual([item["location_valid"] for item in findings],
                         [True, False, False, False, True])
        for item in findings:
            if not item["location_valid"]:
                self.assertTrue(item["location_reason"])

    def test_blocking_requires_unrejected_major_or_blocker(self):
        blocked = _finding()
        rejected = _finding(header="被驳回", verification="rejected")
        minor = _finding(severity="minor", header="小问题")
        self.assertEqual(review_findings.blocking_findings(
            [blocked, rejected, minor]), [blocked])


class VerdictTests(unittest.TestCase):
    def _merged(self, **overrides):
        body = {"ok": True, "pass": True, "findings": [], "scores": {},
                "errors": [], "findings_truncated": False}
        body.update(overrides)
        return body

    def test_model_pass_cannot_override_blocker(self):
        result = review_findings.finalize_review(
            self._merged(findings=[_finding()]), coverage_complete=True)
        self.assertFalse(result["pass"])
        self.assertEqual(result["blocking_count"], 1)
        self.assertTrue(result["review_degraded"])
        self.assertTrue(any("pass" in reason and "blocker" in reason
                            for reason in result["review_degradation_reasons"]))

    def test_rejected_blocker_does_not_block(self):
        result = review_findings.finalize_review(
            self._merged(findings=[_finding(verification="rejected")]),
            coverage_complete=True)
        self.assertTrue(result["pass"])
        self.assertEqual(result["review_status"], "ok")

    def test_incomplete_coverage_blocks_pass_and_is_visible(self):
        result = review_findings.finalize_review(
            self._merged(), coverage_complete=False,
            coverage_reasons=["diff 被截断", "文件内容被截断：a.py"])
        self.assertFalse(result["pass"])
        self.assertEqual(result["review_status"], "partial")
        self.assertIn("评审覆盖不完整", result["review_degradation_reasons"])

    def test_schema_failure_and_failed_verifier_fail_closed(self):
        result = review_findings.finalize_review(
            self._merged(**{"ok": False, "pass": False}),
            coverage_complete=True)
        self.assertFalse(result["pass"])
        self.assertEqual(result["review_status"], "degraded")
        result = review_findings.finalize_review(
            self._merged(findings=[_finding()]), coverage_complete=True,
            verifier={"status": "failed"})
        self.assertFalse(result["pass"])
        self.assertTrue(any("复核" in reason
                            for reason in result["review_degradation_reasons"]))


class ChunkTests(unittest.TestCase):
    @staticmethod
    def _section(path, lines=180):
        body = "\n".join("+value_%d = %s" % (i, "x" * 24)
                         for i in range(lines))
        return ("diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n"
                "@@ -1,0 +1,%d @@\n%s\n" % (path, path, path, path,
                                             lines, body))

    def test_pack_chunks_omits_beyond_cap_and_merge_is_conservative(self):
        diff = "".join(self._section(path) for path in ("a.py", "b.py", "c.py"))
        chunks, omitted = review_findings.pack_chunks(
            diff, budget=4000, max_chunks=2)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(omitted, ["c.py"])
        merged = review_findings.merge_reviews([
            {"ok": True, "pass": True, "scores": {"安全": 8},
             "findings": [_finding(header="同一条")]},
            {"ok": True, "pass": False, "scores": {"安全": 6, "正确性": 7},
             "findings": [_finding(header="同一条", severity="blocker"),
                          _finding(header="另一条", path="new.py")]},
        ])
        self.assertFalse(merged["pass"])
        self.assertEqual(merged["scores"], {"安全": 6, "正确性": 7})
        self.assertEqual(len(merged["findings"]), 2)
        self.assertEqual(merged["findings"][0]["severity"], "blocker")

    def test_render_previous_findings_only_shows_open_rows(self):
        text = review_findings.render_previous_findings([
            {"finding_id": "aaa", "path": "a.py", "line_start": 3,
             "severity": "major", "header": "未修", "detail": "还在",
             "status": "ACTIVE"},
            {"finding_id": "bbb", "path": "b.py", "line_start": 4,
             "severity": "minor", "header": "已修", "detail": "没了",
             "status": "RESOLVED"},
        ])
        self.assertIn("aaa", text)
        self.assertIn("a.py:3", text)
        self.assertNotIn("bbb", text)
        self.assertIn("系统才会把它们标为已解决", text)


class VerifierTests(unittest.TestCase):
    def test_parse_reject_without_reason_becomes_uncertain(self):
        finding = _finding()
        parsed = review_findings.parse_verification(
            {"verdicts": [{"finding_id": finding["finding_id"],
                           "verdict": "reject", "reason": ""}]}, [finding])
        self.assertTrue(parsed["ok"])
        review_findings.apply_verification([finding], parsed)
        self.assertEqual(finding["verification"], "uncertain")

    def test_unknown_id_falls_back_to_position(self):
        finding = _finding()
        parsed = review_findings.parse_verification(
            {"verdicts": [{"finding_id": "missing", "verdict": "accept",
                           "reason": "核对通过"}]}, [finding])
        self.assertTrue(parsed["ok"], parsed["errors"])
        self.assertEqual(parsed["verdicts"][finding["finding_id"]]["verdict"],
                         "accept")

    def test_pipeline_verifier_rejects_and_fails_closed(self):
        finding = _finding()
        task = {"id": "t-verify", "goal": "核对问题"}
        impl = {"id": "impl", "mode": "real"}
        reviewer = {"id": "reviewer", "mode": "real"}
        with mock.patch.object(pipeline.modelhub, "bind_agent",
                               side_effect=lambda agent, *_a, **_k: agent), \
             mock.patch.object(pipeline, "_pick_review_verifier",
                               return_value=({"id": "third", "mode": "real"}, "跨族")):
            text = _review_text(verdicts=[{
                "finding_id": finding["finding_id"], "verdict": "reject",
                "reason": "diff 中已加校验"}])
            with mock.patch.object(pipeline, "_run_step",
                                   return_value={"ok": True, "text": text}):
                result, meta = pipeline._verify_review_findings(
                    "run", task, ".", reviewer, impl, [], None, "default",
                    [finding], DIFF, None)
            self.assertEqual(result[0]["verification"], "rejected")
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(meta["counts"]["reject"], 1)

            with mock.patch.object(pipeline, "_run_step",
                                   return_value={"ok": False, "error": "quota"}):
                result, meta = pipeline._verify_review_findings(
                    "run", task, ".", reviewer, impl, [], None, "default",
                    [finding], DIFF, None)
            self.assertEqual(result[0]["verification"], "unverified")
            self.assertEqual(meta["status"], "failed")
            final = review_findings.finalize_review(
                {"ok": True, "pass": True, "findings": result},
                coverage_complete=True, verifier=meta)
            self.assertFalse(final["pass"])

    def test_verifier_must_be_a_third_real_agent(self):
        impl = {"id": "impl", "mode": "real"}
        reviewer = {"id": "reviewer", "mode": "real"}
        agents = [impl, reviewer]
        with mock.patch.object(pipeline.router, "pick_reviewer",
                               return_value=(impl, "self")):
            self.assertIsNone(pipeline._pick_review_verifier(
                agents, impl, reviewer, None, "default")[0])
        with mock.patch.object(pipeline.router, "pick_reviewer",
                               return_value=({"id": "third", "mode": "real"}, "iso")):
            self.assertEqual(pipeline._pick_review_verifier(
                agents, impl, reviewer, None, "default")[0]["id"], "third")
        self.assertIsNone(pipeline._pick_review_verifier(
            agents, impl, {"id": "reviewer", "mode": "mock"},
            None, "default")[0])


class StorageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous_db = review_storage._DB_PATH
        self.addCleanup(self._restore_db)
        review_storage.init(self.tmp.name)

    def _restore_db(self):
        review_storage._DB_PATH = self.previous_db

    def _status(self, finding_id):
        rows = review_storage.list_findings("task-1", limit=100)
        return next(row["status"] for row in rows
                    if row["finding_id"] == finding_id)

    def test_active_resolved_reopened_dismissed(self):
        finding = _finding()
        state = review_storage.sync_findings("task-1", "run-1", [finding])
        self.assertEqual(state["counts"]["new"], 1)
        self.assertEqual(finding["status"], "ACTIVE")
        self.assertEqual(self._status(finding["finding_id"]), "ACTIVE")

        state = review_storage.sync_findings(
            "task-1", "run-2", [], allow_resolution=True,
            coverage_complete=True, reviewed_files=["src/app.py"])
        self.assertEqual(state["counts"]["resolved"], 1)
        self.assertEqual(self._status(finding["finding_id"]), "RESOLVED")

        again = _finding()
        state = review_storage.sync_findings("task-1", "run-3", [again])
        self.assertEqual(state["counts"]["reopened"], 1)
        self.assertEqual(again["status"], "REOPENED")
        self.assertEqual(self._status(again["finding_id"]), "REOPENED")

        rejected = _finding(header="误报", path="new.py",
                            verification="rejected")
        state = review_storage.sync_findings("task-1", "run-4", [rejected])
        self.assertEqual(state["counts"]["dismissed"], 1)
        self.assertEqual(self._status(rejected["finding_id"]), "DISMISSED")
        self.assertEqual(review_storage.sync_findings(
            "task-1", "run-5", [], allow_resolution=True,
            coverage_complete=True, reviewed_files=["new.py"])["counts"]["resolved"], 0)

    def test_resolution_requires_reviewed_file_and_complete_coverage(self):
        finding = _finding(path="src/app.py")
        review_storage.sync_findings("task-1", "run-1", [finding])
        state = review_storage.sync_findings(
            "task-1", "run-2", [], allow_resolution=True,
            coverage_complete=True, reviewed_files=["other.py"])
        self.assertEqual(state["counts"]["resolved"], 0)
        state = review_storage.sync_findings(
            "task-1", "run-3", [], allow_resolution=True,
            coverage_complete=False, reviewed_files=["src/app.py"])
        self.assertEqual(state["counts"]["resolved"], 0)
        state = review_storage.sync_findings(
            "task-1", "run-4", [], allow_resolution=False,
            coverage_complete=True, reviewed_files=["src/app.py"])
        self.assertEqual(state["counts"]["resolved"], 0)
        self.assertEqual(self._status(finding["finding_id"]), "ACTIVE")

    def test_review_record_persists_findings_and_coverage(self):
        review_storage.record_review({
            "id": "rec-1", "task_id": "task-1", "author_cli": "codex",
            "reviewer_cli": "claude", "scores": {"安全": 8},
            "findings": [_finding()],
            "coverage": _coverage(checked=True, chunks=1, failed_chunks=0,
                                  chunk_omitted_files=[]),
            "review_status": "degraded", "passed": False,
        })
        record = review_storage.list_reviews("task-1")[0]
        self.assertEqual(record["findings"][0]["header"], "未校验输入")
        self.assertTrue(record["coverage"]["complete"])
        self.assertTrue(record["coverage"]["checked"])
        self.assertEqual(record["coverage"]["chunks"], 1)
        self.assertEqual(record["review_status"], "degraded")

    def test_delete_task_reviews_clears_findings(self):
        finding = _finding()
        review_storage.sync_findings("task-1", "run-1", [finding])
        review_storage.delete_task_reviews("task-1")
        self.assertEqual(review_storage.list_findings("task-1"), [])

    def test_legacy_database_is_migrated(self):
        path = Path(self.tmp.name) / "legacy.sqlite3"
        conn = sqlite3.connect(str(path))
        conn.execute("""CREATE TABLE review_records (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT,
            author_cli TEXT NOT NULL, author_model_family TEXT,
            reviewer_cli TEXT NOT NULL, reviewer_model_family TEXT,
            scores_json TEXT NOT NULL, line_comments_json TEXT NOT NULL,
            passed INTEGER NOT NULL, created_at TEXT NOT NULL,
            duration_ms INTEGER, schema_version INTEGER NOT NULL DEFAULT 1)""")
        conn.execute("""INSERT INTO review_records VALUES
            ('old', 'task-1', 'run-0', 'codex', '', 'claude', '',
             '{"安全":7}', '[]', 1, '2026-10-01T00:00:00Z', NULL, 1)""")
        conn.commit()
        conn.close()
        review_storage._DB_PATH = path
        rows = review_storage.list_reviews("task-1")
        self.assertEqual(rows[0]["findings"], [])
        self.assertEqual(rows[0]["review_status"], "ok")
        self.assertEqual(rows[0]["scores"], {"安全": 7.0})


class CollectChangesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        })
        self._git("init", "-q")
        self._write("a.py", "print(1)\n")
        self._git("add", "a.py")
        self._git("commit", "-qm", "base")

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.repo),
                              env=self.env, check=True,
                              capture_output=True, text=True)

    def _write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def test_ignored_noise_and_clean_complete(self):
        self._write("a.py", "print(1)\nprint(2)\n")
        self._write("package-lock.json", "{}\n")
        changes = gitmod.collect_changes(
            str(self.repo), max_diff=200000, ignore_review_noise=True)
        coverage = changes["coverage"]
        self.assertTrue(coverage["repo"])
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["ignored_files"], ["package-lock.json"])
        self.assertEqual(coverage["omitted_files"], [])
        self.assertIn("print(2)", changes["diff"])
        self.assertNotIn("package-lock", changes["diff"])

    def test_truncation_and_omitted_untracked_are_reported(self):
        self._write("a.py", "print(1)\n" + "x = 'y'\n" * 300)
        self._write("new.py", "z = 1\n" * 300)
        changes = gitmod.collect_changes(
            str(self.repo), max_diff=1000, ignore_review_noise=True)
        coverage = changes["coverage"]
        self.assertTrue(coverage["truncated"])
        self.assertFalse(coverage["complete"])
        self.assertIn("a.py", coverage["partial_files"])
        self.assertIn("new.py", coverage["omitted_files"])
        self.assertIn("已截断", changes["diff"])

    def test_non_git_workspace_returns_unchecked_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            changes = gitmod.collect_changes(tmp)
        self.assertFalse(changes["coverage"]["repo"])
        self.assertFalse(changes["coverage"]["complete"])
        self.assertEqual(changes["diff"], "")


class ReviewPipelineWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous_db = review_storage._DB_PATH
        self.addCleanup(self._restore_db)
        review_storage.init(self.tmp.name)
        self.task = {"id": "task-wire", "title": "wire", "goal": "wire test",
                     "verify_command": "true"}
        self.reviewer = {"id": "reviewer", "mode": "real"}
        self.impl = {"id": "impl", "mode": "real"}

    def _restore_db(self):
        review_storage._DB_PATH = self.previous_db

    def _run(self, text, calls=None):
        def step(_run_id, role, _agent, prompt, *_args, **_kwargs):
            if calls is not None:
                calls.append((role, prompt))
            return {"ok": True, "text": text}

        with mock.patch.object(gitmod, "collect_changes",
                               return_value=_changes()), \
             mock.patch.object(gitmod, "head_sha", return_value="abc123"), \
             mock.patch.object(pipeline, "_run_step", side_effect=step):
            return pipeline._run_review(
                "run-1", self.task, str(self.tmp.name), self.reviewer, None,
                agents=[], impl=self.impl)

    def test_blocker_forces_fail_then_complete_round_resolves_it(self):
        finding = _finding()
        result = self._run(_review_text(findings=[{
            "path": finding["path"], "line_start": 2,
            "severity": "blocker", "header": finding["header"],
            "detail": finding["detail"], "evidence": "value = load()"}]))
        self.assertFalse(result["pass"])
        self.assertEqual(result["blocking_count"], 1)
        self.assertEqual(result["findings"][0]["location_valid"], True)
        self.assertEqual(result["finding_state"]["counts"]["new"], 1)
        self.assertEqual(result["coverage"]["complete"], True)
        self.assertEqual(result["review_status"], "degraded")

        calls = []
        result = self._run(_review_text(), calls)
        self.assertTrue(result["pass"], result["review_degradation_reasons"])
        self.assertEqual(result["review_status"], "ok")
        self.assertEqual(result["finding_state"]["counts"]["resolved"], 1)
        self.assertIn("上轮未解决 findings", calls[0][1])
        self.assertIn(finding["header"][:4], calls[0][1])
        self.assertEqual(review_storage.list_findings("task-wire")[0]["status"],
                         "RESOLVED")

    def test_unseen_previous_findings_cannot_be_resolved(self):
        finding = _finding()
        review_storage.sync_findings("task-wire", "run-0", [finding])
        rows = [{"finding_id": "extra-%d" % i, "path": "src/app.py",
                 "line_start": 2, "severity": "major",
                 "header": "旧问题 %d" % i, "detail": "还在",
                 "status": "ACTIVE"}
                for i in range(review_findings.MAX_PROMPT_FINDINGS)]
        rows.append({"finding_id": finding["finding_id"],
                     "path": finding["path"], "line_start": 2,
                     "severity": finding["severity"],
                     "header": finding["header"], "detail": finding["detail"],
                     "status": "ACTIVE"})
        with mock.patch.object(pipeline, "_load_previous_findings",
                               return_value=rows):
            result = self._run(_review_text())
        self.assertTrue(result["pass"])
        self.assertEqual(result["finding_state"]["counts"]["resolved"], 0)
        self.assertEqual(review_storage.list_findings("task-wire")[0]["status"],
                         "ACTIVE")

    def test_prose_only_review_never_passes(self):
        result = self._run("正确性：8 分，可维护性：8 分，安全：8 分。")
        self.assertFalse(result["pass"])
        self.assertFalse(result["reviewer_error"])
        self.assertEqual(result["review_status"], "degraded")
        self.assertEqual(result["scores"]["正确性"], 8.0)

    def test_partial_coverage_and_non_git_policy(self):
        partial = _coverage(complete=False, truncated=True,
                            partial_files=["a.py"], omitted_files=["new.py"])
        with mock.patch.object(gitmod, "collect_changes",
                               return_value=_changes(partial)), \
             mock.patch.object(gitmod, "head_sha", return_value="abc"), \
             mock.patch.object(pipeline, "_run_step",
                               return_value={"ok": True, "text": _review_text()}):
            result = pipeline._run_review(
                "run-1", self.task, str(self.tmp.name), self.reviewer, None,
                agents=[], impl=self.impl)
        self.assertFalse(result["pass"])
        self.assertEqual(result["review_status"], "partial")
        self.assertTrue(any("截断" in reason
                            for reason in result["review_degradation_reasons"]))

        unchecked = _coverage(repo=False, complete=False, changed_files=0,
                              reviewed_files=[])
        with mock.patch.object(gitmod, "collect_changes",
                               return_value=_changes(unchecked, diff="")), \
             mock.patch.object(gitmod, "head_sha", return_value=""), \
             mock.patch.object(pipeline, "_run_step",
                               return_value={"ok": True, "text": _review_text()}):
            result = pipeline._run_review(
                "run-2", self.task, str(self.tmp.name), self.reviewer, None,
                agents=[], impl=self.impl)
        self.assertTrue(result["pass"])
        self.assertEqual(result["review_status"], "degraded")
        self.assertFalse(result["coverage"]["checked"])


class PromptTests(unittest.TestCase):
    def test_prompt_contains_coverage_previous_and_fenced_diff(self):
        task = {"goal": "目标", "verify_command": "pytest"}
        dimensions = [{"name": "正确性", "weight": 1.0}]
        prompt = pipeline._review_prompt(
            task, {"diff": DIFF, "paths": ["src/app.py"]},
            _coverage(complete=False, truncated=True, partial_files=["a.py"]),
            "## 上轮未解决 findings\n- [x] a.py:2 未修",
            dimensions, index=1, total=2, omitted=["z.py"], failed_chunks=1)
        self.assertNotIn("__DIFF__", prompt)
        self.assertNotIn("__COVERAGE__", prompt)
        self.assertNotIn("__PREVIOUS__", prompt)
        self.assertIn("[外部资料", prompt)
        self.assertIn("覆盖状态：不完整", prompt)
        self.assertIn("不得给出无保留的 pass", prompt)
        self.assertIn("「上轮 findings」", prompt)
        self.assertIn("上轮未解决 findings", prompt)
        self.assertIn("分块评审 1/2", prompt)
        self.assertIn("1 个评审分块失败", prompt)

    def test_coverage_note_complete_is_explicit(self):
        note = pipeline._review_coverage_note(_coverage())
        self.assertIn("覆盖状态：完整", note)


if __name__ == "__main__":
    unittest.main()
