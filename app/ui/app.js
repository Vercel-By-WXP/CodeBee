/* Tutti 前端（无依赖，轮询 2s） */
"use strict";

const $ = (id) => document.getElementById(id);
const S = { state: null, catalog: null, catSig: "", providers: null, bindings: null, modelsSig: "", tab: "tasks", detailRunId: null, pollTimer: null, showArchived: localStorage.getItem("orch.showArchived") === "1" };

/* ---------------------------------------------------------- 工具 */
async function api(path, opts) {
  const res = await fetch(path, Object.assign({
    headers: { "Content-Type": "application/json" },
  }, opts || {}));
  let data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
  return data;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function statusChip(st) {
  const zh = { queued: "排队中", running: "运行中", done: "完成", failed: "失败", cancelled: "已取消" };
  return '<span class="chip ' + esc(st) + '">' + (zh[st] || esc(st)) + "</span>";
}

function runKindTag(k) {
  return k === "mgmt" ? '<span class="tag">管理</span>' : '<span class="tag">编排</span>';
}

/* 极简 Markdown 渲染（标题/加粗/行内码/列表/表格/代码块） */
function md2html(md) {
  const lines = String(md || "").split(/\r?\n/);
  let html = [], inCode = false, inTable = false, listOpen = false;
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const closeList = () => { if (listOpen) { html.push("</ul>"); listOpen = false; } };
  const closeTable = () => { if (inTable) { html.push("</tbody></table>"); inTable = false; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      closeList(); closeTable();
      html.push(inCode ? "</pre>" : "<pre>");
      inCode = !inCode; continue;
    }
    if (inCode) { html.push(esc(raw)); continue; }
    if (/^\|(.+)\|\s*$/.test(line)) {
      const cells = line.slice(1, -1).split("|").map((s) => s.trim());
      if (/^[-: ]+$/.test(cells.join(""))) continue; // 分隔行
      if (!inTable) { closeList(); html.push("<table><tbody>"); inTable = true; }
      html.push("<tr>" + cells.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>");
      continue;
    }
    closeTable();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeList(); html.push("<h" + (h[1].length + 1) + ">" + inline(h[2]) + "</h" + (h[1].length + 1) + ">"); continue; }
    if (/^[-*]\s+/.test(line)) {
      if (!listOpen) { html.push("<ul>"); listOpen = true; }
      html.push("<li>" + inline(line.replace(/^[-*]\s+/, "")) + "</li>"); continue;
    }
    closeList();
    if (!line.trim()) { continue; }
    html.push("<p>" + inline(line) + "</p>");
  }
  closeList(); closeTable();
  if (inCode) html.push("</pre>");
  return html.join("\n");
}

/* ---------------------------------------------------------- 轮询 */
async function poll() {
  try {
    const [state, cat, models] = await Promise.all([
      api("/api/state"), api("/api/catalog"), api("/api/models")]);
    S.state = state; S.catalog = cat.catalog;
    S.providers = models.providers; S.bindings = models.bindings;
    $("conn").textContent = "已连接";
    $("conn").className = "conn ok";
    render();
  } catch (e) {
    $("conn").textContent = "连接失败";
    $("conn").className = "conn bad";
  }
}

function render() {
  renderImplSelects();
  renderTaskList();
  renderRunList();
  renderSideTasks();
  renderRunDetail();
  renderCatalog();
  renderModels();
}

/* ---------------------------------------------------------- 模型接入 */
function renderModels() {
  const box = $("provider-list"), bbox = $("binding-list");
  if (!box) return;
  const sig = JSON.stringify([S.providers, S.bindings]);
  if (sig === S.modelsSig) return;  // 数据没变不重绘，避免清掉正在输入的内容
  S.modelsSig = sig;
  const provs = S.providers || [];
  box.innerHTML = provs.length ? provs.map((p) => providerCard(p)).join("")
    : '<div class="hint">暂无供应商——点「从 CCSwitch 导入」一键带入。</div>';
  const bindings = S.bindings || {};
  const targets = [["claude-code", "Claude Code"], ["codex-cli", "Codex CLI"]];
  bbox.innerHTML = targets.map(([id, label]) => {
    const b = bindings[id] || {};
    const opts = '<option value="">（不绑定，用 CLI 默认）</option>' + provs.map((p) =>
      '<option value="' + esc(p.id) + '"' + (b.provider_id === p.id ? " selected" : "") + ">" +
      esc(p.name) + "（" + esc(p.protocol) + "）</option>").join("");
    return '<div class="card"><div class="head"><span class="name">' + esc(label) + '</span></div>' +
      '<div class="model-row"><select id="bindprov-' + id + '">' + opts + '</select>' +
      '<button class="ghost small" onclick="saveBinding(\'' + id + '\')">保存</button></div>' +
      '<div class="model-row"><input id="bindmodel-' + id + '" placeholder="模型覆盖（可空=用供应商默认）" value="' + esc(b.model || "") + '">' +
      '</div>' +
      '<div class="ops" style="margin-top:8px"><label class="toggle"><input type="checkbox" id="binddiff-' + id + '"' +
      (b.difficulty_routing ? " checked" : "") + '> 按难度自动选模型（简单/困难）</label></div></div>';
  }).join("");
}

function providerCard(p) {
  return '<div class="card" id="pcard-' + esc(p.id) + '">' +
    '<div class="head"><span class="name">' + esc(p.name) + '</span><span class="tag">' + esc(p.protocol) + "</span>" +
    (p.source === "ccswitch" ? '<span class="tag">CCSwitch</span>' : "") + "</div>" +
    '<div class="model-row"><input id="pname-' + esc(p.id) + '" value="' + esc(p.name) + '" placeholder="名称"></div>' +
    '<div class="model-row"><input id="purl-' + esc(p.id) + '" value="' + esc(p.base_url) + '" placeholder="base_url"></div>' +
    '<div class="model-row"><input id="pkey-' + esc(p.id) + '" value="" placeholder="密钥（' + esc(p.api_key || "未设置") + '，留空=不改）"></div>' +
    '<div class="model-row"><input id="pmodel-' + esc(p.id) + '" value="' + esc(p.model || "") + '" placeholder="默认模型"></div>' +
    '<div class="model-row"><input id="peasy-' + esc(p.id) + '" value="' + esc(p.model_easy || "") + '" placeholder="简单任务模型（难度路由）"></div>' +
    '<div class="model-row"><input id="phard-' + esc(p.id) + '" value="' + esc(p.model_hard || "") + '" placeholder="困难任务模型（难度路由）"></div>' +
    '<div class="ops" style="margin-top:8px"><button class="ghost small" onclick="saveProvider(\'' + esc(p.id) + '\')">保存</button>' +
    '<button class="danger small" onclick="delProvider(\'' + esc(p.id) + '\')">删除</button></div></div>';
}

async function saveProvider(id) {
  const body = {
    id, name: $("pname-" + id).value.trim(),
    protocol: ($("purl-" + id).value.includes("anthropic") ? "anthropic" : (S.providers.find((x) => x.id === id) || {}).protocol || "anthropic"),
    base_url: $("purl-" + id).value.trim(),
    api_key: $("pkey-" + id).value.trim(),
    model: $("pmodel-" + id).value.trim(),
    model_easy: $("peasy-" + id).value.trim(),
    model_hard: $("phard-" + id).value.trim(),
  };
  try { await api("/api/models/provider", { method: "POST", body: JSON.stringify(body) }); }
  catch (e) { alert("保存失败：" + e.message); }
  poll();
}

async function delProvider(id) {
  if (!confirm("删除该供应商（绑定会自动解绑）？")) return;
  await api("/api/models/provider/delete", { method: "POST", body: JSON.stringify({ id }) });
  poll();
}

async function addProvider() {
  const name = prompt("供应商名称："); if (!name) return;
  const base_url = prompt("API 地址（http/https）："); if (!base_url) return;
  const api_key = prompt("API 密钥：") || "";
  await api("/api/models/provider", { method: "POST", body: JSON.stringify({ name, base_url, api_key, protocol: "anthropic" }) });
  poll();
}

async function importCCSwitch() {
  const msg = $("btn-import-ccswitch");
  msg.disabled = true; msg.textContent = "导入中…";
  try {
    const r = await api("/api/models/import-ccswitch", { method: "POST" });
    alert(r.message || ("导入 " + r.imported + " 个"));
  } catch (e) { alert("导入失败：" + e.message); }
  msg.disabled = false; msg.textContent = "从 CCSwitch 导入";
  poll();
}

async function saveBinding(id) {
  await api("/api/models/binding", { method: "POST", body: JSON.stringify({
    agent_id: id, provider_id: $("bindprov-" + id).value,
    model: $("bindmodel-" + id).value.trim(),
    difficulty_routing: $("binddiff-" + id).checked }) });
  poll();
}

/* ---------------------------------------------------------- 任务表单 */
function renderImplSelects() {
  const agents = (S.state && S.state.agents) || [];
  const sel = $("f-impl");
  const prev = sel.value;
  sel.innerHTML = agents.map((a) =>
    '<option value="' + esc(a.id) + '">' + esc(a.label) + "</option>").join("");
  if (prev && agents.some((a) => a.id === prev)) sel.value = prev;

  const box = $("critic-box");
  if ($("f-type").value === "novel") {
    box.classList.remove("hidden");
    const saved = new Set(Array.from($("f-critics").querySelectorAll("input:checked")).map((i) => i.value));
    $("f-critics").innerHTML = agents.map((a) =>
      '<label><input type="checkbox" value="' + esc(a.id) + '"' +
      (saved.size ? (saved.has(a.id) ? " checked" : "") : " checked") + ">" + esc(a.label) + "</label>"
    ).join("");
  } else {
    box.classList.add("hidden");
  }
}

async function createTask() {
  const msg = $("create-msg");
  msg.className = "msg"; msg.textContent = "提交中…";
  const payload = {
    type: $("f-type").value,
    mode: $("f-mode").value,
    title: $("f-title").value.trim(),
    goal: $("f-goal").value.trim(),
    context: $("f-context").value.trim(),
    workdir: $("f-workdir").value.trim(),
  };
  if (!payload.workdir) {
    msg.className = "msg err"; msg.textContent = "工作目录必填（之后会记住）"; return;
  }
  localStorage.setItem("orch.workdir", payload.workdir);
  if (payload.mode === "manual") payload.implementer = $("f-impl").value;
  const sid = $("f-resume-session").value;
  const resumeAgent = $("f-resume-agent").value;
  if (resumeAgent && sid) {
    const opt = $("f-resume-session").selectedOptions[0];
    payload.resume = { agent: resumeAgent, session: sid, preview: opt ? opt.textContent : "" };
  }
  if (payload.type === "code") {
    payload.verify_command = $("f-verify").value.trim();
  } else {
    payload.manuscript = $("f-manuscript").value.trim() || "manuscript.md";
    payload.rounds = parseInt($("f-rounds").value, 10) || 2;
    payload.threshold = parseFloat($("f-threshold").value) || 7.0;
    const critics = Array.from($("f-critics").querySelectorAll("input:checked")).map((i) => i.value);
    if (critics.length) payload.critics = critics;
  }
  try {
    const r = await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
    msg.textContent = "已创建，跳转运行页…";
    switchTab("runs");
    openRun(r.run_id);
    $("f-goal").value = "";
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  }
}

/* ---------------------------------------------------------- 会话延续 */
async function loadSessions() {
  const agent = $("f-resume-agent").value;
  const row = $("row-resume-session");
  const sel = $("f-resume-session");
  if (!agent) { row.classList.add("hidden"); return; }
  row.classList.remove("hidden");
  sel.innerHTML = '<option>（加载中…）</option>';
  try {
    const r = await api("/api/sessions");
    const list = (r.sessions || {})[agent] || [];
    sel.innerHTML = list.length ? list.map((s) => {
      const who = s.project ? " [" + String(s.project).replace(/[\\\\/]+$/, "").split(/[\\\\/]/).pop() + "]" : "";
      return '<option value="' + esc(s.session_id) + '">[' + esc(s.mtime) + "]" + who + " " + esc(s.preview.slice(0, 60)) + "</option>";
    }).join("") : '<option value="">（未找到该智能体的本地会话）</option>';
  } catch (e) {
    sel.innerHTML = '<option value="">（扫描失败）</option>';
  }
}

function renderTaskList() {
  const tasks = (S.state && S.state.tasks) || [];
  const archived = S.showArchived ? ((S.state && S.state.archived_tasks) || []) : [];
  const row = (t) =>
    '<div class="item"><div class="t"><span class="name">' + esc(t.title) + "</span>" +
    '<span class="tag">' + (t.type === "code" ? "代码" : "小说") + "</span>" +
    (t.archived ? '<span class="tag">已归档</span>' : "") +
    '<span class="time">' + esc(t.created_at) + "</span>" +
    (t.archived
      ? '<button class="ghost small" onclick="archiveTask(\'' + esc(t.id) + '\', false)">取消归档</button>'
      : '<button class="ghost small" onclick="archiveTask(\'' + esc(t.id) + '\', true)">归档</button>') +
    '<button class="danger small" onclick="deleteTask(\'' + esc(t.id) + '\')">删除</button></div>' +
    '<div class="desc">' + esc(t.goal) + "</div></div>";
  let html = tasks.length ? tasks.map((t) => row(t)).join("") : '<div class="hint">暂无任务</div>';
  if (S.showArchived) {
    html += "<h3>已归档</h3>" + (archived.length ? archived.map((t) => row(t)).join("") : '<div class="hint">没有已归档任务</div>');
  }
  $("task-list").innerHTML = html;
}

async function archiveTask(id, archived) {
  try {
    await api("/api/tasks/" + encodeURIComponent(id) + "/archive",
      { method: "POST", body: JSON.stringify({ archived: !!archived }) });
  } catch (e) { alert("操作失败：" + e.message); }
  poll();
}

async function deleteTask(id) {
  if (!confirm("删除该任务及其全部运行记录（含日志与报告）？不可恢复。")) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(id) + "/delete", { method: "POST" });
  } catch (e) { alert("删除失败：" + e.message); return; }
  if (S.detailRunId) closeRun();
  poll();
}

