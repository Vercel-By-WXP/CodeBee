# -*- coding: utf-8 -*-
"""对话时间线：失败卡 / 连败合并 / 评审结构卡 / 运行状态行（2026-10-22）。

用户反馈截图里，失败步骤与普通回答同形（只在 meta 末尾多一个「失败」），
同一 CLI 三次同错抄三遍成失败墙，评审裁定是一整段 JSON/摘要，运行中占位
「CLI 已启动…」看起来像答案。落地形态：

- 失败 → 红卡（标题栏放退出码/原因，原始 stderr 与后端诊断分行）
- 连续同文失败 → 一张卡 + 逐次尝试行（单条失败不合并）
- 评审裁定 → 结构卡（均分/维度分数/严重度标签分行）
- 运行中无输出 → 状态行（呼吸点 + 执行中标签），不再占正文形

drawChatFlow 依赖整套 DOM 与全局态，沿用 tests/test_ui_chat_process_gate.py
的做法对 app.js 源码切片做静态断言；CSS/i18n 直接读文件。
"""

import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
APP_JS = UI / "app.js"
CSS = UI / "conversation-reference.css"
I18N = UI / "i18n.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def draw_chat_flow_source():
    src = _read(APP_JS)
    start = src.index("function drawChatFlow(")
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


class ChatFailCardTests(unittest.TestCase):
    def test_single_failure_renders_card(self):
        src = draw_chat_flow_source()
        self.assertIn(
            'if (it.status === "failed" && rawBody) return chatFailCardHTML(it, idx, rawBody);', src)
        app = _read(APP_JS)
        self.assertIn("function chatFailCardHTML(", app)
        # 卡片正文必须转义后端输出（stderr 是不可信外部文本）
        self.assertIn("esc(p.detail)", app)
        self.assertIn("chatFailCodeChip(p.code)", app)

    def test_consecutive_identical_failures_group(self):
        src = draw_chat_flow_source()
        self.assertIn("failMembers.has(idx)", src)
        self.assertIn("chatFailGroupHTML(failGroups.get(idx), idx)", src)
        # 合并门槛是「至少两条」，孤立失败不合并、细节不丢
        self.assertIn("if (j - i >= 2)", src)
        app = _read(APP_JS)
        self.assertIn("function chatFailGroupHTML(", app)
        self.assertIn("同一失败连续 {0} 次", app)

    def test_failure_parts_split_code_stderr_and_diagnosis(self):
        app = _read(APP_JS)
        self.assertIn("function chatFailureParts(", app)
        # 后端失败串形状：退出码 N；stderr/stdout: <尾段>（诊断）
        self.assertIn(r"stderr\/stdout", app)
        self.assertIn(r"^退出码\s*(-?\d+)$", app)
        # 末尾括注只认带冒号的诊断括注，真实 stderr 括注不被误拆
        self.assertIn(r"[^（）()\n]*[：:][^（）()\n]*", app)


class ChatReviewCardTests(unittest.TestCase):
    def test_verdict_uses_structured_card(self):
        src = draw_chat_flow_source()
        self.assertIn(
            'const digest = rawBody && it.status !== "running" ? verdictDigestData(rawBody) : null;', src)
        self.assertIn("if (digest) return chatReviewHTML(it, idx, digest);", src)
        self.assertIn("function chatReviewHTML(", _read(APP_JS))

    def test_review_card_escapes_model_text(self):
        app = _read(APP_JS)
        for needle in ("esc(x[0])", "esc(x[1].toFixed(1))", "esc(note)", "esc(d.concl.slice(0, 240))"):
            self.assertIn(needle, app)

    def test_digest_data_is_shared_with_text_digest(self):
        app = _read(APP_JS)
        self.assertIn("function verdictDigestData(", app)
        # 文本摘要改为消费同一份解析，避免两套口径漂移
        self.assertIn("const d = verdictDigestData(txt);", app)


class RunningStatusLineTests(unittest.TestCase):
    def test_running_placeholder_is_status_line(self):
        src = draw_chat_flow_source()
        self.assertIn("chat-live-status", src)
        self.assertIn('esc(t("执行中"))', src)
        # 旧占位与答案正文同形，必须消失
        self.assertNotIn("chat-cli-wait", src)


class ChatCardStyleTests(unittest.TestCase):
    def test_cards_are_styled_and_reuse_skin_vars(self):
        css = _read(CSS)
        for selector in (".rd-chat .chat-fail-card", ".rd-chat .chat-review-card",
                         ".rd-chat .chat-live-status"):
            self.assertIn(selector + " {", css, "缺少 " + selector + " 规则")
        # 红色只在皮肤 --bad 上掺色，亮暗自动适配；不引独立配色
        self.assertIn("color-mix(in srgb, var(--bad) 4%, var(--panel))", css)
        self.assertIn("color-mix(in srgb, var(--accent) 45%, var(--chat-rule))", css)
        # 日志字色给深色终端底配的浅字，普通面板/卡片上借它＝亮色白底白字
        for borrowed in ("var(--log-text)", "var(--term)", "var(--cb-log-text)"):
            self.assertNotIn(borrowed, css)

    def test_diagnosis_text_readable_not_muted_only(self):
        css = _read(CSS)
        # 诊断是散文，不能只用 --chat-muted（亮色下约 2.9:1）
        self.assertIn("color-mix(in srgb, var(--chat-muted) 40%, var(--chat-text))", css)


class ChatCardI18nTests(unittest.TestCase):
    KEYS = ("评审意见", "均分", "同一失败连续 {0} 次", "第 {0} 次",
            "…其余 {0} 条见该步日志")

    def test_keys_present_and_unique(self):
        src = _read(I18N)
        for key in self.KEYS:
            self.assertEqual(
                src.count('"' + key + '"'), 1,
                "i18n 键「%s」缺失或重复（重复键会静默覆盖主应用译文）" % key)


if __name__ == "__main__":
    unittest.main()
