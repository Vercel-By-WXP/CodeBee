# -*- coding: utf-8 -*-
"""MCP stdio 客户端（mcp_client）+ builtin_agent 接线单测。

用测试运行时写出的「假 MCP 服务器」走真子进程验证 initialize/tools/list/
tools/call 全链；另覆盖配置校验、崩溃/无响应服务器、dispatch 全名路由、
builtin_agent 的工具清单合并与执行分发。

跑法：python -m unittest discover -s tests -p "test_mcp_client.py" -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from base import BaseTest

from app.core import mcp_client


FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    method = req.get("method")
    if method == "initialize":
        resp = {"jsonrpc": "2.0", "id": req.get("id"),
                "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "serverInfo": {"name": "fake", "version": "0"}}}
    elif method == "tools/list":
        resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": [
            {"name": "echo", "description": "回声工具",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                             "required": ["text"]}}]}}
    elif method == "tools/call":
        text = "echo:" + str((req.get("params") or {}).get("arguments", {}).get("text", ""))
        resp = {"jsonrpc": "2.0", "id": req.get("id"),
                "result": {"content": [{"type": "text", "text": text}], "isError": False}}
    else:
        if req.get("id") is None:
            continue
        resp = {"jsonrpc": "2.0", "id": req.get("id"),
                "error": {"code": -32601, "message": "no such method"}}
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()
'''


def _fake_server_cfg(tmp, name="fs"):
    script = Path(tmp) / ("fake_mcp_%s.py" % name)
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return {"name": name, "command": sys.executable,
            "args": ["-X", "utf8", str(script)], "env": {}}


class TestParseServers(BaseTest):

    def test_empty_ok(self):
        self.assertEqual(mcp_client.parse_servers(""), ([], None))

    def test_valid(self):
        srvs, err = mcp_client.parse_servers(
            json.dumps([{"name": "fs", "command": "npx", "args": ["-y", "x"],
                         "env": {"A": "1"}}]))
        self.assertIsNone(err, err)
        self.assertEqual(srvs[0]["name"], "fs")
        self.assertEqual(srvs[0]["args"], ["-y", "x"])

    def test_bad_cases(self):
        for bad in ("not json", "[1,2]", json.dumps([{"name": "Bad Name", "command": "x"}]),
                    json.dumps([{"name": "ok", "command": ""}])):
            srvs, err = mcp_client.parse_servers(bad)
            self.assertIsNone(srvs, bad)
            self.assertTrue(err, bad)

    def test_cap_four_servers(self):
        arr = [{"name": "s%d" % i, "command": "x"} for i in range(5)]
        _srvs, err = mcp_client.parse_servers(json.dumps(arr))
        self.assertIn("最多", err)


class TestLiveFakeServer(BaseTest):

    def setUp(self):
        super().setUp()
        self.tmp = self.data_dir / "mcpfix"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.cfg = _fake_server_cfg(self.tmp)

    def test_list_and_call(self):
        r = mcp_client.list_tools(self.cfg)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([t["name"] for t in r["tools"]], ["echo"])
        self.assertEqual(r["tools"][0]["input_schema"]["type"], "object")
        c = mcp_client.call_tool(self.cfg, "echo", {"text": "你好"})
        self.assertTrue(c["ok"], c.get("error"))
        self.assertEqual(c["text"], "echo:你好")

    def test_dispatch_full_name_via_settings(self):
        from app.core import settings as settings_mod
        text = json.dumps([{"name": "fs", "command": self.cfg["command"],
                            "args": self.cfg["args"]}])
        mcp_client.set_settings_text(lambda: text)
        r = mcp_client.dispatch_full_name("mcp__fs__echo", {"text": "hi"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["text"], "echo:hi")
        r2 = mcp_client.dispatch_full_name("mcp__nope__echo", {})
        self.assertFalse(r2["ok"])
        self.assertIn("未配置", r2["error"])

    def test_tool_specs_cached(self):
        from app.core import settings as settings_mod
        text = json.dumps([{"name": "fs", "command": self.cfg["command"],
                            "args": self.cfg["args"]}])
        mcp_client.set_settings_text(lambda: text)
        specs = mcp_client.tool_specs_cached()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["full_name"], "mcp__fs__echo")
        # 第二次走缓存（服务器文件删掉也能拿到）
        (self.tmp / "fake_mcp_fs.py").unlink()
        specs2 = mcp_client.tool_specs_cached()
        self.assertEqual(specs2[0]["full_name"], "mcp__fs__echo")

    def test_dead_server_folds_error(self):
        r = mcp_client.list_tools({"name": "dead", "command": sys.executable,
                                   "args": ["-c", "raise SystemExit(1)"], "env": {}})
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])

    def test_silent_server_times_out(self):
        r = mcp_client.list_tools({"name": "quiet", "command": sys.executable,
                                   "args": ["-c", "import time; time.sleep(30)"],
                                   "env": {}})
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])


class TestBuiltinAgentWiring(BaseTest):

    def test_tools_merged_and_dispatched(self):
        from app.core import builtin_agent
        spec = {"server": "fs", "name": "echo", "full_name": "mcp__fs__echo",
                "description": "回声", "input_schema": {"type": "object", "properties": {}}}
        with mock.patch.object(mcp_client, "tool_specs_cached", return_value=[spec]), \
             mock.patch.object(mcp_client, "dispatch_full_name",
                               return_value={"ok": True, "text": "echo:ok", "error": ""}) as mdisp:
            names = [t["function"]["name"] for t in builtin_agent._openai_tools()]
            self.assertIn("mcp__fs__echo", names)
            a_names = [t["name"] for t in builtin_agent._anthropic_tools()]
            self.assertIn("mcp__fs__echo", a_names)
            out = builtin_agent._exec_tool("wd", "mcp__fs__echo", {"text": "x"})
            self.assertEqual(out, "echo:ok")
            self.assertEqual(mdisp.call_args.args[0], "mcp__fs__echo")

    def test_unknown_mcp_server_name_stays_unknown_tool(self):
        from app.core import builtin_agent
        out = builtin_agent._exec_tool("wd", "not_mcp_name", {})
        self.assertIn("未知工具", out)


if __name__ == "__main__":
    unittest.main()
