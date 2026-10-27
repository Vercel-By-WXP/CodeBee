/* 分析洞察台：只读看板。
   数据来自既有接口——/api/publish/stats（发布成功率，口径=只算上线动作）
   与 /api/runs（最近运行）。没有后端数据的板块在页面上明说「随后端开放」，
   不拿假数据顶位置。 */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function api(path) {
    var res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      throw new Error((data && data.error) || ("请求失败（" + res.status + "）"));
    }
    return data || {};
  }

  function pct(rate) {
    return rate == null ? "—" : Math.round(rate * 100) + "%";
  }

  function when(v) {
    if (!v) return "";
    var t = new Date(v);
    if (isNaN(t.getTime())) return String(v).slice(0, 10);
    var d = new Date();
    var diff = (d.getTime() - t.getTime()) / 1000;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    return (t.getMonth() + 1) + "-" + t.getDate();
  }

  /* ---------- KPI ---------- */

  function kpi(label, num, sub, cls) {
    return '<div class="kpi">' +
      '<div class="k-label">' + esc(label) + "</div>" +
      '<div class="k-num' + (cls ? " " + cls : "") + '">' + esc(num) + "</div>" +
      '<div class="k-sub">' + esc(sub || "") + "</div>" +
      "</div>";
  }

  function renderStats(s) {
    var live = s.live || {};
    var draft = s.draft || {};
    var goal = s.goal != null ? Math.round(s.goal * 100) + "%" : "95%";
    var rate = live.rate;
    var cls = rate == null ? "" : (rate >= (s.goal || 0.95) ? "is-good" : "is-bad");
    var rateSub = rate == null ? "还没有发布记录" : "目标 " + goal + " · 近 " + (s.days || 7) + " 天";
    var html =
      kpi("发布成功率（上线）", pct(rate), rateSub, cls) +
      kpi("上线成功", live.ok != null ? live.ok : "—",
          "尝试 " + (live.attempts != null ? live.attempts : 0) + " 次") +
      kpi("上线失败", live.failed != null ? live.failed : "—",
          (live.failed ? "看右侧失败清单" : "近 " + (s.days || 7) + " 天无失败"), live.failed ? "is-bad" : "") +
      kpi("存草稿", draft.attempts != null ? draft.attempts : "—",
          "草稿单算，不进成功率分母");
    $("kpis").innerHTML = html;
  }

  function renderFailures(list, days) {
    var box = $("failures");
    if (!list || !list.length) {
      box.innerHTML = '<div class="soon"><p class="s-title">近 ' + days + " 天没有发布失败</p>" +
        '<p class="s-body">有失败时这里给平台、章节与原话原因。</p></div>';
      return;
    }
    box.innerHTML = list.map(function (f) {
      var title = f.title || ("第 " + (f.chapter_no || "?") + " 章");
      return '<div class="row">' +
        '<span class="dot is-bad"></span>' +
        '<span class="r-main">' +
          '<div class="r-title">' + esc(title) + "</div>" +
          '<div class="r-sub">' + esc(f.platform || "未知平台") + " · " + esc(f.action || "") +
            (f.error ? " · " + esc(f.error) : "") + "</div>" +
        "</span>" +
        '<span class="r-when">' + esc(when(f.ts)) + "</span>" +
        "</div>";
    }).join("");
  }

  function runDot(status) {
    var s = String(status || "").toLowerCase();
    if (s === "ok" || s === "done" || s === "completed" || s === "success" || s === "finished") return "is-ok";
    if (s === "failed" || s === "error" || s === "cancelled" || s === "canceled") return "is-bad";
    if (s === "running" || s === "pending" || s === "queued") return "is-run";
    return "";
  }

  function runTitle(r) {
    return r.name || r.title || r.flow || r.type || r.task_id || r.id || "（未命名）";
  }

  function renderRuns(runs) {
    var box = $("runs");
    if (!runs || !runs.length) {
      box.innerHTML = '<div class="soon"><p class="s-title">还没有运行记录</p>' +
        '<p class="s-body">从创作台或成稿台发起任务后，这里显示它的近况。</p></div>';
      return;
    }
    box.innerHTML = runs.slice(0, 12).map(function (r) {
      var st = String(r.status || "");
      return '<div class="row">' +
        '<span class="dot ' + runDot(st) + '"></span>' +
        '<span class="r-main">' +
          '<div class="r-title">' + esc(runTitle(r)) + "</div>" +
          '<div class="r-sub">' + esc(st || "未知状态") + (r.task_id ? " · " + esc(r.task_id) : "") + "</div>" +
        "</span>" +
        '<span class="r-when">' + esc(when(r.created_at || r.ts || r.time || r.updated_at)) + "</span>" +
        "</div>";
    }).join("");
  }

  /* ---------- 拉取 ---------- */

  var days = 7;

  async function loadStats() {
    renderFailures([], days);
    try {
      var s = await api("/api/publish/stats?days=" + days);
      renderStats(s);
      renderFailures(s.failures || [], s.days || days);
    } catch (err) {
      $("kpis").innerHTML = "";
      renderFailures([], days);
      $("failures").innerHTML = '<div class="err">成功率读取失败：' + esc(err.message) + "</div>";
    }
  }

  async function loadRuns() {
    try {
      var data = await api("/api/runs");
      renderRuns(data.runs || []);
    } catch (err) {
      $("runs").innerHTML = '<div class="err">运行清单读取失败：' + esc(err.message) + "</div>";
    }
  }

  $("days-seg").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button");
    if (!btn) return;
    days = parseInt(btn.getAttribute("data-days"), 10) || 7;
    Array.prototype.forEach.call($("days-seg").children, function (b) {
      b.className = b === btn ? "is-on" : "";
    });
    loadStats();
  });

  loadStats();
  loadRuns();
})();
