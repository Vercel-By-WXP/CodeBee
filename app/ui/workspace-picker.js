/* workspace-picker.js（2026-11-03）—— 输入卡底部「工作空间」选择面板
 *
 * 用户给了两张桌面端观感截图：
 *   ① 工作区选择面板：顶部搜索框 + 工作空间列表（文件夹图标 + 名称 + 当前项打勾）
 *      + 分隔线 + 三行动作（＋ 新建工作空间 / 打开本地文件夹 / 不使用工作空间）；
 *   ② 「新建工作空间」命名弹框：一句说明（本地将自动创建同名文件夹）+ 名称输入框
 *      + 取消 / 确认。
 *   ——原话：「我们的选择文件夹也按照附件样式做」。
 *
 * 旧形态（app.js 的 toggleWdMenu）：一张 300px 的卡里直接铺 5 条最近目录 +
 * 「浏览本机目录…」「用默认路径」，没有搜索、没有「新建」、当前项只靠字色区分。
 *
 * 三条硬约束：
 *   1. **不新增第二份真相**：数据全部现取——当前值 `#f-workdir`、任务表里的
 *      workdir（与侧栏文件夹同源）、`settings.default_workdir_effective`
 *      （与 `/api/settings` 同源）。前端不另存「工作空间清单」。
 *   2. **不碰 app.js**：只用覆盖层接管 `window.toggleWdMenu`（app.js 里是
 *      window 属性赋值，后加载的同名覆盖即生效），行点击、动作、搜索全在本层；
 *      app.js 那条 `.wd-item` 委托与「点外面收起」原样保留并继续生效。
 *   3. **可整体回滚**：删掉 index.html 里一行 link + 一行 script，即回到旧菜单。
 *
 * 血账（照抄 2026-11-03 的教训）：
 *   · 搜索框不能跟着每次按键重绘——输入节点被换掉焦点立刻丢。这里只重绘
 *     `#wsp-list` 一个节点，搜索框与动作行一次性建好。
 *   · 面板向上弹（上下文条在卡片底部），列表必须按「上方可用高度」实测钳位，
 *     别用固定的 max-height 赌视口（矮屏会把面板顶出屏幕）。
 */
