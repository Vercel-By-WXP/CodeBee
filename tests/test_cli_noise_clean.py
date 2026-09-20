# -*- coding: utf-8 -*-
"""CLI 终端噪声清洗端到端（不碰真实 CLI）：假 CLI 吐 ANSI/\\r/画线框/GBK/坏字节，
断言它们进不了步骤摘要与错误串，而原始字节仍留在日志文件里备查。

背景（2026-09-20 用户实报「咋还有乱码」）：详情页蜂巢卡片的结论行与失败摘要
直读 CLI 尾巴，aider 在中文 Windows 上吐 GBK + 画线框、opencode 吐 ANSI 颜色，
于是卡片显示成一片 ◆ / ─，错误串露出 `[91m[1mError:`。三道防线各管一段：
decode_output（混合编码逐行解码）→ clean_cli_text（洗噪声）→ 落盘前再洗一遍。
"""
from __future__ import annotations

import json
import os
import sys

from base import BaseTest

NOISY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_noisy_cli.py")


def _agent(code=0):
    """假 CLI 的启动项；code 非 0 时夹具按失败收尾（走错误摘要那条路径）。"""
    tmpl = [NOISY] + ([str(code)] if code else []) + ["{prompt}"]
    return {"id": "noisy", "kind": "generic", "command": sys.executable,
            "argv_template": tmpl, "model": None, "mode": "real",
            "env": {"TUTTI_TEST_SELFCONFIG": "1"}}   # 自带配置：过死链硬失败闸门


class TestCliNoiseClean(BaseTest):
    def runTest(self):
        from app.core import runner
        log_path = self.tmp / "noisy.log"

        # 成功步（退出码 0）：text 是给摘要/评审解析吃的，必须干净
        out = runner.run_agent(_agent(), "评审这一章", workdir=self.workdir,
                               log_path=str(log_path))
        self.assertTrue(out["ok"], out.get("error"))
        text = out["text"]
        self.assertNotIn("\x1b", text)                 # ANSI 颜色没了
        self.assertNotIn("\ufffd", text)               # 乱码墙没了
        self.assertNotIn("─" * 8, text)                # 画线框折叠
        self.assertNotIn("Loading 10%", text)          # \r 覆写只留最终形态
        self.assertIn("Done.", text)
        self.assertIn("Error: 模型不可用", text)        # ANSI 剥掉后正文完整
        self.assertIn("LLM Provider NOT provided", text)   # GBK 正文解出来了
        self.assertIn("最终结论：本章节奏偏慢。", text)      # GBK 中文不受损

        # 原始字节仍在日志文件里（清洗只发生在消费侧，取证不受影响）
        raw = log_path.read_bytes()
        self.assertIn(b"\x1b[91m", raw)
        self.assertIn(b"\xff", raw)

    def test_failure_summary_clean(self):
        """失败步：错误串是 UI 直接展示的（徽章 title/蜂巢结论），同样不能带噪声。"""
        from app.core import runner
        out = runner.run_agent(_agent(3), "评审这一章", workdir=self.workdir)
        self.assertFalse(out["ok"])
        err = out["error"]
        self.assertNotIn("\x1b", err)
        self.assertNotIn("\ufffd", err)
        self.assertNotIn("─" * 8, err)
        self.assertIn("Error: 模型不可用", err)
        self.assertIn("stderr: 退出码", err)


class TestNoisyLogEndpoint(BaseTest):
    """日志抽屉/蜂巢实时尾巴走 store.read_step_log，同一份噪声也要洗干净。"""

    def runTest(self):
        from app.core import paths, store
        run_id = "r-noisy-1"
        rel = "steps/01-critique-aider.log"
        d = paths.RUNS_DIR / run_id / "steps"
        d.mkdir(parents=True)
        head = "===== 下达 =====\n--- 输出 ---\n".encode("utf-8")
        body = (("\x1b[91mError: boom\x1b[0m\r\n" + "─" * 80 + "\r\n")
                .encode("gbk") + b"\xff" * 20 + b"\r\n" + "结论：节奏偏慢\r\n".encode("gbk"))
        (d / "01-critique-aider.log").write_bytes(head + body)

        view = store.read_step_log(run_id, rel, tail=4000, pretty=True)
        self.assertNotIn("\x1b", view)
        self.assertNotIn("\ufffd", view)
        self.assertNotIn("─" * 8, view)
        self.assertIn("Error: boom", view)
        self.assertIn("无法解码", view)                 # 坏字节给了人话
        self.assertIn("结论：节奏偏慢", view)             # UTF-8 头 + GBK 正文都保住
        self.assertIn("===== 下达 =====", view)
        # 日志抽屉读到的 JSON 一定可序列化（U+FFFD/控制字符曾把 SSE 推送搞崩）
        json.dumps({"log": view})
