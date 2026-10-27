/* 欢迎页：让用户在进产品之前先选形态。
   数据全部来自 /api/profiles，新增一个 data/profiles/*.json 就会多一行，
   这里不需要改代码。视觉按 v2 设计系统（app/ui/v2 三件套）渲染成
   demo 定稿的「入口行」样式。 */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  /* 卡片顺序按「多数人第一眼要做什么」排，不按 order 字段：
     default 的 order=0，排第一会变成「什么都有的那个」先出现，
     而新人第一眼需要的是「我要干什么」。 */
  var ORDER = ["novel", "dossier", "default", "hive"];

  var INTENT = {
    novel: "写小说、短篇或连载",
    dossier: "交标书、方案、周报、合同",
    default: "写代码，或者样样都要",
    hive: "四个工作台都想看看"
  };

  var TAG = {
    novel: "创作台",
    dossier: "成稿台",
    default: "全量 21 个工种",
    hive: "四原型分 tab"
  };

  var ICON = {
    novel: "i-pen",
    dossier: "i-doc",
    default: "i-code",
    hive: "i-hive"
  };

  var MAX_FLOWS = 3;

  var state = { profiles: [], last: "", busy: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function msg(text, bad) {
    var box = $("msg");
    box.textContent = text || "";
    box.className = bad ? "form-msg bad" : "form-msg";
  }

  async function api(path, opt) {
    var res = await fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" }
    }, opt || {}));
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      throw new Error((data && data.error) || ("请求失败（" + res.status + "）"));
    }
    return data || {};
  }

  function sortRows(rows) {
    var rank = {};
    ORDER.forEach(function (id, i) { rank[id] = i; });
    return rows.slice().sort(function (a, b) {
      var ra = rank[a.id] === undefined ? 90 : rank[a.id];
      var rb = rank[b.id] === undefined ? 90 : rank[b.id];
      if (ra !== rb) return ra - rb;
      return (a.order || 0) - (b.order || 0);
    });
  }

  /* 一行入口的描述：意图在前，工种清单收尾做佐证。 */
  function desc(p) {
    var parts = [INTENT[p.id] || "这个版本里的全部工种"];
    var names = (p.flow_names || []).slice(0, MAX_FLOWS);
    if (names.length) {
      parts.push(names.join(" / "));
    }
    return parts.join(" · ");
  }

  function entry(p, index) {
    var el = document.createElement("button");
    el.type = "button";
    el.className = "entry";
    el.setAttribute("role", "listitem");
    /* 高亮跟着「用户上次的选择」走，不跟进程默认值走：
       首次打开时 active 一定是 default，若照它高亮，会让人以为
       「全量版是我上次选的」。 */
    var chosen = !!(state.last && state.last === p.id);
    el.setAttribute("aria-current", chosen ? "true" : "false");
    el.dataset.id = p.id;

    var tagText = chosen ? "上次用这个" : (TAG[p.id] || (p.flow_count ? p.flow_count + " 个工种" : "自定义形态"));

    el.innerHTML =
      '<span class="entry-ico"><svg viewBox="0 0 24 24"><use href="#' +
        (ICON[p.id] || "i-hive") + '"/></svg></span>' +
      '<span class="entry-main">' +
        '<span class="entry-name">' + esc(p.name) +
          '<span class="tag' + (chosen ? " tag-accent" : "") + '">' + esc(tagText) + "</span>" +
        "</span>" +
        '<span class="entry-desc">' + esc(desc(p)) + "</span>" +
      "</span>" +
      '<span class="entry-go"><kbd class="kbd">' + (index + 1) +
        '</kbd><svg viewBox="0 0 24 24"><use href="#i-arrow"/></svg></span>';

    el.addEventListener("click", function () { enter(p); });
    return el;
  }

  function render() {
    var box = $("entries");
    box.innerHTML = "";
    state.profiles.forEach(function (p, i) {
      box.appendChild(entry(p, i));
    });
  }

  async function enter(p) {
    if (state.busy) return;
    state.busy = true;
    msg("正在打开「" + p.name + "」…");
    try {
      await api("/api/profiles/active", {
        method: "POST",
        body: JSON.stringify({ id: p.id })
      });
      // 根路径由服务端按新形态给出入口页，前端不拼路径：
      // 形态的 ui 字段可以随时改，写死跳转就会失效。
      location.replace("/");
    } catch (err) {
      state.busy = false;
      msg(err.message || "切换失败", true);
    }
  }

  async function goSettings() {
    if (state.busy) return;
    var has = state.profiles.some(function (p) { return p.id === "default"; });
    if (!has) {
      msg("这个安装没有全量版形态", true);
      return;
    }
    state.busy = true;
    msg("正在切到全量版，设置在那里…");
    try {
      await api("/api/profiles/active", {
        method: "POST",
        body: JSON.stringify({ id: "default" })
      });
      location.replace("/");
    } catch (err) {
      state.busy = false;
      msg(err.message || "切换失败", true);
    }
  }

  function onKey(ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var n = parseInt(ev.key, 10);
    if (!n || n < 1 || n > state.profiles.length) return;
    enter(state.profiles[n - 1]);
  }

  function wireTheme() {
    var btn = $("theme-toggle");
    var ico = $("theme-ico");
    var root = document.documentElement;
    function paint() {
      var ink = root.getAttribute("data-surface") === "ink";
      ico.innerHTML = ink ? '<use href="#i-sun"/>' : '<use href="#i-moon"/>';
    }
    paint();
    btn.addEventListener("click", function () {
      var ink = root.getAttribute("data-surface") === "ink";
      var next = ink ? "paper" : "ink";
      root.setAttribute("data-surface", next);
      try { localStorage.setItem("wb.surface", next); } catch (e) { /* 忽略 */ }
      paint();
    });
  }

  async function init() {
    wireTheme();
    try {
      var data = await api("/api/profiles");
      state.profiles = sortRows(data.profiles || []);
      state.last = data.last || "";
      if (!state.profiles.length) {
        msg("没有可用的形态", true);
        return;
      }
      render();
      document.addEventListener("keydown", onKey);
      $("btn-settings").addEventListener("click", goSettings);
    } catch (err) {
      msg(err.message || "读取形态清单失败", true);
    }
  }

  init();
})();
