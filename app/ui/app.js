/* CodeBee 前端（无依赖；状态走 SSE 实时推送，断线自动降级轮询） */
"use strict";

const $ = (id) => document.getElementById(id);
const S = { state: null, catalog: null, catSig: "", providers: null, bindings: null, modelsSig: "", bindSig: "", tab: "tasks", detailRunId: null, pollTimer: null, showArchived: false, /* 会话内开关：每次加载默认隐藏已归档、图标不选中（不持久化，见 btn-side-arch） */ selProvs: {}, selModels: {}, selRuns: {}, bindSel: {}, catalogChecking: false, updateCheckAt: 0, control: null, sseLive: false, es: null, flows: null, orch: null, skills: null, orchSig: "", settings: null, sessionAgents: new Set(), atts: [], gitInfo: null, inspKey: null, inspData: null, inspSig: "", inspAt: 0, inspTab: "git", inspAutoSig: "", rdTab: null, rdTabSig: "", rdTabPin: false, _rdCtx: {} };

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

/* 流程图标渲染："i-*" = 精灵表单色线性图标（currentColor，日/夜主题黑白自适应）；
 * 其他值（自定义流程的 emoji、老数据）原样文本显示兜底。 */
function flowIconHtml(f) {
  const ic = (f && f.icon) || "";
  if (ic.indexOf("i-") === 0) return '<svg class="ico" aria-hidden="true"><use href="#' + esc(ic) + '"></use></svg>';
  return ic ? '<span class="tp-emoji">' + esc(ic) + "</span>" : "";
}

function flowDesc(f) {
  return f.engine === "code" ? t("实现 → 验证 → 评审") : t("起草 → 多维评审 → 门禁");
}

function renderTypeOptions() {
  const sel = $("f-type");
  if (!sel || !S.flows) return;
  const prev = sel.value;
  sel.innerHTML = (S.flows || []).map((f) => {
    return '<option value="' + esc(f.id) + '">' + esc(f.name) + t("（") + flowDesc(f) + (f.builtin ? "" : t(" · 自定义")) + "）</option>";
  }).join("");
  if (prev && flowById(prev)) sel.value = prev;
  renderTypeMenu();
  onTypeChange();
}

/* 类型选择的可见层：带黑白图标的按钮 + 弹出菜单。原生 <option> 渲染不了 SVG，
 * 所以隐藏 select 只当值真源（既有联动/测试全部照旧），外观全靠这层。 */
function renderTypeMenu() {
  const menu = $("type-menu");
  if (!menu || !S.flows) return;
  menu.innerHTML = (S.flows || []).map((f) =>
    '<button type="button" class="type-item" role="option" data-v="' + esc(f.id) + '" onclick="pickType(\'' + esc(f.id) + '\')">' +
    '<span class="ti-ico" aria-hidden="true">' + flowIconHtml(f) + "</span>" +
    '<span class="ti-body"><span class="ti-name">' + esc(f.name) +
    (f.builtin ? "" : '<em class="ti-tag">' + t("自定义") + "</em>") + "</span>" +
    '<span class="ti-desc">' + esc(flowDesc(f)) + "</span></span>" +
    '<svg class="ico ti-check" aria-hidden="true"><use href="#i-check"></use></svg>' +
    "</button>").join("");
  syncTypeBtn();
}

function syncTypeBtn() {
  const sel = $("f-type");
  const flow = flowById(sel.value);
  const ico = $("f-type-ico"), name = $("f-type-name");
  if (flow && ico) ico.innerHTML = flowIconHtml(flow);
  if (flow && name) name.textContent = flow.name;
  const menu = $("type-menu");
  if (menu) menu.querySelectorAll(".type-item").forEach((b) => b.classList.toggle("on", b.dataset.v === sel.value));
}

function toggleTypeMenu(force) {
  const menu = $("type-menu");
  if (!menu) return;
  const show = force != null ? !!force : menu.classList.contains("hidden");
  menu.classList.toggle("hidden", !show);
}

function pickType(id) {
  const sel = $("f-type");
  if (sel && sel.value !== id) {
    sel.value = id;
    sel.dispatchEvent(new Event("change"));   // 既有联动（表单显隐/流程默认值）照常走
  }
  toggleTypeMenu(false);
}
window.toggleTypeMenu = toggleTypeMenu;
window.pickType = pickType;

/* 切换类型：按引擎显隐表单区、带出流程默认值 */
function onTypeChange() {
  syncTypeBtn();
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
  if (/Chrome/.test(ua)) return t("Chrome·电脑");
  return t("电脑");
}

function authHeaders(extra) {
  return Object.assign({
    "Content-Type": "application/json",
    "X-CodeBee-Token": localStorage.getItem("orch.token") || "",
    "X-CodeBee-Client": clientId(),
    "X-CodeBee-Name": encodeURIComponent(deviceName()),  // 头值必须 ISO-8859-1
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
  if (res.status === 401) { showTokenGate(t("令牌不正确或已更换，请重新输入")); throw new Error(t("需要访问令牌")); }
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

/* ------------------------------------------------ 应用内确认 / 输入弹框
 * 替代浏览器原生 confirm/prompt（原生框与应用视觉完全脱节）。
 * uiConfirm(msg, {ok, danger, title}) → Promise<boolean>
 * uiPrompt(title, value)               → Promise<string|null>（取消为 null）
 * 可叠在 #modal 之上（z-index 250 > 200）；Esc=取消、Enter=确定。
 */
let _askResolve = null;

function _askClose(v) {
  if (!_askResolve) return;
  const r = _askResolve;
  _askResolve = null;
  $("ask").classList.add("hidden");
  document.body.classList.remove("ask-open");
  r(v);
}

function _askOpen(opts) {
  if (_askResolve) _askClose(false);   // 上一帧未关：先收掉，避免悬挂 Promise
  return new Promise((resolve) => {
    _askResolve = resolve;
    $("ask-title").textContent = opts.title || t("确认");
    $("ask-body").innerHTML = opts.bodyHtml || "";
    const yes = $("ask-yes");
    yes.textContent = opts.okText || t("确定");
    yes.className = opts.danger ? "danger" : "primary";
    $("ask").classList.remove("hidden");
    document.body.classList.add("ask-open");
    if (opts.onOpen) opts.onOpen();
  });
}

function uiConfirm(message, opts) {
  opts = opts || {};
  return _askOpen({
    title: opts.title || t("确认操作"),
    bodyHtml: '<div class="ask-msg">' + esc(message) + "</div>",
    okText: opts.ok, danger: opts.danger,
    onOpen: () => $("ask-yes").focus(),
  });
}

function uiPrompt(title, value) {
  let inp = null;
  return _askOpen({
    title: title,
    bodyHtml: '<input type="text" class="ask-input" id="ask-input" autocomplete="off">',
    okText: t("确定"),
    onOpen: () => {
      inp = $("ask-input");
      inp.value = value || "";
      inp.focus();
      inp.select();
    },
  }).then((ok) => ((ok && inp) ? inp.value.trim() : null));
}

/* ------------------------------------------------- 采访式向导（新建任务） */
/* 不想面对整张表单的新用户：三问一确认——目标 → 类型 → 目录 → 预览填入。
 * 代码类流程多追问一步「验证命令」（怎么算跑通）；页序列按所选类型动态生成。
 * 向导只把答案预填进既有表单，提交/附件/评审设置全部留在表单里：
 * 不新增提交路径，质量闸门与既有测试照旧。目录浏览用表单里的「选择…」。 */
let wiz = null;   // { page, pages, goal, type, workdir, verify }

function wizardPages(type) {
  const flow = flowById(type);
  return ["goal", "type"].concat(
    flow && flow.engine === "code" ? ["verify"] : [], ["workdir", "summary"]);
}

window.openTaskWizard = async function () {
  // 冷启动时 boot 的 loadFlows 可能晚于用户点击：空了就现拉一次（autoForm 同款防御）
  if (!S.flows || !S.flows.length) { try { await loadFlows(); } catch (e) { /* 渲染时兜底 */ } }
  const type = (($("f-type") || {}).value) || ((S.flows || [])[0] || {}).id || "";
  wiz = { page: 0, pages: wizardPages(type), goal: "", type,
          workdir: (($("f-workdir") || {}).value || ""), verify: "" };
  renderWizard();
};

window.wizardBack = function () { if (wiz && wiz.page > 0) { wiz.page--; renderWizard(); } };
window.wizardPickType = function (id) {
  if (!wiz) return;
  wiz.type = id;
  wiz.pages = wizardPages(id);   // 按新类型重排页序列（代码流程插入验证步）
  wiz.page = 2;                  // 类型页的下一页
  renderWizard();
};

window.wizardNext = function () {
  if (!wiz) return;
  const id = wiz.pages[wiz.page];
  if (id === "goal") {
    const v = (($("wz-goal") || {}).value || "").trim();
    if (!v) { toast(t("目标还不能为空"), true); return; }
    wiz.goal = v;
    wiz.page++;
  } else if (id === "verify") {
    wiz.verify = (($("wz-verify") || {}).value || "").trim();
    wiz.page++;
  } else if (id === "workdir") {
    wiz.workdir = (($("wz-workdir") || {}).value || "").trim();
    wiz.page++;
  }
  renderWizard();
};

function wizardFoot(back) {
  return (back ? '<button class="ghost" onclick="wizardBack()">' + esc(t("上一步")) + "</button>" : "") +
    '<button class="primary" onclick="wizardNext()">' + esc(t("下一步")) + "</button>";
}

function renderWizard() {
  if (!wiz) return;
  const f = flowById(wiz.type);
  const id = wiz.pages[wiz.page];
  let body = "", foot = "";
  if (id === "goal") {
    body = '<textarea id="wz-goal" class="wz-goal" rows="4" placeholder="' +
      esc(t("要完成什么，一句话即可")) +
      '" onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();wizardNext()}">' +
      esc(wiz.goal) + "</textarea>";
    foot = wizardFoot(false);
  } else if (id === "type") {
    body = '<div class="wz-flows">' + (S.flows || []).map((fl) =>
      '<button type="button" class="wz-flow' + (fl.id === wiz.type ? " on" : "") +
      '" onclick="wizardPickType(\'' + esc(fl.id) + '\')">' +
      flowIconHtml(fl) +
      '<span class="wz-fname">' + esc(fl.name || fl.id) + "</span>" +
      '<span class="wz-fdesc">' + esc(flowDesc(fl)) + "</span></button>").join("") + "</div>" +
      '<p class="hint">' + esc(t("点一个类型即选定并继续；进表单后仍可改")) + "</p>";
    foot = wizardFoot(true);
  } else if (id === "verify") {
    body = '<input id="wz-verify" class="wz-input" autocomplete="off" value="' + esc(wiz.verify) +
      '" placeholder="' + esc(t("例：npm test、pytest -q；留空=只靠 AI 评审")) +
      '" onkeydown="if(event.key===\'Enter\'){event.preventDefault();wizardNext()}">' +
      '<p class="hint">' + esc(t("一条能在工作目录里跑的命令，退出码 0 即视为通过；之后可在表单改")) + "</p>";
    foot = wizardFoot(true);
  } else if (id === "workdir") {
    body = '<input id="wz-workdir" class="wz-input" autocomplete="off" value="' + esc(wiz.workdir) +
      '" placeholder="' + esc(t("留空用默认保存路径；也可稍后在表单里点「选择…」")) +
      '" onkeydown="if(event.key===\'Enter\'){event.preventDefault();wizardNext()}">' +
      '<p class="hint">' + esc(t("建议给每个任务一个独立目录，产出互不覆盖")) + "</p>";
    foot = wizardFoot(true);
  } else {
    body = '<div class="wz-sum">' +
      '<div><span class="wz-k">' + esc(t("类型")) + "</span>" + esc((f && f.name) || wiz.type) + "</div>" +
      '<div><span class="wz-k">' + esc(t("目标")) + "</span>" + esc(wiz.goal.slice(0, 120)) + "</div>" +
      (f && f.engine === "code"
        ? '<div><span class="wz-k">' + esc(t("验证命令")) + "</span>" +
          esc(wiz.verify || t("留空靠 AI 评审")) + "</div>"
        : "") +
      '<div><span class="wz-k">' + esc(t("工作目录")) + "</span>" +
      esc(wiz.workdir || t("默认保存路径")) + "</div></div>" +
      '<p class="hint">' + esc(t("填入表单后可继续：加附件、选实现者、改评审设置")) + "</p>";
    foot = '<button class="ghost" onclick="wizardBack()">' + esc(t("上一步")) + "</button>" +
      '<button class="primary" onclick="wizardApply()">' + esc(t("填入表单")) + "</button>";
  }
  openModal(t("新建任务向导") + " · " + (wiz.page + 1) + "/" + wiz.pages.length, body, foot);
  const first = $("wz-goal") || $("wz-verify") || $("wz-workdir");
  if (first) first.focus();
}

window.wizardApply = function () {
  if (!wiz) return;
  closeModal();
  const flow = flowById(wiz.type);
  if (flow && $("f-type").value !== wiz.type) { $("f-type").value = wiz.type; onTypeChange(); }
  $("f-goal").value = wiz.goal;
  if (flow && flow.engine === "code") $("f-verify").value = wiz.verify || "";
  if (wiz.workdir) {
    $("f-workdir").value = wiz.workdir;
    localStorage.setItem("orch.workdir", wiz.workdir);
  }
  S.atts = []; renderAttachChips();   // 新目标不带旧附件
  queueGitProbe();                    // 工作目录可能变了，重新探测代码版本
  $("f-goal").focus();
  toast(t("已按向导预填，检查或补充后点「创建并运行」"));
  wiz = null;
};

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
  const zh = { queued: t("排队中"), running: t("运行中"), done: t("完成"), failed: t("失败"), cancelled: t("已取消"), timeout: t("超时") };
  return '<span class="chip ' + esc(st) + '">' + (zh[st] || esc(st)) + "</span>";
}

/* 运行错误来源徽标：错误文案以「超时」打头（runner 统一格式）时标 TIMEOUT，
 * 和普通失败一眼区分；运行状态仍是 failed，不动状态机 */
function errTag(err) {
  return /^超时/.test(err || "") ? '<b class="err-tag">TIMEOUT</b>' : "";
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
  $("conn").textContent = t("已连接");
  $("conn").className = "conn ok";
  restoreInspector();   // 刷新后恢复上次打开的检查器（只在已开时为空操作）
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
    // 供应商集合/启停/密钥变了才重拉编排者：否则「生效中」状态点会在停用厂商后失真
    const provSig = JSON.stringify((S.providers || []).map((p) => [p.id, p.enabled, !!p.api_key]));
    if (provSig !== S.provSig) { S.provSig = provSig; loadOrchestrator(); }
    if (!S.sseLive) await refreshState();
    render();
  } catch (e) {
    if (!S.sseLive || !S.es) {
      $("conn").textContent = t("连接失败");
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
        $("conn").textContent = t("重连中…");
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
  // 顶栏统一纯图标：状态靠图标 + 颜色（mine=绿 / held=黄）传达，
  // 完整语义（谁在控制）放 title / aria-label，悬停与读屏都能拿到
  let icon = "unlock", tip = t("控制空闲：执行任意操作即可自动接管");
  if (c && c.mode !== "free") {
    if (c.mine) {
      el.classList.add("mine");
      icon = "gamepad"; tip = t("你在控制（点击释放）");
    } else {
      el.classList.add("held");
      icon = "lock";
      const holder = c.holder || t("其他设备");
      tip = t("「") + holder + t("」控制中（点击接管）");
    }
  }
  el.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-' + icon + '"></use></svg>';
  el.title = tip;
  el.setAttribute("aria-label", tip);
}

async function ctrlAction(action, force) {
  try {
    const d = await api("/api/control", { method: "POST", body: JSON.stringify({ action, force: !!force }) });
    setControl(d.control);
  } catch (e) { toast(e.message, true); }
}

async function ctrlClick() {
  const c = S.control;
  if (!c || c.mode === "free") return ctrlAction("acquire");
  if (c.mine) return ctrlAction("release");
  if (await uiConfirm(t("接管控制权？「") + (c.holder || t("其他设备")) + t("」将变为只读。"), { ok: t("接管") })) {
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
  renderRunList();
  renderSideTasks();
  renderRunDetail();
  renderCatalog();
  renderModels();
  renderBindings();
  renderOrchSide();   // 用缓存的 S.orch 重画（供应商变更时由 poll 触发重新拉取）
  refreshInspector(); // 检查器开着时节流跟刷（内部 2s 节流，关着直接返回）
}

/* ---------------------------------------------------------- 模型接入 */
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
      esc(p.name) + t("（") + esc(p.protocol) + (p.enabled === false ? t(" · 已停用") : "") + "）</option>").join("");
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
    (kw ? t("（筛选后 ") + provs.length + t(" 个）") : t("（") + provs.length + t("）")) + "</label>" : "";
  box.innerHTML = batch + allBox + (provs.map((p) => {
    const n = p.models == null ? null : p.models.filter((m) => !m.hidden).length;
    const st = n == null ? t("未获取") : n + t(" 模型");
    const off = p.enabled === false;
    return '<div class="prov-item' + (p.id === S.selProv ? " active" : "") + (off ? " off" : "") +
      '" onclick="selectProvider(\'' + esc(p.id) + '\')">' +
      '<input type="checkbox" class="pi-check"' + (S.selProvs[p.id] ? " checked" : "") +
      ' title="勾选以批量操作" onclick="event.stopPropagation()"' +
      ' onchange="toggleProvSel(\'' + esc(p.id) + '\', this.checked)">' +
      '<div class="pi-body"><div class="pi-top"><div class="pi-name">' + esc(p.name) + "</div>" +
      '<span class="pi-n">' + st + "</span></div>" +
      '<div class="pi-meta"><span class="tag">' + esc(p.protocol) + "</span>" +
      (off ? '<span class="tag">' + t("已停用") + "</span>" : "") +
      "</div></div></div>";
  }).join("") ||
    '<div class="hint" style="padding:8px">' + (kw && (S.providers || []).length
      ? t("没有匹配「") + esc(S.provFilter) + t("」的供应商。")
      : t("暂无供应商——点上方「导入」。")) + "</div>");
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
    enable: t("启用所选 ") + ids.length + t(" 个供应商？"),
    disable: t("停用所选 ") + ids.length + t(" 个供应商？\\n停用后其绑定会回落为 CLI 默认；配置与模型列表都保留，可随时再启用。"),
    delete: t("删除所选 ") + ids.length + t(" 个供应商？\\n相关 CLI 绑定会自动解绑，此操作不可撤销。"),
  };
  if (!await uiConfirm(tips[op] || (t("执行「") + op + t("」？")))) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids, op }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
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
    : '<span class="badge bad" title="' + esc(tp.error || "") + '">✗ ' + esc((tp.error || t("失败")).slice(0, 60)) + "</span>") : "";
  const models = (p.models || []).filter((m) => !m.hidden)
    .sort((a, b) => (a.priority || 0) - (b.priority || 0));
  const hidden = (p.models || []).filter((m) => m.hidden);
  const sel = modelSel(p.id);
  const selN = Object.keys(sel).length;
  let html = '<div class="pd-head">' +
    '<div class="pd-title"><span class="pd-name">' + esc(p.name) + "</span>" +
    '<span class="tag">' + esc(p.protocol) + "</span>" +
    (p.enabled === false ? '<span class="tag">' + t("已停用") + "</span>" : "") +
    srcTag(p.source) + tpHtml + "</div>" +
    '<div class="pd-url" title="' + esc(p.base_url || "") + '">' + esc(p.base_url || "") + "</div>" +
    '<div class="pd-ops">' +
    '<button class="primary small" onclick="testProv(\'' + esc(p.id) + '\')">' + t("测试连接") + "</button>" +
    '<button class="ghost small" onclick="refreshProviderModels(\'' + esc(p.id) + '\')">' + t("获取模型列表") + "</button>" +
    '<button class="ghost small" onclick="toggleProviderEnabled(\'' + esc(p.id) + '\', ' +
    (p.enabled === false) + ')">' + (p.enabled === false ? t("启用供应商") : t("停用供应商")) + "</button>" +
    '<span class="pd-ops-gap"></span>' +
    '<button class="danger small" onclick="delProvider(\'' + esc(p.id) + '\')">' + t("删除") + "</button>" +
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
    const g = m.protocol || p.protocol || t("其他");
    (groups[g] = groups[g] || []).push(m);
  }
  const gnames = Object.keys(groups).sort();
  if (!gnames.length) {
    return '<div class="empty">' + (kw
      ? t("没有匹配「") + esc(kw) + t("」的模型。")
      : t("该供应商没有可用模型（或全部被停用）。")) + "</div>";
  }
  let html = "";
  for (const g of gnames) {
    const gm = groups[g];
    const allSel = gm.every((m) => sel[m.name]);
    html += '<div class="pgroup"><div class="pgroup-title">' +
      '<label class="pcheck-all"><input type="checkbox"' + (allSel ? " checked" : "") +
      ' onchange="toggleGroupSel(\'' + esc(p.id) + '\', \'' + esc(g) + '\', this.checked)">全选</label>' +
      esc(g) + t(" 协议 · ") + gm.length + t(" 个") +
      (kw ? t("（过滤中，拖拽排序暂停）") : t("（拖动 ☰ 调序；启用的排最前）")) + "</div>";
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
  el.textContent = kw.trim() ? (shown + " / " + total + t(" 个模型")) : (total + t(" 个模型"));
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
    '<span class="drag" title="拖动调整优先级"><svg class="ico" aria-hidden="true"><use href="#i-grip"></use></svg></span>' +
    '<span class="pprio">#' + m.priority + "</span>" +
    '<span class="pname" title="' + esc(m.name) + '">' + esc(m.name) + "</span>" +
    price + tmHtml +
    '<span class="row-ops">' +
    '<button class="ghost small row-op" onclick="testModelBtn(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">测试</button>' +
    '<button class="danger small row-op" title="从列表删除：刷新/重新导入不会再带回，可在分组底部恢复"' +
    ' onclick="delModel(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">删除</button>' +
    '<label class="tog-mini" title="' + (m.enabled ? t("停用（不影响配置，仅编排选模跳过）") : t("启用")) + '">' +
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
    const g = m.protocol || p.protocol || t("其他");
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
    .then(poll).catch((e) => toast(t("排序失败：") + e.message, true));
}

