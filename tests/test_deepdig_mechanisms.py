# -*- coding: utf-8 -*-
"""待深挖四连（2026-09-21 第二十班后落地）专项测试：

1. gate 验证（pi-subagents）：verify 失败首轮跳过模型评审，连败 2 轮恢复。
2. 资源账本（角色资源账本）：评审 <ledger> 增量追加文件 + 起草注入读取。
3. 逐项目标审稿（AI-Novel-Writer）：<event_check> 解析 + 模板占位符在位。
"""
from __future__ import annotations

import os

from base import BaseTest


class GateVerifyTests(BaseTest):
    def test_verify_fail_first_round_skips_model_review(self):
        """verify 恒败：首轮无 review 步骤（gate 跳过），修复轮后恢复评审。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "gate 验证", "goal": "g",
            "workdir": str(self.workdir), "mode": "expert",
            "verify_command": "exit 1",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        roles = [s["role"] for s in run["steps"]]
        n_review = roles.count("review")
        n_fix = len([r for r in roles if r.startswith("fix-r")])
        # gate 语义：首轮实现后 verify 失败 → 无评审直接 fix；连败 2 轮后恢复
        # 评审参与诊断。断言：第一个 review 必须出现在第一个 fix 之后（首轮被跳过）
        self.assertIn("review", roles)
        self.assertTrue(roles.index("review") > roles.index("fix-r1"),
                        "首轮评审未被 gate 跳过：roles=%s" % roles)
        self.assertGreater(n_review, 0)   # 连败后确实恢复了评审
        v = run["verdict"]
        self.assertFalse(v["pass"])   # 验证恒败最终不通过

    def test_verify_pass_keeps_review(self):
        """verify 通过：评审照常执行（gate 不干预正常路径）。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "gate 放行", "goal": "g",
            "workdir": str(self.workdir), "mode": "expert",
            "verify_command": "exit 0",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertTrue(run["verdict"]["pass"])
        roles = [s["role"] for s in run["steps"]]
        self.assertIn("review", roles)


class LedgerTests(BaseTest):
    def test_parse_tagged_lines(self):
        from app.core.pipeline import _parse_tagged_lines
        text = "评审正文……\n<event_check>\n1. 已完成：主角拿到令牌（第3段）\n2. 未完成：未出现追兵\n</event_check>\n收尾"
        self.assertEqual(_parse_tagged_lines(text, "event_check"),
                         ["1. 已完成：主角拿到令牌（第3段）", "2. 未完成：未出现追兵"])
        self.assertEqual(_parse_tagged_lines("没有块", "event_check"), [])
        self.assertEqual(_parse_tagged_lines(None, "ledger"), [])

    def test_ledger_roundtrip(self):
        from app.core.pipeline import _append_ledger, _read_ledger
        _append_ledger(str(self.workdir), 3,
                       ["道具|玄铁令|交给男主", "伤情|左臂|包扎中"])
        _append_ledger(str(self.workdir), 4, ["承诺|三日后比武|未兑现"])
        txt = _read_ledger(str(self.workdir))
        self.assertIn("玄铁令", txt)
        self.assertIn("第 3 章", txt)
        self.assertIn("三日后比武", txt)
        # 文件落位在 .codebee/resource-ledger.md
        self.assertTrue(os.path.isfile(
            os.path.join(str(self.workdir), ".codebee", "resource-ledger.md")))

    def test_ledger_path_guard(self):
        from app.core.pipeline import _append_ledger
        # workdir 缺失时路径越界必须抛错，绝不写出工作目录
        with self.assertRaises(Exception):
            _append_ledger(None, 1, ["道具|x|y"])

    def test_chapter_prompt_has_ledger_slot(self):
        """起草模板必须保留 __LEDGER__ 占位（账本注入通道）。"""
        from app.core.pipeline import SERIAL_CHAPTER_PROMPT
        self.assertIn("__LEDGER__", SERIAL_CHAPTER_PROMPT)
        self.assertIn("资源账本", SERIAL_CHAPTER_PROMPT)
