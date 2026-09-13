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
            json.dumps({"type": "user", "cwd": "E:\proj\claude-demo",
                        "message": {"content": "再查一遍"}}),
        ]
        clf = clroot / "abc-uuid.jsonl"
        clf.write_text("\n".join(claude_lines), encoding="utf-8")

        sessions.codex_root = lambda: croot.parent.parent.parent
        sessions.claude_root = lambda: clroot.parent

        # qwen：projects/<slug>/chats/<sid>.jsonl，取 real_user 首条消息与 cwd
        qroot = self.tmp / "qwen-projects"
        chats = qroot / "e--work-demo" / "chats"
        chats.mkdir(parents=True)
        qsid = "11112222-3333-4444-5555-666677778888"
        (chats / (qsid + ".jsonl")).write_text(json.dumps({
            "type": "user", "provenance": "real_user", "cwd": "E:\\work\\demo",
            "message": {"role": "user", "parts": [{"text": "帮我写个爬虫"}]}}), encoding="utf-8")
        # 合成消息（系统注入）也是真实会话文件，应列出但不作为预览
        qsid2 = "aaaabbbb-3333-4444-5555-666677778888"
        (chats / (qsid2 + ".jsonl")).write_text(json.dumps({
            "type": "user", "provenance": "synthetic", "cwd": "E:\\work\\demo",
            "message": {"role": "user", "parts": [{"text": "<environment_context>..."}]}}),
            encoding="utf-8")

        # opencode 系：SQLite（opencode/mimo 同 schema），只读打开
        import sqlite3
        db = self.tmp / "mimocode.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE session (id TEXT, directory TEXT, title TEXT,"
            " time_updated INTEGER, time_archived INTEGER);"
            "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
            "CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,"
            " time_created INTEGER, data TEXT);")
        con.execute("INSERT INTO session VALUES"
                    " ('ses_a','E:/work','New session - 2026-09-04',1788500000000,NULL)")
        con.execute("INSERT INTO message VALUES"
                    " ('msg_1','ses_a',1,'%s')" % json.dumps({"role": "user"}))
        con.execute("INSERT INTO part VALUES"
                    " ('p1','msg_1','ses_a',1,'%s')"
                    % json.dumps({"type": "text", "text": "继续上次的重构"}))
        con.commit()
        con.close()

        orig = (sessions.codex_root, sessions.claude_root, sessions.qwen_root,
                sessions.opencode_db, sessions.mimo_db)
        sessions.codex_root = lambda: croot.parent.parent.parent
        sessions.claude_root = lambda: clroot.parent
        sessions.qwen_root = lambda: qroot
        sessions.opencode_db = lambda: self.tmp / "no-opencode.db"  # 走无库→空列表分支
        sessions.mimo_db = lambda: db
        try:
            data = sessions.scan(force=True)
        finally:
            (sessions.codex_root, sessions.claude_root, sessions.qwen_root,
             sessions.opencode_db, sessions.mimo_db) = orig
        cx = [s for s in data["codex-cli"] if s["session_id"] == "sid-123"]
        self.assertEqual(len(cx), 1)
        self.assertEqual(cx[0]["preview"], "帮我写个限流器")
        self.assertEqual(cx[0]["project"], "E:\\proj\\demo")
        self.assertEqual(cx[0]["turns"], 2)
        cl = [s for s in data["claude-code"] if s["session_id"] == "abc-uuid"]
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0]["preview"], "检查登录逻辑")
        # 行内有真实 cwd 时用它（目录名只是转义 slug，不能当路径）；无则退回 slug
        self.assertEqual(cl[0]["project"], "E:\proj\claude-demo")
        # qwen：真实会话均列出，预览取 real_user 的首条
        self.assertEqual({s["session_id"] for s in data["qwencode"]}, {qsid, qsid2})
        qreal = next(s for s in data["qwencode"] if s["session_id"] == qsid)
        self.assertIn("爬虫", qreal["preview"])
        self.assertEqual(qreal["project"], "E:\\work\\demo")
        # opencode 系：SQLite 取 id/目录/首条用户文本
        self.assertEqual(data["mimo-code"][0]["session_id"], "ses_a")
        self.assertIn("重构", data["mimo-code"][0]["preview"])
        self.assertEqual(data["mimo-code"][0]["project"], "E:/work")
        # scan 结果恒定包含五个已知源（UI 判断支持面用）
        self.assertEqual(
            set(data.keys()),
            {"codex-cli", "claude-code", "opencode", "qwencode", "mimo-code"})


