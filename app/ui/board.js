/* CodeBee 任务驾驶舱（/board.html）：4s 轮询 /api/board（KB 级聚合端点），
   不碰 /api/state 全量，也不占 SSE。 */
"use strict";

const $ = (s) => document.querySelector(s);
const t = (k) => (window.t ? window.t(k) : k);   // i18n.js 先于本脚本加载
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
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

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

/* ---------------------------------------------------------------- 拉取 */
let connLost = false;

async function poll() {
  let data;
  try {
    const r = await fetch("/api/board", { headers: { "X-CodeBee-Token": token() } });
    if (r.status === 401) { showGate(t("令牌不正确或已变更")); return; }
    if (!r.ok) throw new Error("HTTP " + r.status);
    data = await r.json();
    if (connLost) { connLost = false; $("#hd-conn").classList.add("hidden"); }
  } catch (e) {
    if (!connLost) { connLost = true; $("#hd-conn").classList.remove("hidden"); }
    return;
  }
  render(data);
}

/* ---------------------------------------------------------------- 渲染 */
const cardEls = new Map();   // task_id -> 卡元素（复用防闪烁）

function render(d) {
  // KPI
  $("#k-running").textContent = d.running.length;
  $("#k-queued").textContent = d.queued.length;
  $("#k-done").textContent = d.today_done;
  $("#k-failed").textContent = d.today_failed;
  $("#k-tokens").textContent = fmtTokens(d.usage_today.tokens);
  $("#k-cost").textContent = fmtCost(d.usage_today.cost_usd);

  // 顶栏
  const pool = d.pool || {};
  $("#hd-pool").textContent = t("蜂巢") + " " + (pool.alive || 0) + "/" + (pool.target || 0)
    + " · " + t("对话") + " " + (pool.chat_alive || 0) + "/" + (pool.chat_pool || 0);
  const downs = (d.health || []).filter((h) => h.status === "down" && !h.silenced);
  const hp = $("#hd-health");
  if (downs.length) {
    hp.classList.remove("hidden");
    hp.textContent = "⚠ " + downs.map((h) => h.provider).join("、") + " " + t("异常");
    hp.title = downs.map((h) => (h.provider + " " + (h.model || "")).trim()).join("\n");
  } else {
    hp.classList.add("hidden");
  }

  renderWall(d);
  renderTrend(d.trend || []);
  renderModels(d.models || []);
  renderRecent(d.recent || []);
}

