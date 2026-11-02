/* 首页快捷入口：口语化标签覆盖层（2026-11-02）
 *
 * 为什么要有这一层：
 *   首页第一屏的四个 chip 原样搬的是 agents.py 的拟人名（快答 / 文书 / 调研员 / 译匠）。
 *   拟人名在专家面板、任务卡里是资产——那里用户已经决定"要干活了"，认个人格更容易信任；
 *   但在首页第一屏它是理解成本：用户得先把"调研员"翻译成"帮我查资料"，才知道点了会发生什么。
 *   而且四个词层级不齐——快答是动作、文书是产出物、调研员和译匠是职业，放在一排天然难比较。
 *
 * 改法：只换首页这一处的呈现，统一成「动词 + 对象」，并让点击预填一句可直接改的示例。
 *   不碰 agents.py —— 专家面板/任务卡/历史列表仍用拟人名，两处各司其职。
 *
 * 挂载方式：index.html 里在 app.js 之后 defer 加载。
 *   不读 app.js 的内部 const（WORKSPACE_MODES / workspaceChoice 都没挂 window，读不到），
 *   只包装 window.pickQuickType + 用 MutationObserver 装饰已渲染的 DOM，
 *   这样 app.js 换模式/切语言重绘时本层自动跟上，且原逻辑一行不动、删掉本文件即回滚。
 */
(function () {
  "use strict";

  /* flow id -> 首页标签 / 图标 / 预填示例。
   * label 与 sample 都要过 t()，所以文案本身即词条 key（i18n.js 里已补 EN）。 */
  var HOME = {
    direct: { label: "快速问答", icon: "i-chat",
      sample: "把这段材料里的关键结论挑出来，按重要性排序" },
    doc: { label: "写文档", icon: "i-file-text",
      sample: "写一份周报：本周完成……（补上你做的事），下周计划……" },
    research: { label: "做调研", icon: "i-file-search",
      sample: "调研三家做工业质检的公司，比较产品差异和报价" },
    translation: { label: "做翻译", icon: "i-lang",
      sample: "把下面这段英文译成中文，技术术语保留英文原词" },
    code: { label: "写代码", icon: "i-code",
      sample: "给这个函数加空值保护，并补一条回归测试" },
    defect_retro: { label: "故障复盘", icon: "i-clip-check",
      sample: "复盘这次线上报错：现象、时间线、根因、可复用的检查项" },
    tech_proposal: { label: "技术方案", icon: "i-layers",
      sample: "写一份缓存方案，比较 Redis 和进程内缓存，给出选型建议" }
  };

  var MARK = "data-home-label";

  function tr(key) {
    return typeof window.t === "function" ? window.t(key) : key;
  }

  function ico(id) {
    return '<svg class="ico" aria-hidden="true"><use href="#' + id + '"></use></svg>';
  }

  /* 从 onclick="pickQuickType('xxx')" 反解 flow id —— 不依赖 app.js 的内部变量。 */
  function chipFlowId(btn) {
    var m = /pickQuickType\(['"]([^'"]+)['"]\)/.exec(btn.getAttribute("onclick") || "");
    return m ? m[1] : "";
  }

  function decorate() {
    var box = document.getElementById("cmp-quick");
    if (!box) return;
    var chips = box.querySelectorAll(".cmp-chip");
    for (var i = 0; i < chips.length; i++) {
      var btn = chips[i];
      var id = chipFlowId(btn);
      var conf = HOME[id];
      if (!conf) continue;
      var label = tr(conf.label);
      // 重绘后节点是新的；同节点重复装饰会反复写 innerHTML，用标记挡住
      if (btn.getAttribute(MARK) === label) continue;
      btn.setAttribute(MARK, label);
      // 原来的头像（首字圆圈）在有图标后是重复信息，整块替换掉
      btn.innerHTML = ico(conf.icon) + "<span>" + (typeof window.esc === "function"
        ? window.esc(label) : label) + "</span>";
      btn.setAttribute("title", label + " · " + tr(conf.sample));
    }
  }

  function wrapPick() {
    var orig = window.pickQuickType;
    if (typeof orig !== "function" || orig.__homeChips) return;
    var wrapped = function (id) {
      var ret = orig.apply(this, arguments);
      var conf = HOME[id];
      var goal = document.getElementById("f-goal");
      // 只在目标框为空时预填：用户已经写了一句话，就别拿示例覆盖他
      if (conf && goal && !String(goal.value || "").trim()) {
        goal.value = tr(conf.sample);
        try {
          goal.dispatchEvent(new Event("input", { bubbles: true }));
        } catch (e) { /* 老内核没有构造器，值已写入即可 */ }
        goal.focus();
        try { goal.setSelectionRange(0, 0); } catch (e) { /* 非文本控件忽略 */ }
      }
      return ret;
    };
    wrapped.__homeChips = true;
    window.pickQuickType = wrapped;
  }

  function watch() {
    var box = document.getElementById("cmp-quick");
    if (!box || typeof window.MutationObserver !== "function") return;
    var timer = 0;
    new MutationObserver(function () {
      clearTimeout(timer);
      // 合并同一帧内的多次重绘，避免连续装饰
      timer = setTimeout(decorate, 0);
    }).observe(box, { childList: true });
  }

  function boot() {
    wrapPick();
    decorate();
    watch();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // 供守测/调试直接查表
  window.HOME_CHIPS = HOME;
})();
