/* composer-context.js（2026-11-03）—— 输入卡「专家胶囊行 + 底部上下文条」
 *
 * 用户两张桌面端观感参考图：
 *   ① 输入框上方：一排分组页签 + 一排专家胶囊，选人一眼可见、一点即中，
 *      不再藏在底部那颗「快答 ▾」下拉里；
 *   ② 输入框底部：文件夹 ▾ / 分支 ▾ 的上下文条，只露目录名与分支名，
 *      而不是把整条绝对路径铺在卡顶。
 *
 * 三条硬约束（与 run-config.js 同一套做法）：
 *   1. **不碰真源**：#f-type / #f-workdir / #f-git-rev 三个原生控件仍是唯一
 *      数据源（id / name / 联动全不变），本层只读它们的值、在选人时回写值并
 *      派发 change。因此 onTypeChange、probeGit、refreshEstimate、模板回填、
 *      提交体组装一行都不用改。
 *   2. **只搬节点不复制**：#f-workdir 与 #row-git 是整块搬进 .ccx-foot 的，
 *      pickFolder / 最近目录菜单 / 分支下拉的事件绑定全在原地生效。
 *   3. **可整体回滚**：删掉 index.html 里的一个 <link> + 一个 <script>，
 *      节点归位由 .ccx-* 类消失后的既有样式接管。
 *
 * composer 会在 #sub-tasks 与 #rd-input-slot 之间来回搬（homeMountComposer），
 * 所以胶囊行靠 MutationObserver 跟着 .cmp-box 走，而不是固定挂在某一处。
 */