/* ---------------------------------------------------------- 运行列表 */
function renderRunList() {
  if (S.detailRunId) return;
  const runs = ((S.state && S.state.runs) || []).slice(0, 30);
  $("run-list").innerHTML = runs.length ? runs.map((r) =>
    '<div class="item" onclick="openRun(\'' + esc(r.id) + '\')">' +
    '<div class="t">' + runKindTag(r.kind) +
    '<span class="name">' + esc(r.title) + "</span>" + statusChip(r.status) +
    '<span class="time">' + esc(r.created_at) + "</span>" +
    '<button class="danger small" title="删除该记录" onclick="event.stopPropagation(); deleteRun(\'' + esc(r.id) + '\')">删除</button></div>' +
    '<div class="desc">' + esc(r.summary || r.error || (r.steps ? r.steps.length + " 个步骤" : "")) + "</div></div>"
  ).join("") : '<div class="hint">暂无运行记录</div>';
}

/* 侧栏「任务」树：任务 → 各 CLI 步骤，点击步骤在右侧打开运行详情 */
function renderSideTasks() {
  const box = $("side-tasks");
  if (!box) return;
  const runs = ((S.state && S.state.runs) || []).slice(0, 40);
  const archivedIds = new Set((((S.state || {}).archived_tasks) || []).map((t) => t.id));
  const sig = JSON.stringify([runs.map((r) => [r.id, r.status, (r.steps || []).length]), S.detailRunId, archivedIds.size]);
  if (sig === S.sideSig && box.children.length) return;
  S.sideSig = sig;
  const openKeys = new Set(Array.from(box.querySelectorAll("details.stask[open]")).map((d) => d.dataset.key));
  const groups = [], byKey = {};
  for (const r of runs) {
    if (r.task_id && archivedIds.has(r.task_id)) continue;  // 已归档任务不上侧栏
    const key = r.task_id || r.id;
    if (!byKey[key]) {
      byKey[key] = { key, title: r.title || r.id, status: r.status, active: false, runIds: [], steps: [] };
      groups.push(byKey[key]);
    }
    const g = byKey[key];
    g.runIds.push(r.id);
    if (r.status === "running") g.active = true;
    (r.steps || []).forEach((s) => g.steps.push(Object.assign({ runId: r.id }, s)));
  }
  box.innerHTML = groups.length ? groups.map((g, gi) => {
    if (g.active) g.status = "running";
    const isOpen = openKeys.size ? openKeys.has(g.key) : (S.detailRunId ? g.runIds.indexOf(S.detailRunId) >= 0 : gi === 0);
    const items = g.steps.slice(0, 8).map((s) =>
      '<div class="stepx" title="' + esc((s.note ? s.note + "：" : "") + (s.summary || "")) + '" onclick="sideOpenRun(\'' + esc(s.runId) + '\')">' +
      '<span class="sagent">' + esc(s.agent_label || s.agent || s.role || "") + "</span>" +
      '<span class="ssum">' + esc((s.note ? s.note + "：" : "") + (s.summary || s.role || "")) + "</span></div>"
    ).join("");
    const more = g.steps.length > 8
      ? '<div class="stepx" onclick="sideOpenRun(\'' + esc(g.runIds[0]) + '\')"><span class="ssum">… 共 ' + g.steps.length + " 步，点击查看全部</span></div>" : "";
    const empty = items ? "" : '<div class="stepx" onclick="sideOpenRun(\'' + esc(g.runIds[0]) + '\')"><span class="ssum">暂无步骤，点击查看</span></div>';
    return '<details class="stask" data-key="' + esc(g.key) + '"' + (isOpen ? " open" : "") + "><summary>" +
      '<span class="dot ' + esc(g.status) + '"></span><span class="t">' + esc(g.title) + "</span></summary>" + items + more + empty + "</details>";
  }).join("") : '<div class="side-empty">暂无任务</div>';
}

