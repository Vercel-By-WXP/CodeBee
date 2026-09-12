/* Tutti 前端（无依赖；状态走 SSE 实时推送，断线自动降级轮询） */
"use strict";

const $ = (id) => document.getElementById(id);
const S = { state: null, catalog: null, catSig: "", providers: null, bindings: null, modelsSig: "", bindSig: "", tab: "tasks", detailRunId: null, pollTimer: null, showArchived: localStorage.getItem("orch.showArchived") === "1", selProvs: {}, selModels: {}, selRuns: {}, bindSel: {}, catalogChecking: false, updateCheckAt: 0, control: null, sseLive: false, es: null, flows: null, orch: null, orchSig: "", settings: null };

/* ---------------------------------------------------------- 任务类型（流程） */
async function loadFlows() {
  try {
    const r = await api("/api/flows");
    S.flows = r.flows || [];
  } catch (e) { S.flows = []; }
  renderTypeOptions();
}

function flowById(id) {
  return (S.flows || []).find((f) => f.id === id) || null;
}

function renderTypeOptions() {
  const sel = $("f-type");
  if (!sel || !S.flows) return;
  const prev = sel.value;
  sel.innerHTML = (S.flows || []).map((f) => {
    const desc = f.engine === "code" ? "实现 → 验证 → 评审" : "起草 → 多维评审 → 门禁";
    return '<option value="' + esc(f.id) + '">' + esc(f.icon || "") + " " + esc(f.name) + "（" + desc + (f.builtin ? "" : " · 自定义") + "）</option>";
  }).join("");
  if (prev && flowById(prev)) sel.value = prev;
  onTypeChange();
}

/* 切换类型：按引擎显隐表单区、带出流程默认值 */
function onTypeChange() {
  const flow = flowById($("f-type").value);
  const engine = flow ? flow.engine : "code";
  const isReview = engine === "review";
  const codeOnly = $("f-code-only"), reviewOnly = $("f-review-only");
  if (codeOnly) codeOnly.classList.toggle("hidden", isReview);
  if (reviewOnly) reviewOnly.classList.toggle("hidden", !isReview);
  const goal = $("f-goal");
  if (goal && flow && flow.goal_hint) goal.placeholder = flow.goal_hint;
  if (isReview && flow) {
    if (flow.manuscript) $("f-manuscript").value = flow.manuscript;
    if (flow.rounds) $("f-rounds").value = flow.rounds;
    if (flow.threshold) $("f-threshold").value = flow.threshold;
    const saved = ($("f-rubric").value || "").trim();
    if (!saved && flow.rubric) $("f-rubric").value = flow.rubric.join(", ");
    // 连载参数预填（用户可改/可清空 = 单稿件模式）
    if (flow.serial) {
      if (!$("f-chapters").value) $("f-chapters").value = flow.serial.chapters || "";
      if (!$("f-words-per-ch").value) $("f-words-per-ch").value = flow.serial.words_per_chapter || "";
    }
  }
  renderImplSelects();
}

/* ---------------------------------------------------------- 本机身份与鉴权 */
function clientId() {
  let id = localStorage.getItem("orch.client");
  if (!id) {
    const buf = new Uint8Array(8);
    if (window.crypto && crypto.getRandomValues) {
      crypto.getRandomValues(buf);
      id = "c-" + Date.now() + "-" + Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
    } else {
      id = "c-" + Date.now();  // 极老浏览器兜底：仅本地设备标识，非加密用途
    }
    localStorage.setItem("orch.client", id);
  }
  return id;
}

function deviceName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Edg\//.test(ua)) return "Edge·电脑";
  if (/Chrome/.test(ua)) return "Chrome·电脑";
  return "电脑";
}

function authHeaders(extra) {
  return Object.assign({
    "Content-Type": "application/json",
    "X-Tutti-Token": localStorage.getItem("orch.token") || "",
    "X-Tutti-Client": clientId(),
    "X-Tutti-Name": encodeURIComponent(deviceName()),  // 头值必须 ISO-8859-1
  }, extra || {});
}

/* EventSource 带不了自定义头：鉴权与设备身份全走 query */
function qsAuth() {
  const q = new URLSearchParams();
  const t = localStorage.getItem("orch.token");
  if (t) q.set("token", t);
  q.set("client", clientId());
  q.set("name", encodeURIComponent(deviceName()));
  const s = q.toString();
  return s ? "?" + s : "";
}

/* ---------------------------------------------------------- 工具 */
async function api(path, opts) {
  const res = await fetch(path, Object.assign({
    headers: authHeaders(),
  }, opts || {}));
  if (res.status === 401) { showTokenGate("令牌不正确或已更换，请重新输入"); throw new Error("需要访问令牌"); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (res.status === 423 && data && data.control) setControl(data.control);
  if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
  return data;
}

/* 轻提示：3.5s 自动消失 */
function toast(msg, bad) {
  let el = $("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "toast"; }, 3500);
}

/* 访问令牌门（仅远程设备会碰到） */
function showTokenGate(err) {
  const g = $("token-gate");
  if (!g) return;
  if (err) $("gate-err").textContent = err;
  g.classList.remove("hidden");
  setTimeout(() => $("gate-token").focus(), 50);
}

function submitToken() {
  const v = $("gate-token").value.trim();
  if (!v) return;
  localStorage.setItem("orch.token", v);
  location.reload();
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

/* 供应商来源标签：手动添加的不打标，其余按来源 id → 显示名映射 */
function srcTag(source) {
  if (!source || source === "manual") return "";
  const name = (S.sourceNames || {})[source] || source;
  return '<span class="tag">' + esc(name) + "</span>";
}

/* 只有 anthropic / openai 能注入到 CLI；google 等仅登记 */
function bindableProvs(provs) {
  return (provs || []).filter((p) => p.protocol === "anthropic" || p.protocol === "openai");
}

/* ---------------------------------------------------------- 通用弹框 */
function openModal(title, bodyHtml, footHtml) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHtml || "";
  $("modal-foot").innerHTML = footHtml || "";
  $("modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function closeModal() {
  const m = $("modal");
  if (!m) return;
  m.classList.add("hidden");
  $("modal-body").innerHTML = "";
  $("modal-foot").innerHTML = "";
  document.body.classList.remove("modal-open");
}

/* catalog 里 model 可能是字符串，也可能是 {default:...} 之类的对象 */
function fmtModel(m) {
  if (m == null || m === "") return "";
  return typeof m === "object" ? JSON.stringify(m) : String(m);
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

/* ---------------------------------------------------------- 状态同步：SSE 实时 + 轮询降级 */
/* 应用 /api/state 或 SSE 推送的状态载荷 */
function applyState(d) {
  S.state = d;
  if (d.control) setControl(d.control);
  $("conn").textContent = "已连接";
  $("conn").className = "conn ok";
}

async function refreshState() {
  applyState(await api("/api/state"));
}

async function poll() {
  try {
    const [cat, models] = await Promise.all([api("/api/catalog"), api("/api/models")]);
    S.catalog = cat.catalog;
    S.catalogChecking = !!cat.checking;
    S.providers = models.providers; S.bindings = models.bindings;
    S.modelCatalog = models.catalog || [];
    S.sourceNames = models.source_names || S.sourceNames || {};
    if (!S.sseLive) await refreshState();
    render();
  } catch (e) {
    if (!S.sseLive || !S.es) {
      $("conn").textContent = "连接失败";
      $("conn").className = "conn bad";
    }
  }
}

function schedulePolling() {
  clearInterval(S.pollTimer);
  S.pollTimer = setInterval(poll, S.sseLive ? 8000 : 2000);
}

/* SSE：状态变化即时推送；断开自动重连，重连失败降级为 2s 轮询 */
function startSSE() {
  if (!window.EventSource) return;
  try {
    const es = new EventSource("/api/events" + qsAuth());
    S.es = es;
    es.onopen = () => {
      S.sseLive = true;
      schedulePolling();
      poll();
    };
    es.onmessage = (ev) => {
      try { applyState(JSON.parse(ev.data)); render(); } catch (e) { /* ignore */ }
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {  // 彻底失败（如 401）：降级轮询
        S.es = null; S.sseLive = false; schedulePolling(); poll();
      } else {
        $("conn").textContent = "重连中…";
        $("conn").className = "conn bad";
      }
    };
  } catch (e) { /* EventSource 不可用：保持轮询 */ }
}

/* ---------------------------------------------------------- 多端控制权 */
function setControl(c) {
  S.control = c || null;
  renderCtrl();
}

function renderCtrl() {
  const el = $("ctrl-pill");
  if (!el) return;
  const c = S.control;
  el.classList.remove("hidden", "mine", "held");
  if (!c || c.mode === "free") {
    el.textContent = "🔓 控制空闲";
  } else if (c.mine) {
    el.classList.add("mine");
    el.textContent = "🎮 你在控制";
  } else {
    el.classList.add("held");
    el.textContent = "🔒 " + (c.holder || "其他设备") + " 控制中";
  }
}

async function ctrlAction(action, force) {
  try {
    const d = await api("/api/control", { method: "POST", body: JSON.stringify({ action, force: !!force }) });
    setControl(d.control);
  } catch (e) { toast(e.message, true); }
}

function ctrlClick() {
  const c = S.control;
  if (!c || c.mode === "free") return ctrlAction("acquire");
  if (c.mine) return ctrlAction("release");
  if (confirm("接管控制权？「" + (c.holder || "其他设备") + "」将变为只读。")) {
    ctrlAction("acquire", true);
  }
}

/* 持有控制权时每 15s 续期；45s 无心跳服务端自动释放 */
function startCtrlHeartbeat() {
  setInterval(() => {
    if (S.control && S.control.mine) {
      api("/api/control/heartbeat", { method: "POST", body: "{}" })
        .then((d) => setControl(d.control)).catch(() => {});
    }
  }, 15000);
  window.addEventListener("pagehide", () => {
    if (S.control && S.control.mine) {
      try {
        fetch("/api/control", { method: "POST", headers: authHeaders(),
                                body: JSON.stringify({ action: "release" }), keepalive: true });
      } catch (e) { /* ignore */ }
    }
  });
}

function render() {
  renderImplSelects();
  renderTaskList();
  renderRunList();
  renderSideTasks();
  renderRunDetail();
  renderCatalog();
  renderModels();
  renderBindings();
}

/* ---------------------------------------------------------- 模型接入 */
/* 顶栏右上角胶囊：显示 Codex CLI 当前绑定的供应商 / 模型 */
function renderModelPill() {
  const el = $("model-pill-text");
  if (!el) return;
  const provs = S.providers || [], bindings = S.bindings || {};
  const b = bindings["codex-cli"] || bindings["claude-code"] || {};
  const p = provs.find((x) => x.id === b.provider_id);
  if (!p) { el.textContent = "模型未绑定"; return; }
  const name = p.name.replace(/^\[[^\]]+\]\s*/, "");  // 去掉 "[CC] " 之类前缀
  const model = b.model || p.model || "";
  el.textContent = model ? name + " · " + model : name;
}

/* 批量选择的只读/可写访问器：渲染只读，点击才写，避免轮询重绘时误改状态 */
function modelSel(pid) { return (S.selModels || {})[pid] || {}; }
function modelSelSet(pid) {
  S.selModels = S.selModels || {};
  return (S.selModels[pid] = S.selModels[pid] || {});
}

function renderModels() {
  renderProvList();
  const box = $("prov-detail");
  if (!box) return;
  const sig = JSON.stringify([S.providers, S.bindings, S.selProv,
                              S.testProvState, S.testModelState,
                              S.selProvs, S.selModels]);
  if (sig === S.modelsSig) return;  // 数据没变不重绘，避免清掉正在输入的内容
  S.modelsSig = sig;
  renderModelPill();
  renderProvDetail();
}

/* ---------------------------------------------------------- CLI 绑定 */
function renderBindings() {
  const bbox = $("binding-list");
  if (!bbox) return;
  const provs = S.providers || [];
  const bindable = bindableProvs(provs);
  const nonBindable = provs.length - bindable.length;
  const targets = (S.catalog || []).filter((c) => c.installed && c.orch_kind);
  // 先建好草稿态并清理失效项，再算签名——否则首帧创建草稿态会让签名变化、白重绘一次。
  for (const c of targets) bindSelById(c.id);
  for (const id of Object.keys(S.bindSel || {})) {
    if (!targets.some((c) => c.id === id)) delete S.bindSel[id];
  }
  const sig = JSON.stringify([provs, S.bindings,
                              targets.map((c) => [c.id, c.orch_kind]), S.bindSel]);
  if (sig === S.bindSig) return;
  S.bindSig = sig;
  bbox.innerHTML = targets.map((c) => {
    const b = (S.bindings || {})[c.id] || {};
    const opts = '<option value="">不绑定（用 CLI 自身的凭据与配置）</option>' + bindable.map((p) =>
      '<option value="' + esc(p.id) + '"' + (b.provider_id === p.id ? " selected" : "") + ">" +
      esc(p.name) + "（" + esc(p.protocol) + (p.enabled === false ? " · 已停用" : "") + "）</option>").join("");
    const bound = provs.find((p) => p.id === b.provider_id);
    const offWarn = bound && bound.enabled === false
      ? '<p class="hint warn">该供应商已停用：编排时不会注入它，将回落为 CLI 默认配置（模型链也不会生效）。</p>' : "";
    const protoWarn = bound && !bindable.some((p) => p.id === bound.id)
      ? '<p class="hint warn">该供应商协议为 ' + esc(bound.protocol) +
        '，当前没有可注入的 CLI，编排时会回落为 CLI 默认配置。</p>' : "";
    return '<div class="card"><div class="head"><span class="name">' + esc(c.name) + "</span>" +
      '<span class="tag">' + esc(c.orch_kind) + "</span></div>" +
      '<div class="field"><label>供应商</label><select id="bindprov-' + esc(c.id) + '">' + opts + "</select></div>" +
      '<p class="hint">绑定后编排调用会注入该供应商的 API key 与地址；不绑定则只按下方模型链传 -m 参数。</p>' +
      bindModelBox(c, b.provider_id) + offWarn + protoWarn +
      '<div class="ops"><label class="toggle"><input type="checkbox" id="binddiff-' + esc(c.id) + '"' +
      (b.difficulty_routing ? " checked" : "") + '> 按难度自动选模型（简单/困难）</label>' +
      '<button class="ghost small" onclick="saveBinding(\'' + esc(c.id) + '\')">保存</button></div></div>';
  }).join("") +
    (!targets.length ? '<p class="hint">还没有已安装且可编排的 CLI——先到「智能体管理」页安装并启用。</p>' : "") +
    (nonBindable ? '<p class="hint">另有 ' + nonBindable +
      ' 个供应商（google 等协议）仅登记，不支持注入 CLI，未出现在上面的下拉中。</p>' : "");
}

/* ---------------------------------------------------------- 供应商主从视图 */
function renderProvList() {
  const box = $("prov-list");
  if (!box) return;
  const kw = (S.provFilter || "").trim().toLowerCase();
  const provs = (S.providers || []).filter((p) => !kw ||
    (p.name || "").toLowerCase().includes(kw) ||
    (p.protocol || "").toLowerCase().includes(kw) ||
    (p.source || "").toLowerCase().includes(kw) ||
    (p.base_url || "").toLowerCase().includes(kw));
  if (!S.selProv && provs.length) S.selProv = provs[0].id;
  if (S.selProv && !provs.some((p) => p.id === S.selProv)) {
    S.selProv = provs.length ? provs[0].id : null;
  }
  // 供应商可能已被（别处）删除，清掉失效的选择
  for (const id of Object.keys(S.selProvs || {})) {
    if (!(S.providers || []).some((p) => p.id === id)) delete S.selProvs[id];
  }
  const selN = Object.keys(S.selProvs || {}).length;
  box.classList.toggle("has-sel", selN > 0);
  const batch = selN ? '<div class="prov-batch"><span class="n">已选 ' + selN + " 个</span>" +
    '<button class="ghost small" onclick="batchProvOp(\'enable\')">启用</button>' +
    '<button class="ghost small" onclick="batchProvOp(\'disable\')">停用</button>' +
    '<button class="danger small" onclick="batchProvOp(\'delete\')">删除</button>' +
    '<button class="ghost small" onclick="clearProvSel()">取消</button></div>' : "";
  const allBox = provs.length ? '<label class="prov-all"><input type="checkbox"' +
    (selN === provs.length && selN > 0 ? " checked" : "") +
    ' onchange="toggleAllProvSel(this.checked)"> 全选' +
    (kw ? "（筛选后 " + provs.length + " 个）" : "（" + provs.length + "）") + "</label>" : "";
  box.innerHTML = batch + allBox + (provs.map((p) => {
    const n = p.models == null ? null : p.models.filter((m) => !m.hidden).length;
    const st = n == null ? "未获取" : n + " 模型";
    const off = p.enabled === false;
    return '<div class="prov-item' + (p.id === S.selProv ? " active" : "") + (off ? " off" : "") +
      '" onclick="selectProvider(\'' + esc(p.id) + '\')">' +
      '<input type="checkbox" class="pi-check"' + (S.selProvs[p.id] ? " checked" : "") +
      ' title="勾选以批量操作" onclick="event.stopPropagation()"' +
      ' onchange="toggleProvSel(\'' + esc(p.id) + '\', this.checked)">' +
      '<div class="pi-body"><div class="pi-name">' + esc(p.name) + "</div>" +
      '<div class="pi-meta"><span class="tag">' + esc(p.protocol) + "</span>" +
      (off ? '<span class="tag">已停用</span>' : "") +
      '<span class="pi-n">' + st + "</span></div></div></div>";
  }).join("") ||
    '<div class="hint" style="padding:8px">' + (kw && (S.providers || []).length
      ? "没有匹配「" + esc(S.provFilter) + "」的供应商。"
      : "暂无供应商——点上方「导入」。") + "</div>");
}

function toggleProvSel(id, on) {
  S.selProvs = S.selProvs || {};
  if (on) S.selProvs[id] = true; else delete S.selProvs[id];
  S.modelsSig = null;
  renderProvList();
}

function toggleAllProvSel(on) {
  S.selProvs = {};
  if (on) {
    const kw = (S.provFilter || "").trim().toLowerCase();
    for (const p of (S.providers || []).filter((p) => !kw ||
      (p.name || "").toLowerCase().includes(kw) ||
      (p.protocol || "").toLowerCase().includes(kw) ||
      (p.source || "").toLowerCase().includes(kw) ||
      (p.base_url || "").toLowerCase().includes(kw))) S.selProvs[p.id] = true;
  }
  S.modelsSig = null;
  renderProvList();
}

function clearProvSel() {
  S.selProvs = {};
  S.modelsSig = null;
  renderProvList();
}

async function batchProvOp(op) {
  const ids = Object.keys(S.selProvs || {});
  if (!ids.length) return;
  const tips = {
    enable: "启用所选 " + ids.length + " 个供应商？",
    disable: "停用所选 " + ids.length + " 个供应商？\n停用后其绑定会回落为 CLI 默认；配置与模型列表都保留，可随时再启用。",
    delete: "删除所选 " + ids.length + " 个供应商？\n相关 CLI 绑定会自动解绑，此操作不可撤销。",
  };
  if (!confirm(tips[op] || ("执行「" + op + "」？"))) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids, op }) });
  } catch (e) { alert("操作失败：" + e.message); }
  S.selProvs = {};
  S.modelsSig = null;
  poll();
}

