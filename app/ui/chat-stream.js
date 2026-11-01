/* ============================================================================
 * chat-stream.js —— 对话化①：主页「最近动态」流（2026-11-01，迭代计划决策 15）
 * ============================================================================
 * 方向拍板：对话是唯一入口、卡片是唯一表达、详情页是唯一下钻。本文件是第一
 * 阶段第一刀：主页 composer 之下挂一块「最近动态」，把任务动态（runs）与
 * 定时任务摘要以会话卡片形式内联出现；点击卡片下钻详情页/定时任务页。
 *
 * 为什么是独立 overlay：并行会话在持续重写 app.js，直接改会被冲掉
 * （memory-center.js / borrow-ui.js 同款纪律）。本文件在 app.js 之后加载，
 * 只依赖全局：$, t, esc, relTime, homeOpenRun, switchTab, authHeaders, S
 * （全部带 typeof 守卫，缺谁退回谁的能力，绝不抛错连累主页）。
 *
 * 分工纪律：注意力（「需要你处理」）不进本流——borrow-ui.js 的注意力条已占
 * 主页顶部，同一件事不说两遍；本流只讲「发生了什么」，不讲「你要做什么」。
 * 数据零额外负担：runs 直接读 S.state.runs（app.js 轮询已带回，零请求）；
 * 定时任务走 /api/automation，120 秒 TTL 缓存，拿不到就只显示 runs。
 * 空态整块隐藏（没事不打扰）；文案一律走 t()；样式在 chat-stream.css，
 * 全部消费 --wb-* token，不写死色。
 * ============================================================================ */