window.sideOpenRun = function (id) { switchTab("runs"); openRun(id); };

async function deleteRun(id) {
  if (!confirm("删除该运行记录（含全部日志与报告）？不可恢复。")) return;
  try {
    await api("/api/runs/" + encodeURIComponent(id) + "/delete", { method: "POST" });
  } catch (e) { alert("删除失败：" + e.message); return; }
  if (S.detailRunId === id) closeRun();
  poll();
}

async function openRun(id) {
  S.detailRunId = id;
  document.querySelector("#page-runs .panel:first-child").classList.add("hidden");
  $("run-detail").classList.remove("hidden");
  renderRunDetail();
}

function closeRun() {
  S.detailRunId = null;
  $("run-detail").classList.add("hidden");
  document.querySelector("#page-runs .panel:first-child").classList.remove("hidden");
}

async function renderRunDetail() {
  const id = S.detailRunId;
  if (!id) return;
  let run;
  try { run = (await api("/api/runs/" + encodeURIComponent(id))).run; }
  catch (e) { return; }
  if (!run) return;
  $("rd-title").textContent = run.title;
  const chip = $("rd-status");
  chip.className = "chip " + run.status;
  chip.textContent = { queued: "排队中", running: "运行中", done: "完成", failed: "失败", cancelled: "已取消" }[run.status] || run.status;
  const active = run.status === "queued" || run.status === "running";
  $("btn-cancel").classList.toggle("hidden", !active);
  $("btn-delete").classList.toggle("hidden", active);
  $("rd-meta").innerHTML =
    "创建 " + esc(run.created_at) +
    "　成本 $" + Number(run.cost_usd || 0).toFixed(3) +
    "　tokens " + (run.tokens || 0) +
    (run.mode ? "　模式 " + (run.mode === "auto" ? "智能" : "手动") : "") +
    (run.error ? '　<span style="color:var(--bad)">' + esc(run.error.slice(0, 200)) + "</span>" : "");
  renderPlan(run);
  $("rd-steps").innerHTML = (run.steps || []).map((s) =>
    '<div class="step" onclick="toggleLog(\'' + esc(run.id) + "', '" + esc(s.log) + '\')" title="' + esc(s.note || "") + '">' +
    '<span class="n">' + String(s.n).padStart(2, "0") + "</span>" +
    '<span class="role">' + esc(s.role) + "</span>" +
    '<span class="who">' + esc(s.agent_label || s.agent) + "</span>" +
    '<span class="sum">' + esc((s.note ? "◆ " + s.note + " — " : "") + (s.summary || "")) + "</span>" +
    '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span>" +
    statusChip(s.status) + "</div>"
  ).join("") || '<div class="hint">尚无步骤</div>';
  // 报告
  if (run.status === "done" || run.report) {
    const md = await fetch("/api/runs/" + encodeURIComponent(id) + "/report").then((r) => r.text());
    $("rd-report").innerHTML = md2html(md);
  } else {
    $("rd-report").innerHTML = '<div class="hint">运行结束后生成</div>';
  }
}