async function testProv(pid) {
  S.testProvState = S.testProvState || {};
  S.testProvState[pid] = { ok: false, error: t("测试中…") };
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
  S.testModelState[key] = { ok: false, error: t("测试中…") };
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
  } catch (e) { toast(t("操作失败：") + e.message, true); }
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
    if ((m.protocol || p.protocol || t("其他")) !== group) continue;
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
    enable: t("启用所选 ") + names.length + t(" 个模型？"),
    disable: t("停用所选 ") + names.length + t(" 个模型？\\n停用只影响编排选模，不删除配置。"),
    delete: t("删除所选 ") + names.length + t(" 个模型？\\n刷新 / 重新导入都不会再带回，可在「已删除」里恢复。"),
    restore: t("恢复所选 ") + names.length + t(" 个模型？\\n它们会重新启用并自动置顶。"),
  };
  if (!await uiConfirm(tips[op] || (t("执行「") + op + t("」？")))) return;
  try {
    await api("/api/models/model-op", { method: "POST",
      body: JSON.stringify({ provider_id: pid, names, op }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
  clearModelSel(pid);
  poll();
}

async function delModel(pid, name) {
  if (!await uiConfirm(t("删除模型「") + name + t("」？\\n刷新 / 重新导入模型列表都不会再带回，可在分组底部「恢复全部」找回。"), { ok: t("删除"), danger: true })) return;
  modelOp(pid, name, "delete");
}

async function toggleProviderEnabled(pid, enabled) {
  const p = (S.providers || []).find((x) => x.id === pid) || {};
  const off = p.enabled === false;
  if (!off && !await uiConfirm(t("停用供应商「") + (p.name || pid) +
      t("」？\\n停用后它的绑定会回落为 CLI 默认；配置与模型列表保留，可随时再启用。"), { ok: t("停用") })) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [pid], op: off ? "enable" : "disable" }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
  S.modelsSig = null;
  poll();
}

async function restoreHidden(pid) {
  if (!await uiConfirm(t("恢复该供应商下全部已删除的模型？\\n它们会回到优先级末尾。"), { ok: t("恢复") })) return;
  modelOp(pid, "", "restore-all");
}

async function refreshAllModels() {
  // 按钮内是 SVG 图标，不能用 textContent 改文案（会抹掉图标）；
  // 加载态用 class 驱动图标旋转，结束时移除
  const btn = $("btn-refresh-models");
  btn.disabled = true; btn.classList.add("loading"); btn.title = t("正在刷新全部模型列表…");
  try {
    const r = await api("/api/models/refresh-all", { method: "POST" });
    btn.title = r.message || t("正在刷新…");
  } catch (e) { toast(t("刷新失败：") + e.message, true); }
  setTimeout(() => {
    btn.disabled = false; btn.classList.remove("loading");
    btn.title = t("全部重新拉取模型列表");
  }, 1500);
  setTimeout(poll, 2500); setTimeout(poll, 8000);
}

async function refreshProviderModels(id) {
  try {
    const r = await api("/api/models/refresh", { method: "POST", body: JSON.stringify({ id }) });
    if (!r.ok && r.message) toast(t("获取失败：") + r.message, true);
  } catch (e) { toast(t("获取失败：") + e.message, true); }
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
    fld("pname-" + p.id, t("名称"), p.name, t("名称")) +
    fld("pmodel-" + p.id, t("默认模型"), p.model || "", t("默认模型")) +
    fld("purl-" + p.id, t("API 地址"), p.base_url, "base_url", true) +
    fld("pkey-" + p.id, t("密钥"), "", t("（") + (p.api_key || t("未设置")) + t("，留空=不改）"), true) +
    fld("peasy-" + p.id, t("简单任务模型"), p.model_easy || "", t("难度路由 · 简单")) +
    fld("phard-" + p.id, t("困难任务模型"), p.model_hard || "", t("难度路由 · 困难")) +
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
  catch (e) { toast(t("保存失败：") + e.message, true); }
  poll();
}

async function delProvider(id) {
  if (!await uiConfirm(t("删除该供应商（绑定会自动解绑）？"), { ok: t("删除"), danger: true })) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [id], op: "delete" }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
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
  openModal(t("添加供应商"),
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
  if (!body.name) { res.textContent = t("请填写名称"); return; }
  if (!/^https?:\/\//.test(body.base_url)) { res.textContent = "API 地址必须以 http:// 或 https:// 开头"; return; }
  const btn = $("btn-do-add");
  btn.disabled = true; btn.textContent = t("保存中…");
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
    res.textContent = t("保存失败：") + e.message;
    btn.disabled = false; btn.textContent = t("保存");
  }
}

/* 「导入」：扫描本机各 AI 工具配置，勾选后可一次导入 */
async function openImportDialog() {
  openModal(t("导入供应商"), '<div class="hint">正在扫描本机 AI 工具配置…</div>',
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
  el.textContent = t("已选 ") + n + " / " + boxes.length + t(" 个来源");
  const btn = $("btn-do-import");
  if (btn && btn.textContent === t("导入选中")) btn.disabled = !n;
}

function toggleAllSources(on) {
  importSelBoxes().forEach((b) => { b.checked = on; });
  updateImportSelHint();
}

async function doImport() {
  const ids = importSelBoxes().filter((b) => b.checked).map((b) => b.value);
  if (!ids.length) { toast(t("请至少选择一个来源。"), true); return; }
  const btn = $("btn-do-import");
  const boxes = importSelBoxes();
  boxes.forEach((b) => { b.disabled = true; });
  btn.disabled = true; btn.textContent = t("导入中…");
  try {
    const r = await api("/api/models/import", { method: "POST", body: JSON.stringify({ sources: ids }) });
    const lines = (r.sources || []).map((s) => {
      if (!s.found) return "<li>" + esc(s.name) + "：未找到配置</li>";
      if (s.error) return "<li>" + esc(s.name) + '：<span class="bad">' + esc(s.error) + "</span></li>";
      let txt = t("新增 ") + s.added + t("，更新 ") + s.updated;
      if (s.duplicate) txt += t("，跳过重复 ") + s.duplicate;
      const extra = s.note ? t("（") + esc(s.note) + t("）") : "";
      return "<li>" + esc(s.name) + t("：") + txt + extra + "</li>";
    }).join("");
    $("import-result").innerHTML =
      '<div class="import-done"><b>' + esc(r.message) + "</b><ul>" + lines + "</ul></div>";
    btn.textContent = t("完成");
    btn.disabled = false;
    btn.onclick = closeModal;
    if (r.imported) { S.selProv = null; S.modelsSig = null; poll(); }
  } catch (e) {
    $("import-result").innerHTML = '<div class="msg bad">导入失败：' + esc(e.message) + "</div>";
    btn.disabled = false; btn.textContent = t("导入选中");
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
    toast(t("保存失败：") + e.message, true);
    return;
  }
  poll();
}

/* ---------------------------------------------------------- 任务表单 */
function renderImplSelects() {
  // 演示智能体（mock）不上选择器：手动编排不该误选到它。
  // 仅当本机一个真实智能体都没有时才回退显示（全新用户仍可零消耗试用）
  const all = (S.state && S.state.agents) || [];
  const real = all.filter((a) => a.mode !== "mock");
  const agents = real.length ? real : all;
  const sel = $("f-impl");
  const prev = sel.value;
  sel.innerHTML = agents.map((a) =>
    '<option value="' + esc(a.id) + '">' + esc(a.label) + "</option>").join("");
  if (prev && agents.some((a) => a.id === prev)) sel.value = prev;

  // 继续会话：已安装且支持会话扫描的 CLI（不要求启用编排——续会话是显式指定）
  const rsel = $("f-resume-agent");
  const rprev = rsel.value;
  const rAgents = (S.catalog || []).filter((e) =>
    e.installed && e.orch_kind && S.sessionAgents.has(e.id));
  rsel.innerHTML = '<option value="">不沿用（全新开始）</option>' +
    rAgents.map((e) => '<option value="' + esc(e.id) + '">' + esc(e.name) + " 的会话</option>").join("");
  if (rprev && rAgents.some((e) => e.id === rprev)) rsel.value = rprev;

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
  msg.className = "msg"; msg.textContent = t("提交中…");
  const payload = {
    type: $("f-type").value,
    mode: $("f-mode").value,
    title: $("f-title").value.trim(),
    goal: $("f-goal").value.trim(),
    context: $("f-context").value.trim(),
    workdir: $("f-workdir").value.trim(),
  };
  if (!payload.workdir) {
    delete payload.workdir;  // 留空 → 服务端用「默认保存路径」（编排设置可改）
  } else {
    localStorage.setItem("orch.workdir", payload.workdir);
  }
  if (payload.mode === "manual") payload.implementer = $("f-impl").value;
  // 附件与代码版本：任务创建时服务端把待提交附件移入 _attachments/ 并注入上下文
  if ((S.atts || []).length) payload.attachments = S.atts.filter((a) => a.id).map((a) => a.id);
  if (!$("row-git").classList.contains("hidden") && $("f-git-rev").value) {
    // 下拉值带 kind: 前缀（branch:main / tag:v1.0 / commit:abc123），提交时还原为纯 rev
    payload.git_rev = $("f-git-rev").value.split(/:(.*)/s)[1] || "";
    if (!payload.git_rev) delete payload.git_rev;
  }
  const sid = $("f-resume-session").value;
  const resumeAgent = $("f-resume-agent").value;
  if (resumeAgent && sid) {
    const opt = $("f-resume-session").selectedOptions[0];
    const hit = (S.sessionList || []).find((s) => s.session_id === sid);
    payload.resume = { agent: resumeAgent, session: sid,
      preview: opt ? opt.textContent : "", project: (hit && hit.project) || "" };
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
      variants: Math.max(1, Math.min(3, parseInt($("f-variants").value, 10) || 1)),
    };
    const critics = Array.from($("f-critics").querySelectorAll("input:checked")).map((i) => i.value);
    if (critics.length) payload.critics = critics;
  }
  try {
    const r = await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
    msg.textContent = t("已创建，跳转运行页…");
    switchTab("runs");
    openRun(r.run_id);
    $("f-goal").value = "";
    S.atts = []; renderAttachChips();   // 附件已移交任务待提交区，清空本地列表
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  }
}

/* ---------------------------------------------------------- 附件（截图/文件） */
/* 待提交附件：选中/粘贴即上传到服务端 pending 区，创建任务时随 payload 落盘
 * 到工作目录 _attachments/ 并注入上下文。图片另走 codex 原生 -i 直读；
 * Word/Excel/PPT 由服务端抽正文生成伴生 .txt，智能体直接读文本版。 */
const ATT_EXT_OK = /\.(png|jpe?g|gif|webp|bmp|svg|txt|md|markdown|csv|json|log|py|js|ts|html?|css|xml|ya?ml|toml|pdf|docx?|xlsx?|pptx?|rtf|odt|ods)$/i;
const ATT_OFFICE_RE = /\.(docx?|xlsx?|pptx?|rtf|odt|ods)$/i;
const ATT_CAP = 8 * 1024 * 1024;         // 普通附件 8MB
const ATT_CAP_OFFICE = 24 * 1024 * 1024; // Office 文档放宽到 24MB

function renderAttachChips() {
  const box = $("att-chips");
  if (!box) return;
  box.innerHTML = (S.atts || []).map((a) =>
    '<span class="att-chip" title="' + esc(a.name) + '">' +
    '<svg class="ico"><use href="#i-paperclip"/></svg>' +
    "<span>" + esc(a.name) + "</span>" +
    " <i>" + fmtSize(a.size) + "</i>" +
    '<button class="ax" title="' + esc(t("移除")) + '" onclick="removeAtt(\'' + a.id + '\')">' +
    '<svg class="ico"><use href="#i-x"/></svg></button></span>'
  ).join("");
}

function fmtSize(n) {
  if (n >= 1048576) return (n / 1048576).toFixed(1) + "MB";
  if (n >= 1024) return Math.round(n / 1024) + "KB";
  return (n || 0) + "B";
}

function removeAtt(id) {
  S.atts = (S.atts || []).filter((a) => a.id !== id);
  renderAttachChips();
}
window.removeAtt = removeAtt;

async function addAttachFiles(files) {
  if (!files || !files.length) return;
  if ((S.atts || []).length + files.length > 12) {
    toast(t("附件最多 12 个"), true); return;
  }
  for (const f of files) {
    if (!ATT_EXT_OK.test(f.name)) { toast(t("不支持的附件类型：") + f.name, true); continue; }
    const cap = ATT_OFFICE_RE.test(f.name) ? ATT_CAP_OFFICE : ATT_CAP;
    if (f.size > cap) { toast(t("附件超过：") + f.name, true); continue; }
    try {
      const b64 = await new Promise((res, rej) => {
        const rd = new FileReader();
        rd.onload = () => res(String(rd.result).split(",")[1] || "");
        rd.onerror = () => rej(new Error(t("读取失败")));
        rd.readAsDataURL(f);
      });
      const r = await api("/api/attachments", { method: "POST",
        body: JSON.stringify({ name: f.name, data: b64 }) });
      S.atts = S.atts || [];
      if (S.atts.some((a) => a.id === r.attachment.id)) continue;  // 去重
      S.atts.push(r.attachment);
      renderAttachChips();
    } catch (e) {
      toast(t("附件上传失败：") + f.name + " — " + e.message, true);
    }
  }
}
window.addAttachFiles = addAttachFiles;

/* 目标框粘贴截图：Ctrl+V 直接作为附件上传 */
function onGoalPaste(e) {
  const files = [];
  for (const item of (e.clipboardData ? e.clipboardData.items : [])) {
    if (item.kind === "file") {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (files.length) {
    e.preventDefault();
    // 截图多为无文件名 blob，起个可读名字（扩展名由服务端白名单校验）
    addAttachFiles(files.map((f, i) => {
      const when = new Date();
      const ts = when.getFullYear() + String(when.getMonth() + 1).padStart(2, "0")
        + String(when.getDate()).padStart(2, "0") + "-" + String(when.getHours()).padStart(2, "0")
        + String(when.getMinutes()).padStart(2, "0") + String(when.getSeconds()).padStart(2, "0");
      const ext = (f.name.match(/\.[a-z0-9]+$/i) || [f.type === "image/png" ? ".png" : ".png"])[0];
      return f.name ? f : new File([f], "pasted-" + ts + (files.length > 1 ? "-" + (i + 1) : "") + ext, { type: f.type });
    }));
  }
}

/* ---------------------------------------------------------- 代码版本（Git） */
/* 工作目录指向 git 仓库时给出分支/标签/提交选择；创建任务后执行前由服务端
 * 从所选版本检出任务分支 tutti/<task-id>（脏工作区会显式失败，不静默降级）。 */
let _gitProbeTimer = null;

function queueGitProbe() {
  clearTimeout(_gitProbeTimer);
  _gitProbeTimer = setTimeout(probeGit, 500);
}

async function probeGit() {
  const row = $("row-git"), sel = $("f-git-rev"), hint = $("git-hint");
  if (!row || !sel) return;
  const wd = ($("f-workdir").value || "").trim();
  if (!wd) { row.classList.add("hidden"); return; }
  let info = null;
  try {
    info = await api("/api/git/info?workdir=" + encodeURIComponent(wd));
  } catch (e) { info = null; }  // 远程端 403 / 非仓库：静默隐藏，不干扰手填路径
  if (!info || !info.repo) { row.classList.add("hidden"); return; }
  S.gitInfo = info;
  row.classList.remove("hidden");
  const prev = sel.value;
  const opt = (v, label) => '<option value="' + esc(v) + '">' + esc(label) + "</option>";
  const groups = [];
  groups.push(opt("", t("不切换（用当前分支 %1）").replace("%1", info.branch || "HEAD")));
  groups.push('<optgroup label="' + esc(t("分支")) + '">'
    + (info.branches || []).map((b) => opt("branch:" + b, b)).join("") + "</optgroup>");
  if ((info.tags || []).length) {
    groups.push('<optgroup label="' + esc(t("标签")) + '">'
      + info.tags.map((tg) => opt("tag:" + tg, tg)).join("") + "</optgroup>");
  }
  if ((info.recent || []).length) {
    groups.push('<optgroup label="' + esc(t("最近提交")) + '">'
      + info.recent.map((c) => opt("commit:" + c.hash, c.hash + " " + c.subject)).join("") + "</optgroup>");
  }
  sel.innerHTML = groups.join("");
  // 记忆旧选择（下拉重绘不弹回）；值带 kind 前缀，服务端解析出纯 rev
  if (prev && Array.from(sel.options).some((o) => o.value === prev)) sel.value = prev;
  renderGitHint();
}

function renderGitHint() {
  const info = S.gitInfo;
  if (!info) return;
  const sel = $("f-git-rev");
  const pick = (sel.value || "").split(":");
  const kind = pick[0], rev = pick[1] || "";
  const base = t("当前分支 %1 @ %2").replace("%1", info.branch).replace("%2", (info.head || "").slice(0, 8));
  const dirty = info.dirty ? t("；工作区有 %1 处未提交改动").replace("%1", info.dirty_count) : "";
  const act = kind
    ? t("；运行时将从「%1」检出任务分支 tutti/&lt;任务ID&gt;").replace("%1", rev)
    : "";
  $("git-hint").innerHTML = esc(base + dirty + act);
}
window.probeGit = probeGit;

/* ---------------------------------------------------------- 会话延续 */
/* 后端支持会话扫描的工具集合（/api/sessions 的键）；决定继续会话下拉出现哪些工具 */
async function refreshSessionAgents() {
  try {
    const r = await api("/api/sessions");
    S.sessionAgents = new Set(Object.keys(r.sessions || {}));
  } catch (e) { /* 拉取失败保持现状，下拉由其余渲染兜底 */ }
  renderImplSelects();
}

async function loadSessions() {
  const agent = $("f-resume-agent").value;
  const row = $("row-resume-session");
  const sel = $("f-resume-session");
  if (!agent) { row.classList.add("hidden"); S.sessionList = []; return; }
  row.classList.remove("hidden");
  sel.innerHTML = '<option>（加载中…）</option>';
  try {
    const r = await api("/api/sessions");
    const list = (r.sessions || {})[agent] || [];
    S.sessionList = list;   // 提交时带上 project（续会话要在该目录下启动 CLI）
    sel.innerHTML = list.length ? list.map((s) => {
      const who = s.project ? " [" + String(s.project).replace(/[\\/]+$/, "").split(/[\\/]/).pop() + "]" : "";
      return '<option value="' + esc(s.session_id) + '">[' + esc(s.mtime) + "]" + who + " " + esc(s.preview.slice(0, 60)) + "</option>";
    }).join("") : '<option value="">（未找到该智能体的本地会话）</option>';
  } catch (e) {
    S.sessionList = [];
    sel.innerHTML = '<option value="">（扫描失败）</option>';
  }
  showResumeHint();
}

/* 提示该会话将在哪个目录续接（CLI 需在会话项目目录下启动） */
function showResumeHint() {
  const hint = $("resume-session-hint");
  if (!hint) return;
  const hit = (S.sessionList || []).find((s) => s.session_id === $("f-resume-session").value);
  if (!hit || !hit.project) { hint.textContent = ""; return; }
  hint.textContent = t("该会话属于 ") + hit.project + t("，续接时 CLI 将在该目录下启动")
    + (hit.turns ? t("（已有 ") + hit.turns + t(" 轮对话上下文）") : "");
}

async function archiveTask(id, archived) {
  try {
    await api("/api/tasks/" + encodeURIComponent(id) + "/archive",
      { method: "POST", body: JSON.stringify({ archived: !!archived }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
  poll();
}

async function deleteTask(id) {
  if (!await uiConfirm(t("删除该任务及其全部运行记录（含日志与报告）？不可恢复。"), { ok: t("删除"), danger: true })) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(id) + "/delete", { method: "POST" });
  } catch (e) { toast(t("删除失败：") + e.message, true); return; }
  if (S.detailRunId) closeRun();
  poll();
}

/* ---------------------------------------------------------- 右键菜单（归档/删除） */
const ORPHAN_DIR = "__orphan__";  // 无主运行（无 task_id / 任务已删）的兜底文件夹，与 renderSideTasks 共用
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
  if (menu && !menu.classList.contains("hidden")) menu.classList.add("hidden");
}

function bindCtxMenus() {
  // 侧栏任务树：右键任务 → 详情/目录/重试/重命名/归档/删除；管理类运行 → 详情/目录/删除记录
  $("side-tasks").addEventListener("contextmenu", (e) => {
    // .stask 在 .sdir 内部：先判任务行，点到才算文件夹自己的菜单
    const det = e.target.closest(".stask");
    if (det) {
    e.preventDefault();
    const taskId = det.dataset.task || "", runId = det.dataset.run || "";
    const items = [];
    if (runId) items.push({ label: t("打开详情"), fn: () => sideOpenRun(runId) });
    if (taskId) {
      items.push("-");
      items.push({ label: t("打开工作目录"), fn: () => revealPath("tasks", taskId, true) });
      items.push({ label: t("复制工作目录路径"), fn: () => revealPath("tasks", taskId, false) });
      if (runId) items.push({ label: t("复制日志目录路径"), fn: () => revealPath("runs", runId, false) });
      const st = det.dataset.status || "";
      if (st === "failed" || st === "cancelled") items.push({ label: t("↻ 重试任务"), fn: () => retryTask(taskId) });
      // 与详情页同一套说法：失败/取消叫「编辑重试」，其余叫「基于此任务新建」
      items.push({ label: (st === "failed" || st === "cancelled") ? t("✎ 编辑重试") : t("基于此任务新建"),
                   fn: () => newFromTask(taskId) });
      const sTask = ((S.state || {}).tasks || []).find((x) => x.id === taskId);
      if (sTask && sTask.serial && st !== "running" && st !== "queued")
        items.push({ label: t("继续连载（新任务）"), fn: () => continueSerial(taskId) });
      items.push({ label: t("重命名任务"), fn: () => renameTask(taskId) });
      items.push({ label: archivedTaskIds().has(taskId) ? t("取消归档") : t("归档"), fn: () => archiveTask(taskId, !archivedTaskIds().has(taskId)) });
      items.push({ label: t("删除任务"), danger: true, fn: () => deleteTask(taskId) });
    } else if (runId) {
      items.push("-");
      items.push({ label: t("复制日志目录路径"), fn: () => revealPath("runs", runId, false) });
      items.push({ label: t("删除记录"), danger: true, fn: () => deleteRun(runId) });
    }
    openCtxMenu(e.clientX, e.clientY, items);
    return;
    }
    // 文件夹行（ZCode 桌面端式样）：新建任务到该目录 / 打开工作目录 / 复制路径 / 移除。
    // 「其他」是兜底展示（无主运行的聚合），不是真实目录，不弹菜单。
    const dirEl = e.target.closest(".sdir");
    if (!dirEl) return;
    e.preventDefault();
    const dir = dirEl.dataset.dir || "";
    if (!dir || dir === ORPHAN_DIR) return;
    const dirTasks = ((S.state || {}).tasks || []).filter((x) => (x.workdir || "") === dir);
    const ids = dirTasks.map((x) => x.id);
    const items = [
      { label: t("查看文件"), fn: () => toggleFolderFiles(dirEl, dir) },
      { label: t("新建任务到该目录"), fn: () => newTaskInDir(dir) },
      "-",
      { label: t("打开工作目录"), fn: () => { if (ids[0]) revealPath("tasks", ids[0], true); } },
      { label: t("复制工作目录路径"), fn: () => { if (ids[0]) revealPath("tasks", ids[0], false); } },
      "-",
      { label: t("移除该文件夹"), danger: true, fn: () => removeSideDir(ids) },
    ];
    openCtxMenu(e.clientX, e.clientY, items);
  });
  // 文件夹展开/折叠：toggle 事件不冒泡，用捕获监听整个侧栏，实时写回 localStorage
  $("side-tasks").addEventListener("toggle", (e) => {
    if (e.target && e.target.classList && e.target.classList.contains("sdir")) saveOpenDirs();
  }, true);
  document.addEventListener("click", closeCtxMenu, true);
  window.addEventListener("blur", closeCtxMenu);
  window.addEventListener("scroll", closeCtxMenu, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtxMenu(); });
}

/* ------------------------------------------------- 文件夹选择弹框（工作目录「选择…」/点输入框）
 * 服务端目录浏览（/api/browse 仅本机；浏览器安全模型拿不到本机绝对路径，
 * 原生目录选择器只回相对路径，没法用）。进入=点行，选定=行内「选这个」或
 * 底部「使用当前目录」。远端设备 browse 被 403 拒：点输入框不再弹框，继续手输。 */
const pickerSt = { target: "", cwd: "", parent: "", local: null };   // local: null=未知 true=本机 false=远端

function pickerRender(r) {
  pickerSt.cwd = r.path;
  pickerSt.parent = r.parent || "";
  const atDrives = r.path === "此电脑";
  // 服务端只给目录名，这里拼回绝对路径（盘符行本身就是绝对路径）
  const sep = r.path.indexOf("\\") >= 0 ? "\\" : "/";
  const rows = (r.dirs || []).map((d) => {
    const full = /:[\\\/]$/.test(d) ? d : r.path.replace(/[\\/]+$/, "") + sep + d;
    return '<div class="pk-row" data-p="' + esc(full) + '" onclick="pickEnterP(this.dataset.p)">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>' +
      '<span class="pk-name">' + esc(d) + "</span>" +
      '<button class="ghost small" onclick="event.stopPropagation();pickUseP(this.closest(\'.pk-row\').dataset.p)">' +
      esc(t("选这个")) + "</button></div>";
  }).join("");
  const body = $("pk-body");
  if (!body) return;
  body.innerHTML =
    '<div class="pk-tools">' +
    (atDrives ? "" :
      '<button class="ghost small" onclick="pickDrivesP()">' + esc(t("此电脑")) + "</button>" +
      (pickerSt.parent ? '<button class="ghost small" onclick="pickParentP()">' + esc(t("上级")) + "</button>" : "")) +
    '<span class="pk-path" title="' + esc(r.path) + '">' + esc(r.path) + "</span></div>" +
    '<div class="pk-list">' + (rows || '<div class="pk-empty hint">' + esc(t("（没有子目录）")) + "</div>") + "</div>";
}

async function pickerBrowse(p) {
  const body = $("pk-body");
  if (body) body.innerHTML = '<div class="hint">' + esc(t("正在读取目录…")) + "</div>";
  let r = null;
  try { r = await api("/api/browse?path=" + encodeURIComponent(p)); }
  catch (e) {
    if (/仅限本机/.test(e.message || "")) pickerSt.local = false;
    if (body) body.innerHTML = '<div class="msg bad">' + esc(t("读取失败：") + (e.message || e)) + "</div>";
    return false;
  }
  if (!r || r.error) {
    if (r && r.error && /仅限本机/.test(r.error)) pickerSt.local = false;
    if (body) body.innerHTML = '<div class="msg bad">' + esc((r && r.error) || t("读取失败")) + "</div>";
    return false;
  }
  pickerSt.local = true;
  pickerRender(r);
  return true;
}

window.pickFolder = async function (targetId, fromClick) {
  if (fromClick && pickerSt.local === false) return;   // 远端已确认被拒：点击不打扰
  pickerSt.target = targetId;
  const cur = (($(targetId) || {}).value || "").trim();
  openModal(t("选择文件夹"), '<div id="pk-body" class="hint">' + esc(t("正在读取目录…")) + "</div>",
    '<button class="ghost" onclick="closeModal();pkRefocus()">' + esc(t("取消")) + "</button>" +
    '<button class="primary" onclick="pickUseP(pickerCwd())">' + esc(t("使用当前目录")) + "</button>");
  const ok = await pickerBrowse(cur || "__drives__");
  // 点输入框进来的：远端被拒就收框并提示，别让每次点击都吃一个错误弹窗
  if (!ok && fromClick && pickerSt.local === false) {
    closeModal();
    toast(t("目录选择仅限本机使用，请手动输入路径"), true);
  }
};
window.pickEnterP = (p) => pickerBrowse(p);
window.pickParentP = () => pickerBrowse(pickerSt.parent || "__drives__");
window.pickDrivesP = () => pickerBrowse("__drives__");
window.pickerCwd = () => pickerSt.cwd;
// 取消/选定后把焦点还给输入框：点框弹出选择是常态，想手输的人取消后能直接打字
window.pkRefocus = () => { const el = $(pickerSt.target); if (el) el.focus(); };
window.pickUseP = (p) => {
  if (!p || p === "此电脑") { toast(t("请先进入一个具体目录"), true); return; }
  const el = $(pickerSt.target);
  if (el) {
    el.value = p;
    // 程序化赋值不触发原生事件：手动补，让 git 探测等既有联动照常走
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  closeModal();
  window.pkRefocus();
};

/* 右键菜单：打开/复制路径。open=true 由服务端在资源管理器打开目录；false 回传路径复制到剪贴板 */
async function revealPath(kind, id, open) {
  let r;
  try {
    r = await api("/api/" + kind + "/" + encodeURIComponent(id) + "/reveal",
      { method: "POST", body: JSON.stringify({ open: !!open }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); return; }
  if (open) return;
  const txt = r.path || "";
  const done = () => toast(t("已复制：") + txt);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done, () => fallbackCopy(txt, done));
  } else {
    fallbackCopy(txt, done);
  }
}

async function renameTask(id) {
  const task = ((S.state && S.state.tasks) || []).find((x) => x.id === id);
  // 局部变量不能叫 t：会遮蔽 i18n 函数 t()，下面的文案调用会直接 TypeError
  const name = (await uiPrompt(t("重命名任务"), (task && task.title) || "") || "").trim();
  if (!name) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(id) + "/rename",
      { method: "POST", body: JSON.stringify({ title: name }) });
  } catch (e) { toast(t("重命名失败：") + e.message, true); return; }
  poll();
}

async function retryTask(id) {
  try {
    const r = await api("/api/tasks/" + encodeURIComponent(id) + "/retry", { method: "POST" });
    switchTab("runs");
    openRun(r.run_id);
  } catch (e) { toast(t("重试失败：") + e.message, true); return; }
  poll();
}

/* 继续连载：在旧任务基础上新建任务接着写下一批章节。沿用目标/目录/评审设置，
 * 章节号衔接（旧章不动），成书合并仍是完整一本。 */
async function continueSerial(id) {
  let info;
  try {
    info = await api("/api/tasks/" + encodeURIComponent(id) + "/continue-info");
  } catch (e) { toast(t("无法续写：") + e.message, true); return; }
  if (!info || !info.can) {
    toast(t("无法续写：") + ((info && info.reason) || t("当前状态不支持")), true);
    return;
  }
  const tip = t("已写到第 %1 章，将从第 %2 章接着写（同一工作目录，成书合并全本）。续写章数：")
    .replace("%1", info.last_chapter).replace("%2", info.last_chapter + 1);
  const raw = await uiPrompt(tip, String(info.default_chapters || 8));
  const ch = parseInt(raw, 10);
  if (!ch || ch < 1) return;
  let r;
  try {
    r = await api("/api/tasks/" + encodeURIComponent(id) + "/continue",
      { method: "POST", body: JSON.stringify({ chapters: ch }) });
  } catch (e) { toast(t("创建续写任务失败：") + e.message, true); return; }
  switchTab("runs");
  openRun(r.run_id);
  poll();
}
window.continueSerial = continueSerial;

/* 基于此任务新建（通用，不限连载）：把旧任务的类型/目标/目录/评审设置预填进
 * 新建表单，确认或修改后提交——「接着写下一批章节」请用连载任务的「继续连载」，
 * 那里才带章节衔接；这里开的是一个全新任务。 */
function newFromTask(id) {
  // 局部变量不能叫 t：会遮蔽 i18n 函数 t()，下面的文案调用会直接 TypeError
  const tk = ((S.state || {}).tasks || []).find((x) => x.id === id);
  if (!tk) { toast(t("任务不存在或已删除"), true); return; }
  exitSettings();   // 回到新建任务表单
  const flow = flowById(tk.type);
  if (flow && $("f-type").value !== tk.type) {
    $("f-type").value = tk.type;
    onTypeChange();  // 先带出流程默认（引擎区显隐/稿件名/维度），再覆盖为任务值
  }
  $("f-title").value = tk.title || "";
  $("f-goal").value = tk.goal || "";
  // context 里的「附件材料」块是服务端注入的（文件已在工作目录 _attachments/ 里），
  // 不随预填带走：新任务要么重新上传，要么由 _attachments 路径引用
  $("f-context").value = (tk.context || "").split("\n## 附件材料")[0].trimEnd();
  $("f-workdir").value = tk.workdir || "";
  if (tk.workdir) localStorage.setItem("orch.workdir", tk.workdir);
  S.atts = []; renderAttachChips();  // 附件清单不继承：同目录引用已随 context 保留
  queueGitProbe();  // 工作目录变了，重新探测代码版本
  $("f-mode").value = tk.mode === "manual" ? "manual" : "auto";
  renderImplSelects();
  if (tk.implementer) $("f-impl").value = tk.implementer;
  if (flow && flow.engine === "code") {
    $("f-verify").value = tk.verify_command || "";
  } else {
    $("f-manuscript").value = tk.manuscript || "manuscript.md";
    $("f-rounds").value = tk.rounds || 2;
    $("f-threshold").value = (tk.threshold != null ? tk.threshold : 7.0);
    $("f-rubric").value = (tk.rubric || []).join(", ");
    // 连载参数只带批次设置：不带 start_chapter/continues（那是「继续连载」的衔接语义）
    $("f-chapters").value = tk.serial ? tk.serial.chapters : "";
    $("f-words-per-ch").value = tk.serial ? tk.serial.words_per_chapter : "";
    $("f-variants").value = tk.serial && tk.serial.variants ? tk.serial.variants : "";
    const want = new Set(tk.critics || []);
    $("f-critics").querySelectorAll("input").forEach((i) => { i.checked = want.has(i.value); });
    // 同目录同稿件名再开一篇会覆盖旧产出——提醒但不阻止（有意重写也合理）
    const hint = $("f-workdir-hint");
    if (hint) hint.textContent = tk.serial
      ? t("注意：沿用原目录时，提交会覆盖原书的章节与成书文件；续写请用「继续连载」")
      : t("注意：沿用原目录且稿件名相同时，提交会覆盖原稿件");
  }
  // 会话延续是那个任务当时的上下文，不跟着复制
  $("f-resume-agent").value = "";
  const rr = $("row-resume-session");
  if (rr) rr.classList.add("hidden");
  toast(t("已按「%1」预填新任务表单，确认或修改后提交").replace("%1", tk.title || tk.id));
}
window.newFromTask = newFromTask;

/* 文件夹右键「查看文件」：在文件夹行内联展开文件列表（附件式 chip，可折叠），
 * 点开后再点即收起。子目录另开独立 inline 段。 */
function toggleFolderFiles(dirEl, dir) {
  // 查找是否已有 .sdir-files 容器
  const dirbody = dirEl.querySelector(":scope > .dirbody");
  if (!dirbody) return;
  let existing = dirbody.querySelector(":scope > .sdir-files");
  if (existing) {
    existing.remove();
    return;
  }
  // 新建容器并插入 dirbody 末尾
  const wrap = document.createElement("div");
  wrap.className = "sdir-files";
  wrap.innerHTML = '<span class="sdir-files-loading">' + t("加载中…") + "</span>";
  dirbody.appendChild(wrap);
  toggleFolderFilesLoad(wrap, dir);
}

async function toggleFolderFilesLoad(wrap, dir) {
  let d;
  try {
    d = await api("/api/dir/scan?path=" + encodeURIComponent(dir));
  } catch (e) {
    wrap.innerHTML = '<div class="sdir-files-empty">' + esc(t("读取失败")) + "</div>";
    return;
  }
  const icon = (n) => { const ext = n.split(".").pop().toLowerCase();
    return ext === "md" || ext === "txt" ? "#i-book" : "#i-file"; };
  const chips = (d.files || []).map((f) => {
    const href = "/api/dir/file?dir=" + encodeURIComponent(dir.replace(/\\/g, "/")) +
      "&name=" + encodeURIComponent(f.name);
    return '<a class="sdir-chip" href="' + href + '" target="_blank" rel="noopener" title="' +
      esc(f.name + " · " + fmtSize(f.size)) + '">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-paperclip"/></svg>' +
      "<span>" + esc(f.name) + "</span><i>" + fmtSize(f.size) + "</i></a>";
  }).join("");
  // 子目录：可点行，递归展开
  const subdirs = (d.subdirs || []).map((n) => {
    const subPath = dir.replace(/[\\/]+$/, "") + "\\" + n;
    return '<div class="sdir-sub" onclick="toggleFolderSub(this, \'' + esc(subPath) + '\')">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-folder"/></svg><span>' + esc(n) + "</span></div>";
  }).join("");
  wrap.innerHTML = (subdirs ? '<div class="sdir-subs">' + subdirs + "</div>" : "") +
    (chips ? '<div class="sdir-chips">' + chips + "</div>"
      : '<div class="sdir-files-empty">' + t("该目录下暂无文件") + "</div>");
}

window.toggleFolderSub = function (el, dir) {
  const existing = el.parentNode.querySelector(":scope > .sdir-files");
  if (existing) { existing.remove(); return; }
  const wrap = document.createElement("div");
  wrap.className = "sdir-files";
  wrap.innerHTML = '<span class="sdir-files-loading">' + t("加载中…") + "</span>";
  el.after(wrap);
  toggleFolderFilesLoad(wrap, dir);
};

/* 文件夹右键「新建任务到该目录」：只预填工作目录，其余留白（ZCode 式——在该工作区里开新活） */
function newTaskInDir(dir) {
  exitSettings();   // 回到新建任务表单
  $("f-workdir").value = dir;
  localStorage.setItem("orch.workdir", dir);
  queueGitProbe();  // 工作目录变了，重新探测代码版本
}

/* 文件夹右键「移除」：把该目录下的任务整体归档（可从侧栏「显示已归档」开关取消归档找回），
 * 文件与运行记录都不动。运行中/排队的任务归档不了，留原位并提示。 */
async function removeSideDir(ids) {
  const latest = (S.state && S.state.task_latest) || {};
  const free = [], busy = [];
  ids.forEach((id) => {
    const st = (latest[id] || {}).status || "";
    (st === "running" || st === "queued" ? busy : free).push(id);
  });
  if (!free.length) { toast(t("该目录下的任务都在运行/排队中，暂不能移除"), true); return; }
  const ok = await uiConfirm(
    t("把该文件夹下 %1 个任务移出侧栏？文件与运行记录不会删除，可在侧栏「显示已归档」开关找回。")
      .replace("%1", free.length)
    + (busy.length ? t("（另有 %1 个运行中任务保留原位）").replace("%1", busy.length) : ""),
    { ok: t("移除") });
  if (!ok) return;
  for (const id of free) await archiveTask(id, true);
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
    '<span class="n">' + (selN ? t("已选 ") + selN + t(" 条") : t("勾选可批量删除")) + "</span>" +
    (selN ? '<button class="danger small" onclick="deleteSelectedRuns()">删除所选</button>' +
            '<button class="ghost small" onclick="clearRunSel()">取消选择</button>' : "") +
    "</div>";
  box.innerHTML = tools + runs.map((r) => {
    const can = runDeletable(r);
    return '<div class="item" onclick="openRun(\'' + esc(r.id) + '\')">' +
      '<div class="t">' +
      '<input type="checkbox" class="rcheck"' + (S.selRuns[r.id] ? " checked" : "") + (can ? "" : " disabled") +
      ' title="' + (can ? t("勾选以批量删除") : t("运行中的记录不可删除，请先取消")) + '"' +
      ' onclick="event.stopPropagation()" onchange="toggleRunSel(\'' + esc(r.id) + '\', this.checked)">' +
      runKindTag(r.kind) +
      '<span class="name">' + esc(r.title) + "</span>" + statusChip(r.status) +
      '<span class="time">' + esc(r.created_at) + "</span>" +
      '<button class="danger small" title="删除该记录" onclick="event.stopPropagation(); deleteRun(\'' + esc(r.id) + '\')">删除</button></div>' +
      '<div class="desc">' + esc(r.summary || r.error || (r.steps ? r.steps.length + t(" 个步骤") : "")) + "</div></div>";
  }).join("");
}

/* 相对时间（紧凑档，ZCode 桌面端式样）："YYYY-MM-DD HH:MM:SS" → 刚刚/N 分/N 小时/昨天/N 天/MM-DD */
function relTime(s, now) {
  const m = String(s || "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
  if (!m) return String(s || "");
  // 局部变量不能叫 t：会遮蔽全局 i18n 函数 t()，下面 t("刚刚") 直接 TypeError
  const ts = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
  const d = ((now || Date.now()) - ts) / 1000;
  if (d < 60) return t("刚刚");
  if (d < 3600) return Math.max(1, Math.floor(d / 60)) + t(" 分");
  if (d < 86400) return Math.floor(d / 3600) + t(" 小时");
  if (d < 172800) return t("昨天");
  if (d < 7 * 86400) return Math.floor(d / 86400) + t(" 天");
  return m[2] + "-" + m[3];
}

/* 任务行状态字形（ZCode 桌面端式样）：进行中旋转✻ / 完成绿点 / 失败红点 /
 * 取消橙点 / 排队空圈 / 从未运行暗点 */
function staskGlyph(status) {
  if (status === "running") return '<span class="sglyph ast">✻</span>';
  if (status === "done") return '<span class="sglyph ok"></span>';
  if (status === "failed") return '<span class="sglyph bad"></span>';
  if (status === "cancelled") return '<span class="sglyph warn"></span>';
  if (status === "timeout") return '<span class="sglyph tout">⏱</span>';
  if (status === "queued") return '<span class="sglyph ring"></span>';
  return '<span class="sglyph none"></span>';
}

/* 工作目录 → 展示名：取路径末段（Windows/Linux 分隔符都吃）；空或盘符根回落全路径。
 * demo-workdir 与 demo-workdir\qimao-20k 是不同目录，末段名不同，不会误合并。 */
function dirName(p) {
  const parts = String(p || "").split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : (String(p || "") || "—");
}

/* 文件夹折叠态持久化：会话内以 DOM 为准，跨刷新靠 localStorage 记回上次展开的目录集合 */
const SIDE_DIRS_KEY = "orch.sideDirs";
function loadOpenDirs() {
  try {
    const raw = localStorage.getItem(SIDE_DIRS_KEY);
    if (!raw) return null;
    const a = JSON.parse(raw);
    return Array.isArray(a) ? new Set(a) : null;
  } catch (e) { return null; }
}
function saveOpenDirs() {
  try {
    const box = $("side-tasks");
    if (!box) return;
    const open = Array.from(box.querySelectorAll("details.sdir[open]")).map((d) => d.dataset.dir);
    localStorage.setItem(SIDE_DIRS_KEY, JSON.stringify(open));
  } catch (e) { /* 隐私模式忽略 */ }
}

/* 侧栏「任务」树：文件夹（工作目录）→ 任务 → 各 CLI 步骤，三级。
 * 一级文件夹 = 任务 workdir 末段名（无主运行归「其他」垫底）；二级任务行 = 折叠箭头 +
 * 状态字形 + 单行省略标题 + 相对时间；三级步骤行 = 状态点 + 工具名 + 摘要 + 时间，点击打开运行详情。
 * 以任务表为底：每个任务恒有一行，近况取后端全量下发的最近一次运行（task_latest）；
 * run 窗口只用来捞无主运行（管理操作/任务已删）。任务不因别人刷屏而消失。 */
/* 闸门等待提醒（Baton 式徽章 + 可选提示音）：
 * 任务进入「待裁决」（任务分支 isolated）或失败/取消时，侧栏行打徽标，
 * 并（默认）响一声短促提示——转移检测按任务快照比对，同状态不重复响。
 * 开关存 localStorage（orch.sound，默认开），侧栏「任务」标题旁的铃铛切换。 */
function soundEnabled() { return localStorage.getItem("orch.sound") !== "0"; }

function beepAttention() {
  if (!soundEnabled()) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!S._ac) S._ac = new AC();
    const ac = S._ac;
    if (ac.state === "suspended") { ac.resume().catch(() => {}); return; }  // 无手势激活时静默跳过
    const t0 = ac.currentTime;
    [[880, 0], [1320, 0.14]].forEach(([freq, dt]) => {
      const o = ac.createOscillator(), gn = ac.createGain();
      o.type = "sine"; o.frequency.value = freq;
      gn.gain.setValueAtTime(0.0001, t0 + dt);
      gn.gain.exponentialRampToValueAtTime(0.12, t0 + dt + 0.02);
      gn.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.16);
      o.connect(gn); gn.connect(ac.destination);
      o.start(t0 + dt); o.stop(t0 + dt + 0.2);
    });
  } catch (e) { /* 音频不可用则忽略 */ }
}

/* 注意力状态快照：每任务 [git_state, 最近 run 状态]；新进入 待裁决/失败/取消 才响 */
function scanAttention(groups) {
  const cur = {};
  groups.forEach((g) => {
    if (!g.taskId) return;
    cur[g.taskId] = (g.verdict ? "isolated" : "") + "|" + (g.status || "");
  });
  const prev = S._attention || {};
  S._attention = cur;
  const fired = Object.keys(cur).some((id) => {
    const was = prev[id];
    if (was === undefined || was === cur[id]) return false;   // 新任务/无变化不响
    const [vNow, sNow] = cur[id].split("|");
    return vNow === "isolated" || sNow === "failed" || sNow === "cancelled";
  });
  if (fired) beepAttention();
  // 标题提醒：有待裁决/失败任务时加前缀，切走页面也能瞄到
  const attention = Object.values(cur).some((v) => {
    const [vNow, sNow] = v.split("|");
    return vNow === "isolated" || sNow === "failed" || sNow === "cancelled";
  });
  const base = (document.title || "").replace(/^[!?]\s*/, "");
  document.title = (attention ? "! " : "") + base;
}

window.toggleNotifySound = function () {
  const on = soundEnabled();
  localStorage.setItem("orch.sound", on ? "0" : "1");
  paintNotifyToggle();
  if (!on) beepAttention();
};

function paintNotifyToggle() {
  const b = $("btn-notify-toggle");
  if (!b) return;
  const on = soundEnabled();
  b.classList.toggle("off", !on);
  b.title = on ? t("提示音已开（点击关闭）") : t("提示音已关（点击开启）");
}

// 「展开全部」钮：文件夹全开时箭头翻成收起朝向，提示文案跟着换（renderSideTasks 末尾与点击后各同步一次）
function syncSideExpandBtn() {
  const b = $("btn-side-expand");
  if (!b) return;
  const dlist = Array.from($("side-tasks").querySelectorAll("details.sdir"));
  const allOpen = dlist.length > 0 && !dlist.some((d) => !d.open);
  b.classList.toggle("up", allOpen);
  b.title = t(allOpen ? "收起全部" : "展开全部");
  b.setAttribute("aria-label", b.title);
}

// 「显示已归档」开关：点亮状态连同提示文案一起刷（原 HTML 里是写死的「显示已归档」）
function paintArchToggle() {
  const b = $("btn-side-arch");
  if (!b) return;
  b.classList.toggle("on", S.showArchived);
  const tip = t(S.showArchived ? "隐藏已归档" : "显示已归档");
  b.title = tip;
  b.setAttribute("aria-label", tip);
}

function renderSideTasks() {
  const box = $("side-tasks");
  if (!box) return;
  const runs = ((S.state && S.state.runs) || []).slice(0, 40);
  // 「最近任务」面板已移除：归档找回走侧栏——开关打开时已归档任务灰显回原文件夹
  const tasks = ((S.state && S.state.tasks) || [])
    .concat(S.showArchived ? ((S.state && S.state.archived_tasks) || []) : []);
  const latest = (S.state && S.state.task_latest) || {};
  const archivedIds = archivedTaskIds();
  const tasksById = {};
  tasks.forEach((t) => { tasksById[t.id] = t; });
  // 标题、工作目录参与签名：重命名/换目录后侧栏要跟着重排，缺了会顶着旧分组
  const sig = JSON.stringify([
    runs.map((r) => [r.id, r.status, (r.steps || []).length, r.title]),
    tasks.map((t) => [t.id, t.title, t.workdir, t.git_state || ""]),
    Object.keys(latest).map((k) => [k, latest[k].id, latest[k].status, (latest[k].steps || []).length]),
    S.detailRunId, S.detailTaskKey, archivedIds.size, S.showArchived, S.sideQ || "",
  ]);
  if (sig === S.sideSig && box.children.length) return;
  S.sideSig = sig;
  const ORPHAN = ORPHAN_DIR;  // 无主运行（无 task_id / 任务已删）的兜底文件夹
  const groups = [], byKey = {};
  const push = (key, taskId, title, status, time, dir) => {
    if (!byKey[key]) {
      byKey[key] = { key, taskId: taskId || "", title, status: status || "", time: time || "",
        dir: dir || ORPHAN, active: false, runIds: [], steps: [] };
      groups.push(byKey[key]);
    }
    return byKey[key];
  };
  for (const t of tasks) {
    const isArch = archivedIds.has(t.id);
    if (isArch && !S.showArchived) continue;  // 已归档任务默认不上侧栏（开关打开则灰显找回）
    const lr = latest[t.id];
    const g = push(t.id, t.id, t.title || t.id, lr ? lr.status : "",
      lr ? (lr.started_at || lr.created_at) : t.created_at, t.workdir || ORPHAN);
    g.verdict = t.git_state === "isolated";   // 闸门等待：任务分支待裁决
    g.archived = isArch;
    if (lr) {
      g.runIds.push(lr.id);
      if (lr.status === "running") g.active = true;
      (lr.steps || []).forEach((s) => g.steps.push(Object.assign({ runId: lr.id }, s)));
    }
  }
  for (const r of runs) {
    if (r.task_id && (tasksById[r.task_id] || archivedIds.has(r.task_id))) continue;  // 任务行已覆盖/已归档
    const g = push(r.task_id || r.id, r.task_id || "", r.title || r.id, r.status,
      r.started_at || r.created_at, ORPHAN);
    g.runIds.push(r.id);
    if (r.status === "running") g.active = true;
    (r.steps || []).forEach((s) => g.steps.push(Object.assign({ runId: r.id }, s)));
  }
  // 侧栏搜索（Ctrl+K）：按任务标题模糊过滤，文件夹随命中任务自动聚拢/消失
  const q = (S.sideQ || "").trim().toLowerCase();
  const fGroups = q ? groups.filter((g) => (g.title || "").toLowerCase().includes(q)) : groups;
  // 按工作目录聚成文件夹：文件夹时间取组内最近活动（排序用），有 running 任务则标记 active
  const dirs = [], dirMap = {};
  for (const g of fGroups) {
    let d = dirMap[g.dir];
    if (!d) { d = dirMap[g.dir] = { dir: g.dir, groups: [], time: "", active: false }; dirs.push(d); }
    d.groups.push(g);
    if (String(g.time || "") > String(d.time || "")) d.time = g.time;
    if (g.active) d.active = true;
  }
  // 组内任务按活动时间倒序；文件夹：无主运行「其他」恒垫底，其余按最近活动倒序（活跃项目浮上来）
  dirs.forEach((d) => d.groups.sort((a, b) => String(b.time || "").localeCompare(String(a.time || ""))));
  dirs.sort((a, b) => {
    const ao = a.dir === ORPHAN ? 1 : 0, bo = b.dir === ORPHAN ? 1 : 0;
    if (ao !== bo) return ao - bo;
    return String(b.time || "").localeCompare(String(a.time || ""));
  });
  // 首轮默认展开的落点：第一个真实文件夹里的最近任务（无 detailRunId 指向时）
  const firstDir = dirs.find((d) => d.dir !== ORPHAN) || dirs[0];
  const firstKey = firstDir && firstDir.groups[0] ? firstDir.groups[0].key : "";
  // 展开态：会话内已渲染过 → 以 DOM 为准（保留用户点击）；首轮 → 读 localStorage，无则启发式
  const paintedDirs = !!box.querySelector("details.sdir");
  let openDirs;
  if (paintedDirs) {
    openDirs = new Set(Array.from(box.querySelectorAll("details.sdir[open]")).map((d) => d.dataset.dir));
  } else {
    const saved = loadOpenDirs();
    if (saved) openDirs = saved;
    else {
      openDirs = new Set();
      // 有 running 任务的文件夹优先展开；全折叠时至少展开最近活动那一个（真实数据里恒为
      // 非「其他」的首个文件夹；纯无主运行的测试场景下它就是唯一目录，必须展开否则几何断言全 0）
      const act = dirs.filter((d) => d.active);
      (act.length ? act : dirs.slice(0, 1)).forEach((d) => openDirs.add(d.dir));
    }
  }
  const paintedTasks = !!box.querySelector("details.stask");
  const openTasks = paintedTasks
    ? new Set(Array.from(box.querySelectorAll("details.stask[open]")).map((d) => d.dataset.key))
    : new Set();
  // 二级任务行（沿用原步骤行/查看全部/选中高亮逻辑），渲染进所属文件夹的 body 里
  const row = (g) => {
    if (g.active) g.status = "running";
    const isOpen = openTasks.size ? openTasks.has(g.key)
      : (S.detailRunId ? g.runIds.indexOf(S.detailRunId) >= 0 : g.key === firstKey);
    const sel = (g.key === S.detailTaskKey || g.runIds.indexOf(S.detailRunId) >= 0) ? " active" : "";
    const items = g.steps.slice(0, 8).map((s, si) =>
      '<div class="stepx" data-n="' + (Number(s.n) || si + 1) + '" title="' + esc((s.note ? s.note + "：" : "") + (s.summary || "")) +
      '" onclick="sideOpenRun(\'' + esc(s.runId) + '\', ' + (Number(s.n) || si + 1) + ')">' +
      '<span class="sdot ' + esc(s.status || "") + '"></span>' +
      '<span class="sagent">' + esc(s.agent_label || s.agent || s.role || "") + "</span>" +
      '<span class="ssum">' + esc(s.summary || s.note || s.role || "") + "</span>" +
      '<span class="stm">' + esc(String(s.started_at || "").slice(0, 5)) + "</span></div>"
    ).join("");
    // 任务可能被续跑/重试过多次：行内只展示最近一次运行的步骤，更多走任务详情；
    // 计数用后端全量统计（task_stats）——从 40 条 run 窗口里数会因刷屏/窗口滑动算错
    const stats = g.taskId ? ((S.state.task_stats || {})[g.taskId]) : null;
    const runCount = stats ? stats.runs : g.runIds.length;
    const totalSteps = stats ? stats.steps : g.steps.length;
    const more = (runCount > 1 || g.steps.length > 8)
      ? '<div class="smore" onclick="sideOpenTask(\'' + esc(g.key) + '\')">查看全部 ' +
        (runCount > 1 ? runCount + t(" 次运行 · ") : "") + totalSteps + t(" 步</div>") : "";
    // 辅助行只出一条：有「查看全部」就不再叠「暂无步骤/暂无运行记录」
    const aux = more || (items ? "" : (g.runIds[0]
      ? '<div class="smore" onclick="sideOpenTask(\'' + esc(g.key) + '\')">暂无步骤，点击查看</div>'
      : '<div class="smore">暂无运行记录</div>'));
    const body = (items || aux) ? '<div class="steps">' + items + aux + "</div>" : "";
    return '<details class="stask' + sel + (g.archived ? " archived" : "") + '" data-key="' + esc(g.key) + '" data-task="' + esc(g.taskId || "") +
      '" data-run="' + esc(g.runIds[0] || "") + '" data-status="' + esc(g.status || "") + '"' + (isOpen ? " open" : "") + "><summary>" +
      '<svg class="chev"><use href="#i-chevron-r"/></svg>' + staskGlyph(g.status) +
      '<span class="t">' + esc(g.title) + "</span>" +
      (g.archived ? '<span class="sbadge archb">' + t("已归档") + "</span>" : "") +
      (g.verdict ? '<span class="sbadge">' + t("待裁决") + "</span>" : "") +
      '<span class="tm">' + esc(relTime(g.time)) + "</span></summary>" + body + "</details>";
  };
  // 一级文件夹行：折叠箭头 + 目录图标 + 末段名（hover 显全路径）+ 任务计数徽标
  box.innerHTML = dirs.map((d) => {
    const isOrphan = d.dir === ORPHAN;
    const label = isOrphan ? t("其他") : dirName(d.dir);
    const inner = d.groups.map(row).join("");
    const isOpen = openDirs.has(d.dir);
    return '<details class="sdir" data-dir="' + esc(d.dir) + '"' + (isOpen ? " open" : "") + "><summary>" +
      '<svg class="chev"><use href="#i-chevron-r"/></svg>' +
      '<svg class="fico"><use href="#i-folder"/></svg>' +
      '<span class="t"' + (isOrphan ? "" : ' title="' + esc(d.dir) + '"') + ">" + esc(label) + "</span>" +
      '<span class="cnt">' + d.groups.length + "</span></summary>" +
      '<div class="dirbody">' + inner + "</div></details>";
  }).join("") || ('<div class="side-empty">' + (q ? t("无匹配任务") : t("暂无任务")) + "</div>");
  scanAttention(groups);
  paintNotifyToggle();
  syncSideExpandBtn();
  // 底部 pill 上的待裁决徽章：数量跟随全量任务（不受搜索过滤影响）
  const vcount = groups.filter((g) => g.verdict).length;
  const vb = $("prov-side-badge");
  if (vb) {
    vb.textContent = vcount > 9 ? "9+" : String(vcount);
    vb.classList.toggle("hidden", !vcount);
    vb.title = vcount ? vcount + t(" 个任务待裁决") : "";
  }
}

/* 主视图打开详情面板的公共部分：留在任务树，主栏切到运行/任务详情 */
function showDetailInMain() {
  S.tab = "runs";
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-runs"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.remove("active"));
  document.body.classList.remove("settings-mode");
  const title = $("page-title");
  if (title) title.textContent = t("运行详情");
  collapseDrawerIfMobile();
  syncInspectorVis();    // 从设置子页点进运行详情：任务上下文，检查器跟着回来
}

/* 侧栏点子任务：不跳设置页——留在任务树主视图，右侧主栏直接展示运行详情。
 * 带步骤号 n 时，详情渲染完自动定位到该步：滚动 + 高亮 + 展开它的日志。 */
window.sideOpenRun = function (id, n) {
  S.focusStep = Number(n) || 0;
  S.focusDone = false;
  S.detailTaskKey = null;
  if (!S.histJump && typeof histPush === "function") histPush({ m: "main", tab: "run-detail" });
  showDetailInMain();
  openRun(id, n ? "steps" : null);   // 带步骤号：钉住步骤分区，自动选卡不抢
};

/* 侧栏「查看全部 N 步」：任务可能被续跑/重试过多次，步骤分散在多条 run 里。
 * 打开任务级详情：按时间顺序列出该任务全部 run 的全部步骤，run 之间加分隔条。 */
window.sideOpenTask = function (key) {
  S.detailTaskKey = key;
  S.detailRunId = null;
  S.focusStep = 0;
  S.taskSig = "";
  rdTabReset();
  if (!S.histJump && typeof histPush === "function") histPush({ m: "main", tab: "run-detail" });
  showDetailInMain();
  $("run-detail").classList.remove("hidden");
  document.querySelector("#sub-runs .panel:first-child").classList.add("hidden");
  renderTaskDetail();
};
window.openRunInRuns = function (id) { S.focusStep = 0; S.focusDone = true; switchTab("runs"); openRun(id); };

/* 详情页右侧「✎ 编辑重试」开关：失败/取消的任务把「基于此任务新建」换成
 * 「编辑重试」——同一预填行为（newFromTask），标签贴合「改完再跑」的意图；
 * 其余状态维持原标签。两按钮互斥出现，按钮区不因新增功能多占一行。 */
function setEditRetry(taskId, status, taskExists) {
  const failedish = !!taskId && (status === "failed" || status === "cancelled");
  const be = $("btn-editretry");
  if (be) be.classList.toggle("hidden", !failedish);
  const bn = $("btn-newfrom");
  if (bn) bn.classList.toggle("hidden", !taskExists || failedish);
}

/* 任务级详情：聚合该任务所有 run 的步骤。
 * 有任务档案的从 /api/tasks/<id>/runs 拉全量——前端 run 窗口只有最近 40 条，
 * 窗口外的历史会被算丢；无主运行组（管理操作/任务已删）仍用窗口数据。
 * 签名没变就不重画；报告与成品取最新一次 run 的（缓存避免轮询期反复拉取）。 */
function renderTaskDetail() {
  const key = S.detailTaskKey;
  if (!key) return;
  // 任务级按钮只看任务本身（state 里有 serial），不依赖 run 拉取——
  // 没跑过运行的任务也不能残留上一个任务留下的按钮状态
  const tk = ((S.state || {}).tasks || []).find((x) => x.id === key);
  const bc = $("btn-continue");
  if (bc) bc.classList.toggle("hidden",
    !(tk && tk.serial && tk.status !== "running" && tk.status !== "queued"));
  setEditRetry(tk ? tk.id : "", tk ? tk.status : "", !!tk);
  const isTask = ((S.state || {}).tasks || []).some((t2) => t2.id === key);
  if (isTask) {
    fetch("/api/tasks/" + encodeURIComponent(key) + "/runs")
      .then((r) => r.json())
      .then((d) => { if (S.detailTaskKey === key) drawTaskDetail(key, d.runs || []); })
      .catch((e) => { /* 拉取失败静默，等下一轮轮询重试 */ });
    return;
  }
  drawTaskDetail(key, ((S.state || {}).runs || []).filter((r) => (r.task_id || r.id) === key));
}

function drawTaskDetail(key, runs) {
  if (!runs.length) return;
  // 消息数/未消费数也入签名：新指令或被 drain 后重画指挥区；步骤状态入签名：
  // 取消收尾把僵尸步骤落成「已取消」时要立即重画，不等条数变化
  const sig = JSON.stringify(runs.map((r) => [r.id, r.status, (r.steps || []).length,
    (r.steps || []).map((s) => s.status).join(""),
    (r.messages || []).length, (r.messages || []).filter((m) => !m.consumed).length]));
  if (sig === S.taskSig) return;
  S.taskSig = sig;
  const latest = runs[0];                       // runs 新→旧
  const chrono = runs.slice().reverse();        // 展示按时间正序
  const totalSteps = runs.reduce((a, r) => a + (r.steps || []).length, 0);
  const active = runs.some((r) => r.status === "running" || r.status === "queued");
  const st = active ? "running" : latest.status;
  $("rd-title").textContent = latest.title || key;
  const chip = $("rd-status");
  chip.className = "chip " + st;
  chip.textContent = { queued: t("排队中"), running: t("运行中"), done: t("完成"), failed: t("失败"), cancelled: t("已取消") }[st] || st;
  $("btn-cancel").classList.add("hidden");
  const bpt = $("btn-pause");
  if (bpt) bpt.classList.add("hidden");
  $("btn-delete").classList.add("hidden");
  $("btn-retry").classList.toggle("hidden", !(latest.task_id && (latest.status === "failed" || latest.status === "cancelled")));
  setEditRetry(latest.task_id, latest.status,
    !!((S.state || {}).tasks || []).some((x) => x.id === latest.task_id));
  S.lastRun = latest;
  // 指挥区指向最新的活跃运行（无则隐藏整个指挥区，避免终态任务误导用户以为指令还能生效）
  const activeRun = runs.find((r) => r.status === "queued" || r.status === "running");
  const bp2 = $("btn-pause");
  if (bp2 && activeRun) {
    bp2.classList.toggle("hidden", false);
    bp2.textContent = activeRun.paused ? t("继续执行") : t("暂停");
  }
  renderDirector(activeRun || null, !!activeRun);
  renderHive(activeRun || latest);   // 终态回看最近一次运行的蜂巢
  const sum = (f) => runs.reduce((a, r) => a + (Number(r[f]) || 0), 0);
  $("rd-meta").innerHTML =
    '<span class="stat">运行 <b>' + runs.length + "</b> 次</span>" +
    '<span class="stat">步骤 <b>' + totalSteps + "</b> 步</span>" +
    '<span class="stat">成本 <b>$' + sum("cost_usd").toFixed(3) + "</b></span>" +
    '<span class="stat">tokens <b>' + sum("tokens") + "</b></span>" +
    (latest.error ? '<span class="stat err">' + errTag(latest.error) + esc(latest.error.slice(0, 200)) + "</span>" : "");
  $("rd-plan").classList.add("hidden");
  const statusTxt = { queued: t("排队中"), running: t("运行中"), done: t("完成"), failed: t("失败"), cancelled: t("已取消") };
  let html = "";
  chrono.forEach((r, i) => {
    html += '<div class="run-sep"><span class="rs-i">第 ' + (i + 1) + "/" + chrono.length + ' 次运行</span>' +
      '<span class="rs-t">' + esc(String(r.created_at || "").slice(5, 16)) + "</span>" +
      '<span class="chip ' + esc(r.status || "") + '">' + (statusTxt[r.status] || r.status || "") + "</span>" +
      (r.error ? '<span class="rs-err" title="' + esc(r.error.slice(0, 200)) + '">' + esc(r.error.slice(0, 60)) + "</span>" : "") +
      "</div>";
    html += (r.steps || []).map((s) =>
      '<div class="step" onclick="toggleLog(\'' + esc(r.id) + "', '" + esc(s.log) + '\')" title="' + esc(s.note || "") + '">' +
      '<span class="n">' + String(s.n).padStart(2, "0") + "</span>" +
      '<span class="role">' + esc(s.role) + "</span>" +
      '<span class="who">' + esc(s.agent_label || s.agent) + "</span>" +
      '<span class="sum">' + esc((s.note ? "◆ " + s.note + " — " : "") + (s.summary || "")) + "</span>" +
      '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span>" +
      statusChip(s.status) + "</div>"
    ).join("");
  });
  $("rd-steps").innerHTML = html || '<div class="empty">尚无步骤</div>';
  // 轮询重画不收起已打开的日志框：运行中日志靠 2.5s live 刷新持续更新，
  // 收起+清 currentLog 会让刚点开的输出被下一轮轮询弹掉
  if (currentLog && !stepsMatch(runs, currentLog)) { $("rd-log").classList.add("hidden"); currentLog = null; stopLogLive(); }
  // 报告：最新一次 run 的（缓存，轮询重画不重复拉取）
  const drawReport = async () => {
    if (S.taskReport && S.taskReport.key === key && S.taskReport.runId === latest.id) {
      $("rd-report").innerHTML = S.taskReport.html; return;
    }
    if (latest.status === "done" || latest.report) {
      try {
        const md = await fetch("/api/runs/" + encodeURIComponent(latest.id) + "/report").then((r) => r.text());
        const html2 = md2html(md);
        S.taskReport = { key, runId: latest.id, html: html2 };
        if (S.detailTaskKey === key) $("rd-report").innerHTML = html2;
      } catch (e) { /* 报告拉取失败静默，保留旧内容 */ }
    } else {
      $("rd-report").innerHTML = '<div class="hint">运行结束后生成</div>';
    }
  };
  drawReport();
  loadArtifacts(latest.id);   // 成品 TAB 数据源（渲染进检查器，主栏不展示）
  // 成品文件已移至右侧检查器「成品文件」分区：主栏详情不再重复展示
  // 任务级详情：git 隔离面板挂在最新一次 run 上（分支/变更快照以它为准）
  const tk = ((S.state || {}).tasks || []).find((x) => x.id === key);
  S.lastRunTask = tk || null;
  renderGitPanel(latest, tk);
  renderBiblePanel(tk);
  const actSteps = ((activeRun || latest).steps || []);
  rdTabsSync({
    running: active, status: st, gitState: (tk || {}).git_state || "",
    steps: totalSteps,
    runningCount: actSteps.filter((x) => x.status === "running").length,
    hasResult: latest.status === "done" || !!latest.report,
  });
}

async function deleteRun(id) {
  if (!await uiConfirm(t("删除该运行记录（含全部日志与报告）？不可恢复。"), { ok: t("删除"), danger: true })) return;
  try {
    await api("/api/runs/" + encodeURIComponent(id) + "/delete", { method: "POST" });
  } catch (e) { toast(t("删除失败：") + e.message, true); return; }
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
  if (!await uiConfirm(t("删除所选 ") + ids.length + t(" 条运行记录（含全部日志与报告）？不可恢复。"), { ok: t("删除"), danger: true })) return;
  let r;
  try {
    r = await api("/api/runs/delete", { method: "POST", body: JSON.stringify({ ids }) });
  } catch (e) { toast(t("批量删除失败：") + e.message, true); return; }
  S.selRuns = {};
  if (S.detailRunId && ids.indexOf(S.detailRunId) >= 0) closeRun();
  if (r && r.message) toast(r.message, true);
  poll();
}

async function clearRuns() {
  if (!await uiConfirm(t("清除全部运行记录（含日志与报告）？运行中的记录会保留，需先取消。不可恢复。"), { ok: t("清除"), danger: true })) return;
  let r;
  try {
    r = await api("/api/runs/clear", { method: "POST" });
  } catch (e) { toast(t("清除失败：") + e.message, true); return; }
  S.selRuns = {};
  if (S.detailRunId) closeRun();
  if (r && r.skipped) toast(t("已清除 ") + r.count + t(" 条；另有 ") + r.skipped + t(" 条运行中的记录已保留（请先取消再清除）。"));
  poll();
}

async function openRun(id, pinTab) {
  S.detailRunId = id;
  S.detailTaskKey = null;
  rdTabReset(pinTab || null);   // sideOpenRun 带步骤号时钉住步骤分区
  document.querySelector("#sub-runs .panel:first-child").classList.add("hidden");
  $("run-detail").classList.remove("hidden");
  renderRunDetail();
}

function closeRun() {
  S.detailRunId = null;
  S.detailTaskKey = null;
  S.taskSig = "";
  S.focusStep = 0;
  rdTabReset();
  stopLogLive();
  stopHiveTick();
  $("run-detail").classList.add("hidden");
  if (document.body.classList.contains("settings-mode")) {
    document.querySelector("#sub-runs .panel:first-child").classList.remove("hidden");
  } else {
    // 从主视图（侧栏点子任务）进来的详情：返回直接回任务页，不露出运行列表
    document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-tasks"));
    document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === "tasks"));
    const title = $("page-title");
    if (title) title.textContent = tabTitle("tasks");
  }
}

