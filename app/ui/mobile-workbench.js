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

/* Phone control surface: expert launch and a compact activity count. */
(() => {
  const setup = () => {
    const head = document.querySelector(".mobile-workspaces-head");
    if (!head || typeof api !== "function" || head.querySelector(".mobile-expert-controls")) return;
    const box = document.createElement("div");
    box.className = "mobile-expert-controls";
    box.innerHTML = '<label><span>专家</span><select aria-label="专家"></select></label>' +
      '<button type="button" class="ghost">启动专家</button><small aria-live="polite"></small>';
    head.insertBefore(box, head.firstChild);
    const select = box.querySelector("select"), button = box.querySelector("button"), status = box.querySelector("small");
    const refresh = () => api("/api/mobile/status", { timeout: 8000, busy: false, operation: false }).then(data => {
      const runs = data.runs || [];
      const active = runs.filter(run => ["queued", "running"].includes(run.status));
      status.textContent = "活动运行：" + active.length;
      box.querySelectorAll(".mobile-run-decision").forEach(node => node.remove());
      runs.filter(run => run.status === "done" && run.task_id).slice(0, 3).forEach(run => {
        const row = document.createElement("span");
        row.className = "mobile-run-decision";
        row.textContent = (run.title || run.id) + "：";
        ["approve", "reject", "blocked"].forEach(action => {
          const actionButton = document.createElement("button");
          actionButton.type = "button"; actionButton.className = "ghost small"; actionButton.textContent = action;
          actionButton.addEventListener("click", async () => {
            actionButton.disabled = true;
            try { await api("/api/mobile/runs/" + encodeURIComponent(run.id) + "/decision", { method: "POST", body: { action, note: action === "reject" ? "手机端裁决" : "" } }); await refresh(); }
            catch (error) { status.textContent = error && error.message ? error.message : "裁决失败"; actionButton.disabled = false; }
          });
          row.append(actionButton);
        });
        box.append(row);
      });
    }).catch(() => {});
    api("/api/mobile/experts", { timeout: 8000, busy: false, operation: false }).then(data => {
      select.innerHTML = (data.experts || []).map(expert => '<option value="' + esc(expert.id) + '">' + esc(expert.name || expert.id) + '</option>').join("");
    }).catch(() => {});
    button.addEventListener("click", async () => {
      const goal = window.prompt("告诉专家要完成什么");
      if (!goal || !goal.trim() || !select.value) return;
      button.disabled = true;
      try {
        await api("/api/mobile/experts/" + encodeURIComponent(select.value) + "/launch", {
          method: "POST", body: { goal: goal.trim(), title: goal.trim().slice(0, 80) }, timeout: 15000
        });
        await refresh();
      } catch (error) { status.textContent = error && error.message ? error.message : "启动失败"; }
      finally { button.disabled = false; }
    });
    refresh();
  };
  window.setTimeout(setup, 500);
})();