function selectProvider(id) {
  if (S.selProv !== id) S.selModels = {};  // 切供应商时清空模型勾选
  S.selProv = id;
  S.modelsSig = null;
  renderProvList();
  renderProvDetail();
}

function renderProvDetail() {
  const box = $("prov-detail");
  if (!box) return;
  if (S.dragging) return;  // 拖拽中不重绘
  const p = (S.providers || []).find((x) => x.id === S.selProv);
  if (!p) {
    box.innerHTML = '<div class="empty">左侧选择供应商；还没有供应商时点左上角「导入」，' +
      '可从 CCSwitch / Codex / Claude Code / ZCode / Qwen / Gemini / OpenCode / Continue / Cursor / Trae 扫描带入。</div>';
    return;
  }
  const tp = (S.testProvState || {})[p.id];
  const tpHtml = tp ? (tp.ok
    ? '<span class="badge ok">✓ 连通 ' + tp.latency_ms + "ms · " + tp.count + " 个模型</span>"
    : '<span class="badge bad" title="' + esc(tp.error || "") + '">✗ ' + esc((tp.error || "失败").slice(0, 60)) + "</span>") : "";
  const models = (p.models || []).filter((m) => !m.hidden)
    .sort((a, b) => (a.priority || 0) - (b.priority || 0));
  const hidden = (p.models || []).filter((m) => m.hidden);
  const sel = modelSel(p.id);
  const selN = Object.keys(sel).length;
  let html = '<div class="pd-head">' +
    '<div class="pd-title"><span class="pd-name">' + esc(p.name) + "</span>" +
    '<span class="tag">' + esc(p.protocol) + "</span>" +
    (p.enabled === false ? '<span class="tag">已停用</span>' : "") +
    srcTag(p.source) + tpHtml + "</div>" +
    '<div class="pd-url" title="' + esc(p.base_url || "") + '">' + esc(p.base_url || "") + "</div>" +
    '<div class="pd-ops">' +
    '<button class="primary small" onclick="testProv(\'' + esc(p.id) + '\')">测试连接</button>' +
    '<button class="ghost small" onclick="refreshProviderModels(\'' + esc(p.id) + '\')">获取模型列表</button>' +
    '<button class="ghost small" onclick="toggleProviderEnabled(\'' + esc(p.id) + '\', ' +
    (p.enabled === false) + ')">' + (p.enabled === false ? "启用供应商" : "停用供应商") + "</button>" +
    '<span class="pd-ops-gap"></span>' +
    '<button class="danger small" onclick="delProvider(\'' + esc(p.id) + '\')">删除</button>' +
    "</div></div>";
  if (selN) {
    html += '<div class="mrow-batch"><span class="n">已选 ' + selN + " 个模型</span>" +
      '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'enable\')">启用</button>' +
      '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'disable\')">停用</button>' +
      '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'restore\')">恢复</button>' +
      '<button class="danger small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'delete\')">删除</button>' +
      '<button class="ghost small" onclick="clearModelSel(\'' + esc(p.id) + '\')">取消</button></div>';
  }
  html += '<details class="pd-config"><summary>编辑供应商配置（地址 / 密钥 / 难度模型）</summary>' +
    providerCard(p) + "</details>";
  if (p.models == null) {
    html += '<div class="empty">尚未获取模型列表——点上方「获取模型列表」。</div>';
  } else {
    const kw = (S.modelFilter || {})[p.id] || "";
    html += '<div class="pm-tools">' +
      '<input id="pm-search-' + esc(p.id) + '" type="search" placeholder="按名称过滤模型…" autocomplete="off"' +
      ' value="' + esc(kw) + '" oninput="filterModels(\'' + esc(p.id) + '\', this.value)">' +
      '<span class="pm-count" id="pm-count-' + esc(p.id) + '"></span></div>';
    html += '<div id="pm-groups" class="pm-groups' + (selN ? " has-sel" : "") + '">' +
      provModelGroupsHtml(p) + "</div>";
    if (hidden.length) {
      html += '<details class="prow-hidden"><summary>已删除 ' + hidden.length +
        " 个模型（刷新不会再带回）</summary><div class=\"prow-hidden-list\">";
      for (const m of hidden) {
        html += '<label><input type="checkbox"' + (sel[m.name] ? " checked" : "") +
          ' onchange="toggleModelSel(\'' + esc(p.id) + '\', \'' + esc(m.name) + '\', this.checked)">' +
          esc(m.name) + "</label>";
      }
      html += '</div><div class="prow-hidden-ops">' +
        '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'restore\')">恢复所选</button>' +
        '<button class="ghost small" onclick="restoreHidden(\'' + esc(p.id) + '\')">恢复全部</button>' +
        "</div></details>";
    }
  }
  box.innerHTML = html;
  updateModelCount(p.id);
  bindModelRowDnD(p.id, box);
}

