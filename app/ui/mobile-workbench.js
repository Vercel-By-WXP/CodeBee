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

/* 手机壳：首页 / 任务 / 我的。数据全部来自 /api/state，动作全部走既有实现。 */
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
  let view = "home";
  let filter = "all";
  let detailWasOpen = false;
  let suppressReturn = false;

  const state = () => (typeof S !== "undefined" && S.state) || {};
  const latestOf = (id) => (state().task_latest || {})[id] || {};
  const attentionOf = (id) => (state().task_attention || {})[id] || {};
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
    $("mob-new-title").textContent = label("新建任务", "New task");
    $("mob-new-sub").textContent = label("描述目标，自动拆解并执行", "Describe the goal — CodeBee plans and runs it");
    $("mob-sec-pending-title").textContent = label("等你处理", "Needs you");
    $("mob-sec-running-title").textContent = label("进行中", "Running");
    $("mob-sec-recent-title").textContent = label("最近完成", "Recently done");
    $("mob-tasks-title").textContent = label("任务", "Tasks");
    $("mob-me-title").textContent = label("我的", "Me");
    $("mob-me-conn-label").textContent = label("连接状态", "Connection");
    $("mob-me-theme-label").textContent = label("外观", "Appearance");
    $("mob-me-lang-label").textContent = label("语言", "Language");
    $("mob-me-sound-label").textContent = label("任务完成提示音", "Completion sound");
    $("mob-me-refresh").textContent = label("重新连接", "Reconnect");
    $("mob-me-note").textContent = label(
      "手机只保留连接、外观、语言和通知；皮肤、模型、通道等其余设置在电脑上打开。",
      "Phones keep only connection, appearance, language and notifications. Open the desktop app for everything else.");
    const titles = { home: label("首页", "Home"), tasks: label("任务", "Tasks"), new: label("新任务", "New"), me: label("我的", "Me") };
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

  const paintHome = () => {
    paintDate();
    const items = taskItems();
    const running = items.filter((item) => item.cat === "running");
    const pending = items.filter((item) => item.cat === "pending");
    const recent = items.filter((item) => item.cat === "done").slice(0, 3);
    const summary = running.length || pending.length
      ? label(running.length + " 个任务在跑 · " + pending.length + " 个等你处理",
              running.length + " running · " + pending.length + " awaiting you")
      : (items.length ? label("现在没有在跑的任务", "Nothing is running right now") : label("还没有任务，点下面新建一个。", "No tasks yet — create one below."));
    $("mob-summary").textContent = summary;
    $("mob-home-pending").innerHTML = pending.map(pendingCard).join("");
    $("mob-home-running").innerHTML = running.map(runningCard).join("");
    $("mob-home-recent").innerHTML = recent.map(taskRow).join("");
    $("mob-pending-count").textContent = pending.length ? String(pending.length) : "";
    $("mob-running-count").textContent = running.length ? String(running.length) : "";
    $("mob-sec-pending").classList.toggle("hidden", !pending.length);
    $("mob-sec-running").classList.toggle("hidden", !running.length);
    $("mob-sec-recent").classList.toggle("hidden", !recent.length);
  };

  const paintTasks = () => {
    const items = taskItems();
    const counts = { all: items.length, running: 0, pending: 0, done: 0 };
    items.forEach((item) => { if (counts[item.cat] != null) counts[item.cat] += 1; });
    const labels = { all: label("全部", "All"), running: label("进行中", "Running"),
      pending: label("待处理", "Needs you"), done: label("已完成", "Done") };
    $("mob-task-filters").innerHTML = Object.keys(labels).map((key) =>
      '<button type="button" class="mob-chip' + (filter === key ? " on" : "") + '" data-filter="' + key + '">' +
      esc(labels[key]) + '<b>' + counts[key] + '</b></button>').join("");
    const shown = filter === "all" ? items : items.filter((item) => item.cat === filter);
    $("mob-task-list").innerHTML = shown.length ? shown.map(taskRow).join("") :
      '<p class="mob-empty">' + esc(filter === "all"
        ? label("还没有任务", "No tasks yet")
        : label("这个筛选下没有任务", "No tasks in this filter")) + '</p>';
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
    $("mob-theme-seg").innerHTML = [["light", label("日间", "Light")], ["dark", label("夜间", "Dark")]]
      .map(([key, text]) => '<button type="button" class="' + (mode === key ? "on" : "") +
        '" data-mode="' + key + '">' + esc(text) + '</button>').join("");
    const lang = (typeof window.getLang === "function" ? window.getLang() : "zh");
    $("mob-lang-seg").innerHTML = [["zh", "中文"], ["en", "English"]]
      .map(([key, text]) => '<button type="button" class="' + (lang === key ? "on" : "") +
        '" data-lang="' + key + '">' + text + '</button>').join("");
    $("mob-sound").checked = localStorage.getItem("orch.sound") !== "0";
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
    paintHome();
    paintTasks();
    paintMe();
    paintBadge();
  };

  const switchView = (name) => {
    if (!views[name]) return;
    view = name;
    Object.keys(views).forEach((key) => views[key].classList.toggle("hidden", key !== name));
    tabbar.querySelectorAll("[data-mob-tab]").forEach((button) =>
      button.classList.toggle("active", button.dataset.mobTab === name));
  };

  const showShell = (name) => {
    if (!media.matches) return;
    document.body.classList.add("mob-shell-on");
    document.body.classList.remove("mob-composing-active");
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
    document.body.classList.remove("mob-shell-on", "mob-composing-active");
    if (typeof window.sideOpenTask === "function") window.sideOpenTask(id);
  };

  const openComposer = () => {
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

  const onClick = (event) => {
    const decision = event.target.closest("[data-decide]");
    if (decision) {
      event.preventDefault();
      event.stopPropagation();
      const id = decision.dataset.task;
      const kind = decision.dataset.decide;
      if (kind === "merge") window.gitMerge(id);
      else if (kind === "discard") window.gitDiscard(id);
      else if (kind === "approve") window.contractAction(id, "approve");
      else if (kind === "retry") window.retryTask(id);
      return;
    }
    const task = event.target.closest("[data-task]");
    if (task) { openTask(task.dataset.task); return; }
  };

  $("mob-home-pending").addEventListener("click", onClick);
  $("mob-home-running").addEventListener("click", onClick);
  $("mob-home-recent").addEventListener("click", onClick);
  $("mob-task-list").addEventListener("click", onClick);
  $("mob-task-filters").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter]");
    if (!chip) return;
    filter = chip.dataset.filter;
    paintTasks();
  });
  $("mob-new-task").addEventListener("click", openComposer);
  $("mob-tasks-new").addEventListener("click", openComposer);
  $("mob-refresh").addEventListener("click", () => {
    if (typeof window.poll === "function") window.poll();
    paintAll();
  });
  $("mob-me-refresh").addEventListener("click", () => {
    if (typeof window.poll === "function") window.poll();
    paintMe();
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
  $("mob-sound").addEventListener("change", (event) => {
    localStorage.setItem("orch.sound", event.target.checked ? "1" : "0");
  });
  tabbar.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mob-tab]");
    if (!button) return;
    const name = button.dataset.mobTab;
    if (name === "new") { openComposer(); return; }
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
      return;
    }
    const open = !!detail && !detail.classList.contains("hidden");
    document.body.classList.toggle("mob-detail-active", open);
    if (open) {
      detailWasOpen = true;
      document.body.classList.remove("mob-shell-on", "mob-composing-active");
    } else if (detailWasOpen) {
      detailWasOpen = false;
      if (suppressReturn) { suppressReturn = false; return; }
      showShell("tasks");
    }
  };
  if (detail) new MutationObserver(syncDetail).observe(detail, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(paintAll).observe(document.documentElement,
    { attributes: true, attributeFilter: ["data-lang", "data-theme", "data-skin"] });
  window.addEventListener("orch:state", paintAll);
  media.addEventListener("change", () => {
    if (!media.matches) {
      document.body.classList.remove("mob-shell-on", "mob-detail-active", "mob-composing-active");
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
    paintAll();
    syncDetail();
    if (!document.body.classList.contains("mob-composing-active") &&
        (!detail || detail.classList.contains("hidden"))) showShell(view);
  };
  if (window.__orchStateReady) setup();
  else window.addEventListener("orch:state-ready", setup, { once: true });
})();
