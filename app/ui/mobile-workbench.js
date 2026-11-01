/* Mobile presentation. All actions call the desktop implementation underneath:
   the real composer, the real detail panes and the real task APIs. */
(() => {
  const media = window.matchMedia("(max-width: 900px)");
  const detail = document.getElementById("run-detail");
  const tabs = document.getElementById("rd-tabs");
  const rail = document.getElementById("rd-rail");
  const input = document.getElementById("rd-chat-input");
  if (!detail || !tabs || !rail || !input) return;
  const label = (zh, en) => document.documentElement.dataset.lang === "en" ? en : zh;

  /* 详情只保留「进展 / 产出」两段；其余 8 个分区收进桌面端。 */
  const seg = document.createElement("div");
  seg.className = "mob-detail-seg";
  seg.setAttribute("role", "tablist");
  seg.setAttribute("aria-label", label("详情分区", "Detail sections"));
  seg.innerHTML = '<button type="button" role="tab" data-mob-pane="chat"></button>' +
    '<button type="button" role="tab" data-mob-pane="output"></button>';
  const context = document.getElementById("rd-context");
  const anchor = context || tabs;
  rail.insertBefore(seg, anchor);
  const actions = document.createElement("div");
  actions.className = "mob-detail-actions hidden";
  rail.insertBefore(actions, anchor);

  /* 「进展」的首要内容是这次跑到哪了：真实步骤时间线放在对话流上方，
     手机端不必为了看进度切到别的分区。 */
  const chatBox = document.getElementById("rd-chat");
  const progress = document.createElement("div");
  progress.className = "mob-run-progress";
  if (chatBox) chatBox.insertBefore(progress, chatBox.firstChild);

  const tabButton = (name) => document.querySelector('#rd-tabs .rd-tab[data-tab="' + name + '"]');
  const paneButton = (name) => seg.querySelector('[data-mob-pane="' + name + '"]');

  /* 手机端只有「进展 / 产出」两段：桌面自动落位到蜂巢/步骤/预览时改落对应段，
     网页预览在手机上会套一层整个应用，看不到报告和成品文件。 */
  const paintSeg = () => {
    const chat = tabButton("chat"), result = tabButton("result");
    const chatOk = !!chat && !chat.classList.contains("hidden");
    const resultOk = !!result && !result.classList.contains("hidden");
    const active = (typeof S !== "undefined" && S.rdTab) || "";
    if (media.matches && typeof S !== "undefined" && !S.rdTabPin &&
        !detail.classList.contains("hidden")) {
      const want = active === "preview" ? (resultOk ? "result" : null)
        : (active !== "chat" && active !== "result" ? (chatOk ? "chat" : (resultOk ? "result" : null)) : null);
      const target = want ? tabButton(want) : null;
      if (target && !target.classList.contains("hidden")) { target.click(); return; }
    }
    const chatBtn = paneButton("chat"), outBtn = paneButton("output");
    chatBtn.textContent = label("进展", "Progress");
    outBtn.textContent = label("产出", "Output");
    chatBtn.disabled = !chatOk;
    outBtn.disabled = !resultOk;
    chatBtn.classList.toggle("on", active === "chat");
    outBtn.classList.toggle("on", active === "result");
    chatBtn.setAttribute("aria-selected", active === "chat" ? "true" : "false");
    outBtn.setAttribute("aria-selected", active === "result" ? "true" : "false");
  };

  seg.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mob-pane]");
    if (!button || button.disabled) return;
    const target = button.dataset.mobPane === "chat" ? tabButton("chat") : tabButton("result");
    if (target) target.click();
    window.setTimeout(paintSeg, 0);
  });

  const currentTaskId = () => {
    if (typeof S === "undefined") return "";
    return S.detailTaskKey || (S.lastRun && S.lastRun.task_id) || "";
  };
  const currentTask = (id) => ((S.state && S.state.tasks) || []).find((task) => task.id === id) || {};
  const currentRun = (id) => {
    if (typeof S !== "undefined" && S.lastRun && (!S.detailTaskKey || S.lastRun.task_id === id)) return S.lastRun;
    return ((S.state && S.state.task_latest) || {})[id] || {};
  };
  const currentAttention = (id) => ((S.state && S.state.task_attention) || {})[id] || {};

  const stepState = (status) => {
    if (status === "done") return ["ok", "已完成", "Done"];
    if (status === "running") return ["run", "进行中", "Running"];
    if (["failed", "timeout", "cancelled"].includes(status)) return ["bad", "未完成", "Not finished"];
    return ["wait", "等待中", "Pending"];
  };

  const paintProgress = () => {
    if (!progress) return;
    const id = currentTaskId();
    const run = currentRun(id);
    if (!media.matches || !id || typeof S === "undefined" || !S.lastRun ||
        S.lastRun.id !== run.id) { progress.innerHTML = ""; return; }
    const steps = Array.isArray(run.steps) ? run.steps : [];
    if (!steps.length) { progress.innerHTML = ""; return; }
    const done = steps.filter((step) => step && step.status === "done").length;
    const pct = Math.max(0, Math.min(100, Math.round(100 * done / steps.length)));
    const status = run.status || currentTask(id).status || "";
    const badge = status === "done" ? ["ok", "已完成", "Done"]
      : status === "running" ? ["run", "运行中", "Running"]
        : status === "queued" ? ["wait", "排队中", "Queued"]
          : ["bad", "已结束", "Finished"];
    const rows = steps.slice(0, 8).map((step, index) => {
      const [kind, zh, en] = stepState(step && step.status);
      const note = String((step && step.summary) || "").replace(/\s+/g, " ").trim().slice(0, 160);
      const agent = (step && (step.agent_label || step.agent || step.role)) || "";
      const title = label("第 " + ((step && step.n) || index + 1) + " 步",
        "Step " + ((step && step.n) || index + 1)) + (agent ? " · " + agent : "");
      return '<li class="mob-step ' + kind + '"><span class="mob-node" aria-hidden="true"></span>' +
        '<span class="mob-step-copy"><b>' + esc(title) + '</b><small>' + esc(label(zh, en)) + '</small>' +
        (note ? '<p>' + esc(note) + '</p>' : '') + '</span></li>';
    });
    progress.innerHTML =
      '<div class="mob-rp-head"><b>' + pct + '%</b><span>' +
      esc(label("已完成 " + done + "/" + steps.length + " 步", done + "/" + steps.length + " steps")) +
      '</span><span class="mob-badge ' + badge[0] + '">' + esc(label(badge[1], badge[2])) + '</span></div>' +
      '<div class="mob-track" aria-hidden="true"><i style="width:' + pct + '%"></i></div>' +
      '<ol class="mob-timeline">' + rows.join("") + '</ol>' +
      (steps.length > 8
        ? '<p class="mob-rp-more">' + esc(label("另有 " + (steps.length - 8) + " 步，可在电脑端查看",
            (steps.length - 8) + " more steps on desktop")) + '</p>'
        : "");
  };

  const actionButton = (kind, text, cls) =>
    '<button type="button" class="mob-act ' + cls + '" data-mob-action="' + kind + '">' + esc(text) + '</button>';

  const refreshActions = () => {
    if (!media.matches) return;
    const id = currentTaskId();
    if (!id) { actions.classList.add("hidden"); actions.innerHTML = ""; return; }
    const task = currentTask(id);
    const run = currentRun(id);
    const attention = currentAttention(id);
    const status = run.status || task.status || "";
    const out = [];
    if (status === "running" || status === "queued") {
      const pause = document.getElementById("btn-pause");
      if (status === "running" && pause && !pause.classList.contains("hidden")) {
        out.push(actionButton("pause", label("暂停", "Pause"), "ghost"));
      }
      const cancel = document.getElementById("btn-cancel");
      if (cancel && !cancel.classList.contains("hidden")) {
        out.push(actionButton("cancel", label("取消", "Cancel"), "danger ghost"));
      }
    } else if (task.git_state === "isolated") {
      out.push(actionButton("merge", label("通过并合并", "Approve & merge"), "primary"));
      out.push(actionButton("discard", label("丢弃分支", "Discard branch"), "danger"));
    } else if (attention.approval_pending) {
      out.push(actionButton("approve", label("批准发布", "Approve release"), "primary"));
    } else if (["failed", "timeout", "cancelled"].includes(status)) {
      out.push(actionButton("retry", label("重试", "Retry"), "primary"));
    }
    if (!out.length) { actions.classList.add("hidden"); actions.innerHTML = ""; return; }
    actions.innerHTML = out.join("");
    actions.classList.remove("hidden");
  };

  actions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mob-action]");
    if (!button) return;
    const id = currentTaskId();
    if (!id) return;
    const kind = button.dataset.mobAction;
    if (kind === "pause") document.getElementById("btn-pause")?.click();
    else if (kind === "cancel") document.getElementById("btn-cancel")?.click();
    else if (kind === "merge") window.gitMerge(id);
    else if (kind === "discard") window.gitDiscard(id);
    else if (kind === "approve") window.contractAction(id, "approve");
    else if (kind === "retry") window.retryTask(id);
    window.setTimeout(refreshActions, 120);
  });

  const apply = () => {
    if (!media.matches) return;
    input.placeholder = label("补充说明或纠偏…", "Add context or correct the run…");
    paintSeg();
    paintProgress();
    refreshActions();
  };
  new MutationObserver(apply).observe(tabs, { attributes: true, subtree: true, attributeFilter: ["class", "aria-selected"] });
  for (const id of ["rd-title", "rd-status"]) {
    const node = document.getElementById(id);
    if (node) new MutationObserver(apply).observe(node, { childList: true, characterData: true, subtree: true });
  }
  new MutationObserver(apply).observe(document.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
  window.addEventListener("orch:state", apply);
  media.addEventListener("change", () => {
    if (media.matches) apply();
    else { actions.classList.add("hidden"); actions.innerHTML = ""; }
  });
  apply();
  // app.js finishes painting detail controls asynchronously; retry once after
  // the first frames so a cold phone load still gets the compact detail.
  window.setTimeout(apply, 0);
  window.setTimeout(apply, 300);
  window.mobileWorkbenchReady = true;
})();

