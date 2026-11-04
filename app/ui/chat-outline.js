/* ============================================================================
 * 对话大纲覆盖层（chat-outline.js，2026-11-04）
 * ============================================================================
 * 参考桌面端观感：对话左缘一条圆点轨道，每个圆点 = 本轮对话里一条用户发言；
 * 悬停展开历史提问列表，点击任意一条平滑滚到那句话并闪一下定位。
 * 为什么是独立文件：设计宪法 §9——新交互落独立覆盖层，不碰共享检出的 app.js。
 * 依赖（app.js 提供）：t（可选）。不依赖其它全局；DOM 真源：
 *   #rd-chat（宿主列）> #rd-chat-flow（滚动区）> .chat-row.me（用户消息行，
 *   气泡 .chat-bubble.me）。who=本机的系统注记渲染成 .chat-sysnote-group，
 *   不会被算进来。
 * 样式在 chat-outline.css；i18n 词条在 i18n.js。
 * 血账对齐：
 *   - 按下与抬起之间不许重建被点的节点 → 指纹不变一个字节都不写；
 *     pointerdown 期间到点的重建推迟到抬起之后。
 *   - 浮层弹出方向/高度不靠布局猜，开面板时用宿主实测高度钳位。
 * -------------------------------------------------------------------------- */