/* 侧栏点了具体子任务后：在运行详情里标出那一步。轮询会不停重画步骤区，
 * 所以高亮是持久的（.focus），而滚动 + 闪烁 + 展开日志只做一次（.flash）。 */
function applyStepFocus() {
  const n = S.focusStep;
  if (!n) return;
  const el = document.querySelector('#rd-steps .step[data-n="' + n + '"]');
  if (!el) return;
  el.classList.add("focus");
  if (S.focusDone) return;
  S.focusDone = true;
  el.classList.add("flash");
  try { el.scrollIntoView({ block: "center", behavior: "smooth" }); }
  catch (e) { el.scrollIntoView(); }
  if (el.dataset.log) toggleLog(S.detailRunId, el.dataset.log);
  setTimeout(() => el.classList.remove("flash"), 1800);
}

/* ---------------- 详情页标签分区：蜂巢（实时）/ 步骤 / 成果 / 版本 / 圣经 ----------------
 * 主栏详情从一根长条改成五个分区：实时监控、历史步骤、交付成果、代码版本、故事圣经。
 * 分区内容的 hidden 语义保持不变（没数据整块收起）；标签页只切外层 .rd-pane，
 * 测试/代码对 #rd-hive、#rd-git 等 hidden 的判断不受影响。
 * 自动选卡只在「详情目标 + run 状态 + git 裁决态」签名变化时触发一次——
 * 轮询重画不抢用户手选的分区；侧栏点具体步骤（sideOpenRun）钉住步骤分区。 */
function rdTabAvail() {
  return {
    hive: !$("rd-hive").classList.contains("hidden"),
    steps: true,
    result: true,
    git: !$("rd-git").classList.contains("hidden"),
    bible: !$("rd-bible").classList.contains("hidden"),
  };
}

function applyRdTabs() {
  const avail = rdTabAvail();
  if (!S.rdTab || !avail[S.rdTab]) {
    S.rdTab = ["hive", "steps", "result", "git", "bible"].find((k) => avail[k]) || "steps";
  }
  document.querySelectorAll("#rd-tabs .rd-tab").forEach((b) => {
    b.classList.toggle("hidden", !avail[b.dataset.tab]);
    b.classList.toggle("active", b.dataset.tab === S.rdTab);
  });
  document.querySelectorAll("#run-detail .rd-pane").forEach((p) =>
    p.classList.toggle("hidden", p.dataset.pane !== S.rdTab));
}

/* 徽章：蜂巢=在岗数（运行中）、步骤=总步数、版本=待裁决、成果=文件数（loadArtifacts 里刷） */
function rdTabBadges() {
  const c = S._rdCtx || {};
  const set = (tab, text, cls) => {
    const el = document.querySelector('#rd-tabs .rd-tab[data-tab="' + tab + '"] .rd-badge');
    if (!el) return;
    el.textContent = text;
    el.className = "rd-badge" + (text ? (cls ? " " + cls : "") : " hidden");
  };
  set("hive", c.runningCount ? "● " + c.runningCount : "");
  set("steps", c.steps ? String(c.steps) : "");
  set("git", c.gitState === "isolated" ? t("待裁决") : "", "verdict");
}

/* ctx 可省略：省略时只刷新可用性/徽章/pane（renderHive/renderGitPanel 等收尾调用）。
 * 带 ctx 时先做自动选卡判断，再落徽章。 */
function rdTabsSync(ctx) {
  if (ctx) S._rdCtx = ctx;
  if (ctx && !S.rdTabPin) {
    const sig = (S.detailTaskKey || S.detailRunId || "") + "|" + (ctx.status || "") +
      "|" + (ctx.gitState || "") + "|" + (ctx.running ? 1 : 0);
    if (S.rdTabSig !== sig) {
      S.rdTabSig = sig;
      const avail = rdTabAvail();
      S.rdTab = ctx.running ? (avail.hive ? "hive" : "steps")
        : (ctx.gitState === "isolated" && avail.git ? "git"
          : (ctx.hasResult ? "result" : "steps"));
    }
  }
  applyRdTabs();
  rdTabBadges();
}

/* 换一个详情目标时清空选卡状态：下一次渲染按新目标自动落位 */
function rdTabReset(pin) {
  S.rdTab = pin || null;
  S.rdTabSig = "";
  S.rdTabPin = !!pin;
  S._rdCtx = {};
}

async function renderRunDetail() {
  const id = S.detailRunId;
  if (S.detailTaskKey) { renderTaskDetail(); return; }   // 任务级详情（轮询也会走到这里刷新）
  if (!id) return;
  let run;
  try { run = (await api("/api/runs/" + encodeURIComponent(id))).run; }
  catch (e) { return; }
  if (!run) return;
  $("rd-title").textContent = run.title;
  const chip = $("rd-status");
  chip.className = "chip " + run.status;
  chip.textContent = { queued: t("排队中"), running: t("运行中"), done: t("完成"), failed: t("失败"), cancelled: t("已取消") }[run.status] || run.status;
  const active = run.status === "queued" || run.status === "running";
  $("btn-cancel").classList.toggle("hidden", !active);
  $("btn-delete").classList.toggle("hidden", active);
  $("btn-retry").classList.toggle("hidden", !(run.task_id && (run.status === "failed" || run.status === "cancelled")));
  const rcTask = ((S.state || {}).tasks || []).find((x) => x.id === run.task_id);
  $("btn-continue").classList.toggle("hidden",
    !(rcTask && rcTask.serial && run.status !== "running" && run.status !== "queued"));
  setEditRetry(run.task_id, run.status, !!rcTask);
  S.lastRun = run;
  const bp = $("btn-pause");
  if (bp) { bp.classList.toggle("hidden", !active);
    bp.textContent = run.paused ? t("继续执行") : t("暂停"); }
  renderDirector(run, active);
  renderHive(run);
  $("rd-meta").innerHTML =
    '<span class="stat">创建 <b>' + esc(run.created_at) + "</b></span>" +
    '<span class="stat">成本 <b>$' + Number(run.cost_usd || 0).toFixed(3) + "</b></span>" +
    '<span class="stat">tokens <b>' + (run.tokens || 0) + "</b></span>" +
    (run.mode ? '<span class="stat">模式 <b>' + (run.mode === "auto" ? t("智能") : t("手动")) + "</b></span>" : "") +
    (run.error ? '<span class="stat err">' + errTag(run.error) + esc(run.error.slice(0, 200)) + "</span>" : "");
  renderPlan(run);
  $("rd-steps").innerHTML = (run.steps || []).map((s) =>
    '<div class="step" data-n="' + Number(s.n) + '" data-log="' + esc(s.log || "") +
    '" onclick="toggleLog(\'' + esc(run.id) + "', '" + esc(s.log) + '\')" title="' + esc(s.note || "") + '">' +
    '<span class="n">' + String(s.n).padStart(2, "0") + "</span>" +
    '<span class="role">' + esc(s.role) + "</span>" +
    '<span class="who">' + esc(s.agent_label || s.agent) + "</span>" +
    '<span class="sum">' + esc((s.note ? "◆ " + s.note + " — " : "") + (s.summary || "")) + "</span>" +
    '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span>" +
    statusChip(s.status) + "</div>"
  ).join("") || '<div class="empty">尚无步骤</div>';
  applyStepFocus();
  // 报告
  if (run.status === "done" || run.report) {
    const md = await fetch("/api/runs/" + encodeURIComponent(id) + "/report").then((r) => r.text());
    $("rd-report").innerHTML = md2html(md);
    loadArtifacts(id);
  } else {
    $("rd-report").innerHTML = '<div class="hint">运行结束后生成</div>';
  }
  // 成品文件已移至右侧检查器（loadArtifacts 不再在主栏调用）
  S.lastRunTask = rcTask || null;
  renderGitPanel(run, rcTask);
  renderBiblePanel(rcTask);
  rdTabsSync({
    running: active, status: run.status, gitState: (rcTask || {}).git_state || "",
    steps: (run.steps || []).length,
    runningCount: (run.steps || []).filter((x) => x.status === "running").length,
    hasResult: run.status === "done" || !!run.report,
  });
}

/* 代码版本隔离面板：run 检出任务分支 tutti/<id> 后，产物提交在该分支上、
 * 用户工作区已切回原分支。这里展示本 run 的变更快照，并按任务裁决状态
 * （isolated 待裁决 / merged 已合并 / discarded 已丢弃）给出合并·丢弃出口。
 * 分支是「每任务一条」，裁决按钮在任务级生效；run 无 git 字段（未指定代码版本）时整块隐藏。 */
const GIT_STATUS_LABEL = { M: "改", A: "新", D: "删", R: "移", C: "新", "?": "新" };
/* 状态徽章文案：新数据后端已归一成单字符；旧 run 快照里可能还存着 "??"/"MM"
 * 这类原始 XY，取首字符兜底查表，查不到再退原文，绝不把 "??" 当文案渲染。 */
function gitStatusLabel(s) {
  const c = String(s == null ? "" : s);
  const key = GIT_STATUS_LABEL[c] != null ? c : c[0];
  return t(GIT_STATUS_LABEL[key] || key) || key;
}
const GIT_STATE_CHIP = {
  isolated: ["isolated", "待裁决"],
  merged: ["merged", "已合并"],
  discarded: ["discarded", "已丢弃"],
};

/* 故事圣经面板：查看/编辑工作目录里的 story-bible.md（每章起草与评审
 * 自动注入的「本书宪法」）。运行中锁定编辑——圣经是提示词前缀的一部分，
 * 运行中改动会打碎供应商前缀缓存，也让本轮各章看到的设定不一致。
 * 只对连载任务渲染：注入点全在连载引擎，非连载任务配了也不生效，
 * 代码类任务更不该看到小说面板。 */
async function renderBiblePanel(task) {
  const box = $("rd-bible");
  if (!box || !(task || {}).id) { if (box) box.classList.add("hidden"); rdTabsSync(); return; }
  if (!task.serial) { box.classList.add("hidden"); rdTabsSync(); return; }
  let d;
  try { d = await api("/api/tasks/" + encodeURIComponent(task.id) + "/bible"); }
  catch (e) { box.classList.add("hidden"); rdTabsSync(); return; }
  if ((S.detailTaskKey || S.detailRunId) !== (S._bibleKey || (S.detailTaskKey || S.detailRunId))) { /* noop */ }
  const running = task.status === "running" || task.status === "queued";
  const text = d.text || "";
  const editing = S._bibleEdit === task.id;
  let html = '<div class="bible-head"><svg class="ico" aria-hidden="true"><use href="#i-book"/></svg>' +
    '<span class="sec-title">' + t("故事圣经") + '</span>' +
    '<span class="hint">' + esc(t("story-bible.md · 人物/世界观/伏笔台账，每章起草与评审自动注入")) + "</span>" +
    '<span class="flex1"></span>';
  if (editing) {
    html += '<button class="primary" onclick="bibleSave(\'' + esc(task.id) + '\')">' + t("保存") + "</button>" +
      '<button class="ghost" onclick="bibleCancel()">' + t("取消") + "</button>";
  } else {
    html += '<button class="ghost" onclick="bibleEdit(\'' + esc(task.id) + '\')"' +
      (running ? ' disabled title="' + esc(t("运行中不能修改")) + '"' : "") + ">" +
      '<svg class="ico" aria-hidden="true"><use href="#i-gear"/></svg>' + t("编辑") + "</button>";
  }
  html += "</div>";
  if (editing) {
    html += '<textarea id="bible-editor" class="bible-editor" rows="12" spellcheck="false">' +
      esc(text) + "</textarea>";
  } else if (text) {
    const head = text.split(/\n/).slice(0, 14).join("\n");
    const more = text.split(/\n/).length > 14;
    html += '<pre class="bible-view">' + esc(head) + (more ? "\n…" : "") + "</pre>";
  } else {
    html += '<div class="hint">' + esc(t("尚未创建。点「编辑」写下人物卡/世界观/伏笔台账，下一轮起草即刻生效。")) + "</div>";
  }
  box.classList.remove("hidden");
  box.innerHTML = html;
  rdTabsSync();   // 圣经面板显隐决定「圣经」标签可用性
}

