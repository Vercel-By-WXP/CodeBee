/* 欢迎页：让用户在进产品之前先选形态。
   数据全部来自 /api/profiles，新增一个 data/profiles/*.json 就会多一张卡，
   这里不需要改代码。 */

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

  var MAX_CHIPS = 5;

  var state = { profiles: [], last: "", busy: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function msg(text, bad) {
    var box = $("msg");
    box.textContent = text || "";
    box.className = bad ? "msg bad" : "msg";
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

  function chips(p) {
    var names = p.flow_names || [];
    var shown = names.slice(0, MAX_CHIPS);
    var rest = names.length - shown.length;
    var html = shown.map(function (n) {
      return '<span class="chip">' + esc(n) + "</span>";
    }).join("");
    if (rest > 0) {
      html += '<span class="chip more">+' + rest + "</span>";
    }
    if (!html) {
      html = '<span class="chip more">全部工种</span>';
    }
    return '<div class="chips">' + html + "</div>";
  }

  function card(p, index) {
    var el = document.createElement("button");
    el.type = "button";
    el.className = "card";
    el.setAttribute("role", "listitem");
    /* 高亮跟着「用户上次的选择」走，不跟进程默认值走：
       首次打开时 active 一定是 default，若照它高亮，会让人以为
       「全量版是我上次选的」。 */
    var chosen = !!(state.last && state.last === p.id);
    el.setAttribute("aria-current", chosen ? "true" : "false");
    el.dataset.id = p.id;

    var tagText = TAG[p.id] || (p.flow_count ? p.flow_count + " 个工种" : "自定义形态");
    var intent = INTENT[p.id] || "这个版本里的全部工种";
    var goText = chosen ? "继续 →" : "进入 →";

    el.innerHTML =
      '<div class="card-head">' +
        '<span class="card-key">' + (index + 1) + "</span>" +
        '<span class="card-name">' + esc(p.name) + "</span>" +
        '<span class="card-tag' + (chosen ? " on" : "") + '">' +
          (chosen ? "上次用这个" : esc(tagText)) + "</span>" +
      "</div>" +
      '<div class="card-intent">' + esc(intent) + "</div>" +
      (p.tagline ? '<div class="card-tagline">' + esc(p.tagline) + "</div>" : "") +
      chips(p) +
      '<div class="card-go">' + goText + "</div>";

    el.addEventListener("click", function () { enter(p); });
    return el;
  }

  function render() {
    var box = $("cards");
    box.innerHTML = "";
    state.profiles.forEach(function (p, i) {
      box.appendChild(card(p, i));
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

  async function init() {
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
