# -*- coding: utf-8 -*-
"""MCP stdio 客户端（mcp_client）+ builtin_agent 接线单测。

用测试运行时写出的「假 MCP 服务器」走真子进程验证 initialize/tools/list/
tools/call 全链；另覆盖配置校验、崩溃/无响应服务器、dispatch 全名路由、
builtin_agent 的工具清单合并与执行分发。

跑法：python -m unittest discover -s tests -p "test_mcp_client.py" -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
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

    def test_duplicate_names_rejected(self):
        arr = [{"name": "fs", "command": "x"}, {"name": "fs", "command": "y"}]
        srvs, err = mcp_client.parse_servers(json.dumps(arr))
        self.assertIsNone(srvs)
        self.assertIn("重名", err)

    def test_server_name_cannot_contain_tool_separator(self):
        srvs, err = mcp_client.parse_servers(json.dumps(
            [{"name": "fs__nested", "command": "x"}]))
        self.assertIsNone(srvs)
        self.assertIn("__", err)


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
        self.assertEqual(r["tools"][0]["input_schema"]["required"], ["text"])
        c = mcp_client.call_tool(self.cfg, "echo", {"text": "你好"})
        self.assertTrue(c["ok"], c.get("error"))
        self.assertEqual(c["text"], "echo:你好")

    def test_dispatch_full_name_via_settings(self):
        from app.core import settings as settings_mod
        settings_mod.save({"mcp_servers": json.dumps(
            [{"name": "fs", "command": self.cfg["command"], "args": self.cfg["args"]}])})
        r = mcp_client.dispatch_full_name("mcp__fs__echo", {"text": "hi"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["text"], "echo:hi")
        nested = mcp_client.dispatch_full_name("mcp__fs__echo__detail", {"text": "hi"})
        self.assertTrue(nested["ok"], nested.get("error"))
        self.assertEqual(nested["text"], "echo:hi")
        r2 = mcp_client.dispatch_full_name("mcp__nope__echo", {})
        self.assertFalse(r2["ok"])
        self.assertIn("未配置", r2["error"])

    def test_tool_specs_cached(self):
        from app.core import settings as settings_mod
        settings_mod.save({"mcp_servers": json.dumps(
            [{"name": "fs", "command": self.cfg["command"], "args": self.cfg["args"]}])})
        specs = mcp_client.tool_specs_cached()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["full_name"], "mcp__fs__echo")
        # 第二次走缓存（服务器文件删掉也能拿到）
        (self.tmp / "fake_mcp_fs.py").unlink()
        specs2 = mcp_client.tool_specs_cached()
        self.assertEqual(specs2[0]["full_name"], "mcp__fs__echo")

    def test_tool_cache_invalidates_on_config_change(self):
        from app.core import settings as settings_mod
        settings_mod.save({"mcp_servers": json.dumps([self.cfg])})
        self.assertEqual(len(mcp_client.tool_specs_cached()), 1)
        settings_mod.save({"mcp_servers": ""})
        self.assertEqual(mcp_client.tool_specs_cached(), [])

        other = _fake_server_cfg(self.tmp, name="other")
        settings_mod.save({"mcp_servers": json.dumps([other])})
        self.assertEqual([s["full_name"] for s in mcp_client.tool_specs_cached()],
                         ["mcp__other__echo"])

    def test_model_tool_specs_skip_unusable_names_and_duplicates(self):
        from app.core import settings as settings_mod
        settings_mod.save({"mcp_servers": json.dumps([self.cfg])})
        listed = {"ok": True, "error": "", "tools": [
            {"name": "read.file", "description": "bad", "input_schema": {}},
            {"name": "x" * 60, "description": "long", "input_schema": {}},
            {"name": "echo", "description": "first", "input_schema": {}},
            {"name": "echo", "description": "duplicate", "input_schema": {}}]}
        with mock.patch.object(mcp_client, "list_tools", return_value=listed):
            specs = mcp_client.tool_specs_cached(force=True)
        self.assertEqual([s["full_name"] for s in specs], ["mcp__fs__echo"])
        self.assertEqual(specs[0]["description"], "first")

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

    def test_failed_initialize_reaps_process_and_closes_pipes(self):
        processes = []
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            if args and args[0] and args[0][0] == sys.executable:
                processes.append(proc)
            return proc

        with mock.patch.object(mcp_client.subprocess, "Popen", side_effect=spawn), \
             mock.patch.object(mcp_client, "_INIT_TIMEOUT", 0.01):
            r = mcp_client.list_tools({"name": "quiet", "command": sys.executable,
                                       "args": ["-c", "import time; time.sleep(30)"],
                                       "env": {}})
        self.assertFalse(r["ok"])
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        for pipe in (processes[0].stdin, processes[0].stdout, processes[0].stderr):
            self.assertTrue(pipe.closed)

    def test_call_can_be_cancelled_during_server_wait(self):
        script = self.tmp / "slow_mcp.py"
        script.write_text(FAKE_SERVER.replace(
            'import json, sys', 'import json, sys, time').replace(
            'elif method == "tools/call":',
            'elif method == "tools/call":\n        time.sleep(30)'), encoding="utf-8")
        cfg = {"name": "slow", "command": sys.executable,
               "args": ["-X", "utf8", str(script)], "env": {}}
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            result = mcp_client.call_tool(cfg, "echo", {}, cancel_event=cancel)
        finally:
            timer.cancel()
        self.assertFalse(result["ok"])
        self.assertIn("取消", result["error"])
        self.assertLess(time.monotonic() - started, 3)

    def test_cancel_does_not_wait_for_child_holding_stdout(self):
        script = self.tmp / "child_mcp.py"
        script.write_text(FAKE_SERVER.replace(
            'import json, sys', 'import json, sys, subprocess, time').replace(
            'elif method == "tools/call":',
            'elif method == "tools/call":\n'
            '        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], '
            'stdout=sys.stdout, stderr=sys.stderr)\n'
            '        time.sleep(30)'), encoding="utf-8")
        cfg = {"name": "child", "command": sys.executable,
               "args": ["-X", "utf8", str(script)], "env": {}}
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            result = mcp_client.call_tool(cfg, "echo", {}, cancel_event=cancel)
        finally:
            timer.cancel()
        self.assertFalse(result["ok"])
        self.assertIn("取消", result["error"])
        self.assertLess(time.monotonic() - started, 3)

    def test_global_deadline_limits_initialize_wait(self):
        started = time.monotonic()
        result = mcp_client.call_tool(
            {"name": "quiet", "command": sys.executable,
             "args": ["-c", "import time; time.sleep(30)"], "env": {}},
            "echo", {}, deadline=started + 0.2)
        self.assertFalse(result["ok"])
        self.assertIn("总时限", result["error"])
        self.assertLess(time.monotonic() - started, 3)


class TestBuiltinAgentWiring(BaseTest):

    def _run_with_mcp_call(self, **run_kwargs):
        from app.core import builtin_agent
        bi = {"prov": {"id": "p", "name": "P", "protocol": "openai",
                       "base_url": "http://gw.test/v1", "api_key": "sk-test",
                       "enabled": True, "model": "m"},
              "model": "m", "provider_id": "p", "provider_name": "P"}
        response = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "mcp__fs__echo", "arguments": "{}"}}]}}]}
        with mock.patch.object(builtin_agent, "_post_json",
                               return_value=(200, response, "")):
            return builtin_agent.run(bi, "go", str(self.workdir), stream=False,
                                     **run_kwargs)

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

    def test_mcp_dispatch_receives_cancel_and_deadline(self):
        from app.core import builtin_agent
        cancel = threading.Event()
        deadline = time.monotonic() + 5
        with mock.patch.object(mcp_client, "dispatch_full_name",
                               return_value={"ok": True, "text": "done"}) as dispatch:
            out = builtin_agent._exec_tool(
                "wd", "mcp__fs__echo", {}, cancel_event=cancel, deadline=deadline)
        self.assertEqual(out, "done")
        self.assertIs(dispatch.call_args.kwargs["cancel_event"], cancel)
        self.assertEqual(dispatch.call_args.kwargs["deadline"], deadline)

    def test_run_marks_cancelled_after_mcp_tool(self):
        cancel = threading.Event()

        def stop(*_args, **_kwargs):
            cancel.set()
            return {"ok": False, "text": "", "error": "任务已取消"}

        with mock.patch.object(mcp_client, "dispatch_full_name", side_effect=stop):
            result = self._run_with_mcp_call(cancel_event=cancel)
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])

    def test_run_marks_deadline_after_mcp_tool(self):
        def slow(*_args, **_kwargs):
            time.sleep(0.2)
            return {"ok": False, "text": "", "error": "任务总时限已到"}

        with mock.patch.object(mcp_client, "dispatch_full_name", side_effect=slow):
            result = self._run_with_mcp_call(deadline=time.monotonic() + 0.1)
        self.assertFalse(result["ok"])
        self.assertTrue(result["raw"]["deadline_exceeded"])

    def test_unknown_mcp_server_name_stays_unknown_tool(self):
        from app.core import builtin_agent
        out = builtin_agent._exec_tool("wd", "not_mcp_name", {})
        self.assertIn("未知工具", out)


if __name__ == "__main__":
    unittest.main()