window.bibleEdit = function (taskId) { S._bibleEdit = taskId; renderBiblePanel(S.lastRunTask || findTask(taskId)); };
window.bibleCancel = function () { S._bibleEdit = null; renderBiblePanel(S.lastRunTask || findTask(S._bibleKey || "")); };
window.bibleSave = async function (taskId) {
  const ta = $("bible-editor");
  if (!ta) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(taskId) + "/bible", {
      method: "POST", body: JSON.stringify({ text: ta.value }) });
    S._bibleEdit = null;
    toast(t("故事圣经已保存"));
    renderBiblePanel(S.lastRunTask || findTask(taskId));
  } catch (e) { toast(t("保存失败：") + e.message, true); }
};

function findTask(taskId) {
  return ((S.state || {}).tasks || []).find((x) => x.id === taskId);
}

function renderGitPanel(run, task) {
  const box = $("rd-git");
  if (!box) return;
  const g = (run || {}).git;
  if (!g || !g.branch) { box.classList.add("hidden"); box.innerHTML = ""; rdTabsSync(); return; }
  const tid = (task || {}).id || "";
  const state = (task || {}).git_state || "";
  const files = ((run || {}).changes || {}).files || [];
  const chip = GIT_STATE_CHIP[state] || ["muted", "—"];
  let html =
    '<div class="git-head"><svg class="ico" aria-hidden="true"><use href="#i-git-branch"></use></svg>' +
    '<span class="sec-title">' + t("代码版本隔离") + '</span>' +
    '<code class="git-branch" title="' + esc(t("任务分支：产物提交在此，原分支未受影响")) + '">' + esc(g.branch) + "</code>" +
    '<span class="chip ' + chip[0] + '">' + t(chip[1]) + "</span>" +
    "</div>";
  html += '<div class="git-meta">' +
    "<span>" + esc(t("基线")) + " <b>" + esc(g.rev || "-") + "</b> → " + esc(g.from_branch || "-") + "</span>" +
    (g.commit ? "<span>" + esc(t("分支提交")) + " <b>" + esc(g.commit) + "</b></span>" : "") +
    "</div>";
  if (g.restore_error) {
    html += '<div class="hint warn">' + esc(t("收尾出错：")) + esc(g.restore_error) + "</div>";
  }
  html += '<div class="git-files">' + (files.length
    ? files.slice(0, 40).map((f) =>
        '<button class="gf" data-p="' + esc(f.path) + '" data-rid="' + esc((run || {}).id || "") +
        '" ondblclick="fileDiffPopup(this.dataset.p, this.dataset.rid)" title="' +
        esc(t("双击弹窗查看该文件的变更")) + '"><i class="gs ' + (f.status === "M" ? "m" : f.status === "D" ? "d" : "n") + '">' +
        esc(gitStatusLabel(f.status)) + "</i>" + esc(f.path) + "</button>").join("") +
      (files.length > 40 ? '<span class="gm">+' + (files.length - 40) + " " + esc(t("个文件")) + "</span>" : "")
    : '<span class="hint">' + esc(t("本次运行没有产生工作区变更。")) + "</span>") +
    "</div>";
  if (state === "isolated" && tid) {
    html += '<div class="git-actions">' +
      '<button class="primary" onclick="gitMerge(\'' + esc(tid) + "')\">" +
      '<svg class="ico" aria-hidden="true"><use href="#i-check"></use></svg>' + t("合并回原分支") + "</button>" +
      '<button class="danger ghost" onclick="gitDiscard(\'' + esc(tid) + "')\">" +
      '<svg class="ico" aria-hidden="true"><use href="#i-x"></use></svg>' + t("丢弃分支") + "</button>" +
      '<span class="hint">' + esc(t("合并＝采纳产物回原分支；丢弃＝删除任务分支（不可恢复）。")) + "</span></div>";
  }
  box.classList.remove("hidden");
  box.innerHTML = html;
  rdTabsSync();   // 版本面板显隐决定「版本」标签可用性
}

async function _gitVerdictDone() {
  poll();
  if (S.detailTaskKey) { S.taskSig = ""; renderTaskDetail(); }
  else if (S.detailRunId) renderRunDetail();
  refreshInspector(true);   // 检查器开着时同步裁决结果
}

window.gitMerge = async function (taskId) {
  if (!await uiConfirm(t("把任务分支的产物合并回原分支？"), { ok: t("合并") })) return;
  try {
    const d = await api("/api/tasks/" + encodeURIComponent(taskId) + "/git-merge", { method: "POST" });
    toast(t("已合并：") + Number(d.commits || 0) + " " + t("个提交 → ") + (d.merged_into || ""));
    if (d.restore_error) toast(t("合并成功，但你之前的未提交改动没能自动还原，") + d.restore_error, true);
    await _gitVerdictDone();
  } catch (e) { toast(t("合并失败：") + e.message, true); }
};

window.gitDiscard = async function (taskId) {
  if (!await uiConfirm(t("丢弃任务分支？该分支上的产物将永久删除，不可恢复。"), { ok: t("丢弃"), danger: true })) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(taskId) + "/git-discard", { method: "POST", body: JSON.stringify({ confirm: true }) });
    toast(t("已丢弃任务分支"));
    await _gitVerdictDone();
  } catch (e) { toast(t("丢弃失败：") + e.message, true); }
};

/* ------------------------------------------------- 任务检查器（右缘停靠列）
 * 参考桌面端的 Git 工具 + 进程浮窗：点任务树的任务行，右缘滑出停靠列，
 * 五张卡——Git 工具（分支 + +/- 统计 + 变更清单 + 合并/丢弃裁决）、进度
 * （x/y 步 + 状态点清单）、运行统计、成品文件快捷区、迷你指挥区。
 * 数据走 /api/tasks/<id>/side（KB 级、可轮询、无 diff 文本）；主视图不动，
 * 步骤点进主栏详情的旧路径原样保留。 */
let inspRunId = null;   // 迷你指挥区指向的活跃 run

/* 检查器准入：任务「跑过」才有得看——task_latest 全量下发，没有记录或最新 run
 * 还在排队（刚建的新任务/重试排在后面）都算没跑过，右缘不滑出。 */
function inspEligible(key) {
  const lr = ((S.state || {}).task_latest || {})[key];
  return !!lr && lr.status !== "queued";
}

window.openInspector = function (key) {
  if (!key) return;
  if (!((S.state || {}).tasks || []).some((x) => x.id === key)) return;   // 无主运行不进检查器
  if (!inspEligible(key)) return;   // 还没跑过的新任务不滑出：Git/进度/成品全空，弹出来只有噪声
  if (S.inspKey !== key) { S.inspSig = ""; inspRunId = null; }
  S.inspKey = key;
  S.inspAt = 0;                      // 换任务立刻拉一次
  document.body.classList.add("inspector-open");
  $("inspector").classList.remove("hidden");
  syncInspBtn();
  try { localStorage.setItem("orch.inspector", key); } catch (e) { /* 存储不可用则不记忆 */ }
  refreshInspector(true);
};

window.closeInspector = function () {
  document.body.classList.remove("inspector-open");
  $("inspector").classList.add("hidden");
  S.inspKey = null;
  S.inspData = null;
  S.inspSig = "";
  syncInspBtn();
  try { localStorage.removeItem("orch.inspector"); } catch (e) { /* ignore */ }
};

/* 顶栏「任务详情」开合按钮：开着显示 ‹（收起）、关着显示 ›（展开）——箭头方向
 * 靠 CSS 变量穿透 use 影子树翻转，这里只同步悬浮提示。 */
function syncInspBtn() {
  const b = $("btn-insp");
  if (!b) return;
  const tip = document.body.classList.contains("inspector-open") ? t("收起任务详情") : t("打开任务详情");
  b.title = tip;
  b.setAttribute("aria-label", tip);
}

/* 手动开合检查器。没选中过任务时兜底拿最近一个跑过的（树序即最新在前），
 * 一个都没有才提示；选中过但已失效（被删）同样落到兜底。 */
window.toggleInspector = function () {
  if (document.body.classList.contains("inspector-open")) { closeInspector(); return; }
  const tasks = ((S.state || {}).tasks || []);
  const ok = (k) => !!k && tasks.some((x) => x.id === k) && inspEligible(k);
  const key = ok(S.inspKey) ? S.inspKey : (tasks.find((x) => ok(x.id)) || {}).id;
  if (!key) { toast(t("还没有可展示的任务详情"), true); return; }
  openInspector(key);
};

/* 检查器只属于任务上下文：设置子页（用量统计/皮肤等）与「新建任务表单」一律收起，
 * 但不丢选中——回到运行详情时原任务自动滑回；重新点任务才换内容 */
function syncInspectorVis() {
  const insp = $("inspector");
  if (!insp) return;
  // 兜底只认「run 记录全部没了」（清理/删除）：排空档不动已开着的检查器，
  // 否则自动续跑的 run 间隙（done→queued→running）会闪关
  const runsGone = S.state && S.inspKey && !((S.state.task_latest || {})[S.inspKey]);
  // 主视图下正在看「要完成什么？」新建表单：不是任务上下文，不自动滑出。
  // （本函数只在模式切换时调用，不在轮询里——用户在表单页手动点树里的任务，
  //   openInspector 直接开，不会被这里关掉）
  const onComposer = !document.body.classList.contains("settings-mode") &&
    !$("sub-tasks").classList.contains("hidden");
  if (document.body.classList.contains("settings-mode") || onComposer || !S.inspKey || runsGone) {
    document.body.classList.remove("inspector-open");
    insp.classList.add("hidden");
    return;                      // 隐藏态不轮询，refreshInspector 的闸门在 body 类上
  }
  document.body.classList.add("inspector-open");
  insp.classList.remove("hidden");
  refreshInspector(true);
}

/* 刷新后恢复检查器：回到上次打开的任务（任务已被删则保持关闭）。
 * 首帧 state 到达时任务清单才可信，所以挂在这里而不是 DOMContentLoaded。 */
function restoreInspector() {
  if (document.body.classList.contains("inspector-open")) return;
  let key = "";
  try { key = localStorage.getItem("orch.inspector") || ""; } catch (e) { /* ignore */ }
  if (key && ((S.state || {}).tasks || []).some((x) => x.id === key)) {
    S.inspKey = key;             // 只记选中，显不显示交给 syncInspectorVis——
    syncInspectorVis();          // 刷新时停在设置子页或新建表单就不滑出
  }
}

/* 节流拉取：SSE 唤醒的 render() 每次都调，2s 内只发一次请求 */
async function refreshInspector(force) {
  if (!document.body.classList.contains("inspector-open") || !S.inspKey) return;
  const now = Date.now();
  if (!force && now - (S.inspAt || 0) < 2000) return;
  S.inspAt = now;
  const key = S.inspKey;
  let d;
  try { d = await api("/api/tasks/" + encodeURIComponent(key) + "/side"); }
  catch (e) { return; }              // 拉取失败静默，等下一轮节流重试
  if (!S.inspKey || S.inspKey !== key) return;   // 期间已切走/收起：过期响应不落盘
  S.inspData = d;
  drawInspector();
}

/* 签名没变不重画；指挥区输入中跳过整帧重绘——整块重绘会把正在打的字弹掉 */
function drawInspector() {
  const d = S.inspData;
  if (!d || !d.task) return;
  const ta = $("insp-msg-input");
  if (ta && document.activeElement === ta) return;
  const sig = JSON.stringify([
    S.inspKey, d.task.status, d.task.git_state, d.run && d.run.status,
    d.progress, (d.steps || []).map((s) => [s.n, s.status, s.summary]),
    d.changes.count, d.changes.add_total, d.changes.del_total,
    (d.changes.files || []).map((f) => [f.path, f.add, f.del]),
    (d.files || []).map((f) => f.name),
  ]);
  if (sig === S.inspSig) return;
  S.inspSig = sig;
  $("insp-title").textContent = d.task.title || d.task.id;
  $("insp-title").title = d.task.workdir || "";
  const runId = (d.run || {}).id || "";
  inspRunId = (d.run && (d.run.status === "running" || d.run.status === "queued")) ? runId : null;

  /* —— Git 工具卡 —— */
  const chipEl = $("insp-git-chip");
  const g = d.git || {};
  const main = $("insp-git-main");
  const boxDiff = $("insp-diff");
  if (!g.branch) {
    // 没有任务分支：区分「没启用」和「启用了但检出失败」——后者要给出错原因，
    // 不能误报成"该任务未指定代码版本"
    const revChosen = (d.task || {}).git_rev || "";
    const lastErr = ((d.run || {}).error || "");
    if (revChosen) {
      chipEl.className = "chip failed"; chipEl.textContent = t("检出失败"); chipEl.classList.remove("hidden");
      main.innerHTML = '<div class="insp-branch"><code>tutti/' + esc(d.task.id || "") +
        '（' + esc(t("未创建")) + '）</code></div>' +
        (lastErr ? '<div class="hint warn">' + esc(lastErr) + "</div>" : "") +
        '<span class="insp-hint">' + esc(t("处理后点「重试任务」，运行会重新检出任务分支。")) + "</span>";
    } else {
      // 没指定代码版本：没有任务分支，卡里只留一句说明
      chipEl.classList.add("hidden");
      main.innerHTML = '<span class="insp-hint">' + esc(t("未启用代码版本隔离（该任务未指定代码版本）。")) + "</span>";
    }
  } else {
    const chip = GIT_STATE_CHIP[g.state] || (g.state ? [g.state, g.state] : null);
    if (chip) { chipEl.className = "chip " + chip[0]; chipEl.textContent = t(chip[1]); chipEl.classList.remove("hidden"); }
    else chipEl.classList.add("hidden");
    let html = '<div class="insp-branch">' +
      '<code title="' + esc(t("任务分支：产物提交在此，原分支未受影响")) + '">' + esc(g.branch) + "</code>";
    // +/- 行级统计：运行中实时、结束后快照；旧 run 没有统计字段显示 —
    const addT = d.changes.add_total, delT = d.changes.del_total;
    if (addT != null && delT != null) {
      html += '<span class="insp-plus">+' + addT + "</span><span class=\"insp-minus\">-" + delT + "</span>";
    }
    html += "</div>";
    html += '<div class="git-meta"><span>' + esc(t("基线")) + " <b>" + esc(g.rev || "-") + "</b> → " + esc(g.from_branch || "-") + "</span>" +
      (g.commit ? "<span>" + esc(t("分支提交")) + " <b>" + esc(g.commit) + "</b></span>" : "") + "</div>";
    if (g.restore_error) html += '<div class="hint warn">' + esc(t("收尾出错：")) + esc(g.restore_error) + "</div>";
    const cfs = d.changes.files || [];
    html += '<div class="insp-changes">' + (cfs.length
      ? cfs.map((f) =>
          '<button class="insp-cf" onclick="inspDiffFile(this.dataset.p)" ondblclick="fileDiffPopup(this.dataset.p)" data-p="' + esc(f.path) + '" title="' +
          esc(t("单击：卡内展开 diff；双击：弹窗查看")) + '">' +
          '<i class="gs ' + (f.status === "M" ? "m" : f.status === "D" ? "d" : "n") + '">' + esc(gitStatusLabel(f.status)) + "</i>" +
          '<span class="p">' + esc(f.path) + "</span>" +
          (f.add ? '<span class="add">+' + f.add + "</span>" : "") +
          (f.del ? '<span class="del">-' + f.del + "</span>" : "") + "</button>").join("")
      : '<span class="insp-hint">' + esc(t("暂无工作区变更。")) + "</span>") + "</div>";
    const canVerdict = g.state === "isolated" && d.task.status !== "running" && d.task.status !== "queued";
    html += '<div class="insp-actions"' + (canVerdict ? "" : ' style="display:none"') + ">" +
      '<button class="primary small" onclick="gitMerge(\'' + esc(d.task.id) + "')\">" +
      '<svg class="ico" aria-hidden="true"><use href="#i-check"></use></svg>' + t("合并回原分支") + "</button>" +
      '<button class="danger ghost small" onclick="gitDiscard(\'' + esc(d.task.id) + "')\">" +
      '<svg class="ico" aria-hidden="true"><use href="#i-x"></use></svg>' + t("丢弃分支") + "</button></div>";
    main.innerHTML = html;
    if (boxDiff.dataset.run !== runId) { boxDiff.classList.add("hidden"); boxDiff.dataset.run = runId; }
  }
  /* —— 徽标 + 自动选 TAB（用户手点后同状态内不再抢）——
   * 规则：运行/排队 → 进度（盯着跑）；待裁决 → Git（该裁断了）；完成有产物 → 成品；其余 → 进度。
   * 状态没变就不动用户当前所在分区。 */
  const badgeGit = $("insp-badge-git"), badgeProg = $("insp-badge-progress"), badgeFiles = $("insp-badge-files");
  if (g.state === "isolated") { badgeGit.textContent = t("待裁决"); badgeGit.className = "insp-badge verdict"; badgeGit.classList.remove("hidden"); }
  else badgeGit.classList.add("hidden");
  badgeProg.textContent = ((d.progress || {}).done || 0) + "/" + ((d.progress || {}).total || 0);
  badgeProg.classList.toggle("hidden", !((d.progress || {}).total || 0));
  if (badgeFiles) {
    badgeFiles.textContent = String((d.files || []).length);
    badgeFiles.classList.toggle("hidden", !(d.files || []).length);
  }
  const autoSig = S.inspKey + "|" + ((d.run || {}).status || "none") + "|" + g.state;
  if (S.inspAutoSig !== autoSig) {
    S.inspAutoSig = autoSig;
    const active = d.run && (d.run.status === "running" || d.run.status === "queued");
    S.inspTab = active ? "progress"
      : (g.state === "isolated" ? "git"
      : ((d.files || []).length ? "files" : "progress"));
  }
  applyInspectorTab();
  loadArtifacts(runId);   // 成品 TAB：工作目录新产出（含实时预览刷新）

  /* —— 进度分区：环形进度 + 步骤清单 —— */
  const pr = d.progress || {};
  $("insp-progress-n").textContent = (pr.done || 0) + "/" + (pr.total || 0);
  // 环只改 attribute 不重建 DOM：CSS transition 接管，推进时平滑生长
  const ring = $("insp-ring");
  if (ring) {
    const C = 226.2;
    const pct = (pr.total || 0) ? (pr.done || 0) / pr.total : 0;
    ring.style.strokeDashoffset = String(C * (1 - Math.min(1, Math.max(0, pct))));
  }
  $("insp-steps").innerHTML = (d.steps || []).map((s) =>
    '<button class="insp-step ' + esc(s.status || "") + '" onclick="sideOpenRun(\'' + esc(runId) + "', " + (Number(s.n) || 0) + ')" title="' +
    esc(s.summary || "") + '">' +
    '<span class="sdot ' + esc(s.status || "") + '"></span>' +
    '<span class="role">' + esc(s.role || "") + "</span>" +
    '<span class="sum">' + esc(s.summary || s.note || "") + "</span>" +
    '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span></button>"
  ).join("") || '<span class="insp-hint">' + esc(t("尚无步骤")) + "</span>";

  /* —— 统计条（常驻，不占分区；件数在成品 TAB 徽标上，不重复）—— */
  const st = d.stats || {};
  $("insp-stats").innerHTML =
    "<span>" + esc(t("运行")) + " <b>" + (st.runs || 0) + "</b></span>" +
    "<span>" + esc(t("步骤")) + " <b>" + (st.steps || 0) + "</b></span>" +
    "<span>" + esc(t("成本")) + " <b>$" + (Number(st.cost_usd) || 0).toFixed(3) + "</b></span>" +
    "<span>tokens <b>" + (st.tokens || 0) + "</b></span>";

  /* —— 成品文件分区（主栏详情不再展示，这里是唯一入口）——
   * 与 Git 变更清单同款行样式：扩展名徽标 + 文件名 + 右侧大小；点开弹内容 */
  const arts = d.files || [];
  const artBox = $("insp-artifacts");
  if (artBox) artBox.innerHTML = arts.length
    ? arts.map((f) =>
        '<a class="file-chip" href="/api/runs/' + encodeURIComponent(runId) + "/file?name=" +
        encodeURIComponent(f.name) + '" target="_blank" rel="noopener" title="' +
        esc(f.name + " · " + fmtSize(f.size)) + '" onclick="artPopup(\'' + esc(runId) + "', '" +
        esc(f.name) + "', " + (Number(f.size) || 0) + ');return false">' +
        '<i class="fx">' + esc(_fpExt(f.name).slice(0, 4) || "file") + "</i>" +
        '<span class="p">' + esc(f.name) + "</span><i>" + fmtSize(f.size) + "</i></a>").join("")
    : '<span class="insp-hint">' + esc(t("本次运行没有在工作目录里产出新文件。")) + "</span>";

  /* —— 迷你指挥区：仅活跃 run 显示 —— */
  $("insp-card-direct").classList.toggle("hidden", !inspRunId);
}

/* 变更清单点开单文件 diff：从该 run 的 changes.diff 按文件头切片（零后端开销）。
 * 运行中的 run 还没落 changes 快照，提示结束后可看。
 * 双击同一行 → fileDiffPopup 弹大窗（行着色、可复制），小窗仍在原位。 */
function _sliceDiff(diff, path) {
  const lines = String(diff || "").split("\n");
  const out = [];
  let take = false;
  for (const ln of lines) {
    if (ln.indexOf("diff --git ") === 0) take = ln.indexOf(" b/" + path) >= 0;
    if (take) out.push(ln);
  }
  return out;
}

window.inspDiffFile = async function (path) {
  const box = $("insp-diff");
  const runId = (S.inspData || {}).run && S.inspData.run.id;
  if (!box || !runId) return;
  if (!box.classList.contains("hidden") && box.dataset.path === path) { box.classList.add("hidden"); return; }
  let d;
  try { d = await api("/api/runs/" + encodeURIComponent(runId)); }
  catch (e) { toast(t("diff 读取失败：") + e.message, true); return; }
  const diff = ((d.run || {}).changes || {}).diff || "";
  box.dataset.path = path;
  box.textContent = _sliceDiff(diff, path).join("\n") ||
    t("暂无该文件的已保存 diff（运行结束后生成变更快照，或该运行没有保存变更内容）。");
  box.classList.remove("hidden");
};

/* ------------------------------------------------- 文件内容弹窗（单例）
 * 两个入口共用：Git 变更行双击 → 该文件的 diff（行着色）；成品文件点开 →
 * 内容预览（md 渲染 / 代码等宽 / 图片直显 / 二进制给下载）。
 * z-index 240：高于主 modal(200)、低于 #ask(250)——弹窗上再弹确认不遮眼。 */
let _fpUrls = [];   // 图片预览的 objectURL，关窗统一回收

function filePopIsOpen() {
  const el = document.getElementById("file-pop");
  return !!el && !el.classList.contains("hidden");
}

window.filePopClose = function () {
  const el = document.getElementById("file-pop");
  if (!el) return;
  el.classList.add("hidden");
  el.querySelector(".fp-body").innerHTML = "";
  el.querySelector(".fp-acts").innerHTML = "";
  _fpUrls.forEach((u) => { try { URL.revokeObjectURL(u); } catch (e) { /* 回收失败忽略 */ } });
  _fpUrls = [];
};

function _fpEnsure() {
  let el = document.getElementById("file-pop");
  if (el) return el;
  el = document.createElement("div");
  el.id = "file-pop";
  el.innerHTML = '<div class="fp-panel" role="dialog" aria-modal="true">' +
    '<div class="fp-head"><code class="fp-title"></code><span class="fp-sub"></span><span class="fp-flex"></span>' +
    '<span class="fp-acts"></span>' +
    '<button class="ghost fp-x" title="' + esc(t("关闭")) + ' (Esc)"><svg class="ico" aria-hidden="true"><use href="#i-x"/></svg></button></div>' +
    '<div class="fp-body"></div></div>';
  document.body.appendChild(el);
  el.addEventListener("click", (e) => { if (e.target === el) window.filePopClose(); });
  el.querySelector(".fp-x").addEventListener("click", window.filePopClose);
  return el;
}

/* 开窗骨架：标题 + 副信息（±行数/大小）+ 动作按钮，正文先放加载态 */
function _fpOpen(title, sub, acts) {
  const el = _fpEnsure();
  el.querySelector(".fp-title").textContent = title;
  el.querySelector(".fp-sub").innerHTML = sub || "";
  el.querySelector(".fp-acts").innerHTML = acts || "";
  el.querySelector(".fp-body").innerHTML = '<div class="fp-hint">' + esc(t("加载中…")) + "</div>";
  el.classList.remove("hidden");
  return el;
}

function _fpSetBody(html) {
  const el = _fpEnsure();
  el.querySelector(".fp-body").innerHTML = html;
}

window.copyFPText = function () {
  const body = document.querySelector("#file-pop .fp-body");
  if (!body) return;
  try { navigator.clipboard.writeText(body.innerText || ""); toast(t("已复制")); } catch (e) { /* 剪贴板不可用则忽略 */ }
};

/* diff 行着色：+/− 上色、@@ 小节、头部落灰；"--- "/"+++ " 先判，避免内容行误染。
 * no 传行序号：开着「显示行号」时前置一个行号槽（diff 的行号 = 文件切片内序号，
 * 与 hunk 头的源行号不严格对应，仅作定位参考）。 */
function _fpDiffLine(ln, no) {
  const cls = (ln.indexOf("diff --git ") === 0 || ln.indexOf("index ") === 0 ||
    ln.indexOf("--- ") === 0 || ln.indexOf("+++ ") === 0 || ln.indexOf("old mode") === 0 ||
    ln.indexOf("new mode") === 0 || ln.indexOf("new file") === 0 || ln.indexOf("deleted file") === 0 ||
    ln.indexOf("rename ") === 0 || ln.indexOf("similarity ") === 0 || ln.indexOf("\\ No newline") === 0)
    ? "fp-meta" : (ln.indexOf("@@") === 0) ? "fp-hunk"
    : (ln.indexOf("+") === 0) ? "fp-add" : (ln.indexOf("-") === 0) ? "fp-del" : "";
  const noHtml = (codeLineNum() && no) ? '<span class="fp-no">' + no + "</span>" : "";
  return '<div class="fp-ln ' + cls + '">' + noHtml + esc(ln.length ? ln : " ") + "</div>";
}

/* Git 变更行双击：弹窗展示该文件的完整 diff。runId 可显式传（主栏 Git 面板），
 * 不传则按 检查器 → 主栏详情 → 最近 run 的顺序兜底。 */
window.fileDiffPopup = async function (path, runId) {
  runId = runId || ((S.inspData || {}).run || {}).id || S.detailRunId || (S.lastRun || {}).id || "";
  if (!runId) return;
  let sub = "";
  for (const fs of [((S.inspData || {}).changes || {}).files, ((S.lastRun || {}).changes || {}).files]) {
    const f = (fs || []).find((x) => x.path === path);
    if (f) { sub = '<span class="insp-plus">+' + (f.add || 0) + '</span><span class="insp-minus">-' + (f.del || 0) + "</span>"; break; }
  }
  _fpOpen(path, sub, '<button class="ghost" onclick="copyFPText()">' + esc(t("复制")) + "</button>");
  let d;
  try { d = await api("/api/runs/" + encodeURIComponent(runId)); }
  catch (e) { _fpSetBody('<div class="fp-hint">' + esc(t("diff 读取失败：") + e.message) + "</div>"); return; }
  if (!filePopIsOpen()) return;   // 读取期间被 Esc 关掉：别把内容又糊上去
  const lines = _sliceDiff(((d.run || {}).changes || {}).diff || "", path);
  _fpSetBody(lines.length
    ? '<div class="fp-diff">' + lines.map((ln, idx) => _fpDiffLine(ln, idx + 1)).join("") + "</div>"
    : '<div class="fp-hint">' + esc(t("暂无该文件的已保存 diff（运行结束后生成变更快照，或该运行没有保存变更内容）。")) + "</div>");
};

const FP_IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg", "avif"]);
const FP_TXT = new Set(["md", "txt", "log", "csv", "yml", "yaml", "ini", "toml", "json",
  "py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "svg", "bat", "sh", "ps1",
  "c", "h", "cpp", "hpp", "java", "go", "rs", "xml", "sql", "mqtt", "proto"]);

function _fpExt(name) { return (String(name).split(".").pop() || "").toLowerCase(); }

/* 成品文件弹窗预览：按扩展名分流——图片 blob 直显、md 渲染、
 * 文本/代码等宽原文、json 美化、其余二进制只给下载。 */
window.artPopup = async function (runId, name, size) {
  const url = "/api/runs/" + encodeURIComponent(runId) + "/file?name=" + encodeURIComponent(name);
  const ext = _fpExt(name);
  _fpOpen(name, size ? "<i>" + esc(fmtSize(size)) + "</i>" : "",
    '<a class="ghost" href="' + url + '" download="' + esc(name) + '">' + esc(t("下载")) + "</a>");
  const el = _fpEnsure();
  try {
    if (FP_IMG.has(ext)) {
      const r = await fetch(url);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const u = URL.createObjectURL(await r.blob());
      _fpUrls.push(u);
      if (!filePopIsOpen()) return;
      _fpSetBody('<div class="fp-img"><img alt="' + esc(name) + '" src="' + u + '"></div>');
      return;
    }
    if (FP_TXT.has(ext)) {
      const r = await fetch(url);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const text = await r.text();
      if (!filePopIsOpen()) return;
      let body;
      if (ext === "md") {
        body = '<div class="fp-md prev-body">' + md2html(text) + "</div>";
      } else if (ext === "json") {
        let pretty = text;
        try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* 坏 json 原样展示 */ }
        body = '<div class="fp-code">' + codeBlockHTML(pretty) + "</div>";
      } else {
        body = '<div class="fp-code">' + codeBlockHTML(text) + "</div>";
      }
      _fpSetBody(body);
      el.querySelector(".fp-acts").insertAdjacentHTML("afterbegin",
        '<button class="ghost" onclick="copyFPText()">' + esc(t("复制")) + "</button>");
      return;
    }
    _fpSetBody('<div class="fp-hint">' + esc(t("二进制文件不预览，可下载查看。")) + "</div>");
  } catch (e) {
    _fpSetBody('<div class="fp-hint">' + esc(t("内容读取失败：") + (e.message || e)) + "</div>");
  }
};

/* 迷你指挥：与详情页指挥区同一个消息端点，只是不带附件 */
window.inspSend = async function () {
  if (!inspRunId) return;
  const ta = $("insp-msg-input");
  const text = (ta.value || "").trim();
  if (!text) return;
  const btn = $("insp-send-btn");
  btn.disabled = true;
  try {
    await api("/api/runs/" + encodeURIComponent(inspRunId) + "/messages", {
      method: "POST", body: JSON.stringify({ text, attachments: [] }),
    });
    ta.value = "";
    toast(t("指令已入箱，将在下一个步骤下达"));
  } catch (e) { toast(t("发送失败：") + e.message, true); }
  finally { btn.disabled = false; }
};

function applyInspectorTab() {
  const tab = S.inspTab || "git";
  document.querySelectorAll("#insp-tabs .insp-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  for (const name of ["git", "progress", "files"]) {
    const pane = $("insp-pane-" + name);
    if (pane) pane.classList.toggle("hidden", name !== tab);
  }
}

function bindInspector() {
  $("insp-close").addEventListener("click", closeInspector);
  $("insp-mask").addEventListener("click", closeInspector);
  syncInspBtn();   // 顶栏开合按钮的初始悬浮提示（关着 → 打开任务详情）
  $("insp-full").addEventListener("click", () => { if (S.inspKey) sideOpenTask(S.inspKey); });
  $("insp-send-btn").addEventListener("click", window.inspSend);
  $("insp-msg-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); window.inspSend(); }
  });
  // 分区 TAB：手点即切；任务状态变化时的自动选中见 drawInspector（autoSig 没变不抢）
  $("insp-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".insp-tab");
    if (!b) return;
    S.inspTab = b.dataset.tab;
    applyInspectorTab();
  });
  // 任务树点任务行（summary）：展开行为保留，同时右缘滑出检查器；
  // 文件夹行 / 步骤行 / 右键菜单不受影响
  $("side-tasks").addEventListener("click", (e) => {
    if (!e.target.closest("summary")) return;
    const det = e.target.closest(".stask");
    if (det && det.dataset.task) openInspector(det.dataset.task);
  });
}

