# -*- coding: utf-8 -*-
"""MCP Harness POST 路由与握手回归 + direct 引擎 verdict 形状守卫。

2026-10-21 冒烟发现：/mcp/orchestration 只注册在 _route_get，POST（MCP 规范的
主通道）整体落 404 "unknown api"，tools/call 完全不可用；且 initialize 握手缺失，
标准客户端首连即失败。本文件把修复钉死：POST 必须在站点 auth 之前分流，
initialize/notifications/initialized 必须可用。

direct 引擎没有评审流程，verdict 曾硬编码 "pass": True，技能台账把每个直连
run 清算成 won（把「模型答了」当「任务过了」）。AST 守卫保证 _run_direct 的
verdict 永远不带 pass 键，_run_outcome 对其返回 None（不归因）。
"""

import ast
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from app.core import skills

APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
import main
from core import mcp_harness

PIPELINE_PATH = Path(__file__).resolve().parents[1] / "app" / "core" / "pipeline.py"


def _post(url, payload, token=None, raw=False):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


class McpBridgeRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.token = None
        cls.httpd = main.ThreadedServer(("127.0.0.1", 0), main.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        # token 存储隔离到临时目录，绝不读写真实数据目录
        store_path = Path(cls.tmp.name) / "mcp_tokens.json"
        patcher = mock.patch.object(mcp_harness, "_TOKEN_STORE_PATH", store_path)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.token = mcp_harness.create_token("route-test")
        # addClassCleanup 是 LIFO：执行顺序 = shutdown → server_close → join
        cls.httpd.daemon_threads = True
        cls.addClassCleanup(cls.thread.join)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def test_post_without_token_is_401_not_404(self):
        # 修复前：POST 落 _route_post fallback → 404 "unknown api"
        code, body = _post(self.url("/mcp/orchestration"), {"jsonrpc": "2.0", "id": 1})
        self.assertEqual(code, 401)
        self.assertIn("Authorization", body.get("error", ""))

    def test_post_with_bad_token_is_401(self):
        code, _ = _post(self.url("/mcp/orchestration"), {}, token="orch_bad")
        self.assertEqual(code, 401)

    def test_initialize_handshake(self):
        code, body = _post(self.url("/mcp/orchestration"), {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "regress"}}}, token=self.token)
        self.assertEqual(code, 200)
        result = body["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(body["id"], 1)

    def test_notifications_initialized_is_202(self):
        code, _ = _post(self.url("/mcp/orchestration"), {
            "jsonrpc": "2.0", "method": "notifications/initialized"}, token=self.token)
        self.assertEqual(code, 202)

    def test_tools_list_via_post(self):
        code, body = _post(self.url("/mcp/orchestration"), {
            "jsonrpc": "2.0", "id": 7, "method": "tools/list"}, token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(body["id"], 7)
        self.assertIsInstance(body["result"]["tools"], list)
        self.assertTrue(body["result"]["tools"])

    def test_invalid_json_is_400_not_500(self):
        # 修复前：self._read_body() 未定义 → AttributeError 500
        code, _ = _post(self.url("/mcp/orchestration"), b"not json", token=self.token)
        self.assertEqual(code, 400)

    def test_unknown_method_is_400(self):
        code, _ = _post(self.url("/mcp/orchestration"), {
            "jsonrpc": "2.0", "id": 9, "method": "bogus"}, token=self.token)
        self.assertEqual(code, 400)


class DirectVerdictShapeGuard(unittest.TestCase):
    """_run_direct 的 verdict 不得携带 pass 键（direct 无评审，判不出就不归因）。"""

    def test_run_direct_verdict_has_no_pass_key(self):
        tree = ast.parse(PIPELINE_PATH.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_run_direct")
        offenders = []
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "verdict" for t in node.targets):
                continue
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            if "pass" in keys:
                offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         "_run_direct 的 verdict 重新带上了 pass 键（行 %s）："
                         "direct 无评审流程，硬编码 pass 会把直连 run 全部清算成 won" % offenders)

    def test_outcome_for_direct_shape_is_none(self):
        run = {"status": "done", "steps": [{"status": "done", "agent": "x"}],
               "verdict": {"type": "chat", "engine": "direct", "direct": True, "turns": 2}}
        self.assertIsNone(skills._run_outcome(run))
        # 真评审结论仍正常归因，防止守卫误伤
        self.assertIsNone(skills._run_outcome(dict(run, verdict={"engine": "review"})))


if __name__ == "__main__":
    unittest.main()
