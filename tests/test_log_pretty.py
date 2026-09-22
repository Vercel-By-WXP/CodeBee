# -*- coding: utf-8 -*-
"""日志抽屉去噪/翻译单元测试：collapse_dup_lines + pretty_cli_log + read_step_log。

背景（2026-09-15 真实运行暴露）：codex_otel 对网关模型名 [opencode]xxx 的
遥测 tag 校验失败，每个 SSE 事件刷 2 条带微秒时间戳的 WARN，4000 字符尾部
窗口全被占满，智能体真输出被顶出视野。
"""
from __future__ import annotations

import json

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

        # claude stream-json：思考心跳邻近组折叠成一行，其余事件翻译
        # （2026-09-22 真实案：3.2MB 日志 15828 条心跳刷屏）
        claude_raw = "\n".join(
            ['{"type":"system","subtype":"init","cwd":"E:\\\\x","session_id":"s1",'
             '"model":"glm-5.3-flash","tools":["Bash"]}']
            + [json.dumps({"type": "system", "subtype": "thinking_tokens",
                           "estimated_tokens": 2300 + i, "estimated_tokens_delta": 2,
                           "session_id": "s1", "uuid": "u%d" % i})
               for i in range(50)]
            + ['{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,'
               '"retry_delay_ms":550,"error":"unknown","session_id":"s1"}',
               '{"type":"assistant","message":{"content":[{"type":"thinking",'
               '"thinking":"先想一下结构"},{"type":"text","text":"正文第一段"}]}}',
               '{"type":"user","message":{"content":[{"type":"tool_result",'
               '"content":"ok"}]}}',
               '{"type":"result","subtype":"success","is_error":false,'
               '"result":"正文第一段","total_cost_usd":0.6089,'
               '"usage":{"input_tokens":41751,"output_tokens":16000}}'])
        c_out = runner.pretty_cli_log(claude_raw)
        self.assertIn("— 会话启动（model=glm-5.3-flash）—", c_out)
        self.assertIn("— 思考中（心跳 ×50 已折叠，估算 ~2349 tokens）—", c_out)
        self.assertNotIn("thinking_tokens", c_out)                 # 心跳原行不再出现
        self.assertIn("— API 重试 1/10（等 550ms）：unknown —", c_out)
        self.assertIn("【思考】先想一下结构", c_out)
        self.assertIn("【消息】正文第一段", c_out)
        self.assertEqual(c_out.count("【消息】"), 1)                # result 不重复正文
        self.assertNotIn('"type":"user"', c_out)                   # 工具回包噪音删除
        self.assertIn("— 完成（tokens 入 41751 / 出 16000，$0.61）—", c_out)
        seq = [c_out.index(x) for x in ("— 会话启动", "— 思考中", "— API 重试",
                                        "【思考】", "【消息】", "— 完成")]
        self.assertEqual(seq, sorted(seq))                          # 时序不乱

        # 旧单 JSON 模式（无 assistant 事件）：正文从 result 补上，不丢答案
        old_raw = json.dumps({"type": "result", "is_error": False,
                              "result": "旧模式正文", "total_cost_usd": 0.5,
                              "usage": {"input_tokens": 10, "output_tokens": 20}})
        self.assertIn("【消息】旧模式正文", runner.pretty_cli_log(old_raw))


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


class TestMixedEncodingDecode(BaseTest):
    """混合编码日志：我方 UTF-8 审计头 + CLI 自家 GBK 输出同处一文件。

    2026-09-20 实案：整块 UTF-8 解码失败 → 整块 GBK 也失败（头的 UTF-8 字节
    在 GBK 里非法）→ 落到 replace 兜底，整篇铺成 U+FFFD 墙，详情页卡片显示成
    一片 ◆；另一种落法是整块回退 GBK，把头部中文全变乱码。逐行解码两边都保住。
    """

    def runTest(self):
        from app.core import runner
        head = "===== 下达 =====\n指令：不要复述正文。\n--- 输出 ---\n".encode("utf-8")
        body = ("Can't initialize prompt toolkit\r\n"
                + "─" * 78 + "\r\n"
                + "litellm.BadRequestError: LLM Provider NOT provided\r\n").encode("gbk")
        raw = head + body

        out = runner.decode_output(raw)
        self.assertNotIn("\ufffd", out)          # 不再有乱码墙
        self.assertIn("不要复述正文", out)         # UTF-8 头部保住
        self.assertIn("─" * 78, out)             # GBK 正文也解出来了
        self.assertIn("LLM Provider NOT provided", out)

        # 成串图元在清洗阶段折叠（保留行首 3 个做视觉提示），正文照旧
        clean = runner.clean_cli_text(out)
        self.assertNotIn("─" * 8, clean)
        self.assertIn("───…", clean)
        self.assertIn("不要复述正文", clean)
        self.assertIn("LLM Provider NOT provided", clean)