/* 成品文件：run 开始后工作目录里新产生/修改过的文件，点击直接查看内容 */
function fmtSize(n) {
  n = Number(n) || 0;
  if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

/* 成品文件行（检查器「成品文件」与主栏「成果」分区共用一套标记）：
 * fc-row：文件行 + （md/txt 的）内联预览按钮同行排布，避免按钮独占一行参差不齐 */
function artifactsChips(runId, files) {
  return files.map((f) => {
    const isMd = /\.md$/i.test(f.name) || /\.txt$/i.test(f.name);
    return '<span class="fc-row">' +
      '<a class="file-chip" href="/api/runs/' + encodeURIComponent(runId) + "/file?name=" +
      encodeURIComponent(f.name) + '" target="_blank" rel="noopener" ' +
      'title="' + esc(f.name + " · " + fmtSize(f.size)) + '" onclick="artPopup(\'' + esc(runId) + "', '" +
      esc(f.name) + "', " + (Number(f.size) || 0) + ');return false">' +
      '<i class="fx">' + esc(_fpExt(f.name).slice(0, 4) || "file") + "</i>" +
      '<span class="p">' + esc(f.name) + "</span><i>" + fmtSize(f.size) + "</i></a>" +
      (isMd ? '<a class="file-chip prev" title="' + esc(t("在面板内预览")) +
        '" onclick="previewArtifact(\'' + esc(runId) + '\', \'' + esc(f.name) + '\')">' +
        '<svg class="ico" aria-hidden="true"><use href="#i-book"/></svg>' + t("预览") + "</a>" : "") +
      "</span>";
  }).join("");
}

async function loadArtifacts(runId) {
  // 双入口同源渲染：检查器「成品文件」TAB + 主栏详情「成果」分区。
  // #rd-preview 是静态节点（在成果分区里），不再内嵌在检查器标记内——
  // 检查器轮询重画不会再把正在看的预览冲掉。
  let d;
  try { d = await api("/api/runs/" + encodeURIComponent(runId) + "/files"); }
  catch (e) { return; }
  // 用户可能已经切到别的详情：过期响应不落盘（检查器当前的 run 同样有效）
  const inspRun = (S.inspData && S.inspData.run && S.inspData.run.id) || "";
  if (S.detailRunId !== runId && inspRun !== runId &&
      !(S.detailTaskKey && S.lastRun && S.lastRun.id === runId)) return;
  const files = d.files || [];
  const box = $("insp-artifacts");
  const mainBox = $("rd-arts");
  // 成果分区徽章：文件数（终态才有产出，运行中不计）
  const fb = document.querySelector('#rd-tabs .rd-tab[data-tab="result"] .rd-badge');
  if (fb) { fb.textContent = files.length ? String(files.length) : "";
    fb.className = "rd-badge" + (files.length ? "" : " hidden"); }
  if (!files.length) {
    if (box) box.innerHTML = '<span class="insp-hint">' + esc(t("本次运行没有在工作目录里产出新文件。")) + "</span>";
    if (mainBox) { mainBox.classList.add("hidden"); mainBox.innerHTML = ""; }
    previewStop();
    return;
  }
  const head = '<div class="files-head"><span class="sec-title">' + t("成品文件") + '</span>' +
    '<span class="wd" title="' + esc(t("点击复制")) + '" onclick="copyText(this.textContent)">' + esc(d.workdir) + "</span></div>";
  const chips = artifactsChips(runId, files);
  if (box) box.innerHTML = head + '<div class="file-chips">' + chips + "</div>";
  if (mainBox) { mainBox.classList.remove("hidden");
    mainBox.innerHTML = head + '<div class="file-chips">' + chips + "</div>"; }
  // 正在预览的文件还活着就原地刷新（运行中轮询 → 稿子越写越长的实时视图）
  if (S.preview && S.preview.runId === runId && S.preview.name) {
    if (!files.some((f) => f.name === S.preview.name)) previewStop();
    else previewRender(runId, S.preview.name, true);
  }
}

/* 面板内预览：拉取成品 markdown/txt 用 md2html 渲染；轮询自动刷新，切换详情时停 */
let _previewSeq = 0;

async function previewRender(runId, name, quiet) {
  const box = $("rd-preview");
  if (!box) return;
  const seq = ++_previewSeq;
  try {
    const r = await fetch("/api/runs/" + encodeURIComponent(runId) + "/file?name=" + encodeURIComponent(name));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const text = await r.text();
    if (seq !== _previewSeq) return;               // 过期响应（已切到别的预览）
    box.classList.remove("hidden");
    box.innerHTML = '<div class="prev-head"><svg class="ico" aria-hidden="true"><use href="#i-book"/></svg>' +
      '<b>' + esc(name) + "</b>" +
      (S.lastRun && (S.lastRun.status === "running") ? '<span class="chip running">' + t("实时") + "</span>" : "") +
      '<button class="ghost" onclick="quoteSelection()" title="' + esc(t("把选中的正文作为引用填进下方指挥框")) + '">' +
      esc(t("引用选中")) + "</button>" +
      '<button class="ghost" onclick="previewStop()">' + esc(t("收起")) + "</button></div>" +
      '<div class="prev-body">' + md2html(text) + "</div>";
  } catch (e) {
    if (!quiet) { box.classList.remove("hidden"); box.innerHTML = '<div class="hint">' + esc(t("预览失败：")) + esc(String(e.message || e)) + "</div>"; }
  }
}

window.previewArtifact = function (runId, name) {
  if (S.preview && S.preview.runId === runId && S.preview.name === name) { previewStop(); return; }
  S.preview = { runId, name };
  previewRender(runId, name);
};

function previewStop() {
  S.preview = null;
  _previewSeq++;
  const box = $("rd-preview");
  if (box) { box.classList.add("hidden"); box.innerHTML = ""; }
}

/* 选中即指挥（dev-3.0 式）：在预览正文里选中一段，点「引用选中」→
 * 该片段以引用块填进运行中指挥框，作者一句话即可定点纠偏。
 * 没有选中时提示先选。目标 run：详情区当前的 run（S.detailRunId）。 */
window.quoteSelection = function () {
  const body = document.querySelector("#rd-preview .prev-body");
  const sel = window.getSelection && window.getSelection();
  const text = sel && !sel.isCollapsed && body && body.contains(sel.anchorNode)
    ? String(sel).trim() : "";
  const ta = (S.inspKey && $("insp-msg-input")) || $("rd-msg-input");
  if (!text) { toast(t("请先在预览正文里选中一段文字"), true); return; }
  if (!ta) { toast(t("找不到指挥输入框"), true); return; }
  const quote = text.length > 400 ? text.slice(0, 400) + "…" : text;
  const ref = S.preview && S.preview.name ? "（" + S.preview.name + "）" : "";
  const NL = String.fromCharCode(10);
  ta.value = (ta.value ? ta.value + NL : "") +
    t("针对 ") + ref + t(" 中这段：") + NL + "> " +
    quote.replace(/\n+/g, NL + "> ") + NL + t("我的意见：");
  ta.focus();
  toast(t("已引用进指挥框，补一句意见即可下达"));
};

window.copyText = function (t) {
  try { navigator.clipboard.writeText(t); } catch (e) { /* 剪贴板不可用则忽略 */ }
};

let currentLog = null;

/* 当前打开的日志是否仍属于这批步骤（重画后日志路径消失说明步骤被重建，才收起） */
function stepsMatch(runs, rel) {
  return (runs || []).some((r) => (r.steps || []).some((s) => s.log === rel));
}

function renderPlan(run) {
  const box = $("rd-plan");
  const plan = run.plan;
  if (!plan || !plan.steps || !plan.steps.length) { box.classList.add("hidden"); return; }
  const route = run.route || {};
  const routeHtml = Object.keys(route).length
    ? '<div class="route">路由依据：' +
      Object.keys(route).map((k) => "<b>" + esc(k) + "</b> " + esc(route[k])).join(t("　|　")) + "</div>"
    : "";
  box.classList.remove("hidden");
  box.innerHTML = '<h3 class="sec-title">编排计划 <span class="tag">来源 ' + esc(plan.source || "?") + "</span></h3>" +
    '<div class="steps">' + plan.steps.map((s, i) =>
      '<div class="step plan"><span class="n">' + String(i + 1).padStart(2, "0") + "</span>" +
      '<span class="role">' + esc(s.title || "") + "</span>" +
      '<span class="sum">' + esc(s.detail || "") + "</span></div>").join("") + "</div>" + routeHtml;
}
/* 日志框顶部状态条：运行中 → ● 实时（呼吸点 + 最后刷新时间）；结束 → 灰字已结束。
 * 没有它，用户分不清"日志在刷但 CLI 暂无输出"和"刷新坏了"——两者长得一模一样。 */
function logLiveBadge(live) {
  const dot = $("rd-log-live"), at = $("rd-log-at");
  if (!dot) return;
  const hm = () => new Date().toTimeString().slice(0, 8);
  if (live) {
    dot.className = "log-live on";
    dot.textContent = t("● 实时");
    at.textContent = t("刚刚更新 ") + hm();
  } else {
    dot.className = "log-live";
    dot.textContent = t("已结束");
    at.textContent = hm();
  }
}

async function toggleLog(runId, rel) {
  const box = $("rd-log"), pre = $("rd-log-text");
  if (currentLog === rel && !box.classList.contains("hidden")) {
    box.classList.add("hidden"); currentLog = null; stopLogLive(); return;
  }
  try {
    const r = await api("/api/runs/" + encodeURIComponent(runId) + "/log?step=" + encodeURIComponent(rel));
    pre.textContent = r.log || t("（等待输出…）");
    box.classList.remove("hidden");
    currentLog = rel;
    pre.scrollTop = pre.scrollHeight;   // 打开即看最新输出
    // 输出是流式写入的：运行中点开就持续刷新；步骤结束（后端带 step_status）即停
    const live = r.step_status === "running";
    logLiveBadge(live);
    stopLogLive();
    if (!live) return;
    S.logLive = setInterval(async () => {
      if (!currentLog || currentLog !== rel) return stopLogLive();
      try {
        const rr = await api("/api/runs/" + encodeURIComponent(runId) + "/log?step=" + encodeURIComponent(rel));
        if (currentLog === rel) {
          // 贴底跟随：用户滚到底部附近才自动滚到最新输出，回看历史不打扰
          const stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48;
          pre.textContent = rr.log || t("（等待输出…）");
          if (stick) pre.scrollTop = pre.scrollHeight;
          logLiveBadge(true);   // 每帧都打时间戳：内容没变也能证明通道活着
          if (rr.step_status && rr.step_status !== "running") {
            logLiveBadge(false);
            stopLogLive();      // 步骤终态：拉完最后一帧就停
          }
        }
      } catch (e) { /* 网络抖动保留上一帧 */ }
    }, 2500);
  } catch (e) { pre.textContent = t("日志读取失败: ") + e.message; box.classList.remove("hidden"); logLiveBadge(false); }
}
/* ---------------- 蜂巢工作台：每个智能体一格，点开即看实时输出 ---------------- */
let hiveTimer = null;                    // 卡片尾巴轮询表
let hiveClock = null;                    // running 秒表（1s 走动）
const hiveTails = {};                    // "runid|rel" -> 最近一行输出缓存

function stopHiveTick() { if (hiveTimer) { clearInterval(hiveTimer); hiveTimer = null; } }

/* run.steps -> 阶段泳道流水线：规划→起草→评审→修订→打磨→合成，先后关系一眼可见；
 * 运行中蜜蜂摆动+秒表走动；轨道光点流向下一阶段；点格即看实时输出。 */
function hiveStage(role) {
  const r = String(role || "");
  if (/^(plan|outline)/.test(r)) return t("规划");
  if (/^draft/.test(r)) return t("起草");
  if (/critique/.test(r) || r === "review") return t("评审");
  if (/^(revise|fix)/.test(r)) return t("修订");
  if (/^polish/.test(r)) return t("打磨");
  if (/^(merge|verify)/.test(r)) return t("合成");
  if (/^implement/.test(r)) return t("实现");
  return t("执行");
}

/* running 卡片秒表：started_at HH:MM:SS → 走动计时（跨天/解析失败回退原值） */
function hiveElapsed(started) {
  const m = /^(\d{2}):(\d{2}):(\d{2})$/.exec(String(started || ""));
  if (!m) return String(started || "");
  const now = new Date();
  const secs = Math.max(0, now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds()
    - (+m[1] * 3600 + +m[2] * 60 + +m[3]));
  const h = Math.floor(secs / 3600), mn = Math.floor((secs % 3600) / 60), sc = secs % 60;
  return (h ? h + ":" + String(mn).padStart(2, "0") : String(mn)) + ":" + String(sc).padStart(2, "0");
}

/* 尾巴去噪：codex 遥测 WARN/半行 JSON 不是"它在干什么"；JSONL agent_message 是正文 */
function hiveCleanLine(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = String(lines[i] || "").trim();
    if (!l) continue;
    if (/warn\b|telemetry|metrics|failed to flush|mcp/i.test(l)) continue;
    if (l.startsWith("{") && /"type"\s*:/.test(l)) {
      try {
        const ev = JSON.parse(l);
        if (ev.type === "item.completed" && ev.item && ev.item.type === "agent_message" && ev.item.text)
          return String(ev.item.text).slice(-160);
      } catch (e) { /* 半行 JSON 等下一轮 */ }
      continue;
    }
    return l.slice(-160);
  }
  return "";
}

window.renderHive = function (run) {
  const box = $("rd-hive");
  if (!box) return;
  const steps = (run && run.steps) || [];
  if (!run || !steps.length) { box.classList.add("hidden"); stopHiveTick(); return; }
  box.classList.remove("hidden");
  // 按步骤首次出现顺序分泳道（流水线天然有序）
  const lanes = [], byStage = {};
  steps.forEach((s) => {
    const stg = hiveStage(s.role);
    if (!byStage[stg]) { byStage[stg] = []; lanes.push(stg); }
    byStage[stg].push(s);
  });
  const running = steps.filter((s) => s.status === "running");
  const sub = $("rd-hive-sub");
  if (sub) sub.textContent = running.length
    ? t("在岗") + " " + running.length + " / " + steps.length : t("全部空闲");
  const activeIdx = lanes.map((l) => byStage[l].some((s) => s.status === "running")).lastIndexOf(true);
  const cell = (s) => {
    // 五归一：取消/超时的格子不再冒充「完成」——与步骤芯片同三色体系
    const st = s.status === "running" || s.status === "queued" ? "running"
      : s.status === "failed" ? "failed"
      : s.status === "cancelled" ? "cancelled"
      : s.status === "timeout" ? "timeout" : "done";
    const who = s.agent_label || s.agent || "";
    const title = [s.role, who, s.started_at ? t("开始于 ") + s.started_at : "",
                   (s.note ? "◆ " + s.note : ""), s.summary].filter(Boolean).join(" · ");
    const key = run.id + "|" + (s.log || "");
    return '<div class="hive-cell st-' + st + '" title="' + esc(title) + '" ' +
      'onclick="hiveOpenLog(\'' + esc(run.id) + "', '" + esc(s.log || "") + '\')">' +
      '<div class="hc-head">' +
      '<svg class="ico hc-bee" aria-hidden="true"><use href="#i-bee"></use></svg>' +
      '<span class="hc-role">' + esc(s.role || "") + "</span>" +
      '<span class="hc-who">' + esc(who) + "</span></div>" +
      '<div class="hc-tail" data-log="' + esc(s.log || "") + '">' +
      esc(hiveTails[key] || (s.summary || "")) + "</div>" +
      '<div class="hc-meta"><span class="hc-elapsed" data-started="' + esc(s.started_at || "") + '">' +
      (s.duration_s != null ? s.duration_s + "s" : hiveElapsed(s.started_at)) + "</span>" +
      (st === "running" ? '<span class="hc-live">● ' + t("工作中") + "</span>" : "") +
      (st === "timeout" ? '<span class="hc-dead">⏱ ' + t("超时") + "</span>" : "") +
      (st === "cancelled" ? '<span class="hc-dead">' + t("已取消") + "</span>" : "") +
      "</div></div>";
  };
  const dots = (list) => list.slice(-12).map((s) => {
    const dc = s.status === "running" ? "run" : s.status === "failed" ? "fail"
      : s.status === "cancelled" ? "cancel" : s.status === "timeout" ? "time" : "done";
    const stx = { running: t("运行中"), failed: t("失败"), cancelled: t("已取消"),
                  timeout: t("超时"), done: t("完成") }[dc] || s.status;
    return '<i class="ld ld-' + dc + '" title="' + esc((s.role || "") + " · " + stx) + '"></i>';
  }).join("");
  $("rd-hive-cells").innerHTML = lanes.map((stage, i) => {
    const list = byStage[stage];
    const hasRun = list.some((s) => s.status === "running");
    // 无 running（终态回看）：全部视为已完成泳道；有 running 时 activeIdx 之前算完成
    const cls = hasRun ? "lane-active"
      : (activeIdx === -1 || i < activeIdx ? "lane-done" : "lane-idle");
    return '<div class="hive-lane ' + cls + '">' +
      '<div class="lane-head"><span class="lane-name">' + esc(stage) + '</span>' +
      '<span class="lane-n">×' + list.length + "</span>" +
      (hasRun ? '<span class="lane-live">● ' + t("进行中") + "</span>" : "") + "</div>" +
      '<div class="lane-track">' + dots(list) + "</div>" +
      '<div class="lane-cells">' + list.slice(-8).map(cell).join("") + "</div></div>";
  }).join('<div class="lane-flow" aria-hidden="true"></div>');
  const live = running.length && (run.status === "running" || run.status === "queued");
  if (hiveClock) { clearInterval(hiveClock); hiveClock = null; }
  if (live) {
    if (!hiveTimer) hiveTimer = setInterval(() => hiveTick(run.id), 2000);
    hiveTick(run.id);
    // 秒表走动：running 卡计时每秒刷新，页面一眼可见"在动"
    hiveClock = setInterval(() => {
      document.querySelectorAll("#rd-hive-cells .hc-elapsed[data-started]").forEach((el) => {
        el.textContent = hiveElapsed(el.dataset.started);
      });
    }, 1000);
    // 点开任务第一眼就在干活：自动展开第一个在岗步骤的实时日志。
    // 每 run 只自动开一次；用户手动收起后不再打扰。
    if (S.hiveAutoLog !== run.id && !currentLog) {
      const first = running.find((s) => s.log);
      if (first) { S.hiveAutoLog = run.id; toggleLog(run.id, first.log); }
    }
  } else stopHiveTick();
  rdTabsSync();   // 蜂巢显隐直接决定「蜂巢」标签可用性
};

/* 活跃步骤的实时尾巴：拉 900 字符窗口，去噪后取最后一行正文 */
async function hiveTick(rid) {
  if (document.hidden || !rid) return;
  const box = $("rd-hive-cells");
  if (!box) return;
  const rels = Array.from(box.querySelectorAll(".hc-tail[data-log]"))
    .map((el) => el.dataset.log).filter(Boolean);
  for (const rel of rels) {
    try {
      const r = await api("/api/runs/" + encodeURIComponent(rid) +
        "/log?step=" + encodeURIComponent(rel) + "&tail=900");
      const lines = String(r.log || "").split("\n").filter((l) => l.trim());
      const last = hiveCleanLine(lines);
      const key = rid + "|" + rel;
      if (last && hiveTails[key] !== last) {
        hiveTails[key] = last;
        box.querySelectorAll(".hc-tail").forEach((el) => {
          if (el.dataset.log === rel) el.textContent = last;
        });
      }
    } catch (e) { /* 网络抖动保留上一帧 */ }
  }
}

/* 蜂巢格点击 → 打开该步骤实时日志（toggleLog 自带 2.5s 跟随 + 贴底滚动） */
window.hiveOpenLog = function (runId, rel) {
  if (!rel) { toast(t("该步骤无日志"), true); return; }
  toggleLog(runId, rel);
};

function stopLogLive() { if (S.logLive) { clearInterval(S.logLive); S.logLive = null; } }

async function cancelRun() {
  if (!S.detailRunId) return;
  if (!await uiConfirm(t("确定取消该运行？"), { ok: t("确定") })) return;
  await api("/api/runs/" + encodeURIComponent(S.detailRunId) + "/cancel", { method: "POST" });
}

/* ---------------------------------------------------- 运行中指挥（消息信箱） */
/* 用户在详情页往运行中的任务「递话」：文字 + 附件（截图/文件）。
 * 无头 CLI 插不进正在跑的进程，指令在下一次步骤下达前由后端 drain 注入。
 * 附件先 POST /api/attachments 进待提交区拿 id，发送时随消息一起提交到工作目录。 */
const DIR_ATT_MAX = 12;
const DIR_ATT_BYTES = 8 * 1024 * 1024;
let dirAtts = [];        // [{id,name,size}] 已上传待发送的附件
let dirRunId = null;     // 指挥区当前指向的 run

function fileToB64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
    fr.onerror = () => reject(new Error(t("读取文件失败")));
    fr.readAsDataURL(file);
  });
}

async function dirUploadFiles(files) {
  for (const f of files) {
    if (dirAtts.length >= DIR_ATT_MAX) { toast(t("附件最多 ") + DIR_ATT_MAX + t(" 个"), true); break; }
    if (f.size > DIR_ATT_BYTES) { toast(f.name + t("：超过 8MB，已跳过"), true); continue; }
    try {
      const b64 = await fileToB64(f);
      const d = await api("/api/attachments", { method: "POST",
        body: JSON.stringify({ name: f.name, data: b64 }) });
      if (d.attachment) dirAtts.push(d.attachment);
    } catch (e) { toast(t("附件上传失败：") + e.message, true); }
  }
  drawDirAtts();
}

function drawDirAtts() {
  const box = $("rd-attach-list");
  if (!box) return;
  box.innerHTML = dirAtts.map((a, i) =>
    '<span class="att-chip2">' + esc(a.name) + "<b onclick=\"dirRemoveAtt(" + i + ")\" title=\"" +
    t("移除") + "\">×</b></span>").join("");
}
window.dirRemoveAtt = function (i) { dirAtts.splice(i, 1); drawDirAtts(); };

/* 渲染指挥区：只认当前详情页指向的运行；messages 随 run 对象来（SSE 刷新即更新）
 * 【已屏蔽】运行中指挥信箱暂不露出；函数与后端链路保留，恢复时删掉首行 return 即可 */
function renderDirector(run, active) {
  const box = $("rd-direct");
  if (!box) return;
  box.classList.add("hidden");
  return;
  if (!run || !active) { box.classList.add("hidden"); return; }
  if (dirRunId !== run.id) { dirRunId = run.id; dirAtts = []; drawDirAtts(); }
  box.classList.remove("hidden");
  const msgs = run.messages || [];
  const mb = $("rd-msgs");
  mb.innerHTML = msgs.map((m) =>
    '<div class="rd-msg' + (m.consumed ? " m-consumed" : "") + '">' +
    '<span class="m-when">' + esc(m.created_at || "") + "</span>" +
    '<span class="m-who">' + esc(m.sender || "") + "</span>" +
    esc(m.text || t("（仅附件）")) +
    ((m.attachments || []).length
      ? '<span class="m-atts">' + t("附件：") + m.attachments.map(esc).join("、") + "</span>" : "") +
    (m.consumed && m.consumed_by && m.consumed_by.step
      ? '<span class="m-atts">' + t("已随步骤送达：") + "#" + Number(m.consumed_by.step) +
        " " + esc(m.consumed_by.role || "") + "</span>" : "") +
    "</div>").join("");
  const hint = $("rd-direct-hint");
  if (hint) hint.textContent = run.paused ? t("已暂停：指令入箱，放行后随下一步送达")
    : (msgs.some((m) => !m.consumed) ? t("将在下一个步骤开始前送达执行者") : "");
}

window.dirTogglePause = async function () {
  const run = S.lastRun;
  if (!run) return;
  const to = !run.paused;
  try {
    await api("/api/runs/" + encodeURIComponent(run.id) + "/pause",
      { method: "POST", body: JSON.stringify({ paused: to }) });
    toast(to ? t("已暂停：当前步骤跑完后挂起") : t("已放行，继续执行"));
  } catch (e) { toast(t("操作失败：") + e.message, true); }
};

window.dirSend = async function () {
  if (!dirRunId) return;
  const ta = $("rd-msg-input");
  const text = (ta.value || "").trim();
  if (!text && !dirAtts.length) return;
  const btn = $("rd-send-btn");
  btn.disabled = true;
  try {
    await api("/api/runs/" + encodeURIComponent(dirRunId) + "/messages", {
      method: "POST",
      body: JSON.stringify({ text, attachments: dirAtts.map((a) => a.id) }),
    });
    ta.value = "";
    dirAtts = []; drawDirAtts();
    toast(t("指令已入箱，将在下一个步骤下达"));
    // 不等 SSE 回程：本地立即拉一次该 run 刷新消息流
    const d = await api("/api/runs/" + encodeURIComponent(dirRunId));
    if (d.run && dirRunId === d.run.id) renderDirector(d.run,
      d.run.status === "running" || d.run.status === "queued");
  } catch (e) { toast(t("发送失败：") + e.message, true); }
  finally { btn.disabled = false; }
};

function bindDirector() {
  $("rd-attach-btn").addEventListener("click", () => $("rd-attach-file").click());
  $("btn-pause").addEventListener("click", window.dirTogglePause);
  $("rd-attach-file").addEventListener("change", (e) => {
    dirUploadFiles(Array.from(e.target.files || []));
    e.target.value = "";  // 允许再次选同名文件
  });
  $("rd-send-btn").addEventListener("click", window.dirSend);
  $("rd-msg-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); window.dirSend(); }
  });
  // 粘贴截图：图片走上传，纯文本交回默认行为
  $("rd-msg-input").addEventListener("paste", (e) => {
    const items = Array.from((e.clipboardData || {}).items || [])
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"));
    if (!items.length) return;
    e.preventDefault();
    const files = items.map((it) => {
      const f = it.getAsFile();
      return f && !f.name ? new File([f], "paste-" + Date.now() + ".png", { type: f.type }) : f;
    }).filter(Boolean);
    dirUploadFiles(files);
  });
}


/* ---------------------------------------------------------- 外观：皮肤 + 明暗（换肤） */
/* 调色板全部在 style.css（html[data-skin="X"]，每套含夜间/日间两版变量）；这里只放顺序
 * 与文案。卡片预览色块用 skinPalette 从 CSS 变量实时取值，不在 JS 里重复写色值——
 * 皮肤改色只需要动 style.css，预览与真实界面不会各自漂移。 */
const SKINS = [
  { id: "classic", name: t("经典"), desc: t("黑白灰 + 蓝色强调，ChatGPT 式清爽配色（默认）") },
  { id: "ocean", name: t("深海"), desc: t("藏青底色 + 天蓝强调，夜间长时间盯任务更沉静") },
  { id: "forest", name: t("森野"), desc: t("墨绿底色 + 青翠强调，偏自然的护眼配色") },
  { id: "amber", name: t("暖阳"), desc: t("暖棕底色 + 琥珀强调，纸感暖调") },
  { id: "violet", name: t("霓虹"), desc: t("暗紫底色 + 品红强调，霓虹感强") },
  { id: "contrast", name: t("高对比"), desc: t("纯黑 / 纯白 + 硬边框、去阴影，弱视与强光环境更清晰") },
];
const SKIN_IDS = new Set(SKINS.map((s) => s.id));
const SKIN_KEY = "orch.skin";
const THEME_KEY = "orch.theme";

function currentSkin() {
  const s = localStorage.getItem(SKIN_KEY) || "";
  return SKIN_IDS.has(s) ? s : "classic";   // 值缺失/非法（如换过版本）一律回落经典
}

function currentMode() { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; }

/* 取某皮肤在指定明暗下的实际配色：临时拨到根属性读计算值再还原。
 * 同步执行，浏览器不会中途绘制，所以看不到闪烁。 */
function skinPalette(id, mode) {
  const root = document.documentElement;
  const skin0 = root.dataset.skin, theme0 = root.dataset.theme;
  root.dataset.skin = id;
  root.dataset.theme = mode;
  const cs = getComputedStyle(root);
  const get = (n) => cs.getPropertyValue(n).trim();
  const pal = { bg: get("--bg"), sidebar: get("--sidebar"), accent: get("--accent"), accent2: get("--accent2"), text: get("--text") };
  root.dataset.skin = skin0;
  root.dataset.theme = theme0;
  return pal;
}

/* 把皮肤 + 明暗落到根属性，并同步顶栏两个入口、手机状态栏色 */
function applyAppearance() {
  const root = document.documentElement;
  const skin = currentSkin(), mode = currentMode();
  root.dataset.skin = skin;
  root.dataset.theme = mode;
  const dark = mode === "dark";
  const th = $("btn-theme");
  if (th) th.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-' + (dark ? "moon" : "sun") + '"></use></svg>';
  // 手机浏览器的状态栏 / 地址栏配色跟着皮肤走（各皮肤底色差异大，写死一个色会脱节）
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    const side = getComputedStyle(root).getPropertyValue("--sidebar").trim();
    if (side) meta.setAttribute("content", side);
  }
  applyCodeTheme();   // 代码主题跟随明暗：换的其实是 [data-codetheme] 指向的那套调色板
}

function setThemeMode(mode) {
  localStorage.setItem(THEME_KEY, mode === "light" ? "light" : "dark");
  applyAppearance();
  renderAppearance();
  renderCodePreviews();   // 明暗换了，「当前生效」徽章跟着挪到另一张预览卡
}

function setSkin(id) {
  if (!SKIN_IDS.has(id)) return;
  localStorage.setItem(SKIN_KEY, id);
  applyAppearance();
  renderAppearance();
  const s = SKINS.find((x) => x.id === id);
  toast(t("已换肤：") + (s ? s.name : id) + t("（") + (currentMode() === "dark" ? t("夜间") : t("日间")) + t("）"));
}

function toggleTheme() { setThemeMode(currentMode() === "light" ? "dark" : "light"); }

/* 外观页：皮肤卡片（点选即换） + 明暗分段控。选中态同时给描边与勾选，
 * 不只靠颜色深浅传达，保证在高对比等皮肤下也一眼可辨。 */
function renderAppearance() {
  const grid = $("skin-grid");
  if (!grid) return;
  const cur = currentSkin(), mode = currentMode();
  grid.innerHTML = SKINS.map((s) => {
    const p = skinPalette(s.id, mode);
    return '<button type="button" class="skin-card' + (s.id === cur ? " active" : "") + '" data-skin="' + s.id + '" title="' + esc(s.desc) + '">'
      + '<span class="skin-prev" aria-hidden="true">'
      + '<span class="pv-side" style="background:' + p.sidebar + '"></span>'
      + '<span class="pv-main" style="background:' + p.bg + '">'
      + '<span class="pv-bar w85" style="background:' + p.accent + '"></span>'
      + '<span class="pv-bar w60" style="background:' + p.text + ';opacity:.3"></span>'
      + '<span class="pv-bar w40" style="background:' + p.accent2 + '"></span>'
      + '</span></span>'
      + '<span class="skin-meta"><span class="skin-name">' + esc(s.name) + '</span>'
      + '<svg class="ico skin-check" aria-hidden="true"><use href="#i-check"></use></svg></span>'
      + '<span class="skin-desc">' + esc(s.desc) + '</span>'
      + '</button>';
  }).join("");
  document.querySelectorAll("#skin-mode [data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  const tag = $("skin-cur");
  if (tag) {
    const s = SKINS.find((x) => x.id === cur);
    tag.textContent = (s ? s.name : cur) + " · " + (mode === "dark" ? t("夜间") : t("日间"));
  }
}

/* ---------------------------------------------------------- 代码设置：高亮主题 + 行号/换行/字号 */
/* 只管代码内容（文件弹窗 / diff 弹窗 / 预览卡），不受界面字号影响。偏好存 localStorage
 * （与皮肤同模式，设备级）。主题 = 一小份 --ct-* CSS 变量，作用域挂
 * [data-ctheme-mode][data-codetheme]：html 上由 applyCodeTheme 落当前生效主题，
 * 预览卡同名属性拨到另一套即可同屏双色，无需 iframe。 */
const CODE_THEMES = [
  { id: "github", name: "GitHub Light", dark: false, desc: t("经典浅色，清爽不抢眼，日间界面首选") },
  { id: "vs", name: "Visual Studio Light", dark: false, desc: t("VS 家族浅色，蓝紫关键词配色") },
  { id: "xcode", name: "Xcode Light", dark: false, desc: t("苹果开发工具同款浅色，冷色克制") },
  { id: "solarized", name: "Solarized Light", dark: false, desc: t("米黄纸感底色，长时间阅读更柔和") },
  { id: "github-dark", name: "GitHub Dark", dark: true, desc: t("经典深色，夜间界面首选") },
  { id: "vs2015", name: "Visual Studio Dark", dark: true, desc: t("VS 家族深色，灰蓝底更沉稳") },
  { id: "monokai", name: "Monokai", dark: true, desc: t("高饱和黄紫粉，老牌编辑器名主题") },
  { id: "one-dark", name: "One Dark", dark: true, desc: t("Atom 出品的均衡深色，蓝灰底不刺眼") },
];
const CT_LIGHT_KEY = "orch.codeTheme.light";
const CT_DARK_KEY = "orch.codeTheme.dark";
const CT_LINENUM_KEY = "orch.code.linenum";
const CT_WRAP_KEY = "orch.code.wrap";
const CT_SIZE_KEY = "orch.code.size";
const CS_SAMPLE = [
  "const themePreview: ThemeConfig = {",
  '  surface: "sidebar",',
  '  accent: "#339CFF",',
  "  contrast: 45,",
  "};",
].join("\n");

function codeThemeFor(mode) {
  const saved = localStorage.getItem(mode === "dark" ? CT_DARK_KEY : CT_LIGHT_KEY) || "";
  const ok = CODE_THEMES.find((x) => x.id === saved && ((mode === "dark") === !!x.dark));
  return ok ? ok.id : (mode === "dark" ? "github-dark" : "github");
}
function codeLineNum() { return localStorage.getItem(CT_LINENUM_KEY) !== "0"; }   // 缺省开
function codeWrap() { return localStorage.getItem(CT_WRAP_KEY) === "1"; }
function codeFontSize() {
  const n = parseFloat(localStorage.getItem(CT_SIZE_KEY) || "12.5");
  return isFinite(n) ? Math.min(22, Math.max(10, n)) : 12.5;
}

/* 把当前代码主题 + 行号/换行/字号落到根属性（随明暗切换一起被 applyAppearance 调） */
function applyCodeTheme() {
  const root = document.documentElement;
  const mode = currentMode();
  root.dataset.cthemeMode = mode;
  root.dataset.codetheme = codeThemeFor(mode);
  root.classList.toggle("code-linenum", codeLineNum());
  root.classList.toggle("code-wrap", codeWrap());
  root.style.setProperty("--code-size", codeFontSize() + "px");
}

/* 单趟字符扫描 tokenize：不依赖长正则，逐字符判定注释/字符串/数字/单词。
 * 覆盖 JS/TS/Python/Go/Rust/Java/C 的常见词法；类型映射到 .ct-* 上色类。 */
const _CT_KW = new Set(("if else for while return function class import export from const let var " +
  "new this super extends implements interface type enum public private protected static async await " +
  "try catch finally throw switch case break continue default do in of typeof instanceof void delete " +
  "yield defer go func package chan select struct map nil null true false None True False self def lambda " +
  "elif pass raise with as and or not is fn impl pub use mut match loop crate mod").split(" "));

function _ctIsWordChar(d) {
  return (d >= "a" && d <= "z") || (d >= "A" && d <= "Z") || (d >= "0" && d <= "9") || d === "_" || d === "$";
}

function _ctScan(src) {
  const out = [];
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === "/" && src[i + 1] === "/") {
      let j = src.indexOf("\n", i);
      if (j < 0) j = n;
      out.push({ t: "com", v: src.slice(i, j) });
      i = j;
    } else if (c === "#") {
      let j = src.indexOf("\n", i);
      if (j < 0) j = n;
      out.push({ t: "com", v: src.slice(i, j) });
      i = j;
    } else if (c === "/" && src[i + 1] === "*") {
      let j = src.indexOf("*/", i + 2);
      j = j < 0 ? n : j + 2;
      out.push({ t: "com", v: src.slice(i, j) });
      i = j;
    } else if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      while (j < n) {
        if (src[j] === "\\") { j += 2; continue; }
        j++;
        if (src[j - 1] === c || src[j - 1] === "\n") break;   // 未闭合到行尾为止
      }
      out.push({ t: "str", v: src.slice(i, j) });
      i = j;
    } else if (c >= "0" && c <= "9") {
      let j = i;
      while (j < n && ((src[j] >= "0" && src[j] <= "9") || src[j] === ".")) j++;
      out.push({ t: "num", v: src.slice(i, j) });
      i = j;
    } else if ((c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_" || c === "$") {
      let j = i;
      while (j < n && _ctIsWordChar(src[j])) j++;
      const w = src.slice(i, j);
      out.push({ t: _CT_KW.has(w) ? "kw" : (src[j] === "(" ? "fn" : "txt"), v: w });
      i = j;
    } else {
      out.push({ t: "txt", v: c });
      i++;
    }
  }
  return out;
}

