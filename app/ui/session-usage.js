/* session-usage.js（2026-11-03）—— 对话条上方「本会话用量」一行 + 明细浮层
 *
 * 用户给了一张桌面端观感截图（箭头指向输入框上方那行小字）：
 *   「共 521.3K Token · 49.88 · DeepSeek-V4.1-Flash × 3  ⧉」
 * 点它弹出「本会话用量明细」：标题 + 总计 + 一张按模型列的表
 * （模型 / 调用 / Token / 费用），副标题「用量统计随会话动态更新」。
 *
 * 三条硬约束：
 *   1. **不新增第二份真相**：数字全部来自 /api/usage/session，即用量台账
 *      （与「用量统计」页同源）。不读 run.json 上的 tokens 副本——回填过的
 *      老运行两边会对不上，同一件事显示两个数比不显示更糟。
 *   2. **没有数据就整条不出现**：calls=0 时 CSS 收成 display:none（DESIGN.md
 *      §0「有事给事，没事不打扰」）；也不显示「¥0」，未标价模型单独说明。
 *   3. **只读不写**：不碰 #rd-chat-flow / .rd-chat-bar 内部，只在其前插一个
 *      兄弟节点；删掉 index.html 的一行 link + 一行 script 即整体回滚。
 *
 * 「本会话」= 当前对话面板正在看的那次运行（#rd-chat-flow 的 data-run-id，
 * 与 chatRunId 同源）。追话会另开一轮运行，所以数字会从新一轮重新累计——
 * 这也是产品话术里「新一轮」的意思。
 */
