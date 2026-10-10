# -*- coding: utf-8 -*-
"""大屏实时流回归（2026-10-10 用户反馈）：连载全 CLI 流程在大屏上永远
「等待实时输出…」而弹窗日志有内容——外部 CLI 步骤只落日志文件，run.json
里没有 stream 字段，/api/board 聚合只读 stream/thinking。修为 CLI 步骤
回退读日志尾巴（与蜂巢卡 hiveTick 同源，tail 夹取不整读大日志）。"""
from __future__ import annotations

from base import BaseTest


class TestBoardLiveOutput(BaseTest):

    def _running_card(self, board, run_id):
        from app.core import board as board_mod
        board_mod._CACHE["data"] = None   # 8s TTL 聚合缓存按用例清空
        board_mod._CACHE["ts"] = 0.0
        data = board_mod.payload()
        cards = [c for c in data["running"] if c["run_id"] == run_id]
        self.assertEqual(len(cards), 1, "运行中任务必须出现在大屏卡片里")
        self.assertIsNotNone(cards[0]["current"], "运行中步骤必须给出 current 视图")
        return cards[0]["current"]

    def _serial_run(self, store):
        task = store.create_task({"type": "serial_novel", "title": "连载·大屏实时流",
                                  "goal": "验证大屏实时流", "workdir": str(self.workdir)})
        store.update_task_status(task["id"], "running")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        return run

    def test_cli_step_falls_back_to_log_tail(self):
        """CLI 步骤（无 stream/thinking）：大屏实时流回退读步骤日志尾巴。"""
        from app.core import board, store
        run = self._serial_run(store)
        step, log_abs = store.add_step(run["id"], "critique", "opencode",
                                       "OpenCode CLI", model="glm-5.3-flash")
        log_abs.write_text(
            "[消息] 评审开始\n"
            "视角/文风：女主限知（A场）→男主限知（B场），短句白描、对白攻防\n"
            "新增台账：贺临（特助/送检人，幕后线入口）、华正司法鉴定中心\n",
            encoding="utf-8")
        cur = self._running_card(board, run["id"])
        self.assertEqual(cur["live_label"], "output")
        self.assertIn("短句白描", cur["live_text"], "实时流窗口必须读到日志正文")
        self.assertEqual(cur["tail"], "新增台账：贺临（特助/送检人，幕后线入口）、华正司法鉴定中心")

    def test_builtin_stream_still_wins_over_log(self):
        """内置智能体步骤：stream 字段在，优先用流，不被日志回退覆盖。"""
        from app.core import board, store
        run = self._serial_run(store)
        step, log_abs = store.add_step(run["id"], "draft", "builtin", "CodeBee",
                                       model="glm-5.3-flash")
        log_abs.write_text("日志里的旧行\n", encoding="utf-8")
        self.assertTrue(store.stream_step(run["id"], step["n"],
                                          text="正在写第2章开头：机场对峙"))
        cur = self._running_card(board, run["id"])
        self.assertEqual(cur["live_label"], "output")
        self.assertIn("机场对峙", cur["live_text"])
        self.assertNotIn("日志里的旧行", cur["live_text"])

    def test_silent_step_stays_empty(self):
        """无流也无日志内容：维持空文本，前端继续显示等待占位。"""
        from app.core import board, store
        run = self._serial_run(store)
        store.add_step(run["id"], "outline", "claude-code", "Claude Code")
        cur = self._running_card(board, run["id"])
        self.assertEqual(cur["live_text"], "")
        self.assertEqual(cur["live_label"], "")
        self.assertEqual(cur["tail"], "")