/* 模型分组区 HTML（全量重绘与过滤重绘共用；过滤时暂停拖拽排序） */
function provModelGroupsHtml(p) {
  const kw = ((S.modelFilter || {})[p.id] || "").trim().toLowerCase();
  const models = (p.models || []).filter((m) => !m.hidden)
    .filter((m) => !kw || (m.name || "").toLowerCase().includes(kw))
    .sort((a, b) => (a.priority || 0) - (b.priority || 0));
  const sel = modelSel(p.id);
  const groups = {};
  for (const m of models) {
    const g = m.protocol || p.protocol || "其他";
    (groups[g] = groups[g] || []).push(m);
  }
  const gnames = Object.keys(groups).sort();
  if (!gnames.length) {
    return '<div class="empty">' + (kw
      ? "没有匹配「" + esc(kw) + "」的模型。"
      : "该供应商没有可用模型（或全部被停用）。") + "</div>";
  }
  let html = "";
  for (const g of gnames) {
    const gm = groups[g];
    const allSel = gm.every((m) => sel[m.name]);
    html += '<div class="pgroup"><div class="pgroup-title">' +
      '<label class="pcheck-all"><input type="checkbox"' + (allSel ? " checked" : "") +
      ' onchange="toggleGroupSel(\'' + esc(p.id) + '\', \'' + esc(g) + '\', this.checked)">全选</label>' +
      esc(g) + " 协议 · " + gm.length + " 个" +
      (kw ? "（过滤中，拖拽排序暂停）" : "（拖动 ☰ 调序；启用的排最前）") + "</div>";
    for (const m of gm) html += provModelRow(p.id, m, g);
    html += "</div>";
  }
  return html;
}

/* 搜索框输入：只重绘分组区，避免输入框失焦 */
function filterModels(pid, val) {
  S.modelFilter = S.modelFilter || {};
  S.modelFilter[pid] = val;
  const p = (S.providers || []).find((x) => x.id === pid);
  const gbox = $("pm-groups");
  if (p && gbox) {
    gbox.innerHTML = provModelGroupsHtml(p);
    bindModelRowDnD(pid, gbox);
  }
  updateModelCount(pid);
}

function updateModelCount(pid) {
  const el = $("pm-count-" + pid);
  const p = (S.providers || []).find((x) => x.id === pid);
  if (!el || !p || !p.models) return;
  const kw = (S.modelFilter || {})[pid] || "";
  const total = p.models.filter((m) => !m.hidden).length;
  const shown = total && kw.trim()
    ? p.models.filter((m) => !m.hidden &&
        (m.name || "").toLowerCase().includes(kw.trim().toLowerCase())).length
    : total;
  el.textContent = kw.trim() ? (shown + " / " + total + " 个模型") : (total + " 个模型");
}

function bindModelRowDnD(pid, root) {
  root.querySelectorAll(".prow").forEach((row) => {
    row.addEventListener("dragstart", (e) => {
      S.dragging = { group: row.dataset.group, name: row.dataset.name };
      row.classList.add("dragging");
      if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    });
    row.addEventListener("dragend", () => {
      S.dragging = false;
      row.classList.remove("dragging");
      root.querySelectorAll(".prow").forEach((r) => r.classList.remove("drag-over"));
    });
    row.addEventListener("dragover", (e) => {
      if (!S.dragging || S.dragging.group !== row.dataset.group) return;
      e.preventDefault();
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!S.dragging || S.dragging.group !== row.dataset.group) return;
      moveModelInGroup(pid, row.dataset.group, S.dragging.name, row.dataset.name);
    });
  });
}

function provModelRow(pid, m, group) {
  const key = pid + "|" + m.name;
  const tm = (S.testModelState || {})[key];
  const tmHtml = tm ? (tm.ok
    ? '<span class="badge ok">✓ ' + tm.latency_ms + "ms</span>"
    : '<span class="badge bad" title="' + esc(tm.error || "") + '">✗ ' +
      esc((tm.error || "").slice(0, 24)) + "</span>") : "";
  const price = (m.price_in != null && m.price_out != null)
    ? '<span class="hint">¥' + esc(m.price_in) + "/¥" + esc(m.price_out) + "</span>" : "";
  const sel = modelSel(pid);
  return '<div class="prow' + (m.enabled ? "" : " off") + '" draggable="true" data-group="' +
    esc(group) + '" data-name="' + esc(m.name) + '">' +
    '<input type="checkbox" class="pcheck"' + (sel[m.name] ? " checked" : "") +
    ' title="勾选以批量操作"' +
    ' onchange="toggleModelSel(\'' + esc(pid) + '\', \'' + esc(m.name) + '\', this.checked)">' +
    '<span class="drag" title="拖动调整优先级">☰</span>' +
    '<span class="pprio">#' + m.priority + "</span>" +
    '<span class="pname" title="' + esc(m.name) + '">' + esc(m.name) + "</span>" +
    price + tmHtml +
    '<span class="row-ops">' +
    '<button class="ghost small row-op" onclick="testModelBtn(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">测试</button>' +
    '<button class="danger small row-op" title="从列表删除：刷新/重新导入不会再带回，可在分组底部恢复"' +
    ' onclick="delModel(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">删除</button>' +
    '<label class="tog-mini" title="' + (m.enabled ? "停用（不影响配置，仅编排选模跳过）" : "启用") + '">' +
    '<input type="checkbox" ' + (m.enabled ? "checked" : "") +
    ' onchange="modelOp(\'' + esc(pid) + '\', \'' + esc(m.name) + '\', this.checked ? \'enable\' : \'disable\')"><i></i></label>' +
    "</span></div>";
}

function moveModelInGroup(pid, group, fromName, toName) {
  const p = (S.providers || []).find((x) => x.id === pid);
  if (!p || !p.models) return;
  // 组内顺序调整后，按分组展示顺序重排该供应商全部模型的优先级
  const groups = {};
  for (const m of (p.models || []).filter((x) => !x.hidden)
      .sort((a, b) => (a.priority || 0) - (b.priority || 0))) {
    const g = m.protocol || p.protocol || "其他";
    (groups[g] = groups[g] || []).push(m.name);
  }
  const arr = groups[group] || [];
  const i = arr.indexOf(fromName), j = arr.indexOf(toName);
  if (i < 0 || j < 0) return;
  arr.splice(j, 0, arr.splice(i, 1)[0]);
  groups[group] = arr;
  const names = Object.keys(groups).sort().reduce((acc, g) => acc.concat(groups[g]), []);
  api("/api/models/reorder", { method: "POST",
    body: JSON.stringify({ provider_id: pid, names }) })
    .then(poll).catch((e) => alert("排序失败：" + e.message));
}

async function testProv(pid) {
  S.testProvState = S.testProvState || {};
  S.testProvState[pid] = { ok: false, error: "测试中…" };
  renderProvDetail();
  try {
    S.testProvState[pid] = await api("/api/models/test-provider",
      { method: "POST", body: JSON.stringify({ id: pid }) });
  } catch (e) { S.testProvState[pid] = { ok: false, error: e.message }; }
  S.modelsSig = null;
  renderModels();
}

async function testModelBtn(pid, name) {
  S.testModelState = S.testModelState || {};
  const key = pid + "|" + name;
  S.testModelState[key] = { ok: false, error: "测试中…" };
  renderProvDetail();
  try {
    S.testModelState[key] = await api("/api/models/test-model",
      { method: "POST", body: JSON.stringify({ provider_id: pid, name }) });
  } catch (e) { S.testModelState[key] = { ok: false, error: e.message }; }
  S.modelsSig = null;
  renderModels();
}

async function modelOp(pid, name, op) {
  try {
    await api("/api/models/model-op", { method: "POST",
      body: JSON.stringify({ provider_id: pid, name, op }) });
    const s = modelSel(pid);            // 单行操作后同步清掉该行勾选
    if (s[name]) { delete modelSelSet(pid)[name]; }
  } catch (e) { alert("操作失败：" + e.message); }
  S.modelsSig = null;
  poll();
}

/* ---- 模型批量选择 ---- */
function toggleModelSel(pid, name, on) {
  const s = modelSelSet(pid);
  if (on) s[name] = true; else delete s[name];
  S.modelsSig = null;
  renderProvDetail();
  renderProvList();
}

function toggleGroupSel(pid, group, on) {
  const p = (S.providers || []).find((x) => x.id === pid);
  if (!p) return;
  const kw = ((S.modelFilter || {})[pid] || "").trim().toLowerCase();
  const s = modelSelSet(pid);
  for (const m of p.models || []) {
    if (m.hidden) continue;
    if ((m.protocol || p.protocol || "其他") !== group) continue;
    if (kw && !(m.name || "").toLowerCase().includes(kw)) continue;  // 过滤时只作用于可见行
    if (on) s[m.name] = true; else delete s[m.name];
  }
  S.modelsSig = null;
  renderProvDetail();
}

function clearModelSel(pid) {
  if (S.selModels) delete S.selModels[pid];
  S.modelsSig = null;
  renderProvDetail();
  renderProvList();
}

async function batchModelOp(pid, op) {
  const names = Object.keys(modelSel(pid));
  if (!names.length) return;
  const tips = {
    enable: "启用所选 " + names.length + " 个模型？",
    disable: "停用所选 " + names.length + " 个模型？\n停用只影响编排选模，不删除配置。",
    delete: "删除所选 " + names.length + " 个模型？\n刷新 / 重新导入都不会再带回，可在「已删除」里恢复。",
    restore: "恢复所选 " + names.length + " 个模型？\n它们会重新启用并自动置顶。",
  };
  if (!confirm(tips[op] || ("执行「" + op + "」？"))) return;
  try {
    await api("/api/models/model-op", { method: "POST",
      body: JSON.stringify({ provider_id: pid, names, op }) });
  } catch (e) { alert("操作失败：" + e.message); }
  clearModelSel(pid);
  poll();
}

function delModel(pid, name) {
  if (!confirm("删除模型「" + name + "」？\n刷新 / 重新导入模型列表都不会再带回，可在分组底部「恢复全部」找回。")) return;
  modelOp(pid, name, "delete");
}

async function toggleProviderEnabled(pid, enabled) {
  const p = (S.providers || []).find((x) => x.id === pid) || {};
  const off = p.enabled === false;
  if (!off && !confirm("停用供应商「" + (p.name || pid) +
      "」？\n停用后它的绑定会回落为 CLI 默认；配置与模型列表保留，可随时再启用。")) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [pid], op: off ? "enable" : "disable" }) });
  } catch (e) { alert("操作失败：" + e.message); }
  S.modelsSig = null;
  poll();
}

function restoreHidden(pid) {
  if (!confirm("恢复该供应商下全部已删除的模型？\n它们会回到优先级末尾。")) return;
  modelOp(pid, "", "restore-all");
}

async function refreshAllModels() {
  const btn = $("btn-refresh-models");
  btn.disabled = true; btn.textContent = "…"; btn.title = "正在刷新全部模型列表…";
  try {
    const r = await api("/api/models/refresh-all", { method: "POST" });
    btn.title = r.message || "正在刷新…";
  } catch (e) { alert("刷新失败：" + e.message); }
  setTimeout(() => { btn.disabled = false; btn.textContent = "⟳"; btn.title = "全部重新拉取模型列表"; }, 1500);
  setTimeout(poll, 2500); setTimeout(poll, 8000);
}

async function refreshProviderModels(id) {
  try {
    const r = await api("/api/models/refresh", { method: "POST", body: JSON.stringify({ id }) });
    if (!r.ok && r.message) alert("获取失败：" + r.message);
  } catch (e) { alert("获取失败：" + e.message); }
  poll();
}

function providerCard(p) {
  const fld = (id, label, val, ph, full) =>
    '<div class="field' + (full ? " full" : "") + '"><label>' + label + '</label>' +
    '<input id="' + id + '" value="' + esc(val) + '" placeholder="' + esc(ph) + '"></div>';
  return '<div class="card" id="pcard-' + esc(p.id) + '">' +
    '<div class="head"><span class="name">' + esc(p.name) + '</span><span class="tag">' + esc(p.protocol) + "</span>" +
    srcTag(p.source) + "</div>" +
    '<div class="fields grid">' +
    fld("pname-" + p.id, "名称", p.name, "名称") +
    fld("pmodel-" + p.id, "默认模型", p.model || "", "默认模型") +
    fld("purl-" + p.id, "API 地址", p.base_url, "base_url", true) +
    fld("pkey-" + p.id, "密钥", "", "（" + (p.api_key || "未设置") + "，留空=不改）", true) +
    fld("peasy-" + p.id, "简单任务模型", p.model_easy || "", "难度路由 · 简单") +
    fld("phard-" + p.id, "困难任务模型", p.model_hard || "", "难度路由 · 困难") +
    "</div>" +
    '<div class="ops"><button class="ghost small" onclick="saveProvider(\'' + esc(p.id) + '\')">保存</button>' +
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
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [id], op: "delete" }) });
  } catch (e) { alert("操作失败：" + e.message); }
  if (S.selModels) delete S.selModels[id];
  if (S.selProvs) delete S.selProvs[id];
  S.modelsSig = null;
  poll();
}

