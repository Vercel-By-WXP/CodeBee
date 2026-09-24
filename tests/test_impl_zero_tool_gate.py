# -*- coding: utf-8 -*-
"""默认全权沙箱 + 实现步空转闸 + opencode 接入适配回归。

2026-09-17 mo-so BUG#27596 实测（几件事一起封）：
1. 写模式 codex 原默认 workspace-write（禁网+盘外写），模型偶发误判沙箱受限、
   谎报「进程执行被策略拦截」直接躺平——用户拍板写模式默认 danger-full-access；
   claude 写模式 acceptEdits 在无人值守下 Bash 一律被权限拒，同样默认全权。
2. 同一实现步其实一条命令都没发（事件流 command_execution=0），纯口头汇报却
   exit 0 被当成功往下传——require_tools 闸把零动手判 VENDOR_REFUSAL。
3. opencode 换将必炸两层：同步函数给 @ai-sdk/anthropic 写的 baseURL 缺 /v1
   （网关回 Not Allowed）；runner 传裸模型名而 opencode 只认 provider/model
   全名（UnknownError）——补 /v1 + 裸名加 orch/ 前缀。
所有 CLI 均为打桩假子进程，无真实调用。
"""
from __future__ import annotations

import json
import os

from base import BaseTest


class TestCodexSandboxDefaults(BaseTest):
    def runTest(self):
        from app.core import runner as R
        old = os.environ.pop("TUTTI_CODEX_SANDBOX", None)
        try:
            # 写模式默认全权；只读步永远 read-only（评审者可写会污染 diff 裁决）
            self.assertEqual(R._codex_sandbox(False), "danger-full-access")
            self.assertEqual(R._codex_sandbox(True), "read-only")
            # env 只能钉写模式档位，压不过只读步
            os.environ["TUTTI_CODEX_SANDBOX"] = "workspace-write"
            self.assertEqual(R._codex_sandbox(False), "workspace-write")
            self.assertEqual(R._codex_sandbox(True), "read-only")
            # 新鲜与 resume 两条构建路径都吃同一档位
            agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
            argv, _, _, _ = R._build_call(agent, "codex", "", False, None, "p")
            self.assertIn("-s", argv)
            self.assertEqual(argv[argv.index("-s") + 1], "workspace-write")
            argv2, _, _, _ = R._build_call(agent, "codex", "sid-1", False, None, "p")
            self.assertTrue(any("workspace-write" in a for a in argv2))
            reasoned = dict(agent, reasoning_effort="high")
            argv3, _, _, _ = R._build_call(reasoned, "codex", "", False, None, "p")
            self.assertIn('model_reasoning_effort="high"', argv3)
            # 非法 env 值 → 回落默认全权
            os.environ["TUTTI_CODEX_SANDBOX"] = "bogus"
            self.assertEqual(R._codex_sandbox(False), "danger-full-access")
        finally:
            if old is None:
                os.environ.pop("TUTTI_CODEX_SANDBOX", None)
            else:
                os.environ["TUTTI_CODEX_SANDBOX"] = old


class TestClaudeFullPermsDefault(BaseTest):
    def runTest(self):
        from app.core import runner as R
        old = os.environ.pop("TUTTI_CLAUDE_PERMS", None)
        try:
            agent = {"id": "claude-cli", "kind": "claude", "mode": "real", "command": "claude"}
            argv, _, _, _ = R._build_call(agent, "claude", "", False, None, "p")
            self.assertIn("--dangerously-skip-permissions", argv)
            os.environ["TUTTI_CLAUDE_PERMS"] = "acceptEdits"
            argv2, _, _, _ = R._build_call(agent, "claude", "", False, None, "p")
            self.assertIn("acceptEdits", argv2)
            self.assertNotIn("--dangerously-skip-permissions", argv2)
        finally:
            if old is None:
                os.environ.pop("TUTTI_CLAUDE_PERMS", None)
            else:
                os.environ["TUTTI_CLAUDE_PERMS"] = old


class TestCodexWorkEventCount(BaseTest):
    def runTest(self):
        from app.core import runner as R
        evs = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "汇报"}},
            {"type": "item.completed", "item": {"id": "b", "type": "command_execution",
                                                "command": "dir", "exit_code": 0}},
            {"type": "item.started", "item": {"id": "b2", "type": "command_execution"}},
            {"type": "item.completed", "item": {"id": "c", "type": "file_change", "path": "x"}},
            {"type": "item.completed", "item": {"id": "d", "type": "mcp_tool_call"}},
            {"type": "turn.completed", "usage": {}},
        ]
        # 只统计 item.completed 的动手事件（started 未完成不算）
        self.assertEqual(R._codex_work_events("\n".join(json.dumps(e) for e in evs)), 3)
        self.assertEqual(R._codex_work_events("not json"), 0)