(function () {
  "use strict";

  const MAX_ROWS = 4;              // 流里最多几条 run 动态（卡片是摘要不是台账）
  const AUTO_TTL = 120000;         // 定时任务摘要缓存毫秒数
  let autoCache = null;
  let autoAt = 0;
  let refreshSeq = 0;              // 乱序 fetch 响应只认最新一轮

  const _t = (s) => (typeof window.t === "function" ? window.t(s) : s);
  const _esc = (s) =>
    typeof window.esc === "function" ? window.esc(s) : String(s == null ? "" : s);

  function runsList() {
    try {
      if (typeof S === "undefined" || !S || !S.state) return [];
      return ((S.state || {}).runs || []).slice(0, MAX_ROWS);
    } catch (e) {
      return [];
    }
  }

  /* 状态 → 色调/人话，与 app.js renderHome 同一套标签（不另造第二份口径） */
  function toneOf(r) {
    if (r.status === "running") return "run";
    if (r.status === "queued" || r.status === "cancelled") return "wait";
    if (r.status === "failed" || r.status === "timeout") return "bad";
    return "ok";
  }
  function labelOf(r) {
    if (r.status === "running") return _t("运行中");
    if (r.status === "queued") return _t("排队中");
    if (r.status === "failed") return _t("失败");
    if (r.status === "timeout") return _t("超时");
    if (r.status === "cancelled") return _t("已取消");
    return _t("已完成");
  }

  function runRow(r) {
    const total = r.step_count || 0;
    const step = (r.current_step && r.current_step.n) || 0;
    const prog = total ? " · " + _t("第 ") + step + "/" + total + _t(" 步") : "";
    const timeFn = typeof window.relTime === "function" ? window.relTime : null;
    const time = timeFn ? String(timeFn(r.created_at || r.started_at || "") || "") : "";
    return (
      '<button type="button" class="cs-row" data-run="' + _esc(r.id || "") + '">' +
      '<span class="cs-dot ' + toneOf(r) + '" aria-hidden="true"></span>' +
      '<span class="cs-text"><b>' + _esc(r.title || r.id || "") + "</b>" +
      '<span class="cs-sep">·</span>' + _esc(labelOf(r)) + _esc(prog) + "</span>" +
      (time ? '<span class="cs-time">' + _esc(time) + "</span>" : "") +
      "</button>"
    );
  }

  function autoRow(tasks) {
    const on = (tasks || []).filter((x) => x && x.enabled);
    if (!on.length) return "";
    const withNext = on.filter((x) => x && x.next_run)
      .sort((a, b) => String(a.next_run).localeCompare(String(b.next_run)));
    const next = withNext[0];
    let txt = _t("定时任务") + " " + on.length;
    if (next) txt += " · " + _t("下次运行 ") + _esc(String(next.next_run)) + " " + _esc(next.name || "");
    return (
      '<button type="button" class="cs-row cs-auto-row" data-tab="automation">' +
      '<span class="cs-dot auto" aria-hidden="true"></span>' +
      '<span class="cs-text">' + txt + "</span></button>"
    );
  }

  async function loadAuto() {
    const now = Date.now();
    if (autoCache && now - autoAt < AUTO_TTL) return autoCache;
    try {
      const headers =
        (typeof window.authHeaders === "function" ? window.authHeaders() : null) || {};
      const res = await fetch("/api/automation", { headers });
      const d = await res.json().catch(() => ({}));
      if (res.ok && d && Array.isArray(d.tasks)) {
        autoCache = d.tasks;
        autoAt = now;
      }
    } catch (e) { /* 拿不到就只显示 runs，静默 */ }
    return autoCache;
  }

  function sigOf(runs, tasks) {
    const a = (tasks || []).filter((x) => x && x.enabled)
      .map((x) => String(x.next_run || "") + ":" + String(x.name || "")).join("|");
    return runs.map((r) => r.id + ":" + r.status + ":" +
      ((r.current_step && r.current_step.n) || 0)).join("|") + "#" + a;
  }

  async function refresh() {
    const seq = ++refreshSeq;
    const box = document.getElementById("rd-chat-feed");
    if (!box) return;
    const runs = runsList();
    let tasks = autoCache;
    if (!tasks || Date.now() - autoAt >= AUTO_TTL) {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") {
        tasks = await loadAuto();
      }
    }
    if (seq !== refreshSeq) return;              // 已有更新一轮在渲染，丢弃旧响应
    const html = runs.map(runRow).join("") + autoRow(tasks);
    const sig = sigOf(runs, tasks);
    if (!html.trim()) {                          // 空态整块消失（没事不打扰）
      box.dataset.sig = "";
      box.classList.add("hidden");
      box.innerHTML = "";
      return;
    }
    if (box.dataset.sig === sig && !box.classList.contains("hidden")) return;
    box.dataset.sig = sig;
    box.classList.remove("hidden");
    box.innerHTML =
      '<div class="cs-head"><span>' + _esc(_t("最近动态")) + "</span></div>" +
      '<div class="cs-rows">' + html + "</div>";
  }

  function onRowClick(e) {
    const row = e.target && e.target.closest ? e.target.closest(".cs-row") : null;
    if (!row) return;
    try {
      if (row.dataset.run && typeof window.homeOpenRun === "function") {
        window.homeOpenRun(row.dataset.run);
      } else if (row.dataset.tab && typeof window.switchTab === "function") {
        window.switchTab(row.dataset.tab);
      }
    } catch (err) { /* 下钻失败不打扰 */ }
  }

  function ensureBox() {
    let box = document.getElementById("rd-chat-feed");
    if (box) return box;
    const anchor = document.querySelector("#page-home .rd-quick-start");
    if (!anchor || !anchor.parentElement) return null;   // 找不到锚点就不挂（非主页壳）
    box = document.createElement("div");
    box.id = "rd-chat-feed";
    box.className = "cs-feed hidden";
    box.addEventListener("click", onRowClick);
    anchor.parentElement.insertBefore(box, anchor);      // composer 之下、快速开始之上
    return box;
  }

  /* renderHome 由 app.js 在主页刷新链（state 轮询 / showHome）里调用；
   * 包一层让动态流跟着刷新，不改 app.js 一行。 */
  function wrapRenderHome() {
    const orig = window.renderHome;
    if (typeof orig !== "function" || orig.__csWrapped) return;
    const wrapped = function () {
      try {
        return orig.apply(this, arguments);
      } finally {
        try { ensureBox(); refresh(); } catch (e) { /* 静默 */ }
      }
    };
    wrapped.__csWrapped = true;
    window.renderHome = wrapped;
  }

  function boot() {
    try { ensureBox(); } catch (e) { /* 静默 */ }
    try { wrapRenderHome(); } catch (e) { /* 静默 */ }
    try { refresh(); } catch (e) { /* 静默 */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