let currentLog = null;

function renderPlan(run) {
  const box = $("rd-plan");
  const plan = run.plan;
  if (!plan || !plan.steps || !plan.steps.length) { box.classList.add("hidden"); return; }
  const route = run.route || {};
  const routeHtml = Object.keys(route).length
    ? '<div class="meta" style="margin-top:8px">路由依据：' +
      Object.keys(route).map((k) => "<b>" + esc(k) + "</b> " + esc(route[k])).join("　|　") + "</div>"
    : "";
  box.classList.remove("hidden");
  box.innerHTML = "<h3 style='margin-top:0'>编排计划 <span class='tag'>来源 " + esc(plan.source || "?") + "</span></h3>" +
    '<div class="steps">' + plan.steps.map((s, i) =>
      '<div class="step" style="cursor:default"><span class="n">' + String(i + 1).padStart(2, "0") + "</span>" +
      '<span class="role">' + esc(s.title || "") + "</span>" +
      '<span class="sum">' + esc(s.detail || "") + "</span></div>").join("") + "</div>" + routeHtml;
}
async function toggleLog(runId, rel) {
  const box = $("rd-log"), pre = $("rd-log-text");
  if (currentLog === rel && !box.classList.contains("hidden")) {
    box.classList.add("hidden"); currentLog = null; return;
  }
  try {
    const r = await api("/api/runs/" + encodeURIComponent(runId) + "/log?step=" + encodeURIComponent(rel));
    pre.textContent = r.log || "（无输出）";
    box.classList.remove("hidden");
    currentLog = rel;
  } catch (e) { pre.textContent = "日志读取失败: " + e.message; box.classList.remove("hidden"); }
}