function _ctWrapHTML(toks) {
  let html = "";
  for (const k of toks) html += k.t === "txt" ? esc(k.v) : '<span class="ct-' + k.t + '">' + esc(k.v) + "</span>";
  return html;
}

/* 行号 + 高亮二合一：单 <table>（行号列 + 代码列），滚动/换行时天然对齐。 */
function codeBlockHTML(text) {
  const lines = String(text || "").replace(/\n$/, "").split("\n");
  const rows = lines.map((ln, i) =>
    "<tr>" + '<td class="cb-ln">' + (i + 1) + "</td>" +
    '<td class="cb-code">' + _ctWrapHTML(_ctScan(ln)) + "</td></tr>");
  return '<table class="code-block"><tbody>' + rows.join("") + "</tbody></table>";
}

function renderCodeSettings() {
  const selL = $("cs-theme-light"), selD = $("cs-theme-dark");
  if (!selL) return;
  const fill = (sel, dark) => {
    sel.innerHTML = CODE_THEMES.filter((x) => !!x.dark === dark).map((x) =>
      '<option value="' + x.id + '"' + (x.id === codeThemeFor(dark ? "dark" : "light") ? " selected" : "") + ">" + esc(x.name) + "</option>").join("");
  };
  fill(selL, false);
  fill(selD, true);
  $("cs-linenum").checked = codeLineNum();
  $("cs-wrap").checked = codeWrap();
  $("cs-size").value = codeFontSize();
  renderCodePreviews();
}

function renderCodePreviews() {
  for (const mode of ["light", "dark"]) {
    const code = document.querySelector("#cs-preview-" + mode + " .cs-pv-code");
    if (!code) continue;
    const thId = codeThemeFor(mode);
    const th = CODE_THEMES.find((x) => x.id === thId);
    code.dataset.codetheme = thId;
    code.innerHTML = codeBlockHTML(CS_SAMPLE);
    const badge = document.querySelector("#cs-preview-" + mode + " .cs-pv-badge");
    const active = mode === currentMode();
    if (badge) {
      badge.textContent = active ? t("当前生效") : (mode === "dark" ? t("深色") : t("浅色"));
      badge.classList.toggle("cs-badge-on", active);
    }
    const tt = document.querySelector("#cs-preview-" + mode + " .cs-pv-theme");
    if (tt) tt.textContent = th ? th.name : thId;
  }
}

/* 代码设置改动 → 存偏好 + 立即生效 + 预览刷新；不重绘设置行本身（防选值弹回） */
function csApplyFromControls() {
  localStorage.setItem(CT_LIGHT_KEY, $("cs-theme-light").value);
  localStorage.setItem(CT_DARK_KEY, $("cs-theme-dark").value);
  localStorage.setItem(CT_LINENUM_KEY, $("cs-linenum").checked ? "1" : "0");
  localStorage.setItem(CT_WRAP_KEY, $("cs-wrap").checked ? "1" : "0");
  const n = parseFloat($("cs-size").value);
  localStorage.setItem(CT_SIZE_KEY, String(isFinite(n) ? Math.min(22, Math.max(10, n)) : 12.5));
  applyCodeTheme();
  renderCodePreviews();
}

/* 皮肤页锚点：「外观 / 代码」两胶囊。点击滚到对应面板（main 是滚动容器）；
 * main 滚动时反算当前区块高亮胶囊。只在皮肤页可见时计算，别处滚动不花开销。 */
let _apnClicking = false;   // 点击平滑滚动期间不抢高亮，落定后按位置归位

function apnScrollTo(id) {
  const el = document.getElementById(id);
  const main = document.querySelector("main");
  if (!el || !main) return;
  _apnClicking = true;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  setTimeout(() => { _apnClicking = false; apnSyncActive(); }, 600);   // 平滑滚动约 400-500ms
}

function apnSyncActive() {
  if (_apnClicking || S.tab !== "appearance") return;
  const main = document.querySelector("main");
  const anchor = document.getElementById("cs-anchor");
  if (!main || !anchor || anchor.offsetParent === null) return;   // 皮肤页没显示就不算
  const code = document.getElementById("apn-code");
  if (!code) return;
  // 代码面板顶进视口上缘（留一点余量）就算「代码」区，否则「外观」
  const onCode = code.getBoundingClientRect().top - main.getBoundingClientRect().top <= 120;
  anchor.querySelectorAll("[data-apn]").forEach((b) => b.classList.toggle("active", b.dataset.apn === (onCode ? "apn-code" : "apn-skin")));
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
        const pname = c2.p ? provName(c2.p) : t("CLI 默认凭据");
        return '<span class="ochip' + (i === 0 ? " primary" : "") + '">' +
          "<b>" + (i === 0 ? t("主") : t("备")) + "</b>" + esc(pname) + " · " + esc(c2.m) +
          (i > 0 ? '<button class="mini" data-m="' + esc(c2.m) + '" data-p="' + esc(c2.p) +
                  '" title="设为主模型" onclick="bindPromote(\'' + esc(c.id) + '\', this)">' +
                  '<svg class="ico" aria-hidden="true"><use href="#i-arrow-up"></use></svg></button>' : "") +
          '<button class="mini" data-m="' + esc(c2.m) + '" data-p="' + esc(c2.p) +
            '" title="移除" onclick="bindRemove(\'' + esc(c.id) + '\', this)">×</button>' +
          "</span>";
      }).join("")
    : '<span class="hint">未设置' + (provId ? t("（按供应商/难度自动解析）") : t("（用 CLI 默认模型）")) + "</span>";
  return '<div class="field"><label>运行时模型链（跨厂商，最多 ' + MAX_ORCH_MODELS + " 条）</label>" +
    '<div class="orch-row">' + chips +
    '<button class="ghost small" onclick="bindToggle(\'' + esc(c.id) + '\')">' +
    (st.open ? t("收起") : t("＋ 添加")) + "</button>" +
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
      toast(t("最多选 ") + MAX_ORCH_MODELS + t(" 条（1 个主模型 + ") +
            (MAX_ORCH_MODELS - 1) + t(" 个降级备选）。"), true);
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
  // 日志流式刷新后定位到最新一行（除非用户主动往上翻看历史）
  document.querySelectorAll("pre.mgmt-log").forEach((el) => {
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) el.scrollTop = el.scrollHeight;
  });
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
    // 一键打开（web 类开浏览器 / console 类新终端跑 TUI）：已安装且配了 launch 才出
    c.installed && c.launch ? '<button class="primary small" onclick="openAgent(\'' + esc(c.id) + '\')">' +
      (c.launch.kind === "web" ? t("打开网页") : t("打开")) + "</button>" : "",
    c.installed && c.orch_kind ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'smoke\')">冒烟测试</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'upgrade\')">升级</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small" onclick="checkUpdate(\'' + esc(c.id) + '\')">检查更新</button>' : "",
    !c.installed && c.has_install ? '<button class="ghost small" onclick="mgmt(\'' + esc(c.id) + '\', \'install\')">安装</button>' : "",
    c.installed ? '<button class="ghost small" onclick="showMgmtLog(\'' + esc(c.id) + '\')">日志</button>' : "",
    !c.installed && !c.has_install ? '<span class="hint">安装命令待配置（编辑 data/catalog.json）</span>' : "",
  ].join("");
  return '<div class="card">' +
    '<div class="head"><span class="name">' + esc(c.name) + "</span>" +
    (c.installed ? statusChip("done") : '<span class="tag">未安装</span>') + updateChip(c) +
    // 卸载放卡片右上角（与状态徽标同行），不再吊在操作行尾部
    (c.installed && c.uninstall_cmd ? '<button class="danger small" onclick="mgmt(\'' + esc(c.id) + '\', \'uninstall\')">卸载</button>' : "") +
    "</div>" +
    '<div class="note">' + esc(c.note || "") + "</div>" +
    '<div class="facts">版本 <b>' + esc(c.version || "-") + "</b>　模型 <b>" + esc(fmtModel(c.model) || "-") + "</b>" +
    ((c.detail || c.config_path) ? "<br>" + esc(c.detail || c.config_path) : "") + "</div>" +
    mgmtPanel(c.id) +
    '<div class="ops">' + orchBox + ops + "</div>" + modelBox + "</div>";
}

/* 管理操作（升级/安装/卸载/冒烟）的就地反馈面板：状态 + 版本对比 + 流式日志 */
function mgmtPanel(agentId) {
  const st = (S.mgmt || {})[agentId];
  if (!st) return "";
  const running = st.status === "queued" || st.status === "running";
  const chip = statusChip(st.status || "queued");
  const vcmp = (st.versionBefore || st.versionAfter)
    ? '<span class="hint">版本 ' + esc(st.versionBefore || "?") + " → <b>" +
      esc(st.versionAfter || "…") + "</b>" +
      (st.status === "done" && st.versionBefore && st.versionAfter === st.versionBefore
        ? t("（已是最新版本，无需升级）") : "") + "</span>"
    : "";
  // 运行且暂无输出时给"等待中"提示（npm 下载阶段本来就有一段静默期）
  const empty = running ? t("（等待输出…安装/下载阶段可能有一段静默期）") : t("（无输出）");
  const logBox = st.showLog
    ? '<pre class="mgmt-log' + (st.log ? "" : " muted") + '">' +
      esc(st.log || empty) + "</pre>" : "";
  return '<div class="mgmt-panel ' + esc(st.status || "queued") + '">' +
    '<div class="mgmt-head">' + (running ? '<span class="live-dot"></span>' : "") + chip +
    '<span class="mgmt-title">' + esc(st.opLabel || "") + "</span>" + vcmp +
    '<span class="mgmt-ops">' +
    '<button class="ghost small" onclick="toggleMgmtLog(\'' + esc(agentId) + '\')">' +
    (st.showLog ? t("收起日志") : t("查看日志")) + "</button>" +
    (st.runId ? '<button class="ghost small" onclick="openRunInRuns(\'' + esc(st.runId) + '\')">完整运行</button>' : "") +
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
  if (!v) { toast(t("请先在下拉里选择一个模型（要清除配置请手动编辑配置文件）。"), true); return; }
  try {
    const r = await api("/api/catalog/" + encodeURIComponent(id) + "/model", { method: "POST", body: JSON.stringify({ model: v }) });
    toast(t("已写入：") + (r.model || v));
  } catch (e) { toast(t("失败：") + e.message, true); }
  poll();
}

/* 一键打开：web 类后台起服务并自动开浏览器；console 类新开终端窗口跑交互 TUI。
   用的模型就是目录页「默认模型」已写入该 CLI 配置文件的那份；密钥由服务端按编排同款规则注入 */
async function openAgent(id) {
  try {
    const r = await api("/api/catalog/" + encodeURIComponent(id) + "/launch", { method: "POST", body: "{}" });
    toast(r.message || t("已发出打开指令"));
  } catch (e) { toast(t("失败：") + e.message, true); }
}

async function mgmt(id, op) {
  const names = { install: t("安装"), upgrade: t("升级"), uninstall: t("卸载"), smoke: t("冒烟测试") };
  const entry = (S.catalog || []).find((x) => x.id === id) || {};
  if (op === "install" && !await uiConfirm(t("确定执行安装？命令来自 data/catalog.json，可在管理页查看。"), { ok: t("安装") })) return;
  if (op === "uninstall") {
    // 卸载不可逆：把将要执行的真实命令摊开给用户确认
    const cmd = entry.uninstall_cmd || t("（未能推导，请先在 catalog 配置 uninstall）");
    if (!await uiConfirm(t("确定卸载 ") + entry.name + t("？\\n\\n将执行：\\n") + cmd +
                 t("\\n\\n该 CLI 会从本机移除（配置文件保留）。此操作不可撤销。"), { ok: t("卸载"), danger: true })) return;
  }
  const before = ((S.catalog || []).find((x) => x.id === id) || {}).version;
  S.mgmt = S.mgmt || {};
  // 安装/升级/卸载默认展开日志：输出就是用户要看的进度（流式实时刷新）
  S.mgmt[id] = { opLabel: names[op] || op, status: "queued", versionBefore: before,
                 log: "", showLog: op !== "smoke" };
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
    // 运行中也持续拉日志：输出是流式写入的，边跑边看才有意义
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
      st.summary = t("连接中断，正在重试（") + e.message + t("）");
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
  if (!steps.length) return t("（尚无输出）");
  const last = steps[steps.length - 1];
  const res = await api("/api/runs/" + encodeURIComponent(runId) +
                        "/log?step=" + encodeURIComponent(last.log || ""));
  return res.log || t("（无输出）");
}

async function toggleMgmtLog(agentId) {
  const st = S.mgmt[agentId];
  if (!st) return;
  st.showLog = !st.showLog;
  if (st.showLog && st.runId) {
    try { st.log = await fetchRunLog(st.runId); }
    catch (e) { st.log = t("日志读取失败：") + e.message; }
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
    if (!run) { toast(t("还没有该智能体的管理操作记录（先点升级/安装/冒烟测试）。"), true); return; }
    S.mgmt = S.mgmt || {};
    S.mgmt[agentId] = { opLabel: run.title, status: run.status, runId: run.id,
                        summary: run.summary || run.error || "",
                        log: await fetchRunLog(run.id), showLog: true };
    S.catSig = null; renderCatalog();
  } catch (e) { toast(t("读取失败：") + e.message, true); }
}

/* 「检查更新」：查询远端最新版本，明确告知是否已是最新（避免"升级没反应"的误解） */
async function checkUpdate(agentId) {
  S.mgmt = S.mgmt || {};
  const prev = S.mgmt[agentId] || {};
  S.mgmt[agentId] = Object.assign({}, prev, {
    opLabel: t("检查更新"), status: "running", summary: t("正在查询远端最新版本…"),
    showLog: prev.showLog, runId: prev.runId, versionBefore: prev.versionBefore,
  });
  S.catSig = null; renderCatalog();
  try {
    const r = await api("/api/catalog/" + encodeURIComponent(agentId) + "/check-update",
                        { method: "POST" });
    const up = r.updatable;
    let msg;
    if (up === true) {
      msg = t("发现新版本：") + (r.current || "?") + " → " + (r.latest || "?") + t("，可点「升级」更新。");
    } else if (up === false) {
      msg = t("已是最新版本（") + (r.latest || r.current || "?") + t("），无需升级。");
    } else {
      msg = r.note || t("无法判断是否有更新。");
    }
    S.mgmt[agentId] = Object.assign({}, S.mgmt[agentId], {
      status: up === true ? "done" : (up === false ? "done" : "failed"),
      summary: msg, log: t("当前版本：") + (r.current || "?") +
        t("\\n最新版本：") + (r.latest || t("(未知)")) +
        t("\\n可更新：") + (up === true ? t("是") : up === false ? t("否") : t("未知")) +
        (r.note ? t("\\n说明：") + r.note : ""),
      showLog: true, versionAfter: r.latest || undefined,
    });
  } catch (e) {
    S.mgmt[agentId] = Object.assign({}, S.mgmt[agentId],
      { status: "failed", summary: t("检查失败：") + e.message });
  }
  S.catSig = null; renderCatalog();
}

/* ---------------------------------------------------------- 自定义流程管理 */
function openFlowsManager() {
  const flows = S.flows || [];
  const rows = flows.map((f) =>
    '<div class="item"><div class="t"><span class="name">' + flowIconHtml(f) + " " + esc(f.name) +
    '</span><span class="tag">' + esc(f.id) + "</span>" +
    '<span class="tag">' + (f.engine === "code" ? t("代码引擎") : t("评审引擎")) + "</span>" +
    (f.serial ? '<span class="tag">连载 ' + f.serial.chapters + " 章</span>" : "") +
    (f.builtin ? '<span class="tag ok">预置</span>' : '<span class="tag">自定义</span>') +
    (f.edited ? '<span class="tag">已改</span>' : "") +
    '<button class="ghost small" onclick="flowForm(\'' + esc(f.id) + '\')">编辑</button>' +
    (f.builtin
      ? (f.edited ? '<button class="ghost small" onclick="flowReset(\'' + esc(f.id) + '\')">恢复默认</button>' : "")
      : '<button class="danger small" onclick="deleteFlow(\'' + esc(f.id) + '\')">删除</button>') +
    '</div><div class="desc">' + esc(f.note || "") +
    (f.rubric ? t("　维度：") + esc(f.rubric.join(" / ")) : "") +
    (f.threshold ? t("　阈值：") + f.threshold : "") +
    (f.serial ? t("　每章 ") + f.serial.words_per_chapter + t(" 字") : "") + "</div></div>").join("");
  const body = '<p class="hint">预置流程可直接编辑（阈值/轮数/维度/章节数/提示词），改动随时可「恢复默认」；' +
    '自定义流程只需填名称与引擎，其余留空走默认。</p>' +
    '<div class="list">' + rows + "</div>" +
    '<button class="primary" style="margin-top:10px" onclick="flowForm()">＋ 新建自定义流程</button>';
  openModal(t("🧩 任务类型管理"), body, "");
}

async function flowReset(fid) {
  if (!await uiConfirm(t("把「") + fid + t("」恢复为内置默认配置？"), { ok: t("恢复") })) return;
  try { await api("/api/flows/" + encodeURIComponent(fid) + "/reset", { method: "POST" }); }
  catch (e) { toast(t("恢复失败：") + e.message, true); return; }
  await loadFlows();
  openFlowsManager();
  toast(t("已恢复默认"));
}

/* 新建/编辑流程表单弹框；fid 空 = 新建 */
function flowForm(fid) {
  const f = fid ? flowById(fid) : null;
  const isBuiltin = !!(f && f.builtin);
  const engine = f ? f.engine : "review";
  const engLocked = !!f;   // 编辑时引擎不可改（语义一致性）
  const body =
    '<div class="form">' +
    '<div class="grid-2">' +
    '<div class="field"><label>名称 <span class="req">*</span></label><input id="fl-name" value="' +
      esc(f ? f.name : "") + '" placeholder="例：播客脚本"></div>' +
    '<div class="field"><label>引擎 <span class="req">*</span></label><select id="fl-engine"' +
      (engLocked ? " disabled" : "") + '>' +
      '<option value="review"' + (engine === "review" ? " selected" : "") + '>评审引擎（起草 → 多维评审 → 修订 → 门禁）</option>' +
      '<option value="code"' + (engine === "code" ? " selected" : "") + '>代码引擎（实现 → 验证 → 评审 → 修复）</option>' +
      "</select></div></div>" +
    (f ? "" : '<div class="field"><label>流程 ID（留空 = 按名称自动生成）</label><input id="fl-id" placeholder="小写字母开头，可留空"></div>') +
    '<details id="fl-adv"' + (f ? " open" : "") + '><summary>进阶设置（可留空，走引擎默认）</summary><div class="adv-body">' +
    '<div id="fl-review-fields"' + (engine === "review" ? "" : ' class="hidden"') + ">" +
    '<div class="grid-2">' +
    '<div class="field"><label>产出文件名</label><input id="fl-manuscript" value="' + esc(f && f.manuscript ? f.manuscript : "") + '" placeholder="留空 = 按流程 ID 生成"></div>' +
    '<div class="field"><label>发布阈值（1-10）</label><input id="fl-threshold" type="number" step="0.5" min="1" max="10" value="' + (f && f.threshold ? f.threshold : "") + '" placeholder="留空 = 7.0"></div>' +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>评审轮数（1-5）</label><input id="fl-rounds" type="number" min="1" max="5" value="' + (f && f.rounds ? f.rounds : "") + '" placeholder="留空 = 2"></div>' +
    '<div class="field"><label>评审维度（逗号分隔）</label><input id="fl-rubric" value="' + esc(f && f.rubric ? f.rubric.join(", ") : "") + '" placeholder="留空 = 内容, 结构, 表达"></div>' +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>连载章节数（留空 = 单稿件）</label><input id="fl-chapters" type="number" min="2" max="20" value="' + (f && f.serial ? f.serial.chapters : "") + '" placeholder="例：8"></div>' +
    '<div class="field"><label>每章约字数</label><input id="fl-words-per-ch" type="number" min="500" max="8000" step="100" value="' + (f && f.serial ? f.serial.words_per_chapter : "") + '" placeholder="例：2500"></div>' +
    '<div class="field"><label>同章赛马稿件数</label><input id="fl-variants" type="number" min="1" max="3" step="1" value="' + (f && f.serial ? (f.serial.variants || 1) : 1) + '" placeholder="1 = 关闭"></div>' +
    "</div>" +
    '<div class="field"><label>起草提示词（可选，占位符 __FILE__ __GOAL__ __CONTEXT__ __SKILLS__）</label><textarea id="fl-draft" rows="3" placeholder="留空 = 内置通用模板">' + esc(f && f.draft_prompt ? f.draft_prompt : "") + "</textarea></div>" +
    '<div class="field"><label>评审提示词（可选，占位符 __DIMKEYS__ __MANUSCRIPT__）</label><textarea id="fl-critique" rows="3" placeholder="留空 = 内置通用模板">' + esc(f && f.critique_prompt ? f.critique_prompt : "") + "</textarea></div>" +
    "</div>" +
    '<div class="field"><label>一句话说明（显示在流程列表）</label><input id="fl-note" value="' + esc(f ? (f.note || "") : "") + '" placeholder="例：技术播客单集脚本产出"></div>' +
    "</div></details></div>";
  openModal(f ? (t("编辑流程：") + esc(f.name) + (isBuiltin ? t("（预置）") : "")) : t("新建自定义流程"), body, "");
  const foot = $("modal-foot");
  if (foot) foot.innerHTML = '<button class="primary" onclick="saveFlow()">保存</button>' +
    '<button class="ghost" onclick="closeModal()">取消</button>';
  const eng = $("fl-engine");
  if (eng) eng.addEventListener("change", () => {
    $("fl-review-fields").classList.toggle("hidden", eng.value !== "review");
  });
}

async function saveFlow() {
  const name = $("fl-name").value.trim();
  if (!name) { toast(t("请填写名称"), true); return; }
  const idEl = $("fl-id");
  let id = idEl ? idEl.value.trim() : ($("fl-engine").dataset.fid || "");
  if (!id) {
    // 按名称生成 id（中文名 → 用拼音不可行，退化为时间戳短 id，用户可事后改）
    id = "flow-" + Date.now().toString(36);
  }
  const engine = $("fl-engine").value;
  const payload = { id, name, engine, note: $("fl-note").value.trim() };
  const iconEl = $("fl-icon");
  if (iconEl && iconEl.value.trim()) payload.icon = iconEl.value.trim();
  if (engine === "review") {
    const ms = $("fl-manuscript").value.trim();
    if (ms) payload.manuscript = ms;
    const th = parseFloat($("fl-threshold").value);
    if (th) payload.threshold = th;
    const rd = parseInt($("fl-rounds").value, 10);
    if (rd) payload.rounds = rd;
    const rb = $("fl-rubric").value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
    if (rb.length) payload.rubric = rb;
    const ch = parseInt($("fl-chapters").value, 10);
    if (ch >= 2) payload.serial = {
      chapters: ch,
      words_per_chapter: parseInt($("fl-words-per-ch").value, 10) || 2500,
      variants: Math.max(1, Math.min(3, parseInt($("fl-variants").value, 10) || 1)),
    };
    const dp = $("fl-draft").value.trim();
    if (dp) payload.draft_prompt = dp;
    const cp = $("fl-critique").value.trim();
    if (cp) payload.critique_prompt = cp;
  }
  try {
    await api("/api/flows", { method: "POST", body: JSON.stringify(payload) });
  } catch (e) { toast(t("保存失败：") + e.message, true); return; }
  closeModal();
  await loadFlows();
  openFlowsManager();
  toast(t("流程已保存"));
}

async function deleteFlow(fid) {
  if (!await uiConfirm(t("删除自定义流程「") + fid + t("」？已有任务不受影响。"), { ok: t("删除"), danger: true })) return;
  try { await api("/api/flows/" + encodeURIComponent(fid) + "/delete", { method: "POST" }); }
  catch (e) { toast(t("删除失败：") + e.message, true); return; }
  await loadFlows();
  openFlowsManager();
  toast(t("已删除"));
}

/* ---------------------------------------------------------- 经验库 */
// 分类配色：chip 圆点与卡片 tag 共用，同一分类在所有视图里颜色一致
const CAT_COLOR = {
  "情节逻辑": "#7c9cff", "人物塑造": "#e08bbf", "节奏爽点": "#f0a851",
  "文笔风格": "#5fc9a8", "一致性": "#b48cf2", "流程规范": "#5fb8e0", "未分类": "#8a8f99",
};
async function loadSkills() {
  try {
    S.skills = await api("/api/skills");
  } catch (e) { S.skills = null; }
  renderSkills();
}

function renderSkills() {
  const box = $("skill-packs");
  if (!box || !S.skills) return;
  const packs = S.skills.packs || [], lessons = S.skills.lessons || [];
  box.innerHTML = packs.map((p) =>
    '<div class="card"><div class="head"><span class="name">📚 ' + esc(p.name) + "</span>" +
    '<span class="tag">' + esc((p.scopes || []).join(" / ")) + "</span>" +
    (p.enabled ? '<span class="tag ok">' + t("启用中") + "</span>" : '<span class="tag">' + t("已停用") + "</span>") + "</div>" +
    '<div class="note">' + esc(p.note || "") + "</div>" +
    '<div class="facts">' + t("正文 ") + "<b>" + p.chars + "</b>" + t(" 字（自动注入该类任务的规划与评审提示词）") + "</div>" +
    '<div class="ops"><button class="ghost small" onclick="skillPackOp(\'' + esc(p.id) + '\', \'' +
    (p.enabled ? "disable" : "enable") + '\')">' + (p.enabled ? t("停用") : t("启用")) + "</button></div></div>").join("")
    || '<div class="empty">' + t("暂无经验包") + "</div>";
  // 分类 chip 过滤：药丸按钮 + 彩色圆点 + 计数，选中态高亮。值 = 分类名，空串 = 全部。
  const cats = S.skills.categories || [];
  const counts = S.skills.counts || {};
  let filt = S.skillCat || "";
  if (filt && !cats.includes(filt)) filt = "";   // 过滤的分类已空，回落全部
  const chipBox = $("skill-cat-chips");
  if (chipBox) {
    const chip = (val, label) =>
      '<button class="cat-chip' + (filt === val ? " active" : "") + '" onclick="skillCatFilter(\'' +
      esc(val) + '\')">' + (val ? '<span class="cdot" style="background:' + CAT_COLOR[val] + '"></span>' : "") +
      esc(label) + "<b>" + (val ? (counts[val] || 0) : lessons.length) + "</b></button>";
    chipBox.innerHTML = cats.length > 1 ? chip("", t("全部")) + cats.map((c) => chip(c, t(c))).join("") : "";
  }
  const shown = filt ? lessons.filter((x) => (x.category || "未分类") === filt) : lessons;
  const cnt = $("skill-count");
  if (cnt) cnt.textContent = lessons.length
    ? (filt ? shown.length + t(" / 共 ") + lessons.length + t(" 条") : lessons.length + t(" 条"))
    : t("（暂无，跑一次任务后自动生成）");
  $("skill-lessons").innerHTML = shown.map((x) =>
    '<div class="card' + (x.enabled === false ? " off" : "") + '"><div class="head">' +
    '<span class="name">' + esc(x.title) + "</span>" +
    (x.category ? '<span class="tag cat" style="color:' + (CAT_COLOR[x.category] || "var(--muted)") + '" ' +
      'onclick="skillCatFilter(\'' + esc(x.category) + '\')" title="' + t("只看该类") + '">' + esc(t(x.category)) + "</span>" : "") +
    '<span class="tag">' + esc(x.scope === "*" ? t("通用") : x.scope) + "</span>" +
    (x.seen > 1 ? '<span class="tag">' + t("出现 ") + x.seen + t(" 次") + "</span>" : "") +
    (x.hits ? '<span class="tag">' + t("已注入 ") + x.hits + t(" 次") + "</span>" : "") +
    (x.enabled === false ? '<span class="tag">' + t("已停用") + "</span>" : "") + "</div>" +
    '<div class="note">' + esc(x.content) + "</div>" +
    '<div class="ops">' +
    '<button class="ghost small" onclick="skillLessonOp(\'' + esc(x.id) + '\', \'' +
    (x.enabled === false ? "enable" : "disable") + '\')">' + (x.enabled === false ? t("启用") : t("停用")) + "</button>" +
    '<button class="danger small" onclick="skillLessonOp(\'' + esc(x.id) + '\', \'delete\')">' + t("删除") + "</button>" +
    "</div></div>").join("") ||
    (filt ? '<div class="empty">' + t("该分类下暂无教训") + "</div>"
          : '<div class="empty">' + t("还没有自动教训——完成一次真实任务后，系统会自己复盘并沉淀。") + "</div>");
}

function skillCatFilter(v) {
  S.skillCat = v || "";
  renderSkills();   // 客户端过滤，无需重拉
}

async function skillPackOp(id, op) {
  try { await api("/api/skills/pack-op", { method: "POST", body: JSON.stringify({ id, op }) }); }
  catch (e) { toast(e.message, true); return; }
  loadSkills();
}

async function skillLessonOp(id, op) {
  if (op === "delete" && !await uiConfirm(t("删除这条教训？"), { ok: t("删除"), danger: true })) return;
  try { await api("/api/skills/lesson-op", { method: "POST", body: JSON.stringify({ id, op }) }); }
  catch (e) { toast(e.message, true); return; }
  loadSkills();
}

/* ---------------------------------------------------------- 自动化（定时任务） */
/* 进页拉一次 + 每次操作后重拉 + 页面可见时每 8s 轮询（离页停表）。
 * 后端 /api/automation → {tasks, templates}；任务/模板操作走子路径。 */
let AUTO_TIMER = null;   // 自动化页轮询句柄

const AUTO_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];   // 0=周一

async function loadAutomation() {
  try {
    const r = await api("/api/automation");
    S.autoTasks = r.tasks || [];
    S.autoTpl = r.templates || [];
  } catch (e) {
    S.autoTasks = null;
  }
  renderAutomation();
}

function startAutoPoll() {
  if (AUTO_TIMER) return;
  AUTO_TIMER = setInterval(() => {
    if (S.tab === "automation" && !document.hidden) loadAutomation();
  }, 8000);
}

function stopAutoPoll() {
  if (AUTO_TIMER) { clearInterval(AUTO_TIMER); AUTO_TIMER = null; }
}

/* kind → 中文可读节奏：「每天 09:00」「每 6 小时」「每周一 09:00」「…执行一次」 */
function autoKindText(tsk) {
  const wd = (n) => (n >= 0 && n <= 6) ? t(AUTO_WD[n]) : "";
  if (tsk.kind === "daily") return t("每天 ") + (tsk.time || "");
  if (tsk.kind === "interval") return t("每 ") + (tsk.interval_hours || 0) + t(" 小时");
  if (tsk.kind === "weekly") return t("每") + wd(tsk.weekday) + " " + (tsk.time || "");
  if (tsk.kind === "once") return (tsk.run_at || "") + t(" 执行一次");
  return t("未设置节奏");
}

/* 模板的建议节奏（复用任务节奏文案，字段带 suggested_ 前缀） */
function autoTplPace(tp) {
  return autoKindText({
    kind: tp.suggested_kind,
    time: tp.suggested_time,
    interval_hours: tp.suggested_interval_hours,
    weekday: tp.suggested_weekday != null ? tp.suggested_weekday : -1,
    run_at: tp.suggested_run_at || "",
  });
}

/* last_status → 徽章：queued=正常灰、error=红「拉起失败」、missed=黄「已错过」 */
function autoStatusTag(tsk) {
  if (tsk.last_status === "error") return '<span class="tag auto-tag-err">' + t("拉起失败") + "</span>";
  if (tsk.last_status === "missed") return '<span class="tag auto-tag-miss">' + t("已错过") + "</span>";
  if (tsk.last_status === "queued") return '<span class="tag">' + t("正常") + "</span>";
  return "";
}

function autoCardHtml(tsk) {
  const flow = tsk.flow ? flowById(tsk.flow) : null;
  return '<div class="card' + (tsk.enabled ? "" : " off") + '"><div class="head">' +
    '<span class="name">' + esc(tsk.name) + "</span>" +
    autoStatusTag(tsk) +
    '<label class="toggle" title="' + t("启用 / 停用") + '"><input type="checkbox"' + (tsk.enabled ? " checked" : "") +
    ' onchange="autoToggle(\'' + esc(tsk.id) + '\', this.checked)"><span>' + (tsk.enabled ? t("启用中") : t("已停用")) + "</span></label>" +
    "</div>" +
    '<div class="auto-kind">' + esc(autoKindText(tsk)) +
    (flow ? '<span class="tag">' + t("流程：") + esc(flow.name) + "</span>" : "") + "</div>" +
    '<div class="auto-prompt">' + esc(tsk.prompt) + "</div>" +
    '<div class="auto-meta">' +
    (tsk.enabled && tsk.next_run ? "<span>" + t("下次运行 ") + esc(tsk.next_run) + "</span>" : "") +
    "<span>" + t("已运行 ") + (tsk.run_count || 0) + t(" 次") + "</span>" +
    "</div>" +
    '<div class="ops">' +
    '<button class="primary small" onclick="autoRunNow(\'' + esc(tsk.id) + '\')">' + t("立即执行") + "</button>" +
    '<button class="ghost small" onclick="autoEdit(\'' + esc(tsk.id) + '\')">' + t("编辑") + "</button>" +
    '<button class="danger small" onclick="autoRemove(\'' + esc(tsk.id) + '\')">' + t("删除") + "</button>" +
    "</div></div>";
}

