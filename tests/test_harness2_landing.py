# -*- coding: utf-8 -*-
"""Harness 2（deepseek-harness 0.2.x）落地回归：A3/A4/A5 + B6/B7/B8/B9/B10。

各项对应的 dsh 借鉴点：
  A3 已执行但结果未知的写操作 → 先核实副作用再重试（dsh 0.2.0 修复原则）
  A4 对话记忆手动压缩 /compact（dsh 0.2.1-alpha.3）
  A5 用户级全局指令 data/GLOBAL.md（dsh DSH_AGENTS_HOME/共享 AGENTS.md）
  B6 market 自定义索引源 + engines 兼容 + 版本可见/force 更新（dsh 0.2.1-rc.1）
  B7 Agent 创建技能草稿·保留草稿态（dsh 0.2.1-rc.1）
  B8 等待类失败不烧续跑轮次 + 并发压力提前提示（dsh 委派纪律）
  B9 MCP client HTTP（Streamable HTTP）transport
  B10 工具请求先上屏参数（dsh agent-tool-presentation）
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from app.core import jobs, market, market_remote, mcp_client, paths, skills
from app.core import builtin_agent_tools as bat


def _redirect(tmp: Path):
    """DATA_DIR 系重定向 + 各模块目录级缓存作废（skills/market 的惯用法）。"""
    patchers = [
        mock.patch.object(paths, "DATA_DIR", tmp),
        mock.patch.object(skills, "_user_pack_cache", {}),
        mock.patch.object(skills, "_user_dir_mtime", {"ts": 0.0, "ids": None}),
        mock.patch.object(skills, "_global_directives_cache", {}),
    ]
    for p in patchers:
        p.start()
    return patchers


def _stop(patchers):
    for p in patchers:
        p.stop()


# ---------------------------------------------------------------- B7 技能草稿

class SkillDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = _redirect(Path(self.tmp.name))

    def tearDown(self):
        _stop(self.patchers)
        self.tmp.cleanup()

    def test_save_and_list_draft(self):
        info, err = skills.save_draft("体例规范", "正文守则第一条", scopes=["serial_novel"],
                                      note="对话沉淀")
        self.assertIsNone(err)
        self.assertTrue(info["file"].startswith("draft-"))   # 中文名 → hash slug
        self.assertEqual(info["scopes"], ["serial_novel"])
        rows = skills.list_drafts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "体例规范")
        # 草稿不进用户包扫描面（drafts/ 子目录不在顶层 *.md 之列）
        self.assertEqual([p.name for p in skills.user_packs()], [])

    def test_duplicate_name_rejected(self):
        info, err = skills.save_draft("retry-rules", "A")
        self.assertIsNone(err)
        again, err2 = skills.save_draft("retry-rules", "B")
        self.assertIsNotNone(err2)
        self.assertIsNone(again)

    def test_promote_makes_user_pack_and_discard(self):
        info, _ = skills.save_draft("flow-rules", "守则正文")
        name, err = skills.promote_draft(info["file"])
        self.assertIsNone(err)
        self.assertEqual(name, "flow-rules")
        ups = skills.user_packs()
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0]["name"], "flow-rules")
        self.assertEqual(skills.list_drafts(), [])
        # 再启用同文件：草稿已不在 → 报错不误删
        _, err2 = skills.promote_draft(info["file"])
        self.assertIsNotNone(err2)

    def test_promote_refuses_existing_top_level(self):
        (paths.DATA_DIR / "skillpacks").mkdir(parents=True, exist_ok=True)
        (paths.DATA_DIR / "skillpacks" / "clash-pk.md").write_text("---\nname: x\n---\nB\n",
                                                                   encoding="utf-8")
        info, _ = skills.save_draft("clash-pk", "A")
        # 同名冲突：save_draft 本身就该拒（顶层同名包）
        self.assertIsNone(info)

    def test_discard_removes_file(self):
        info, _ = skills.save_draft("temp-rule", "A")
        ok, err = skills.discard_draft(info["file"])
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(skills.list_drafts(), [])
        ok2, err2 = skills.discard_draft(info["file"])
        self.assertFalse(ok2)

    def test_tool_save_skill_draft_writes_and_reports(self):
        out = bat._tool_save_skill_draft({
            "name": "排查套路", "content": "先看日志尾部",
            "scopes": "code, doc", "note": "测试"})
        self.assertIn("技能草稿已保存", out)
        self.assertIn("知识库", out)     # 提示用户去哪审核
        rows = skills.list_drafts()
        self.assertEqual(rows[0]["scopes"], ["code", "doc"])

    def test_action_policy_registered_write(self):
        spec = __import__("app.core.action_policy", fromlist=["x"]).spec_for("save_skill_draft")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.zone, "yellow")


# ---------------------------------------------------------------- A5 全局指令

class GlobalDirectivesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = _redirect(Path(self.tmp.name))

    def tearDown(self):
        _stop(self.patchers)
        self.tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(skills.global_directives(), "")
        self.assertEqual(skills.global_directives_block(), "")

    def test_reads_and_caches(self):
        d = paths.DATA_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / "GLOBAL.md").write_text("回复一律用中文。", encoding="utf-8")
        self.assertEqual(skills.global_directives(), "回复一律用中文。")
        block = skills.global_directives_block()
        self.assertIn("data/GLOBAL.md", block)
        self.assertIn("回复一律用中文。", block)
        # 改文件即失效（mtime 缓存）
        (d / "GLOBAL.md").write_text("回复用英文。", encoding="utf-8")
        import os
        st = (d / "GLOBAL.md").stat()
        os.utime(d / "GLOBAL.md", (st.st_atime, st.st_mtime + 5))
        self.assertEqual(skills.global_directives(), "回复用英文。")

    def test_size_cap(self):
        d = paths.DATA_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / "GLOBAL.md").write_text("x" * (skills.GLOBAL_DIRECTIVES_MAX_CHARS + 999),
                                     encoding="utf-8")
        text = skills.global_directives()
        self.assertLessEqual(len(text), skills.GLOBAL_DIRECTIVES_MAX_CHARS + 20)
        self.assertIn("截断", text)


# ---------------------------------------------------------------- A3 未知结果核实

class UnknownOutcomeGuidanceTests(unittest.TestCase):
    def test_run_command_timeout_note_tells_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(bat.runner, "run_process",
                                   return_value={"stdout": "", "stderr": "",
                                                 "timed_out": True, "exit_code": None}):
                out = bat._tool_run_command(tmp, {"command": "slow-write.cmd"})
        self.assertIn("超时", out)
        self.assertIn("结果未知", out)
        self.assertIn("核实", out)
        self.assertIn("不要直接重放", out)

    def test_mutating_tool_exception_carries_verify_hint(self):
        from app.core import policy
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = policy.normalize_sandbox({}, tmp)
            boom = mock.Mock(side_effect=RuntimeError("disk blew up mid-move"))
            with mock.patch.dict(bat._TOOL_IMPL, {"fs_manage": boom}):
                out = bat._exec_tool_impl(tmp, "fs_manage",
                                          {"action": "move", "path": "a", "dest": "b"},
                                          sandbox=sandbox)
        self.assertIn("工具执行失败", out)
        self.assertIn("结果未知", out)
        self.assertIn("核实", out)

    def test_read_tool_failure_has_no_verify_hint(self):
        # 只读工具失败 = 什么都没发生，不该带副作用核实指引（防噪音）
        boom = mock.Mock(side_effect=RuntimeError("io error"))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(bat._TOOL_IMPL, {"find_files": boom}):
                out = bat._exec_tool_impl(tmp, "find_files", {"pattern": "*.x"})
        self.assertIn("工具执行失败", out)
        self.assertNotIn("结果未知", out)


# ---------------------------------------------------------------- B10 请求工具带参数

class ActivityBriefTests(unittest.TestCase):
    def test_run_builtin_activity_carries_args(self):
        from app.core import builtin_agent as ba
        with tempfile.TemporaryDirectory() as tmp:
            prov = {"id": "prov-b", "name": "B", "protocol": "openai", "enabled": True,
                    "api_key": "k", "models": [{"name": "m", "enabled": True, "hidden": False}]}
            bi = {"prov": prov, "model": "m", "provider_id": "prov-b",
                  "provider_name": "B"}
            steps = [{"role": "assistant",
                      "tool_calls": [{"id": "c1", "name": "run_command",
                                      "args": {"command": "echo hi"}}]},
                     {"role": "assistant", "content": "完成。", "tool_calls": []}]
            state = {"i": 0}

            def fake_stream(*a, **k):
                s = steps[min(state["i"], len(steps) - 1)]
                state["i"] += 1
                return {"status": 200, "text": s.get("content") or "", "reasoning": "",
                        "usage": {}, "calls": s.get("tool_calls") or [], "events": 3,
                        "first_token_ms": 1.0, "tokens_per_sec": 1.0, "error": ""}

            acts = []
            with mock.patch.object(ba, "_post_sse_stream", side_effect=fake_stream), \
                 mock.patch.object(ba.modelhub, "_protocol_candidates",
                                   return_value=[("openai", "https://gw.example/v1")]), \
                 mock.patch.object(ba.modelhub, "_model_image_in", return_value=False), \
                 mock.patch.object(bat, "_mcp_specs", return_value=[]):
                res = ba.run(bi, "做点事", tmp, log=None, on_activity=acts.append)
            self.assertTrue(res.get("ok"))
            joined = " | ".join(acts)
            self.assertIn("请求工具: run_command（echo hi）", joined)


# ---------------------------------------------------------------- B8 等待类续跑分账

class WaitResumeTests(unittest.TestCase):
    def test_wait_error_classification(self):
        yes = ["HTTP 429 Too Many Requests", "rate limit exceeded",
               "额度不足，请充值", "429 使用上限", "服务商过载 overload", "稍后重试"]
        for s in yes:
            self.assertTrue(jobs.is_wait_type_error(s), s)
        no = ["模型拒答 refusal", "HTTP 400 bad request", "文件不存在", ""]
        for s in no:
            self.assertFalse(jobs.is_wait_type_error(s), s)

    def test_maybe_auto_resume_wait_type_keeps_budget(self):
        from app.core import store
        task = {"id": "t1", "serial": {"chapters": 20}, "workdir": "w"}
        run = {"id": "r1", "task_id": "t1", "status": "failed",
               "error": "HTTP 429 rate limit", "auto_resumes": 5,
               "auto_wait_resumes": 0, "chapter_scores": [1, 2]}
        new_run = {"id": "r2"}
        with mock.patch.object(store, "get_run", return_value=run), \
             mock.patch.object(store, "get_task", return_value=task), \
             mock.patch.object(store, "task_runs", return_value=[]), \
             mock.patch.object(jobs, "_task_active_run", return_value=None), \
             mock.patch.object(store, "retry_task", return_value=(True, "", new_run)) as rt, \
             mock.patch.object(store, "update_run") as ur, \
             mock.patch.object(jobs, "_schedule_enqueue") as se:
            self.assertTrue(jobs._maybe_auto_resume("r1"))
        rt.assert_called_once()
        # 等待类：正式预算不动，等待计数 +1
        kwargs = ur.call_args.kwargs
        self.assertEqual(kwargs.get("auto_resumes"), 5)
        self.assertEqual(kwargs.get("auto_wait_resumes"), 1)
        se.assert_called_once()

    def test_maybe_auto_resume_hard_error_burns_budget(self):
        from app.core import store
        task = {"id": "t1", "serial": {"chapters": 20}, "workdir": "w"}
        run = {"id": "r1", "task_id": "t1", "status": "failed",
               "error": "模型拒答 refusal", "auto_resumes": 0,
               "auto_wait_resumes": 0}
        with mock.patch.object(store, "get_run", return_value=run), \
             mock.patch.object(store, "get_task", return_value=task), \
             mock.patch.object(store, "task_runs", return_value=[]), \
             mock.patch.object(jobs, "_task_active_run", return_value=None), \
             mock.patch.object(store, "retry_task",
                               return_value=(True, "", {"id": "r2"})), \
             mock.patch.object(store, "update_run") as ur, \
             mock.patch.object(jobs, "_schedule_enqueue"):
            self.assertTrue(jobs._maybe_auto_resume("r1"))
        kwargs = ur.call_args.kwargs
        self.assertEqual(kwargs.get("auto_resumes"), 1)
        self.assertEqual(kwargs.get("auto_wait_resumes"), 0)

    def test_wait_budget_cap_stops(self):
        from app.core import store
        task = {"id": "t1", "serial": {"chapters": 20}, "workdir": "w"}
        run = {"id": "r1", "task_id": "t1", "status": "failed",
               "error": "429 限流", "auto_resumes": 10, "auto_wait_resumes": 10}
        with mock.patch.object(store, "get_run", return_value=run), \
             mock.patch.object(store, "get_task", return_value=task), \
             mock.patch.object(store, "retry_task") as rt, \
             mock.patch.object(store, "update_run"):
            self.assertFalse(jobs._maybe_auto_resume("r1"))
        rt.assert_not_called()

    def test_pool_pressure_counts_active(self):
        from app.core import store
        runs = [{"status": "running"}, {"status": "queued"}, {"status": "done"}]
        with mock.patch.object(store, "list_runs", return_value=runs):
            active, cap = jobs.pool_pressure()
        self.assertEqual(active, 2)
        self.assertEqual(cap, jobs.MAX_POOL)


# ---------------------------------------------------------------- A4 对话摘要

class DirectSummaryTests(unittest.TestCase):
    def test_summarize_direct_stores_and_injects(self):
        from app.core import modelhub, pipeline
        task = {"id": "t9", "engine": "direct", "status": "done",
                "goal": "g", "workdir": "w"}
        runs = [{"id": "r1", "task_id": "t9", "status": "done", "hidden": False,
                 "steps": [{"summary": "第一轮：定了 A 方案"}]},
                {"id": "r2", "task_id": "t9", "status": "done", "hidden": False,
                 "steps": [{"summary": "第二轮：产出 report.md"}]}]
        saved = {}

        def fake_update(task_id, patch):
            self.assertEqual(task_id, "t9")
            saved.update(patch.get("direct_summary") or {})
            return True, ""

        with mock.patch.object(pipeline.store, "get_task", return_value=task), \
             mock.patch.object(pipeline.store, "task_runs", return_value=runs), \
             mock.patch.object(pipeline.store, "update_task_params",
                               side_effect=fake_update), \
             mock.patch.object(pipeline.modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(pipeline.modelhub, "chat",
                               return_value={"ok": True, "text": "- 定了 A 方案\n- 产出 report.md"}):
            summary, err = pipeline.summarize_direct("t9")
        self.assertIsNone(err)
        self.assertIn("A 方案", summary["text"])
        self.assertEqual(summary["runs_covered"], 2)
        self.assertEqual(saved.get("runs_covered"), 2)

    def test_summarize_direct_without_runs(self):
        from app.core import pipeline
        with mock.patch.object(pipeline.store, "get_task",
                               return_value={"id": "t9", "status": "done"}), \
             mock.patch.object(pipeline.store, "task_runs", return_value=[]):
            summary, err = pipeline.summarize_direct("t9")
        self.assertIsNone(summary)
        self.assertIn("还没有", err)

    def test_followup_prompt_carries_summary(self):
        # _run_direct 的续轮注入点以提示词模板函数不可直测，这里验证 store
        # 白名单接受 direct_summary 键（A4 链路的持久化半环）。
        from app.core import store
        task = {"id": "tx", "status": "done", "engine": "direct"}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "_TASKS", {"tx": task}), \
                 mock.patch.object(store, "_save_json") as sj, \
                 mock.patch.object(store, "bump_state"):
                ok, err = store.update_task_params("tx", {
                    "direct_summary": {"text": "摘要", "updated_at": "2026-10-22 12:00:00",
                                       "runs_covered": 3}})
            self.assertTrue(ok)
            self.assertEqual(task["direct_summary"]["text"], "摘要")
            self.assertEqual(task["direct_summary"]["runs_covered"], 3)
            sj.assert_called_once()


# ---------------------------------------------------------------- B9 MCP HTTP

class _McpHTTPHandler(BaseHTTPRequestHandler):
    """最小 Streamable HTTP MCP 服务器桩：initialize/tools/list/tools/call。"""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "stub", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "回声",
                                 "inputSchema": {"type": "object",
                                                 "properties": {"text": {"type": "string"}}}}]}
        elif method == "tools/call":
            text = ((req.get("params") or {}).get("arguments") or {}).get("text", "")
            result = {"content": [{"type": "text", "text": "echo:%s" % text}], "isError": False}
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class McpHttpTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _McpHTTPHandler)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _server(self, **over):
        srv = {"name": "stub", "command": "", "url": "http://127.0.0.1:%d/mcp" % self.port,
               "args": [], "env": {}, "headers": {}, "disabled_tools": []}
        srv.update(over)
        return srv

    def test_parse_servers_accepts_url_entries(self):
        text = json.dumps([self._server()])
        out, err = mcp_client.parse_servers(text)
        self.assertIsNone(err)
        self.assertEqual(out[0]["url"].startswith("http://"), True)

    def test_parse_servers_rejects_url_and_command_both(self):
        text = json.dumps([self._server(command="npx")])
        out, err = mcp_client.parse_servers(text)
        self.assertIsNotNone(err)
        self.assertIn("二选一", err)

    def test_list_tools_and_call_over_http(self):
        res = mcp_client.list_tools(self._server())
        self.assertTrue(res["ok"], res["error"])
        self.assertEqual(res["tools"][0]["name"], "echo")
        called = mcp_client.call_tool(self._server(), "echo", {"text": "hi"},
                                      timeout_s=10)
        self.assertTrue(called["ok"], called["error"])
        self.assertEqual(called["text"], "echo:hi")

    def test_session_header_roundtrip(self):
        seen = {}

        class Handler(_McpHTTPHandler):
            def do_POST(self):
                seen["sid"] = self.headers.get("Mcp-Session-Id")
                _McpHTTPHandler.do_POST(self)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as srv2:
            th2 = threading.Thread(target=srv2.serve_forever, daemon=True)
            th2.start()
            try:
                srv = self._server(url="http://127.0.0.1:%d/mcp" % srv2.server_address[1])
                # initialize 不带 sid；服务器桩不回 Mcp-Session-Id → 后续也不带（空直通）
                res = mcp_client.call_tool(srv, "echo", {"text": "x"}, timeout_s=10)
                self.assertTrue(res["ok"])
                self.assertIsNone(seen.get("sid"))   # 服务器没发会话头，客户端不凭空造
            finally:
                srv2.shutdown()

    def test_http_error_surfaces_readably(self):
        class Handler(_McpHTTPHandler):
            def do_POST(self):
                body = b'{"error":"denied"}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as srv2:
            th2 = threading.Thread(target=srv2.serve_forever, daemon=True)
            th2.start()
            try:
                srv = self._server(url="http://127.0.0.1:%d/mcp" % srv2.server_address[1])
                res = mcp_client.list_tools(srv)
                self.assertFalse(res["ok"])
                self.assertIn("401", res["error"])
            finally:
                srv2.shutdown()


# ---------------------------------------------------------------- B6 market 源/engines/版本

class MarketCustomSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = _redirect(Path(self.tmp.name))

    def tearDown(self):
        _stop(self.patchers)
        self.tmp.cleanup()

    def test_add_list_remove_roundtrip(self):
        with mock.patch.object(market_remote, "assert_public_url",
                               return_value=("example.com", 443)):
            src, err = market_remote.add_custom_source("我的源",
                                                       "https://example.com/marketplace.json")
        self.assertIsNone(err)
        self.assertTrue(src["id"].startswith("custom-"))
        self.assertEqual(len(market_remote.custom_sources()), 1)
        # 重复 URL 拒绝
        with mock.patch.object(market_remote, "assert_public_url",
                               return_value=("example.com", 443)):
            _, err2 = market_remote.add_custom_source("again",
                                                      "https://example.com/marketplace.json")
        self.assertIsNotNone(err2)
        ok, err3 = market_remote.remove_custom_source(src["id"])
        self.assertTrue(ok)
        self.assertIsNone(err3)
        self.assertEqual(market_remote.custom_sources(), [])

    def test_add_rejects_bad_url(self):
        with mock.patch.object(market_remote, "assert_public_url",
                               side_effect=ValueError("仅允许 https 地址")):
            _, err = market_remote.add_custom_source("x", "http://example.com/a.json")
        self.assertIn("https", err)

    def test_all_sources_merges_custom(self):
        with mock.patch.object(market_remote, "assert_public_url",
                               return_value=("example.com", 443)):
            market_remote.add_custom_source("s", "https://example.com/m.json")
        ids = [s["id"] for s in market_remote._all_sources()]
        self.assertTrue(any(i.startswith("custom-") for i in ids))
        self.assertIn("zcode", ids)

    def test_entries_from_cache_includes_custom_and_version(self):
        cache = {"custom-abcd1234": {
            "fetched_at": "2026-10-22 10:00:00", "url": "u",
            "catalog": {"plugins": [{
                "name": "custom-pack", "version": "2.0",
                "source": {"type": "zip", "url": "https://example.com/p.zip"},
                "engines": {"codebee": ">=0.1.1"},
            }]}}}
        fake_sources = [{"id": "custom-abcd1234", "name": "s", "url": "u"}]
        reg = {"remote-custom-abcd1234-custom-pack": {"remote": {"version": "1.0"}}}
        with mock.patch.object(market_remote, "custom_sources",
                               return_value=fake_sources), \
             mock.patch.object(market_remote, "_load_cache", return_value=cache), \
             mock.patch.object(market_remote.market, "installed_ids",
                               return_value={"remote-custom-abcd1234-custom-pack"}), \
             mock.patch.object(market_remote.market, "_load_registry",
                               return_value={"installed": reg}):
            entries = market_remote._entries_from_cache()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["source_id"], "custom-abcd1234")
        self.assertEqual(e["installed_version"], "1.0")
        self.assertTrue(e["update_available"])
        self.assertEqual(e["compat"], "ok")   # engines >=0.1.1 对当前版本放行


class EnginesCompatTests(unittest.TestCase):
    def test_operators(self):
        cur = (0, 1, 119)
        self.assertIsNone(market_remote.engines_compatible({}, current=cur))
        self.assertIsNone(market_remote.engines_compatible(
            {"codebee": ">=0.1.100"}, current=cur))
        self.assertIsNotNone(market_remote.engines_compatible(
            {"codebee": ">=0.2.0"}, current=cur))
        self.assertIsNone(market_remote.engines_compatible(
            {"codebee": "^0.1"}, current=cur))
        self.assertIsNotNone(market_remote.engines_compatible(
            {"codebee": "^1.2"}, current=cur))
        self.assertIsNone(market_remote.engines_compatible(
            {"tutti": "0.1.50"}, current=cur))
        self.assertIsNotNone(market_remote.engines_compatible(
            {"tutti": "~0.2.1"}, current=cur))
        # 不认的键不拦；版本读不到不拦（VERSION 文件缺失场景）
        self.assertIsNone(market_remote.engines_compatible(
            {"claude-code": ">=9.9"}, current=cur))
        with mock.patch.object(market_remote, "_app_version_tuple", return_value=None):
            self.assertIsNone(market_remote.engines_compatible({"codebee": ">=9.9"}))


class ForceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = _redirect(Path(self.tmp.name))

    def tearDown(self):
        _stop(self.patchers)
        self.tmp.cleanup()

    def _files(self, body="v1 内容"):
        return {"market-fx.md": ("---\nname: FX\nsource: market\nmarket_id: fx\n---\n"
                                 + body + "\n")}

    def test_force_write_updates_content_and_record(self):
        res, err = market.install_files("fx", "FX", self._files("v1 内容"))
        self.assertIsNone(err)
        target = Path(res["file"])
        root = paths.DATA_DIR / "skillpacks"
        self.assertIn("v1 内容", (root / target.name).read_text(encoding="utf-8"))
        res2, err2 = market.install_files("fx", "FX", self._files("v2 内容"),
                                          force_write=True)
        self.assertIsNone(err2)
        self.assertEqual(res2["file"], res["file"])   # 沿用原目标名，不出双胞胎
        self.assertIn("v2 内容", (root / target.name).read_text(encoding="utf-8"))
        self.assertTrue(res2["changed"])              # 内容突变对账仍生效
        self.assertTrue(res2["warnings"])             # 突变警示逐条返回

    def test_force_write_never_overwrites_unmarked_user_file(self):
        root = paths.DATA_DIR / "skillpacks"
        root.mkdir(parents=True, exist_ok=True)
        (root / "market-fx.md").write_text("---\nname: 用户自己的\n---\n别动我\n",
                                           encoding="utf-8")
        res, err = market.install_files("fx", "FX", self._files("v1"), force_write=True)
        self.assertIsNone(err)
        # 避让出 market-fx-2.md，用户文件原样
        self.assertEqual(res["file"], "market-fx-2.md")
        self.assertIn("别动我", (root / "market-fx.md").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 前端接线静态检查

class FrontendWiringTests(unittest.TestCase):
    """A1/A4/B6/B7 的前端落点静态验证（app.js/index.html 是浏览器脚本，
    无模块面；按仓库先例（test_ui_native_dialog_ban）做源码级断言）。"""

    ROOT = Path(__file__).resolve().parent.parent

    def test_secret_mask_wired(self):
        js = (self.ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function maskSecretsInDOM", js)
        self.assertIn("maskSecretsInDOM(flow)", js)   # drawChatFlow 内调用
        self.assertIn("sec-mask", (self.ROOT / "app" / "ui" / "style.css")
                      .read_text(encoding="utf-8"))

    def test_summarize_button_and_drafts_panel_wired(self):
        html = (self.ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("rd-chat-summarize", html)
        self.assertIn("skill-drafts", html)
        js = (self.ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("chatSummarizeDirect", js)
        self.assertIn("/api/skillpacks/drafts", js)
        self.assertIn("/api/tasks/", js)
        self.assertIn("direct-summary", js)

    def test_market_custom_source_ui_wired(self):
        html = (self.ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("mkr-add-source", html)
        js = (self.ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/market/remote/sources", js)
        self.assertIn("mkrUpdate", js)


if __name__ == "__main__":
    unittest.main()