async function cancelRun() {
  if (!S.detailRunId) return;
  if (!confirm("确定取消该运行？")) return;
  await api("/api/runs/" + encodeURIComponent(S.detailRunId) + "/cancel", { method: "POST" });
}

/* ---------------------------------------------------------- 主题（白天/黑夜） */
function applyTheme() {
  const t = localStorage.getItem("orch.theme") === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  $("btn-theme").textContent = t === "dark" ? "🌙 夜间" : "☀️ 日间";
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  localStorage.setItem("orch.theme", next);
  applyTheme();
}

/* ---------------------------------------------------------- 智能体管理 */
function renderCatalog() {
  const cat = S.catalog || [];
  // 数据没变化就不重绘，避免 2s 轮询清掉用户正在输入的模型名
  const sig = JSON.stringify(cat);
  if (sig === S.catSig) return;
  S.catSig = sig;
  const groups = { installed: [], installable: [] };
  for (const c of cat) (groups[c.group] || groups.installable).push(c);
  $("ag-installed").innerHTML = groups.installed.map(card).join("");
  $("ag-installable").innerHTML = groups.installable.map(card).join("");
  $("ag-mocks").innerHTML =
    '<div class="card"><div class="head"><span class="name">演示智能体 A / B（mock）</span><span class="tag">内置</span></div>' +
    '<div class="note">不消耗任何配额即可演示完整流水线（起草→评审→修订→报告）。</div>' +
    '<div class="facts">始终可用，出现在任务表单中。</div></div>';
}

