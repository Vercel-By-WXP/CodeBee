/* chat-quickprompts.js（2026-11-03）—— 对话条「快捷指令」按钮 + 快捷指令浮层
 *
 * 参考桌面端观感截图（红箭头指向输入行右端那颗圆形小按钮，点开一枚向上弹出的
 * 快捷指令菜单，每条指令带「点击后发送 / 编辑 →」两个动作）：
 *   ① 按钮挂在 .rd-chat-tools 里、对话模型选择之前（与参考图同位）；
 *   ② 点开浮层列出当前可用的快捷指令：点条目主体＝把指令作为对话消息直接发送
 *      （与按 Enter 同一条 window.chatSend 链路，排队送达），点「编辑 →」＝
 *      填入输入框手动改完再发；
 *   ③ 输入框里已有草稿时：直接发送只发指令本身、草稿原样保留回输入框；「编辑」
 *      把指令追加在草稿下一行，不覆盖用户写的内容。
 *
 * 落点说明（DESIGN.md §0 注意力守恒）：只挂在详情页对话条上——「继续执行」这类
 * 指令只有在有运行上下文时才有意义；首页输入卡是新建任务，「一键开始」已由专家
 * 胶囊行承担，再摆一颗点了没有对应语义的按钮就是假控件。
 *
 * 三条硬约束：
 *   1. 发送只复用 window.chatSend（真源），不另写第二条 /chat 请求；
 *   2. 浮层挂 document.body、用按钮实测坐标做 absolute 定位——.rd-chat-bar
 *      自带 overflow:auto + sticky + backdrop-filter（z-index:8），浮层挂它
 *     里面会被裁剪（实拍坐实：左侧 45px 与顶部两行全被吃掉）；而 position:fixed
 *      的包含块会被 #run-detail 的 backdrop-filter 改写（11-03 两起浮层飞走的
 *      血账），body 上的 absolute 两个坑都绕开；
 *   3. 删掉 index.html 的一行 link + 一行 script 即整体回滚。
 */