/* 手机壳：任务 / 定时任务 / 发现 / 我的（WorkBuddy 视觉，2026-11-01）。
   数据全部来自 /api/state 与 /api/automation，动作全部走既有实现。 */
(() => {
  const media = window.matchMedia("(max-width: 900px)");
  const label = (zh, en) => document.documentElement.dataset.lang === "en" ? en : zh;
  const $ = (id) => document.getElementById(id);
  const shell = $("mobile-shell");
  const tabbar = $("mobile-tabbar");
  const detail = $("run-detail");
  if (!shell || !tabbar) return;
  const views = {};
  shell.querySelectorAll("[data-mob-view]").forEach((node) => { views[node.dataset.mobView] = node; });
  let view = "tasks";
  let filter = "all";
  let dirFilter = "";
  let detailWasOpen = false;
  let suppressReturn = false;

  const state = () => (typeof S !== "undefined" && S.state) || {};
  const latestOf = (id) => (state().task_latest || {})[id] || {};
  const attentionOf = (id) => (state().task_attention || {})[id] || {};
  const hapticOn = () => { try { return localStorage.getItem("orch.haptic") === "1"; } catch (e) { return false; } };
  const haptic = () => {
    if (!hapticOn() || !navigator.vibrate) return;
    try { navigator.vibrate(10); } catch (e) { /* 忽略：部分浏览器禁用 */ }
  };
  const categoryOf = (task, run, attention) => {
    const status = run.status || task.status || "";
    if (task.git_state === "isolated" || attention.approval_pending ||
        (attention.blocker && attention.blocker.count) || ["failed", "timeout"].includes(status)) return "pending";
    if (status === "running" || status === "queued") return "running";
    if (status === "done") return "done";
    return "other";
  };
  const statusOf = (task, run, attention) => {
    if (task.git_state === "isolated") return { text: label("待裁决", "Review"), cls: "bad" };
    if (attention.approval_pending) return { text: label("待审批", "Approval"), cls: "bad" };
    if (attention.blocker && attention.blocker.count) return { text: label("有阻塞", "Blocked"), cls: "bad" };
    const status = run.status || task.status || "";
    if (status === "running") return { text: label("进行中", "Running"), cls: "run" };
    if (status === "queued") return { text: label("排队中", "Queued"), cls: "wait" };
    if (status === "done") return { text: label("已完成", "Done"), cls: "ok" };
    if (status === "failed" || status === "timeout") return { text: label("失败", "Failed"), cls: "bad" };
    if (status === "cancelled") return { text: label("已取消", "Cancelled"), cls: "plain" };
    return { text: label("未开始", "Not started"), cls: "plain" };
  };
  const timeOf = (run, task) =>
    run.ended_at || run.started_at || run.created_at || task.created_at || "";

  const taskItems = () => {
    const items = ((state().tasks) || []).map((task) => {
      const run = latestOf(task.id);
      const attention = attentionOf(task.id);
      return { task, run, attention, cat: categoryOf(task, run, attention), time: timeOf(run, task) };
    });
    return items.sort((a, b) => String(b.time).localeCompare(String(a.time)));
  };

  const paintDate = () => {
    const now = new Date();
    const el = $("mob-date");
    if (!el) return;
    if (document.documentElement.dataset.lang === "en") {
      el.textContent = now.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
        " · " + now.toLocaleDateString("en-US", { weekday: "short" });
    } else {
      const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
      el.textContent = (now.getMonth() + 1) + " 月 " + now.getDate() + " 日 · 星期" + weekdays[now.getDay()];
    }
  };

  const paintText = () => {
    $("mob-greeting").textContent = label("把要做的事，交给 CodeBee。", "Hand it to CodeBee.");
    $("mob-sec-pending-title").textContent = label("等你处理", "Needs you");
    $("mob-sec-running-title").textContent = label("进行中", "Running");
    $("mob-tasks-title").textContent = label("全部任务", "All tasks");
    $("mob-auto-title").textContent = label("定时任务", "Scheduled");
    $("mob-find-title").textContent = label("发现", "Discover");
    $("mob-me-title").textContent = label("我的", "Me");
    $("mob-composer-ph").textContent = label("描述目标，交给 CodeBee…", "Describe the goal…");
    $("mob-drawer-new-label").textContent = label("新建任务", "New task");
    $("mob-auto-note").textContent = label(
      "到点自动运行；新建、编辑和模板在电脑端「自动化」页。",
      "Runs on schedule. Create, edit and templates live on the desktop Automation page.");
    $("mob-auto-manage").textContent = label("去电脑端管理", "Manage on desktop");
    $("mob-me-host").textContent = label("连接电脑端后自动同步", "Syncs with your desktop");
    $("mob-me-conn-label").textContent = label("连接状态", "Connection");
    $("mob-me-theme-label").textContent = label("主题", "Theme");
    $("mob-me-lang-label").textContent = label("语言", "Language");
    $("mob-me-font-label").textContent = label("字体大小", "Font size");
    $("mob-me-haptic-label").textContent = label("触感反馈", "Haptics");
    $("mob-me-sound-label").textContent = label("提示音", "Sound");
    $("mob-me-storage-label").textContent = label("存储空间", "Storage");
    $("mob-me-desktop-label").textContent = label("更多设置（电脑端）", "More settings (desktop)");
    $("mob-me-refresh").textContent = label("重新连接", "Reconnect");
    $("mob-me-note").textContent = label(
      "任务与产物保存在电脑端；手机只保留上面这些常用设置。",
      "Tasks and outputs live on the desktop; the phone keeps only these quick settings.");
    $("mob-font-title").textContent = label("字体大小", "Font size");
    $("mob-font-ok").textContent = label("确认", "Done");
    $("mob-fp-user").textContent = label("帮我预览一下字号大小", "Preview the font size");
    $("mob-fp-bot").textContent = label(
      "拖动下面的滑块设置字号。设置后会改变手机版全局的文字大小。",
      "Drag the slider to change the font size across the mobile app.");
    $("mob-slider-mid").textContent = label("标准", "Default");
    $("mob-font-note").textContent = label(
      "跟随系统的字号暂不支持，手动选择会一直生效。",
      "System font scale is not followed; your pick stays until changed.");
    $("mob-st-title").textContent = label("存储空间", "Storage");
    $("mob-st-clearall").textContent = label("全部清除", "Clear all");
    $("mob-st-hero-label").textContent = label("CodeBee 占用空间", "CodeBee storage used");
    $("mob-st-cache-title").textContent = label("界面缓存", "Interface cache");
    $("mob-st-cache-clear").textContent = label("清除", "Clear");
    $("mob-st-cache-desc").textContent = label(
      "本机保存的界面状态与草稿（页签、折叠、筛选等）。清理后不影响任务数据。",
      "Local UI state and drafts (tabs, folds, filters). Task data is not affected.");
    $("mob-st-cloud-title").textContent = label("任务与产物数据", "Tasks and outputs");
    $("mob-st-cloud-desc").textContent = label(
      "保存在电脑端，不占用本机存储；打开会话时从电脑端读取。",
      "Stored on the desktop and read from there; they do not use phone storage.");
    const titles = { tasks: label("任务", "Tasks"), auto: label("定时任务", "Scheduled"),
      find: label("发现", "Discover"), me: label("我的", "Me") };
    tabbar.querySelectorAll("[data-mob-tab-label]").forEach((span) => {
      const key = span.dataset.mobTabLabel;
      if (titles[key]) span.textContent = titles[key];
    });
    shell.setAttribute("aria-label", label("手机工作台", "Mobile workbench"));
  };

  const pendingActions = (task, run, attention) => {
    const id = esc(task.id);
    if (task.git_state === "isolated") {
      return '<button type="button" class="primary" data-decide="merge" data-task="' + id + '">' + esc(label("通过", "Approve")) + '</button>' +
        '<button type="button" class="danger ghost" data-decide="discard" data-task="' + id + '">' + esc(label("打回", "Reject")) + '</button>';
    }
    if (attention.approval_pending) {
      return '<button type="button" class="primary" data-decide="approve" data-task="' + id + '">' + esc(label("批准发布", "Approve release")) + '</button>';
    }
    if (attention.blocker && attention.blocker.count) {
      return '<button type="button" class="ghost" data-task="' + id + '">' + esc(label("查看阻塞", "View blocker")) + '</button>';
    }
    if (["failed", "timeout", "cancelled"].includes(run.status)) {
      return '<button type="button" class="primary" data-decide="retry" data-task="' + id + '">' + esc(label("重试", "Retry")) + '</button>';
    }
    return "";
  };

  const pendingCard = ({ task, run, attention }) => {
    const status = statusOf(task, run, attention);
    const reason = (attention.blocker && attention.blocker.text) ||
      (task.git_state === "isolated"
        ? label("任务分支已就绪，确认后合并或丢弃。", "The task branch is ready — merge or discard it.")
        : attention.approval_pending
          ? label("运行已完成，等待你批准发布。", "The run finished; approve the release when ready.")
          : label("上次运行没有完成，可以重试。", "The last run did not finish. You can retry."));
    const decisions = pendingActions(task, run, attention);
    return '<article class="mob-card-item pending">' +
      '<div class="mob-card-top"><span class="mob-badge ' + status.cls + '">' + esc(status.text) +
      '</span><span class="mob-time">' + esc(relTime(timeOf(run, task))) + '</span></div>' +
      '<button type="button" class="mob-card-main" data-task="' + esc(task.id) + '">' +
      '<b>' + esc(task.title || task.id) + '</b><p>' + esc(reason) + '</p></button>' +
      (decisions ? '<div class="mob-card-actions">' + decisions + '</div>' : "") +
      '</article>';
  };

  const runningCard = ({ task, run }) => {
    const status = statusOf(task, run, {});
    const total = Number(run.step_count) || 0;
    const step = (run.current_step && run.current_step.n) || 0;
    const pct = Math.max(0, Math.min(100, Math.round(Number(run.progress) || 0)));
    const meta = [dirName(task.workdir),
      total ? label("第 " + step + "/" + total + " 步", "Step " + step + "/" + total) : label("等待开始", "Starting")]
      .filter(Boolean).join(" · ");
    return '<button type="button" class="mob-task-card" data-task="' + esc(task.id) + '">' +
      '<span class="mob-card-top"><b>' + esc(task.title || task.id) + '</b>' +
      '<span class="mob-badge ' + status.cls + '">' + esc(status.text) + '</span></span>' +
      '<span class="mob-task-meta">' + esc(meta) + '</span>' +
      '<span class="mob-track"><i style="width:' + pct + '%"></i></span></button>';
  };

  const taskRow = ({ task, run, attention }) => {
    const status = statusOf(task, run, attention);
    const pct = Math.max(0, Math.min(100, Math.round(Number(run.progress) || 0)));
    const meta = [dirName(task.workdir), relTime(timeOf(run, task)),
      (run.status === "running" || run.status === "queued") && pct ? pct + "%" : ""].filter(Boolean).join(" · ");
    return '<button type="button" class="mob-task-row" data-task="' + esc(task.id) + '">' +
      '<i class="mob-dot ' + status.cls + '" aria-hidden="true"></i>' +
      '<span class="mob-task-copy"><b>' + esc(task.title || task.id) + '</b><small>' + esc(meta) + '</small></span>' +
      '<span class="mob-badge ' + status.cls + '">' + esc(status.text) + '</span></button>';
  };

  const paintFeed = () => {
    paintDate();
    const items = taskItems();
    const running = items.filter((item) => item.cat === "running");
    const pending = items.filter((item) => item.cat === "pending");
    const summary = running.length || pending.length
      ? label(running.length + " 个任务在跑 · " + pending.length + " 个等你处理",
              running.length + " running · " + pending.length + " awaiting you")
      : (items.length ? label("现在没有在跑的任务", "Nothing is running right now") : label("还没有任务，点下面说第一句。", "No tasks yet — say your first goal below."));
    $("mob-summary").textContent = summary;
    $("mob-home-pending").innerHTML = pending.map(pendingCard).join("");
    $("mob-home-running").innerHTML = running.map(runningCard).join("");
    $("mob-pending-count").textContent = pending.length ? String(pending.length) : "";
    $("mob-running-count").textContent = running.length ? String(running.length) : "";
    $("mob-sec-pending").classList.toggle("hidden", !pending.length);
    $("mob-sec-running").classList.toggle("hidden", !running.length);
  };

  const paintTasks = () => {
    const items = taskItems();
    const counts = { all: items.length, running: 0, pending: 0, done: 0 };
    items.forEach((item) => { if (counts[item.cat] != null) counts[item.cat] += 1; });
    const labels = { all: label("全部", "All"), running: label("进行中", "Running"),
      pending: label("待处理", "Needs you"), done: label("已完成", "Done") };
    let chips = Object.keys(labels).map((key) =>
      '<button type="button" class="mob-chip' + (filter === key && !dirFilter ? " on" : "") + '" data-filter="' + key + '">' +
      esc(labels[key]) + '<b>' + counts[key] + '</b></button>').join("");
    if (dirFilter) {
      chips += '<button type="button" class="mob-chip on" data-filter-dir="' + esc(dirFilter) + '">📂 ' +
        esc(dirFilter) + ' ✕</button>';
    }
    $("mob-task-filters").innerHTML = chips;
    $("mob-all-count").textContent = items.length ? String(items.length) : "";
    let shown = filter === "all" ? items : items.filter((item) => item.cat === filter);
    if (dirFilter) shown = shown.filter((item) => dirName(item.task.workdir) === dirFilter);
    $("mob-task-list").innerHTML = shown.length ? shown.map(taskRow).join("") :
      '<p class="mob-empty">' + esc(filter === "all" && !dirFilter
        ? label("还没有任务", "No tasks yet")
        : label("这个筛选下没有任务", "No tasks in this filter")) + '</p>';
  };

  /* ---------- 定时任务页：真源 /api/automation（与桌面同源，只读清单） ---------- */
  const paintAuto = () => {
    const list = $("mob-auto-list");
    const tasks = (typeof S !== "undefined" && Array.isArray(S.autoTasks)) ? S.autoTasks : null;
    if (!tasks) {
      list.innerHTML = '<p class="mob-empty">' + esc(label("还没连上，稍后再试。", "Not connected yet.")) + '</p>';
      return;
    }
    if (!tasks.length) {
      list.innerHTML = '<p class="mob-empty">' + esc(label(
        "还没有定时任务——去电脑端「自动化」页新建，或从模板一键创建。",
        "No scheduled tasks yet — create one on the desktop Automation page.")) + '</p>';
      return;
    }
    list.innerHTML = tasks.map((tsk) => {
      const kind = (typeof window.autoKindText === "function") ? window.autoKindText(tsk) : (tsk.time || "");
      return '<div class="mob-row mob-auto-row">' +
        '<span class="mob-auto-copy"><b>' + esc(tsk.name || tsk.id) + '</b>' +
        '<small>' + esc(kind || "") + '</small></span>' +
        '<span class="mob-state-pill ' + (tsk.enabled ? "on" : "off") + '">' +
        esc(tsk.enabled ? label("启用中", "On") : label("已停用", "Off")) + '</span></div>';
    }).join("");
  };
  const refreshAuto = () => {
    if (typeof window.loadAutomation !== "function") { paintAuto(); return; }
    Promise.resolve(window.loadAutomation()).then(paintAuto, paintAuto);
  };

  /* ---------- 发现页：入口 + 专家 ---------- */
  const FIND_ENTRIES = [
    { key: "market", icon: "i-folder", tab: "market" },
    { key: "skills", icon: "i-tasks", tab: "skills" },
    { key: "knowledge", icon: "i-folder", tab: "knowledge" },
    { key: "memory", icon: "i-chat", tab: "memory" }
  ];
  const paintFind = () => {
    const entries = $("mob-find-entries");
    const names = { market: label("插件市场", "Market"), skills: label("经验库", "Skills"),
      knowledge: label("知识库", "Knowledge"), memory: label("记忆中心", "Memory") };
    entries.innerHTML = '<div class="mob-find-grid">' + FIND_ENTRIES.map((item) =>
      '<button type="button" class="mob-find-item" data-find-tab="' + item.tab + '">' +
      '<span class="mob-find-ico"><svg class="ico" aria-hidden="true"><use href="#' + item.icon + '"></use></svg></span>' +
      esc(names[item.key]) + '</button>').join("") + '</div>';
    const flows = (typeof S !== "undefined" && Array.isArray(S.flows)) ? S.flows.filter((f) => f && f.id && f.agent) : [];
    const box = $("mob-find-experts");
    if (!flows.length) {
      box.innerHTML = "";
      return;
    }
    const nameOf = (f) => (typeof window.expertName === "function") ? window.expertName(f) : (f.agent.name || f.id);
    const descOf = (f) => {
      if (typeof window.expertFocus === "function" && window.expertFocus(f)) return window.expertFocus(f);
      return (typeof window.flowDesc === "function") ? window.flowDesc(f) : (f.name || "");
    };
    const card = (f) => {
      const nm = nameOf(f);
      const av = String(nm).slice(0, 1).toUpperCase();
      return '<button type="button" class="mob-exp-card" data-find-expert="' + esc(f.id) + '">' +
        '<span class="mob-exp-top"><span class="mob-exp-av" aria-hidden="true">' + esc(av) + '</span>' +
        '<span class="mob-exp-name">' + esc(nm) + '</span></span>' +
        '<span class="mob-exp-desc">' + esc(descOf(f) || "") + '</span></button>';
    };
    box.innerHTML = '<div class="mob-find-head">' + esc(label("选位专家开始", "Pick an expert")) + '</div>' +
      '<div class="mob-exp-grid">' + flows.slice(0, 12).map(card).join("") + '</div>';
  };

  /* ---------- 我的：连接 / 主题 / 语言 / 字号 / 触感 / 提示音 / 存储 ---------- */
  const fmtBytes = (n) => {
    if (!Number.isFinite(n)) return "—";
    if (n < 1024) return Math.round(n) + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10 * 1024 ? 1 : 0) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  };
  const localBytes = () => {
    let total = 0;
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        total += (key.length + String(localStorage.getItem(key) || "").length) * 2;
      }
    } catch (e) { return NaN; }
    return total;
  };
  const paintStorageRows = () => {
    const row = $("mob-st-row-value");
    if (row) row.textContent = fmtBytes(localBytes());
  };
  const FONT_LEVELS = [
    { z: 0.92 }, { z: 1 }, { z: 1.08 }, { z: 1.16 }, { z: 1.25 }
  ];
  const FONT_LABELS = () => [label("小", "S"), label("标准", "Default"), label("大", "L"), label("更大", "XL"), label("超大", "XXL")];
  let fontTemp = 1;
  const applyFontLevel = (idx, temp) => {
    const level = FONT_LEVELS[Math.max(0, Math.min(FONT_LEVELS.length - 1, idx))];
    document.documentElement.style.setProperty("--mob-font-scale", String(level.z));
    const range = $("mob-font-range");
    if (range) {
      range.value = String(idx);
      const pct = Math.round(100 * idx / (FONT_LEVELS.length - 1));
      range.style.setProperty("--mob-font-fill", pct + "%");
    }
    const val = $("mob-font-value");
    if (val) val.textContent = FONT_LABELS()[Math.max(0, Math.min(FONT_LABELS().length - 1, idx))];
    if (!temp) fontTemp = idx;
  };
  const savedFontLevel = () => {
    const idx = parseInt(localStorage.getItem("orch.mobFontScale") || "1", 10);
    return Number.isFinite(idx) ? Math.max(0, Math.min(FONT_LEVELS.length - 1, idx)) : 1;
  };
  const paintMe = () => {
    const conn = document.getElementById("conn");
    const ok = !!conn && conn.classList.contains("ok");
    const value = $("mob-me-conn");
    value.textContent = ok ? label("已连接", "Connected")
      : (state().v ? label("连接异常", "Offline") : label("连接中…", "Connecting…"));
    value.className = "mob-conn-value " + (ok ? "ok" : "bad");
    const dot = $("mob-conn");
    dot.className = "mob-conn " + (ok ? "ok" : "bad");
    dot.lastElementChild.textContent = ok ? label("已连接", "Connected")
      : (state().v ? label("连接异常", "Offline") : label("连接中…", "Connecting…"));
    const mode = localStorage.getItem("orch.theme") === "dark" ? "dark" : "light";
    $("mob-theme-seg").innerHTML = [["light", label("浅色", "Light")], ["dark", label("深色", "Dark")]]
      .map(([key, text]) => '<button type="button" class="' + (mode === key ? "on" : "") +
        '" data-mode="' + key + '">' + esc(text) + '</button>').join("");
    const lang = (typeof window.getLang === "function" ? window.getLang() : "zh");
    $("mob-lang-seg").innerHTML = [["zh", "中文"], ["en", "English"]]
      .map(([key, text]) => '<button type="button" class="' + (lang === key ? "on" : "") +
        '" data-lang="' + key + '">' + text + '</button>').join("");
    $("mob-haptic").checked = hapticOn();
    $("mob-sound").checked = localStorage.getItem("orch.sound") !== "0";
    // 字号子页打开时用户可能正在拖滑杆——状态帧不许把临时档位拍回已保存档
    if (shell.dataset.sub !== "font") applyFontLevel(savedFontLevel());
    paintStorageRows();
  };

  /* ---------- 存储子页 ---------- */
  const paintStoragePage = () => {
    paintStorageRows();
    const num = $("mob-st-num");
    const estimate = navigator.storage && typeof navigator.storage.estimate === "function"
      ? navigator.storage.estimate() : null;
    if (!estimate || !num) return;
    Promise.resolve(estimate).then((info) => {
      num.textContent = fmtBytes(Number(info && info.usage));
    }, () => { num.textContent = "—"; });
  };
  /* 只清界面状态与草稿；连接、主题、语言、声音、触感、字号、常用专家一律保留 */
  const UI_CACHE_KEYS = [
    "orch.availAuto", "orch.hiveView.v3", "orch.inspector", "orch.lastTask", "orch.setTab",
    "orch.ui-level", "orch.welcomed", "orch.voice_terms", "orch.browser-notify",
    "orch.workspace-mode.v2", "orch.side-fold", "orch.side.adv", "orch.home.adv",
    "orch.ws-entered.v1", "su.myver", "su.seen", "tutti.hiddenDirs"
  ];
  const clearUiCache = () => {
    let n = 0;
    UI_CACHE_KEYS.forEach((key) => {
      try { if (localStorage.getItem(key) != null) { localStorage.removeItem(key); n += 1; } } catch (e) { /* 忽略 */ }
    });
    paintStorageRows();
    return n;
  };

  /* ---------- 抽屉 ---------- */
  const drawer = $("mob-drawer");
  const drawerMask = $("mob-drawer-mask");
  const paintDrawer = () => {
    const items = taskItems();
    const tasksHead = $("mob-drawer-tasks-head");
    tasksHead.textContent = label("任务", "Tasks") + " (" + items.length + ")";
    const dirsHead = $("mob-drawer-dirs-head");
    const dirCounts = new Map();
    items.forEach((item) => {
      const d = dirName(item.task.workdir) || label("未归类", "Other");
      dirCounts.set(d, (dirCounts.get(d) || 0) + 1);
    });
    dirsHead.textContent = label("空间", "Spaces") + " (" + dirCounts.size + ")";
    $("mob-drawer-tasks").innerHTML = items.slice(0, 12).map((item) => {
      const st = statusOf(item.task, item.run, item.attention);
      return '<button type="button" class="mob-drawer-item" data-task="' + esc(item.task.id) + '">' +
        '<i class="' + st.cls + '" aria-hidden="true"></i><span>' + esc(item.task.title || item.task.id) + '</span></button>';
    }).join("") || '<p class="mob-drawer-empty">' + esc(label("还没有任务", "No tasks yet")) + '</p>';
    $("mob-drawer-dirs").innerHTML = Array.from(dirCounts.entries()).map(([d, n]) =>
      '<button type="button" class="mob-drawer-item" data-drawer-dir="' + esc(d) + '">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>' +
      '<span>' + esc(d) + '</span><small style="color:var(--mob-dim)">' + n + '</small></button>').join("");
  };
  const openDrawer = () => {
    paintDrawer();
    drawer.classList.remove("hidden");
    drawerMask.classList.remove("hidden");
    haptic();
  };
  const closeDrawer = () => {
    drawer.classList.add("hidden");
    drawerMask.classList.add("hidden");
  };

  /* ---------- 子页 ---------- */
  const openSub = (name) => {
    shell.dataset.sub = name;
    const el = $("mob-sub-" + name);
    if (el) {
      el.classList.remove("hidden");
      if (name === "storage") paintStoragePage();
    }
    haptic();
  };
  const closeSubs = () => {
    shell.removeAttribute("data-sub");
    ["font", "storage"].forEach((name) => {
      const el = $("mob-sub-" + name);
      if (el) el.classList.add("hidden");
    });
  };

  const paintBadge = () => {
    const pending = taskItems().filter((item) => item.cat === "pending").length;
    const badge = $("mob-tab-badge");
    if (!badge) return;
    badge.textContent = pending > 9 ? "9+" : String(pending);
    badge.classList.toggle("hidden", !pending);
  };

  const paintAll = () => {
    if (!media.matches) return;
    paintText();
    paintFeed();
    paintTasks();
    paintAuto();
    paintFind();
    paintMe();
    paintBadge();
    if (drawer && !drawer.classList.contains("hidden")) paintDrawer();
  };

  const switchView = (name) => {
    if (!views[name]) return;
    view = name;
    closeSubs();
    Object.keys(views).forEach((key) => views[key].classList.toggle("hidden", key !== name));
    tabbar.querySelectorAll("[data-mob-tab]").forEach((button) =>
      button.classList.toggle("active", button.dataset.mobTab === name));
    shell.dataset.view = name;
    if (name === "auto") refreshAuto();
  };

  const showShell = (name) => {
    if (!media.matches) return;
    document.body.classList.add("mob-shell-on");
    document.body.classList.remove("mob-composing-active");
    closeDrawer();
    switchView(name || view);
    paintAll();
    const scroller = $("mob-views");
    if (scroller) scroller.scrollTop = 0;
  };

  const openTask = (id) => {
    if (!id) return;
    const known = ((state().tasks) || []).some((task) => task.id === id);
    if (!known) {
      if (typeof window.toast === "function") window.toast(label("任务不存在或已删除", "Task not found"), true);
      return;
    }
    closeDrawer();
    haptic();
    document.body.classList.remove("mob-shell-on", "mob-composing-active");
    if (typeof window.sideOpenTask === "function") window.sideOpenTask(id);
  };

  const openComposer = () => {
    closeDrawer();
    haptic();
    document.body.classList.add("mob-composing-active");
    document.body.classList.remove("mob-shell-on");
    suppressReturn = true;
    if (typeof window.exitSettings === "function") window.exitSettings(true);
    else if (typeof window.switchTab === "function") window.switchTab("tasks");
    const sub = $("sub-tasks");
    if (sub) sub.classList.add("mobile-composing");
    const main = document.querySelector("main");
    if (main) main.scrollTop = 0;
    window.setTimeout(() => {
      suppressReturn = false;
      const goal = $("f-goal");
      if (goal) goal.focus({ preventScroll: true });
    }, 80);
  };

  /* 从手机壳跳到电脑端页面：壳收起，右上留一枚「手机版」回程胶囊 */
  let returnChip = null;
  const ensureReturnChip = () => {
    if (returnChip) return returnChip;
    returnChip = document.createElement("button");
    returnChip.type = "button";
    returnChip.id = "mob-return";
    returnChip.textContent = label("手机版", "Mobile");
    returnChip.addEventListener("click", () => showShell(view));
    document.body.appendChild(returnChip);
    return returnChip;
  };
  const goDesktop = (tab, sub) => {
    closeDrawer();
    document.body.classList.remove("mob-shell-on");
    if (typeof window.switchTab === "function") window.switchTab(tab);
    if (sub && typeof window.mkSetTab === "function") window.mkSetTab(sub);
    ensureReturnChip();
  };
  const syncReturnChip = () => {
    if (!returnChip) return;
    const show = media.matches &&
      !document.body.classList.contains("mob-shell-on") &&
      !document.body.classList.contains("mob-detail-active") &&
      !document.body.classList.contains("mob-composing-active") &&
      (!detail || detail.classList.contains("hidden"));
    returnChip.classList.toggle("hidden", !show);
  };
  new MutationObserver(syncReturnChip).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  if (detail) new MutationObserver(syncReturnChip).observe(detail, { attributes: true, attributeFilter: ["class"] });

  const onClick = (event) => {
    const decision = event.target.closest("[data-decide]");
    if (decision) {
      event.preventDefault();
      event.stopPropagation();
      haptic();
      const id = decision.dataset.task;
      const kind = decision.dataset.decide;
      if (kind === "merge") window.gitMerge(id);
      else if (kind === "discard") window.gitDiscard(id);
      else if (kind === "approve") window.contractAction(id, "approve");
      else if (kind === "retry") window.retryTask(id);
      return;
    }
    const dirChip = event.target.closest("[data-filter-dir]");
    if (dirChip) { dirFilter = ""; paintTasks(); return; }
    const drawerDir = event.target.closest("[data-drawer-dir]");
    if (drawerDir) {
      dirFilter = drawerDir.dataset.drawerDir;
      closeDrawer();
      switchView("tasks");
      paintTasks();
      return;
    }
    const findTab = event.target.closest("[data-find-tab]");
    if (findTab) { goDesktop(findTab.dataset.findTab); return; }
    const findExpert = event.target.closest("[data-find-expert]");
    if (findExpert) {
      haptic();
      if (typeof window.pickExpert === "function") window.pickExpert(findExpert.dataset.findExpert);
      openComposer();
      return;
    }
    const task = event.target.closest("[data-task]");
    if (task) { openTask(task.dataset.task); return; }
  };

  $("mob-home-pending").addEventListener("click", onClick);
  $("mob-home-running").addEventListener("click", onClick);
  $("mob-task-list").addEventListener("click", onClick);
  $("mob-task-filters").addEventListener("click", (event) => {
    const dirChip = event.target.closest("[data-filter-dir]");
    if (dirChip) { dirFilter = ""; paintTasks(); return; }
    const chip = event.target.closest("[data-filter]");
    if (!chip) return;
    filter = chip.dataset.filter;
    dirFilter = "";
    haptic();
    paintTasks();
  });
  $("mob-drawer-tasks").addEventListener("click", onClick);
  $("mob-drawer-dirs").addEventListener("click", onClick);
  $("mob-find-entries").addEventListener("click", onClick);
  $("mob-find-experts").addEventListener("click", onClick);
  $("mob-composer").addEventListener("click", openComposer);
  $("mob-menu").addEventListener("click", openDrawer);
  drawerMask.addEventListener("click", closeDrawer);
  $("mob-drawer-new").addEventListener("click", openComposer);
  $("mob-refresh").addEventListener("click", () => {
    if (typeof window.poll === "function") window.poll();
    paintAll();
  });
  $("mob-me-refresh").addEventListener("click", () => {
    if (typeof window.poll === "function") window.poll();
    paintMe();
  });
  $("mob-me-desktop").addEventListener("click", () => {
    closeDrawer();
    document.body.classList.remove("mob-shell-on");
    if (typeof window.enterSettings === "function") window.enterSettings();
    ensureReturnChip();
  });
  $("mob-me-font").addEventListener("click", () => openSub("font"));
  $("mob-me-storage").addEventListener("click", () => openSub("storage"));
  shell.querySelectorAll("[data-mob-back]").forEach((button) =>
    button.addEventListener("click", () => {
      if (shell.dataset.sub === "font") applyFontLevel(savedFontLevel());   // 不确认即回退
      closeSubs();
    }));
  $("mob-font-range").addEventListener("input", (event) => {
    fontTemp = parseInt(event.target.value, 10) || 0;
    applyFontLevel(fontTemp, true);
  });
  $("mob-font-ok").addEventListener("click", () => {
    try { localStorage.setItem("orch.mobFontScale", String(fontTemp)); } catch (e) { /* 忽略 */ }
    applyFontLevel(fontTemp);
    closeSubs();
    if (typeof window.toast === "function") window.toast(label("字号已保存", "Font size saved"));
  });
  $("mob-st-cache-clear").addEventListener("click", async () => {
    if (typeof window.uiConfirm === "function" && !await window.uiConfirm(
      label("清除界面缓存？页签、折叠、筛选等本机状态会被重置。", "Clear interface cache? Local UI state resets."), 
      { ok: label("清除", "Clear") })) return;
    const n = clearUiCache();
    if (typeof window.toast === "function") window.toast(label("已清除 " + n + " 项", n + " items cleared"));
    paintStoragePage();
  });
  $("mob-st-clearall").addEventListener("click", async () => {
    if (typeof window.uiConfirm === "function" && !await window.uiConfirm(
      label("清除全部本机界面缓存？任务数据保存在电脑端，不受影响。", "Clear all local interface cache? Desktop task data is not affected."),
      { ok: label("全部清除", "Clear all") })) return;
    const n = clearUiCache();
    if (typeof window.toast === "function") window.toast(label("已清除 " + n + " 项", n + " items cleared"));
    paintStoragePage();
  });
  $("mob-theme-seg").addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    if (typeof window.setThemeMode === "function") window.setThemeMode(button.dataset.mode);
    paintMe();
  });
  $("mob-lang-seg").addEventListener("click", (event) => {
    const button = event.target.closest("[data-lang]");
    if (!button) return;
    if (typeof window.setLangBtn === "function") window.setLangBtn(button.dataset.lang);
    paintAll();
  });
  $("mob-haptic").addEventListener("change", (event) => {
    try { localStorage.setItem("orch.haptic", event.target.checked ? "1" : "0"); } catch (e) { /* 忽略 */ }
    haptic();
  });
  $("mob-sound").addEventListener("change", (event) => {
    localStorage.setItem("orch.sound", event.target.checked ? "1" : "0");
  });
  tabbar.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mob-tab]");
    if (!button) return;
    const name = button.dataset.mobTab;
    haptic();
    if (detail && !detail.classList.contains("hidden")) {
      detailWasOpen = false;
      suppressReturn = true;
      if (typeof window.closeRun === "function") window.closeRun();
      window.setTimeout(() => { suppressReturn = false; }, 0);
    }
    showShell(name);
  });

  const syncDetail = () => {
    if (!media.matches) {
      detailWasOpen = false;
      document.body.classList.remove("mob-detail-active");
      syncReturnChip();
      return;
    }
    const open = !!detail && !detail.classList.contains("hidden");
    document.body.classList.toggle("mob-detail-active", open);
    if (open) {
      detailWasOpen = true;
      document.body.classList.remove("mob-shell-on", "mob-composing-active");
    } else if (detailWasOpen) {
      detailWasOpen = false;
      if (suppressReturn) { suppressReturn = false; syncReturnChip(); return; }
      showShell("tasks");
    }
    syncReturnChip();
  };
  if (detail) new MutationObserver(syncDetail).observe(detail, { attributes: true, attributeFilter: ["class"] });
  /* 新建表单关掉后壳回位（「返回工作区」等桌面路径不经过本文件）：composing 撤且无详情 → 回壳 */
  let wasComposing = false;
  new MutationObserver(() => {
    const composing = document.body.classList.contains("mob-composing-active");
    if (!media.matches) { wasComposing = composing; return; }
    const detailOpen = !!detail && !detail.classList.contains("hidden");
    if (wasComposing && !composing && !detailOpen &&
        !document.body.classList.contains("mob-shell-on")) {
      showShell(view);
    }
    wasComposing = composing;
  }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(paintAll).observe(document.documentElement,
    { attributes: true, attributeFilter: ["data-lang", "data-theme", "data-skin"] });
  window.addEventListener("orch:state", paintAll);
  media.addEventListener("change", () => {
    if (!media.matches) {
      document.body.classList.remove("mob-shell-on", "mob-detail-active", "mob-composing-active");
      if (returnChip) returnChip.classList.add("hidden");
      return;
    }
    paintAll();
    const detailOpen = !!detail && !detail.classList.contains("hidden");
    document.body.classList.toggle("mob-detail-active", detailOpen);
    if (detailOpen) document.body.classList.remove("mob-shell-on");
    else if (!document.body.classList.contains("mob-composing-active")) showShell(view);
  });

  const setup = () => {
    if (!media.matches) return;
    applyFontLevel(savedFontLevel());
    paintAll();
    syncDetail();
    if (!document.body.classList.contains("mob-composing-active") &&
        (!detail || detail.classList.contains("hidden"))) showShell(view);
  };
  if (window.__orchStateReady) setup();
  else window.addEventListener("orch:state-ready", setup, { once: true });
})();