function card(c) {
  const orchBox = c.orch_kind
    ? '<label class="toggle"><input type="checkbox" ' + (c.orch_enabled ? "checked" : "") +
      ' onchange="toggleOrch(\'' + esc(c.id) + '\', this.checked)"> 参与编排</label>'
    : '<span class="tag">仅管理</span>';
  const modelBox = c.config_writable
    ? '<div class="model-row"><input id="model-' + esc(c.id) + '" placeholder="默认模型（写入配置文件）" value="' + esc(c.model || "") + '">' +
      '<button class="ghost small" onclick="saveModel(\'' + esc(c.id) + '\')">保存</button></div>'
    : (c.model ? '<div class="facts">模型：<b>' + esc(c.model) + "</b></div>" : "");
  const orchModel = c.orch_kind
    ? '<div class="model-row"><input id="orchmodel-' + esc(c.id) + '" placeholder="编排调用模型（如 gpt-5.5，留空用默认）" value="' + esc(c.orch_model || "") + '">' +
      '<button class="ghost small" onclick="saveOrchModel(\'' + esc(c.id) + '\')">应用</button></div>'
    : "";
  const ops = [
    c.installed && c.orch_kind ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'smoke\')">冒烟测试</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'upgrade\')">升级</button>' : "",
    !c.installed && c.has_install ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'install\')">安装</button>' : "",
    !c.installed && !c.has_install ? '<span class="hint">安装命令待配置（编辑 data/catalog.json）</span>' : "",
  ].join("");
  return '<div class="card">' +
    '<div class="head"><span class="name">' + esc(c.name) + "</span>" +
    (c.installed ? statusChip("done") : '<span class="tag">未安装</span>') + "</div>" +
    '<div class="note">' + esc(c.note || "") + "</div>" +
    '<div class="facts">版本 <b>' + esc(c.version || "-") + "</b>　" +
    "模型 <b>" + esc(c.model || "-") + "</b><br>" + esc(c.detail || c.config_path || "") + "</div>" +
    '<div class="ops">' + orchBox + ops + "</div>" + modelBox + orchModel + "</div>";
}

