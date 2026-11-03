/* demo-parity.js（2026-11-02，「完全按照 demo 来」的装饰层）
 * ---------------------------------------------------------------------------
 * 两件事，都是 demo 首页的视觉件，零业务逻辑、零 API 调用：
 *   1. 往 <main> 注入场景层 `.cb-scene`：5 个同心环 + 24 片漂浮菱形（demo .ring / .kite）。
 *   2. 把「任务信息」弹层里的操作按钮收纳好（demo 的 56px 任务条只放 3 个 ghost 按钮）。
 * 删掉 index.html 里 demo-parity.css 的 <link> 即整层失效（注入节点无样式、
 * 且 pointer-events:none，不会造成任何视觉或交互残留）。
 *
 * 2026-11-03 修正：**不再**把 `#home-adv` 里的「最近任务」搬到首页底部。
 * 原生化①（chat-stream.js，2026-11-01 决策 15）已经在 composer 下方挂了
 * `#rd-chat-feed`「最近动态」，再搬一个 `.rd-recent-tasks` 就是两块同名列表
 * （实测首页出现两个「最近动态」）。只留原生那块，样式在 CSS 里按 demo .home-recent 摊平。
 */
(function () {
  "use strict";
  if (window.__cbDemoParityScene) return;
  window.__cbDemoParityScene = true;

  /* demo app.js 的菱形种子：[left%, top%, 尺寸基数, 初始旋转]。
     位置刻意避开中间 788 内容列，只在两侧留白里飘。 */
  var KITE_SEED = [
    [5, 9, 26, -18], [10, 23, 13, 12], [4, 37, 30, -8], [13, 52, 17, 18], [6, 67, 21, -24],
    [22, 6, 15, 8], [34, 3, 21, -14], [47, 4, 12, 20], [61, 5, 19, -6], [75, 3, 27, 14], [88, 8, 14, -20],
    [93, 19, 22, 10], [85, 30, 13, -16], [96, 39, 29, 6], [79, 45, 16, 22], [89, 54, 23, -10], [72, 60, 11, 16],
    [86, 68, 18, -14], [65, 73, 13, 8], [51, 79, 22, -22], [35, 84, 12, 14], [19, 88, 19, -6], [7, 80, 14, 20],
    [44, 62, 10, -12], [58, 89, 16, 10]
  ];
  var KITE_PATH = "M12 1.8 18.6 12 12 22.2 5.4 12 12 1.8Z";

  function buildScene() {
    var main = document.querySelector("main");
    if (!main) return;
    if (main.querySelector(":scope > .cb-scene")) return;
    var scene = document.createElement("div");
    scene.className = "cb-scene";
    scene.setAttribute("aria-hidden", "true");

    var html = "";
    for (var i = 1; i <= 5; i++) html += '<div class="ring r' + i + '"></div>';
    html += '<div class="kitefield">';
    for (var k = 0; k < KITE_SEED.length; k++) {
      var h = KITE_SEED[k];
      var size = Math.round(h[2] * 1.6);
      html += '<svg class="kite" viewBox="0 0 24 24" aria-hidden="true" focusable="false" style="' +
        "left:" + h[0] + "%;top:" + h[1] + "%;width:" + size + "px;height:" + size + "px;" +
        "--rot:" + h[3] + "deg;opacity:" + (0.6 + (k % 4) * 0.12).toFixed(2) + ";" +
        "animation-delay:" + (-k * 0.62).toFixed(2) + "s;animation-duration:" + (9 + (k % 5) * 1.6).toFixed(1) + "s" +
        '"><path d="' + KITE_PATH + '"></path></svg>';
    }
    html += "</div>";
    scene.innerHTML = html;
    main.insertBefore(scene, main.firstChild);
  }

  /* 任务条操作收纳（demo .task-head 右侧只有 3 个 ghost 按钮）。
     产品的 .rd-actions 塞了 12 个按状态显隐的按钮：立即重试 / 编辑重试 / 继续连载 /
     提前完结 / 归档 / 分享页 / 删除记录 … 在 56px 高的横条里会把标题挤成两个字。
     这里把按钮**移进**右栏面板的「操作」区（#rd-actions-shelf，2026-11-03 之前是
     挂在 #rd-meta-popover 里，任务信息改浮层后单独有个宿主），
     顶栏只留仪表盘（任务信息）、••• 与任务动态三个图标。移的是同一个节点，
     原有显隐/事件/aria 全部照旧。 */
  function tidyDetailActions() {
    var actions = document.querySelector("#rd-rail > .detail-head > .rd-actions");
    var shelf = document.getElementById("rd-actions-shelf") ||
                document.getElementById("rd-meta-popover");
    if (!actions || !shelf) return;
    var body = shelf.querySelector(".cb-shelf-body");
    if (!body) {
      body = document.createElement("div");
      body.className = "cb-shelf-body";
      shelf.appendChild(body);
    }
    if (!shelf.querySelector(".cb-shelf-h")) {
      var head = document.createElement("div");
      head.className = "cb-shelf-h";
      head.textContent = "操作";
      shelf.insertBefore(head, body);
    }
    var btns = actions.querySelectorAll(":scope > button");
    for (var i = 0; i < btns.length; i++) {
      var b = btns[i];
      if (b.id === "rd-more-toggle" || b.id === "rd-context-toggle" ||
          b.id === "rd-meta-toggle") continue;
      if (b.parentElement !== body) body.appendChild(b);
    }
  }

  /* ---- 并排浏览器：把右上角常驻分区面板退回「⋯」弹出式（2026-11-03）---------
     用户两次贴图「并排浏览器实际挡住了页面」。让位本身在 demo-parity.css 第 12 节
     （main 右内距 = 面板宽），但内容列收缩后只剩 450 上下，常驻面板 188 + 让位 204
     又把正文挤到了 194px。所以并排态把面板收起，正文吃满整宽；关掉并排再恢复。
     不动 app.js：它的 toggleRdTabsMore 对 .is-pinned 直接 return，这里只要摘掉
     is-pinned，面板就回到「⋯」开合的弹出式（fixed，点击定位那套照旧生效）。
     用 MutationObserver 盯 body 的 class（split 开关只改这一个），幂等。 */
  var SPLIT_MIN = 901;      // 更窄时 #sub-browser 是整屏接管，正文本来就看不见
  function syncSplitPanel() {
    var panel = document.getElementById("rd-tabs-more");
    if (!panel) return;
    var btn = document.getElementById("rd-tabs-more-btn");
    var on = document.body.classList.contains("browser-split-open") &&
             window.innerWidth >= SPLIT_MIN;
    // 按元素自身状态判等，不缓存开关：面板若被别处重建也能自己纠回来
    if (on === !panel.classList.contains("is-pinned")) return;
    // 弹出式用 inline left/top 定位；两种形态切换前清掉，否则会压过类里的坐标
    panel.style.left = "";
    panel.style.top = "";
    if (on) {
      panel.classList.add("hidden");
      panel.classList.remove("is-pinned");
    } else {
      panel.classList.remove("hidden");
      panel.classList.add("is-pinned");
    }
    if (btn) btn.setAttribute("aria-expanded", "false");
  }
  function watchSplit() {
    syncSplitPanel();
    if (window.MutationObserver) {
      new MutationObserver(syncSplitPanel).observe(document.body, {
        attributes: true, attributeFilter: ["class"]
      });
    }
    window.addEventListener("resize", syncSplitPanel);
  }

  function mount() {
    buildScene();
    tidyDetailActions();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { mount(); watchSplit(); }, { once: true });
  } else {
    mount();
    watchSplit();
  }
  // 首页是 showHome() 动态翻出来的，DOM 就绪后再补一次（幂等）
  document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
