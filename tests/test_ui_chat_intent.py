"""对话化②守测：意图解析 + 计划确认卡（chat-intent.js）。

2026-11-01 C0 阶段②落地（决策 15）。本守测锁四件事：
  1. 接线契约——index.html 必须在 chat-stream.js 之后 defer 加载
     chat-intent.js，且带 chat-intent.css；
  2. 解析器 fixture（PARSER 切片，纯函数真跑）——「发 5 章」解析出平台/
     章数/模式；「第 N 章」= 指定章号；中文数字；定时/草稿模式；查询/暂停/
     恢复/重试；**否定集**：快速开始模板句、修复失败任务、周报等一律不拦
     （宁漏勿误——漏判照旧建任务无害，误判会抢用户的提交）；
  3. 行为闸门（微 DOM 真跑 overlay）——**确认前零请求零 run 创建**；取消
     零动作；确认才 fetch（publish-all 载荷正确：平台/章号/直发）；查询直出
     答案卡；非意图句照旧走 createTask；手机壳激活不拦；定时出引导卡不出
     请求；非失败态重试确认后如实报错零请求；
  4. 纪律——不用原生对话框；CSS 十六进制只准在 var() 回落位。

沿用 tests/test_ui_home_composer_back.py 的 Node 切片做法。
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
INTENT_JS = UI / "chat-intent.js"
INTENT_CSS = UI / "chat-intent.css"
I18N = UI / "i18n.js"

PARSER_HARNESS = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const a = src.indexOf('/* == PARSER-BEGIN ==');
const b = src.indexOf('/* == PARSER-END == */');
assert(a >= 0 && b > a, 'PARSER 标记必须成对');
const { ciParseIntent } = new Function(src.slice(a, b) +
  '; return { ciParseIntent };')();

const W = "E:/w1";
const P = (text, ctx) => ciParseIntent(text, Object.assign({ workdir: W }, ctx || {}));

// —— 发布：fixture 主句（验收口径：解析出平台/章数/队列） ——
let it = P("把《头七结账人》最新5章发到番茄");
assert.equal(it.kind, "publish");
assert.equal(it.book, "头七结账人");
assert.equal(it.count, 5);
assert.equal(it.platform, "fanqie");
assert.equal(it.mode, "direct");

// 无书名：落在当前工作目录的语境里
it = P("发5章");
assert.equal(it.kind, "publish");
assert.equal(it.book, "");
assert.equal(it.count, 5);
assert.equal(it.workdir, W);

// 「第 N 章」= 指定章号（不能被当成数量 58）
it = P("把《甲》第58章发到七猫");
assert.equal(it.platform, "qimao");
assert.deepEqual(it.chapters, [58]);
assert.equal(it.count, 0);

// 中文数字 + 草稿模式
it = P("把《甲》最新三章存草稿");
assert.equal(it.mode, "draft");
assert.equal(it.count, 3);

// 定时：识别但 mode=schedule（执行层出引导卡不出请求）
it = P("明早10点把《甲》发5章");
assert.equal(it.mode, "schedule");

// 「第 N 章」优先于数量词（「第58章」里的 58 不许被读成 58 章）
it = P("把《甲》第58章发到番茄");
assert.deepEqual(it.chapters, [58]);

// —— 定时的时间解析 ——
it = P("明早10点把《甲》发5章");
assert.equal(it.mode, "schedule");
assert.equal(it.time.hh, 10);
assert.equal(it.time.mm, 0);
assert.equal(it.time.def, false);
it = P("把《甲》晚上8点半发到番茄");
assert.equal(it.time.hh, 20);
assert.equal(it.time.mm, 30);
it = P("10:20 把《甲》定时发一下");
assert.equal(it.time.hh, 10);
assert.equal(it.time.mm, 20);
it = P("把《甲》发5章");
assert.equal(it.mode, "direct");
assert.equal(P("把《甲》安排一下"), null, "「安排一下」不得误中「排一下」（交 triage 兜底）");
it = P("把《甲》定时发一下");
assert.equal(it.time.def, true, "没说时间用默认档并如实标注");
assert.equal(it.time.hh, 10);
it = P("把《甲》排到明早8点发到番茄");
assert.equal(it.mode, "schedule");
assert.equal(it.time.hh, 8);

// —— 控制类 ——
assert.equal(P("暂停《甲》").kind, "pause");
assert.equal(P("恢复《甲》").kind, "resume");
assert.equal(P("重试《甲》").kind, "retry");
assert.equal(P("重试失败的《甲》").kind, "retry");

// —— 取消定时 ——
it = P("把《甲》的定时取消");
assert.equal(it.kind, "unschedule");
it = P("《甲》别再自动发了");
assert.equal(it.kind, "unschedule");
it = P("把《甲》的定时发布关掉");
assert.equal(it.kind, "unschedule");
assert.equal(P("取消修订《甲》的定时"), null, "创作动词守卫照常生效（漏判无害）");

// —— 查询 ——
assert.equal(P("查一下《头七结账人》进度").kind, "query");
assert.equal(P("《头七结账人》写到哪了").kind, "query");
assert.equal(P("查一下《头七结账人》写了多少字").kind, "query");

// —— 扫榜（选材官） ——
it = P("扫一下番茄女频榜");
assert.equal(it.kind, "scan");
assert.equal(it.platform, "fanqie");
assert.equal(it.category, "女频");
it = P("扫榜");
assert.equal(it.kind, "scan");
assert.equal(it.platform, "");
assert.equal(P("写一篇榜单分析文章"), null, "创作动词守卫照常生效");

// —— 否定集：宁漏勿误，这些必须放行建任务（null） ——
assert.equal(P("继续修订《头七结账人》第 11–20 章，保持上一轮的叙事节奏。"), null,
  "快速开始模板句绝不能被拦");
assert.equal(P("修复最近的失败任务，分析根因并生成补丁。"), null);
assert.equal(P("生成本周编排质量与成本周报。"), null);
assert.equal(P("写一个发布策略文档"), null);
assert.equal(P("把今天要完成的事，交给 CodeBee。"), null);
assert.equal(P("帮我审阅未提交的改动"), null);
assert.equal(P(""), null);
assert.equal(P("继续"), null, "裸「继续」不许触发恢复");

process.stdout.write("parser fixtures OK\n");
""")