async function toggleOrch(id, enabled) {
  await api("/api/orchestration", { method: "POST", body: JSON.stringify({ agent_id: id, enabled }) });
  poll();
}

async function saveModel(id) {
  const v = $("model-" + id).value.trim();
  try {
    const r = await api("/api/catalog/" + encodeURIComponent(id) + "/model", { method: "POST", body: JSON.stringify({ model: v }) });
    alert("已写入：" + (r.model || v));
  } catch (e) { alert("失败：" + e.message); }
  poll();
}

async function saveOrchModel(id) {
  const v = $("orchmodel-" + id).value.trim();
  await api("/api/orchestration", { method: "POST", body: JSON.stringify({ agent_id: id, model: v }) });
  poll();
}

async function mgmt(id, op) {
  const names = { install: "安装", upgrade: "升级", smoke: "冒烟测试" };
  if (op === "install" && !confirm("确定执行安装？命令来自 data/catalog.json，可在管理页查看。")) return;
  const r = await api("/api/catalog/" + encodeURIComponent(id) + "/" + op, { method: "POST" });
  switchTab("runs");
  openRun(r.run_id);
}

/* ---------------------------------------------------------- 页签 & 初始化 */
const TAB_TITLES = { tasks: "任务", runs: "运行记录", agents: "智能体管理", models: "模型接入" };

