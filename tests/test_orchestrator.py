# -*- coding: utf-8 -*-
"""编排中枢（编排者模型）测试：配置校验、直连 chat（本地假 API）、规划优先级。

编排者 = Tutti 自己的智能体：直连供应商 API 的模型，统一规划/难度判定/大纲；
未启用或失败时回落 CLI 智能体规划，流程不阻塞。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from base import BaseTest

FAKE_KEY = "sk-test-" + "cccccccc" * 3


class _FakeOpenAI(HTTPServer):
    """假 openai 协议网关：记录请求，返回固定 chat completion。"""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests = []
        self.reply = {"choices": [{"message": {"content": "你好，已收到"}}],
                      "usage": {"total_tokens": 42}}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _server(self):
        return self.server

    def do_POST(self):
        srv = self._server()
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        auth = self.headers.get("Authorization") or ""
        srv.requests.append({"path": self.path, "auth": auth, "body": body})
        out = json.dumps(srv.reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestOrchestrator(BaseTest):
    def runTest(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "网关G", "protocol": "openai",
                                  "base_url": "https://g.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]

        # 1) 配置校验：供应商不存在拒绝；默认未启用
        self.assertTrue(modelhub.set_orchestrator("ghost", model="m", enabled=True))
        self.assertIsNone(modelhub.set_orchestrator(pid, model="gpt-x", enabled=True))
        self.assertIsNone(modelhub.resolve_orchestrator())   # enabled True 但刚写入即可用
        orch = modelhub.resolve_orchestrator()
        self.assertEqual(orch[0]["id"], pid)
        self.assertEqual(orch[1], "gpt-x")
        # 未选模型 → 用供应商默认
        modelhub.set_orchestrator(pid, model="", enabled=True)
        modelhub._save({**modelhub._load()})   # 触发一次读写循环
        data = modelhub._load()
        data["providers"][0]["model"] = "gateway-default"
        modelhub._save(data)
        self.assertEqual(modelhub.resolve_orchestrator()[1], "gateway-default")
        # 停用 → None；停用供应商 → None
        modelhub.set_orchestrator(pid, enabled=False)
        self.assertIsNone(modelhub.resolve_orchestrator())
        modelhub.set_orchestrator(pid, enabled=True)
        modelhub.providers_op([pid], "disable")
        self.assertIsNone(modelhub.resolve_orchestrator())
        modelhub.providers_op([pid], "enable")
        # view 脱敏且带 ready
        view = modelhub.orchestrator_view()
        self.assertTrue(view["ready"])
        self.assertNotIn(FAKE_KEY, json.dumps(view))

    def test_chat_against_local_gateway(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        srv = _FakeOpenAI()
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            base = "http://127.0.0.1:%d/v1" % srv.server_address[1]
            modelhub.upsert_provider({"name": "本地网关", "protocol": "openai",
                                      "base_url": base, "api_key": FAKE_KEY})
            pid = modelhub.providers()[0]["id"]
            res = modelhub.chat(pid, "gpt-x", "测试消息", timeout=10)
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(res["text"], "你好，已收到")
            self.assertEqual(res["tokens"], 42)
            req = srv.requests[0]
            self.assertTrue(req["auth"].endswith(FAKE_KEY[-6:]))
            self.assertEqual(req["body"]["model"], "gpt-x")
            self.assertEqual(req["body"]["messages"][0]["content"], "测试消息")
            # 错误模型/供应商：不存在时明确报错而不是崩
            bad = modelhub.chat("nope", "m", "x")
            self.assertFalse(bad["ok"])
        finally:
            srv.shutdown()

    def test_planner_prefers_orchestrator(self):
        from app.core import modelhub, planner
        calls = {"chat": 0}

        def fake_resolve():
            return ({"id": "p1", "name": "编排网关"}, "orch-x")

        def fake_chat(pid, model, prompt, **kw):
            calls["chat"] += 1
            return {"ok": True, "text": '```json\n{"difficulty": "hard", "subtasks": '
                                        '[{"title": "A", "detail": "da"}, '
                                        '{"title": "B", "detail": "db"}]}\n```',
                    "tokens": 10, "error": ""}

        orig_r, orig_c = modelhub.resolve_orchestrator, modelhub.chat
        modelhub.resolve_orchestrator = fake_resolve
        modelhub.chat = fake_chat
        try:
            task = {"goal": "做个功能", "context": "", "verify_command": "exit 0"}
            plan = planner.make_code_plan(task, {"id": "mock-a", "mode": "mock"}, ".")
            self.assertEqual(calls["chat"], 1)          # 编排者优先：不问 CLI
            self.assertTrue(plan["source"].startswith("编排者("))
            self.assertEqual(plan["difficulty"], "hard")
            self.assertEqual(len(plan["steps"]), 2)
            # 编排者失败 → 回落 CLI（mock → 模板）
            def bad_chat(*a, **k):
                return {"ok": False, "text": "", "tokens": 0, "error": "boom"}
            modelhub.chat = bad_chat
            plan2 = planner.make_code_plan(task, {"id": "mock-a", "mode": "mock"}, ".")
            self.assertEqual(plan2["source"], "template")
            # review 大纲
            modelhub.chat = fake_chat
            modelhub.chat = lambda *a, **k: {"ok": True, "text": '```json\n{"outline": '
                                               '["要点1", "要点2"]}\n```', "tokens": 1, "error": ""}
            outline = planner.make_review_outline(task)
            self.assertEqual(outline["items"], ["要点1", "要点2"])
            self.assertTrue(outline["source"].startswith("编排者("))
        finally:
            modelhub.resolve_orchestrator = orig_r
            modelhub.chat = orig_c


if __name__ == "__main__":
    import unittest as _u
    _u.main()
