/* 过签体检卡（P2-A，2026-11-04）
 *
 * 为什么做在右栏：闸门只在「按发布」那一刻生效，挡不住作者把稿子拷到平台网页
 * 手动投（P0-A 实测结论③）。作者写完几章就该看得见「哪一章没给读者东西」，
 * 所以结论挂在详情页右栏常驻面板里，不用去发布卡上找。
 *
 * 与 app.js 的关系：独立覆盖层，只读 S.detailTaskKey / S.lastRun 与全局
 * openModal / closeModal / toast，**不改主逻辑**（并行会话常整文件重写 app.js）。
 * 数据只走 `GET /api/tasks/<id>/signing-check`，现算不缓存。
 *
 * 三条纪律：
 *   ① 卡片只建一次，之后改文本不改节点——按下与抬起之间换节点，点击会丢（血账）。
 *   ② 判不了（unknown）也说一句，但不说成「通过」：绿标在判不了时等于骗人。
 *   ③ 弹层里不出现正文，只出现章号 / 字数 / 获得处数与判据原话。
 */
(function () {
  "use strict";
  if (window.__scMounted) return;
  window.__scMounted = true;

  // 透传全部参数：词条里带 {0}/{1} 占位（「查了 {0} 章」），只传 key 会把占位留在界面上。
  var t = function (s) {
    try {
      return typeof window.t === "function"
        ? window.t.apply(null, arguments) : s;
    } catch (e) { return s; }
  };
  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ESC[c]; });
  };

  var VERDICT_TEXT = {
    blocked: "有拦截",
    warn: "有短板",
    pass: "通过",
    unknown: "判不了"
  };
  var STATUS_TEXT = { ok: "有获得", empty: "没给东西", short: "偏短" };
  /* 空窗但没被拦（门禁外单章）：说成「没给东西」会让人以为跟被拦的章一样要命，
     所以行级文案与配色都分开——见 CSS 的 [data-status="empty"][data-blocked="false"]。 */
  var STATUS_TEXT_SOFT = "单章空窗";
  var POLL_MS = 1500;        // 详情页开着的轮询：只读两个变量，不请求
  var REFRESH_MS = 30000;    // 同一任务的重拉间隔（写完新章最迟半分钟反映）

  var card = null, dotEl = null, nameEl = null, badgeEl = null, subEl = null;
  var lastKey = "", lastAt = 0, busy = false, current = null;

  /* —— 当前详情任务：与 app.js 的 rdTaskId() 同口径 —— */
  function detailTaskId() {
    try {
      if (typeof S === "undefined" || !S) return "";
      var run = S.lastRun || null;
      return String(S.detailTaskKey || (run && run.task_id) || "");
    } catch (e) { return ""; }
  }

  function detailOpen() {
    var rd = document.getElementById("run-detail");
    return !!rd && !rd.classList.contains("hidden") && !rd.classList.contains("hidden-soft");
  }

  function load(taskId) {
    var path = "/api/tasks/" + encodeURIComponent(taskId) + "/signing-check";
    if (typeof api === "function") return api(path);
    return fetch(path).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }

  /* —— 卡片：建一次，之后只改文本 —— */
  function ensureCard() {
    var host = document.getElementById("rd-tabs-more");
    if (!host) return null;
    if (card && card.isConnected && card.parentNode === host) return card;
    if (!card) {
      card = document.createElement("div");
      card.id = "sc-card";
      card.className = "sc-card";
      card.setAttribute("data-verdict", "unknown");
      var head = document.createElement("button");
      head.type = "button";
      head.className = "sc-head";
      head.addEventListener("click", function () { window.openSigningCheck(); });
      dotEl = document.createElement("span");
      dotEl.className = "sc-dot";
      dotEl.setAttribute("aria-hidden", "true");
      nameEl = document.createElement("span");
      nameEl.className = "sc-name";
      nameEl.textContent = t("过签体检");
      badgeEl = document.createElement("span");
      badgeEl.className = "sc-badge";
      head.appendChild(dotEl);
      head.appendChild(nameEl);
      head.appendChild(badgeEl);
      subEl = document.createElement("div");
      subEl.className = "sc-sub";
      card.appendChild(head);
      card.appendChild(subEl);
    }
    var shelf = host.querySelector("#rd-actions-shelf");
    if (shelf && shelf.parentNode === host) host.insertBefore(card, shelf);
    else host.appendChild(card);
    return card;
  }

  function paint(data) {
    current = data;
    var verdict = String((data && data.verdict) || "unknown");
    // 不是长篇/连载：这类任务占库里绝大多数，每个都挂一行「判不了」就是噪音。
    // 其余三种判不了（还没写稿 / 没目录 / 认不出题材）对小说任务有信息量，照样显示。
    if (verdict === "unknown" && String((data && data.reason) || "") === "not_serial") {
      // 只摘节点、不清 lastKey：清了会让 tick 每 1.5s 重拉同一个任务。
      if (card && card.parentNode) card.parentNode.removeChild(card);
      return;
    }
    if (!ensureCard()) return;
    card.setAttribute("data-verdict", verdict);
    badgeEl.textContent = String((data && data.headline) || VERDICT_TEXT[verdict] || "");
    nameEl.title = VERDICT_TEXT[verdict] || "";
    if (verdict === "unknown") {
      subEl.textContent = String((data && data.reason_text) || "");
      return;
    }
    var parts = [];
    if (data.genre_name) parts.push(String(data.genre_name));
    parts.push(t("查了 {0} 章", (data.chapters || []).length));
    subEl.textContent = parts.join(" · ");
  }

  function clearCard() {
    if (card && card.parentNode) card.parentNode.removeChild(card);
    current = null;
    lastKey = "";
  }

  function tick() {
    if (!detailOpen()) { clearCard(); return; }
    var id = detailTaskId();
    if (!id) { clearCard(); return; }
    if (busy) return;
    var fresh = id !== lastKey;
    if (!fresh && Date.now() - lastAt < REFRESH_MS) return;
    busy = true;
    load(id).then(function (data) {
      busy = false;
      lastKey = id;
      lastAt = Date.now();
      paint(data || {});
    }, function () {
      // 拉不到就什么都不说：体检是锦上添花，不能因为它报错打扰干活的人。
      busy = false;
      lastKey = id;
      lastAt = Date.now();
    });
  }

  /* —— 弹层：逐章一行 + 拦截项 + 短板 + 没人替你查的承诺 —— */
  function chapterRow(row) {
    var status = String(row.status || "ok");
    var gains = Number(row.gains || 0);
    var blocked = row.blocked === true;
    var label = (status === "empty" && !blocked)
      ? STATUS_TEXT_SOFT : (STATUS_TEXT[status] || status);
    return '<div class="sc-row" data-status="' + esc(status) +
      '" data-blocked="' + (blocked ? "true" : "false") + '">' +
      '<span class="sc-ch">' + esc(t("第 {0} 章", row.chapter)) + "</span>" +
      '<span class="sc-w">' + esc(t("{0} 字", row.words || 0)) + "</span>" +
      '<span class="sc-g">' + esc(gains ? t("{0} 处获得", gains) : t("0 处获得")) + "</span>" +
      '<span class="sc-st">' + esc(t(label)) + "</span>" +
      (row.note ? '<span class="sc-note">' + esc(row.note) + "</span>" : "") +
      "</div>";
  }

  function block(title, items, cls) {
    if (!items || !items.length) return "";
    return '<div class="sc-block ' + (cls || "") + '">' +
      '<div class="sc-h">' + esc(title) + "</div>" +
      items.map(function (x) { return '<div class="sc-li">' + esc(x) + "</div>"; }).join("") +
      "</div>";
  }

  function reportHtml(data) {
    var verdict = String(data.verdict || "unknown");
    if (verdict === "unknown") {
      return '<div class="sc-lead">' + esc(data.reason_text || t("这本还判不了")) + "</div>" +
        '<div class="sc-meta">' + esc(t("过签体检只对长篇 / 连载任务跑题材判据；别的类型不硬套")) +
        "</div>";
    }
    var rows = (data.chapters || []).map(chapterRow).join("");
    var meta = [
      data.genre_name ? String(data.genre_name) : "",
      esc(t("门禁 {0} 章", data.gate || 3)),
      esc(t("字数线 {0} 字", data.min_words || 2000))
    ].filter(Boolean).join(" · ");
    return '<div class="sc-lead">' + esc(data.headline || "") + "</div>" +
      '<div class="sc-meta">' + meta + "</div>" +
      '<div class="sc-rows">' + rows + "</div>" +
      block(t("会被拦的地方"), data.blockers, "is-bad") +
      block(t("短板（不拦，但读者能感觉到）"), data.warnings, "is-warn") +
      block(t("这几条没有机器判据，得你自己看"), data.unjudged, "is-muted") +
      '<div class="sc-foot-note">' + esc(t("体检只判「有没有给读者东西」与字数；文笔、人物、节奏由人判断")) +
      "</div>";
  }

  window.openSigningCheck = function () {
    var data = current;
    var id = detailTaskId();
    if (!data || (lastKey && id && lastKey !== id)) {
      // 卡片还没拉到就点：现拉一次，别让按钮点了没反应。
      if (!id) return;
      load(id).then(function (next) {
        lastKey = id; lastAt = Date.now(); paint(next || {});
        window.openSigningCheck();
      }, function () {
        if (typeof toast === "function") toast(t("体检报告加载失败"), true);
      });
      return;
    }
    var foot = "";
    if (data.verdict === "blocked" && typeof window.openRevisionBrief === "function") {
      foot += '<button class="ghost" onclick="scOpenBrief()">' + esc(t("看改稿单")) + "</button>";
    }
    foot += '<button class="primary" onclick="closeModal()">' + esc(t("知道了")) + "</button>";
    if (typeof openModal === "function") {
      openModal(t("过签体检"), reportHtml(data), foot, "sc-modal");
    }
  };

  window.scOpenBrief = function () {
    var id = detailTaskId();
    if (!id || typeof window.openRevisionBrief !== "function") return;
    if (typeof closeModal === "function") closeModal();
    window.openRevisionBrief(id);
  };

  setInterval(tick, POLL_MS);
  if (document.readyState !== "loading") setTimeout(tick, 600);
  else document.addEventListener("DOMContentLoaded", function () { setTimeout(tick, 600); });
})();
