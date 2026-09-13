# -*- coding: utf-8 -*-
"""经验库（Skills）测试：内置包注入、教训沉淀去重、启停删除、运行后自动学习。

经验库让系统自己越跑越好：内置领域规范（七猫签约标准）+ 每次运行自动沉淀的教训，
下次同类任务自动注入提示词；全部自动，无需人工干预。
"""
from __future__ import annotations

import json

from base import BaseTest


class TestSkillPacks(BaseTest):
    def runTest(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"

        # 1) 内置包：七猫规范可用、按流程类型匹配
        packs = skills.list_packs()
        qimao = next(p for p in packs if p["id"] == "qimao-signing")
        self.assertTrue(qimao["enabled"])
        self.assertGreater(qimao["chars"], 2000)            # 正文真实存在
        self.assertIn("serial_novel", qimao["scopes"])
        text = skills.pack_text(next(p for p in skills.BUILTIN_PACKS if p["id"] == "qimao-signing"))
        for kw in ("黄金一章", "期待感", "主角主观能动性", "钩子"):
            self.assertIn(kw, text)

        # 2) 注入：连载任务带上经验包 + 命中计数
        block, used = skills.block_for({"type": "serial_novel"})
        self.assertIn("经验库", block)
        self.assertIn("七猫", block)
        self.assertIn("qimao-signing", used)
        # 无关类型不注入七猫包
        block2, used2 = skills.block_for({"type": "code"})
        self.assertNotIn("七猫", block2 or "")

        # 3) 停用后不再注入
        self.assertIsNone(skills.pack_op("qimao-signing", "disable"))
        block3, _ = skills.block_for({"type": "serial_novel"})
        self.assertNotIn("七猫", block3 or "")
        self.assertIsNone(skills.pack_op("qimao-signing", "enable"))
        self.assertIn("七猫", skills.block_for({"type": "serial_novel"})[0])
        # 未知包拒绝
        self.assertTrue(skills.pack_op("nope", "disable"))


class TestLessons(BaseTest):
    def runTest(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"

        # 1) 写入与去重：同 scope 同标题视为同一条（seen 累加）
        a = skills.upsert_lesson("serial_novel", "节奏拖沓", "每章结尾必须有钩子，断章要狠")
        b = skills.upsert_lesson("serial_novel", "节奏 拖沓！", "每章结尾必须有钩子；过渡章也要小冲突")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["seen"], 2)
        self.assertIn("过渡章", b["content"])                 # 内容以最新为准
        # 不同 scope 互不影响
        c = skills.upsert_lesson("doc", "节奏拖沓", "文档不需要节奏")
        self.assertNotEqual(a["id"], c["id"])
        self.assertEqual(len(skills.list_lessons("serial_novel")), 1)

        # 2) 注入到提示词并计数
        block, used = skills.block_for({"type": "serial_novel"})
        self.assertIn("节奏拖沓", block)
        self.assertIn(a["id"], used)
        self.assertEqual(skills.list_lessons("serial_novel")[0]["hits"], 1)
        # 高频排前
        skills.upsert_lesson("serial_novel", "视角漂移", "严禁上帝视角")
        got = skills.list_lessons("serial_novel")
        self.assertEqual(got[0]["title"], "节奏拖沓")         # hits=1 排在新条目(0)前

        # 3) 停用/删除
        self.assertIsNone(skills.lesson_op(c["id"], "disable"))
        self.assertFalse([x for x in skills.list_lessons("doc") if x["id"] == c["id"]][0]["enabled"])
        self.assertNotIn("文档不需要节奏", skills.block_for({"type": "doc"})[0] or "")
        self.assertIsNone(skills.lesson_op(c["id"], "enable"))
        self.assertIn("文档不需要节奏", skills.block_for({"type": "doc"})[0] or "")
        self.assertIsNone(skills.lesson_op(c["id"], "delete"))
        self.assertEqual(skills.list_lessons("doc"), [])
        self.assertTrue(skills.lesson_op("ghost", "delete"))


class TestLearnFromRun(BaseTest):
    """运行结束自动沉淀教训（无编排者时走确定性兜底）。"""

    def runTest(self):
        from app.core import pipeline, skills, store
        skills._FILE = self.data_dir / "skills.json"
        pipeline._agents = self.mock_agents
        task = store.create_task({"type": "serial_novel", "title": "学习测试", "mode": "auto",
                                  "goal": "短篇", "workdir": str(self.workdir),
                                  "serial": {"chapters": 2, "words_per_chapter": 600},
                                  "threshold": 9.9})   # 阈值极高 → 必然不达标，有可学的信号
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done")
        self.assertFalse(run["verdict"]["publishable"])

        n = skills.learn_from_run(run["id"], use_orchestrator=False)   # 确定性兜底路径
        self.assertGreater(n, 0)
        lessons = skills.list_lessons("serial_novel")
        self.assertTrue(lessons)
        self.assertTrue(any("维度" in x["title"] or "一致性" in x["title"] for x in lessons), lessons)

        # mock 运行不沉淀（无真实评审信号）
        from app.core import store as _st
        mock_run = _st.create_run("orchestration", "纯 mock", task_id=task["id"])
        _st.update_run(mock_run["id"], verdict={"publishable": False})
        step, _ = _st.add_step(mock_run["id"], "draft", "mock-a", "mock")
        _st.finish_step(mock_run["id"], step["n"], "done", summary="x")
        self.assertEqual(skills.learn_from_run(mock_run["id"], use_orchestrator=False), 0)

    def test_learn_uses_orchestrator_when_available(self):
        from app.core import modelhub, skills, store
        skills._FILE = self.data_dir / "skills.json"
        modelhub._FILE = self.data_dir / "models.json"
        task = store.create_task({"type": "novel", "title": "T", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], verdict={"publishable": False, "threshold": 7.0,
                                             "global_scores": {"节奏": 6.0}})
        step, _ = store.add_step(run["id"], "draft", "claude-code", "Claude")
        store.finish_step(run["id"], step["n"], "done", summary="x")   # 非 mock → 会尝试总结

        calls = {"chat": 0}

        def fake_resolve():
            return ({"id": "p1", "name": "编排网关"}, "m1")

        def fake_chat(pid, model, prompt, **kw):
            calls["chat"] += 1
            self.assertIn("复盘官", prompt)
            return {"ok": True, "tokens": 10, "error": "",
                    "text": '```json\n{"lessons": [{"title": "章间衔接", '
                            '"content": "写章纲时先定全书节奏曲线"}]}\n```'}

        orig_r, orig_c = modelhub.resolve_orchestrator, modelhub.chat
        modelhub.resolve_orchestrator, modelhub.chat = fake_resolve, fake_chat
        try:
            n = skills.learn_from_run(run["id"])
        finally:
            modelhub.resolve_orchestrator, modelhub.chat = orig_r, orig_c
        self.assertEqual(calls["chat"], 1)
        self.assertEqual(n, 1)
        self.assertEqual(skills.list_lessons("novel")[0]["title"], "章间衔接")


if __name__ == "__main__":
    import unittest as _u
    _u.main()