def _stub_stdout(with_work):
    evs = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"id": "a", "type": "agent_message",
                                            "text": "进程执行被策略拦截，本轮未进行任何代码变更。"}},
    ]
    if with_work:
        evs.append({"type": "item.completed",
                    "item": {"id": "b", "type": "command_execution",
                             "command": "dir", "aggregated_output": "x", "exit_code": 0}})
    evs.append({"type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})
    return "\n".join(json.dumps(e) for e in evs)


class TestZeroToolGate(BaseTest):
    def runTest(self):
        from app.core import runner as R
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        orig = R.run_process

        def fake(**kw):
            return {"ok": True, "exit_code": 0, "stdout": _stub_stdout(False),
                    "stderr": "", "duration": 0, "cancelled": False, "timed_out": False}

        R.run_process = fake
        try:
            # 开闸：零动手 + 一套说辞 → VENDOR_REFUSAL，谎报不往下传
            out = R.run_agent(agent, "改代码", readonly=False, timeout=60, require_tools=True)
            self.assertFalse(out["ok"])
            self.assertEqual(out["error_code"], "VENDOR_REFUSAL")
            self.assertIn("零工具调用", out["error"])
            self.assertIn("策略拦截", out["error"])  # 原文尾段保留供人核对
            # 不开闸（对话/写作等纯文本路径）：同样的输出仍是成功
            out2 = R.run_agent(agent, "聊聊", readonly=False, timeout=60)
            self.assertTrue(out2["ok"])

            R.run_process = lambda **kw: dict(fake(), stdout=_stub_stdout(True))
            out3 = R.run_agent(agent, "改代码", readonly=False, timeout=60, require_tools=True)
            self.assertTrue(out3["ok"])
        finally:
            R.run_process = orig


class TestOpencodeModelFullName(BaseTest):
    def runTest(self):
        from app.core import runner as R
        agent = {"id": "opencode", "kind": "opencode", "mode": "real", "command": "opencode"}
        # 绑定里的裸名 → 补 orch/ 前缀（opencode 只认 provider/model 全名）
        argv, _, _, _ = R._build_call(agent, "opencode", "", None, "glm-5.3-flash", "p")
        self.assertEqual(argv[argv.index("--model") + 1], "orch/glm-5.3-flash")
        # 绑定里已是全名 → 原样透传
        argv2, _, _, _ = R._build_call(agent, "opencode", "", None, "myprov/m1", "p")
        self.assertEqual(argv2[argv2.index("--model") + 1], "myprov/m1")


class TestStallWatchdog(BaseTest):
    """停滞看门狗：长静默提前杀（stalled），持续输出的进程活到自然结束。

    起因（2026-09-17）：卡死步骤要耗满 20-40 分钟总超时才换将——用户要求
    「定时检测，挂了立马派下一个」。"""

    def runTest(self):
        import time as _t
        from app.core import runner as R
        py = __import__("sys").executable
        t0 = _t.time()
        res = R.run_process(argv=[py, "-c", "import time; time.sleep(30)"],
                            timeout=60, stall_timeout=2)
        wall = _t.time() - t0
        self.assertTrue(res["stalled"] and res["timed_out"] and not res["ok"])
        self.assertLess(wall, 20)          # 2s 看门狗生效，没耗满 30s sleep
        self.assertIn("输出停滞", res["stderr"])
        # 持续输出（1s 间隔 < 2s 看门狗）→ 不误杀
        res2 = R.run_process(
            argv=[py, "-c",
                  "import time\nfor i in range(3):\n    print('tick', flush=True)\n    time.sleep(1)"],
            timeout=60, stall_timeout=2)
        self.assertTrue(res2["ok"] and not res2["stalled"])
        # 错误归类为 TIMEOUT（瞬态）→ run_agent 链降级 / pipeline 换将能吃到
        self.assertEqual(R._classify_failure(res, kind="codex"), "TIMEOUT")
        self.assertTrue(R._transient_error("输出停滞 600s（stall timed out…）"))


class TestClaudeStreamJson(BaseTest):
    def runTest(self):
        import json as _json
        from app.core import runner as R
        # argv：claude 步骤默认走 stream-json（停滞看门狗 + 实时日志的前提）
        agent = {"id": "claude-code", "kind": "claude", "mode": "real", "command": "claude"}
        argv, _, _, _ = R._build_call(agent, "claude", "", False, None, "p")
        self.assertIn("stream-json", argv)
        self.assertIn("--verbose", argv)
        # 解析器：stream 形态取最后一条 result 行（与旧单 JSON 同构）
        result = {"type": "result", "result": "OK", "is_error": False,
                  "session_id": "s-1", "total_cost_usd": 0.01,
                  "usage": {"input_tokens": 10, "output_tokens": 2,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0}}
        stream = "\n".join([
            _json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}),
            _json.dumps({"type": "assistant", "message": {}}),
            _json.dumps(result),
        ])
        p = R._parse_claude_json(stream)
        self.assertIsNotNone(p)
        self.assertEqual(p["text"], "OK")
        self.assertEqual(p["sid"], "s-1")
        self.assertFalse(p["is_error"])
        # 旧单 JSON 形态继续兼容
        p2 = R._parse_claude_json(_json.dumps(result))
        self.assertEqual(p2["text"], "OK")
