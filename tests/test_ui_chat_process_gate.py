"""对话气泡的执行过程只在执行中展示（2026-10-22 用户拍板）。

drawChatFlow 组装气泡时，思考过程/ACP 活动/工具活动（执行过程）/完整 CLI
日志四块全部收进 `it.status === "running"` 条件；终态气泡只留结果正文，
收尾另有「执行结果」卡。运行日志轮询同样只服务运行中的步骤。

drawChatFlow 依赖整套 DOM 与全局态，无法单独 require——沿用
tests/test_ui_side_arch_toggle.py 的做法，对 app.js 源码切片做静态断言，
锁住「终态不再渲染执行过程」这条产品决定。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
APP_JS = UI / "app.js"


def draw_chat_flow_source() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("function drawChatFlow(")
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


class ChatProcessGateTests(unittest.TestCase):
    def setUp(self):
        self.src = draw_chat_flow_source()

    def test_think_acp_execution_only_while_running(self):
        # 旧的无条件组装必须消失（\n 锚定行首，六空格 = 气泡 return 模板顶层）
        self.assertNotIn(
            "\n      chatThinkHTML(it) +\n      chatAcpEventsHTML(it) +\n"
            "      chatExecutionHTML(it, idx, latestLogIndex) +\n", self.src)
        gated = re.search(
            r'\(it\.status === "running" \?\s*'
            r'chatThinkHTML\(it\) \+\s*'
            r'chatAcpEventsHTML\(it\) \+\s*'
            r'chatExecutionHTML\(it, idx, latestLogIndex\) : ""\)', self.src)
        self.assertIsNotNone(gated, "思考/ACP/执行过程三块必须包在 running 条件里")

    def test_cli_log_only_while_running(self):
        self.assertNotIn("\n      chatLogHTML(it, idx, latestLogIndex) +\n", self.src)
        self.assertIn(
            '(it.status === "running" ? chatLogHTML(it, idx, latestLogIndex) : "")',
            self.src)

    def test_log_poll_only_running_steps(self):
        m = re.search(r"items\.forEach\(\(it\) => \{[^}]*?chatLoadLog\(it\.run, it\.log\);\s*\}\);",
                      self.src, re.S)
        self.assertIsNotNone(m, "日志轮询循环结构变了，请同步更新本测试")
        self.assertIn('it.status !== "running"', m.group(0))

    def test_bubble_still_renders_body_and_result(self):
        # 隐藏的是过程，不是结果：正文与收尾「执行结果」卡必须保留
        self.assertIn('\'<div class="chat-body">\' + bodyHtml + "</div>" +', self.src)
        self.assertIn("chatResultHTML(run, res)", self.src)


if __name__ == "__main__":
    unittest.main()