class TestResumeArgv(BaseTest):
    def runTest(self):
        from app.core import runner
        codex = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        claude = {"id": "claude-code", "kind": "claude", "mode": "real", "command": "claude"}
        # 直接检查内部构建逻辑：用 monkeypatch 拦截 run_process（不得真实调用 CLI）
        import app.core.runner as R
        captured = {}
        orig = R.run_process
        def fake_run(argv=None, stdin_text=None, **kw):
            captured["argv"] = argv
            captured["stdin"] = stdin_text
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

        # opencode：run -s <sid>，提示词走 stdin
        R.run_agent({"id": "opencode", "kind": "opencode", "mode": "real",
                     "command": "opencode"}, "hi", resume="ses_x")
        self.assertEqual(captured["argv"][-3:], ["run", "-s", "ses_x"])
        # qwen：-r <sid>
        R.run_agent({"id": "qwencode", "kind": "qwen", "mode": "real",
                     "command": "qwen"}, "hi", resume="sid9")
        self.assertEqual(captured["argv"][captured["argv"].index("-r") + 1], "sid9")
        # generic：resume 模板替换 {session}，模板不带 {prompt} 时提示词走 stdin
        R.run_agent({"id": "mimo-code", "kind": "generic", "mode": "real", "command": "mimo",
                     "argv_template": ["run", "{prompt}"],
                     "resume_argv_template": ["run", "-s", "{session}"]}, "hi", resume="ses_z")
        self.assertEqual(captured["argv"][-3:], ["run", "-s", "ses_z"])
        self.assertEqual(captured.get("stdin"), "hi")
        # generic 无恢复模板：直接拒绝，不发起 CLI 调用
        def boom(argv=None, **kw):
            raise AssertionError("不应发起 CLI 调用")
        R.run_process = boom
        try:
            out = R.run_agent({"id": "kimi-code", "kind": "generic", "mode": "real",
                               "command": "kimi", "argv_template": ["-p", "{prompt}"]},
                              "hi", resume="sid1")
        finally:
            R.run_process = fake_run
        self.assertFalse(out["ok"])
        self.assertIn("resume_argv_template", out["error"])
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


class TestResumeWorkdir(BaseTest):
    """续会话的工作目录：CLI 必须在会话所属项目目录下启动才能定位到会话。"""

    def runTest(self):
        from app.core import pipeline
        ctx = {"project": str(self.workdir)}
        self.assertEqual(pipeline._resume_workdir(ctx, "FALLBACK"), str(self.workdir))
        # 会话目录已不存在 → 退回任务工作目录，不让 CLI 起在坏路径上
        gone = {"project": str(self.tmp / "no-such-dir")}
        self.assertEqual(pipeline._resume_workdir(gone, "FALLBACK"), "FALLBACK")
        # 无 resume / 无 project 时原样返回
        self.assertEqual(pipeline._resume_workdir(None, "FALLBACK"), "FALLBACK")
        self.assertEqual(pipeline._resume_workdir({"project": ""}, "FALLBACK"), "FALLBACK")
        # _valid_resume 透传 project
        agents = [{"id": "opencode", "mode": "real", "kind": "opencode", "command": "opencode"}]
        task = {"resume": {"agent": "opencode", "session": "s1", "project": "E:\work"}}
        v = pipeline._valid_resume(task, agents)
        self.assertEqual(v["project"], "E:\work")
        self.assertEqual(v["session"], "s1")


class TestResumeProjectPersist(BaseTest):
    """resume.project 落盘（UI 选中会话时带上，运行期用它定位 CLI 启动目录）。"""

    def runTest(self):
        from app.core import store
        t = store.create_task({"type": "code", "goal": "g", "workdir": str(self.workdir),
                               "resume": {"agent": "opencode", "session": "s1",
                                          "project": "E:\work\demo", "preview": "p"}})
        self.assertEqual(t["resume"]["project"], "E:\work\demo")
        # 不带 project 时不凭空造字段
        t2 = store.create_task({"type": "code", "goal": "g", "workdir": str(self.workdir),
                                "resume": {"agent": "codex-cli", "session": "s2"}})
        self.assertNotIn("project", t2["resume"])


class TestResumeBypassesEnableSwitch(BaseTest):
    """续会话是显式指定 CLI：目标未启用编排时也应注入本次运行。"""

    def runTest(self):
        from app.core import registry, catalog, paths
        import json as _json
        entries = [dict(e) for e in catalog.DEFAULT_CATALOG]
        paths.CATALOG_FILE.write_text(_json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        catalog._CACHE["entries"] = None
        detected = {"opencode": {"installed": True}, "codex-cli": {"installed": True}}
        # 未启用 → 不进路由池
        pool = registry.effective_agents(catalog.load(), detected)
        self.assertNotIn("opencode", [a["id"] for a in pool])
        # 但 installed_agent 仍能按 id 构建（供续会话注入）
        a = registry.installed_agent("opencode", catalog.load(), detected)
        self.assertIsNotNone(a)
        self.assertEqual(a["kind"], "opencode")
        # 未安装 → 不构建
        self.assertIsNone(registry.installed_agent("aider", catalog.load(), detected))
        # 无编排配置的 id → 不构建
        self.assertIsNone(registry.installed_agent("nope", catalog.load(), detected))
        catalog._CACHE["entries"] = None


if __name__ == "__main__":
    import unittest as _u
    _u.main()
