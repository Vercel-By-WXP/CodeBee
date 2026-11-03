# -*- coding: utf-8 -*-
"""对话条上方「本会话用量」一行 + 明细浮层（2026-11-03 用户截图，箭头指向那行小字）。

截图里的东西是两件：输入框上方一行
    「共 521.3K Token · 49.88 · DeepSeek-V4.1-Flash × 3 ⧉」
点它弹出「本会话用量明细」：总计 + 一张按模型列的表（模型 / 调用 / Token / 费用）。

这一层是纯覆盖层（session-usage.{js,css}）：删掉 index.html 一行 link + 一行
script 即整体回滚。守测盯住六件事（都是线上真会出事的点）：

  1. 挂载位置：必须是 .rd-chat-bar 的**前一个兄弟**，不能塞进 bar 内部——
     塞进去就跟着 bar 的 max-width / margin 一起漂，还会被 bar 的 overflow 裁；
  2. 定高 flex 列：.rd-chat 是 `flex-direction: column`，流吃 flex:1，本行必须
     `flex: 0 0 auto`，否则被压成 0 高（composer 被压到 22px 的同款血账）；
  3. 左沿对齐靠实测：工作台布局是 `margin: 8px 26px`、base 布局是
     `calc(100% - 48px)` 居中——写死任何一套都会在另一套里错位，只许量
     `.rd-chat-bar` 的 getBoundingClientRect 再补 padding；
  4. 没数据就整条不出现（DESIGN.md §0），不许把「0 次调用」显示成「花了 ¥0」；
  5. 未标价不许折成 ¥0：费用列写「—」并在底下说明，金额符号是 ¥（台账语义，
     core/usage.py 的 cost_usd 列名沿用历史）；
  6. 交互三件套（点开 / Esc 关 / 外点关）+ 文案全部走 t()（i18n 双份）。

另附一条接线检查：index.html 必须在 app.js **之后**引它（要复用 api / fmtTok / toast）。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _rule(css, selector_pattern):
    hits = re.findall(r"(?m)^\s*%s\s*\{([^}]*)\}" % selector_pattern, css)
    return hits[-1] if hits else None


class TestSessionUsageWiring(unittest.TestCase):

    def test_overlay_files_are_wired_after_app_js(self):
        html = _read("index.html")
        self.assertIn('href="session-usage.css', html,
                      "session-usage.css 未接入 index.html —— 汇总行会退化成裸按钮")
        self.assertIn('src="session-usage.js', html,
                      "session-usage.js 未接入 index.html")
        self.assertGreater(html.index('src="session-usage.js'),
                           html.index('src="app.js"'),
                           "session-usage.js 必须在 app.js 之后：要用 api() / fmtTok() / toast()")

    def test_route_is_registered(self):
        main = open(os.path.join(ROOT, "app", "main.py"), "r", encoding="utf-8").read()
        self.assertIn('path == "/api/usage/session"', main,
                      "/api/usage/session 路由缺失：前端会一直拿不到数据")
        self.assertIn("usage.session_usage(", main)


class TestSessionUsageMount(unittest.TestCase):

    def setUp(self):
        self.js = _read("session-usage.js")
        self.css = _read("session-usage.css")

    def test_inserts_before_chat_bar_not_inside(self):
        self.assertIn("box.insertBefore(wrap, bar)", self.js,
                      "汇总行必须是 .rd-chat-bar 的前一个兄弟（贴输入卡上方）")
        self.assertIn('box.querySelector(".rd-chat-bar")', self.js)
        # 不许出现「appendChild 到 bar 里」的写法
        self.assertNotIn("bar.appendChild(wrap)", self.js)

    def test_row_is_not_shrinkable_in_flex_column(self):
        body = _rule(self.css, r"#rd-chat > \.ssx-wrap")
        self.assertIsNotNone(body, "#rd-chat > .ssx-wrap 规则缺失")
        self.assertIn("flex: 0 0 auto", body,
                      ".rd-chat 是定高 flex 列，本行必须 flex:0 0 auto，"
                      "否则会被内容流挤成 0 高")
        self.assertIn("box-sizing: border-box", body)

    def test_left_edge_is_measured_not_hardcoded(self):
        self.assertIn("getBoundingClientRect", self.js)
        self.assertIn("wrapper" if False else "alignBar", self.js)
        # 宽度不许写死 calc(100% - Npx)：两套布局的输入卡内缩不同
        self.assertNotIn("width: calc(100% - 24px)", self.css)
        self.assertNotIn("margin: 0 auto", self.css)

    def test_mount_retries_then_stops(self):
        # 400ms 一次 × 最多 40 次：DOM 没就绪时重试，但绝不无限转
        self.assertIn("tries < 40", self.js)


class TestSessionUsageHonesty(unittest.TestCase):

    def setUp(self):
        self.js = _read("session-usage.js")
        self.css = _read("session-usage.css")

    def test_empty_session_hides_the_whole_row(self):
        body = _rule(self.css, r"#rd-chat > \.ssx-wrap\.is-empty")
        self.assertIsNotNone(body, "缺少 is-empty 规则")
        self.assertIn("display: none", body,
                      "没有用量时必须整条隐藏——显示 0 会被读成「花了 0 元」")
        self.assertIn("calls <= 0", self.js)
        self.assertIn('classList.add("is-empty")', self.js)

    def test_money_uses_yen_and_marks_unpriced(self):
        self.assertIn("¥", self.js, "台账金额语义是 ¥（core/usage.py），不许写成 $")
        self.assertNotIn('"$" +', self.js)
        self.assertIn("未计价", self.js,
                      "未折出费用时要明说「未计价」，不能显示 ¥0")
        self.assertIn('money(row.cost) || "—"', self.js,
                      "按模型明细里未标价行必须写「—」")
        self.assertIn("unpriced", self.js)

    def test_counts_come_from_the_ledger_api(self):
        self.assertIn("/api/usage/session?run=", self.js)
        # 不许读 run.json 的 tokens 副本（回填过的老运行两边对不上）
        self.assertNotIn("lastRun.tokens", self.js)
        self.assertNotIn("run.cost_usd", self.js)


class TestSessionUsageInteraction(unittest.TestCase):

    def setUp(self):
        self.js = _read("session-usage.js")

    def test_escape_and_outside_click_close(self):
        self.assertIn('e.key !== "Escape"', self.js)
        self.assertIn("setOpen(false)", self.js)
        self.assertIn("wrap.contains(e.target)", self.js,
                      "外点关闭必须先排除自身容器，否则点浮层内部也会关")

    def test_copy_button_stops_propagation(self):
        self.assertIn("e.stopPropagation()", self.js,
                      "复制按钮不能冒泡成「开关浮层」")

    def test_run_switch_discards_stale_data(self):
        self.assertIn("if (runId !== rid) return;", self.js,
                      "会话切走后回来的旧数据必须丢弃，否则数字串到新一轮")

    def test_does_not_poll_when_chat_pane_hidden(self):
        self.assertIn('box.classList.contains("hidden")', self.js)

    def test_new_copy_goes_through_i18n(self):
        i18n = _read("i18n.js")
        for key in ("本会话用量明细", "用量统计随会话动态更新。", "未计价",
                    "复制会话用量", "已复制会话用量", "点击查看本会话用量明细"):
            self.assertIn('"%s"' % key, i18n,
                          "新文案「%s」缺 i18n 词条（DESIGN.md §7：中英双份）" % key)


if __name__ == "__main__":
    unittest.main()