FLOW_HARNESS = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const src = fs.readFileSync(process.argv[1], 'utf8');

// ---------- 微 DOM：innerHTML 解析 + class 查询（只覆盖本组件用到的面） ----------
function parseHTML(html) {
  const root = { children: [], textNode: false };
  const stack = [root];
  const re = /<(\/?)(\w+)((?:[^>"]|"[^"]*")*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[2]) {
      if (m[1] === "/") { if (stack.length > 1) stack.pop(); continue; }
      const tag = m[2].toLowerCase();
      const el = makeEl(tag);
      const cls = (m[3] || "").match(/class="([^"]*)"/);
      if (cls) cls[1].split(/\s+/).forEach((c) => c && el.classList.add(c));
      const idm = (m[3] || "").match(/id="([^"]*)"/);
      if (idm) el.id = idm[1];
      const attrRe = /([\w-]+)="([^"]*)"/g;
      let am;
      while ((am = attrRe.exec(m[3] || ""))) el.__attrs[am[1]] = am[2];
      const parent = stack[stack.length - 1];
      el.parent = parent;
      parent.children.push(el);
      if (!/^(br|input|img)$/.test(tag)) stack.push(el);
    } else if (m[4] !== undefined) {
      const parent = stack[stack.length - 1];
      parent.children.push({ textNode: true, text: m[4], children: [] });
    }
  }
  return root.children;
}
function serialize(c) {
  if (c.textNode) return c.text;
  const cls = c.className ? ' class="' + c.className + '"' : "";
  const id = c.id ? ' id="' + c.id + '"' : "";
  return "<" + c.tagName.toLowerCase() + cls + id + ">" +
    c.children.map(serialize).join("") + "</" + c.tagName.toLowerCase() + ">";
}
const created = [];
function makeEl(tag) {
  const classes = [];
  const handlers = {};
  const el = {
    tagName: tag.toUpperCase(), id: "", children: [], parent: null,
    disabled: false, __handlers: handlers, value: "", __attrs: {},
    get parentElement() { return el.parent; },
    set parentElement(v) { el.parent = v; },
    classList: {
      add(...cs) { cs.forEach((c) => { if (c && !classes.includes(c)) classes.push(c); }); },
      remove(...cs) { cs.forEach((c) => { const i = classes.indexOf(c); if (i >= 0) classes.splice(i, 1); }); },
      contains(c) { return classes.includes(c); },
    },
    addEventListener(type, fn) { handlers[type] = fn; },
    __fire(type, ev) { const h = handlers[type]; return h ? h.call(el, ev) : undefined; },
    prepend(c) { c.parent = el; el.children.unshift(c); },
    appendChild(c) { c.parent = el; el.children.push(c); },
    removeChild(c) {
      const i = el.children.indexOf(c);
      if (i >= 0) { el.children.splice(i, 1); c.parent = null; }
      return c;
    },
    insertBefore(node, ref) {
      node.parent = el;
      const i = el.children.indexOf(ref);
      if (i >= 0) el.children.splice(i, 0, node); else el.children.push(node);
    },
    remove() {
      if (el.parent) {
        const i = el.parent.children.indexOf(el);
        if (i >= 0) el.parent.children.splice(i, 1);
        el.parent = null;
      }
    },
    querySelector(sel) { return el.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const toks = sel.match(/([.#][\w-]+|\[[^\]]+\]|[a-zA-Z]+)/g) || [];
      const out = [];
      (function walk(n) {
        n.children.forEach((c) => {
          if (c.textNode) return;
          const ok = toks.every((tk) => {
            if (tk[0] === ".") return c.classList.contains(tk.slice(1));
            if (tk[0] === "#") return c.id === tk.slice(1);
            if (tk[0] === "[") {
              const m = tk.match(/\[([\w-]+)="([^"]*)"\]/);
              if (!m) return true;
              return ((c.__attrs || {})[m[1]] === m[2]) ||
                (m[1] === "value" && c.value === m[2]);
            }
            return !!c.tagName && c.tagName.toLowerCase() === tk.toLowerCase();
          });
          if (ok) out.push(c);
          walk(c);
        });
      })(el);
      return out;
    },
  };
  Object.defineProperty(el, "className", {
    get() { return classes.join(" "); },
    set(v) { classes.length = 0; String(v).split(/\s+/).forEach((c) => c && classes.push(c)); },
  });
  Object.defineProperty(el, "textContent", {
    get() { return el.children.map((c) => (c.textNode ? c.text : c.textContent)).join(""); },
    set(v) { el.children = [{ textNode: true, text: String(v), children: [] }]; },
  });
  Object.defineProperty(el, "innerHTML", {
    get() { return el.children.map(serialize).join(""); },
    set(v) { el.children = parseHTML(String(v)); },
  });
  created.push(el);
  return el;
}

// ---------- 世界 ----------
const homeEl = makeEl("section");
const anchor = makeEl("div");
anchor.className = "rd-quick-start";
homeEl.children.push(anchor);
anchor.parentElement = homeEl;

  const fGoal = makeEl("input"); fGoal.id = "f-goal";
  const fWd = makeEl("input"); fWd.id = "f-workdir"; fWd.value = "E:/w1";
  const fType = makeEl("select"); fType.id = "f-type";
  const optRank = makeEl("option");
  optRank.value = "rank_scan";
  optRank.__attrs = { value: "rank_scan" };
  fType.appendChild(optRank);
  const named = { "f-goal": fGoal, "f-workdir": fWd, "f-type": fType };

const doc = {
  readyState: "complete",
  body: makeEl("body"),
  getElementById(id) {
    if (named[id]) return named[id];
    return created.find((e) => e.id === id) || null;
  },
  createElement(tag) { return makeEl(tag); },
  querySelector(sel) { return sel === "#page-home .rd-quick-start" ? anchor : null; },
  addEventListener() {},
};

const S = { state: {
  tasks: [
    { id: "t1", title: "连载《头七结账人》第 58 章起草", type: "serial_novel",
      status: "running", workdir: "E:/w1", updated_at: "2026-11-01 10:00:00",
      auto_publish: { enabled: true, platform: "fanqie", time: "10:00",
                      auto_submit: true } },
    { id: "t2", title: "短篇《乙》评审修订", type: "short_story",
      status: "done", workdir: "E:/w2", updated_at: "2026-11-01 09:00:00" },
  ],
  runs: [
    { id: "r9", task_id: "t1", status: "running", paused: false,
      step_count: 12, current_step: { n: 3 }, created_at: "2026-11-01 10:00:00" },
  ],
} };

const fetchCalls = [];
const bodies = [];
let pendingBooks = null;   // 测试可注入：null = 默认单平台（fanqie 待发 3 章）
let triageReply = null;    // 测试可注入：/api/intent/triage 的返回意图
const origCalls = [];
const opened = [];
const tabs = [];
async function fetchStub(path, opts) {
  const o = opts || {};
  fetchCalls.push((o.method || "GET") + " " + path);
  bodies.push(o.body ? JSON.parse(o.body) : null);
  if (path.endsWith("/pending")) {
    return { ok: true, json: async () => ({ books: pendingBooks || [
      { platform: "fanqie", pending: 3, items: [{ chapter_no: 56 }, { chapter_no: 57 }, { chapter_no: 58 }] },
    ] }) };
  }
  if (path.endsWith("/intent/triage")) {
    return { ok: true, json: async () => ({ intent: triageReply }) };
  }
  return { ok: true, json: async () => ({}) };
}

  const win = {
    t: (s) => s,
    esc: (s) => String(s == null ? "" : s).replace(/</g, "&lt;"),
    authHeaders: () => ({}),
    homeOpenRun: (id) => opened.push(id),
    switchTab: (tab) => tabs.push(tab),
    recommendTaskType: (s) => (/扫榜|榜/.test(String(s)) ? "rank_scan" : ""),
    createTask: function () { origCalls.push(fGoal.value); return Promise.resolve("created"); },
  };

new Function("window", "document", "S", "fetch", src)(win, doc, S, fetchStub);
assert.equal(win.createTask.__ciWrapped, true, "createTask 必须被包一层");
const createTask = win.createTask;
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 5)); };
const box = () => doc.getElementById("rd-chat-turns");

