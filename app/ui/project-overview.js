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
    const remoteByPath = new Map();
    (remoteProjects || []).forEach(project => {
      if (key(project.workdir)) {
        projects.set(key(project.workdir), project.workdir);
        remoteByPath.set(key(project.workdir), project);
      }
    });
    if (selected && !projects.has(selected) && !(remoteProjects || []).some(project => project.id === selected)) { selected = ""; attentionOnly = false; }
    const options = '<option value="">' + esc(t("全部项目")) + '</option>' +
      Array.from(projects).sort((a, b) => a[1].localeCompare(b[1])).map(([id, path]) => {
        const project = remoteByPath.get(id);
        return '<option value="' + esc(project ? project.id : id) + '">' +
          esc(dirName(path) + ' · ' + path) + '</option>';
      }).join("");
    // Preserve the native select's focus/open state on routine SSE refreshes.
    if (picker.innerHTML !== options) picker.innerHTML = options;
    const selectedRemote = (remoteProjects || []).find(project => project.id === selected);
    const selectedPath = selectedRemote ? key(selectedRemote.workdir) : selected;
    picker.value = selectedRemote ? selectedRemote.id : selected;
    picker.title = selectedRemote ? selectedRemote.workdir : (selected ? projects.get(selected) : t("全部项目"));
    picker.onchange = () => { selected = picker.value; attentionOnly = false; renderProjectOverview(); };

    const signature = JSON.stringify([selected, attentionOnly, getLang(), runs,
      tasks.map(task => [task.id, task.title, task.workdir, task.git_state])]);
    if (signature === lastRender) return;
    lastRender = signature;

    const scoped = runs.filter(run => !selected || key(directory(run)) === selected || key(directory(run)) === selectedPath);
    const remote = selectedRemote || remoteByPath.get(selected);
    const remoteCounts = remote && remote.counts || {};
    const gates = tasks.filter(task => task.git_state === "isolated" && (!selected || key(task.workdir) === selected || key(task.workdir) === selectedPath));
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

  const selectedProjectId = () => document.getElementById("ov-project-select")?.value || "";
  window.projectShareOpen = async function () {
    const id = selectedProjectId();
    if (!id) { toast(t("请先选择项目"), true); return; }
    try {
      const result = await api("/api/sharing/project/" + encodeURIComponent(id), { timeout: 10000 });
      openModal(t("分享项目"), '<p class="hint">' + esc(t("复制分享码给团队成员；内容只包含项目元数据、记忆、自动化和专家快照。")) + '</p>' +
        '<textarea id="project-share-code" rows="7" readonly style="width:100%;font-family:var(--mono);font-size:12px;word-break:break-all">' + esc(result.code || "") + '</textarea>',
        '<button class="primary" onclick="navigator.clipboard?.writeText($(\'project-share-code\').value);toast(t(\'分享码已复制到剪贴板\'))">' + esc(t("复制分享码")) + '</button>');
    } catch (error) { toast(t("导出失败：") + error.message, true); }
  };
  window.projectImportOpen = function () {
    openModal(t("导入分享包"), '<p class="hint">' + esc(t("先预览分享包，确认后才会导入。")) + '</p><textarea id="project-import-code" rows="7" style="width:100%;font-family:var(--mono);font-size:12px;word-break:break-all"></textarea><div id="project-import-preview" class="hint"></div>',
      '<button class="ghost" onclick="closeModal()">' + esc(t("取消")) + '</button><button class="ghost" onclick="projectImportPreview()">' + esc(t("解析预览")) + '</button><button class="primary" id="project-import-apply" disabled onclick="projectImportApply()">' + esc(t("确认导入")) + '</button>');
  };
  window.projectImportPreview = async function () {
    const code = $("project-import-code")?.value.trim(); if (!code) return;
    try { const result = await api("/api/sharing/import", { method: "POST", body: JSON.stringify({ code, apply: false }) });
      $("project-import-preview").textContent = t("类型：") + result.kind + " · " + t("预览成功"); $("project-import-apply").disabled = false;
    } catch (error) { $("project-import-preview").textContent = error.message; $("project-import-apply").disabled = true; }
  };
  window.projectImportApply = async function () {
    const code = $("project-import-code")?.value.trim(); if (!code) return;
    try { const result = await api("/api/sharing/import", { method: "POST", body: JSON.stringify({ code, apply: true }) }); toast(t("已导入分享包：") + (result.kind || "")); closeModal(); }
    catch (error) { toast(t("导入失败：") + error.message, true); }
  };
  window.projectPermissionsOpen = async function () {
    const id = selectedProjectId(); if (!id) { toast(t("请先选择项目"), true); return; }
    try { const result = await api("/api/projects/" + encodeURIComponent(id) + "/permissions");
      const rows = Object.entries(result.members || {}).map(([member, role]) => '<div class="item"><span class="name">' + esc(member) + '</span><span class="tag">' + esc(role) + '</span></div>').join("");
      openModal(t("成员权限"), '<div class="list">' + (rows || '<p class="hint">' + esc(t("当前只有本机 owner")) + '</p>') + '</div><div class="input-row"><input id="project-member-name" placeholder="' + esc(t("成员 ID")) + '"><select id="project-member-role"><option value="viewer">viewer</option><option value="editor">editor</option></select><button class="primary" onclick="projectPermissionSave()">' + esc(t("保存成员")) + '</button></div>', '');
      window._projectPermissionId = id;
    } catch (error) { toast(t("读取权限失败：") + error.message, true); }
  };
  window.projectPermissionSave = async function () {
    const member = $("project-member-name")?.value.trim(), role = $("project-member-role")?.value;
    if (!member || !window._projectPermissionId) return;
    try { await api("/api/projects/" + encodeURIComponent(window._projectPermissionId) + "/permissions", { method: "POST", body: JSON.stringify({ member, role }) }); toast(t("成员权限已更新")); projectPermissionsOpen(); }
    catch (error) { toast(t("更新权限失败：") + error.message, true); }
  };
  window.cloudReadinessOpen = async function () {
    try { const data = await api("/api/cloud/readiness");
      const checks = Object.entries(data.checks || {}).map(([key, value]) => '<div class="item"><span class="name">' + esc(key) + '</span><span class="tag ' + (value ? 'ok' : 'warn') + '">' + (value ? esc(t("通过")) : esc(t("未通过"))) + '</span></div>').join("");
      openModal(t("云端准入"), '<p class="hint">' + esc(data.policy || "") + '</p><div class="list">' + checks + '</div>', '');
    } catch (error) { toast(t("读取云端准入失败：") + error.message, true); }
  };
})();