function renderWall(d) {
  const wrap = $("#wall-cards");
  const empty = $("#wall-empty");
  const has = d.running.length > 0;
  empty.classList.toggle("hidden", has);
  wrap.classList.toggle("hidden", !has);

  const seen = new Set();
  for (const c of d.running) {
    seen.add(c.task_id);
    let el = cardEls.get(c.task_id);
    if (!el) {
      el = document.createElement("div");
      el.className = "card";
      el.innerHTML =
        '<div class="card-top"><span class="c-type"></span><span class="c-title"></span>' +
        '<span class="c-elapsed" data-started=""></span></div>' +
        '<div class="c-meta"><span class="b b-model"></span><span class="b b-prov"></span>' +
        '<span class="b b-agent"></span><span class="c-run-tok"></span></div>' +
        '<div class="c-steps"></div>' +
        '<div class="c-stepinfo"><span class="no"></span><span class="ag"></span><span class="sm"></span></div>' +
        '<div class="c-live"><span class="c-live-tag"></span><pre class="c-live-text"></pre></div>' +
        '<div class="c-pct"></div>';
      wrap.appendChild(el);
      cardEls.set(c.task_id, el);
    }
    el.querySelector(".c-type").textContent = c.type_name || c.type || t("任务");
    el.querySelector(".c-title").textContent = c.title || t("（未命名）");
    const el2 = el.querySelector(".c-elapsed");
    el2.dataset.started = c.started_at || "";
    el2.textContent = fmtDur((Date.now() - parseTs(c.started_at)) / 1000);

    // 模型 / 厂商 / 执行智能体徽章 + 本次消耗
    const cur0 = c.current || {};
    const bModel = el.querySelector(".b-model");
    bModel.textContent = cur0.model || t("模型待定");
    bModel.classList.toggle("dim", !cur0.model);
    const bProv = el.querySelector(".b-prov");
    bProv.textContent = cur0.provider || "";
    bProv.classList.toggle("hidden", !cur0.provider);
    const bAgent = el.querySelector(".b-agent");
    bAgent.textContent = cur0.agent || "";
    bAgent.classList.toggle("hidden", !cur0.agent);
    const rt = el.querySelector(".c-run-tok");
    const cost = Number(c.run_cost_usd) || 0;
    rt.textContent = (c.run_tokens ? fmtTokens(c.run_tokens) + " tok" : "") +
      (cost > 0 ? (c.run_tokens ? " · " : "") + "$" + cost.toFixed(cost >= 1 ? 2 : 3) : "");
    rt.classList.toggle("hidden", !c.run_tokens && !(cost > 0));

    // 分段进度条
    const seg = el.querySelector(".c-steps");
    const total = Math.max(c.steps_total || 0, 1);
    if (seg.childElementCount !== total) {
      seg.innerHTML = "";
      for (let i = 0; i < total; i++) seg.appendChild(document.createElement("i"));
    }
    [...seg.children].forEach((b, i) => {
      b.className = i < (c.steps_done || 0) ? "d" : (i === (c.steps_done || 0) ? "r" : "");
    });

    const cur = c.current || {};
    el.querySelector(".no").textContent = cur.n ? ("#" + cur.n) : "—";
    el.querySelector(".ag").textContent = cur.agent || "";
    el.querySelector(".sm").textContent = cur.summary || t("执行中");

    // 实时流窗口：思考/输出尾部多行（live_label 是 code，前端翻译）
    const live = el.querySelector(".c-live");
    const lt = cur.live_text || "";
    live.classList.toggle("hidden", !lt);
    if (lt) {
      const tagMap = { output: t("输出"), thinking: t("思考") };
      el.querySelector(".c-live-tag").textContent = tagMap[cur.live_label] || t("实时");
      el.querySelector(".c-live-text").textContent = lt;
    }

    // 有预估时长则给完成度百分比
    const pct = el.querySelector(".c-pct");
    if (c.estimate_s && parseTs(c.started_at)) {
      const p = Math.min(99, Math.round(((Date.now() - parseTs(c.started_at)) / 1000) / c.estimate_s * 100));
      pct.textContent = t("预估 {0} · 进度≈{1}%", fmtDur(c.estimate_s), p);
    } else {
      pct.textContent = "";
    }
  }
  for (const [tid, el] of cardEls) {
    if (!seen.has(tid)) { el.remove(); cardEls.delete(tid); }
  }

  // 排队条
  const qr = $("#queue-row");
  qr.classList.toggle("hidden", !d.queued.length);
  $("#queue-chips").innerHTML = d.queued.map((q) =>
    '<span class="chip" title="' + esc(q.title) + '">' + esc(q.title) + "</span>").join("");
}

function renderTrend(trend) {
  const box = $("#trend");
  const max = Math.max(1, ...trend.map((t) => t.tokens || 0));
  box.innerHTML = trend.map((t) => {
    const h = Math.round((t.tokens || 0) / max * 100);
    const label = String(t.day || "").slice(5);   // MM-DD
    return '<div class="bar' + (t.tokens ? "" : " zero") + '" title="' + esc(t.day) + " · " +
      fmtTokens(t.tokens) + '"><i style="height:' + Math.max(h, 3) + '%"></i><b>' + esc(label) + "</b></div>";
  }).join("");
}

function renderModels(models) {
  const box = $("#models");
  if (!models.length) { box.innerHTML = '<div class="empty">' + t("暂无调用记录") + "</div>"; return; }
  const max = Math.max(1, ...models.map((m) => m.tokens || 0));
  box.innerHTML = models.map((m) =>
    '<div class="model-row"><span class="nm" title="' + esc(m.model) + '">' + esc(m.model) + "</span>" +
    '<span class="tk"><i style="width:' + Math.max(4, Math.round((m.tokens || 0) / max * 100)) + '%"></i></span>' +
    '<span class="vl">' + fmtTokens(m.tokens) + "</span></div>").join("");
}

function renderRecent(recent) {
  const box = $("#recent");
  if (!recent.length) { box.innerHTML = '<div class="empty">' + t("还没有已完成的运行") + "</div>"; return; }
  const dot = { done: "done", failed: "failed", cancelled: "cancelled" };
  box.innerHTML = recent.map((r) => {
    const cls = dot[r.status] || "cancelled";
    const t = String(r.ended_at || "").slice(11, 16);
    return '<div class="r-row"><span class="r-dot ' + cls + '"></span>' +
      '<span class="r-t' + (r.status === "failed" ? " failed" : "") + '" title="' +
      esc(r.title + (r.error ? " · " + r.error : "")) + '">' + esc(r.title) + "</span>" +
      '<span class="r-d">' + (r.duration_s != null ? fmtDur(r.duration_s) : "—") + " · " + esc(t) + "</span></div>";
  }).join("");
}

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
