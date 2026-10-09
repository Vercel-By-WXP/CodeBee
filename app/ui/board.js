/* CodeBee 任务驾驶舱（/board.html）：4s 轮询 /api/board（KB 级聚合端点），
   不碰 /api/state 全量，也不占 SSE。下钻抽屉按需拉 /api/board/run 与
   /api/runs/<id>/log，不参与 4s 轮询。 */
"use strict";

const $ = (s) => document.querySelector(s);
const t = (...a) => (window.t ? window.t(...a) : a[0]);   // i18n.js 先于本脚本加载；透传占位符参数（{0}/{1}）
const POLL_MS = 4000;

// URL ?lang=en|zh 可覆盖语言（i18n.js 已按 localStorage 自动 apply 过一次，
// 这里写回偏好并重刷静态文案；动态文案走 t() 每次渲染实时取）
try {
  const ql = new URLSearchParams(location.search).get("lang");
  if ((ql === "en" || ql === "zh") && window.getLang && window.getLang() !== ql) {
    window.setLang(ql);
    if (window.applyI18n) window.applyI18n();
  }
} catch (e) { /* ignore */ }

/* ---------------------------------------------------------------- 工具 */
function fmtTokens(n) {
  n = Number(n) || 0;
  if (n >= 1e8) return (n / 1e8).toFixed(2) + t("亿");
  if (n >= 1e4) return (n / 1e4).toFixed(1) + t("万");
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function fmtCost(n) {
  n = Number(n) || 0;
  if (n >= 100) return "$" + n.toFixed(0);
  if (n >= 1) return "$" + n.toFixed(2);
  return "$" + n.toFixed(3);
}
function fmtDur(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return h + "h" + String(m).padStart(2, "0") + "m";
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}
function parseTs(s) {
  // 后端时间格式 "YYYY-MM-DD HH:MM:SS"；补 T 让各浏览器 Date 都能解析
  const t = Date.parse(String(s || "").replace(" ", "T"));
  return isNaN(t) ? 0 : t;
}
/* 指标条长数值自适应：行宽放不下就逐档降字号（27→21→16px）。
   判据用 k-row 的 scrollWidth：num/sub 都 nowrap 不收缩，超宽才会外溢。 */
function fitKpis() {
  document.querySelectorAll("#kpis .k-row").forEach((row) => {
    const num = row.querySelector(".k-num");
    if (!num) return;
    num.classList.remove("sm", "xs");
    if (row.scrollWidth <= row.clientWidth + 1) return;
    num.classList.add("sm");
    if (row.scrollWidth <= row.clientWidth + 1) return;
    num.classList.remove("sm");
    num.classList.add("xs");
  });
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
function hmms(s) { return String(s || "").slice(11, 16); }

/* ---------------------------------------------------------------- 令牌门 */
function token() { return localStorage.getItem("orch.token") || ""; }

function showGate(msg) {
  $("#gate").classList.remove("hidden");
  $("#gate-err").textContent = msg || "";
  setTimeout(() => $("#gate-token").focus(), 50);
}
$("#gate-ok").addEventListener("click", submitGate);
$("#gate-token").addEventListener("keydown", (e) => { if (e.key === "Enter") submitGate(); });
function submitGate() {
  const v = $("#gate-token").value.trim();
  if (!v) return;
  localStorage.setItem("orch.token", v);
  $("#gate").classList.add("hidden");
  poll();
}

/* ---------------------------------------------------------------- 换肤
   大屏独立记忆：orch.boardSkin / orch.boardTheme 优先，缺省时继承主界面的
   orch.skin / orch.theme（第一次打开跟主界面一致），再缺省落 ocean/dark。
   只写 html 的 data-skin/data-theme，值集与主应用 style.css 同一套。 */
const SKINS = [["ocean", "深海"], ["classic", "经典"], ["forest", "森林"],
               ["amber", "暖阳"], ["violet", "霓虹"], ["contrast", "高对比"]];
const SKIN_IDS = new Set(SKINS.map((s) => s[0]));
const _q = new URLSearchParams(location.search);

function skin() {
  const v = _q.get("skin") || localStorage.getItem("orch.boardSkin") || localStorage.getItem("orch.skin") || "ocean";
  return SKIN_IDS.has(v) ? v : "ocean";
}
function mode() {
  const v = _q.get("mode") || localStorage.getItem("orch.boardTheme") || localStorage.getItem("orch.theme") || "dark";
  return v === "light" ? "light" : "dark";
}
function applySkin() {
  const r = document.documentElement;
  r.dataset.skin = skin();
  r.dataset.theme = mode();
  $("#sw-skin-n").textContent = t((SKINS.find((s) => s[0] === skin()) || SKINS[0])[1]);
  $("#sw-mode-n").textContent = t(mode() === "dark" ? "夜间" : "日间");
}
$("#sw-skin").addEventListener("click", () => {
  const i = SKINS.findIndex((s) => s[0] === skin());
  localStorage.setItem("orch.boardSkin", SKINS[(i + 1) % SKINS.length][0]);
  applySkin();
});
$("#sw-mode").addEventListener("click", () => {
  localStorage.setItem("orch.boardTheme", mode() === "dark" ? "light" : "dark");
  applySkin();
});
applySkin();

/* ---------------------------------------------------------------- 拉取 */
let connLost = false;

async function api(path) {
  const r = await fetch(path, { headers: { "X-CodeBee-Token": token() } });
  if (r.status === 401) { showGate(t("令牌不正确或已变更")); throw new Error("401"); }
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function poll() {
  let data;
  try {
    data = await api("/api/board");
    if (connLost) { connLost = false; $("#hd-conn").classList.add("hidden"); }
  } catch (e) {
    if (e && e.message === "401") return;
    if (!connLost) { connLost = true; $("#hd-conn").classList.remove("hidden"); }
    $("#hd-updated").textContent = t("重试连接中");
    return;
  }
  LAST = data;
  render(data);
  if (DRAWER.runId) syncDrawer();
}
let LAST = null;

/* ---------------------------------------------------------------- 渲染 */
const cardEls = new Map();   // task_id -> 卡元素（复用防闪烁）

function render(d) {
  const running = Array.isArray(d.running) ? d.running : [];
  const queued = Array.isArray(d.queued) ? d.queued : [];
  const recent = Array.isArray(d.recent) ? d.recent : [];
  const usage = d.usage_today || {};
  const pool = d.pool || {};
  const health = Array.isArray(d.health) ? d.health : [];

  // 指标条：一格一个数，副位放该指标自己的第二维度（不再重复标签）
  const done = Number(d.today_done) || 0;
  const failed = Number(d.today_failed) || 0;
  const finished = done + failed;
  $("#k-running").textContent = running.length;
  const dSum = running.reduce((a, c) => a + (Number(c.steps_done) || 0), 0);
  const tSum = running.reduce((a, c) => a + (Number(c.steps_total) || 0), 0);
  $("#k-run-sub").textContent = running.length ? t("步 {0}/{1}", dSum, tSum) : t("无活动");

  $("#k-queued").textContent = queued.length;
  $(".k-queue").classList.toggle("zero", !queued.length);
  $("#k-queue-sub").textContent = queued.length
    ? t("最长等待 {0}", fmtDur((Date.now() - parseTs(queued[0].created_at)) / 1000))
    : t("队首空闲");

  $("#k-done").textContent = done;
  $("#k-done-sub").textContent = finished ? t("成功率 {0}%", Math.round(done / finished * 100)) : t("尚无结课运行");
  $("#k-ratio-fill").style.width = (finished ? done / finished * 100 : 0) + "%";

  $("#k-failed").textContent = failed;
  $(".k-fail").classList.toggle("zero", !failed);
  const firstFail = recent.find((r) => r.status === "failed");
  $("#k-fail-sub").textContent = failed
    ? (firstFail ? t("最近 {0}", hmms(firstFail.ended_at)) : t("需要关注"))
    : t("今日零失败");

  $("#k-tokens").textContent = fmtTokens(usage.tokens);
  $("#k-use-sub").textContent = fmtCost(usage.cost_usd) + " · " + (Number(usage.calls) || 0) + " " + t("次调用");
  fitKpis();

  // 顶栏态势：整屏只在这里说一次「有没有活」
  const st = $("#hd-state");
  st.classList.toggle("live", running.length > 0);
  $("#hd-state-text").textContent = running.length
    ? t("蜂群运行中 · {0} 个任务并行", running.length)
    : (queued.length ? t("蜂群待命 · {0} 个任务排队", queued.length) : t("蜂群待命"));
  $("#hd-pool-alive").textContent = pool.alive || 0;
  $("#hd-pool-target").textContent = "/" + (pool.target || 0);
  $("#hd-chat-alive").textContent = pool.chat_alive || 0;
  $("#hd-chat-pool").textContent = "/" + (pool.chat_pool || 0);
  $("#hd-updated").textContent = t("已同步") + " · " +
    (String(d.now || "").slice(11, 19) || new Date().toLocaleTimeString([], { hour12: false }));

  // 服务健康：常态一行计数，异常才点名（7 个绿灯点阵是噪音）
  const downs = health.filter((h) => h.status === "down" && !h.silenced);
  const hp = $("#hd-health");
  hp.classList.toggle("hidden", !health.length);
  hp.classList.toggle("bad", downs.length > 0);
  $("#hd-health-text").textContent = downs.length
    ? t("{0} 个服务异常", downs.length)
    : t("服务 {0}/{1} 正常", health.length, health.length);
  hp.title = downs.length
    ? downs.map((h) => (h.provider + " " + (h.model || "")).trim()).join("\n") : "";

  // 执行池
  const alive = Math.max(0, Number(pool.alive) || 0);
  const target = Math.max(0, Number(pool.target) || 0);
  $("#pool-meter-fill").style.width = (target ? Math.min(100, alive / target * 100) : 0) + "%";
  $("#pool-alive").textContent = alive;
  $("#pool-target").textContent = target;
  $("#chat-alive").textContent = Math.max(0, Number(pool.chat_alive) || 0);
  $("#chat-pool").textContent = Math.max(0, Number(pool.chat_pool) || 0);
  $("#chat-meter-fill").style.width = (pool.chat_pool
    ? Math.min(100, (pool.chat_alive || 0) / pool.chat_pool * 100) : 0) + "%";
  $("#pool-available").textContent = Math.max(0, Number(pool.available) || 0);
  $("#pool-mode").textContent = pool.mode || "—";

  renderWall(d);
  renderTrend(d.trend || [], String(d.now || "").slice(0, 10));
  renderModels(d.models || []);
  renderRecent(recent);
}

/* 卡片骨架建一次，之后只改字段——4s 重绘不能把终端滚动和动画打断 */
function buildCard() {
  const el = document.createElement("article");
  el.className = "card";
  el.innerHTML =
    '<div class="c-head"><span class="c-type"></span><span class="c-title"></span>' +
    '<span class="c-elapsed" data-started=""></span></div>' +
    '<div class="c-main"><p class="c-goal"></p><div class="c-steps"></div>' +
    '<div class="c-step-line"><span class="no"></span><span class="ag"></span><span class="sm"></span></div>' +
    '<div class="c-badges"><span class="b b-model"></span><span class="b b-prov"></span></div></div>' +
    '<div class="c-foot"><span class="c-est"><span class="est-track"><i></i></span><em></em></span>' +
    '<span class="c-tok"></span></div>' +
    '<div class="c-live"><div class="c-live-head"><span class="tag"></span><span class="caret"></span></div>' +
    '<div class="c-live-body"><pre class="c-live-text"></pre></div></div>';
  el.addEventListener("click", () => openDrawer(el.dataset.run || "", el));
  return el;
}

function renderWall(d) {
  const wrap = $("#wall-cards");
  const empty = $("#wall-empty");
  const running = Array.isArray(d.running) ? d.running : [];
  const queued = Array.isArray(d.queued) ? d.queued : [];
  const has = running.length > 0;

  $("#wall-count").textContent = running.length + " ACTIVE";
  $("#queue-count").textContent = queued.length;
  // toggle 第二参是 force（true=加类），传反会让空态面板在运行时常驻
  empty.classList.toggle("hidden", has);
  // className 整写会抹掉刚 toggle 的 hidden，所以密度与 hidden 一起算
  wrap.className = "wall-grid " +
    (running.length <= 1 ? "d-solo" : running.length <= 3 ? "d-few" : "d-many") +
    (has ? "" : " hidden");
  renderIdle(d);

  const seen = new Set();
  for (const c of running) {
    seen.add(c.task_id);
    let el = cardEls.get(c.task_id);
    if (!el) { el = buildCard(); wrap.appendChild(el); cardEls.set(c.task_id, el); }
    el.dataset.task = c.task_id;
    el.dataset.run = c.run_id || "";

    el.querySelector(".c-type").textContent = c.type_name || c.type || t("任务");
    el.querySelector(".c-title").textContent = c.title || t("（未命名）");
    el.querySelector(".c-goal").textContent = c.goal || "";
    const el2 = el.querySelector(".c-elapsed");
    el2.dataset.started = c.started_at || "";
    el2.textContent = fmtDur((Date.now() - parseTs(c.started_at)) / 1000);

    const cur = c.current || {};
    const seg = el.querySelector(".c-steps");
    const total = Math.max(c.steps_total || 0, 1);
    if (seg.childElementCount !== total) {
      seg.innerHTML = "";
      for (let i = 0; i < total; i++) seg.appendChild(document.createElement("i"));
    }
    [...seg.children].forEach((b, i) => {
      b.className = i < (c.steps_done || 0) ? "d" : (i === (c.steps_done || 0) ? "r" : "");
    });

    el.querySelector(".c-step-line .no").textContent = cur.n ? ("#" + cur.n) : "—";
    el.querySelector(".c-step-line .ag").textContent = cur.agent || "";
    el.querySelector(".c-step-line .sm").textContent = cur.summary || cur.tail || t("执行中");

    const bModel = el.querySelector(".b-model");
    bModel.textContent = cur.model || t("模型待定");
    bModel.classList.toggle("dim", !cur.model);
    const bProv = el.querySelector(".b-prov");
    bProv.textContent = cur.provider || "";
    bProv.classList.toggle("hidden", !cur.provider);

    const cost = Number(c.run_cost_usd) || 0;
    const rt = el.querySelector(".c-tok");
    rt.textContent = (c.run_tokens ? fmtTokens(c.run_tokens) + " tok" : "") +
      (cost > 0 ? (c.run_tokens ? " · " : "") + fmtCost(cost) : "");
    rt.classList.toggle("hidden", !c.run_tokens && !(cost > 0));

    const est = el.querySelector(".c-est");
    if (c.estimate_s && parseTs(c.started_at)) {
      const p = Math.min(99, Math.round(((Date.now() - parseTs(c.started_at)) / 1000) / c.estimate_s * 100));
      est.classList.remove("hidden");
      est.querySelector(".est-track i").style.width = p + "%";
      est.querySelector("em").textContent = t("预估 {0} · 进度≈{1}%", fmtDur(c.estimate_s), p);
    } else {
      est.classList.add("hidden");
    }

    // 终端框常驻：没有流也留占位，否则卡内多出来的高度无处安放，卡片会塌成一条
    const live = el.querySelector(".c-live");
    const lt = cur.live_text || cur.tail || "";
    const tagMap = { output: t("输出"), thinking: t("思考") };
    live.querySelector(".tag").textContent = tagMap[cur.live_label] || (lt ? t("实时") : "");
    const txt = live.querySelector(".c-live-text");
    txt.textContent = lt || t("等待实时输出…");
    txt.classList.toggle("dim", !lt);
    live.querySelector(".caret").classList.toggle("hidden", !lt);
    if (DRAWER.taskId === c.task_id) el.classList.add("sel");
  }
  for (const [tid, el] of cardEls) {
    if (!seen.has(tid)) { el.remove(); cardEls.delete(tid); }
  }

  const qr = $("#queue-row");
  qr.classList.toggle("hidden", !queued.length);
  $("#queue-chips").innerHTML = queued.map((q) => {
    const wait = q.created_at ? fmtDur((Date.now() - parseTs(q.created_at)) / 1000) : "";
    return '<span class="chip" title="' + esc(q.title) + '">' + esc(q.title) +
      (wait ? "<em>" + esc(wait) + "</em>" : "") + "</span>";
  }).join("");
}

/* 空闲时面板不放轨道动画装饰，改画今日运行表——大屏最该回答「今天跑过什么」 */
function renderIdle(d) {
  const today = String(d.today || "");
  const rows = (Array.isArray(d.recent) ? d.recent : []).filter((r) =>
    String(r.ended_at || "").startsWith(today));
  $("#idle-count").textContent = rows.length;
  const ST = { done: t("完成"), failed: t("失败"), cancelled: t("取消") };
  $("#idle-rows").innerHTML = rows.length ? rows.map((r) =>
    '<div class="h-row"><span class="h-st ' + esc(r.status) + '">' + esc(ST[r.status] || r.status) + "</span>" +
    '<span class="h-t" title="' + esc(r.title) + '">' + esc(r.title) + "</span>" +
    '<span class="h-meta">' + (r.duration_s != null ? fmtDur(r.duration_s) : "—") + "</span>" +
    '<span class="h-time">' + esc(hmms(r.ended_at)) + "</span>" +
    (r.error ? '<span class="h-e" title="' + esc(r.error) + '">' + esc(r.error) + "</span>" : "") +
    "</div>").join("") : '<div class="empty">' + t("今天还没有已结束的") + "</div>";
}

function renderTrend(trend, today) {
  const box = $("#trend");
  const max = Math.max(1, ...trend.map((t) => t.tokens || 0));
  const sum = trend.reduce((a, t) => a + (t.tokens || 0), 0);
  $("#trend-total").textContent = fmtTokens(sum) + " / " + t("7日");
  box.innerHTML = trend.map((t) => {
    const h = Math.round((t.tokens || 0) / max * 100);
    const isToday = today && String(t.day || "") === today;
    return '<div class="bar' + (t.tokens ? " on" : "") + (isToday ? " today" : "") +
      '" title="' + esc(t.day) + " · " + fmtTokens(t.tokens) + '"><i style="height:' +
      Math.max(h, 3) + '%"></i><b>' + esc(String(t.day || "").slice(5)) + "</b></div>";
  }).join("");
}

function renderModels(models) {
  const box = $("#models");
  if (!models.length) { box.innerHTML = '<div class="empty">' + t("暂无调用记录") + "</div>"; return; }
  const max = Math.max(1, ...models.map((m) => m.tokens || 0));
  const sum = models.reduce((a, m) => a + (m.tokens || 0), 0) || 1;
  box.innerHTML = models.map((m) =>
    '<div class="model-row"><div class="l"><div class="nm" title="' + esc(m.model) + '">' + esc(m.model) + "</div>" +
    '<div class="tk"><i style="width:' + Math.max(3, (m.tokens || 0) / max * 100) + '%"></i></div></div>' +
    '<div class="vl">' + fmtTokens(m.tokens) + "<em>" + Math.round((m.tokens || 0) / sum * 100) + "%</em></div></div>"
  ).join("");
}

function renderRecent(recent) {
  const box = $("#recent");
  $("#recent-count").textContent = recent.length + " " + t("条");
  if (!recent.length) { box.innerHTML = '<div class="empty">' + t("还没有已完成的运行") + "</div>"; return; }
  box.innerHTML = recent.map((r) => {
    const cls = r.status === "done" ? "done" : r.status === "failed" ? "failed" : "cancelled";
    return '<div class="r-row"><span class="r-dot ' + cls + '"></span>' +
      '<span class="r-t" title="' + esc(r.title + (r.error ? " · " + r.error : "")) + '">' + esc(r.title) + "</span>" +
      '<span class="r-meta">' + (r.duration_s != null ? fmtDur(r.duration_s) : "—") + " · " + esc(hmms(r.ended_at)) + "</span>" +
      (r.error ? '<span class="r-err">' + esc(r.error) + "</span>" : "") + "</div>";
  }).join("");
}

/* ---------------------------------------------------------------- 下钻抽屉
   步骤链：/api/board/run?run_id=（点开才拉，不进 4s 轮询）
   单步日志：现成的 /api/runs/<id>/log?step=<rel>&pretty=1
   运行中的步每 2.5s 续拉，结束即停，避免盯着不动的日志以为坏了。 */
const DRAWER = { taskId: "", runId: "", sel: -1, steps: [], timer: 0 };

async function openDrawer(runId, cardEl) {
  if (!runId) return;
  DRAWER.runId = runId;
  DRAWER.taskId = cardEl ? cardEl.dataset.task || "" : "";
  DRAWER.sel = -1;
  document.querySelectorAll(".card.sel").forEach((el) => el.classList.remove("sel"));
  if (cardEl) cardEl.classList.add("sel");
  $("#scrim").classList.add("on");
  $("#drawer").classList.add("on");
  $("#drawer").setAttribute("aria-hidden", "false");

  const c = ((LAST || {}).running || []).find((x) => x.run_id === runId) || {};
  $("#dw-type").textContent = c.type_name || t("任务");
  $("#dw-title").textContent = c.title || runId;
  $("#dw-goal").textContent = c.goal || "";
  DRAWER.taskId = c.task_id || DRAWER.taskId;
  $("#dw-meta").innerHTML = [
    [t("开始"), String(c.started_at || "").slice(11, 19) || "—"],
    [t("已运行"), c.started_at ? fmtDur((Date.now() - parseTs(c.started_at)) / 1000) : "—"],
    [t("进度"), (c.steps_done || 0) + "/" + (c.steps_total || 0)],
    [t("预估"), c.estimate_s ? fmtDur(c.estimate_s) : t("无")],
    [t("执行体"), (c.current || {}).agent || "—"],
    [t("模型"), (c.current || {}).model || "—"],
    [t("厂商"), (c.current || {}).provider || "—"],
    [t("消耗"), fmtTokens(c.run_tokens) + " · " + fmtCost(c.run_cost_usd)],
  ].map(([k, v]) => "<div><span>" + esc(k) + "</span><b>" + esc(v) + "</b></div>").join("");

  $("#dw-steps").innerHTML = '<div class="empty">' + t("加载中") + "…</div>";
  $("#dw-log-body").textContent = "";
  await syncDrawer();
  clearInterval(DRAWER.timer);
  DRAWER.timer = setInterval(() => {
    if (!DRAWER.runId) return clearInterval(DRAWER.timer);
    syncDrawer();
  }, 2500);
}

const STEP_ST = () => ({ done: t("完成"), running: t("运行中"), failed: t("失败"),
                         cancelled: t("取消"), pending: t("等待") });

async function syncDrawer() {
  if (!DRAWER.runId) return;
  let d;
  try { d = await api("/api/board/run?run_id=" + encodeURIComponent(DRAWER.runId)); }
  catch (e) { $("#dw-steps").innerHTML = '<div class="empty">' + t("读取失败") + "</div>"; return; }
  const steps = Array.isArray(d.steps) ? d.steps : [];
  DRAWER.steps = steps;
  if (!steps.length) { $("#dw-steps").innerHTML = '<div class="empty">' + t("暂无步骤") + "</div>"; return; }
  // 未手选时自动跟到运行中的那一步（没有运行步就停到最后一步）
  if (DRAWER.sel < 0) {
    const live = steps.findIndex((s) => s.status === "running");
    DRAWER.sel = live >= 0 ? live : steps.length - 1;
  }
  DRAWER.sel = Math.min(DRAWER.sel, steps.length - 1);
  const ST = STEP_ST();
  $("#dw-steps").innerHTML = steps.map((s, i) =>
    '<div class="s-row' + (i === DRAWER.sel ? " act" : "") + '" data-s="' + i + '">' +
    '<span class="s-i">' + (s.n != null ? s.n : i + 1) + "</span>" +
    '<span class="s-n">' + esc(s.role || s.agent || t("步骤")) +
    (s.status === "running" ? '<i class="s-m"><i></i></i>' : "") + "</span>" +
    '<span class="s-a">' + esc(s.agent_label || s.agent || "—") + "</span>" +
    '<span class="s-d">' + (s.duration_s != null ? fmtDur(s.duration_s) : "—") + "</span>" +
    '<span class="s-s ' + esc(s.status || "") + '">' + esc(ST[s.status] || s.status || "—") + "</span></div>"
  ).join("");
  renderStepLog(steps[DRAWER.sel]);
}

async function renderStepLog(s) {
  if (!s) return;
  $("#dw-log-tag").textContent = "#" + (s.n != null ? s.n : "") + " " + (s.role || t("步骤"));
  const ST = STEP_ST();
  $("#dw-log-sub").textContent = ST[s.status] || s.status || "";
  const box = $("#dw-log-body");
  if (!s.log) { box.textContent = s.summary || s.error || t("该步没有日志文件"); return; }
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  try {
    const d = await api("/api/runs/" + encodeURIComponent(DRAWER.runId) + "/log?step=" +
      encodeURIComponent(s.log) + "&pretty=1&tail=20000");
    box.textContent = d.log || t("（日志为空）");
    if (s.status === "running" || stick) box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.textContent = t("日志读取失败");
  }
}

function closeDrawer() {
  clearInterval(DRAWER.timer);
  DRAWER.runId = ""; DRAWER.taskId = ""; DRAWER.sel = -1; DRAWER.steps = [];
  $("#scrim").classList.remove("on");
  $("#drawer").classList.remove("on");
  $("#drawer").setAttribute("aria-hidden", "true");
  document.querySelectorAll(".card.sel").forEach((el) => el.classList.remove("sel"));
}

$("#dw-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
$("#dw-steps").addEventListener("click", (e) => {
  const row = e.target.closest(".s-row");
  if (!row) return;
  DRAWER.sel = Number(row.dataset.s);
  document.querySelectorAll("#dw-steps .s-row").forEach((el) =>
    el.classList.toggle("act", el === row));
  renderStepLog(DRAWER.steps[DRAWER.sel]);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
  if (e.key === "T" || e.key === "t") $("#sw-mode").click();
  if (e.key === "S" || e.key === "s") $("#sw-skin").click();
});
window.addEventListener("resize", fitKpis);

/* ---------------------------------------------------------------- 时钟与耗时每秒跳 */
setInterval(() => {
  const n = new Date();
  const p = (x) => String(x).padStart(2, "0");
  $("#hd-clock").textContent = p(n.getHours()) + ":" + p(n.getMinutes()) + ":" + p(n.getSeconds());
  document.querySelectorAll(".c-elapsed[data-started]").forEach((el) => {
    const st = parseTs(el.dataset.started);
    if (st) el.textContent = fmtDur((Date.now() - st) / 1000);
  });
}, 1000);

poll();
setInterval(poll, POLL_MS);