(function () {
  "use strict";
  if (window.__wsPickerMounted) return;
  window.__wsPickerMounted = true;

  var ROW_MAX = 14;          /* 列表最多几条：再多就滚，不把面板撑成一整屏 */
  var ROW_H = 34;            /* 与 CSS .wsp-row min-height 一致，钳位时按整行数算 */

  function $(id) { return document.getElementById(id); }
  function tr() {
    return typeof window.t === "function" ? window.t.apply(null, arguments) : arguments[0];
  }
  function esc2(v) {
    if (typeof window.esc === "function") return window.esc(v);
    return String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function settings() {
    try { return (typeof S !== "undefined" && S && S.settings) || {}; } catch (e) { return {}; }
  }
  function defaultWorkdir() {
    return String(settings().default_workdir_effective || "").trim();
  }
  function trimSlash(p) { return String(p || "").trim().replace(/[\\/]+$/, ""); }
  function baseName(p) {
    var parts = trimSlash(p).split(/[\\/]+/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
  }
  function currentVal() { return (($("f-workdir") || {}).value || "").trim(); }
  function menuEl() { return $("wd-menu"); }

  /* ------------------------------------------------------------ 数据（现取） */

  /* 用户在本机选过/建过的工作空间：localStorage 记最近 12 个。
   * 为什么必须记：清单的底账是「任务表里的 workdir」，但刚创建还没跑过任务、
   * 或者被用户清空了当前值的目录，任务表里没有 —— 不记的话它一转身就从列表里
   * 消失（2026-11-03 实测：新建后清空再开面板，刚建的目录不见了）。这只是
   * 展示层的记忆，不参与任务提交，不是第二份真相。 */
  var KNOWN_KEY = "orch.wsKnown";
  function knownDirs() {
    try {
      var a = JSON.parse(localStorage.getItem(KNOWN_KEY) || "[]");
      return Array.isArray(a) ? a.filter(function (p) { return typeof p === "string" && p.trim(); }).slice(0, 12) : [];
    } catch (e) { return []; }
  }
  function rememberDir(p) {
    var v = trimSlash(p);
    if (!v || !window.localStorage) return;
    var next = [v].concat(knownDirs().filter(function (x) { return x.toLowerCase() !== v.toLowerCase(); })).slice(0, 12);
    try { localStorage.setItem(KNOWN_KEY, JSON.stringify(next)); } catch (e) { /* 隐私模式忽略 */ }
  }

  /* 工作空间清单 = 当前值 + 记忆里的目录 + 任务表用过的目录 + 默认工作区（去重，保序）。
   * 任务段顺序即「最近用过」：S.state.tasks 由后端按最近活动下发，当前值恒在第一位。 */
  function wsRows() {
    var out = [];
    var seen = {};
    function add(p) {
      var v = trimSlash(p);
      if (!v) return;
      var key = v.toLowerCase();
      if (seen[key]) return;
      seen[key] = 1;
      out.push({ path: v, name: baseName(v) || v });
    }
    add(currentVal());
    knownDirs().forEach(add);
    var st = null;
    try { st = (typeof S !== "undefined" && S && S.state) || null; } catch (e) { st = null; }
    if (st) {
      (st.tasks || []).forEach(function (tk) { add(tk && tk.workdir); });
      (st.archived_tasks || []).forEach(function (tk) { add(tk && tk.workdir); });
    }
    add(defaultWorkdir());
    return out.slice(0, ROW_MAX);
  }

  /* ------------------------------------------------------------ 面板渲染 */

  /* 外壳只建一次（搜索框 + 列表宿主 + 分隔线 + 三行动作）。
   * 搜索框必须活到面板关掉为止——每次按键重绘整个面板，输入节点在按键途中被
   * 换掉，焦点会丢、中文输入法组词会中断（2026-11-03 点击血账同源问题）。 */
  function renderShell() {
    var menu = menuEl();
    if (!menu) return;
    menu.innerHTML =
      '<div class="wsp-search">' +
        '<svg class="ico" aria-hidden="true"><use href="#i-search"></use></svg>' +
        '<input type="text" class="wsp-q" id="wsp-q" autocomplete="off" spellcheck="false" placeholder="' +
          esc2(tr("搜索工作空间")) + '" aria-label="' + esc2(tr("搜索工作空间")) + '">' +
      "</div>" +
      '<div class="wsp-list" id="wsp-list" role="listbox" aria-label="' + esc2(tr("工作空间")) + '"></div>' +
      '<div class="wsp-sep"></div>' +
      '<div class="wsp-acts">' +
        actHtml("new", "i-plus", tr("新建工作空间")) +
        actHtml("open", "i-folder", tr("打开本地文件夹")) +
        actHtml("none", "i-x", tr("不使用工作空间")) +
      "</div>";
  }

  function actHtml(key, icon, label) {
    return '<button type="button" class="wsp-act" data-wsp-act="' + esc2(key) + '">' +
      '<svg class="ico" aria-hidden="true"><use href="#' + icon + '"></use></svg>' +
      "<span>" + esc2(label) + "</span></button>";
  }

  /* 只重绘列表：搜索时输入框与动作行原地不动 */
  function paintList(q) {
    var list = $("wsp-list");
    if (!list) return;
    var rows = wsRows();
    var key = String(q || "").trim().toLowerCase();
    var cur = trimSlash(currentVal()).toLowerCase();
    var html = "";
    if (!rows.length) {
      /* 一个都没有：说清下一步（DESIGN.md §0「空态要教用户第一句话该往哪说」） */
      html = '<p class="wsp-empty">' + esc2(tr("还没有用过的工作空间，先打开一个本地文件夹")) + "</p>";
    } else {
      var list2 = key ? rows.filter(function (r) {
        return (r.name + " " + r.path).toLowerCase().indexOf(key) >= 0;
      }) : rows;
      if (!list2.length) {
        html = '<p class="wsp-empty">' + esc2(tr("没有匹配的工作空间")) + "</p>";
      } else {
        html = list2.map(function (r) {
          var on = r.path.toLowerCase() === cur;
          return '<button type="button" class="wsp-row' + (on ? " is-on" : "") +
            '" role="option" aria-selected="' + (on ? "true" : "false") +
            '" data-wsp-row="' + esc2(r.path) + '" title="' + esc2(r.path) + '">' +
            '<svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>' +
            "<span>" + esc2(r.name) + "</span>" +
            (on ? '<svg class="ico wsp-check" aria-hidden="true"><use href="#i-check"></use></svg>' : "") +
            "</button>";
        }).join("");
      }
    }
    list.innerHTML = html;
    list.scrollTop = 0;
    clampList();
  }

  /* 面板向上弹：按「胶囊上沿往上还有多少空间」实测列表能有多高。
   * 不写死 max-height —— 矮屏（或任务页输入坞贴底）会把面板顶出屏幕外。 */
  function clampList() {
    var menu = menuEl(), list = $("wsp-list");
    if (!menu || !list) return;
    list.style.maxHeight = "";
    var anchor = menu.parentElement;             /* .cmp-wd */
    if (!anchor) return;
    var avail = anchor.getBoundingClientRect().top - 14;
    if (avail <= 0) return;                      /* 面板还没布局：下次再量 */
    var chrome = menu.offsetHeight - list.offsetHeight;
    var room = avail - chrome;
    if (room < list.scrollHeight) {
      var rows = Math.floor(room / ROW_H);
      list.style.maxHeight = Math.max(ROW_H * 3, rows * ROW_H) + "px";
    }
  }

  /* ------------------------------------------------------------ 动作 */

  function hideMenu() {
    var menu = menuEl();
    if (menu) menu.classList.add("hidden");
  }

  /* 回填工作目录：value 真源仍是 #f-workdir，手动派发 input/change 让
   * queueGitProbe（分支胶囊）与 composer-context 的目录名标签跟上 */
  function setWorkdir(p) {
    var inp = $("f-workdir");
    if (!inp) return;
    inp.value = p;
    if (p) rememberDir(p);
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.dispatchEvent(new Event("change", { bubbles: true }));
    if (typeof unhideSideDir === "function") {
      try { unhideSideDir(p); } catch (e) { /* 目录被手动移除过：捞回来即可，失败不影响选目录 */ }
    }
  }

  function pickRow(path) {
    if (!path) return;
    hideMenu();
    setWorkdir(path);
  }

  function openFolder() {
    hideMenu();
    if (typeof window.pickFolder === "function") window.pickFolder("f-workdir");
  }

  /* 「不使用工作空间」= 清空 #f-workdir（空值即走默认工作区，与旧「用默认路径」同一行为）。
   * 清完把落点说出来，别让用户猜稿子存哪了（DESIGN.md §0「数字不撒谎」的同理）。 */
  function useDefault() {
    hideMenu();
    var eff = defaultWorkdir();
    setWorkdir("");
    if (typeof window.toast === "function") {
      window.toast(eff
        ? tr("不使用工作空间：任务将落在默认目录 ") + eff
        : tr("已清空工作目录，将使用默认目录"));
    }
  }

  function doAct(key) {
    if (key === "new") { hideMenu(); openCreate(); return; }
    if (key === "open") { openFolder(); return; }
    if (key === "none") { useDefault(); return; }
  }

  /* --------------------------------------------------- 新建工作空间（命名弹框） */

  /* 建在哪里：默认工作区根（内置 workspace/ 或用户设置的自定义路径）——
   * 与「不使用工作空间」落在同一处，用户的心智模型才对得上。 */
  function rootDir() { return defaultWorkdir(); }

  function note(msg, bad) {
    var el = $("wsp-note");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("is-bad", !!bad);
  }

  function openCreate() {
    if (typeof window.openModal !== "function") return;
    var root = rootDir();
    window.openModal(tr("新建工作空间"),
      '<div class="wsp-dlg">' +
        '<p class="wsp-dlg-hint">' + esc2(tr("为工作空间命名，本地会自动创建同名文件夹；创建后仍可随时切换。")) + "</p>" +
        '<input type="text" class="wsp-dlg-input" id="wsp-name" autocomplete="off" spellcheck="false" maxlength="64" placeholder="' +
          esc2(tr("输入工作空间名称")) + '">' +
        '<p class="wsp-dlg-note" id="wsp-note">' +
          esc2(root ? tr("将创建在：") + root : tr("将创建在默认工作区")) + "</p>" +
      "</div>",
      '<span class="spacer"></span>' +
      '<button class="ghost" type="button" onclick="closeModal()">' + esc2(tr("取消")) + "</button>" +
      '<button class="primary" type="button" id="wsp-ok" onclick="wspCreate()">' + esc2(tr("确认")) + "</button>",
      "wsp-modal");
    var inp = $("wsp-name");
    if (!inp) return;
    inp.focus();
    inp.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); window.wspCreate(); }
      else if (e.key === "Escape") { e.preventDefault(); if (typeof window.closeModal === "function") window.closeModal(); }
    });
    inp.addEventListener("input", function () { note(root ? tr("将创建在：") + root : "", false); });
  }

  /* 名称校验：单段目录名，不许分隔符/上跳/Windows 保留字符。
   * 后端同一套规则再挡一次（前端只为即时反馈，不作为信任边界）。 */
  function badName(name) {
    if (!name) return tr("请先输入名称");
    if (/[\\/:*?"<>|]/.test(name)) return tr("名称不能包含 \\ / : * ? \" < > | 等字符");
    if (name === "." || name === ".." || name.charAt(0) === ".") return tr("名称不能以 . 开头");
    return "";
  }

  window.wspCreate = async function () {
    var inp = $("wsp-name");
    var name = ((inp && inp.value) || "").trim();
    var bad = badName(name);
    if (bad) { note(bad, true); if (inp) inp.focus(); return; }
    var btn = $("wsp-ok");
    if (btn) { btn.disabled = true; btn.textContent = tr("创建中…"); }
    var r = null;
    try {
      r = await window.api("/api/workdir/create", {
        method: "POST",
        body: JSON.stringify({ name: name }),
      });
    } catch (e) {
      var msg = String((e && e.message) || e || "");
      /* 老进程还没带上这条路由（需要重启一次才生效）：说清下一步，不假装成功 */
      if (/404|not\s*found|未找到/i.test(msg)) {
        note(tr("当前运行的 CodeBee 还没有这个接口，重启一次后即可自动创建；也可以先用「打开本地文件夹」。"), true);
      } else {
        note(tr("创建失败：") + msg, true);
      }
      if (btn) { btn.disabled = false; btn.textContent = tr("确认"); }
      if (inp) inp.focus();
      return;
    }
    var path = (r && r.path) || "";
    if (!path) {
      note(tr("创建失败：服务端没有返回目录路径"), true);
      if (btn) { btn.disabled = false; btn.textContent = tr("确认"); }
      return;
    }
    if (typeof window.closeModal === "function") window.closeModal();
    setWorkdir(path);
    if (typeof window.toast === "function") {
      window.toast(r && r.created === false
        ? tr("已有同名文件夹，已直接选中：") + path
        : tr("已创建工作空间并选中：") + path);
    }
  };

  /* ------------------------------------------------------------ 接管入口 */

  function openMenu() {
    var menu = menuEl();
    if (!menu) return;
    renderShell();
    paintList("");
    menu.classList.remove("hidden");
    clampList();
    var q = $("wsp-q");
    if (q) { try { q.focus({ preventScroll: true }); } catch (e) { q.focus(); } }
  }

  window.toggleWdMenu = function (ev) {
    if (ev && ev.stopPropagation) ev.stopPropagation();
    var menu = menuEl();
    if (!menu) return;
    if (!menu.classList.contains("hidden")) { hideMenu(); return; }
    openMenu();
  };

  /* 搜索：只重绘列表（见文件头血账） */
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!el || el.id !== "wsp-q") return;
    paintList(el.value);
  }, true);

  /* 行与动作的点击：拦住冒泡，别让 app.js 那条「点外面收起」先判成外部点击；
   * 行不用 .wd-item 类，app.js 的 .wd-item 委托自然不参与（避免双重处理）。 */
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var menu = menuEl();
    if (!menu || !menu.contains(t)) return;
    var row = t.closest(".wsp-row");
    if (row) { e.stopPropagation(); pickRow(row.dataset.wspRow); return; }
    var act = t.closest(".wsp-act");
    if (act) { e.stopPropagation(); doAct(act.dataset.wspAct); return; }
  }, true);

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var menu = menuEl();
    if (menu && !menu.classList.contains("hidden") && !$("wsp-name")) hideMenu();
  });

  window.addEventListener("resize", function () {
    var menu = menuEl();
    if (menu && !menu.classList.contains("hidden")) clampList();
  });

  /* 守测/调试入口 */
  window.wspPicker = { open: openMenu, close: hideMenu, rows: wsRows, paintList: paintList };
})();
