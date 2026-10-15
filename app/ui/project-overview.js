/* Project progress uses the same state snapshot as the sidebar. No extra report
 * fetches, synthetic progress, or mutation of the new-task draft. */
"use strict";
(function () {
  let selected = "", attentionOnly = false, lastRender = "", remoteProjects = null, remoteFetchedAt = 0;
  const refreshProjectSpace = () => {
    if (typeof api !== "function" || Date.now() - remoteFetchedAt < 15000) return;
    remoteFetchedAt = Date.now();
    api("/api/projects", { timeout: 8000, busy: false, operation: false }).then(data => {
      if (data && Array.isArray(data.projects)) {
        remoteProjects = data.projects;
        lastRender = "";
        window.renderProjectOverview();
      }
    }).catch(() => {});
  };
  const key = path => {
    const raw = String(path || "").trim().replace(/\\/g, "/");
    const value = raw === "/" ? raw : raw.replace(/\/+$/, "");
    return /^[a-z]:/i.test(value) || value.startsWith("//") ? value.toLowerCase() : value;
  };
  const icon = name => '<svg class="ico" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  const date = run => Date.parse(String(run.created_at || run.started_at || "").replace(" ", "T")) || 0;
  const issue = run => run.paused || ["failed", "timeout"].includes(run.status) ||
    (run.status === "done" && run.verdict && run.verdict.pass === false);
  const badge = run => {
    const tone = issue(run) ? "warn" : run.status === "done" ? "ok" :
      ["running", "queued"].includes(run.status) ? "active" : "muted";
    return '<span class="project-badge ' + tone + '">' +
      icon(issue(run) ? "file-search" : run.status === "done" ? "check" : "history") + esc(runStatusText(run)) + '</span>';
  };

  window.renderProjectOverview = function () {
    const picker = document.getElementById("ov-project-select");
    if (!picker) return;
    refreshProjectSpace();
    const state = S.state || {};
    const archived = new Set((state.archived_tasks || []).map(task => task.id));
    (state.tasks || []).forEach(task => { if (task.archived) archived.add(task.id); });
    const tasks = (state.tasks || []).filter(task => !task.archived && !archived.has(task.id));
    const byId = new Map(tasks.map(task => [task.id, task]));
    const snapshots = new Map();
    [...Object.values(state.task_latest || {}), ...(state.runs || [])].forEach(run => snapshots.set(run.id, run));
    const runs = Array.from(snapshots.values()).filter(run => !archived.has(run.task_id))
      .slice().sort((a, b) => date(b) - date(a));
    const directory = run => (byId.get(run.task_id) || {}).workdir || run.workdir || "";
    const projects = new Map();
    tasks.forEach(task => { if (key(task.workdir)) projects.set(key(task.workdir), task.workdir); });
    runs.forEach(run => { const dir = directory(run); if (key(dir)) projects.set(key(dir), dir); });
    (remoteProjects || []).forEach(project => {
      if (key(project.workdir)) projects.set(key(project.workdir), project.workdir);
    });
    if (selected && !projects.has(selected)) { selected = ""; attentionOnly = false; }
    const options = '<option value="">' + esc(t("全部项目")) + '</option>' +
      Array.from(projects).sort((a, b) => a[1].localeCompare(b[1])).map(([id, path]) =>
        '<option value="' + esc(id) + '">' + esc(dirName(path) + ' · ' + path) + '</option>').join("");
    // Preserve the native select's focus/open state on routine SSE refreshes.
    if (picker.innerHTML !== options) picker.innerHTML = options;
    picker.value = selected;
    picker.title = selected ? projects.get(selected) : t("全部项目");
    picker.onchange = () => { selected = picker.value; attentionOnly = false; renderProjectOverview(); };

    const signature = JSON.stringify([selected, attentionOnly, getLang(), runs,
      tasks.map(task => [task.id, task.title, task.workdir, task.git_state])]);
    if (signature === lastRender) return;
    lastRender = signature;

    const scoped = runs.filter(run => !selected || key(directory(run)) === selected);
    const remote = (remoteProjects || []).find(project => project.id === selected);
    const remoteCounts = remote && remote.counts || {};
    const gates = tasks.filter(task => task.git_state === "isolated" && (!selected || key(task.workdir) === selected));
    const latest = new Map();
    scoped.forEach(run => { const id = run.task_id || run.id; if (!latest.has(id)) latest.set(id, run); });
    const needsAttention = new Set(gates.map(task => task.id));
    latest.forEach((run, id) => { if (issue(run)) needsAttention.add(id); });
    const status = document.getElementById("ov-project-status");
    status.innerHTML = '<span>' + icon("gauge") + esc(t("运行中")) + '<b>' + (remote ? (remoteCounts.running || 0) : scoped.filter(r => r.status === "running").length) + '</b></span>' +
      '<span>' + icon("history") + esc(t("排队中")) + '<b>' + (remote ? (remoteCounts.queued || 0) : scoped.filter(r => r.status === "queued").length) + '</b></span>' +
      (remote ? '<span>' + icon("folder") + esc(t("任务")) + '<b>' + (remoteCounts.tasks || 0) + '</b></span>' +
        '<span>' + icon("brain") + esc(t("记忆")) + '<b>' + (remoteCounts.memory || 0) + '</b></span>' : '') +
      '<button type="button" class="project-attention-toggle' + (needsAttention.size ? ' has-items' : '') + '" aria-pressed="' + attentionOnly + '">' +
      icon("file-search") + esc(t("需关注")) + '<b>' + needsAttention.size + '</b></button>';
    status.querySelector("button").onclick = () => { attentionOnly = !attentionOnly; renderProjectOverview(); };

    const visible = attentionOnly ? Array.from(latest.values()).filter(issue) : scoped;
    const gateList = attentionOnly && gates.length ? '<div class="project-gates">' + gates.map(task =>
      '<button type="button" class="ghost small" data-project-task="' + esc(task.id) + '">' +
      icon("file-search") + '<span>' + esc(t("待裁决")) + ' · ' + esc(task.title || task.id) + '</span></button>').join("") + '</div>' : "";
    const body = document.getElementById("ov-recent-body");
    body.innerHTML = gateList + (visible.length ?
      '<div class="project-table-wrap"><table class="project-runs"><thead><tr><th scope="col">' + esc(t("任务")) +
      '</th><th scope="col">' + esc(t("状态")) + '</th><th scope="col">' + esc(t("耗时")) +
      '</th><th scope="col">' + esc(t("开始时间")) + '</th></tr></thead><tbody>' + visible.slice(0, 5).map(run => {
        const duration = runDurationSeconds(run);
        const path = directory(run);
        return '<tr><td><button type="button" class="ov-run project-run-link" data-project-run="' + esc(run.id) + '">' +
          '<b>' + esc(run.title || (byId.get(run.task_id) || {}).title || run.id) + '</b>' +
          '<small>' + esc(path ? dirName(path) : t("默认工作区")) + '</small></button></td><td>' + badge(run) +
          '</td><td class="project-duration">' + esc(duration === null ? "—" : chatDurTxt(Math.round(duration))) +
          '</td><td><time>' + esc(run.started_at || run.created_at || "—") + '</time></td></tr>';
      }).join("") + '</tbody></table></div>' :
      (gateList ? "" : '<p class="project-empty">' + esc(t(attentionOnly ? "当前项目暂无待处理项" : "暂无运行记录")) + '</p>'));

    const results = scoped.filter(run => run.status === "done" && runOutcomeSummary(run)).slice(0, 3);
    const resultBox = document.getElementById("ov-project-results");
    resultBox.innerHTML = '<h3 class="project-section-title">' + esc(t("结果摘要")) + '</h3>' +
      (results.length ? '<div class="project-result-grid">' + results.map(run =>
        '<button type="button" class="project-result" data-project-run="' + esc(run.id) + '">' +
        '<span class="project-result-top">' + icon("file-text") + badge(run) + '</span>' +
        '<b>' + esc(run.title || run.id) + '</b><p>' + esc(runOutcomeSummary(run)) + '</p>' +
        '<span class="project-result-open">' + esc(t("查看运行结果")) + icon("arrow-right") + '</span></button>'
      ).join("") + '</div>' : '<p class="project-empty">' + esc(t("运行完成后，结果摘要会显示在这里")) + '</p>');
    [body, resultBox].forEach(box => {
      box.querySelectorAll("[data-project-run]").forEach(button => {
        button.onclick = () => jumpToRun(button.dataset.projectRun);
      });
    });
    body.querySelectorAll("[data-project-task]").forEach(button => {
      button.onclick = () => window.sideOpenTask(button.dataset.projectTask);
    });
  };
})();
