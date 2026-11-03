/* run-config.js（2026-11-03）—— 输入行「运行设置」胶囊 + 分段选择浮层
 *
 * 只做三件事：把 #f-mode / #f-thinking 的选项渲染成短名胶囊、把当前值写到胶囊上、
 * 点选时回写 select.value 并派发 change。**三个 select 仍是唯一真源**：id、option、
 * data-i18n 全部原样保留，所以取值（S.typeTouched / 提交体）、onTypeChange、
 * refreshEstimate、模板回填、语言切换都零改动。
 *
 * 短名规则与 app.js 的 cmpSelShort 一致：「自动（推荐）：按任务类型与风险动态决定步骤」
 * → 「自动」。这里自己实现一份是为了不依赖 app.js 的内部函数（并行会话会整文件重写它）。
 */
(function () {
  "use strict";
  if (window.__runConfigMounted) return;
  window.__runConfigMounted = true;

  var MODE = "f-mode";
  var THINK = "f-thinking";
  var TIMER = 0;

  function $(id) { return document.getElementById(id); }

  function short(text) {
    return String(text || "").split(/[：:]/)[0].replace(/[（(].*?[）)]/g, "").trim() || "";
  }

  /* 整句说明：去掉「自动（推荐）：」这种前缀，只留解释部分 */
  function explain(text) {
    var s = String(text || "").trim();
    var i = s.search(/[：:]/);
    return i >= 0 ? s.slice(i + 1).trim() : s;
  }

  function selectedText(sel) {
    var opt = sel && sel.options[sel.selectedIndex];
    return opt ? opt.textContent : "";
  }

  function buildChips(sel, box) {
    if (!sel || !box) return;
    var opts = Array.prototype.slice.call(sel.options).filter(function (o) { return o.value !== ""; });
    var key = opts.map(function (o) { return o.value + "|" + short(o.textContent); }).join(",");
    if (box.dataset.key === key) return;   /* 选项集合与文案都没变，不重建（保住焦点） */
    box.dataset.key = key;
    box.textContent = "";
    opts.forEach(function (opt) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "rc-chip";
      chip.dataset.value = opt.value;
      chip.setAttribute("role", "radio");
      chip.textContent = short(opt.textContent) || opt.value;
      chip.title = String(opt.textContent || "").trim();
      chip.addEventListener("click", function () {
        if (sel.value !== opt.value) {
          sel.value = opt.value;
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
        paint();
      });
      chip.addEventListener("keydown", onChipKey);
      box.appendChild(chip);
    });
  }

  /* 单选组键盘行为：方向键在组内移动并即时选中 */
  function onChipKey(e) {
    var chips = Array.prototype.slice.call(e.currentTarget.parentElement.children);
    var i = chips.indexOf(e.currentTarget);
    var next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % chips.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + chips.length) % chips.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = chips.length - 1;
    if (next < 0) return;
    e.preventDefault();
    chips[next].focus();
    chips[next].click();
  }

  function paintGroup(sel, box, descEl) {
    if (!sel || !box) return;
    var value = sel.value;
    Array.prototype.forEach.call(box.children, function (chip) {
      var on = chip.dataset.value === value;
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-checked", on ? "true" : "false");
      chip.tabIndex = on ? 0 : -1;
    });
    if (descEl) descEl.textContent = explain(selectedText(sel));
  }

  function paintPill() {
    var el = $("rc-pill-text");
    if (!el) return;
    var text = [short(selectedText($(MODE))), short(selectedText($(THINK)))]
      .filter(Boolean).join(" · ");
    if (text && el.textContent !== text) el.textContent = text;
  }

  function paint() {
    paintPill();
    paintGroup($(MODE), $("rc-chips-mode"), $("rc-mode-desc"));
    paintGroup($(THINK), $("rc-chips-thinking"), $("rc-thinking-desc"));
  }

  function rebuild() {
    buildChips($(MODE), $("rc-chips-mode"));
    buildChips($(THINK), $("rc-chips-thinking"));
    paint();
  }

  function isOpen() {
    var pop = $("rc-pop");
    return !!pop && !pop.classList.contains("hidden");
  }

  function setOpen(open) {
    var pop = $("rc-pop");
    var btn = $("rc-pill");
    if (!pop || !btn) return;
    if (open) rebuild();
    pop.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var on = pop.querySelector(".rc-chip.on");
      if (on) on.focus({ preventScroll: true });
    }
  }

  function mount() {
    var btn = $("rc-pill");
    var pop = $("rc-pop");
    if (!btn || !pop) return;

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!isOpen());
    });
    pop.addEventListener("click", function (e) { e.stopPropagation(); });

    document.addEventListener("click", function () { if (isOpen()) setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) {
        setOpen(false);
        btn.focus({ preventScroll: true });
      }
    });

    /* 值还可能在别处被程序改写而**不发 change**：模板回填（app.js:6452）、
       applyTaskPreferenceDefaults（app.js:766）都只写 value 再调 syncCmpSelFace。
       那条路径会重写 #f-mode-face / #f-thinking-face 的文字，所以盯住这两个节点——
       比轮询可靠，也不用改 app.js。 */
    ["f-mode-face", "f-thinking-face"].forEach(function (id) {
      var face = $(id);
      if (!face) return;
      new MutationObserver(paint).observe(face, { childList: true, subtree: true, characterData: true });
    });

    /* 交互前再对一次：只在文案真的不同时才写 DOM */
    document.addEventListener("focusin", paint, true);
    document.addEventListener("pointerdown", paint, true);

    [MODE, THINK].forEach(function (id) {
      var sel = $(id);
      if (!sel) return;
      sel.addEventListener("change", paint);
      /* 切语言时 applyI18n 会重译 option 文本 → 短名要跟着变，清掉缓存重建 */
      new MutationObserver(function () {
        clearTimeout(TIMER);
        TIMER = setTimeout(function () {
          var box = $(id === MODE ? "rc-chips-mode" : "rc-chips-thinking");
          if (box) delete box.dataset.key;
          rebuild();
        }, 60);
      }).observe(sel, { childList: true, subtree: true, characterData: true });
    });

    rebuild();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();

  window.rcRunSettings = { paint: paint, rebuild: rebuild, setOpen: setOpen };
})();
