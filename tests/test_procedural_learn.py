# -*- coding: utf-8 -*-
"""程序性记忆（mengram 借鉴·从做中学）单测：LEARN_PROMPT 提炼「做法」+
一次通过高分运行也走学习链（此前只从问题学）。

跑法：python -m unittest discover -s tests -p "test_procedural_learn.py" -v
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


class ProceduralLearnTests(BaseTest):
    def _mk_pass_run(self):
        """真实（非 mock）步骤 + pass verdict 的一次通过 run。"""
        from app.core import store
        task = store.create_task({"type": "code", "title": "高分通过", "goal": "g",
                                  "workdir": str(self.workdir), "mode": "auto"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        step, _ = store.add_step(run["id"], "implement", "codex-cli", "Codex")
        store.finish_step(run["id"], step["n"], "done", summary="ok")
        store.update_run(run["id"], status="done",
                         verdict={"type": "code", "pass": True,
                                  "scores": {"quality": 9.1}})
        return task, run

    def test_prompt_asks_for_procedures(self):
        """LEARN_PROMPT 必须要求提炼有效做法（「做法：」约定+高分优先）。"""
        from app.core import skills
        self.assertIn("已验证有效的做法", skills.LEARN_PROMPT)
        self.assertIn("做法：", skills.LEARN_PROMPT)
        self.assertIn("一次通过的高分运行优先提炼", skills.LEARN_PROMPT)

    def test_pass_run_learns_procedure(self):
        """一次通过的运行也进学习链：编排者返回「做法：」条目被 upsert 入库。"""
        import sys
        sys.path.insert(0, "app")
        from app.core import skills, modelhub, runner
        task, run = self._mk_pass_run()
        reply = json.dumps({"lessons": [
            {"title": "做法：先列评分点再写", "category": "流程规范",
             "content": "起草前先把评审维度抄成检查单，逐条写完自查"}]},
            ensure_ascii=False)
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p", "name": "P"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value={"ok": True, "text":
                                             "```json\n%s\n```" % reply}), \
             mock.patch.object(runner, "extract_json",
                               return_value=json.loads(reply)):
            n = skills.learn_from_run(run["id"])
        self.assertGreaterEqual(n, 1)
        hit = next((l for l in skills.list_lessons("code")
                    if l["title"].startswith("做法：")), None)
        self.assertIsNotNone(hit, "「做法：」教训应已入库")
        self.assertIn("检查单", hit["content"])

    def test_upsert_dedup_keeps_procedure_idempotent(self):
        """同一次学习的「做法」重复执行不产生重复条目（upsert 幂等）。"""
        from app.core import skills
        a = skills.upsert_lesson("code", "做法：先列评分点再写", "内容A")
        b = skills.upsert_lesson("code", "做法：先列评分点再写", "内容B")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["content"], "内容B")


if __name__ == "__main__":
    import unittest
    unittest.main()
