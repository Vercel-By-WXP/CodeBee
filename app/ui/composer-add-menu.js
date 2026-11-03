/* composer-add-menu.js（2026-11-03）—— 输入行「＋」菜单：一级分类 + 二级浮层
 *
 * 挂载方式：index.html 里在 app.js 之后 defer 加载。只做三件事——
 *   1. 渲染一级 6 行（添加文件 / 引用对话中的文件 / ─ / 模式 / 专家 / 技能 / 连接器）；
 *   2. 悬停或点击某行，在右侧张二级浮层（搜索 + 列表 + 底部动作）；
 *   3. 包装 window.cmpAddMenu，保证开合、外点关闭、Esc 关闭与旧逻辑一致。
 *
 * 边界（刻意为之）：
 *   - 数据只读既有接口：/api/flows（专家）、/api/capabilities（技能/连接器/智能体）、
 *     /api/dir/scan（工作区文件）；会话列表读 S.state.tasks（app.js 已有轮询）。
 *   - 动作全部复用 app.js 既有的点击委托（data-add-action / data-add-capability /
 *     data-add-chat / data-add-workspace-file）与 window.pickExpert，不重复实现一遍。
 *   - 不读 app.js 的函数体、不改它的 CSS 之外的实现；删掉本文件 + CSS + index.html
 *     里那段结构即回滚成旧的单卡弹层。
 */