/* 「＋」手动添加供应商：弹框表单（替代原来的多段 prompt） */
function openAddProviderDialog() {
  const protoOpts =
    '<option value="anthropic">anthropic（Claude 系）</option>' +
    '<option value="openai">openai（Codex / 通用）</option>' +
    '<option value="google">google（Gemini，仅登记不支持注入）</option>';
  openModal("添加供应商",
    '<div class="form">' +
    '<div class="grid-2">' +
    '<div class="field"><label>名称 *</label><input id="np-name" placeholder="例：公司网关"></div>' +
    '<div class="field"><label>协议 *</label><select id="np-proto">' + protoOpts + "</select></div>" +
    "</div>" +
    '<div class="field"><label>API 地址 *</label>' +
    '<input id="np-url" placeholder="https://host/v1（若填 /chat/completions 会自动收敛为基址）"></div>' +
    '<div class="field"><label>API 密钥</label>' +
    '<input id="np-key" placeholder="sk-...（可留空，稍后补填）"></div>' +
    '<div class="grid-3">' +
    '<div class="field"><label>默认模型</label><input id="np-model" placeholder="可留空"></div>' +
    '<div class="field"><label>简单任务模型</label><input id="np-easy" placeholder="可留空"></div>' +
    '<div class="field"><label>困难任务模型</label><input id="np-hard" placeholder="可留空"></div>' +
    "</div>" +
    '<p class="hint">保存后会自动拉取该供应商的模型列表（未填密钥时跳过）。</p>' +
    '<div id="add-result" class="msg"></div>' +
    "</div>",
    '<button class="ghost" onclick="closeModal()">取消</button>' +
    '<span class="spacer"></span>' +
    '<button class="primary" id="btn-do-add" onclick="doAddProvider()">保存</button>');
  setTimeout(() => { const el = $("np-name"); if (el) el.focus(); }, 0);
}

async function doAddProvider() {
  const res = $("add-result");
  const body = {
    name: $("np-name").value.trim(),
    protocol: $("np-proto").value,
    base_url: $("np-url").value.trim(),
    api_key: $("np-key").value.trim(),
    model: $("np-model").value.trim(),
    model_easy: $("np-easy").value.trim(),
    model_hard: $("np-hard").value.trim(),
  };
  if (!body.name) { res.textContent = "请填写名称"; return; }
  if (!/^https?:\/\//.test(body.base_url)) { res.textContent = "API 地址必须以 http:// 或 https:// 开头"; return; }
  const btn = $("btn-do-add");
  btn.disabled = true; btn.textContent = "保存中…";
  try {
    await api("/api/models/provider", { method: "POST", body: JSON.stringify(body) });
    const r = await api("/api/models");
    const p = (r.providers || []).find((x) => x.name === body.name);
    S.selProv = p ? p.id : null;
    S.modelsSig = null;
    closeModal();
    poll();
    if (p && body.api_key) refreshProviderModels(p.id);  // 有密钥才自动拉模型列表
  } catch (e) {
    res.textContent = "保存失败：" + e.message;
    btn.disabled = false; btn.textContent = "保存";
  }
}

/* 「导入」：扫描本机各 AI 工具配置，勾选后可一次导入 */
async function openImportDialog() {
  openModal("导入供应商", '<div class="hint">正在扫描本机 AI 工具配置…</div>',
    '<button class="ghost" onclick="closeModal()">取消</button>');
  let data;
  try {
    data = await api("/api/models/sources");
  } catch (e) {
    $("modal-body").innerHTML = '<div class="msg bad">扫描失败：' + esc(e.message) + "</div>";
    return;
  }
  const srcs = data.sources || [];
  const usable = srcs.filter((s) => s.found && s.count > 0);
  const rows = srcs.map((s) => {
    const ok = s.found && s.count > 0;
    let status;
    if (!s.found) status = '<span class="badge">未找到</span>';
    else if (s.error) status = '<span class="badge bad">解析失败</span>';
    else if (s.count > 0) status = '<span class="badge ok">发现 ' + s.count + " 个</span>";
    else status = '<span class="badge">无可用配置</span>';
    return '<label class="src-row' + (ok ? "" : " disabled") + '">' +
      '<input type="checkbox" value="' + esc(s.id) + '"' + (ok ? " checked" : " disabled") +
      (ok ? ' onchange="updateImportSelHint()"' : "") + ">" +
      '<div class="src-main">' +
      '<div class="src-name">' + esc(s.name) + status + "</div>" +
      '<div class="src-desc">' + esc(s.desc) + "</div>" +
      '<div class="src-path">' + esc((s.paths || []).join("  ·  ")) + "</div>" +
      (s.note ? '<div class="src-note">' + esc(s.note) + "</div>" : "") +
      (s.error ? '<div class="src-note bad">' + esc(s.error) + "</div>" : "") +
      "</div></label>";
  }).join("");
  $("modal-body").innerHTML =
    '<p class="hint">勾选要导入的来源。导入只读取这些工具的配置，不会改动它们本身；' +
    "已导入过的供应商会原地更新（保留你设置的模型与启停状态）。</p>" +
    '<div class="src-list">' + (rows || '<div class="hint">没有可扫描的来源。</div>') + "</div>" +
    '<div id="import-result" class="import-result"></div>';
  $("modal-foot").innerHTML =
    '<span class="hint" id="import-sel-hint"></span>' +
    '<span class="spacer"></span>' +
    '<button class="ghost" onclick="toggleAllSources(true)">全选</button>' +
    '<button class="ghost" onclick="toggleAllSources(false)">全不选</button>' +
    '<button class="ghost" onclick="closeModal()">取消</button>' +
    '<button class="primary" id="btn-do-import" onclick="doImport()"' +
    (usable.length ? "" : " disabled") + ">导入选中</button>";
  updateImportSelHint();
}

function importSelBoxes() {
  return Array.from(document.querySelectorAll("#modal-body .src-row input[type=checkbox]:not(:disabled)"));
}

function updateImportSelHint() {
  const el = $("import-sel-hint");
  if (!el) return;
  const boxes = importSelBoxes();
  const n = boxes.filter((b) => b.checked).length;
  el.textContent = "已选 " + n + " / " + boxes.length + " 个来源";
  const btn = $("btn-do-import");
  if (btn && btn.textContent === "导入选中") btn.disabled = !n;
}

function toggleAllSources(on) {
  importSelBoxes().forEach((b) => { b.checked = on; });
  updateImportSelHint();
}

async function doImport() {
  const ids = importSelBoxes().filter((b) => b.checked).map((b) => b.value);
  if (!ids.length) { alert("请至少选择一个来源。"); return; }
  const btn = $("btn-do-import");
  const boxes = importSelBoxes();
  boxes.forEach((b) => { b.disabled = true; });
  btn.disabled = true; btn.textContent = "导入中…";
  try {
    const r = await api("/api/models/import", { method: "POST", body: JSON.stringify({ sources: ids }) });
    const lines = (r.sources || []).map((s) => {
      if (!s.found) return "<li>" + esc(s.name) + "：未找到配置</li>";
      if (s.error) return "<li>" + esc(s.name) + '：<span class="bad">' + esc(s.error) + "</span></li>";
      let txt = "新增 " + s.added + "，更新 " + s.updated;
      if (s.duplicate) txt += "，跳过重复 " + s.duplicate;
      const extra = s.note ? "（" + esc(s.note) + "）" : "";
      return "<li>" + esc(s.name) + "：" + txt + extra + "</li>";
    }).join("");
    $("import-result").innerHTML =
      '<div class="import-done"><b>' + esc(r.message) + "</b><ul>" + lines + "</ul></div>";
    btn.textContent = "完成";
    btn.disabled = false;
    btn.onclick = closeModal;
    if (r.imported) { S.selProv = null; S.modelsSig = null; poll(); }
  } catch (e) {
    $("import-result").innerHTML = '<div class="msg bad">导入失败：' + esc(e.message) + "</div>";
    btn.disabled = false; btn.textContent = "导入选中";
    boxes.forEach((b) => { b.disabled = false; });
    updateImportSelHint();
  }
}

async function saveBinding(id) {
  const st = bindSelById(id);
  try {
    await api("/api/models/binding", { method: "POST", body: JSON.stringify({
      agent_id: id, provider_id: $("bindprov-" + id).value,
      chain: st.chain.map((c) => ({ provider_id: c.p, model: c.m })),
      difficulty_routing: $("binddiff-" + id).checked }) });
    st.dirty = false;
    st.key = chainKey(st.chain);
  } catch (e) {
    alert("保存失败：" + e.message);
    return;
  }
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
  if (flowById($("f-type").value)?.engine === "review") {
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
  if (flowById(payload.type)?.engine === "code") {
    payload.verify_command = $("f-verify").value.trim();
  } else {
    payload.manuscript = $("f-manuscript").value.trim() || "manuscript.md";
    payload.rounds = parseInt($("f-rounds").value, 10) || 2;
    payload.threshold = parseFloat($("f-threshold").value) || 7.0;
    const rubric = $("f-rubric").value.trim();
    if (rubric) payload.rubric = rubric.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
    const ch = parseInt($("f-chapters").value, 10);
    if (ch >= 2) payload.serial = {
      chapters: ch,
      words_per_chapter: parseInt($("f-words-per-ch").value, 10) || 2500,
    };
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
    '<div class="item" data-task-id="' + esc(t.id) + '"><div class="t"><span class="name">' + esc(t.title) + "</span>" +
    '<span class="tag">' + esc((flowById(t.type) || {}).name || t.type) + "</span>" +
    (t.archived ? '<span class="tag">已归档</span>' : "") +
    '<span class="time">' + esc(t.created_at) + "</span>" +
    (t.archived
      ? '<button class="ghost small" onclick="archiveTask(\'' + esc(t.id) + '\', false)">取消归档</button>'
      : '<button class="ghost small" onclick="archiveTask(\'' + esc(t.id) + '\', true)">归档</button>') +
    '<button class="danger small" onclick="deleteTask(\'' + esc(t.id) + '\')">删除</button></div>' +
    '<div class="desc">' + esc(t.goal) + "</div></div>";
  let html = tasks.length ? tasks.map((t) => row(t)).join("") : '<div class="empty">暂无任务——在上面输入一句话目标开始。</div>';
  if (S.showArchived) {
    html += '<h3 class="sec-title">已归档</h3>' + (archived.length ? archived.map((t) => row(t)).join("") : '<div class="empty">没有已归档任务</div>');
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

/* ---------------------------------------------------------- 右键菜单（归档/删除） */
function archivedTaskIds() {
  return new Set((((S.state || {}).archived_tasks) || []).map((t) => t.id));
}

let ctxItems = [];

function openCtxMenu(x, y, items) {
  const menu = $("ctx-menu");
  if (!menu) return;
  ctxItems = items;
  menu.innerHTML = items.map((it, i) => it === "-"
    ? '<div class="ctx-sep"></div>'
    : '<div class="ctx-item' + (it.danger ? " danger" : "") + '" data-i="' + i + '">' + esc(it.label) + "</div>").join("");
  menu.classList.remove("hidden");
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 8)) + "px";
  menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 8)) + "px";
  menu.onclick = (e) => {
    const el = e.target.closest(".ctx-item");
    if (!el) return;
    const it = ctxItems[+el.dataset.i];
    closeCtxMenu();
    if (it && it.fn) it.fn();
  };
}

function closeCtxMenu() {
  const menu = $("ctx-menu");
  if (menu && !menu.classList.contains("hidden")) {
    menu.dataset.closedAt = String(Date.now());  // 记录关闭时间，供“点按钮开→再点关”的切换判断
    menu.classList.add("hidden");
  }
}

function bindCtxMenus() {
  // 侧栏任务树：右键任务 → 详情/归档/删除；管理类运行 → 详情/删除记录
  $("side-tasks").addEventListener("contextmenu", (e) => {
    const det = e.target.closest("details.stask");
    if (!det) return;
    e.preventDefault();
    const taskId = det.dataset.task || "", runId = det.dataset.run || "";
    const items = [];
    if (runId) items.push({ label: "打开详情", fn: () => sideOpenRun(runId) });
    if (taskId) {
      items.push("-");
      const st = det.dataset.status || "";
      if (st === "failed" || st === "cancelled") items.push({ label: "↻ 重试任务", fn: () => retryTask(taskId) });
      items.push({ label: archivedTaskIds().has(taskId) ? "取消归档" : "归档", fn: () => archiveTask(taskId, !archivedTaskIds().has(taskId)) });
      items.push({ label: "删除任务", danger: true, fn: () => deleteTask(taskId) });
    } else if (runId) {
      items.push("-");
      items.push({ label: "删除记录", danger: true, fn: () => deleteRun(runId) });
    }
    openCtxMenu(e.clientX, e.clientY, items);
  });
  // 最近任务列表：右键 → 归档/删除
  $("task-list").addEventListener("contextmenu", (e) => {
    const item = e.target.closest(".item");
    if (!item || !item.dataset.taskId) return;
    e.preventDefault();
    const id = item.dataset.taskId;
    const isArch = archivedTaskIds().has(id);
    openCtxMenu(e.clientX, e.clientY, [
      { label: isArch ? "取消归档" : "归档", fn: () => archiveTask(id, !isArch) },
      { label: "删除任务", danger: true, fn: () => deleteTask(id) },
    ]);
  });
  document.addEventListener("click", closeCtxMenu, true);
  window.addEventListener("blur", closeCtxMenu);
  window.addEventListener("scroll", closeCtxMenu, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtxMenu(); });
}

