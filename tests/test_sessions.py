# -*- coding: utf-8 -*-
"""会话扫描 / resume 参数 / 极简输入 测试。"""
from __future__ import annotations

import json
from pathlib import Path

from base import BaseTest


class TestSessionsScan(BaseTest):
    def runTest(self):
        from app.core import sessions
        # 构造假会话目录
        croot = self.tmp / "codex-sessions" / "2026" / "09" / "11"
        croot.mkdir(parents=True)
        codex_lines = [
            json.dumps({"type": "session_meta", "payload": {
                "id": "sid-123", "cwd": "E:\\proj\\demo"}}),
            json.dumps({"type": "event_msg", "payload": {"type": "user_message",
                                                         "message": "帮我写个限流器"}}),
            json.dumps({"type": "event_msg", "payload": {"type": "user_message",
                                                         "message": "第二步"}}),
        ]
        cf = croot / "rollout-x.jsonl"
        cf.write_text("\n".join(codex_lines), encoding="utf-8")

        clroot = self.tmp / "claude-projects" / "E--demo"
        clroot.mkdir(parents=True)
        claude_lines = [
            json.dumps({"type": "queue-operation", "operation": "enqueue",
                        "content": "检查登录逻辑"}),
            json.dumps({"type": "user", "message": {"content": "再查一遍"}}),
        ]
        clf = clroot / "abc-uuid.jsonl"
        clf.write_text("\n".join(claude_lines), encoding="utf-8")

        sessions.codex_root = lambda: croot.parent.parent.parent
        sessions.claude_root = lambda: clroot.parent
        data = sessions.scan(force=True)
        cx = [s for s in data["codex-cli"] if s["session_id"] == "sid-123"]
        self.assertEqual(len(cx), 1)
        self.assertEqual(cx[0]["preview"], "帮我写个限流器")
        self.assertEqual(cx[0]["project"], "E:\\proj\\demo")
        self.assertEqual(cx[0]["turns"], 2)
        cl = [s for s in data["claude-code"] if s["session_id"] == "abc-uuid"]
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0]["preview"], "检查登录逻辑")
        self.assertEqual(cl[0]["project"], "E--demo")


class TestResumeArgv(BaseTest):
    def runTest(self):
        from app.core import runner
        codex = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        claude = {"id": "claude-code", "kind": "claude", "mode": "real", "command": "claude"}
        # 直接检查内部构建逻辑：用 monkeypatch 拦截 run_process（不得真实调用 CLI）
        import app.core.runner as R
        captured = {}
        orig = R.run_process
        def fake_run(argv=None, **kw):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": '{"type":"result"}', "stderr": "",
                    "duration": 0, "cancelled": False, "timed_out": False}
        R.run_process = fake_run
        R.run_agent(codex, "hi", resume="sid-123")
        self.assertIn("resume", captured["argv"])
        self.assertIn("sid-123", captured["argv"])
        self.assertIn("-", captured["argv"])  # stdin 占位
        self.assertIn("sandbox_mode", " ".join(str(a) for a in captured["argv"]))  # 读模式经 config 指定
        R.run_agent(claude, "hi", resume="abc")
        self.assertIn("--resume", captured["argv"])
        self.assertIn("abc", captured["argv"])
        R.run_process = orig
        # mock 智能体忽略 resume（不炸即可）
        R.run_agent({"id": "mock-a", "kind": "mock", "mode": "mock", "command": ""}, "hi", resume="x")


class TestMinimalTask(BaseTest):
    def runTest(self):
        from app.core import store
        # 只给 type/goal/workdir：标题自动取目标首行，模式自动 auto
        t = store.create_task({"type": "code", "goal": "给登录接口加限流\n更多描述",
                               "workdir": str(self.workdir)})
        self.assertEqual(t["title"], "给登录接口加限流")
        self.assertEqual(t["mode"], "auto")
        # resume 声明落盘
        t2 = store.create_task({"type": "novel", "goal": "g", "workdir": str(self.workdir),
                                "resume": {"agent": "codex-cli", "session": "sid-1",
                                           "preview": "p"}})
        self.assertEqual(t2["resume"]["session"], "sid-1")
        # resume 不完整则忽略
        t3 = store.create_task({"type": "code", "goal": "g", "workdir": str(self.workdir),
                                "resume": {"agent": "codex-cli"}})
        self.assertNotIn("resume", t3)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