function renderAutomation() {
  const box = $("auto-list");
  if (!box) return;
  if (!S.autoTasks) {
    box.innerHTML = '<div class="empty">' + t("加载失败：服务未连接") + "</div>";
  } else {
    let shown = S.autoTasks;
    if (S.autoFilter === "on") shown = shown.filter((x) => x.enabled);
    if (S.autoFilter === "off") shown = shown.filter((x) => !x.enabled);
    box.innerHTML = shown.map(autoCardHtml).join("") ||
      (S.autoTasks.length ? '<div class="empty">' + t("没有符合条件任务") + "</div>"
        : '<div class="empty">' + t("还没有定时任务——点右上「新建定时任务」，或从下面的模板一键创建。") + "</div>");
  }
  const tpl = $("auto-tpl");
  if (tpl) {
    tpl.innerHTML = (S.autoTpl || []).map((tp) =>
      '<div class="card auto-tpl-card"><div class="head"><span class="name">' + esc(tp.name) + "</span></div>" +
      '<div class="note">' + esc(tp.desc || "") + "</div>" +
      '<div class="ops"><span class="auto-pace">' + esc(autoTplPace(tp)) + "</span>" +
      '<button class="ghost small" onclick="autoTemplate(\'' + esc(tp.id) + '\')">' + t("用此模板") + "</button></div></div>"
    ).join("") || '<div class="empty">' + t("暂无模板") + "</div>";
  }
}

/* 创建 / 编辑表单（通用弹框）：task 空 = 新建；tpl = 模板预填 */
async function autoForm(task, tpl) {
  if (!S.flows) await loadFlows();   // 流程下拉数据源（一般启动时已就绪）
  const f = task || {};
  const kind = f.kind || (tpl && tpl.suggested_kind) || "daily";
  const curFlow = f.flow || (tpl && tpl.suggested_flow) || "";
  const wdOpts = AUTO_WD.map((w, i) =>
    '<option value="' + i + '"' + (Number(f.weekday) === i || (!task && tpl && tpl.suggested_weekday === i) ? " selected" : "") + ">" + t(w) + "</option>").join("");
  const runAt = f.run_at ? String(f.run_at).replace(" ", "T").slice(0, 16) : "";
  const body = '<div class="form">' +
    '<div class="field"><label>' + t("名称") + ' <span class="req">*</span></label><input id="au-name" value="' +
      esc(f.name || (tpl ? tpl.name : "")) + '" placeholder="' + t("例：每日仓库巡检") + '"></div>' +
    '<div class="field"><label>' + t("执行内容") + ' <span class="req">*</span></label><textarea id="au-prompt" rows="5" placeholder="' +
      t("到点要完成什么，一句话或分步说明均可") + '">' + esc(f.prompt || (tpl ? tpl.prompt : "")) + "</textarea></div>" +
    '<div class="field"><label>' + t("工作目录") + '</label><input id="au-workdir" value="' + esc(f.workdir || "") +
      '" placeholder="' + t("留空 = 默认保存路径；或填绝对路径") + '"></div>' +
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("类型") + '</label><select id="au-kind">' +
      '<option value="daily"' + (kind === "daily" ? " selected" : "") + ">" + t("每天") + "</option>" +
      '<option value="interval"' + (kind === "interval" ? " selected" : "") + ">" + t("每隔 N 小时") + "</option>" +
      '<option value="weekly"' + (kind === "weekly" ? " selected" : "") + ">" + t("每周") + "</option>" +
      '<option value="once"' + (kind === "once" ? " selected" : "") + ">" + t("仅一次") + "</option>" +
      "</select></div>" +
    '<div class="field"><label>' + t("流程") + '</label><select id="au-flow">' +
      '<option value="">' + t("默认流程") + "</option>" +
      (S.flows || []).map((fl) =>
        '<option value="' + esc(fl.id) + '"' + (fl.id === curFlow ? " selected" : "") + ">" + esc(fl.name) + "</option>").join("") +
      "</select></div>" +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field au-fld" data-k="time"><label>' + t("时间") + '</label><input id="au-time" type="time" value="' +
      esc(f.time || (tpl && tpl.suggested_time) || "09:00") + '"></div>' +
    '<div class="field au-fld hidden" data-k="interval"><label>' + t("间隔小时（1-720）") + '</label><input id="au-interval" type="number" min="1" max="720" value="' +
      (f.interval_hours || (tpl && tpl.suggested_interval_hours) || 6) + '"></div>' +
    '<div class="field au-fld hidden" data-k="weekday"><label>' + t("星期几") + '</label><select id="au-weekday">' + wdOpts + "</select></div>" +
    '<div class="field au-fld hidden" data-k="runat"><label>' + t("执行时间") + '</label><input id="au-runat" type="datetime-local" value="' + esc(runAt) + '"></div>' +
    "</div>" +
    '<p class="hint">' + t("到点自动把执行内容作为一个新任务跑起来；错过的一次性任务不补跑。") + "</p>" +
    "</div>";
  openModal(task ? t("编辑定时任务") : t("新建定时任务"), body, "");
  const foot = $("modal-foot");
  if (foot) foot.innerHTML = '<button class="primary" onclick="saveAutoForm(\'' + esc(task ? task.id : "") + '\')">' + t("保存") + "</button>" +
    '<button class="ghost" onclick="closeModal()">' + t("取消") + "</button>";
  const kindSel = $("au-kind");
  if (kindSel) {
    kindSel.addEventListener("change", autoFormSyncKind);
    autoFormSyncKind();
  }
}

/* 类型 → 字段联动：daily/weekly→时间；weekly→星期几；interval→N 小时；once→未来时间 */
function autoFormSyncKind() {
  const sel = $("au-kind");
  if (!sel) return;
  const kind = sel.value;
  const show = {
    time: kind === "daily" || kind === "weekly",
    interval: kind === "interval",
    weekday: kind === "weekly",
    runat: kind === "once",
  };
  document.querySelectorAll("#modal-body .au-fld").forEach((el) =>
    el.classList.toggle("hidden", !show[el.dataset.k]));
}

async function saveAutoForm(id) {
  const name = ($("au-name").value || "").trim();
  const prompt = ($("au-prompt").value || "").trim();
  if (!name) { toast(t("请填写名称"), true); return; }
  if (!prompt) { toast(t("请填写执行内容"), true); return; }
  const kind = $("au-kind").value;
  const payload = { name, prompt, workdir: ($("au-workdir").value || "").trim(), kind, flow: $("au-flow").value };
  if (kind === "daily" || kind === "weekly") payload.time = $("au-time").value;
  if (kind === "weekly") payload.weekday = parseInt($("au-weekday").value, 10);
  if (kind === "interval") payload.interval_hours = parseInt($("au-interval").value, 10) || 0;
  if (kind === "once") {
    payload.run_at = $("au-runat").value;
    if (!payload.run_at) { toast(t("请选择执行时间"), true); return; }
  }
  try {
    if (id) await api("/api/automation/" + encodeURIComponent(id), { method: "POST", body: JSON.stringify(payload) });
    else await api("/api/automation", { method: "POST", body: JSON.stringify(payload) });
  } catch (e) { toast(t("保存失败：") + e.message, true); return; }
  closeModal();
  toast(id ? t("定时任务已更新") : t("定时任务已创建，到点自动运行"));
  loadAutomation();
}

async function autoToggle(id, enabled) {
  try {
    await api("/api/automation/" + encodeURIComponent(id) + "/toggle",
      { method: "POST", body: JSON.stringify({ enabled: !!enabled }) });
  } catch (e) { toast(e.message, true); }
  loadAutomation();
}

async function autoRunNow(id) {
  const tsk = (S.autoTasks || []).find((x) => x.id === id);
  if (tsk && tsk.last_status === "error") {
    toast(t("上次拉起失败，请先检查工作目录 / 流程配置后再试"), true);
    return;
  }
  try {
    await api("/api/automation/" + encodeURIComponent(id) + "/run", { method: "POST", body: "{}" });
  } catch (e) { toast(e.message, true); return; }
  toast(t("已开始执行，可在任务树查看新运行"));
  loadAutomation();
}

async function autoEdit(id) {
  const tsk = (S.autoTasks || []).find((x) => x.id === id);
  if (tsk) autoForm(tsk);
}

async function autoRemove(id) {
  const tsk = (S.autoTasks || []).find((x) => x.id === id);
  if (!await uiConfirm(t("删除定时任务「") + (tsk ? tsk.name : id) + t("」？已产生的运行记录不受影响。"),
      { ok: t("删除"), danger: true })) return;
  try {
    await api("/api/automation/" + encodeURIComponent(id) + "/delete", { method: "POST", body: "{}" });
  } catch (e) { toast(e.message, true); return; }
  toast(t("已删除"));
  loadAutomation();
}

/* 模板卡 → 预填创建表单（prompt/name + suggested_kind/time/interval/flow） */
function autoTemplate(tplId) {
  const tp = (S.autoTpl || []).find((x) => x.id === tplId);
  if (tp) autoForm(null, tp);
}

/* ---------------------------------------------------------- 插件市场 */
/* /api/market → {catalog, categories(闭集)}；搜索/分类/状态全在本地过滤 */
async function loadMarket() {
  try { S.market = await api("/api/market"); }
  catch (e) { S.market = null; }
  renderMarket();
}

function mkScopeText(scopes) {
  const list = scopes || [];
  if (!list.length || list.indexOf("*") >= 0) return t("全部流程");
  return list.map((s) => { const f = flowById(s); return f ? f.name : s; }).join(" / ");
}

/* 体量：「约 1.7k 字」；千字以下原样数字 */
function mkCharsText(n) {
  n = Number(n) || 0;
  if (n >= 1000) return t("约 ") + (n / 1000).toFixed(1).replace(/\.0$/, "") + "k " + t("字");
  return t("约 ") + n + " " + t("字");
}

function mkCardHtml(p) {
  let ops;
  if (p.builtin) ops = '<button class="ghost small" disabled>' + t("已内置") + "</button>";
  else if (p.installed) ops = '<span class="tag ok">' + t("已安装") + "</span>" +
    '<button class="ghost small" onclick="mkRemove(\'' + esc(p.id) + '\')">' + t("卸载") + "</button>";
  else ops = '<button class="primary small" onclick="mkInstall(\'' + esc(p.id) + '\')">' + t("安装") + "</button>";
  return '<div class="card mk-card"><div class="head">' +
    '<span class="name">' + esc(p.name) + "</span>" +
    '<span class="tag">' + esc(t(p.category || "")) + "</span>" +
    "</div>" +
    '<div class="note">' + esc(p.desc || "") + "</div>" +
    '<div class="mk-meta"><span>' + t("适用：") + esc(mkScopeText(p.scopes)) + "</span><span>" + esc(mkCharsText(p.chars)) + "</span></div>" +
    '<div class="ops">' + ops + "</div></div>";
}

function renderMarket() {
  const grid = $("mk-grid");
  if (!grid) return;
  const data = S.market;
  if (!data) {
    grid.innerHTML = '<div class="empty">' + t("加载失败：服务未连接") + "</div>";
    const cnt0 = $("mk-count");
    if (cnt0) cnt0.textContent = "";
    return;
  }
  // 分类下拉：闭集（含 0 条类目），只在首次填充，之后保留用户选择
  const catSel = $("mk-cat");
  if (catSel && !catSel.options.length) {
    catSel.innerHTML = '<option value="">' + t("全部分类") + "</option>" +
      (data.categories || []).map((c) => '<option value="' + esc(c) + '">' + esc(t(c)) + "</option>").join("");
  }
  const q = (($("mk-search") || {}).value || "").trim().toLowerCase();
  const cat = catSel ? catSel.value : "";
  const st = S.mkState || "all";
  let items = data.catalog || [];
  if (cat) items = items.filter((p) => p.category === cat);
  if (st === "installed") items = items.filter((p) => p.installed);
  if (st === "not") items = items.filter((p) => !p.installed);
  if (q) items = items.filter((p) =>
    (p.name || "").toLowerCase().indexOf(q) >= 0 || (p.desc || "").toLowerCase().indexOf(q) >= 0);
  const cnt = $("mk-count");
  if (cnt) cnt.textContent = t("共 ") + items.length + t(" 个");
  grid.innerHTML = items.map(mkCardHtml).join("") || '<div class="empty">' + t("没有符合条件插件") + "</div>";
}

async function mkInstall(id) {
  try {
    const r = await api("/api/market/" + encodeURIComponent(id) + "/install", { method: "POST", body: "{}" });
    toast(r && r.already ? t("该插件已安装过") : t("安装成功，可到「经验库」查看"));
  } catch (e) { toast(e.message, true); return; }
  loadMarket();
}

async function mkRemove(id) {
  if (!await uiConfirm(t("卸载该插件？它会从经验库中移除。"), { ok: t("卸载"), danger: true })) return;
  try {
    await api("/api/market/" + encodeURIComponent(id) + "/remove", { method: "POST", body: "{}" });
  } catch (e) { toast(e.message, true); return; }
  toast(t("已卸载"));
  loadMarket();
}

/* ---------------------------------------------------------- 编排设置（编排者 + 并发设置） */
async function loadOrchestrator() {
  try {
    const r = await api("/api/orchestrator");
    S.orch = r.orchestrator || null;
  } catch (e) { S.orch = null; }
  renderOrchSide();
  renderOrch();
}

/* 侧栏左下角中间的「编排者供应商」：显示 CodeBee 自己的智能体当前用的厂商，
 * 点一下到设置里的「编排设置」更换（可选范围＝已接入的供应商）。 */
function renderOrchSide() {
  const btn = $("btn-prov-side"), txt = $("prov-side-text"), dot = $("prov-side-dot");
  if (!btn || !txt || !dot) return;
  const o = S.orch || {};
  const name = (o.provider_name || "").trim().replace(/^\[[^\]]+\]\s*/, "");  // 去掉 "[CC] " 之类前缀
  const model = (o.model || "").trim();
  if (!name) {
    txt.textContent = t("未选厂商");
    dot.className = "pdot";
    btn.title = t("尚未选择编排者供应商\\n点击到「编排设置」，从已接入的厂商里选一个");
    return;
  }
  txt.textContent = name;
  dot.className = "pdot " + (o.ready ? "ok" : "warn");
  btn.title = t("编排者供应商：") + name + (model ? " · " + model : "") +
    (o.ready ? t("（生效中）") : t("（未生效：检查密钥 / 启停 / 模型）")) +
    t("\\n点击到「编排设置」更换");
}

async function loadSettings() {
  try {
    S.settings = await api("/api/settings");
    const inp = $("set-workers");
    if (inp && S.settings) inp.value = S.settings.max_concurrent_jobs;
    const wd = $("set-workdir"), hint = $("set-workdir-hint");
    if (wd && S.settings) wd.value = S.settings.default_workdir_effective || "";
    if (hint && S.settings) {
      const custom = !!(S.settings.default_workdir || "").trim();
      hint.textContent = custom ? t("当前生效：") + S.settings.default_workdir_effective + t("（自定义）")
                                : t("当前生效：") + S.settings.default_workdir_effective + t("（内置默认，尚未自定义）");
    }
    // 新任务表单：目录留空即落到默认路径，给一句话提示
    const fhint = $("f-workdir-hint");
    if (fhint && S.settings) fhint.textContent = t("留空则保存到：") + (S.settings.default_workdir_effective || "");
  } catch (e) { /* 忽略 */ }
}

/* 保存默认保存路径；可选把旧默认路径下的现有任务目录迁移到新路径 */
async function saveDefaultWorkdir() {
  const msg = $("settings-msg");
  try {
    const r = await api("/api/settings/default-workdir", { method: "POST", body: JSON.stringify({
      path: ($("set-workdir").value || "").trim(),
      migrate: $("set-workdir-migrate") && $("set-workdir-migrate").checked,
    }) });
    S.settings = r.settings;
    const n = (r.moved || 0), sk = (r.skipped || 0);
    if (msg) {
      msg.className = "msg ok";
      msg.textContent = t("默认保存路径已更新：") + r.settings.default_workdir_effective +
        (n ? t("；已迁移 ") + n + t(" 个任务目录") : "") + (sk ? t("（跳过 ") + sk + t(" 个运行中/迁移失败）") : "");
    }
    loadSettings();
  } catch (e) {
    if (msg) { msg.className = "msg err"; msg.textContent = e.message; }
  }
}

/* 编排者供应商下拉：三种协议都支持直连（含 google），不限于可注入 CLI 的 */
function orchProvs() {
  return (S.providers || []).filter((p) => p.api_key && p.enabled !== false);
}

/* 编排者模型下拉的选项：当前值 + 该供应商已启用模型（按优先级） */
function orchModelHtml(prov, curModel) {
  const names = (prov ? (prov.models || []).filter((m) => !m.hidden && m.enabled !== false)
    .sort((a, b) => (a.priority || 0) - (b.priority || 0)).map((m) => m.name) : []);
  const all = Array.from(new Set([curModel].concat(names).filter(Boolean)));
  return '<option value="">（用供应商默认模型）</option>' +
    all.map((m) => '<option value="' + esc(m) + '"' + (m === curModel ? " selected" : "") + ">" + esc(m) + "</option>").join("");
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
    esc(p.name) + t("（") + esc(p.protocol) + "）</option>").join("");
  const curModel = S.orch.model || (sel ? sel.model : "") || "";
  box.innerHTML =
    '<div class="grid-2">' +
    '<div class="field"><label>编排者供应商</label><select id="orch-prov">' + opts + "</select></div>" +
    '<div class="field"><label>编排者模型</label><select id="orch-model">' + orchModelHtml(sel, curModel) + "</select></div></div>" +
    '<div class="ops"><label class="toggle"><input type="checkbox" id="orch-enabled"' +
    (S.orch.enabled ? " checked" : "") + "> 启用编排者（规划 / 难度判定 / 写作大纲）</label>" +
    '<button class="ghost small" onclick="saveOrchestrator()">保存</button>' +
    '<button class="ghost small" onclick="testOrchestrator()">测试连通</button>' +
    '<span id="orch-test" class="msg"></span></div>' +
    (S.orch.enabled && !S.orch.ready ? '<p class="hint warn">当前配置不生效：请检查供应商密钥、启停状态与模型选择。</p>' : "");
  // 换供应商只刷新模型下拉，不整块重绘：整块重绘会按 S.orch 重写 selected，
  // 把用户刚选中的供应商弹回旧值，保存下去的还是旧值
  $("orch-prov").addEventListener("change", () => {
    const p = orchProvs().find((x) => x.id === $("orch-prov").value) || null;
    $("orch-model").innerHTML = orchModelHtml(p, (p ? p.model : "") || "");
  });
}

async function saveOrchestrator() {
  try {
    const r = await api("/api/orchestrator", { method: "POST", body: JSON.stringify({
      provider_id: $("orch-prov").value,
      model: $("orch-model").value,
      enabled: $("orch-enabled").checked }) });
    S.orch = r.orchestrator;
    S.orchSig = null;
    renderOrchSide();   // 侧栏指示同步
    renderOrch();
    toast(t("编排者配置已保存"));
  } catch (e) { toast(t("保存失败：") + e.message, true); }
}

async function testOrchestrator() {
  const el = $("orch-test");
  if (!el) return;
  el.textContent = t("测试中…"); el.className = "msg";
  try {
    const r = await api("/api/orchestrator/test", { method: "POST" });
    el.className = "msg " + (r.ok ? "ok" : "err");
    el.textContent = r.ok ? ("✓ " + r.provider + " · " + r.model + t(" 回应正常")) : ("✗ " + (r.error || t("失败")));
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
    if (msg) { msg.className = "msg ok"; msg.textContent = t("已保存：最大并发 ") + r.workers + t(" 个任务"); }
  } catch (e) {
    if (msg) { msg.className = "msg err"; msg.textContent = e.message; }
  }
}

/* ---------------------------------------------------------- 关于与更新（selfupdate）
 * 后端 /api/selfupdate：mode=npm 才可自动升级；repo（git clone）提示 git pull。
 * 升级 = 建 mgmt run 跑 npm install -g @latest（日志实时落盘）→ 完成后点「重启」，
 * 服务就地拉起新实例并自退（--wait-port 等端口释放），前端轮询恢复后自动刷新。 */
let SU = null;          // 最近一次 check() 结果
let SU_TIMER = null;    // 升级 run 轮询句柄

const SU_MODE_TXT = { npm: "npm 全局安装", repo: "开发仓库（git clone）", source: "源码拷贝", other: "未知安装方式" };

/* 最近一次「升级 Tutti 本体」run 是否已完成（决定重启按钮显隐；查 S.runs，
 * 页面刷新后按钮不丢）。runs 里 op=selfupgrade 的那条即升级 run。 */
function suLastUpgradeRun() {
  return (S.runs || []).find((r) => r.op === "selfupgrade") || null;
}

async function loadSelfupdate(force) {
  try {
    SU = await api("/api/selfupdate" + (force ? "?force=1" : ""));
  } catch (e) { SU = null; }
  renderSu();
  return SU;
}

function renderSu() {
  const info = $("su-info");
  if (!info) return;
  if (!SU) { info.textContent = t("无法获取版本信息（服务未连接）"); return; }
  const modeTxt = t(SU_MODE_TXT[SU.mode] || "未知安装方式");
  const cur = SU.current ? "v" + SU.current : t("（未同步版本号）");
  let html = "<p class=\"hint\">" + t("当前版本") + "：<b>" + cur + "</b>　·　" + t("安装方式") + "：" + modeTxt + "</p>";
  if (SU.has_update) {
    html += "<p class=\"hint\"><b>" + t("发现新版本") + " v" + SU.latest +
      "　<a href=\"#\" onclick=\"event.preventDefault();suApply()\">" + t("立即升级") + "</a></b></p>";
  } else if (SU.mode === "npm" && !SU.note) {
    html += "<p class=\"hint\">" + t("已是最新版。") + "</p>";
  }
  info.innerHTML = html;
  const note = $("su-note");
  if (note) note.textContent = SU.note || "";
  const b = $("su-check");
  if (b) b.disabled = false;
  $("su-apply").classList.toggle("hidden", !SU.has_update);
  const last = suLastUpgradeRun();
  $("su-restart").classList.toggle("hidden", !(last && last.status === "done"));
}

async function suCheck() {
  const b = $("su-check");
  if (b) b.disabled = true;
  await loadSelfupdate(true);
}

async function suApply() {
  if (!(await uiConfirm(t("升级会下载并安装最新版（约 1-2 分钟），期间服务继续可用。现在开始？"),
      { ok: t("开始升级") }))) return;
  const prog = $("su-progress");
  try {
    const r = await api("/api/selfupdate/apply", { method: "POST", body: JSON.stringify({}) });
    toast(t("升级已开始，日志见运行记录"));
    const runId = r.run_id;
    $("su-apply").disabled = true;
    if (prog) prog.textContent = t("正在升级…");
    clearInterval(SU_TIMER);
    SU_TIMER = setInterval(async () => {
      try {
        const run = await api("/api/runs/" + encodeURIComponent(runId));
        if (run && run.status && run.status !== "running" && run.status !== "queued") {
          clearInterval(SU_TIMER); SU_TIMER = null;
          $("su-apply").disabled = false;
          if (prog) prog.textContent = "";
          if (run.status === "done") {
            toast(t("升级完成！点「重启服务生效」换新版本"));
            refreshState();
          } else {
            toast(t("升级失败，详情见运行记录"), true);
          }
        }
      } catch (e) { /* 轮询抖动忽略 */ }
    }, 2500);
  } catch (e) {
    if (prog) prog.textContent = "";
    toast(e.message, true);
  }
}

async function suRestart() {
  if (!(await uiConfirm(t("重启服务换上新版本？页面会短暂断开并自动恢复。"), { ok: t("重启") }))) return;
  const prog = $("su-progress");
  try {
    await api("/api/selfupdate/restart", { method: "POST", body: JSON.stringify({}) });
  } catch (e) { /* 请求发出即视为成功：旧进程可能已退出 */ }
  if (prog) prog.textContent = t("正在重启…");
  toast(t("服务重启中，几秒后自动恢复"));
  let tries = 0;
  const t2 = setInterval(async () => {
    tries++;
    try {
      await api("/api/control");
      clearInterval(t2);
      location.reload();
    } catch (e) {
      if (tries > 60) { clearInterval(t2); if (prog) prog.textContent = t("重启超时，请手动刷新页面"); }
    }
  }, 1500);
}

/* 页面加载后静默查一次新版本（服务端有 10 分钟缓存）；同版本只提醒一次 */
async function suStartupCheck() {
  const s = await loadSelfupdate(false);
  if (s && s.has_update && localStorage.getItem("su.seen") !== s.latest) {
    localStorage.setItem("su.seen", s.latest);
    toast(t("发现新版本 v") + s.latest + t("，可在「关于与更新」一键升级"));
  }
}

/* ---------------------------------------------------------- 页签 & 初始化 */
const TAB_TITLES = { tasks: "任务", runs: "运行记录", automation: "自动化", usage: "用量统计", agents: "智能体管理", models: "模型接入", bindings: "CLI 绑定", skills: "经验库", market: "插件市场", orch: "编排设置", appearance: "皮肤", about: "关于与更新" };
const SET_TABS = new Set(Object.keys(TAB_TITLES));   // 设置导航里的子页（__phone 是弹框，不算）

function tabTitle(name) {
  return t(TAB_TITLES[name]) || t("设置");
}



/* ---------------------------------------------------------- 用量统计 */
/* 台账：/api/usage 按天/工具(CLI)/智能体/模型/角色/任务类型多维聚合；纯 SVG 画趋势 */

function fmtTok(n) {
  n = Number(n) || 0;
  if (n >= 1e8) return (n / 1e8).toFixed(2) + t("亿");
  if (n >= 1e4) return (n / 1e4).toFixed(n >= 1e6 ? 0 : 1) + t("万");
  return n.toLocaleString("en-US");
}

function fmtUsd(x) {
  x = Number(x) || 0;
  if (!x) return "$0";
  if (x < 0.01) return "$" + x.toFixed(4);
  if (x < 1) return "$" + x.toFixed(3);
  return "$" + x.toFixed(2);
}

function fmtDur(s) {
  s = Number(s) || 0;
  if (s >= 3600) return (s / 3600).toFixed(1) + "h";
  if (s >= 60) return Math.round(s / 60) + "min";
  return Math.round(s) + "s";
}

/* 工具 kind → 显示名 */
function toolName(key) {
  return { codex: "Codex CLI", claude: "Claude Code", qwen: "Qwen CLI",
           opencode: "OpenCode", aider: "Aider", generic: t("自定义 CLI"),
           orchestrator: t("编排者 · 直连API") }[key] || key;
}

/* 角色 → 中文。带后缀的角色保留区分度，否则 implement-1/4…implement-4/4
 * 会全部显示成「实现」，维度表里看着像重复行。
 *   implement-2/4 → 实现 2/4    draft-c3 → 起草 第3章
 *   fix-r2        → 修复 第2轮   global-critique → 全局评审 */
function roleName(r) {
  const raw = String(r || "");
  const FULL = { "global-critique": t("全局评审"), "ai-repair": t("AI修复") };
  if (FULL[raw]) return FULL[raw];
  const ZH = { plan: t("规划"), implement: t("实现"), review: t("评审"), verify: t("验证"),
               fix: t("修复"), draft: t("起草"), critique: t("评审"), revise: t("修订"),
               outline: t("大纲"), merge: t("合并"), smoke: t("冒烟"), global: t("全局评审"),
               orch: t("连通测试") };
  const m = raw.match(/^([A-Za-z]+)(?:-(.+))?$/);
  if (!m) return raw;
  const base = ZH[m[1].toLowerCase()] || m[1];
  let suf = m[2];
  if (!suf) return base;
  const ch = suf.match(/^c(\d+)$/);
  if (ch) return base + t(" 第") + ch[1] + t("章");
  const rd = suf.match(/^r(\d+)$/);
  if (rd) return base + t(" 第") + rd[1] + t("轮");
  return base + " " + suf;
}

async function loadUsage() {
  const kpis = $("usage-kpis");
  if (!kpis) return;
  try {
    S.usage = await api("/api/usage?days=" + encodeURIComponent(usageRange()));
    renderUsage();
  } catch (e) {
    // 失败时四个区都要给出可见反馈，否则标题下全空、看着像页面坏了
    const hint = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    kpis.innerHTML = hint;
    ["usage-trend", "usage-dims", "usage-recent"].forEach((id) => {
      const el = $(id);
      if (el) el.innerHTML = hint;
    });
  }
}

/* 时间范围默认「今天」：看用量最常关心的是当下消耗，历史区间按需切换 */
const USAGE_DAYS_KEY = "orch.usageDays";

function usageRange() {
  const raw = S.usageDays;
  const n = raw === undefined || raw === null || raw === "" ? 1 : Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : 1;
}

function setUsageDays(days) {
  S.usageDays = Number(days);
  try { localStorage.setItem(USAGE_DAYS_KEY, String(S.usageDays)); } catch (e) { /* 隐私模式忽略 */ }
  document.querySelectorAll("#usage-ranges [data-days]").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.days) === S.usageDays));
  loadUsage();
}

/* 进入用量页时对齐分段控件的选中态（默认今天；记忆上次选择） */
function syncUsageRange() {
  if (S.usageDays === undefined || S.usageDays === null || S.usageDays === "") {
    const saved = localStorage.getItem(USAGE_DAYS_KEY);
    S.usageDays = saved === null || saved === "" ? 1 : Number(saved);
  }
  const cur = usageRange();
  document.querySelectorAll("#usage-ranges [data-days]").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.days) === cur));
}

/* 语言切换：选完立刻 setLang + 重画（动态内容用模板字面量写死的仍是中文——已知限制）。 */
function syncLangMode() {
  const lang = getLang();
  document.querySelectorAll("#lang-mode [data-lang]").forEach((b) =>
    b.classList.toggle("active", b.dataset.lang === lang));
}

function setLangBtn(lang) {
  if (getLang() === lang) return;
  setLang(lang);
  // 失效全部渲染签名，让下一次 render() 把所有动态内容重画一遍
  S.catSig = ""; S.modelsSig = ""; S.bindSig = ""; S.orchSig = ""; S.sideSig = ""; S.taskSig = ""; S.mgmt = {};
  syncLangMode();
  // 浏览器标签标题
  document.title = (lang === "en" ? "CodeBee · Multi-agent Orchestrator" : "CodeBee · 多智能体编排台");
  applyI18n();
  render();
  // 侧栏区头两颗钮的 title/aria 是 JS 按状态写的，applyI18n 会用静态词条盖掉，这里重刷
  paintArchToggle();
  syncSideExpandBtn();
  renderCodePreviews();   // 预览徽章是动态文案：语言切换时若停在皮肤页要跟着换
  // 重画还在缓存里的页面标题
  const title = $("page-title");
  if (title && S.tab) title.textContent = tabTitle(S.tab);
  // 同步顶栏的连接状态文案（applyI18n 不会处理 textContent 写入的）
  const cn = $("conn");
  if (cn) {
    if (cn.classList.contains("ok")) cn.textContent = t("已连接");
    else if (cn.textContent === t("重连中…") || cn.textContent === "重连中…") cn.textContent = t("重连中…");
    else cn.textContent = t("连接失败");
  }
}

/* 卡片标签：图标 + 名称（图标用精灵表，stroke=currentColor 自动跟随主题） */
function kpiCard(label, value, sub, wide, icon) {
  const ico = icon ? '<svg class="ico" aria-hidden="true"><use href="#' + icon + '"></use></svg>' : "";
  return '<div class="kpi' + (wide ? " wide" : "") + '"><div class="kpi-v">' + value +
    '</div><div class="kpi-l">' + ico + esc(label) + "</div>" +
    (sub ? '<div class="kpi-s">' + esc(sub) + "</div>" : "") + "</div>";
}

/* 单日没有趋势可言，一根孤柱悬在空白里很难看：改画全宽的
 * 输入/缓存/输出构成条 + 汇总信息头，信息量也更大。 */
function usageSingleDay(d) {
  const tok = d.tokens || 0;
  const p = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const today = now.getFullYear() + "-" + p(now.getMonth() + 1) + "-" + p(now.getDate());
  const dayTxt = String(d.day).slice(5) + (d.day === today ? t(" · 今天") : "");
  const head = '<div class="us-head"><span class="us-day">' + esc(dayTxt) + "</span>" +
    '<span class="us-sum">' + esc(fmtTok(tok) + t(" tokens · 调用 ") + (d.calls || 0) + t(t(" 次 · ")) + fmtUsd(d.cost_usd)) + "</span></div>";
  if (tok <= 0) return head + '<div class="us-bar us-empty"><span>当天暂无用量</span></div>';
  const seg = (cls, val, label) => {
    const pct = (val || 0) * 100 / tok;
    if (pct <= 0) return "";
    // 太窄的段不放内嵌文字（挤成一团），数值交给底部明细行
    const inner = pct >= 10 ? "<span>" + label + " " + Math.round(pct) + "%</span>" : "";
    return '<div class="us-seg us-' + cls + '" style="width:' + pct.toFixed(2) + '%"' +
      ' title="' + label + " " + fmtTok(val || 0) + t("（") + pct.toFixed(1) + '%）">' + inner + "</div>";
  };
  const other = Math.max(0, tok - (d.input || 0) - (d.output || 0));
  return head +
    '<div class="us-bar">' +
    seg("in", d.input, t("输入")) +
    seg("ca", other, t("缓存/其他")) +
    seg("out", d.output, t("输出")) +
    "</div>" +
    '<div class="us-foot">输入 ' + esc(fmtTok(d.input || 0)) + t(" · 缓存/其他 ") + esc(fmtTok(other)) +
    t(" · 输出 ") + esc(fmtTok(d.output || 0)) + "</div>";
}

/* 每日堆叠柱状图：输入(accent) / 缓存(ok) / 输出(accent2)，悬浮出明细。
 * 槽位布局：每天一个等宽槽、柱子在槽内居中，柱子沿全宽均匀分布；
 * 无数据的天画基线小短柱占位，不再是一片空白里悬着几根孤柱。 */
/* 多日横向构成条（2~14 天）："单日构成卡"的逐日版——每天一行全宽堆叠条，
 * 日期在左、总量在右；零用量那天画灰色的细占位行。天数多时行太多，仍走竖柱。 */