async function retryTask(id) {
  try {
    const r = await api("/api/tasks/" + encodeURIComponent(id) + "/retry", { method: "POST" });
    switchTab("runs");
    openRun(r.run_id);
  } catch (e) { alert("重试失败：" + e.message); return; }
  poll();
}

/* ---------------------------------------------------------- 运行列表 */
/* 运行中/排队中的记录不可删除，勾选框置灰 */
function runDeletable(r) { return r.status !== "queued" && r.status !== "running"; }

function renderRunList() {
  if (S.detailRunId) return;
  const box = $("run-list");
  if (!box) return;
  const runs = ((S.state && S.state.runs) || []).slice(0, 30);
  const pickable = new Set(runs.filter(runDeletable).map((r) => r.id));
  // 记录被删/转为活跃后清掉残留勾选，避免下次批量误删
  S.selRuns = S.selRuns || {};
  for (const id of Object.keys(S.selRuns)) if (!pickable.has(id)) delete S.selRuns[id];
  const selN = Object.keys(S.selRuns).length;
  const clr = $("btn-clear-runs");
  if (clr) clr.disabled = !runs.length;
  if (!runs.length) { box.innerHTML = '<div class="empty">暂无运行记录</div>'; return; }
  const tools = '<div class="run-tools">' +
    '<label class="toggle"><input type="checkbox"' +
    (pickable.size && selN === pickable.size ? " checked" : "") +
    ' onchange="toggleAllRunSel(this.checked)"> 全选</label>' +
    '<span class="n">' + (selN ? "已选 " + selN + " 条" : "勾选可批量删除") + "</span>" +
    (selN ? '<button class="danger small" onclick="deleteSelectedRuns()">删除所选</button>' +
            '<button class="ghost small" onclick="clearRunSel()">取消选择</button>' : "") +
    "</div>";
  box.innerHTML = tools + runs.map((r) => {
    const can = runDeletable(r);
    return '<div class="item" onclick="openRun(\'' + esc(r.id) + '\')">' +
      '<div class="t">' +
      '<input type="checkbox" class="rcheck"' + (S.selRuns[r.id] ? " checked" : "") + (can ? "" : " disabled") +
      ' title="' + (can ? "勾选以批量删除" : "运行中的记录不可删除，请先取消") + '"' +
      ' onclick="event.stopPropagation()" onchange="toggleRunSel(\'' + esc(r.id) + '\', this.checked)">' +
      runKindTag(r.kind) +
      '<span class="name">' + esc(r.title) + "</span>" + statusChip(r.status) +
      '<span class="time">' + esc(r.created_at) + "</span>" +
      '<button class="danger small" title="删除该记录" onclick="event.stopPropagation(); deleteRun(\'' + esc(r.id) + '\')">删除</button></div>' +
      '<div class="desc">' + esc(r.summary || r.error || (r.steps ? r.steps.length + " 个步骤" : "")) + "</div></div>";
  }).join("");
}

/* 侧栏「任务」树：任务 → 各 CLI 步骤，点击步骤在右侧打开运行详情 */
function renderSideTasks() {
  const box = $("side-tasks");
  if (!box) return;
  const runs = ((S.state && S.state.runs) || []).slice(0, 40);
  const archivedIds = archivedTaskIds();
  const sig = JSON.stringify([runs.map((r) => [r.id, r.status, (r.steps || []).length]), S.detailRunId, archivedIds.size]);
  if (sig === S.sideSig && box.children.length) return;
  S.sideSig = sig;
  const openKeys = new Set(Array.from(box.querySelectorAll("details.stask[open]")).map((d) => d.dataset.key));
  const groups = [], byKey = {};
  for (const r of runs) {
    if (r.task_id && archivedIds.has(r.task_id)) continue;  // 已归档任务不上侧栏
    const key = r.task_id || r.id;
    if (!byKey[key]) {
      byKey[key] = { key, taskId: r.task_id || "", title: r.title || r.id, status: r.status, active: false, runIds: [], steps: [] };
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
    return '<details class="stask" data-key="' + esc(g.key) + '" data-task="' + esc(g.taskId || "") + '" data-run="' + esc(g.runIds[0] || "") + '" data-status="' + esc(g.status || "") + '"' + (isOpen ? " open" : "") + "><summary>" +
      '<span class="dot ' + esc(g.status) + '"></span><span class="t">' + esc(g.title) + "</span></summary>" + items + more + empty + "</details>";
  }).join("") : '<div class="side-empty">暂无任务</div>';
}

window.sideOpenRun = function (id) { switchTab("runs"); openRun(id); };
window.openRunInRuns = function (id) { switchTab("runs"); openRun(id); };

async function deleteRun(id) {
  if (!confirm("删除该运行记录（含全部日志与报告）？不可恢复。")) return;
  try {
    await api("/api/runs/" + encodeURIComponent(id) + "/delete", { method: "POST" });
  } catch (e) { alert("删除失败：" + e.message); return; }
  if (S.selRuns) delete S.selRuns[id];
  if (S.detailRunId === id) closeRun();
  poll();
}

/* 运行记录批量删除 / 一键全部清除 */
function toggleRunSel(id, on) {
  S.selRuns = S.selRuns || {};
  if (on) S.selRuns[id] = true; else delete S.selRuns[id];
  renderRunList();
}

function toggleAllRunSel(on) {
  S.selRuns = {};
  if (on) {
    const runs = ((S.state && S.state.runs) || []).slice(0, 30);
    for (const r of runs) if (runDeletable(r)) S.selRuns[r.id] = true;
  }
  renderRunList();
}

function clearRunSel() { S.selRuns = {}; renderRunList(); }

async function deleteSelectedRuns() {
  const ids = Object.keys(S.selRuns || {});
  if (!ids.length) return;
  if (!confirm("删除所选 " + ids.length + " 条运行记录（含全部日志与报告）？不可恢复。")) return;
  let r;
  try {
    r = await api("/api/runs/delete", { method: "POST", body: JSON.stringify({ ids }) });
  } catch (e) { alert("批量删除失败：" + e.message); return; }
  S.selRuns = {};
  if (S.detailRunId && ids.indexOf(S.detailRunId) >= 0) closeRun();
  if (r && r.message) alert(r.message);
  poll();
}

async function clearRuns() {
  if (!confirm("清除全部运行记录（含日志与报告）？运行中的记录会保留，需先取消。不可恢复。")) return;
  let r;
  try {
    r = await api("/api/runs/clear", { method: "POST" });
  } catch (e) { alert("清除失败：" + e.message); return; }
  S.selRuns = {};
  if (S.detailRunId) closeRun();
  if (r && r.skipped) alert("已清除 " + r.count + " 条；另有 " + r.skipped + " 条运行中的记录已保留（请先取消再清除）。");
  poll();
}

async function openRun(id) {
  S.detailRunId = id;
  document.querySelector("#sub-runs .panel:first-child").classList.add("hidden");
  $("run-detail").classList.remove("hidden");
  renderRunDetail();
}

