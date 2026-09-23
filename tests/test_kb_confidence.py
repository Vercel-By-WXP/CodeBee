# -*- coding: utf-8 -*-
"""知识库 confidence 可信度分级（引用核验借鉴）单测。

跑法：python -m unittest discover -s tests -p "test_kb_confidence.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ConfidenceTests(BaseTest):
    def _kb(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        return knowledge

    def test_upsert_stores_confidence(self):
        kb = self._kb()
        e = kb.upsert_entry("code", "有据事实", "内容" * 3,
                            status="approved", confidence="high")
        self.assertEqual(e["confidence"], "high")
        # 缺省 = medium
        e2 = kb.upsert_entry("code", "无据事实", "内容" * 3, status="approved")
        self.assertEqual(e2["confidence"], "medium")

    def test_merge_upgrades_confidence(self):
        """近似/同题合并时 high 覆盖 medium（有据版本吸收无据版本）。"""
        kb = self._kb()
        kb.upsert_entry("code", "接口基线", "旧版" * 3, status="approved")
        e = kb.upsert_entry("code", "接口基线数据", "新版" * 3,
                            status="approved", confidence="high")
        self.assertEqual(e["confidence"], "high")

    def test_learn_drops_low(self):
        """learn_from_run：confidence=low 的 fact 直接丢弃，其余落库。"""
        import sys
        import json
        from unittest import mock
        sys.path.insert(0, "app")
        from app.core import knowledge, modelhub, runner, store
        knowledge._FILE = self.data_dir / "knowledge.json"
        task = store.create_task({"type": "research", "title": "t", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "t", task_id=task["id"])
        step, _ = store.add_step(run["id"], "draft", "x-cli", "X")
        store.finish_step(run["id"], step["n"], "done", summary="ok")
        store.update_run(run["id"], status="done")
        # 产出材料（learn 只挑 done run）
        (self.workdir / "report.md").write_text("调研结论若干" * 8, encoding="utf-8")
        reply = json.dumps({"entries": [
            {"kind": "fact", "title": "高置信事实", "body": "有来源 " * 5,
             "confidence": "high"},
            {"kind": "fact", "title": "低置信事实", "body": "不确定 " * 5,
             "confidence": "low"},
            {"kind": "fact", "title": "无标事实", "body": "默认 " * 5},
        ]}, ensure_ascii=False)
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value={"ok": True, "text":
                                             "```json\n%s\n```" % reply}), \
             mock.patch.object(runner, "extract_json",
                               return_value=json.loads(reply)):
            n = knowledge.learn_from_run(run["id"])
        self.assertGreaterEqual(n, 2)
        titles = [x["title"] for x in knowledge.list_entries("research")]
        self.assertIn("高置信事实", titles)
        self.assertIn("无标事实", titles)
        self.assertNotIn("低置信事实", titles)   # low 被丢
        by_t = {x["title"]: x for x in knowledge.list_entries("research")}
        self.assertEqual(by_t["高置信事实"]["confidence"], "high")
        self.assertEqual(by_t["无标事实"]["confidence"], "medium")

    def test_prompt_asks_confidence(self):
        kb = self._kb()
        self.assertIn("confidence", kb.KNOWLEDGE_PROMPT)
        self.assertIn("low", kb.KNOWLEDGE_PROMPT)   # low 丢弃指引在场


if __name__ == "__main__":
    import unittest
    unittest.main()
