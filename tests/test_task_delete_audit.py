# -*- coding: utf-8 -*-
"""删除任务必须落审计台账（2026-09-28 连载根任务被删、事后无法回答
「谁删的、何时删的」案）。台账在 data/audit/task_delete-YYYYMM.jsonl，
一行一对象、正序追加。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from base import BaseTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


class TestTaskDeleteAudit(BaseTest):
    def _audit_lines(self):
        audit_dir = self.data_dir / "audit"
        files = sorted(audit_dir.glob("task_delete-*.jsonl")) if audit_dir.is_dir() else []
        lines = []
        for f in files:
            lines.extend(json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
        return lines

    def test_delete_writes_audit_record(self):
        from app.core import store

        task = store.create_task({"type": "doc", "title": "审计样例", "goal": "写个开篇"})
        tid = task["id"]
        ok, err = store.delete_task(tid, actor="10.0.0.9")
        self.assertTrue(ok, err)

        lines = self._audit_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["event"], "task_delete")
        self.assertEqual(rec["task_id"], tid)
        self.assertEqual(rec["title"], "审计样例")
        self.assertEqual(rec["actor"], "10.0.0.9")
        self.assertIsInstance(rec["run_ids"], list)
        # 台账是给人查案的：时间戳必须像话
        self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_failed_delete_writes_nothing(self):
        from app.core import store

        ok, _ = store.delete_task("t-20990101-000000-0000")
        self.assertFalse(ok)
        self.assertEqual(self._audit_lines(), [])

    def test_local_delete_without_actor_marks_local(self):
        from app.core import store

        task = store.create_task({"type": "doc", "title": "本地删除", "goal": "x"})
        ok, err = store.delete_task(task["id"])
        self.assertTrue(ok, err)
        rec = self._audit_lines()[0]
        self.assertEqual(rec["actor"], "local")

    def test_serial_chain_parent_refuses_delete(self):
        """连载链前序任务被后续任务依赖时拒绝物理删除——只许归档。
        （2026-09-28 连载根任务被程序化批量删除案：链的地基不许抽。）"""
        from app.core import store

        parent = store.create_task({"type": "serial_novel", "title": "连载·上", "goal": "x",
                                    "serial": {"chapters": 8, "start_chapter": 1}})
        child = store.create_task({"type": "serial_novel", "title": "连载·续", "goal": "x",
                                   "serial": {"chapters": 12, "start_chapter": 9,
                                              "continues": parent["id"]}})
        ok, err = store.delete_task(parent["id"])
        self.assertFalse(ok)
        self.assertIn("归档", err)
        self.assertIsNotNone(store.get_task(parent["id"]), "被依赖的前序任务必须还在")
        # 链尾（无后续依赖）照常可删
        ok, err = store.delete_task(child["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_task(child["id"]))