(async () => {
  // 1) 发布：确认前零请求零 run；取消零动作
  fGoal.value = "把《头七结账人》最新5章发到番茄";
  await createTask();
  assert.equal(origCalls.length, 0, "意图接手后不得再走 createTask（零 run 创建）");
  assert.equal(fetchCalls.length, 0, "确认卡渲染必须零请求");
  let card = box().querySelector(".ci-confirm");
  assert(card, "必须出计划确认卡");
  assert(card.textContent.includes("最新 5 章"), "占位符必须被替换（_fmt 回归锚）");
  assert(card.textContent.includes("番茄"), "平台要进确认卡");
  assert(card.querySelector(".ci-allow") && card.querySelector(".ci-cancel"), "必须有取消/确认两钮");
  card.querySelector(".ci-cancel").__fire("click", { target: { closest: () => null } });
  assert.equal(fetchCalls.length, 0, "取消后必须仍然零请求");
  assert(card.classList.contains("ci-idle"), "取消后卡转已取消态");
  assert(box().querySelector(".ci-card").textContent.includes("已取消"), "取消文案要说人话");

  // 2) 重新提交 → 确认 → 才 fetch，载荷正确
  fGoal.value = "把《头七结账人》最新5章发到番茄";
  await createTask();
  assert.equal(box().querySelectorAll(".ci-confirm").length, 1, "新意图必须顶掉旧卡");
  card = box().querySelector(".ci-confirm");
  card.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  assert.deepEqual(fetchCalls,
    ["GET /api/publish/task/t1/pending", "POST /api/publish/task/t1/publish-all"],
    "确认后才请求，且先盘点后执行");
  assert.deepEqual(bodies[1], { platform: "fanqie", chapters: [56, 57, 58], auto_submit: true },
    "publish-all 载荷：平台/最新章号升序/直发");
  assert(box().querySelector(".ci-ok"), "成功结果卡");
  assert.equal(fGoal.value, "", "执行成功后清输入，防重复提交");
  assert.equal(origCalls.length, 0, "全程零 run 创建");

  // 3) 查询：直接答案卡（读 state + 字数接口 + 定时状态），零 run
  fGoal.value = "查一下《头七结账人》进度";
  await createTask();
  await settle();
  assert.equal(origCalls.length, 0);
  assert(box().querySelector(".ci-answer"), "查询必须出答案卡");
  assert(fetchCalls.some((c) => c.startsWith("GET /api/writing/counts")), "字数走真接口");
  assert(box().querySelector(".ci-answer").textContent.includes("头七结账人"), "答案带书名");
  assert(box().querySelector(".ci-answer").textContent.includes("每天 10:00"),
    "答案卡要带定时状态（挂了定时就展示）");

  // 4) 非意图句：照旧 createTask（创作句零 triage 调用）
  fGoal.value = "帮我写第59章，把节奏拉紧";
  await createTask();
  assert.equal(origCalls.length, 1, "漏判必须放行建任务");
  assert.equal(box().querySelectorAll(".ci-confirm").length, 0, "不得出卡");
  assert(!fetchCalls.some((c) => c.includes("/intent/triage")),
    "创作动词句不得触发模型 triage（零调用零延迟）");

  // 5) 手机壳激活不拦
  doc.body.classList.add("mob-shell-on");
  fGoal.value = "把《头七结账人》最新5章发到番茄";
  await createTask();
  assert.equal(origCalls.length, 2, "手机壳不拦（卡片容器不在手机可视层）");
  doc.body.classList.remove("mob-shell-on");

  // 6) 定时：真建单（渲染零请求；确认→pending 认领平台→auto-publish 落单）
  const nBefore = fetchCalls.length;
  fGoal.value = "明早10点把《头七结账人》发5章";
  await createTask();
  assert.equal(fetchCalls.length, nBefore, "确认卡渲染必须零请求");
  card = box().querySelector(".ci-confirm");
  assert(card, "定时必须出计划确认卡");
  assert(card.textContent.includes("每天 10:00"), "时间要解析（明早10点→10:00）");
  assert(card.textContent.includes("不是只发 5 章"), "定时语义要如实（发全部待发章）");
  card.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  assert(fetchCalls.some((c) => c.includes("/auto-publish")), "确认后必须调 auto-publish 建单");
  const apBody = bodies[bodies.length - 1];
  assert.deepEqual(apBody, { platform: "fanqie", time: "10:00", enabled: true },
    "建单载荷：平台自动认领/时间归一 HH:MM/enabled");
  assert(box().querySelector(".ci-ok"), "建单成功结果卡");
  assert.equal(origCalls.length, 2, "全程不建 run");

  // 7) 非失败态重试：确认后如实报错，零请求
  fGoal.value = "重试《乙》";
  await createTask();
  card = box().querySelector(".ci-confirm");
  assert(card, "重试也要过确认卡");
  card.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  assert(fetchCalls.every((c) => !c.includes("/retry")), "非失败态不得发 retry 请求");
  assert(box().querySelector(".ci-err"), "如实出错误卡");
  assert(box().querySelector(".ci-err").textContent.includes("不是失败态"), "错误说人话");

  // 8) 定时双平台：确认后 pending 发现两家 → 追问卡选平台才落单
  pendingBooks = [
    { platform: "fanqie", bound: true, pending: 2 },
    { platform: "qimao", bound: true, pending: 0 },
  ];
  fGoal.value = "把《头七结账人》定时发一下";
  await createTask();
  let card2 = box().querySelector(".ci-confirm");
  assert(card2, "双平台也要先出确认卡");
  card2.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  const apN = fetchCalls.filter((c) => c.includes("/auto-publish")).length;
  assert(fetchCalls.filter((c) => c.includes("/pending")).length > 0, "确认后先盘点平台");
  assert.equal(fetchCalls.filter((c) => c.includes("/auto-publish")).length, apN,
    "平台有歧义时不得擅自建单");
  const btns = card2.querySelectorAll(".ci-ghost");
  assert.equal(btns.length, 2, "追问卡必须给两家平台按钮");
  btns[0].__fire("click", { target: { closest: () => null } });
  await settle();
  assert.equal(fetchCalls.filter((c) => c.includes("/auto-publish")).length, apN + 1,
    "选定平台后才建单");
  assert.deepEqual(bodies[bodies.length - 1],
    { platform: "fanqie", time: "10:10", enabled: true },
    "追问后按所选平台落单（无时间→默认 10:10）");
  assert(box().querySelector(".ci-ok"), "落单成功卡");
  pendingBooks = null;

  // 9) 取消定时：确认卡带现状 → enabled:false 落单；无定时如实说零请求
  fGoal.value = "把《头七结账人》的定时取消";
  await createTask();
  card = box().querySelector(".ci-confirm");
  assert(card, "取消定时也要过确认卡");
  assert(card.textContent.includes("每天 10:00"), "确认卡要展示现行定时");
  const apN9 = fetchCalls.filter((c) => c.includes("/auto-publish")).length;
  card.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  assert.equal(fetchCalls.filter((c) => c.includes("/auto-publish")).length, apN9 + 1,
    "确认后才调 auto-publish");
  assert.deepEqual(bodies[bodies.length - 1],
    { platform: "fanqie", time: "10:00", enabled: false },
    "取消载荷：保留平台/时间，enabled=false（供再开）");
  assert(box().querySelector(".ci-ok").textContent.includes("已取消定时"), "取消成功卡");

  // 10) 无定时取消：诚实直接说，零请求零确认卡
  const n10 = fetchCalls.length;
  fGoal.value = "把《乙》的定时取消";
  await createTask();
  await settle();
  assert.equal(fetchCalls.length, n10, "无定时时零请求");
  assert.equal(box().querySelectorAll(".ci-confirm").length, 0, "无定时不浪费确认卡");
  assert(box().querySelector(".ci-card").textContent.includes("没挂定时"), "如实说没挂定时");

  // 11) triage 命中：确定性没接住的「最新几章」→ 模型起草意图 → 确认卡（不执行）
  const origN = origCalls.length;
  const paN = fetchCalls.filter((c) => c.includes("/publish-all")).length;
  triageReply = { kind: "publish", book: "头七结账人", count: 0,
                  platform: "fanqie", mode: "direct", time: "" };
  fGoal.value = "帮我把《头七结账人》最新几章发出去";
  await createTask();
  await settle();
  assert.equal(origCalls.length, origN, "triage 接手不得走 createTask");
  assert(fetchCalls.some((c) => c.includes("/intent/triage")), "必须问过模型");
  const card11 = box().querySelector(".ci-confirm");
  assert(card11, "triage 命中必须出确认卡");
  assert(card11.textContent.includes("全部待发章"), "count=0 → 全部待发章");
  assert(!box().querySelector(".ci-thinking"), "思考卡必须撤走");
  assert.equal(fetchCalls.filter((c) => c.includes("/publish-all")).length, paN,
    "triage 只起草：不点确认不发任何执行请求");

  // 12) triage 未识别 → fail-open 照旧建任务，思考卡撤走
  triageReply = null;
  const cfN = box().querySelectorAll(".ci-confirm").length;
  fGoal.value = "《头七结账人》帮我发一下呗";
  await createTask();
  await settle();
  assert.equal(origCalls.length, origN + 1, "triage 没识别必须照旧建任务");
  assert.equal(box().querySelectorAll(".ci-confirm").length, cfN, "不出新确认卡");
  assert(!box().querySelector(".ci-thinking"), "思考卡必须撤走");
  triageReply = null;

  // 13) 扫榜：确认卡（选材官/女频）→ 确认才带类型+结构化指令走真建任务
  const origN2 = origCalls.length;
  fGoal.value = "扫一下番茄女频榜";
  await createTask();
  let card13 = box().querySelector(".ci-confirm");
  assert(card13, "扫榜必须出确认卡");
  assert(card13.textContent.includes("选材官"), "专家要进卡");
  assert(card13.textContent.includes("女频"), "品类要进卡");
  assert.equal(origCalls.length, origN2, "确认前零建任务");
  const apN13 = fetchCalls.filter((c) => c.includes("/auto-publish")).length;
  card13.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  assert.equal(fType.value, "rank_scan", "确认后类型切到选材官");
  assert(fGoal.value.indexOf("扫描番茄女频榜") >= 0 &&
         fGoal.value.indexOf("原始要求") >= 0,
    "结构化指令要带扫描目标与原始要求（不改写丢信息）");
  assert.equal(origCalls.length, origN2 + 1, "确认后走真建任务（__ciOrig 旁路）");
  assert.equal(fetchCalls.filter((c) => c.includes("/auto-publish")).length, apN13,
    "扫榜不发发布请求");

  // 14) 形态没开选材官：如实报错零建任务
  fType.value = "";
  fType.removeChild(optRank);
  const origN3 = origCalls.length;
  fGoal.value = "扫一下七猫男频榜";
  await createTask();
  card13 = box().querySelector(".ci-confirm");
  assert(card13, "仍要出确认卡");
  card13.querySelector(".ci-allow").__fire("click", { target: { closest: () => null } });
  await settle();
  assert(box().querySelector(".ci-err").textContent.includes("没启用选材官"),
    "如实说形态没开");
  assert.equal(origCalls.length, origN3, "形态没开不得建任务");
  assert.equal(fGoal.value.indexOf("扫描") >= 0, false, "失败路径不改写输入");

  process.stdout.write("flow gates OK\n");
})().catch((e) => { console.error(e); process.exit(1); });
""")


class ChatIntentTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to exercise the chat-intent overlay")

    def test_wiring_in_index_html(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="chat-intent.css">', html)
        self.assertLess(
            html.index('<script defer src="chat-stream.js"></script>'),
            html.index('<script defer src="chat-intent.js"></script>'),
            "chat-intent.js 必须在 chat-stream.js 之后加载")

    def test_no_native_dialogs(self):
        src = INTENT_JS.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b(alert|confirm|prompt)\s*\(", src))

    def test_css_no_hardcoded_colors(self):
        css = INTENT_CSS.read_text(encoding="utf-8")
        for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", css):
            head = css[max(0, m.start() - 40):m.start()]
            self.assertIn("var(--", head, "十六进制必须放在 var() 回落位")

    def test_i18n_labels_registered(self):
        i18n = I18N.read_text(encoding="utf-8")
        for key in ("计划确认", "确认执行", "已取消，什么都没执行。", "番茄", "七猫",
                    "没找到《{0}》对应的任务。", "查到了", "已写字数", "最近一轮"):
            self.assertIn('"%s":' % key, i18n, "缺 EN 词条：%s" % key)

    def test_parser_fixtures(self):
        r = subprocess.run(
            [self.node, "-e", PARSER_HARNESS, str(INTENT_JS)],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0,
                         "解析器 fixture 失败：\n" + ((r.stderr or "") + (r.stdout or ""))[-3000:])

    def test_flow_gates(self):
        r = subprocess.run(
            [self.node, "-e", FLOW_HARNESS, str(INTENT_JS)],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0,
                         "行为闸门失败：\n" + ((r.stderr or "") + (r.stdout or ""))[-3000:])


if __name__ == "__main__":
    unittest.main()