(function () {
  "use strict";
  if (window.__sessionUsageMounted) return;
  window.__sessionUsageMounted = true;

  var LIVE_MS = 5000;     /* 运行中：5s 一次（台账秒级在涨） */
  var IDLE_MS = 20000;    /* 空闲：20s 一次（只为了别处补记的账能落进来） */
  var MIN_MS = 2000;      /* 任何两次请求之间的硬下限 */

  var wrap = null;        /* .ssx-wrap 整块 */
  var anchorEl = null;    /* .ssx-anchor 文字起点（浮层定位父级） */
  var lineEl = null;      /* .ssx-line 汇总行（button） */
  var copyEl = null;      /* .ssx-copy 复制按钮 */
  var popEl = null;       /* .ssx-pop 明细浮层 */
  var totalEl = null;     /* 浮层右上「总计 …」 */
  var tbodyEl = null;     /* 浮层表体 */
  var noteEl = null;      /* 浮层底部说明（未标价 / 失败调用） */

  var data = null;        /* 最近一次接口结果 */
  var runId = "";         /* 这批数据对应的运行 id（防串会话） */
  var lastAt = 0;
  var fetching = false;
  var open = false;
  var timer = 0;
  var debounce = 0;

  function $(id) { return document.getElementById(id); }
  function tr() {
    return typeof window.t === "function" ? window.t.apply(null, arguments) : arguments[0];
  }
  function tok(n) {
    if (typeof window.fmtTok === "function") return window.fmtTok(n);
    return String(Number(n) || 0);
  }
  /* 台账的金额语义是「费用（¥）」（core/usage.py 的 cost_usd 沿用历史列名）。
   * 这里用 ¥，不跟用量统计页的 "$" 混——它那边是历史遗留，另案修。 */
  function money(v) {
    v = Number(v) || 0;
    if (v <= 0) return "";
    if (v < 0.01) return "¥" + v.toFixed(4);
    if (v < 1) return "¥" + v.toFixed(3);
    return "¥" + v.toFixed(2);
  }

  /* ------------------------------------------------------------ 会话识别 */

  function chatBox() { return $("rd-chat"); }

  function currentRunId() {
    var flow = $("rd-chat-flow");
    var id = (flow && flow.dataset && flow.dataset.runId) || "";
    if (id) return id;
    /* 兜底：对话面板还没渲染出 run 戳时，用详情状态；再不行才看 lastRun。
     * 顺序不能反——lastRun 可能是别的任务的运行，串了就是「本会话」显示别人
     * 的账。 */
    try {
      if (typeof S !== "undefined" && S) {
        if (S.detailRunId) return S.detailRunId;
        if (S.lastRun && S.lastRun.id) return S.lastRun.id;
      }
    } catch (e) { /* 状态没就绪，按空处理 */ }
    return "";
  }

  function isLive() {
    var st = $("rd-chat-run-status");
    return !!(st && !st.classList.contains("hidden"));
  }

  /* ------------------------------------------------------------ DOM 构建 */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function build() {
    wrap = el("div", "ssx-wrap is-empty");
    anchorEl = el("div", "ssx-anchor");

    lineEl = el("button", "ssx-line");
    lineEl.type = "button";
    lineEl.id = "ssx-line";
    lineEl.setAttribute("aria-expanded", "false");
    lineEl.setAttribute("aria-controls", "ssx-pop");
    lineEl.addEventListener("click", function () { setOpen(!open); });

    copyEl = el("button", "ssx-copy");
    copyEl.type = "button";
    copyEl.setAttribute("aria-label", tr("复制会话用量"));
    copyEl.title = tr("复制会话用量");
    copyEl.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-copy"></use></svg>';
    copyEl.addEventListener("click", function (e) {
      e.stopPropagation();
      copyLine();
    });

    popEl = el("div", "ssx-pop hidden");
    popEl.id = "ssx-pop";
    popEl.setAttribute("role", "dialog");
    popEl.setAttribute("aria-label", tr("本会话用量明细"));

    var head = el("div", "ssx-pop-head");
    head.appendChild(el("b", "ssx-pop-title", tr("本会话用量明细")));
    totalEl = el("span", "ssx-pop-total");
    head.appendChild(totalEl);

    popEl.appendChild(head);
    popEl.appendChild(el("p", "ssx-pop-sub", tr("用量统计随会话动态更新。")));

    var table = el("table", "ssx-table");
    var thead = el("thead");
    var hrow = el("tr");
    var heads = [[tr("模型"), ""], [tr("调用"), "num"], [tr("Token"), "num"], [tr("费用"), "num"]];
    heads.forEach(function (h) {
      var th = el("th", h[1], h[0]);
      if (h[1]) th.setAttribute("scope", "col");
      hrow.appendChild(th);
    });
    thead.appendChild(hrow);
    table.appendChild(thead);
    tbodyEl = el("tbody");
    table.appendChild(tbodyEl);
    popEl.appendChild(table);

    noteEl = el("p", "ssx-note hidden");
    popEl.appendChild(noteEl);

    wrap.appendChild(anchorEl);
    anchorEl.appendChild(lineEl);
    anchorEl.appendChild(copyEl);
    anchorEl.appendChild(popEl);
  }

  /* ------------------------------------------------------------ 渲染 */

  function summaryLine(d) {
    var parts = [tr("共 {0} Token", tok(d.tokens))];
    var m = money(d.cost);
    if (m) parts.push(m);
    else if (Number(d.tokens) > 0) parts.push(tr("未计价"));
    var models = d.models || [];
    if (models.length === 1) {
      parts.push(String(models[0].model || "") + " × " + (Number(models[0].calls) || 0));
    } else if (models.length > 1) {
      parts.push(tr("{0} 等 {1} 个模型", models[0].model, models.length));
    }
    return parts.join(" · ");
  }

  function paint() {
    if (!wrap || !lineEl) return;
    var d = data || {};
    var calls = Number(d.calls) || 0;
    var empty = calls <= 0;
    /* 面板整体隐藏（非对话型运行 / 别的页签）时不改状态：留着上次读数，
     * 等它再显出来时由 refresh 决定新旧 */
    if (empty) {
      wrap.classList.add("is-empty");
      lineEl.textContent = "";
      if (open) setOpen(false);
      return;
    }
    wrap.classList.remove("is-empty");
    /* 挂载那一刻若还没数据，本行是 display:none（量不到宽度），左沿补齐会
     * 提前返回——首次显形时必须重量一次；下一帧再补一次，等输入卡换行/滚动条
     * 落定后的真实左沿（第一版就吃过「量早了 6px」的亏） */
    alignBar();
    if (window.requestAnimationFrame) window.requestAnimationFrame(alignBar);
    lineEl.textContent = summaryLine(d);    lineEl.title = tr("点击查看本会话用量明细");
    if (open) paintPop(d);
  }

  function paintPop(d) {
    if (!totalEl || !tbodyEl) return;
    var m = money(d.cost);
    totalEl.textContent = tr("总计 {0} Token · {1}",
      tok(d.tokens), m || tr("未计价"));

    while (tbodyEl.firstChild) tbodyEl.removeChild(tbodyEl.firstChild);
    (d.models || []).forEach(function (row) {
      var trEl = el("tr");
      var c1 = el("td", "mdl", String(row.model || "unknown"));
      c1.title = String(row.model || "");
      trEl.appendChild(c1);

      var c2 = el("td", "num", String(Number(row.calls) || 0));
      trEl.appendChild(c2);

      var c3 = el("td", "num", tok(row.tokens));
      c3.title = tr("输入 {0} · 输出 {1}", tok(row.input), tok(row.output));
      trEl.appendChild(c3);

      /* 没折出钱就写「—」，不写 ¥0：未标价 ≠ 免费（DESIGN.md §0 数字不撒谎） */
      var c4 = el("td", "num", money(row.cost) || "—");
      trEl.appendChild(c4);
      tbodyEl.appendChild(trEl);
    });

    var notes = [];
    if (Number(d.unpriced) > 0) {
      notes.push(tr("有 {0} 个模型未标价，费用不含它们（设置 → 模型可手标单价）。",
        Number(d.unpriced)));
    }
    if (Number(d.failed) > 0) {
      /* 措辞不与费用挂钩：未标价模型的失败调用不产生费用，说「仍会计费」会自相矛盾 */
      notes.push(tr("其中 {0} 次调用失败，Token 已计入。", Number(d.failed)));
    }
    if (notes.length) {
      noteEl.textContent = notes.join(" ");
      noteEl.classList.remove("hidden");
    } else {
      noteEl.textContent = "";
      noteEl.classList.add("hidden");
    }
  }

  /* ------------------------------------------------------------ 展开/收起 */

  function setOpen(on) {
    open = !!on;
    if (!popEl) return;
    popEl.classList.toggle("hidden", !open);
    if (lineEl) lineEl.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      alignBar();
      paintPop(data || {});
      refresh(true);            /* 打开就取一次新鲜的，不吃 20s 前的缓存值 */
    }
  }

  function copyLine() {
    var text = (lineEl && lineEl.textContent) || "";
    if (!text) return;
    var done = function () {
      if (typeof window.toast === "function") window.toast(tr("已复制会话用量"));
    };
    var fallback = function () {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "readonly");
        ta.style.position = "fixed";
        ta.style.top = "-1000px";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        done();
      } catch (e) { /* 复制失败静默：辅助信息不该打断对话 */ }
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
        return;
      }
    } catch (e) { /* 走 fallback */ }
    fallback();
  }

  /* ------------------------------------------------------------ 取数 */

  function refresh(force) {
    var box = chatBox();
    /* 对话面板藏着（非对话型运行 / 停在别的页签）就别发请求：这条看不见 */
    if (!box || box.classList.contains("hidden")) return;
    var rid = currentRunId();
    if (!rid) {
      if (data) { data = null; runId = ""; paint(); }
      return;
    }
    var now = Date.now();
    if (!force && (fetching || now - lastAt < MIN_MS)) return;
    if (rid !== runId) {
      /* 换会话：先清掉旧读数，别让上一轮的账挂在新一轮上 */
      runId = rid;
      data = null;
      paint();
    }
    fetching = true;
    lastAt = now;
    var url = "/api/usage/session?run=" + encodeURIComponent(rid);
    var req;
    if (typeof window.api === "function") {
      req = window.api(url);
    } else {
      req = fetch(url).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
    }
    req.then(function (d) {
      if (runId !== rid) return;     /* 期间已切走：这批数据作废 */
      data = d || null;
      paint();
    }).catch(function () {
      /* 统计失败不弹错：它只是辅助信息，保留上一次读数即可 */
    }).then(function () { fetching = false; });
  }

  function tick() {
    if (document.visibilityState === "hidden") return;
    var gap = isLive() ? LIVE_MS : IDLE_MS;
    if (Date.now() - lastAt < gap) return;
    refresh();
  }

  function schedule() {
    clearTimeout(debounce);
    debounce = setTimeout(function () { refresh(); }, 350);
  }

  /* ------------------------------------------------------------ 挂载与跟随 */

  function mount() {
    var box = chatBox();
    var bar = box && box.querySelector(".rd-chat-bar");
    if (!box || !bar) return false;
    if (!wrap) build();
    /* 贴在输入卡**上方**（参考图就是这一位置），且是 .rd-chat-bar 的前一个
     * 兄弟——不是塞进 bar 里面，免得跟着 bar 的 max-width/margin 一起漂 */
    if (wrap.parentElement !== box || wrap.nextElementSibling !== bar) {
      box.insertBefore(wrap, bar);
    }
    alignBar();
    return true;
  }

  /* 文字起点对齐输入卡左沿：工作台布局下输入卡是 `margin: 8px 26px`，base
   * 布局是 `calc(100% - 48px)` 居中，写死任何一套都会在另一套里错位，所以
   * 量一次实测左沿补到 padding 上（尺寸变化时重量）。 */
  function alignBar() {
    var box = chatBox();
    var bar = box && box.querySelector(".rd-chat-bar");
    if (!box || !bar || !wrap) return;
    var b = bar.getBoundingClientRect();
    var w = wrap.getBoundingClientRect();
    if (!b.width || !w.width) return;
    var pad = Math.round(b.left - w.left);
    if (pad < 0) pad = 0;
    if (wrap.dataset.pad === String(pad)) return;
    wrap.dataset.pad = String(pad);
    wrap.style.paddingLeft = pad + "px";
    wrap.style.paddingRight = pad + "px";
  }

  function watch() {
    if (!window.MutationObserver) return;
    var flow = $("rd-chat-flow");
    if (flow) {
      new MutationObserver(schedule)
        .observe(flow, { attributes: true, attributeFilter: ["data-run-id"] });
    }
    var st = $("rd-chat-run-status");
    if (st) {
      new MutationObserver(function () { schedule(); })
        .observe(st, { attributes: true, attributeFilter: ["class"] });
    }
    var box = $("rd-chat");
    if (box) {
      /* 有人重排 .rd-chat 子节点（切换对话/重建）时重新贴位 */
      new MutationObserver(function () { mount(); }).observe(box, { childList: true });
    }
    var bar = box && box.querySelector(".rd-chat-bar");
    if (bar && window.ResizeObserver) {
      new ResizeObserver(alignBar).observe(bar);
    }
    if (box && window.ResizeObserver) {
      /* 侧栏/面板收放会改对话列宽度，输入卡的左沿跟着变——一起盯 */
      new ResizeObserver(alignBar).observe(box);
    }
    var flowEl = $("rd-chat-flow");
    if (flowEl && window.ResizeObserver) {
      /* 滚动条出现/消失会改内容宽度；输入卡按 margin 定位，宽度不变时左沿
       * 也可能变，所以连对话流也盯一眼 */
      new ResizeObserver(alignBar).observe(flowEl);
    }
    window.addEventListener("resize", alignBar);
    window.addEventListener("focus", function () { refresh(); });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") refresh();
    });
    document.addEventListener("pointerdown", function (e) {
      if (!open || !wrap) return;
      if (wrap.contains(e.target)) return;
      setOpen(false);
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !open) return;
      setOpen(false);
      if (lineEl) lineEl.focus();
    });
  }

  function boot() {
    var tries = 0;
    (function attempt() {
      tries++;
      if (mount()) {
        watch();
        refresh(true);
        clearInterval(timer);
        timer = setInterval(tick, 5000);
        return;
      }
      if (tries < 40) setTimeout(attempt, 250);   /* 最多等 10s，之后不再重试 */
    })();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  /* 守测/探针入口 */
  window.ssxSessionUsage = {
    refresh: refresh, paint: paint, setOpen: setOpen, alignBar: alignBar,
    currentRunId: currentRunId,
    snapshot: function () { return data; }
  };
})();