(function () {
  "use strict";
  if (window.__cmpAddMenuMounted) return;
  window.__cmpAddMenuMounted = true;

  /* 一级菜单：与参考截图逐行对齐（顺序即视觉顺序） */
  var ROOT_ROWS = [
    { pane: "file", label: "添加文件", icon: "i-paperclip" },
    { pane: "chat", label: "引用对话中的文件", icon: "i-at" },
    { sep: true },
    { pane: "mode", label: "模式", icon: "i-feather" },
    { pane: "expert", label: "专家", icon: "i-headset" },
    { pane: "skill", label: "技能", icon: "i-sparkle" },
    { pane: "conn", label: "连接器", icon: "i-link" }
  ];

  var PANE_PH = {
    file: "搜索工作区文件",
    chat: "搜索会话",
    mode: "搜索模式",
    expert: "搜索专家",
    skill: "搜索技能",
    conn: "搜索连接器"
  };

  /* 「模式」二级：全部沿用旧弹层里 data-add-action 的动作，行为零改动 */
  var MODE_ROWS = [
    { action: "workflow", label: "工作流", note: "选择任务流程", icon: "i-workflow" },
    { action: "plan", label: "计划模式", note: "开启计划模式", icon: "i-tasks", toggle: true },
    { action: "draw", label: "绘图", note: "画草图作为附件", icon: "i-palette" },
    { action: "browser", label: "浏览器工作区", note: "在内置浏览器打开", icon: "i-browser" },
    { action: "goal", label: "目标", note: "多轮推进的长目标", icon: "i-baton" }
  ];

  var MAX_ROWS = 40;          // 单屏最多渲染行数（技能目录 200+，靠搜索收敛）
  var HOVER_GUARD = 260;      // 刚打开时忽略 mouseenter（菜单可能恰好出现在指针下）

  var st = {
    pane: "", q: "", openedAt: 0,
    flows: null, flowsP: null, flowsErr: "",
    caps: null, capsP: null, capsErr: "",
    files: [], fileDir: null, fileBusy: false, fileErr: "", fileP: null
  };

  function $(id) { return document.getElementById(id); }
  function tt(s) { return typeof window.t === "function" ? window.t(s) : String(s); }
  function es(s) {
    var v = s == null ? "" : String(s);
    if (typeof window.esc === "function") return window.esc(v);
    return v.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function icon(id, cls) {
    return '<svg class="ico' + (cls ? " " + cls : "") + '" aria-hidden="true"><use href="#' + id + '"></use></svg>';
  }

  /* ── 数据（懒加载 + 单飞，失败只影响本页签） ───────────────────────── */

  function ensureFlows() {
    if (st.flows || st.flowsP) return;
    var call = window.api ? window.api("/api/flows") : Promise.reject(new Error("no-api"));
    st.flowsP = call
      .then(function (r) { st.flows = (r && r.flows) || []; })
      .catch(function (e) { st.flowsErr = "读取专家名单失败"; st.flows = []; })
      .then(function () { st.flowsP = null; if (st.pane === "expert") renderList(); });
  }

  function ensureCaps() {
    if (st.caps || st.capsP) return;
    var call = window.api ? window.api("/api/capabilities", { timeout: 15000 }) : Promise.reject(new Error("no-api"));
    st.capsP = call
      .then(function (r) { st.caps = r || { plugins: [], skills: [], agents: [] }; })
      .catch(function (e) { st.capsErr = "读取能力目录失败"; st.caps = null; })
      .then(function () { st.capsP = null; if (st.pane === "skill" || st.pane === "conn") renderList(); });
  }

  function ensureFiles() {
    var dir = (($("f-workdir") || {}).value || "").trim();
    if (dir === st.fileDir && !st.fileP) return;
    st.fileDir = dir;
    st.files = [];
    st.fileErr = "";
    if (!dir) { st.fileBusy = false; return; }
    st.fileBusy = true;
    var call = window.api
      ? window.api("/api/dir/scan?path=" + encodeURIComponent(dir), { timeout: 15000 })
      : Promise.reject(new Error("no-api"));
    st.fileP = call
      .then(function (r) { st.files = (r && r.files) || []; })
      .catch(function (e) { st.fileErr = "读取工作区失败"; st.files = []; })
      .then(function () { st.fileP = null; st.fileBusy = false; if (st.pane === "file") renderList(); });
  }

  function tasks() {
    var S = window.S;
    return (S && S.state && S.state.tasks) || [];
  }

  function roster() {
    try { return typeof window.expertRoster === "function" ? window.expertRoster() : { has: function () { return false; } }; }
    catch (e) { return { has: function () { return false; } }; }
  }

  function selectedPlugins() {
    var S = window.S;
    return (S && S.cmpPlugins) || [];
  }

  /* ── 一级列表 ───────────────────────────────────────────────────── */

  function renderRoot() {
    var box = $("cmp-add-list");
    if (!box) return;
    box.innerHTML = ROOT_ROWS.map(function (row) {
      if (row.sep) return '<div class="cmp-add-sep" aria-hidden="true"></div>';
      var on = st.pane === row.pane;
      return '<button type="button" class="cmp-add-row' + (on ? " is-pane" : "") +
        '" data-cmp-pane="' + row.pane + '" role="menuitem" aria-haspopup="true" aria-expanded="' +
        (on ? "true" : "false") + '">' + icon(row.icon) + "<span>" + es(tt(row.label)) + "</span>" +
        icon("i-chevron-r", "cmp-add-row-chev") + "</button>";
    }).join("");
  }

  /* ── 二级列表 ───────────────────────────────────────────────────── */

  function match(text) {
    if (!st.q) return true;
    return String(text || "").toLowerCase().indexOf(st.q) >= 0;
  }

  function emptyHtml(msg) { return '<p class="cmp-fly-empty">' + es(msg) + "</p>"; }
  /* 每个页签返回 { list, foot }：list 进滚区，foot 钉在浮层底部（不进滚动） */
  function pane(list, foot) { return { list: list || "", foot: foot || "" }; }

  function fileRows() {
    if (!st.fileDir) return pane(emptyHtml(tt("选择工作目录后可引用文件")));
    if (st.fileBusy) return pane(emptyHtml(tt("正在读取工作区…")));
    var list = st.files.filter(function (f) { return match(f && f.name); }).slice(0, MAX_ROWS);
    var html = list.map(function (f) {
      return '<button type="button" class="cmp-fly-row" data-add-workspace-file="' + es(f.name) + '" role="menuitem">' +
        icon("i-file-text") + "<b>" + es(f.name) + "</b></button>";
    }).join("");
    if (!html) html = emptyHtml(st.fileErr ? tt(st.fileErr) : tt("没有匹配的内容"));
    var foot = '<button type="button" class="cmp-fly-row" data-cmp-pick="file">' + icon("i-folder") +
        "<b>" + es(tt("选择文件")) + "</b></button>" +
      '<button type="button" class="cmp-fly-row" data-cmp-pick="folder">' + icon("i-folder") +
        "<b>" + es(tt("选择文件夹")) + "</b></button>";
    return pane(html, foot);
  }

  function chatRows() {
    var rows = tasks().filter(function (t) { return match((t && t.title) + " " + (t && t.goal)); }).slice(0, MAX_ROWS);
    if (!rows.length) return pane(emptyHtml(tt("没有匹配的内容")));
    return pane(rows.map(function (t) {
      return '<button type="button" class="cmp-fly-row" data-add-chat="' + es(t.id) + '" role="menuitem">' +
        icon("i-chat") + "<b>" + es(t.title || t.id) + "</b></button>";
    }).join(""));
  }

  function modeRows() {
    var list = MODE_ROWS.filter(function (r) { return match(tt(r.label) + " " + tt(r.note)); });
    if (!list.length) return pane(emptyHtml(tt("没有匹配的内容")));
    return pane(list.map(function (r) {
      /* 计划模式那行保留旧钩子：cmpSyncPlanMode() 会给 [data-add-action=plan] 加 is-on、
         给 #cmp-plan-hint 写「已开启 / 开启计划模式」。 */
      return '<button type="button" class="cmp-fly-row' + (r.toggle ? " cmp-fly-plan" : "") +
        '" data-add-action="' + r.action + '" role="menuitem">' + icon(r.icon) + "<b>" + es(tt(r.label)) + "</b>" +
        (r.toggle ? '<i class="cmp-fly-note" id="cmp-plan-hint"></i><span class="cmp-add-check cmp-fly-check" id="cmp-plan-check" aria-hidden="true"></span>' : "") +
        "</button>";
    }).join(""));
  }

  function expertRows() {
    if (!st.flows) return pane(emptyHtml(st.flowsErr ? tt(st.flowsErr) : tt("正在读取专家名单…")));
    var fav = roster();
    var list = st.flows.filter(function (f) { return f && f.id; }).map(function (f) {
      var a = f.agent || {};
      return { id: f.id, name: String(a.name || f.name || f.id), focus: String(a.focus || ""), role: String(f.name || "") };
    });
    if (st.q) list = list.filter(function (x) { return match(x.name + " " + x.focus + " " + x.role + " " + x.id); });
    else list = list.filter(function (x) { return fav.has(x.id); })
      .concat(list.filter(function (x) { return !fav.has(x.id); }));
    if (!list.length) return pane(emptyHtml(tt("没有匹配的内容")));
    var html = list.slice(0, MAX_ROWS).map(function (x) {
      var av = typeof window.expertAvHtml === "function" ? window.expertAvHtml(tt(x.name), x.id) : "";
      return '<button type="button" class="cmp-fly-row cmp-fly-expert" data-cmp-expert="' + es(x.id) + '" role="menuitem">' +
        av + "<b>" + es(tt(x.name)) + "</b></button>";
    }).join("");
    var foot = '<button type="button" class="cmp-fly-row cmp-fly-more" data-cmp-more-experts="1" role="menuitem">' +
      "<b>" + es(tt("召唤更多专家")) + "</b>" + icon("i-arrow-up") + "</button>";
    return pane(html, foot);
  }

  /* 能力行：与旧渲染器同一套可用性判据（未安装 / 已停用 / 不适配 / 损坏） */
  function capState(row) {
    var state = row.state || "available";
    var incompatible = row.compatibility && row.compatibility.compatible === false;
    var disabled = row.installed !== true || row.enabled !== true || row.available === false ||
      incompatible || ["broken", "blocked"].indexOf(state) >= 0;
    var label = incompatible ? "不适配" : ["broken", "blocked"].indexOf(state) >= 0 ? "损坏"
      : row.installed !== true ? "未安装" : row.enabled !== true ? "已停用"
      : (row.unsupported || []).length ? "部分能力" : "已安装";
    return { disabled: disabled, label: label };
  }

  function capGroup(rows, kind) {
    var picked = selectedPlugins();
    return rows.filter(function (row) {
      return match((row.display_name || row.name || row.id) + " " + (row.short_description || row.description));
    }).filter(function (row) {
      return st.q || row.installed === true;   // 无搜索词时只列已装，避免把 200 条目录摊开
    }).sort(function (a, b) {
      return (b.installed === true) - (a.installed === true) ||
        String(a.display_name || a.name || "").localeCompare(String(b.display_name || b.name || ""), "zh");
    }).map(function (row) {
      var label = row.display_name || row.name || row.id;
      var state = capState(row);
      var on = picked.indexOf(row.id) >= 0;
      return '<button type="button" class="cmp-fly-row' + (on ? " is-on" : "") + '" data-add-capability="' + es(row.id) +
        '"' + (state.disabled ? ' disabled aria-disabled="true"' : "") + ' role="menuitem" title="' +
        es((row.compatibility || {}).reason || row.error || row.short_description || row.description || "") + '">' +
        icon(kind === "skill" ? "i-sparkle" : kind === "agent" ? "i-users" : "i-link") +
        "<b>" + es(tt(label)) + "</b>" +
        (state.disabled ? '<i class="cmp-fly-state">' + es(tt(state.label)) + "</i>" : "") +
        '<span class="cmp-add-check cmp-fly-check" aria-hidden="true"></span></button>';
    });
  }

  function skillRows() {
    if (!st.caps && st.capsErr) return pane(emptyHtml(tt(st.capsErr)));
    if (!st.caps) return pane(emptyHtml(tt("正在读取能力目录…")));
    var rows = capGroup(st.caps.skills || [], "skill");
    var total = (st.caps.skills || []).length;
    var html = rows.slice(0, MAX_ROWS).join("");
    if (!html) {
      return pane(emptyHtml(st.q ? tt("没有匹配的内容") : tt("还没有装技能，输入关键词可查全部 {0} 个").replace("{0}", total)));
    }
    /* 提示钉在底部（跟「召唤更多专家」同一位置语言），而不是滚到列表最后才看见 */
    var tip = rows.length > MAX_ROWS
      ? tt("还有 {0} 个，输入关键词继续找").replace("{0}", rows.length - MAX_ROWS)
      : (st.q ? "" : tt("共 {0} 个可用，输入关键词搜索全部").replace("{0}", total));
    return pane(html, tip ? '<p class="cmp-fly-tip">' + es(tip) + "</p>" : "");
  }

  function connRows() {
    if (!st.caps && st.capsErr) return pane(emptyHtml(tt(st.capsErr)));
    if (!st.caps) return pane(emptyHtml(tt("正在读取能力目录…")));
    var plugins = capGroup(st.caps.plugins || [], "plugin");
    var agents = capGroup(st.caps.agents || [], "agent");
    var html = "";
    if (plugins.length) html += plugins.slice(0, MAX_ROWS).join("");
    if (agents.length) {
      html += '<div class="cmp-fly-kicker">' + es(tt("智能体")) + "</div>" + agents.slice(0, MAX_ROWS).join("");
    }
    if (!html) return pane(emptyHtml(st.q ? tt("没有匹配的内容") : tt("还没有装连接器")));
    var total = (st.caps.plugins || []).length;
    var tip = st.q ? "" : tt("共 {0} 个可用，输入关键词搜索全部").replace("{0}", total);
    return pane(html, tip ? '<p class="cmp-fly-tip">' + es(tip) + "</p>" : "");
  }

  function paneHtml(key) {
    switch (key) {
      case "file": return fileRows();
      case "chat": return chatRows();
      case "mode": return modeRows();
      case "expert": return expertRows();
      case "skill": return skillRows();
      case "conn": return connRows();
      default: return pane("");
    }
  }

  function renderList() {
    var box = $("cmp-fly-list"), foot = $("cmp-fly-foot");
    if (!box || !st.pane) return;
    var out = paneHtml(st.pane) || { list: "", foot: "" };
    box.innerHTML = out.list;
    if (foot) {
      foot.innerHTML = out.foot || "";
      foot.classList.toggle("hidden", !out.foot);
    }
    clampFly();
    if (st.pane === "mode" && typeof window.cmpSyncPlanMode === "function") {
      try { window.cmpSyncPlanMode(); } catch (e) { /* 旧实现缺失不影响本层 */ }
    }
  }

  /* 高度钳位：浮层贴着一级卡片，卡片向上弹时底对齐、向下弹时顶对齐；
   * 无论哪种，都不能长出视口（主页输入卡在半空，向下弹只剩很少空间）。 */
  function clampFly() {
    var fly = $("cmp-add-flyout"), menu = $("cmp-add-menu"), btn = $("btn-attach");
    if (!fly || !menu || fly.classList.contains("hidden")) return;
    var m = menu.getBoundingClientRect();
    var b = btn ? btn.getBoundingClientRect() : null;
    var down = b ? (m.top >= b.top + b.height / 2) : false;
    var avail = down ? (window.innerHeight - m.top - 16) : (m.bottom - 16);
    fly.classList.toggle("is-down", down);
    fly.style.maxHeight = Math.max(200, Math.min(392, Math.round(avail))) + "px";
  }

  /* 一级卡片自身也可能放不下：主页输入卡贴得低，向下弹 271px 会顶出视口底部
   * （线上实测菜单 bottom 903 > 视口 900，最后一行「连接器」被切）。这里量一次
   * 上下可用空间，下面不够就整块翻到按钮上方（`.is-up`），不靠 CSS 猜方向。 */
  function placeMenu() {
    var menu = $("cmp-add-menu"), btn = $("btn-attach");
    if (!menu || !btn || menu.classList.contains("hidden")) return;
    menu.classList.remove("is-up");
    var b = btn.getBoundingClientRect();
    var h = menu.getBoundingClientRect().height;
    var below = window.innerHeight - b.bottom - 14;
    var above = b.top - 14;
    if (h > below && above > below) menu.classList.add("is-up");
    clampFly();
  }

  /* ── 开合 ───────────────────────────────────────────────────────── */

  function openPane(pane) {
    var fly = $("cmp-add-flyout"), wrap = document.querySelector(".cmp-add-wrap"), input = $("cmp-capability-search");
    if (!fly || !pane) return;
    var changed = st.pane !== pane;
    st.pane = pane;
    if (changed) { st.q = ""; if (input) input.value = ""; }
    if (input) {
      input.placeholder = tt(PANE_PH[pane] || "搜索");
      input.setAttribute("aria-label", tt(PANE_PH[pane] || "搜索"));
    }
    fly.classList.remove("hidden");
    if (wrap) wrap.classList.add("is-pane");
    renderRoot();
    if (pane === "expert") ensureFlows();
    if (pane === "skill" || pane === "conn") ensureCaps();
    if (pane === "file") ensureFiles();
    renderList();
  }

  function closePane() {
    st.pane = "";
    st.q = "";
    var fly = $("cmp-add-flyout"), wrap = document.querySelector(".cmp-add-wrap"), input = $("cmp-capability-search");
    if (fly) fly.classList.add("hidden");
    if (wrap) wrap.classList.remove("is-pane");
    if (input) input.value = "";
    renderRoot();
  }

  function bind() {
    var menu = $("cmp-add-menu");
    if (!menu || !$("cmp-add-flyout") || !$("cmp-add-list")) return false;

    renderRoot();

    /* 包装开合入口：app.js 里所有 cmpAddMenu(...) 调用（含外部点击 / Esc）都落到这里 */
    var orig = window.cmpAddMenu;
    window.cmpAddMenu = function (open) {
      var next = typeof open === "boolean" ? open : menu.classList.contains("hidden");
      if (typeof orig === "function") { try { orig.call(window, open); } catch (e) { /* 旧实现异常不挡新层 */ } }
      menu.classList.toggle("hidden", !next);
      var btn = $("btn-attach");
      if (btn) btn.setAttribute("aria-expanded", next ? "true" : "false");
      if (next) {
        st.openedAt = Date.now();
        ensureFlows();
        ensureCaps();
        placeMenu();
      } else {
        closePane();
        menu.classList.remove("is-up");
      }
    };

    menu.addEventListener("mouseover", function (event) {
      var row = event.target.closest("[data-cmp-pane]");
      if (!row || menu.classList.contains("hidden")) return;
      if (Date.now() - st.openedAt < HOVER_GUARD) return;   // 刚弹出时指针可能正压在行上
      if (st.pane === row.dataset.cmpPane) return;
      openPane(row.dataset.cmpPane);
    });

    menu.addEventListener("click", function (event) {
      var row = event.target.closest("[data-cmp-pane]");
      if (row) { openPane(row.dataset.cmpPane); return; }
      if (event.target.closest("[data-cmp-back]")) { closePane(); return; }
      var expert = event.target.closest("[data-cmp-expert]");
      if (expert) {
        window.cmpAddMenu(false);
        if (typeof window.pickExpert === "function") window.pickExpert(expert.dataset.cmpExpert);
        return;
      }
      if (event.target.closest("[data-cmp-more-experts]")) {
        window.cmpAddMenu(false);
        if (typeof window.switchTab === "function") window.switchTab("market", "main");
        if (typeof window.mkSetTab === "function") window.mkSetTab("expert");
        return;
      }
      var pick = event.target.closest("[data-cmp-pick]");
      if (pick) {
        /* 直接点隐藏的 file input：省掉旧版那个「选择文件 / 选择文件夹」二选一确认框 */
        var input = $(pick.dataset.cmpPick === "folder" ? "f-attach-folder" : "f-attach-file");
        window.cmpAddMenu(false);
        if (input) input.click();
      }
    });

    var search = $("cmp-capability-search");
    if (search) {
      search.addEventListener("input", function () {
        st.q = String(search.value || "").trim().toLowerCase();
        renderList();
      });
    }

    /* 语言切换后重绘（data-i18n 机制只改静态节点，动态列表要自己跟上） */
    var mo = new MutationObserver(function () { if (st.pane) renderList(); else renderRoot(); });
    var lang = document.documentElement;
    if (lang) mo.observe(lang, { attributes: true, attributeFilter: ["lang"] });

    /* 视口变化重算方向与高度钳位（主页输入卡位置随窗口变，可用空间也跟着变） */
    window.addEventListener("resize", function () { if (st.pane || !$("cmp-add-menu").classList.contains("hidden")) placeMenu(); });
    return true;
  }

  if (!bind()) {
    /* app.js 还没执行完（理论上不会：同为 defer 且本文件在后），退一步等 DOM 就绪 */
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
    else setTimeout(bind, 0);
  }
})();