(function () {
  "use strict";
  if (window.__composerContextMounted) return;
  window.__composerContextMounted = true;

  /* 页签顺序＝产品序（与 app/core/agents.py 的 EXPERT_GROUPS 一致）；
   * 显示名从 /api/flows 的 agent.group_name 读，不在前端另存一份文案表。 */
  var GROUPS = ["chat", "create", "code", "docs", "research"];

  var pickEl = null;     /* .ccx-pick 整体 */
  var tabsEl = null;     /* .ccx-tabs */
  var chipsEl = null;    /* .ccx-chips */
  var footEl = null;     /* .ccx-foot */
  var wdEl = null;       /* .cmp-wd（搬进来的目录胶囊） */
  var cwdEl = null;      /* .ccx-cwd（目录名标签） */
  var noGitEl = null;    /* .ccx-git-no（非 Git 目录时的说明胶囊） */
  var lastSig = "";      /* 上一次绘制的指纹：内容没变就一个字节都不写 */
  var picked = "";       /* 用户点过的页签；空 = 跟随（默认「常用」） */
  var syncTimer = 0;

  function $(id) { return document.getElementById(id); }
  function tr(k) { return typeof window.t === "function" ? window.t(k) : k; }
  function langOf() {
    var h = document.documentElement;
    return (h.getAttribute("data-lang") || h.getAttribute("lang") || "").toLowerCase();
  }
  function esc2(v) {
    return typeof window.esc === "function" ? window.esc(v) : String(v == null ? "" : v);
  }
  function flows() {
    try {
      if (typeof S !== "undefined" && S && S.flows && S.flows.length) return S.flows;
    } catch (e) { /* 极端情况下按空表处理，不阻塞首屏 */ }
    return [];
  }
  function experts() {
    /* 与专家页（openExperts）同一份名单：有 agent 人设即可选。
     * 不按 agent.hidden 过滤——「快答」（direct）在 agents.py 里是 hidden 的
     * （只做定向点名），但它是本产品默认类型，滤掉会连「对话速办」整组消失。 */
    return flows().filter(function (f) { return f && f.id && f.agent; });
  }
  function groupOf(f) { return (f && f.agent && f.agent.group) || "more"; }
  function orderOf(f) {
    var o = f && f.agent && f.agent.group_order;
    return o == null ? 999 : o;
  }
  /* 「常用」页签 = 每个分组的第一位（后端 group_order 最小者）。
   * 为什么不是另写一份「常用名单」：那会变成第二份真相；而每组首位本来就
   * 是产品序里的代表（快答 / 小说家 / 工程师 / 文书 / 调研员），一眼覆盖全部能力面，
   * 也正好把「对话速办只有一位」这种稀疏组的首屏填满。 */
  function pinnedRows() {
    var rows = experts().slice().sort(function (a, b) { return orderOf(a) - orderOf(b); });
    var seen = {};
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var g = groupOf(rows[i]);
      if (seen[g]) continue;
      seen[g] = 1;
      out.push(rows[i]);
    }
    return out;
  }
  function groupName(key) {
    var rows = experts();
    for (var i = 0; i < rows.length; i++) if (groupOf(rows[i]) === key) {
      return String((rows[i].agent && rows[i].agent.group_name) || "");
    }
    return "";
  }
  function currentFlowId() { return (($("f-type") || {}).value || "").trim(); }
  function flowName(f) {
    var a = (f && f.agent) || {};
    return String(a.name || (f && f.name) || "").trim() || tr("助手");
  }
  function boxEl() {
    return document.querySelector("#rd-input-slot .cmp-box") ||
      document.querySelector("#sub-tasks .cmp-box");
  }

  /* ------------------------------------------------------------ 专家胶囊行 */

  function buildPick() {
    pickEl = document.createElement("div");
    pickEl.className = "ccx-pick";
    pickEl.setAttribute("role", "group");
    pickEl.setAttribute("aria-label", tr("选择专家"));
    tabsEl = document.createElement("div");
    tabsEl.className = "ccx-tabs";
    tabsEl.setAttribute("role", "tablist");
    chipsEl = document.createElement("div");
    chipsEl.className = "ccx-chips";
    chipsEl.setAttribute("role", "listbox");
    chipsEl.setAttribute("aria-label", tr("专家"));
    pickEl.appendChild(tabsEl);
    pickEl.appendChild(chipsEl);

    tabsEl.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest(".ccx-tab") : null;
      if (!btn) return;
      picked = btn.dataset.group || "";
      paint();
    });

    chipsEl.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest(".ccx-chip") : null;
      if (!btn) return;
      if (btn.dataset.more === "1") {
        if (typeof window.openExperts === "function") window.openExperts();
        return;
      }
      var id = btn.dataset.flow || "";
      if (!id) return;
      /* 不重置 picked：在「写作创作」里选完人，行就该留在写作创作，
       * 跳到「常用」会让刚点中的胶囊跑掉，读起来像「点了没反应」。 */
      pickFlow(id);
    });
  }

  /* 选人：优先走既有 pickQuickType（含预填示例、聚焦目标框），退化到直接写 select */
  function pickFlow(id) {
    if (typeof window.pickQuickType === "function") {
      window.pickQuickType(id);
    } else {
      var sel = $("f-type");
      if (sel && sel.value !== id) {
        sel.value = id;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    paint();
  }

  /* paint() 会在 pointerdown / focusin 里被同步调用（按键的那一刻，mouseup 还没来）。
   * 早先每次都重写 innerHTML：按下的那颗按钮在 mouseup 之前就被换成新节点，浏览器把
   * click 派给已经脱离文档的旧节点，`e.target.closest(".ccx-chip")` 自然是 null ——
   * 表现就是「点不动」（2026-11-03 用户实拍，CDP 里 detach:{chip:true,tab:true}）。
   * 现在先比指纹，内容没变直接返回，节点不动，点击照常落地。 */
  function paint() {
    if (!tabsEl || !chipsEl) return;
    var cur = currentFlowId();
    var rows = experts();
    var curFlow = null;
    for (var i = 0; i < rows.length; i++) if (rows[i].id === cur) { curFlow = rows[i]; break; }

    /* 「常用」放最前并作为默认：稀疏组（「对话速办」只有快答一位）首屏不空，
     * 同时每位专家都是该组代表，能力面一眼覆盖。真源仍是 agent.group（后端下发），
     * 这里只是换个投影方式，不另存名单。 */
    var keys = ["pinned"].concat(GROUPS.filter(function (k) { return !!groupName(k); }));
    if (keys.length < 2) { tabsEl.textContent = ""; chipsEl.textContent = ""; lastSig = ""; return; }
    var active = picked;
    if (!active) {
      /* 没手动点过页签时：当前专家在「常用」里就停在「常用」（选完不跳走），
       * 不在里面则落到它自己那一组，否则会「选了人却看不到高亮」。 */
      active = "pinned";
      if (curFlow) {
        var pin = pinnedRows();
        var hit = false;
        for (var p = 0; p < pin.length; p++) if (pin[p].id === curFlow.id) { hit = true; break; }
        if (!hit) active = groupOf(curFlow);
      }
    }
    if (keys.indexOf(active) < 0) active = "pinned";

    var list = active === "pinned" ? pinnedRows() : rows.filter(function (f) { return groupOf(f) === active; })
      .sort(function (a, b) { return orderOf(a) - orderOf(b); });

    /* 指纹：页签集合 + 选中页签 + 当前专家 + 语言 + 每个胶囊的 id 与显示名 */
    var sig = [active, cur, langOf(), tr("常用"),
      list.map(function (f) { return f.id + "|" + flowName(f); }).join(",")].join("~");
    if (sig === lastSig && tabsEl.childElementCount && chipsEl.childElementCount) return;
    lastSig = sig;

    tabsEl.innerHTML = keys.map(function (k) {
      var on = k === active;
      return '<button type="button" class="ccx-tab' + (on ? " on" : "") +
        '" role="tab" aria-selected="' + (on ? "true" : "false") +
        '" data-group="' + esc2(k) + '">' +
        esc2(k === "pinned" ? tr("常用") : tr(groupName(k))) + "</button>";
    }).join("");

    chipsEl.innerHTML = list.map(function (f) {
      var nm = flowName(f);
      var on = f.id === cur;
      return '<button type="button" class="ccx-chip' + (on ? " on" : "") +
        '" role="option" aria-selected="' + (on ? "true" : "false") +
        '" data-flow="' + esc2(f.id) + '" title="' + esc2(tr(nm)) + '">' +
        (typeof window.expertAvHtml === "function" ? window.expertAvHtml(nm, f.id) : "") +
        "<span>" + esc2(tr(nm)) + "</span></button>";
    }).join("") +
      '<button type="button" class="ccx-chip ccx-more" data-more="1" title="' +
      esc2(tr("查看全部专家")) + '">' + esc2(tr("更多专家")) +
      '<span class="ccx-more-n" hidden></span>' +
      '<svg class="ico" aria-hidden="true"><use href="#i-chevron-r"></use></svg></button>';

    scheduleFold();
  }

  /* 一行放不下就折进「更多专家」。
   * 为什么不靠 CSS 换行/横滚：换行会让首屏高度随分组跳动（切「写作创作」整块内容
   * 下移 38px），横滚则把后面的人藏到屏幕外且没有提示。量一次宽度、把放不下的
   * 那几枚 display:none，右边永远收在「更多专家」上，高度恒定一行。
   * 宽度算式（gap 只在「枚与枚之间」出现，行首行尾都没有）：
   *   Σ宽度 + (n-1)*gap  [n 枚胶囊]  +  gap + 「更多专家」宽  ≤  容器宽
   * 少算一个 gap 就会溢出 8px —— center 对齐下两侧各被裁掉 4px（实测「更多专家」
   * 的虚线边被切掉一角），所以这里留 2px 富余。 */
  function foldChips() {
    if (!chipsEl) return;
    var all = chipsEl.querySelectorAll(".ccx-chip");
    if (!all.length) return;
    var more = chipsEl.querySelector(".ccx-more");
    var items = [];
    for (var i = 0; i < all.length; i++) if (all[i] !== more) items.push(all[i]);
    if (!items.length) return;
    for (var r = 0; r < items.length; r++) items[r].style.display = "";
    var avail = chipsEl.clientWidth - 2;
    if (avail <= 0) return;                   /* 还没布局（隐藏页），下次 resize 再折 */
    var gap = 8;                              /* 与 .ccx-chips 的 gap 保持一致 */
    var moreW = more ? more.offsetWidth : 0;
    var tail = more ? moreW + gap : 0;        /* 末尾那枚「更多专家」连同它前面的 gap */
    var cut = items.length;
    var acc = 0;
    for (var j = 0; j < items.length; j++) {
      var w = items[j].offsetWidth + (j ? gap : 0);
      if (acc + w + tail > avail) { cut = j; break; }
      acc += w;
    }
    if (cut < 1) cut = 1;                     /* 再窄也留一枚，别只剩「更多专家」 */
    for (var h = cut; h < items.length; h++) items[h].style.display = "none";
    var folded = items.length - cut;
    chipsEl.dataset.folded = String(folded);
    paintMoreCount(more, folded);
  }

  /* 折掉的几位必须写在脸上（用户 2026-11-03：「折掉了不提示，看到 7 颗就以为是全部」）。
   * 只改这颗胶囊里的计数，不重绘列表 —— 重绘会和 foldChips 的量宽互相触发（会抖）。 */
  function paintMoreCount(more, folded) {
    if (!more) return;
    var n = more.querySelector(".ccx-more-n");
    if (n) {
      var txt = folded > 0 ? "+" + folded : "";
      if (n.textContent !== txt) n.textContent = txt;
      if (n.hidden !== !(folded > 0)) n.hidden = !(folded > 0);
    }
    more.classList.toggle("has-folded", folded > 0);
    /* title 用「查看全部专家 (+2)」：数字不参与翻译，不新增词条 */
    more.title = folded > 0 ? tr("查看全部专家") + " (+" + folded + ")" : tr("查看全部专家");
  }

  /* 刚写完 innerHTML 时头像图还没解码、品牌字体还没生效，量到的胶囊偏窄，
   * 按那个宽度折出来的行会在 100ms 后又被撑出去（实测「更多专家」右侧被裁一条）。
   * 所以：立刻折一次定基本盘，下一帧、字体就绪、容器尺寸变化时各补一次。 */
  var foldTimer2 = 0;
  function scheduleFold() {
    foldChips();
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(foldChips);
    clearTimeout(foldTimer2);
    foldTimer2 = setTimeout(foldChips, 160);
  }

  /* ---------------------------------------------------- 底部上下文条与目录胶囊 */

  function buildFoot() {
    footEl = document.createElement("div");
    footEl.className = "ccx-foot";
    /* 分支胶囊的兜底：probeGit 在「目录不是 Git 仓库」时会把 #row-git 整块
     * .hidden（真源的行为不动）。那样用户看不到任何分支信息，只会觉得「怎么
     * 没有分支」（2026-11-03 反馈：「当前属于哪个分支也没呈现出来」，实测默认
     * 工作目录 E:\CodeBee\workspace 不是仓库）。这里补一枚说明胶囊，把「没有」
     * 本身讲清楚，顺手提示可换目录 —— 它只是状态，不带动作，故不是按钮。 */
    noGitEl = document.createElement("span");
    noGitEl.id = "ccx-git-none";
    noGitEl.className = "ccx-git-no";
    noGitEl.textContent = tr("非 Git 目录");
    noGitEl.title = tr("当前工作目录不是 Git 仓库 · 换到仓库目录后可选分支");
  }

  /* #row-git 的显隐由 probeGit 决定（带 .hidden），这里只镜像它的结果 */
  function paintGit() {
    var row = $("row-git");
    if (!noGitEl || !row || !noGitEl.parentElement) return;
    var sel = $("f-git-rev");
    /* open = 分支胶囊在位。它在位 → 说明胶囊退场（hidden=true）；它不在位 → 说明胶囊登场。
     * 即 noGitEl.hidden 就是 open 本身，别再取反（写反过一次，界面看着像没加）。 */
    var open = !row.classList.contains("hidden") && !!sel && sel.options.length > 0;
    if (noGitEl.hidden !== open) noGitEl.hidden = open;
    fitGitPill();
  }

  /* 目录胶囊：显示目录名（不是整条路径），点名字就地改路径 → 输入框展开 */
  function ensureCwd() {
    wdEl = document.querySelector(".cmp-wd");
    if (!wdEl) return;
    var input = $("f-workdir");
    if (!cwdEl) {
      cwdEl = document.createElement("span");
      cwdEl.className = "ccx-cwd";
      var ico = wdEl.querySelector(":scope > .ico");
      if (ico && ico.nextSibling) wdEl.insertBefore(cwdEl, ico.nextSibling);
      else wdEl.insertBefore(cwdEl, wdEl.firstChild);

      wdEl.addEventListener("click", function (e) {
        /* 编辑态点输入框：拦在捕获阶段，别让 app.js 那条「点输入框弹目录选择」
         * 把正在打的字打断；点选择按钮/下拉菜单则原样放行 */
        if (input && e.target === input && wdEl.classList.contains("ccx-editing")) {
          e.stopPropagation();
          return;
        }
        if (e.target.closest && (e.target.closest("button") || e.target.closest(".wd-menu"))) return;
        setEditing(true);
      }, true);
    }
    if (input && !input.__ccxCwdBound) {
      input.__ccxCwdBound = true;
      input.addEventListener("focus", function () {
        if (!wdEl.classList.contains("ccx-editing")) setEditing(true);
      });
      input.addEventListener("blur", function () {
        if (wdEl.classList.contains("ccx-editing")) setEditing(false);
      });
      ["input", "change"].forEach(function (ev) {
        input.addEventListener(ev, paintCwd);
      });
      /* 编辑态：Enter 收工（不是提交任务——composer 不是 <form>，但别让按键悬空） */
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          setEditing(false);
        }
      });
    }
    paintCwd();
  }

  function paintCwd() {
    if (!wdEl || !cwdEl) return;
    var input = $("f-workdir");
    var v = ((input && input.value) || "").trim();
    var base = v ? (v.split(/[\\/]/).filter(Boolean).pop() || v) : "";
    var text = base || tr("选择工作目录");
    if (cwdEl.textContent !== text) cwdEl.textContent = text;
    var tip = v || tr("工作目录 · 留空用默认");
    if (cwdEl.title !== tip) cwdEl.title = tip;
    wdEl.classList.toggle("ccx-empty", !base);
  }

  function setEditing(on) {
    var input = $("f-workdir");
    if (!wdEl || !input) return;
    wdEl.classList.toggle("ccx-editing", !!on);
    if (on) {
      try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
      var n = (input.value || "").length;
      try { input.setSelectionRange(n, n); } catch (e) { /* 非文本输入忽略 */ }
    } else if (document.activeElement === input) {
      input.blur();
      paintCwd();
    }
  }

  /* ------------------------------------- 对话页（#run-detail）的上下文条
   *
   * 用户贴 `#rd-chat-bar` 实拍：「对话这里面这个也一起改了吧」—— 和首页同一套
   * 「谁在干 / 在哪个目录 / 哪个分支」，一行摆开。
   *
   * 与首页的两处**故意不同**（都是为诚实，不是为了省事）：
   *   1. **只读**：任务一旦开跑，目录与分支就是这次运行的既成事实，改输入框改不了
   *      已经在跑的工作区；真能改的是「新建任务」。所以这里不做可点胶囊，
   *      详情全部挂在 title 上。
   *   2. **专家是身份不是切换器**：`/api/runs/<id>/chat` 只收 text/attachments/
   *      delivery，任务参数接口（/api/tasks/<id>/params）只认 mode/thinking/模型，
   *      没有任何 flow 覆盖入口。放一排能点但改不了谁答事的胶囊就是假控件，
   *      所以这里只显示「本任务由 X 执行」。
   *
   * 数据全部取该任务自己的：目录 = task.workdir，分支 = /api/git/info 现探
   * （和首页 probeGit 同一个接口，只读），专家名 = /api/flows 的 agent.name。
   */
  var rdFoot = null;
  var rdSig = "";
  var gitInfoCache = {};     /* workdir -> {repo,branch,head,dirty,dirty_count} | {pending:1} */

  function rdVisible() {
    var d = $("run-detail");
    return !!(d && !d.classList.contains("hidden"));
  }
  function rdTask() {
    var id = "", rid = "";
    try {
      if (typeof S === "undefined" || !S) return null;
      id = String(S.detailTaskKey || (S.lastRun && S.lastRun.task_id) || "");
      /* run 级详情刚打开时 S.lastRun 还没拉回来：用 task_latest 反查 run → task */
      if (!id) {
        rid = String(S.detailRunId || "");
        var tl = (S.state && S.state.task_latest) || {};
        for (var k in tl) {
          if (tl[k] && tl[k].id === rid) { id = String(tl[k].task_id || ""); break; }
        }
      }
    } catch (e) { return null; }
    if (!id) return null;
    var pools = [];
    try {
      pools = [(S.state && S.state.tasks) || [], (S.state && S.state.archived_tasks) || []];
    } catch (e) { pools = []; }
    for (var p = 0; p < pools.length; p++) {
      for (var i = 0; i < pools[p].length; i++) {
        if (pools[p][i] && pools[p][i].id === id) return pools[p][i];
      }
    }
    return null;   /* 拿不到任务就不显示，绝不拿新任务草稿的值冒充 */
  }
  function rdExpertName(task) {
    var t = String((task && task.type) || "");
    var rows = experts();
    for (var i = 0; i < rows.length; i++) if (rows[i].id === t) return flowName(rows[i]);
    var snap = (task && task.flow_snapshot) || {};
    return String(snap.name || t || "");
  }
  function rdProbeGit(wd) {
    if (!wd || gitInfoCache[wd]) return;
    gitInfoCache[wd] = { pending: 1 };
    var done = function (info) {
      gitInfoCache[wd] = info || { repo: false };
      paintRd(true);
    };
    try {
      fetch("/api/git/info?workdir=" + encodeURIComponent(wd), { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(done)
        .catch(function () { done(null); });
    } catch (e) { done(null); }
  }

  function buildRdFoot() {
    rdFoot = document.createElement("div");
    rdFoot.className = "ccx-rd-foot";
    rdFoot.innerHTML =
      '<span class="ccx-rd-chip ccx-rd-expert"><span class="ccx-rd-av"></span>' +
      '<span class="ccx-rd-txt"></span></span>' +
      '<span class="ccx-rd-chip"><svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>' +
      '<span class="ccx-rd-txt"></span></span>' +
      '<span class="ccx-rd-chip"><svg class="ico" aria-hidden="true"><use href="#i-git-branch"></use></svg>' +
      '<span class="ccx-rd-txt"></span></span>';
    rdFoot.setAttribute("aria-label", tr("当前执行上下文"));
  }

  function paintRd(force) {
    if (!rdFoot) return;
    if (!rdVisible()) return;
    var task = rdTask();
    var wd = String((task && task.workdir) || "").trim();
    var expert = rdExpertName(task);
    var info = wd ? (gitInfoCache[wd] || (rdProbeGit(wd), { pending: 1 })) : null;

    var sig = [task && task.id, wd, expert,
      info ? (info.pending ? "…" : (info.repo ? info.branch + "|" + (info.dirty ? info.dirty_count : 0) : "-")) : ""
    ].join("~");
    if (!force && sig === rdSig) return;
    rdSig = sig;

    var chips = rdFoot.children;
    var expertChip = chips[0], dirChip = chips[1], gitChip = chips[2];

    /* ① 谁在执行。带团任务升级出团队身份（team_snapshot 在创建时钉住，
     * 改团不影响旧任务——所以这里显示的是快照，只读、零新接口） */
    var snap = (task && task.team_snapshot) || null;
    var snapMembers = (snap && snap.members) || [];
    var label = expert, tip = "";
    if (snap && snapMembers.length && expert) {
      var helpers = snapMembers.slice(1);
      label = tr("主责 %1 · %2 · %3 位协作")
        .replace("%1", expert).replace("%2", String(snap.name || ""))
        .replace("%3", String(helpers.length));
      tip = tr("按「%1」分工执行：主责主导产出，协作在关键节点复核；改团不影响本任务")
        .replace("%1", String(snap.name || ""));
      try {
        tip += "\n" + snapMembers.map(function (m2) {
          return (m2.role ? m2.role + " " : "") +
            String(m2.name || m2.flow_id || "?") +
            (m2.focus ? "（" + m2.focus + "）" : "");
        }).join("\n");
      } catch (e2) { /* title 只是个补充，拼不出就略 */ }
    }
    expertChip.style.display = expert ? "" : "none";
    expertChip.querySelector(".ccx-rd-txt").textContent = label;
    expertChip.title = expert
      ? (tip || tr("由 %1 执行 · 换专家请新建任务").replace("%1", expert)) : "";
    var av = expertChip.querySelector(".ccx-rd-av");
    if (expert && typeof window.expertAvHtml === "function") {
      if (av.dataset.n !== expert) {
        av.dataset.n = expert;
        av.innerHTML = window.expertAvHtml(expert, (task && task.type) || expert);
      }
    }

    /* ② 在哪个目录（只露目录名，title 给全路径） */
    var base = wd ? (wd.split(/[\\/]/).filter(Boolean).pop() || wd) : "";
    dirChip.style.display = wd ? "" : "none";
    dirChip.querySelector(".ccx-rd-txt").textContent = base;
    dirChip.title = wd ? wd : "";

    /* ③ 在哪个分支（非仓库时把「没有」讲清楚，同首页口径） */
    var branch = "", tip = "";
    if (!info) { branch = ""; }
    else if (info.pending) { branch = "…"; }
    else if (!info.repo) { branch = tr("非 Git 目录"); tip = tr("当前工作目录不是 Git 仓库 · 换到仓库目录后可选分支"); }
    else {
      branch = String(info.branch || "HEAD");
      tip = tr("当前分支 %1 @ %2").replace("%1", branch).replace("%2", String(info.head || "").slice(0, 8));
      if (info.dirty) tip += tr("；工作区有 %1 处未提交改动").replace("%1", info.dirty_count);
    }
    gitChip.style.display = branch ? "" : "none";
    gitChip.querySelector(".ccx-rd-txt").textContent = branch;
    gitChip.title = tip;
    gitChip.classList.toggle("is-muted", !!(info && !info.pending && !info.repo));
  }

  function mountRd() {
    var bar = document.querySelector("#run-detail .rd-chat-bar");
    if (!bar) return;
    var tools = bar.querySelector(".rd-chat-tools");
    /* 摆在 textarea 与工具栏之间：既在卡内，也不挤占发送按钮那一行 */
    if (!tools) return;
    if (!rdFoot || !rdFoot.parentElement || rdFoot.nextElementSibling !== tools) {
      if (!rdFoot) buildRdFoot();
      tools.parentElement.insertBefore(rdFoot, tools);
    }
    paintRd();
  }

  /* ------------------------------------------------------------ 挂载与跟随 */

  /* 分支胶囊宽度跟着当前分支名走：原生 select 的固有宽度由「最长的 option」
   * （提交标题能到 60 字）撑开，不量一下会留一大段空白、小箭头飘在中间 */
  var measureCtx = null;
  function fitGitPill() {
    var sel = $("f-git-rev");
    if (!sel || !sel.options.length) return;
    var txt = sel.options[sel.selectedIndex] ? String(sel.options[sel.selectedIndex].textContent || "") : "";
    var w = 0;
    try {
      measureCtx = measureCtx || document.createElement("canvas").getContext("2d");
      var cs = window.getComputedStyle(sel);
      measureCtx.font = (cs.fontWeight || "400") + " " + (cs.fontSize || "12.5px") + " " + (cs.fontFamily || "sans-serif");
      w = Math.ceil(measureCtx.measureText(txt).width) + 16;
    } catch (e) { w = 0; }
    var px = Math.max(46, Math.min(190, w || 120));
    var next = px + "px";
    if (sel.style.width !== next) sel.style.width = next;
  }

  function sync() {
    var box = boxEl();
    if (!box) return;
    if (!pickEl) buildPick();
    if (!footEl) buildFoot();

    /* 胶囊行要贴在**卡片外沿之上**：主页的卡是 .rd-input-card（#rd-input-slot 在它里面，
     * 插进 slot 会落在白底卡内），任务页的卡就是 .cmp-box 自己。 */
    var anchor = box.closest(".rd-input-card") || box;
    var parent = anchor.parentElement;
    if (parent && (pickEl.parentElement !== parent || pickEl.nextElementSibling !== anchor)) {
      parent.insertBefore(pickEl, anchor);
    }

    var bar = box.querySelector(".cmp-bar");
    var anchor2 = bar || box.lastElementChild;
    if (footEl.parentElement !== box || (anchor2 && anchor2.nextElementSibling !== footEl && anchor2 !== footEl)) {
      if (anchor2 && anchor2.parentElement === box) box.insertBefore(footEl, anchor2.nextSibling);
      else box.appendChild(footEl);
    }

    var wd = document.querySelector(".cmp-wd");
    if (wd && wd.parentElement !== footEl) footEl.appendChild(wd);
    var git = $("row-git");
    if (git && git.parentElement !== footEl) footEl.appendChild(git);
    if (noGitEl && noGitEl.parentElement !== footEl) footEl.appendChild(noGitEl);

    /* 两条上下文搬走后 .cmp-pills 只剩空白文本节点，:empty 不成立 → 显式收掉 */
    var pills = document.querySelector(".cmp-pills");
    if (pills) {
      var left = pills.querySelector(".cmp-wd, #row-git");
      pills.classList.toggle("ccx-gone", !left);
    }

    ensureCwd();
    paintGit();
    paint();
    foldChips();        /* 宿主换了、宽度变了：重新数一遍能放几枚 */
    bindFoldWatchers();
  }

  function scheduleSync() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(sync, 0);
  }

  function watch() {
    var mo = window.MutationObserver;
    if (!mo) return;
    /* 真源 #f-workdir 有两处是「直接赋值 + 不派发事件」：
     *   app.js applySettings 把 default_workdir_effective 写进去（随后 queueGitProbe），
     *   模板回填 $("f-workdir").value = tk.workdir。
     * 属性/tree 都观察不到 value 变化，所以用一次 700ms 的轻量对齐：只比对手上这个
     * 字符串与文案，没变就一个 DOM 操作都不做。 */
    setInterval(function () {
      paintCwd();
      paintGit();
      mountRd();
    }, 700);
    /* composer 在两个宿主之间来回搬 → 盯住两个容器与卡内结构 */
    ["rd-input-slot", "sub-tasks"].forEach(function (id) {
      var host = $(id) || (id === "sub-tasks" ? document.querySelector("#sub-tasks .form") : null);
      if (host) new mo(scheduleSync).observe(host, { childList: true, subtree: false });
    });
    var form = document.querySelector("#sub-tasks .form");
    if (form) new mo(scheduleSync).observe(form, { childList: true, subtree: false });

    /* 选人状态可能被别处改写（模板回填 / 偏好预填 / 场景切换）：盯胶囊上的名字，
     * 它每次都由 syncCmpSelFace 重写 */
    var face = $("f-type-name");
    if (face) new mo(paint).observe(face, { childList: true, characterData: true, subtree: true });
    var sel = $("f-type");
    if (sel) {
      sel.addEventListener("change", paint);
      new mo(function () { clearTimeout(syncTimer); syncTimer = setTimeout(function () { paint(); }, 30); })
        .observe(sel, { childList: true, subtree: true, characterData: true });
    }
    /* 分支下拉的选项由 probeGit 异步灌入：重建或换值都重新量宽度。
     * probeGit 先 remove("hidden") 再灌 option，所以选项到达时也要再 paintGit 一次，
     * 否则「非 Git 目录」会比真分支多挂一拍 */
    var gitSel = $("f-git-rev");
    if (gitSel) {
      gitSel.addEventListener("change", paintGit);
      new mo(paintGit).observe(gitSel, { childList: true });
    }
    /* probeGit 用 .hidden 控制分支胶囊显隐 —— 盯 class 变化同步「非 Git 目录」说明 */
    var gitRow = $("row-git");
    if (gitRow) new mo(paintGit).observe(gitRow, { attributes: true, attributeFilter: ["class"] });

    document.addEventListener("focusin", paint, true);
    document.addEventListener("pointerdown", function (e) {
      if (!pickEl) return;
      /* 正在点自己的页签/胶囊：这一下要交给 click 处理。虽然 paint() 已有指纹挡着，
       * 这里再从源头掐一次，避免任何情况下在 mouseup 之前换掉按键下的节点。 */
      if (e.target && pickEl.contains(e.target)) return;
      paint();
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && wdEl && wdEl.classList.contains("ccx-editing")) setEditing(false);
    });
    /* 切语言 / 换肤：页签与胶囊文案、配色都要跟上 */
    new mo(function () { clearTimeout(syncTimer); syncTimer = setTimeout(paint, 40); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["lang", "data-lang", "data-skin", "data-layout"] });

    /* 窗口宽度变了 → 一行能放几枚也要重算（否则窄屏下末尾会被裁掉半枚） */
    var foldTimer = 0;
    window.addEventListener("resize", function () {
      clearTimeout(foldTimer);
      foldTimer = setTimeout(foldChips, 120);
    });
    bindFoldWatchers();
  }

  /* 容器宽度变化（换宿主 / 侧栏折叠 / 详情页右栏展开）与字体就绪都要重量一次 */
  function bindFoldWatchers() {
    if (!chipsEl) return;
    if (window.ResizeObserver && !chipsEl.__ccxRO) {
      chipsEl.__ccxRO = new window.ResizeObserver(function () { foldChips(); });
      chipsEl.__ccxRO.observe(chipsEl);
    }
    try {
      if (!chipsEl.__ccxFonts && document.fonts && document.fonts.ready && document.fonts.ready.then) {
        chipsEl.__ccxFonts = 1;
        document.fonts.ready.then(foldChips);
      }
    } catch (e) { /* 老内核没有 FontFaceSet，忽略 */ }
  }

  function boot() {
    sync();
    mountRd();
    watch();
    /* 数据是异步到的：flows 落定后再补一次 */
    var tries = 0;
    var timer = setInterval(function () {
      tries++;
      if (experts().length || tries > 20) {
        clearInterval(timer);
        sync();
      }
    }, 300);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  /* 守测/调试入口 */
  window.ccxComposer = { paint: paint, sync: sync, setEditing: setEditing };
})();