(function () {
  "use strict";
  if (window.__chatQuickPromptsMounted) return;
  window.__chatQuickPromptsMounted = true;

  var btn = null;         /* #rd-chat-qp 圆形按钮 */
  var pop = null;         /* #rd-chat-qp-pop 快捷指令浮层 */
  var wrapEl = null;      /* .qp-wrap 按钮包壳（浮层定位父级） */
  var open = false;

  function $(id) { return document.getElementById(id); }
  function tr() {
    return typeof window.t === "function" ? window.t.apply(null, arguments) : arguments[0];
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ------------------------------------------------------------ 指令清单 */
  /* title＝浮层里的短标签；text＝真正发出的消息。text 与 title 相同时不重复
   * 渲染描述行。新增指令只动这一张表。 */
  function prompts() {
    return [
      { title: tr("继续执行"), text: tr("继续执行") },
      { title: tr("检查后再继续"),
        text: tr("先检查已完成的部分，发现问题直接修复，然后继续往下做。") },
      { title: tr("总结进度"),
        text: tr("总结当前进度：已完成什么、还剩什么、下一步是什么。") },
    ];
  }

  /* ------------------------------------------------------------ 渲染 */

  function renderItems() {
    if (!pop) return;
    while (pop.firstChild) pop.removeChild(pop.firstChild);
    prompts().forEach(function (p) {
      var item = el("div", "qp-item");

      var send = el("button", "qp-send");
      send.type = "button";
      send.setAttribute("role", "menuitem");
      send.title = p.text;
      send.appendChild(el("span", "qp-title", p.title));
      if (p.text !== p.title) send.appendChild(el("span", "qp-desc", p.text));
      var foot = el("span", "qp-foot");
      foot.appendChild(el("span", "qp-hint", tr("点击后发送")));
      send.appendChild(foot);
      send.addEventListener("click", function () { sendNow(p.text); });
      item.appendChild(send);

      var edit = el("button", "qp-edit");
      edit.type = "button";
      edit.setAttribute("role", "menuitem");
      edit.textContent = tr("编辑") + " →";
      edit.title = tr("先填入输入框，改完再发");
      edit.addEventListener("click", function (e) {
        e.stopPropagation();
        editInto(p.text);
      });
      item.appendChild(edit);

      pop.appendChild(item);
    });
  }

  /* ------------------------------------------------------------ 发送 / 填入 */

  function autoHeight(ta) {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }

  /* 直接发送：把指令写进输入框后走 window.chatSend（与 Enter 同一条链路）。
   * chatSend 在第一个 await 之前就同步读走了 ta.value，所以随后把草稿放回
   * 输入框是安全的；它结尾只在「框里还是指令原文」时才清空，草稿不会被吞。 */
  function sendNow(text) {
    var ta = $("rd-chat-input");
    if (!ta || typeof window.chatSend !== "function") return;
    var prev = ta.value;
    ta.value = text;
    window.chatSend("queue");
    if (prev && prev.trim()) {
      ta.value = prev;
      autoHeight(ta);
    }
    setOpen(false);
  }

  /* 编辑：草稿为空就整句填入；有草稿追加在下一行，不覆盖。 */
  function editInto(text) {
    var ta = $("rd-chat-input");
    if (!ta) return;
    var prev = ta.value.trim();
    ta.value = prev ? prev + "\n" + text : text;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    setOpen(false);
    ta.focus();
    try { ta.selectionStart = ta.selectionEnd = ta.value.length; } catch (e) { /* 旧浏览器 */ }
  }

  /* ------------------------------------------------------------ 开合与钳位 */

  function place() {
    if (!pop || pop.classList.contains("hidden") || !btn) return;
    var r = btn.getBoundingClientRect();
    var sx = window.scrollX || window.pageXOffset || 0;
    var sy = window.scrollY || window.pageYOffset || 0;
    var vw = window.innerWidth;
    /* 右对齐锚点（按钮右沿外扩 4px），左沿出视口就钳回 8px */
    var w = pop.offsetWidth;
    var left = r.right + 4 - w;
    if (left < 8) left = 8;
    if (left + w > vw - 8) left = vw - 8 - w;
    /* 向上弹：浮层底沿 = 按钮上沿 - 10；高度钳到按钮上方剩余空间 */
    var above = Math.round(r.top - 12);
    pop.style.maxHeight = (above > 0 ? above : 300) + "px";
    pop.style.left = Math.round(left + sx) + "px";
    pop.style.top = Math.round(r.top + sy - pop.offsetHeight - 10) + "px";
  }

  function setOpen(on) {
    open = !!on;
    if (!pop || !btn) return;
    if (open) renderItems();     /* 每次开都重画：语言切换后文案跟着走 */
    pop.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      if (window.requestAnimationFrame) window.requestAnimationFrame(place);
      else place();
    }
  }

  /* ------------------------------------------------------------ 挂载 */

  function build() {
    wrapEl = el("span", "qp-wrap");
    wrapEl.id = "rd-chat-qp-wrap";

    btn = el("button", "ghost qp-btn");
    btn.type = "button";
    btn.id = "rd-chat-qp";
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "rd-chat-qp-pop");
    btn.title = tr("快捷指令：点一条直接发送，或先改再发");
    btn.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-chat"></use></svg>';
    btn.addEventListener("click", function () { setOpen(!open); });

    pop = el("div", "qp-pop hidden");
    pop.id = "rd-chat-qp-pop";
    pop.setAttribute("role", "menu");
    pop.setAttribute("aria-label", tr("快捷指令"));

    wrapEl.appendChild(btn);
    /* 浮层挂 body：.rd-chat-bar 的 overflow:auto/sticky/backdrop-filter 会把
     * 挂在它里面的浮层裁掉（实拍坐实），body 上的 absolute 不受这些影响 */
    document.body.appendChild(pop);
  }

  function mount() {
    var tools = document.querySelector(".rd-chat-tools");
    if (!tools) return false;
    if (!wrapEl) build();
    if (wrapEl.parentElement === tools) return true;
    /* 与参考图同位：对话模型选择之前；模型选择被收起时退到发送区之前 */
    var model = tools.querySelector("#rd-model-wrap");
    var sendArea = tools.querySelector(".chat-send-actions");
    if (model) tools.insertBefore(wrapEl, model);
    else if (sendArea) tools.insertBefore(wrapEl, sendArea);
    else tools.appendChild(wrapEl);
    return true;
  }

  function watch() {
    var bar = document.querySelector(".rd-chat-bar");
    if (bar && window.MutationObserver) {
      /* 对话条被整体重建（切换运行 / 换皮肤重排）时重新挂回去 */
      new MutationObserver(function () { mount(); })
        .observe(bar, { childList: true, subtree: true });
    }
    window.addEventListener("resize", function () { if (open) place(); });
    document.addEventListener("pointerdown", function (e) {
      if (!open || !wrapEl || !pop) return;
      var t = e.target;
      if (wrapEl.contains(t) || pop.contains(t)) return;
      setOpen(false);
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !open) return;
      setOpen(false);
      if (btn) btn.focus();
    });
  }

  function boot() {
    var tries = 0;
    (function attempt() {
      tries++;
      if (mount()) { watch(); return; }
      if (tries < 40) setTimeout(attempt, 250);   /* 最多等 10s，对话条还没渲染就不挂 */
    })();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  /* 守测/探针入口 */
  window.rdChatQuickPrompts = {
    mount: mount, setOpen: setOpen, place: place,
    sendNow: sendNow, editInto: editInto,
    prompts: prompts,
    isOpen: function () { return open; }
  };
})();
