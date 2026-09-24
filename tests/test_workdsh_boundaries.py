# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from unittest import mock

from base import BaseTest


class TestWorkdshBoundaries(BaseTest):
    def test_run_captures_sanitized_execution_snapshot(self):
        from app.core import modelhub, store

        modelhub._save({
            "providers": [{
                "id": "p1", "name": "Gateway", "protocol": "openai",
                "base_url": "https://example.test/v1", "api_key": "secret-value",
                "keys": [{"id": "k1", "key": "secret-value", "enabled": True}],
            }],
            "bindings": {"codex-cli": {"chain": [
                {"provider_id": "p1", "model": "model-a"},
            ]}},
        })
        task = store.create_task({"type": "code", "goal": "snapshot", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])

        snapshot = run["execution_snapshot"]
        self.assertEqual(snapshot["flow_revision"], task["flow_snapshot"]["digest"])
        self.assertRegex(snapshot["flow_revision_id"], r"^flowrev-")
        self.assertEqual(len(snapshot["flow_content_sha256"]), 64)
        self.assertEqual(snapshot["agent_bindings"]["codex-cli"]["chain"][0]["key_ids"], ["k1"])
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("secret-value", encoded)
        self.assertIn("connection_id", snapshot["agent_bindings"]["codex-cli"]["chain"][0])

    def test_direct_snapshot_keeps_connection_identity_without_secret(self):
        from app.core import modelhub, store

        modelhub._save({
            "providers": [{
                "id": "direct-p", "protocol": "anthropic",
                "base_url": "https://gateway.example.test/v1",
                "keys": [{"id": "acct-7", "key": "direct-secret", "enabled": True}],
            }],
            "bindings": {},
        })
        task = store.create_task({"type": "direct", "goal": "direct snapshot",
                                  "workdir": str(self.workdir),
                                  "direct_provider_id": "direct-p",
                                  "direct_model": "claude-test"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        direct = run["execution_snapshot"]["direct"]
        self.assertEqual(direct["connection_id"], "direct-p")
        self.assertEqual(direct["credential_id"], "acct-7")
        self.assertEqual(direct["protocol"], "anthropic")
        encoded = json.dumps(run["execution_snapshot"], ensure_ascii=False)
        self.assertNotIn("direct-secret", encoded)
        redacted = store._execution_snapshot(
            dict(task, verify_command="pytest --token super-secret"))["verify_command"]
        self.assertNotIn("super-secret", redacted)
        self.assertIn("<redacted>", redacted)

    def test_knowledge_and_lessons_expose_revision_digest(self):
        from app.core import knowledge, skills

        lesson = skills.upsert_lesson("code", "稳定回归", "先跑测试")
        entry = knowledge.upsert_entry("code", "稳定回归知识", "使用确定性验证", status="approved")
        self.assertRegex(lesson["revision_id"], r"^skrev-")
        self.assertEqual(len(lesson["content_sha256"]), 64)
        self.assertRegex(entry["revision_id"], r"^kbrev-")
        self.assertEqual(len(entry["content_sha256"]), 64)

        updated = skills.upsert_lesson("code", "稳定回归", "先跑全量测试")
        self.assertNotEqual(updated["revision_id"], lesson["revision_id"])
        self.assertTrue(updated["revisions"])

    def test_attachment_asset_ref_is_content_addressed(self):
        from app.core import assets, attachments

        path = self.workdir / "_attachments"
        path.mkdir()
        fp = path / "note.txt"
        fp.write_text("hello", encoding="utf-8")
        refs = attachments.asset_refs(str(self.workdir), [{"path": "_attachments/note.txt", "name": "note.txt"}])
        self.assertEqual(len(refs), 1)
        self.assertRegex(refs[0]["asset_id"], r"^asset-")
        self.assertEqual(refs[0]["content_sha256"],
                         "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        self.assertEqual(refs[0]["revision_id"], "assetrev-2cf24dba5fb0a30e")
        saved = assets.register(str(self.workdir), "_attachments/note.txt",
                                owner_type="task", owner_id="t1")
        self.assertEqual(saved["asset_id"], refs[0]["asset_id"])
        self.assertEqual(assets.get(saved["asset_id"], saved["revision_id"])["size"], 5)

    def test_operation_ledger_marks_unknown_and_recovers_stale_pending(self):
        from app.core import operations

        op_id = operations.begin("publish:demo", {"task_id": "t1", "title": "A"})
        self.assertEqual(operations.get(op_id)["status"], "pending")
        operations.mark_unknown(op_id, "远端响应超时")
        self.assertEqual(operations.get(op_id)["status"], "unknown")

        stale = operations.begin("zentao:bug:1:resolve", {"bug_id": "1"})
        data = operations._read_all()
        for row in data:
            if row.get("operation_id") == stale:
                row["submitted_at"] = time.time() - operations.PENDING_TTL_SECONDS - 1
        operations._write_all(data)
        self.assertEqual(operations.recover_pending(), 1)
        self.assertEqual(operations.get(stale)["status"], "unknown")

    def test_terminal_run_contains_separate_acceptance_cases(self):
        from app.core import store

        task = store.create_task({"type": "code", "goal": "acceptance", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="done",
                         verdict={"verify_pass": True, "review_pass": True})
        saved = store.get_run(run["id"])
        self.assertEqual(saved["acceptance"]["version"], 1)
        cases = {item["case"]: item for item in saved["acceptance"]["cases"]}
        self.assertEqual(cases["correctness"]["status"], "passed")
        self.assertEqual(cases["boundary"]["status"], "not_evaluated")
        self.assertEqual(cases["performance"]["status"], "not_evaluated")

    def test_upload_chapter_resolves_book_without_closure_scope_error(self):
        """发章在闭包内补 book_id 时，前置解析仍使用原始书籍引用。"""
        from app.core import store
        from app.core.publish import ledger, manager

        task = store.create_task({"type": "code", "goal": "publish", "workdir": str(self.workdir)})
        ledger.save_book(task["id"], "fanqie", {"title": "测试书", "book_id": ""})
        chapter = self.workdir / "第1章.md"
        chapter.write_text("# 第1章\n" + ("正文" * 80), encoding="utf-8")

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self._target = target

            def start(self):
                self._target()

        page = mock.Mock()
        seen_books = []

        def resolve_book_id(_plat, _page, book):
            seen_books.append(dict(book))
            return "book-42"

        with mock.patch.object(manager, "_login_guard", return_value=(True, "")), \
                mock.patch.object(manager, "_open_page", return_value=(object(), page)), \
                mock.patch.object(manager, "_resolve_book_id", side_effect=resolve_book_id), \
                mock.patch.object(manager, "load_flow", return_value=[]), \
                mock.patch.object(manager.flow, "run_flow", return_value=0), \
                mock.patch.object(manager.threading, "Thread", ImmediateThread):
            ok, err = manager.upload_chapter_async(task["id"], "fanqie", str(chapter))

        self.assertTrue(ok, err)
        self.assertEqual(err, "")
        self.assertEqual(len(seen_books), 1)
        self.assertEqual({key: seen_books[0][key] for key in ("book_id", "title", "url")},
                         {"book_id": "", "title": "测试书", "url": ""})
        self.assertEqual(ledger.book_for(task["id"], "fanqie")["book_id"], "book-42")

    def test_acceptance_needs_evidence_for_every_dimension(self):
        from app.core.acceptance import evaluate_run

        result = evaluate_run({"status": "done", "tokens": 0, "cost_usd": 0.0,
                               "verdict": None}, {"engine": "direct"})
        cases = {item["case"]: item for item in result["cases"]}
        self.assertEqual(cases["correctness"]["status"], "not_evaluated")
        self.assertEqual(cases["performance"]["status"], "not_evaluated")
        self.assertEqual(cases["exception"]["status"], "not_evaluated")
        self.assertFalse(result["passed"])

        stale = evaluate_run({"status": "failed", "verdict": {"verify_pass": True}}, {})
        self.assertEqual(stale["cases"][0]["status"], "unknown")

    def test_operation_terminal_state_is_monotonic_and_reconcilable(self):
        from app.core import operations

        confirmed = operations.begin("publish:confirmed", {"id": "1"})
        self.assertTrue(operations.confirm(confirmed, remote_receipt="remote-1"))
        self.assertFalse(operations.finish_exception(confirmed, RuntimeError("late callback")))
        saved = operations.get(confirmed)
        self.assertEqual(saved["status"], "confirmed")
        self.assertEqual(saved["remote_receipt"], "remote-1")

        unknown = operations.begin("publish:unknown", {"id": "2"})
        self.assertTrue(operations.mark_unknown(unknown, "网络超时"))
        self.assertFalse(operations.confirm(unknown, remote_receipt="guess"))
        self.assertTrue(operations.reconcile(unknown, "confirmed", remote_receipt="remote-2"))
        self.assertEqual(operations.get(unknown)["remote_receipt"], "remote-2")

    def test_same_content_asset_keeps_canonical_source_metadata(self):
        from app.core import assets

        first = self.workdir / "first.txt"
        second = self.workdir / "nested" / "second.txt"
        second.parent.mkdir()
        first.write_text("same", encoding="utf-8")
        second.write_text("same", encoding="utf-8")
        one = assets.register(str(self.workdir), "first.txt", owner_type="task", owner_id="1")
        two = assets.register(str(self.workdir), "nested/second.txt", owner_type="task", owner_id="2")
        self.assertEqual(one["asset_id"], two["asset_id"])
        self.assertEqual(assets.get(one["asset_id"])["path"], "first.txt")