class TestCleanCliText(BaseTest):
    """终端噪声清洗：ANSI 转义 / 裸 \\r 覆写 / 控制字符 / U+FFFD 乱码墙。"""

    def runTest(self):
        from app.core import runner

        # ANSI 颜色/加粗：清洗后 E 不可见只剩 `[91m[1mError:` 的老毛病
        ansi = "\x1b[91m\x1b[1mError: \x1b[0m{\"name\": \"UnknownError\"}"
        self.assertEqual(runner.strip_ansi(ansi), 'Error: {"name": "UnknownError"}')
        self.assertEqual(runner.clean_cli_text(ansi), 'Error: {"name": "UnknownError"}')
        # 行首被截断的裸残尾（切片割断 ESC 后留下的 `[91m`）也要止血
        self.assertEqual(runner.strip_ansi("[91m[0mError: x"), "Error: x")
        # 正文里合法的 [1m] 引用不动（只清行首真残尾）
        self.assertEqual(runner.strip_ansi("见 [1m] 注"), "见 [1m] 注")
        # OSC（窗口标题）也要剥掉
        self.assertEqual(runner.strip_ansi("\x1b]0;title\x07ok"), "ok")

        # \r 覆写：进度条/旋转动画只留该行最终形态；\r\n 是行结束符，不能当覆写
        self.assertEqual(runner.clean_cli_text("10%\r55%\r100%\n"), "100%\n")
        out = runner.clean_cli_text("Are you running \r\ncmd.exe?\r\n")
        self.assertEqual(out, "Are you running \ncmd.exe?\n")

        # 乱码墙给一句人话，控制字符清掉
        self.assertIn("无法解码", runner.clean_cli_text("\ufffd" * 40))
        self.assertEqual(runner.clean_cli_text("a\x00b\x07c"), "abc")

        # 幂等：清洗过的文本再洗不变
        once = runner.clean_cli_text("\x1b[33m" + "█" * 40 + "\ufffd" * 20 + "\r\nok")
        self.assertEqual(runner.clean_cli_text(once), once)


class TestStepSummarySanitized(BaseTest):
    """落盘/读盘两侧都要挡：ANSI 与乱码墙不能进摘要（详情页卡片直读它）。"""

    def runTest(self):
        from app.core import paths, store
        run = store.create_run("serial", "t")
        store.add_step(run["id"], "critique", "aider", "通用评审")
        store.finish_step(run["id"], 1, "failed",
                          summary="退出码 1；stderr/stdout: \x1b[91mError: \x1b[0m" + "█" * 40)
        s = store.get_run(run["id"])["steps"][0]
        self.assertNotIn("\x1b", s["summary"])
        self.assertNotIn("█" * 8, s["summary"])
        self.assertIn("Error:", s["summary"])

        # 存量数据（旧版本写进 run.json 的）在 load_all 读盘时洗一遍
        rp = paths.RUNS_DIR / "r-legacy-1" / "run.json"
        rp.parent.mkdir(parents=True)
        rp.write_text(json.dumps({
            "id": "r-legacy-1", "task_id": "t", "kind": "serial", "status": "failed",
            "error": "\x1b[91mboom\x1b[0m" + "\ufffd" * 30,
            "steps": [{"n": 1, "role": "draft", "status": "failed",
                       "summary": "\x1b[1m" + "─" * 60, "output": "\x1b[32m正文\x1b[0m"},
                      "字符串步骤也要能过（不崩）"],
        }, ensure_ascii=False), encoding="utf-8")
        store.load_all()
        legacy = store.get_run("r-legacy-1")
        self.assertEqual(legacy["error"], "boom" + "…（30 个字符无法解码：CLI 输出不是 UTF-8/GBK）")
        st = legacy["steps"][0]
        self.assertNotIn("\x1b", st["summary"])
        self.assertNotIn("─" * 8, st["summary"])
        # output 是智能体正文：只剥 ANSI，不做噪声折叠
        self.assertEqual(st["output"], "正文")