(function () {
  "use strict";

  const t = (s) => { try { return typeof window.t === "function" ? window.t(s) : s; } catch (e) { return s; } };
  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let host = null, rail = null, panel = null;
  let rowsSig = "";
  let downCount = 0;          // rail 上按住的指针数：>0 时不重建（点击保护）
  let hideTimer = 0, retryTimer = 0;
  let flashTimer = 0, flashRow = null;

  function meRows() {
    const flow = document.getElementById("rd-chat-flow");
    if (!flow) return [];
    return Array.from(flow.querySelectorAll(":scope > .chat-row.me")).map((row) => {
      const bubble = row.querySelector(".chat-bubble.me");
      const text = String(bubble ? bubble.textContent : "").replace(/\s+/g, " ").trim();
      return { el: row, text: text.slice(0, 90) };
    });
  }

  function rowsFingerprint(rows) {
    return rows.length + "|" + rows.map((r) => r.text).join("\u0001");
  }

  /* —— 轨道：圆点 = 用户发言，等距排布，当前段加宽成短横 —— */

  function buildDots(rows) {
    if (!rail) return;
    const fp = rowsFingerprint(rows);
    if (fp === rowsSig) return;          // 指纹没变一个字节都不写（点击保护）
    if (downCount > 0) {                 // 正按着：推迟到抬起，别把按下的那颗换掉
      if (!retryTimer) retryTimer = setTimeout(() => { retryTimer = 0; scheduleRebuild(true); }, 350);
      return;
    }
    rowsSig = fp;
    rail.innerHTML = rows.map((r, i) =>
      '<button type="button" class="rd-outline-dot" data-i="' + i + '" title="' +
      esc(r.text.slice(0, 60)) + '" aria-label="' + esc(t("对话大纲")) + ' ' + (i + 1) + '"></button>'
    ).join("");
    rail.classList.toggle("hidden", rows.length < 2);   // 摘掉初始 hidden：有内容才亮轨道
  }

  /* —— 当前段：视口上部 35% 上方最近的一条用户发言 —— */

  function activeIndex(rows) {
    const flow = document.getElementById("rd-chat-flow");
    if (!flow || !rows.length) return -1;
    const top = flow.getBoundingClientRect().top;
    const line = top + flow.clientHeight * 0.35;
    let act = -1;
    rows.forEach((r, i) => {
      const rt = r.el.getBoundingClientRect().top;
      if (rt <= line) act = i;
    });
    // 贴底 = 一定在看最后一段之后的内容（最后一条发言可能在 35% 线以下）；
    // 全在线下 = 还没滚过第一条。两个极端各补一次，别让当前段空标。
    if (flow.scrollTop + flow.clientHeight >= flow.scrollHeight - 4) return rows.length - 1;
    if (act === -1) return 0;
    return act;
  }

  function paintActive() {
    if (!rail || rail.classList.contains("hidden")) return;
    const rows = meRows();
    if (!rows.length) return;
    const act = activeIndex(rows);
    const dots = rail.children;
    for (let i = 0; i < dots.length; i++) {
      const want = i === act;
      if (want !== dots[i].classList.contains("is-active")) dots[i].classList.toggle("is-active", want);
    }
    if (panel && !panel.classList.contains("hidden")) {
      const items = panel.children;
      for (let i = 0; i < items.length; i++) {
        if (!items[i].dataset || items[i].dataset.i == null) continue;
        items[i].classList.toggle("is-active", Number(items[i].dataset.i) === act);
      }
    }
  }

  /* —— 跳转：滚动 + 目标气泡闪一圈 —— */

  function jump(i) {
    const rows = meRows();
    const r = rows[i];
    if (!r) return;
    r.el.scrollIntoView({ block: "center", behavior: "smooth" });
    if (flashRow) { flashRow.classList.remove("rd-outline-flash"); flashRow = null; }
    if (flashTimer) { clearTimeout(flashTimer); flashTimer = 0; }
    flashRow = r.el;
    r.el.classList.add("rd-outline-flash");
    flashTimer = setTimeout(() => {
      if (flashRow) flashRow.classList.remove("rd-outline-flash");
      flashRow = null; flashTimer = 0;
    }, 1400);
    closePanel();
  }

  /* —— 悬停面板：历史提问一行一条 —— */

  function openPanel() {
    if (!panel || !host || host.classList.contains("hidden")) return;
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = 0; }
    const rows = meRows();
    if (rows.length < 2) return;
    panel.innerHTML = rows.map((r, i) =>
      '<button type="button" class="rd-outline-item" data-i="' + i + '">' +
      '<span class="rd-outline-n">' + (i + 1) + '</span>' +
      '<span class="rd-outline-t">' + esc(r.text || "…") + '</span></button>'
    ).join("");
    panel.style.maxHeight = Math.max(180, host.clientHeight - 10) + "px";   // 实测钳位，不猜视口
    panel.classList.remove("hidden");
    paintActive();
  }

  function scheduleHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(closePanel, 260);
  }

  function closePanel() {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = 0; }
    if (panel) panel.classList.add("hidden");
  }

  /* —— 显隐：对话可见 + 至少两条用户发言才有资格占注意力 —— */

  function syncVisible(rows) {
    if (!host) return;
    const chat = document.getElementById("rd-chat");
    const flow = document.getElementById("rd-chat-flow");
    const show = !!(chat && flow && !chat.classList.contains("hidden") &&
      rows.length >= 2 && window.innerWidth > 900);
    host.classList.toggle("hidden", !show);
    if (!show) closePanel();
  }

  function scheduleRebuild(force) {
    if (force && retryTimer) { clearTimeout(retryTimer); retryTimer = 0; }
    const rows = meRows();
    buildDots(rows);
    syncVisible(rows);
    paintActive();
  }

  function ensureHost() {
    const chat = document.getElementById("rd-chat");
    if (!chat) return false;
    if (host && host.isConnected) return true;
    host = document.createElement("div");
    host.id = "rd-outline";
    host.className = "hidden";
    host.setAttribute("role", "navigation");
    host.setAttribute("aria-label", t("对话大纲"));
    rail = document.createElement("div");
    rail.className = "rd-outline-rail";
    rail.classList.add("hidden");
    panel = document.createElement("div");
    panel.className = "rd-outline-panel hidden";
    host.appendChild(rail);
    host.appendChild(panel);
    chat.appendChild(host);

    rail.addEventListener("pointerdown", () => { downCount++; });
    const up = () => { if (downCount > 0) { downCount--; scheduleRebuild(true); } };
    rail.addEventListener("pointerup", up);
    rail.addEventListener("pointercancel", up);
    rail.addEventListener("mouseenter", openPanel);
    rail.addEventListener("mouseleave", scheduleHide);
    rail.addEventListener("click", (e) => {
      const dot = e.target.closest(".rd-outline-dot");
      if (dot) jump(Number(dot.dataset.i));
    });
    panel.addEventListener("mouseenter", () => { if (hideTimer) { clearTimeout(hideTimer); hideTimer = 0; } });
    panel.addEventListener("mouseleave", scheduleHide);
    panel.addEventListener("click", (e) => {
      const item = e.target.closest(".rd-outline-item");
      if (item) jump(Number(item.dataset.i));
    });

    const flow = document.getElementById("rd-chat-flow");
    if (flow) {
      flow.addEventListener("scroll", paintActive, { passive: true });
      new MutationObserver(() => scheduleRebuild()).observe(flow, { childList: true });
    }
    window.addEventListener("resize", () => scheduleRebuild());
    return true;
  }

  function boot() {
    if (!ensureHost()) return;
    scheduleRebuild();
    // app.js 的轮询与页签切换不走 DOM 事件兜不到的情形，低频对账一次
    setInterval(() => scheduleRebuild(), 4000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