function closeRun() {
  S.detailRunId = null;
  $("run-detail").classList.add("hidden");
  document.querySelector("#sub-runs .panel:first-child").classList.remove("hidden");
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
  $("btn-retry").classList.toggle("hidden", !(run.task_id && (run.status === "failed" || run.status === "cancelled")));
  S.lastRun = run;
  $("rd-meta").innerHTML =
    '<span class="stat">创建 <b>' + esc(run.created_at) + "</b></span>" +
    '<span class="stat">成本 <b>$' + Number(run.cost_usd || 0).toFixed(3) + "</b></span>" +
    '<span class="stat">tokens <b>' + (run.tokens || 0) + "</b></span>" +
    (run.mode ? '<span class="stat">模式 <b>' + (run.mode === "auto" ? "智能" : "手动") + "</b></span>" : "") +
    (run.error ? '<span class="stat err">' + esc(run.error.slice(0, 200)) + "</span>" : "");
  renderPlan(run);
  $("rd-steps").innerHTML = (run.steps || []).map((s) =>
    '<div class="step" onclick="toggleLog(\'' + esc(run.id) + "', '" + esc(s.log) + '\')" title="' + esc(s.note || "") + '">' +
    '<span class="n">' + String(s.n).padStart(2, "0") + "</span>" +
    '<span class="role">' + esc(s.role) + "</span>" +
    '<span class="who">' + esc(s.agent_label || s.agent) + "</span>" +
    '<span class="sum">' + esc((s.note ? "◆ " + s.note + " — " : "") + (s.summary || "")) + "</span>" +
    '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span>" +
    statusChip(s.status) + "</div>"
  ).join("") || '<div class="empty">尚无步骤</div>';
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
    ? '<div class="route">路由依据：' +
      Object.keys(route).map((k) => "<b>" + esc(k) + "</b> " + esc(route[k])).join("　|　") + "</div>"
    : "";
  box.classList.remove("hidden");
  box.innerHTML = '<h3 class="sec-title">编排计划 <span class="tag">来源 ' + esc(plan.source || "?") + "</span></h3>" +
    '<div class="steps">' + plan.steps.map((s, i) =>
      '<div class="step plan"><span class="n">' + String(i + 1).padStart(2, "0") + "</span>" +
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

/* ---------------------------------------------------------- 智能体目录：模型选择 */
const MAX_ORCH_MODELS = 3;   // 与后端 modelhub.MAX_BIND_MODELS、runner 降级链保持一致

/* 供应商 → 已启用模型（按优先级），用于分组下拉与多选面板 */
function modelGroups() {
  const out = [];
  for (const p of S.providers || []) {
    if (p.enabled === false) continue;
    const ms = (p.models || [])
      .filter((m) => !m.hidden && m.enabled !== false)
      .sort((a, b) => (a.priority || 0) - (b.priority || 0))
      .map((m) => m.name).filter(Boolean);
    if (ms.length) out.push({ id: p.id, name: p.name, models: ms });
  }
  return out;
}

/* 默认模型下拉：按供应商分组；当前值不在列表里时保留为选项，避免显示丢失 */
function modelSelectHtml(id, current) {
  const cur = fmtModel(current);
  const groups = modelGroups();
  let opts = '<option value="">（未设置）</option>';
  if (cur && !groups.some((g) => g.models.includes(cur))) {
    opts += '<option value="' + esc(cur) + '" selected>' + esc(cur) + "（当前值）</option>";
  }
  for (const g of groups) {
    opts += '<optgroup label="' + esc(g.name) + '">' +
      g.models.map((m) => '<option value="' + esc(m) + '"' +
        (m === cur ? " selected" : "") + ">" + esc(m) + "</option>").join("") +
      "</optgroup>";
  }
  return '<select id="model-' + esc(id) + '">' + opts + "</select>";
}

/* 卡片右上角的更新状态徽标（进入本页时后台自动检查） */
function updateChip(c) {
  if (!c.installed) return "";
  const u = c.update || {};
  if (S.catalogChecking && u.status === "unknown") return '<span class="tag">检查更新中…</span>';
  if (u.status === "updatable") return '<span class="tag upd">有新版本 ' + esc(u.latest || "") + "</span>";
  if (u.status === "current") return '<span class="tag ok">已是最新</span>';
  if (u.status === "unsupported") return '<span class="tag">该渠道无法自动检查</span>';
  return "";
}

/* 进入智能体目录页自动检查各 CLI 是否有新版本（后端 10 分钟缓存，前端 5 分钟节流） */
async function autoCheckUpdates() {
  if (Date.now() - (S.updateCheckAt || 0) < 5 * 60 * 1000) return;
  S.updateCheckAt = Date.now();
  try { await api("/api/catalog/check-updates", { method: "POST" }); } catch (e) { /* 忽略 */ }
  poll();
}

/* 运行时模型链（CLI 绑定页）：本地草稿态，点「保存」才写盘 */
function provName(pid) {
  const p = (S.providers || []).find((x) => x.id === pid);
  return p ? (p.name || pid) : "";
}

function bindChain(b) {
  if (b.chain && b.chain.length) return b.chain.map((c) => ({ p: c.provider_id || "", m: c.model || "" }));
  const pid = b.provider_id || "";
  const names = (b.models && b.models.length) ? b.models : (b.model ? [b.model] : []);
  return names.map((m) => ({ p: pid, m }));
}

function chainKey(chain) {
  return (chain || []).map((c) => c.p + "\u0002" + c.m).join("\u0001");
}

function bindSelById(id) {
  S.bindSel = S.bindSel || {};
  const server = bindChain((S.bindings || {})[id] || {});
  const key = chainKey(server);
  let st = S.bindSel[id];
  if (!st) {
    st = S.bindSel[id] = { open: false, chain: server, dirty: false, key };
  } else if (!st.dirty && st.key !== key) {
    st.chain = server;   // 别处改了配置 → 同步；本地有未保存改动时不覆盖
    st.key = key;
  }
  return st;
}

function bindRepaint() { S.bindSig = null; renderBindings(); }

function bindModelBox(c, provId) {
  const st = bindSelById(c.id);
  const chips = st.chain.length
    ? st.chain.map((c2, i) => {
        const pname = c2.p ? provName(c2.p) : "CLI 默认凭据";
        return '<span class="ochip' + (i === 0 ? " primary" : "") + '">' +
          "<b>" + (i === 0 ? "主" : "备") + "</b>" + esc(pname) + " · " + esc(c2.m) +
          (i > 0 ? '<button class="mini" data-m="' + esc(c2.m) + '" data-p="' + esc(c2.p) +
                  '" title="设为主模型" onclick="bindPromote(\'' + esc(c.id) + '\', this)">↑</button>' : "") +
          '<button class="mini" data-m="' + esc(c2.m) + '" data-p="' + esc(c2.p) +
            '" title="移除" onclick="bindRemove(\'' + esc(c.id) + '\', this)">×</button>' +
          "</span>";
      }).join("")
    : '<span class="hint">未设置' + (provId ? "（按供应商/难度自动解析）" : "（用 CLI 默认模型）") + "</span>";
  return '<div class="field"><label>运行时模型链（跨厂商，最多 ' + MAX_ORCH_MODELS + " 条）</label>" +
    '<div class="orch-row">' + chips +
    '<button class="ghost small" onclick="bindToggle(\'' + esc(c.id) + '\')">' +
    (st.open ? "收起" : "＋ 添加") + "</button>" +
    (st.dirty ? ' <span class="hint">有未保存改动</span>' : "") +
    "</div>" +
    (st.chain.length ? '<div class="hint">链先生效（覆盖「按难度自动选模型」）；主模型瞬态失败自动降级到下一条——可以是另一家厂商。</div>' : "") +
    (st.open ? bindPanel(c) : "") + "</div>";
}

function bindPanel(c) {
  const st = bindSelById(c.id);
  const groups = modelGroups();
  if (!groups.length) {
    return '<div class="ohint">还没有可用模型——先到「模型接入」页导入供应商并获取模型列表。</div>';
  }
  return '<div class="opanel">' +
    '<div class="ohint">勾选该 CLI 编排运行时的模型（可跨供应商混选）：第 1 条是主模型，其余按顺序作降级备选，每条自带该供应商的凭据注入。</div>' +
    groups.map((g) =>
      '<div class="ogroup"><div class="ogname">' + esc(g.name) +
      ' <span class="tag">注入该厂商凭据</span></div>' +
      g.models.map((m) => {
        const has = st.chain.some((x) => x.p === g.id && x.m === m);
        return '<label class="oitem"><input type="checkbox" value="' + esc(m) + '" data-p="' + esc(g.id) + '"' +
          (has ? " checked" : "") +
          " onchange=\"bindPick('" + esc(c.id) + "', this, this.checked)\">" + esc(m) + "</label>";
      }).join("") +
      "</div>").join("") + "</div>";
}

function bindToggle(id) {
  const st = bindSelById(id);
  st.open = !st.open;
  bindRepaint();
}

function bindPick(id, el, on) {
  const st = bindSelById(id), model = el.value, pid = el.dataset.p || "";
  const i = st.chain.findIndex((x) => x.p === pid && x.m === model);
  if (on && i < 0) {
    if (st.chain.length >= MAX_ORCH_MODELS) {
      alert("最多选 " + MAX_ORCH_MODELS + " 条（1 个主模型 + " +
            (MAX_ORCH_MODELS - 1) + " 个降级备选）。");
      bindRepaint();
      return;
    }
    st.chain.push({ p: pid, m: model });
  } else if (!on && i >= 0) {
    st.chain.splice(i, 1);
  }
  st.dirty = true;
  bindRepaint();
}

function bindRemove(id, el) {
  const st = bindSelById(id), m = el.dataset.m, p = el.dataset.p || "";
  st.chain = st.chain.filter((x) => !(x.p === p && x.m === m));
  st.dirty = true;
  bindRepaint();
}

function bindPromote(id, el) {
  const st = bindSelById(id), m = el.dataset.m, p = el.dataset.p || "";
  const hit = st.chain.find((x) => x.p === p && x.m === m);
  if (hit) st.chain = [hit].concat(st.chain.filter((x) => x !== hit));
  st.dirty = true;
  bindRepaint();
}

/* ---------------------------------------------------------- 智能体管理 */
function renderCatalog() {
  const cat = S.catalog || [];
  // 数据没变化就不重绘，避免轮询打断正在编辑的下拉/输入。
  const sig = JSON.stringify([cat, S.mgmt || {}, S.catalogChecking]);
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
  const hasModels = modelGroups().length > 0;
  const modelBox = c.config_writable
    ? (hasModels
        ? '<div class="field"><label>默认模型（写入配置文件）</label><div class="input-row">' +
          modelSelectHtml(c.id, c.model) +
          '<button class="ghost small" onclick="saveModel(\'' + esc(c.id) + '\')">保存</button></div></div>'
        : '<div class="field"><label>默认模型（写入配置文件）</label>' +
          '<div class="hint warn">还没有可选模型：先到「模型接入」页导入供应商并获取模型列表。</div></div>')
    : (c.model ? '<div class="facts">模型：<b>' + esc(fmtModel(c.model)) + "</b></div>" : "");
  const ops = [
    c.installed && c.orch_kind ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'smoke\')">冒烟测试</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'upgrade\')">升级</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small" onclick="checkUpdate(\'' + esc(c.id) + '\')">检查更新</button>' : "",
    !c.installed && c.has_install ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'install\')">安装</button>' : "",
    c.installed ? '<button class="ghost small" onclick="showMgmtLog(\'' + esc(c.id) + '\')">日志</button>' : "",
    !c.installed && !c.has_install ? '<span class="hint">安装命令待配置（编辑 data/catalog.json）</span>' : "",
  ].join("");
  return '<div class="card">' +
    '<div class="head"><span class="name">' + esc(c.name) + "</span>" +
    (c.installed ? statusChip("done") : '<span class="tag">未安装</span>') + updateChip(c) + "</div>" +
    '<div class="note">' + esc(c.note || "") + "</div>" +
    '<div class="facts">版本 <b>' + esc(c.version || "-") + "</b>　模型 <b>" + esc(fmtModel(c.model) || "-") + "</b>" +
    ((c.detail || c.config_path) ? "<br>" + esc(c.detail || c.config_path) : "") + "</div>" +
    mgmtPanel(c.id) +
    '<div class="ops">' + orchBox + ops + "</div>" + modelBox + "</div>";
}

/* 管理操作（升级/安装/冒烟）的就地反馈面板：状态 + 版本对比 + 输出日志 */
function mgmtPanel(agentId) {
  const st = (S.mgmt || {})[agentId];
  if (!st) return "";
  const chip = statusChip(st.status || "queued");
  const vcmp = (st.versionBefore || st.versionAfter)
    ? '<span class="hint">版本 ' + esc(st.versionBefore || "?") + " → <b>" +
      esc(st.versionAfter || "…") + "</b>" +
      (st.status === "done" && st.versionBefore && st.versionAfter === st.versionBefore
        ? "（已是最新版本，无需升级）" : "") + "</span>"
    : "";
  const logBox = st.showLog
    ? '<pre class="mgmt-log">' + esc(st.log || "（尚无输出）") + "</pre>" : "";
  return '<div class="mgmt-panel ' + esc(st.status || "queued") + '">' +
    '<div class="mgmt-head">' + chip +
    '<span class="mgmt-title">' + esc(st.opLabel || "") + "</span>" + vcmp +
    '<span class="mgmt-ops">' +
    '<button class="ghost small" onclick="toggleMgmtLog(\'' + esc(agentId) + '\')">' +
    (st.showLog ? "收起日志" : "查看日志") + "</button>" +
    '<button class="ghost small" onclick="openRunInRuns(\'' + esc(st.runId) + '\')">完整运行</button>' +
    '<button class="ghost small" onclick="clearMgmt(\'' + esc(agentId) + '\')">×</button>' +
    "</span></div>" +
    (st.summary ? '<div class="hint">' + esc(String(st.summary).slice(0, 300)) + "</div>" : "") +
    logBox + "</div>";
}

async function toggleOrch(id, enabled) {
  await api("/api/orchestration", { method: "POST", body: JSON.stringify({ agent_id: id, enabled }) });
  poll();
}

async function saveModel(id) {
  const v = $("model-" + id).value.trim();
  if (!v) { alert("请先在下拉里选择一个模型（要清除配置请手动编辑配置文件）。"); return; }
  try {
    const r = await api("/api/catalog/" + encodeURIComponent(id) + "/model", { method: "POST", body: JSON.stringify({ model: v }) });
    alert("已写入：" + (r.model || v));
  } catch (e) { alert("失败：" + e.message); }
  poll();
}

async function mgmt(id, op) {
  const names = { install: "安装", upgrade: "升级", smoke: "冒烟测试" };
  if (op === "install" && !confirm("确定执行安装？命令来自 data/catalog.json，可在管理页查看。")) return;
  const before = ((S.catalog || []).find((x) => x.id === id) || {}).version;
  S.mgmt = S.mgmt || {};
  S.mgmt[id] = { opLabel: names[op] || op, status: "queued", versionBefore: before,
                 log: "", showLog: false };
  S.catSig = null;
  renderCatalog();
  let r;
  try {
    r = await api("/api/catalog/" + encodeURIComponent(id) + "/" + op, { method: "POST" });
  } catch (e) {
    S.mgmt[id].status = "failed";
    S.mgmt[id].summary = e.message;
    S.catSig = null; renderCatalog();
    return;
  }
  S.mgmt[id].runId = r.run_id;
  S.catSig = null; renderCatalog();
  pollMgmt(id, r.run_id);   // 就地跟踪，不再跳转页面
}

/* 跟踪一次管理操作：更新状态、完成后拉日志并对比版本 */
async function pollMgmt(agentId, runId) {
  const st = S.mgmt[agentId];
  if (!st) return;
  try {
    const r = await api("/api/runs/" + encodeURIComponent(runId));
    const run = r.run;
    if (!run) return;
    st.status = run.status;
    st.summary = run.summary || run.error || "";
    st.retries = 0;
    if (st.showLog || run.status === "failed") {
      try { st.log = await fetchRunLog(runId); } catch (e) { /* 网络抖动时保留上一份日志 */ }
    }
    if (run.status === "queued" || run.status === "running") {
      S.catSig = null; renderCatalog();
      setTimeout(() => pollMgmt(agentId, runId), 2000);
      return;
    }
    // 结束：重新拉 catalog 拿最新版本，做前后对比
    const cat = await api("/api/catalog");
    S.catalog = cat.catalog;
    const now = (cat.catalog || []).find((x) => x.id === agentId) || {};
    st.versionAfter = now.version;
    if (!st.showLog) {
      try { st.log = await fetchRunLog(runId); } catch (e) { /* 保留上一次日志 */ }
    }
    S.catSig = null; renderCatalog();
    if (S.tab === "agents") poll();
  } catch (e) {
    // 升级/安装期间服务重启或网络抖动会让 fetch 直接失败（Failed to fetch）。
    // 只要运行还没结束就继续重试，否则面板会永久停在错误上。
    st.retries = (st.retries || 0) + 1;
    const pending = !st.status || st.status === "queued" || st.status === "running";
    if (pending && st.retries <= 15) {
      st.summary = "连接中断，正在重试（" + e.message + "）";
      S.catSig = null; renderCatalog();
      setTimeout(() => pollMgmt(agentId, runId), 3000);
      return;
    }
    st.summary = e.message;
    S.catSig = null; renderCatalog();
  }
}

/* 取一次运行最后一个步骤的输出日志；失败时抛出，由调用方决定如何提示 */
async function fetchRunLog(runId) {
  const r = await api("/api/runs/" + encodeURIComponent(runId));
  const steps = (r.run && r.run.steps) || [];
  if (!steps.length) return "（尚无输出）";
  const last = steps[steps.length - 1];
  const res = await api("/api/runs/" + encodeURIComponent(runId) +
                        "/log?step=" + encodeURIComponent(last.log || ""));
  return res.log || "（无输出）";
}

async function toggleMgmtLog(agentId) {
  const st = S.mgmt[agentId];
  if (!st) return;
  st.showLog = !st.showLog;
  if (st.showLog && st.runId) {
    try { st.log = await fetchRunLog(st.runId); }
    catch (e) { st.log = "日志读取失败：" + e.message; }
  }
  S.catSig = null; renderCatalog();
}

function clearMgmt(agentId) {
  if (S.mgmt) delete S.mgmt[agentId];
  S.catSig = null; renderCatalog();
}

/* 「日志」按钮：查看该智能体最近一次管理操作的输出 */
async function showMgmtLog(agentId) {
  const st = (S.mgmt || {})[agentId];
  if (st && st.runId) return toggleMgmtLog(agentId);
  // 没有本次会话记录时，从运行历史里找该智能体最近一次管理运行
  try {
    const r = await api("/api/runs");
    const run = (r.runs || []).find((x) => x.kind === "mgmt" && x.entry_id === agentId);
    if (!run) { alert("还没有该智能体的管理操作记录（先点升级/安装/冒烟测试）。"); return; }
    S.mgmt = S.mgmt || {};
    S.mgmt[agentId] = { opLabel: run.title, status: run.status, runId: run.id,
                        summary: run.summary || run.error || "",
                        log: await fetchRunLog(run.id), showLog: true };
    S.catSig = null; renderCatalog();
  } catch (e) { alert("读取失败：" + e.message); }
}

/* 「检查更新」：查询远端最新版本，明确告知是否已是最新（避免"升级没反应"的误解） */
async function checkUpdate(agentId) {
  S.mgmt = S.mgmt || {};
  const prev = S.mgmt[agentId] || {};
  S.mgmt[agentId] = Object.assign({}, prev, {
    opLabel: "检查更新", status: "running", summary: "正在查询远端最新版本…",
    showLog: prev.showLog, runId: prev.runId, versionBefore: prev.versionBefore,
  });
  S.catSig = null; renderCatalog();
  try {
    const r = await api("/api/catalog/" + encodeURIComponent(agentId) + "/check-update",
                        { method: "POST" });
    const up = r.updatable;
    let msg;
    if (up === true) {
      msg = "发现新版本：" + (r.current || "?") + " → " + (r.latest || "?") + "，可点「升级」更新。";
    } else if (up === false) {
      msg = "已是最新版本（" + (r.latest || r.current || "?") + "），无需升级。";
    } else {
      msg = r.note || "无法判断是否有更新。";
    }
    S.mgmt[agentId] = Object.assign({}, S.mgmt[agentId], {
      status: up === true ? "done" : (up === false ? "done" : "failed"),
      summary: msg, log: "当前版本：" + (r.current || "?") +
        "\n最新版本：" + (r.latest || "(未知)") +
        "\n可更新：" + (up === true ? "是" : up === false ? "否" : "未知") +
        (r.note ? "\n说明：" + r.note : ""),
      showLog: true, versionAfter: r.latest || undefined,
    });
  } catch (e) {
    S.mgmt[agentId] = Object.assign({}, S.mgmt[agentId],
      { status: "failed", summary: "检查失败：" + e.message });
  }
  S.catSig = null; renderCatalog();
}

/* ---------------------------------------------------------- 自定义流程管理 */
function openFlowsManager() {
  const flows = S.flows || [];
  const rows = flows.map((f) =>
    '<div class="item"><div class="t"><span class="name">' + esc(f.icon || "") + " " + esc(f.name) +
    '</span><span class="tag">' + esc(f.id) + "</span>" +
    '<span class="tag">' + (f.engine === "code" ? "代码引擎" : "评审引擎") + "</span>" +
    (f.builtin ? '<span class="tag ok">内置</span>' : "") +
    (!f.builtin ? '<button class="ghost small" onclick="flowForm(\'' + esc(f.id) + '\')">编辑</button>' +
      '<button class="danger small" onclick="deleteFlow(\'' + esc(f.id) + '\')">删除</button>' : "") +
    '</div><div class="desc">' + esc(f.note || "") +
    (f.rubric ? "　维度：" + esc(f.rubric.join(" / ")) : "") + "</div></div>").join("");
  const body = '<div class="list">' + rows + "</div>" +
    '<button class="primary" style="margin-top:10px" onclick="flowForm()">＋ 新建自定义流程</button>';
  openModal("🧩 任务类型管理", body, "");
}

/* 新建/编辑流程表单弹框；fid 空 = 新建 */
function flowForm(fid) {
  const f = fid ? flowById(fid) : null;
  const engineSel =
    '<div class="grid-2">' +
    '<div class="field"><label>流程 ID（小写字母开头）</label><input id="fl-id" value="' + esc(f ? f.id : "") + '"' + (f ? " disabled" : "") + ' placeholder="例：podcast-script"></div>' +
    '<div class="field"><label>名称</label><input id="fl-name" value="' + esc(f ? f.name : "") + '" placeholder="例：播客脚本"></div>' +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>图标</label><input id="fl-icon" value="' + esc(f ? (f.icon || "") : "") + '" maxlength="4" placeholder="✨"></div>' +
    '<div class="field"><label>引擎</label><select id="fl-engine"' + (f ? " disabled" : "") + '>' +
    '<option value="review"' + (!f || f.engine === "review" ? " selected" : "") + '>评审引擎（起草 → 多维评审 → 修订 → 门禁）</option>' +
    '<option value="code"' + (f && f.engine === "code" ? " selected" : "") + '>代码引擎（实现 → 验证 → 评审 → 修复）</option>' +
    "</select></div></div>" +
    '<div id="fl-review-fields">' +
    '<div class="grid-2">' +
    '<div class="field"><label>产出文件名</label><input id="fl-manuscript" value="' + esc(f ? (f.manuscript || "") : "") + '" placeholder="例：script.md"></div>' +
    '<div class="field"><label>发布阈值（1-10）</label><input id="fl-threshold" type="number" step="0.5" min="1" max="10" value="' + (f ? (f.threshold || 7) : 7) + '"></div>' +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>评审轮数（1-5）</label><input id="fl-rounds" type="number" min="1" max="5" value="' + (f ? (f.rounds || 2) : 2) + '"></div>' +
    '<div class="field"><label>评审维度（逗号分隔）</label><input id="fl-rubric" value="' + esc(f && f.rubric ? f.rubric.join(", ") : "") + '" placeholder="例：结构, 内容, 表达"></div>' +
    "</div>" +
    '<div class="field"><label>起草提示词（可选，占位符 __FILE__ __GOAL__ __CONTEXT__）</label><textarea id="fl-draft" rows="3" placeholder="留空 = 用内置通用模板">' + esc(f && f.draft_prompt ? f.draft_prompt : "") + "</textarea></div>" +
    '<div class="field"><label>评审提示词（可选，占位符 __DIMKEYS__ __MANUSCRIPT__）</label><textarea id="fl-critique" rows="3" placeholder="留空 = 用内置通用模板">' + esc(f && f.critique_prompt ? f.critique_prompt : "") + "</textarea></div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>连载模式：默认章节数（空 = 单稿件）</label><input id="fl-chapters" type="number" min="2" max="20" value="' + (f && f.serial ? f.serial.chapters : "") + '" placeholder="例：8"></div>' +
    '<div class="field"><label>每章约字数</label><input id="fl-words-per-ch" type="number" min="500" max="8000" step="100" value="' + (f && f.serial ? f.serial.words_per_chapter : "") + '" placeholder="例：2500"></div>' +
    "</div>" +
    "</div>";
  openModal(f ? "编辑流程：" + esc(f.name) : "新建自定义流程",
    '<div class="form">' +
    '<div class="field"><label>一句话说明（显示在流程列表）</label><input id="fl-note" value="' + esc(f ? (f.note || "") : "") + '" placeholder="例：技术播客单集脚本产出"></div>' +
    engineSel + "</div>", "");
  const foot = $("modal-foot");
  if (foot) foot.innerHTML = '<button class="primary" onclick="saveFlow()">保存</button>' +
    '<button class="ghost" onclick="closeModal()">取消</button>';
  const eng = $("fl-engine");
  if (eng) eng.addEventListener("change", () => {
    $("fl-review-fields").classList.toggle("hidden", eng.value !== "review");
  });
}

async function saveFlow() {
  const payload = {
    id: $("fl-id").value.trim(), name: $("fl-name").value.trim(),
    icon: $("fl-icon").value.trim() || "✨", engine: $("fl-engine").value,
    note: $("fl-note").value.trim(),
  };
  if (payload.engine === "review") {
    payload.manuscript = $("fl-manuscript").value.trim();
    payload.threshold = parseFloat($("fl-threshold").value) || 7;
    payload.rounds = parseInt($("fl-rounds").value, 10) || 2;
    payload.rubric = $("fl-rubric").value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
    payload.draft_prompt = $("fl-draft").value.trim();
    payload.critique_prompt = $("fl-critique").value.trim();
    const ch = parseInt($("fl-chapters").value, 10);
    if (ch >= 2) payload.serial = {
      chapters: ch,
      words_per_chapter: parseInt($("fl-words-per-ch").value, 10) || 2500,
    };
  }
  try {
    await api("/api/flows", { method: "POST", body: JSON.stringify(payload) });
  } catch (e) { alert("保存失败：" + e.message); return; }
  closeModal();
  await loadFlows();
  openFlowsManager();
  toast("流程已保存");
}

async function deleteFlow(fid) {
  if (!confirm("删除自定义流程「" + fid + "」？已有任务不受影响。")) return;
  try { await api("/api/flows/" + encodeURIComponent(fid) + "/delete", { method: "POST" }); }
  catch (e) { alert("删除失败：" + e.message); return; }
  await loadFlows();
  openFlowsManager();
  toast("已删除");
}

/* ---------------------------------------------------------- 编排中枢（编排者 + 并发设置） */
async function loadOrchestrator() {
  try {
    const r = await api("/api/orchestrator");
    S.orch = r.orchestrator || null;
  } catch (e) { S.orch = null; }
  renderOrch();
}

async function loadSettings() {
  try {
    S.settings = await api("/api/settings");
    const inp = $("set-workers");
    if (inp && S.settings) inp.value = S.settings.max_concurrent_jobs;
  } catch (e) { /* 忽略 */ }
}

/* 编排者供应商下拉：三种协议都支持直连（含 google），不限于可注入 CLI 的 */
function orchProvs() {
  return (S.providers || []).filter((p) => p.api_key && p.enabled !== false);
}

function renderOrch() {
  const box = $("orch-config");
  if (!box || !S.orch) return;
  const provs = orchProvs();
  const sig = JSON.stringify([S.orch, provs.map((p) => p.id)]);
  if (sig === S.orchSig) return;
  S.orchSig = sig;
  const sel = provs.find((p) => p.id === S.orch.provider_id) || null;
  const opts = '<option value="">（不使用编排者）</option>' + provs.map((p) =>
    '<option value="' + esc(p.id) + '"' + (S.orch.provider_id === p.id ? " selected" : "") + ">" +
    esc(p.name) + "（" + esc(p.protocol) + "）</option>").join("");
  const modelOpts = (sel ? (sel.models || []).filter((m) => !m.hidden && m.enabled !== false)
      .sort((a, b) => (a.priority || 0) - (b.priority || 0)).map((m) => m.name) : []);
  const curModel = S.orch.model || (sel ? sel.model : "") || "";
  const allOpts = Array.from(new Set([curModel].concat(modelOpts).filter(Boolean)));
  box.innerHTML =
    '<div class="grid-2">' +
    '<div class="field"><label>编排者供应商</label><select id="orch-prov">' + opts + "</select></div>" +
    '<div class="field"><label>编排者模型</label><select id="orch-model">' +
    '<option value="">（用供应商默认模型）</option>' +
    allOpts.map((m) => '<option value="' + esc(m) + '"' + (m === curModel ? " selected" : "") + ">" + esc(m) + "</option>").join("") +
    "</select></div></div>" +
    '<div class="ops"><label class="toggle"><input type="checkbox" id="orch-enabled"' +
    (S.orch.enabled ? " checked" : "") + "> 启用编排者（规划 / 难度判定 / 写作大纲）</label>" +
    '<button class="ghost small" onclick="saveOrchestrator()">保存</button>' +
    '<button class="ghost small" onclick="testOrchestrator()">测试连通</button>' +
    '<span id="orch-test" class="msg"></span></div>' +
    (S.orch.enabled && !S.orch.ready ? '<p class="hint warn">当前配置不生效：请检查供应商密钥、启停状态与模型选择。</p>' : "");
  $("orch-prov").addEventListener("change", () => { S.orchSig = null; renderOrch(); });
}

async function saveOrchestrator() {
  try {
    const r = await api("/api/orchestrator", { method: "POST", body: JSON.stringify({
      provider_id: $("orch-prov").value,
      model: $("orch-model").value,
      enabled: $("orch-enabled").checked }) });
    S.orch = r.orchestrator;
    S.orchSig = null;
    renderOrch();
    toast("编排者配置已保存");
  } catch (e) { alert("保存失败：" + e.message); }
}

async function testOrchestrator() {
  const el = $("orch-test");
  if (!el) return;
  el.textContent = "测试中…"; el.className = "msg";
  try {
    const r = await api("/api/orchestrator/test", { method: "POST" });
    el.className = "msg " + (r.ok ? "ok" : "err");
    el.textContent = r.ok ? ("✓ " + r.provider + " · " + r.model + " 回应正常") : ("✗ " + (r.error || "失败"));
  } catch (e) {
    el.className = "msg err"; el.textContent = "✗ " + e.message;
  }
}

async function saveSettings() {
  const msg = $("settings-msg");
  try {
    const r = await api("/api/settings", { method: "POST", body: JSON.stringify({
      max_concurrent_jobs: parseInt($("set-workers").value, 10) }) });
    S.settings = r.settings;
    if (msg) { msg.className = "msg ok"; msg.textContent = "已保存：最大并发 " + r.workers + " 个任务"; }
  } catch (e) {
    if (msg) { msg.className = "msg err"; msg.textContent = e.message; }
  }
}

/* ---------------------------------------------------------- 页签 & 初始化 */
const TAB_TITLES = { tasks: "任务", runs: "运行记录", agents: "智能体管理", models: "模型接入", bindings: "CLI 绑定", orch: "编排中枢" };



/* ---------------------------------------------------------- 手机连接（扫码） */
/* ZCode 桌面端式样：大二维码居中，地址+复制在下方；多来源（Tailscale/局域网）用 chips 切换 */
async function openPhoneConnect() {
  let urls = [];
  try {
    const d = await api("/api/connect");
    urls = d.urls || [];
  } catch (e) { toast(e.message, true); return; }
  if (!urls.length) { toast("未获取到可用的连接地址", true); return; }
  S.connUrls = urls; S.connIdx = 0;
  const chips = urls.map((u, i) =>
    '<button class="chip2' + (i === 0 ? " on" : "") + '" data-i="' + i + '" onclick="pickConnUrl(' + i + ')">' + esc(u.label) + "</button>").join("");
  const body =
    '<div class="phone-connect">' +
    '<div class="qr-box" id="qr-box"></div>' +
    (urls.length > 1 ? '<div class="conn-chips">' + chips + "</div>" : "") +
    '<div class="conn-url-row"><code id="conn-url"></code>' +
    '<button class="ghost small" onclick="copyConnUrl()">复制地址</button></div>' +
    '<p class="hint">手机相机扫码即自动登录（地址已含访问令牌，扫一次永久记住）。' +
    '局域网地址要求手机与电脑连同一 WiFi；Tailscale 地址出门也能用，' +
    '两端需登录同一 Tailscale 账号。手机控制时另一端自动变为只读，可在顶栏接管。<br>' +
    '连不上时（如路由器重启后地址变了）回电脑重新打开此弹框扫新码即可。</p>' +
    "</div>";
  openModal("📱 手机连接", body, "");
  renderConnQR();
}

function renderConnQR() {
  const u = S.connUrls[S.connIdx];
  const box = $("qr-box");
  if (!u || !box) return;
  if (typeof qrcode !== "function") { box.textContent = u.url; return; }  // 兜底：库没加载也能用
  const qr = qrcode(0, "M");
  qr.addData(u.url);
  qr.make();
  box.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 4, scalable: true });
  $("conn-url").textContent = u.url;
}