function usageRowsHtml(byDay) {
  const p = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const today = now.getFullYear() + "-" + p(now.getMonth() + 1) + "-" + p(now.getDate());
  const rows = byDay.map((d) => {
    const tok = d.tokens || 0;
    const tip = esc(d.day + "　总 " + fmtTok(tok) + (tok > 0 ? "（输入 " + fmtTok(d.input || 0) +
      " · 输出 " + fmtTok(d.output || 0) + " · 其他/缓存 " + fmtTok(Math.max(0, tok - (d.input || 0) - (d.output || 0))) + "）" : "") +
      "\n调用 " + (d.calls || 0) + t(" 次 · ") + fmtUsd(d.cost_usd));
    let track;
    if (tok > 0) {
      const seg = (cls, v) => {
        const pct = (v || 0) * 100 / tok;
        return pct > 0 ? '<div class="ur-seg us-' + cls + '" style="width:' + pct.toFixed(2) + '%" title="' + tip + '"></div>' : "";
      };
      track = seg("in", d.input) + seg("ca", Math.max(0, tok - (d.input || 0) - (d.output || 0))) + seg("out", d.output);
    } else {
      track = '<div class="ur-zero" title="' + tip + '">无用量</div>';
    }
    return '<div class="ur-row' + (tok > 0 ? "" : " zero") + (d.day === today ? " today" : "") + '">' +
      '<span class="ur-day">' + esc(String(d.day).slice(5)) + "</span>" +
      '<div class="ur-track">' + track + "</div>" +
      '<span class="ur-sum">' + (tok > 0 ? esc(fmtTok(tok)) : "—") + "</span></div>";
  }).join("");
  const legend = '<div class="ur-legend">' +
    '<span><i class="us-in"></i>输入</span><span><i class="us-ca"></i>缓存/其他</span><span><i class="us-out"></i>输出</span></div>';
  return '<div class="usage-rows">' + legend + rows + "</div>";
}

function usageTrendSvg(byDay) {
  if (!byDay || !byDay.length) return '<p class="hint">（暂无数据）</p>';
  if (byDay.length === 1) return '<div class="usage-single">' + usageSingleDay(byDay[0]) + "</div>";
  if (byDay.length <= 14) return usageRowsHtml(byDay);
  const W = 720, H = 210, padT = 30, padB = 24, padL = 6, padR = 6;  // padT 给图例行留净空
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = Math.max(1, ...byDay.map((d) => (d.tokens || 0)));
  const n = byDay.length;
  const slot = iw / n;
  const gap = Math.max(1, Math.min(slot * 0.25, 36));
  const bw = Math.max(2, Math.min(48, slot - gap));
  const xOf = (i) => padL + i * slot + (slot - bw) / 2;
  const yBase = padT + ih;
  let bars = "", labels = "";
  const labelStep = Math.max(1, Math.ceil(n / 9));
  // 抽稀 x 轴标签：末日必须标；若与前一标签太近（< 半步长）则挤掉前者防重叠
  const labeled = new Set();
  for (let i = 0; i < n; i += labelStep) labeled.add(i);
  if (labeled.has(n - 1) || labeled.size === 0) labeled.add(n - 1);
  else if (n - 1 - [...labeled].pop() < labelStep / 2) { labeled.delete([...labeled].pop()); labeled.add(n - 1); }
  else labeled.add(n - 1);
  byDay.forEach((d, i) => {
    const x = xOf(i);
    const tok = d.tokens || 0;
    const dayTxt = String(d.day).slice(5);
    if (tok > 0) {
      const tip = esc(d.day + t("　总 ") + fmtTok(tok) + t("（输入 ") + fmtTok(d.input || 0) +
        t(" · 输出 ") + fmtTok(d.output || 0) + t(" · 其他/缓存 ") + fmtTok(Math.max(0, tok - (d.input || 0) - (d.output || 0))) +
        t("）\\n调用 ") + (d.calls || 0) + t(t(" 次 · ")) + fmtUsd(d.cost_usd));
      const hIn = ih * ((d.input || 0) / max);
      const hCa = ih * (((d.tokens || 0) - (d.input || 0) - (d.output || 0)) / max);
      const hOut = ih * ((d.output || 0) / max);
      bars += '<rect x="' + x.toFixed(1) + '" y="' + (yBase - hIn).toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + Math.max(hIn, 1).toFixed(1) +
        '" fill="var(--uc-in)"><title>' + tip + "</title></rect>";
      bars += '<rect x="' + x.toFixed(1) + '" y="' + (yBase - hIn - hCa).toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, hCa).toFixed(1) +
        '" fill="var(--uc-ca)" opacity="0.85"><title>' + tip + "</title></rect>";
      bars += '<rect x="' + x.toFixed(1) + '" y="' + (yBase - hIn - hCa - hOut).toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, hOut).toFixed(1) +
        '" fill="var(--uc-out)" opacity="0.95"><title>' + tip + "</title></rect>";
    } else {
      // 当天无用量：基线上的占位短柱（不再是空白），悬浮给出说明
      bars += '<rect x="' + x.toFixed(1) + '" y="' + (yBase - 2.5).toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="2.5" rx="1" fill="var(--border-strong)" opacity="0.9">' +
        "<title>" + esc(d.day + t("　无用量")) + "</title></rect>";
    }
    if (labeled.has(i)) {
      // 夹住标签中心，避免首/末标签的文字探出 viewBox 被裁掉
      const cx = Math.min(Math.max(x + bw / 2, padL + 18), W - padR - 18);
      labels += '<text class="uc-x" x="' + cx.toFixed(1) + '" y="' + (H - 7) +
        '" text-anchor="middle">' + esc(dayTxt) + "</text>";
    }
  });
  // 基线 + 1/4、1/2、3/4 参考虚线：柱子有「地」可落，高度有参照
  const grid =
    '<line class="uc-grid" x1="' + padL + '" y1="' + (padT + ih * 0.25).toFixed(1) + '" x2="' + (W - padR) + '" y2="' + (padT + ih * 0.25).toFixed(1) + '"/>' +
    '<line class="uc-grid" x1="' + padL + '" y1="' + (padT + ih * 0.5).toFixed(1) + '" x2="' + (W - padR) + '" y2="' + (padT + ih * 0.5).toFixed(1) + '"/>' +
    '<line class="uc-grid" x1="' + padL + '" y1="' + (padT + ih * 0.75).toFixed(1) + '" x2="' + (W - padR) + '" y2="' + (padT + ih * 0.75).toFixed(1) + '"/>' +
    '<line class="uc-base" x1="' + padL + '" y1="' + yBase + '" x2="' + (W - padR) + '" y2="' + yBase + '"/>';
  return '<svg class="usage-svg" viewBox="0 0 ' + W + " " + H + '" role="img" preserveAspectRatio="xMidYMid meet">' +
    grid + bars + labels +
    '<g class="uc-legend">' +
    '<rect x="6" y="2" width="10" height="10" rx="2" fill="var(--uc-in)"/><text class="uc-x" x="20" y="11">输入</text>' +
    '<rect x="52" y="2" width="10" height="10" rx="2" fill="var(--uc-ca)" opacity="0.85"/><text class="uc-x" x="66" y="11">缓存/其他</text>' +
    '<rect x="118" y="2" width="10" height="10" rx="2" fill="var(--uc-out)" opacity="0.95"/><text class="uc-x" x="132" y="11">输出</text>' +
    '<text class="uc-peak" x="176" y="11">峰值 ' + esc(fmtTok(max)) + "</text>" +
    "</g></svg>";
}

/* 维度排行表：首列名称带相对占比条 */
function usageDimTable(title, rows) {
  let body;
  if (!rows || !rows.length) {
    body = '<p class="hint">（该维度暂无数据）</p>';
  } else {
    const maxTok = Math.max(1, ...rows.map((r) => r.tokens || 0));
    body = '<table class="usage-table"><thead><tr>' +
      "<th>" + esc(title) + '</th><th class="num">调用</th><th class="num">Tokens</th>' +
      '<th class="num">输入/输出</th><th class="num">耗时</th><th class="num">费用</th>' +
      "</tr></thead><tbody>" + rows.map((r) => {
        const pct = Math.max(4, Math.round((r.tokens || 0) * 100 / maxTok));
        const okPct = r.calls ? Math.round((r.ok || 0) * 100 / r.calls) : 0;
        return "<tr>" +
          '<td class="bar-cell"><div class="bar-outer">' +
          '<div class="bar-fill" style="width:' + pct + '%"></div>' +
          "<span>" + esc(r.key) + "</span><i>" + okPct + "% 成</i></div></td>" +
          '<td class="num">' + fmtTok(r.calls) + "</td>" +
          '<td class="num" title="输入 ' + fmtTok(r.input) + t(' · 输出 ') + fmtTok(r.output) + '">' + fmtTok(r.tokens) + "</td>" +
          '<td class="num sub">' + fmtTok(r.input) + " / " + fmtTok(r.output) + "</td>" +
          '<td class="num sub">' + fmtDur(r.duration_s) + "</td>" +
          '<td class="num">' + fmtUsd(r.cost_usd) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }
  return '<h3 class="sec-title">' + esc(title) + "</h3>" + body;
}

function usageRecentTable(rows) {
  if (!rows || !rows.length) return '<p class="hint">（暂无数据）</p>';
  return '<table class="usage-table recent"><thead><tr>' +
    '<th class="num">时间</th><th>工具</th><th>智能体</th><th>模型</th><th>角色</th><th>状态</th>' +
    '<th class="num">Tokens</th><th class="num">费用</th>' +
    "</tr></thead><tbody>" + rows.map((r) => {
      const det = t("输入 ") + fmtTok(r.input) + t(" · 缓存 ") + fmtTok(r.cached) + t(" · 输出 ") + fmtTok(r.output);
      const model = String(r.model || "");
      return "<tr" + (r.ok ? "" : ' class="bad-row"') + ">" +
        '<td class="num sub">' + esc(String(r.ts || "").slice(5)) + "</td>" +
        "<td>" + esc(toolName(r.tool)) + "</td>" +
        "<td>" + esc(r.agent_label || r.agent || "") + "</td>" +
        '<td class="mono" title="' + esc(det) + '">' + esc(model.length > 30 ? model.slice(0, 29) + "…" : model) + "</td>" +
        '<td class="sub">' + esc(roleName(r.role)) + "</td>" +
        "<td>" + (r.ok ? '<span class="chip done">OK</span>' : '<span class="chip failed">失败</span>') + "</td>" +
        '<td class="num" title="' + esc(det) + '">' + fmtTok(r.total) + "</td>" +
        '<td class="num">' + fmtUsd(r.cost_usd) + "</td></tr>";
    }).join("") + "</tbody></table>";
}

function renderUsage() {
  const u = S.usage;
  if (!u) return;
  const tot = u.totals || {};
  const activeDays = tot.days_active || 0;
  // 回填记录只有总量、没有输入/输出细分：细分全为 0 时说明当前范围只有历史数据，
  // 不提示的话「输入 0 · 输出 0」会被误读成统计坏了
  const noBreakdown = tot.tokens > 0 && !tot.input && !tot.output && !tot.cached;
  $("usage-kpis").innerHTML = [
    kpiCard(t("总 Tokens"), fmtTok(tot.tokens),
      t("输入 ") + fmtTok(tot.input) + t(" · 输出 ") + fmtTok(tot.output) + t(" · 缓存 ") + fmtTok(tot.cached),
      true, "i-sigma"),
    kpiCard(t("调用次数"), fmtTok(tot.calls),
      t("成功率 ") + (tot.calls ? Math.round(tot.ok * 100 / tot.calls) : 0) + t("%（失败 ") + fmtTok(tot.failed) + t("）"),
      false, "i-hash"),
    kpiCard(t("累计费用"), fmtUsd(tot.cost_usd),
      t("活跃日均 ") + fmtUsd(activeDays ? tot.cost_usd / activeDays : 0), false, "i-coin"),
    kpiCard(t("单次均值"), fmtTok(tot.avg_tokens_per_call) + " tok",
      t("缓存命中率 ") + (tot.cache_rate || 0) + "%", false, "i-gauge"),
    kpiCard(t("活跃天数"), fmtTok(activeDays),
      t("累计调用时长 ") + fmtDur(tot.duration_s), false, "i-calendar-days"),
  ].join("");
  const note = $("usage-note");
  if (note) {
    note.innerHTML = noBreakdown
      ? '<p class="hint">当前范围内都是历史运行回填的记录：只保留总量与费用，' +
        "输入/输出/缓存细分从新调用开始记录。</p>"
      : "";
  }
  $("usage-trend").innerHTML = usageTrendSvg(u.by_day || []);
  $("usage-dims").innerHTML = [
    [t("按工具（CLI / API）"), (u.by_tool || []).map((r) => Object.assign({}, r, { key: toolName(r.key) }))],
    [t("按智能体"), u.by_agent],
    [t("按模型"), u.by_model],
    [t("按步骤角色"), (u.by_role || []).map((r) => Object.assign({}, r, { key: roleName(r.key) }))],
    [t("按任务类型"), u.by_task_type],
  ].map(([title, rows]) => usageDimTable(title, rows)).join("");
  $("usage-recent").innerHTML = usageRecentTable(u.recent || []);
}



/* ---------------------------------------------------------- 手机连接（扫码） */
/* ZCode 桌面端式样：大二维码居中，地址+复制在下方；多来源（Tailscale/局域网）用 chips 切换 */
async function openPhoneConnect() {
  let urls = [];
  try {
    const d = await api("/api/connect");
    urls = d.urls || [];
  } catch (e) { toast(e.message, true); return; }
  if (!urls.length) { toast(t("未获取到可用的连接地址"), true); return; }
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
    t('局域网地址要求手机与电脑连同一 WiFi；Tailscale 地址出门也能用，') +
    '两端需登录同一 Tailscale 账号。手机控制时另一端自动变为只读，可在顶栏接管。<br>' +
    '连不上时（如路由器重启后地址变了）回电脑重新打开此弹框扫新码即可。</p>' +
    "</div>";
  openModal(t("📱 手机连接"), body, "");
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
  const done = () => toast(t("已复制，手机浏览器粘贴打开即可"));
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(u.url).then(done, () => fallbackCopy(u.url, done));
  } else fallbackCopy(u.url, done);
}

function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast(t("复制失败，请手动选择地址"), true); }
  ta.remove();
}

/* 手机端（<900px）抽屉收起：点导航/任何侧栏可点项后都应收回，别挡内容 */
function collapseDrawerIfMobile() {
  if (window.innerWidth < 900) document.body.classList.add("side-collapsed");
}

function switchTab(name) {
  if (name === "__phone") { openPhoneConnect(); return; }  // 手机连接是弹框，不切页
  // 导航收进「设置」：进设置后左栏整体换成设置导航，内容铺满
  S.tab = name;
  if (SET_TABS.has(name)) localStorage.setItem("orch.setTab", name);
  document.body.classList.add("settings-mode");
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-settings"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === name));
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-" + name));
  const title = $("page-title");
  if (title) title.textContent = tabTitle(name);
  if (name === "runs" && !S.detailRunId) closeRun();
  if (name === "agents") autoCheckUpdates();   // 进目录页自动查各 CLI 新版本
  if (name === "orch") { loadOrchestrator(); loadSettings(); }  // 进编排设置页拉取配置
  if (name === "skills") loadSkills();   // 进经验库页拉取沉淀
  if (name === "automation") { loadAutomation(); startAutoPoll(); }   // 进自动化页：拉取 + 页面可见时每 8s 轮询
  else stopAutoPoll();   // 离开自动化页（或切到别的子页）即停表
  if (name === "market") loadMarket();   // 进插件市场页拉取目录
  if (name === "usage") { syncUsageRange(); loadUsage(); }   // 进用量页：对齐范围选中态并拉取
  if (name === "appearance") renderAppearance();   // 进皮肤页：按当前皮肤/明暗重画卡片
  if (name === "appearance") renderCodeSettings();  // 代码设置行 + 双主题预览卡同步当前值
  if (name === "about") loadSelfupdate(false);     // 进关于页：拉版本与更新状态
  collapseDrawerIfMobile();
  syncInspectorVis();    // 检查器只属于任务上下文：进设置子页自动收起
}

/* 退出设置：左栏恢复任务树，内容回到任务页 */
function exitSettings() {
  S.tab = "tasks";
  stopAutoPoll();   // 离开设置视图：自动化页轮询一并停掉
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-settings"));
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-tasks"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === "tasks"));
  document.body.classList.remove("settings-mode");
  const title = $("page-title");
  if (title) title.textContent = tabTitle("tasks");
  collapseDrawerIfMobile();
  syncInspectorVis();    // 回到新建表单：不是任务上下文，检查器收起（点运行详情才会滑回）
}

/* 侧栏底部齿轮：直接进设置（左栏换成设置导航，不再弹菜单）。回到上次看的那页，默认编排设置 */
function enterSettings() {
  const saved = localStorage.getItem("orch.setTab") || "";
  switchTab(SET_TABS.has(saved) ? saved : "orch");
}

window.openRun = openRun;
window.closeRun = closeRun;
window.archiveTask = archiveTask;
window.deleteTask = deleteTask;
window.retryTask = retryTask;
window.revealPath = revealPath;
window.renameTask = renameTask;
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
window.setUsageDays = setUsageDays;
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
window.flowReset = flowReset;
window.skillPackOp = skillPackOp;
window.skillLessonOp = skillLessonOp;
window.skillCatFilter = skillCatFilter;
window.autoToggle = autoToggle;
window.autoRunNow = autoRunNow;
window.autoEdit = autoEdit;
window.autoRemove = autoRemove;
window.autoTemplate = autoTemplate;
window.saveAutoForm = saveAutoForm;
window.loadAutomation = loadAutomation;
window.mkInstall = mkInstall;
window.mkRemove = mkRemove;
window.loadMarket = loadMarket;
window.saveOrchestrator = saveOrchestrator;
window.testOrchestrator = testOrchestrator;
window.saveSettings = saveSettings;
window.setSkin = setSkin;
window.setThemeMode = setThemeMode;
window.renderAppearance = renderAppearance;
window.setLangBtn = setLangBtn;
window.syncLangMode = syncLangMode;
window.suCheck = suCheck;
window.suApply = suApply;
window.suRestart = suRestart;

document.addEventListener("DOMContentLoaded", () => {
  // 远程地址里带的 ?token= 存起来并从地址栏抹掉，之后所有请求走请求头
  const urlTok = new URLSearchParams(location.search).get("token");
  if (urlTok) {
    localStorage.setItem("orch.token", urlTok);
    history.replaceState(null, "", location.pathname);
  }
  document.querySelectorAll(".set-item").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.sub)));
  $("btn-set-back").addEventListener("click", exitSettings);
  $("btn-settings").addEventListener("click", enterSettings);
  $("btn-phone-side").addEventListener("click", openPhoneConnect);
  $("btn-prov-side").addEventListener("click", () => switchTab("orch"));   // 供应商指示 → 编排设置页更换
  document.querySelectorAll("#usage-ranges [data-days]").forEach((b) =>
    b.addEventListener("click", () => setUsageDays(b.dataset.days)));
  $("btn-usage-refresh").addEventListener("click", loadUsage);
  $("btn-back").addEventListener("click", closeRun);
  $("btn-cancel").addEventListener("click", cancelRun);
  bindDirector();
  // 详情标签页：手点即切并钉住——状态变化触发的自动选卡不再抢用户的手选
  $("rd-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".rd-tab");
    if (!b || b.classList.contains("hidden")) return;
    S.rdTab = b.dataset.tab;
    S.rdTabPin = true;
    applyRdTabs();
  });
  // 详情子页显隐 → 容器宽度 class（:has 在部分浏览器不生效，JS 同步为准）
  (function () {
    const sub = document.getElementById("sub-runs");
    const page = document.getElementById("page-settings");
    if (!sub || !page) return;
    const sync = () => page.classList.toggle("page-wide", !sub.classList.contains("hidden"));
    new MutationObserver(sync).observe(sub, { attributes: true, attributeFilter: ["class"] });
    sync();
  })();
  $("btn-retry").addEventListener("click", () => { const r = S.lastRun; if (r && r.task_id) retryTask(r.task_id); });
  $("btn-continue").addEventListener("click", () => {
    const tid = (S.lastRun && S.lastRun.task_id) || S.detailTaskKey || "";
    if (tid) continueSerial(tid);
  });
  $("btn-newfrom").addEventListener("click", () => {
    const tid = (S.lastRun && S.lastRun.task_id) || S.detailTaskKey || "";
    if (tid) newFromTask(tid);
  });
  $("btn-editretry").addEventListener("click", () => {
    const tid = (S.lastRun && S.lastRun.task_id) || S.detailTaskKey || "";
    if (!tid) return;
    newFromTask(tid);   // 与「基于此任务新建」同一套预填；差别只在入口语义
    const g = $("f-goal");
    if (g) g.focus();   // 焦点直接落到目标框，改完即可提交
  });
  $("btn-delete").addEventListener("click", () => { if (S.detailRunId) deleteRun(S.detailRunId); });
  $("btn-theme").addEventListener("click", toggleTheme);
  $("btn-notify-toggle").addEventListener("click", toggleNotifySound);
  // 命令面板：侧栏搜索行 / Ctrl+K 唤起（函数若尚未落地，跳过而不炸整个初始化）
  if (typeof bindCmdK === "function") bindCmdK();
  // 文件夹全部展开/收起（写回 localStorage，与手点单个 folder 同一套持久化）
  $("btn-side-expand").addEventListener("click", () => {
    const dlist = Array.from($("side-tasks").querySelectorAll("details.sdir"));
    if (!dlist.length) return;
    const anyClosed = dlist.some((d) => !d.open);
    dlist.forEach((d) => { d.open = anyClosed; });
    saveOpenDirs();
    syncSideExpandBtn();
  });
  // 顶栏皮肤胶囊 → 皮肤页；皮肤卡片 / 明暗分段用事件委托（卡片是动态渲染的）
  $("btn-skin").addEventListener("click", () => switchTab("appearance"));
  $("skin-grid").addEventListener("click", (e) => {
    const card = e.target.closest(".skin-card");
    if (card) setSkin(card.dataset.skin);
  });
  $("skin-mode").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (b) setThemeMode(b.dataset.mode);
  });
  $("lang-mode").addEventListener("click", (e) => {
    const b = e.target.closest("[data-lang]");
    if (b) setLangBtn(b.dataset.lang);
  });
  // 代码设置：任何一行改动都全量落偏好再刷新预览（值都来自控件自身，不会弹回）
  for (const id of ["cs-theme-light", "cs-theme-dark", "cs-linenum", "cs-wrap", "cs-size"]) {
    const el = $(id);
    if (el) el.addEventListener("input", csApplyFromControls);
    if (el && el.tagName === "SELECT") el.addEventListener("change", csApplyFromControls);
  }
  // 皮肤页锚点：胶囊点击滚到对应面板；main 滚动时高亮跟随（处理器内部自判皮肤页可见）
  const csAnchor = $("cs-anchor");
  if (csAnchor) csAnchor.addEventListener("click", (e) => {
    const b = e.target.closest("[data-apn]");
    if (b) apnScrollTo(b.dataset.apn);
  });
  document.querySelector("main").addEventListener("scroll", apnSyncActive, { passive: true });
  syncLangMode();
  $("btn-menu").addEventListener("click", () => document.body.classList.toggle("side-collapsed"));
  // 手机抽屉：遮罩点击 / 侧栏内任何可点项（导航、任务树、设置入口）点击后都收回
  $("drawer-mask").addEventListener("click", () => document.body.classList.add("side-collapsed"));
  $("sidebar").addEventListener("click", (e) => {
    if (e.target.closest("button, summary, .stepx")) collapseDrawerIfMobile();
  });
  $("btn-new-task").addEventListener("click", exitSettings);
  // 主侧栏快捷入口：直达自动化 / 插件市场（switchTab 自己会切设置模式并挂载页面）
  $("btn-q-automation").addEventListener("click", () => switchTab("automation"));
  $("btn-q-market").addEventListener("click", () => switchTab("market"));
  bindCtxMenus();
  bindInspector();
  if (window.innerWidth < 900) document.body.classList.add("side-collapsed");
  applyAppearance();
  $("btn-create").addEventListener("click", createTask);
  /* 侧栏「显示已归档」开关（原「最近任务」面板移除后，归档找回的唯一入口）。
   * 只作当次查看，不写 localStorage——持久化会让图标常亮、违背「默认隐藏」的预期 */
  $("btn-side-arch").addEventListener("click", () => {
    S.showArchived = !S.showArchived;
    paintArchToggle();
    S.sideSig = "";   // 强制侧栏重绘
    renderSideTasks();
  });
  paintArchToggle();
  localStorage.removeItem("orch.showArchived");   // 清掉旧版持久化残留，避免误解为默认选中
  $("f-resume-agent").addEventListener("change", loadSessions);
  $("f-resume-session").addEventListener("change", showResumeHint);
  $("f-workdir").value = localStorage.getItem("orch.workdir") || "";
  // 附件：按钮选文件 + 目标框粘贴截图；工作目录变化时探测 git 仓库（代码版本下拉）
  $("btn-attach").addEventListener("click", () => $("f-attach-file").click());
  $("f-attach-file").addEventListener("change", (e) => {
    addAttachFiles(Array.from(e.target.files || []));
    e.target.value = "";  // 允许重复选同一个文件
  });
  $("f-goal").addEventListener("paste", onGoalPaste);
  $("f-workdir").addEventListener("input", queueGitProbe);
  $("f-workdir").addEventListener("change", queueGitProbe);
  // 点工作目录输入框直接进文件夹选择（远端设备被 403 守卫拦下，静默继续手输）
  $("f-workdir").addEventListener("click", () => window.pickFolder("f-workdir", true));
  $("set-workdir").addEventListener("click", () => window.pickFolder("set-workdir", true));
  $("f-git-rev").addEventListener("change", renderGitHint);
  if ($("f-workdir").value) queueGitProbe();  // 回填的目录：进页面即探测代码版本
  $("f-type").addEventListener("change", onTypeChange);
  // 类型菜单点外面收起（Escape 同收）；按钮自身点击由 toggleTypeMenu 负责
  document.addEventListener("click", (e) => {
    const menu = $("type-menu"), btn = $("f-type-btn");
    if (menu && !menu.classList.contains("hidden") &&
        !menu.contains(e.target) && !(btn && btn.contains(e.target))) toggleTypeMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleTypeMenu(false);
  });
  $("f-mode").addEventListener("change", () => {
    $("f-manual-only").classList.toggle("hidden", $("f-mode").value !== "manual");
  });
  $("f-type").dispatchEvent(new Event("change"));
  $("btn-reload-catalog").addEventListener("click", async () => {
    await api("/api/catalog/reload", { method: "POST" }); poll(); refreshSessionAgents();
  });
  $("btn-import").addEventListener("click", openImportDialog);
  $("btn-add-provider").addEventListener("click", openAddProviderDialog);
  $("prov-search").addEventListener("input", () => {
    S.provFilter = $("prov-search").value;
    renderProvList();
  });
  document.addEventListener("keydown", (e) => {
    // #ask 叠在 #modal 之上时 Esc 只关 #ask（不连坐关掉底下正在填的弹框）
    if (e.key === "Escape" && !$("ask").classList.contains("hidden")) { _askClose(false); return; }
    if (e.key === "Enter" && !$("ask").classList.contains("hidden")
        && e.target.tagName !== "TEXTAREA") { _askClose(true); return; }
    // 文件内容弹窗(240) 夹在 ask(250) 与 modal(200) 之间，Esc 同样逐层关
    if (e.key === "Escape" && filePopIsOpen()) { window.filePopClose(); return; }
    if (e.key === "Escape" && !$("modal").classList.contains("hidden")) closeModal();
  });
  // 快捷键：Ctrl/Cmd+K 命令面板；N 新建任务（正在输入或弹框打开时不劫持）
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault(); cmdkOpen();
      return;
    }
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && (e.key === "n" || e.key === "N")
        && $("modal").classList.contains("hidden") && $("ask").classList.contains("hidden")
        && filePopIsOpen() === false && $("cmdk-mask").classList.contains("hidden")
        && !document.body.classList.contains("settings-mode")) {
      $("btn-new-task").click();
    }
  });
  $("ask-mask").addEventListener("click", () => _askClose(false));
  $("ask-x").addEventListener("click", () => _askClose(false));
  $("ask-no").addEventListener("click", () => _askClose(false));
  $("ask-yes").addEventListener("click", () => _askClose(true));
  $("btn-refresh-models").addEventListener("click", refreshAllModels);
  // 自动化页：刷新 / 新建 / 三段筛选（本地过滤）
  $("btn-auto-refresh").addEventListener("click", loadAutomation);
  $("btn-auto-new").addEventListener("click", () => autoForm());
  $("auto-filter").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    S.autoFilter = b.dataset.f;
    document.querySelectorAll("#auto-filter .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    renderAutomation();
  });
  // 插件市场：搜索 / 分类 / 状态段选（本地过滤）
  $("mk-search").addEventListener("input", renderMarket);
  $("mk-cat").addEventListener("change", renderMarket);
  $("mk-state").addEventListener("click", (e) => {
    const b = e.target.closest("[data-s]");
    if (!b) return;
    S.mkState = b.dataset.s;
    document.querySelectorAll("#mk-state .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    renderMarket();
  });
  $("btn-reset-catalog").addEventListener("click", async () => {
    if (!await uiConfirm(t("恢复内置默认 catalog？你对该文件的修改将丢失。"), { ok: "恢复", danger: true })) return;
    await api("/api/catalog/reset", { method: "POST" }); poll();
  });
  poll();
  schedulePolling();
  startSSE();
  startCtrlHeartbeat();
  loadFlows();   // 任务类型下拉（内置 + 自定义流程）
  refreshSessionAgents();  // 继续会话下拉的工具集合（服务端 60s 缓存，开销小）
  loadOrchestrator();      // 侧栏左下角的编排者供应商指示（进入编排设置页时会再拉一次）
  suStartupCheck();        // 静默查一次新版本（有新版 toast 提醒，同版本只提一次）
});

/* ===== 命令面板（Ctrl+K / 侧栏搜索行）：任务直达 + 快捷命令 ===== */
const CmdK = { tab: "all", q: "", sel: 0, flat: [] };

function cmdkOps() {
  return [
    { icon: "i-tasks", label: t("新任务"), kbd: "N", run: () => $("btn-new-task").click() },
    { icon: "i-calendar-days", label: t("自动化"), run: () => switchTab("automation") },
    { icon: "i-blocks", label: t("插件市场"), run: () => switchTab("market") },
    { icon: "i-book", label: t("经验库"), run: () => switchTab("skills") },
    { icon: "i-cpu", label: t("智能体管理"), run: () => switchTab("agents") },
    { icon: "i-chart", label: t("用量统计"), run: () => switchTab("usage") },
    { icon: "i-gear", label: t("设置"), run: () => enterSettings() },
    { icon: "i-phone", label: t("手机连接"), run: () => switchTab("__phone") },
  ];
}
function cmdkTaskItems() {
  const latest = (S.state && S.state.task_latest) || {};
  const arr = ((S.state && S.state.tasks) || []).map((tk) => {
    const lr = latest[tk.id];
    return { key: tk.id, label: tk.title || tk.id, time: lr ? (lr.started_at || lr.created_at) : (tk.created_at || "") };
  }).filter((x) => x.label);
  arr.sort((a, b) => String(b.time || "").localeCompare(String(a.time || "")));
  return arr.slice(0, 8).map((x) => ({
    icon: "i-tasks", label: x.label, time: relTime(x.time),
    run: () => { if (window.sideOpenTask) sideOpenTask(x.key); },
  }));
}
function cmdkRender() {
  const box = $("cmdk-list");
  if (!box) return;
  const q = CmdK.q.trim().toLowerCase();
  const match = (it) => !q || it.label.toLowerCase().includes(q);
  const secs = [];
  if (CmdK.tab !== "ops") {
    const items = cmdkTaskItems().filter(match);
    if (items.length) secs.push({ name: t("最近任务"), items });
  }
  if (CmdK.tab !== "tasks") {
    const items = cmdkOps().filter(match);
    if (items.length) secs.push({ name: t("命令"), items });
  }
  CmdK.flat = secs.reduce((a, s) => a.concat(s.items), []);
  if (CmdK.sel >= CmdK.flat.length) CmdK.sel = Math.max(0, CmdK.flat.length - 1);
  if (!CmdK.flat.length) { box.innerHTML = '<div class="cmdk-empty">' + t("无匹配结果") + "</div>"; return; }
  let idx = 0;
  box.innerHTML = secs.map((s) =>
    '<div class="cmdk-sec">' + esc(s.name) + "</div>" +
    s.items.map((it) => {
      const on = idx++ === CmdK.sel ? " on" : "";
      return '<div class="cmdk-item' + on + '" data-i="' + (idx - 1) + '">' +
        '<svg class="ico" aria-hidden="true"><use href="#' + it.icon + '"></use></svg>' +
        '<span class="l">' + esc(it.label) + "</span>" +
        (it.time ? '<span class="tm">' + esc(it.time) + "</span>" : "") +
        (it.kbd ? '<span class="kbd">' + esc(it.kbd) + "</span>" : "") +
        "</div>";
    }).join("")
  ).join("");
  const on = box.querySelector(".cmdk-item.on");
  if (on) on.scrollIntoView({ block: "nearest" });
}
function cmdkMove(d) {
  if (!CmdK.flat.length) return;
  CmdK.sel = (CmdK.sel + d + CmdK.flat.length) % CmdK.flat.length;
  cmdkRender();
}
function cmdkRun(i) {
  const it = CmdK.flat[i];
  if (!it) return;
  cmdkClose();
  it.run();
}
function cmdkOpen() {
  const mask = $("cmdk-mask");
  if (!mask) return;
  mask.classList.remove("hidden");
  CmdK.tab = "all"; CmdK.q = ""; CmdK.sel = 0;
  const inp = $("cmdk-q");
  if (inp) inp.value = "";
  document.querySelectorAll("#cmdk-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === "all"));
  cmdkRender();
  if (inp) inp.focus();
}
function cmdkClose() {
  const mask = $("cmdk-mask");
  if (mask) mask.classList.add("hidden");
}
function bindCmdK() {
  const btn = $("btn-cmdk");
  if (btn) btn.addEventListener("click", cmdkOpen);
  const mask = $("cmdk-mask");
  if (mask) mask.addEventListener("click", cmdkClose);
  const inp = $("cmdk-q");
  if (inp) {
    inp.addEventListener("input", () => { CmdK.q = inp.value; CmdK.sel = 0; cmdkRender(); });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); cmdkMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); cmdkMove(-1); }
      else if (e.key === "Enter") { e.preventDefault(); cmdkRun(CmdK.sel); }
      else if (e.key === "Escape") { e.stopPropagation(); cmdkClose(); }
    });
  }
  const tabs = $("cmdk-tabs");
  if (tabs) tabs.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-t]");
    if (!b) return;
    CmdK.tab = b.dataset.t; CmdK.sel = 0;
    tabs.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    cmdkRender();
  });
  const list = $("cmdk-list");
  if (list) list.addEventListener("click", (e) => {
    const it = e.target.closest(".cmdk-item");
    if (it) cmdkRun(Number(it.dataset.i) || 0);
  });
}
