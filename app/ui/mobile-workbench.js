/* Mobile presentation reuses the original controls and their event handlers. */
(() => {
  const media = window.matchMedia("(max-width: 900px)");
  const detail = document.getElementById("run-detail");
  const nav = document.getElementById("rd-chat-nav");
  const tools = document.querySelector(".rd-chat-tools");
  const input = document.getElementById("rd-chat-input");
  if (!detail || !nav || !tools || !input) return;
  const placements = [];
  const originalPlaceholder = input.getAttribute("data-i18n-ph");
  const label = (zh, en) => document.documentElement.dataset.lang === "en" ? en : zh;
  const actions = document.createElement("div");
  actions.className = "mobile-task-actions";
  const options = document.createElement("div");
  options.id = "mobile-chat-options";
  options.className = "mobile-chat-options hidden";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "ghost mobile-chat-options-toggle";
  toggle.id = "mobile-chat-options-toggle";
  toggle.setAttribute("aria-controls", options.id);
  toggle.setAttribute("aria-expanded", "false");
  const closeOptions = () => {
    if (!options.classList.contains("hidden")) options.classList.add("hidden");
    if (toggle.getAttribute("aria-expanded") !== "false") toggle.setAttribute("aria-expanded", "false");
  };
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    options.classList.toggle("hidden", !open);
    toggle.setAttribute("aria-expanded", String(open));
  });
  const move = (el, parent) => {
    const marker = document.createComment("mobile control placement");
    el.before(marker);
    placements.push({ el, marker });
    parent.append(el);
  };
  let navSignature = "";
  const syncNav = () => {
    if (!media.matches) return;
    const buttons = [...nav.querySelectorAll(".cn-pill")];
    const selected = buttons.find(b => b.classList.contains("on"));
    const signature = JSON.stringify([buttons.map(b => b.textContent), selected?.textContent,
      document.documentElement.dataset.lang]);
    if (signature === navSignature && nav.querySelector(".mobile-section-select")) return;
    navSignature = signature;
    nav.querySelector(".mobile-section-select")?.remove();
    const select = document.createElement("select");
    select.className = "mobile-section-select";
    select.setAttribute("aria-label", label("切换任务分区", "Task section"));
    select.add(new Option(label("对话", "Conversation"), "chat"));
    buttons.forEach((button, i) => select.add(new Option(button.textContent.trim(), String(i))));
    select.value = selected ? String(buttons.indexOf(selected)) : "chat";
    select.addEventListener("change", () => {
      closeOptions();
      if (select.value === "chat") window.rdChatNavBack();
      else buttons[Number(select.value)]?.click();
    });
    nav.append(select);
  };
  const syncViewport = () => {
    const viewport = window.visualViewport;
    if (media.matches && viewport && viewport.scale === 1) {
      document.documentElement.style.setProperty("--mobile-viewport-height", viewport.height + "px");
    } else document.documentElement.style.removeProperty("--mobile-viewport-height");
  };
  const apply = () => {
    window.mobileWorkbenchState = { matches: media.matches, placements: placements.length, width: window.innerWidth };
    if (media.matches && !placements.length) {
      document.getElementById("rd-meta-popover").append(actions);
      document.querySelectorAll(".rd-actions > button:not(#rd-more-toggle)").forEach(el => move(el, actions));
      tools.before(options);
      tools.querySelectorAll(":scope > .cmp-sel").forEach(el => move(el, options));
      document.getElementById("rd-chat-att-list").after(toggle);
      closeOptions();
    } else if (!media.matches && placements.length) {
      placements.splice(0).forEach(({ el, marker }) => { marker.replaceWith(el); });
      actions.remove(); options.remove(); toggle.remove();
      nav.querySelector(".mobile-section-select")?.remove();
      navSignature = "";
    }
    if (media.matches) {
      toggle.textContent = label("选项", "Options");
      input.removeAttribute("data-i18n-ph");
      input.placeholder = label("继续说…", "Message…");
      syncNav();
    } else {
      input.setAttribute("data-i18n-ph", originalPlaceholder);
      input.placeholder = typeof t === "function" ? t(originalPlaceholder) : originalPlaceholder;
    }
    syncViewport();
  };
  new window.MutationObserver(syncNav).observe(nav, { childList: true });
  new window.MutationObserver(apply).observe(document.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
  let target = "";
  const resetForTask = () => {
    const next = document.getElementById("rd-title").textContent;
    if (target !== next || detail.classList.contains("hidden")) { closeOptions(); target = next; }
  };
  new window.MutationObserver(resetForTask).observe(detail, { attributes: true, attributeFilter: ["class"] });
  new window.MutationObserver(resetForTask).observe(document.getElementById("rd-title"), { childList: true, characterData: true, subtree: true });
  media.addEventListener("change", apply);
  window.addEventListener("resize", syncViewport);
  window.visualViewport?.addEventListener("resize", syncViewport);
  input.addEventListener("focus", closeOptions);
  document.addEventListener("focusin", (event) => {
    if (event.target && event.target.id === "rd-chat-input") closeOptions();
  }, true);
  apply();
  // app.js finishes binding and painting detail controls asynchronously after
  // this script tag; retry once after the first frame so a cold mobile load
  // gets the compact presentation even when the detail starts hidden.
  window.setTimeout(apply, 0);
  window.setTimeout(apply, 300);
  window.mobileWorkbenchReady = true;
})();
