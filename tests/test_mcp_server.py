# -*- coding: utf-8 -*-
"""CodeBee MCP 服务器（app/mcp_server.py）单测：handler 单测覆盖 5 工具 +
stdio 形态 smoke（真子进程 initialize/tools/list）。

跑法：python -m unittest discover -s tests -p "test_mcp_server.py" -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from base import BaseTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import mcp_server  # noqa: E402  （app/ 目录下的入口模块，与 main.py 同形态）


class TestHandleRequest(BaseTest):

    def test_initialize_and_tools_list(self):
        r = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(r["result"]["serverInfo"]["name"], "codebee")
        r2 = mcp_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r2["result"]["tools"]]
        self.assertEqual(names, ["create_task", "get_status", "list_recent",
                                 "usage_summary", "bench_leaderboard"])
        # 通知无响应；未知方法报错
        self.assertIsNone(mcp_server.handle_request({"method": "notifications/initialized"}))
        r3 = mcp_server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
        self.assertIn("error", r3)

    def test_create_task_enqueues_real_chain(self):
        from core import store
        with mock.patch.object(mcp_server, "_enqueue") as menq:
            r = mcp_server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                           "params": {"name": "create_task",
                                                      "arguments": {"goal": "写一封确认邮件",
                                                                    "type": "email",
                                                                    "workdir": str(self.workdir)}}})
        out = r["result"]["content"]
        self.assertIn("已创建并排队", out)
        self.assertTrue(menq.called)
        job = menq.call_args.args[0]
        self.assertEqual(job["kind"], "orchestration")
        task = store.get_task(job["task_id"])
        self.assertEqual(task["type"], "email")
        self.assertTrue(store.get_run(job["run_id"]))

    def test_create_task_requires_goal(self):
        r = mcp_server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                       "params": {"name": "create_task", "arguments": {}}})
        self.assertTrue(r["result"]["isError"])

    def test_get_status_and_unknown_tool(self):
        from core import store
        task = store.create_task({"type": "direct", "title": "夜更", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="failed", error="boom")
        r = mcp_server.handle_request({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                       "params": {"name": "get_status",
                                                  "arguments": {"task_id": task["id"]}}})
        out = r["result"]["content"]
        self.assertIn("夜更", out)
        self.assertIn("failed", out)
        self.assertIn("boom", out)
        r2 = mcp_server.handle_request({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                        "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(r2["result"]["isError"])

    def test_usage_summary_shape(self):
        from app.core import usage
        usage.record(source="pipeline", run_id="r", task_id="t", task_type="code",
                     role="x", model="m", provider="p", ok=True,
                     usage={"input": 100, "output": 50})
        r = mcp_server.handle_request({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                       "params": {"name": "usage_summary", "arguments": {}}})
        self.assertIn("今日花费", r["result"]["content"])


class TestStdioSmoke(BaseTest):

    def test_spawn_initialize_and_tools_list(self):
        env = {**dict(__import__("os").environ), "PYTHONPATH": str(ROOT),
               "TUTTI_DATA": str(self.data_dir)}
        proc = subprocess.Popen([sys.executable, "-X", "utf8",
                                 str(ROOT / "app" / "mcp_server.py")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        try:
            reqs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]
            for rq in reqs:
                proc.stdin.write((json.dumps(rq) + "\n").encode("utf-8"))
            proc.stdin.flush()
            outs = []
            for _ in range(2):
                line = proc.stdout.readline()
                if line:
                    outs.append(json.loads(line.decode("utf-8")))
            self.assertEqual(outs[0]["result"]["serverInfo"]["name"], "codebee")
            self.assertEqual(len(outs[1]["result"]["tools"]), 5)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.kill()


if __name__ == "__main__":
    unittest.main()