function switchTab(name) {
  S.tab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-" + name));
  const title = $("page-title");
  if (title) title.textContent = TAB_TITLES[name] || "";
  if (name === "runs" && !S.detailRunId) closeRun();
}

window.openRun = openRun;
window.closeRun = closeRun;
window.archiveTask = archiveTask;
window.deleteTask = deleteTask;
window.toggleLog = toggleLog;
window.cancelRun = cancelRun;
window.deleteRun = deleteRun;
window.toggleOrch = toggleOrch;
window.saveModel = saveModel;
window.saveOrchModel = saveOrchModel;
window.mgmt = mgmt;
window.switchTab = switchTab;
window.saveProvider = saveProvider;
window.delProvider = delProvider;
window.addProvider = addProvider;
window.importCCSwitch = importCCSwitch;
window.saveBinding = saveBinding;

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("btn-back").addEventListener("click", closeRun);
  $("btn-cancel").addEventListener("click", cancelRun);
  $("btn-delete").addEventListener("click", () => { if (S.detailRunId) deleteRun(S.detailRunId); });
  $("btn-theme").addEventListener("click", toggleTheme);
  $("btn-menu").addEventListener("click", () => document.body.classList.toggle("side-collapsed"));
  $("btn-new-task").addEventListener("click", () => switchTab("tasks"));
  if (window.innerWidth < 900) document.body.classList.add("side-collapsed");
  applyTheme();
  $("btn-create").addEventListener("click", createTask);
  $("chk-archived").addEventListener("change", () => {
    S.showArchived = $("chk-archived").checked;
    localStorage.setItem("orch.showArchived", S.showArchived ? "1" : "0");
    renderTaskList();
  });
  $("chk-archived").checked = S.showArchived;
  $("f-resume-agent").addEventListener("change", loadSessions);
  $("f-workdir").value = localStorage.getItem("orch.workdir") || "";
  $("f-type").addEventListener("change", () => {
    const novel = $("f-type").value === "novel";
    $("f-code-only").classList.toggle("hidden", novel);
    $("f-novel-only").classList.toggle("hidden", !novel);
    renderImplSelects();
  });
  $("f-mode").addEventListener("change", () => {
    $("f-manual-only").classList.toggle("hidden", $("f-mode").value !== "manual");
  });
  $("f-type").dispatchEvent(new Event("change"));
  $("btn-reload-catalog").addEventListener("click", async () => {
    await api("/api/catalog/reload", { method: "POST" }); poll();
  });
  $("btn-import-ccswitch").addEventListener("click", importCCSwitch);
  $("btn-add-provider").addEventListener("click", addProvider);
  $("btn-reset-catalog").addEventListener("click", async () => {
    if (!confirm("恢复内置默认 catalog？你对该文件的修改将丢失。")) return;
    await api("/api/catalog/reset", { method: "POST" }); poll();
  });
  poll();
  S.pollTimer = setInterval(poll, 2000);
});
