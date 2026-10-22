/* Activity model is dependency-free; browser integration uses the existing UI state. */
"use strict";
(function (root) {
  const READ_KEY = "orch.activity.read.v1";
  const VIEW_KEY = "orch.activity.view.v1";
  const IGNORE_KEY = "orch.activity.ignored.v1";
  const activeStatuses = new Set(["running", "queued"]);
  // A cancelled run is already resolved and does not require a verdict.
  // Keep only runs that need follow-up in the attention bucket; otherwise a
  // cancelled latest run can keep the activity badge alive indefinitely.
  const failures = new Set(["failed", "timeout"]);
  // rowHtml 引用的状态文案表：此前 browserStatusLabel 全仓库无定义，
  // 任何非待裁决行渲染即 ReferenceError 断掉整个活动列表（2026-10-21 自测实抓）。
  const STATUS_LABELS = {
    running: "运行中", queued: "排队中", done: "已完成",
    failed: "失败", timeout: "超时", canceled: "已取消", cancelled: "已取消",
  };
  const browserStatusLabel = (s) => t(STATUS_LABELS[s] || s || "");
  const compact = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 240);
  const fingerprint = (run) => [run.id, run.status, run.ended_at || ""].join("|");

  function parseTime(value) {
    // Server timestamps without a zone are local time, like the rest of the UI.
    return new Date(String(value || "").replace(" ", "T"));
  }

  function dayGroup(value, now) {
    const date = parseTime(value);
    if (!Number.isFinite(date.getTime())) return "更早";
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    return date >= today ? "今天" : date >= yesterday ? "昨天" : "更早";
  }

  function build(state, reads, now, ignoredRuns) {
    state = state || {};
    reads = reads || {};
    now = now || new Date();
    ignoredRuns = ignoredRuns || ignored;
    const tasks = new Map((state.tasks || []).filter((task) => !task.archived).map((task) => [task.id, task]));
    const archived = new Set((state.archived_tasks || []).map((task) => task.id));
    const latest = state.task_latest || {};
    const runs = new Map();
    // task_latest covers tasks outside the recent 40-run window.
    (state.runs || []).forEach((run) => runs.set(run.id, run));
    Object.values(latest).forEach((run) => runs.set(run.id, run));
    const rows = [];
    runs.forEach((run) => {
      if (!run.id || (run.task_id && (!tasks.has(run.task_id) || archived.has(run.task_id)))) return;
      const task = tasks.get(run.task_id) || {};
      const fp = fingerprint(run);
      const isLatest = !run.task_id || !latest[run.task_id] || latest[run.task_id].id === run.id;
      const active = activeStatuses.has(run.status);
      const attention = (state.task_attention || {})[run.task_id] || {};
      // A running isolated branch is being written; offer verdict only after it stops.
      const reasons = [];
      if (isLatest && task.git_state === "isolated" && !active) reasons.push("待裁决");
      if (isLatest && attention.approval_pending) reasons.push("待审批");
      if (isLatest && active && run.paused) reasons.push("已暂停");
      if (isLatest && failures.has(run.status)) reasons.push(run.status === "timeout" ? "超时" : "执行失败");
      // Ignoring dismisses this exact run state; a rerun changes the fingerprint and comes back.
      const muted = ignoredRuns[run.id] === fp;
      const needs = reasons.length > 0 && !muted;
      if (!needs && !active && run.status !== "done") return;
      const step = run.current_step || {};
      const failedStep = run.failed_step || {};
      const stepText = [step.n ? "#" + step.n : "", step.role, step.agent_label || step.agent].filter(Boolean).join(" · ");
      const summary = compact(needs ? (run.error || run.summary || failedStep.summary || step.summary) :
        active ? [stepText, step.summary].filter(Boolean).join(" — ") : (run.summary || step.summary));
      const time = run.ended_at || run.started_at || run.created_at || task.created_at || "";
      const read = reads[run.id] === fp;
      const unread = run.status === "done" && !read;
      rows.push({ id: run.id, taskId: task.id || "", title: task.title || run.title || run.id,
        status: run.status, time, workdir: task.workdir || "", summary,
        reasons, needs, read, unread, fingerprint: fp,
        step: Number(failedStep.n) || 0,
        target: failures.has(run.status) ? "steps" : reasons.includes("待裁决") ? "git" :
          reasons.includes("待审批") ? "approval" : active ? "hive" : "result",
        group: needs ? "需要你处理" : active ? "正在执行" : dayGroup(time, now) });
    });
    rows.sort((a, b) => (parseTime(b.time).getTime() || 0) - (parseTime(a.time).getTime() || 0) || a.id.localeCompare(b.id));
    return { rows, pending: rows.filter((row) => row.needs && !row.read).length,
      unread: rows.filter((row) => row.unread && !row.needs).length };
  }

  let reads = {};
  let ignored = {};
  const model = { build, fingerprint, dayGroup, ignore };
  if (typeof module !== "undefined" && module.exports) module.exports = model;
  root.activityModel = model;   // 状态侧栏「待你处理」等处复用同一套 needs 判定
  if (typeof document === "undefined") return;

  let enabled = false;
  let signature = "";
  try {
    const saved = JSON.parse(localStorage.getItem(READ_KEY) || "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) reads = saved;
    const savedIgnored = JSON.parse(localStorage.getItem(IGNORE_KEY) || "{}");
    if (savedIgnored && typeof savedIgnored === "object" && !Array.isArray(savedIgnored)) ignored = savedIgnored;
    enabled = localStorage.getItem(VIEW_KEY) === "1";
  } catch (error) { /* Storage may be unavailable; session state still works. */ }

  function saveReads() {
    // Bounded persistent index. Reading a rerun cannot acknowledge an older/newer run.
    reads = Object.fromEntries(Object.entries(reads).slice(-1000));
    try { localStorage.setItem(READ_KEY, JSON.stringify(reads)); } catch (error) { /* memory only */ }
  }

  function saveIgnored() {
    ignored = Object.fromEntries(Object.entries(ignored).slice(-1000));
    try { localStorage.setItem(IGNORE_KEY, JSON.stringify(ignored)); } catch (error) { /* memory only */ }
  }

  function paintToggle() {
    const button = $("btn-notify-toggle");
    if (!button) return;
    button.classList.toggle("on", enabled);
    button.classList.remove("off");
    button.setAttribute("aria-pressed", String(enabled));
    button.title = t(enabled ? "关闭活动视图" : "打开活动视图") + " (Ctrl+Alt+U)";
    button.setAttribute("aria-label", button.title);
    $("side-projects").classList.toggle("hidden", enabled);
    $("side-activity").classList.toggle("hidden", !enabled);
  }

  function markViewed(run) {
    if (!run || run.status !== "done" || reads[run.id] === fingerprint(run)) return;
    delete reads[run.id];
    reads[run.id] = fingerprint(run);
    saveReads();
    render();
  }

  function ignore(id) {
    const row = build(S.state, reads).rows.find((item) => item.id === id);
    if (!row) return;
    delete ignored[id];
    ignored[id] = row.fingerprint;
    saveIgnored();
    render();
  }

  function rowHtml(row) {
    const selected = S.detailRunId === row.id || (row.taskId && S.detailTaskKey === row.taskId);
    const status = row.needs ? row.reasons.map((reason) => t(reason)).join(" · ") : browserStatusLabel(row.status);
    return '<button type="button" class="activity-row' + (selected ? ' selected' : '') + '" data-activity-run="' + esc(row.id) + '"' +
      (selected ? ' aria-current="true"' : '') + '>' +
      '<span class="activity-title"><strong>' + esc(row.title) + '</strong>' +
      (row.unread ? '<span class="activity-dot" title="' + esc(t("未读")) + '"><span class="sr-only">' + esc(t("未读")) + '</span></span>' : '') + '</span>' +
      '<span class="activity-meta"><span class="activity-status ' + (row.needs ? 'needs ' : '') + esc(row.status) + '">' + esc(status) + '</span>' +
      '<time title="' + esc(row.time) + '">' + esc(relTime(row.time)) + '</time></span>' +
      (row.summary ? '<span class="activity-summary">' + esc(row.summary) + '</span>' : '') +
      (row.workdir ? '<span class="activity-project" title="' + esc(row.workdir) + '">' + esc(dirName(row.workdir)) + '</span>' : '') + '</button>';
  }

  function render() {
    if (!$("activity-list")) return;
    const data = build(S.state, reads);
    const badge = $("activity-badge");
    const count = data.pending + data.unread;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.classList.toggle("hidden", !count);
    badge.title = t("{0} 项待处理 · {1} 项完成未读", data.pending, data.unread);
    $("activity-count").textContent = badge.title;
    $("activity-read-all").disabled = !data.rows.some((row) => !row.read);
    paintToggle();
    if (!enabled) return;
    const sig = JSON.stringify([data, S.detailRunId, S.detailTaskKey, document.documentElement.dataset.lang]);
    if (sig === signature) return;
    signature = sig;
    const focused = document.activeElement && document.activeElement.dataset.activityRun;
    $("activity-list").innerHTML = ["需要你处理", "正在执行", "今天", "昨天", "更早"].map((group) => {
      const rows = data.rows.filter((row) => row.group === group);
      if (!rows.length) return "";
      return '<section class="activity-group' + (group === "需要你处理" ? ' needs' : '') + '"><h3>' + esc(t(group)) +
        '<span>' + rows.length + '</span></h3>' + rows.map(rowHtml).join("") + '</section>';
    }).join("") || '<div class="activity-empty">' + esc(t("暂无活动，开始一个任务后会在这里显示进展。")) + '</div>';
    if (focused) {
      const button = Array.from($("activity-list").querySelectorAll("[data-activity-run]")).find((item) => item.dataset.activityRun === focused);
      if (button) button.focus({ preventScroll: true });
    }
  }

  function toggle() {
    enabled = !enabled;
    if (document.body.classList.contains("files-mode")) closeFolderFiles();
    if (document.body.classList.contains("settings-mode")) exitSettings();
    document.body.classList.remove("side-collapsed");
    try { localStorage.setItem(VIEW_KEY, enabled ? "1" : "0"); } catch (error) { /* memory only */ }
    signature = "";
    render();
  }

  function openRow(id) {
    const row = build(S.state, reads).rows.find((item) => item.id === id);
    if (!row) return;
    if (row.target === "steps") sideOpenRun(row.id, row.step, "steps");
    else if (row.taskId && ["git", "approval"].includes(row.target)) sideOpenTask(row.taskId, row.target === "approval" ? "hive" : row.target);
    else sideOpenRun(row.id, 0, row.target);
    if (row.target === "approval") setRdMetaOpen(true);
    render();
  }

  root.CodeBeeActivity = { model, render, paintToggle, markViewed };
  document.addEventListener("DOMContentLoaded", () => {
    $("btn-notify-toggle").addEventListener("click", toggle);
    const closeBtn = $("activity-close");
    if (closeBtn) closeBtn.addEventListener("click", () => { if (enabled) toggle(); });
    $("activity-sound").addEventListener("click", toggleNotifySound);
    $("activity-read-all").addEventListener("click", () => {
      build(S.state, reads).rows.filter((row) => !row.read).forEach((row) => {
        delete reads[row.id]; reads[row.id] = row.fingerprint;
      });
      saveReads(); render();
    });
    $("activity-list").addEventListener("click", (event) => {
      const row = event.target.closest("[data-activity-run]");
      if (row) openRow(row.dataset.activityRun);
    });
    document.addEventListener("keydown", (event) => {
      if (event.ctrlKey && event.altKey && !event.metaKey && !event.shiftKey && !event.repeat && !event.isComposing && event.key.toLowerCase() === "u") {
        event.preventDefault(); toggle();
      }
    });
    window.addEventListener("storage", (event) => {
      if (event.key !== READ_KEY && event.key !== IGNORE_KEY) return;
      try {
        const saved = JSON.parse(event.newValue || "{}");
        if (!saved || typeof saved !== "object" || Array.isArray(saved)) return;
        if (event.key === READ_KEY) reads = saved; else ignored = saved;
      } catch (error) { return; }
      render();
    });
    render();
  });
})(typeof window !== "undefined" ? window : globalThis);
