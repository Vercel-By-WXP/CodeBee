# -*- coding: utf-8 -*-
"""日志抽屉去噪/翻译单元测试：collapse_dup_lines + pretty_cli_log + read_step_log。

背景（2026-09-15 真实运行暴露）：codex_otel 对网关模型名 [opencode]xxx 的
遥测 tag 校验失败，每个 SSE 事件刷 2 条带微秒时间戳的 WARN，4000 字符尾部
窗口全被占满，智能体真输出被顶出视野。
"""
from __future__ import annotations

from base import BaseTest

_WARN = ("2026-09-15T09:53:04.%06dZ  WARN codex_otel::events::session_telemetry: "
         "metrics counter [codex.sse_event] failed: tag value contains invalid "
         "characters: [opencode]deepseek-v4-flash")


class TestCollapseDupLines(BaseTest):
    def runTest(self):
        from app.core import runner
        self.assertEqual(runner.collapse_dup_lines(""), "")

        # 时间戳不同的「同一行」必须折叠（首行保留原文 + 计数）
        storm = "\n".join(_WARN % i for i in range(30))
        out = runner.collapse_dup_lines(storm)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("2026-09-15T09:53:04.000000Z"))
        self.assertIn("×29", lines[1])
        self.assertIn("已折叠", lines[1])

        # 实景：counter/duration 两种文案交替成对，相邻行永不相等也要折
        _D = _WARN.replace("metrics counter [codex.sse_event]",
                           "metrics duration [codex.sse_event.duration_ms]")
        pair = "\n".join(x for i in range(15) for x in (_WARN % i, _D % i))
        plines = runner.collapse_dup_lines(pair).splitlines()
        self.assertEqual(len(plines), 4)          # 两种各留首行 + 各一条折叠标记
        self.assertIn("metrics counter", plines[0])
        self.assertIn("metrics duration", plines[2])
        self.assertEqual(plines[1], plines[3])    # 均 ×14 已折叠
        self.assertIn("×14", plines[1])

        # 互不相邻（中间隔着足量新行、组已满结算）的重复行不折叠；空行原样保留
        text = "a\nb\nc\nd\ne\nf\ng\na\n\n\nz"
        self.assertEqual(runner.collapse_dup_lines(text), text)

        # 完全相同的相邻行（无时间戳）也折叠
        self.assertEqual(runner.collapse_dup_lines("x\nx\nx"),
                         "x\n⋯（上行重复 ×2 已折叠）")

        # 尾部换行保留
        self.assertTrue(runner.collapse_dup_lines("a\nb\n").endswith("b\n"))


class TestPrettyCliLog(BaseTest):
    def runTest(self):
        from app.core import runner
        raw = "\n".join([
            "===== 下达 2026-09-15 17:50:58 =====",
            "$ cmd /c codex.CMD exec --json",
            "--- 输出 ---",
            _WARN % 1,
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.started","item":{"id":"i0","type":"agent_message"}}',
            '{"type":"item.completed","item":{"id":"i1","type":"reasoning",'
            '"text":"先读一下章节文件"}}',
            '{"type":"item.completed","item":{"id":"i2","type":"command_execution",'
            '"command":"pwsh Get-Content","exit_code":0,"aggregated_output":"第20章"}}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message",'
            '"text":"正文第一段\\n正文第二段"}}',
            '{"type":"item.completed","item":{"id":"i3","type":"error",'
            '"message":"Model metadata not found"}}',
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":5}}',
            '{"type":"broken',               # 坏 JSON 原样保留
            '{"type":"future.unknown":1}',   # 未识别事件原样保留
        ])
        out = runner.pretty_cli_log(raw)
        lines = out.splitlines()

        self.assertIn("===== 下达 2026-09-15 17:50:58 =====", out)   # 审计头不动
        self.assertIn(_WARN % 1, out)                                 # 非 JSONL 行不动
        self.assertNotIn('"type":"turn.started"', out)                # 过程事件删除
        self.assertNotIn("item.started", out)
        self.assertIn("【思考】先读一下章节文件", out)
        self.assertIn("【命令】pwsh Get-Content（退出码 0）", out)
        self.assertIn("  | 第20章", out)                              # 命令输出缩进保留
        self.assertIn("【消息】正文第一段\n正文第二段", out)
        self.assertIn("【错误】Model metadata not found", out)
        self.assertIn("— 一轮完成（tokens 入 100 / 出 5）—", out)
        self.assertIn('{"type":"broken', out)
        # 纯文本日志（claude/generic 等）原样通过
        self.assertEqual(runner.pretty_cli_log("普通输出\n第二行"), "普通输出\n第二行")


class TestReadStepLogPretty(BaseTest):
    def runTest(self):
        from app.core import paths, store
        run_id = "r-test-logpretty"
        rel = "steps/1-draft-codex-cli.log"
        log_dir = paths.RUNS_DIR / run_id / "steps"
        log_dir.mkdir(parents=True)
        body = "\n".join([_WARN % i for i in range(200)] +
                         ['{"type":"item.completed","item":{"id":"i0",'
                          '"type":"agent_message","text":"最终回答"}}'])
        (log_dir / "1-draft-codex-cli.log").write_text(body + "\n", encoding="utf-8")

        # 默认（机器视图）：截断标记 + WARN 墙折叠成 2 行，末尾 JSONL 保留
        raw_view = store.read_step_log(run_id, rel, tail=4000)
        self.assertTrue(raw_view.startswith("...(已截断)..."))
        self.assertEqual(raw_view.count("WARN codex_otel::"), 1)  # 折叠后仅剩首条
        self.assertIn("×", raw_view)                              # 有折叠标记
        self.assertIn('{"type":"item.completed"', raw_view)

        # pretty（抽屉视图）：JSONL 翻译成可读行
        pretty_view = store.read_step_log(run_id, rel, tail=4000, pretty=True)
        self.assertIn("【消息】最终回答", pretty_view)
        self.assertNotIn('{"type"', pretty_view)

        # 路径穿越防护仍在
        self.assertEqual(store.read_step_log(run_id, "../secret.txt"), "")
