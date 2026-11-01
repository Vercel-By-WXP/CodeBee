"""对话化①守测：主页「最近动态」流 overlay（chat-stream.js）。

2026-11-01 决策 15 拍板「对话是唯一入口、卡片是唯一表达」后落地的第一刀。
本守测锁三件事：
  1. 接线契约——index.html 必须在 borrow-ui.js 之后 defer 加载 chat-stream.js，
     且带 chat-stream.css（加载顺序即 overlay 依赖契约，动了就静默失效）；
  2. 行为契约——Node 最小假 DOM 里真跑 overlay：runs 渲染成卡片、超量截断、
     空态整块隐藏、定时任务摘要行、点击下钻 homeOpenRun/switchTab、标题转义、
     renderHome 只包一层（幂等，重复 eval 不双包）；
  3. 纪律——不用原生对话框；CSS 不写死色（十六进制只准出现在 var() 回落位）；
     分工纪律——本流不渲染注意力条（那是 borrow-ui 的职责，同一件事不说两遍）。

沿用 tests/test_ui_home_composer_back.py 的做法：源码切片/全文在 Node 里执行。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
INDEX = UI / "index.html"
STREAM_JS = UI / "chat-stream.js"
STREAM_CSS = UI / "chat-stream.css"
I18N = UI / "i18n.js"

HARNESS = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const src = fs.readFileSync(process.argv[1], 'utf8');

// ---- 最小假 DOM -------------------------------------------------------
function makeEl(tag) {
  const hidden = new Set();
  const handlers = {};
  const el = {
    tag, children: [], parent: null, dataset: {}, _html: "", id: "",
    classList: {
      add(c) { hidden.add(c); },
      remove(c) { hidden.delete(c); },
      contains(c) { return hidden.has(c); },
    },
    addEventListener(type, fn) { handlers[type] = fn; },
    __handlers: handlers,
    insertBefore(node, ref) { node.parent = this; this.children.push(node); },
    querySelector() { return null; },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return el._html; },
    set(v) { el._html = String(v); el.children = []; },
  });
  return el;
}

function makeWorld(state) {
  const anchor = makeEl("div");               // #page-home .rd-quick-start
  anchor.className = "rd-quick-start";
  const homeEl = makeEl("section");           // ensureBox 守卫要 anchor.parentElement
  homeEl.children.push(anchor);
  anchor.parentElement = homeEl;
  const created = [];
  const doc = {
    readyState: "complete",
    visibilityState: "visible",
    getElementById(id) { return created.find((e) => e.id === id) || null; },
    createElement(tag) { const e = makeEl(tag); created.push(e); return e; },
    querySelector(sel) { return sel === "#page-home .rd-quick-start" ? anchor : null; },
    addEventListener() {},
  };
  const opened = [];
  let renderHomeCalls = 0;
  const win = {
    t: (s) => s,
    esc: (s) => String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
    relTime: () => "5 分钟前",
    homeOpenRun: (id) => opened.push(["run", id]),
    switchTab: (tab) => opened.push(["tab", tab]),
    authHeaders: () => ({}),
    renderHome: () => { renderHomeCalls++; },   // app.js 的真函数在包之前必须存在
  };
  win.document = doc;
  const fetchCalls = [];
  const fetchStub = async (path) => {
    fetchCalls.push(path);
    return {
      ok: true,
      json: async () => ({ tasks: state.autoTasks || [] }),
    };
  };
  const run = () => new Function("window", "document", "S", "fetch", src)(
    win, doc, { state: state.state || null }, fetchStub);
  return { run, win, doc, created, opened, fetchCalls,
           get renderHomeCalls() { return renderHomeCalls; } };
}

async function settle() { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 5)); }

(async () => {
  const RUNS = [
    { id: "r1", title: "连载《甲》第 58 章", status: "running", step_count: 12,
      current_step: { n: 3 }, created_at: "2026-11-01 10:00:00" },
    { id: "r2", title: "连载《乙》修订", status: "failed", step_count: 5,
      current_step: { n: 5 }, created_at: "2026-11-01 09:00:00" },
    { id: "r3", title: "短篇《丙》", status: "done", step_count: 0, created_at: "2026-11-01 08:00:00" },
  ];
  const AUTO = [
    { name: "《甲》直发", enabled: true, next_run: "11-02 10:10" },
    { name: "已停用的任务", enabled: false, next_run: "11-01 23:00" },
  ];

  // 1) 有 runs + 定时任务：流出现，卡片带标题/状态/步进，定时行只数启用的
  {
    const w = makeWorld({ state: { runs: RUNS }, autoTasks: AUTO });
    w.run();
    assert.equal(typeof w.win.renderHome, "function", "必须包住 renderHome");
    w.win.renderHome();
    await settle();
    const box = w.doc.getElementById("rd-chat-feed");
    assert(box, "必须创建 #rd-chat-feed");
    assert(!box.classList.contains("hidden"), "有动态时流必须可见");
    assert(box.innerHTML.includes("最近动态"), "必须有流头");
    assert(box.innerHTML.includes("连载《甲》第 58 章"), "run 卡片带标题");
    assert(box.innerHTML.includes("失败"), "失败状态进卡片");
    assert(box.innerHTML.includes("3/12"), "步进进度进卡片");
    assert(box.innerHTML.includes("定时任务 1"), "定时行只计启用任务");
    assert(box.innerHTML.includes("11-02 10:10"), "定时行带下一趟时间");
    assert(box.innerHTML.includes("已停用的任务") === false, "停用任务不进摘要行");
    // 点击下钻：run 卡 → homeOpenRun，定时卡 → switchTab
    const click = box.__handlers.click;
    click({ target: { closest: () => ({ dataset: { run: "r2" } }) } });
    click({ target: { closest: () => ({ dataset: { tab: "automation" } }) } });
    assert.deepEqual(w.opened, [["run", "r2"], ["tab", "automation"]], "下钻分发必须正确");
  }

  // 2) 超量截断：6 条 run 只出 4 张卡
  {
    const six = [];
    for (let i = 0; i < 6; i++) six.push({ id: "x" + i, title: "t" + i, status: "done",
      step_count: 0, created_at: "" });
    const w = makeWorld({ state: { runs: six }, autoTasks: [] });
    w.run();
    w.win.renderHome();
    await settle();
    const box = w.doc.getElementById("rd-chat-feed");
    const n = (box.innerHTML.match(/data-run=/g) || []).length;
    assert.equal(n, 4, "流里最多 4 条 run 动态，实际 " + n);
  }

  // 3) 空态整块隐藏（没事不打扰）
  {
    const w = makeWorld({ state: { runs: [] }, autoTasks: [{ name: "停用", enabled: false }] });
    w.run();
    w.win.renderHome();
    await settle();
    const box = w.doc.getElementById("rd-chat-feed");
    assert(box.classList.contains("hidden"), "无动态时整块必须隐藏");
    assert.equal(box.innerHTML, "", "空态不留 DOM 垃圾");
  }

  // 4) 标题转义：<script> 不得原样进 innerHTML
  {
    const w = makeWorld({ state: { runs: [{ id: "r9", title: "<script>x</script>",
      status: "done", step_count: 0, created_at: "" }] }, autoTasks: [] });
    w.run();
    w.win.renderHome();
    await settle();
    const box = w.doc.getElementById("rd-chat-feed");
    assert(!box.innerHTML.includes("<script>"), "标题必须转义");
    assert(box.innerHTML.includes("&lt;script&gt;"), "转义后仍要可见");
  }

  // 5) 包装幂等：同 world 里二次 eval 不双包 renderHome
  {
    const w = makeWorld({ state: { runs: RUNS }, autoTasks: [] });
    w.run();
    const first = w.win.renderHome;
    assert(first.__csWrapped === true, "包装必须带 __csWrapped 标记");
    w.run();
    assert.equal(w.win.renderHome, first, "重复加载不得二次包装");
    w.win.renderHome();
    await settle();
  }

  // 6) 无 S.state（首帧未回包）不炸且保持隐藏
  {
    const w = makeWorld({ state: null, autoTasks: [] });
    w.run();
    w.win.renderHome();
    await settle();
    const box = w.doc.getElementById("rd-chat-feed");
    assert(box.classList.contains("hidden"), "无状态时保持隐藏");
  }

  // 7) fetch 只在缓存过期后发生：连续两次渲染至多一次 /api/automation（TTL 生效）
  {
    const w = makeWorld({ state: { runs: RUNS }, autoTasks: AUTO });
    w.run();
    w.win.renderHome();
    await settle();
    const n1 = w.fetchCalls.length;
    w.win.renderHome();
    await settle();
    assert(w.fetchCalls.length <= n1 + 1, "TTL 内不得重复拉 /api/automation");
  }

  process.stdout.write("chat-stream harness OK\n");
})().catch((e) => { console.error(e); process.exit(1); });
""")


class ChatStreamTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to exercise the chat-stream overlay")

    def test_wiring_in_index_html(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="chat-stream.css">', html)
        self.assertLess(
            html.index('<script defer src="borrow-ui.js"></script>'),
            html.index('<script defer src="chat-stream.js"></script>'),
            "chat-stream.js 必须在 borrow-ui.js 之后加载（overlay 依赖契约）")

    def test_no_native_dialogs(self):
        src = STREAM_JS.read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"\b(alert|confirm|prompt)\s*\(", src),
            "原生对话框全仓禁止（test_ui_native_dialog_ban 同一口径）")

    def test_css_no_hardcoded_colors(self):
        css = STREAM_CSS.read_text(encoding="utf-8")
        # 十六进制只准出现在 var(--token, #fallback) 的回落位
        for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", css):
            head = css[max(0, m.start() - 40):m.start()]
            self.assertIn("var(--", head,
                          "CSS 写死色回归事故来源：十六进制必须放在 var() 回落位")

    def test_attention_not_duplicated_here(self):
        src = STREAM_JS.read_text(encoding="utf-8")
        self.assertNotIn("/api/attention", src,
                         "注意力（需要你处理）归 borrow-ui 注意力条，同一件事不说两遍")

    def test_i18n_labels_registered(self):
        i18n = I18N.read_text(encoding="utf-8")
        for key in ("最近动态", "定时任务", "下次运行 ", "运行中", "已完成",
                    "已取消", "排队中", "超时", "失败", "第 ", " 步"):
            self.assertIn('"%s":' % key, i18n, "缺 EN 词条：%s" % key)

    def test_overlay_behavior(self):
        r = subprocess.run(
            [self.node, "-e", HARNESS, str(STREAM_JS)],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",   # 断言消息含中文，GBK 默认解码会炸读线程
        )
        self.assertEqual(
            r.returncode, 0,
            "node harness 失败：\n" + ((r.stderr or "") + (r.stdout or ""))[-3000:])


if __name__ == "__main__":
    unittest.main()