function pickConnUrl(i) {
  S.connIdx = i;
  document.querySelectorAll(".conn-chips .chip2").forEach((c) => c.classList.toggle("on", +c.dataset.i === i));
  renderConnQR();
}

function copyConnUrl() {
  const u = S.connUrls[S.connIdx];
  if (!u) return;
  const done = () => toast("已复制，手机浏览器粘贴打开即可");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(u.url).then(done, () => fallbackCopy(u.url, done));
  } else fallbackCopy(u.url, done);
}

function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("复制失败，请手动选择地址", true); }
  ta.remove();
}

function switchTab(name) {
  if (name === "__phone") { openPhoneConnect(); return; }  // 手机连接是弹框，不切页
  // 导航收进「设置」：进设置后左栏整体换成设置导航，内容铺满
  S.tab = name;
  document.body.classList.add("settings-mode");
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-settings"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === name));
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-" + name));
  const title = $("page-title");
  if (title) title.textContent = TAB_TITLES[name] || "设置";
  if (name === "runs" && !S.detailRunId) closeRun();
  if (name === "agents") autoCheckUpdates();   // 进目录页自动查各 CLI 新版本
  if (name === "orch") { loadOrchestrator(); loadSettings(); }  // 进编排中枢页拉取配置
}

/* 退出设置：左栏恢复任务树，内容回到任务页 */
function exitSettings() {
  S.tab = "tasks";
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-settings"));
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-tasks"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === "tasks"));
  document.body.classList.remove("settings-mode");
  const title = $("page-title");
  if (title) title.textContent = TAB_TITLES.tasks;
}

