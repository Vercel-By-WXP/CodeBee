/* CodeBee 成稿台：文档/标书作者端。
 *
 * 只消费内核接口：/api/profile、/api/dossier/tasks、/api/dossier/task/<id>、
 * /api/dossier/task/<id>/export。要件表在后端 dossier.py，前端不维护第二份
 * 检查规则——否则改了一条检查项，页面和报告会各说各话。
 */
(function () {
  "use strict";

  var S = {
    profile: null,
    tasks: [],
    current: "",
    doc: null
  };

  /* ---------- 基础 ---------- */

  function $(id) { return document.getElementById(id); }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function api(path, opt) {
    opt = opt || {};
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, opt.timeout || 20000);
    try {
      var res = await fetch(path, {
        method: opt.method || "GET",
        headers: opt.body ? { "Content-Type": "application/json" } : {},
        body: opt.body,
        signal: ctrl.signal,
        credentials: "same-origin"
      });
      var text = await res.text();
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
      if (!res.ok) throw new Error((data && data.error) || ("请求失败（" + res.status + "）"));
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  function toast(msg) {
    var el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.add("hidden"); }, 2600);
  }

  function statusText(status) {
    if (status === "done") return "已完成";
    if (status === "running" || status === "queued") return "进行中";
    if (status === "failed") return "卡住了";
    if (status === "paused") return "已暂停";
    return status || "草稿";
  }

  function num(n) {
    n = Number(n) || 0;
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  /* ---------- 侧栏 ---------- */

  function renderRail() {
    var list = S.tasks;
    if (!list.length) {
      $("rail-list").innerHTML = '<p class="rail-foot" style="border:0;margin:0;padding:6px 10px">还没有文档。</p>';
      $("rail-sub").textContent = "按时间倒序";
      return;
    }
    $("rail-list").innerHTML = list.map(function (t) {
      var words = t.known ? num(t.words) + " 字" : "未开始";
      return '<a class="rail-item' + (S.current === t.id ? " is-on" : "") +
        '" href="#/task/' + encodeURIComponent(t.id) + '">' +
        '<span class="t">' + esc(t.title) + "</span>" +
        '<span class="m"><span>' + esc(t.flow_name) + "</span>" +
        '<span class="c">' + esc(words) + "</span></span>" +
        "</a>";
    }).join("");
    $("rail-sub").textContent = list.length + " 份 · 按时间倒序";
  }

  /* ---------- 汇总 ---------- */

  function renderSummary(doc) {
    var s = doc.summary;
    var html = '<span class="state ' + esc(s.state) + '">' + esc(s.label) + "</span>";
    html += '<span class="stat">要件 <b>' + s.present + "</b> / " + s.total + " 已具备</span>";
    if (s.hard_missing) {
      html += '<span class="stat bad">废标风险 <b>' + s.hard_missing + "</b> 项</span>";
    } else if (s.missing) {
      html += '<span class="stat">建议补充 <b>' + s.missing + "</b> 项</span>";
    }
    if (!doc.has_body) {
      html += '<span class="stat">出了正文再核对，现在不做判断</span>';
    }
    $("summary").innerHTML = html;
  }

  /* ---------- 章节树 ---------- */

  function renderTree(doc) {
    var nodes = doc.outline || [];
    $("tree-note").textContent = nodes.length ? nodes.length + " 节" : "";
    if (!nodes.length) {
      $("tree").innerHTML = '<p class="chk-sub" style="padding:10px 8px">还没有正文，写完稿后这里会显示章节结构。</p>';
      return;
    }
    $("tree").innerHTML = nodes.map(function (n) {
      var lv = Math.max(1, Math.min(3, Number(n.level) || 1));
      return '<div class="tree-row lv' + lv + '" style="padding-left:' +
        (6 + (lv - 1) * 16) + 'px">' +
        '<span class="tt" title="' + esc(n.title) + '">' + esc(n.title) + "</span>" +
        '<span class="tw">' + num(n.words) + "</span></div>";
    }).join("");
  }

  /* ---------- 要件核对 ---------- */

  var STATUS_ICON = { present: "+", missing: "!", unknown: "·" };

  function renderChecklist(doc) {
    var items = doc.checklist || [];
    $("check-note").textContent = doc.has_body
      ? (doc.summary.present + "/" + doc.summary.total)
      : "待正文";
    if (!items.length) {
      $("checklist").innerHTML = '<p class="chk-sub" style="padding:10px 8px">这个流程没有登记要件表。</p>';
      return;
    }
    var groups = [];
    var byGroup = {};
    items.forEach(function (it) {
      if (!byGroup[it.group]) { byGroup[it.group] = []; groups.push(it.group); }
      byGroup[it.group].push(it);
    });

    var html = groups.map(function (g) {
      var rows = byGroup[g].map(function (it) {
        var cls = "chk is-" + it.status + (it.hard ? " is-hard" : "");
        var tag = "";
        if (it.status === "missing" && it.hard) tag = '<span class="tag hard">废标项</span>';
        else if (it.status === "missing") tag = '<span class="tag soft">建议</span>';
        else if (it.status === "present" && it.hard) tag = '<span class="tag ok">硬性</span>';

        var sub;
        if (it.status === "present") {
          sub = '<p class="chk-sub"><span class="q">命中</span> ' + esc(it.evidence || "") + "</p>";
        } else if (it.status === "missing") {
          sub = '<p class="chk-sub">全文未见「' + esc(it.label) + "」相关表述</p>";
        } else {
          sub = '<p class="chk-sub">还没有正文，暂不下结论</p>';
        }
        return '<div class="' + cls + '">' +
          '<span class="dot">' + (STATUS_ICON[it.status] || "·") + "</span>" +
          '<div class="chk-main"><div class="chk-top">' +
          '<span class="chk-label">' + esc(it.label) + "</span>" + tag + "</div>" +
          sub + "</div></div>";
      }).join("");
      return '<p class="group-title">' + esc(g) + "</p>" + rows;
    }).join("");

    $("checklist").innerHTML = html;
  }

  /* ---------- 文档视图 ---------- */

  function renderDoc(doc) {
    S.doc = doc;
    var t = doc.task;
    $("view-empty").classList.add("hidden");
    $("view-doc").classList.remove("hidden");

    $("doc-flow").textContent = doc.task.flow_name || doc.task.type;
    $("doc-title").textContent = t.title;
    $("doc-status").textContent = statusText(t.status);
    $("doc-words").textContent = doc.has_body
      ? "已写 " + num(doc.words) + " 字"
      : "还没有正文";
    $("doc-file").textContent = t.manuscript || "";

    var btn = $("btn-export");
    btn.disabled = !doc.export.available;
    btn.title = doc.export.available ? "导出 Markdown 定稿" : "还没有正文可导出";

    renderSummary(doc);
    renderTree(doc);
    renderChecklist(doc);
    document.title = t.title + " · 成稿台";
  }

  function renderEmpty() {
    S.doc = null;
    $("view-doc").classList.add("hidden");
    $("view-empty").classList.remove("hidden");
    document.title = "成稿台 · CodeBee";
  }

  /* ---------- 加载 ---------- */

  async function loadDoc(id) {
    if (!id) { renderEmpty(); return; }
    try {
      var doc = await api("/api/dossier/task/" + encodeURIComponent(id));
      S.current = id;
      renderRail();
      renderDoc(doc);
    } catch (e) {
      toast(e.message || "读不到这份文档");
      renderEmpty();
    }
  }

  async function refresh(force) {
    var dot = $("conn-dot");
    try {
      var pack = await Promise.all([api("/api/profile"), api("/api/dossier/tasks")]);
      S.profile = pack[0] || {};
      S.tasks = (pack[1] && pack[1].tasks) || [];
      dot.className = "conn-dot on";
      dot.title = "已连接";
      if (S.profile.name && S.profile.name !== "CodeBee") {
        $("brand-name").textContent = S.profile.name + " · 成稿台";
      }
    } catch (e) {
      dot.className = "conn-dot off";
      dot.title = "连不上内核";
      throw e;
    }
    renderRail();
    if (force && S.current) await loadDoc(S.current);
  }

  async function route() {
    var m = /^#\/task\/(.+)$/.exec(location.hash || "");
    if (m) {
      await loadDoc(decodeURIComponent(m[1]));
    } else if (S.tasks.length) {
      await loadDoc(S.tasks[0].id);
    } else {
      renderEmpty();
    }
  }

  async function boot() {
    try {
      await refresh();
      await route();
    } catch (e) {
      toast(e.message || "连不上内核");
    }

    $("btn-refresh").addEventListener("click", function () {
      refresh(true).then(function () { toast("已刷新"); }).catch(function () {});
    });
    $("btn-export").addEventListener("click", function () {
      if (!S.current || !S.doc || !S.doc.export.available) return;
      window.location.href = "/api/dossier/task/" + encodeURIComponent(S.current) + "/export";
    });
    window.addEventListener("hashchange", function () { route(); });
  }

  boot();
})();