/* 侧栏底部「⚙ 设置」：弹出导航菜单（任务/运行记录/智能体管理/模型接入/CLI 绑定） */
function openSettingsMenu() {
  const btn = $("btn-settings");
  if (!btn) return;
  const r = btn.getBoundingClientRect();
  openCtxMenu(r.left, r.top - 8, [
    { label: "✦ 任务", fn: () => switchTab("tasks") },
    { label: "≡ 运行记录", fn: () => switchTab("runs") },
    { label: "⚙ 智能体管理", fn: () => switchTab("agents") },
    { label: "◈ 模型接入", fn: () => switchTab("models") },
    { label: "🔗 CLI 绑定", fn: () => switchTab("bindings") },
    { label: "✦ 编排中枢", fn: () => switchTab("orch") },
  ]);
}

window.openRun = openRun;
window.closeRun = closeRun;
window.archiveTask = archiveTask;
window.deleteTask = deleteTask;
window.retryTask = retryTask;
window.toggleLog = toggleLog;
window.cancelRun = cancelRun;
window.deleteRun = deleteRun;
window.toggleRunSel = toggleRunSel;
window.toggleAllRunSel = toggleAllRunSel;
window.clearRunSel = clearRunSel;
window.deleteSelectedRuns = deleteSelectedRuns;
window.clearRuns = clearRuns;
window.toggleOrch = toggleOrch;
window.saveModel = saveModel;
window.bindToggle = bindToggle;
window.bindPick = bindPick;
window.bindRemove = bindRemove;
window.bindPromote = bindPromote;
window.mgmt = mgmt;
window.switchTab = switchTab;
window.saveProvider = saveProvider;
window.delProvider = delProvider;
window.openAddProviderDialog = openAddProviderDialog;
window.doAddProvider = doAddProvider;
window.openImportDialog = openImportDialog;
window.doImport = doImport;
window.toggleAllSources = toggleAllSources;
window.updateImportSelHint = updateImportSelHint;
window.closeModal = closeModal;
window.saveBinding = saveBinding;
window.modelOp = modelOp;
window.delModel = delModel;
window.restoreHidden = restoreHidden;
window.toggleModelSel = toggleModelSel;
window.toggleGroupSel = toggleGroupSel;
window.clearModelSel = clearModelSel;
window.batchModelOp = batchModelOp;
window.toggleProvSel = toggleProvSel;
window.toggleAllProvSel = toggleAllProvSel;
window.filterModels = filterModels;
window.clearProvSel = clearProvSel;
window.batchProvOp = batchProvOp;
window.toggleProviderEnabled = toggleProviderEnabled;
window.selectProvider = selectProvider;
window.refreshAllModels = refreshAllModels;
window.refreshProviderModels = refreshProviderModels;
window.toggleMgmtLog = toggleMgmtLog;
window.clearMgmt = clearMgmt;
window.showMgmtLog = showMgmtLog;
window.checkUpdate = checkUpdate;
window.ctrlClick = ctrlClick;
window.submitToken = submitToken;
window.openPhoneConnect = openPhoneConnect;
window.pickConnUrl = pickConnUrl;
window.copyConnUrl = copyConnUrl;
window.openFlowsManager = openFlowsManager;
window.flowForm = flowForm;
window.saveFlow = saveFlow;
window.deleteFlow = deleteFlow;
window.saveOrchestrator = saveOrchestrator;
window.testOrchestrator = testOrchestrator;
window.saveSettings = saveSettings;

document.addEventListener("DOMContentLoaded", () => {
  // 远程地址里带的 ?token= 存起来并从地址栏抹掉，之后所有请求走请求头
  const urlTok = new URLSearchParams(location.search).get("token");
  if (urlTok) {
    localStorage.setItem("orch.token", urlTok);
    history.replaceState(null, "", location.pathname);
  }
  document.querySelectorAll(".set-item").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.sub)));
  $("btn-set-back").addEventListener("click", exitSettings);
  $("btn-settings").addEventListener("click", () => {
    // document 级捕获监听会先关掉已打开的菜单；若刚因本次点击而关闭则不再重开（实现开关切换）
    const menu = $("ctx-menu");
    if (menu.dataset.closedAt && Date.now() - +menu.dataset.closedAt < 250) return;
    openSettingsMenu();
  });
  $("model-pill").addEventListener("click", () => switchTab("models"));
  $("btn-back").addEventListener("click", closeRun);
  $("btn-cancel").addEventListener("click", cancelRun);
  $("btn-retry").addEventListener("click", () => { const r = S.lastRun; if (r && r.task_id) retryTask(r.task_id); });
  $("btn-delete").addEventListener("click", () => { if (S.detailRunId) deleteRun(S.detailRunId); });
  $("btn-theme").addEventListener("click", toggleTheme);
  $("btn-menu").addEventListener("click", () => document.body.classList.toggle("side-collapsed"));
  $("btn-new-task").addEventListener("click", exitSettings);
  bindCtxMenus();
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
  $("f-type").addEventListener("change", onTypeChange);
  $("f-mode").addEventListener("change", () => {
    $("f-manual-only").classList.toggle("hidden", $("f-mode").value !== "manual");
  });
  $("f-type").dispatchEvent(new Event("change"));
  $("btn-reload-catalog").addEventListener("click", async () => {
    await api("/api/catalog/reload", { method: "POST" }); poll();
  });
  $("btn-import").addEventListener("click", openImportDialog);
  $("btn-add-provider").addEventListener("click", openAddProviderDialog);
  $("prov-search").addEventListener("input", () => {
    S.provFilter = $("prov-search").value;
    renderProvList();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("modal").classList.contains("hidden")) closeModal();
  });
  $("btn-refresh-models").addEventListener("click", refreshAllModels);
  $("btn-reset-catalog").addEventListener("click", async () => {
    if (!confirm("恢复内置默认 catalog？你对该文件的修改将丢失。")) return;
    await api("/api/catalog/reset", { method: "POST" }); poll();
  });
  poll();
  schedulePolling();
  startSSE();
  startCtrlHeartbeat();
  loadFlows();   // 任务类型下拉（内置 + 自定义流程）
});
