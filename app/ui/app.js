/* CodeBee 前端（无依赖；状态走 SSE 实时推送，断线自动降级轮询） */
"use strict";

// 桌宠通过窗口标题里的端口识别并复用当前 CodeBee 实例。
function codebeeDocumentTitle(lang) {
  const brand = lang === "en" ? "CodeBee · Multi-agent Orchestrator" : "CodeBee · 多智能体编排台";
  const port = location.port || (location.protocol === "https:" ? "443" : "80");
  return brand + " [" + port + "]";
}
document.title = codebeeDocumentTitle((localStorage.getItem("orch.lang") || "zh").toLowerCase());

const $ = (id) => document.getElementById(id);
const S = { state: null, catalog: null, catSig: "", providers: null, bindings: null, modelsSig: "", bindSig: "", tab: "tasks", mainPage: "", /* 主栏正显示的全局页（"" = 新建任务表单）；左栏是任务树还是设置导航看 body.settings-mode */ detailRunId: null, pollTimer: null, showArchived: false, /* 会话内开关：每次加载默认隐藏已归档、图标不选中（不持久化，见 btn-side-arch） */ selProvs: {}, selModels: {}, selRuns: {}, bindSel: {}, catalogChecking: false, updateCheckAt: 0, control: null, sseLive: false, es: null, flows: null, orch: null, skills: null, orchSig: "", settings: null, sessionAgents: new Set(), atts: [], gitInfo: null, gitWb: null, gitWbKey: "", gitWbAt: 0, gitWbBusy: false, creatingTask: false, inspKey: null, inspData: null, inspSig: "", inspAt: 0, inspTab: "git", inspAutoSig: "", rdTab: null, rdTabSig: "", rdTabPin: false, _rdCtx: {}, lastPrefs: null, prefsApplied: false, sideUsage: null, sideUsageAt: 0, ovUsage: null, ovDays: undefined };

/* ---------------------------------------------------------- 任务类型（流程） */
async function loadFlows() {
  // 偏好记忆（借鉴 chinese-novelist-skill）：并行拉最近一次的任务参数，
  // renderTypeOptions/onTypeChange 用它预填类型与轮数/阈值等（拉失败不挡首屏）
  api("/api/prefs").then((r) => { S.lastPrefs = (r && r.prefs) || {}; })
    .then(() => renderTypeOptions())
    .catch(() => {});
  try {
    const r = await api("/api/flows");
    S.flows = r.flows || [];
  } catch (e) { S.flows = []; }
  renderTypeOptions();
}

function invalidateFlowRubricDraft(flowId) {
  if (S.flowRubricDrafts) delete S.flowRubricDrafts[flowId];
  const rubric = $("f-rubric");
  if (rubric && rubric.dataset.flowId === flowId) rubric.dataset.flowId = "";
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
  if (f.id === "rank_scan") return t("抓双平台榜 → AI 选题洞察");
  if (f.engine === "direct") return t("单智能体直达（快）");
  if (f.engine === "code") return t("实现 → 验证 → 评审");
  // 连载与单稿件同引擎，描述必须区分：连载强调逐章与断点续跑
  if (f.serial) return t("大纲 → 逐章起草评审 → 合并（可续跑）");
  return t("起草 → 多维评审 → 门禁");
}

function renderTypeOptions() {
  const sel = $("f-type");
  if (!sel || !S.flows) return;
  const prev = sel.value;  sel.innerHTML = (S.flows || []).map((f) => {
    return '<option value="' + esc(f.id) + '">' + esc(t(f.name)) + t("（") + flowDesc(f) + (f.builtin ? "" : t(" · 自定义")) + t("）") + "</option>";
  }).join("");
  // 保住用户手动选择（typeTouched）；出厂默认 direct 不算选择——prefs 迟到
  // 竞态下要让位给偏好记忆，否则「上次类型」永远恢复不了（高负载下实测）
  if (prev && flowById(prev) && (S.typeTouched || prev !== "direct")) sel.value = prev;
  // 偏好记忆：上次创建用的类型优先于出厂默认「直接执行」（老数据/已删流程回落）
  else if (S.lastPrefs && S.lastPrefs.type && flowById(S.lastPrefs.type)) sel.value = S.lastPrefs.type;
  // 首屏默认落在「直接执行」（快档位：单 CLI 直达，无拆解/评审）；
  // 老数据或该流程被删时保持首项，不硬造一个不存在的值
  else if (flowById("direct")) sel.value = "direct";
  applyTaskPreferenceDefaults();
  renderTypeMenu();
  onTypeChange();
}

function applyTaskPreferenceDefaults() {
  if (S.prefsApplied || !S.lastPrefs) return;
  const lp = S.lastPrefs;
  const mode = $("f-mode"), thinking = $("f-thinking");
  if (mode && ["auto", "fast", "expert", "manual"].includes(lp.mode)) mode.value = lp.mode;
  if (thinking && ["auto", "low", "standard", "high"].includes(lp.thinking)) thinking.value = lp.thinking;
  const manual = $("f-manual-only");
  if (manual && mode) manual.classList.toggle("hidden", mode.value !== "manual");
  syncCmpSelFace("f-mode");
  syncCmpSelFace("f-thinking");
  S.prefsApplied = true;
}

/* composer 内「编排模式/思考程度」pill 的短名：原生 select 透明覆盖在 pill 上，
 * face 只显示选项的短前缀（「自动（推荐）：…」→「自动」），值真源仍是 select。 */
function cmpSelShort(text) {
  return (String(text || "").split(/[：:]/)[0]
    .replace(/[（(].*?[）)]/g, "").trim()) || "";
}

function syncCmpSelFace(id) {
  const sel = $(id), face = $(id + "-face");
  if (!sel || !face) return;
  const opt = sel.options[sel.selectedIndex];
  face.textContent = cmpSelShort(opt && opt.textContent) || sel.value;
}
window.syncCmpSelFace = syncCmpSelFace;   // applyI18n 重译 option 后同步短名

/* ---- 对话模型 pill：厂商 → 模型两级钻取菜单（ChatGPT 选择器同款语汇）。
 * 真源仍是 f-direct-provider / f-direct-model-name 两个隐藏 select，
 * 菜单只改它们的值——既有提交流 / onTypeChange 显隐 / 测试全兼容。 */
let cmpModelProv = "";   // ""=厂商级；否则=已钻入的厂商 id（模型级）

/* 可指定为对话执行者的真实 CLI（手动指定 CLI，2026-09-23）：
 * state.agents 即 effective_agents（已装+启用编排），mode=real 排掉 mock */
function realCliAgents() {
  return ((S.state && S.state.agents) || []).filter((a) => a && a.mode === "real");
}

function cmpDirectAgentVal() {
  const el = $("f-direct-agent");
  return el ? String(el.value || "").trim() : "";
}

function cmpDirectBtnSync() {
  const btn = $("f-direct-btn");
  if (!btn) return;
  const av = cmpDirectAgentVal();
  if (av) {
    const a = realCliAgents().find((x) => x.id === av);
    btn.textContent = (a ? (a.label || a.id) : av) + " · CLI";
    return;
  }
  const pv = $("f-direct-provider").value, mv = $("f-direct-model-name").value;
  if (!pv) { btn.textContent = t("自动推荐"); return; }
  const p = directProviders().find((x) => x.id === pv);
  btn.textContent = (p ? (p.name || p.id) : pv) + (mv ? "/" + mv : "/" + t("推荐"));
}

/* 菜单里「指定 CLI」分组（两级菜单复用同一段，差别只在真源变量）：
 * entriesHtml(选中id, 空文案) 返回分组 HTML；空列表返回 "" */
function cliMenuSectionHtml(selectedId) {
  const clis = realCliAgents();
  if (!clis.length) return "";
  const chk = '<svg class="ico ti-check" aria-hidden="true"><use href="#i-check"></use></svg>';
  return '<div class="type-menu-cap">' + t("指定 CLI") + "</div>" +
    clis.map((a) =>
      '<button type="button" class="type-item" data-cli="' + esc(a.id) + '"><span class="ti-body"><span class="ti-name">' +
      esc(a.label || a.id) + "</span></span>" + (selectedId === a.id ? chk : "") + "</button>").join("");
}

function cmpModelMenuRender() {
  const menu = $("cmp-model-menu");
  if (!menu) return;
  const pv = $("f-direct-provider").value, mv = $("f-direct-model-name").value;
  const av = cmpDirectAgentVal();
  const chk = '<svg class="ico ti-check" aria-hidden="true"><use href="#i-check"></use></svg>';
  const arr = '<svg class="ico ti-check" aria-hidden="true"><use href="#i-chevron-r"></use></svg>';
  if (!cmpModelProv) {
    menu.innerHTML =
      '<button type="button" class="type-item" data-p="" data-m=""><span class="ti-body"><span class="ti-name">' +
        t("自动推荐") + "</span></span>" + (!pv && !av ? chk : "") + "</button>" +
      directProviders().map((p) =>
        '<button type="button" class="type-item" data-p="' + esc(p.id) + '"><span class="ti-body"><span class="ti-name">' +
        esc(p.name || p.id) + "</span></span>" + (pv === p.id && !av ? chk : arr) + "</button>").join("") +
      cliMenuSectionHtml(av) +
      '<div class="type-menu-foot"><button type="button" class="type-item" data-manage="1"><span class="ti-body"><span class="ti-name">' +
        t("管理模型…") + "</span></span></button></div>";
  } else {
    const p = directProviders().find((x) => x.id === cmpModelProv);
    const names = p ? (p.models || []).filter((m) => m.enabled !== false && !m.hidden)
      .map((m) => m.name).filter(Boolean) : [];
    menu.innerHTML =
      '<button type="button" class="type-item" data-back="1"><span class="ti-body"><span class="ti-name">‹ ' +
        esc(p ? (p.name || p.id) : cmpModelProv) + "</span></span></button>" +
      '<button type="button" class="type-item" data-p="' + esc(cmpModelProv) + '" data-m=""><span class="ti-body"><span class="ti-name">' +
        t("随厂商推荐") + "</span></span>" + (pv === cmpModelProv && !mv ? chk : "") + "</button>" +
      names.map((n) =>
        '<button type="button" class="type-item" data-p="' + esc(cmpModelProv) + '" data-m="' + esc(n) + '"><span class="ti-body"><span class="ti-name">' +
        esc(n) + "</span></span>" + (pv === cmpModelProv && mv === n ? chk : "") + "</button>").join("");
  }
  positionFloatingModelMenu(menu, $("f-direct-wrap"));
}

const FLOATING_MODEL_MENUS = [
  ["cmp-model-menu", "f-direct-wrap"],
  ["rd-model-menu", "rd-model-wrap"],
];

function positionFloatingModelMenu(menu, anchor) {
  if (!menu || !anchor || menu.classList.contains("hidden")) return;
  const rect = anchor.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  const width = Math.max(1, Math.min(320, viewportWidth - 16));
  const left = Math.max(8, Math.min(rect.left, viewportWidth - width - 8));
  const above = Math.max(0, rect.top - 14);
  const below = Math.max(0, viewportHeight - rect.bottom - 14);
  const openAbove = above >= below;
  const available = openAbove ? above : below;
  const maxHeight = Math.max(1, Math.min(316, viewportHeight - 16, Math.max(48, available - 6)));

  menu.style.width = width + "px";
  menu.style.maxHeight = maxHeight + "px";
  const height = Math.min(menu.scrollHeight, maxHeight);
  const maxTop = Math.max(8, viewportHeight - height - 8);
  const desiredTop = openAbove ? rect.top - height - 6 : rect.bottom + 6;
  menu.style.left = left + "px";
  menu.style.top = Math.max(8, Math.min(maxTop, desiredTop)) + "px";
}

function syncFloatingModelMenus() {
  for (const [menuId, anchorId] of FLOATING_MODEL_MENUS)
    positionFloatingModelMenu($(menuId), $(anchorId));
}

document.addEventListener("scroll", syncFloatingModelMenus, true);
window.addEventListener("resize", syncFloatingModelMenus);

function toggleModelMenu(force) {
  const menu = $("cmp-model-menu");
  if (!menu) return;
  const show = force !== undefined ? force : menu.classList.contains("hidden");
  if (show) { cmpModelProv = ""; cmpModelMenuRender(); }
  menu.classList.toggle("hidden", !show);
  if (show) positionFloatingModelMenu(menu, $("f-direct-wrap"));
}
window.toggleModelMenu = toggleModelMenu;

(function () {
  const menu = $("cmp-model-menu");
  if (!menu || menu.dataset.cmpBound) return;
  document.body.appendChild(menu);
  menu.dataset.cmpBound = "1";
  menu.addEventListener("click", (e) => {
    // 钻取/选定都在菜单内完成：阻止冒泡到 document 的「点外面收起」——
    // innerHTML 重写后 e.target 是已脱离 DOM 的旧节点，wrap.contains 会误判
    // false 而把刚钻取的菜单收掉（真机「点厂商没反应」根因）
    e.stopPropagation();
    const b = e.target.closest("[data-p],[data-back],[data-manage],[data-cli]");
    if (!b) return;
    if (b.dataset.manage) { toggleModelMenu(false); switchTab("models"); return; }
    if (b.dataset.back) { cmpModelProv = ""; cmpModelMenuRender(); return; }
    if (b.dataset.cli !== undefined) {
      // 手动指定执行 CLI：写真源即可，模型绑定保留在任务上（运行时 CLI 优先）
      $("f-direct-agent").value = b.dataset.cli;
      cmpDirectBtnSync();
      toggleModelMenu(false);
      return;
    }
    if (b.dataset.m === undefined) {
      // 点厂商 = 只钻取到模型级（用户拍板 2026-09-21：模型必须亲手点，
      // 不要自动带推荐）。真源不动——用户点了具体模型才写 provider/model。
      cmpModelProv = b.dataset.p;
      cmpModelMenuRender();
      return;
    }
    // 选定：写回隐藏 select 真源（提交流/回归测试全走原路径）。顺序关键：
    // 先让 provider 的 option 就位再赋值，否则 select.value 静默失效（真机
    // 「没法选模型」根因：options 由 poll 重建，可能仍是残缺态）。
    // 选定模型/自动推荐同时清除 CLI 指定（三者互斥，模型生效=走内置智能体）
    $("f-direct-agent").value = "";
    renderDirectModelPicker();
    $("f-direct-provider").value = b.dataset.p;
    renderDirectModelPicker();
    $("f-direct-model-name").value = b.dataset.m;
    if ($("f-direct-model-name").value !== b.dataset.m) {
      const opt = document.createElement("option");
      opt.value = b.dataset.m;
      $("f-direct-model-name").appendChild(opt);
      $("f-direct-model-name").value = b.dataset.m;
    }
    cmpDirectBtnSync();
    toggleModelMenu(false);
  });
  document.addEventListener("click", (e) => {
    const wrap = $("f-direct-wrap");
    if (wrap && !wrap.contains(e.target) && !menu.contains(e.target)) toggleModelMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleModelMenu(false);
  });
})();

/* 类型选择的可见层：带黑白图标的按钮 + 弹出菜单。原生 <option> 渲染不了 SVG，
 * 所以隐藏 select 只当值真源（既有联动/测试全部照旧），外观全靠这层。 */
function renderTypeMenu() {
  const menu = $("type-menu");
  if (!menu || !S.flows) return;
  menu.innerHTML = (S.flows || []).map((f) => {
    const est = S.flowEstimates && S.flowEstimates[f.id];
    return '<button type="button" class="type-item" role="option" data-v="' + esc(f.id) + '" onclick="pickType(\'' + esc(f.id) + '\')">' +
    '<span class="ti-ico" aria-hidden="true">' + flowIconHtml(f) + "</span>" +
    '<span class="ti-body"><span class="ti-name">' + esc(t(f.name)) +
    (f.builtin ? "" : '<em class="ti-tag">' + t("自定义") + "</em>") + "</span>" +
    '<span class="ti-desc">' + esc(flowDesc(f)) + "</span>" +
    (est ? '<span class="ti-est">' + esc(t("≈{0} tokens · {1}次", fmtTok(est.median_tokens), est.samples)) + "</span>" : "") +
    "</span>" +
    '<svg class="ico ti-check" aria-hidden="true"><use href="#i-check"></use></svg>' +
    "</button>";
  }).join("")
    /* 底部固定入口：管理流程替代原工具条上的「管理」按钮（简化 composer 工具行） */
    + '<div class="type-menu-foot"><button type="button" class="type-item" onclick="manageFlowsFromMenu()">' +
    '<span class="ti-ico" aria-hidden="true"><svg class="ico"><use href="#i-gear"></use></svg></span>' +
    '<span class="ti-body"><span class="ti-name">' + esc(t("管理任务类型…")) + "</span></span></button></div>";
  syncTypeBtn();
  fetchFlowEstimates();   // 异步回填预估行（就地更新，不重建菜单）
}

/* 类型菜单的成本/次数预估（借鉴 omnigent，数据源 /api/usage/estimate）：
 * 拉到手后就地更新菜单项——菜单开着也不重建 DOM，不打断滚动/悬停 */
async function fetchFlowEstimates() {
  if (!S.flows) return;
  const targets = S.flows;
  const results = await Promise.all(targets.map(async (f) => {
    try {
      const d = await api("/api/usage/estimate?type=" + encodeURIComponent(f.id) + "&days=90");
      return [f.id, (d && d.samples > 0) ? d : null];
    } catch (e) { return [f.id, null]; }
  }));
  S.flowEstimates = Object.fromEntries(results.filter(([, d]) => d));
  const menu = $("type-menu");
  if (!menu) return;
  menu.querySelectorAll(".type-item[data-v]").forEach((b) => {
    const d = S.flowEstimates[b.dataset.v];
    const body = b.querySelector(".ti-body");
    if (!d || !body || body.querySelector(".ti-est")) return;
    const est = document.createElement("span");
    est.className = "ti-est";
    est.textContent = t("≈{0} tokens · {1}次", fmtTok(d.median_tokens), d.samples);
    body.appendChild(est);
  });
}
window.manageFlowsFromMenu = function () {
  toggleTypeMenu(false);
  openFlowsManager();
};

function syncTypeBtn() {
  const sel = $("f-type");
  const flow = flowById(sel.value);
  const ico = $("f-type-ico"), name = $("f-type-name");
  if (flow && ico) ico.innerHTML = flowIconHtml(flow);
  if (flow && name) name.textContent = t(flow.name);
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
  S.typeTouched = true;   // 用户手动选过：flows 重渲染时优先保住，不让位偏好记忆
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
  renderQuickChips();
  const flow = flowById($("f-type").value);
  const engine = flow ? flow.engine : "code";
  const isReview = engine === "review";
  const isDirect = engine === "direct";
  const codeOnly = $("f-code-only"), reviewOnly = $("f-review-only");
  // 直连：验证命令与评审参数都不适用，两块一起收起（目标+附件即全部输入）
  if (codeOnly) codeOnly.classList.toggle("hidden", isReview || isDirect);
  if (reviewOnly) reviewOnly.classList.toggle("hidden", !isReview);
  // 对话模型选择已收进工具行 pill：更多选项里的旧块永久隐藏（select 真源保留）
  if ($("f-direct-model")) $("f-direct-model").classList.add("hidden");
  const dwrap = $("f-direct-wrap");
  if (dwrap) dwrap.classList.toggle("hidden", !isDirect);
  if (isDirect) { renderDirectModelPicker(); cmpDirectBtnSync(); }
  const bibleCreate = $("bible-create-field");
  const isSerialReview = isReview && !!(flow && flow.serial);
  if (bibleCreate) bibleCreate.classList.toggle("hidden", !isSerialReview);
  const clearSerialFields = () => ["f-chapters", "f-words-per-ch", "f-variants", "f-bible",
    "f-vol-chapters", "f-vol-spec"].forEach((id) => {
    const el = $(id);
    if (el) el.value = "";
  });
  const goal = $("f-goal");
  if (goal && flow && flow.goal_hint) goal.placeholder = t(flow.goal_hint);
  if (isReview && flow) {
    if (flow.manuscript) $("f-manuscript").value = flow.manuscript;
    // 偏好记忆（借鉴 chinese-novelist-skill）：上次创建用过的参数优先于流程出厂默认
    const lp = S.lastPrefs || {};
    $("f-rounds").value = lp.rounds || flow.rounds || 2;
    $("f-threshold").value = (lp.threshold != null ? lp.threshold : (flow.threshold || 7.0));
    $("f-bestof").value = lp.best_of || flow.best_of || 1;
    const rubric = $("f-rubric");
    const previousFlow = rubric.dataset.flowId || "";
    if (!S.flowRubricDrafts) S.flowRubricDrafts = {};
    if (previousFlow && previousFlow !== flow.id) {
      S.flowRubricDrafts[previousFlow] = rubric.value;
    }
    if (previousFlow !== flow.id) {
      rubric.value = Object.prototype.hasOwnProperty.call(S.flowRubricDrafts, flow.id)
        ? S.flowRubricDrafts[flow.id]
        : (flow.rubric || []).join(", ");
      rubric.dataset.flowId = flow.id;
    }
    // 连载参数预填（用户可改/可清空 = 单稿件模式）；偏好记忆的最近值优先
    if (flow.serial) {
      if (!$("f-chapters").value) $("f-chapters").value = lp.chapters || flow.serial.chapters || "";
      if (!$("f-words-per-ch").value) $("f-words-per-ch").value = lp.words_per_chapter || flow.serial.words_per_chapter || "";
      // 分卷开箱即用：没填过就预填一个常见每卷章数（用户可改；清空 = 不分卷）。
      // 卷边界由后端按全书章号推导，续写批次自动接上同一卷。
      if (!$("f-vol-chapters").value && !$("f-vol-spec").value) {
        $("f-vol-chapters").value = (lp.volume_chapters != null && lp.volume_chapters !== "")
          ? lp.volume_chapters : (flow.serial.volume_chapters || 20);
      }
    } else clearSerialFields();
  } else {
    // 隐藏控件仍保留 DOM 值；换流程时清理，避免章节/圣经状态污染下一次提交。
    clearSerialFields();
  }
  renderImplSelects();
  refreshEstimate();
}

function directProviders() {
  /* 停用厂商不列出（用户拍板 2026-09-21「停用的不弄出来」）；只列启用且有 key 的 */
  return (S.providers || []).filter((p) => p.enabled !== false && p.api_key);
}

function renderDirectModelPicker() {
  const ps = $("f-direct-provider"), ms = $("f-direct-model-name");
  if (!ps || !ms) return;
  const prev = ps.value;
  const provs = directProviders();
  ps.innerHTML = '<option value="">' + t("自动推荐") + '</option>' + provs.map((p) =>
    '<option value="' + esc(p.id) + '">' + esc(p.name || p.id) + "</option>").join("");
  if (prev && provs.some((p) => p.id === prev)) ps.value = prev;
  const p = provs.find((x) => x.id === ps.value);
  const oldModel = ms.value;
  const names = p ? (p.models || []).filter((m) => !m.hidden)
    .map((m) => m.name).filter(Boolean) : [];
  ms.innerHTML = '<option value="">' + t("随厂商推荐") + '</option>' + names.map((name) =>
    '<option value="' + esc(name) + '">' + esc(name) + "</option>").join("");
  if (oldModel && names.includes(oldModel)) ms.value = oldModel;
  cmpDirectBtnSync();   // 工具行 pill 文字跟随真源
}

function recommendTaskType(goal) {
  const s = String(goal || "").toLowerCase();
  const rules = [
    ["code", /(代码|bug|报错|修复|重构|接口|数据库|前端|后端|python|javascript|typescript|java|go\b|git)/i],
    ["translation", /(翻译|译成|translate|translation|中译英|英译中)/i],
    ["email", /(邮件|email|e-mail|回信|邀约信|商务函)/i],
    ["weekly_report", /(周报|月报|述职|工作汇报|工作总结)/i],
    ["video_script", /(短视频|口播|分镜|抖音|视频脚本)/i],
    ["speech", /(演讲稿|发言稿|致辞|演讲)/i],
    ["tech_proposal", /(技术方案|架构方案|选型方案|实施方案)/i],
    ["research", /(调研报告|竞品调研|市场调研|深度研究)/i],
    ["serial_novel", /(连载|续写.*章|网文|长篇小说)/i],
    ["novel", /(小说|故事|短篇|章节)/i],
    ["article", /(公众号|自媒体|文章|博客|小红书)/i],
  ];
  const hit = rules.find((x) => x[1].test(s));
  return hit && flowById(hit[0]) ? hit[0] : "";
}

/* 快捷类型 chips（Composer 框下方）：内置流程一键选定，选中态跟 #f-type 走 */
function renderQuickChips() {
  const box = $("cmp-quick");
  if (!box || !S.flows) return;
  const cur = ($("f-type") || {}).value;
  // 直连是默认档位：固定首枚 chip；其余内置流程跟后面（去重，共 5 枚）
  const builtins = (S.flows || []).filter((f) => f.builtin);
  const d = builtins.find((f) => f.id === "direct");
  const list = d ? [d].concat(builtins.filter((f) => f.id !== "direct").slice(0, 4))
                 : builtins.slice(0, 5);
  box.innerHTML = list.map((f) =>
    '<button type="button" class="cmp-chip' + (f.id === cur ? " on" : "") +
    '" onclick="pickQuickType(\'' + esc(f.id) + '\')">' + flowIconHtml(f) +
    "<span>" + esc(t(f.name)) + "</span></button>").join("");
}
window.pickQuickType = function (id) {
  pickType(id);
  const g = $("f-goal");
  if (g) g.focus();
};

/* 问候语（Composer 起手，ZCode 式）：按时段换称呼；启动/切语言/回任务页时重算 */
function cmpGreeting() {
  const el = $("cmp-greet");
  if (!el) return;
  const h = new Date().getHours();
  el.textContent = t(h >= 5 && h < 12 ? "上午好呀，有什么想让我帮忙的吗"
    : h < 18 ? "下午好呀，有什么想让我帮忙的吗"
    : "晚上好呀，有什么想让我帮忙的吗");
}

/* 开跑前成本与耗时预估（借鉴 omnigent 的 pre-run estimate）：优先读同类台账，
 * 无历史时显示流程基线；快速切类型用序号丢弃过期响应。 */
let _estSeq = 0;
async function refreshEstimate() {
  const el = $("cmp-estimate");
  const flowId = ($("f-type") || {}).value || "";
  if (!el) return;
  const seq = ++_estSeq;
  try {
    const mode = ($("f-mode") || {}).value || "auto";
    const thinking = ($("f-thinking") || {}).value || "standard";
    const rounds = ($("f-rounds") || {}).value || "";
    const query = new URLSearchParams({ type: flowId, days: "90", mode, thinking });
    if (rounds) query.set("rounds", rounds);
    const d = await api("/api/usage/estimate?" + query.toString());
    if (seq !== _estSeq) return;
    if (!d || !(d.estimated_duration_s > 0)) {
      el.classList.add("hidden"); el.textContent = ""; return;
    }
    const duration = chatDurTxt(d.estimated_duration_s);
    const p90 = chatDurTxt(d.p90_duration_s || d.estimated_duration_s);
    const source = d.samples > 0
      ? t("近{0}天 · {1}次同类", d.days || 90, d.samples)
      : t("暂无同类历史，按流程基线估算");
    const tokenPart = d.samples > 0 && d.median_tokens != null
      ? " · ≈" + fmtTok(d.median_tokens) + " tokens" : "";
    const cost = (d.median_cost_usd || 0) > 0 ? " · ≈$" + Number(d.median_cost_usd).toFixed(2) : "";
    el.textContent = t("预计完成约 {0}，保守不超过 {1}", duration, p90) +
      tokenPart + cost + t("（") + source + t("）");
    el.classList.remove("hidden");
  } catch (e) {
    if (seq === _estSeq) { el.classList.add("hidden"); el.textContent = ""; }
  }
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

/* 下载/预览等导航类 URL 同样带不了请求头：令牌拼进 query（已有 query 用 &，
 * 没有用 ?；已带 token 的 URL 原样返回）。本机会话令牌为空则原样返回。 */
function urlAuth(u) {
  const t = localStorage.getItem("orch.token");
  if (!t || String(u).includes("token=")) return u;
  return u + (String(u).includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

/* ---------------------------------------------------------- 工具 */
let _requestBusyCount = 0;
let _lastActionButton = null;
let _lastActionAt = 0;
const _requestBusyButtons = new WeakMap();
const _operations = [];
let _operationSeq = 0;

function operationLabel(path, opts) {
  if (opts && opts.operation === false) return "";
  if (opts && typeof opts.operation === "string") return t(opts.operation);
  if (opts && opts.busy === false && opts.operation !== true) return "";
  const method = String((opts && opts.method) || "GET").toUpperCase();
  if (method === "GET" || method === "HEAD") return "";
  // 只自动记录刚由用户点击触发的写操作；后台维护请求需显式传 operation 才显示。
  if (!(opts && opts.operation === true) &&
      (!_lastActionAt || performance.now() - _lastActionAt >= 2000)) return "";
  const clean = String(path || "").split("?")[0];
  const rules = [
    [/^\/api\/tasks$/, "创建任务"],
    [/\/tasks\/[^/]+\/delete$/, "删除任务"],
    [/\/tasks\/[^/]+\/archive$/, "归档任务"],
    [/\/tasks\/[^/]+\/retry$/, "重试任务"],
    [/\/tasks\/[^/]+\/continue$/, "创建续写任务"],
    [/\/tasks\/[^/]+\/rename$/, "重命名任务"],
    [/\/runs\/[^/]+\/cancel$/, "取消运行"],
    [/\/runs\/[^/]+\/delete$/, "删除运行"],
    [/\/runs\/(delete-batch|clear)$/, "清理运行记录"],
    [/\/attachments(?:\/[^/]+)?$/, "上传附件"],
    [/\/selfupdate\/apply$/, "升级 CodeBee"],
    [/\/market\//, "更新插件"],
    [/\/models\/binding$/, "更新智能体绑定"],
    [/\/models\//, "更新模型配置"],
    [/\/zentao\/test$/, "测试禅道连接"],
    [/\/zentao\/products$/, "拉取禅道产品"],
    [/\/zentao\/users$/, "拉取禅道账号"],
    [/\/zentao\/modules$/, "拉取模块清单"],
    [/\/zentao\/scan$/, "禅道扫描 Bug"],
    [/\/zentao\/config$/, "保存禅道配置"],
    [/\/wxdigest\/groups$/, "刷新群列表"],
    [/\/wxdigest\/scan$/, "扫描群摘要"],
    [/\/wxdigest\/config$/, "保存群摘要设置"],
    [/\/ports\/close$/, "关闭端口进程"],
  ];
  const hit = rules.find((it) => it[0].test(clean));
  return t(hit ? hit[1] : "提交操作");
}

function operationStart(path, opts) {
  const label = operationLabel(path, opts);
  if (!label) return null;
  const op = { id: ++_operationSeq, label, status: "running", at: Date.now(), error: "" };
  _operations.unshift(op);
  if (_operations.length > 12) _operations.length = 12;
  renderOperationCenter();
  return op.id;
}

function operationFinish(id, status, error) {
  if (!id) return;
  const op = _operations.find((it) => it.id === id);
  if (!op) return;
  op.status = status;
  op.error = String(error || "").slice(0, 240);
  op.endedAt = Date.now();
  renderOperationCenter();
}

function operationTime(ts) {
  try { return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  catch (e) { return ""; }
}

function renderOperationCenter() {
  const button = $("btn-ops");
  const list = $("op-list");
  if (!button || !list) return;
  button.classList.toggle("hidden", !_operations.length);
  const running = _operations.filter((it) => it.status === "running").length;
  const failed = _operations.filter((it) => it.status === "failed").length;
  button.classList.toggle("running", running > 0);
  button.classList.toggle("failed", !running && failed > 0);
  button.classList.toggle("done", !running && !failed && _operations.length > 0);
  const pill = $("op-pill-text");
  if (pill) pill.textContent = running ? t("{0} 项处理中").replace("{0}", running)
    : failed ? t("{0} 项失败").replace("{0}", failed) : t("操作完成");
  list.innerHTML = _operations.map((op) => {
    const status = op.status === "running" ? t("处理中")
      : op.status === "failed" ? t("失败") : t("已完成");
    const remove = op.status === "running" ? "" :
      '<button type="button" class="op-dismiss" aria-label="' + esc(t("移除")) +
      '" title="' + esc(t("移除")) + '" onclick="dismissOperation(event,' + op.id + ')">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-x"></use></svg></button>';
    return '<div class="op-row ' + op.status + '"><span class="op-state" aria-hidden="true"></span>' +
      '<div class="op-copy"><div><b>' + esc(op.label) + '</b><span>' + esc(status) + " · " +
      esc(operationTime(op.endedAt || op.at)) + '</span></div>' +
      (op.error ? '<p title="' + esc(op.error) + '">' + esc(op.error) + "</p>" : "") +
      "</div>" + remove + "</div>";
  }).join("");
}

function toggleOperationCenter(force) {
  const panel = $("op-center");
  const button = $("btn-ops");
  if (!panel || !button) return;
  const open = force === undefined ? panel.classList.contains("hidden") : !!force;
  panel.classList.toggle("hidden", !open);
  button.setAttribute("aria-expanded", open ? "true" : "false");
}

function clearFinishedOperations() {
  for (let i = _operations.length - 1; i >= 0; i--) {
    if (_operations[i].status === "done") _operations.splice(i, 1);
  }
  renderOperationCenter();
  if (!_operations.length) toggleOperationCenter(false);
}

function dismissOperation(event, id) {
  if (event) event.stopPropagation();
  const i = _operations.findIndex((it) => it.id === id && it.status !== "running");
  if (i >= 0) _operations.splice(i, 1);
  renderOperationCenter();
  if (!_operations.length) toggleOperationCenter(false);
}

window.toggleOperationCenter = toggleOperationCenter;
window.clearFinishedOperations = clearFinishedOperations;
window.dismissOperation = dismissOperation;

document.addEventListener("click", (e) => {
  const wrap = $("op-wrap");
  if (wrap && !wrap.contains(e.target)) toggleOperationCenter(false);
});

/* 记录发起请求的按钮。写请求统一在 api() 里挂忙碌态，避免每个业务入口各写一套；
 * 确认框的按钮不覆盖原始操作按钮，但会续期，使“确认后执行”仍反馈在原按钮上。 */
document.addEventListener("click", (e) => {
  const btn = e.target && e.target.closest ? e.target.closest("button") : null;
  if (!btn) return;
  if (btn.classList.contains("request-busy")) {
    e.preventDefault();
    e.stopImmediatePropagation();
    return;
  }
  _lastActionAt = performance.now();
  if (!btn.closest("#ask")) _lastActionButton = btn;
}, true);

function requestBusyStart(opts) {
  const method = String((opts && opts.method) || "GET").toUpperCase();
  if (opts && opts.busy === false) return null;
  if ((method === "GET" || method === "HEAD") && !(opts && opts.busy)) return null;

  _requestBusyCount++;
  let bar = $("request-progress");
  if (!bar && document.body) {
    bar = document.createElement("div");
    bar.id = "request-progress";
    bar.className = "request-progress";
    bar.setAttribute("role", "status");
    bar.setAttribute("aria-label", t("操作处理中"));
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
  }
  if (bar) {
    bar.classList.add("active");
    bar.setAttribute("aria-hidden", "false");
  }

  let btn = opts && opts.busyElement;
  if (typeof btn === "string") btn = document.querySelector(btn);
  if (!btn && performance.now() - _lastActionAt < 2000) btn = _lastActionButton;
  if (!btn || !document.documentElement.contains(btn)) btn = null;
  if (btn) {
    let state = _requestBusyButtons.get(btn);
    if (!state) {
      state = {
        count: 0,
        ariaBusy: btn.getAttribute("aria-busy"),
        ariaDisabled: btn.getAttribute("aria-disabled"),
      };
      _requestBusyButtons.set(btn, state);
    }
    state.count++;
    btn.classList.add("request-busy");
    btn.setAttribute("aria-busy", "true");
    btn.setAttribute("aria-disabled", "true");
  }
  return { button: btn };
}

function requestBusyEnd(token) {
  if (!token) return;
  _requestBusyCount = Math.max(0, _requestBusyCount - 1);
  const btn = token.button;
  const state = btn && _requestBusyButtons.get(btn);
  if (state) {
    state.count--;
    if (state.count <= 0) {
      btn.classList.remove("request-busy");
      if (state.ariaBusy === null) btn.removeAttribute("aria-busy");
      else btn.setAttribute("aria-busy", state.ariaBusy);
      if (state.ariaDisabled === null) btn.removeAttribute("aria-disabled");
      else btn.setAttribute("aria-disabled", state.ariaDisabled);
      _requestBusyButtons.delete(btn);
    }
  }
  if (_requestBusyCount === 0) {
    const bar = $("request-progress");
    if (bar) {
      bar.classList.remove("active");
      bar.setAttribute("aria-hidden", "true");
    }
  }
}

async function api(path, opts) {
  opts = opts || {};
  // 可选超时：长请求（如外部插件下载）传 opts.timeout，网络卡死时也能
  // 按时给出错误反馈，而不是无限静默
  let ctrl = null;
  if (opts.timeout) {
    ctrl = new AbortController();
    var timer = setTimeout(() => ctrl.abort(), opts.timeout);
  }
  const busyToken = requestBusyStart(opts);
  const operationToken = operationStart(path, opts);
  const fetchOpts = Object.assign({}, opts);
  delete fetchOpts.timeout;
  delete fetchOpts.busy;
  delete fetchOpts.busyElement;
  delete fetchOpts.operation;
  delete fetchOpts._ctrlRetry;
  try {
    let res;
    try {
      res = await fetch(path, Object.assign({ headers: authHeaders() }, fetchOpts,
        ctrl ? { signal: ctrl.signal } : {}));
    } catch (e) {
      if (ctrl && e.name === "AbortError") throw new Error(t("请求超时，请重试或检查网络"));
      throw e;
    }
    if (res.status === 401) { showTokenGate(t("令牌不正确或已更换，请重新输入")); throw new Error(t("需要访问令牌")); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (res.status === 423 && data && data.control) {
      setControl(data.control);
      // 手机端接管闭环（用户实测反馈：423 连点全失败不知道怎么办）：
      // 他人持有时弹「接管并重试」确认，确认后抢夺控制权并自动重试一次。
      // uiConfirm 弹窗互斥防堆叠；重试标记防递归；GET 轮询不会走到这里。
      const holder = data.control.holder || "";
      if (!opts._ctrlRetry && !opts.silent && !api._ctrlPrompting && holder) {
        api._ctrlPrompting = true;
        try {
          if (await uiConfirm(t("「{0}」正在控制，接管后自动重试「{1}」？",
            holder, operationLabel(path, { ...opts, operation: true }) || t("提交操作")),
            { ok: t("接管并重试") })) {
            await ctrlAction("acquire", true);
            api._ctrlPrompting = false;
            return api(path, Object.assign({}, opts, { _ctrlRetry: true }));
          }
        } finally {
          api._ctrlPrompting = false;
        }
      }
    }
    if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
    const method = String(fetchOpts.method || "GET").toUpperCase();
    if (method !== "GET") {
      if (String(path).startsWith("/api/catalog/")) S._catalogLoaded = false;
      if (String(path).startsWith("/api/models/")) S._modelsLoaded = false;
    }
    operationFinish(operationToken, "done");
    return data;
  } catch (e) {
    operationFinish(operationToken, "failed", e && e.message ? e.message : e);
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
    requestBusyEnd(busyToken);
  }
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

/* 内联 onclick 里的字符串字面量转义。esc 不够用：它把 ' 编成 &#39;，浏览器
 * 解码属性值后 JS 仍看到一个裸引号，用户自建标签含撇号就会截断字面量
 * （语法错误或参数错位）。这里先做 HTML 属性转义再叠 JS 反斜杠转义。 */
function jsq(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function statusChip(st) {
  const zh = { queued: t("排队中"), running: t("运行中"), done: t("完成"), failed: t("失败"), cancelled: t("已取消"), timeout: t("超时") };
  return '<span class="chip ' + esc(st) + '">' + (zh[st] || esc(st)) + "</span>";
}

/* 运行状态文案（传 run 对象）：退避窗口内的续跑副本写明「将于 HH:MM 自动
 * 续跑」，别让 5 分钟等待看起来像卡死/资源排队（2026-09-18 重写任务误判案）。
 * 到点后翻回「排队中」——页面轮询重渲染时 Date.now() 已过预定时刻。 */
function runStatusText(run) {
  const st = String((run && run.status) || "");
  if (st === "queued" && run && run.resume_enqueue_at) {
    const at = Date.parse(String(run.resume_enqueue_at).replace(" ", "T"));
    if (!isNaN(at) && Date.now() < at)
      return t("将于 {0} 自动续跑", String(run.resume_enqueue_at).slice(11, 16));
  }
  const txt = { queued: t("排队中"), running: t("运行中"), done: t("完成"),
    failed: t("失败"), cancelled: t("已取消"), timeout: t("超时") }[st] || st;
  // 「跑完」≠「通过」：done 但评审未过（verdict.pass=false）必须一眼可辨——
  // 此前一律显示「完成」，用户以为修好了（2026-09-23 禅道双单实案反馈）
  if (st === "done" && run && run.verdict && run.verdict.pass === false)
    return txt + t("·未达标");
  return txt;
}

/* 运行错误来源徽标：错误文案以「超时」打头（runner 统一格式）时标 TIMEOUT，
 * 和普通失败一眼区分；运行状态仍是 failed，不动状态机 */
function errTag(err) {
  return /^超时/.test(err || "") ? '<b class="err-tag">TIMEOUT</b>' : "";
}

function runKindTag(k) {
  return k === "mgmt" ? '<span class="tag">' + t("管理") + '</span>' : '<span class="tag">' + t("编排") + '</span>';
}

/* 供应商来源标签：手动添加的不打标，其余按来源 id → 显示名映射 */
function srcTag(source) {
  if (!source || source === "manual") return "";
  const name = (S.sourceNames || {})[source] || source;
  return '<span class="tag">' + esc(name) + "</span>";
}

/* 可注入 CLI 的供应商：显式 anthropic/openai，或 auto 但实测出过 wire
 * （wire_caps）。google 与「auto 且还没探测」的仅登记。 */
function bindableProvs(provs) {
  return (provs || []).filter((p) => p.protocol === "anthropic" || p.protocol === "openai" ||
    (p.wire_caps || {}).anthropic || (p.wire_caps || {}).openai);
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

/* 极简 Markdown 渲染（标题/加粗/行内码/列表/表格/代码块）。
 * 围栏代码块走 codeBlockHTML（行号表格+高亮+代码主题）：裸 <pre> 只有
 * --log-bg 底色没配浅字，暖色皮肤里深底配深字根本读不了。 */
function md2html(md) {
  const lines = String(md || "").split(/\r?\n/);
  let html = [], inCode = false, codeBuf = null, inTable = false, listOpen = false;
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const closeList = () => { if (listOpen) { html.push("</ul>"); listOpen = false; } };
  const closeTable = () => { if (inTable) { html.push("</tbody></table>"); inTable = false; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      closeList(); closeTable();
      if (inCode) { html.push(codeBlockHTML(codeBuf.join("\n"))); codeBuf = null; }
      else codeBuf = [];
      inCode = !inCode; continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }
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
  if (inCode && codeBuf) html.push(codeBlockHTML(codeBuf.join("\n")));   // 未闭合围栏也按代码块收尾
  return html.join("\n");
}

/* ---------------------------------------------------------- 状态同步：SSE 实时 + 轮询降级 */
/* 应用 /api/state 或 SSE 推送的状态载荷 */
function applyState(d) {
  S.state = d;
  if (d.control) setControl(d.control);
  if (d.health) renderHealthBanner(d.health);
  $("conn").textContent = t("已连接");
  $("conn").className = "conn ok";
  restoreInspector();   // 刷新后恢复上次打开的检查器（只在已开时为空操作）
  ovMaybeRefresh();     // 停在概览页时：概览语与最近运行跟着这次状态一起刷新
}

/* ---------------------------------------------------------- 供应商健康告警横幅 */
/* 告警触发时顶栏横幅（厂商+模型）+ 提示音；点击弹出详情弹框：
   静默本次告警 / 手动标记恢复 / 一键禁用厂商。 */
let _healthBeeped = false;

function renderHealthBanner(health) {
  const el = $("health-banner");
  if (!el) return;
  const alerts = (health && health.alerts) || [];
  if (!alerts.length) {
    const rec = (health.providers || []).find((p) => p.status === "recovered");
    if (rec && Date.now() - S._healthRecoveredAt < 60000) {
      el.textContent = "✓ " + rec.provider + " " + t("已恢复");
      el.className = "health-banner recovered";
    } else {
      el.className = "health-banner hidden";
      el.textContent = "";
      _healthBeeped = false;
    }
    return;
  }
  el.textContent = healthBannerText(health);
  el.className = "health-banner alerting";
  el.title = alerts.map((p) =>
    p.provider + (p.model ? " · " + p.model : "") +
    (p.static
      ? t("：") + (p.last_error || t("未知错误"))
      : t("：连续失败 ") + p.consecutive_failures + t(" 次（") + (p.last_error || t("未知错误")) + t("）"))
  ).join("\n");
  if (!_healthBeeped) { _healthBeeped = true; beepAttention(); }
}

function healthBannerText(health) {
  return ((health && health.alerts) || []).map((p) => {
    const m = p.model ? " · " + p.model : "";
    return "⚠ " + p.provider + m + " " + (p.static ? t("绑定链失效") : t("连接异常"));
  }).join(t("　"));
}

function healthBannerClick() {
  const alerts = (S.state && S.state.health && S.state.health.alerts) || [];
  if (!alerts.length) return;
  const rows = alerts.map((p) => {
    const model = p.model ? " · " + p.model : "";
    const statLine = p.static ? "" :
      "<div style='opacity:.75;font-size:12px;margin-top:2px'>" +
      t("连续失败") + " " + p.consecutive_failures + " " + t("次 · 首次失败 ") + (p.first_fail_at || "-") +
      "</div>";
    return "<div style='margin-bottom:10px'>" +
      "<b>" + esc(p.provider) + (p.model ? " · " + esc(p.model) : "") + "</b>" +
      statLine +
      "<div style='color:#dc2626;font-size:12px;margin-top:2px;word-break:break-all'>" +
      esc(p.last_error || "") + "</div></div>";
  }).join("");
  const pid0 = alerts[0].provider_id || "";
  const model0 = alerts[0].model || "";
  const foot =
    (pid0 && model0 ? "<button class='btn ghost' onclick=\"healthDisableModel('" + esc(pid0) + "','" + esc(model0) + "')\">" + t("禁用该模型") + "</button>" : "") +
    (pid0 ? "<button class='btn ghost' onclick=\"healthDisableProvider('" + esc(pid0) + "')\">" + t("禁用该厂商") + "</button>" : "") +
    "<button class='btn ghost' onclick=\"healthOp('silence')\">" + t("静默本次告警") + "</button>" +
    "<button class='btn ghost' onclick=\"healthOp('reset')\">" + t("手动标记恢复") + "</button>" +
    "<button class='primary' onclick='closeModal()'>" + t("关闭") + "</button>";
  openModal(t("供应商健康告警"), rows, foot);
}

async function healthOp(op) {
  const alerts = (S.state && S.state.health && S.state.health.alerts) || [];
  const down = alerts[0];
  if (!down) return;
  try {
    const r = await api("/api/health/op", { method: "POST",
      body: JSON.stringify({ provider: down.provider, op }) });
    if (r && r.health) { S.state.health = r.health; renderHealthBanner(r.health); }
    closeModal(); render();
    if (op === "silence") toast(t("已静默 {0} 的告警（恢复后自动重新武装）").replace("{0}", down.provider));
    if (op === "reset") toast(t("已手动标记 {0} 为恢复，探针将重新核实").replace("{0}", down.provider));
  } catch (e) {
    toast(t("操作失败：") + e.message, true);
  }
}

/* 模型级禁用：绑定链里它的条目由后端自动剔除（modelhub 停用即出调度）。
   只对出问题的那一格生效——厂商其它模型照常可用。 */
async function healthDisableModel(pid, model) {
  if (!pid || !model) { closeModal(); return; }
  const yes = await uiConfirm(
    t("确认禁用模型 {0}？调度链里它的条目会自动移除，其余模型不受影响；可在模型调度页重新启用。").replace("{0}", pid + " · " + model),
    { title: t("禁用模型"), danger: true, ok: t("禁用") });
  if (!yes) return;
  try {
    await api("/api/models/model-op", { method: "POST",
      body: JSON.stringify({ provider_id: pid, name: model, op: "disable" }) });
    if (S.state.health && S.state.health.alerts[0]) {
      await api("/api/health/op", { method: "POST",
        body: JSON.stringify({ provider: S.state.health.alerts[0].provider, op: "silence" }) });
    }
    const models = await api("/api/models");
    S.providers = models.providers; S.bindings = models.bindings;
    S.modelCatalog = models.catalog || [];
    S.provSig = "";
    closeModal(); render(); loadOrchestrator();
    toast(t("已禁用模型 {0} · {1}，调度链已自动移除它；绑定页可重新启用").replace("{0}", pid).replace("{1}", model));
  } catch (e) {
    toast(t("操作失败：") + e.message, true);
  }
}

async function healthDisableProvider(pid) {
  if (!pid) { closeModal(); return; }
  const yes = await uiConfirm(
    t("确认禁用该厂商？调度链里它的条目会自动移除，恢复后可在模型调度页重新启用。"),
    { title: t("禁用厂商"), danger: true, ok: t("禁用") });
  if (!yes) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [pid], op: "disable" }) });
    if (S.state.health && S.state.health.alerts[0]) {
      await api("/api/health/op", { method: "POST",
        body: JSON.stringify({ provider: S.state.health.alerts[0].provider, op: "silence" }) });
    }
    const models = await api("/api/models");
    S.providers = models.providers; S.bindings = models.bindings;
    S.provSig = "";
    closeModal(); render(); loadOrchestrator();
    toast(t("已禁用厂商 {0}：调度链已自动移除它的条目，绑定页可重新启用").replace("{0}", pid));
  } catch (e) {
    toast(t("操作失败：") + e.message, true);
  }
}

async function refreshState() {
  applyState(await api("/api/state", { timeout: 15000, busy: false, operation: false }));
}

let _lookupRefresh = null;
async function refreshPollLookups(force) {
  if (_lookupRefresh) return _lookupRefresh;
  const now = Date.now();
  const loadCatalog = !!force || !S._catalogLoaded || now - (S._catalogAt || 0) >= 60000;
  const loadModels = !!force || !S._modelsLoaded || now - (S._modelsAt || 0) >= 30000;
  if (!loadCatalog && !loadModels) return;
  if (loadCatalog) S._catalogAt = now;
  if (loadModels) S._modelsAt = now;
  _lookupRefresh = (async () => {
    try {
      const [cat, models] = await Promise.all([
        loadCatalog ? api("/api/catalog", { timeout: 15000 }) : Promise.resolve(null),
        loadModels ? api("/api/models", { timeout: 15000 }) : Promise.resolve(null),
      ]);
      if (cat) {
        S.catalog = cat.catalog;
        S.catalogChecking = !!cat.checking;
        S._catalogLoaded = true;
      }
      if (models) {
        S.providers = models.providers; S.bindings = models.bindings;
        S.modelCatalog = models.catalog || [];
        S.sourceNames = models.source_names || S.sourceNames || {};
        S._modelsLoaded = true;
        renderDirectModelPicker();
        const provSig = JSON.stringify((S.providers || []).map((p) => [p.id, p.enabled, !!p.api_key]));
        if (provSig !== S.provSig) { S.provSig = provSig; loadOrchestrator(); }
      }
      render();
    } catch (e) {
      if (loadCatalog) S._catalogAt = 0;
      if (loadModels) S._modelsAt = 0;
    }
  })();
  try { await _lookupRefresh; }
  finally { _lookupRefresh = null; }
}

let _pollBusy = false;
async function poll() {
  // 上一轮还没回来就跳过本轮：服务端偶发卡顿（如目录探测）时，无超时的
  // 轮询会每 2s 叠两条连接，塞满浏览器 6 连接池后连点击的请求都排不上队
  // ——整个界面（左栏+右栏）一起假死（2026-09-22 用户实测）。在飞保护 +
  // 15s 超时保证任何时刻最多占两条连接，卡完即自愈。
  if (_pollBusy) return;
  _pollBusy = true;
  try {
    // 状态先独立拉取并渲染；目录/模型是低频数据，后台刷新不能挡住任务树和点击。
    if (!document.hidden) refreshPollLookups(false);
    if (!S.sseLive) await refreshState();
    else if (S._lastSseAt && Date.now() - S._lastSseAt > 60000 && !document.hidden) {
      // SSE 静默看门狗：连接半死时 EventSource 不报错、sseLive 恒真、上面
      // 的拉取被跳过——侧栏会冻在几分钟前的快照，而详情卡自拉自新，两头
      // 对不上（2026-09-22 详情「完成」侧栏「在跑」实案）。60s 没有任何
      // 推送就主动拉一次全量兜底；拉完把基准推后，别每 8s 都拉。后台页签
      // 没人看，不烧这份流量。
      S._lastSseAt = Date.now();
      await refreshState();
    }
    render();
  } catch (e) {
    if (!S.sseLive || !S.es) {
      $("conn").textContent = t("连接失败");
      $("conn").className = "conn bad";
    }
  } finally {
    _pollBusy = false;
  }
}

function schedulePolling() {
  clearInterval(S.pollTimer);
  S.pollTimer = setInterval(() => { if (!document.hidden) poll(); }, S.sseLive ? 8000 : 2000);
}

/* SSE：状态变化即时推送；断开自动重连，重连失败降级为 2s 轮询 */
function startSSE() {
  if (!window.EventSource) return;
  try {
    const es = new EventSource("/api/events" + qsAuth());
    S.es = es;
    es.onopen = () => {
      S.sseLive = true;
      S._lastSseAt = Date.now();
      schedulePolling();
      poll();
    };
    es.onmessage = (ev) => {
      S._lastSseAt = Date.now();
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

/* 页签可见性：后台页签关闭 SSE 让出连接（HTTP/1.1 单域名 6 连接上限，
 * 每个隐藏页签一条永久 SSE 会把连接池吃光，前台页签的接口全部排队超时
 * ——2026-09-23 实案：多标签打开时步骤列表/日志请求超时）。回到前台
 * 立即重开 SSE 并拉一次全量。轮询在隐藏态本就被跳过，后台零连接占用。 */
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (S.es) { try { S.es.close(); } catch (e) {} S.es = null; S.sseLive = false; schedulePolling(); }
  } else if (!S.es) {
    startSSE();
    poll();
  }
});

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
      tip = t("{0} 正在控制（点击接管）", holder);
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
      api("/api/control/heartbeat", { method: "POST", body: "{}", busy: false })
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
  refreshDetailSide(); // 详情页开着时节流跟刷任务级 side（累计统计 + git 实时）
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
                              S.testProvState, S.testModelState, S.probeState,
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
  syncSaveAllBtn();
  if (!targets.length) {
    bbox.innerHTML = '<div class="empty">' + t("还没有装好的 CLI 智能体（codex / claude / kimi 等）。") +
      '<a href="#" onclick="return helpOpenTopic(\'quickstart\')">' + t("看看快速上手") + '</a></div>';
    return;
  }
  bbox.innerHTML = targets.map((c) => {
    const b = (S.bindings || {})[c.id] || {};
    const opts = '<option value="">' + t("自动推荐（推荐）") + '</option>' + bindable.map((p) => {
      const adaptedOnly = p.protocol !== "anthropic" && p.protocol !== "openai";
      return '<option value="' + esc(p.id) + '"' + (b.provider_id === p.id ? " selected" : "") + ">" +
        esc(p.name) + t("（") + esc(protoLabel(p)) + (adaptedOnly ? t(" · 已适配") : "") +
        (p.enabled === false ? t(" · 已停用") : "") + t("）") + "</option>";
    }).join("");
    const bound = provs.find((p) => p.id === b.provider_id);
    const offWarn = bound && bound.enabled === false
      ? '<p class="hint warn">' + t("该供应商已停用：编排时不会注入它，模型链因此失效时相关步骤会直接判失败。") + '</p>' : "";
    const protoWarn = bound && !bindable.some((p) => p.id === bound.id)
      ? '<p class="hint warn">' + t("该供应商协议为 ") + esc(protoLabel(bound)) +
        t("，当前没有可注入的 CLI，编排时相关步骤会直接判失败。") + '</p>' : "";
    // 供应商停用但链上勾选了它家模型：链条目在解析时也会被整条跳过，链空了
    // 相关步骤会直接判失败（2026-09-17 起；此前的静默回落本机默认正是 2026-09-16
    // 配额事故的隐藏形态）——单说「供应商下拉」警告不够，链本身的死活也要点名。
    const chainDead = bindChain(b).some((c2) => c2.p && (() => {
      const pv = provs.find((p) => p.id === c2.p);
      return !pv || pv.enabled === false;
    })());
    const chainWarn = (!bound || bound.enabled !== false) && chainDead
      ? '<p class="hint warn">' + t("模型链里有已停用/已删除的供应商：这些条目解析时会被跳过，链可能因此整体失效，相关步骤将判失败。") + '</p>' : "";
    return '<div class="card"><div class="head"><span class="name">' + esc(c.name) + "</span>" +
      '<span class="tag">' + esc(c.orch_kind) + "</span></div>" +
      '<div class="field"><label>' + t("供应商") + '</label><select id="bindprov-' + esc(c.id) + '">' + opts + "</select></div>" +
      '<p class="hint">' + t("默认不需要绑定：系统会按任务类型、难度、成本与健康状态自动推荐可用模型；只有需要固定厂商、模型或降级顺序时才在这里绑定。绑定后编排调用会注入该供应商的 API key 与地址；完全未指定时保持自动调度，没有兼容可用供应商才使用 CLI 自身配置。") + '</p>' +
      bindModelBox(c, b.provider_id) + offWarn + protoWarn + chainWarn +
      '<div class="ops"><label class="toggle"><input type="checkbox" id="binddiff-' + esc(c.id) + '"' +
      (b.difficulty_routing ? " checked" : "") + '>' + t(" 按难度自动选模型（简单/困难）") + '</label>' +
      '<button class="ghost small" onclick="saveBinding(\'' + esc(c.id) + '\')">' + t("保存") + '</button></div></div>';
  }).join("") +
    (!targets.length ? '<p class="hint">' + t("还没有已安装且可编排的 CLI——先到「本机智能体」页安装并启用。") + '</p>' : "") +
    (nonBindable ? '<p class="hint">' + t("另有 ") + nonBindable +
      t(" 个供应商（google 等协议）仅登记，不支持注入 CLI，未出现在上面的下拉中。") + '</p>' : "");
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
  const batch = selN ? '<div class="prov-batch"><span class="n">' + t("已选 ") + selN + t(" 个") + "</span>" +
    '<button class="ghost small" onclick="batchProvOp(\'enable\')">' + t("启用") + '</button>' +
    '<button class="ghost small" onclick="batchProvOp(\'disable\')">' + t("停用") + '</button>' +
    '<button class="danger small" onclick="batchProvOp(\'delete\')">' + t("删除") + '</button>' +
    '<button class="ghost small" onclick="clearProvSel()">' + t("取消") + '</button></div>' : "";
  const allBox = provs.length ? '<label class="prov-all"><input type="checkbox"' +
    (selN === provs.length && selN > 0 ? " checked" : "") +
    ' onchange="toggleAllProvSel(this.checked)">' + t(" 全选") +
    (kw ? t("（筛选后 ") + provs.length + t(" 个）") : t("（") + provs.length + t("）")) + "</label>" : "";
  box.innerHTML = batch + allBox + (provs.map((p) => {
    const n = p.models == null ? null : p.models.filter((m) => !m.hidden).length;
    const st = n == null ? t("未获取") : n + t(" 模型");
    const off = p.enabled === false;
    return '<div class="prov-item' + (p.id === S.selProv ? " active" : "") + (off ? " off" : "") +
      '" onclick="selectProvider(\'' + esc(p.id) + '\')">' +
      '<input type="checkbox" class="pi-check"' + (S.selProvs[p.id] ? " checked" : "") +
      ' title="' + t("勾选以批量操作") + '" onclick="event.stopPropagation()"' +
      ' onchange="toggleProvSel(\'' + esc(p.id) + '\', this.checked)">' +
      '<div class="pi-body"><div class="pi-top"><div class="pi-name">' + esc(p.name) + "</div>" +
      '<span class="pi-n">' + st + "</span></div>" +
      '<div class="pi-meta"><span class="tag">' + esc(protoLabel(p)) + "</span>" +
      (p.protocol === "auto" ? "" : wireCapTags(p, p.protocol)) +
      ((p.keys || []).length > 1 ? '<span class="tag">' + t("%1 把密钥").replace("%1", (p.keys || []).length) + "</span>" : "") +
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
    disable: t("停用所选 ") + ids.length + t(" 个供应商？\n停用后其绑定会回落为 CLI 默认；配置与模型列表都保留，可随时再启用。"),
    delete: t("删除所选 ") + ids.length + t(" 个供应商？\n相关显式模型调度会自动解除，此操作不可撤销。"),
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
    box.innerHTML = '<div class="empty">' + t("左侧选择供应商；还没有供应商时点左上角「导入」，") +
      t("可从 CCSwitch / Codex / Claude Code / ZCode / Qwen / Gemini / OpenCode / Continue / Cursor / Trae 扫描带入。") +
      '<br><a href="#" onclick="return helpOpenTopic(\'models\')">' + t("还没配过模型？看「模型接入与绑定」帮助") + '</a></div>';
    return;
  }
  const tp = (S.testProvState || {})[p.id];
  const tpHtml = tp ? (tp.status === "reachable_unverified"
    ? '<span class="badge warn" title="' + esc(tp.note || "") + '">⚠ ' +
      t("可达，密钥未验证") + " · " + tp.latency_ms + "ms</span>"
    : (tp.ok
    ? '<span class="badge ok"' + (tp.note ? ' title="' + esc(tp.note) + '"' : "") + '>' +
      t("✓ 连通 ") + tp.latency_ms + "ms" +
      (tp.note ? " · " + t("无模型列表接口") : " · " + tp.count + t(" 个模型")) + "</span>"
    : '<span class="badge bad" title="' + esc(tp.error || "") + '">✗ ' + esc((tp.error || t("失败")).slice(0, 60)) + "</span>")) : "";
  // 适配测试通过的 wire（同密钥实测可注入的另一条协议面）；形态随徽章标出
  const capTags = wireCapTags(p, "");
  const pb = (S.probeState || {})[p.id];
  const pbHtml = !pb ? "" : (pb.busy
    ? '<span class="badge">' + esc(t("适配测试中…")) + "</span>"
    : (pb.ok ? '<span class="badge ok">' : '<span class="badge bad">') + esc(pb.message || "") + "</span>");
  const models = (p.models || []).filter((m) => !m.hidden)
    .sort((a, b) => (a.priority || 0) - (b.priority || 0));
  const hidden = (p.models || []).filter((m) => m.hidden);
  const sel = modelSel(p.id);
  const selN = Object.keys(sel).length;
  let html = '<div class="pd-head">' +
    '<div class="pd-title"><span class="pd-name">' + esc(p.name) + "</span>" +
    '<span class="tag">' + esc(protoLabel(p)) + "</span>" + capTags +
    (p.enabled === false ? '<span class="tag">' + t("已停用") + "</span>" : "") +
    srcTag(p.source) + tpHtml + pbHtml + "</div>" +
    '<div class="pd-url" title="' + esc(p.base_url || "") + '">' + esc(p.base_url || "") + "</div>" +
    '<div class="pd-ops">' +
    '<button class="primary small" onclick="testProv(\'' + esc(p.id) + '\')">' + t("测试连接") + "</button>" +
    '<button class="ghost small" onclick="probeWire(\'' + esc(p.id) + '\')" title="' +
    esc(t("实测该网关同一密钥是否也支持另一条 wire 协议；通过后绑定链可跨协议注入")) + '">' + t("适配测试") + "</button>" +
    '<button class="ghost small" onclick="refreshProviderModels(\'' + esc(p.id) + '\')">' + t("获取模型列表") + "</button>" +
    '<button class="ghost small" onclick="duplicateProvider(\'' + esc(p.id) + '\')" title="' + t("复制一份（同地址同密钥，可再换 KEY / 改名）") + '">' + t("复制一份") + "</button>" +
    '<button class="ghost small" onclick="toggleProviderEnabled(\'' + esc(p.id) + '\', ' +
    (p.enabled === false) + ')">' + (p.enabled === false ? t("启用供应商") : t("停用供应商")) + "</button>" +
    '<span class="pd-ops-gap"></span>' +
    '<button class="danger small" onclick="delProvider(\'' + esc(p.id) + '\')">' + t("删除") + "</button>" +
    "</div></div>";
  if (selN) {
    html += '<div class="mrow-batch"><span class="n">' + t("已选 ") + selN + t(" 个模型") + "</span>" +
      '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'enable\')">' + t("启用") + '</button>' +
      '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'disable\')">' + t("停用") + '</button>' +
      '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'restore\')">' + t("恢复") + '</button>' +
      '<button class="danger small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'delete\')">' + t("删除") + '</button>' +
      '<button class="ghost small" onclick="clearModelSel(\'' + esc(p.id) + '\')">' + t("取消") + '</button></div>';
  }
  html += '<details class="pd-config"><summary>' + t("编辑供应商配置（地址 / 协议 / 难度模型）") + '</summary>' +
    providerCard(p) + "</details>";
  if (p.models == null) {
    html += '<div class="empty">' + t("尚未获取模型列表——点上方「获取模型列表」。") + '</div>' +
      '<div class="hint">' + t("厂商列表接口调不通？直接手工添加模型名也能用。") + '</div>' +
      pmAddHtml(p);
  } else {
    const kw = (S.modelFilter || {})[p.id] || "";
    html += '<div class="pm-tools">' +
      '<input id="pm-search-' + esc(p.id) + '" type="search" placeholder="' + t("按名称过滤模型…") + '" autocomplete="off"' +
      ' value="' + esc(kw) + '" oninput="filterModels(\'' + esc(p.id) + '\', this.value)">' +
      '<span class="pm-count" id="pm-count-' + esc(p.id) + '"></span>' +
      pmAddHtml(p) + '</div>';
    html += '<div id="pm-groups" class="pm-groups' + (selN ? " has-sel" : "") + '">' +
      provModelGroupsHtml(p) + "</div>";
    if (hidden.length) {
      html += '<details class="prow-hidden"><summary>' + t("已删除 ") + hidden.length +
        t(" 个模型（刷新不会再带回）") + "</summary><div class=\"prow-hidden-list\">";
      for (const m of hidden) {
        html += '<label><input type="checkbox"' + (sel[m.name] ? " checked" : "") +
          ' onchange="toggleModelSel(\'' + esc(p.id) + '\', \'' + esc(m.name) + '\', this.checked)">' +
          esc(m.name) + "</label>";
      }
      html += '</div><div class="prow-hidden-ops">' +
        '<button class="ghost small" onclick="batchModelOp(\'' + esc(p.id) + '\', \'restore\')">' + t("恢复所选") + '</button>' +
        '<button class="ghost small" onclick="restoreHidden(\'' + esc(p.id) + '\')">' + t("恢复全部") + '</button>' +
        "</div></details>";
    }
  }
  // 密钥区放最下：与模型列表同属「日常不动、偶尔来看」的配置，别挡在模型区前面
  html += keyListHtml(p);
  box.innerHTML = html;
  updateModelCount(p.id);
  bindModelRowDnD(p.id, box);
  bindKeyRowDnD(p.id, box);
}

/* ---------------------------------------------------------- 多 KEY（同厂商多密钥）
 * 顺序即调用顺序：欠费/停用的 KEY 被解析自动跳过，切到下一把。 */
function keyListHtml(p) {
  const keys = p.keys || [];
  const single = keys.length <= 1;
  const rows = keys.map((k, i) => {
    const badge = !k.enabled
      ? '<span class="tag">' + t("已停用") + "</span>"
      : (k.cooling
         ? '<span class="tag warn" title="' + esc(k.last_error || "") + '">' + t("冷却中") + "</span>" : "");
    const err = k.last_error
      ? '<span class="hint warn" title="' + esc(k.last_error) + '">✗ ' + esc(k.last_error.slice(0, 40)) + "</span>" : "";
    const inUse = k.enabled && !k.cooling && k.key === p.api_key;
    return '<div class="krow' + (k.enabled ? "" : " off") + '" draggable="true" data-kid="' + esc(k.id) + '">' +
      '<span class="drag" title="' + t("拖动调整调用顺序") + '"><svg class="ico" aria-hidden="true"><use href="#i-grip"></use></svg></span>' +
      '<span class="pprio">#' + (i + 1) + "</span>" +
      '<span class="klabel" title="' + esc(k.label || "") + '">' +
        esc(k.label || t("密钥") + " " + k.id.slice(1)) + "</span>" +
      '<span class="kval hint">' + esc(k.key) + "</span>" +
      (inUse ? '<span class="tag ok">' + t("使用中") + "</span>" : "") + badge + err +
      '<span class="row-ops">' +
      (k.cooling ? '<button class="ghost small row-op" onclick="keyOp(\'' + esc(p.id) + '\', \'' + esc(k.id) + '\', \'reset\')" title="' + t("充值后手动恢复（清掉冷却）") + '">' + t("恢复") + "</button>" : "") +
      '<label class="tog-mini" title="' + (k.enabled ? t("停用该密钥（编排自动跳过）") : t("启用")) + '">' +
        '<input type="checkbox" ' + (k.enabled ? "checked" : "") +
        ' onchange="keyOp(\'' + esc(p.id) + '\', \'' + esc(k.id) + '\', this.checked ? \'enable\' : \'disable\')"><i></i></label>' +
      '<button class="danger small row-op" onclick="keyOp(\'' + esc(p.id) + '\', \'' + esc(k.id) + '\', \'delete\')">' + t("删除") + "</button>" +
      "</span></div>";
  }).join("");
  return '<div class="keys-box"><div class="keys-head"><b>' + t("密钥（按顺序调用，欠费自动切备用）") + "</b>" +
    (single ? '<span class="hint">' + t("点「添加密钥」配备用号") + "</span>" : "") +
    '<button class="ghost small" onclick="promptAddKey(\'' + esc(p.id) + '\')">＋ ' + t("添加密钥") + "</button></div>" +
    (rows || '<div class="hint" style="padding:4px 8px">' + t("尚未配置密钥") + "</div>") +
    "</div>";
}

function bindKeyRowDnD(pid, root) {
  const box = root.querySelector(".keys-box");
  if (!box) return;
  box.querySelectorAll(".krow").forEach((row) => {
    row.addEventListener("dragstart", () => { S.draggingKey = row.dataset.kid; row.classList.add("dragging"); });
    row.addEventListener("dragend", () => {
      S.draggingKey = null;
      box.querySelectorAll(".krow").forEach((r) => r.classList.remove("dragging", "drag-over"));
    });
    row.addEventListener("dragover", (e) => {
      if (!S.draggingKey || S.draggingKey === row.dataset.kid) return;
      e.preventDefault();
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!S.draggingKey || S.draggingKey === row.dataset.kid) return;
      const p = (S.providers || []).find((x) => x.id === pid);
      const order = (p.keys || []).map((k) => k.id);
      const i = order.indexOf(S.draggingKey), j = order.indexOf(row.dataset.kid);
      if (i < 0 || j < 0) return;
      order.splice(j, 0, order.splice(i, 1)[0]);
      keyReorder(pid, order);
    });
  });
}

async function keyReorder(pid, ids) {
  try {
    await api("/api/models/key-op", { method: "POST",
      body: JSON.stringify({ provider_id: pid, op: "reorder", ids }) });
  } catch (e) { toast(t("排序失败：") + e.message, true); }
  poll();
}

/* 「＋ 添加密钥」：两问（密钥、备注名）走应用内弹框，不用原生 prompt */
async function promptAddKey(pid) {
  const key = await uiPrompt(t("新密钥（sk-...）"), "");
  if (key === null) return;
  if (!key.trim()) { toast(t("已取消：密钥为空"), true); return; }
  const label = await uiPrompt(t("备注名（可空，如：备用号）"), "");
  if (label === null) label = "";
  await keyOpCall(pid, "add", "", key.trim(), (label || "").trim());
  toast(t("已添加密钥"));
}

async function keyOpCall(pid, op, keyId, key, label) {
  try {
    await api("/api/models/key-op", { method: "POST",
      body: JSON.stringify({ provider_id: pid, op, key_id: keyId, key, label }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
  // 操作期间轮询照跑（uiPrompt 弹框不阻塞轮询），S.modelsSig 还是旧值：必须
  // 清签名强制重绘，否则 KEY 写盘成功界面也看不到。
  S.modelsSig = null;
  await poll();
}

async function keyOp(pid, keyId, op) {
  if (op === "delete" && !await uiConfirm(t("删除该密钥？（不影响其它密钥）"),
      { danger: true, ok: t("删除") })) return;
  await keyOpCall(pid, op, keyId);
}

async function duplicateProvider(pid) {
  try {
    const r = await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [pid], op: "duplicate" }) });
    if (r.ok) toast(t("已复制供应商（启用状态，绑定不随带）"));
  } catch (e) { toast(t("复制失败：") + e.message, true); }
  poll();
}

/* 多 wire 网关的「另一条协议面」徽章：适配测试实测通过、又不是当前分组的 wire。
 * 同一份模型列表在两条 wire 上都能注入（解析走适配端点），列表不重复摆两份，
 * 但得把另一面亮出来——否则页面看上去只有一种协议能用，OpenAI 面永远被忽略。
 * 形态必须标出：chat 形态除 codex 外都能用（codex 0.154+ 只讲 responses），
 * 不标的话模型接入页一片 ✓、codex 链上却 ⚠ 协议不匹配，两个页面自相矛盾。 */
function wireCapTags(p, excludeProto) {
  return Object.keys((p || {}).wire_caps || {}).filter((pr) => pr !== excludeProto).map((pr) => {
    const cap = (p.wire_caps || {})[pr] || {};
    const chat = (cap.wire_api || "responses") === "chat";
    const tip = (chat
      ? t("适配测试实测：该面为 chat 形态——除 codex 外的 openai 系 CLI 可用；codex 只讲 responses，链上注入会被跳过")
      : t("适配测试实测：同密钥也可走 {0} wire——这组模型同样可注入 {0} 系 CLI（codex/dsh 等），运行时自动走适配端点", pr)) +
      (cap.checked_at ? " · " + cap.checked_at : "");
    return '<span class="tag ok" title="' + esc(tip) + '">' +
      esc(pr) + " ✓" + (chat ? " · chat" : "") + "</span>";
  }).join("");
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
      ' onchange="toggleGroupSel(\'' + esc(p.id) + '\', \'' + esc(g) + '\', this.checked)">' + t("全选") + '</label>' +
      esc(g) + t(" 协议 · ") + gm.length + t(" 个") +
      wireCapTags(p, g) +
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
  const imgcap = '<span class="tag imgcap' + (m.image_in ? " ok" : "") + '" title="' +
    (m.image_in ? t("支持图片输入，点击关闭")
                : t("纯文本模型，点击开启图片输入（内置智能体传图以此为准）")) + '"' +
    ' onclick="toggleModelImage(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("图") + "</span>";
  const sel = modelSel(pid);
  return '<div class="prow' + (m.enabled ? "" : " off") + '" draggable="true" data-group="' +
    esc(group) + '" data-name="' + esc(m.name) + '">' +
    '<input type="checkbox" class="pcheck"' + (sel[m.name] ? " checked" : "") +
    ' title="' + t("勾选以批量操作") + '"' +
    ' onchange="toggleModelSel(\'' + esc(pid) + '\', \'' + esc(m.name) + '\', this.checked)">' +
    '<span class="drag" title="' + t("拖动调整优先级") + '"><svg class="ico" aria-hidden="true"><use href="#i-grip"></use></svg></span>' +
    '<span class="pprio">#' + m.priority + "</span>" +
    '<span class="pname" title="' + esc(m.name) + '">' + esc(m.name) + "</span>" +
    price + imgcap + tmHtml +
    '<span class="row-ops">' +
    '<button class="ghost small row-op" onclick="testModelBtn(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("测试") + '</button>' +
    '<button class="danger small row-op" title="' + t("从列表删除：刷新/重新导入不会再带回，可在分组底部恢复") + '"' +
    ' onclick="delModel(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("删除") + '</button>' +
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

/* 适配测试：实测该网关同一密钥是否也支持另一条 wire 协议（结果存 wire_caps，
 * 绑定链/默认绑定的解析随之放宽）。「获取模型列表」成功后后端也会自动测一次。 */
async function probeWire(pid) {
  S.probeState = S.probeState || {};
  S.probeState[pid] = { busy: true };
  renderProvDetail();
  try {
    const res = await api("/api/models/probe-wire",
      { method: "POST", body: JSON.stringify({ id: pid }) });
    const names = Object.keys(res.wire_caps || {});
    S.probeState[pid] = names.length
      ? { ok: true, message: t("已适配：") + names.map((n) => n + " wire").join(t("、")) }
      : { ok: false, message: t("未发现可适配的 wire") + (res.note ? t("：") + res.note : "") };
  } catch (e) { S.probeState[pid] = { ok: false, message: e.message }; }
  S.modelsSig = null;
  renderModels();
  poll();  // wire_caps 已落盘：拉最新供应商状态，绑定页徽标同步
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

function toggleModelImage(pid, name) {
  const p = (S.providers || []).find((x) => x.id === pid);
  const m = p && (p.models || []).find((x) => x.name === name);
  api("/api/models/model-caps", { method: "POST",
    body: JSON.stringify({ provider_id: pid, name, image_in: !(m && m.image_in) }) })
    .then(() => { S.modelsSig = null; poll(); })
    .catch((e) => toast(t("保存失败：") + e.message, true));
}

/* 反查模型是否声明图片输入（绑定页 chip/列表只读徽标用） */
function modelHasImage(pid, name) {
  const p = (S.providers || []).find((x) => x.id === pid);
  return !!(p && (p.models || []).some((x) => x.name === name && x.image_in));
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
    disable: t("停用所选 ") + names.length + t(" 个模型？\n停用只影响编排选模，不删除配置。"),
    delete: t("删除所选 ") + names.length + t(" 个模型？\n刷新 / 重新导入都不会再带回，可在「已删除」里恢复。"),
    restore: t("恢复所选 ") + names.length + t(" 个模型？\n它们会重新启用并自动置顶。"),
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
  if (!await uiConfirm(t("删除模型「") + name + t("」？\n刷新 / 重新导入模型列表都不会再带回，可在分组底部「恢复全部」找回。"), { ok: t("删除"), danger: true })) return;
  modelOp(pid, name, "delete");
}

async function toggleProviderEnabled(pid, enabled) {
  const p = (S.providers || []).find((x) => x.id === pid) || {};
  const off = p.enabled === false;
  if (!off && !await uiConfirm(t("停用供应商「") + (p.name || pid) +
      t("」？\n停用后它的绑定会回落为 CLI 默认；配置与模型列表保留，可随时再启用。"), { ok: t("停用") })) return;
  try {
    await api("/api/models/provider-op", { method: "POST",
      body: JSON.stringify({ ids: [pid], op: off ? "enable" : "disable" }) });
  } catch (e) { toast(t("操作失败：") + e.message, true); }
  S.modelsSig = null;
  poll();
}

async function restoreHidden(pid) {
  if (!await uiConfirm(t("恢复该供应商下全部已删除的模型？\n它们会回到优先级末尾。"), { ok: t("恢复") })) return;
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
  setTimeout(poll, 2500);
}

/* 手工添加模型：有的厂商列表接口调不通（或只返回部分），直接填模型名也能进列表。
 * 后端打 manual 标记，之后刷新即使厂商列表里没有它也不丢。 */
function pmAddHtml(p) {
  return '<div class="pm-add">' +
    '<input id="pm-add-' + esc(p.id) + '" type="text" autocomplete="off" spellcheck="false"' +
    ' placeholder="' + t("手工添加模型名（列表接口调不通时用），回车添加") + '"' +
    ' onkeydown="pmAddKey(event, \'' + esc(p.id) + '\')">' +
    '<button class="ghost small" title="' + esc(t("厂商列表接口调不通时，直接把模型名加进列表")) + '"' +
    ' onclick="addModelManual(\'' + esc(p.id) + '\')">' +
    '<svg class="ico" aria-hidden="true"><use href="#i-plus"/></svg>' + t("添加") + '</button></div>';
}

function pmAddKey(ev, pid) {
  if (ev.key === "Enter") { ev.preventDefault(); addModelManual(pid); }
}

async function addModelManual(pid) {
  const inp = $("pm-add-" + pid);
  const name = ((inp && inp.value) || "").trim();
  if (!name) { toast(t("先填模型名"), true); return; }
  try {
    const r = await api("/api/models/add", { method: "POST", body: JSON.stringify({ id: pid, name }) });
    if (!r.ok) { toast(t("添加失败：") + (r.message || ""), true); return; }
    toast(t("已添加模型 ") + name);
    S.modelsSig = null;   // 新模型落盘后签名必变，这里主动置空确保立刻重绘
    poll();
  } catch (e) { toast(t("添加失败：") + e.message, true); }
}

async function refreshProviderModels(id) {
  toast(t("正在获取模型列表…"));
  try {
    const r = await api("/api/models/refresh", { method: "POST", body: JSON.stringify({ id }) });
    if (!r.ok && r.message) toast(t("获取失败：") + r.message, true);
    else if (r.note) toast(r.note);   // 网关无模型列表接口（404）：良性提示，不算失败
    else toast(t("已获取模型列表 · wire 适配测试后台进行中"));
  } catch (e) { toast(t("获取失败：") + e.message, true); }
  poll();
}

/* 供应商编辑卡：协议可选「自动」；显式选定时它是主协议（用于需要唯一协议的
 * 场景，如 opencode 配置生成），auto 则完全按实测能力集（wire_caps）走。 */
function providerCard(p) {
  const fld = (id, label, val, ph, full) =>
    '<div class="field' + (full ? " full" : "") + '"><label>' + label + '</label>' +
    '<input id="' + id + '" value="' + esc(val) + '" placeholder="' + esc(ph) + '"></div>';
  const cur = p.protocol || "auto";
  const protoSel =
    '<div class="field"><label>' + t("协议") + '</label><select id="pproto-' + esc(p.id) + '">' +
    [["auto", t("自动（按实测能力集）")],
     ["anthropic", "anthropic"],
     ["openai", "openai"],
     ["google", t("google（仅登记）")]].map(([v, label]) =>
      '<option value="' + v + '"' + (cur === v ? " selected" : "") + ">" + esc(label) + "</option>").join("") +
    "</select></div>";
  return '<div class="card" id="pcard-' + esc(p.id) + '">' +
    '<div class="head"><span class="name">' + esc(p.name) + '</span><span class="tag">' + esc(cur) + "</span>" +
    srcTag(p.source) + "</div>" +
    '<div class="fields grid">' +
    fld("pname-" + p.id, t("名称"), p.name, t("名称")) +
    protoSel +
    fld("pmodel-" + p.id, t("默认模型"), p.model || "", t("默认模型")) +
    fld("purl-" + p.id, t("API 地址"), p.base_url, "base_url", true) +
    fld("pkey-" + p.id, t("首选密钥（填了=替换 #1；备用号在下方密钥区添加）"), "", t("（") + (p.api_key || t("未设置")) + t("，留空=不改）"), true) +
    fld("peasy-" + p.id, t("简单任务模型"), p.model_easy || "", t("难度路由 · 简单")) +
    fld("phard-" + p.id, t("困难任务模型"), p.model_hard || "", t("难度路由 · 困难")) +
    "</div>" +
    (cur === "auto"
      ? '<p class="hint">' + t("协议=自动：该网关支持哪些 wire 由「适配测试」实测决定（上方能力标签）。") + '</p>' : "") +
    '<div class="ops"><button class="ghost small" onclick="saveProvider(\'' + esc(p.id) + '\')">' + t("保存") + '</button>' +
    '<button class="danger small" onclick="delProvider(\'' + esc(p.id) + '\')">' + t("删除") + '</button></div></div>';
}

async function saveProvider(id) {
  const body = {
    id, name: $("pname-" + id).value.trim(),
    protocol: ($("pproto-" + id) || {}).value || "auto",
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

/* 「＋」手动添加供应商：弹框表单（替代原来的多段 prompt）。
 * 协议默认「自动」——聚合网关一个密钥常同时开多条 wire，保存后由后台探测
 * 分类（wire_caps），不必先问用户。 */
function openAddProviderDialog() {
  const protoOpts =
    '<option value="auto">' + t("自动探测（推荐 · 聚合网关多协议都开）") + '</option>' +
    '<option value="anthropic">' + t("anthropic（Claude 系）") + '</option>' +
    '<option value="openai">' + t("openai（Codex / 通用）") + '</option>' +
    '<option value="google">' + t("google（Gemini，仅登记不支持注入）") + '</option>';
  openModal(t("添加供应商"),
    '<div class="form">' +
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("名称 *") + '</label><input id="np-name" placeholder="' + t("例：公司网关") + '"></div>' +
    '<div class="field"><label>' + t("协议") + '</label><select id="np-proto">' + protoOpts + "</select></div>" +
    "</div>" +
    '<div class="field"><label>' + t("API 地址 *") + '</label>' +
    '<input id="np-url" placeholder="' + t("https://host/v1（若填 /chat/completions 会自动收敛为基址）") + '"></div>' +
    '<div class="field"><label>' + t("API 密钥") + '</label>' +
    '<input id="np-key" placeholder="' + t("sk-...（可留空，稍后补填）") + '"></div>' +
    '<div class="grid-3">' +
    '<div class="field"><label>' + t("默认模型") + '</label><input id="np-model" placeholder="' + t("可留空") + '"></div>' +
    '<div class="field"><label>' + t("简单任务模型") + '</label><input id="np-easy" placeholder="' + t("可留空") + '"></div>' +
    '<div class="field"><label>' + t("困难任务模型") + '</label><input id="np-hard" placeholder="' + t("可留空") + '"></div>' +
    "</div>" +
    '<p class="hint">' + t("保存后会自动拉取模型列表并探测该网关支持的 wire 协议（未填密钥时跳过）；探测结果决定哪些 CLI 能用它。") + '</p>' +
    '<div id="add-result" class="msg"></div>' +
    "</div>",
    '<button class="ghost" onclick="closeModal()">' + t("取消") + '</button>' +
    '<span class="spacer"></span>' +
    '<button class="primary" id="btn-do-add" onclick="doAddProvider()">' + t("保存") + '</button>');
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
  openModal(t("导入供应商"), '<div class="hint">' + t("正在扫描本机 AI 工具配置…") + '</div>',
    '<button class="ghost" onclick="closeModal()">' + t("取消") + '</button>');
  let data;
  try {
    data = await api("/api/models/sources", { busy: true });
  } catch (e) {
    $("modal-body").innerHTML = '<div class="msg bad">' + t("扫描失败：") + esc(e.message) + "</div>";
    return;
  }
  const srcs = data.sources || [];
  const usable = srcs.filter((s) => s.found && s.count > 0);
  const rows = srcs.map((s) => {
    const ok = s.found && s.count > 0;
    let status;
    if (!s.found) status = '<span class="badge">' + t("未找到") + '</span>';
    else if (s.error) status = '<span class="badge bad">' + t("解析失败") + '</span>';
    else if (s.count > 0) status = '<span class="badge ok">' + t("发现 ") + s.count + t(" 个") + "</span>";
    else status = '<span class="badge">' + t("无可用配置") + '</span>';
    return '<label class="src-row' + (ok ? "" : " disabled") + '">' +
      '<input type="checkbox" value="' + esc(s.id) + '"' + (ok ? " checked" : " disabled") +
      (ok ? ' onchange="updateImportSelHint()"' : "") + ">" +
      '<div class="src-main">' +
      '<div class="src-name">' + esc(t(s.name)) + status + "</div>" +
      '<div class="src-desc">' + esc(t(s.desc)) + "</div>" +
      '<div class="src-path">' + esc((s.paths || []).join("  ·  ")) + "</div>" +
      (s.note ? '<div class="src-note">' + esc(t.apply(null, [s.note].concat(s.note_args || []))) + "</div>" : "") +
      (s.error ? '<div class="src-note bad">' + esc(s.error) + "</div>" : "") +
      "</div></label>";
  }).join("");
  $("modal-body").innerHTML =
    '<p class="hint">' + t("勾选要导入的来源。导入只读取这些工具的配置，不会改动它们本身；") +
    t("已导入过的供应商会原地更新（保留你设置的模型与启停状态）。") + "</p>" +
    '<div class="src-list">' + (rows || '<div class="hint">' + t("没有可扫描的来源。") + '</div>') + "</div>" +
    '<div id="import-result" class="import-result"></div>';
  $("modal-foot").innerHTML =
    '<span class="hint" id="import-sel-hint"></span>' +
    '<span class="spacer"></span>' +
    '<button class="ghost" onclick="toggleAllSources(true)">' + t("全选") + '</button>' +
    '<button class="ghost" onclick="toggleAllSources(false)">' + t("全不选") + '</button>' +
    '<button class="ghost" onclick="closeModal()">' + t("取消") + '</button>' +
    '<button class="primary" id="btn-do-import" onclick="doImport()"' +
    (usable.length ? "" : " disabled") + ">" + t("导入选中") + "</button>";
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
      if (!s.found) return "<li>" + esc(s.name) + t("：未找到配置") + "</li>";
      if (s.error) return "<li>" + esc(s.name) + t("：") + '<span class="bad">' + esc(s.error) + "</span></li>";
      let txt = t("新增 ") + s.added + t("，更新 ") + s.updated;
      if (s.duplicate) txt += t("，跳过重复 ") + s.duplicate;
      const extra = s.note ? t("（") + esc(t.apply(null, [s.note].concat(s.note_args || []))) + t("）") : "";
      return "<li>" + esc(s.name) + t("：") + txt + extra + "</li>";
    }).join("");
    $("import-result").innerHTML =
      '<div class="import-done"><b>' + esc(r.message) + "</b><ul>" + lines + "</ul></div>";
    btn.textContent = t("完成");
    btn.disabled = false;
    btn.onclick = closeModal;
    if (r.imported) { S.selProv = null; S.modelsSig = null; poll(); }
  } catch (e) {
    $("import-result").innerHTML = '<div class="msg bad">' + t("导入失败：") + esc(e.message) + "</div>";
    btn.disabled = false; btn.textContent = t("导入选中");
    boxes.forEach((b) => { b.disabled = false; });
    updateImportSelHint();
  }
}

async function saveBinding(id) {
  const st = bindSelById(id);
  const provSel = $("bindprov-" + id);
  // 主供应商跟链首走：模型链是唯一真源，链首非空时供应商下拉必须与之一致
  // （下拉是旧状态时把旧值发上去，后端「显式指定」就会盖回停用的旧供应商）。
  // 空链时保留下拉选择：这是“只锁定供应商，模型按该供应商默认/难度选”的
  // 合法显式覆盖，不能重置成自动模式。
  const headP = st.chain.length ? st.chain[0].p : (provSel ? provSel.value : "");
  if (st.chain.length && provSel && provSel.value !== (headP || "")) {
    provSel.value = headP || "";
  }
  try {
    await api("/api/models/binding", { method: "POST", body: JSON.stringify({
      agent_id: id, provider_id: provSel ? provSel.value : "",
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
  rsel.innerHTML = '<option value="">' + t("不沿用（全新开始）") + '</option>' +
    rAgents.map((e) => '<option value="' + esc(e.id) + '">' + esc(e.name) + t(" 的会话") + "</option>").join("");
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

/* 需求拷问采访卡（借鉴 grill-me 一次一问的折中：一卡多问、芯片点选）：
 * 点选项把「问题+所选」追加进目标框，凑齐后用户补一句即可发送；「跳过」直接创建 */
function renderClarify(questions, goal, resetSubmit) {
  const box = $("clarify-box");
  if (!box) { resetSubmit(); return; }
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="cl-head"><svg class="ico" aria-hidden="true"><use href="#i-chat"></use></svg>' +
    '<b>' + esc(t("先对齐几个点，再做更准")) + "</b>" +
    '<button type="button" class="ghost small" id="clarify-skip">' + esc(t("跳过，直接做")) + "</button></div>" +
    questions.map((it, i) =>
      '<div class="cl-q"><div class="cl-qtext">' + (i + 1) + ". " + esc(it.q) + "</div>" +
      '<div class="cl-opts">' + (it.options || []).map((o) =>
        '<button type="button" class="cl-opt" data-q="' + esc(it.q) + '" data-o="' + esc(o) + '">' +
        esc(o) + "</button>").join("") + "</div></div>").join("");
  box.querySelectorAll(".cl-opt").forEach((b) => b.addEventListener("click", () => {
    const ta = $("f-goal");
    if (!ta) return;
    const line = it0safe(b.dataset.q, b.dataset.o);
    ta.value = ta.value.trim() + (ta.value.includes(line) ? "" : (ta.value ? "\n" : "") + line);
    b.classList.add("picked");
    b.disabled = true;
    // spec 分段签核（借鉴 superpowers「分段短块逐段消化」）：全部问题选完后
    // 显示已确认摘要条 + 「确认开跑」——用户看一眼再动手，不蒙头就创建
    const allPicked = questions.every((it) =>
      Array.from(box.querySelectorAll(".cl-opt")).some(
        (x) => x.dataset.q === it.q && x.classList.contains("picked")));
    let bar = box.querySelector("#clarify-confirm");
    if (allPicked && !bar) {
      bar = document.createElement("div");
      bar.id = "clarify-confirm";
      bar.className = "cl-confirm";
      bar.innerHTML = '<button type="button" class="primary small" id="clarify-go">' +
        esc(t("已确认，开跑")) + "</button></div>";
      box.appendChild(bar);
      bar.querySelector("#clarify-go").addEventListener("click", () => {
        box.classList.add("hidden");
        box.innerHTML = "";
        S.clarifyDone = true;
        $("btn-create").click();
      });
    }
  }));
  function it0safe(q, o) { return q + "：" + o; }
  const skip = $("clarify-skip");
  if (skip) skip.addEventListener("click", () => {
    box.classList.add("hidden");
    box.innerHTML = "";
    S.clarifyDone = true;              // 跳过 = 本轮不再采访
    $("btn-create").click();
  });
  $("f-goal").focus();
}

async function createTask() {
  if (S.creatingTask) return;
  S.creatingTask = true;
  const submit = $("btn-create");
  if (submit) {
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
  }
  const resetSubmit = () => {
    S.creatingTask = false;
    if (submit) {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
    }
  };
  const msg = $("create-msg");
  msg.className = "msg"; msg.textContent = t("提交中…");
  const payload = {
    type: $("f-type").value,
    mode: $("f-mode").value,
    title: $("f-title").value.trim(),
    goal: $("f-goal").value.trim(),
    context: $("f-context").value.trim(),
    workdir: $("f-workdir").value.trim(),
    thinking: $("f-thinking").value,
  };
  if (!payload.goal) {
    msg.className = "msg err";
    msg.textContent = t("请先填写目标");
    $("f-goal").focus();
    resetSubmit();
    return;
  }
  if (payload.type === "direct" && !S.typeSuggestionDone) {
    const suggested = recommendTaskType(payload.goal);
    if (suggested) {
      const flow = flowById(suggested) || {};
      resetSubmit();
      const yes = await uiConfirm(
        t("这个需求更适合「{0}」流程，能使用对应的规划与质量门禁。是否切换后执行？")
          .replace("{0}", t(flow.name || suggested)),
        { title: t("推荐任务类型"), ok: t("切换类型") });
      S.typeSuggestionDone = true;
      if (yes) pickType(suggested);
      $("btn-create").click();
      return;
    }
  }
  S.typeSuggestionDone = false;
  // 需求拷问闸（借鉴 grill-me）：goal 很短且没写背景时先问 1-3 个澄清问题；
  // 采访失败/无问题照常创建——是增强不是闸门
  if (payload.goal.length < 12 && !payload.context && !S.clarifyDone) {
    msg.textContent = t("目标有点简短，先问几个问题…");
    try {
      const cq = await api("/api/tasks/clarify", {
        // 澄清只是增强，不能让建任务被模型调用拖住；超时后直接按原目标开跑。
        method: "POST", timeout: 8000, operation: false,
        body: JSON.stringify({ goal: payload.goal, type: payload.type }) });
      const qs = (cq && cq.questions) || [];
      if (qs.length) {
        S.clarifyDone = true;            // 本轮已采访；再点发送直接创建
        renderClarify(qs, payload.goal, resetSubmit);
        return;
      }
    } catch (e) {
      // 澄清服务不可用或超时不阻塞主流程，明确告诉用户已经自动继续。
      msg.textContent = t("澄清响应较慢，已直接创建任务…");
    }
  }
  S.clarifyDone = false;
  if (!payload.workdir) delete payload.workdir;  // 留空 → 服务端用「默认保存路径」（编排设置可改）
  else unhideSideDir(payload.workdir);           // 在已移除的目录新建任务 → 自动恢复显示
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
  const flow = flowById(payload.type) || {};
  const _eng = flow.engine;
  if (_eng === "code") {
    payload.verify_command = $("f-verify").value.trim();
  } else if (_eng === "direct") {
    // 直连：目标+附件即全部输入，不带验证/评审参数；direct_agent 为空时走
    // 内置智能体/模型绑定（后端按空串处理）
    payload.direct_provider_id = $("f-direct-provider").value;
    payload.direct_model = $("f-direct-model-name").value;
    payload.direct_agent = cmpDirectAgentVal();
  } else {
    payload.manuscript = $("f-manuscript").value.trim() || "manuscript.md";
    payload.rounds = parseInt($("f-rounds").value, 10) || 2;
    payload.threshold = parseFloat($("f-threshold").value) || 7.0;
    payload.best_of = Math.max(1, Math.min(3, parseInt($("f-bestof").value, 10) || 1));
    const rubric = $("f-rubric").value.trim();
    if (rubric) payload.rubric = rubric.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
    const ch = parseInt($("f-chapters").value, 10);
    if (ch >= 2) {
      payload.serial = {
        chapters: ch,
        words_per_chapter: parseInt($("f-words-per-ch").value, 10) || 2500,
        variants: Math.max(1, Math.min(3, parseInt($("f-variants").value, 10) || 1)),
      };
      // 分卷：显式卷结构文本优先（后端解析成卷表），否则用每卷章数。
      // 两个都留空 = 不分卷（后端收到 0/缺字段即不分卷）。
      const volSpec = ($("f-vol-spec") || {}).value ? $("f-vol-spec").value.trim() : "";
      const volPer = parseInt(($("f-vol-chapters") || {}).value, 10);
      if (volSpec) payload.serial.volumes_text = volSpec;
      else if (volPer >= 2) payload.serial.volume_chapters = volPer;
    } else if (flow.serial) {
      // 显式 null 覆盖 serial_novel 的流程默认，空章节就是单稿件。
      payload.serial = null;
    }
    const initialBible = ($("f-bible") || {}).value ? $("f-bible").value.trim() : "";
    if (initialBible && flow.serial && payload.serial) payload.story_bible = initialBible;
    const critics = Array.from($("f-critics").querySelectorAll("input:checked")).map((i) => i.value);
    if (critics.length) payload.critics = critics;
  }
  try {
    const r = await api("/api/tasks", { method: "POST", timeout: 30000, operation: "创建任务",
      body: JSON.stringify(payload) });
    msg.textContent = t("已创建，跳转运行页…");
    // 先把新任务刷进 state 再跳：chatEngineIsDirect 靠 S.state.tasks 判引擎，
    // 不刷的话对话页签不会就绪，自动选卡落不到「对话」
    try { await refreshState(); } catch (e) { /* 刷失败等轮询兜底 */ }
    jumpToRun(r.run_id);
    $("f-goal").value = "";
    if ($("f-bible")) $("f-bible").value = "";
    S.atts = []; renderAttachChips();   // 附件已移交任务待提交区，清空本地列表
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  } finally {
    resetSubmit();
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
  box.innerHTML = (S.atts || []).map((a) => {
    const name = String(a.name || "attachment");
    const id = String(a.id || "");
    const image = /^image\//i.test(String(a.mime || "")) ||
      /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name);
    const remove = '<button type="button" class="ax" data-att-remove="' + esc(id) +
      '" title="' + esc(t("移除")) + '" aria-label="' + esc(t("移除")) + '">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-x"></use></svg></button>';
    if (image && id) {
      const src = "/api/attachments/" + encodeURIComponent(id) + qsAuth();
      return '<span class="att-chip att-image" title="' + esc(name) + '">' +
        '<img src="' + esc(src) + '" alt="' + esc(name) + '" loading="lazy" draggable="false">' +
        '<span class="att-image-name">' + esc(name) + "</span>" + remove + "</span>";
    }
    return '<span class="att-chip" title="' + esc(name) + '">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-paperclip"></use></svg>' +
      "<span>" + esc(name) + "</span>" +
      " <i>" + fmtSize(a.size) + "</i>" + remove + "</span>";
  }).join("");
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
 * 从所选版本检出任务分支 codebee/<task-id>（脏工作区会显式失败，不静默降级）。 */
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
  // 首选项=跟随当前分支：胶囊上直接显示分支名（参考工作区选择器的「main ▾」），
  // 悬停胶囊看完整说明（renderGitHint 写 row.title）；换分支在「分支」组里选
  groups.push(opt("", info.branch || "HEAD"));
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
    ? t("；运行时将从「%1」检出任务分支 codebee/&lt;任务ID&gt;").replace("%1", rev)
    : "";
  const text = base + dirty + act;
  $("git-hint").innerHTML = esc(text);
  // 极简：提示行不再常驻，分支胶囊的悬停 title 承载同一份详情（剥掉 innerHTML 用的实体）
  const row = $("row-git");
  if (row) row.title = text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}
window.probeGit = probeGit;

/* ---- 工作目录「最近文件夹」下拉：默认选中一个目录，▾ 展开最近 5 个 + 浏览/用默认 ---- */
function wdRecents() {
  const st = S.state || {};
  const seen = new Set(); const out = [];
  for (const tk of (st.tasks || []).concat(st.archived_tasks || [])) {
    const wd = (tk.workdir || "").trim();
    if (!wd || seen.has(wd)) continue;
    seen.add(wd); out.push(wd);
    if (out.length >= 5) break;
  }
  return out;
}
window.toggleWdMenu = function (ev) {
  if (ev) ev.stopPropagation();
  const menu = $("wd-menu");
  if (!menu) return;
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  const cur = (($("f-workdir") || {}).value || "").trim();
  const eff = ((S.settings || {}).default_workdir_effective || "").trim();
  const base = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;
  const row = (p, label, on) =>
    '<button type="button" class="wd-item' + (on ? " on" : "") + '" data-wd="' + esc(p) +
    '" title="' + esc(p) + '">' +
    '<svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>' +
    "<span>" + esc(label) + "</span>" +
    (on ? '<svg class="ico wd-check" aria-hidden="true"><use href="#i-check"></use></svg>' : "") +
    "</button>";
  let html = wdRecents().map((p) => row(p, base(p), p === cur)).join("");
  html += '<div class="wd-sep"></div>' +
    '<button type="button" class="wd-item" data-wd-act="browse">' +
    '<svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>' +
    "<span>" + esc(t("浏览本机目录…")) + "</span></button>" +
    '<button type="button" class="wd-item" data-wd-act="default"' + (eff ? "" : " disabled") + ">" +
    '<svg class="ico" aria-hidden="true"><use href="#i-refresh"></use></svg>' +
    "<span>" + esc(t("用默认路径")) + "</span></button>";
  menu.innerHTML = html;
  menu.classList.remove("hidden");
};
window.wdMenuPick = function (item) {
  const inp = $("f-workdir"), menu = $("wd-menu");
  if (!item || !inp || item.disabled) return;
  if (item.dataset.wdAct === "browse") {
    if (menu) menu.classList.add("hidden");
    window.pickFolder("f-workdir");
    return;
  }
  inp.value = item.dataset.wdAct === "default"
    ? ((S.settings || {}).default_workdir_effective || "")
    : (item.dataset.wd || "");
  inp.dispatchEvent(new Event("input", { bubbles: true }));   // queueGitProbe 跟上换目录
  inp.dispatchEvent(new Event("change", { bubbles: true }));
  if (menu) menu.classList.add("hidden");
  inp.focus();
};

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
  sel.innerHTML = '<option>' + t("（加载中…）") + '</option>';
  try {
    const r = await api("/api/sessions");
    const list = (r.sessions || {})[agent] || [];
    S.sessionList = list;   // 提交时带上 project（续会话要在该目录下启动 CLI）
    sel.innerHTML = list.length ? list.map((s) => {
      const who = s.project ? " [" + String(s.project).replace(/[\\/]+$/, "").split(/[\\/]/).pop() + "]" : "";
      return '<option value="' + esc(s.session_id) + '">[' + esc(s.mtime) + "]" + who + " " + esc(s.preview.slice(0, 60)) + "</option>";
    }).join("") : '<option value="">' + t("（未找到该智能体的本地会话）") + '</option>';
  } catch (e) {
    S.sessionList = [];
    sel.innerHTML = '<option value="">' + t("（扫描失败）") + '</option>';
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
    let restored = null;
    if (!archived && archivedTaskIds().has(id)) {
      restored = await loadTaskForPrefill(id);
    }
    await api("/api/tasks/" + encodeURIComponent(id) + "/archive",
      { method: "POST", body: JSON.stringify({ archived: !!archived }) });
    // 取消归档时把任务工作目录从「已移除目录」里捞回来：用户点时钟开关找回
    // 归档任务再取消归档，直觉上文件夹就该回侧栏（否则只能靠在该目录新建任务）。
    if (!archived) {
      const t0 = restored || ((S.state || {}).tasks || []).concat(((S.state || {}).archived_tasks) || [])
        .find((x) => x.id === id);
      if (t0 && t0.workdir) unhideSideDir(t0.workdir);
    }
    // 乐观挪位：归档=任务从 tasks 挪进 archived_tasks，取消归档反向。同删除的
    // 乐观清场——SSE 半死时推送不到，行要在侧栏挂到 60s 看门狗兜底才走。
    // archivedIds 参与侧栏签名，挪位即重画；显式清掉不赌签名算法。
    const st = S.state;
    if (st) {
      const from = archived ? (st.tasks || []) : (st.archived_tasks || []);
      const i0 = from.findIndex((x) => x.id === id);
      if (i0 >= 0) {
        const t0 = from.splice(i0, 1)[0];
        const moved = restored || t0;
        moved.archived = !!archived;
        if (!archived && restored) _taskDetailCache.set(id, restored);
        const to = archived ? (st.archived_tasks = st.archived_tasks || []) : (st.tasks = st.tasks || []);
        to.push(moved);
      }
      S.sideSig = "";
    }
    render();
  } catch (e) { toast(t("操作失败：") + e.message, true); }
  poll();
}

async function deleteTask(id) {
  if (!await uiConfirm(t("删除该任务及其全部运行记录（含日志与报告）？不可恢复。"), { ok: t("删除"), danger: true })) return;
  let gone = false;
  try {
    toast(t("正在删除任务…"));
    await api("/api/tasks/" + encodeURIComponent(id) + "/delete",
      { method: "POST", operation: "删除任务" });
  } catch (e) {
    // 任务已不在（他端删过 / 重复点）：不报错晾着，照常把视图清掉
    if (!/任务不存在/.test(e.message)) { toast(t("删除失败：") + e.message, true); return; }
    gone = true;
  }
  // 详情有两种打开方式：run 级（detailRunId）与任务级（detailTaskKey，detailRunId 为空）。
  // 删的正是当前详情对应的任务时都要关，否则右侧还挂着已删任务、再点重试就撞「任务不存在」。
  if (S.detailRunId || S.detailTaskKey === id) closeRun();
  // 乐观清场：不等 SSE 推送/轮询。删除靠 bump_state 走 SSE，SSE 半死时推送
  // 不到，看门狗要 60s 才兜底拉全量——期间已删任务就挂在侧栏（2026-09-23
  // 用户实测「任务删了，左侧列表不刷新」）。后端已删，就地清本地状态并强制
  // 重画；下一次全量推送自然对齐。
  const st = S.state;
  if (st) {
    st.tasks = (st.tasks || []).filter((x) => x.id !== id);
    st.archived_tasks = (st.archived_tasks || []).filter((x) => x.id !== id);
    if (st.task_latest) delete st.task_latest[id];
    if (st.task_stats) delete st.task_stats[id];
    const goneRuns = new Set((st.runs || []).filter((r) => r.task_id === id).map((r) => r.id));
    if (goneRuns.size) st.runs = (st.runs || []).filter((r) => !goneRuns.has(r.id));
    S.sideSig = "";   // 任务条目少了签名理应变化；显式清掉，不赌签名算法
  }
  _taskDetailCache.delete(id);
  _taskRunsCache.delete(id);
  _taskRunsAt.delete(id);
  _taskRunsPaging.delete(id);
  render();
  poll();
  if (gone) toast(t("任务已删除"));
}

/* ---------------------------------------------------------- 右键菜单（归档/删除） */
const ORPHAN_DIR = "__orphan__";  // 无主运行（无 task_id / 任务已删）的兜底文件夹，与 renderSideTasks 共用
function archivedTaskIds() {
  return new Set((((S.state || {}).archived_tasks) || []).map((t) => t.id));
}

/* 文件夹「移除」隐藏表（localStorage）：移除的目录连同其下任务（含已归档灰显的）
 * 从侧栏隐藏——纯归档文件夹重复归档是 no-op，状态不变侧栏不重绘，必须显式隐藏；
 * 在该目录新建任务时自动恢复显示。 */
let _hiddenDirs = null;
function sideHiddenDirs() {
  if (!_hiddenDirs) {
    try { _hiddenDirs = new Set(JSON.parse(localStorage.getItem("tutti.hiddenDirs") || "[]")); }
    catch (e) { _hiddenDirs = new Set(); }
  }
  return _hiddenDirs;
}
function hideSideDir(dir) {
  const s = sideHiddenDirs();
  if (!s.has(dir)) { s.add(dir); localStorage.setItem("tutti.hiddenDirs", JSON.stringify([...s])); }
  S.sideSig = ""; renderSideTasks();
}
function unhideSideDir(dir) {
  const s = sideHiddenDirs();
  if (!dir || !s.delete(dir)) return;
  localStorage.setItem("tutti.hiddenDirs", JSON.stringify([...s]));
  S.sideSig = "";
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

/* 任务行的「⋯」与右键共用同一组动作，避免两个入口越改越不一样。
 * det 是 .stask 元素；菜单位置由调用方提供，缺省时贴在行右下角。 */
function taskContextItems(det) {
  const taskId = det && det.dataset ? (det.dataset.task || "") : "";
  const runId = det && det.dataset ? (det.dataset.run || "") : "";
  const items = [];
  if (runId) items.push({ label: t("打开详情"), fn: () => sideOpenRun(runId) });
  if (taskId) {
    items.push("-");
    items.push({ label: t("打开工作目录"), fn: () => revealPath("tasks", taskId, true) });
    items.push({ label: t("复制工作目录路径"), fn: () => revealPath("tasks", taskId, false) });
    if (runId) items.push({ label: t("复制日志目录路径"), fn: () => revealPath("runs", runId, false) });
    const st = det.dataset.status || "";
    const latestTaskRun = ((S.state || {}).task_latest || {})[taskId] || {};
    const retryable = runNeedsRetry(latestTaskRun) ||
      st === "failed" || st === "cancelled" || st === "timeout";
    if (retryable) items.push({ label: t("↻ 继续任务"), fn: () => retryTask(taskId) });
    items.push({ label: retryable ? t("✎ 编辑重试") : t("基于此任务新建"),
      fn: () => newFromTask(taskId) });
    const sTask = ((S.state || {}).tasks || []).find((x) => x.id === taskId);
    if (sTask && sTask.serial && st !== "running" && st !== "queued")
      items.push({ label: t("继续连载（新任务）"), fn: () => continueSerial(taskId) });
    // 连载完整跑完但未达标：给「重写未达标章」，与详情页按钮同源
    // （task_latest 全量下发最近一次 run，verdict 就在里面）
    const lrTask = ((S.state || {}).task_latest || {})[taskId];
    if (sTask && sTask.serial && lrTask && lrTask.status === "done"
      && lrTask.verdict && lrTask.verdict.publishable === false)
      items.push({ label: t("↻ 重写未达标章"), fn: () => retryTask(taskId) });
    items.push({ label: t("重命名任务"), fn: () => renameTask(taskId) });
    const archived = archivedTaskIds().has(taskId);
    items.push({ label: archived ? t("取消归档") : t("归档"), fn: () => archiveTask(taskId, !archived) });
    items.push({ label: t("删除任务"), danger: true, fn: () => deleteTask(taskId) });
  } else if (runId) {
    items.push("-");
    items.push({ label: t("复制日志目录路径"), fn: () => revealPath("runs", runId, false) });
    items.push({ label: t("删除记录"), danger: true, fn: () => deleteRun(runId) });
  }
  return items;
}

window.openTaskActions = function (det, ev) {
  if (!det) return;
  const r = det.getBoundingClientRect();
  const x = ev && Number.isFinite(ev.clientX) ? ev.clientX : r.right - 8;
  const y = ev && Number.isFinite(ev.clientY) ? ev.clientY : r.bottom;
  openCtxMenu(x, y, taskContextItems(det));
};

function bindCtxMenus() {
  // 侧栏任务树：右键任务 → 详情/目录/重试/重命名/归档/删除；管理类运行 → 详情/目录/删除记录
  $("side-tasks").addEventListener("contextmenu", (e) => {
    // .stask 在 .sdir 内部：先判任务行，点到才算文件夹自己的菜单
    const det = e.target.closest(".stask");
    if (det) {
    e.preventDefault();
    openCtxMenu(e.clientX, e.clientY, taskContextItems(det));
    return;
    }
    // 文件夹行（ZCode 桌面端式样）：新建任务到该目录 / 打开工作目录 / 复制路径 / 移除。
    // 「其他」是兜底展示（无主运行的聚合），不是真实目录，不弹菜单。
    const dirEl = e.target.closest(".sdir");
    if (!dirEl) return;
    e.preventDefault();
    const dir = dirEl.dataset.dir || "";
    if (!dir || dir === ORPHAN_DIR) return;
    // 含已归档任务：「显示已归档」开着时灰显回原文件夹的那批也算在内——
    // 不然纯归档文件夹右键移除会收集到空列表，点了没反应（重复归档是无害 no-op）
    const stAll = S.state || {};
    const dirTasks = (stAll.tasks || []).concat(stAll.archived_tasks || [])
      .filter((x) => (x.workdir || "") === dir);
    const ids = dirTasks.map((x) => x.id);
    const items = [
      { label: t("查看文件"), fn: () => openFolderFiles(dir) },
      { label: t("新建任务到该目录"), fn: () => newTaskInDir(dir) },
      "-",
      { label: t("打开工作目录"), fn: () => { if (ids[0]) revealPath("tasks", ids[0], true); } },
      { label: t("复制工作目录路径"), fn: () => { if (ids[0]) revealPath("tasks", ids[0], false); } },
      "-",
      { label: t("移除该文件夹"), danger: true, fn: () => removeSideDir(ids, dir) },
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

/* ------------------------------------------------- 文件夹选择（工作目录「选择…」/点输入框）
 * 首选系统原生对话框：服务端 tkinter 子进程弹真窗口（/api/pick_folder，仅本机），
 * 前端只收回填路径。机器没有 tkinter（fallback=true）时回落网页目录弹框：
 * /api/browse 浏览（服务端只给目录名、前端拼绝对路径），选定=行内「选这个」或
 * 底部「使用当前目录」。远端设备两条通道都被 403 拒：点输入框不再弹框，继续手输。 */
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
    '<span class="pk-path" title="' + esc(r.path === "此电脑" ? t("此电脑") : r.path) + '">' + esc(r.path === "此电脑" ? t("此电脑") : r.path) + "</span></div>" +
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

/* 网页目录弹框（原生对话框不可用时的回落通道） */
async function webPickFolder(targetId, cur) {
  openModal(t("选择文件夹"), '<div id="pk-body" class="hint">' + esc(t("正在读取目录…")) + "</div>",
    '<button class="ghost" onclick="closeModal();pkRefocus()">' + esc(t("取消")) + "</button>" +
    '<button class="primary" onclick="pickUseP(pickerCwd())">' + esc(t("使用当前目录")) + "</button>");
  await pickerBrowse(cur || "__drives__");
}

// 目标可以是输入框 id，也可以直接是元素（禅道档案卡等工作目录输入框无 id）
const pickTargetEl = (tgt) => (typeof tgt === "string" ? $(tgt) : tgt) || null;

window.pickFolder = async function (targetId, fromClick) {
  if (fromClick && pickerSt.local === false) return;   // 远端已确认被拒：点击不打扰
  pickerSt.target = targetId;
  const cur = ((pickTargetEl(targetId) || {}).value || "").trim();
  let r = null;
  try {
    r = await api("/api/pick_folder", { method: "POST",
      body: JSON.stringify({ initial: cur, title: t("选择文件夹") }) });
  } catch (e) {
    if (/仅限本机/.test(e.message || "")) {
      pickerSt.local = false;
      if (fromClick) toast(t("目录选择仅限本机使用，请手动输入路径"), true);
      return;
    }
    toast(t("操作失败：") + (e.message || e), true);
    return;
  }
  if (r.busy) { toast(t("已有一个选择窗口正在等待"), true); return; }
  if (r.fallback) { await webPickFolder(targetId, cur); return; }
  if (r.path) pickUseP(r.path);
  else window.pkRefocus();   // 用户取消：焦点还给输入框，方便手输
};
window.pickEnterP = (p) => pickerBrowse(p);
window.pickParentP = () => pickerBrowse(pickerSt.parent || "__drives__");
window.pickDrivesP = () => pickerBrowse("__drives__");
window.pickerCwd = () => pickerSt.cwd;
// 取消/选定后把焦点还给输入框：点框弹出选择是常态，想手输的人取消后能直接打字
window.pkRefocus = () => { const el = pickTargetEl(pickerSt.target); if (el) el.focus(); };
window.pickUseP = (p) => {
  if (!p || p === "此电脑") { toast(t("请先进入一个具体目录"), true); return; }
  const el = pickTargetEl(pickerSt.target);
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
    jumpToRun(r.run_id);
  } catch (e) { toast(t("重试失败：") + e.message, true); return; }
  poll();
}

/* 继续连载：在旧任务基础上新建任务接着写下一批章节。沿用目标/目录/评审设置，
 * 章节号衔接（旧章不动），成书合并仍是完整一本。 */
async function continueSerial(id) {
  let info;
  try {
    info = await api("/api/tasks/" + encodeURIComponent(id) + "/continue-info", { busy: true });
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
  jumpToRun(r.run_id);
  poll();
}
window.continueSerial = continueSerial;

/* 基于此任务新建（通用，不限连载）：把旧任务的类型/目标/目录/评审设置预填进
 * 新建表单，确认或修改后提交——「接着写下一批章节」请用连载任务的「继续连载」，
 * 那里才带章节衔接；这里开的是一个全新任务。 */
const _taskDetailCache = new Map();
const _taskDetailInflight = new Map();
const _taskRunsCache = new Map();
const _taskRunsInflight = new Map();
const _taskRunsAt = new Map();
const _taskRunsPaging = new Map();
const _runMessagesCache = new Map();
const _runMessagesInflight = new Map();
let _taskStepRenderToken = 0;

async function loadTaskForPrefill(id) {
  const active = ((S.state || {}).tasks || []).find((x) => x.id === id);
  if (active) return active;
  const cached = _taskDetailCache.get(id);
  if (cached) return cached;
  if (_taskDetailInflight.has(id)) return _taskDetailInflight.get(id);
  const request = api("/api/tasks/" + encodeURIComponent(id) + "/detail",
    { timeout: 15000, busy: true, operation: "读取任务详情" })
    .then((data) => {
      if (!data || !data.task) throw new Error(t("任务不存在或已删除"));
      _taskDetailCache.set(id, data.task);
      return data.task;
    })
    .finally(() => _taskDetailInflight.delete(id));
  _taskDetailInflight.set(id, request);
  return request;
}

function newFromTask(id) {
  newFromTaskAsync(id);
}

async function newFromTaskAsync(id) {
  // 局部变量不能叫 t：会遮蔽 i18n 函数 t()，下面的文案调用会直接 TypeError
  let tk;
  try {
    const inState = ((S.state || {}).tasks || []).some((x) => x.id === id);
    if (!inState && !_taskDetailCache.has(id)) toast(t("正在读取任务详情…"));
    tk = await loadTaskForPrefill(id);
  } catch (e) { toast(t("读取任务详情失败：") + e.message, true); return; }
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
  S.atts = []; renderAttachChips();  // 附件清单不继承：同目录引用已随 context 保留
  queueGitProbe();  // 工作目录变了，重新探测代码版本
  $("f-mode").value = ["fast", "expert", "manual"].includes(tk.mode) ? tk.mode : "auto";
  $("f-thinking").value = ["low", "standard", "high"].includes(tk.thinking)
    ? tk.thinking : "auto";
  $("f-manual-only").classList.toggle("hidden", $("f-mode").value !== "manual");
  renderImplSelects();
  if (tk.implementer) $("f-impl").value = tk.implementer;
  if (flow && flow.engine === "code") {
    $("f-verify").value = tk.verify_command || "";
  } else if (flow && flow.engine === "direct") {
    renderDirectModelPicker();
    $("f-direct-provider").value = tk.direct_provider_id || "";
    renderDirectModelPicker();
    $("f-direct-model-name").value = tk.direct_model || "";
    $("f-direct-agent").value = tk.direct_agent || "";
    cmpDirectBtnSync();
  } else {
    $("f-manuscript").value = tk.manuscript || "manuscript.md";
    $("f-rounds").value = tk.rounds || 2;
    $("f-threshold").value = (tk.threshold != null ? tk.threshold : 7.0);
    $("f-bestof").value = tk.best_of || 1;
    $("f-rubric").value = (tk.rubric || []).join(", ");
    // 连载参数只带批次设置：不带 start_chapter/continues（那是「继续连载」的衔接语义）
    $("f-chapters").value = tk.serial ? tk.serial.chapters : "";
    $("f-words-per-ch").value = tk.serial ? tk.serial.words_per_chapter : "";
    $("f-variants").value = tk.serial && tk.serial.variants ? tk.serial.variants : "";
    // 分卷：还原每卷章数；指定卷结构还原成可读文本（卷名 + 章数/章号范围）
    $("f-vol-chapters").value = tk.serial && tk.serial.volume_chapters ? tk.serial.volume_chapters : "";
    $("f-vol-spec").value = tk.serial && Array.isArray(tk.serial.volumes)
      ? tk.serial.volumes.map((v) => {
          const ttl = (v && v.title) || t("未命名");
          if (v && v.start && v.end) return ttl + " " + t("第") + v.start + "-" + v.end + t("章");
          if (v && v.chapters) return ttl + " " + t("第") + v.chapters + t("章");
          return ttl;
        }).join(t("；"))
      : "";
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

/* 文件夹右键「查看文件」：左侧栏整体切到文件浏览页（同设置视图的整页切换），
 * 递归列出该工作目录下全部文件，按目录层级渲染成可折叠树；点文件中央弹窗预览。
 * 返回 = 「返回任务列表」按钮，恢复任务树。 */
function openFolderFiles(dir) {
  S.sfDir = dir;   // 迟到响应比对用：返回后丢弃
  document.body.classList.add("files-mode");
  document.body.classList.remove("settings-mode");
  const last = String(dir || "").split(/[\\/]/).filter(Boolean).pop() || dir;
  const titleEl = $("sf-dir"), rowEl = $("sf-dir-row");
  if (titleEl) titleEl.textContent = last;
  if (rowEl) rowEl.title = dir;
  if ($("sf-count")) $("sf-count").textContent = "";
  if ($("sf-body")) $("sf-body").innerHTML = '<div class="sf-msg">' + esc(t("加载中…")) + "</div>";
  loadFolderFiles(dir);
}

/* 相对路径文件数组 → 嵌套树节点 {dirs:{名:节点}, files:[...]} */
function sfBuildTree(files) {
  const root = { dirs: {}, files: [] };
  (files || []).forEach((f) => {
    const parts = String(f.name).split("/");
    let node = root;
    for (let i = 0; i < parts.length - 1; i++)
      node = node.dirs[parts[i]] || (node.dirs[parts[i]] = { dirs: {}, files: [] });
    node.files.push(f);
  });
  return root;
}

function sfFIcon(n) {
  const ext = String(n).split(".").pop().toLowerCase();
  return ext === "md" || ext === "txt" ? "#i-book" : "#i-file";
}

/* 目录内文件行：点击中央弹窗预览（不开新标签页）。
 * data-dir 记文件名相对的目录（根扫=根目录，懒加载子目录=该子目录）——
 * 取内容按行上自己的目录拼 URL，不能用根目录，否则子目录文件必 404。 */
function sfFileHtml(f, depth, dir) {
  return '<a class="sf-file" style="--sf-d:' + depth + '" data-name="' + esc(f.name) +
    '" data-dir="' + esc(dir || "") + '" data-size="' + (Number(f.size) || 0) +
    '" href="javascript:void(0)"' +
    ' title="' + esc(f.name + " · " + fmtSize(f.size)) + '">' +
    '<svg class="ico" aria-hidden="true"><use href="' + sfFIcon(f.name) + '"/></svg>' +
    "<span>" + esc(String(f.name).split("/").pop()) + "</span><i>" + fmtSize(f.size) + "</i></a>";
}

/* 树节点渲染层：目录字典序在前、目录内文件 mtime 新→旧；
 * lazyDirs 是旧后端 scan 只扫一层时回的 subdirs 名单 → 渲染成懒加载文件夹，
 * 点开（ontoggle）再拉该子目录。dir 用于拼懒加载子目录的绝对路径。 */
function sfRenderLevel(node, depth, lazyDirs, dir) {
  const dirs = Object.keys(node.dirs).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const fs = node.files.slice().sort((a, b) => (b.mtime - a.mtime) || a.name.localeCompare(b.name));
  const countOf = (nd) => nd.files.length +
    Object.keys(nd.dirs).reduce((n, k) => n + countOf(nd.dirs[k]), 0);
  return dirs.map((n) => {
    const sub = node.dirs[n];
    return '<details class="sf-dir" open style="--sf-d:' + depth + '">' +
      '<summary title="' + esc(n) + '"><svg class="ico" aria-hidden="true"><use href="#i-folder"/></svg>' +
      "<span>" + esc(n) + "</span><i>" + countOf(sub) + "</i></summary>" +
      sfRenderLevel(sub, depth + 1, null, dir) + "</details>";
  }).join("") +
    (lazyDirs || []).map((n) => {
      const sep = String(dir).indexOf("\\") >= 0 ? "\\" : "/";
      const full = String(dir).replace(/[\\/]+$/, "") + sep + n;
      return '<details class="sf-dir sf-lazy" style="--sf-d:' + depth + '" data-lazy="' + esc(full) +
        '" ontoggle="sfLazyToggle(this)">' +
        '<summary title="' + esc(n) + '"><svg class="ico" aria-hidden="true"><use href="#i-folder"/></svg>' +
        "<span>" + esc(n) + '</span><i class="sf-lc"></i></summary>' +
        '<div class="sf-kids"></div></details>';
    }).join("") + fs.map((f) => sfFileHtml(f, depth, dir)).join("");
}

/* 懒加载文件夹展开：第一次展开时扫该子目录并填充；失败清标记可重开重试 */
window.sfLazyToggle = async function (det) {
  if (!det.open || det.dataset.loaded) return;
  const dir = det.dataset.lazy || "";
  if (!dir) return;
  const kids = det.querySelector(".sf-kids");
  const badge = det.querySelector(".sf-lc");
  if (kids) kids.innerHTML = '<div class="sf-msg">' + esc(t("加载中…")) + "</div>";
  let d = null, err = "";
  try { d = await api("/api/dir/scan?path=" + encodeURIComponent(dir)); }
  catch (e) { err = (e && e.message) || String(e); }
  if (!det.open) return;   // 加载期间又被收起：丢弃
  if (!d || d.error) {
    if (kids) kids.innerHTML = '<div class="sf-msg sf-error">' +
      esc(t("读取失败：") + (err || (d && d.error) || t("未知错误"))) + "</div>";
    det.dataset.loaded = "";   // 允许下次展开重试
    return;
  }
  det.dataset.loaded = "1";
  let depth = 0;
  for (let p = det.parentElement; p; p = p.parentElement)
    if (p.classList && p.classList.contains("sf-dir")) depth++;
  if (kids) kids.innerHTML = sfRenderLevel(sfBuildTree(d.files), depth, d.subdirs, dir);
  if (badge) badge.textContent = (d.files || []).length ? String(d.files.length) : "";
};

async function loadFolderFiles(dir) {
  let d = null, err = "";
  try { d = await api("/api/dir/scan?path=" + encodeURIComponent(dir)); }
  catch (e) { err = (e && e.message) || String(e); }
  // 已返回任务列表 / 已切去别的文件夹：丢弃迟到响应
  if (!document.body.classList.contains("files-mode") || S.sfDir !== dir) return;
  const body = $("sf-body");
  if (!body) return;
  const cnt = $("sf-count");
  if (cnt) cnt.textContent = "";
  if (!d || (d.error && !(d.files || []).length)) {
    body.innerHTML = '<div class="sf-msg sf-error">' +
      esc(t("读取失败：") + (err || (d && d.error) || t("未知错误"))) + "</div>";
    return;
  }
  const files = d.files || [];
  if (cnt) cnt.textContent = files.length ? files.length + t(" 个文件") : "";
  if (!files.length && !(d.subdirs || []).length) {
    body.innerHTML = '<div class="sf-msg">' + esc(t("该目录下暂无文件")) + "</div>";
    return;
  }
  body.innerHTML = sfRenderLevel(sfBuildTree(files), 0, d.subdirs, dir);
}

/* 返回任务列表：左栏恢复任务树（文件页不留状态，重进即重扫） */
function closeFolderFiles() {
  S.sfDir = "";
  document.body.classList.remove("files-mode");
}

/* 文件夹右键「新建任务到该目录」：只预填工作目录，其余留白（ZCode 式——在该工作区里开新活） */
function newTaskInDir(dir) {
  exitSettings();   // 回到新建任务表单
  $("f-workdir").value = dir;
  unhideSideDir(dir);  // 在已移除的目录建任务 → 恢复显示
  queueGitProbe();  // 工作目录变了，重新探测代码版本
}

/* 文件夹右键「移除」：未归档任务整体归档后，整个目录从侧栏隐藏（可随时从
 * localStorage 的 tutti.hiddenDirs 找回，或在该目录新建任务自动恢复）；
 * 文件与运行记录都不动，运行中/排队的任务也照常归档——归档只是隐藏，运行不受影响。 */
async function removeSideDir(ids, dir) {
  if (!ids.length) {
    if (dir) hideSideDir(dir);
    return;
  }
  const ok = await uiConfirm(
    t("把该文件夹下 %1 个任务移出侧栏？文件与运行记录不会删除；移除后文件夹从侧栏隐藏，在该目录新建任务会重新显示。")
      .replace("%1", ids.length),
    { ok: t("移除") });
  if (!ok) return;
  for (const id of ids) await archiveTask(id, true);
  if (dir) hideSideDir(dir);
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
  if (!runs.length) { box.innerHTML = '<div class="empty">' + t("暂无运行记录") + '</div>'; return; }
  const tools = '<div class="run-tools">' +
    '<label class="toggle"><input type="checkbox"' +
    (pickable.size && selN === pickable.size ? " checked" : "") +
    ' onchange="toggleAllRunSel(this.checked)">' + t(" 全选") + '</label>' +
    '<span class="n">' + (selN ? t("已选 ") + selN + t(" 条") : t("勾选可批量删除")) + "</span>" +
    (selN ? '<button class="danger small" onclick="deleteSelectedRuns()">' + t("删除所选") + '</button>' +
            '<button class="ghost small" onclick="clearRunSel()">' + t("取消选择") + '</button>' : "") +
    "</div>";
  box.innerHTML = tools + runs.map((r) => {
    const can = runDeletable(r);
    // 行结构照参考站的「主副标题 + 右侧状态与相对时间」：标题与摘要竖排成一块，
    // 状态点用侧栏同款字形（staskGlyph），时间用相对档（完整时间戳挪进悬停提示）
    const sub = String(r.summary || r.error || (r.step_count ? r.step_count + t(" 个步骤") : "")).trim();
    return '<div class="item" onclick="openRun(\'' + esc(r.id) + '\')">' +
      '<div class="t">' +
      '<input type="checkbox" class="rcheck"' + (S.selRuns[r.id] ? " checked" : "") + (can ? "" : " disabled") +
      ' title="' + (can ? t("勾选以批量删除") : t("运行中的记录不可删除，请先取消")) + '"' +
      ' onclick="event.stopPropagation()" onchange="toggleRunSel(\'' + esc(r.id) + '\', this.checked)">' +
      '<span class="rglyph">' + staskGlyph(r.status || "") + "</span>" +
      '<div class="rt">' +
      '<span class="name">' + esc(r.title) + runKindTag(r.kind) + "</span>" +
      (sub ? '<span class="desc">' + esc(t(sub)) + "</span>" : "") +
      "</div>" +
      '<span class="chip ' + esc(r.status || "") + '">' + esc(runStatusText(r)) + "</span>" +
      '<span class="time" title="' + esc(r.created_at) + '">' + esc(relTime(r.created_at)) + "</span>" +
      '<button class="danger small" title="' + t("删除该记录") + '" onclick="event.stopPropagation(); deleteRun(\'' + esc(r.id) + '\')">' + t("删除") + '</button></div></div>';
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

/* 侧栏「任务」树：文件夹（工作目录）→ 任务，两级。
 * 一级文件夹 = 任务 workdir 末段名（无主运行归「其他」垫底）；二级任务行 = 单行按钮：
 * 状态字形 + 单行省略标题 + 徽章 + 相对时间，点击主栏直开任务详情。
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
  // （内嵌步骤层已移除，run 步骤数不再上侧栏，不参与签名）
  const sig = JSON.stringify([
    runs.map((r) => [r.id, r.status, r.title]),
    tasks.map((t) => [t.id, t.title, t.workdir, t.git_state || ""]),
    Object.keys(latest).map((k) => [k, latest[k].id, latest[k].status]),
    S.detailRunId, S.detailTaskKey, archivedIds.size, S.showArchived, S.sideQ || "",
  ]);
  if (sig === S.sideSig && box.children.length) return;
  S.sideSig = sig;
  const ORPHAN = ORPHAN_DIR;  // 无主运行（无 task_id / 任务已删）的兜底文件夹
  const groups = [], byKey = {};
  const push = (key, taskId, title, status, time, dir) => {
    if (!byKey[key]) {
      byKey[key] = { key, taskId: taskId || "", title, status: status || "", time: time || "",
        dir: dir || ORPHAN, active: false, runIds: [] };
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
    }
  }
  for (const r of runs) {
    if (r.task_id && (tasksById[r.task_id] || archivedIds.has(r.task_id))) continue;  // 任务行已覆盖/已归档
    const g = push(r.task_id || r.id, r.task_id || "", r.title || r.id, r.status,
      r.started_at || r.created_at, ORPHAN);
    g.runIds.push(r.id);
    if (r.status === "running") g.active = true;
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
  // 已移除（隐藏）的目录不上侧栏；「其他」兜底永远保留
  const hid = sideHiddenDirs();
  for (let i = dirs.length - 1; i >= 0; i--) if (hid.has(dirs[i].dir)) dirs.splice(i, 1);
  // 组内任务按活动时间倒序；文件夹：无主运行「其他」恒垫底，其余按最近活动倒序（活跃项目浮上来）
  dirs.forEach((d) => d.groups.sort((a, b) => String(b.time || "").localeCompare(String(a.time || ""))));
  dirs.sort((a, b) => {
    const ao = a.dir === ORPHAN ? 1 : 0, bo = b.dir === ORPHAN ? 1 : 0;
    if (ao !== bo) return ao - bo;
    return String(b.time || "").localeCompare(String(a.time || ""));
  });
  // 文件夹展开态：会话内已渲染过 → 以 DOM 为准（保留用户点击）；首轮 → 读 localStorage，无则启发式
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
  // 二级任务行：单行即全部——点击主栏直开任务详情。
  // 内嵌步骤层已砍：步骤/运行明细在任务详情里更全，侧栏只留状态字形 + 徽章 + 相对时间。
  // 右键菜单仍吃 data-task / data-run；主栏开着某任务详情时行保持高亮。
  const row = (g) => {
    if (g.active) g.status = "running";
    const sel = (g.key === S.detailTaskKey || g.runIds.indexOf(S.detailRunId) >= 0) ? " active" : "";
    return '<div class="stask' + sel + (g.archived ? " archived" : "") + '" tabindex="0" role="button" data-key="' + esc(g.key) +
      '" data-task="' + esc(g.taskId || "") + '" data-run="' + esc(g.runIds[0] || "") +
      '" data-status="' + esc(g.status || "") + '">' + staskGlyph(g.status) +
      '<span class="t">' + esc(g.title) + "</span>" +
      (g.archived ? '<span class="sbadge archb">' + t("已归档") + "</span>" : "") +
      (g.verdict ? '<span class="sbadge">' + t("待裁决") + "</span>" : "") +
      '<span class="tm">' + esc(relTime(g.time)) + "</span>" +
      '<button class="stask-more" type="button" aria-haspopup="menu" aria-label="' + esc(t("任务操作")) + '" title="' + esc(t("任务操作")) +
      '" onclick="event.stopPropagation();openTaskActions(this.closest(\'.stask\'), event)">⋯</button></div>';
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
  renderPageCrumb();
  collapseDrawerIfMobile();
  syncInspectorVis();    // 从设置子页点进运行详情：任务上下文，检查器跟着回来
}

/* 重试/续写/建任务产生新 run 后的统一去向：任务树主视图就地进详情（左栏不动），
 * 只有本来就在设置导航里才切 runs 子页（目录页「完整运行」按钮进来仍留在设置侧栏）。
 * 顺带清步骤定位，别把旧 run 的 focusStep 带进新详情。 */
function jumpToRun(id) {
  S.focusStep = 0;
  S.focusDone = false;
  if (document.body.classList.contains("settings-mode")) switchTab("runs", "settings");   // 已在设置里：留在设置侧栏
  else showDetailInMain();
  openRun(id);
}

/* 任务行激活（点击/键盘）：点任务 = 主栏（侧栏右边的中间区域）直接展开任务详情，
 * 聚合该任务全部 run 的步骤；不允许任何行点了没反应。
 * 无主运行行（任务已删/管理运行，data-task 为空）→ 直开它自己的运行详情；
 * 已归档任务 → toast 指路（归档行先取消归档）；
 * 从未跑过 → toast 说明；跑过但最新 run 排队中（自动续跑退避窗口）→ 照常开
 * 任务详情，能回看历史轮次的步骤与日志。 */
function sideRowActivate(det) {
  const taskId = det.dataset.task || "", runId = det.dataset.run || "";
  if (!taskId) {
    if (runId) sideOpenRun(runId);   // 无主运行：直开运行详情
    return;
  }
  const known = ((S.state || {}).tasks || []).some((x) => x.id === taskId);
  if (!known) {
    const arch = archivedTaskIds().has(taskId);
    toast(arch ? t("已归档任务：先在右键菜单里取消归档，再看任务详情") : t("任务不存在或已删除"), true);
    return;
  }
  if (!inspEligible(taskId)) {
    const lr = ((S.state || {}).task_latest || {})[taskId];
    if (!lr) { toast(t("该任务还没跑过，还没有任务详情"), true); return; }
  }
  sideOpenTask(taskId);
}

/* 打开单条运行详情（右键菜单「打开详情」/检查器步骤条）：不跳设置页——
 * 留在任务树主视图，主栏直接展示运行详情。
 * 带步骤号 n 时，详情渲染完自动定位到该步：滚动 + 高亮 + 展开它的日志。 */
window.sideOpenRun = function (id, n) {
  _taskStepRenderToken++;
  const earlierRunsButton = $("rd-load-earlier");
  if (earlierRunsButton) earlierRunsButton.hidden = true;
  S.focusStep = Number(n) || 0;
  S.focusDone = false;
  S.detailTaskKey = null;
  if (!S.histJump && typeof histPush === "function") histPush({ m: "main", tab: "run-detail" });
  showDetailInMain();
  openRun(id, n ? "steps" : null);   // 带步骤号：钉住步骤分区，自动选卡不抢
};

/* 打开任务级详情：任务可能被续跑/重试过多次，步骤分散在多条 run 里。
 * 按时间顺序列出该任务全部 run 的全部步骤，run 之间加分隔条。 */
window.sideOpenTask = function (key) {
  _taskStepRenderToken++;
  $("rd-steps").innerHTML = '<div class="hint" role="status">' + esc(t("正在加载任务详情…")) + "</div>";
  S.lastRun = null;
  S.detailTaskKey = key;
  S.detailRunId = null;
  S.focusStep = 0;
  S.taskSig = "";
  rdTabReset();
  if (!S.histJump && typeof histPush === "function") histPush({ m: "main", tab: "run-detail" });
  showDetailInMain();
  $("run-detail").classList.remove("hidden");
  document.querySelector("#sub-runs .panel:first-child").classList.add("hidden");
  detailSideReset();     // 换详情目标：任务级 side 缓存作废，等首拉
  const olderRunsButton = $("rd-load-earlier");
  if (olderRunsButton) olderRunsButton.hidden = true;
  S.rdTab = "steps";
  S.rdTabSig = "";
  applyRdTabs();
  syncInspectorVis();    // 详情已铺开：检查器让位（选中保留，返回列表自动滑回）
  renderTaskDetail();
};
window.openRunInRuns = function (id) { jumpToRun(id); };

/* 详情页右侧「✎ 编辑重试」开关：失败/取消的任务把「基于此任务新建」换成
 * 「编辑重试」——同一预填行为（newFromTask），标签贴合「改完再跑」的意图；
 * 其余状态维持原标签。两按钮互斥出现，按钮区不因新增功能多占一行。 */
function setEditRetry(taskId, status, taskExists) {
  const failedish = !!taskId && (status === "failed" || status === "cancelled" || status === "timeout");
  const be = $("btn-editretry");
  if (be) be.classList.toggle("hidden", !failedish);
  const bn = $("btn-newfrom");
  if (bn) bn.classList.toggle("hidden", !taskExists || failedish);
}

/* 详情页归档按钮：非运行中显示；已归档任务显示「取消归档」。
 * 点击走 archiveTask（与右键菜单同一链路：取消归档会自动恢复侧栏目录显示）。 */
function syncArchBtn(task, active) {
  const b = $("btn-arch");
  if (!b) return;
  const show = !!task && !active;
  b.classList.toggle("hidden", !show);
  if (!show) return;
  const archived = !!task.archived;
  b.textContent = archived ? t("取消归档") : t("归档任务");
  b.onclick = async () => {
    try {
      await archiveTask(task.id, !archived);
      b.textContent = archived ? t("归档任务") : t("取消归档");
    } catch (e) { /* archiveTask 内部已 toast */ }
  };
}

/* 质量未通过的代码任务也必须能重试：代码流水线会把「实现完成但验收未通过」
 * 收在 done + verdict.pass=false，不能因此把唯一的重试入口隐藏。 */
function runNeedsRetry(run) {
  if (!run || !run.task_id) return false;
  if (["failed", "cancelled", "timeout"].includes(run.status)) return true;
  if (run.status !== "done") return false;
  const v = run.verdict || {};
  return v.pass === false || v.publishable === false ||
    v.verify_pass === false || v.review_pass === false;
}

function retrySnapshotForTask(taskId, task, latestSummary) {
  const summary = latestSummary || {};
  if (task && (task.status === "running" || task.status === "queued"))
    return { task_id: taskId, status: task.status };
  if (runNeedsRetry(summary)) return summary;
  if (task && ["failed", "cancelled", "timeout"].includes(task.status))
    return { task_id: taskId, status: task.status };
  return summary;
}

/* 重试按钮三态：失败/取消/质量未通过 → 「↻ 继续任务」（断点续跑语义）；连载任务完整跑完
 * 但质量未达标（verdict.publishable=false）→ 「↻ 重写未达标章」——后端 retry
 * 会把未过线章从继承清单剔除，只重写重评这几章（store.retry_task 未达标分支）；
 * 其余状态隐藏。 */
function setRetryBtn(run, task) {
  const b = $("btn-retry");
  if (!b) return;
  const retryable = runNeedsRetry(run);
  const rewrite = !!(run.task_id && task && task.serial && run.status === "done"
    && run.verdict && run.verdict.publishable === false);
  b.classList.toggle("hidden", !(retryable || rewrite));
  b.textContent = rewrite ? t("↻ 重写未达标章")
    : (run.status === "done" ? t("↻ 重试任务")
      : run.status === "timeout" ? t("↻ 从超时进度继续") : t("↻ 继续任务"));
  b.title = rewrite
    ? t("只重写上一遍未过线的章节，已过线章节沿用成稿与评分")
    : run.status === "timeout"
      ? t("从最近一次已保存的大纲和已完成章节断点续跑")
      : t("再跑一次这个任务；连载任务会断点续跑，已完成章节不重写");
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
  const latestSummary = ((S.state || {}).task_latest || {})[key] || {};
  setRetryBtn(retrySnapshotForTask(key, tk, latestSummary), tk);
  setEditRetry(tk ? tk.id : "", tk ? tk.status : "", !!tk);
  syncArchBtn(tk, tk && (tk.status === "running" || tk.status === "queued"));
  const isTask = ((S.state || {}).tasks || []).some((t2) => t2.id === key);
  if (isTask) {
    loadTaskRuns(key, latestSummary);
    return;
  }
  drawTaskDetail(key, ((S.state || {}).runs || []).filter((r) => (r.task_id || r.id) === key));
}

function loadTaskRuns(key, latestSummary) {
  const cached = _taskRunsCache.get(key);
  if (cached) {
    _taskRunsCache.delete(key);
    _taskRunsCache.set(key, cached);
  }
  if (cached && cached.length && latestSummary && cached[0].id === latestSummary.id) {
    const detailVerdict = cached[0].verdict;
    Object.assign(cached[0], latestSummary);
    if (detailVerdict) cached[0].verdict = Object.assign({}, latestSummary.verdict || {}, detailVerdict);
    drawTaskDetail(key, cached);
  }
  const latest = cached && cached[0];
  const hasNewRun = !!(latestSummary && latestSummary.id && (!latest || latestSummary.id !== latest.id));
  const active = !!(latestSummary && ["running", "queued"].includes(latestSummary.status));
  const minAge = active ? 2000 : 12000;
  const pageState = _taskRunsPaging.get(key);
  if (pageState && pageState.loading) return;
  if (_taskRunsInflight.has(key) ||
      (!hasNewRun && cached && Date.now() - (_taskRunsAt.get(key) || 0) < minAge)) return;
  if (!cached) {
    const task = ((S.state || {}).tasks || []).find((x) => x.id === key);
    $("rd-title").textContent = (task && task.title) || key;
    const chip = $("rd-status");
    chip.className = "chip queued";
    chip.textContent = t("加载中…");
    $("rd-steps").innerHTML = '<div class="hint" role="status">' + esc(t("正在加载任务详情…")) + "</div>";
  }
  _taskRunsAt.set(key, Date.now());
  const refreshLatestOnly = !!(cached && cached.length && latestSummary && cached[0].id === latestSummary.id);
  const url = refreshLatestOnly
    ? "/api/runs/" + encodeURIComponent(latestSummary.id)
    : "/api/tasks/" + encodeURIComponent(key) + "/runs?limit=3&offset=0";
  const request = api(url, { timeout: 20000 })
    .then((data) => {
      if (S.detailTaskKey !== key) return;
      let runs = (data && data.runs) || [];
      if (refreshLatestOnly && data && data.run) {
        runs = cached.slice();
        const prior = runs[0];
        runs[0] = data.run;
        const totals = (_taskRunsPaging.get(key) || {}).totals;
        if (totals) {
          const settled = (run) => (run.steps || []).filter((step) =>
            !["running", "queued"].includes(String(step.status || ""))).length;
          totals.steps += (data.run.steps || []).length - (prior.steps || []).length;
          totals.settled_steps += settled(data.run) - settled(prior);
          totals.cost_usd += (Number(data.run.cost_usd) || 0) - (Number(prior.cost_usd) || 0);
          totals.tokens += (Number(data.run.tokens) || 0) - (Number(prior.tokens) || 0);
        }
      }
      if (!refreshLatestOnly && data.total != null) _taskRunsPaging.set(key, {
        offset: Number(data.offset) + runs.length, total: Number(data.total),
        hasMore: !!data.has_more, totals: data.totals || null, loading: false,
      });
      _taskRunsCache.delete(key);
      _taskRunsCache.set(key, runs);
      while (_taskRunsCache.size > 3) {
        const oldest = _taskRunsCache.keys().next().value;
        _taskRunsCache.delete(oldest);
        _taskRunsAt.delete(oldest);
        _taskRunsPaging.delete(oldest);
      }
      if (S.detailTaskKey === key) {
        if (runs.length) drawTaskDetail(key, runs);
        else {
          $("rd-status").textContent = t("暂无运行步骤");
          $("rd-steps").innerHTML = '<div class="empty">' + esc(t("暂无运行步骤")) + "</div>";
        }
      }
    })
    .catch((e) => {
      _taskRunsAt.set(key, 0);
      if (S.detailTaskKey === key && !_taskRunsCache.has(key)) {
        const chip = $("rd-status");
        chip.className = "chip failed";
        chip.textContent = t("加载失败");
        $("rd-steps").innerHTML = '<div class="hint">' + esc(t("加载失败：")) + esc(e.message) +
          ' <button type="button" class="ghost small" onclick="renderTaskDetail()">' + esc(t("重试")) + "</button></div>";
      }
    })
    .finally(() => _taskRunsInflight.delete(key));
  _taskRunsInflight.set(key, request);
}

async function loadEarlierTaskRuns(key) {
  const page = _taskRunsPaging.get(key);
  if (!page || !page.hasMore || page.loading) return;
  page.loading = true;
  _taskRunsPaging.set(key, page);
  const button = $("rd-load-earlier");
  if (button) { button.disabled = true; button.textContent = t("正在加载…"); }
  const request = api("/api/tasks/" + encodeURIComponent(key) + "/runs?limit=3&offset=" + page.offset,
    { timeout: 20000 });
  try {
    const data = await request;
    if (S.detailTaskKey !== key) return;
    const runs = _taskRunsCache.get(key) || [];
    const older = (data && data.runs) || [];
    const appendFrom = runs.length;
    _taskRunsCache.set(key, runs.concat(older));
    page.offset = Number(data.offset) + older.length;
    page.total = Number(data.total) || page.total;
    page.hasMore = !!data.has_more;
    drawTaskDetail(key, _taskRunsCache.get(key), appendFrom);
  } catch (e) {
    toast(t("加载历史运行失败：") + e.message, true);
  } finally {
    page.loading = false;
    _taskRunsPaging.set(key, page);
    if (S.detailTaskKey === key && $("rd-load-earlier")) {
      const button = $("rd-load-earlier");
      button.disabled = !!page.rendering;
      button.textContent = t("加载更早运行") + " · " + Math.max(0, page.total - page.offset);
    }
  }
}
window.loadEarlierTaskRuns = loadEarlierTaskRuns;

function drawTaskDetail(key, runs, appendFrom) {
  if (!runs.length) return;
  // 消息数/未消费数也入签名：新指令或被 drain 后重画指挥区；步骤状态入签名：
  // 取消收尾把僵尸步骤落成「已取消」时要立即重画，不等条数变化；
  // 作品信息状态入签名：后台一键生成 running→done 要立刻反映到成果区面板
  const bmTask = ((S.state || {}).tasks || []).find((x) => x.id === key);
  // 自动续跑退避相位入签名：预定启动时刻过了之后 chip 翻为「正在启动」
  const lr0 = runs[0] || {};
  const resumePending = (lr0.resume_enqueue_at &&
    Date.now() < Date.parse(String(lr0.resume_enqueue_at).replace(" ", "T"))) ? 1 : 0;
  const sig = JSON.stringify(runs.map((r) => [r.id, r.status, (r.steps || []).length,
    (r.steps || []).map((s) => s.status).join(""),
    (r.messages || []).length, (r.messages || []).filter((m) => !m.consumed).length,
    r.message_count, r.pending_message_count,
    r.summary, r.error, r.cost_usd, r.tokens, JSON.stringify(r.verdict || {})])
    .concat([JSON.stringify((bmTask || {}).book_meta || null), resumePending]));
  if (sig === S.taskSig) return;
  S.taskSig = sig;
  const latest = runs[0];                       // runs 新→旧
  const ordered = runs.slice();                 // 详情按最近运行优先，方便排查
  const totalSteps = runs.reduce((a, r) => a + (r.steps || []).length, 0);
  const paging = _taskRunsPaging.get(key);
  const taskStats = ((S.state || {}).task_stats || {})[key] || {};
  const totalRuns = Number(taskStats.runs) || (paging && paging.total) || runs.length;
  const allSteps = Number(taskStats.steps) || (paging && paging.totals && paging.totals.steps) || totalSteps;
  const active = runs.some((r) => r.status === "running" || r.status === "queued");
  // 活跃态细分真实状态：queued 仅用于自动续跑退避或创建到起跑的瞬时状态；
  // 前者在 chip 上写明下一轮何时起跑，避免退避窗口看起来像卡死。
  const activeRun0 = runs.find((r) => r.status === "running" || r.status === "queued");
  const st = activeRun0 ? activeRun0.status : latest.status;
  const resumeIn = (st === "queued" && resumePending)
    ? String(latest.resume_enqueue_at).slice(11, 16) : "";
  $("rd-title").textContent = latest.title || key;
  const chip = $("rd-status");
  chip.className = "chip " + st;
  chip.textContent = resumeIn
    ? t("将于 ") + resumeIn + t(" 自动续跑（第 ") + (Number(latest.auto_resumes) || 0) + t(" 次）")
    : ({ queued: t("排队中"), running: t("运行中"), done: t("完成"), failed: t("失败"), cancelled: t("已取消") }[st] || st);
  const bpt = $("btn-pause");
  if (bpt) bpt.classList.add("hidden");
  $("btn-delete").classList.add("hidden");
  setRetryBtn(latest, bmTask);
  $("btn-talk").classList.toggle("hidden", !(latest.task_id && !chatEngineIsDirect(latest)));
  setEditRetry(latest.task_id, latest.status,
    !!((S.state || {}).tasks || []).some((x) => x.id === latest.task_id));
  S.lastRun = latest;
  const sum = (f) => runs.reduce((a, r) => a + (Number(r[f]) || 0), 0);
  const aggregate = paging ? Object.assign({}, paging.totals || {}, {
    steps: allSteps,
    settled_steps: (paging.totals || {}).settled_steps != null
      ? Number(paging.totals.settled_steps) : totalSteps,
    cost_usd: (paging.totals || {}).cost_usd != null
      ? Number(paging.totals.cost_usd) : sum("cost_usd"),
    tokens: (paging.totals || {}).tokens != null
      ? Number(paging.totals.tokens) : sum("tokens"),
  }) : null;
  renderRunOutcome(latest, runs, aggregate);
  renderDetailOverview(latest, runs, bmTask, aggregate);
  // 指挥区指向最新的活跃运行（无则隐藏整个指挥区，避免终态任务误导用户以为指令还能生效）
  const activeRun = runs.find((r) => r.status === "queued" || r.status === "running");
  // 活跃运行（含排队中）显示取消按钮；取消目标钉在活跃 run——任务级详情没有
  // S.detailRunId，cancelRun 靠 S.cancelTargetRunId 知道取消谁
  $("btn-cancel").classList.toggle("hidden", !activeRun);
  S.cancelTargetRunId = activeRun ? activeRun.id : null;
  const bp2 = $("btn-pause");
  if (bp2 && activeRun) {
    bp2.classList.toggle("hidden", false);
    bp2.textContent = activeRun.paused ? t("继续执行") : t("暂停");
  }
  renderDirector(activeRun || null, !!activeRun);
  renderHive(activeRun || latest);   // 终态回看最近一次运行的蜂巢
  renderChat(latest, active);        // 直连任务：对话时间线在任务级详情同样渲染（S.lastRun 已指向 latest）
  $("rd-meta").innerHTML =
    '<span class="stat">' + t("运行 ") + '<b>' + runs.length + "/" + totalRuns + "</b>" + t(" 次") + "</span>" +
    '<span class="stat">' + t("步骤 ") + '<b>' + totalSteps + "/" + allSteps + "</b>" + t(" 步") + "</span>" +
    '<span class="stat">' + t("成本 ") + '<b>$' + (aggregate ? aggregate.cost_usd : sum("cost_usd")).toFixed(3) + "</b></span>" +
    '<span class="stat">tokens <b>' + (aggregate ? aggregate.tokens : sum("tokens")) + "</b></span>" +
    (latest.error ? '<span class="stat err">' + errTag(latest.error) + esc(latest.error.slice(0, 200)) + "</span>" : "");
  $("rd-plan").classList.add("hidden");
  renderRouting(latest);
  renderChapterScores(latest);
  const renderItems = [];
  ordered.forEach((r, i) => {
    if (appendFrom && i < appendFrom) return;
    renderItems.push({ run: r, runNo: totalRuns - i });
    const steps = r.steps || [];
    for (let stepIndex = steps.length - 1; stepIndex >= 0; stepIndex--)
      renderItems.push({ step: steps[stepIndex], runId: r.id });
  });
  const stepsBox = $("rd-steps");
  const appendOnly = Number.isInteger(appendFrom) && appendFrom > 0;
  if (!appendOnly) stepsBox.replaceChildren();
  let earlier = $("rd-load-earlier");
  if (!earlier) {
    earlier = document.createElement("button");
    earlier.id = "rd-load-earlier";
    earlier.type = "button";
    earlier.className = "ghost small";
    earlier.addEventListener("click", () => loadEarlierTaskRuns(S.detailTaskKey));
    stepsBox.after(earlier);
  }
  earlier.hidden = !paging || !paging.hasMore;
  earlier.disabled = !!(paging && (paging.loading || paging.rendering)) || renderItems.length > 30;
  if (paging) paging.rendering = renderItems.length > 30;
  earlier.textContent = t("加载更早运行") + (paging && paging.hasMore
    ? " · " + Math.max(0, paging.total - paging.offset) : "");
  const renderToken = ++_taskStepRenderToken;
  if (!renderItems.length) {
    stepsBox.innerHTML = '<div class="empty">' + t("尚无步骤") + '</div>';
  } else {
    let renderIndex = 0;
    const renderBatch = () => {
      if (renderToken !== _taskStepRenderToken || S.detailTaskKey !== key) return;
      const batch = [];
      const end = Math.min(renderIndex + 30, renderItems.length);
      for (; renderIndex < end; renderIndex++) {
        const item = renderItems[renderIndex];
        if (item.run) {
          const r = item.run;
          batch.push('<div class="run-sep"><span class="rs-i">' + t("第 ") + item.runNo + "/" + totalRuns + t(" 次运行") + '</span>' +
            '<span class="rs-t">' + esc(String(r.created_at || "").slice(5, 16)) + "</span>" +
            '<span class="chip ' + esc(r.status || "") + '">' + esc(runStatusText(r)) + "</span>" +
            (r.error ? '<span class="rs-err" title="' + esc(r.error.slice(0, 200)) + '">' + esc(r.error.slice(0, 60)) + "</span>" : "") +
            "</div>");
          continue;
        }
        const s = item.step;
        batch.push('<div class="step" data-n="' + Number(s.n) + '" data-run-id="' + esc(item.runId) + '" data-log="' + esc(s.log || "") + '" title="' + esc(s.note || "") + '">' +
          '<span class="n">' + String(s.n).padStart(2, "0") + "</span>" +
          '<span class="role">' + esc(s.role) + "</span>" +
          '<span class="who">' + esc(t(s.agent_label || s.agent)) + "</span>" +
          (String(s.model || "").trim() ? '<span class="st-model" title="' + esc(t("实际派发模型")) + '">' + esc(String(s.model).trim()) + "</span>" : "") +
          '<span class="sum">' + esc((s.note ? "◆ " + s.note + " — " : "") + (s.summary || "")) + "</span>" +
          '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span>" +
          statusChip(s.status) +
          (s.log ? '<button class="step-log-btn" type="button" data-log-run="' + esc(item.runId) + '" data-log-rel="' + esc(s.log) + '" title="' + esc(t("查看日志")) + '">' + esc(t("CLI 日志")) + "</button>" : "") +
          "</div>");
      }
      stepsBox.insertAdjacentHTML("beforeend", batch.join(""));
      if (renderIndex < renderItems.length) requestAnimationFrame(renderBatch);
      else {
        if (paging) paging.rendering = false;
        earlier.disabled = !!(paging && paging.loading);
        applyStepFocus();
      }
    };
    renderBatch();
  }
  // 轮询重画不收起已打开的日志框：运行中日志靠 2.5s live 刷新持续更新，
  // 收起+清 currentLog 会让刚点开的输出被下一轮轮询弹掉
  if (currentLog && !stepsMatch(runs, currentLog)) window.rdLogClose();
  // 报告：最新一次 run 的（缓存，轮询重画不重复拉取）
  const drawReport = async () => {
    const hint = (msg) => {
      if (S.detailTaskKey !== key) return;   // 拉取期间切了详情：别糊到别的任务上
      $("rd-report").innerHTML = '<div class="hint">' + esc(msg) + "</div>";
    };
    if (S.taskReport && S.taskReport.key === key && S.taskReport.runId === latest.id) {
      $("rd-report").innerHTML = S.taskReport.html; return;
    }
    if (latest.status === "done" || latest.report) {
      try {
        const r = await fetch("/api/runs/" + encodeURIComponent(latest.id) + "/report",
          { headers: authHeaders() });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const md = await r.text();
        if (!md.trim()) throw new Error("empty");
        const html2 = md2html(md);
        if (S.detailTaskKey !== key) return;   // 拉取期间切了详情：过期报告不落缓存/不落盘
        S.taskReport = { key, runId: latest.id, html: html2 };
        $("rd-report").innerHTML = html2;
      } catch (e) {
        // 拉取失败不再静默留白（手机端「报告空空」的来源之一）：给可见提示，
        // 不落缓存，下一轮轮询签名变化时自动重试
        hint(t("报告读取失败（{0}），稍后自动重试", (e || {}).message || t("网络异常")));
      }
    } else if (latest.status === "running" || latest.status === "queued") {
      hint(t("本次运行进行中：报告结束后在这里生成，实时进度看「步骤」页签"));
    } else {
      const st = { failed: t("失败"), cancelled: t("已取消"), timeout: t("超时") }[latest.status] || latest.status || "";
      hint(t("本次运行没有生成报告") + (st ? t("（状态：") + st + t("）") : "") +
        t("；各步骤日志在「步骤」页签"));
    }
  };
  drawReport();
  loadArtifacts(latest.id);   // 成品文件双入口同源：主栏「成果」分区 + 检查器（列表上下文）
  renderPreview(latest.id);   // 网页成品预览：入口页 + 可运行 iframe（无成品则整块隐藏）
  // 任务级详情：git 面板吃任务级 side（实时/最新快照），side 未到先以最新 run 快照落位
  const tk = ((S.state || {}).tasks || []).find((x) => x.id === key);
  S.lastRunTask = tk || null;
  renderGitPanel(latest, tk);
  renderBiblePanel(tk);
  renderBookMetaPanel(tk);
  refreshDetailSide();   // 有变化才重画版本面板；meta 累计组任务级详情已有全量 sums，不重复出
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
  _taskStepRenderToken++;
  S.detailRunId = id;
  S.detailTaskKey = null;
  renderPageCrumb();   // 标题「运行详情」；必须在赋值后画——showDetailInMain 先于本函数跑，那边画不到
  rdTabReset(pinTab || null);   // sideOpenRun 带步骤号时钉住步骤分区
  document.querySelector("#sub-runs .panel:first-child").classList.add("hidden");
  $("run-detail").classList.remove("hidden");
  detailSideReset();     // 换详情目标：任务级 side 缓存作废，等首拉
  syncInspectorVis();    // 详情已铺开：检查器让位（选中保留，返回列表自动滑回）
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
  detailSideReset();
  $("run-detail").classList.add("hidden");
  renderChatNav();   // 解除 main.chat-fill（对话为主的固定高度），恢复外层滚动
  if (document.body.classList.contains("settings-mode") || S.mainPage === "runs") {
    // 回运行列表：设置导航里点进来的，或主栏本就停在运行记录页（快捷导航/概览的入口）
    document.querySelector("#sub-runs .panel:first-child").classList.remove("hidden");
    renderPageCrumb();   // 详情关了，标题从「运行详情」回到当前页名
  } else if (S.mainPage) {
    // 主栏停在别的快捷导航页（概览/自动化）：返回点开详情前那一页，左栏任务树不动
    switchTab(S.mainPage, "main");
  } else {
    // 从主视图（侧栏点任务行）进来的详情：返回直接回任务页，不露出运行列表。
    // S.tab 还停在 "runs"（showDetailInMain 设的），视图既然回到任务页就一并归位，
    // 否则标题会拿 "runs" 的页名显示成「运行记录」
    S.tab = "tasks";
    document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-tasks"));
    document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === "tasks"));
    document.querySelectorAll(".qitem").forEach((b) => b.classList.remove("active"));
    renderPageCrumb();
  }
  syncInspectorVis();    // 离开详情：回设置运行列表时检查器按各上下文规则重新落位
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
  if (el.dataset.log) toggleLog(el.dataset.runId || S.detailRunId, el.dataset.log);
  setTimeout(() => el.classList.remove("flash"), 1800);
}

/* ---------------- 详情页标签分区：蜂巢（实时）/ 步骤 / 成果 / 版本 / 圣经 ----------------
 * 主栏详情从一根长条改成五个分区：实时监控、历史步骤、交付成果、代码版本、故事圣经。
 * 分区内容的 hidden 语义保持不变（没数据整块收起）；标签页只切外层 .rd-pane，
 * 测试/代码对 #rd-hive、#rd-git 等 hidden 的判断不受影响。
 * 自动选卡只在「详情目标 + run 状态 + git 裁决态」签名变化时触发一次——
 * 轮询重画不抢用户手选的分区；带步骤号打开详情（sideOpenRun，检查器步骤条入口）钉住步骤分区。 */
function rdTabAvail() {
  return {
    chat: !$("rd-chat").classList.contains("hidden"),
    hive: !$("rd-hive").classList.contains("hidden"),
    steps: true,
    result: true,
    git: !$("rd-git").classList.contains("hidden"),
    bible: !$("rd-bible").classList.contains("hidden"),
    bookmeta: !$("rd-bookmeta").classList.contains("hidden"),
    preview: !$("rd-preview").classList.contains("hidden"),
  };
}

function applyRdTabs() {
  const avail = rdTabAvail();
  if (!S.rdTab || !avail[S.rdTab]) {
    S.rdTab = ["chat", "hive", "steps", "result", "preview", "git", "bible", "bookmeta"]
      .find((k) => avail[k]) || "steps";
  }
  document.querySelectorAll("#rd-tabs .rd-tab").forEach((b) => {
    b.classList.toggle("hidden", !avail[b.dataset.tab]);
    b.classList.toggle("active", b.dataset.tab === S.rdTab);
  });
  document.querySelectorAll("#run-detail .rd-pane").forEach((p) =>
    p.classList.toggle("hidden", p.dataset.pane !== S.rdTab));
  document.querySelectorAll("#rd-tabs .rd-tab").forEach((b) => {
    const active = b.dataset.tab === S.rdTab && !b.classList.contains("hidden");
    b.setAttribute("aria-selected", active ? "true" : "false");
    b.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("#run-detail .rd-pane").forEach((p) =>
    p.setAttribute("aria-hidden", p.dataset.pane === S.rdTab ? "false" : "true"));
  const focusedTab = document.activeElement;
  if (focusedTab && focusedTab.matches && focusedTab.matches("#rd-tabs .rd-tab.hidden")) {
    const nextTab = document.querySelector('#rd-tabs .rd-tab[data-tab="' + S.rdTab + '"]:not(.hidden)');
    if (nextTab) nextTab.focus({ preventScroll: true });
  }
  // 对话页签不放日志抽屉：会盖住贴底输入条（手动切来/自动选卡都覆盖）
  if (S.rdTab === "chat" && !$("rd-log").classList.contains("hidden")) window.rdLogClose();
  renderChatNav();
}

/* 直连任务「对话为主」布局：收起常规页签条，右上角一排小胶囊按需打开
 * 蜂巢/步骤/成果等分区；离开对话时给「返回对话」入口（用户 2026-09-17 拍板：
 * 对话场景其它页签用处不大，默认关掉、需要再点开）。 */
function renderChatNav() {
  const nav = $("rd-chat-nav"), detail = $("run-detail");
  if (!nav || !detail) return;
  const direct = !detail.classList.contains("hidden") && !!S.lastRun &&
    chatEngineIsDirect(S.lastRun);
  detail.classList.toggle("chat-mode", direct);
  // 对话为主时锁死外层滚动（main 是滚动根）；离开详情必须解锚，否则任务列表滚不动
  const mainEl = document.querySelector("main");
  if (mainEl) mainEl.classList.toggle("chat-fill", direct);
  if (!direct) { nav.classList.add("hidden"); nav.innerHTML = ""; return; }
  const labels = { hive: t("蜂巢"), steps: t("步骤"), result: t("成果"),
    preview: t("预览"), git: "Git", bible: t("圣经"), bookmeta: t("作品信息") };
  const avail = rdTabAvail();
  let pills = "";
  for (const k of ["preview", "hive", "steps", "result", "git", "bible", "bookmeta"]) {
    if (!avail[k]) continue;
    // 徽章直接镜像隐藏页签上的（蜂巢在岗数/步骤数/待裁决/成果数），不另设状态源
    const badge = document.querySelector('#rd-tabs .rd-tab[data-tab="' + k + '"] .rd-badge');
    pills += '<button class="cn-pill' + (S.rdTab === k ? " on" : "") +
      '" onclick="rdChatNavGo(\'' + k + '\')">' + labels[k] +
      (badge && badge.textContent ? "<b>" + esc(badge.textContent) + "</b>" : "") + "</button>";
  }
  nav.innerHTML = (S.rdTab !== "chat"
    ? '<button class="cn-back" onclick="rdChatNavBack()">' +
      '<svg class="ico" aria-hidden="true"><use href="#i-arrow-left"></use></svg>' +
      t("返回对话") + "</button>"
    : "") +
    '<span class="cn-pills">' + pills + "</span>";
  nav.classList.remove("hidden");
}
window.rdChatNavGo = function (tab) {
  S.rdTab = tab;
  S.rdTabPin = true;   // 用户主动去看其它分区：别被自动选卡拽回对话
  applyRdTabs();
};
window.rdChatNavBack = function () {
  S.rdTab = "chat";
  S.rdTabPin = false;
  S.rdTabSig = "";
  applyRdTabs();
};

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

/* ctx 可省略：省略时沿用上次的 _rdCtx 做自动选卡判断（renderHive/renderGitPanel/
 * renderPreview 等收尾调用——预览可用性是异步翻过来的，翻过来这次必须重选卡）。 */
function rdTabsSync(ctx) {
  if (ctx) S._rdCtx = ctx;
  const c = ctx || S._rdCtx || {};
  if (!S.rdTabPin) {
    const chatAvail = $("rd-chat") && !$("rd-chat").classList.contains("hidden");
    // 预览可用性入签名：网页成品是跑完才落盘的，从不可用变可用时要重选一次卡
    const pvAvail = $("rd-preview") && !$("rd-preview").classList.contains("hidden");
    const sig = (S.detailTaskKey || S.detailRunId || "") + "|" + (c.status || "") +
      "|" + (c.gitState || "") + "|" + (c.running ? 1 : 0) + "|" + (chatAvail ? 1 : 0) +
      "|" + (pvAvail ? 1 : 0);
    if (S.rdTabSig !== sig) {
      S.rdTabSig = sig;
      const avail = rdTabAvail();
      const direct = chatAvail && chatEngineIsDirect(S.lastRun);
      // 直连任务的主问题是「继续聊什么、上一轮回答是什么」——默认把对话
      // 放在第一视线；编排/代码任务才默认落蜂巢，先看阶段与在岗步骤。
      // 没跑到终态时成果分区是空的，避免打开详情先看到白板。
      const finishing = !c.running && ["done", "failed", "cancelled", "timeout"].includes(c.status);
      S.rdTab = (direct && avail.chat ? "chat" : null)
        // 跑出了能打开的网页成品：第一视线给「预览」——这正是用户做完一个
        // 前端任务最想看到的（借鉴对话式编程产品：跑完先看东西跑起来）。
        // 直连任务的对话、待裁决的版本仍排在它前面：那两个是"要用户说话/动手"。
        || (finishing && avail.preview ? "preview" : null)
        || (avail.hive ? "hive" : null)
        || (avail.chat ? "chat" : null)
        || (c.running ? (avail.hive ? "hive" : "steps")
          : (c.gitState === "isolated" && avail.git ? "git"
            : (c.hasResult && finishing ? "result" : "steps")));
    }
  }
  applyRdTabs();
  rdTabBadges();
}

/* 详情头部的按需信息面板：统计和任务概览不再常驻挤占蜂巢/对话空间。 */
function setRdMetaOpen(open) {
  const detail = $("run-detail");
  const pop = $("rd-meta-popover");
  const toggle = $("rd-more-toggle");
  if (!pop || !toggle) return;
  const on = !!open;
  pop.classList.toggle("hidden", !on);
  if (detail) detail.classList.toggle("rd-info-open", on);
  toggle.setAttribute("aria-expanded", on ? "true" : "false");
}

/* 换一个详情目标时清空选卡状态：下一次渲染按新目标自动落位 */
function rdTabReset(pin) {
  S.rdTab = pin || null;
  S.rdTabSig = "";
  S.rdTabPin = !!pin;
  S._rdCtx = {};
  S._rdHeavy = { runId: "", report: false, artifacts: false, preview: false };
  S._rdDetailToken = (S._rdDetailToken || 0) + 1;
  pvReset();   // 预览也随详情目标清空：别家的 iframe/页签绝不带到新详情
  syncChatLogSpace(false);
  setRdMetaOpen(false);
}

const _rdReportCache = new Map();

function rdCurrent(id, token) {
  return S.detailRunId === id && !S.detailTaskKey &&
    (!token || token === S._rdDetailToken) &&
    !$('run-detail').classList.contains('hidden');
}

function rdScheduleNonCritical(fn) {
  const run = () => { try { fn(); } catch (e) { /* optional detail panels */ } };
  if (typeof window.requestIdleCallback === "function")
    window.requestIdleCallback(run, { timeout: 1200 });
  else setTimeout(run, 80);
}

function rdEnsureHeavyState(id) {
  if (!S._rdHeavy || S._rdHeavy.runId !== id)
    S._rdHeavy = { runId: id, status: "", report: false, artifacts: false, preview: false };
  return S._rdHeavy;
}

async function rdLoadReport(id, token) {
  const state = rdEnsureHeavyState(id);
  if (state.report || !rdCurrent(id, token)) return;
  const run = S.lastRun;
  if (!run || !(run.status === "done" || run.report)) return;
  state.report = true;
  const cached = _rdReportCache.get(id);
  if (cached) { $("rd-report").innerHTML = cached; return; }
  $("rd-report").innerHTML = '<div class="hint" role="status">' + esc(t("正在读取报告…")) + "</div>";
  try {
    const r = await fetch("/api/runs/" + encodeURIComponent(id) + "/report", { headers: authHeaders() });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const md = await r.text();
    if (!md.trim()) throw new Error("empty");
    const html = md2html(md);
    if (!rdCurrent(id, token)) return;
    _rdReportCache.set(id, html);
    if (_rdReportCache.size > 3) _rdReportCache.delete(_rdReportCache.keys().next().value);
    $("rd-report").innerHTML = html;
  } catch (e) {
    if (rdCurrent(id, token)) {
      state.report = false;
      $("rd-report").innerHTML = '<div class="hint">' +
        esc(t("报告读取失败（{0}），稍后可重试", (e || {}).message || t("网络异常"))) + "</div>";
    }
  }
}

function rdEnsureActiveTabData() {
  if (S.detailTaskKey || !S.detailRunId) return;
  const id = S.detailRunId;
  const token = S._rdDetailToken;
  const state = rdEnsureHeavyState(id);
  if (S.rdTab === "result") {
    rdLoadReport(id, token);
    if (!state.artifacts) { state.artifacts = true; loadArtifacts(id); }
  } else if (S.rdTab === "preview" && !state.preview) {
    state.preview = true;
    renderPreview(id);
  } else if (S.rdTab === "git") {
    renderGitPanel(S.lastRun, S.lastRunTask);
  } else if (S.rdTab === "bible") {
    renderBiblePanel(S.lastRunTask);
  } else if (S.rdTab === "bookmeta") {
    renderBookMetaPanel(S.lastRunTask);
  }
}

function renderRunDetailSteps(run, token) {
  const box = $("rd-steps");
  if (!box) return;
  const rows = (run.steps || []).slice().reverse();
  if (!rows.length) { box.innerHTML = '<div class="empty">' + t("尚无步骤") + '</div>'; return; }
  box.replaceChildren();
  let index = 0;
  const renderBatch = () => {
    if (token !== S._rdDetailToken || S.detailRunId !== run.id || S.detailTaskKey) return;
    const html = [];
    const end = Math.min(index + 30, rows.length);
    for (; index < end; index++) {
      const s = rows[index];
      html.push('<div class="step" data-n="' + Number(s.n) + '" data-run-id="' + esc(run.id) + '" data-log="' + esc(s.log || "") +
        '" title="' + esc(s.note || "") + '">' +
        '<span class="n">' + String(s.n).padStart(2, "0") + "</span>" +
        '<span class="role">' + esc(s.role) + "</span>" +
        '<span class="who">' + esc(t(s.agent_label || s.agent)) + "</span>" +
        (s.model ? '<span class="st-model" title="' + esc(t("实际派发模型")) + '">' + esc(s.model) + "</span>" : "") +
        '<span class="sum">' + esc((s.note ? "◆ " + s.note + " — " : "") + (s.summary || "")) + "</span>" +
        '<span class="dur">' + (s.duration_s != null ? s.duration_s + "s" : "") + "</span>" +
        statusChip(s.status) +
        (s.log ? '<button class="step-log-btn" type="button" data-log-run="' + esc(run.id) + '" data-log-rel="' + esc(s.log) + '" title="' + esc(t("查看日志")) + '">' + esc(t("CLI 日志")) + "</button>" : "") +
        "</div>");
    }
    box.insertAdjacentHTML("beforeend", html.join(""));
    if (index < rows.length) requestAnimationFrame(renderBatch);
    else applyStepFocus();
  };
  renderBatch();
}

async function renderRunDetail() {
  const id = S.detailRunId;
  if (S.detailTaskKey) { renderTaskDetail(); return; }   // 任务级详情（轮询也会走到这里刷新）
  if (!id) return;
  const token = S._rdDetailToken || (S._rdDetailToken = 1);
  const fetchSeq = S._rdFetchSeq = (S._rdFetchSeq || 0) + 1;
  let run;
  try { run = (await api("/api/runs/" + encodeURIComponent(id))).run; }
  catch (e) { return; }
  if (!run) return;
  if (!rdCurrent(id, token) || fetchSeq !== S._rdFetchSeq) return;   // 拉取期间已切走/被更新请求取代
  $("rd-title").textContent = run.title;
  const chip = $("rd-status");
  chip.className = "chip " + run.status +
    (run.status === "done" && run.verdict && run.verdict.pass === false ? " unqualified" : "");
  chip.textContent = runStatusText(run);
  const active = run.status === "queued" || run.status === "running";
  $("btn-cancel").classList.toggle("hidden", !active);
  S.cancelTargetRunId = active ? run.id : null;
  $("btn-delete").classList.toggle("hidden", active);
  $("btn-share").classList.toggle("hidden", active);   // 分享页：结束后可生成自包含 HTML
  $("btn-talk").classList.toggle("hidden", !(run.task_id && !chatEngineIsDirect(run)));
  const rcTask = ((S.state || {}).tasks || []).find((x) => x.id === run.task_id);
  // 归档按钮：非运行中任务可归档/取消归档（此前只有侧栏右键菜单入口，
  // 用户反馈「归档按钮不见了」——补显式入口，文案随状态切换）
  syncArchBtn(rcTask, active);
  setRetryBtn(run, rcTask);
  $("btn-continue").classList.toggle("hidden",
    !(rcTask && rcTask.serial && run.status !== "running" && run.status !== "queued"));
  setEditRetry(run.task_id, run.status, !!rcTask);
  S.lastRun = run;
  renderRunOutcome(run);
  renderDetailOverview(run, [run], rcTask);
  const bp = $("btn-pause");
  if (bp) { bp.classList.toggle("hidden", !active);
    bp.textContent = run.paused ? t("继续执行") : t("暂停"); }
  renderDirector(run, active);
  renderHive(run);
  renderChat(run, active);
  $("rd-meta").innerHTML =
    '<span class="stat">' + t("创建 ") + '<b>' + esc(run.created_at) + "</b></span>" +
    '<span class="stat">' + t("成本 ") + '<b>$' + Number(run.cost_usd || 0).toFixed(3) + "</b></span>" +
    '<span class="stat">tokens <b>' + (run.tokens || 0) + "</b></span>" +
    (run.mode ? '<span class="stat">' + t("模式 ") + '<b>' + ({auto: t("自动"), fast: t("快速"), expert: t("专家"), manual: t("手动")}[run.mode] || esc(run.mode)) + "</b></span>" : "") +
    (runEtaText(run) ? '<span class="stat eta">' + esc(runEtaText(run)) + "</span>" : "") +
    (run.error ? '<span class="stat err">' + errTag(run.error) + esc(run.error.slice(0, 200)) + "</span>" : "") +
    '<span class="stat tasksum hidden" id="rd-meta-task"></span>';
  if (S.detailSide) fillMetaTask(S.detailSide.stats || {});   // 缓存命中：轮询重画不闪丢累计组
  renderPlan(run);
  renderRouting(run);
  renderChapterScores(run);
  // 首帧只输出一小批步骤，剩余内容交给浏览器下一帧，避免大运行记录堵住交互。
  renderRunDetailSteps(run, token);
  applyStepFocus();
  const heavy = rdEnsureHeavyState(id);
  if (heavy.status !== run.status) {
    heavy.status = run.status;
    heavy.report = false;
    heavy.artifacts = false;
    heavy.preview = false;
  }
  const reportHint = run.status === "done" || run.report
    ? t("点击“成果”页签后加载报告和成品文件")
    : ({ running: t("本次运行进行中：报告结束后在这里生成，实时进度看「步骤」页签"), queued: t("本次运行排队中：实时进度看「步骤」页签") }[run.status] || t("本次运行没有生成报告"));
  $("rd-report").innerHTML = '<div class="hint">' + esc(reportHint) + "</div>";
  // 预览只在首屏空闲时探测，报告及其 Markdown 解析仍等用户打开「成果」。
  rdScheduleNonCritical(() => {
    if (!rdCurrent(id, token)) return;
    const currentHeavy = rdEnsureHeavyState(id);
    if (!currentHeavy.preview) { currentHeavy.preview = true; renderPreview(id); }
  });
  // 成品文件双入口同源：主栏「成果」分区 + 检查器成品 TAB（列表上下文），
  // 详情上下文检查器让位后主栏是唯一可见面
  S.lastRunTask = rcTask || null;
  rdTabsSync({
    running: active, status: run.status, gitState: (rcTask || {}).git_state || "",
    steps: (run.steps || []).length,
    runningCount: (run.steps || []).filter((x) => x.status === "running").length,
    hasResult: run.status === "done" || !!run.report,
  });
  // 自动选中的页签也先让首帧交互完成；用户主动点击页签时，点击处理器
  // 会直接调用 rdEnsureActiveTabData，不会让这次延迟影响明确操作。
  rdScheduleNonCritical(rdEnsureActiveTabData);
  rdScheduleNonCritical(() => {
    if (!rdCurrent(id, token)) return;
    renderGitPanel(run, rcTask);
    renderBiblePanel(rcTask);
    renderBookMetaPanel(rcTask);
    refreshDetailSide();
    rdTabsSync();
  });
}

/* 代码版本隔离面板：run 检出任务分支 codebee/<id> 后，产物提交在该分支上、
 * 用户工作区已切回原分支。分支是「每任务一条」，裁决（合并/丢弃）在任务级生效。
 * 数据源两路：任务级 side（详情页自拉，运行中实时 numstat、结束后最新 run 快照，
 * 续跑/重试的新 run 没带 git 字段也能从任务带出分支）优先；side 不可用
 * （无主运行/任务已删）退回本 run 自带快照。两路都没有分支时整块隐藏。 */
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
  // 拉取期间可能切详情：过期响应不落盘（把 A 任务的圣经写进 B 详情的串台面）
  const detailKey = S.detailTaskKey || S.detailRunId || "";
  let d;
  try { d = await api("/api/tasks/" + encodeURIComponent(task.id) + "/bible"); }
  catch (e) { box.classList.add("hidden"); rdTabsSync(); return; }
  if ((S.detailTaskKey || S.detailRunId || "") !== detailKey ||
      ((S.lastRunTask || {}).id || "") !== (task.id || "")) {
    box.classList.add("hidden"); rdTabsSync(); return;
  }
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

/* ---------------- 作品信息一键生成（番茄/七猫建书表单资料） ----------------
 * 独立 TAB（仅连载任务）：用户手动点按钮触发生成（不自动调模型），后台线程跑，
 * 状态随任务 JSON（SSE 全量状态）自动刷进来。生成完字段直接内联在平台卡片里，
 * 每个字段带复制按钮——用户的目的就是往番茄/七猫建书表单里逐格粘贴。 */
const BOOKMETA_PLATFORMS = [
  { id: "fanqie", label: "番茄" },
  { id: "qimao", label: "七猫" },
];
const BOOKMETA_FIELDS = {
  fanqie: [["book_name", "作品名"], ["signing_mode", "签约模式"], ["target_reader", "目标读者"],
    ["category", "主分类"], ["tags_theme", "主题标签"], ["tags_role", "角色标签"],
    ["tags_plot", "情节标签"], ["content_plot", "内容·情节"], ["content_emotion", "内容·情感"],
    ["content_character", "内容·人设"], ["content_world", "内容·世界观"],
    ["protagonist_1", "主角名1"], ["protagonist_2", "主角名2"], ["summary", "作品简介"]],
  qimao: [["book_name", "作品名称"], ["target_reader", "目标读者"], ["category_main", "一级分类"],
    ["category_sub", "二级分类"], ["tags_style", "风格标签"], ["tags_role", "角色标签"],
    ["tags_plot", "情节标签"], ["tags_bg", "背景标签"], ["protagonist_1", "主角名1"],
    ["protagonist_2", "主角名2"], ["status", "作品状态"], ["summary", "作品简介"]],
};

function bmFieldValue(v) {
  if (Array.isArray(v)) return v.length ? v.join("、") : t("（待补充）");
  const s = String(v == null ? "" : v).trim();
  return s || t("（待补充）");
}

function bmStatusChip(st) {
  const map = { done: ["ok", "已生成"], running: ["run", "生成中"], failed: ["bad", "生成失败"] };
  const m = map[st];
  return m ? '<span class="bm-st ' + m[0] + '">' + (m[1] === "生成中" ? "● " : "") + t(m[1]) + "</span>" : "";
}

/* 作品信息 TAB：只有连载首批需要（建书表单只在开书时填一次）。
 * 续写批次（serial.start_chapter > 1）沿用第一批的书名/简介/标签，不再重复出面板——
 * 开书资料属于「这本书」，不属于某一批章节。非连载任务同样不渲染。 */
function bmNeedsPanel(task) {
  const s = (task || {}).serial;
  if (!s) return false;
  try { return intOf(s.start_chapter, 1) <= 1; } catch (e) { return true; }
}

function intOf(v, dflt) {
  const n = parseInt(v, 10);
  return isNaN(n) ? dflt : n;
}

function renderBookMetaPanel(task) {
  const box = $("rd-bookmeta");
  if (!box || !(task || {}).id || !bmNeedsPanel(task)) {
    if (box) box.classList.add("hidden");
    return;
  }
  const bm = task.book_meta || {};
  const taskBusy = task.status === "running" || task.status === "queued";
  let html = '<div class="bm-head"><svg class="ico" aria-hidden="true"><use href="#i-idcard"/></svg>' +
    '<span class="sec-title">' + t("作品信息") + "</span>" +
    '<span class="hint">' + esc(t("按发布平台生成建书表单资料，逐字段复制过去")) + "</span>" +
    '<button class="hhelp" data-help-topic="publish" title="' + esc(t("这是什么？点开看帮助")) + '" aria-label="' + esc(t("帮助")) + '"><svg class="ico" aria-hidden="true"><use href="#i-help"></use></svg></button>' +
    '<span class="flex1"></span>';
  if (taskBusy) html += '<span class="bm-warn">' + esc(t("任务运行中，生成将在本轮结束后可用")) + "</span>";
  html += "</div>";
  if (!bm.fanqie && !bm.qimao && !taskBusy) {
    html += '<div class="bm-guide"><svg class="ico" aria-hidden="true"><use href="#i-idcard"/></svg>' +
      '<div><b>' + esc(t("章节已就绪，创建作品还差开书资料")) + "</b>" +
      "<p>" + esc(t("点平台卡片上的「生成」按钮，一键产出书名/简介/标签/主角名等建书资料，生成后逐字段复制进建书表单。")) + "</p></div></div>";
  }
  html += '<div class="bm-cards">' + BOOKMETA_PLATFORMS.map((p) => {
    const entry = bm[p.id] || {};
    const st = entry.status || "";
    let body = "";
    if (st === "running") {
      body = '<div class="bm-skwrap">' + BOOKMETA_FIELDS[p.id].map(() =>
        '<div class="bm-sk"><span class="bm-sk-l"></span><span class="bm-sk-v"></span></div>').join("") + "</div>";
    } else if (st === "done" && entry.data) {
      body = '<div class="bm-fields">' + (BOOKMETA_FIELDS[p.id] || []).map(([key, label]) => {
        const raw = entry.data[key];
        const long = key === "summary";
        const tags = Array.isArray(raw) && raw.length;
        const val = bmFieldValue(raw);
        // 标签类字段渲染成 chips（复制仍复制顿号串），长字段独占整行
        const v = tags
          ? '<span class="bm-chips">' + raw.map((x) => '<span class="bm-chip">' + esc(String(x)) + "</span>").join("") + "</span>"
          : '<span class="bm-f-v' + (long ? " bm-f--scroll" : "") + '" title="' + esc(val) + '">' + esc(val) + "</span>";
        return '<div class="bm-f' + (long || tags ? " bm-f--full" : "") + '">' +
          '<span class="bm-f-k">' + esc(t(label)) + "</span>" + v +
          '<button class="bm-copy" data-text="' + esc(val) + '" data-label="' + esc(t(label)) +
          '" title="' + esc(t("点击复制")) + '" onclick="bmCopyBtn(this)"><svg class="ico" aria-hidden="true"><use href="#i-file-text"/></svg>' + t("复制") + "</button></div>";
      }).join("") + "</div>";
    } else if (st === "failed") {
      body = '<div class="bm-errhint">' + esc(entry.error || t("生成失败")) + "</div>";
    } else {
      body = '<div class="bm-empty">' + esc(t("尚未生成")) + "</div>";
    }
    let action;
    if (st === "running") {
      action = '<button class="ghost" disabled><svg class="ico spin" aria-hidden="true"><use href="#i-refresh"/></svg>' + t("生成中…") + "</button>";
    } else if (st === "done") {
      action = '<button class="ghost" onclick="bmGen(\'' + esc(task.id) + "', '" + p.id + '\')" title="' + esc(t("重新生成会覆盖现有内容")) + '">' +
        '<svg class="ico" aria-hidden="true"><use href="#i-refresh"/></svg>' + t("重新生成") + "</button>";
    } else {
      action = '<button class="primary" onclick="bmGen(\'' + esc(task.id) + "', '" + p.id + '\')">' +
        (st === "failed" ? t("重试") : t("生成")) + "</button>";
    }
    // 头部行：平台名 + 状态徽章 + 右侧操作按钮（action 必须在 head 内闭合，
    // 否则头部 div 吞掉后面的字段区，卡片会嵌套叠在一起）
    const head = '<div class="bm-card-head">' +
      '<span class="bm-plat-name"><svg class="ico" aria-hidden="true"><use href="#i-library"/></svg>' + esc(t(p.label)) + "</span>" +
      bmStatusChip(st) + '<span class="flex1"></span>' + action + "</div>";
    return '<div class="bm-card st-' + esc(st || "new") + '">' + head + body +
      (entry.source && st === "done" ? '<div class="bm-src">' + esc(t("来源：") + entry.source) + "</div>" : "") +
      pbBlock(task, p.id) +
      "</div>";
  }).join("") + "</div>";
  // 封面卡（covergen，借鉴 oh-story 封面图环节）：curl 落盘运行目录，
  // done 后卡片内直接嵌缩略图（/api/runs/<id>/cover 专用通道），点开新页看大图
  const cg = task.cover_gen || {};
  const cgSt = cg.status || "";
  let cgBody = "";
  if (cgSt === "running") {
    cgBody = '<div class="bm-empty"><svg class="ico spin" aria-hidden="true"><use href="#i-refresh"/></svg> ' + esc(t("生成中…")) + "</div>";
  } else if (cgSt === "done" && cg.run_id) {
    const covUrl = "/api/runs/" + encodeURIComponent(cg.run_id) + "/cover" +
      (cg.at ? "?ts=" + encodeURIComponent(cg.at) : "");   // at 当缓存戳，重生成后缩略图跟着换
    cgBody = '<div class="bm-cover-wrap">' +
      '<a href="' + esc(urlAuth(covUrl)) + '" target="_blank" rel="noopener" title="' + esc(t("点击查看大图")) + '">' +
      '<img class="bm-cover-img" src="' + esc(urlAuth(covUrl)) + '" alt="cover.png" ' +
      'onerror="this.closest(&quot;.bm-cover-wrap&quot;).classList.add(&quot;noimg&quot;)"></a>' +
      '<span class="bm-cover-fallback">' + esc(t("cover.png 已不在运行目录")) + "</span>" +
      '<div class="bm-cover-meta">' + esc([cg.model, cg.provider, cg.size].filter(Boolean).join(" · ")) + "</div></div>";
  } else if (cgSt === "done") {
    cgBody = '<div class="bm-empty">' + esc(t("封面已生成，但缺少运行记录归属")) + "</div>";
  } else if (cgSt === "failed") {
    cgBody = '<div class="bm-errhint">' + esc(cg.error || t("生成失败")) + "</div>";
  } else {
    cgBody = '<div class="bm-empty">' + esc(t("生成竖版封面插画，产出 cover.png")) + "</div>";
  }
  const cgBusy = taskBusy || cgSt === "running";
  const cgAction = cgSt === "running"
    ? '<button class="ghost" disabled><svg class="ico spin" aria-hidden="true"><use href="#i-refresh"/></svg>' + t("生成中…") + "</button>"
    : '<button class="primary" onclick="coverGen(\'' + esc(task.id) + '\')">' +
      (cgSt === "failed" ? t("重试") : t("生成封面")) + "</button>";
  html += '<div class="bm-cards"><div class="bm-card st-' + (cgSt || "new") + '">' +
    '<div class="bm-card-head"><span class="bm-plat-name"><svg class="ico" aria-hidden="true"><use href="#i-book-open"/></svg>' + esc(t("封面图")) + "</span>" +
    bmStatusChip(cgSt) + '<span class="flex1"></span>' + cgAction + "</div>" + cgBody + "</div></div>";
  box.classList.remove("hidden");
  box.innerHTML = html;
  // TAB 徽章：已生成平台数（生成中显示 ●，随下次轮询刷新）
  const badge = document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"] .rd-badge');
  if (badge) {
    const n = BOOKMETA_PLATFORMS.filter((p) => (bm[p.id] || {}).status === "done").length;
    const anyRun = BOOKMETA_PLATFORMS.some((p) => (bm[p.id] || {}).status === "running");
    badge.textContent = anyRun ? "●" : (n ? n + "/" + BOOKMETA_PLATFORMS.length : "");
    badge.className = "rd-badge" + (badge.textContent ? (anyRun ? " live" : "") : " hidden");
  }
  pbSyncState(task);   // 发布状态独立于 SSE（/api/publish），节流拉取+变化重渲染
}

window.bmGen = async function (taskId, platform) {
  try {
    await api("/api/tasks/" + encodeURIComponent(taskId) + "/book-meta",
      { method: "POST", body: JSON.stringify({ platform }) });
    toast(t("已开始生成，完成后这里会自动更新"));
    await refreshState(); render();   // 立即翻到「生成中」，不等 SSE 推送/下一轮轮询
  } catch (e) { toast(t("生成失败：") + e.message, true); }
};

/* 封面图生成（covergen）：curl 子进程直接落盘运行目录，Python 不经手图像字节 */
window.coverGen = async function (taskId) {
  try {
    await api("/api/tasks/" + encodeURIComponent(taskId) + "/cover",
      { method: "POST", body: "{}" });
    toast(t("已开始生成封面，完成后这里会自动更新"));
    await refreshState(); render();
  } catch (e) { toast(t("封面生成失败：") + e.message, true); }
};

window.bmCopyBtn = function (btn) {
  copyText(btn.dataset.text || "");
  toast(t("已复制：") + (btn.dataset.label || ""));
};

/* ---------------- 一键发布（bookmeta 分区内每平台卡的发布行） ----------------
 * 状态独立于 SSE（/api/publish 轮询，5s 节流；waiting_login/busy 靠主轮询周期
 * 自然刷新）。Phase 1 纪律：所有提交动作 auto_submit=false——表单填好后停，
 * 提交权留给用户在浏览器窗口里人工确认（建书/发章皆是）。 */
const PB_ST = {
  none: ["未连接", "pb-st-none"], waiting_login: ["等扫码…", "pb-st-wait"],
  connected: ["已连接", "pb-st-ok"], busy: ["操作中…", "pb-st-busy"],
  error: ["出错", "pb-st-err"],
};

function pbBlock(task, platform) {
  const ps = (S.pubState && S.pubState.platforms && S.pubState.platforms[platform]) || {};
  const st = ps.status || "none";
  const [label, cls] = PB_ST[st] || PB_ST.none;
  const books = (S.pubTaskInfo && S.pubTaskInfo.books) || {};
  const book = books[platform];
  const hist = (S.pubTaskInfo && S.pubTaskInfo.history) || [];
  const nCh = hist.filter((r) => r.platform === platform && r.action === "upload_chapter" && r.ok).length;
  const busy = st === "busy";
  let btns = "";
  if (st === "none" || st === "error" || st === "waiting_login") {
    btns += '<button class="ghost" onclick="pbConnect(\'' + platform + '\')">' +
      (st === "error" ? t("重连") : t("连接平台")) + "</button>";
  } else if (st === "connected") {
    btns += '<button class="ghost" onclick="pbDisconnect(\'' + platform + '\')" title="' + esc(t("关掉该平台的浏览器窗口（登录态保留）")) + '">' + t("断开") + "</button>";
  }
  btns += '<button class="ghost pb-tool" onclick="pbProbe(\'' + platform + '\')" title="' +
    esc(t("dump 平台表单结构（校准自动填表用）")) + '">' + t("探测") + "</button>";
  if (book) {
    btns += '<span class="pb-book" title="' + esc(t("已在此平台创建的作品")) + '">' +
      esc(t("已建书：") + (book.title || "")) + "</span>";
    btns += ' <button class="primary" ' + (busy ? "disabled" : "") +
      ' onclick="pbUploadChapter(\'' + esc(task.id) + "', '" + platform + '\')">' +
      t("发一章") + (nCh ? t("（已发 ") + nCh + t("）") : "") + "</button>";
    // 批量发布（publish/auto.py）：待发清单 + 护栏 + 进度都来自 /pending 视图
    const au = (((S.pubAuto && S.pubAuto.books) || [])
      .find((b) => b.platform === platform)) || {};
    const run = (S.pubAuto && S.pubAuto.running &&
      S.pubAuto.running.platform === platform) ? S.pubAuto.running : null;
    if (run && run.status === "running") {
      btns += '<span class="pb-book">' + esc(t("自动发布中 ") + run.done + "/" + run.total) + "</span>";
    } else if (au.pending > 0) {
      btns += ' <button class="ghost" ' + (busy || !au.guard_ok ? "disabled" : "") +
        ' title="' + esc(au.guard_ok ? t("按章号顺序逐章填稿（人工模式每点一次填一章，浏览器里提交后再点发下一章）") : au.guard_reason || "") + '"' +
        ' onclick="pbPublishAll(\'' + esc(task.id) + "', '" + platform + '\')">' +
        t("发布全部待发") + t("（") + au.pending + t("）") + "</button>";
    }
    if (au.pending > 0 && !au.guard_ok) {
      btns += '<div class="pb-err">' + esc(au.guard_reason || t("护栏拦截")) + "</div>";
    }
    if (run && run.status === "manual_pause") {
      // 人工模式一轮只填一章：等用户在浏览器提交后再发起（连发会导航离开
      // 未提交的编辑器丢稿）。message 是下一步指引，不是错误。
      btns += '<div class="pb-hint">' + esc(run.message || t("已填好一章，请在浏览器确认提交")) + "</div>";
    } else if (run && run.status !== "running") {
      const doneLine = run.status === "done"
        ? t("自动发布完成：") + run.done + "/" + run.total + (run.message ? t("。") + run.message : "")
        : t("自动发布中断：") + (run.error || "");
      btns += '<div class="' + (run.status === "done" ? "pb-hint" : "pb-err") + '">' +
        esc(doneLine + t("（") + (run.at || "") + t("）")) + "</div>";
    }
  } else {
    btns += '<button class="primary" ' + (busy ? "disabled" : "") +
      ' onclick="pbCreateBook(\'' + esc(task.id) + "', '" + platform + '\')">' + t("创建作品") + "</button>";
    btns += '<button class="ghost" onclick="pbRegToggle(\'' + platform + '\')" title="' +
      esc(t("作品已在平台建好（建书流程没走通或手工建的）？补登记后直接发章，不会在平台新建")) + '">' + t("登记已有作品") + "</button>";
  }
  let extra = "";
  if (st === "error" && !book && ps.last_action === "create_book") {
    extra += '<div class="pb-hint">' + esc(t("若作品其实已在平台建好，点「登记已有作品」补登记，勿重复创建")) + "</div>";
  }
  if (!book) {
    extra += '<div class="pb-reg hidden" id="pb-reg-' + platform + '">' +
      '<input id="pb-reg-title-' + platform + '" placeholder="' + esc(t("平台上的作品名（必填）")) + '">' +
      '<input id="pb-reg-id-' + platform + '" placeholder="' + esc(t("作品ID（选填，管理页URL里有）")) + '">' +
      '<button class="ghost" onclick="pbRegister(\'' + esc(task.id) + "', '" + platform + '\')">' + t("确认登记") + "</button>" +
      "</div>";
  }
  return '<div class="bm-pub">' +
    '<span class="pb-badge ' + cls + '">' + esc(t(label)) + "</span>" +
    btns +
    (st === "error" && ps.error ? '<div class="pb-err">' + esc(ps.error) + "</div>" : "") +
    extra +
    '<div class="pb-chapters hidden" id="pb-ch-' + platform + '"></div>' +
    "</div>";
}

let _pbTimer = 0;
function pbSyncState(task) {
  clearTimeout(_pbTimer);
  _pbTimer = setTimeout(async () => {
    const now = Date.now();
    if (now - (S.pubFetchAt || 0) < 5000) return;   // 节流：主轮询不每次都打 /api/publish
    S.pubFetchAt = now;
    let sig = "";
    try {
      const v = await api("/api/publish");
      S.pubState = v;
      sig += JSON.stringify(v.platforms || {});
    } catch (e) { return; }
    try {
      const ti = await api("/api/publish/task/" + encodeURIComponent(task.id) + "/history");
      S.pubTaskInfo = ti;
      sig += "#" + JSON.stringify(ti.books || {}) + "#" + (ti.history || []).length;
    } catch (e) { /* 任务级失败不阻塞平台状态 */ }
    try {
      // 批量发布视图（待发数/护栏/进度）——进行中时靠本节流轮询自然刷新
      const au = await api("/api/publish/task/" + encodeURIComponent(task.id) + "/pending");
      S.pubAuto = au;
      sig += "@" + JSON.stringify(au.books || {}) + "@" + JSON.stringify(au.running || {});
    } catch (e) { /* 视图缺失（老服务）不阻塞 */ }
    if (sig !== (S._pbSig || "") && !$("rd-bookmeta").classList.contains("hidden")) {
      S._pbSig = sig;
      renderBookMetaPanel(task);
    } else S._pbSig = sig;
  }, 120);
}

window.pbConnect = async function (platform) {
  try {
    await api("/api/publish/" + platform + "/connect", { method: "POST", body: "{}" });
    toast(t("已打开浏览器——请在窗口里登录平台，登录后这里自动变为「已连接」"));
  } catch (e) { toast(t("连接失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

window.pbDisconnect = async function (platform) {
  try { await api("/api/publish/" + platform + "/disconnect", { method: "POST", body: "{}" }); }
  catch (e) { toast(t("断开失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

window.pbProbe = async function (platform) {
  try {
    await api("/api/publish/" + platform + "/probe", { method: "POST", body: "{}" });
    toast(t("已开始探测，结果记录在发布台账（data/publish）里"));
  } catch (e) { toast(t("探测失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

window.pbCreateBook = async function (taskId, platform) {
  if (!confirm(t("将用生成的作品信息在平台自动填建书表单。填好后会停在最后一步，由你在浏览器里人工点提交。继续？"))) return;
  try {
    await api("/api/publish/task/" + encodeURIComponent(taskId) + "/create-book",
      { method: "POST", body: JSON.stringify({ platform }) });
    toast(t("正在自动填写建书表单…（完成后请在浏览器里确认提交）"));
  } catch (e) { toast(t("建书失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

window.pbRegToggle = function (platform) {
  const box = $("pb-reg-" + platform);
  if (box) box.classList.toggle("hidden");
};

window.pbRegister = async function (taskId, platform) {
  const title = (($("pb-reg-title-" + platform) || {}).value || "").trim();
  const bookId = (($("pb-reg-id-" + platform) || {}).value || "").trim();
  if (!title) { toast(t("请填写平台上的作品名"), true); return; }
  try {
    await api("/api/publish/task/" + encodeURIComponent(taskId) + "/register-book",
      { method: "POST", body: JSON.stringify({ platform: platform, title: title, book_id: bookId }) });
    toast(t("已登记，可直接发章"));
    const box = $("pb-reg-" + platform);
    if (box) box.classList.add("hidden");
  } catch (e) { toast(t("登记失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

window.pbUploadChapter = async function (taskId, platform) {
  const box = $("pb-ch-" + platform);
  if (!box) return;
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  box.innerHTML = '<div class="pb-loading">' + esc(t("正在列出章节文件…")) + "</div>";
  let wd = "", pubTask = null;
  try {
    pubTask = ((S.state || {}).tasks || []).find((x) => x.id === taskId);
    wd = (pubTask && pubTask.workdir) || "";
  } catch (e) { /* 兜底空 */ }
  if (!wd) { box.innerHTML = '<div class="pb-err">' + esc(t("找不到任务工作目录")) + "</div>"; return; }
  let files = [];
  try {
    const r = await api("/api/dir/scan?path=" + encodeURIComponent(wd));
    files = (r.files || []).filter((f) => /\.md$/i.test(f.name || "") &&
      !/^作品信息/.test((f.name || "").split("/").pop()));
  } catch (e) {
    box.innerHTML = '<div class="pb-err">' + esc(t("列文件失败：") + e.message) + "</div>";
    return;
  }
  if (!files.length) { box.innerHTML = '<div class="pb-err">' + esc(t("工作目录里没有 .md 章稿")) + "</div>"; return; }
  files.sort((a, b) => (a.name > b.name ? 1 : -1));
  const item = (f) =>
    '<button class="ghost pb-ch-item" onclick="pbSendChapter(\'' + esc(taskId) + "', '" + platform +
    '\',\'' + esc(f.name) + '\')" title="' + esc(f.name) + '">' + esc(f.name.split("/").pop()) + "</button>";
  const hint = '<div class="pb-hint">' + esc(t("选一章发送（表单填好后停，人工确认提交）；已成功发过的章会被台账拦下：")) + "</div>";
  // 最高章号：让卷规划表铺到最后一章（否则超出显式卷表的章节会掉进「其他」）
  let maxCh = 0;
  files.forEach((f) => {
    const n = chapterNumOf(f.name);
    if (n > maxCh) maxCh = n;
  });
  const plan = volumePlanOf(pubTask, maxCh);
  if (!plan.length) { box.innerHTML = hint + files.map(item).join(""); return; }
  // 分卷：按卷分组列出可发章节（章号取自 chapter-NN.md）。未落入卷的文件归「其他」。
  const groups = plan.map((v) => ({ v: v, files: [] }));
  const rest = [];
  files.forEach((f) => {
    const n = chapterNumOf(f.name);
    if (!n) { rest.push(f); return; }
    const g = groups.find((x) => n >= x.v.first && (x.v.last == null || n <= x.v.last));
    if (g) g.files.push(f); else rest.push(f);
  });
  box.innerHTML = hint + groups.filter((g) => g.files.length).map((g) => {
    const label = t("第") + " " + g.v.vol + " " + t("卷") + (g.v.title ? " " + t("《") + g.v.title + t("》") : "");
    return '<div class="pb-vol"><div class="pb-vol-head">' + esc(label) + "</div>" +
      g.files.map(item).join("") + "</div>";
  }).join("") + (rest.length ? '<div class="pb-vol"><div class="pb-vol-head">' +
    esc(t("其他文件")) + "</div>" + rest.map(item).join("") + "</div>" : "");
};

window.pbSendChapter = async function (taskId, platform, relName) {
  if (!confirm(t("将把《{0}》填进平台章节编辑器，填好后由你人工提交。继续？", relName))) return;
  try {
    await api("/api/publish/task/" + encodeURIComponent(taskId) + "/chapter",
      { method: "POST", body: JSON.stringify({ platform, file: relName }) });
    toast(t("正在填写章节…（完成后请在浏览器里确认提交）"));
    const box = $("pb-ch-" + platform);
    if (box) box.classList.add("hidden");
  } catch (e) { toast(t("发章失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

window.pbPublishAll = async function (taskId, platform) {
  const au = (((S.pubAuto && S.pubAuto.books) || [])
    .find((b) => b.platform === platform)) || {};
  if (!au.pending) return;
  if (!confirm(t("将从最靠前的待发章节开始填稿（共 {0} 章待发）。本轮只填一章并停在表单页，由你在浏览器里确认提交；提交后再点一次即发下一章。护栏（每日上限/连续失败暂停）生效。继续？", au.pending))) return;
  try {
    await api("/api/publish/task/" + encodeURIComponent(taskId) + "/publish-all",
      { method: "POST", body: JSON.stringify({ platform }) });
    toast(t("正在填第一章稿——填好后请在浏览器窗口里确认提交"));
  } catch (e) { toast(t("自动发布失败：") + e.message, true); }
  S._pbSig = ""; pbKick();
};

function pbKick() {
  const key = detailSideTaskKey();
  if (!key) return;
  const tk = ((S.state || {}).tasks || []).find((x) => x.id === key);
  if (tk) { S.pubFetchAt = 0; pbSyncState(tk); }
}

/* ---------------- 详情页任务级 side 数据（检查器让位后的主栏自给） ----------------
 * 检查器收进列表上下文后，任务累计统计与 git 实时状态由详情页自己轮询
 * /api/tasks/<id>/side（KB 级、2s 节流，与检查器同款）。拉不到（无主运行、
 * 任务已删、接口失败）就静默保持 null，版本面板退回 run 快照、meta 不出累计组。 */
function detailSideReset() {
  S.detailSide = null;
  S.detailSideSig = "";
  S.detailSideAt = 0;
}

function detailSideTaskKey() {
  return (S.detailTaskKey || (S.lastRun && S.lastRun.task_id) || "");
}

async function refreshDetailSide(force) {
  const key = detailSideTaskKey();
  if (!key || $("run-detail").classList.contains("hidden")) return;
  const now = Date.now();
  if (!force && now - (S.detailSideAt || 0) < 2000) return;
  S.detailSideAt = now;
  let d;
  try { d = await api("/api/tasks/" + encodeURIComponent(key) + "/side"); }
  catch (e) { return; }                      // 拉取失败静默，等下一轮节流重试
  if (detailSideTaskKey() !== key) return;   // 期间已切到别的详情：过期响应不落盘
  if (!d || !d.task) { S.detailSide = null; return; }
  S.detailSide = d;
  const sig = JSON.stringify([d.task.git_state, d.git, d.changes, d.stats]);
  if (sig === S.detailSideSig) return;       // 没变化不重画（保住文件行 hover 态）
  S.detailSideSig = sig;
  applyDetailSide();
}

/* side 数据落位：meta 条任务累计组 + 版本面板重画（内部再按数据源取舍） */
function applyDetailSide() {
  const d = S.detailSide;
  if (!d) return;
  fillMetaTask((d.stats || {}));
  renderGitPanel(S.lastRun, S.lastRunTask);
}

/* meta 条的任务累计 pill：run 级统计旁边给任务全貌。renderRunDetail 每轮
 * 轮询都会重建 rd-meta，所以渲染时也要回填（S.detailSide 有缓存就即时填） */
function fillMetaTask(st) {
  const el = $("rd-meta-task");
  if (!el) return;
  el.classList.remove("hidden");
  el.innerHTML = esc(t("任务累计")) + " <b>" + (st.runs || 0) + "</b> " + esc(t("次运行")) +
    " · <b>" + (st.steps || 0) + "</b> " + esc(t("步")) +
    ' · <b>$' + (Number(st.cost_usd) || 0).toFixed(3) + "</b> · tok <b>" + (st.tokens || 0) + "</b>";
}

/* ---------------- GIT 工作台（详情页「版本」页签） ----------------
 * 工作目录是 git 仓库 → 完整工作台：分支切换 / 三组变更文件（暂存·取消暂存·
 * 丢弃/删除）/ 提交 / fetch·pull·push / stash 收起还原；数据走
 * /api/tasks/<id>/git（workdir 由服务端按任务推导，不接受任意路径），
 * 5s 节流轮询、写操作后立即重拉。非仓库 → 退回「代码版本隔离」快照面板。
 * 写操作在任务运行中被服务端 409 拒绝，前端同步禁用并给只读提示。 */
async function loadGitWb(force) {
  const key = detailSideTaskKey();
  if (!key || S.gitWbBusy) return;
  if (!force && S.gitWbKey === key && Date.now() - (S.gitWbAt || 0) < 5000) return;
  S.gitWbBusy = true;
  try {
    const d = await api("/api/tasks/" + encodeURIComponent(key) + "/git");
    if (detailSideTaskKey() !== key) return;   // 期间已切详情目标：过期响应不落盘
    S.gitWb = d; S.gitWbKey = key; S.gitWbAt = Date.now();
    renderGitPanel(S.lastRun, S.lastRunTask);
  } catch (e) { /* 静默：下一轮节流重试 */ }
  finally { S.gitWbBusy = false; }
}

function renderGitPanel(run, task) {
  const box = $("rd-git");
  if (!box) return;
  const tid = (task || {}).id || "";
  if (tid) {
    loadGitWb();   // 5s 节流；写操作后 force 立即重拉
    const wb = (S.gitWbKey === tid) ? S.gitWb : null;
    if (wb && wb.repo) { renderGitWbMain(wb, task); return; }
    // 探测未回（wb=null）：先按快照落位不闪空；确认非仓库（repo=false）也走快照
  }
  _renderGitSnapshot(run, task);
}

function _renderGitSnapshot(run, task) {
  const box = $("rd-git");
  if (!box) return;
  const tid = (task || {}).id || "";
  // 数据源取舍：side 命中且确实属于当前详情的任务才用，否则退 run 快照
  const sTask = ((S.detailSide || {}).task || {});
  const useSide = !!sTask.id && sTask.id === ((run || {}).task_id || tid);
  const side = useSide ? S.detailSide : null;
  const g = side ? (side.git || {}) : ((run || {}).git || {});
  const state = side ? (sTask.git_state || "") : ((task || {}).git_state || "");
  const revChosen = side ? (sTask.git_rev || "") : ((task || {}).git_rev || "");
  const ch = side ? (side.changes || {}) : ((run || {}).changes || {});
  const files = ch.files || [];
  const active = ["running", "queued"].indexOf(side ? sTask.status : ((task || {}).status)) >= 0;
  if (!g || !g.branch) {
    // 没有任务分支：区分「没启用」（未指定代码版本 → 整块隐藏）和
    // 「启用了但检出失败」（给原因，不能误报成没启用——同检查器口径）
    if (revChosen) {
      const lastErr = side ? (((side.run || {}).error) || "") : ((run || {}).error || "");
      box.classList.remove("hidden");
      box.innerHTML =
        '<div class="git-head"><svg class="ico" aria-hidden="true"><use href="#i-git-branch"></use></svg>' +
        '<span class="sec-title">' + t("代码版本隔离") + '</span>' +
        '<span class="chip failed">' + t("检出失败") + "</span>" +
        '<code class="git-branch">codebee/' + esc(tid || t("（无主运行）")) + t("（") + esc(t("未创建")) + t("）") + "</code></div>" +
        (lastErr ? '<div class="hint warn">' + esc(lastErr) + "</div>" : "") +
        '<div class="hint">' + esc(t("处理后点「继续任务」，运行会重新检出任务分支。")) + "</div>";
      rdTabsSync();
      return;
    }
    box.classList.add("hidden"); box.innerHTML = ""; rdTabsSync(); return;
  }
  const chip = GIT_STATE_CHIP[state] || ["muted", "—"];
  let html =
    '<div class="git-head"><svg class="ico" aria-hidden="true"><use href="#i-git-branch"></use></svg>' +
    '<span class="sec-title">' + t("代码版本隔离") + '</span>' +
    '<button class="hhelp" data-help-topic="code" title="' + esc(t("这是什么？点开看帮助")) + '" aria-label="' + esc(t("帮助")) + '"><svg class="ico" aria-hidden="true"><use href="#i-help"></use></svg></button>' +
    '<code class="git-branch" title="' + esc(t("任务分支：产物提交在此，原分支未受影响")) + '">' + esc(g.branch) + "</code>";
  const addT = ch.add_total, delT = ch.del_total;
  if (addT != null && delT != null) {
    html += '<span class="insp-plus">+' + addT + '</span><span class="insp-minus">-' + delT + "</span>";
  }
  html += '<span class="chip ' + chip[0] + '">' + t(chip[1]) + "</span>" +
    "</div>";
  html += '<div class="git-meta">' +
    "<span>" + esc(t("基线")) + " <b>" + esc(g.rev || "-") + "</b> → " + esc(g.from_branch || "-") + "</span>" +
    (g.commit ? "<span>" + esc(t("分支提交")) + " <b>" + esc(g.commit) + "</b></span>" : "") +
    (side ? "" : "<span>" + esc(t("本 run 快照")) + "</span>") +
    "</div>";
  if (g.restore_error) {
    html += '<div class="hint warn">' + esc(t("收尾出错：")) + esc(g.restore_error) + "</div>";
  }
  // 变更行双击看 diff：diff 文本挂在 run 的 changes 快照上，side 场景取最新 run
  const diffRunId = (side && (side.run || {}).id) || (run || {}).id || "";
  html += '<div class="git-files">' + (files.length
    ? files.slice(0, 40).map((f) =>
        '<button class="gf" data-p="' + esc(f.path) + '" data-rid="' + esc(diffRunId) +
        '" ondblclick="fileDiffPopup(this.dataset.p, this.dataset.rid)" title="' +
        esc(t("双击弹窗查看该文件的变更")) + '"><i class="gs ' + (f.status === "M" ? "m" : f.status === "D" ? "d" : "n") + '">' +
        esc(gitStatusLabel(f.status)) + "</i>" + esc(f.path) + "</button>").join("") +
      (files.length > 40 ? '<span class="gm">+' + (files.length - 40) + " " + esc(t("个文件")) + "</span>" : "")
    : '<span class="hint">' + esc(t(active ? "运行中：工作区变更会实时出现在这里。" : "本次运行没有产生工作区变更。")) + "</span>") +
    "</div>";
  // 裁决是任务级动作：只在待裁决且任务空闲时给出（运行/排队中合并会踩正在写的分支）
  if (state === "isolated" && tid && !active) {
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

/* 工作台文件行：状态字 + 路径 + ±统计，悬停出 暂存/取消暂存/丢弃(删除) 按钮，
 * 点击/双击弹实时 diff（/api/tasks/<id>/git/diff，读工作区，不依赖 run 快照） */
function _gwbRow(f, kind, ro) {
  const p = f.path || "";
  const cls = f.status === "M" ? "m" : f.status === "D" ? "d" : "n";
  let acts = "";
  if (!ro) {
    const stage = '<button class="ghost gwb-act" data-p="' + esc(p) + '" onclick="gitWbStage(this.dataset.p)" title="' +
      esc(t("暂存")) + '">+</button>';
    const unstage = '<button class="ghost gwb-act" data-p="' + esc(p) + '" onclick="gitWbUnstage(this.dataset.p)" title="' +
      esc(t("取消暂存")) + '">−</button>';
    const discard = '<button class="ghost gwb-act gwb-danger" data-p="' + esc(p) + '" onclick="gitWbDiscard(this.dataset.p)" title="' +
      esc(t("丢弃改动")) + '">↶</button>';
    const del = '<button class="ghost gwb-act gwb-danger" data-p="' + esc(p) + '" onclick="gitWbDelete(this.dataset.p)" title="' +
      esc(t("删除文件")) + '">✕</button>';
    if (kind === "staged") acts = unstage + discard;
    else if (kind === "unstaged") acts = stage + discard;
    else acts = stage + del;
  }
  return '<div class="gwb-row">' +
    '<button class="gf" data-p="' + esc(p) + '" onclick="gitWbDiff(this.dataset.p)" ' +
    'ondblclick="gitWbDiff(this.dataset.p)" title="' + esc(t("点击查看该文件的变更内容")) + '">' +
    '<i class="gs ' + cls + '">' + esc(gitStatusLabel(f.status)) + "</i>" +
    '<span class="p">' + esc(p) + "</span>" +
    (f.add ? '<span class="insp-plus">+' + f.add + "</span>" : "") +
    (f.del ? '<span class="insp-minus">-' + f.del + "</span>" : "") +
    "</button>" + acts + "</div>";
}

function _gwbGroup(title, files, kind, ro, emptyHint) {
  const n = (files || []).length;
  return '<div class="gwb-group" data-kind="' + kind + '">' +
    '<div class="gwb-ghead"><b>' + esc(title) + '</b><span class="gm">' + n + "</span></div>" +
    (n ? files.map((f) => _gwbRow(f, kind, ro)).join("")
       : '<span class="hint">' + esc(emptyHint) + "</span>") +
    "</div>";
}

function renderGitWbMain(d, task) {
  const box = $("rd-git");
  if (!box) return;
  const tid = (task || {}).id || "";
  const ro = d.task && (d.task.status === "running" || d.task.status === "queued");
  const prevMsg = (box.querySelector("#gwb-msg") || {}).value || "";
  const dis = ro ? " disabled" : "";
  // 分支下拉：本地 + 远程两组；游离 HEAD 时当前值给占位项
  const cur = d.branch || "";
  const opt = (b) => '<option value="' + esc(b) + '"' + (b === cur ? " selected" : "") + ">" + esc(b) + "</option>";
  let sel = '<select id="gwb-branch" class="git-branch gwb-branch" title="' + esc(t("切换分支")) + '"' + dis + ">";
  if (d.detached) sel += '<option value="" selected disabled>' + esc(t("(游离 HEAD)")) + "</option>";
  if ((d.branches || []).length) sel += '<optgroup label="' + esc(t("本地分支")) + '">' + d.branches.map(opt).join("") + "</optgroup>";
  if ((d.remote_branches || []).length) sel += '<optgroup label="' + esc(t("远程分支")) + '">' + d.remote_branches.map(opt).join("") + "</optgroup>";
  sel += "</select>";
  let html =
    '<div class="git-head"><svg class="ico" aria-hidden="true"><use href="#i-git-branch"></use></svg>' +
    '<span class="sec-title">' + t("Git 工作台") + "</span>" +
    '<button class="hhelp" data-help-topic="code" title="' + esc(t("这是什么？点开看帮助")) + '" aria-label="' + esc(t("帮助")) + '"><svg class="ico" aria-hidden="true"><use href="#i-help"></use></svg></button>' + sel +
    '<code class="git-branch" title="HEAD">' + esc(d.head || "") + "</code>" +
    (d.upstream ? '<span class="gm" title="' + esc(d.upstream) + '">↑' + (d.ahead || 0) + " ↓" + (d.behind || 0) + "</span>" : "") +
    '<span class="flex1"></span>' +
    '<button class="ghost" onclick="loadGitWb(true)" title="' + esc(t("刷新")) + '">' +
    '<svg class="ico" aria-hidden="true"><use href="#i-refresh"></use></svg></button></div>';
  // 工具条：抓取/拉取/推送 + 全部暂存 + stash；无远程时网络类禁用
  const noRemote = !(d.remotes || []).length;
  const hasChg = (d.staged || []).length + (d.unstaged || []).length + (d.untracked || []).length;
  html += '<div class="gwb-toolbar">' +
    '<button class="ghost"' + (noRemote || ro ? " disabled" : "") + ' onclick="gitWbFetch()">' + t("抓取") + "</button>" +
    '<button class="ghost"' + (noRemote || ro ? " disabled" : "") + ' onclick="gitWbPull()">' + t("拉取") + "</button>" +
    '<button class="ghost"' + (noRemote || ro ? " disabled" : "") + ' onclick="gitWbPush()">' + t("推送") + "</button>" +
    '<button class="ghost"' + (noRemote || ro || d.detached ? " disabled" : "") + ' onclick="gitWbPrCreate()" title="' +
    esc(t("推送当前分支并用 gh 创建 PR（目标分支自动取 main/master）")) + '">' + t("建 PR") + "</button>" +
    '<button class="ghost"' + (!hasChg || ro ? " disabled" : "") + ' onclick="gitWbStageAll()">' + t("全部暂存") + "</button>" +
    '<button class="ghost"' + (!hasChg || ro ? " disabled" : "") + ' onclick="gitWbStash()" title="' +
    esc(t("把未提交改动收进 stash（不含 _attachments）")) + '">' + t("收起改动") + "</button>" +
    (ro ? '<span class="hint">' + esc(t("任务运行中：Git 写操作暂停（只读查看）。")) + "</span>" : "") +
    "</div>";
  (d.stashes || []).forEach((s) => {
    html += '<div class="gwb-stash"><span class="gm">' + esc(s.ref) + '</span><span class="p">' + esc(s.subject) + "</span>" +
      (ro ? "" : '<button class="ghost" onclick="gitWbStashPop(\'' + esc(s.ref) + '\')">' + t("还原") + "</button>" +
        '<button class="ghost gwb-danger" onclick="gitWbStashDrop(\'' + esc(s.ref) + '\')">' + t("删除") + "</button>") +
      "</div>";
  });
  // 三组变更文件
  html += _gwbGroup(t("暂存的变更"), d.staged, "staged", ro, t("暂存区为空")) +
    _gwbGroup(t("变更（未暂存）"), d.unstaged, "unstaged", ro, t("没有未暂存的改动")) +
    _gwbGroup(t("未跟踪"), d.untracked, "untracked", ro, t("没有未跟踪文件"));
  // 提交区
  html += '<div class="gwb-commit">' +
    '<input id="gwb-msg" maxlength="500" placeholder="' + esc(t("提交信息（提交暂存区里的文件）")) + '"' + dis + ">" +
    '<button class="primary" onclick="gitWbCommit()"' + (!(d.staged || []).length || ro ? " disabled" : "") + ">" + t("提交") + "</button></div>";
  // 最近提交时间线
  if ((d.recent || []).length) {
    html += '<div class="gwb-log">' + d.recent.map((c) =>
      '<div class="gwb-lc"><code>' + esc(c.hash) + '</code><span class="p">' + esc(c.subject) + "</span>" +
      '<span class="gm">' + esc(c.author) + " · " + esc(c.age) + "</span></div>").join("") + "</div>";
  }
  // 任务分支隔离区（沿用快照面板的裁决按钮）
  const iso = d.isolation || {};
  if (iso.rev && !iso.state) {
    html += '<div class="hint">' + esc(t("已配置代码版本隔离，但任务分支尚未创建（检出失败见运行错误）。")) + "</div>";
  } else if (iso.state === "isolated") {
    html += '<div class="git-meta"><span>' + esc(t("任务分支")) + " <b>codebee/" + esc(tid) + "</b></span>" +
      '<span class="chip isolated">' + t("待裁决") + "</span></div>";
    if (!ro) {
      html += '<div class="git-actions">' +
        '<button class="primary" onclick="gitMerge(\'' + esc(tid) + "')\">" +
        '<svg class="ico" aria-hidden="true"><use href="#i-check"></use></svg>' + t("合并回原分支") + "</button>" +
        '<button class="danger ghost" onclick="gitDiscard(\'' + esc(tid) + "')\">" +
        '<svg class="ico" aria-hidden="true"><use href="#i-x"></use></svg>' + t("丢弃分支") + "</button>" +
        '<span class="hint">' + esc(t("合并＝采纳产物回原分支；丢弃＝删除任务分支（不可恢复）。")) + "</span></div>";
    }
  } else if (iso.state) {
    const chip = GIT_STATE_CHIP[iso.state] || ["muted", "—"];
    html += '<div class="git-meta"><span>' + esc(t("任务分支")) + " <b>codebee/" + esc(tid) + "</b></span>" +
      '<span class="chip ' + chip[0] + '">' + t(chip[1]) + "</span></div>";
  }
  box.classList.remove("hidden");
  box.innerHTML = html;
  const bs = box.querySelector("#gwb-branch");
  if (bs) bs.addEventListener("change", () => { if (bs.value) window.gitWbCheckout(bs.value); });
  const mi = box.querySelector("#gwb-msg");
  if (mi) {
    if (prevMsg) mi.value = prevMsg;   // 轮询重画不丢用户正在写的提交信息
    mi.addEventListener("keydown", (e) => { if (e.key === "Enter") window.gitWbCommit(); });
  }
  rdTabsSync();   // 版本面板显隐决定「版本」标签可用性
}

/* 工作台单文件实时 diff：点击文件行即弹（读工作区，运行中也能看最新状态） */
window.gitWbDiff = async function (path) {
  const key = detailSideTaskKey();
  if (!key || !path) return;
  _fpSaveCtx = null; _fpRawText = ""; _fpDirty = false; _fpCopyText = null;   // 复用单例弹窗，别带上一个文件的编辑态
  _fpOpen(String(path).split("/").pop(), "",
    '<button class="ghost" onclick="copyFPText()">' + esc(t("复制")) + "</button>");
  let d;
  try { d = await api("/api/tasks/" + encodeURIComponent(key) + "/git/diff?path=" + encodeURIComponent(path)); }
  catch (e) { _fpSetBody('<div class="fp-hint">' + esc(t("diff 读取失败：") + e.message) + "</div>"); return; }
  if (!filePopIsOpen()) return;   // 读取期间被 Esc 关掉：别把内容又糊上去
  const lines = String(d.diff || "").split("\n");
  _fpCopyText = (d.diff && lines.length) ? lines.join("\n") : "";   // 复制给 diff 原文，行号槽不进剪贴板
  _fpSetBody((d.diff && lines.length)
    ? '<div class="fp-diff">' + lines.map((ln, i) => _fpDiffLine(ln, i + 1)).join("") + "</div>"
    : '<div class="fp-hint">' + esc(t("该文件当前没有未提交的变更。")) + "</div>");
};

async function _gitWbPost(action, params, okMsg) {
  const key = detailSideTaskKey();
  if (!key) return null;
  let res = null;
  try {
    res = await api("/api/tasks/" + encodeURIComponent(key) + "/git",
      { method: "POST", body: JSON.stringify(Object.assign({ action }, params || {})) });
    if (okMsg) toast(typeof okMsg === "function" ? okMsg(res || {}) : okMsg);
  } catch (e) { toast(e.message, true); }
  loadGitWb(true);   // 成败都立即重拉：失败信息已在 toast，列表反映真实状态
  return res;
}

window.gitWbStage = function (p) { return _gitWbPost("stage", { path: p }); };
window.gitWbUnstage = function (p) { return _gitWbPost("unstage", { path: p }); };
window.gitWbStageAll = function () { return _gitWbPost("stage_all", null, t("已暂存全部变更")); };
window.gitWbFetch = function () { return _gitWbPost("fetch", null, t("已抓取远程更新")); };
window.gitWbPull = function () { return _gitWbPost("pull", null, t("已拉取远程更新")); };
window.gitWbPush = function () { return _gitWbPost("push", null, t("已推送到远程")); };
window.gitWbPrCreate = async function () {
  // 提交信息框有字时兼作 PR 标题（一处输入两用，不为 PR 单开输入框）
  const mt = ((($("gwb-msg") || {}).value || "").trim());
  const q = mt
    ? t("把当前分支推送到远程并用 gh 创建 PR？（目标分支自动取 main/master；提交信息框内容将作为 PR 标题）")
    : t("把当前分支推送到远程并用 gh 创建 PR？（目标分支自动取 main/master）");
  if (!await uiConfirm(q, { ok: t("建 PR") })) return null;
  const res = await _gitWbPost("pr_create", mt ? { title: mt.slice(0, 120) } : null,
    (r) => (r && r.url ? t("PR 已创建：") + r.url : t("PR 已创建")));
  if (res && res.url) window.open(res.url, "_blank", "noopener");
  return res;
};
window.gitWbCheckout = function (br) {
  if (!br) return;
  return _gitWbPost("checkout", { branch: br }, t("已切换到 ") + br);
};
window.gitWbDiscard = async function (p) {
  if (!await uiConfirm(t("丢弃该文件的全部未提交改动？此操作不可恢复。"), { ok: t("丢弃"), danger: true })) return;
  return _gitWbPost("discard", { path: p, confirm: true });
};
window.gitWbDelete = async function (p) {
  if (!await uiConfirm(t("删除这个未跟踪文件？此操作不可恢复。"), { ok: t("删除"), danger: true })) return;
  return _gitWbPost("delete", { path: p, confirm: true });
};
window.gitWbStash = function () { return _gitWbPost("stash_push", null, t("未提交改动已收进 stash")); };
window.gitWbStashPop = function (ref) { return _gitWbPost("stash_pop", { ref }, t("已还原 stash 改动")); };
window.gitWbStashDrop = async function (ref) {
  if (!await uiConfirm(t("删除这条 stash？收起的改动将永久丢弃。"), { ok: t("删除"), danger: true })) return;
  return _gitWbPost("stash_drop", { ref, confirm: true });
};
window.gitWbCommit = async function () {
  const msg = (($("gwb-msg") || {}).value || "").trim();
  if (!msg) { toast(t("请先填写提交信息"), true); return; }
  const d = await _gitWbPost("commit", { message: msg },
    (r) => t("已提交 ") + (r.files || 0) + t(" 个文件 → ") + (r.commit || ""));
  if (d && $("gwb-msg")) $("gwb-msg").value = "";
};

async function _gitVerdictDone() {
  poll();
  S.gitWbAt = 0;            // 工作台缓存作废：裁决改了 git_state，5s 节流内也要立刻重拉
  if (S.detailTaskKey) { S.taskSig = ""; renderTaskDetail(); }
  else if (S.detailRunId) renderRunDetail();
  refreshDetailSide(true);  // 裁决改变 git_state：立即重拉任务级数据重画面板
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
 * 参考桌面端的 Git 工具 + 进程浮窗：顶栏「任务详情」按钮开合（点任务树的任务行
 * 现在主栏直开任务级详情，检查器入口收拢到按钮/刷新恢复），
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

/* 检查器只属于「列表快捷预览」上下文：设置子页、快捷导航页、新建表单、运行/任务详情一律收起。
 * 详情页已铺开全部信息（蜂巢/步骤/成果/版本/圣经/指挥），检查器留着只会同屏
 * 出现两份标题/成品/Git——让它让位，但不丢选中，回到列表上下文自动滑回 */
function syncInspectorVis() {
  const insp = $("inspector");
  if (!insp) return;
  // 兜底只认「run 记录全部没了」（清理/删除）：排空档不动已开着的检查器，
  // 否则自动续跑的 run 间隙（done→queued→running）会闪关
  const runsGone = S.state && S.inspKey && !((S.state.task_latest || {})[S.inspKey]);
  // 主视图下正在看「要完成什么？」新建表单：不是任务上下文，不自动滑出。
  // （本函数只在模式切换时调用，不在轮询里——用户在表单页手动点树里的任务，
  //   openInspector 直接开，不会被这里关掉）
  const onComposer = !S.mainPage && !document.body.classList.contains("settings-mode") &&
    !$("sub-tasks").classList.contains("hidden");
  // 主栏正开着运行/任务详情：详情页是全功能视图，检查器同屏只会重复
  const onDetail = !$("run-detail").classList.contains("hidden");
  // 主栏停在快捷导航页（概览/运行记录/自动化）：同设置子页，检查器让位不抢版面
  if (document.body.classList.contains("settings-mode") || S.mainPage || onComposer || onDetail ||
      !S.inspKey || runsGone) {
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
  // 已收起（详情页让位/用户关闭）就整帧不落盘：在途响应回来时用户多半已切到
  // 别的详情，落盘只会让 loadArtifacts 等渲染面拿到别人任务的 run（串台源头）
  if (!document.body.classList.contains("inspector-open")) return;
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
      main.innerHTML = '<div class="insp-branch"><code>codebee/' + esc(d.task.id || "") +
        t("（") + esc(t("未创建")) + t("）") + '</code></div>' +
        (lastErr ? '<div class="hint warn">' + esc(lastErr) + "</div>" : "") +
        '<span class="insp-hint">' + esc(t("处理后点「继续任务」，运行会重新检出任务分支。")) + "</span>";
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
  loadArtifacts(runId);   // 成品 TAB：工作目录新产出（运行中轮询刷新文件列表）

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
        '<a class="file-chip" href="' + urlAuth("/api/runs/" + encodeURIComponent(runId) + "/file?name=" +
        encodeURIComponent(f.name)) + '" target="_blank" rel="noopener" title="' +
        esc(f.name + " · " + fmtSize(f.size)) + '" data-file-run="' + esc(runId) +
        '" data-file-name="' + esc(f.name) + '" data-file-size="' + (Number(f.size) || 0) + '">' +
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
/* 编辑态：_fpRawText=开窗时取到的原文（json 展示会美化，编辑始终用原文）；
 * _fpSaveCtx={dir,name,mtime} 非空=该弹窗可编辑（目录文件弹窗专属，成品只读）；
 * _fpDirty=编辑框有改动未保存——关窗/退出编辑先确认，防误丢；
 * _fpCopyText=复制按钮要吐的原文（正文是行号表格，innerText 会连行号列一起带出）；
 * null=非文本内容，回退 innerText。 */
let _fpRawText = "", _fpSaveCtx = null, _fpDirty = false, _fpCopyText = null;

function filePopIsOpen() {
  const el = document.getElementById("file-pop");
  return !!el && !el.classList.contains("hidden");
}

window.filePopClose = async function () {
  const el = document.getElementById("file-pop");
  if (!el) return;
  if (_fpDirty) {
    const ok = await uiConfirm(t("有未保存的修改，确定关闭？"));
    if (!ok) return;
  }
  _fpDirty = false; _fpSaveCtx = null; _fpRawText = ""; _fpCopyText = null;
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
  // 编辑模式下 Ctrl/Cmd+S = 保存（拦掉浏览器默认的「保存网页」）
  el.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
      const ta = el.querySelector(".fp-edit");
      if (ta && !ta.classList.contains("hidden")) { e.preventDefault(); window.fpSaveEdit(); }
    }
  });
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

/* 复制源：文本类正文优先给预留原文（不带行号/着色槽），其余回退 innerText */
window.fpCopySource = function () {
  const body = document.querySelector("#file-pop .fp-body");
  if (!body) return "";
  return _fpCopyText !== null ? _fpCopyText : (body.innerText || "");
};

window.copyFPText = function () {
  try { navigator.clipboard.writeText(window.fpCopySource() || ""); toast(t("已复制")); } catch (e) { /* 剪贴板不可用则忽略 */ }
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
  _fpSaveCtx = null; _fpRawText = ""; _fpDirty = false; _fpCopyText = null;   // diff 弹窗只读，别带上一个目录文件的编辑态
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
  _fpCopyText = lines.length ? lines.join("\n") : "";   // 复制给切片原文，行号槽(.fp-no)不进剪贴板
  _fpSetBody(lines.length
    ? '<div class="fp-diff">' + lines.map((ln, idx) => _fpDiffLine(ln, idx + 1)).join("") + "</div>"
    : '<div class="fp-hint">' + esc(t("暂无该文件的已保存 diff（运行结束后生成变更快照，或该运行没有保存变更内容）。")) + "</div>");
};

const FP_IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg", "avif"]);
const FP_TXT = new Set(["md", "txt", "log", "csv", "yml", "yaml", "ini", "toml", "json",
  "py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "svg", "bat", "sh", "ps1",
  "c", "h", "cpp", "hpp", "java", "go", "rs", "xml", "sql", "mqtt", "proto"]);

function _fpExt(name) { return (String(name).split(".").pop() || "").toLowerCase(); }

/* 按 URL 弹窗预览：图片 blob 直显、md 渲染、文本/代码等宽原文、json 美化、
 * 其余二进制只给下载。成品弹窗与目录文件弹窗共用；目录文件弹窗带 save
 * 上下文 → 额外给「编辑」，改完保存回工作目录（POST /api/dir/save）。 */
async function _fpPreviewUrl(url, name, size, opts) {
  opts = opts || {};
  url = urlAuth(url);   // 预览 fetch 与下载链接都走这个 URL：远程会话在此统一补令牌
  const ext = _fpExt(name);
  _fpSaveCtx = opts.save || null;
  _fpRawText = ""; _fpDirty = false; _fpCopyText = null;
  _fpOpen(String(name).split("/").pop(), size ? "<i>" + esc(fmtSize(size)) + "</i>" : "",
    '<a class="ghost" href="' + url + '" download="' + esc(String(name).split("/").pop()) + '">' + esc(t("下载")) + "</a>");
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
      _fpRawText = text;
      if (_fpSaveCtx) _fpSaveCtx.mtime = r.headers.get("X-Tutti-Mtime") || "";
      let shown = text;
      if (ext === "json") { try { shown = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* 坏 json 原样展示 */ } }
      _fpCopyText = shown;   // 复制=看到的原文；行号列不进剪贴板（编辑/保存仍用 _fpRawText 原文）
      const codeHtml = codeBlockHTML(shown);
      const body = '<div class="fp-code">' + '<div class="fp-vp">' + codeHtml + "</div>" +
        (_fpSaveCtx ? '<textarea class="fp-edit hidden" spellcheck="false"></textarea>' : "") + "</div>";
      _fpSetBody(body);
      if (_fpSaveCtx) {
        el.querySelector(".fp-edit").addEventListener("input", () => { _fpDirty = true; });
      }
      const btns = [];
      if (_fpSaveCtx) btns.push('<button class="ghost fp-b-edit" onclick="fpEnterEdit()">' + esc(t("编辑")) + "</button>");
      btns.push('<button class="ghost" onclick="copyFPText()">' + esc(t("复制")) + "</button>");
      if (_fpSaveCtx) btns.push('<button class="ghost fp-b-save hidden" onclick="fpSaveEdit()">' + esc(t("保存")) + "</button>" +
        '<button class="ghost fp-b-cancel hidden" onclick="fpExitEdit()">' + esc(t("退出编辑")) + "</button>");
      el.querySelector(".fp-acts").insertAdjacentHTML("afterbegin", btns.join(""));
      return;
    }
    _fpSetBody('<div class="fp-hint">' + esc(t("二进制文件不预览，可下载查看。")) + "</div>");
  } catch (e) {
    _fpSetBody('<div class="fp-hint">' + esc(t("内容读取失败：") + (e.message || e)) + "</div>");
  }
}

/* 弹窗编辑模式切换：编辑按钮 ⇄ 保存/退出编辑；预览视图 ⇄ 文本编辑框 */
function _fpToggleActs(editing) {
  const el = _fpEnsure();
  const q = (c) => el.querySelector(".fp-acts " + c);
  if (q(".fp-b-edit")) q(".fp-b-edit").classList.toggle("hidden", editing);
  if (q(".fp-b-save")) q(".fp-b-save").classList.toggle("hidden", !editing);
  if (q(".fp-b-cancel")) q(".fp-b-cancel").classList.toggle("hidden", !editing);
}

window.fpEnterEdit = function () {
  if (!_fpSaveCtx) return;
  const el = _fpEnsure();
  const ta = el.querySelector(".fp-edit");
  if (!ta) return;
  ta.value = _fpRawText;
  el.querySelector(".fp-vp").classList.add("hidden");
  ta.classList.remove("hidden");
  _fpToggleActs(true);
  _fpDirty = false;
  ta.focus();
};

window.fpExitEdit = async function () {
  if (_fpDirty) {
    const ok = await uiConfirm(t("有未保存的修改，确定退出编辑？"));
    if (!ok) return;
  }
  const el = _fpEnsure();
  el.querySelector(".fp-edit").classList.add("hidden");
  el.querySelector(".fp-vp").classList.remove("hidden");
  _fpToggleActs(false);
  _fpDirty = false;
};

window.fpSaveEdit = async function () {
  const el = _fpEnsure();
  const ta = el.querySelector(".fp-edit");
  if (!ta || !_fpSaveCtx) return;
  const btn = el.querySelector(".fp-b-save");
  if (btn) btn.disabled = true;
  try {
    const d = await api("/api/dir/save", { method: "POST", body: JSON.stringify({
      dir: _fpSaveCtx.dir, name: _fpSaveCtx.name, content: ta.value, mtime: _fpSaveCtx.mtime,
    }) });
    _fpRawText = ta.value;
    _fpCopyText = ta.value;
    _fpSaveCtx.mtime = String(d.mtime || "");
    _fpDirty = false;
    // 回到预览层并重画为保存后的内容（同一个框，只换里子）
    el.querySelector(".fp-vp").innerHTML = codeBlockHTML(_fpRawText);
    ta.classList.add("hidden");
    el.querySelector(".fp-vp").classList.remove("hidden");
    _fpToggleActs(false);
    toast(t("已保存"));
  } catch (e) {
    toast(t("保存失败：") + e.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
};

/* 成品文件弹窗预览 */
window.artPopup = async function (runId, name, size) {
  return _fpPreviewUrl("/api/runs/" + encodeURIComponent(runId) + "/file?name=" + encodeURIComponent(name), name, size);
};

/* 对话结果卡「运行展示」：HTML 成品在弹窗里真跑——复用 /preview 只读挂载
 * （令牌走路径段，style.css/script.js 相对子资源照常加载）；拿不到挂载信息
 * （非网页成品/工作目录不在了）回落源码预览，按钮永远不会点了没反应。 */
window.artRunPopup = async function (runId, name, size) {
  const nm = String(name).split("/").pop();
  _fpSaveCtx = null; _fpRawText = ""; _fpDirty = false; _fpCopyText = null;
  const dl = urlAuth("/api/runs/" + encodeURIComponent(runId) + "/file?name=" + encodeURIComponent(name));
  _fpOpen(nm, size ? "<i>" + esc(fmtSize(size)) + "</i>" : "",
    '<a class="ghost" href="' + dl + '" download="' + esc(nm) + '">' + esc(t("下载")) + "</a>");
  let app = null;
  try { app = await api("/api/runs/" + encodeURIComponent(runId) + "/preview"); }
  catch (e) { /* 挂载不可用：走回落 */ }
  if (!filePopIsOpen()) return;
  if (app && app.ok && app.base) {
    _fpSetBody('<iframe class="fp-frame" src="' + esc(urlAuth(app.base + name)) +
      '" title="' + esc(t("运行预览")) +
      '" sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-downloads"></iframe>');
    return;
  }
  return _fpPreviewUrl("/api/runs/" + encodeURIComponent(runId) + "/file?name=" + encodeURIComponent(name), name, size);
};

/* 文件浏览页点文件：中央弹窗预览工作目录里的文件（dir=根目录，name=相对路径）。
 * 带 save 上下文 → 弹窗内可直接编辑并保存回该文件。 */
window.dirFilePopup = async function (dir, name, size) {
  return _fpPreviewUrl("/api/dir/file?dir=" + encodeURIComponent(String(dir).replace(/\\/g, "/")) +
    "&name=" + encodeURIComponent(name), name, size,
    { editable: true, save: { dir: String(dir), name: String(name) } });
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
  // 任务树点任务行：主栏（中间区域）直开任务详情；任务行无内嵌层，整行即按钮；
  // 文件夹行折叠 / 右键菜单不受影响。不在详情准入范围内的行也不允许点了没反应：
  // 无主运行行直开运行详情，归档/没跑过给 toast 说明。
  $("side-tasks").addEventListener("click", (e) => {
    const det = e.target.closest(".stask");
    if (det) sideRowActivate(det);
  });
  $("side-tasks").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (e.target.closest("button, a, input, select, textarea, summary")) return;
    const det = e.target.closest(".stask");
    if (det) { e.preventDefault(); sideRowActivate(det); }
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
 * fc-row：文件行 + 弹窗预览按钮同行排布，避免按钮独占一行参差不齐。
 * 点文件名与点「预览」同款 artPopup 弹窗，行为一致不 surprise。 */
function artifactsChips(runId, files) {
  return files.map((f) => {
    const isTxt = FP_TXT.has(_fpExt(f.name));   // 文本/代码都有弹窗预览（按代码格式展示）
    return '<span class="fc-row">' +
      '<a class="file-chip artifact-file-open" href="' + urlAuth("/api/runs/" + encodeURIComponent(runId) + "/file?name=" +
      encodeURIComponent(f.name)) + '" target="_blank" rel="noopener" ' +
      'title="' + esc(f.name + " · " + fmtSize(f.size)) + '" data-file-run="' + esc(runId) + '" data-file-name="' + esc(f.name) + '" data-file-size="' + (Number(f.size) || 0) + '">' +
      '<i class="fx">' + esc(_fpExt(f.name).slice(0, 4) || "file") + "</i>" +
      '<span class="p">' + esc(f.name) + "</span><i>" + fmtSize(f.size) + "</i></a>" +
      (isTxt ? '<a class="file-chip prev artifact-file-preview" title="' + esc(t("查看内容")) +
        '" data-file-run="' + esc(runId) + '" data-file-name="' + esc(f.name) + '" data-file-size="' + (Number(f.size) || 0) + '">' +
        '<svg class="ico" aria-hidden="true"><use href="#i-book"/></svg>' + t("预览") + "</a>" : "") +
      "</span>";
  }).join("");
}

async function loadArtifacts(runId) {
  // 双入口同源渲染：检查器「成品文件」TAB + 主栏详情「成果」分区。
  let d;
  try { d = await api("/api/runs/" + encodeURIComponent(runId) + "/files"); }
  catch (e) { return; }
  // 成果分区/徽章只吃当前详情目标的 run：详情开着但 runId 是别的任务（检查器
  // 后台跟刷旧选中）时，绝不能把别家的文件写进主栏——那是肉眼可见的串台
  const detailOpen = !$("run-detail").classList.contains("hidden");
  const detailOwns = detailOpen &&
    (S.detailRunId === runId ||
     (S.detailTaskKey && S.lastRun && S.lastRun.id === runId));
  // 检查器入口只在「开着且 runId 仍是检查器当前 run」时认领；两边都不认 → 过期响应丢弃
  const inspOpen = document.body.classList.contains("inspector-open") && S.inspKey !== null;
  const inspOwns = inspOpen &&
    runId === ((S.inspData && S.inspData.run && S.inspData.run.id) || "");
  if (!detailOwns && !inspOwns) return;
  const files = d.files || [];
  const box = inspOwns ? $("insp-artifacts") : null;
  const mainBox = detailOwns ? $("rd-arts") : null;
  // 成果分区徽章：文件数（终态才有产出，运行中不计）；只随 detailOwns 一起刷
  const fb = detailOwns ? document.querySelector('#rd-tabs .rd-tab[data-tab="result"] .rd-badge') : null;
  if (fb) { fb.textContent = files.length ? String(files.length) : "";
    fb.className = "rd-badge" + (files.length ? "" : " hidden"); }
  if (!files.length) {
    if (box) box.innerHTML = '<span class="insp-hint">' + esc(t("本次运行没有在工作目录里产出新文件。")) + "</span>";
    if (mainBox) { mainBox.classList.add("hidden"); mainBox.innerHTML = ""; }
    return;
  }
  const head = '<div class="files-head"><span class="sec-title">' + t("成品文件") + '</span>' +
    '<span class="wd" title="' + esc(t("点击复制")) + '" onclick="copyText(this.textContent)">' + esc(d.workdir) + "</span></div>";
  const chips = artifactsChips(runId, files);
  if (box) box.innerHTML = head + '<div class="file-chips">' + chips + "</div>";
  if (mainBox) { mainBox.classList.remove("hidden");
    mainBox.innerHTML = head + '<div class="file-chips">' + chips + "</div>"; }
}

/* ---------------- 网页成品预览（借鉴对话式编程产品的预览窗） ----------------
 * 代码任务跑完，用户要的是「看到东西跑起来」，而不是逐个点开文件读代码。
 * 左「运行」= 入口 HTML 的 iframe（后端 /preview/<run>/<令牌>/ 只读挂载工作
 * 目录，令牌走路径段让 style.css / script.js 这些相对子资源也带上）；
 * 右各文件页签 = 源码视图（复用 codeBlockHTML：行号+高亮，与成果预览同款）。
 * 「刷新」重挂 iframe（改完文件不用退出详情页），「打开新窗口」给真全屏。
 * 页签在无网页成品时整体隐藏（见 rdTabAvail），不打扰小说/普通任务。 */
let pvRunId = null;      // 当前预览归属的 run（换 run 必须重挑入口与页签）
let pvApp = null;        // 后端 /api/runs/<id>/preview 的返回（入口 + 文件清单 + base）
let pvView = "run";      // "run" = 运行视图；否则是文件名
let pvCodeSig = "";      // 代码视图去抖签名（轮询重画不重取同一个文件）

function pvReset() {
  pvRunId = null; pvApp = null; pvView = "run"; pvCodeSig = "";
}

/* 预览 iframe 的地址：base 已带令牌与尾部斜杠，相对路径交给 <base href> 解析 */
function pvRunUrl() {
  if (!pvApp || !pvApp.base) return "";
  return urlAuth(pvApp.base + (pvApp.entry || ""));
}

function pvTabsHTML() {
  const f = (pvApp && pvApp.files) || [];
  const mk = (key, label, title) =>
    '<button class="pv-tab' + (pvView === key ? " on" : "") + '" role="tab" ' +
    'aria-selected="' + (pvView === key ? "true" : "false") + '" data-pv="' + esc(key) + '" ' +
    'title="' + esc(title || label) + '">' + esc(label) + "</button>";
  let h = mk("run", t("运行视图"), t("实时运行入口页面"));
  for (const it of f) h += mk(it.name, it.name.split("/").pop(), it.name);
  return h;
}

function pvBodyHTML() {
  if (!pvApp || !pvApp.ok) return "";
  if (pvView === "run") {
    // 每次重挂 iframe 都换 src 尾参，绕开浏览器缓存：用户点「刷新」就是要看新代码
    const src = pvRunUrl();
    return '<iframe class="pv-frame" id="rd-pv-frame" src="' + esc(src) +
      '" title="' + esc(t("运行预览")) + '" sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-downloads"></iframe>';
  }
  return '<div class="pv-code"><div class="fp-vp" id="rd-pv-code">' +
    '<div class="fp-hint">' + esc(t("正在读取文件…")) + "</div></div></div>";
}

async function pvLoadCode(name) {
  const el = $("rd-pv-code");
  if (!el) return;
  const url = "/api/runs/" + encodeURIComponent(pvRunId) + "/file?name=" + encodeURIComponent(name);
  try {
    const r = await fetch(urlAuth(url), { headers: authHeaders() });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const text = await r.text();
    if (!el.isConnected || pvView !== name) return;   // 拉取期间切走了：过期响应不落盘
    el.innerHTML = codeBlockHTML(text);
  } catch (e) {
    if (el.isConnected && pvView === name)
      el.innerHTML = '<div class="fp-hint">' + esc(t("内容读取失败：") + (e.message || e)) + "</div>";
  }
}

function pvDraw() {
  const box = $("rd-preview");
  if (!box) return;
  if (!pvApp || !pvApp.ok) {
    box.classList.add("hidden");
    // iframe 一并摘掉：hidden 只是不可见，留在 DOM 里旧页面还在跑（定时器/
    // 音频不会停）；换成无预览的 run 时更不能留上一家的残影
    const body = $("rd-pv-body");
    if (body) body.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  $("rd-pv-tabs").innerHTML = pvTabsHTML();
  const open = $("rd-pv-open");
  if (open) open.href = urlAuth(pvRunUrl());
  $("rd-pv-body").innerHTML = pvBodyHTML();
  const hint = $("rd-pv-hint");
  if (hint) {
    // 工作目录路径给出来：预览的是哪个目录一目了然（与「成果」同款可点复制）
    hint.innerHTML = '<span data-i18n-x>' + esc(t("预览工作目录：")) + "</span>" +
      '<span class="pv-wd" title="' + esc(t("点击复制")) + '" onclick="copyText(this.textContent)">' +
      esc(pvApp.workdir || "") + "</span>";
    hint.classList.remove("hidden");
  }
  if (pvView !== "run") { pvCodeSig = pvView; pvLoadCode(pvView); }
}

window.pvGo = function (view) {
  pvView = view;
  pvCodeSig = "";
  pvDraw();
};
window.pvReload = function () {
  // 重挂 iframe：加时间戳绕缓存；代码视图则重取当前文件
  if (pvView === "run") {
    const f = $("rd-pv-frame");
    if (f) f.src = urlAuth(pvRunUrl()) + (String(pvRunUrl()).includes("?") ? "&" : "?") + "_=" + Date.now();
  } else {
    pvLoadCode(pvView);
  }
};

async function renderPreview(runId) {
  const box = $("rd-preview");
  if (!box) return;
  if (!runId) { box.classList.add("hidden"); rdTabsSync(); return; }
  if (pvRunId !== runId) { pvRunId = runId; pvApp = null; pvView = "run"; pvCodeSig = ""; }
  let d = null;
  try { d = await api("/api/runs/" + encodeURIComponent(runId) + "/preview"); }
  catch (e) { d = null; }
  // 过期闸门：拉取期间切了详情就丢弃响应（同 loadArtifacts 的双入口闸门）
  if (pvRunId !== runId) return;
  if (!$("run-detail") || $("run-detail").classList.contains("hidden")) { rdTabsSync(); return; }
  pvApp = d || { ok: false };
  const badge = document.querySelector('#rd-tabs .rd-tab[data-tab="preview"] .rd-badge');
  if (badge) {
    const n = pvApp && pvApp.ok ? (pvApp.files || []).length : 0;
    badge.textContent = n ? String(n) : "";
    badge.className = "rd-badge" + (n ? "" : " hidden");
  }
  pvDraw();
  rdTabsSync();   // 预览可用性（rdTabAvail 认 #rd-preview 的显隐）随之更新
}

window.copyText = function (t) {
  // writeText 返回 promise，无头/非安全上下文会拒绝——必须挂 catch，否则
  // 未处理的 promise 拒绝会以控制台错误冒出来（测试断言 0 错误会被它打爆）
  try {
    const p = navigator.clipboard.writeText(t);
    if (p && p.catch) p.catch(() => { /* 剪贴板不可用则忽略 */ });
  } catch (e) { /* 剪贴板不可用则忽略 */ }
};

let currentLog = null; // { runId, rel }：同一路径在不同轮次也必须能切换

/* 当前打开的日志是否仍属于这批步骤（重画后日志路径消失说明步骤被重建，才收起） */
function stepsMatch(runs, current) {
  if (!current) return false;
  return (runs || []).some((r) => String(r.id || "") === String(current.runId || "") &&
    (r.steps || []).some((s) => s.log === current.rel));
}

/* 从章稿文件名取全书章号：chapter-07.md → 7；非章稿（或解析不出）返回 0。
 * 只认「chapter-数字.md」这一约定（与后端 _CH_FILE_RE 一致）。 */
function chapterNumOf(name) {
  const base = String(name || "").split("/").pop();
  if (!/^chapter-\d{1,4}\.md$/i.test(base)) return 0;
  const digits = base.slice("chapter-".length, -3);
  const n = parseInt(digits, 10);
  return n > 0 ? n : 0;
}

/* 分卷规划表（前端只读副本）：把任务的 serial 分卷配置还原成
 * [{vol, title, first, last}]，供发布页按卷分组列章。后端 volumes.py 是唯一
 * 真源（写作与成书都走它）；这里只做「列清单」这一用途的最小推导，取不到
 * 分卷配置时返回 []（调用方退化为平铺列表）。
 *   serial.volumes        显式卷表：[{title, chapters|start/end}]（优先）
 *   serial.volume_chapters 每卷章数：等长切卷（兜底）
 * last=null 表示开放到书末。 */
function volumePlanOf(task, upto) {
  const s = (task || {}).serial || {};
  const spec = Array.isArray(s.volumes) ? s.volumes : [];
  let per = parseInt(s.volume_chapters, 10);
  if (!(per >= 2)) per = 0;
  if (!spec.length && !per) return [];
  const plan = [];
  let prevLast = 0;
  const push = (title, first, last) => plan.push({ vol: plan.length + 1, title: title || "", first: first, last: last });
  for (const it of spec) {
    if (!it) continue;
    const first = (it.start > 0) ? it.start : prevLast + 1;
    let last = null;
    if (it.end > 0) last = it.end;
    else if (it.chapters > 0) last = first + it.chapters - 1;
    if (last != null && last < first) last = first;
    push(it.title, first, last);
    if (last == null) return plan;     // 开放卷：后面不再有卷
    prevLast = last;
  }
  // 显式卷表用尽后按每卷章数（或末卷长度）续卷，保证覆盖到 upto（与后端
  // volumes.build_plan 同规则）——否则第 21 章起会掉进「其他文件」分组。
  const fb = per || (plan.length && plan[plan.length - 1].last != null
    ? plan[plan.length - 1].last - plan[plan.length - 1].first + 1 : 0);
  if (fb > 0) {
    let cursor = prevLast + 1;
    const limit = (upto > 0) ? upto : (plan.length ? prevLast : fb);
    while (cursor <= limit && plan.length < 200) {
      push("", cursor, cursor + fb - 1);
      cursor += fb;
    }
  }
  return plan;
}


/* 连载章节评审卡：每章均分/是否达标/字数 + 逐项目标审稿结果（event_check，
 * 借鉴 AI-Novel-Writer：大纲要点逐项判定+正文证据）。非连载 run 无
 * chapter_scores 保持隐藏。
 * 分卷：章节卡按卷分组显示（卷名来自 run.volumes / chapter_scores[].vol_title），
 * 不分卷时退化为原来的平铺列表，视觉零变化。 */
function renderChapterScores(run) {
  const box = $("rd-chapters");
  if (!box) return;
  const cs = run && run.chapter_scores;
  if (!Array.isArray(cs) || !cs.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  // 卷名表：优先 run.volumes（全书卷规划），退到章节自带的 vol_title
  const volTitles = {};
  (run.volumes || []).forEach((v) => { volTitles[v.vol] = v.title || ""; });
  cs.forEach((c) => {
    if (c.vol && c.vol_title && !volTitles[c.vol]) volTitles[c.vol] = c.vol_title;
  });
  const rowHtml = (c) => {
    const means = c.means || {};
    const dims = Object.keys(means).map((d) =>
      '<span class="ch-dim' + (means[d] >= 7 ? "" : " low") + '">' + esc(d) + " " + means[d] + "</span>").join("");
    const ev = (c.event_check || []).map((line) => {
      const ok = /已完成/.test(line), bad = /未完成/.test(line);
      return '<div class="ch-ev' + (ok ? " ok" : bad ? " bad" : "") + '">' + esc(line) + "</div>";
    }).join("");
    return '<div class="ch-row' + (c.passed ? "" : " fail") + '">' +
      '<div class="ch-head"><b>' + esc(t("第") + " " + c.chapter + " " + t("章")) + '</b>' +
      '<span class="ch-title">' + esc(c.title || "") + "</span>" +
      '<span class="tag">' + (c.passed ? t("达标") : t("未达标")) + "</span>" +
      (c.reused ? '<span class="tag">' + t("沿用") + "</span>" : "") +
      '<span class="ch-meta">' + Number(c.words || 0) + t(" 字") +
      (c.rounds > 1 ? " · " + c.rounds + t(" 轮") : "") + "</span></div>" +
      (dims ? '<div class="ch-dims">' + dims + "</div>" : "") +
      (ev ? '<div class="ch-evs"><span class="hint">' + t("逐项目标核对：") + "</span>" + ev + "</div>" : "") +
      "</div>";
  };
  const head = '<h3 class="sec-title">' + t("章节评审") +
    '<span class="tag">' + cs.filter((c) => c.passed).length + "/" + cs.length + " " + t("达标") + "</span></h3>";
  const hasVol = cs.some((c) => c.vol);
  if (!hasVol) { box.innerHTML = head + cs.map(rowHtml).join(""); return; }
  // 按卷分组（章号顺序，卷内保持原序）
  const groups = [];
  cs.forEach((c) => {
    const v = c.vol || 0;
    let g = groups.find((x) => x.vol === v);
    if (!g) { g = { vol: v, rows: [] }; groups.push(g); }
    g.rows.push(c);
  });
  box.innerHTML = head + groups.map((g) => {
    const title = volTitles[g.vol] || "";
    const nPass = g.rows.filter((c) => c.passed).length;
    const label = g.vol ? (t("第") + " " + g.vol + " " + t("卷") + (title ? " " + t("《") + title + t("》") : "")) : t("未分卷");
    return '<div class="ch-vol"><div class="ch-vol-head"><b>' + esc(label) + "</b>" +
      '<span class="tag">' + nPass + "/" + g.rows.length + " " + t("达标") + "</span>" +
      '<span class="ch-meta">' + t("第 ") + g.rows[0].chapter + "–" +
      g.rows[g.rows.length - 1].chapter + t(" 章") + "</span></div>" +
      g.rows.map(rowHtml).join("") + "</div>";
  }).join("");
}

function renderPlan(run) {
  const box = $("rd-plan");
  const plan = run.plan;
  if (!plan || !plan.steps || !plan.steps.length) { box.classList.add("hidden"); return; }
  const route = run.route || {};
  const routeHtml = Object.keys(route).length
    ? '<div class="route">' + t("路由依据：") +
      Object.keys(route).map((k) => "<b>" + esc(k) + "</b> " + esc(route[k])).join(t("　|　")) + "</div>"
    : "";
  box.classList.remove("hidden");
  box.innerHTML = '<h3 class="sec-title">' + t("编排计划 ") + '<span class="tag">' + t("来源 ") + esc(plan.source || "?") + "</span></h3>" +
    '<div class="steps">' + plan.steps.map((s, i) =>
      '<div class="step plan"><span class="n">' + String(i + 1).padStart(2, "0") + "</span>" +
      '<span class="role">' + esc(s.title || "") + "</span>" +
      '<span class="sum">' + esc(s.detail || "") + "</span></div>").join("") + "</div>" + routeHtml;
}

/* 运行详情的统一调度审计：任务画像和实际选路来自同一次编译结果。
 * 旧运行没有 task_spec/route_plan 时保持隐藏，避免把历史数据伪装成当前决策。 */
function renderRouting(run) {
  let box = $("rd-routing");
  if (!box) {
    const steps = $("rd-steps");
    if (!steps || !steps.parentNode) return;
    box = document.createElement("div");
    box.id = "rd-routing";
    box.className = "rd-routing hidden";
    steps.parentNode.insertBefore(box, steps);
  }
  const spec = run && run.task_spec;
  const plan = run && run.route_plan;
  if (!spec || !plan) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const type = spec.type || "direct";
  const difficulty = spec.difficulty || "default";
  const dimension = spec.dimension || "reasoning";
  const caps = Array.isArray(spec.capabilities) ? spec.capabilities.filter(Boolean) : [];
  const typeLabel = ((flowById(type) || {}).name) || type;
  const difficultyLabel = t(({ easy: "简单", default: "标准", hard: "困难" })[difficulty] || difficulty);
  const dimensionLabel = t(({ coding: "编码", writing: "写作", reasoning: "推理", vision: "视觉" })[dimension] || dimension);
  const capLabels = {
    coding: "编码", writing: "写作", reasoning: "推理", vision: "视觉",
    filesystem: "文件读写", verification: "验证", long_context: "长上下文",
    continuity: "连续性", attachments: "附件",
  };
  const capsText = caps.length ? caps.map((x) => t(capLabels[x] || x)).join(t("、")) : t("通用能力");
  const selectedLabel = (entry) => entry && (entry.label || entry.agent_id || "") || t("未选定");
  const roleBlock = (key, title) => {
    const route = plan[key] || {};
    const candidates = Array.isArray(route.candidates) ? route.candidates : [];
    if (!route.selected && !candidates.length) return "";
    const selected = candidates.find((x) => x.agent_id === route.selected) || candidates[0];
    const participants = Array.isArray(route.participants) ? route.participants : [];
    const activeIds = participants.length ? participants.slice() : (route.selected ? [route.selected] : []);
    if (route.selected && activeIds.includes(route.selected)) {
      activeIds.splice(activeIds.indexOf(route.selected), 1);
      activeIds.unshift(route.selected);
    }
    const activeLabels = activeIds.map((id) => {
      const item = candidates.find((x) => x.agent_id === id);
      return selectedLabel(item || { agent_id: id });
    }).filter(Boolean);
    const fallback = Array.isArray(route.fallback) ? route.fallback : [];
    const fallbackLabels = fallback.map((id) => {
      const item = candidates.find((x) => x.agent_id === id);
      return selectedLabel(item || { agent_id: id });
    }).filter(Boolean);
    const rows = candidates.map((x) =>
      '<div class="rd-route-row"><span class="rd-route-score">' + esc(Number(x.score || 0).toFixed(1)) +
      '</span><span class="rd-route-agent">' + esc(selectedLabel(x)) + '</span><span class="rd-route-reason">' +
      esc(x.reason || "") + '</span></div>').join("");
    return '<div class="rd-route-role"><div class="rd-route-role-head"><b>' + esc(title) +
      '</b><span>' + esc(activeLabels.join(" + ") || selectedLabel(selected)) + '</span></div>' +
      (route.selection_reason ? '<div class="rd-route-selection">' + esc(route.selection_reason) + '</div>' : "") +
      (fallbackLabels.length ? '<div class="rd-route-fallback">' + esc(t("降级：")) + esc(fallbackLabels.join(" → ")) + '</div>' : "") +
      (rows ? '<details class="rd-route-candidates"><summary>' + esc(t("查看候选评分")) + '</summary>' + rows + '</details>' : "") +
      '</div>';
  };
  const roles = [roleBlock("implement", t("实现")), roleBlock("review", t("评审"))].filter(Boolean).join("");
  if (!roles) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  box.classList.remove("hidden");
  box.innerHTML = '<div class="rd-routing-head"><span class="rd-routing-title">' + esc(t("任务画像与调度")) +
    '</span><span class="rd-routing-spec">' + esc(t("{0} · {1} · {2}", t(typeLabel), difficultyLabel, dimensionLabel)) + '</span></div>' +
    '<div class="rd-routing-caps"><span>' + esc(t("需要：")) + '</span>' + esc(capsText) + '</div>' +
    '<div class="rd-route-roles">' + roles + '</div>';
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

/* 复制抽屉日志全文：报错 JSON 动辄几十行还带横向滚动，手动圈选很折磨 */
window.rdLogCopy = function () {
  const pre = $("rd-log-text");
  const text = ((pre && pre.textContent) || "").trim();
  const idle = [t("（等待输出…）"), t("（无输出）")];
  if (!text || idle.includes(text)) return;
  const done = () => toast(t("已复制"));
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else fallbackCopy(text, done);
};

/* 抽屉标题：按 (runId, 日志路径) 反查步骤角色与执行者——不知道在看谁的日志，
 * 抽屉就成了无名黑框 */
function rdLogStepLabel(runId, rel) {
  const pools = [];
  if (S.lastRun && S.lastRun.id === runId) pools.push(S.lastRun.steps || []);
  if (S.state && S.state.runs) {
    const r = S.state.runs.find((x) => x.id === runId);
    if (r) pools.push(r.steps || []);
  }
  for (const steps of pools) {
    const s = steps.find((x) => x.log === rel);
    if (s) return [s.role || "", s.agent_label || s.agent || ""].filter(Boolean).join(" · ");
  }
  return rel || "";
}

/* 直连任务的输入框只在日志抽屉真正打开时才让出底部空间。
 * 日志和聊天 pane 位于同一个主区，不能靠普通兄弟选择器反向联动，
 * 所以用详情根节点的状态类做显式同步。 */
function syncChatLogSpace(open) {
  const detail = $("run-detail");
  if (detail) detail.classList.toggle("chat-log-open", !!open);
}

window.rdLogClose = function () {
  const box = $("rd-log");
  syncChatLogSpace(false);
  if (!box) return;
  box.classList.add("hidden");
  currentLog = null;
  stopLogLive();
  const st = $("rd-log-step");
  if (st) st.textContent = "";
};

async function toggleLog(runId, rel) {
  const box = $("rd-log"), pre = $("rd-log-text");
  if (currentLog && currentLog.runId === runId && currentLog.rel === rel && !box.classList.contains("hidden")) {
    window.rdLogClose(); return;
  }
  try {
    const r = await api("/api/runs/" + encodeURIComponent(runId) + "/log?step=" + encodeURIComponent(rel) + "&pretty=1");
    // 拉取期间可能切详情：过期日志不落盘（A 的步骤输出写进 B 详情的抽屉）。
    // 检查器蜂巢/步骤点开日志同款：run 属检查器当前 run 也算在场
    const owned = S.detailRunId === runId ||
      // 任务级直连时间线会跨多轮 run 回放；按钮上的 run 可能不是最新一轮，
      // 只按 latest.id 判断会让旧轮次的「CLI 日志」点击后悄悄失效。
      (S.detailTaskKey && S.lastRun && S.lastRun.task_id === S.detailTaskKey) ||
      ((S.inspData || {}).run || {}).id === runId;
    if (!owned) return;
    // 占位符跟状态走：运行中才「等待输出」，终态没产出就是「无输出」——
    // 否则内置合成器这类不落 CLI 日志的步骤结束后，一直挂「等待输出…」
    // 假装还在产字（2026-09-22 merge 卡已结束却等待输出案）
    pre.textContent = r.log ||
      (r.step_status === "running" ? t("（等待输出…）") : t("（无输出）"));
    box.classList.remove("hidden");
    syncChatLogSpace(true);
    currentLog = { runId, rel };
    const st = $("rd-log-step");
    if (st) st.textContent = rdLogStepLabel(runId, rel);
    pre.scrollTop = pre.scrollHeight;   // 打开即看最新输出
    // 输出是流式写入的：运行中点开就持续刷新；步骤结束（后端带 step_status）即停
    const live = r.step_status === "running";
    logLiveBadge(live);
    stopLogLive();
    if (!live) return;
    S.logLive = setInterval(async () => {
      if (!currentLog || currentLog.runId !== runId || currentLog.rel !== rel) return stopLogLive();
      try {
        const rr = await api("/api/runs/" + encodeURIComponent(runId) + "/log?step=" + encodeURIComponent(rel) + "&pretty=1");
        if (currentLog && currentLog.runId === runId && currentLog.rel === rel) {
          // 贴底跟随：用户滚到底部附近才自动滚到最新输出，回看历史不打扰
          const stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48;
          // 最后一帧常在步骤翻终态后才拉到：此刻仍无输出就该换「无输出」
          pre.textContent = rr.log ||
            (rr.step_status === "running" ? t("（等待输出…）") : t("（无输出）"));
          if (stick) pre.scrollTop = pre.scrollHeight;
          logLiveBadge(true);   // 每帧都打时间戳：内容没变也能证明通道活着
          if (rr.step_status && rr.step_status !== "running") {
            logLiveBadge(false);
            stopLogLive();      // 步骤终态：拉完最后一帧就停
          }
        }
      } catch (e) { /* 网络抖动保留上一帧 */ }
    }, 2500);
  } catch (e) { pre.textContent = t("日志读取失败: ") + e.message; box.classList.remove("hidden");
    syncChatLogSpace(true);
    const st = $("rd-log-step"); if (st) st.textContent = rdLogStepLabel(runId, rel);
    logLiveBadge(false); }
}
/* ---------------- 蜂巢工作台：每个智能体一格，点开即看实时输出 ---------------- */
let hiveTimer = null;                    // 卡片尾巴轮询表
let hiveClock = null;                    // running 秒表（1s 走动）
const hiveTails = {};                    // "runid|rel" -> 最近一行输出缓存
const hiveThink = {};                    // "runid|rel" -> 最近思考片段（【思考】行提取）

function stopHiveTick() { if (hiveTimer) { clearInterval(hiveTimer); hiveTimer = null; } }

/* run.steps -> 阶段泳道流水线：规划→起草→评审→修订→打磨→合成，先后关系一眼可见；
 * 运行中蜜蜂摆动+秒表走动+蜜光呼吸；彗尾光点流向下一阶段；点格即看实时输出；
 * 入场瀑布只在步骤结构真变时重播（签名闸门，轮询空转不重建 DOM）。 */
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

/* 尾巴去噪：codex 遥测 WARN/折叠标记/半行 JSON 不是"它在干什么"；翻译后的【消息】行是正文 */
function hiveCleanLine(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = String(lines[i] || "").trim();
    if (!l) continue;
    if (/warn\b|telemetry|metrics|failed to flush|mcp|已折叠/i.test(l)) continue;
    // 二进制/UTF-16 残渣与乱码墙不配当「它在干什么」（claude 流里实测出现过
    // \u0000 夹正文的残渣行，展示出来就是一串占位符）
    if ((l.match(/\x00|\\u0000/gi) || []).length >= 2) continue;
    if ((l.match(/\ufffd/g) || []).length > 6) continue;
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

/* 思考片段：pretty 日志里最近的【思考】/思考心跳行——运行中卡片的一条
 * 「💭 …」弱化行，让用户不用点开日志也能看到模型在想什么。没有思考行
 * （qwen/mimo 等一次性输出）返回空，卡片不多占一行。 */
function hiveThinkLine(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = String(lines[i] || "").trim();
    if (!l) continue;
    if (l.startsWith("【思考】")) return l.slice(0, 220);
    if (/^— 思考中（心跳/.test(l)) return l;
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
  if (sub) {
    const done = steps.filter((s) => ["done", "completed", "success"].includes(String(s.status || "").toLowerCase())).length;
    const issues = steps.filter((s) => ["failed", "timeout", "cancelled"].includes(String(s.status || "").toLowerCase())).length;
    sub.innerHTML = '<span class="hive-count hive-count-live"><i class="live-dot"></i>' +
      esc(t("在岗")) + " " + running.length + '</span>' +
      '<span class="hive-count hive-count-done">✓ ' + done + '</span>' +
      (issues ? '<span class="hive-count hive-count-issue">! ' + issues + '</span>' : "") +
      '<span class="hive-count hive-count-total">' + steps.length + ' ' + esc(t("步骤")) + '</span>' +
      '<span class="hive-hint">' + esc(t("点击步骤卡片查看 CLI 日志")) + '</span>';
  }
  const activeIdx = lanes.map((l) => byStage[l].some((s) => s.status === "running")).lastIndexOf(true);
  const cell = (s, laneIdx, cellIdx) => {
    // 五归一：取消/超时的格子不再冒充「完成」——与步骤芯片同三色体系
    const st = s.status === "running" || s.status === "queued" ? "running"
      : s.status === "failed" ? "failed"
      : s.status === "cancelled" ? "cancelled"
      : s.status === "timeout" ? "timeout" : "done";
    const who = s.agent_label || s.agent || "";
    // 当前使用的模型：起跑即有（解析到的链首），收尾以实际使用覆盖；空则不显示
    const model = String(s.model || "").trim();
    const whoShow = who + (model ? " · " + model : "");
    // 完成即给结论：终态格子定格在 summary（智能体最终回答/失败原因），
    // 悬停 title 展示全文；运行中格子才跟实时尾巴
    const concl = String(s.summary || "").replace(/\s+/g, " ").trim();
    const key = run.id + "|" + (s.log || "");
    if (st !== "running") { delete hiveTails[key]; delete hiveThink[key]; }
    const tailText = st === "running" ? (hiveTails[key] || concl) : concl;
    // 思考行（仅运行中）：内置智能体步骤自带 s.thinking（流式落盘），CLI 步骤
    // 由 hiveTick 从 pretty 日志提取【思考】行；终态格子的结论已定格，不再补
    const thinkText = st === "running"
      ? (hiveThink[key] ||
         (s.thinking ? "💭 " + String(s.thinking).replace(/\s+/g, " ").trim().slice(-180) : ""))
      : "";
    const title = [s.role, whoShow,
                   (s.duration_s != null ? s.duration_s + "s" : ""),
                   s.started_at ? t("开始于 ") + s.started_at : "",
                   (s.note ? "◆ " + s.note : "")].filter(Boolean).join(" · ")
      + (concl && st !== "running" ? "\n" + t("结论：") + concl : "");
    // --d：入场瀑布逐格延迟（泳道间 110ms + 同泳道逐格 45ms），样式端消费
    return '<div class="hive-cell st-' + st + (st === "running" ? " hc-breathe" : "") + '" role="button" tabindex="0"' +
      ' data-hive-run="' + esc(run.id) + '" data-hive-log="' + esc(s.log || "") + '"' +
      ' style="--d:' + (laneIdx * 110 + cellIdx * 45) + 'ms"' +
      ' title="' + esc(title) + '" ' +
      '>' +
      '<div class="hc-head">' +
      '<img class="hc-bee" src="icons/bee.svg" alt="" aria-hidden="true">' +
      '<span class="hc-role">' + esc(s.role || "") + "</span>" +
      '<span class="hc-who">' + esc(whoShow) + "</span></div>" +
      '<div class="hc-tail" data-log="' + esc(s.log || "") + '">' +
      esc(tailText) + "</div>" +
      // 运行中始终渲染 hc-think（空时高度为 0）：hiveTick 拉到思考片段后
      // 要有元素可写；终态格子连元素都不留
      (st === "running" ? '<div class="hc-think" data-think="' + esc(s.log || "") + '">' + esc(thinkText) + "</div>" : "") +
      '<div class="hc-meta"><span class="hc-elapsed" data-started="' + esc(s.started_at || "") + '">' +
      (s.duration_s != null ? s.duration_s + "s" : hiveElapsed(s.started_at)) + "</span>" +
      (st === "running" ? '<span class="hc-live"><i class="live-dot"></i>' + t("工作中") + "</span>" : "") +
      (st === "timeout" ? '<span class="hc-dead">⏱ ' + t("超时") + "</span>" : "") +
      (st === "cancelled" ? '<span class="hc-dead">' + t("已取消") + "</span>" : "") +
      (s.log ? '<span class="hc-log-link">' + esc(t("CLI 日志")) + "</span>" : "") +
      "</div></div>";
  };
  const dots = (list, laneIdx) => list.slice(-12).map((s, di) => {
    const dc = s.status === "running" ? "run" : s.status === "failed" ? "fail"
      : s.status === "cancelled" ? "cancel" : s.status === "timeout" ? "time" : "done";
    const stx = { running: t("运行中"), failed: t("失败"), cancelled: t("已取消"),
                  timeout: t("超时"), done: t("完成") }[dc] || s.status;
    // --d：与格子同款级联延迟——入场时整条轨道按执行顺序"哗"地点亮（进度回放感）
    return '<i class="ld ld-' + dc + '" style="--d:' + (laneIdx * 110 + di * 45) + 'ms"' +
      ' title="' + esc((s.role || "") + " · " + stx) + '"></i>';
  }).join("");
  $("rd-hive-cells").innerHTML = lanes.map((stage, i) => {
    const list = byStage[stage];
    const hasRun = list.some((s) => s.status === "running");
    // 无 running（终态回看）：全部视为已完成泳道；有 running 时 activeIdx 之前算完成
    const cls = hasRun ? "lane-active"
      : (activeIdx === -1 || i < activeIdx ? "lane-done" : "lane-idle");
    // 泳道头视觉 v2：阶段序号徽章（01/02…流水线站序）+ 已完成进度（settled/total）
    const settled = list.filter((s) => !["running", "queued"].includes(s.status)).length;
    const meter = list.length ? Math.round((settled / list.length) * 100) : 0;
    return '<div class="hive-lane ' + cls + '">' +
      '<div class="lane-head">' +
      '<span class="lane-idx">' + String(i + 1).padStart(2, "0") + "</span>" +
      '<span class="lane-name">' + esc(stage) + '</span>' +
      '<span class="lane-n">×' + list.length + "</span>" +
      (settled ? '<span class="lane-prog">' + settled + "/" + list.length + "</span>" : "") +
      '<span class="lane-meter" aria-label="' + meter + '%"><i style="width:' + meter + '%"></i></span>' +
      (hasRun ? '<span class="lane-live"><i class="live-dot"></i>' + t("进行中") + "</span>" : "") + "</div>" +
      '<div class="lane-track">' + dots(list, i) + "</div>" +
      '<div class="lane-cells">' + list.slice(-8).map((s, ci) => cell(s, i, ci)).join("") + "</div></div>";
  }).join('<div class="lane-flow" aria-hidden="true">'
    + '<svg class="flow-line flow-line-down" viewBox="0 0 36 44" aria-hidden="true">'
    + '<path class="flow-path" d="M14 2 C14 16 22 28 22 42"/><path class="flow-tip" d="M16 36 L22 43 L28 36"/></svg>'
    + '<svg class="flow-line flow-line-up" viewBox="0 0 36 44" aria-hidden="true">'
    + '<path class="flow-path" d="M22 2 C22 16 14 28 14 42"/><path class="flow-tip" d="M8 36 L14 43 L20 36"/></svg>'
    + '<span class="flow-bee"><img src="icons/bee.svg" alt="" aria-hidden="true"></span>'
    + '</div>');
  // 入场瀑布闸门：只按"结构"（泳道数/格数/状态构成）签名；运行中尾巴/秒表每 2s
  // 变化不能触重播。签名没变就不动 DOM——格子不闪、动画不重启、悬停不弹手。
  const sig = lanes.map((l) => l + ":" + byStage[l].length + "(" +
    byStage[l].map((s) => s.status[0] || "?").join("") + ")").join("|");
  const cellsBox = $("rd-hive-cells");
  if (cellsBox.dataset.hiveSig !== sig) {
    const fresh = cellsBox.dataset.hiveSig == null;   // 首次展开详情也照播，先落框架再逐格亮起
    cellsBox.dataset.hiveSig = sig;
    // hive-enter 只在下一帧就摘（Rune/Hermes 式一闪而过的瀑布），全程保留会锁死
    // 入场帧；1400ms 定时是 RAF 被后台标签页冻结时的兜底。
    if (!fresh) cellsBox.classList.remove("hive-enter");
    cellsBox.classList.add("hive-enter");
    if (cellsBox._hiveEnterRAF) cancelAnimationFrame(cellsBox._hiveEnterRAF);
    cellsBox._hiveEnterRAF = requestAnimationFrame(() => {
      requestAnimationFrame(() => cellsBox.classList.remove("hive-enter"));
      cellsBox._hiveEnterRAF = null;
    });
    if (cellsBox._hiveEnterT) clearTimeout(cellsBox._hiveEnterT);
    cellsBox._hiveEnterT = setTimeout(() => cellsBox.classList.remove("hive-enter"), 1400);
  }
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
    // 日志抽屉一律手动打开（点蜂巢格/步骤行）：自动弹开会盖住对话输入条，
    // 用户要求所有页面默认不展示日志（2026-09-17）
  } else stopHiveTick();
  rdTabsSync();   // 蜂巢显隐直接决定「蜂巢」标签可用性
};

/* 活跃步骤的实时尾巴：拉 900 字符窗口，去噪后取最后一行正文。
 * 只刷运行中格子——终态格子的结论一旦定格，不再被命令回显覆盖。 */
async function hiveTick(rid) {
  if (document.hidden || !rid) return;
  // 换详情目标后旧蜂巢定时器必须停：先把 run.id 记下来，拉回来不是它就停表，
  // 否则 A 任务的步骤尾巴会持续写进 B 详情的蜂巢格
  if (!((S.inspData || {}).run || {}).id && S.detailRunId !== rid &&
      !(S.detailTaskKey && S.lastRun && S.lastRun.id === rid)) { stopHiveTick(); return; }
  const box = $("rd-hive-cells");
  if (!box) return;
  const rels = Array.from(box.querySelectorAll(".hive-cell.st-running .hc-tail[data-log]"))
    .map((el) => el.dataset.log).filter(Boolean);
  for (const rel of rels) {
    try {
      const r = await api("/api/runs/" + encodeURIComponent(rid) +
        "/log?step=" + encodeURIComponent(rel) + "&tail=2400&pretty=1");
      const lines = String(r.log || "").split("\n").filter((l) => l.trim());
      const last = hiveCleanLine(lines);
      const key = rid + "|" + rel;
      if (last && hiveTails[key] !== last) {
        hiveTails[key] = last;
        box.querySelectorAll(".hive-cell.st-running .hc-tail").forEach((el) => {
          if (el.dataset.log === rel) el.textContent = last;
        });
      }
      // 思考行与尾巴同源（pretty 日志）：有新【思考】片段就推进卡片
      const thk = hiveThinkLine(lines);
      if (thk && hiveThink[key] !== thk) {
        hiveThink[key] = thk;
        box.querySelectorAll(".hive-cell.st-running .hc-think").forEach((el) => {
          if (el.dataset.think === rel) el.textContent = thk;
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
  // 任务级详情没有 S.detailRunId，取消目标用渲染时钉好的 cancelTargetRunId
  const rid = S.detailRunId || S.cancelTargetRunId;
  if (!rid) return;
  if (!await uiConfirm(t("确定取消该运行？运行将立即强制终止。"), { ok: t("确定") })) return;
  let r;
  try {
    r = await api("/api/runs/" + encodeURIComponent(rid) + "/cancel", { method: "POST" });
  } catch (e) { toast(t("取消失败：网络异常"), true); return; }
  toast(r && r.ok ? t("已强制终止运行") : t("该运行已结束，无需取消"));
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

/* 渲染指挥区：只认当前详情页指向的运行；messages 随 run 对象来（SSE 刷新即更新）。
 * 放在「蜂巢」分区蜂巢下方——运行中的驾驶舱：看着蜜蜂干活，随手递话/贴截图。 */
function renderDirector(run, active) {
  const box = $("rd-direct");
  if (!box) return;
  if (!run || !active) { box.classList.add("hidden"); return; }
  if (dirRunId !== run.id) { dirRunId = run.id; dirAtts = []; drawDirAtts(); }
  box.classList.remove("hidden");
  const cached = _runMessagesCache.get(run.id);
  const expectedCount = run.message_count == null ? null : Number(run.message_count);
  const expectedPending = run.pending_message_count == null ? null : Number(run.pending_message_count);
  const runMessages = Array.isArray(run.messages) ? run.messages : null;
  const runPending = runMessages ? runMessages.filter((m) => !m.consumed).length : null;
  const runMessagesCurrent = !!runMessages && (expectedCount == null ||
    (runMessages.length === expectedCount && runPending === expectedPending));
  const msgs = runMessagesCurrent ? runMessages : (cached ? cached.messages : []);
  if (runMessagesCurrent) {
    const pending = msgs.filter((m) => !m.consumed).length;
    _runMessagesCache.set(run.id, { messages: msgs, count: msgs.length, pending });
  }
  if (expectedCount != null) {
    const messageCache = _runMessagesCache.get(run.id);
    if (!messageCache || messageCache.count !== expectedCount || messageCache.pending !== expectedPending) {
      if (!_runMessagesInflight.has(run.id)) {
        const request = api("/api/runs/" + encodeURIComponent(run.id) + "/messages", { timeout: 10000 })
          .then((data) => {
            if (dirRunId !== run.id || !S.lastRun || S.lastRun.id !== run.id) return;
            const messages = (data && data.messages) || [];
            _runMessagesCache.clear();
            _runMessagesCache.set(run.id, {
              messages, count: messages.length,
              pending: messages.filter((m) => !m.consumed).length,
            });
            renderDirector(Object.assign({}, S.lastRun, { messages }),
              S.lastRun.status === "running" || S.lastRun.status === "queued");
          })
          .catch(() => {})
          .finally(() => _runMessagesInflight.delete(run.id));
        _runMessagesInflight.set(run.id, request);
      }
    }
  }
  const mb = $("rd-msgs");
  mb.innerHTML = msgs.map((m) =>
    '<div class="rd-msg' + (m.consumed ? " m-consumed" : "") + '">' +
    '<span class="m-when">' + esc(m.created_at || "") + "</span>" +
    '<span class="m-who">' + esc(m.sender || "") + "</span>" +
    esc(m.text || t("（仅附件）")) +
    ((m.attachments || []).length
      ? '<span class="m-atts">' + t("附件：") + m.attachments.map(esc).join(t("、")) + "</span>" : "") +
    (m.consumed && m.consumed_by && m.consumed_by.step
      ? '<span class="m-atts">' + t("已随步骤送达：") + "#" + Number(m.consumed_by.step) +
        " " + esc(m.consumed_by.role || "") + "</span>"
      : (m.consumed ? "" :
        '<button class="m-retract ghost small" title="' + esc(t("尚未送达，可撤回")) +
        '" onclick="dirRetract(\'' + esc(String(m.id || "")) + '\')">' + t("撤回") + "</button>")) +
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

/* 撤回尚未下达的指令：drain 前从信箱删除；已随步骤送达的后端会拒绝。 */
window.dirRetract = async function (msgId) {
  if (!dirRunId || !msgId) return;
  try {
    await api("/api/runs/" + encodeURIComponent(dirRunId) + "/messages/retract", {
      method: "POST", body: JSON.stringify({ id: msgId }),
    });
    toast(t("指令已撤回，不会送达执行"));
  } catch (e) { toast(t("撤回失败：") + e.message, true); }
  // 本地立即重拉该 run 重画消息流（不等 SSE 回程）
  try {
    const d = await api("/api/runs/" + encodeURIComponent(dirRunId));
    if (d.run && dirRunId === d.run.id) renderDirector(d.run,
      d.run.status === "running" || d.run.status === "queued");
  } catch (e) { /* 下一轮轮询兜底 */ }
};

/* 人工干预（非直连任务）：运行中=递话入箱，下一个步骤开始前送达主智能体
 * （它判断后引导执行方向）；已结束=发送后自动断点续跑，主智能体带着
 * 反馈重新规划。后端链路：信箱 → retry 继承未消费消息 → 规划 peek/执行 drain。 */
window.rdTalkToggle = function () {
  const box = $("rd-talk");
  if (!box) return;
  const show = box.classList.contains("hidden");
  box.classList.toggle("hidden", !show);
  const hint = $("rd-talk-hint");
  if (hint) {
    const run = S.lastRun;
    const active = !!run && (run.status === "running" || run.status === "queued");
    hint.textContent = active ? t("运行中：下一个步骤开始前送达主智能体")
      : t("已结束：发送后自动断点续跑，主智能体带着反馈重新规划");
  }
  if (show) { const ta = $("rd-talk-input"); if (ta) setTimeout(() => ta.focus(), 0); }
};

window.rdTalkSend = async function () {
  const run = S.lastRun;
  const ta = $("rd-talk-input");
  const text = ((ta && ta.value) || "").trim();
  if (!run || !run.id) return;
  if (!text && !talkAtts.length) { if (ta) ta.focus(); return; }
  const btn = $("rd-talk-send");
  btn.disabled = true;
  try {
    await api("/api/runs/" + encodeURIComponent(run.id) + "/messages",
      { method: "POST", body: JSON.stringify({ text, attachments: talkAtts.map((a) => a.id) }) });
    if (ta) ta.value = "";
    talkAtts = []; drawTalkAtts();
    const box = $("rd-talk");
    if (box) box.classList.add("hidden");
    const active = run.status === "running" || run.status === "queued";
    if (!active && run.task_id) {
      await retryTask(run.task_id);
      toast(t("已转交：带着你的反馈断点续跑，主智能体会重新规划"));
    } else {
      toast(t("已递话：主智能体在下一个步骤开始前会看到"));
    }
  } catch (e) { toast(t("发送失败：") + e.message, true); }
  finally { btn.disabled = false; }
};

/* 递话附件：与运行中指挥同一落盘通道（/api/attachments 待提交区 → 随消息入箱） */
let talkAtts = [];
async function talkUploadFiles(files) {
  for (const f of files) {
    if (talkAtts.length >= DIR_ATT_MAX) { toast(t("附件最多 ") + DIR_ATT_MAX + t(" 个"), true); break; }
    if (f.size > DIR_ATT_BYTES) { toast(f.name + t("：超过 8MB，已跳过"), true); continue; }
    try {
      const b64 = await fileToB64(f);
      const d = await api("/api/attachments", { method: "POST",
        body: JSON.stringify({ name: f.name, data: b64 }) });
      if (d.attachment) talkAtts.push(d.attachment);
    } catch (e) { toast(t("附件上传失败：") + e.message, true); }
  }
  drawTalkAtts();
}
function drawTalkAtts() {
  const box = $("rd-talk-atts");
  if (!box) return;
  box.innerHTML = talkAtts.map((a, i) =>
    '<span class="att-chip2">' + esc(a.name) + "<b onclick=\"talkRemoveAtt(" + i + ')" title="' +
    t("移除") + "\">×</b></span>").join("");
}
window.talkRemoveAtt = function (i) { talkAtts.splice(i, 1); drawTalkAtts(); };

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


/* ---------------- 对话分区（直连任务默认视图） ----------------
 * 直连 run 的蜂巢/版本/圣经基本是空的，真正的主角是时间线：用户说的话与 CLI
 * 每轮输出混排成气泡。运行中发送 = 信箱（下一步送达）；已结束发送 = /chat
 * （后端自动起新一轮 run 接着聊）。轮次边界如实标注：无头 CLI 插不进正在跑的
 * 进程，运行中递的话只在下一步生效。 */
let chatRunId = null;
let chatAtts = [];
let chatTimelineItems = [];   // renderChat 每次重建时间线时刷新；复制/编辑重发按索引回查

/* 聊天正文协议清洗：剥 DIRECT_DONE/<followups> 尾巴与「本轮做了什么」复读头。
 * 渲染与「复制」共用同一口径——复制出来的就是看到的内容。 */
function chatCleanText(raw) {
  return String(raw || "")
    .replace(/^\s*本轮做了什么\s*[：:][^\n]*\n+(回复\s*[：:]\s*\n?)?/, "")
    .replace(/\n*DIRECT_DONE:.*\s*$/s, "")
    .replace(/\n*<followups>[\s\S]*?<\/followups>\s*$/s, "")
    .trim();
}
let chatSig = "";
let chatLiveSig = "";        // 时间线实时增量签名（思考/正文长度变才重建 DOM）
let chatLiveTimer = null;    // 运行中快轮询句柄（1.2s，见 scheduleChatLive）
let chatLiveRunId = null;
let chatForceBottom = false; // 发送消息后强制贴底一次（不管当时滚到哪，都要看到自己刚发的内容与回复）

function chatEngineIsDirect(run) {
  const st = S.state || {};
  const all = (st.tasks || []).concat(st.archived_tasks || []);
  const task = all.find((x) => x.id === (run && run.task_id));
  return !!((task && task.engine === "direct") ||
    (!task && run && (run.engine === "direct" || run.type === "direct")));
}

/* 思考过程块（2026-09-22 用户诉求「把思考过程打印出来」）：内置智能体流式抓的
 * 模型思维链 + 工具活动。运行中=常开实时块（逐句长出来）；结束后=折叠摘要
 * （点开回看），不再让整轮只有三点打字动画。thinking 最长 2 万字（后端已截）。
 * 活动行是后端烤死的中文标签（activity 落库进步骤记录，老 run 也是中文），
 * 只能在渲染时翻：前缀命中「请求工具: 」就换字典词条，其余原样放行。 */
function chatActLine(a) {
  const s = String(a || "");
  const PRE = "请求工具: ";
  if (s.startsWith(PRE)) {
    return t("请求工具") + ": " + s.slice(PRE.length).replace(/、/g, t("、"));
  }
  return s;
}

function chatThinkHTML(it) {
  const running = it.status === "running";
  const think = String(it.thinking || "").trim();
  const acts = running ? (it.activity || []).slice(-4) : [];
  if (!think && !acts.length) return "";
  const shown = think.length > 12000 ? "…" + think.slice(-12000) : think;
  if (running) {
    return '<div class="chat-think live" data-think-live="1">' +
      '<div class="ct-head"><span class="ct-dot" aria-hidden="true"></span>' +
      esc(t("思考过程")) + '<span class="ct-live">' + esc(t("实时")) + "</span></div>" +
      (shown ? '<div class="ct-body">' + esc(shown) + "</div>" : "") +
      (acts.length ? '<div class="ct-acts">' +
        acts.map((a) => "<div>" + esc(chatActLine(a)) + "</div>").join("") + "</div>" : "") +
      "</div>";
  }
  return '<details class="chat-think"><summary>' + esc(t("思考过程")) +
    '<span class="ct-len">' + think.length + " " + esc(t("字")) + "</span></summary>" +
    '<div class="ct-body">' + esc(think) + "</div></details>";
}

/* 运行中直连对话的快轮询：SSE/轮询最密 2s、闲时 8s，思考是逐句吐的——
 * 1.2s 直拉时间线把增量打印出来。run 终态、切详情、分区隐藏即停。 */
function stopChatLive() {
  if (chatLiveTimer) { clearInterval(chatLiveTimer); chatLiveTimer = null; }
  chatLiveRunId = null;
}

function scheduleChatLive(runId) {
  if (chatLiveTimer && chatLiveRunId === runId) return;
  stopChatLive();
  chatLiveRunId = runId;
  chatLiveTimer = setInterval(async () => {
    const run = S.lastRun;
    const flow = $("rd-chat-flow");
    if (!run || run.id !== runId || !flow ||
        !$("run-detail") || $("run-detail").classList.contains("hidden") ||
        (run.status !== "running" && run.status !== "queued")) return stopChatLive();
    try {
      const data = await api("/api/runs/" + encodeURIComponent(runId) + "/timeline");
      if (!S.lastRun || S.lastRun.id !== runId) return;   // 拉取期间切了详情
      drawChatFlow(run, data, true);
    } catch (e) { /* 网络抖动保留上一帧 */ }
  }, 1200);
}

/* 对话正文：``` 围栏渲染成真代码块（复用 codeBlockHTML：行号+高亮+代码主题），
 * 其余文本保持 pre-wrap 原样（行内 `code` 与 **加粗** 轻量翻译）。模型输出先
 * 全量 esc 再插标记；未闭合围栏按代码块收尾，流式输出中途也不撒裸反引号。 */
function chatBodyHTML(text) {
  const lines = String(text || "").split(/\r?\n/);
  const out = [];
  let txt = [], code = null;
  const flushTxt = () => {
    if (!txt.length) return;
    out.push('<div class="chat-body">' + txt.map((l) => {
      let h = esc(l);
      h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
      h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
      return h;
    }).join("\n") + "</div>");
    txt = [];
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      if (code) { out.push(codeBlockHTML(code.join("\n"))); code = null; }
      else { flushTxt(); code = []; }
      continue;
    }
    if (code) code.push(raw);
    else txt.push(raw);
  }
  if (code) out.push(codeBlockHTML(code.join("\n")));
  flushTxt();
  return out.join("");
}

async function renderChat(run, active) {
  const box = $("rd-chat");
  if (!box) return;
  // 只对直连任务启用（其它流程有自己的蜂巢/步骤视图，不抢默认选卡）
  if (!run || !chatEngineIsDirect(run)) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const continueNote = $("rd-chat-continue-note");
  if (continueNote) continueNote.classList.toggle("hidden", !!active);
  if (chatRunId !== run.id) { chatRunId = run.id; chatAtts = []; drawChatAtts(); chatSig = ""; chatLiveSig = ""; }
  // 三件套回填：任务对象来自 S.state（渲染时已就位）；只在任务未在跑时回显，
  // 防止轮询把用户正在改的值弹回（select change 重绘弹回教训）
  if (run.status !== "running" && run.status !== "queued" && S.state) {
    const task = (S.state.tasks || []).find((x) => x.id === run.task_id);
    if (task) rdPrefsFill(task);
  }
  const sig = run.id + "|" + run.status + "|" + (run.steps || []).length +
    "|" + ((run.messages || []).length);
  if (sig === chatSig) return;   // 轮询重画去抖：内容没变不重建 DOM（保住输入焦点）
  const runForFetch = run.id;    // 拉取期间可能切详情：过期响应不落盘（同 renderRunDetail 闸门）
  chatSig = sig;
  let data = null;
  try { data = await api("/api/runs/" + encodeURIComponent(run.id) + "/timeline"); }
  catch (e) { data = null; }
  if (!$("run-detail") || $("run-detail").classList.contains("hidden")) return;
  // 过期闸门：run 级（renderRunDetail）与任务级（drawTaskDetail）都经 S.lastRun
  // 指向当前详情正在展示的那条 run——拉取期间切了详情就丢弃响应
  if (!S.lastRun || S.lastRun.id !== runForFetch) return;
  drawChatFlow(run, data, active);
}

/* 时间线渲染本体（renderChat 与运行中快轮询共用）：items → 气泡流。
 * force=true 时跳过增量签名闸门（快轮询拿到的一定是新帧）。 */
function drawChatFlow(run, data, active) {
  const items = (data && data.items) || [];
  const flow = $("rd-chat-flow");
  if (!flow) return;
  // 增量闸门：时间线任何可见输入（状态/正文/思考/实时流/活动/追问/已送达）
  // 都没变才跳过重建——保滚动位置与折叠态。维度少了不行：只看 live 长度的话，
  // 没开思考的 CLI 步骤 running→done 两态签名相同，气泡会永远停在打字动画
  const liveSig = items.reduce((a, it) => a + "|" + (it.kind || "") + ":" +
    (it.status || "") + ":" + String(it.text || "").length + ":" +
    String(it.thinking || "").length + ":" + String(it.stream || "").length + ":" +
    (it.activity || []).length + ":" + (it.followups || []).length +
    ":" + (it.consumed ? 1 : 0), "") + "|" + ((data && data.result) ? "r" : "");
  if (liveSig === chatLiveSig && flow.childElementCount) return;
  chatLiveSig = liveSig;
  // 时间显示统一收敛到 HH:MM（日期在 meta 条里，全量戳塞气泡就是噪音）；
  // 附件兼容 字符串路径 / {name|path} 对象 两种形态（对象直接拼会成 [object Object]）
  const chatTime = (v) => { const s = String(v || "");
    return /^\d{4}-\d{2}-\d{2}[ T]/.test(s) ? s.slice(11, 16) : s; };
  const attName = (a) => { const p = typeof a === "string" ? a
    : ((a && (a.name || a.path)) || "");
    return String(p).split(/[\\/]/).pop() || ""; };
  chatTimelineItems = items;   // 复制/编辑重发按索引回查（见 rd-chat-flow 委托）
  let html = items.map((it, idx) => {
    if (it.kind === "user") {
      // 用户消息：右侧浅灰圆角块，元信息在块内顶部。
      // 附件胶囊可点开预览：data-att 带 _attachments/ 相对路径（老数据裸文件名
      // 在前端钉回 _attachments/，与服务端 norm_rel 同口径），事件委托绑在
      // rd-chat-flow 上——时间线整块 innerHTML 重建也不丢监听
      const atts = (it.attachments || []).map((a) => {
        const nm = attName(a);
        if (!nm) return null;
        let rel = typeof a === "string" ? a : ((a && (a.path || a.name)) || nm);
        rel = String(rel).replace(/\\/g, "/");
        if (!rel.startsWith("_attachments/")) rel = "_attachments/" + rel.split("/").pop();
        const isImg = /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(nm);
        const src = isImg && chatRunId
          ? "/api/runs/" + encodeURIComponent(chatRunId) + "/file?name=" + encodeURIComponent(rel) : "";
        return { nm, rel, img: src };
      }).filter(Boolean);
      // 悬停动作：复制原文 / 编辑重发（载入输入框改完再发，2026-09-22 用户诉求）
      const acts = '<span class="chat-acts2">' +
        '<button type="button" class="chat-act" data-cact="copy" data-ci="' + idx + '">' + esc(t("复制")) + "</button>" +
        '<button type="button" class="chat-act" data-cact="edit" data-ci="' + idx + '">' + esc(t("编辑")) + "</button>" +
        "</span>";
      return '<div class="chat-row me">' +
        '<div class="chat-bubble me">' +
        '<div class="chat-meta">' + esc(it.who || "") + " " + esc(chatTime(it.at)) +
        (it.consumed ? "" : " · " + t("待送达")) + acts + "</div>" +
        esc(it.text || t("（仅附件）")) +
        (atts.length
          ? '<div class="chat-atts">' + atts.map((a) =>
              (a.img
                ? '<span class="att-open chat-img" data-att="' + esc(a.rel) + '" title="' + esc(a.nm) + '">' +
                  '<img src="' + esc(a.img) + '" alt="' + esc(a.nm) + '" loading="lazy" draggable="false"></span>'
                : '<span class="att-chip2 att-open" data-att="' + esc(a.rel) +
                  '" title="' + esc(t("点击查看")) + '">' + esc(a.nm) + "</span>")).join("") + "</div>"
          : "") +
        "</div></div>";
    }
    // 协议尾行是给编排器判收的机器标记，不该出现在聊天正文里（真源在提示词约定）；
    // <followups> 块后端已剥并转成结构化字段，这里再兜一次底防老数据/漏剥；
    // 开场「本轮做了什么：…」+「回复：」是模型复读协议措辞的元描述（提示词已禁，
    // 这里兜历史数据），只剥消息开头的第一行
    const body = chatCleanText(it.text);
    // 运行中还没有正文：先给思考过程块（有思维链/工具活动时），正文位退化为
    // 实时预览（stream 累计值）；两者皆无才是三点打字动画（终态无正文才落占位）
    const liveText = it.status === "running" ? String(it.stream || "").trim() : "";
    const bodyHtml = body ? chatBodyHTML(body)
      : (liveText ? '<div class="chat-body chat-live-text">' + esc(liveText.slice(-4000)) + "</div>"
      : (it.status === "running"
        ? '<span class="chat-typing" aria-label="' + esc(t("正在执行…")) + '"><i></i><i></i><i></i></span>'
        : esc(t("（本轮无文本输出）"))));
    // 元信息只留 名字+时间（+失败标记）：路由/供应商细节属于「步骤」页签，塞这里就是噪音
    const metaBad = it.status === "failed" || it.status === "cancelled";
    // 智能体回复：头像 + 整幅正文（不套气泡框），元信息小字落在正文下方；
    // 悬停可复制回答原文（协议清洗后）
    const actsA = body ? '<span class="chat-acts2">' +
      '<button type="button" class="chat-act" data-cact="copy" data-ci="' + idx + '">' + esc(t("复制")) + "</button></span>" : "";
    // 智能体回复：头像 + 整幅正文（不套气泡框），元信息小字落在正文下方
    return '<div class="chat-row">' +
      '<span class="chat-avatar" aria-hidden="true"><svg class="ico"><use href="#i-bee"></use></svg></span>' +
      '<div class="chat-bubble agent">' +
      chatThinkHTML(it) +
      '<div class="chat-body">' + bodyHtml + "</div>" +
      '<div class="chat-meta">' + esc(it.who || "") + " " + esc(chatTime(it.at)) +
      (metaBad ? " · " + t(it.status === "failed" ? "失败" : "已取消") : "") + actsA + "</div>" +
      (it.log && it.run ? '<div class="chat-actions"><button class="chat-log-btn" type="button" data-chat-log-run="' +
        esc(it.run) + '" data-chat-log-rel="' + esc(it.log) + '" title="' + esc(t("查看日志")) + '">' + esc(t("CLI 日志")) + "</button></div>" : "") +
      (Array.isArray(it.followups) && it.followups.length
        ? '<div class="chat-fups">' + it.followups.map((f) =>
          '<button type="button" class="chat-fup-btn" data-fup="' + esc(f.prompt || "") +
          '" title="' + esc(f.prompt || "") + '">' + esc(f.label || "") + "</button>").join("") + "</div>"
        : "") +
      "</div></div>";
  }).join("");
  // 时间线收尾的「执行结果」卡：run 终态后的确定性摘要（成没成/耗时/执行者/
  // 产出文件）——不依赖模型最后一句话自觉交代（用户反馈：输"1"跑完 55 秒
  // 只见一句寒暄，执行结果无处可看）。
  const res = data && data.result;
  if (res) html += chatResultHTML(run, res);
  const hint = $("rd-chat-hint");
  if (hint) hint.textContent = active
    ? t("运行中：新消息会排队，本轮回答完后依次送达")
    : t("已结束：发送后将自动开新一轮接着做");
  // 贴底跟随：用户滚到底部附近才自动滚到最新输出，回看历史不打扰；
  // 刚发送过消息则强制贴底一次（用户要看到自己发的消息与正在来的回复）
  const stick = chatForceBottom ||
    flow.scrollHeight - flow.scrollTop - flow.clientHeight < 80;
  chatForceBottom = false;
  // 思考过程面板（.ct-body 自带滚动条）逐帧整体重建会丢滚动位置——重绘前
  // 记住「贴底 or 用户上滑到哪」，重绘后恢复：贴底则一直跟最新（2026-09-22
  // 用户诉求），上滑回看历史思维链则保持位置不被打扰。
  const prevThink = flow.querySelector('[data-think-live="1"] .ct-body');
  const thinkPin = prevThink
    ? prevThink.scrollHeight - prevThink.scrollTop - prevThink.clientHeight < 24
    : true;
  const thinkTop = prevThink ? prevThink.scrollTop : 0;
  flow.innerHTML = html || '<div class="hint">' + esc(t("还没有对话内容")) + "</div>";
  if (stick) flow.scrollTop = flow.scrollHeight;
  const newThink = flow.querySelector('[data-think-live="1"] .ct-body');
  if (newThink) {
    if (thinkPin) newThink.scrollTop = newThink.scrollHeight;
    else newThink.scrollTop = Math.min(thinkTop, newThink.scrollHeight);
  }
  // 有运行中的直连步骤 → 快轮询把思考过程/正文增量打印出来；终态即停
  const hasRunning = items.some((it) => it.kind === "agent" && it.status === "running");
  if (active && hasRunning && run && run.id) scheduleChatLive(run.id);
  else stopChatLive();
}

/* 执行结果卡（时间线收尾）：run 终态后的确定性摘要——成没成、跑多久、谁执行
 * 的、产出了哪些文件，全部产品明示，不依赖模型自觉交代。失败给「查看执行步骤」
 * 入口；文件 chip 复用成品预览通道（artPopup）。
 * 措辞说「本轮」不说「任务」：追话每轮都落一张卡，卡头喊「任务完成」会被读成
 * 又建了一个新任务（2026-09-22 用户反馈：其实一直都在同一个任务里）。 */
function chatResultHTML(run, res) {
  const ok = res.status === "done";
  const bad = res.status === "failed";
  const icon = ok ? "#i-check" : "#i-x";
  const label = ok ? t("本轮完成") : (bad ? t("本轮失败") : t("已取消"));
  const meta = [];
  if (res.executor) meta.push(esc(res.executor));
  if (res.turns) meta.push(res.turns + " " + t("轮对话"));
  if (res.duration_s != null) meta.push(chatDurTxt(res.duration_s));
  const files = res.files || [];
  const chips = files.map((f) => {
    const fUrl = urlAuth("/api/runs/" + encodeURIComponent(run.id) + "/file?name=" + encodeURIComponent(f.name));
    // 网页成品给「运行」：弹窗里 iframe 真跑（源码弹窗看不出页面长什么样，
    // 用户要的是「看到东西跑起来」——与详情页「预览」页签同款挂载）
    const fExt = _fpExt(f.name);
    const runBtn = (fExt === "html" || fExt === "htm")
      ? '<button type="button" class="chip-btn" title="' + esc(t("运行展示")) +
        '" onclick="artRunPopup(\'' + esc(run.id) + "', '" + esc(f.name) + "', " + (Number(f.size) || 0) + ')"><svg class="ico" aria-hidden="true"><use href="#i-rocket"/></svg></button>'
      : "";
    return '<div class="file-chip has-actions">' +
      '<i class="fx">' + esc(_fpExt(f.name).slice(0, 4) || "file") + "</i>" +
      '<span class="p" title="' + esc(f.name + " · " + fmtSize(f.size)) + '">' + esc(f.name) + "</span>" +
      '<span class="fsz">' + fmtSize(f.size) + "</span>" +
      '<span class="fbtns">' + runBtn +
      '<button type="button" class="chip-btn" title="' + esc(t("预览")) + '" onclick="artPopup(\'' + esc(run.id) + "', '" + esc(f.name) + "', " + (Number(f.size) || 0) + ')"><svg class="ico" aria-hidden="true"><use href="#i-file-text"/></svg></button>' +
      '<a class="chip-btn" href="' + fUrl + '" download="' + esc(f.name) + '" title="' + esc(t("下载")) + '"><svg class="ico" aria-hidden="true"><use href="#i-arrow-left"/></svg></a>' +
      "</span></div>";
  }).join("");
  return '<div class="chat-row">' +
    '<span class="chat-avatar" aria-hidden="true"><svg class="ico"><use href="' + icon + '"></use></svg></span>' +
    '<div class="chat-result' + (ok ? "" : bad ? " bad" : " off") + '">' +
    '<div class="cr-head"><svg class="ico" aria-hidden="true"><use href="' + icon + '"></use></svg>' +
    esc(label) + "</div>" +
    (meta.length ? '<div class="cr-meta">' + meta.join(" · ") + "</div>" : "") +
    (bad && res.error ? '<div class="cr-err">' + esc(res.error) + "</div>" : "") +
    '<div class="cr-files">' + (chips
      ? '<div class="cr-files-title">' + esc(t("产出文件")) + t("（") + files.length + t("）") + "</div>" +
        '<div class="file-chips">' + chips + "</div>"
      : '<div class="cr-files-title">' + esc(t("无文件产出")) + "</div>") + "</div>" +
    (bad ? '<button class="ghost cr-steps" onclick="rdChatNavGo(\'steps\')">' +
      esc(t("查看执行步骤")) + "</button>" : "") +
    "</div></div>";
}

function chatDurTxt(s) {
  s = Number(s) || 0;
  if (s < 60) return s + " " + t("秒");
  const m = Math.floor(s / 60), r = s % 60;
  return m + " " + t("分") + (r ? " " + r + " " + t("秒") : "");
}

/* 代码/编排任务的确定性结果摘要：报告是长文，步骤是过程，用户还需要一眼
 * 知道这次到底成没成、做了多少步、用了多久，以及失败原因。这个卡片不依赖
 * 智能体是否在最后一句话里主动总结；直连任务也能在「成果」页签看到同一口径。 */
function runDurationSeconds(run) {
  if (!run) return null;
  if (Number.isFinite(Number(run.duration_s))) return Number(run.duration_s);
  const steps = (run.steps || []).filter((s) => Number.isFinite(Number(s.duration_s)));
  if (steps.length) return steps.reduce((a, s) => a + Number(s.duration_s || 0), 0);
  const a = Date.parse(String(run.started_at || "").replace(" ", "T"));
  const b = Date.parse(String(run.ended_at || "").replace(" ", "T"));
  return Number.isFinite(a) && Number.isFinite(b) && b >= a ? (b - a) / 1000 : null;
}

function runEtaText(run) {
  if (!run || !Number.isFinite(Number(run.estimated_duration_s))) return "";
  const estimate = Math.max(1, Number(run.estimated_duration_s));
  const p90 = Math.max(estimate, Number(run.estimated_p90_s) || estimate);
  const active = run.status === "running" || run.status === "queued";
  if (!active) return t("预计完成约 {0}", chatDurTxt(Math.round(estimate)));
  if (run.status === "queued" || !run.started_at) {
    return t("预计完成约 {0}，保守不超过 {1}",
      chatDurTxt(Math.round(estimate)), chatDurTxt(Math.round(p90)));
  }
  const started = Date.parse(String(run.started_at).replace(" ", "T"));
  if (!Number.isFinite(started)) return "";
  const elapsed = Math.max(0, (Date.now() - started) / 1000);
  if (elapsed < estimate) {
    return t("预计剩余约 {0}", chatDurTxt(Math.max(1, Math.round(estimate - elapsed))));
  }
  if (elapsed < p90) {
    return t("已超过常规预估，仍在保守区间内（约剩 {0}）",
      chatDurTxt(Math.max(1, Math.round(p90 - elapsed))));
  }
  return t("已超过保守预估，正在继续执行，可查看步骤日志");
}

function runOutcomeSummary(run) {
  if (!run) return "";
  const last = (run.steps || []).slice().reverse().find((s) => s.output || s.summary);
  const raw = run.summary || (last && (last.output || last.summary)) || run.error || "";
  return String(raw).replace(/\s+/g, " ").trim().slice(0, 520);
}

function renderRunOutcome(run, runs, aggregate) {
  const box = $("rd-outcome");
  if (!box) return;
  if (!run) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const all = runs && runs.length ? runs : [run];
  const steps = (run.steps || []);
  const total = aggregate && Number.isFinite(Number(aggregate.steps))
    ? Number(aggregate.steps) : all.reduce((n, r) => n + ((r.steps || []).length), 0);
  const settled = aggregate && Number.isFinite(Number(aggregate.settled_steps))
    ? Number(aggregate.settled_steps) : all.reduce((n, r) => n + (r.steps || []).filter((s) =>
      !["running", "queued"].includes(String(s.status || ""))).length, 0);
  const status = String(run.status || "");
  const active = status === "running" || status === "queued";
  const bad = status === "failed" || status === "cancelled" || status === "timeout";
  const label = runStatusText(run);
  // 运行级警告（如：工作目录与运行中任务并行——计划/记忆/分支检出互相干扰）
  const warns = Array.isArray(run.warnings) ? run.warnings.filter(Boolean) : [];
  const icon = status === "done" ? "#i-check" : bad ? "#i-x" : "#i-gauge";
  const duration = runDurationSeconds(run);
  const latest = steps.slice().reverse().find((s) => s.agent_label || s.agent || s.role);
  const route = run.route && typeof run.route === "object" ? run.route.implementer : "";
  // route.implementer 是路由评分说明，不是用户真正关心的 CLI 名称；优先
  // 展示步骤上的 agent_label，只有旧数据没有步骤执行者时才回退到路由说明。
  const executor = run.executor || run.implementer || (latest && (latest.agent_label || latest.agent)) || route || "";
  const summary = runOutcomeSummary(run);
  const meta = [];
  if (executor) meta.push('<span><b>' + esc(t("执行者")) + '</b> ' + esc(executor) + "</span>");
  if (total) meta.push('<span><b>' + esc(t("步骤")) + '</b> ' + settled + "/" + total + "</span>");
  if (duration != null) meta.push('<span><b>' + esc(t("耗时")) + '</b> ' + esc(chatDurTxt(duration < 1 ? Number(duration.toFixed(1)) : Math.round(duration))) + "</span>");
  return (box.classList.remove("hidden"), box.innerHTML =
    '<div class="outcome-top">' +
      '<div class="outcome-title ' + (bad ? "bad" : active ? "live" : "ok") + '">' +
        '<svg class="ico" aria-hidden="true"><use href="' + icon + '"></use></svg>' + esc(t("执行结果")) +
        '<span class="outcome-status">' + esc(label) + "</span></div>" +
      (meta.length ? '<div class="outcome-meta">' + meta.join("") + "</div>" : "") +
    "</div>" +
    (summary ? '<div class="outcome-summary">' + esc(summary) + "</div>" : "") +
    (warns.length ? '<div class="outcome-warn">' + warns.map((w) => "⚠ " + esc(w)).join("<br>") + "</div>" : "") +
    (run.error ? '<div class="outcome-error">' + esc(run.error) + "</div>" : "") +
    (total ? '<div class="outcome-actions"><button class="ghost" type="button" onclick="rdChatNavGo(\'steps\')">' + esc(t("查看执行步骤")) + "</button></div>" : "")
  );
}

/* 任务详情首屏概览：把“这次任务做了什么、做到哪、最近发生了什么”放在
 * 蜂巢上方。直连任务仍以对话为主，由 chat-mode 隐藏概览，避免它抢走输入空间。 */
function renderDetailOverview(run, runs, task, aggregate) {
  const box = $("rd-overview");
  if (!box || !run) return;
  const direct = chatEngineIsDirect(run);
  box.classList.toggle("direct-overview", direct);
  const all = runs && runs.length ? runs : [run];
  const steps = (run.steps || []);
  const total = aggregate && Number.isFinite(Number(aggregate.steps))
    ? Number(aggregate.steps) : all.reduce((n, r) => n + ((r.steps || []).length), 0);
  const settled = aggregate && Number.isFinite(Number(aggregate.settled_steps))
    ? Number(aggregate.settled_steps) : all.reduce((n, r) => n + (r.steps || []).filter((s) =>
      !["running", "queued"].includes(String(s.status || ""))).length, 0);
  const pct = total ? Math.max(0, Math.min(100, Math.round((settled / total) * 100))) : 0;
  const status = String(run.status || "");
  const active = status === "running" || status === "queued";
  const bad = status === "failed" || status === "cancelled" || status === "timeout";
  const statusText = runStatusText(run);
  const last = steps.slice().reverse().find((s) => s.summary || s.output || s.note) || steps[steps.length - 1];
  const lastText = last ? String(last.summary || last.output || last.note || "").replace(/\s+/g, " ").trim().slice(0, 240) : "";
  const executor = run.executor || run.implementer || (last && (last.agent_label || last.agent)) || "";
  const duration = runDurationSeconds(run);
  const etaText = runEtaText(run);
  const goal = (task && task.goal) || run.goal || run.title || "";
  const actions = [];
  if (total) actions.push('<button type="button" class="ghost" data-overview-tab="steps">' + esc(t("查看步骤")) + "</button>");
  if (last && last.log) actions.push('<button type="button" class="ghost" data-overview-run="' + esc(run.id) + '" data-overview-log="' + esc(last.log) + '">' + esc(t("打开 CLI 日志")) + "</button>");
  if (status === "done" || run.report) actions.push('<button type="button" class="ghost" data-overview-tab="result">' + esc(t("查看成果")) + "</button>");
  box.innerHTML =
    '<div class="rd-overview-main">' +
      '<div class="rd-overview-kicker">' + esc(t("任务概览")) + '</div>' +
      '<div class="rd-overview-title" title="' + esc(run.title || "") + '">' + esc(run.title || "") + '</div>' +
      (goal ? '<div class="rd-overview-goal" title="' + esc(goal) + '">' + esc(goal) + '</div>' : "") +
    '</div>' +
    '<div class="rd-overview-state">' +
      '<span class="state-label">' + esc(t("当前状态")) + '</span>' +
      '<strong class="state-value ' + (bad ? "bad" : status === "done" ? "ok" : "") + '">' + esc(statusText) + '</strong>' +
    '</div>' +
    '<div class="rd-overview-progress" aria-label="' + esc(pct + "%") + '"><i style="width:' + pct + '%"></i></div>' +
    '<div class="rd-overview-facts">' +
      '<span><b>' + settled + "/" + total + '</b> ' + esc(t("步骤已完成")) + '</span>' +
      (executor ? '<span>' + esc(t("执行者")) + ' <b>' + esc(executor) + '</b></span>' : "") +
      (duration != null ? '<span>' + esc(t("耗时")) + ' <b>' + esc(chatDurTxt(duration < 1 ? Number(duration.toFixed(1)) : Math.round(duration))) + '</b></span>' : "") +
      (etaText ? '<span class="rd-eta">' + esc(etaText) + '</span>' : "") +
    '</div>' +
    (lastText ? '<div class="rd-overview-last"><b>' + esc(t("最近一步")) + '</b><span>' + esc(lastText) + '</span></div>' : "") +
    (actions.length ? '<div class="rd-overview-actions">' + actions.join("") + '</div>' : "");
  box.classList.remove("hidden");
}

/* 对话条三件套：与任务参数双向同步（2026-09-21 用户反馈「这里面也需要同步改」）。
 * 回填自当前任务；改动即写任务（空闲态），发送 /chat 前再兜底同步一次。 */
let rdPrefsTaskId = "";
let rdModelPv = "", rdModelMv = "";   // 对话条当前模型选择（厂商 id + 模型名）
let rdModelAgent = "";                // 手动指定的执行 CLI（空 = 不指定）

function rdPrefsFill(task) {
  if (!task) return;
  const set1 = (id, v, allow) => { const el = $(id); if (el && allow.includes(v)) el.value = v; };
  set1("rd-mode", task.mode, ["auto", "fast", "expert", "manual"]);
  set1("rd-thinking", task.thinking, ["auto", "low", "standard", "high"]);
  if (rdPrefsTaskId !== task.id) {
    rdPrefsTaskId = task.id;
    rdModelPv = task.direct_provider_id || "";
    rdModelMv = task.direct_model || "";
    rdModelAgent = task.direct_agent || "";
  }
  ["rd-mode", "rd-thinking"].forEach((id) => syncCmpSelFace(id));
  rdModelFaceSync();
}

function rdModelFaceSync() {
  const face = $("rd-model-btn");
  if (!face) return;
  if (rdModelAgent) {
    const a = realCliAgents().find((x) => x.id === rdModelAgent);
    face.textContent = (a ? (a.label || a.id) : rdModelAgent) + " · CLI";
    return;
  }
  if (!rdModelPv) { face.textContent = t("自动推荐"); return; }
  const p = directProviders().find((x) => x.id === rdModelPv);
  face.textContent = (p ? (p.name || p.id) : rdModelPv) +
    (rdModelMv ? "/" + rdModelMv : "/" + t("推荐"));
}

/* 对话条模型菜单（与新建 composer 的 cmp-model-menu 同款两级钻取，独立实例） */
let rdMenuProv = "";

function rdModelMenuRender() {
  const menu = $("rd-model-menu");
  if (!menu) return;
  const chk = '<svg class="ico ti-check" aria-hidden="true"><use href="#i-check"></use></svg>';
  const arr = '<svg class="ico ti-check" aria-hidden="true"><use href="#i-chevron-r"></use></svg>';
  if (!rdMenuProv) {
    menu.innerHTML =
      '<button type="button" class="type-item" data-p="" data-m=""><span class="ti-body"><span class="ti-name">' +
        t("自动推荐") + "</span></span>" + (!rdModelPv && !rdModelAgent ? chk : "") + "</button>" +
      directProviders().map((p) =>
        '<button type="button" class="type-item" data-p="' + esc(p.id) + '"><span class="ti-body"><span class="ti-name">' +
        esc(p.name || p.id) + "</span></span>" + (rdModelPv === p.id && !rdModelAgent ? chk : arr) + "</button>").join("") +
      cliMenuSectionHtml(rdModelAgent) +
      '<div class="type-menu-foot"><button type="button" class="type-item" data-manage="1"><span class="ti-body"><span class="ti-name">' +
        t("管理模型…") + "</span></span></button></div>";
  } else {
    const p = directProviders().find((x) => x.id === rdMenuProv);
    const names = p ? (p.models || []).filter((m) => !m.hidden)
      .map((m) => m.name).filter(Boolean) : [];
    menu.innerHTML =
      '<button type="button" class="type-item" data-back="1"><span class="ti-body"><span class="ti-name">‹ ' +
        esc(p ? (p.name || p.id) : rdMenuProv) + "</span></span></button>" +
      '<button type="button" class="type-item" data-p="' + esc(rdMenuProv) + '" data-m=""><span class="ti-body"><span class="ti-name">' +
        t("随厂商推荐") + "</span></span>" + (rdModelPv === rdMenuProv && !rdModelMv ? chk : "") + "</button>" +
      names.map((n) =>
        '<button type="button" class="type-item" data-p="' + esc(rdMenuProv) + '" data-m="' + esc(n) + '"><span class="ti-body"><span class="ti-name">' +
        esc(n) + "</span></span>" + (rdModelPv === rdMenuProv && rdModelMv === n ? chk : "") + "</button>").join("");
  }
  positionFloatingModelMenu(menu, $("rd-model-wrap"));
}

function toggleRdModelMenu(force) {
  const menu = $("rd-model-menu");
  if (!menu) return;
  const show = force !== undefined ? force : menu.classList.contains("hidden");
  if (show) { rdMenuProv = ""; rdModelMenuRender(); }
  menu.classList.toggle("hidden", !show);
  if (show) positionFloatingModelMenu(menu, $("rd-model-wrap"));
}
window.toggleRdModelMenu = toggleRdModelMenu;

(function () {
  const menu = $("rd-model-menu");
  if (!menu || menu.dataset.rdBound) return;
  document.body.appendChild(menu);
  menu.dataset.rdBound = "1";
  menu.addEventListener("click", (e) => {
    e.stopPropagation();   // 同 cmp-model-menu：防「点外面收起」误判（DOM 重写竞态）
    const b = e.target.closest("[data-p],[data-back],[data-manage],[data-cli]");
    if (!b) return;
    if (b.dataset.manage) { toggleRdModelMenu(false); switchTab("models"); return; }
    if (b.dataset.back) { rdMenuProv = ""; rdModelMenuRender(); return; }
    if (b.dataset.cli !== undefined) {
      rdModelAgent = b.dataset.cli;
      rdModelFaceSync();
      toggleRdModelMenu(false);
      rdPrefsPush("model");
      return;
    }
    if (b.dataset.m === undefined) { rdMenuProv = b.dataset.p; rdModelMenuRender(); return; }
    rdModelPv = b.dataset.p;
    rdModelMv = b.dataset.m;
    rdModelAgent = "";   // 选定模型/自动推荐：CLI 指定随之清除（互斥）
    rdModelFaceSync();
    toggleRdModelMenu(false);
    rdPrefsPush("model");
  });
  document.addEventListener("click", (e) => {
    const wrap = $("rd-model-wrap");
    if (wrap && !wrap.contains(e.target) && !menu.contains(e.target)) toggleRdModelMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleRdModelMenu(false);
  });
})();

async function rdPrefsPush(what) {
  // what: mode|thinking|model（变更源）；写任务参数（空闲态才允许）
  const task = (S.lastRun && S.lastRun.task_id && S.state &&
    (S.state.tasks || []).find((x) => x.id === S.lastRun.task_id)) || null;
  if (!task) return;
  const patch = {};
  if (what === "mode") patch.mode = $("rd-mode").value;
  if (what === "thinking") patch.thinking = $("rd-thinking").value;
  if (what === "model") {
    patch.direct_provider_id = rdModelPv;
    patch.direct_model = rdModelMv;
    patch.direct_agent = rdModelAgent;
  }
  try {
    await api("/api/tasks/" + encodeURIComponent(task.id) + "/params", {
      method: "POST", operation: false, body: JSON.stringify(patch) });
  } catch (e) { /* 运行中/网络失败不打断对话，发送前还有兜底 */ }
}

async function rdPrefsPushAll() {
  // 发送前兜底：三个一起推（/chat 开新一轮前调用；失败静默——值已在前端，
  // 下一轮 poll 的回填也不会覆盖用户刚选的值）
  await Promise.all([rdPrefsPush("mode"), rdPrefsPush("thinking"), rdPrefsPush("model")]);
}

function drawChatAtts() {
  const box = $("rd-chat-att-list");
  if (!box) return;
  // 点名字开预览（待提交区原文走 /api/attachments/<id>），× 才是移除。
  // 图片直接展示缩略图（2026-09-21 用户反馈「图片要展示出来」）
  box.innerHTML = chatAtts.map((a, i) => {
    const isImg = /^image\//i.test(String(a.mime || "")) ||
      /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(a.name || "");
    if (isImg && a.id) {
      const src = "/api/attachments/" + encodeURIComponent(a.id) + qsAuth();
      return '<span class="att-view rd-att-thumb" data-att-id="' + esc(a.id) +
        '" data-att-name="' + esc(a.name) + '" title="' + esc(a.name) + '">' +
        '<img src="' + esc(src) + '" alt="' + esc(a.name) + '" loading="lazy" draggable="false">' +
        '<b onclick="chatRemoveAtt(' + i + ')" title="' + esc(t("移除")) + '">×</b></span>';
    }
    return '<span class="att-chip2 att-view" data-att-id="' + esc(a.id || "") +
      '" data-att-name="' + esc(a.name) + '" title="' + esc(t("点击查看")) + '">' + esc(a.name) +
      '<b onclick="chatRemoveAtt(' + i + ')" title="' + esc(t("移除")) + '">×' +
      '</b></span>';
  }).join("");
}
window.chatRemoveAtt = function (i) { chatAtts.splice(i, 1); drawChatAtts(); };

/* 对话附件点击预览：已落盘的走 run 工作目录 /api/runs/<id>/file（rel 为
 * _attachments/ 相对路径），待提交的走 /api/attachments/<id>；
 * 弹窗与成品预览同款——图片直接看图、文本看码、二进制给下载。 */
window.chatAttPopup = function (rel) {
  if (!rel || !chatRunId) return;
  return _fpPreviewUrl("/api/runs/" + encodeURIComponent(chatRunId) +
    "/file?name=" + encodeURIComponent(rel), rel);
};
window.chatPendingPopup = function (id, name) {
  if (!id) return;
  return _fpPreviewUrl("/api/attachments/" + encodeURIComponent(id), name || "attachment");
};

async function chatUploadFiles(files) {
  for (const f of files) {
    try {
      const b64 = await new Promise((res, rej) => {
        const rd = new FileReader();
        rd.onload = () => res(String(rd.result).split(",")[1] || "");
        rd.onerror = () => rej(new Error(t("读取失败")));
        rd.readAsDataURL(f);
      });
      const r = await api("/api/attachments", { method: "POST",
        body: JSON.stringify({ name: f.name, data: b64 }) });
      chatAtts.push(r.attachment); drawChatAtts();
      // 图片能力前置提醒：模型 image_in 未开启时收不到图（内置智能体会降级为
      // 纯文本，模型只能回复「读不到」——与其事后看模型解释，不如发图时提一句）
      if (/^image\//i.test(String(f.type || "")) || /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name || "")) {
        if (!window._chatImgTipShown) {
          window._chatImgTipShown = true;
          toast(t("已添加图片。若当前模型未开启「图」输入（模型接入页可开），模型将看不到这张图"));
        }
      }
    } catch (e) { toast(t("附件上传失败：") + f.name + " — " + e.message, true); }
  }
}

window.chatSend = async function () {
  if (!chatRunId) return;
  const ta = $("rd-chat-input");
  const text = (ta.value || "").trim();
  if (!text && !chatAtts.length) return;
  const btn = $("rd-chat-send");
  const run = S.lastRun || {};
  const active = run.status === "running" || run.status === "queued";
  btn.disabled = true;
  try {
    if (active) {
      // 运行中：消息进信箱排队（时间线上带「待送达」标记），本轮跑完自动续轮送达
      await api("/api/runs/" + encodeURIComponent(chatRunId) + "/messages", {
        method: "POST",
        body: JSON.stringify({ text, attachments: chatAtts.map((a) => a.id) }),
      });
      toast(t("已排队：随下一步一起送达"));
    } else {
      // 空闲态发送：先把三件套落到任务（变更即推失败时的兜底），再开新一轮
      await rdPrefsPushAll();
      const r = await api("/api/runs/" + encodeURIComponent(chatRunId) + "/chat", {
        method: "POST",
        body: JSON.stringify({ text, attachments: chatAtts.map((a) => a.id) }),
      });
      toast(t("已开新一轮，接着做…"));
      if (r && r.run_id) {
        if (S.detailTaskKey) { S.taskSig = ""; renderTaskDetail(); }
        else jumpToRun(r.run_id);
      }
    }
    // 发送成功一律清空输入框（只清输入，时间线里的历史回复不动）；
    // 此前 /chat 分支提前 return 跳过了清空，发出去的字一直留在框里
    ta.value = "";
    ta.style.height = "";
    chatAtts = []; drawChatAtts();
    chatSig = "";   // 强制重画时间线
    chatForceBottom = true;   // 发完滚到最新：看到自己刚发的消息与正在来的回复
    const fl = $("rd-chat-flow");
    if (fl) fl.scrollTop = fl.scrollHeight;   // 重画前先即时贴底，视觉零跳动
    if (active) {
      const d = await api("/api/runs/" + encodeURIComponent(chatRunId));
      if (d.run) renderChat(d.run, d.run.status === "running" || d.run.status === "queued");
    }
  } catch (e) { toast(t("发送失败：") + e.message, true); }
  finally { btn.disabled = false; }
};

function bindChat() {
  const btn = $("rd-chat-attach"), file = $("rd-chat-file"), send = $("rd-chat-send"),
        ta = $("rd-chat-input");
  if (!btn || !file || !send || !ta) return;
  const focusBtn = $("rd-chat-focus");
  if (focusBtn) focusBtn.addEventListener("click", () => {
    S.rdTab = "chat";
    S.rdTabPin = true;
    applyRdTabs();
    setTimeout(() => ta.focus(), 0);
  });
  btn.addEventListener("click", () => file.click());
  file.addEventListener("change", (e) => {
    chatUploadFiles(Array.from(e.target.files || []));
    e.target.value = "";
  });
  send.addEventListener("click", window.chatSend);
  // 自动伸高：跟内容走、有上限（160px 后内部滚动）；清空由 chatSend 归零。
  // 高度只由用户输入驱动，轮询重画不碰它——框框不再忽大忽小
  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); window.chatSend(); }
  });
  ta.addEventListener("paste", (e) => {
    const items = Array.from((e.clipboardData || {}).items || [])
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"));
    if (!items.length) return;
    e.preventDefault();
    chatUploadFiles(items.map((it) => {
      const f = it.getAsFile();
      return f && !f.name ? new File([f], "paste-" + Date.now() + ".png", { type: f.type }) : f;
    }).filter(Boolean));
  });
  // 附件胶囊点击预览：委托绑在静态容器上——时间线轮询整块重建 innerHTML、
  // 输入条胶囊增删重画，都不丢监听。时间线胶囊走已落盘文件，输入条胶囊
  // （× 移除按钮除外）走待提交区
  $("rd-chat-flow").addEventListener("click", (e) => {
    const logBtn = e.target.closest("[data-chat-log-run][data-chat-log-rel]");
    if (logBtn) { e.stopPropagation(); toggleLog(logBtn.dataset.chatLogRun, logBtn.dataset.chatLogRel); return; }
    // 建议追问芯片：点击把整句回填输入框（不直接发送——用户可改后再发），焦点落输入框
    const fup = e.target.closest(".chat-fup-btn[data-fup]");
    if (fup) {
      const ta = $("rd-chat-input");
      if (ta) {
        ta.value = fup.dataset.fup;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        ta.focus();
      }
      return;
    }
    const file = e.target.closest(".chat-file-open[data-file-run]");
    if (file) {
      e.preventDefault();
      artPopup(file.dataset.fileRun, file.dataset.fileName || "", Number(file.dataset.fileSize) || 0);
      return;
    }
    // 复制 / 编辑重发（2026-09-22 用户诉求：已发送的对话可复制修改再次发送）。
    // 编辑=原文载入输入框，用户改完按发送即再次发出（不静默自动重发）
    const act = e.target.closest("[data-cact]");
    if (act) {
      const it = chatTimelineItems[Number(act.dataset.ci)];
      if (!it) return;
      const txt = it.kind === "user" ? String(it.text || "") : chatCleanText(it.text);
      if (act.dataset.cact === "copy") {
        copyText(txt);
        toast(t("已复制"));
      } else if (act.dataset.cact === "edit") {
        const ta = $("rd-chat-input");
        if (ta && txt) {
          ta.value = txt;
          ta.focus();
          ta.selectionStart = ta.selectionEnd = ta.value.length;
          toast(t("已载入原文，可修改后发送"));
        }
      }
      return;
    }
    const chip = e.target.closest(".att-open");
    if (chip) window.chatAttPopup(chip.dataset.att);
  });
  const attBox = $("rd-chat-att-list");
  if (attBox) attBox.addEventListener("click", (e) => {
    if (e.target.closest("b")) return;   // × 自己的 onclick 负责移除
    const chip = e.target.closest("[data-att-id]");
    if (chip) window.chatPendingPopup(chip.dataset.attId, chip.dataset.attName);
  });
  // 对话条三件套：改动即写任务参数（空闲态），face 跟随
  $("rd-mode").addEventListener("change", () => { syncCmpSelFace("rd-mode"); rdPrefsPush("mode"); });
  $("rd-thinking").addEventListener("change", () => { syncCmpSelFace("rd-thinking"); rdPrefsPush("thinking"); });
  // 语音输入（借鉴 paseo voice control）：Web Speech API 口述转文字填入输入框。
  // 仅浏览器支持时显示按钮（Chrome/Edge）；识别结果追加到光标处，不自动发送。
  bindVoiceInput();
}

function bindVoiceInput() {
  const btn = $("rd-chat-voice");
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!btn || !SR) return;   // 浏览器不支持：按钮保持 hidden
  btn.classList.remove("hidden");
  let rec = null, on = false;
  const ta = $("rd-chat-input");
  btn.addEventListener("click", () => {
    if (on) { try { rec.stop(); } catch (e) {} return; }
    rec = new SR();
    rec.lang = (getLang() === "en") ? "en-US" : "zh-CN";
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
      if (!ta) return;
      let text = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) text += e.results[i][0].transcript;
      }
      if (text) {
        text = voiceFixup(text);
        ta.value = (ta.value ? ta.value.replace(/\s+$/, "") + " " : "") + text;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      }
    };
    rec.onend = () => { on = false; btn.classList.remove("live"); };
    rec.onerror = () => { on = false; btn.classList.remove("live"); };
    try { rec.start(); on = true; btn.classList.add("live"); }
    catch (e) { toast(t("语音不可用，请检查麦克风权限"), true); }
  });
}

/* 语音识别结果纠偏（借鉴 lexicon 个人词典）：设置里维护的专有名词表
 * （localStorage orch.voice_terms，一行一个「错误→正确」或直接「术语」），
 * 识别结果做逐词替换。人名/书名/项目名是识别高频错位，词表是最小可用解。 */
function voiceFixup(text) {
  let terms = [];
  try {
    const raw = localStorage.getItem("orch.voice_terms") || "";
    terms = raw.split("\n").map((l) => l.trim()).filter(Boolean);
  } catch (e) {}
  for (const t of terms) {
    const m = t.split("→");
    if (m.length === 2 && m[0].trim() && m[1].trim()) {
      text = text.split(m[0].trim()).join(m[1].trim());
    }
  }
  return text;
}

/* ---------------------------------------------------------- 外观：皮肤 + 明暗（换肤） */
/* 调色板全部在 style.css（html[data-skin="X"]，每套含夜间/日间两版变量）；这里只放顺序
 * 与文案。卡片预览色块用 skinPalette 从 CSS 变量实时取值，不在 JS 里重复写色值——
 * 皮肤改色只需要动 style.css，预览与真实界面不会各自漂移。 */
const SKINS = [
  { id: "ocean", name: "深海", desc: "藏青底色 + 天蓝强调，夜间长时间盯任务更沉静（默认）" },
  { id: "hermes", name: "墨金", desc: "暖墨底色 + 金色发丝线，Hermes 式古典优雅" },
  { id: "classic", name: "经典", desc: "黑白灰 + 蓝色强调，ChatGPT 式清爽配色" },
  { id: "forest", name: "森野", desc: "墨绿底色 + 青翠强调，偏自然的护眼配色" },
  { id: "amber", name: "暖阳", desc: "暖棕底色 + 琥珀强调，纸感暖调" },
  { id: "violet", name: "霓虹", desc: "暗紫底色 + 品红强调，霓虹感强" },
  { id: "contrast", name: "高对比", desc: "纯黑 / 纯白 + 硬边框、去阴影，弱视与强光环境更清晰" },
];
const SKIN_IDS = new Set(SKINS.map((s) => s.id));
const SKIN_KEY = "orch.skin";
const THEME_KEY = "orch.theme";

function currentSkin() {
  const s = localStorage.getItem(SKIN_KEY) || "";
  return SKIN_IDS.has(s) ? s : "ocean";   // 值缺失/非法（如换过版本）一律回落深海
}

function currentMode() { return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"; }

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
  toast(t("已换肤：") + (s ? t(s.name) : id) + t("（") + (currentMode() === "dark" ? t("夜间") : t("日间")) + t("）"));
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
    return '<button type="button" class="skin-card' + (s.id === cur ? " active" : "") + '" data-skin="' + s.id + '" title="' + esc(t(s.desc)) + '">'
      + '<span class="skin-prev" aria-hidden="true">'
      + '<span class="pv-side" style="background:' + p.sidebar + '"></span>'
      + '<span class="pv-main" style="background:' + p.bg + '">'
      + '<span class="pv-bar w85" style="background:' + p.accent + '"></span>'
      + '<span class="pv-bar w60" style="background:' + p.text + ';opacity:.3"></span>'
      + '<span class="pv-bar w40" style="background:' + p.accent2 + '"></span>'
      + '</span></span>'
      + '<span class="skin-meta"><span class="skin-name">' + esc(t(s.name)) + '</span>'
      + '<svg class="ico skin-check" aria-hidden="true"><use href="#i-check"></use></svg></span>'
      + '<span class="skin-desc">' + esc(t(s.desc)) + '</span>'
      + '</button>';
  }).join("");
  document.querySelectorAll("#skin-mode [data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  const tag = $("skin-cur");
  if (tag) {
    const s = SKINS.find((x) => x.id === cur);
    tag.textContent = (s ? t(s.name) : cur) + " · " + (mode === "dark" ? t("夜间") : t("日间"));
  }
}

/* ---------------------------------------------------------- 代码设置：高亮主题 + 行号/换行/字号 */
/* 只管代码内容（文件弹窗 / diff 弹窗 / 预览卡），不受界面字号影响。偏好存 localStorage
 * （与皮肤同模式，设备级）。主题 = 一小份 --ct-* CSS 变量，作用域挂
 * [data-ctheme-mode][data-codetheme]：html 上由 applyCodeTheme 落当前生效主题，
 * 预览卡同名属性拨到另一套即可同屏双色，无需 iframe。 */
const CODE_THEMES = [
  { id: "github", name: "GitHub Light", dark: false, desc: "经典浅色，清爽不抢眼，日间界面首选" },
  { id: "vs", name: "Visual Studio Light", dark: false, desc: "VS 家族浅色，蓝紫关键词配色" },
  { id: "xcode", name: "Xcode Light", dark: false, desc: "苹果开发工具同款浅色，冷色克制" },
  { id: "solarized", name: "Solarized Light", dark: false, desc: "米黄纸感底色，长时间阅读更柔和" },
  { id: "github-dark", name: "GitHub Dark", dark: true, desc: "经典深色，夜间界面首选" },
  { id: "vs2015", name: "Visual Studio Dark", dark: true, desc: "VS 家族深色，灰蓝底更沉稳" },
  { id: "monokai", name: "Monokai", dark: true, desc: "高饱和黄紫粉，老牌编辑器名主题" },
  { id: "one-dark", name: "One Dark", dark: true, desc: "Atom 出品的均衡深色，蓝灰底不刺眼" },
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
      '<option value="' + x.id + '"' + (x.id === codeThemeFor(dark ? "dark" : "light") ? " selected" : "") + ">" + esc(t(x.desc)) + "</option>").join("");
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
    if (tt) tt.textContent = th ? t(th.desc) : thId;
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
    if (ms.length) out.push({ id: p.id, name: p.name, models: ms,
      protocol: p.protocol || "", caps: p.wire_caps || {} });
  }
  return out;
}

/* 供应商协议标签：auto 显示成「自动 · 实测 wire 列表」，便于一眼看出能注入什么 */
function protoLabel(p) {
  const proto = (p || {}).protocol || "";
  if (proto !== "auto") return proto;
  const caps = Object.keys((p || {}).wire_caps || {});
  return caps.length ? t("自动 · ") + caps.join("/") : t("自动（未探测）");
}

/* 供应商可否按 allow 里的某协议注入：显式协议或「模型接入」页适配测试
 * 通过的 wire_caps（auto 供应商全靠它）。返回命中的协议名，不适配返回空串。
 * 与 modelhub._entry_endpoint 同规则——它同时就是「注入本 CLI 实际走的 wire」，
 * 勾选面板据此标死解析时会被跳过的条目。 */
function provAdaptedProto(p, allow, kind) {
  if (!p) return "";
  // codex 0.154+ 只讲 responses wire：chat-only 端点（显式 wire_api=chat 或
  // wire_caps 实测为 chat）对 codex 等于没有可用协议——与后端 resolve_binding
  // 的剔除规则同源（2026-09-18 重写任务案：链显示健康、运行时必剔死的假象）
  const codexOnly = /codex/.test(String(kind || "").toLowerCase());
  const chatBlocked = (w) => codexOnly && (w || "responses") === "chat";
  if (allow.indexOf(p.protocol) >= 0) {
    if (chatBlocked(p.wire_api)) return "";
    return p.protocol;
  }
  const caps = p.wire_caps || {};
  return allow.find((pr) => (caps[pr] || {}).base
    && !chatBlocked((caps[pr] || {}).wire_api)) || "";
}

/* 死因细化：协议适配失败里最常见的一种是「openai 面实测只探到 chat 形态」
 * （codex 0.154+ 只讲 responses，其余 CLI 不受影响）。按真实死因说话——
 * 模型接入页明明亮着 openai ✓，链上却笼统说「协议不匹配」，看着就像自相矛盾。 */
function chatShapedFace(pv, allow) {
  if (!pv) return false;
  const chat = (w) => (w || "responses") === "chat";
  if (allow.indexOf(pv.protocol) >= 0 && chat(pv.wire_api)) return true;
  const caps = pv.wire_caps || {};
  return allow.some((pr) => (caps[pr] || {}).base && chat((caps[pr] || {}).wire_api));
}

/* CLI 允许注入的供应商 wire 协议——与 modelhub.resolve_binding 的 allowed 规则
 * 保持一致（codex 只吃 openai wire、claude 只吃 anthropic、dsh 只吃 OpenAI
 * 兼容端点，其余两种皆可）。后端会跳过不匹配的链条目，这里在选择层就挡住。 */
function bindAllowedProtocols(kind) {
  const k = String(kind || "").toLowerCase();
  if (/codex/.test(k)) return ["openai"];
  if (/claude/.test(k)) return ["anthropic"];
  if (/dsh|deepseek/.test(k)) return ["openai"];
  return ["anthropic", "openai"];
}

/* 默认模型下拉：按供应商分组，只列该 CLI 协议能吃的供应商（auto 靠 wire_caps 命中）；
 * 当前值不在过滤后列表里时保留为选项，避免显示丢失 */
function modelSelectHtml(id, current, kind) {
  const cur = fmtModel(current);
  const allow = bindAllowedProtocols(kind);
  const groups = modelGroups().filter((g) => {
    const p = (S.providers || []).find((x) => x.id === g.id);
    return provAdaptedProto(p, allow, kind);
  });
  let opts = '<option value="">' + t("（未设置）") + '</option>';
  if (cur && !groups.some((g) => g.models.includes(cur))) {
    opts += '<option value="' + esc(cur) + '" selected>' + esc(cur) + t("（当前值）") + "</option>";
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
  if (S.catalogChecking && u.status === "unknown") return '<span class="tag">' + t("检查更新中…") + '</span>';
  if (u.status === "updatable") return '<span class="tag upd">' + t("有新版本 ") + esc(u.latest || "") + "</span>";
  if (u.status === "current") return '<span class="tag ok">' + t("已是最新") + '</span>';
  if (u.status === "unsupported") return '<span class="tag">' + t("该渠道无法自动检查") + '</span>';
  return "";
}

/* 进入智能体目录页自动检查各 CLI 是否有新版本（后端 10 分钟缓存，前端 5 分钟节流） */
async function autoCheckUpdates() {
  if (Date.now() - (S.updateCheckAt || 0) < 5 * 60 * 1000) return;
  S.updateCheckAt = Date.now();
  try { await api("/api/catalog/check-updates", { method: "POST", busy: false }); } catch (e) { /* 忽略 */ }
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

/* 一键推荐绑定：给每个链为空的 CLI 按协议适配规则预填一条主模型。
 * 推荐顺序：显式协议原生匹配 > auto 且 wire_caps 命中；同分按 priority /
 * 数组序稳定。默认模型停用的厂商直接不推荐——它当前键位就是坏的，绑上去
 * 解析照样失败；没有合适的就什么都不绑（2026-09-17 用户拍板，不再保留
 * 「只剩它也上」的兜底）。只预填草稿态（dirty），逐条看清后各自保存
 * 或「全部保存」，健康链绝不被覆盖。 */
function recommendFor(c) {
  // 目录页「默认模型」下拉用 orch_kind || id 判协议（仅管理条目没有 orch_kind），
  // 推荐口径与下拉过滤保持一致；绑定页条目恒有 orch_kind，不受影响
  const allow = bindAllowedProtocols(c.orch_kind || c.id);
  const badDefault = (p) => (p.models || []).some((m) => m.name === p.model &&
    (m.hidden || m.enabled === false));
  const provs = (S.providers || []).filter((p) => !badDefault(p) && chainProvUsable(p, allow, c.orch_kind || c.id));
  if (!provs.length) return null;
  const score = (p) => (allow.indexOf(p.protocol) >= 0 ? 0 : 1) * 1000 + (p.priority || 0);
  provs.sort((a, b) => score(a) - score(b));
  const p = provs[0];
  const models = (p.models || []).filter((m) => !m.hidden && m.enabled !== false);
  if (!models.length) return null;
  return { p: p.id, m: models[0].name };
}

/* 供应商对某 CLI 是否「推荐可用」：启用、协议适配，且至少有一把不在冷却期的
 * KEY（全冷却=欠费失效，推荐了也白推荐——解析层照样失败）。 */
function chainProvUsable(p, allow, kind) {
  if (!p || p.enabled === false) return false;
  if (!provAdaptedProto(p, allow, kind)) return false;
  const ks = p.keys || [];
  if (!ks.length) return true; // 老数据单 KEY（api_key 镜像）：无 keys 结构视为可用
  return ks.some((k) => k.enabled !== false && !k.cooling);
}

/* 绑定链里的死条目：厂商已删/停用/协议不再适配/KEY 全冷却，或该模型已被
 * 隐藏停用。这些条目解析时会被跳过，留在链首等于配了个寂寞。 */
function chainDeadReasons(c, chain) {
  const allow = bindAllowedProtocols(c.orch_kind);
  const dead = (chain || []).map((x) => {
    if (!x.p) return ""; // 空 p = CLI 默认凭据，不算死
    const pv = (S.providers || []).find((p) => p.id === x.p);
    if (!pv) return t("供应商已删除");
    if (pv.enabled === false) return t("供应商已停用");
    if (!provAdaptedProto(pv, allow, c.orch_kind))
      return chatShapedFace(pv, allow)
        ? t("openai 面仅 chat 形态（codex 只讲 responses）")
        : t("协议不匹配");
    const ks = pv.keys || [];
    if (ks.length && !ks.some((k) => k.enabled !== false && !k.cooling))
      return t("密钥全部冷却中");
    const mm = (pv.models || []).find((m) => m.name === x.m);
    if (mm && (mm.hidden || mm.enabled === false)) return t("模型已停用");
    return "";
  });
  return dead;
}

/* 单条显式绑定链的推荐修复动作：需要改返回新链，不动返回 null。
 * 空链代表默认自动调度，不能悄悄写成持久绑定；已有链失效时才推荐替代链。 */
function bindRepairAction(c, st, includeEmpty) {
  const rec = recommendFor(c);
  if (!st.chain.length) return includeEmpty && rec ? [rec] : null;
  const dead = chainDeadReasons(c, st.chain);
  const allDead = dead.every((d) => d);
  const headDead = dead[0] !== ""; // 空 p = CLI 默认凭据，是有意配置不算死
  if (allDead) return rec ? [rec] : null;
  if (headDead && rec && chainKey(st.chain) !== chainKey([rec]))
    return [rec].concat(st.chain.filter((x) => !(x.p === rec.p && x.m === rec.m)));
  return null;
}

function autoBindAll() {
  const targets = (S.catalog || []).filter((c) => c.installed && c.orch_kind);
  let filled = 0, skipped = 0, noProv = 0, refilled = 0;
  for (const c of targets) {
    const st = bindSelById(c.id);
    const act = bindRepairAction(c, st, true);
    if (!act) {
      // 没动它：分清「健康链无需推荐」和「没有可推荐的」两种落空
      if (!st.chain.length) { noProv++; continue; }
      const dead = chainDeadReasons(c, st.chain);
      if (dead.every((d) => d)) noProv++; else skipped++;
      continue;
    }
    const wasEmpty = !st.chain.length;
    st.chain = act;
    st.dirty = true;
    if (wasEmpty) filled++; else refilled++;
  }
  bindRepaint();
  if (filled && refilled) {
    toast(t("已为 %1 个空链 CLI 预填推荐，并修复 %2 条失效链——确认后保存。")
      .replace("%1", filled).replace("%2", refilled));
  } else if (filled) {
    toast(t("已为 %1 个 CLI 预填推荐模型，确认无误后点各卡片「保存」，或点右上「全部保存」。").replace("%1", filled));
  } else if (refilled) {
    toast(t("已为 %1 条失效链重新推荐（原厂商失效/停用/模型停用）——确认后点「全部保存」生效。")
      .replace("%1", refilled));
  } else if (noProv && !skipped) {
    toast(t("没有可推荐的：先到「模型接入」页导入与 CLI 协议匹配的供应商。"), true);
  } else if (skipped && !filled && !refilled) {
    toast(t("所有 CLI 都已配置模型链，无需推荐。"));
  }
}

/* 厂商/模型停用·删除后不再自动改写绑定链（2026-09-22 用户拍板：不要去动
 * 模型调度）——后端在停用/删除时已把死条目从链里剔除（modelhub._prune_binding_chains），
 * 空链回落 CLI 默认；要补推荐用「一键推荐绑定」手动来。 */

async function saveAllBindings() {
  const targets = (S.catalog || []).filter((c) => c.installed && c.orch_kind);
  const dirty = targets.filter((c) => (S.bindSel[c.id] || {}).dirty);
  for (const c of dirty) await saveBinding(c.id);
  if (dirty.length) toast(t("已保存 %1 个 CLI 的绑定。").replace("%1", dirty.length));
}

/* 有未保存草稿时显示「全部保存」按钮（在 poll 重绘里顺带刷新） */
function syncSaveAllBtn() {
  const btn = $("btn-saveall-bind");
  if (!btn) return;
  const anyDirty = Object.values(S.bindSel || {}).some((st) => st.dirty);
  btn.classList.toggle("hidden", !anyDirty);
}

function bindModelBox(c, provId) {
  const st = bindSelById(c.id);
  const allow = bindAllowedProtocols(c.orch_kind);
  const chips = st.chain.length
    ? st.chain.map((c2, i) => {
        const pname = c2.p ? provName(c2.p) : t("CLI 默认凭据");
        const pv = c2.p ? (S.providers || []).find((p) => p.id === c2.p) : null;
        const adapted = provAdaptedProto(pv, allow, c.orch_kind);
        const dead = c2.p && !adapted;
        // auto 供应商按实测 wire 注入是常态，说「自动」；显式协议靠适配才通的
        // 才叫「已适配」——两者含义不同，别混成一句。
        const badge = !c2.p || !adapted ? ""
          : (pv.protocol === "auto"
             ? ' <span class="hint ok">✓ ' + esc(t("自动 · %1 wire").replace("%1", adapted)) + "</span>"
             : (pv.protocol !== adapted
                ? ' <span class="hint ok">✓ ' + esc(t("已适配（%1 wire）").replace("%1", adapted)) + "</span>"
                : ""));
        return '<span class="ochip' + (i === 0 ? " primary" : "") + '">' +
          "<b>" + (i === 0 ? t("主") : t("备")) + "</b>" + esc(pname) + " · " + esc(c2.m) +
          (modelHasImage(c2.p, c2.m)
            ? ' <span class="tag ok" title="' + esc(t("支持图片输入")) + '">' + t("图") + "</span>" : "") +
          (dead ? ' <span class="hint warn">⚠ ' + esc(chatShapedFace(pv, allow)
            ? t("openai 面仅 chat 形态（codex 只讲 responses），解析时跳过")
            : t("协议不匹配，解析时跳过")) + "</span>" : badge) +
          (i > 0 ? '<button class="mini" data-m="' + esc(c2.m) + '" data-p="' + esc(c2.p) +
                  '" title="' + t("设为主模型") + '" onclick="bindPromote(\'' + esc(c.id) + '\', this)">' +
                  '<svg class="ico" aria-hidden="true"><use href="#i-arrow-up"></use></svg></button>' : "") +
          '<button class="mini" data-m="' + esc(c2.m) + '" data-p="' + esc(c2.p) +
            '" title="' + t("移除") + '" onclick="bindRemove(\'' + esc(c.id) + '\', this)">×</button>' +
          "</span>";
      }).join("")
    : '<span class="hint">' + t("未设置") + (provId
        ? t("（按供应商/难度自动解析——供应商协议不匹配或被停用时解析为空，相关步骤将判失败）")
        : t("（自动推荐厂商与模型；没有兼容可用供应商时才使用 CLI 自身配置）")) + "</span>";
  return '<div class="field"><label>' + t("运行时模型链（跨厂商，最多 ") + MAX_ORCH_MODELS + t(" 条）") + "</label>" +
    '<div class="orch-row">' + chips +
    '<button class="ghost small" onclick="bindToggle(\'' + esc(c.id) + '\')">' +
    (st.open ? t("收起") : t("＋ 添加")) + "</button>" +
    (st.dirty ? ' <span class="hint">' + t("有未保存改动") + '</span>' : "") +
    "</div>" +
    (st.chain.length ? '<div class="hint">' + t("链先生效（覆盖「按难度自动选模型」）；主模型瞬态失败自动降级到下一条——可以是另一家厂商。") + '</div>' : "") +
    (st.open ? bindPanel(c) : "") + "</div>";
}

function bindPanel(c) {
  const st = bindSelById(c.id);
  const allow = bindAllowedProtocols(c.orch_kind);
  const all = modelGroups();
  // 原生协议匹配，或适配测试过 allow 里某条 wire 的供应商都可勾选
  const groups = all.filter((g) => provAdaptedProto(g, allow, c.orch_kind));
  const hiddenN = all.length - groups.length;
  if (!groups.length) {
    return '<div class="ohint">' + esc(all.length
      ? t("该 CLI 只接受特定 wire 协议的供应商，当前没有匹配项——先到「模型接入」页导入对应协议的网关。")
      : t("还没有可用模型——先到「模型接入」页导入供应商并获取模型列表。")) + "</div>";
  }
  return '<div class="opanel">' +
    '<div class="ohint">' + t("勾选该 CLI 编排运行时的模型（可跨供应商混选）：第 1 条是主模型，其余按顺序作降级备选，每条自带该供应商的凭据注入。") +
    (hiddenN ? "<br>" + esc(t("%1 个供应商 wire 协议不匹配已隐藏（降级只在同协议网关间进行）。").replace("%1", hiddenN)) : "") +
    "</div>" +
    groups.map((g) =>
      '<div class="ogroup"><div class="ogname">' + esc(g.name) +
      ' <span class="tag">' + (g.protocol === "auto"
        ? t("自动 · 注入该厂商凭据")
        : (provAdaptedProto(g, allow, c.orch_kind) === g.protocol
           ? t("注入该厂商凭据") : t("已适配 · 注入该厂商凭据"))) + '</span></div>' +
      g.models.map((m) => {
        // 本供应商注入本 CLI 实际走的 wire（与后端 _entry_endpoint 同规则）；
        // 无命中 = 勾了也会被解析层整条跳过，标死防止白配
        const wire = provAdaptedProto(g, allow, c.orch_kind);
        const dead = !wire;
        const has = st.chain.some((x) => x.p === g.id && x.m === m);
        return '<label class="oitem' + (dead ? " dim" : "") + '"><input type="checkbox" value="' + esc(m) + '" data-p="' + esc(g.id) + '"' +
          (has ? " checked" : "") + (dead ? " disabled" : "") +
          " onchange=\"bindPick('" + esc(c.id) + "', this, this.checked)\">" + esc(m) +
          (modelHasImage(g.id, m)
            ? ' <span class="tag ok" title="' + esc(t("支持图片输入")) + '">' + t("图") + "</span>" : "") +
          (dead ? ' <span class="hint warn">' + esc(t("无可用 wire，解析时跳过")) + "</span>" : "") +
          "</label>";
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

/* 该条目有管理操作在跑（queued/running）时，写操作按钮置灰防重复触发；
   页面刷新后按钮恢复，后端同条目去重闸仍然兜底 */
function mgmtBusy(id) {
  const st = (S.mgmt || {})[id];
  return !!st && (st.status === "queued" || st.status === "running");
}

function card(c) {
  const orchBox = c.orch_kind
    ? '<label class="toggle"><input type="checkbox" ' + (c.orch_enabled ? "checked" : "") +
      ' onchange="toggleOrch(\'' + esc(c.id) + '\', this.checked)">' + t(" 参与编排") + '</label>'
    : '<span class="tag">' + t("仅管理") + '</span>';
  const dis = mgmtBusy(c.id) ? " disabled" : "";
  const hasModels = modelGroups().length > 0;
  const modelBox = c.config_writable
    ? (hasModels
        ? '<div class="field"><label>' + t("默认模型（写入配置文件）") + '</label><div class="input-row">' +
          modelSelectHtml(c.id, c.model, c.orch_kind || c.id) +
          '<button class="ghost small" onclick="saveModel(\'' + esc(c.id) + '\')">' + t("保存") + '</button></div></div>'
        : '<div class="field"><label>' + t("默认模型（写入配置文件）") + '</label>' +
          '<div class="hint warn">' + t("还没有可选模型：先到「模型接入」页导入供应商并获取模型列表。") + '</div></div>')
    : (c.model ? '<div class="facts">' + t("模型：") + '<b>' + esc(fmtModel(c.model)) + "</b></div>" : "");
  const ops = [
    // 一键打开（web 类开浏览器 / console 类新终端跑 TUI）：已安装且配了 launch 才出
    c.installed && c.launch ? '<button class="primary small" onclick="openAgent(\'' + esc(c.id) + '\')">' +
      (c.launch.kind === "web" ? t("打开网页") : t("打开")) + "</button>" : "",
    c.installed && c.orch_kind ? '<button class="ghost small"' + dis + ' onclick="mgmt(\'' + esc(c.id) + '\', \'smoke\')">' + t("冒烟测试") + '</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small"' + dis + ' onclick="mgmt(\'' + esc(c.id) + '\', \'upgrade\')">' + t("升级") + '</button>' : "",
    c.installed && c.has_upgrade ? '<button class="ghost small" onclick="checkUpdate(\'' + esc(c.id) + '\')">' + t("检查更新") + '</button>' : "",
    !c.installed && c.has_install ? '<button class="ghost small"' + dis + ' onclick="mgmt(\'' + esc(c.id) + '\', \'install\')">' + t("安装") + '</button>' : "",
    c.installed ? '<button class="ghost small" onclick="showMgmtLog(\'' + esc(c.id) + '\')">' + t("日志") + '</button>' : "",
    !c.installed && !c.has_install ? '<span class="hint">' + t("安装命令待配置（编辑 data/catalog.json）") + '</span>' : "",
  ].join("");
  return '<div class="card">' +
    '<div class="head"><span class="name">' + esc(c.name) + "</span>" +
    (c.installed ? statusChip("done") : '<span class="tag">' + t("未安装") + '</span>') + updateChip(c) +
    // 卸载放卡片右上角（与状态徽标同行），不再吊在操作行尾部
    (c.installed && c.uninstall_cmd ? '<button class="danger small"' + dis + ' onclick="mgmt(\'' + esc(c.id) + '\', \'uninstall\')">' + t("卸载") + '</button>' : "") +
    "</div>" +
    '<div class="note">' + esc(t(c.note || "")) + "</div>" +
    '<div class="facts">' + t("版本 ") + '<b>' + esc(c.version || "-") + "</b>" + t("　模型 ") + "<b>" + esc(fmtModel(c.model) || "-") + "</b>" +
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
    ? '<span class="hint">' + t("版本 ") + esc(st.versionBefore || "?") + " → <b>" +
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
    (st.runId ? '<button class="ghost small" onclick="openRunInRuns(\'' + esc(st.runId) + '\')">' + t("完整运行") + '</button>' : "") +
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

/* 一键绑定推荐模型（智能体目录页）：给「默认模型」还空着的已安装智能体按
 * 协议适配规则（复用绑定页 recommendFor 评分）挑一家厂商的启用模型并直接
 * 写入该 CLI 自身配置（写入侧先自动 .bak 备份）。已配置的不动——目录页的
 * 默认模型会写进 CLI 全局配置，手工设置过的值（含 CLI 订阅自带的默认）不
 * 该被一键覆盖；要换模型逐卡下拉改就行。 */
async function catAutoBindAll() {
  const targets = (S.catalog || []).filter((c) => c.installed && c.config_writable);
  if (!targets.length) { toast(t("没有支持写入默认模型的已安装智能体。"), true); return; }
  const empty = targets.filter((c) => !fmtModel(c.model));
  if (!empty.length) { toast(t("所有已安装智能体都已配置默认模型，无需绑定。")); return; }
  let done = 0, failed = 0, noProv = 0, firstErr = "";
  for (const c of empty) {
    const rec = recommendFor(c);
    if (!rec) { noProv++; continue; }
    try {
      const r = await api("/api/catalog/" + encodeURIComponent(c.id) + "/model",
        { method: "POST", body: JSON.stringify({ model: rec.m }) });
      c.model = r.model || rec.m;
      done++;
    } catch (e) {
      failed++;
      if (!firstErr) firstErr = c.name + t("：") + e.message;
    }
  }
  S.catSig = null;
  renderCatalog();
  if (done && failed) {
    toast(t("已为 %1 个智能体写入推荐模型，%2 个失败。").replace("%1", done).replace("%2", failed) +
      (firstErr ? " " + firstErr : ""), true);
  } else if (done) {
    toast(t("已为 %1 个智能体写入推荐模型（原配置已自动备份 .bak）。").replace("%1", done));
  } else if (failed) {
    toast(t("推荐模型写入失败：") + firstErr, true);
  } else {
    toast(t("没有可推荐的：先到「模型接入」页导入与 CLI 协议匹配的供应商。"), true);
  }
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
    if (!await uiConfirm(t("确定卸载 ") + entry.name + t("？\n\n将执行：\n") + cmd +
                 t("\n\n该 CLI 会从本机移除（配置文件保留）。此操作不可撤销。"), { ok: t("卸载"), danger: true })) return;
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
  if (r.deduped) toast(t("该条目已有进行中的任务，已转为跟踪该任务"));
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
                        "/log?step=" + encodeURIComponent(last.log || "") + "&pretty=1");
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
        t("\n最新版本：") + (r.latest || t("(未知)")) +
        t("\n可更新：") + (up === true ? t("是") : up === false ? t("否") : t("未知")) +
        (r.note ? t("\n说明：") + r.note : ""),
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
    '<div class="item"><div class="t"><span class="name">' + flowIconHtml(f) + " " + esc(t(f.name)) +
    '</span><span class="tag">' + esc(f.id) + "</span>" +
    '<span class="tag">' + (f.engine === "code" ? t("代码引擎")
      : f.engine === "direct" ? t("直连引擎") : t("评审引擎")) + "</span>" +
    (f.serial ? '<span class="tag">' + t("连载 ") + f.serial.chapters + t(" 章") + "</span>" : "") +
    (f.builtin ? '<span class="tag ok">' + t("预置") + '</span>' : '<span class="tag">' + t("自定义") + '</span>') +
    (f.edited ? '<span class="tag">' + t("已改") + '</span>' : "") +
    '<button class="ghost small" onclick="flowForm(\'' + esc(f.id) + '\')">' + t("编辑") + '</button>' +
    (f.builtin
      ? (f.edited ? '<button class="ghost small" onclick="flowReset(\'' + esc(f.id) + '\')">' + t("恢复默认") + '</button>' : "")
      : '<button class="danger small" onclick="deleteFlow(\'' + esc(f.id) + '\')">' + t("删除") + '</button>') +
    '</div><div class="desc">' + esc(t(f.note || "")) +
    (f.rubric ? t("　维度：") + esc(f.rubric.map((d) => t(d)).join(" / ")) : "") +
    (f.threshold ? t("　阈值：") + f.threshold : "") +
    (f.serial ? t("　每章 ") + f.serial.words_per_chapter + t(" 字") : "") + "</div></div>").join("");
  const body = '<p class="hint">' + t("预置流程可直接编辑（阈值/轮数/维度/章节数/提示词），改动随时可「恢复默认」；") +
    t("自定义流程只需填名称与引擎，其余留空走默认。") + '</p>' +
    '<div class="list">' + rows + "</div>" +
    '<button class="primary" style="margin-top:10px" onclick="flowForm()">' + t("＋ 新建自定义流程") + '</button>';
  openModal(t("🧩 任务类型管理"), body, "");
}

async function flowReset(fid) {
  if (!await uiConfirm(t("把「") + fid + t("」恢复为内置默认配置？"), { ok: t("恢复") })) return;
  try { await api("/api/flows/" + encodeURIComponent(fid) + "/reset", { method: "POST" }); }
  catch (e) { toast(t("恢复失败：") + e.message, true); return; }
  invalidateFlowRubricDraft(fid);
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
    '<div class="field"><label>' + t("名称 ") + '<span class="req">*</span></label><input id="fl-name" value="' +
      esc(f ? f.name : "") + '" placeholder="' + t("例：播客脚本") + '"></div>' +
    '<div class="field"><label>' + t("引擎 ") + '<span class="req">*</span></label><select id="fl-engine" data-fid="' +
      esc(f ? f.id : "") + '"' +
      (engLocked ? " disabled" : "") + '>' +
      '<option value="review"' + (engine === "review" ? " selected" : "") + '>' + t("评审引擎（起草 → 多维评审 → 修订 → 门禁）") + '</option>' +
      '<option value="code"' + (engine === "code" ? " selected" : "") + '>' + t("代码引擎（实现 → 验证 → 评审 → 修复）") + '</option>' +
      '<option value="direct"' + (engine === "direct" ? " selected" : "") + '>' + t("直连引擎（单智能体直达，无拆解/评审，快）") + '</option>' +
      "</select></div></div>" +
    (f ? "" : '<div class="field"><label>' + t("流程 ID（留空 = 按名称自动生成）") + '</label><input id="fl-id" placeholder="' + t("小写字母开头，可留空") + '"></div>') +
    '<details id="fl-adv"' + (f ? " open" : "") + '><summary>' + t("进阶设置（可留空，走引擎默认）") + '</summary><div class="adv-body">' +
    '<div id="fl-review-fields"' + (engine === "review" ? "" : ' class="hidden"') + ">" +
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("产出文件名") + '</label><input id="fl-manuscript" value="' + esc(f && f.manuscript ? f.manuscript : "") + '" placeholder="' + t("留空 = 按流程 ID 生成") + '"></div>' +
    '<div class="field"><label>' + t("发布阈值（1-10）") + '</label><input id="fl-threshold" type="number" step="0.5" min="1" max="10" value="' + (f && f.threshold ? f.threshold : "") + '" placeholder="' + t("留空 = 7.0") + '"></div>' +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("评审轮数（1-5）") + '</label><input id="fl-rounds" type="number" min="1" max="5" value="' + (f && f.rounds ? f.rounds : "") + '" placeholder="' + t("留空 = 2") + '"></div>' +
    '<div class="field"><label>' + t("评审维度（逗号分隔）") + '</label><input id="fl-rubric" value="' + esc(f && f.rubric ? f.rubric.join(", ") : "") + '" placeholder="' + t("留空 = 内容, 结构, 表达") + '"></div>' +
    "</div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("连载章节数（留空 = 单稿件）") + '</label><input id="fl-chapters" type="number" min="2" max="20" value="' + (f && f.serial ? f.serial.chapters : "") + '" placeholder="' + t("例：8") + '"></div>' +
    '<div class="field"><label>' + t("每章约字数") + '</label><input id="fl-words-per-ch" type="number" min="500" max="8000" step="100" value="' + (f && f.serial ? f.serial.words_per_chapter : "") + '" placeholder="' + t("例：2500") + '"></div>' +
    '<div class="field"><label>' + t("同章赛马稿件数") + '</label><input id="fl-variants" type="number" min="1" max="3" step="1" value="' + (f && f.serial ? (f.serial.variants || 1) : 1) + '" placeholder="' + t("1 = 关闭") + '"></div>' +
    '<div class="field"><label>' + t("分卷：每卷章数（留空 = 不分卷）") + '</label><input id="fl-vol-chapters" type="number" min="2" max="200" step="1" value="' + (f && f.serial && f.serial.volume_chapters ? f.serial.volume_chapters : "") + '" placeholder="' + t("例：20") + '"></div>' +
    "</div>" +
    '<div class="field"><label>' + t("起草提示词（可选，占位符 __FILE__ __GOAL__ __CONTEXT__ __SKILLS__）") + '</label><textarea id="fl-draft" rows="3" placeholder="' + t("留空 = 内置通用模板") + '">' + esc(f && f.draft_prompt ? f.draft_prompt : "") + "</textarea></div>" +
    '<div class="field"><label>' + t("评审提示词（可选，占位符 __DIMKEYS__ __MANUSCRIPT__）") + '</label><textarea id="fl-critique" rows="3" placeholder="' + t("留空 = 内置通用模板") + '">' + esc(f && f.critique_prompt ? f.critique_prompt : "") + "</textarea></div>" +
    "</div>" +
    '<div class="field"><label>' + t("一句话说明（显示在流程列表）") + '</label><input id="fl-note" value="' + esc(f ? (f.note || "") : "") + '" placeholder="' + t("例：技术播客单集脚本产出") + '"></div>' +
    "</div></details></div>";
  openModal(f ? (t("编辑流程：") + esc(f.name) + (isBuiltin ? t("（预置）") : "")) : t("新建自定义流程"), body, "");
  const foot = $("modal-foot");
  if (foot) foot.innerHTML = '<button class="primary" onclick="saveFlow()">' + t("保存") + '</button>' +
    '<button class="ghost" onclick="closeModal()">' + t("取消") + '</button>';
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
    if (ch >= 2) {
      payload.serial = {
        chapters: ch,
        words_per_chapter: parseInt($("fl-words-per-ch").value, 10) || 2500,
        variants: Math.max(1, Math.min(3, parseInt($("fl-variants").value, 10) || 1)),
      };
      const vp = parseInt(($("fl-vol-chapters") || {}).value, 10);
      if (vp >= 2) payload.serial.volume_chapters = vp;   // 流程默认分卷（任务级可覆盖）
    }
    const dp = $("fl-draft").value.trim();
    if (dp) payload.draft_prompt = dp;
    const cp = $("fl-critique").value.trim();
    if (cp) payload.critique_prompt = cp;
  }
  try {
    await api("/api/flows", { method: "POST", body: JSON.stringify(payload) });
  } catch (e) { toast(t("保存失败：") + e.message, true); return; }
  closeModal();
  invalidateFlowRubricDraft(id);
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
    '<div class="card"><div class="head"><span class="name">📚 ' + esc(t(p.name)) + "</span>" +
    '<span class="tag">' + esc((p.scopes || []).map((s) => t(s)).join(" / ")) + "</span>" +
    (p.enabled ? '<span class="tag ok">' + t("启用中") + "</span>" : '<span class="tag">' + t("已停用") + "</span>") + "</div>" +
    '<div class="note">' + esc(t(p.note || "")) + "</div>" +
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
    '<span class="name">' + esc(t(x.title)) + "</span>" +
    (x.kind === "做法" ? '<span class="tag ok" title="' + t("已验证有效的做法（程序性记忆）") + '">' + t("做法") + "</span>" : "") +
    (x.category ? '<span class="tag cat" style="color:' + (CAT_COLOR[x.category] || "var(--muted)") + '" ' +
      'onclick="skillCatFilter(\'' + esc(x.category) + '\')" title="' + t("只看该类") + '">' + esc(t(x.category)) + "</span>" : "") +
    '<span class="tag">' + esc(x.scope === "*" ? t("通用") : x.scope) + "</span>" +
    (x.seen > 1 ? '<span class="tag">' + t("出现 ") + x.seen + t(" 次") + "</span>" : "") +
    (x.hits ? '<span class="tag">' + t("已注入 ") + x.hits + t(" 次") + "</span>" : "") +
    (x.won > 0 ? '<span class="tag ok" title="' + t("注入后任务过审的次数（真实帮上忙）") + '">' + t("有效 ") + x.won + "</span>" : "") +
    (x.lost > 0 ? '<span class="tag" style="color:var(--bad)" title="' + t("注入后任务仍失败的次数（没防住）") + '">' + t("失守 ") + x.lost + "</span>" : "") +
    (x.enabled === false ? '<span class="tag">' + t("已停用") + "</span>" : "") + "</div>" +
    '<div class="note">' + esc(t(x.content)) + "</div>" +
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

/* ---------------------------------------------------------- 知识库 */
/* 运行产出自动提炼（草稿→人工转正）+ 手动新建的个人知识库；
 * 进页拉一次，操作后重拉，搜索/状态/标签过滤全在客户端。 */
let KB = { data: null, q: "", tag: "", status: "" };

async function loadKnowledge() {
  try { KB.data = await api("/api/knowledge"); } catch (e) { KB.data = null; }
  renderKnowledge();
}

function renderKnowledge() {
  const box = $("kb-cards");
  if (!box || !KB.data) return;
  const entries = KB.data.entries || [];
  const drafts = entries.filter((x) => (x.status || "draft") !== "approved").length;
  // 状态 chips 只在还有草稿时出现（默认直接转正后草稿是例外态，不值得常驻占位）；
  // chips 隐藏时同步清掉残留的草稿过滤态，否则列表会被看不见的过滤器扣成空
  if (KB.status === "draft" && !drafts) KB.status = "";
  const stBox = $("kb-status-chips");
  if (stBox) {
    const cnt = (s) => s ? entries.filter((x) => (x.status || "draft") === s).length : entries.length;
    stBox.innerHTML = drafts ? [["", "全部"], ["draft", "草稿"], ["approved", "已转正"]].map((p) =>
      '<button class="cat-chip' + (KB.status === p[0] ? " active" : "") + '" onclick="kbSetFilter(\'status\',\'' + p[0] + '\')">' +
      esc(t(p[1])) + "<b>" + cnt(p[0]) + "</b></button>").join("") : "";
  }
  const tags = KB.data.tags || [];
  const tagBox = $("kb-tag-chips");
  if (tagBox) {
    tagBox.innerHTML = tags.length
      ? '<button class="cat-chip' + (!KB.tag ? " active" : "") + '" onclick="kbSetFilter(\'tag\',\'\')">' + esc(t("全部标签")) + "</button>" +
        tags.map((tg) => '<button class="cat-chip' + (KB.tag === tg ? " active" : "") +
          '" onclick="kbSetFilter(\'tag\',\'' + jsq(tg) + '\')">' + esc(tg) + "<b>" + (KB.data.tag_counts[tg] || 0) + "</b></button>").join("")
      : "";
  }
  let shown = entries;
  if (KB.status) shown = shown.filter((x) => (x.status || "draft") === KB.status);
  if (KB.tag) shown = shown.filter((x) => (x.tags || []).includes(KB.tag));
  if (KB.q) shown = shown.filter((x) =>
    (x.title || "").toLowerCase().includes(KB.q) || (x.body || "").toLowerCase().includes(KB.q) ||
    (x.tags || []).some((tg) => tg.toLowerCase().includes(KB.q)));
  const cnt = $("kb-count");
  if (cnt) cnt.textContent = shown.length ? shown.length + t(" / 共 ") + entries.length + t(" 条") : t("（暂无）");
  box.innerHTML = shown.map(kbCard).join("") ||
    '<div class="empty">' + t("还没有知识条目——完成一次产出型任务后自动提炼并直接转正，也可以点右上角手动新建。") + "</div>";
}

function kbCard(x) {
  const draft = (x.status || "draft") !== "approved";
  // 标题独占首行（.name 不再被标签挤成省略号）；状态徽章与可点标签合并为
  // 标题下方的单行元信息区，视觉层级：标题 > 状态/标签 > 正文 > 元信息 > 操作。
  // 默认即转正：approved 不再挂「已转正」徽章（全员都有 = 没有信息量），
  // 只把例外态「草稿」标出来（琥珀圆点）。
  const badges =
    (draft ? '<span class="tag warn"><span class="cdot"></span>' + esc(t("草稿")) + "</span>" : "") +
    (x.stale ? '<span class="tag" title="' + esc(t("事实较旧，注入时会标注可能过期")) + '">' + esc(t("可能过期")) + "</span>" : "") +
    (x.confidence === "high" ? '<span class="tag ok" title="' + esc(t("产出材料中有明确来源或数据支撑")) + '">' + esc(t("有据")) + "</span>" : "") +
    '<span class="tag">' + esc(x.scope === "*" ? t("通用") : x.scope) + "</span>" +
    (x.seen > 1 ? '<span class="tag">' + t("出现 ") + x.seen + t(" 次") + "</span>" : "") +
    (x.hits ? '<span class="tag">' + t("已注入 ") + x.hits + t(" 次") + "</span>" : "") +
    (x.enabled === false ? '<span class="tag">' + t("已停用") + "</span>" : "");
  const tags = (x.tags || []).map((tg) =>
    '<span class="tag kt" onclick="kbSetFilter(\'tag\',\'' + jsq(tg) + '\')" title="' + t("只看该标签") + '">' + esc(tg) + "</span>").join("");
  return '<div class="card' + (x.enabled === false ? " off" : "") + '" data-kb-id="' + esc(x.id) + '">' +
    '<div class="head">' +
    '<span class="name">' + esc(x.title) + "</span>" +
    '<span class="kb-meta">' + badges + tags + "</span></div>" +
    '<div class="note">' + esc(x.body) + "</div>" +
    '<div class="facts">' + (x.as_of ? t("事实截至 ") + esc(x.as_of) : "") +
    (x.source ? (x.as_of ? '<span class="sep">·</span>' : "") +
      '<a href="#" onclick="openRun(\'' + esc(x.source) + '\');return false">' + t("来源运行") + "</a>" : "") + "</div>" +
    '<div class="ops">' +
    (draft ? '<button class="ghost small" onclick="kbOp(\'' + esc(x.id) + '\', \'approve\')">' + t("转正") + "</button>" : "") +
    '<button class="ghost small" onclick="kbEditOpen(\'' + esc(x.id) + '\')">' + t("编辑") + "</button>" +
    '<button class="ghost small" onclick="kbOp(\'' + esc(x.id) + '\', \'' + (x.enabled === false ? "enable" : "disable") + '\')">' + (x.enabled === false ? t("启用") : t("停用")) + "</button>" +
    '<button class="danger small" onclick="kbOp(\'' + esc(x.id) + '\', \'delete\')">' + t("删除") + "</button>" +
    "</div></div>";
}

function kbSetFilter(k, v) {
  KB[k] = v || "";
  renderKnowledge();   // 客户端过滤，无需重拉
}

function kbSearch(v) {
  KB.q = String(v || "").trim().toLowerCase();
  renderKnowledge();
}

async function kbOp(id, op) {
  if (op === "delete" && !await uiConfirm(t("删除这条知识？转正条目不再注入，删除后不可恢复。"), { ok: t("删除"), danger: true })) return;
  try { await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({ id, op }) }); }
  catch (e) { toast(e.message, true); return; }
  loadKnowledge();
}

function kbFormOpen(x) {   // x 为空 = 新建（保存即转正），否则编辑（不改状态）
  const isNew = !x;
  openModal(isNew ? t("新建知识条目") : t("编辑知识条目"),
    '<div class="form">' +
    '<div class="field"><label>' + t("标题") + ' <span class="req">*</span></label><input id="kb-f-title" value="' + esc(x ? x.title || "" : "") + '"></div>' +
    '<div class="field"><label>' + t("正文") + ' <span class="req">*</span></label><textarea id="kb-f-body" rows="5">' + esc(x ? x.body || "" : "") + "</textarea></div>" +
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("标签（逗号分隔）") + '</label><input id="kb-f-tags" value="' + esc((x ? x.tags || [] : []).join(", ")) + '" placeholder="' + t("例：番茄, 签约") + '"></div>' +
    '<div class="field"><label>' + t("事实截至") + '</label><input id="kb-f-asof" value="' + esc(x ? x.as_of || "" : "") + '" placeholder="YYYY-MM-DD"></div>' +
    "</div>" +
    '<div class="field"><label>' + t("适用范围（任务类型，* 为通用）") + '</label><input id="kb-f-scope" value="' + esc(x ? x.scope || "*" : "*") + '" placeholder="novel / serial_novel / code / *"></div>' +
    "</div>",
    '<button class="ghost" onclick="closeModal()">' + t("取消") + "</button>" +
    '<button onclick="kbFormSave(' + (isNew ? "" : "'" + esc(x.id) + "'") + ')">' + t("保存") + "</button>");
}

async function kbFormSave(id) {
  const fields = {
    title: $("kb-f-title").value.trim(),
    body: $("kb-f-body").value.trim(),
    tags: $("kb-f-tags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    scope: $("kb-f-scope").value.trim() || "*",
    as_of: $("kb-f-asof").value.trim(),
  };
  if (!fields.title || !fields.body) { toast(t("标题与正文不能为空"), true); return; }
  try {
    await api("/api/knowledge/op", { method: "POST", body: JSON.stringify(id
      ? { id, op: "edit", fields }
      : { op: "create", fields }) });
  } catch (e) { toast(e.message, true); return; }
  closeModal();
  loadKnowledge();
}

function kbEditOpen(id) {
  kbFormOpen((KB.data && KB.data.entries || []).find((x) => x.id === id));
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

/* last_status → 徽章：started=正常灰、error=红「拉起失败」、missed=黄「已错过」；
 * done/failed/cancelled=运行终态（2026-09-23 回写接线后会出现，失败必须可见） */
function autoStatusTag(tsk) {
  if (tsk.last_status === "error") return '<span class="tag auto-tag-err">' + t("拉起失败") + "</span>";
  if (tsk.last_status === "missed") return '<span class="tag auto-tag-miss">' + t("已错过") + "</span>";
  if (tsk.last_status === "started" || tsk.last_status === "queued") return '<span class="tag">' + t("正常") + "</span>";
  if (tsk.last_status === "failed") return '<span class="tag auto-tag-err">' + t("上次失败") + "</span>";
  if (tsk.last_status === "cancelled") return '<span class="tag auto-tag-miss">' + t("已取消") + "</span>";
  if (tsk.last_status === "done") return '<span class="tag">' + t("已完成") + "</span>";
  return "";
}

/* 运行偏好标签：只显示「偏离默认」的那些（编排默认 auto、思考默认 standard），
 * 否则每张卡都挂两颗没有信息量的胶囊，反而盖住真正要看的节奏与流程 */
function autoPrefTags(tsk) {
  const MODE = { auto: "自动", fast: "快速", expert: "专家", manual: "手动" };
  const TH = { auto: "自动", low: "快速", standard: "标准", high: "深度" };
  const out = [];
  if (tsk.mode && tsk.mode !== "auto" && MODE[tsk.mode]) {
    out.push(t("编排模式") + t("：") + t(MODE[tsk.mode]));
  }
  if (tsk.thinking && tsk.thinking !== "standard" && TH[tsk.thinking]) {
    out.push(t("思考程度") + t("：") + t(TH[tsk.thinking]));
  }
  if (tsk.direct_agent) {
    const a = realCliAgents().find((x) => x.id === tsk.direct_agent) ||
      ((S.state && S.state.agents) || []).find((x) => x.id === tsk.direct_agent);
    out.push(t("执行 CLI") + t("：") + (a ? (a.label || a.id) : tsk.direct_agent));
  }
  if (tsk.direct_provider_id) {
    const p = directProviders().find((x) => x.id === tsk.direct_provider_id);
    out.push(t("对话模型") + t("：") + (p ? (p.name || p.id) : tsk.direct_provider_id) +
      "/" + (tsk.direct_model || t("随厂商推荐")));
  }
  return out.map((s) => '<span class="tag">' + esc(s) + "</span>").join("");
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
    (flow ? '<span class="tag">' + t("流程：") + esc(t(flow.name)) + "</span>" : "") +
    autoPrefTags(tsk) + "</div>" +
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
      '<div class="card auto-tpl-card"><div class="head"><span class="name">' + esc(t(tp.name)) + "</span></div>" +
      '<div class="note">' + esc(t(tp.desc || "")) + "</div>" +
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
  const mode = f.mode || "auto";
  const thinking = f.thinking || "standard";
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
        '<option value="' + esc(fl.id) + '"' + (fl.id === curFlow ? " selected" : "") + ">" + esc(t(fl.name)) + "</option>").join("") +
      "</select></div>" +
    "</div>" +
    /* 运行偏好：与新建任务 Composer 的三颗胶囊同一套取值（编排模式/思考程度/
       对话模型），到点拉起的运行与手动建的任务完全等价 */
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("编排模式") + "</label>" +
      autoPillHtml("au-mode", AUTO_MODES, mode) + "</div>" +
    '<div class="field"><label>' + t("思考程度") + "</label>" +
      autoPillHtml("au-thinking", AUTO_THINKINGS, thinking) + "</div>" +
    "</div>" +
    '<div id="au-model-field" class="field hidden"><label>' + t("对话模型（可选）") + '</label><div class="grid-2">' +
      '<select id="au-direct-provider" aria-label="' + t("对话厂商") + '"></select>' +
      '<select id="au-direct-model-name" aria-label="' + t("对话模型") + '"></select>' +
      '</div><p class="hint">' + t("只对「直接执行」类流程生效；留「自动推荐」时按系统路由挑模型。") + "</p></div>" +
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
  ["au-mode", "au-thinking"].forEach((id) => {
    const sel = $(id);
    if (sel) sel.addEventListener("change", () => syncCmpSelFace(id));
    syncCmpSelFace(id);   // 弹框是动态插入的：选项文字要在这里同步一次
  });
  renderAuModelPicker(f.direct_provider_id || "", f.direct_model || "");
  const flowSel = $("au-flow");
  if (flowSel) {
    flowSel.addEventListener("change", autoFormSyncFlow);
    autoFormSyncFlow();
  }
}

/* 运行偏好取值（真源 = automation.py 的 MODES/THINKINGS，选项文案与 Composer 共用） */
const AUTO_MODES = [
  { v: "auto", label: "自动（推荐）：按任务类型与风险动态决定步骤" },
  { v: "fast", label: "快速：少步骤优先，先做确定性验收" },
  { v: "expert", label: "专家：完整规划、多评审、失败修复与换将" },
  { v: "manual", label: "手动：自行指定实现者与评审组" },
];
const AUTO_THINKINGS = [
  { v: "standard", label: "标准：平衡质量与速度" },
  { v: "auto", label: "自动：随任务难度调整" },
  { v: "low", label: "快速：少推理、优先响应" },
  { v: "high", label: "深度：更多推理与检查" },
];

/* 偏好胶囊：外观与 Composer 同款（透明原生 select 铺满整颗，短名由
 * syncCmpSelFace 从选中 option 文本截取——语言切换后短名跟着换） */
function autoPillHtml(id, opts, cur) {
  return '<span class="cmp-sel"><select id="' + id + '" class="cmp-sel-native" aria-label="' +
    esc(id === "au-mode" ? t("编排模式") : t("思考程度")) + '">' +
    opts.map((o) => '<option value="' + o.v + '"' + (o.v === cur ? " selected" : "") +
      ' data-i18n="' + esc(o.label) + '">' + esc(t(o.label)) + "</option>").join("") +
    '</select><span class="cmp-sel-face" id="' + id + '-face"></span>' +
    '<svg class="ico chev" aria-hidden="true"><use href="#i-chevron-r"></use></svg></span>';
}

/* 对话模型（仅 direct 流程）：复用 Composer 的厂商/模型数据源与重建规则 */
function renderAuModelPicker(pid, model) {
  const ps = $("au-direct-provider"), ms = $("au-direct-model-name");
  if (!ps || !ms) return;
  const provs = directProviders();
  const keepP = pid != null ? pid : ps.value;
  ps.innerHTML = '<option value="">' + t("自动推荐") + "</option>" + provs.map((p) =>
    '<option value="' + esc(p.id) + '">' + esc(p.name || p.id) + "</option>").join("");
  ps.value = provs.some((p) => p.id === keepP) ? keepP : "";
  const fillModels = (keepM) => {
    const p = provs.find((x) => x.id === ps.value);
    const names = p ? (p.models || []).filter((m) => !m.hidden)
      .map((m) => m.name).filter(Boolean) : [];
    ms.innerHTML = '<option value="">' + t("随厂商推荐") + "</option>" + names.map((name) =>
      '<option value="' + esc(name) + '">' + esc(name) + "</option>").join("");
    ms.value = names.includes(keepM) ? keepM : "";
  };
  fillModels(model != null ? model : ms.value);
  // 换厂商 = 旧模型名多半不在新厂商名录里：回「随厂商推荐」，不留悬空值
  ps.onchange = () => fillModels("");
}

/* 流程 → 对话模型字段显隐：只有 direct 引擎流程用得上（其余流程模型由系统路由） */
function autoFormSyncFlow() {
  const flow = flowById(($("au-flow") || {}).value);
  const box = $("au-model-field");
  if (box) box.classList.toggle("hidden", !(flow && flow.engine === "direct"));
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
  // 运行偏好：与 Composer 同名字段，后端 _apply_run_prefs 再归一一次
  payload.mode = $("au-mode") ? $("au-mode").value : "";
  payload.thinking = $("au-thinking") ? $("au-thinking").value : "";
  if (!$("au-model-field") || !$("au-model-field").classList.contains("hidden")) {
    payload.direct_provider_id = $("au-direct-provider") ? $("au-direct-provider").value : "";
    payload.direct_model = $("au-direct-model-name") ? $("au-direct-model-name").value : "";
    if (payload.direct_model && !payload.direct_provider_id) {
      toast(t("选择对话模型前请先选厂商"), true);
      return;
    }
  }
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

/* ---------------------------------------------------------- 禅道 Bug 自动修复 */
/* /api/zentao → {config(脱敏，含 product_profiles), claims, last_scan, next_scan, last_error}。
 * 产品档案卡：结构增删走「先采集 DOM 回 S.ztProfiles → 改 → 重渲染」，保住已输入值。 */

async function loadZentao() {
  let v = null;
  try { v = await api("/api/zentao"); } catch (e) { v = null; }
  S.zentao = v;
  const cfg = (v && v.config) || {};
  const set = (id, val) => { const el = $(id); if (el) el.value = val == null ? "" : val; };
  set("zt-base-url", cfg.base_url);
  set("zt-account", cfg.account);
  const pw = $("zt-password");
  if (pw) { pw.value = ""; pw.placeholder = cfg.has_password ? t("已保存（不改就留空）") : ""; }
  const tg = (id, on) => { const el = $(id); if (el) el.checked = !!on; };
  tg("zt-triageai", cfg.triage_ai);
  tg("zt-autoresolve", cfg.auto_resolve);
  tg("zt-automerge", cfg.auto_merge);
  tg("zt-poll", cfg.poll_enabled);
  set("zt-interval", cfg.interval_minutes || 5);
  S.ztProfiles = JSON.parse(JSON.stringify(cfg.product_profiles || []));
  renderZentaoProfiles();
  renderZentaoStatus();
  renderZentaoClaims();
  if (cfg.base_url && cfg.has_password) ztFetchCatalog(true);   // 静默拉清单：产品/账号就位即变下拉
}

/* ---- 产品档案：渲染与采集 ---- */

const ZT_SIDES = ["backend", "frontend"];

/* 模块字段双形态（同产品字段口径）：拉到清单 → 下拉选（#id 名称）；
 * 没拉到（连接不可用/该产品无模块）→ 保留手填 ID。已配值不在清单里也给
 * 「手填」选项，否则 select 选中态落空、采集时该路由会被当成 0 丢掉。 */
function ztModuleField(p, r) {
  const mods = (S.ztMods || {})[p.product] || [];
  const cur = r.module ? String(r.module) : "";
  if (!mods.length)
    return '<input class="zt-mr-module zt-num" placeholder="' + t("模块 ID") + '" value="' + esc(cur) + '">';
  const opts = ['<option value=""' + (cur ? "" : " selected") + " disabled>" + t("— 选模块 —") + "</option>"];
  if (cur && !mods.some((m) => String(m.id) === cur))
    opts.push('<option value="' + esc(cur) + '" selected>#' + esc(cur) + t("（手填）") + "</option>");
  for (const m of mods)
    opts.push('<option value="' + esc(m.id) + '"' + (cur === String(m.id) ? " selected" : "") + ">#" +
      esc(m.id) + " " + esc(m.name || "") + "</option>");
  return '<select class="zt-mr-module">' + opts.join("") + "</select>";
}

/* 产品字段双形态：拉到清单 → 下拉选；没拉到（连接不可用/老版无接口）→ 保留手填 ID */
function ztProductField(p) {
  const cur = p.product || "";
  const list = S.ztProducts || [];
  if (!list.length)
    return '<input class="zt-p-product zt-num" type="number" min="1" value="' + esc(cur) +
      '" placeholder="ID" title="' + t("配好连接后点「拉取清单」变下拉选择") + '">';
  const opts = [];
  if (cur && !list.some((x) => String(x.id) === String(cur)))
    opts.push('<option value="' + esc(cur) + '" selected>#' + esc(cur) + t("（手填）") + "</option>");
  opts.push('<option value=""' + (cur ? "" : " selected") + " disabled>" + t("— 选产品 —") + "</option>");
  for (const x of list) {
    const label = "#" + x.id + " " + (x.name || "") +
      (x.status && x.status !== "normal" ? t("（已关闭）") : "");
    opts.push('<option value="' + esc(x.id) + '"' +
      (String(cur) === String(x.id) ? " selected" : "") + ">" + esc(label) + "</option>");
  }
  return '<select class="zt-p-product" onchange="ztProdChanged(this)">' + opts.join("") + "</select>";
}

/* 选了产品 → 静默拉该产品的模块清单进 S.ztMods（模块路由就地变下拉） */
function ztProdChanged(el) {
  const card = el.closest(".zt-prof");
  const i = Array.from(document.querySelectorAll("#zt-profiles .zt-prof")).indexOf(card);
  if (i >= 0) ztFetchMods(i, true);
}

/* 工作目录「选择…」：复用全局 pickFolder（原生对话框→网页目录弹框回落），回填同一行的输入框 */
function ztWdPick(btn) {
  const inp = btn.closest(".zt-wd-row").querySelector(".zt-p-wd");
  window.pickFolder(inp, true);
  // 选完目录顺手拉该仓库的分支清单，基线分支变下拉可选
  const card = btn.closest(".zt-prof");
  const side = inp.dataset.side;
  if (card && side) setTimeout(() => ztRevFill(card, side), 400);
}

/* 基线分支下拉（datalist）：从仓库工作目录拉本地分支列表。
 * 复用 /api/git/info（只读探测，仅限本机）；拉不到就保持手填，不报错打扰。 */
function ztRevFill(card, side, force) {
  if (!card) return;
  const wd = (card.querySelector(".zt-p-wd") || {}).value || "";
  const rev = card.querySelector(".zt-p-rev");
  const dl = card.querySelector('datalist[data-side="' + side + '"]');
  if (!rev || !dl) return;
  if (!wd.trim()) { dl.innerHTML = ""; return; }
  if (!force && dl.options.length) return;   // 已有清单不重拉
  api("/api/git/info?workdir=" + encodeURIComponent(wd.trim()))
    .then((r) => {
      if (!r || !r.repo) return;
      dl.innerHTML = (r.branches || []).map((b) =>
        '<option value="' + esc(b) + '">').join("");
    })
    .catch(() => {});
}

function ztRepoHtml(i, side) {
  const cn = side === "backend" ? t("后端") : t("前端");
  const p = (S.ztProfiles[i] || {});
  const repo = (p.repos || {})[side] || {};
  const hint = (p.repo_hints || {})[side] || "";
  return '<label>' + cn + t("仓库·工作目录") + '</label><span class="zt-wd-row">' +
    '<input class="zt-p-wd" data-side="' + side + '" value="' + esc(repo.workdir || "") + '" placeholder="' + t("空 = 用默认保存路径") + '">' +
    '<button class="ghost small" type="button" title="' + t("浏览本机目录选择") + '" onclick="ztWdPick(this)">' + t("选择…") + "</button></span>" +
    '<label>' + cn + t("仓库·基线分支") + '</label><span class="zt-wd-row">' +
    '<input class="zt-p-rev" data-side="' + side + '" list="zt-rev-' + i + '-' + side + '" value="' + esc(repo.git_rev || "") + '" placeholder="' + t("如 main；空 = 直接改工作目录") + '" onfocus="ztRevFill(this.closest(\'.zt-prof\'), \'' + side + '\')">' +
    '<button class="ghost small" type="button" title="' + t("刷新分支列表") + '" onclick="ztRevFill(this.closest(\'.zt-prof\'), \'' + side + '\', true)">▾</button>' +
    '<datalist id="zt-rev-' + i + '-' + side + '" data-side="' + side + '"></datalist></span>' +
    '<label>' + cn + t("仓库·验证命令") + '</label><input class="zt-p-verify" data-side="' + side + '" value="' + esc(repo.verify_command || "") + '" placeholder="' + t("如 npm test；空 = 靠评审把关") + '">' +
    '<label>' + cn + t("仓库·一句话描述") + '</label><input class="zt-p-hint" data-side="' + side + '" value="' + esc(hint) + '" placeholder="' + t("给 AI 排查看，如：Vue3 管理台前端") + '">';
}

function ztProfCardHtml(p, i) {
  const ours = p.our_sides || [];
  const routes = p.module_routes || [];
  const SIDE_OPTS = [["backend", "后端"], ["frontend", "前端"], ["both", "双端"], ["not_ours", "非我方"]];
  const rows = routes.map((r, j) =>
    '<div class="zt-mr-row" data-j="' + j + '">' +
    ztModuleField(p, r) +
    '<select class="zt-mr-side">' + SIDE_OPTS.map((o) =>
      '<option value="' + o[0] + '"' + (r.side === o[0] ? " selected" : "") + ">" + t(o[1]) + "</option>").join("") + "</select>" +
    '<input class="zt-mr-account" list="zt-users" placeholder="' + t("转给谁（禅道账号，可空）") + '" value="' + esc(r.account || "") + '">' +
    '<button class="ghost small zt-mr-del" type="button" onclick="ztMrDel(' + i + "," + j + ')">' + t("删") + "</button>" +
    "</div>").join("");
  const owners = p.owners || {};
  return '<div class="card zt-prof" data-i="' + i + '"><div class="head">' +
    '<span class="name">' + t("产品") + " " + ztProductField(p) + "</span>" +
    '<button class="danger small" onclick="ztProfDel(' + i + ')">' + t("删除产品") + "</button></div>" +
    '<div class="zt-grid">' +
    '<label data-i18n="只认领指派给">只认领指派给</label><input class="zt-p-assigned" list="zt-users" value="' + esc(p.assigned_to || "") + '" placeholder="' + t("禅道账号名，空 = 不按指派过滤") + '">' +
    '<label data-i18n="严重度上限">严重度上限</label><select class="zt-p-sev">' +
    [0, 1, 2, 3, 4].map((n) => '<option value="' + n + '"' + (Number(p.severity_cap || 0) === n ? " selected" : "") + ">" +
      (n === 0 ? t("不限") : n + (n === 1 ? t("（最严重）") : "")) + "</option>").join("") + "</select>" +
    "</div>" +
    '<div class="zt-ours"><span>' + t("我方端（自动修）：") + "</span>" +
    ZT_SIDES.map((s) =>
      '<label class="toggle"><input type="checkbox" class="zt-p-ours" data-side="' + s + '"' + (ours.indexOf(s) >= 0 ? " checked" : "") + "><span>" +
      (s === "backend" ? t("后端") : t("前端")) + "</span></label>").join("") +
    '<span class="hint">' + t("都不勾 = 该产品只排查转派，不自动修") + "</span></div>" +
    '<div class="zt-grid">' + ZT_SIDES.map((s) => ztRepoHtml(i, s)).join("") + "</div>" +
    '<div class="zt-grid">' +
    '<label data-i18n="后端负责人">后端负责人</label><input class="zt-p-owner" list="zt-users" data-side="backend" value="' + esc(owners.backend || "") + '" placeholder="' + t("禅道账号，转派/升级用") + '">' +
    '<label data-i18n="前端负责人">前端负责人</label><input class="zt-p-owner" list="zt-users" data-side="frontend" value="' + esc(owners.frontend || "") + '" placeholder="' + t("禅道账号，转派/升级用") + '">' +
    '<label data-i18n="非我方转派给">非我方转派给</label><input class="zt-p-owner" list="zt-users" data-side="not_ours" value="' + esc(owners.not_ours || "") + '" placeholder="' + t("空 = 指回报告人") + '">' +
    "</div>" +
    '<div class="zt-mrs"><div class="zt-mr-head">' + t("模块路由（模块 → 端/人，排查优先级最高）") +
    '<button class="ghost small" onclick="ztFetchMods(' + i + ', false, true)">' + t("拉取模块清单") + "</button>" +
    '<button class="ghost small" onclick="ztMrAdd(' + i + ')">' + t("＋ 加路由") + "</button></div>" +
    (rows || '<div class="hint">' + t("未配路由——模块不在路由里的 Bug 走 AI 排查") + "</div>") +
    "</div></div>";
}

function renderZentaoProfiles() {
  const box = $("zt-profiles");
  if (!box) return;
  box.innerHTML = (S.ztProfiles || []).map((p, i) => ztProfCardHtml(p, i)).join("") ||
    '<div class="empty">' + t("还没有产品档案——先测试连接，再从禅道选择产品，无需自己查 ID。") + "</div>";
}

/* DOM 卡片 → profiles 数组（结构变化前采集，保住已输入值） */
function ztHarvestProfiles() {
  return Array.from(document.querySelectorAll("#zt-profiles .zt-prof")).map((card) => {
    const gv = (sel) => { const el = card.querySelector(sel); return el ? (el.value || "").trim() : ""; };
    const routes = Array.from(card.querySelectorAll(".zt-mr-row")).map((row) => ({
      module: parseInt((row.querySelector(".zt-mr-module").value || ""), 10) || 0,
      side: row.querySelector(".zt-mr-side").value,
      account: (row.querySelector(".zt-mr-account").value || "").trim(),
    })).filter((r) => r.module > 0);
    return {
      product: parseInt(gv(".zt-p-product"), 10) || 0,
      assigned_to: gv(".zt-p-assigned"),
      severity_cap: parseInt(gv(".zt-p-sev"), 10) || 0,
      our_sides: Array.from(card.querySelectorAll(".zt-p-ours:checked")).map((el) => el.dataset.side),
      repos: {
        backend: { workdir: gv('.zt-p-wd[data-side="backend"]'), git_rev: gv('.zt-p-rev[data-side="backend"]'), verify_command: gv('.zt-p-verify[data-side="backend"]') },
        frontend: { workdir: gv('.zt-p-wd[data-side="frontend"]'), git_rev: gv('.zt-p-rev[data-side="frontend"]'), verify_command: gv('.zt-p-verify[data-side="frontend"]') },
      },
      repo_hints: {
        backend: gv('.zt-p-hint[data-side="backend"]'),
        frontend: gv('.zt-p-hint[data-side="frontend"]'),
      },
      owners: {
        backend: gv('.zt-p-owner[data-side="backend"]'),
        frontend: gv('.zt-p-owner[data-side="frontend"]'),
        not_ours: gv('.zt-p-owner[data-side="not_ours"]'),
      },
      module_routes: routes,
    };
  });
}

async function ztProfAdd() {
  S.ztProfiles = ztHarvestProfiles();
  // 新档案必须来自禅道产品清单。先拉清单，再自动选第一个尚未配置的产品，
  // 避免生成 product=0 的空卡，用户随后保存才看到“缺产品 ID”。
  if (!(S.ztProducts || []).length) {
    if (!($("zt-base-url") && $("zt-base-url").value.trim())) {
      toast(t("请先测试并保存禅道连接，再添加产品"), true);
      return;
    }
    await ztFetchCatalog(false);
    S.ztProfiles = ztHarvestProfiles();
  }
  const used = new Set(S.ztProfiles.map((p) => String(p.product || "")));
  const next = (S.ztProducts || []).find((p) => p.id && !used.has(String(p.id)));
  if (!next) {
    toast(t((S.ztProducts || []).length ? "禅道产品均已添加" : "未拉到可用产品，请先测试连接"), true);
    return;
  }
  S.ztProfiles.push({ product: Number(next.id), assigned_to: "", severity_cap: 0, our_sides: ["backend"],
    repos: { backend: {}, frontend: {} }, repo_hints: { backend: "", frontend: "" },
    owners: { backend: "", frontend: "", not_ours: "" }, module_routes: [] });
  renderZentaoProfiles();
  ztFetchMods(S.ztProfiles.length - 1, true);
}

function ztProfDel(i) {
  S.ztProfiles = ztHarvestProfiles();
  S.ztProfiles.splice(i, 1);
  renderZentaoProfiles();
}

function ztMrAdd(i) {
  S.ztProfiles = ztHarvestProfiles();
  (S.ztProfiles[i].module_routes = S.ztProfiles[i].module_routes || []).push({ module: "", side: "backend", account: "" });
  renderZentaoProfiles();
}

function ztMrDel(i, j) {
  S.ztProfiles = ztHarvestProfiles();
  S.ztProfiles[i].module_routes.splice(j, 1);
  renderZentaoProfiles();
}

/* 拉产品模块清单 → S.ztMods[产品ID]（重画后模块 ID 变下拉）。
 * S.ztModsAsked 每产品只自动拉一次（失败不重试，免得每次重画都打一趟）；
 * 「拉取模块清单」按钮 force=true 绕过去，手动可反复刷新。
 * silent=自动补拉（静默无busy）；重画前先采集，保住未保存输入。 */
async function ztFetchMods(i, silent, force) {
  const card = $("zt-profiles").querySelectorAll(".zt-prof")[i];
  const pidEl = card && card.querySelector(".zt-p-product");
  const pid = parseInt((pidEl && pidEl.value) || "", 10);
  if (!pid) {
    if (!silent) toast(t("先选产品再拉模块清单"), true);
    return;
  }
  S.ztModsAsked = S.ztModsAsked || {};
  if (S.ztModsAsked[pid] && !force) return;
  S.ztModsAsked[pid] = true;
  let r;
  try {
    r = await api("/api/zentao/modules", { method: "POST", body: JSON.stringify({ product: pid }),
      silent: !!silent, busy: !silent });
  } catch (e) { if (!silent) toast(e.message, true); return; }
  if (!r.ok) { if (!silent) toast(r.error || t("拉取失败"), true); return; }
  const mods = r.modules || [];
  if (!mods.length) { if (!silent) toast(t("该产品没有模块清单"), true); return; }
  S.ztMods = S.ztMods || {};
  S.ztMods[pid] = mods;
  S.ztProfiles = ztHarvestProfiles();
  renderZentaoProfiles();
  if (!silent) toast(t("模块清单已拉到 ") + mods.length + t(" 条，模块 ID 已变下拉选择"));
}

/* 拉产品/账号清单：产品 ID 变下拉，负责人/转派账号挂 datalist。silent=进页自动拉 */
async function ztFetchCatalog(silent) {
  // 后台拉清单：静默（不画顶部滑动线、不挂按钮转圈）+ 前端 45s 超时——
  // 禅道内网不可达时此前会挂住页面加载指示十几二十秒（用户实测反馈）
  const grab = (url) => api(url, { method: "POST", body: "{}",
    timeout: 45000, busy: false, silent: true })
    .catch((e) => ({ ok: false, error: e.message }));
  const [pr, ur] = await Promise.all([grab("/api/zentao/products"), grab("/api/zentao/users")]);
  if (pr && pr.ok) S.ztProducts = pr.products || [];
  if (ur && ur.ok) S.ztUsers = ur.users || [];
  const dl = $("zt-users");
  if (dl) dl.innerHTML = (S.ztUsers || []).map((u) =>
    '<option value="' + esc(u.account) + '">' + esc(u.realname || "") + "</option>").join("");
  const prodOk = pr && pr.ok && (pr.products || []).length > 0;
  if (prodOk) {           // 产品下拉就位（先采集已填值再重渲染）
    S.ztProfiles = ztHarvestProfiles();
    renderZentaoProfiles();
    // 各产品模块清单静默补拉（每产品一次）：有清单的档案，模块 ID 路由行就地变下拉
    (S.ztProfiles || []).forEach((p, i) => {
      if (parseInt(p.product, 10) > 0 && !((S.ztMods || {})[p.product])) ztFetchMods(i, true);
    });
  }
  if (silent) return;
  const got = [];
  if (prodOk) got.push((pr.products || []).length + t(" 个产品"));
  if (ur && ur.ok) got.push((ur.users || []).length + t(" 个账号"));
  if (got.length) toast(t("已拉到 ") + got.join(t("、")) + t("，产品/负责人已是下拉选择"));
  else toast((pr && pr.error) || (ur && ur.error) || t("拉取失败——先「测试连接」确认可用"), true);
}

/* ---------------------------------------------------------- 钩子（任务生命周期） */
/* 设置 → 钩子：task_start / message_submit / run_end 三事件挂本机命令。
 * stdin 收 JSON、stdout 回 {"inject"} 或纯文本即注入；失败超时静默跳过。 */
function loadHooksPanel() {
  api("/api/hooks").then((d) => {
    S.hooks = d.hooks || [];
    S.hookEvents = d.events || [];
    renderHooks();
  }).catch((e) => toast(t("加载失败：") + (e.message || e), true));
}

function renderHooks() {
  const box = $("hooks-list"), empty = $("hooks-empty");
  if (!box) return;
  const list = S.hooks || [];
  if (empty) empty.classList.toggle("hidden", list.length > 0);
  const evLabel = { task_start: t("任务开始"), message_submit: t("消息送达"), run_end: t("运行结束") };
  box.innerHTML = list.map((h, i) => {
    return '<div class="card hook-card" data-i="' + i + '">' +
      '<div class="hook-row">' +
      '<input class="hook-name" value="' + esc(h.name || "") + '" placeholder="' + esc(t("钩子名称")) + '" data-hf="name">' +
      '<select class="hook-event" data-hf="event">' +
      ["task_start", "message_submit", "run_end"].map((ev) =>
        '<option value="' + ev + '"' + (h.event === ev ? " selected" : "") + ">" +
        esc(evLabel[ev]) + "</option>").join("") +
      "</select>" +
      "</div>" +
      '<input class="hook-cmd mono" value="' + esc(h.cmd || "") + '" placeholder="' +
      esc(t('命令：python D:\\proj\\hook.py（stdin 收 JSON，stdout 回 {"inject":"文本"}）')) + '" data-hf="cmd">' +
      '<div class="hook-row hook-foot">' +
      '<label class="switch" title="' + esc(t("启用")) + '"><input type="checkbox" data-hf="enabled"' +
      (h.enabled ? " checked" : "") + '><span></span><em>' + esc(t("启用")) + '</em></label>' +
      '<span class="hook-foot-lab">' + esc(t("超时（秒）")) + '</span>' +
      '<input class="hook-timeout" type="number" min="1" max="120" value="' + Number(h.timeout_s || 10) + '" data-hf="timeout_s">' +
      '<button type="button" class="ghost small" data-hook-test="' + i + '">' + t("测试") + "</button>" +
      '<button type="button" class="ghost small" onclick="removeHook(' + i + ')">' + t("删除") + "</button>" +
      '<span class="hint hook-test-out"></span>' +
      "</div></div>";
  }).join("");
}

window.removeHook = function (i) {
  (S.hooks || []).splice(i, 1);
  renderHooks();
};

function hookHarvest() {
  return Array.from(document.querySelectorAll("#hooks-list .hook-card")).map((card) => {
    const gv = (f) => card.querySelector('[data-hf="' + f + '"]');
    return { id: (S.hooks || [])[Number(card.dataset.i)]?.id || "",
      name: gv("name").value.trim(), event: gv("event").value,
      cmd: gv("cmd").value.trim(), enabled: gv("enabled").checked,
      timeout_s: Number(gv("timeout_s").value || 10) };
  });
}

function bindHooksPanel() {
  if ($("btn-hooks-add").dataset.bound) return;
  $("btn-hooks-add").dataset.bound = "1";
  $("btn-hooks-add").addEventListener("click", () => {
    S.hooks = S.hooks || [];
    S.hooks.push({ id: "hk-" + Date.now(), name: "", event: "task_start",
      cmd: "", enabled: true, timeout_s: 10 });
    renderHooks();
  });
  $("btn-hooks-save").addEventListener("click", async () => {
    try {
      const r = await api("/api/hooks/save", { method: "POST",
        body: JSON.stringify({ hooks: hookHarvest() }) });
      S.hooks = r.hooks || [];
      renderHooks();
      toast(t("钩子已保存"));
    } catch (e) { toast(t("保存失败：") + (e.message || e), true); }
  });
  $("hooks-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-hook-test]");
    if (!btn) return;
    const card = btn.closest(".hook-card");
    const gv = (f) => card.querySelector('[data-hf="' + f + '"]');
    const out = card.querySelector(".hook-test-out");
    btn.disabled = true;
    out.textContent = t("运行中…");
    try {
      const r = await api("/api/hooks/test", { method: "POST",
        body: JSON.stringify({ cmd: gv("cmd").value.trim(), event: gv("event").value,
          timeout_s: Number(gv("timeout_s").value || 10) }) });
      out.textContent = (r.ok ? "✓ " : "✗ ") + String(r.output || "").slice(0, 120);
    } catch (e2) { out.textContent = "✗ " + (e2.message || e2); }
    btn.disabled = false;
  });
}
bindHooksPanel();

/* ---- 状态行 / 修复记录 ---- */

function zentaoStateTag(st) {
  const MAP = {
    fixing: ["修复中", "zt-tag-fix"],
    resolved: ["已解决", "zt-tag-ok"],
    transferred: ["已转派", "zt-tag-fix"],
    escalated: ["已升级转派", "zt-tag-warn"],
    merge_failed: ["合并失败", "zt-tag-warn"],
    commented: ["已评论留人工", "zt-tag-warn"],
    resolve_failed: ["回写失败", "zt-tag-warn"],
    done_manual: ["待人工确认", "zt-tag-warn"],
    need_manual: ["需人工", "zt-tag-warn"],
    lost: ["记录丢失", "zt-tag-err"],
  };
  const m = MAP[st] || [st || "?", ""];
  return '<span class="tag ' + m[1] + '">' + t(m[0]) + "</span>";
}

function zentaoTriText(c) {
  const tri = c.triage || {};
  if (!tri.side) return "";
  const NAME = { backend: "后端问题", frontend: "前端问题", both: "双端问题", not_ours: "非我方", unknown: "待人工" };
  const by = tri.by === "rule" ? t("模块路由") : tri.by === "ai" ? "AI" : "";
  return t("排查：") + t(NAME[tri.side] || tri.side) + (by ? "（" + by + "）" : "");
}

/* 扫描间隔（分钟）→ 人类可读：整小时显示「2 小时」，否则「5 分钟」 */
function ztIntervalText(mins) {
  const n = Math.max(1, parseInt(mins, 10) || 5);
  if (n % 60 === 0) return (n / 60) + t(" 小时一次");
  return n + t(" 分钟一次");
}

function renderZentaoStatus() {
  const box = $("zentao-status");
  if (!box) return;
  const v = S.zentao;
  if (!v) { box.innerHTML = t("加载失败：服务未连接"); return; }
  const cfg = v.config || {};
  const parts = [];
  parts.push(cfg.poll_enabled
    ? (t("定时扫描已开启，每 ") + ztIntervalText(cfg.interval_minutes)
       + (v.next_scan ? t("，下次 ") + esc(v.next_scan) : ""))
    : t("定时扫描未开启（仍可手动「立即扫描」）"));
  if (v.last_scan) parts.push(t("上次扫描 ") + esc(v.last_scan));
  box.innerHTML = parts.map(esc).join(" · ") +
    (v.last_error ? '<span class="zt-err">' + t("最近错误：") + esc(v.last_error) + "</span>" : "");
}

/* bug 详情页地址：后端回传探测出的 web 根（含 /zentao 子路径）与路由形态
   bug_style。默认伪静态 bug-view-N.html（官方默认；2026-09-23 真机实证
   伪静态部署对 GET 式链接不路由），探到 GET 形态部署才回退查询串形态。 */
function zentaoBugUrl(c) {
  const z = S.zentao || {};
  const base = String(z.bug_base || "").replace(/\/+$/, "");
  if (!base || !c.bug_id) return "";
  if (z.bug_style === "get")
    return base + "/index.php?m=bug&f=view&bugID=" + encodeURIComponent(c.bug_id);
  return base + "/bug-view-" + encodeURIComponent(c.bug_id) + ".html";
}

function renderZentaoClaims() {
  const box = $("zentao-claims");
  if (!box) return;
  const claims = (S.zentao && S.zentao.claims) || [];
  box.innerHTML = claims.map((c) => {
    const tasks = (c.tasks || []).map((tk) =>
      "<span>" + t("【") + (tk.side === "frontend" ? t("前端") : t("后端")) + t("】") +
      '<a href="#" onclick="zentaoOpenRun(\'' + esc(tk.run_id || "") + '\');return false;">' + esc(tk.task_id || "?") + "</a></span>").join("");
    const bugUrl = zentaoBugUrl(c);
    const name = "#" + esc(c.bug_id) + " " + esc(c.title || "");
    const headName = bugUrl
      ? '<a class="zt-bug-link" href="' + esc(bugUrl) + '" target="_blank" rel="noopener" title="' +
        esc(t("在禅道中打开 Bug 详情")) + '">' + name + "</a>"
      : name;
    return '<div class="card"><div class="head"><span class="name">' + headName + "</span>" +
      zentaoStateTag(c.state) + "</div>" +
      '<div class="auto-meta">' +
      (zentaoTriText(c) ? "<span>" + esc(zentaoTriText(c)) + "</span>" : "") +
      (tasks || "") +
      (c.claimed_at ? "<span>" + t("认领于 ") + esc(c.claimed_at) + "</span>" : "") +
      "</div>" +
      (c.note ? '<div class="note">' + esc(c.note) + "</div>" : "") +
      "</div>";
  }).join("") || '<div class="empty">' + t("还没有认领过 Bug——配置好连接与产品档案后点「立即扫描」。") + "</div>";
}

function zentaoOpenRun(runId) {
  if (runId) { openRun(runId); return; }
  switchTab("tasks");
}

/* 表单 → 保存配置。password 只在用户输入了才提交（空 = 保持已存密码）。 */
function ztFormPayload() {
  const val = (id) => (($(id).value || "").trim());
  const payload = {
    base_url: val("zt-base-url"),
    account: val("zt-account"),
    product_profiles: ztHarvestProfiles(),
    triage_ai: $("zt-triageai").checked,
    auto_resolve: $("zt-autoresolve").checked,
    auto_merge: $("zt-automerge").checked,
    poll_enabled: $("zt-poll").checked,
    interval_minutes: Math.max(5, parseInt($("zt-interval").value, 10) || 5),
  };
  const pw = ($("zt-password").value || "").trim();
  if (pw) payload.password = pw;
  return payload;
}

async function saveZentao() {
  const payload = ztFormPayload();
  if ((payload.product_profiles || []).some((p) => !p.product)) {
    toast(t("请选择禅道产品后再保存"), true);
    const empty = Array.from(document.querySelectorAll("#zt-profiles .zt-p-product"))
      .find((el) => !parseInt(el.value || "", 10));
    if (empty) empty.focus();
    return;
  }
  try {
    await api("/api/zentao/config", { method: "POST", body: JSON.stringify(payload) });
  } catch (e) { toast(t("保存失败：") + e.message, true); return; }
  toast(t("禅道配置已保存"));
  loadZentao();
}

async function testZentao() {
  const val = (id) => (($(id).value || "").trim());
  const body = { base_url: val("zt-base-url"), account: val("zt-account") };
  const pw = ($("zt-password").value || "").trim();
  if (pw) body.password = pw;
  let r;
  try {
    r = await api("/api/zentao/test", { method: "POST", body: JSON.stringify(body) });
  } catch (e) { toast(e.message, true); return; }
  toast(r.message || (r.ok ? t("连接成功") : t("连接失败")), !r.ok);
  if (r.ok && r.base_url) {          // 子路径部署被自动补全：回填输入框提醒保存
    const inp = $("zt-base-url");
    const cur = (inp.value || "").trim().replace(/\/+$/, "");
    if (inp && r.base_url !== cur) {
      inp.value = r.base_url;
      toast(t("地址已自动补全子路径，记得「保存配置」"));
    }
  }
  if (r.ok) {
    // 测试成功即保存当前连接；产品/账号清单后台拉（真机禅道慢，await 会把
    // 确认按钮按住几十秒——「保存为啥要这么久」根因），拉到后下拉自动就位。
    api("/api/zentao/config", { method: "POST", operation: false,
      body: JSON.stringify(ztFormPayload()) })
      .then(() => loadZentao())
      .catch(() => {});
    toast(t("连接成功，已保存；正在拉取产品清单…"));
    ztFetchCatalog(true).catch(() => {});
  }
}

async function scanZentao() {
  let r;
  try {
    r = await api("/api/zentao/scan", { method: "POST", body: "{}" });
  } catch (e) { toast(e.message, true); return; }
  if (r.error) toast(r.error, true);
  else toast(t("扫描完成，新认领 ") + (r.claimed || 0) + t(" 个 Bug"));
  loadZentao();
}

/* ---------------------------------------------------------- 插件市场 */
/* /api/market → {catalog, categories(闭集)}；搜索/分类/状态全在本地过滤。
 * 外部目录（/api/market/remote）只读缓存不联网；联网拉取仅在用户点「拉取更新」。 */
async function loadMarket() {
  try { S.market = await api("/api/market"); }
  catch (e) { S.market = null; }
  if (mkActiveView() === "remote") {
    await mkrLoadPage(true);   // 外部视图：按当前过滤条件重拉第一页（装/卸后状态翻牌）
  } else {
    S.mkrAll = null;           // 本地视图进市场：外部数据进视图时再拉（mkSetView 触发）
    S.marketRemote = null;
  }
  renderMarket();
  renderMarketRemote();
}

function mkCount(text) {
  const el = $("mk-count");
  if (el) el.textContent = text || "";
}

function mkScopeText(scopes) {
  const list = scopes || [];
  if (!list.length || list.indexOf("*") >= 0) return t("全部流程");
  return list.map((s) => { const f = flowById(s); return f ? t(f.name) : s; }).join(" / ");
}

/* 体量：「约 1.7k 字」；千字以下原样数字 */
function mkCharsText(n) {
  n = Number(n) || 0;
  if (n >= 1000) return t("约 ") + (n / 1000).toFixed(1).replace(/\.0$/, "") + "k " + t("字");
  return t("约 ") + n + " " + t("字");
}

function mkCardHtml(p, section) {
  let ops;
  if (p.builtin) ops = '<button class="ghost small" disabled>' + t("已内置") + "</button>";
  else if (p.installed) ops = '<span class="tag ok">' + t("已安装") + "</span>" +
    '<button class="ghost small" onclick="mkRemove(\'' + esc(p.id) + '\')">' + t("卸载") + "</button>";
  else ops = '<button class="primary small" onclick="mkInstall(\'' + esc(p.id) + '\')">' + t("安装") + "</button>";
  // 已装插件卡片直接给启停开关（对齐 ZCode 插件管理形态：启停不用去经验库翻）。
  // 注意传 pack_id（user-哈希）而非市场 id——启停记账在 skills 用户包上
  const toggle = p.installed
    ? '<label class="switch" title="' + (p.enabled ? t("已启用") : t("已停用")) + '">' +
      '<input type="checkbox"' + (p.enabled ? " checked" : "") +
      ' onchange="mkTogglePack(\'' + esc(p.pack_id || p.id) + '\', this.checked)"></label>'
    : "";
  const sec = section ? '<span class="tag mk-sec">' + esc(section) + "</span>" : "";
  return '<div class="card mk-card"><div class="head">' +
    '<span class="name">' + esc(t(p.name)) + "</span>" +
    '<span class="tag">' + esc(t(p.category || "")) + "</span>" + sec +
    "</div>" +
    '<div class="note">' + esc(t(p.desc || "")) + "</div>" +
    '<div class="mk-meta"><span title="' + esc(t("运行该类型任务时自动注入提示词，无需手动调用")) + '">' + t("适用：") + esc(mkScopeText(p.scopes)) + "</span><span>" + esc(mkCharsText(p.chars)) + "</span></div>" +
    '<div class="ops">' + ops + toggle + "</div></div>";
}

/* 已装插件启停：走经验库包启停（同一条链），停用后该插件不再注入任务提示词 */
async function mkTogglePack(id, enabled) {
  try {
    await api("/api/skills/pack-op", { method: "POST",
      body: JSON.stringify({ id, op: enabled ? "enable" : "disable" }) });
    toast(enabled ? t("已启用") : t("已停用（不再注入任务提示词）"));
    loadMarket();
  } catch (e) {
    toast(e.message || t("操作失败"), true);
    loadMarket();
  }
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
  if (cnt && mkActiveView() === "local") cnt.textContent = t("共 ") + items.length + t(" 个");
  // 分区渲染（对齐 ZCode 插件管理形态）：内置 / 已安装 / 可安装三组。
  // #mk-grid 本身是 grid 容器，直接塞 h3 会把标题当格子排（样式乱）——
  // 分区模式给外层加 .mk-grouped 转块级，每组自带独立 .mk-grid。
  if (st === "all" && !cat && !q) {
    const builtin = items.filter((p) => p.builtin);
    const inst = items.filter((p) => !p.builtin && p.installed);
    const avail = items.filter((p) => !p.builtin && !p.installed);
    const sec = (title, list) => !list.length ? "" :
      '<h3 class="sec-title">' + esc(t(title)) + ' <span class="tag">' + list.length + "</span></h3>" +
      '<div class="mk-grid">' + list.map((p) => mkCardHtml(p)).join("") + "</div>";
    grid.classList.add("mk-grouped");
    grid.innerHTML = sec(t("内置"), builtin) + sec(t("已安装"), inst) + sec(t("可安装"), avail) ||
      '<div class="empty">' + t("没有符合条件插件") + "</div>";
    return;
  }
  grid.classList.remove("mk-grouped");
  grid.innerHTML = items.map((p) => mkCardHtml(p)).join("") || '<div class="empty">' + t("没有符合条件插件") + "</div>";
}

async function mkInstall(id) {
  try {
    const r = await api("/api/market/" + encodeURIComponent(id) + "/install", { method: "POST", body: "{}" });
    toast(r && r.already ? t("该插件已安装过") : t("安装成功，运行任务时自动注入提示词；可到「经验库」启停或查看"));
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

/* ------------------------------------------------------ 市场：外部目录 */
/* 公开生态插件目录：只装纯技能类（文档型）；含脚本/钩子/MCP 的条目服务端
 * 预分类灰显。服务端对全量本地缓存做搜索/来源过滤后分页下发（每页 60），
 * 前端累积渲染 + 滚动哨兵自动续页；缓存全在本地，翻页零成本。 */
const MKR_PAGE = 60;

function mkActiveView() {
  const btn = document.querySelector("#mk-view .seg-btn.active");
  return btn ? (btn.dataset.v || "local") : "local";
}

function mkSetView(v) {
  document.querySelectorAll("#mk-view .seg-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.v === v));
  const local = $("mk-local"), remote = $("mk-remote");
  if (local) local.classList.toggle("hidden", v !== "local");
  if (remote) remote.classList.toggle("hidden", v !== "remote");
  renderMarket();
  if (v === "remote" && !S.mkrAll) mkrLoadPage(true);
  else renderMarketRemote();
}

function mkrFilterKey() {
  const q = (($("mkr-search") || {}).value || "").trim().toLowerCase();
  const srcSel = $("mkr-source");
  return q + "|" + (srcSel ? srcSel.value : "");
}

/* 搜索防抖：过滤在服务端对全量做，停 350ms 再发请求；只跟当前过滤键变化时重拉首页 */
function mkrDebounce() {
  clearTimeout(mkrDebounce._t);
  mkrDebounce._t = setTimeout(() => {
    if (mkrFilterKey() === S.mkrKey && S.mkrAll) return;   // 同条件不重复拉
    mkrLoadPage(true);
  }, 350);
}

/* 拉一页：reset=清空已累积列表（首进/搜索/换来源/刷新后）。 */
async function mkrLoadPage(reset) {
  // 翻页保持单飞；搜索/换来源/刷新属于重置请求，可以抢占旧请求。
  // 旧响应回来时由 requestKey 丢弃，不能覆盖用户刚选的新条件。
  if (S.mkrLoading && !reset) return;
  const requestKey = (S.mkrRequestKey || 0) + 1;
  S.mkrRequestKey = requestKey;
  if (reset) { S.mkrAll = []; S.mkrOffset = 0; }
  const q = (($("mkr-search") || {}).value || "").trim();
  const srcSel = $("mkr-source");
  let url = "/api/market/remote?offset=" + (S.mkrOffset || 0) + "&limit=" + MKR_PAGE;
  if (srcSel && srcSel.value) url += "&source=" + encodeURIComponent(srcSel.value);
  if (q) url += "&q=" + encodeURIComponent(q);
  S.mkrLoading = true;
  let data = null;
  try { data = await api(url); } catch (e) { data = null; }
  if (requestKey !== S.mkrRequestKey) return;
  S.mkrLoading = false;
  if (!data) { S.marketRemote = null; S.mkrAll = null; renderMarketRemote(); return; }
  S.marketRemote = data;
  S.mkrAll = (S.mkrAll || []).concat(data.entries || []);
  S.mkrOffset = S.mkrAll.length;
  S.mkrKey = mkrFilterKey();
  renderMarketRemote();
}

function mkrCardHtml(p) {
  let ops;
  if (p.installed) ops = '<span class="tag ok">' + t("已安装") + "</span>" +
    '<button class="ghost small" onclick="mkRemove(\'' + esc(p.id) + '\')">' + t("卸载") + "</button>";
  else if (p.compat === "blocked") ops = '<button class="ghost small" disabled title="' +
    esc(p.block_reason || t("该插件无法从公开生态下载")) + '">' + t("不适配") + "</button>";
  else ops = '<button class="primary small" data-mk="' + esc(p.id) + '" onclick="mkrInstall(\'' + esc(p.id) + '\')">' + t("安装") + "</button>";
  const meta = [p.author, p.version ? "v" + p.version : ""].filter(Boolean).join(" · ");
  return '<div class="card mk-card' + (p.compat === "blocked" ? " blocked" : "") + '"><div class="head">' +
    '<span class="name">' + esc(t(p.title || p.name)) + "</span>" +
    '<span class="tag mk-src">' + esc(t(p.source_name || "")) + "</span>" +
    (p.category ? '<span class="tag">' + esc(p.category) + "</span>" : "") +
    "</div>" +
    '<div class="note">' + esc(t(p.desc || "")) + "</div>" +
    '<div class="mk-meta"><span>' + esc(meta) + "</span></div>" +
    '<div class="ops">' + ops + "</div></div>";
}

function renderMarketRemote() {
  const grid = $("mkr-grid");
  if (!grid) return;
  if (mkActiveView() !== "remote") return;   // 本地视图时不动计数（renderMarket 负责）
  const data = S.marketRemote;
  if (!data) {
    grid.innerHTML = '<div class="empty">' + t("加载失败：服务未连接") + "</div>";
    mkCount("");
    return;
  }
  // 来源下拉：首次填充，之后保留用户选择（计数是目录全量，不是本页）
  const srcSel = $("mkr-source");
  if (srcSel && !srcSel.options.length) {
    srcSel.innerHTML = '<option value="">' + t("全部来源") + "</option>" +
      (data.sources || []).map((s) =>
        '<option value="' + esc(s.id) + '">' + esc(t(s.name)) + t("（") + s.count + t("）") + "</option>").join("");
  }
  const times = (data.sources || []).map((s) => s.fetched_at).filter(Boolean);
  const meta = $("mkr-meta");
  if (meta) meta.textContent = times.length ? t("目录更新于 ") + times.join(" / ") : "";
  const items = S.mkrAll || [];
  mkCount(t("已加载 ") + items.length + t(" / 共 ") + data.total + t(" 个"));
  if (!items.length) {
    grid.innerHTML = '<div class="empty">' + (data.total === 0 && !S.mkrOffset && !(($("mkr-search")||{}).value||"").trim() && !(srcSel||{}).value
      ? t("外部目录还是空的，点「拉取更新」从公开生态获取。")
      : t("没有符合条件插件")) + "</div>";
    const more = $("mkr-more");
    if (more) more.classList.add("hidden");
    return;
  }
  grid.innerHTML = items.map(mkrCardHtml).join("");
  const more = $("mkr-more");
  if (more) {
    more.classList.toggle("hidden", !data.has_more);
    const txt = $("mkr-more-txt");
    if (txt) txt.textContent = t("还有 ") + (data.total - items.length) + t(" 条，继续滚动加载");
    const btn = $("mkr-more-btn");
    if (btn) btn.disabled = false;
  }
}

/* 滚动哨兵进入视野即续页（main 是滚动容器，哨兵贴在列表尾）。 */
function mkrBindSentinel() {
  const sentinel = $("mkr-sentinel");
  if (!sentinel || !("IntersectionObserver" in window)) return;
  new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    if (mkActiveView() !== "remote") return;
    if (S.mkrLoading || !S.marketRemote || !S.marketRemote.has_more) return;
    mkrLoadPage(false);
  }, { root: document.querySelector("main"), rootMargin: "400px" }).observe(sentinel);
}

async function mkrRefresh() {
  const btn = $("mkr-refresh");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = t("拉取中…");
  try {
    const r = await api("/api/market/remote/refresh", {
      method: "POST", timeout: 120000, body: "{}" });
    const bad = (r.refresh || []).filter((x) => !x.ok);
    if (bad.length) toast(t("部分来源拉取失败：") + bad.map((x) => x.error || x.id).join(t("；")), true);
    else toast(t("拉取成功"));
    // 刷新后来源计数变了：下拉重建并保留当前选择，再重拉第一页
    const srcSel = $("mkr-source");
    if (srcSel) {
      const keep = srcSel.value;
      srcSel.innerHTML = '<option value="">' + t("全部来源") + "</option>" +
        (r.sources || []).map((s) =>
          '<option value="' + esc(s.id) + '">' + esc(t(s.name)) + t("（") + s.count + t("）") + "</option>").join("");
      srcSel.value = keep;
    }
    mkrLoadPage(true);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
  btn.textContent = old;
}

async function mkrInstall(id) {
  // 安装要现场下载插件包（zip/tarball，实测 10~60s），必须立刻给反馈，
  // 否则用户以为点了没反应；按钮按 data-mk 定位，失败时由重拉页面复位
  const btn = document.querySelector('#mkr-grid button[data-mk="' + id + '"]');
  if (btn) { btn.disabled = true; btn.textContent = t("安装中…"); }
  toast(t("正在下载安装，插件包较大时需要约一分钟…"));
  try {
    const r = await api("/api/market/remote/install", { method: "POST",
      body: JSON.stringify({ id }), timeout: 180000 });
    if (r && r.already) toast(t("该插件已安装过"));
    else {
      // 剥离式安装：脚本/钩子/MCP 文件被剔除但技能装上了，如实告知剔除了什么
      const stripped = (r && r.stripped) || [];
      toast(t("安装成功，运行任务时自动注入提示词；可到「经验库」启停或查看") + (stripped.length
        ? t("；已自动剥离脚本/钩子/MCP 文件：") + stripped.slice(0, 3).join("、")
          + (stripped.length > 3 ? t(" 等 ") + stripped.length + t(" 个文件") : "")
        : ""));
    }
  } catch (e) { toast(e.message, true); }
  mkrLoadPage(true);   // 重装当前过滤页（已安装徽章要翻牌，失败也复位按钮）
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
    btn.title = t("尚未选择编排者供应商\n点击到「编排设置」，从已接入的厂商里选一个");
    return;
  }
  txt.textContent = name;
  dot.className = "pdot " + (o.ready ? "ok" : "warn");
  btn.title = t("编排者供应商：") + name + (model ? " · " + model : "") +
    (o.ready ? t("（生效中）") : t("（未生效：检查密钥 / 启停 / 模型）")) +
    t("\n点击到「编排设置」更换");
}

async function loadSettings() {
  try {
    S.settings = await api("/api/settings");
    const inp = $("set-workers");
    if (inp && S.settings) inp.value = S.settings.max_concurrent_jobs;
    const tel = $("set-telemetry");
    if (tel && S.settings) tel.checked = S.settings.telemetry_errors !== false;
    const pet = $("set-pet");
    if (pet && S.settings) pet.checked = S.settings.pet_enabled !== false;
    const cs = $("set-claude-sync");
    if (cs && S.settings) cs.checked = S.settings.claude_config_sync !== false;
    syncPetModeSeg();
    syncPetSkinSeg();
    const wd = $("set-workdir"), hint = $("set-workdir-hint");
    if (wd && S.settings) wd.value = S.settings.default_workdir_effective || "";
    if (hint && S.settings) {
      const custom = !!(S.settings.default_workdir || "").trim();
      hint.textContent = custom ? t("当前生效：") + S.settings.default_workdir_effective + t("（自定义）")
                                : t("当前生效：") + S.settings.default_workdir_effective + t("（内置默认，尚未自定义）");
    }
    // 新任务表单：默认就「选中」默认路径（参考工作区选择器的默认选中态）；
    // 手动清空输入仍表示跟随默认。f-workdir-hint 保留给「继续会话/克隆任务」的覆盖警告
    const wdInput = $("f-workdir");
    if (wdInput && S.settings && !wdInput.value.trim()) {
      wdInput.value = S.settings.default_workdir_effective || "";
      queueGitProbe();   // 目录在场即探测代码版本，点亮分支胶囊
    }
  } catch (e) { /* 忽略 */ }
  loadSettingsV2();   // 引擎调参卡独立拉取（挂了不影响基础设置）
}

/* ---------------- settings_v2 引擎调参卡（schema 驱动） ----------------
 * /api/settings-v2 拉 describe（含字段元数据），按 namespace 渲染控件；
 * 保存走 /api/settings-v2/<ns> mutate（expected_revision CAS，409=别处已改，
 * 自动重拉最新值）。schema 变更零前端改动。 */
async function loadSettingsV2() {
  const box = $("set-v2-card");
  if (!box) return;
  let v2 = null;
  try { v2 = await api("/api/settings-v2"); } catch (e) { box.innerHTML = ""; return; }
  S.settingsV2 = v2;
  const NS_LABEL = { orchestrator: t("编排引擎"), budget: t("预算"), cascade: t("级联路由") };
  let html = "<label>" + esc(t("引擎调参（schema 化设置，立即生效）")) + "</label>";
  for (const ns of Object.keys(v2.namespaces || {})) {
    const d = v2.namespaces[ns] || {};
    html += '<div class="set-v2-ns" data-ns="' + esc(ns) + '" data-rev="' + (d.revision || 0) + '">' +
      '<div class="set-v2-ns-h">' + esc(NS_LABEL[ns] || ns) +
      '<span class="flex1"></span><span class="set-v2-rev">rev ' + (d.revision || 0) + "</span></div>";
    for (const f of (d.fields || [])) {
      const iid = "setv2-" + esc(ns) + "-" + f.path.replace(/\./g, "-");
      const val = f.path.split(".").reduce((o, k) => (o && o[k] !== undefined) ? o[k] : undefined, d.values || {});
      const cur = val === undefined ? "" : val;
      let inp;
      if (f.type === "bool") {
        inp = '<input id="' + iid + '" type="checkbox"' + (cur ? " checked" : "") + ">";
      } else if (f.type === "int" || f.type === "float") {
        const step = f.type === "float" ? "0.01" : "1";
        inp = '<input id="' + iid + '" type="number" step="' + step + '" value="' + esc(String(cur)) + '"' +
          (f.clamp ? ' min="' + f.clamp[0] + '" max="' + f.clamp[1] + '"' : "") + ' style="max-width:140px">';
      } else {
        inp = '<input id="' + iid + '" type="text" value="' + esc(String(cur)) + '">';
      }
      html += '<div class="set-v2-row">' + inp +
        '<span class="set-v2-lb" title="' + esc(t(f.description || f.path)) + '">' + esc(t(f.description || f.path)) + "</span></div>";
    }
    html += '<div class="input-row" style="margin-top:6px"><button class="ghost small" onclick="saveSettingsV2(\'' + esc(ns) + '\')">' + t("保存") + "</button>" +
      '<span class="set-v2-msg msg"></span></div></div>';
  }
  box.innerHTML = html;
}

window.saveSettingsV2 = async function (ns) {
  const v2 = S.settingsV2 || {};
  const d = (v2.namespaces || {})[ns];
  const box = document.querySelector('.set-v2-ns[data-ns="' + ns + '"]');
  if (!d || !box) return;
  const ops = [];
  for (const f of (d.fields || [])) {
    const iid = "setv2-" + ns + "-" + f.path.replace(/\./g, "-");
    const el = document.getElementById(iid);
    if (!el) continue;
    if (f.type === "bool") ops.push({ op: "set", path: f.path, value: el.checked });
    else if (f.type === "int" || f.type === "float") ops.push({ op: "set", path: f.path, value: Number(el.value) });
    else ops.push({ op: "set", path: f.path, value: el.value });
  }
  const msg = box.querySelector(".set-v2-msg");
  try {
    const r = await api("/api/settings-v2/" + ns, {
      method: "POST", body: JSON.stringify({ ops, expected_revision: d.revision || 0 }) });
    if (msg) { msg.className = "set-v2-msg msg ok"; msg.textContent = t("已保存 rev ") + r.revision; }
    loadSettingsV2();
  } catch (e) {
    if (/期望 rev/.test(String(e.message))) {
      if (msg) { msg.className = "set-v2-msg msg err"; msg.textContent = t("配置已被别处修改，已刷新，请重试"); }
      loadSettingsV2();
    } else if (msg) { msg.className = "set-v2-msg msg err"; msg.textContent = e.message; }
  }
};

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
  return '<option value="">' + t("（用供应商默认模型）") + '</option>' +
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
  const opts = '<option value="">' + t("（不使用编排者）") + '</option>' + provs.map((p) =>
    '<option value="' + esc(p.id) + '"' + (S.orch.provider_id === p.id ? " selected" : "") + ">" +
    esc(p.name) + t("（") + esc(protoLabel(p)) + t("）") + "</option>").join("");
  const curModel = S.orch.model || (sel ? sel.model : "") || "";
  box.innerHTML =
    '<div class="grid-2">' +
    '<div class="field"><label>' + t("编排者供应商") + '</label><select id="orch-prov">' + opts + "</select></div>" +
    '<div class="field"><label>' + t("编排者模型") + '</label><select id="orch-model">' + orchModelHtml(sel, curModel) + "</select></div></div>" +
    '<div class="ops"><label class="toggle"><input type="checkbox" id="orch-enabled"' +
    (S.orch.enabled ? " checked" : "") + ">" + t(" 启用编排者（规划 / 难度判定 / 写作大纲）") + "</label>" +
    '<button class="ghost small" onclick="saveOrchestrator()">' + t("保存") + '</button>' +
    '<button class="ghost small" onclick="testOrchestrator()">' + t("测试连通") + '</button>' +
    '<span id="orch-test" class="msg"></span></div>' +
    (S.orch.enabled && !S.orch.ready ? '<p class="hint warn">' + t("当前配置不生效：请检查供应商密钥、启停状态与模型选择。") + '</p>' : "");
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
      max_concurrent_jobs: parseInt($("set-workers").value, 10),
      claude_config_sync: !$("set-claude-sync") || $("set-claude-sync").checked }) });
    S.settings = r.settings;
    if (msg) { msg.className = "msg ok"; msg.textContent = t("已保存：编排并发上限 ") + r.workers; }
  } catch (e) {
    if (msg) { msg.className = "msg err"; msg.textContent = e.message; }
  }
}

/* ---------------------------------------------------------- 关于与更新（selfupdate）
 * 后端 /api/selfupdate：mode=npm 才可自动升级；repo（git clone）提示 git pull。
 * 升级 = 建 mgmt run 跑 npm install -g @latest（日志实时落盘）→ 服务自动重启，
 * 前端等待断线恢复后自动刷新。 */
let SU = null;          // 最近一次 check() 结果
let SU_TIMER = null;    // 升级 run 轮询句柄
let SU_RESTART_TIMER = null;

const SU_MODE_TXT = { npm: "npm 全局安装", repo: "开发仓库（git clone）", source: "源码拷贝", other: "未知安装方式" };

async function loadSelfupdate(force) {
  try {
    SU = await api("/api/selfupdate" + (force ? "?force=1" : ""), { busy: !!force });
  } catch (e) { SU = null; }
  renderSu();
  maybeWhatsnew();
  return SU;
}

/* 升级重启后的一次性「本次更新内容」：localStorage 记住已展示的版本，
 * 版本变化且有本地 changelog 小节时弹窗（首次使用只记账不弹）。 */
function maybeWhatsnew() {
  if (!SU || !SU.current) return;
  const prev = localStorage.getItem("su.myver");
  localStorage.setItem("su.myver", SU.current);
  if (prev && prev !== SU.current && SU.whatsnew) {
    openModal(t("本次更新内容"),
      "<p class=\"hint\">" + t("已更新到") + " <b>v" + esc(SU.current) + "</b></p>" +
      "<pre class=\"su-notes\">" + esc(SU.whatsnew) + "</pre>",
      "<button class=\"small\" onclick=\"closeModal()\">" + t("知道了") + "</button>");
  }
}

/* ===== 帮助中心（首启欢迎引导升级版）：左目录右内容 =====
 * 首启自动弹（orch.welcomed 记账只弹一次，徽章「首次启动」，落在「快速上手」章）；
 * 手动开（设置导航/关于页/Ctrl+K）徽章「帮助中心」。加新章 = HELP_CHAPTERS
 * 追加一条 + helpChapterBody 加一个分支；todo:true 的章自动渲染「整理中」占位。 */
var HELP_CHAPTERS = [
  { id: "quickstart", icon: "i-rocket",        label: "快速上手" },
  { id: "models",     icon: "i-blocks",        label: "模型接入与绑定" },
  { id: "features",   icon: "i-bee",           label: "功能一览" },
  { id: "serial",     icon: "i-book-open",     label: "连载创作流程" },
  { id: "publish",    icon: "i-clapper",       label: "发布上架" },
  { id: "code",       icon: "i-git-branch",    label: "代码任务与版本" },
  { id: "auto",       icon: "i-calendar-days", label: "自动化与技能市场" },
  { id: "faq",        icon: "i-search",        label: "常见问题" },
];
var helpCur = "quickstart";
var helpFirst = false;   // 本次打开是否首启自动弹（决定徽章文案；语言切换时也要用）

function helpChapterBody(id) {
  if (id === "quickstart") {
    return '<div class="welcome-steps">' +
      '<div class="wstep"><b>1</b><span>' + t("添加模型供应商：设置 → 模型接入，填入 API Key") + '</span></div>' +
      '<div class="wstep"><b>2</b><span>' + t("启用本机智能体：运行时默认自动推荐模型") + '</span></div>' +
      '<div class="wstep"><b>3</b><span>' + t("新建任务：选工作目录、写目标，蜂群开工") + '</span></div>' +
      '</div>' +
      '<p class="help-note">' + t("任务跑起来后，详情页能看到步骤、蜂巢、成果文件与 Git 版本；「直接执行」类任务还能像聊天一样边跑边追加消息。") + '</p>' +
      '<div class="welcome-acts">' +
      '<button class="wl-btn primary" onclick="welcomeGo(\'models\')"><span>' + t("开始配置模型") + '</span><svg class="ico" aria-hidden="true"><use href="#i-chevron-r"></use></svg></button>' +
      '<button class="wl-btn" onclick="welcomeGo(\'bindings\')"><span>' + t("模型调度（可选）") + '</span><svg class="ico" aria-hidden="true"><use href="#i-chevron-r"></use></svg></button>' +
      '<button class="wl-btn ghost" onclick="welcomeClose()"><span>' + t("先跳过，直接体验") + '</span></button>' +
      '</div>';
  }
  if (id === "models") {
    return '<h3 class="help-h3">' + t("第一步：添加供应商") + '</h3>' +
      '<p class="help-p">' + t("「设置 → 模型接入 → 添加供应商」：填名称、API Key、接口地址（一般用默认）。保存后点「获取模型列表」自动拉取该厂商的模型；网关不提供列表接口时直接手工填模型名即可。同一厂商可配多把 Key，按顺序轮换，某把欠费自动切下一把。") + '</p>' +
      '<h3 class="help-h3">' + t("第二步：认识协议徽章") + '</h3>' +
      '<p class="help-p">' + t("新供应商默认自动识别协议，保存后在后台实测支持哪些调用形态，结果以徽章标在卡片上。带「· chat」表示该模型只适合站内直连对话；codex 这类只讲 responses 协议的 CLI 用不了它，绑定页会自动剔除，不用自己排查。") + '</p>' +
      '<h3 class="help-h3">' + t("第三步：启用本机智能体") + '</h3>' +
      '<p class="help-p">' + t("无需预先绑定模型。CodeBee 默认按任务类型、难度、成本和健康状态自动推荐；需要固定厂商、模型或降级顺序时，再到「设置 → 模型调度（可选）」指定。") + '</p>' +
      '<h3 class="help-h3">' + t("报错速查") + '</h3>' +
      '<ul class="help-list">' +
      '<li><b>401</b><span>' + t("Key 无效或过期——检查或更换 Key。") + '</span></li>' +
      '<li><b>404</b><span>' + t("模型名不对；若发生在「获取模型列表」，是网关不提供列表接口，能正常对话就不用管。") + '</span></li>' +
      '<li><b>503</b><span>' + t("该模型当前无可用渠道——查余额或换一个模型。") + '</span></li>' +
      '<li><b>' + t("冷却中") + '</b><span>' + t("这把 Key 连续失败被暂停 30 分钟，到期自动恢复，也可在密钥旁手动重置。") + '</span></li>' +
      '</ul>' +
      '<p class="help-note">' + t("想零成本先跑通：挑有免费档位的厂商配一个模型，跑通第一个任务后再按需加码。") + '</p>';
  }
  if (id === "features") {
    return '<ul class="help-feats">' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-workflow"></use></svg><div><b>' + t("多智能体编排") + '</b><span>' + t("规划 · 实现 · 评审 · 打磨，接棒交付") + '</span></div></li>' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-book-open"></use></svg><div><b>' + t("连载写作") + '</b><span>' + t("断点续跑 · 经验沉淀 · 作品资料") + '</span></div></li>' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-clapper"></use></svg><div><b>' + t("发布上架") + '</b><span>' + t("一键发布到番茄、七猫，含封面与作品资料") + '</span></div></li>' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-git-branch"></use></svg><div><b>' + t("版本工作台") + '</b><span>' + t("任务分支隔离，改动可审可合") + '</span></div></li>' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-calendar-days"></use></svg><div><b>' + t("自动化") + '</b><span>' + t("定时任务，无人值守") + '</span></div></li>' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-library"></use></svg><div><b>' + t("经验库") + '</b><span>' + t("内置规范包 + 自动沉淀教训，越用越顺手") + '</span></div></li>' +
      '<li><svg class="ico" aria-hidden="true"><use href="#i-blocks"></use></svg><div><b>' + t("插件市场") + '</b><span>' + t("600+ 技能，一键安装") + '</span></div></li>' +
      '</ul>';
  }
  if (id === "serial") {
    return '<h3 class="help-h3">' + t("开一本新书") + '</h3>' +
      '<p class="help-p">' + t("新建任务选「连载写作」类型，目标里写清书名、题材和计划篇幅（如「都市异能，先写 20 章」）。目标写得太笼统时，蜂群会先反问几个澄清问题再开工。开书前会先准备作品资料：书简介与分类标签可一键生成，按平台官方选项实抓，不用自己去查规则。") + '</p>' +
      '<h3 class="help-h3">' + t("每章怎么跑") + '</h3>' +
      '<p class="help-p">' + t("大纲确认后分章推进：起草 → 跨厂商评审 → 打磨 → 定稿。评审不过会自动换将重写，不带着问题过关；需要你拍板的地方会亮起「待裁决」。写作时详情页可实时预览章节，蜂巢页每个智能体一格，点开能看谁在干什么。") + '</p>' +
      '<h3 class="help-h3">' + t("分卷（卷结构）") + '</h3>' +
      '<p class="help-p">' + t("长篇建议分卷。两种给法：填「每卷章数」（默认预填 20，清空 = 不分卷），系统按全书章号自动切卷；或者直接写明各卷——在「指定卷结构」框里，也可以在任务目标里写「第一卷 少年初入江湖 第1-20章；卷二 风云再起 21-40章」，会自动识别。给了卷名的会原样沿用，不会被改写。") + '</p>' +
      '<p class="help-p">' + t("分卷会贯穿全程：大纲按卷给卷名与卷弧光（本卷主线冲突与卷末高潮）；每章起草和评审都带着「第几卷第几章」，靠近卷末会提醒收拢支线、卷末章必须写出本卷高潮与卷末钩子；成书自动插入「第 X 卷《卷名》」分隔；详情页章节卡与发布页清单按卷分组，发章时一卷一卷地发。卷边界只看全书章号，所以续写批次会自动接上同一卷，不会把一卷劈成两半。") + '</p>' +
      '<h3 class="help-h3">' + t("中断了怎么办") + '</h3>' +
      '<p class="help-p">' + t("任务随时可续跑：已写章节都落了盘，续跑接着往下写，不会从头再来。单章失败会自动退避重试，任务状态里会显示「将于 HH:MM 自动续跑」，不用盯着等。") + '</p>' +
      '<h3 class="help-h3">' + t("递话与经验") + '</h3>' +
      '<p class="help-p">' + t("跑的过程中可以在详情页给蜂群递话（文字/截图/附件），下一轮生效。每轮结束复盘官会把踩过的坑沉淀进经验库，后续任务自动复用，越用越顺手。") + '</p>';
  }
  if (id === "publish") {
    return '<h3 class="help-h3">' + t("第一次发布前：登录平台") + '</h3>' +
      '<p class="help-p">' + t("目前支持番茄小说与七猫。第一次发布会引导你在浏览器里登录平台账号（扫码或账密），登录态保存在本机，之后发布免登录；过期了重新登一次即可。") + '</p>' +
      '<h3 class="help-h3">' + t("建书：资料与封面") + '</h3>' +
      '<p class="help-p">' + t("连载任务的建书面板里，作品简介、分类标签可以按平台官方选项一键生成；封面点「生成封面」自动出竖版图（cover.png 落在任务运行目录）。") + '</p>' +
      '<h3 class="help-h3">' + t("发章与确认") + '</h3>' +
      '<p class="help-p">' + t("章稿定稿后一键发布：自动填标题正文、处理平台的各类弹层，但提交前会停下来让你确认——绝不静默替你上架。发布完成后任务详情里能看到结果与章节链接。") + '</p>' +
      '<h3 class="help-h3">' + t("发布失败") + '</h3>' +
      '<p class="help-p">' + t("平台改版或登录态过期会导致失败，失败信息会写明原因。重新登录后再点重试即可；反复失败可以到 GitHub 提 Issue 附上失败截图。") + '</p>';
  }
  if (id === "code") {
    return '<h3 class="help-h3">' + t("代码任务怎么跑") + '</h3>' +
      '<p class="help-p">' + t("新建任务选开发类类型，目标写清楚要做什么（可以附需求文档、截图，附件会进入上下文）。蜂群先出计划再实现：实现 → 客观验证 → 跨厂商评审 → 修复，单路实现失败还有多候选赛马——各候选在隔离的 worktree 里各写各的，择优采纳。跑的过程中可以在详情页递话（文字/截图），下一轮生效。") + '</p>' +
      '<h3 class="help-h3">' + t("任务分支隔离：你的工作区不受影响") + '</h3>' +
      '<p class="help-p">' + t("启用代码版本隔离的任务会检出独立的任务分支（codebee/任务ID），所有改动只落在那条分支上，你当前的分支和未提交的改动完全不动——开跑前工作区有脏改动会先帮你收起（stash），跑完自动还原。") + '</p>' +
      '<h3 class="help-h3">' + t("版本裁决：合并还是丢弃") + '</h3>' +
      '<p class="help-p">' + t("任务跑完后，「版本」页签会亮起「待裁决」等你拍板：先看变更清单，双击文件能看 diff；满意点「合并」，任务分支合回基线分支；不满意点「丢弃」，分支和工作现场自动清场。中途停下的任务也会留好提交，可以继续跑完再裁决。合并与丢弃绝不静默替你决定。") + '</p>' +
      '<h3 class="help-h3">' + t("哪里看细节") + '</h3>' +
      '<p class="help-p">' + t("「蜂巢」页签看每个智能体实时在干什么（点格子看实时日志）；「步骤」页签看任务拆解、每步实际用的模型、耗时与失败原因，评审分数与打磨记录也在这里。") + '</p>';
  }
  if (id === "auto") {
    return '<h3 class="help-h3">' + t("定时任务") + '</h3>' +
      '<p class="help-p">' + t("设置 → 自动化：把任意任务类型挂上时间表（每天/每周固定时刻），到点自动按真实运行链开跑，结果照常落盘。配合群机器人（钉钉/飞书/企微自动适配）可以把完成状态推到群里。") + '</p>' +
      '<h3 class="help-h3">' + t("禅道 Bug 自动修复") + '</h3>' +
      '<p class="help-p">' + t("设置 → 禅道：配好地址与产品档案后，定时扫描激活 Bug，按模块路由 / AI 排查定责（前端/后端/双端/非我方）；我方端的 Bug 自动创建代码修复任务，修完自动合并、resolve 并回写报告到群里；测试指错人的也会按排查结论改派。合并失败不 resolve 不转派，留人工兜底。") + '</p>' +
      '<h3 class="help-h3">' + t("技能市场") + '</h3>' +
      '<p class="help-p">' + t("设置 → 插件市场：600+ 技能一键安装，来源包括内置库和 ZCode、Anthropic 等多个外部目录。装上的技能会注入智能体能力，按任务类型生效。") + '</p>' +
      '<h3 class="help-h3">' + t("用量台账") + '</h3>' +
      '<p class="help-p">' + t("设置 → 用量统计：每次调用的模型、token 与费用按多个维度聚合展示，历史运行会在启动时自动回填——每个任务花了多少，一目了然。") + '</p>';
  }
  if (id === "faq") {
    return '<div class="help-qa"><b>' + t("点「获取模型列表」报 404？") + '</b><p>' + t("部分网关不提供模型列表接口，属正常现象；只要能正常对话就不用管，模型名手工填即可。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("报 503 / 无可用渠道？") + '</b><p>' + t("该模型当前没有可用渠道，最常见是余额耗尽。查一下余额或换个模型；同一厂商配了多把 Key 会自动轮换。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("「冷却中」是什么意思？") + '</b><p>' + t("一把 Key 连续失败会被暂停 30 分钟，防止反复撞墙烧钱，到期自动恢复；也可以在密钥旁手动重置。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("任务一直显示「排队」？") + '</b><p>' + t("编排任务满载（达到并发上限）时自动转入「排队中」，每 15 秒尝试补跑，最长等 10 分钟，超时才判失败；排队不占额外资源，取消随时可停。direct 对话走独立轻量通道，不占编排并发额度。自动续跑的退避等待会显示预定恢复时间；服务重启遗留的排队记录由恢复机制立即接管或收口。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("状态里写着「将于 HH:MM 自动续跑」？") + '</b><p>' + t("这一步失败了，正在退避等待自动重试，到点会接着跑，不需要手动干预；等不及也可以在详情页手动重试。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("生成的文件在哪？") + '</b><p>' + t("写作类任务的章节、封面、报告都落在任务的运行目录，详情页「成果」页签可浏览和预览。代码类任务则在仓库的任务分支上改代码，详情页「版本」里审阅后再合并。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("忘了令牌 / 手机打不开页面？") + '</b><p>' + t("服务启动日志里有带令牌的完整访问地址；远程设备必须用带令牌的 URL 打开（或在令牌门里输入一次），否则会一直要求授权。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("升级后数据会丢吗？") + '</b><p>' + t("不会。任务、配置、用量台账都在独立的数据目录里，升级只替换程序本体；老版本装新版会自动沿用原数据目录。") + '</p></div>' +
      '<div class="help-qa"><b>' + t("页面打不开 / 服务起不来？") + '</b><p>' + t("多半是默认端口 8765 被旧进程占着：结束旧的 python 进程，或启动时用 --port 换个端口。还不行就到 GitHub 提 Issue。") + '</p></div>';
  }
  const ch = HELP_CHAPTERS.find((c) => c.id === id);
  if (ch && ch.todo) {
    return '<div class="help-todo"><b>' + t(ch.label || "") + '</b><p>' +
      t("本章节正在整理，可先看「快速上手」与「模型接入与绑定」，或到 GitHub 提 Issue 提问。") + '</p></div>';
  }
  return "";
}

function renderHelp() {
  const toc = $("help-toc");
  if (toc) {
    toc.innerHTML = HELP_CHAPTERS.map((c) =>
      '<button class="hitem' + (c.id === helpCur ? " active" : "") + (c.todo ? " todo" : "") +
      '" data-ch="' + c.id + '" onclick="helpGo(\'' + c.id + '\')">' +
      '<svg class="ico" aria-hidden="true"><use href="#' + c.icon + '"></use></svg><span>' + t(c.label) + '</span>' +
      (c.todo ? '<em class="h-todo">' + t("整理中") + '</em>' : "") +
      '</button>').join("");
  }
  const body = $("help-body");
  if (body) {
    body.innerHTML = helpChapterBody(helpCur);
  }
}
function setHelpChapter(id) {
  helpCur = HELP_CHAPTERS.some((c) => c.id === id) ? id : "quickstart";
  renderHelp();
}
function helpGo(id) { setHelpChapter(id); }
function paintHelpChrome() {
  const badge = $("welcome-badge");
  if (badge) badge.textContent = t(helpFirst ? "首次启动" : "帮助中心");
  const title = $("welcome-title");
  if (title) title.textContent = t(helpFirst ? "欢迎使用 CodeBee" : "帮助中心");
}
function welcomeOpen(opts) {
  const w = $("welcome");
  if (!w) return;
  if (!$("token-gate").classList.contains("hidden")) return;
  helpFirst = !!(opts && opts.firstRun);
  if (helpFirst) helpCur = "quickstart";
  else if (opts && opts.topic && HELP_CHAPTERS.some((c) => c.id === opts.topic)) helpCur = opts.topic;
  paintHelpChrome();
  renderHelp();
  w.classList.remove("hidden");
  document.body.classList.add("welcome-open");
}
function welcomeClose() {
  const w = $("welcome");
  if (!w) return;
  w.classList.add("hidden");
  document.body.classList.remove("welcome-open");
  try { localStorage.setItem("orch.welcomed", "1"); } catch (e) { /* 存储不可用则下次再弹 */ }
}
function welcomeGo(tab) {
  welcomeClose();
  switchTab(tab);   // 直达「模型接入」/「CLI 绑定」
}
function maybeWelcome() {
  let seen = "";
  try { seen = localStorage.getItem("orch.welcomed"); } catch (e) { /* ignore */ }
  if (!seen) welcomeOpen({ firstRun: true });
}
window.welcomeOpen = welcomeOpen;
window.welcomeClose = welcomeClose;
window.welcomeGo = welcomeGo;
window.helpGo = helpGo;
// 空状态里的「看帮助」链接用：打开帮助中心对应章并吞掉默认跳转
window.helpOpenTopic = function (topic) { welcomeOpen({ topic: topic }); return false; };

/* 就地帮助问号：页面里任何 .hhelp（data-help-topic）点击直达帮助中心对应章。
 * 事件委托挂在 document 上，动态渲染出来的问号无需逐个接线。 */
document.addEventListener("click", (e) => {
  const btn = e.target && e.target.closest ? e.target.closest(".hhelp") : null;
  if (!btn) return;
  welcomeOpen({ topic: btn.dataset.helpTopic });
});

function renderSu() {
  const info = $("su-info");
  if (!info) return;
  if (!SU) { info.textContent = t("无法获取版本信息（服务未连接）"); return; }
  // 真实内容就绪后摘掉「空状态」皮肤：.empty 是居中+虚线框的加载占位样式，
  // 版本信息与更新说明都应该左对齐阅读（用户反馈「能不能左对齐」）
  info.classList.remove("empty");
  const modeTxt = t(SU_MODE_TXT[SU.mode] || "未知安装方式");
  const cur = SU.current ? "v" + SU.current : t("（未同步版本号）");
  let html = "<p class=\"hint\">" + t("当前版本") + t("：") + "<b>" + cur + "</b>" + t("　·　") + t("安装方式") + t("：") + modeTxt + "</p>";
  if (SU.has_update) {
    html += "<p class=\"hint\"><b>" + t("发现新版本") + " v" + SU.latest +
      t("　") + "<a href=\"#\" onclick=\"event.preventDefault();suApply()\">" + t("立即升级") + "</a></b></p>";
    if (SU.notes) {
      html += "<div class=\"hint\"><b>" + t("新版本更新内容") + t("：") + "</b>" +
        renderRelnotes(SU.notes) + "</div>";
    }
  } else if (SU.mode === "npm" && !SU.note) {
    html += "<p class=\"hint\">" + t("已是最新版。") + "</p>";
  }
  info.innerHTML = html;
  const note = $("su-note");
  if (note) note.textContent = t(SU.note || "");
  const b = $("su-check");
  if (b) b.disabled = false;
  $("su-apply").classList.toggle("hidden", !SU.has_update);
  renderUpdPill();
}

/* 更新说明分类渲染：### 行渲染为小节标题，- 行渲染为条目（对照主流产品的
 * 更新日志样式：问题修复/新功能分组，一眼看懂）。全部 esc 后拼接。 */
function renderRelnotes(txt) {
  const lines = String(txt || "").replace(/\r/g, "").split("\n");
  let html = "", inList = false;
  const closeList = () => { if (inList) { html += "</div>"; inList = false; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (/^#{2,4}\s+/.test(line)) {
      closeList();
      html += "<p style=\"margin:6px 0 2px\"><b>" + esc(line.replace(/^#{2,4}\s+/, "")) + "</b></p>";
    } else if (/^[-*]\s+/.test(line)) {
      if (!inList) { html += "<div style=\"margin:2px 0 2px 4px\">"; inList = true; }
      html += "<div style=\"display:flex;gap:6px\"><span style=\"opacity:.6\">·</span>" +
        "<span style=\"flex:1\">" + esc(line.replace(/^[-*]\s+/, "")) + "</span></div>";
    } else {
      closeList();
      html += "<p style=\"margin:2px 0\">" + esc(line) + "</p>";
    }
  }
  closeList();
  return html || ("<pre class=\"su-notes\">" + esc(txt) + "</pre>");
}

/* 顶栏左上角常驻更新胶囊：有新版才出现（同版本被忽略后不再出现），
 * 点击直达「关于与更新」，× 忽略本版本（与 su.seen 记账键同一套语义） */
function renderUpdPill() {
  const pill = $("upd-pill");
  if (!pill) return;
  const show = !!(SU && SU.has_update && localStorage.getItem("su.seen") !== SU.latest);
  pill.classList.toggle("hidden", !show);
  if (show) $("upd-pill-txt").textContent = t("新版本 v") + SU.latest;
}

function updDismiss() {
  if (SU && SU.latest) localStorage.setItem("su.seen", SU.latest);
  renderUpdPill();
  toast(t("已忽略该版本提醒，可随时在「关于与更新」里升级"));
}

/* 匿名错误报告开关：改完立即生效（后端每轮上报前都会读一次设置） */
async function saveTelemetry(on) {
  try {
    const r = await api("/api/settings", { method: "POST", body: JSON.stringify({ telemetry_errors: !!on }) });
    S.settings = r.settings;
    toast(on ? t("已开启匿名错误报告，感谢帮助改进 CodeBee")
             : t("已关闭匿名错误报告，数据不再离开本机"));
  } catch (e) {
    const tel = $("set-telemetry");
    if (tel) tel.checked = !on;   // 存失败弹回旧值，不让 UI 与后端各说各话
    toast(e.message || t("保存失败"), true);
  }
}

/* 桌面蜜蜂：开关与显示模式都落 settings（唯一真源），蜜蜂子进程轮询到变化
 * 自行启停——开=true 时看护线程 5s 内拉起，false 时在岗蜜蜂下一个轮询自离。 */
function syncPetModeSeg() {
  const mode = (S.settings && S.settings.pet_mode) || "always";
  document.querySelectorAll("#pet-mode [data-pmode]").forEach((b) =>
    b.classList.toggle("active", b.dataset.pmode === mode));
}
async function savePet(on) {
  try {
    const r = await api("/api/settings", { method: "POST", body: JSON.stringify({ pet_enabled: !!on }) });
    S.settings = r.settings;
    toast(on ? t("蜜蜂已放出，就落在桌面右下角") : t("蜜蜂已回巢"));
  } catch (e) {
    const pet = $("set-pet");
    if (pet) pet.checked = !on;   // 存失败弹回旧值，不让 UI 与后端各说各话
    toast(e.message || t("保存失败"), true);
  }
}
async function savePetMode(mode) {
  try {
    const r = await api("/api/settings", { method: "POST", body: JSON.stringify({ pet_mode: mode }) });
    S.settings = r.settings;
    syncPetModeSeg();
  } catch (e) {
    syncPetModeSeg();   // 弹回当前真值
    toast(e.message || t("保存失败"), true);
  }
}

/* 桌面蜜蜂形象：落 settings，蜜蜂轮询到 pet_skin 变化原位换肤（不用重启） */
function syncPetSkinSeg() {
  const skin = (S.settings && S.settings.pet_skin) || "plush";
  document.querySelectorAll("#pet-skin [data-pskin]").forEach((b) =>
    b.classList.toggle("active", b.dataset.pskin === skin));
}
async function savePetSkin(skin) {
  try {
    const r = await api("/api/settings", { method: "POST", body: JSON.stringify({ pet_skin: skin }) });
    S.settings = r.settings;
    syncPetSkinSeg();
    toast(t("蜜蜂换好新装了"));
  } catch (e) {
    syncPetSkinSeg();
    toast(e.message || t("保存失败"), true);
  }
}

/* 导出诊断包：脱敏错误台账 + 用量统计 + 版本环境信息（zip），贴 Issue 用 */
async function exportDiagBundle() {
  const b = $("diag-export");
  if (b) b.disabled = true;
  const busyToken = requestBusyStart({ method: "GET", busy: true, busyElement: b });
  try {
    const r = await fetch("/api/diagnostics/bundle", { headers: authHeaders() });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const u = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = u;
    a.download = "codebee-diag-" + new Date().toISOString().slice(0, 10) + ".zip";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 5000);
    toast(t("诊断包已开始下载，反馈问题时可附在 Issue 里"));
  } catch (e) {
    toast(t("诊断包导出失败：") + e.message, true);
  } finally {
    requestBusyEnd(busyToken);
    if (b) b.disabled = false;
  }
}

/* 一键反馈 Issue：拉脱敏摘要 → 预填 GitHub Issue 新建页（用户亲手提交，不自动回传） */
async function reportIssue() {
  try {
    const s = await api("/api/diagnostics/issue-summary", { busy: true });
    const url = "https://github.com/Vercel-By-WXP/CodeBee/issues/new" +
      "?title=" + encodeURIComponent(s.title || "") +
      "&body=" + encodeURIComponent(s.body || "");
    window.open(url, "_blank");
    toast(t("已打开 GitHub 反馈页，内容已预填，可直接提交（也可附上诊断包 zip）"));
  } catch (e) {
    toast(t("反馈摘要生成失败：") + e.message, true);
  }
}

/* 端口占用诊断（借鉴 leftopen）：谁占着端口、属于哪个项目；可温和关闭 */
async function scanPorts() {
  const box = $("ports-list");
  if (!box) return;
  const b = $("ports-scan");
  if (b) b.disabled = true;
  try {
    const r = await api("/api/ports");
    const ports = r.ports || [];
    if (!ports.length) { box.innerHTML = '<span class="hint">' + esc(t("没有发现监听端口")) + "</span>"; return; }
    box.innerHTML = "";
    const tbl = document.createElement("table");
    tbl.style.width = "100%";
    tbl.style.fontSize = "12px";
    tbl.style.borderCollapse = "collapse";
    const th = (s) => '<th style="text-align:left;padding:4px 8px;opacity:.7">' + esc(s) + "</th>";
    tbl.innerHTML = "<thead><tr>" + th(t("端口")) + th(t("进程")) + th(t("项目")) + th("") + "</tr></thead>";
    const tb = document.createElement("tbody");
    for (const p of ports) {
      const tr = document.createElement("tr");
      const td = (s) => '<td style="padding:4px 8px;border-top:1px solid var(--border)">' + s + "</td>";
      tr.innerHTML =
        td(p.port + (p.local_only ? ' <span class="pill">' + esc(t("仅本机")) + "</span>" : "") + (p.self ? ' <span class="pill">' + esc(t("本服务")) + "</span>" : "")) +
        td(esc((p.process || "?") + (p.pid ? " · pid " + p.pid : ""))) +
        td(p.project ? esc(p.project) : "-") +
        "<td></td>";
      const act = tr.lastElementChild;
      if (!p.self && p.pid) {
        const btn = document.createElement("button");
        btn.className = "ghost small";
        btn.textContent = t("关闭");
        btn.onclick = () => closePort(p.port);
        act.appendChild(btn);
      }
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    box.appendChild(tbl);
  } catch (e) {
    box.innerHTML = '<span class="hint">' + esc(t("扫描失败：") + (e.message || e)) + "</span>";
  } finally {
    if (b) b.disabled = false;
  }
}

async function closePort(port) {
  if (!confirm(t("结束占用端口 %1 的进程？先温和关闭，无效会自动强制结束（系统进程会被拒绝）").replace("%1", port))) return;
  try {
    const r = await api("/api/ports/close", { method: "POST", body: JSON.stringify({ port }) });
    toast(r.message || (r.ok ? t("已关闭") : t("未能关闭")), !r.ok);
    scanPorts();
  } catch (e) {
    toast(e.message || t("关闭失败"), true);
  }
}



async function suCheck() {
  const b = $("su-check");
  if (b) b.disabled = true;
  let s = null;
  try { s = await loadSelfupdate(true); }  // repo 模式秒回；npm 模式真查 registry
  finally { if (b) b.disabled = false; }
  if (!s) { toast(t("检查更新失败：服务未连接"), true); return; }
  if (s.has_update) toast(t("发现新版本 v") + s.latest + t("，点「升级到新版」即可"));
  else if (s.note) toast(t(s.note));
  else toast(t("已是最新版"));
}

function suWaitForAutoRestart() {
  if (SU_RESTART_TIMER) clearTimeout(SU_RESTART_TIMER);
  const prog = $("su-progress");
  if (prog) prog.textContent = t("服务重启中，页面会自动恢复…");
  let disconnected = false;
  let tries = 0;
  const probe = async () => {
    tries++;
    try {
      await api("/api/control", { timeout: 2500, busy: false, operation: false });
      if (disconnected) {
        SU_RESTART_TIMER = null;
        location.reload();
        return;
      }
    } catch (e) {
      disconnected = true;
    }
    if (tries >= 80) {
      SU_RESTART_TIMER = null;
      if (prog) prog.textContent = t("自动重启超时，请刷新页面");
      return;
    }
    SU_RESTART_TIMER = setTimeout(probe, 1500);
  };
  SU_RESTART_TIMER = setTimeout(probe, 1500);
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
        const d = await api("/api/runs/" + encodeURIComponent(runId));
        const run = d && d.run;   // /api/runs/<id> 返回 {run:{…}} 信封，别读外壳
        if (run && run.status && run.status !== "running" && run.status !== "queued") {
          clearInterval(SU_TIMER); SU_TIMER = null;
          $("su-apply").disabled = false;
          if (prog) prog.textContent = "";
          if (run.status === "done") {
            toast(t("升级完成，服务将自动重启并应用新版本"));
            suWaitForAutoRestart();
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
const TAB_TITLES = { overview: "概览", tasks: "任务", runs: "运行记录", automation: "自动化", zentao: "禅道 Bug 自动修复", __wxdigest: "群摘要", usage: "用量统计", agents: "本机智能体", models: "模型接入", bindings: "模型调度（可选）", skills: "经验库", knowledge: "知识库", market: "插件市场", orch: "编排设置", data: "数据与备份", appearance: "皮肤", about: "关于与更新" };
const SET_TABS = new Set(Object.keys(TAB_TITLES));   // 全部设置子页（__phone 是弹框，不算）

function tabTitle(name) {
  return t(TAB_TITLES[name]) || t("设置");
}

/* 顶栏标题 + 面包屑：页名永远落在 #page-title（多处测试断言它的 textContent
 * 精确等于页名，所以组名绝不能并进去），父级组名单独放 #crumb-root。
 * 组名从左栏设置导航现读——找选中项上方最近的分组标签，HTML 增删导航项时
 * 这里自动跟上，不需要维护一份硬编码映射。全页统一切标题只走这一个函数。 */
function renderPageCrumb() {
  const el = $("page-title");
  if (!el) return;
  const crumb = $("crumb-root");
  const inDetail = !!(S.detailRunId || S.detailTaskKey);   // 详情自成语境，不挂父级
  let group = "";
  if (!inDetail && document.body.classList.contains("settings-mode")) {
    const active = document.querySelector(".side-settings .set-item.active");
    let n = active ? active.previousElementSibling : null;
    while (n && !(n.classList && n.classList.contains("set-group"))) n = n.previousElementSibling;
    if (n) group = (n.textContent || "").trim();
  }
  if (crumb) {
    crumb.textContent = group;
    crumb.classList.toggle("hidden", !group);
  }
  el.textContent = inDetail ? t("运行详情") : tabTitle(S.tab);
}

/* 左栏两套导航各管哪些页，一律从 index.html 现读——HTML 改了 JS 自动跟上，
 * 不再各存一份常量、两处漂移：
 *   .side-quick    快捷导航（概览/运行记录/自动化）→ main 形态：只换主栏内容，左栏任务树不动
 *   .side-settings 设置导航                        → settings 形态：左栏整体换成设置导航
 * 同一个页只应挂在其中一边（重复挂就是两处入口）。 */
function mainPageSubs() {
  const s = new Set();
  document.querySelectorAll(".side-quick .qitem").forEach((b) => { if (b.dataset.page) s.add(b.dataset.page); });
  return s;
}
function settingsNavSubs() {
  const s = new Set();
  document.querySelectorAll(".side-settings .set-item").forEach((b) => { if (b.dataset.sub) s.add(b.dataset.sub); });
  return s;
}



/* ---------------------------------------------------------- 用量统计 */
/* 台账：/api/usage 按天/工具(CLI)/智能体/模型/角色/任务类型多维聚合；纯 SVG 画趋势 */

function fmtTok(n) {
  n = Number(n) || 0;
  // 中文按 万/亿；英文按 K/M/B 数量级（123456 万≠123.5M，须各走各的进率）
  if (getLang() === "en") {
    if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  } else {
    if (n >= 1e8) return (n / 1e8).toFixed(2) + t("亿");
    if (n >= 1e4) return (n / 1e4).toFixed(n >= 1e6 ? 0 : 1) + t("万");
  }
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
    const hint = '<p class="hint">' + t("加载失败：") + esc(e.message) + "</p>";
    kpis.innerHTML = hint;
    ["usage-trend", "usage-dims", "usage-recent", "usage-heat", "usage-models"].forEach((id) => {
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
  // 顶栏地球下拉的选中态（皮肤页分段与顶栏菜单两个入口共享同一真源）
  document.querySelectorAll("#lang-menu .lang-item").forEach((b) =>
    b.classList.toggle("on", b.dataset.lang === lang));
}

/* 顶栏语言快捷切换：地球按钮弹下拉，语言名用母语写死（见 index.html 注释） */
function toggleLangMenu(force) {
  const menu = $("lang-menu");
  if (!menu) return;
  const show = force != null ? !!force : menu.classList.contains("hidden");
  if (show) syncLangMenu();
  menu.classList.toggle("hidden", !show);
}

function syncLangMenu() {
  const lang = getLang();
  document.querySelectorAll("#lang-menu .lang-item").forEach((b) =>
    b.classList.toggle("on", b.dataset.lang === lang));
}

function pickLang(lang) {
  toggleLangMenu(false);
  setLangBtn(lang);
}
window.toggleLangMenu = toggleLangMenu;
window.pickLang = pickLang;

/* 顶栏大屏入口：新标签页打开任务驾驶舱；同源共享 orch.token，board 页免二次输令牌 */
function openBoard() {
  window.open("/board.html", "_blank", "noopener");
}
window.openBoard = openBoard;

function setLangBtn(lang) {
  if (getLang() === lang) return;
  setLang(lang);
  // 失效全部渲染签名，让下一次 render() 把所有动态内容重画一遍
  S.catSig = ""; S.modelsSig = ""; S.bindSig = ""; S.orchSig = ""; S.sideSig = ""; S.taskSig = ""; S.mgmt = {};
  syncLangMode();
  // 浏览器标签标题
  document.title = codebeeDocumentTitle(lang);
  applyI18n();
  render();
  cmpGreeting();   // 问候语跟随语言重算
  // 侧栏区头两颗钮的 title/aria 是 JS 按状态写的，applyI18n 会用静态词条盖掉，这里重刷
  paintArchToggle();
  syncSideExpandBtn();
  renderCodePreviews();   // 预览徽章是动态文案：语言切换时若停在皮肤页要跟着换
  // 数据与备份页的清理预估/状态是 JS 动态生成的：停在那一页时也要跟着换语言
  if (S.tab === "data") loadDataPage();
  // 帮助中心若开着：徽章/标题/目录/正文跟着换语言重画
  if (!$("welcome").classList.contains("hidden")) {
    paintHelpChrome();
    renderHelp();
  }
  // 重画还在缓存里的页面标题（语言切换后组名也要跟着换）
  renderPageCrumb();
  // 同步顶栏的连接状态文案（applyI18n 不会处理 textContent 写入的）
  const cn = $("conn");
  if (cn) {
    if (cn.classList.contains("ok")) cn.textContent = t("已连接");
    else if (cn.textContent === t("重连中…") || cn.textContent === "重连中…") cn.textContent = t("重连中…");
    else cn.textContent = t("连接失败");
  }
}

/* KPI 卡：图标块 + 环比徽章 + 数值 + 标签 + 副行 + 迷你走势图。
 * opts 全部可选；不传时回落成旧版布局（图标内联在标签行），老调用点无需改动。
 *   tone:  图标块/走势图色相（accent|ok|warn|bad|calm）
 *   delta: { pct:Number, invert?:true } 环比；invert 给花钱类指标（涨了显示告警色）
 *   spark: [Number,...] 迷你走势序列 */
function kpiCard(label, value, sub, wide, icon, opts) {
  opts = opts || {};
  const tone = opts.tone || "accent";
  const ico = icon ? '<span class="kpi-ico t-' + esc(tone) + '">' +
    '<svg class="ico" aria-hidden="true"><use href="#' + esc(icon) + '"></use></svg></span>' : "";
  const delta = kpiDelta(opts.delta);
  const top = (ico || delta) ? '<div class="kpi-top">' + ico + delta + "</div>" : "";
  const spark = (opts.spark && opts.spark.length > 1)
    ? '<div class="kpi-spark">' + kpiSparkSvg(opts.spark, tone) + "</div>" : "";
  return '<div class="kpi' + (wide ? " wide" : "") + (top ? " has-top" : "") +
    (spark ? " has-spark" : "") + '">' + top +
    '<div class="kpi-v">' + value + "</div>" +
    '<div class="kpi-l">' + (top ? "" : ico) + esc(label) + "</div>" +
    (sub ? '<div class="kpi-s">' + esc(sub) + "</div>" : "") + spark + "</div>";
}

/* 环比徽章：涨跌用颜色区分，不用箭头字符（几何字形跨平台宽度不一）。 
 * 花钱类指标要 invert——费用涨 20% 不是好事，不能标绿。 */
function kpiDelta(d) {
  if (!d || typeof d.pct !== "number" || !isFinite(d.pct)) return "";
  const pct = d.pct;
  const flat = Math.abs(pct) < 0.05;
  const dir = flat ? "flat" : (pct > 0 ? "up" : "down");
  const happy = d.invert ? (dir === "down") : (dir === "up");
  const cls = flat ? "flat" : (happy ? "good" : "bad");
  const sign = pct > 0 ? "+" : "";
  return '<span class="kpi-delta ' + cls + '" title="' +
    esc(t("与上一个同长周期相比")) + '">' + esc(t("较上期")) + " " +
    esc(sign + pct.toFixed(1) + "%") + "</span>";
}

/* 迷你走势图：折线 + 面积。preserveAspectRatio=none 让图随卡宽拉伸，
 * 折线靠 vector-effect 保住描边不被横向拉粗。 */
function kpiSparkSvg(vals, tone) {
  const w = 88, h = 26, n = vals.length;
  const max = Math.max.apply(null, vals), min = Math.min.apply(null, vals);
  const span = Math.max(1, max - min);
  const px = (i) => (n <= 1 ? w : i * w / (n - 1));
  const py = (v) => h - 3 - (v - min) * (h - 8) / span;
  const line = vals.map((v, i) => px(i).toFixed(1) + "," + py(v).toFixed(1)).join(" ");
  const area = "0," + h + " " + line + " " + w + "," + h;
  return '<svg class="kspark t-' + esc(tone) + '" viewBox="0 0 ' + w + " " + h +
    '" preserveAspectRatio="none" aria-hidden="true" focusable="false">' +
    '<polygon class="ks-area" points="' + area + '"></polygon>' +
    '<polyline class="ks-line" points="' + line +
    '" vector-effect="non-scaling-stroke"></polyline></svg>';
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
  if (tok <= 0) return head + '<div class="us-bar us-empty"><span>' + t("当天暂无用量") + '</span></div>';
  const seg = (cls, val, label) => {
    const pct = (val || 0) * 100 / tok;
    if (pct <= 0) return "";
    // 太窄的段不放内嵌文字（挤成一团），数值交给底部明细行
    const inner = pct >= 10 ? "<span>" + label + " " + Math.round(pct) + "%</span>" : "";
    return '<div class="us-seg us-' + cls + '" style="width:' + pct.toFixed(2) + '%"' +
      ' title="' + label + " " + fmtTok(val || 0) + t("（") + pct.toFixed(1) + t("%）") + '">' + inner + "</div>";
  };
  const other = Math.max(0, tok - (d.input || 0) - (d.output || 0));
  return head +
    '<div class="us-bar">' +
    seg("in", d.input, t("输入")) +
    seg("ca", other, t("缓存/其他")) +
    seg("out", d.output, t("输出")) +
    "</div>" +
    '<div class="us-foot">' + t("输入 ") + esc(fmtTok(d.input || 0)) + t(" · 缓存/其他 ") + esc(fmtTok(other)) +
    t(" · 输出 ") + esc(fmtTok(d.output || 0)) + "</div>";
}

/* 折线/甜甜圈共用色板：主题变量 --uch-1..6（明暗皮肤各配亮暗两套） */
function usageColor(i) {
  return "var(--uch-" + ((i % 6) + 1) + ")";
}

/* Catmull-Rom → 三次贝塞尔：数据点间的平滑曲线（轻微过冲，控制点纵向夹住） */
function _smoothPath(pts, yMin, yMax) {
  if (pts.length < 2) return "";
  const cl = (y) => Math.min(yMax, Math.max(yMin, y));
  let d = "M" + pts[0][0].toFixed(1) + " " + pts[0][1].toFixed(1);
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = cl(p1[1] + (p2[1] - p0[1]) / 6);
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = cl(p2[1] - (p3[1] - p1[1]) / 6);
    d += " C" + c1x.toFixed(1) + " " + c1y.toFixed(1) + " " + c2x.toFixed(1) + " " + c2y.toFixed(1) +
      " " + p2[0].toFixed(1) + " " + p2[1].toFixed(1);
  }
  return d;
}

/* 多模型平滑折线（≥2 天）：每个模型一条曲线（Top5 + 其他），点带明细悬浮；
 * 台账无模型细分（历史回填）时兜底画一条总量线。 */
function usageMultilineSvg(byDay, byDayModel) {
  const days = (byDay || []).map((d) => d.day);
  const n = days.length;
  if (n < 2) return '<p class="hint">' + t("（暂无数据）") + '</p>';
  const modelOfDay = {};
  (byDayModel || []).forEach((d) => { modelOfDay[d.day] = d.models || {}; });
  const dayModels = days.map((day) => modelOfDay[day] || {});
  const totals = {};
  dayModels.forEach((m) => Object.entries(m).forEach(([k, v]) => { totals[k] = (totals[k] || 0) + (v || 0); }));
  const names = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
  const top = names.slice(0, 5);
  const rest = names.slice(5);
  const series = top.map((m) => ({ name: m, vals: dayModels.map((mm) => mm[m] || 0) }));
  if (rest.length) {
    series.push({ name: t("其他"), vals: dayModels.map((mm) => rest.reduce((s, m) => s + (mm[m] || 0), 0)) });
  }
  if (!series.length) series.push({ name: t("总量"), vals: (byDay || []).map((d) => d.tokens || 0) });

  const max = Math.max(1, ...series.flatMap((s) => s.vals));
  const W = 760, H = 250, padT = 26, padB = 26, padL = 10, padR = 10;
  const iw = W - padL - padR, ih = H - padT - padB;
  const xOf = (i) => padL + (iw * i) / (n - 1);
  const yOf = (v) => padT + ih - (ih * v) / max;

  let body = "", labels = "";
  const labelStep = Math.max(1, Math.ceil(n / 9));
  const labeled = new Set();
  for (let i = 0; i < n; i += labelStep) labeled.add(i);
  labeled.add(n - 1);   // 末日必须标
  days.forEach((day, i) => {
    if (!labeled.has(i)) return;
    const cx = Math.min(Math.max(xOf(i), padL + 20), W - padR - 20);
    labels += '<text class="uc-x" x="' + cx.toFixed(1) + '" y="' + (H - 7) +
      '" text-anchor="middle">' + esc(String(day).slice(5)) + "</text>";
  });
  series.forEach((s, si) => {
    const col = usageColor(si);
    const pts = s.vals.map((v, i) => [xOf(i), yOf(v)]);
    body += '<path d="' + _smoothPath(pts, padT - 6, padT + ih + 6) +
      '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linecap="round"/>';
    pts.forEach((pt, i) => {
      body += '<circle cx="' + pt[0].toFixed(1) + '" cy="' + pt[1].toFixed(1) + '" r="2.7" fill="' + col + '">' +
        "<title>" + esc(days[i] + "\n" + s.name + " " + fmtTok(s.vals[i]) + t(" tokens")) + "</title></circle>";
    });
  });
  const grid = [0.25, 0.5, 0.75].map((f) =>
    '<line class="uc-grid" x1="' + padL + '" y1="' + (padT + ih * f).toFixed(1) +
    '" x2="' + (W - padR) + '" y2="' + (padT + ih * f).toFixed(1) + '"/>').join("") +
    '<line class="uc-base" x1="' + padL + '" y1="' + (padT + ih) + '" x2="' + (W - padR) + '" y2="' + (padT + ih) + '"/>';
  // 底层活动量柱保留每日总量轮廓；折线仍表达各模型趋势，双层信息比单一曲线
  // 更容易看出“哪天整体有调用、哪天只是某个模型占比变化”。
  const dailyTotals = days.map((_, i) => series.reduce((sum, s) => sum + (s.vals[i] || 0), 0));
  const dayMax = Math.max(1, ...dailyTotals);
  const barW = Math.max(3, (iw / n) * 0.52);
  const bars = dailyTotals.map((v, i) => {
    const h = ih * v / dayMax;
    const x = xOf(i) - barW / 2;
    const y = padT + ih - h;
    return '<rect class="uc-bar" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
      '" width="' + barW.toFixed(1) + '" height="' + Math.max(0.5, h).toFixed(1) +
      '" rx="2"><title>' + esc(days[i] + " · " + fmtTok(v) + t(" tokens")) + "</title></rect>";
  }).join("");
  const legend = '<div class="um-legend">' +
    series.map((s, si) =>
      '<span><i style="background:' + usageColor(si) + '"></i>' +
      esc(s.name.length > 22 ? s.name.slice(0, 21) + "…" : s.name) + "</span>").join("") +
    '<span class="um-peak">' + esc(t("峰值 ") + fmtTok(max)) + "</span></div>";
  return legend +
    '<svg class="usage-svg" viewBox="0 0 ' + W + " " + H + '" role="img" preserveAspectRatio="xMidYMid meet">' +
    grid + bars + body + labels + "</svg>";
}

/* Token 活动热力图：GitHub 贡献图风格。窗口自适应——覆盖全部活跃历史（封顶
 * 53 周、不足 8 周也补足 8 周），新装用户不会再拖出十个月的灰格子。
 * 每日=7 行×周列；每周=每周一格；累计=按月一格。 */
const USAGE_HEAT_KEY = "orch.usageHeatMode";

function usageHeatMode() {
  if (S.usageHeatMode === undefined || S.usageHeatMode === null || S.usageHeatMode === "") {
    try { S.usageHeatMode = localStorage.getItem(USAGE_HEAT_KEY) || "day"; } catch (e) { S.usageHeatMode = "day"; }
  }
  return ["day", "week", "total"].includes(S.usageHeatMode) ? S.usageHeatMode : "day";
}

function setUsageHeatMode(mode) {
  S.usageHeatMode = mode;
  try { localStorage.setItem(USAGE_HEAT_KEY, mode); } catch (e) { /* 隐私模式忽略 */ }
  document.querySelectorAll("#usage-heat-modes [data-heat]").forEach((b) =>
    b.classList.toggle("active", b.dataset.heat === mode));
  renderUsageHeat();
}

function renderUsageHeat() {
  const el = $("usage-heat");
  if (!el) return;
  const all = (S.usage && S.usage.all_by_day) || [];
  if (!all.length) { el.innerHTML = '<p class="hint">' + t("（暂无数据）") + "</p>"; return; }
  el.innerHTML = usageHeatSvg(all, usageHeatMode());
}

function usageHeatSvg(all, mode) {
  const p2 = (n) => String(n).padStart(2, "0");
  const iso = (dt) => dt.getFullYear() + "-" + p2(dt.getMonth() + 1) + "-" + p2(dt.getDate());
  const map = {};
  all.forEach((d) => { map[d.day] = d.tokens || 0; });
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const firstActive = new Date(all[0].day + "T00:00:00");
  if (isNaN(firstActive.getTime())) return '<p class="hint">' + t("（暂无数据）") + "</p>";
  // 窗口：最早活跃日与 53 周前取较早者，不足 8 周补足 8 周，对齐周一
  let start = new Date(Math.min(firstActive.getTime(), today.getTime() - 364 * 864e5));
  const minStart = new Date(today.getTime() - 55 * 864e5);
  if (start > minStart) start = minStart;
  start = new Date(start.getTime() - ((start.getDay() + 6) % 7) * 864e5);
  const totalDays = Math.round((today - start) / 864e5) + 1;
  const weeks = Math.ceil(totalDays / 7);

  // 中文 M月D日；英文走本地化短日期（9m5d 不成词）
  const MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtD = (dt) => getLang() === "en"
    ? MON_EN[dt.getMonth()] + " " + dt.getDate()
    : (dt.getMonth() + 1) + t("月") + dt.getDate() + t("日");
  const CELL = 12, GAP = 3, PITCH = CELL + GAP, TOP = 18;
  let cells = "", labelEls = "";
  const clsOf = (v, maxV) => v <= 0 ? "uh-h0" : v < maxV * 0.25 ? "uh-h1" : v < maxV * 0.5 ? "uh-h2" : v < maxV * 0.75 ? "uh-h3" : "uh-h4";

  if (mode === "day" || mode === "week") {
    const perWeek = [];
    for (let w = 0; w < weeks; w++) {
      let sum = 0, first = null, last = null;
      for (let k = 0; k < 7; k++) {
        const dt = new Date(start.getTime() + (w * 7 + k) * 864e5);
        if (dt > today) break;
        const v = map[iso(dt)] || 0;
        sum += v;
        if (!first) first = dt;
        last = dt;
      }
      perWeek.push({ sum, first, last });
    }
    const maxW = Math.max(0, ...perWeek.map((w) => w.sum));
    if (mode === "day") {
      for (let w = 0; w < weeks; w++) {
        for (let k = 0; k < 7; k++) {
          const dt = new Date(start.getTime() + (w * 7 + k) * 864e5);
          if (dt > today) break;
          const v = map[iso(dt)] || 0;
          cells += '<rect class="' + clsOf(v, maxW) + '" x="' + (w * PITCH) + '" y="' + (TOP + k * PITCH) +
            '" width="' + CELL + '" height="' + CELL + '" rx="2.5"><title>' +
            esc(fmtD(dt) + " · " + fmtTok(v) + t(" tokens")) + "</title></rect>";
        }
      }
    } else {
      perWeek.forEach((w, i) => {
        if (!w.first) return;
        cells += '<rect class="' + clsOf(w.sum, maxW) + '" x="' + (i * PITCH) + '" y="' + TOP +
          '" width="' + CELL + '" height="' + CELL + '" rx="2.5"><title>' +
          esc(fmtD(w.first) + (w.last && w.last !== w.first ? " ~ " + fmtD(w.last) : "") +
            " · " + fmtTok(w.sum) + t(" tokens")) + "</title></rect>";
      });
    }
    // 月份标签：每月第一次出现的列
    let lastMon = -1;
    for (let w = 0; w < weeks; w++) {
      const dt = new Date(start.getTime() + w * 7 * 864e5);
      if (dt > today) break;
      if (dt.getMonth() !== lastMon) {
        lastMon = dt.getMonth();
        labelEls += '<text class="uh-mon" x="' + (w * PITCH) + '" y="11">' + esc(getLang() === "en" ? MON_EN[dt.getMonth()] : (dt.getMonth() + 1) + t("月")) + "</text>";
      }
    }
    const H = TOP + 7 * PITCH + 4;
    return '<svg class="usage-heat-svg' + (mode === "week" ? " single-row" : "") +
      '" viewBox="0 0 ' + (weeks * PITCH + 4) + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img">' +
      labelEls + cells + "</svg>";
  }

  // 累计：按月一格（宽格 + 月份标签），值=当月合计
  const perMonth = [];
  let cur = null;
  for (let dt = new Date(start); dt <= today; dt = new Date(dt.getTime() + 864e5)) {
    const key = dt.getFullYear() + "-" + p2(dt.getMonth() + 1);
    if (!cur || cur.key !== key) {
      cur = { key, label: (dt.getFullYear() % 100) + "." + p2(dt.getMonth() + 1), sum: 0 };
      perMonth.push(cur);
    }
    cur.sum += map[iso(dt)] || 0;
  }
  const maxM = Math.max(0, ...perMonth.map((m) => m.sum));
  const MW = 34, MH = 18, MP = 42;
  perMonth.forEach((m, i) => {
    cells += '<rect class="' + clsOf(m.sum, maxM) + '" x="' + (i * MP) + '" y="' + TOP + '" width="' + MW + '" height="' + MH +
      '" rx="3"><title>' + esc((getLang() === "en"
        ? MON_EN[Number(m.label.split(".")[1]) - 1] + " 20" + m.label.split(".")[0]
        : m.label.replace(".", t("年")) + t("月")) + " · " + fmtTok(m.sum) + t(" tokens")) + "</title></rect>";
    labelEls += '<text class="uh-mon" x="' + (i * MP + MW / 2) + '" y="' + (TOP + MH + 13) +
      '" text-anchor="middle">' + esc(m.label) + "</text>";
  });
  return '<svg class="usage-heat-svg" viewBox="0 0 ' + (perMonth.length * MP + 4) + " " + (TOP + MH + 22) +
    '" preserveAspectRatio="xMidYMid meet" role="img">' + labelEls + cells + "</svg>";
}

/* 模型用量甜甜圈：Top5 + 其他；左环（中心总量）右列表（名称/百分比/tokens） */
function renderUsageModels() {
  const el = $("usage-models");
  if (!el) return;
  const rows = (S.usage && S.usage.by_model) || [];
  if (!rows.length) { el.innerHTML = '<p class="hint">' + t("（该维度暂无数据）") + "</p>"; return; }
  const total = rows.reduce((s, r) => s + (r.tokens || 0), 0);
  const top = rows.slice(0, 5);
  const rest = rows.slice(5);
  const segs = top.map((r) => ({ name: r.key, tokens: r.tokens || 0 }));
  if (rest.length) segs.push({ name: t("其他模型"), tokens: rest.reduce((s, r) => s + (r.tokens || 0), 0) });

  const R = 64, C = 2 * Math.PI * R;
  let off = 0, arcs = "";
  segs.forEach((s, i) => {
    const frac = total > 0 ? s.tokens / total : 0;
    if (frac <= 0) return;
    const len = frac * C;
    arcs += '<circle class="ud-seg" cx="90" cy="90" r="' + R + '" fill="none" stroke="' + usageColor(i) +
      '" stroke-width="26" stroke-dasharray="' + Math.max(len - 1.6, 0.6).toFixed(2) + " " + C.toFixed(2) +
      '" stroke-dashoffset="' + (-off).toFixed(2) + '" transform="rotate(-90 90 90)"><title>' +
      esc(s.name + " · " + fmtTok(s.tokens) + t(" tokens（") + (frac * 100).toFixed(1) + t("%）")) + "</title></circle>";
    off += len;
  });
  const svg = '<svg class="ud-ring" viewBox="0 0 180 180" role="img">' + arcs +
    '<text class="ud-total" x="90" y="88" text-anchor="middle">' + esc(fmtTok(total)) + "</text>" +
    '<text class="ud-unit" x="90" y="107" text-anchor="middle">' + esc(t("tokens")) + "</text></svg>";
  const list = segs.map((s, i) => {
    const pct = total > 0 ? (s.tokens * 100 / total) : 0;
    return '<div class="ud-row"><span class="ud-dot" style="background:' + usageColor(i) + '"></span>' +
      '<div class="ud-name">' + esc(t(s.name)) + "<small>" + esc(fmtTok(s.tokens) + t(" tokens")) + "</small></div>" +
      '<span class="ud-pct">' + (pct >= 10 ? Math.round(pct) : pct.toFixed(1)) + "%</span></div>";
  }).join("");
  el.innerHTML = '<div class="usage-donut">' + svg + '<div class="ud-list">' + list + "</div></div>";
}

function usageTrendSvg(byDay) {
  if (!byDay || !byDay.length) return '<p class="hint">' + t("（暂无数据）") + '</p>';
  if (byDay.length === 1) return '<div class="usage-single">' + usageSingleDay(byDay[0]) + "</div>";
  return usageMultilineSvg(byDay, (S.usage && S.usage.by_day_model) || []);
}

/* 维度排行表：首列名称带相对占比条 */
function usageDimTable(title, rows) {
  let body;
  if (!rows || !rows.length) {
    body = '<p class="hint">' + t("（该维度暂无数据）") + '</p>';
  } else {
    const maxTok = Math.max(1, ...rows.map((r) => r.tokens || 0));
    body = '<table class="usage-table"><thead><tr>' +
      "<th>" + esc(title) + '</th><th class="num">' + t("调用") + '</th><th class="num">Tokens</th>' +
      '<th class="num">' + t("输入/输出") + '</th><th class="num">' + t("耗时") + '</th><th class="num">' + t("费用") + '</th>' +
      "</tr></thead><tbody>" + rows.map((r) => {
        const pct = Math.max(4, Math.round((r.tokens || 0) * 100 / maxTok));
        const okPct = r.calls ? Math.round((r.ok || 0) * 100 / r.calls) : 0;
        return "<tr>" +
          '<td class="bar-cell"><div class="bar-outer">' +
          '<div class="bar-fill" style="width:' + pct + '%"></div>' +
          "<span>" + esc(r.key) + "</span><i>" + okPct + t("% 成") + "</i></div></td>" +
          '<td class="num">' + fmtTok(r.calls) + "</td>" +
          '<td class="num" title="' + t("输入 ") + fmtTok(r.input) + t(" · 输出 ") + fmtTok(r.output) + '">' + fmtTok(r.tokens) + "</td>" +
          '<td class="num sub">' + fmtTok(r.input) + " / " + fmtTok(r.output) + "</td>" +
          '<td class="num sub">' + fmtDur(r.duration_s) + "</td>" +
          '<td class="num">' + fmtUsd(r.cost_usd) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }
  return '<h3 class="sec-title">' + esc(title) + "</h3>" + body;
}

function usageRecentTable(rows) {
  if (!rows || !rows.length) return '<p class="hint">' + t("（暂无数据）") + '</p>';
  return '<table class="usage-table recent"><thead><tr>' +
    '<th class="num">' + t("时间") + '</th><th>' + t("工具") + '</th><th>' + t("智能体") + '</th><th>' + t("模型") + '</th><th>' + t("角色") + '</th><th>' + t("状态") + '</th>' +
    '<th class="num">Tokens</th><th class="num">' + t("费用") + '</th>' +
    "</tr></thead><tbody>" + rows.map((r) => {
      const det = t("输入 ") + fmtTok(r.input) + t(" · 缓存 ") + fmtTok(r.cached) + t(" · 输出 ") + fmtTok(r.output);
      const model = String(r.model || "");
      return "<tr" + (r.ok ? "" : ' class="bad-row"') + ">" +
        '<td class="num sub">' + esc(String(r.ts || "").slice(5)) + "</td>" +
        "<td>" + esc(toolName(r.tool)) + "</td>" +
        "<td>" + esc(t(r.agent_label || r.agent || "")) + "</td>" +
        '<td class="mono" title="' + esc(det) + '">' + esc(t(model.length > 30 ? model.slice(0, 29) + "…" : model)) + "</td>" +
        '<td class="sub">' + esc(roleName(r.role)) + "</td>" +
        "<td>" + (r.ok ? '<span class="chip done">OK</span>' : '<span class="chip failed">' + t("失败") + '</span>') + "</td>" +
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
  // 迷你走势取范围内逐日真值（by_day 已连续补零）；单日范围只有一格，kpiCard 自动不出图。
  // 用量页不做环比：范围由用户任选（含「全部」），没有等长上一期可比，硬算会是假数。
  const dayTok = (u.by_day || []).map((d) => d.tokens || 0);
  const dayDur = (u.by_day || []).map((d) => d.duration_s || 0);
  // 首字延迟只有内置直连的流式调用可测（CLI 事件流没有逐 token 时刻）：
  // perf_samples=0 时显示"暂无可测数据"，不能显示成 0ms。
  const fmtMs = (x) => (x >= 1000 ? (x / 1000).toFixed(1) + "s" : Math.round(x) + "ms");
  $("usage-kpis").innerHTML = [
    kpiCard(t("累计 Token 数"), fmtTok(tot.tokens),
      t("调用 ") + fmtTok(tot.calls) + t(" 次 · 成功率 ") +
      (tot.calls ? Math.round(tot.ok * 100 / tot.calls) : 0) + "% · " +
      t("失败 ") + fmtTok(tot.failed || 0) + t(" · ") + fmtUsd(tot.cost_usd),
      true, "i-sigma", { tone: "accent", spark: dayTok }),
    kpiCard(t("峰值 Token 数"), fmtTok(tot.peak_tokens),
      t("单日最高 · 日均 ") + fmtTok(activeDays ? tot.tokens / activeDays : 0),
      false, "i-gauge", { tone: "warn" }),
    kpiCard(t("最长单次时长"), fmtDur(tot.max_duration_s),
      t("累计调用 ") + fmtDur(tot.duration_s), false, "i-history",
      { tone: "calm", spark: dayDur }),
    kpiCard(t("当前连续天数"), fmtTok(tot.streak_current) + t(" 天"),
      t("范围内活跃 ") + fmtTok(activeDays) + t(" 天"), false, "i-calendar-days",
      { tone: "ok" }),
    kpiCard(t("最长连续天数"), fmtTok(tot.streak_longest) + t(" 天"),
      t("缓存命中率 ") + (tot.cache_rate || 0) + "%", false, "i-calendar-days",
      { tone: "ok" }),
    kpiCard(t("首字延迟"), tot.perf_samples ? fmtMs(tot.avg_first_token_ms) : t("暂无可测数据"),
      tot.perf_samples
        ? t("P95 ") + fmtMs(tot.p95_first_token_ms) + t(" · 吞吐 ") +
          (tot.avg_tokens_per_sec || 0) + t(" tok/s · 样本 ") + fmtTok(tot.perf_samples) + t(" 次")
        : t("仅内置直连流式调用可测"), false, "i-cpu", { tone: "calm" }),
  ].join("");
  const note = $("usage-note");
  if (note) {
    note.innerHTML = noBreakdown
      ? '<p class="hint">' + t("当前范围内都是历史运行回填的记录：只保留总量与费用，") +
        t("输入/输出/缓存细分从新调用开始记录。") + "</p>"
      : "";
  }
  $("usage-trend").innerHTML = usageTrendSvg(u.by_day || []);
  renderUsageHeat();
  renderUsageModels();
  $("usage-dims").innerHTML = [
    [t("按工具（CLI / API）"), (u.by_tool || []).map((r) => Object.assign({}, r, { key: toolName(r.key) }))],
    [t("按智能体"), u.by_agent],
    [t("按模型"), u.by_model],
    [t("按步骤角色"), (u.by_role || []).map((r) => Object.assign({}, r, { key: roleName(r.key) }))],
    [t("按任务类型"), u.by_task_type],
  ].map(([title, rows]) => usageDimTable(title, rows)).join("");
  $("usage-recent").innerHTML = usageRecentTable(u.recent || []);
}



/* ---------------------------------------------------------- 概览首页 */
/* 数据全部来自真实台账与运行记录，没有任何占位假数：
 *   - /api/usage?days=60 一次拿两期：by_day 是连续补零的日序列，前 30 天=上期、
 *     后 30 天=本期，环比在本地对半切算出来，不额外发请求、不加后端端点；
 *   - 最近运行取 S.state.runs（SSE 推送，天然实时，不需要自己轮询）。
 * 周期固定 60 天而不是可调：环比必须有等长的上一期，做成可选范围就得再拉一次。 */
const OV_PERIOD = 30;               // 一期长度（上期 / 本期各 30 天）
const OV_DAYS_KEY = "orch.ovDays";  // 趋势图范围（7 / 30），与用量页的范围各自独立
let _ovSeq = 0;                     // 请求序号：重入/切走时丢弃过期响应

function ovDays() {
  if (S.ovDays === undefined || S.ovDays === null || S.ovDays === "") {
    let saved = null;
    try { saved = localStorage.getItem(OV_DAYS_KEY); } catch (e) { /* 隐私模式忽略 */ }
    S.ovDays = Number(saved) === 30 ? 30 : 7;
  }
  return S.ovDays === 30 ? 30 : 7;
}

function setOvDays(n) {
  S.ovDays = Number(n) === 30 ? 30 : 7;
  try { localStorage.setItem(OV_DAYS_KEY, String(S.ovDays)); } catch (e) { /* 隐私模式忽略 */ }
  syncOvRanges();
  renderOverviewTrend();
}

function syncOvRanges() {
  const cur = ovDays();
  document.querySelectorAll("#ov-ranges [data-days]").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.days) === cur));
}

async function loadOverview() {
  const seq = ++_ovSeq;
  const kp = $("ov-kpis");
  if (kp) kp.innerHTML = '<p class="hint">' + t("加载中…") + "</p>";
  try {
    const u = await api("/api/usage?days=" + (OV_PERIOD * 2));
    if (seq !== _ovSeq) return;      // 期间已切走/重入：过期响应不落盘
    S.ovUsage = u;
  } catch (e) {
    if (seq !== _ovSeq) return;
    S.ovUsage = null;
  }
  renderOverview();
}

/* 日序列对半切：前一半=上期，后一半=本期。服务刚装不久、总天数不足两期时
 * 上期为空数组，环比自然不显示（而不是拿不足一期的数据硬算出一个假百分比）。 */
function ovSplit(byDay) {
  const days = byDay || [];
  const cut = Math.max(0, days.length - OV_PERIOD);
  return { prev: days.slice(0, cut), cur: days.slice(cut) };
}

function ovSum(rows, k) {
  return (rows || []).reduce((s, r) => s + (Number(r[k]) || 0), 0);
}

/* 环比：上期为 0 时不显示——除零得到 Infinity，硬算出来是假数 */
function ovDelta(cur, prev, invert) {
  if (!prev) return null;
  return { pct: (cur - prev) * 100 / prev, invert: !!invert };
}

/* 日期眉标：跟随界面语言排版（en 走 en-US），拿不到就回落空串不阻塞渲染 */
function ovDateText() {
  try {
    return new Date().toLocaleDateString(getLang() === "en" ? "en-US" : "zh-CN",
      { weekday: "long", month: "long", day: "numeric" });
  } catch (e) { return ""; }
}

function renderOverview() {
  ovRenderHead();
  ovRenderKpis();
  syncOvRanges();
  renderOverviewTrend();
  ovRenderQuick();
  ovRenderRecent();
}

function ovRenderHead() {
  const dateEl = $("ov-date");
  if (dateEl) dateEl.textContent = ovDateText();
  const sub = $("ov-sub");
  if (!sub) return;
  const u = S.ovUsage;
  if (!u || !u.totals) { sub.textContent = ""; return; }
  const cur = ovSplit(u.by_day).cur;
  const calls = ovSum(cur, "calls"), ok = ovSum(cur, "ok");
  const rate = calls ? Math.round(ok * 100 / calls) : 0;
  let s = t("近 30 天执行 ") + fmtTok(calls) + t(" 次 · 成功率 ") + rate + "%" +
    t(" · 消耗 ") + fmtTok(ovSum(cur, "tokens")) + t(" tokens · 费用 ") + fmtUsd(ovSum(cur, "cost_usd"));
  const running = ((S.state && S.state.runs) || []).filter((r) => r.status === "running").length;
  if (running) s += t(" · 当前 ") + running + t(" 个任务运行中");
  sub.textContent = s;
}

function ovRenderKpis() {
  const box = $("ov-kpis");
  if (!box) return;
  const u = S.ovUsage;
  if (!u || !u.totals) {
    box.innerHTML = '<p class="hint">' + t("暂无用量记录。跑一个任务后这里会出现统计。") + "</p>";
    return;
  }
  const { prev, cur } = ovSplit(u.by_day);
  const C = { calls: ovSum(cur, "calls"), ok: ovSum(cur, "ok"),
    tok: ovSum(cur, "tokens"), cost: ovSum(cur, "cost_usd") };
  const P = { calls: ovSum(prev, "calls"), ok: ovSum(prev, "ok"),
    tok: ovSum(prev, "tokens"), cost: ovSum(prev, "cost_usd") };
  const cRate = C.calls ? C.ok * 100 / C.calls : 0;
  const pRate = P.calls ? P.ok * 100 / P.calls : 0;
  // 两个「日均」按真实活跃天数算：刚装不久只跑了 3 天，除以 30 会得到一个
  // 看着像「用量很低」的假平均值
  const activeDays = Math.max(1, cur.filter((d) => (d.calls || 0) > 0).length);
  box.innerHTML = [
    kpiCard(t("执行次数"), fmtTok(C.calls),
      t("成功 ") + fmtTok(C.ok) + t(" · 失败 ") + fmtTok(Math.max(0, C.calls - C.ok)),
      false, "i-tasks",
      { tone: "accent", delta: ovDelta(C.calls, P.calls),
        spark: cur.map((d) => d.calls || 0) }),
    // 成功率环比给「百分点差」：98.7% 比 96.3% 高 2.4 个点，写成 (+2.4/96.3)=+2.5%
    // 反而绕；kpiDelta 输出的 +X.X% 在这里就是百分点，读起来是对的
    kpiCard(t("成功率"), C.calls ? cRate.toFixed(1) + "%" : "—",
      t("上期 ") + (P.calls ? pRate.toFixed(1) + "%" : "—"),
      false, "i-clip-check",
      { tone: "ok", delta: (P.calls && C.calls) ? { pct: cRate - pRate } : null,
        spark: cur.map((d) => ((d.calls || 0) ? (d.ok || 0) * 100 / d.calls : 0)) }),
    kpiCard(t("Token 消耗"), fmtTok(C.tok),
      t("日均 ") + fmtTok(C.tok / activeDays),
      false, "i-sigma",
      { tone: "calm", delta: ovDelta(C.tok, P.tok),
        spark: cur.map((d) => d.tokens || 0) }),
    kpiCard(t("费用"), fmtUsd(C.cost),
      t("日均 ") + fmtUsd(C.cost / activeDays),
      false, "i-coin",
      { tone: "warn", delta: ovDelta(C.cost, P.cost, true),
        spark: cur.map((d) => d.cost_usd || 0) }),
  ].join("");
}

/* 趋势复用用量页同一套折线（Top5 模型 + 其他），只是把日序列截到所选范围 */
function renderOverviewTrend() {
  const box = $("ov-trend-body");
  if (!box) return;
  const u = S.ovUsage;
  if (!u || !u.by_day || !u.by_day.length) {
    box.innerHTML = '<p class="hint">' + t("（暂无数据）") + "</p>";
    return;
  }
  const byDay = u.by_day.slice(-ovDays());
  const modelOfDay = {};
  (u.by_day_model || []).forEach((d) => { modelOfDay[d.day] = d.models || {}; });
  box.innerHTML = usageMultilineSvg(byDay,
    byDay.map((d) => ({ day: d.day, models: modelOfDay[d.day] || {} })));
}

function ovRenderQuick() {
  const box = $("ov-quick-body");
  if (!box) return;
  const items = [
    { key: "tasks", icon: "i-plus", label: t("新建任务"), sub: t("描述目标，交给智能体执行") },
    { key: "knowledge", icon: "i-sigma", label: t("知识库"), sub: t("项目资料与沉淀") },
    { key: "market", icon: "i-blocks", label: t("插件市场"), sub: t("装技能扩展能力") },
    { key: "automation", icon: "i-calendar-days", label: t("自动化"), sub: t("定时与周期执行") },
  ];
  box.innerHTML = items.map((it) =>
    '<button class="ov-q" type="button" onclick="switchTab(\'' + it.key + '\')">' +
    '<span class="ov-q-ico"><svg class="ico" aria-hidden="true"><use href="#' + it.icon + '"></use></svg></span>' +
    '<span class="ov-q-text"><b>' + esc(it.label) + "</b><i>" + esc(it.sub) + "</i></span>" +
    '<span class="ov-q-go" aria-hidden="true"><svg class="ico" aria-hidden="true"><use href="#i-arrow-up"></use></svg></span>' +
    "</button>").join("");
}

/* 最近运行：状态点复用侧栏那套字形（staskGlyph），行样式照参考站的
 * 「图标 + 主副标题 + 右侧状态与相对时间」。点击走 jumpToRun——
 * 它会先切到运行记录页再开详情；在概览页直接 openRun 会把详情开在错的分区上。 */
function ovRenderRecent() {
  const box = $("ov-recent-body");
  if (!box) return;
  const runs = ((S.state && S.state.runs) || []).slice(0, 5);
  if (!runs.length) { box.innerHTML = '<div class="empty">' + t("暂无运行记录") + "</div>"; return; }
  box.innerHTML = runs.map((r) => {
    const st = String(r.status || "");
    const sub = String(r.summary || r.error || "").trim();
    return '<button class="ov-run" type="button" onclick="jumpToRun(\'' + esc(r.id) + '\')">' +
      '<span class="ov-run-ico">' + staskGlyph(st) + "</span>" +
      '<span class="ov-run-main"><b>' + esc(r.title || "") + "</b>" +
      (sub ? "<i>" + esc(sub.length > 90 ? sub.slice(0, 89) + "…" : sub) + "</i>" : "") +
      "</span>" +
      '<span class="ov-run-meta">' +
      '<span class="chip ' + esc(st) + '">' + esc(runStatusText(r)) + "</span>" +
      '<span class="ov-run-time">' + esc(relTime(r.created_at)) + "</span>" +
      "</span></button>";
  }).join("");
}

/* 运行状态经 SSE 推来：停在概览页时只重画依赖 runs 的两处（概览语 + 最近运行），
 * 不重新拉用量（台账不会因为一次运行状态变化就变）。
 * 侧栏常驻件（用量条 + 计数徽章）不跟着 S.tab 走，每次状态推送都要顺手刷新。 */
function ovMaybeRefresh() {
  if (S.tab === "overview") {
    ovRenderHead();
    ovRenderRecent();
  }
  sideUsageRefresh();
}

/* ---------------------------------------------------------- 侧栏常驻：用量条 + 计数徽章 */
/* 用量条的数字来自真实台账，口径写死为「本月」（自然月，从 1 号起）。
 * 刻意不做「用量 68%」那种进度条：CodeBee 没有套餐配额，没有分母可除，
 * 编一个上限出来就是假数。这里陈列的是能直接去用量页核对的事实。 */
async function sideUsageLoad() {
  if (S.sideUsage && S.sideUsageAt && Date.now() - S.sideUsageAt < 60000) return;  // 1 分钟节流
  try {
    const u = await api("/api/usage?days=30");
    S.sideUsage = u || null;
    S.sideUsageAt = Date.now();
  } catch (e) { /* 拉不到就保留上次的值，不把侧栏清空 */ }
  sideUsageRender();
}

function sideUsageRefresh() {
  sideUsageRender();          // 计数徽章靠 S.state，同步重画
  sideUsageLoad();            // 台账按节流异步补
}

function sideUsageRender() {
  sideUsageRenderRuns();
  const box = $("side-usage");
  if (!box) return;
  const u = S.sideUsage;
  const tok = $("su-tok"), cost = $("su-cost"), spark = $("su-spark");
  if (!tok || !cost || !spark) return;
  if (!u || !u.by_day || !u.by_day.length) {
    tok.textContent = t("本月还没有调用");
    cost.textContent = "";
    spark.innerHTML = "";
    return;
  }
  // 口径=自然月：days=31 只是取数窗口（任一自然月的已过天数都不超过它），
  // 展示前必须按当前年月再滤一遍——不滤的话月初那几天会把上个月的尾巴算进"本月"，
  // 标签写着本月、数字却是滚动 30 天，就是假口径
  const now = new Date();
  const ym = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
  const monthDays = u.by_day.filter((d) => String(d.day || "").slice(0, 7) === ym);
  const sum = (k) => monthDays.reduce((s, d) => s + (Number(d[k]) || 0), 0);
  const calls = sum("calls");
  if (!calls) {
    tok.textContent = t("本月还没有调用");
    cost.textContent = "";
    spark.innerHTML = "";
    return;
  }
  tok.textContent = fmtTok(sum("tokens")) + t(" tokens · ") + fmtTok(calls) + t(" 次");
  cost.textContent = fmtUsd(sum("cost_usd"));
  // 走势不受月界影响：近 14 天日序列，只是节奏参考，by_day 连续补零直接取尾
  const days = u.by_day.slice(-14).map((d) => d.tokens || 0);
  spark.innerHTML = days.length > 1 ? kpiSparkSvg(days, "accent") : "";
}

/* 计数徽章：挂在「运行记录」快捷导航项上，数当前在跑 + 排队的 run 数。
 * 数据取自 S.state.runs（SSE 推送），不额外请求；为 0 时收起徽章不留空位。 */
function sideUsageRenderRuns() {
  const el = $("nav-badge-runs");
  if (!el) return;
  const runs = (S.state && S.state.runs) || [];
  const n = runs.filter((r) => r.status === "running" || r.status === "queued").length;
  el.textContent = n > 99 ? "99+" : String(n);
  el.classList.toggle("hidden", n === 0);
  el.title = n ? t("正在运行或排队 ") + n + t(" 个任务") : "";
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
    '<button class="ghost small" onclick="copyConnUrl()">' + t("复制地址") + '</button></div>' +
    '<p class="hint">' + t("手机相机扫码即自动登录（地址已含访问令牌，扫一次永久记住）。") +
    t('局域网地址要求手机与电脑连同一 WiFi；Tailscale 地址出门也能用，') +
    t("两端需登录同一 Tailscale 账号。手机控制时另一端自动变为只读，可在顶栏接管。") + '<br>' +
    t("连不上时（如路由器重启后地址变了）回电脑重新打开此弹框扫新码即可。") + '</p>' +
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

/* ------------------------------------------------ 数据与备份（导出/导入/每日清理） */

let _biMode = "merge";       // 导入方式：merge 合并 | replace 替换
let _biPreview = null;       // 预览通过后暂存，确认导入时复用（防跳过预览直接导）

async function loadDataPage() {
  try {
    const d = await api("/api/cleanup");
    if ($("set-cleanup")) $("set-cleanup").checked = !!(d.config && d.config.enabled);
    if ($("set-cleanup-days")) $("set-cleanup-days").value = (d.config && d.config.retention_days) || 14;
    renderCleanupState(d.state);
    renderCleanupPlan(d.plan);
  } catch (e) { /* 静默：列表拉不到不挡页面其余功能 */ }
}

function renderCleanupState(st) {
  const el = $("cl-state");
  if (!el) return;
  if (!st || !st.last_run) { el.textContent = t("尚未清理过（每天自动一次）"); return; }
  el.textContent = t("上次清理：") + st.last_run + t("，") + t("释放") + " " + fmtSize(st.last_freed || 0)
    + ((st.last_result && st.last_result.errors && st.last_result.errors.length)
      ? t("，") + t("有") + " " + st.last_result.errors.length + " " + t("项被占用跳过") : "");
}

function renderCleanupPlan(plan) {
  const el = $("cl-plan");
  if (!el) return;
  const items = (plan && plan.items) || [];
  if (!items.length) { el.innerHTML = '<p class="hint">' + esc(t("没有可清理的垃圾，很干净")) + "</p>"; return; }
  el.innerHTML = items.map((it) =>
    '<div class="cl-row"><b>' + esc(t(it.label)) + '</b><span class="hint">' + esc(t(it.note || ""))
    + '</span><span class="tag">' + esc(fmtSize(it.bytes)) + "</span></div>").join("")
    + '<p class="hint">' + esc(t("共可释放约")) + " " + esc(fmtSize(plan.total_bytes || 0)) + "</p>";
}

window.exportBackup = async function () {
  const btn = $("bk-go");
  const old = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "<span>" + esc(t("正在打包…")) + "</span>"; }
  try {
    const r = await api("/api/data/export", { method: "POST", timeout: 600000,
      body: JSON.stringify({ target_dir: (($("bk-target") || {}).value || "").trim(),
                             include_logs: $("bk-logs").checked }) });
    const ext = (r.external_workdirs || []);
    $("bk-result").innerHTML = '<span class="ok">' + esc(t("已导出")) + t("：") + esc(r.path)
      + t("（") + esc(fmtSize(r.size || 0)) + t("，") + esc(t("任务")) + " " + (r.tasks || 0)
      + " · " + esc(t("运行记录")) + " " + (r.runs || 0) + t("）") + "</span>"
      + (ext.length ? '<div><span class="bad">' + esc(t("以下外部目录不在备份里，需自行拷贝："))
        + esc(ext.join("、")) + "</span></div>" : "")
      + '<button class="ghost small" onclick="copyText(this)" data-copy="' + esc(r.path) + '">'
      + esc(t("复制路径")) + "</button>";
  } catch (e) {
    $("bk-result").innerHTML = '<span class="bad">' + esc(e.message || String(e)) + "</span>";
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = old; }
  }
};

/* 通用「复制到剪贴板」：data-copy 属性携带文本（导出路径等） */
window.copyText = async function (btn) {
  const text = btn.getAttribute("data-copy") || "";
  try { await navigator.clipboard.writeText(text); toast(t("已复制")); }
  catch (e) { fallbackCopy(text, () => toast(t("已复制"))); }
};

window.pickBackupFile = async function () {
  const cur = (($("bi-path") || {}).value || "").trim();
  const initial = cur ? cur.replace(/[\\/][^\\/]*$/, "") : "";
  let r = null;
  try {
    r = await api("/api/pick_file", { method: "POST",
      body: JSON.stringify({ initial, title: t("选择备份包") }) });
  } catch (e) {
    if (/仅限本机/.test(e.message || "")) { toast(t("文件选择仅限本机使用，请手动输入路径"), true); return; }
    toast(t("操作失败：") + (e.message || e), true);
    return;
  }
  if (r.busy) { toast(t("已有一个选择窗口正在等待"), true); return; }
  if (r.error) { toast(t("本机没有可用的文件选择器，请手动输入 zip 路径"), true); return; }
  if (r.path) { $("bi-path").value = r.path; inspectBackup(); }
};

window.inspectBackup = async function () {
  const p = (($("bi-path") || {}).value || "").trim();
  if (!p) { toast(t("请先选择备份包"), true); return; }
  _biPreview = null;
  $("bi-apply").classList.add("hidden");
  $("bi-restart").classList.add("hidden");
  $("bi-preview").innerHTML = esc(t("正在读取备份包…"));
  try {
    const r = await api("/api/data/import", { method: "POST",
      body: JSON.stringify({ op: "inspect", path: p }) });
    _biPreview = r.preview;
    renderBackupPreview(r.preview);
  } catch (e) {
    $("bi-preview").innerHTML = '<span class="bad">' + esc(e.message || String(e)) + "</span>";
  }
};

function renderBackupPreview(pv) {
  const el = $("bi-preview");
  if (!el) return;
  const rows = [];
  rows.push('<b>' + esc(t("备份时间")) + t("：") + "</b>" + esc(pv.exported_at || "-")
    + (pv.version ? " · v" + esc(pv.version) : ""));
  rows.push(esc(t("任务")) + " " + (pv.tasks == null ? "-" : pv.tasks)
    + " · " + esc(t("运行记录")) + " " + (pv.runs == null ? "-" : pv.runs)
    + " · " + esc(t("数据文件")) + " " + (pv.data_files == null ? "-" : pv.data_files)
    + (pv.workspace_files ? " · " + esc(t("工作目录文件")) + " " + pv.workspace_files : ""));
  if (pv.include_logs === false) rows.push('<span class="hint">' + esc(t("备份不含运行过程日志")) + "</span>");
  if (pv.remap_needed) rows.push(esc(t("路径重映射")) + t("：") + esc(pv.old_workdir || pv.old_data_dir)
    + " → " + esc(pv.cur_workdir));
  if ((pv.external_workdirs || []).length)
    rows.push('<span class="bad">' + esc(t("以下外部目录不在备份里，需自行拷贝："))
      + esc(pv.external_workdirs.join(t("、"))) + "</span>");
  if ((pv.busy_runs || []).length)
    rows.push('<span class="bad">' + esc(t("有任务正在运行或等待自动续跑，先等它们结束再导入")) + "</span>");
  el.innerHTML = rows.map((s) => "<div>" + s + "</div>").join("");
  if (!(pv.busy_runs || []).length) $("bi-apply").classList.remove("hidden");
}

window.applyBackupImport = async function () {
  if (!_biPreview) return;
  const replaceMode = _biMode === "replace";
  const ok = await uiConfirm(replaceMode
    ? t("替换导入会先清空本机数据目录，再整体落入备份内容（当前数据已自动留了反悔备份）。确定继续？")
    : t("确认导入该备份包？同名任务与配置将被覆盖。"),
    { danger: replaceMode, ok: t("确认导入") });
  if (!ok) return;
  const btn = $("bi-apply");
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "<span>" + esc(t("正在导入…")) + "</span>";
  try {
    const r = await api("/api/data/import", { method: "POST", timeout: 600000,
      body: JSON.stringify({ op: "apply", path: (($("bi-path") || {}).value || "").trim(),
                             mode: _biMode, remap: $("bi-remap").checked }) });
    const rm = r.remap || {};
    $("bi-preview").innerHTML = '<span class="ok">' + esc(replaceMode ? t("替换导入完成") : t("合并导入完成"))
      + t("：") + "</span>" + esc(t("数据文件")) + " " + (r.data_files || 0)
      + " · " + esc(t("工作目录文件")) + " " + (r.workspace_files || 0)
      + (rm.applied ? " · " + esc(t("已重映射")) + " " + rm.files + " " + esc(t("个文件中的")) + " " + rm.values + " " + esc(t("处路径")) : "")
      + '<div class="hint">' + esc(t("导入前的本机数据备份在")) + " " + esc(r.backup_path || "-") + "</div>"
      + ((r.external_workdirs || []).length
        ? '<div><span class="bad">' + esc(t("以下外部目录不在备份里，需自行拷贝："))
          + esc(r.external_workdirs.join(t("、"))) + "</span></div>" : "")
      + '<div class="ok">' + esc(t("请重启服务使导入的数据全部生效")) + "</div>";
    $("bi-apply").classList.add("hidden");
    $("bi-restart").classList.remove("hidden");
  } catch (e) {
    $("bi-preview").innerHTML = '<span class="bad">' + esc(e.message || String(e)) + "</span>";
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
};

async function saveCleanupSettings() {
  const days = parseInt($("set-cleanup-days").value, 10);
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({
      cleanup_enabled: $("set-cleanup").checked,
      cleanup_retention_days: isNaN(days) ? 14 : days }) });
    toast(t("清理设置已保存"));
    loadDataPage();   // 保留天数变了，重扫可清理预估
  } catch (e) { toast(t("操作失败：") + (e.message || e), true); }
}

window.runCleanup = async function (includeProfiles) {
  if (includeProfiles) {
    const ok = await uiConfirm(t("清理发布浏览器缓存会丢失平台登录态，下次发布需要重新扫码。确定清理？"),
      { danger: true, ok: t("仍然清理") });
    if (!ok) return;
  }
  const btn = includeProfiles ? null : $("cl-run");
  const old = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "<span>" + esc(t("正在清理…")) + "</span>"; }
  try {
    const r = await api("/api/cleanup", { method: "POST", timeout: 300000,
      body: JSON.stringify({ op: "run", include_profiles: !!includeProfiles }) });
    toast(t("清理完成，释放") + " " + fmtSize((r.result && r.result.freed) || 0));
    loadDataPage();
  } catch (e) { toast(t("操作失败：") + (e.message || e), true); }
  finally {
    if (btn) { btn.disabled = false; btn.innerHTML = old; }
  }
};
window.saveCleanupSettings = saveCleanupSettings;

/* 手机端（<900px）抽屉收起：点导航/任何侧栏可点项后都应收回，别挡内容 */
function collapseDrawerIfMobile() {
  if (window.innerWidth < 900) document.body.classList.add("side-collapsed");
}

/* 切页：主栏换成目标子页。shell 决定左栏形态——"main" 保持任务树、"settings" 换成
 * 设置导航，缺省按目标页归哪套导航自动选（见 mainPageSubs/settingsNavSubs）。 */
function switchTab(name, shell) {
  if (name === "__phone") { openPhoneConnect(); return; }  // 手机连接是弹框，不切页
  if (name === "__guide") { welcomeOpen(); return; }       // 帮助中心是弹层，不切页（设置导航「软件」组）
  // 形态由目标页决定：快捷导航页（概览/运行记录/自动化）走 main——只换主栏内容、
  // 左栏任务树原地不动；其余页进设置导航。调用方可用 shell 显式覆盖（如已在设置里
  // 点运行详情，仍要留在设置侧栏）。用户主动进设置时左栏一定会换成设置导航。
  const inMain = (shell || (mainPageSubs().has(name) ? "main" : "settings")) === "main";
  S.tab = name;
  S.mainPage = inMain ? name : "";   // 主栏停在哪个全局页；closeRun 靠它回到点开详情前那一页
  if (settingsNavSubs().has(name)) localStorage.setItem("orch.setTab", name);
  document.body.classList.toggle("settings-mode", !inMain);
  document.body.classList.remove("files-mode");   // 文件浏览页与两套导航都互斥，别叠在左栏
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-settings"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", !inMain && b.dataset.sub === name));
  document.querySelectorAll(".qitem").forEach((b) => b.classList.toggle("active", inMain && b.dataset.page === name));
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-" + name));
  renderPageCrumb();   // 页名 + 面包屑组名一起刷新（组名从左栏导航现读）
  if (name === "runs" && !S.detailRunId) closeRun();
  if (name === "agents") autoCheckUpdates();   // 进目录页自动查各 CLI 新版本
  if (name === "orch") { loadOrchestrator(); loadSettings(); }  // 进编排设置页拉取配置
  if (name === "skills") loadSkills();   // 进经验库页拉取沉淀
  if (name === "knowledge") loadKnowledge();   // 进知识库页拉取条目
  if (name === "automation") { loadAutomation(); startAutoPoll(); }   // 进自动化页：拉取 + 页面可见时每 8s 轮询
  else stopAutoPoll();   // 离开自动化页（或切到别的子页）即停表
  if (name === "hooks") loadHooksPanel();   // 进钩子页：拉配置渲染管理列表
  if (name === "zentao") loadZentao();   // 进禅道页：拉配置与修复记录回填表单
  if (name === "__wxdigest") { beeRefresh(); beeMarkSeen(); }   // 进群摘要页：拉取视图并清未读
  if (name === "market") loadMarket();   // 进插件市场页拉取目录
  if (name === "data") loadDataPage();   // 进数据与备份页：清理配置/状态/可清理预估
  if (name === "usage") { syncUsageRange(); loadUsage(); }   // 进用量页：对齐范围选中态并拉取
  if (name === "overview") { ovDays(); loadOverview(); }   // 进概览页：对齐趋势范围并拉两期台账
  if (name === "appearance") renderAppearance();   // 进皮肤页：按当前皮肤/明暗重画卡片
  if (name === "appearance") renderCodeSettings();  // 代码设置行 + 双主题预览卡同步当前值
  if (name === "about") loadSelfupdate(false);     // 进关于页：拉版本与更新状态
  collapseDrawerIfMobile();
  syncInspectorVis();    // 检查器只属于任务上下文：进设置子页自动收起
}

/* 退出设置/回到新建任务表单：左栏恢复任务树，主栏回到任务页 */
function exitSettings() {
  S.tab = "tasks";
  S.mainPage = "";   // 主栏回到表单：不再是快捷导航页
  stopAutoPoll();   // 离开设置视图：自动化页轮询一并停掉
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-settings"));
  document.querySelectorAll("#page-settings .subpage").forEach((d) => d.classList.toggle("hidden", d.id !== "sub-tasks"));
  document.querySelectorAll(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sub === "tasks"));
  document.querySelectorAll(".qitem").forEach((b) => b.classList.remove("active"));
  document.body.classList.remove("settings-mode");
  document.body.classList.remove("files-mode");
  cmpGreeting();   // 回到任务页：问候语刷新
  renderPageCrumb();
  collapseDrawerIfMobile();
  syncInspectorVis();    // 回到新建表单：不是任务上下文，检查器收起（点运行详情才会滑回）
}

/* 侧栏底部齿轮：直接进设置（左栏换成设置导航，不再弹菜单）。回到上次看的那页，默认编排设置。
 * 只认设置导航里真实存在的子页——概览/运行记录/自动化已升为快捷导航，落回它们会停在
 * 一个没有导航项高亮的设置页。 */
function enterSettings() {
  const saved = localStorage.getItem("orch.setTab") || "";
  switchTab(settingsNavSubs().has(saved) ? saved : "orch", "settings");
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
window.setOvDays = setOvDays;
window.renderPageCrumb = renderPageCrumb;   // i18n.js 换语言后补画标题用
window.jumpToRun = jumpToRun;
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
window.mkrInstall = mkrInstall;
window.mkrRefresh = mkrRefresh;
window.mkSetView = mkSetView;
window.mkrLoadPage = mkrLoadPage;
window.renderMarketRemote = renderMarketRemote;
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
window.updDismiss = updDismiss;

document.addEventListener("DOMContentLoaded", () => {
  // 远程地址里带的 ?token= 存起来并从地址栏抹掉，之后所有请求走请求头
  const urlTok = new URLSearchParams(location.search).get("token");
  if (urlTok) {
    localStorage.setItem("orch.token", urlTok);
    history.replaceState(null, "", location.pathname);
  }
  document.querySelectorAll(".set-item").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.sub)));
  $("btn-set-back").addEventListener("click", exitSettings);
  $("btn-files-back").addEventListener("click", closeFolderFiles);   // 文件浏览页返回任务树
  // 文件浏览页点文件行：中央弹窗预览（行是委托绑定，懒加载子目录里的行同样生效；
  // dir 用行上记录的所属目录——懒加载行的文件名相对的是子目录，用根目录拼会 404）
  $("sf-body").addEventListener("click", (e) => {
    const f = e.target.closest(".sf-file");
    if (!f) return;
    const dir = f.dataset.dir || S.sfDir;
    if (!dir) return;
    dirFilePopup(dir, f.dataset.name || "", Number(f.dataset.size) || 0);
  });
  $("btn-settings").addEventListener("click", enterSettings);
  $("btn-phone-side").addEventListener("click", openPhoneConnect);
  $("btn-prov-side").addEventListener("click", () => switchTab("orch"));   // 供应商指示 → 编排设置页更换
  document.querySelectorAll("#usage-ranges [data-days]").forEach((b) =>
    b.addEventListener("click", () => setUsageDays(b.dataset.days)));
  $("btn-usage-refresh").addEventListener("click", loadUsage);
  document.querySelectorAll("#usage-heat-modes [data-heat]").forEach((b) =>
    b.addEventListener("click", () => setUsageHeatMode(b.dataset.heat)));
  $("btn-back").addEventListener("click", closeRun);
  $("btn-cancel").addEventListener("click", cancelRun);
  bindDirector();
  bindChat();
  const bindArtifactClicks = (root) => {
    if (!root) return;
    root.addEventListener("click", (e) => {
      const file = e.target.closest("[data-file-run][data-file-name]");
      if (!file) return;
      e.preventDefault();
      artPopup(file.dataset.fileRun, file.dataset.fileName || "", Number(file.dataset.fileSize) || 0);
    });
  };
  bindArtifactClicks($("rd-arts"));
  bindArtifactClicks($("insp-artifacts"));
  // 预览子页签（运行 / 各文件）：委托一次，页签条整块重画也不用重绑
  const pvTabs = $("rd-pv-tabs");
  if (pvTabs) pvTabs.addEventListener("click", (e) => {
    const b = e.target.closest(".pv-tab");
    if (b && b.dataset.pv) window.pvGo(b.dataset.pv);
  });
  const pvReload = $("rd-pv-reload");
  if (pvReload) pvReload.addEventListener("click", () => window.pvReload());
  // 动态详情区统一用 data 属性委托，日志路径/文件名不再拼进 inline JS。
  const detailOverview = $("rd-overview");
  if (detailOverview) detailOverview.addEventListener("click", (e) => {
    const log = e.target.closest("[data-overview-log]");
    if (log) { toggleLog(log.dataset.overviewRun, log.dataset.overviewLog); return; }
    const tab = e.target.closest("[data-overview-tab]");
    if (tab) { S.rdTab = tab.dataset.overviewTab; S.rdTabPin = true; applyRdTabs(); }
  });
  const rdMore = $("rd-more-toggle"), rdMetaPop = $("rd-meta-popover"), rdMoreClose = $("rd-more-close");
  if (rdMore) rdMore.addEventListener("click", (e) => {
    e.stopPropagation();
    setRdMetaOpen(rdMetaPop && rdMetaPop.classList.contains("hidden"));
  });
  if (rdMoreClose) rdMoreClose.addEventListener("click", () => setRdMetaOpen(false));
  document.addEventListener("click", (e) => {
    if (!rdMetaPop || rdMetaPop.classList.contains("hidden")) return;
    if (rdMetaPop.contains(e.target) || (rdMore && rdMore.contains(e.target))) return;
    setRdMetaOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && rdMetaPop && !rdMetaPop.classList.contains("hidden")) setRdMetaOpen(false);
  });
  const stepBox = $("rd-steps");
  if (stepBox) stepBox.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-log-run][data-log-rel]");
    const row = e.target.closest(".step[data-log]");
    if (btn) { e.stopPropagation(); toggleLog(btn.dataset.logRun, btn.dataset.logRel); return; }
    if (row && row.dataset.log) toggleLog(row.dataset.runId || S.detailRunId, row.dataset.log);
  });
  const hiveBox = $("rd-hive-cells");
  if (hiveBox) {
    hiveBox.addEventListener("click", (e) => {
      const cell = e.target.closest("[data-hive-run][data-hive-log]");
      if (cell) hiveOpenLog(cell.dataset.hiveRun, cell.dataset.hiveLog);
    });
    hiveBox.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const cell = e.target.closest("[data-hive-run][data-hive-log]");
      if (cell) { e.preventDefault(); hiveOpenLog(cell.dataset.hiveRun, cell.dataset.hiveLog); }
    });
  }
  // 详情标签页：手点即切并钉住——状态变化触发的自动选卡不再抢用户的手选
  $("rd-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".rd-tab");
    if (!b || b.classList.contains("hidden")) return;
    S.rdTab = b.dataset.tab;
    S.rdTabPin = true;
    applyRdTabs();
    rdEnsureActiveTabData();
  });
  $("rd-tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const tabs = Array.from($("rd-tabs").querySelectorAll(".rd-tab:not(.hidden)"));
    if (!tabs.length) return;
    const current = Math.max(0, tabs.indexOf(document.activeElement));
    let next = current;
    if (e.key === "ArrowRight") next = (current + 1) % tabs.length;
    if (e.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = tabs.length - 1;
    e.preventDefault();
    tabs[next].focus();
    tabs[next].click();
    tabs[next].scrollIntoView({ block: "nearest", inline: "nearest" });
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
  $("btn-retry").addEventListener("click", () => {
    const r = S.lastRun;
    const taskId = (r && r.task_id) ||
      (((S.state || {}).tasks || []).some((task) => task.id === S.detailTaskKey)
        ? S.detailTaskKey : "");
    if (taskId) retryTask(taskId);
  });
  /* 日志控制台高度：顶边手柄拖拽调整（localStorage 记忆），双击复位为默认弹性高度 */
  (() => {
    const grip = $("rd-log-grip"), box = $("rd-log");
    if (!grip || !box) return;
    const KEY = "orch.rdLogH";
    const saved = parseInt(localStorage.getItem(KEY), 10);
    /* 记忆值是「期望高度」：flex 允许收缩（shrink 1），窗口变矮时自动缩回视口内，
       永不把抽屉顶出窗口底（用户反馈「还是超长了」的根因就是固定 px 不收缩） */
    if (saved >= 140) box.style.flex = "0 1 " + saved + "px";
    grip.addEventListener("dblclick", () => {
      localStorage.removeItem(KEY);
      box.style.flex = "";
    });
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* 老浏览器降级 */ }
      const startY = e.clientY;
      const startH = box.getBoundingClientRect().height;
      const pre = $("rd-log-text");
      const stick = pre ? pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48 : false;
      let lastH = Math.round(Math.max(startH, 140));
      document.body.classList.add("rd-log-resizing");
      const onMove = (ev) => {
        lastH = Math.round(Math.min(Math.max(startH + (startY - ev.clientY), 140), window.innerHeight * 0.72));
        box.style.flex = "0 1 " + lastH + "px";
      };
      const onUp = (ev) => {
        try { grip.releasePointerCapture(ev.pointerId); } catch (err) { /* 已释放 */ }
        document.body.classList.remove("rd-log-resizing");
        grip.removeEventListener("pointermove", onMove);
        grip.removeEventListener("pointerup", onUp);
        grip.removeEventListener("pointercancel", onUp);
        localStorage.setItem(KEY, String(lastH));   // 存钳制后的目标值，隐藏态 rect 是 0 不能用
        if (stick && pre) pre.scrollTop = pre.scrollHeight;   // 拖完仍在贴底状态则继续跟最新
      };
      grip.addEventListener("pointermove", onMove);
      grip.addEventListener("pointerup", onUp);
      grip.addEventListener("pointercancel", onUp);
    });
  })();
  if ($("btn-talk")) $("btn-talk").addEventListener("click", window.rdTalkToggle);
  if ($("rd-talk-send")) $("rd-talk-send").addEventListener("click", window.rdTalkSend);
  if ($("rd-talk-input")) $("rd-talk-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); window.rdTalkSend(); }
  });
  if ($("rd-talk-attach")) $("rd-talk-attach").addEventListener("click", () => $("rd-talk-file").click());
  if ($("rd-talk-file")) $("rd-talk-file").addEventListener("change", (e) => {
    talkUploadFiles(Array.from(e.target.files || []));
    e.target.value = "";
  });
  if ($("rd-talk-input")) $("rd-talk-input").addEventListener("paste", (e) => {
    const files = Array.from((e.clipboardData || {}).files || []);
    if (files.length) { e.preventDefault(); talkUploadFiles(files); }
  });
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
  // 分享页：新窗口打开自包含 HTML（零依赖离线可看，可直接另存/转发）
  $("btn-share").addEventListener("click", () => {
    if (S.detailRunId) window.open("/api/runs/" + encodeURIComponent(S.detailRunId) + "/share",
      "_blank", "noopener");
  });
  $("btn-theme").addEventListener("click", toggleTheme);
  $("btn-notify-toggle").addEventListener("click", toggleNotifySound);
  // 命令面板：侧栏搜索行 / Ctrl+K 唤起（函数若尚未落地，跳过而不炸整个初始化）
  if (typeof bindCmdK === "function") bindCmdK();
  // 侧栏常驻用量条：首屏先取一次台账（之后由 applyState 的节流刷新接手）
  sideUsageRefresh();
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
  // 桌面蜜蜂显示模式：常驻 / 仅任务运行时
  $("pet-mode").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pmode]");
    if (b) savePetMode(b.dataset.pmode);
  });
  // 桌面蜜蜂形象：毛绒 / 机械
  $("pet-skin").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pskin]");
    if (b) savePetSkin(b.dataset.pskin);
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
    if (e.target.closest("button, summary, .stask")) collapseDrawerIfMobile();
  });
  $("btn-new-task").addEventListener("click", exitSettings);
  // 侧栏快捷导航：主栏换成目标页，左栏任务树不动（shell="main"）。
  // 页面清单以 index.html .side-quick 的 data-page 为准，这里逐项绑 id。
  $("btn-q-overview").addEventListener("click", () => switchTab("overview", "main"));
  $("btn-q-runs").addEventListener("click", () => switchTab("runs", "main"));
  $("btn-q-automation").addEventListener("click", () => switchTab("automation", "main"));
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
  /* 工作目录不再用「上次用过」的旧记忆预填：那样改默认保存路径后，新建任务仍会被
   * 旧目录盖过。首屏默认选中设置里的默认路径（loadSettings 落值），克隆/右键新建
   * 等显式入口各自直接写字段值，不跨刷新持久化。清掉旧版残留键，避免误解为默认选中。 */
  localStorage.removeItem("orch.workdir");
  // 附件：按钮选文件 + 目标框粘贴截图；工作目录变化时探测 git 仓库（代码版本下拉）
  $("btn-attach").addEventListener("click", () => $("f-attach-file").click());
  $("f-attach-file").addEventListener("change", (e) => {
    addAttachFiles(Array.from(e.target.files || []));
    e.target.value = "";  // 允许重复选同一个文件
  });
  const attChipBox = $("att-chips");
  if (attChipBox) attChipBox.addEventListener("click", (e) => {
    const remove = e.target.closest("[data-att-remove]");
    if (remove) {
      e.preventDefault();
      e.stopPropagation();
      removeAtt(remove.dataset.attRemove || "");
    }
  });
  $("f-goal").addEventListener("paste", onGoalPaste);
  // Composer 手感：Enter 直接发送，Shift+Enter 换行（输入法组词中不劫持）
  $("f-goal").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); createTask(); }
  });
  $("f-workdir").addEventListener("input", queueGitProbe);
  $("f-workdir").addEventListener("change", queueGitProbe);
  // 点工作目录输入框直接进文件夹选择（远端设备被 403 守卫拦下，静默继续手输）
  $("f-workdir").addEventListener("click", () => window.pickFolder("f-workdir", true));
  // 最近文件夹菜单：条目点击统一走 wdMenuPick；点外面收起
  const wdMenu = $("wd-menu");
  if (wdMenu) wdMenu.addEventListener("click", (e) => {
    const item = e.target.closest(".wd-item");
    if (!item) return;
    e.stopPropagation();
    window.wdMenuPick(item);
  });
  document.addEventListener("click", (e) => {
    if (wdMenu && !wdMenu.classList.contains("hidden") &&
        !e.target.closest("#wd-menu, #f-workdir-menu-btn")) wdMenu.classList.add("hidden");
  });
  $("set-workdir").addEventListener("click", () => window.pickFolder("set-workdir", true));
  // 数据与备份页：导入方式 seg（合并/替换），纯前端状态，导入时随请求带上
  $("bi-mode").addEventListener("click", (e) => {
    const b = e.target.closest("[data-bimode]");
    if (!b) return;
    _biMode = b.dataset.bimode;
    document.querySelectorAll("#bi-mode .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  });
  $("f-git-rev").addEventListener("change", renderGitHint);
  if ($("f-workdir").value) queueGitProbe();  // 回填的目录：进页面即探测代码版本
  $("f-type").addEventListener("change", onTypeChange);
  // 编排模式/思考程度 pill：选项变化同步短名（含语言切换后的 option 文本变化）
  $("f-mode").addEventListener("change", () => syncCmpSelFace("f-mode"));
  $("f-thinking").addEventListener("change", () => syncCmpSelFace("f-thinking"));
  // 类型菜单点外面收起（Escape 同收）；按钮自身点击由 toggleTypeMenu 负责
  document.addEventListener("click", (e) => {
    const menu = $("type-menu"), btn = $("f-type-btn");
    if (menu && !menu.classList.contains("hidden") &&
        !menu.contains(e.target) && !(btn && btn.contains(e.target))) toggleTypeMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleTypeMenu(false);
  });
  // 语言下拉同款：点外面 / Escape 收起；按钮自身点击由 toggleLangMenu 负责
  document.addEventListener("click", (e) => {
    const menu = $("lang-menu"), btn = $("btn-lang");
    if (menu && !menu.classList.contains("hidden") &&
        !menu.contains(e.target) && !(btn && btn.contains(e.target))) toggleLangMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleLangMenu(false);
  });
  $("f-mode").addEventListener("change", () => {
    $("f-manual-only").classList.toggle("hidden", $("f-mode").value !== "manual");
    refreshEstimate();
  });
  $("f-thinking").addEventListener("change", refreshEstimate);
  $("f-rounds").addEventListener("change", refreshEstimate);
  $("f-direct-provider").addEventListener("change", renderDirectModelPicker);
  $("f-direct-model-name").addEventListener("change", cmpDirectBtnSync);   // 更多选项里改模型也要同步 pill
  $("f-type").dispatchEvent(new Event("change"));
  cmpGreeting();   // 问候语按时段刷新（切语言/回任务页也会重算）
  $("btn-reload-catalog").addEventListener("click", async () => {
    await api("/api/catalog/reload", { method: "POST" }); poll(); refreshSessionAgents();
  });
  $("btn-catalog-autobind").addEventListener("click", catAutoBindAll);
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
    if (e.key === "F1") { e.preventDefault(); welcomeOpen(); return; }   // F1 = 帮助中心（浏览器帮助被拦下）
    if (e.key === "Escape" && !$("welcome").classList.contains("hidden")) { welcomeClose(); return; }
    if (e.key === "Escape" && !$("modal").classList.contains("hidden")) closeModal();
    // 日志抽屉：Esc 收起（最底层，放在弹窗之后）
    if (e.key === "Escape" && !$("rd-log").classList.contains("hidden")) window.rdLogClose();
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
        && $("welcome").classList.contains("hidden") && filePopIsOpen() === false && $("cmdk-mask").classList.contains("hidden")
        && !document.body.classList.contains("settings-mode")
        && !document.body.classList.contains("files-mode")) {
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
  // 禅道页：测试连接 / 立即扫描 / 保存配置
  $("btn-zentao-test").addEventListener("click", testZentao);
  $("btn-zentao-fetchcat").addEventListener("click", () => ztFetchCatalog(false));
  $("btn-zentao-scan").addEventListener("click", scanZentao);
  $("btn-zentao-save").addEventListener("click", saveZentao);
  $("btn-zentao-addprofile").addEventListener("click", ztProfAdd);
  $("auto-filter").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    S.autoFilter = b.dataset.f;
    document.querySelectorAll("#auto-filter .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    renderAutomation();
  });
  // 插件市场：搜索 / 分类 / 状态段选（本地过滤）；本地/外部视图切换与外部目录交互
  $("mk-search").addEventListener("input", renderMarket);
  $("mk-cat").addEventListener("change", renderMarket);
  $("mk-state").addEventListener("click", (e) => {
    const b = e.target.closest("[data-s]");
    if (!b) return;
    S.mkState = b.dataset.s;
    document.querySelectorAll("#mk-state .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    renderMarket();
  });
  $("mk-view").addEventListener("click", (e) => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    mkSetView(b.dataset.v);
  });
  $("mkr-search").addEventListener("input", mkrDebounce);
  $("mkr-search").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    clearTimeout(mkrDebounce._t);
    mkrLoadPage(true);   // 回车立即搜（不等防抖）
  });
  $("mkr-search-btn").addEventListener("click", () => {
    clearTimeout(mkrDebounce._t);
    mkrLoadPage(true);
  });
  $("mkr-source").addEventListener("change", () => mkrLoadPage(true));
  $("mkr-refresh").addEventListener("click", mkrRefresh);
  $("mkr-more-btn").addEventListener("click", () => mkrLoadPage(false));
  mkrBindSentinel();
  $("btn-reset-catalog").addEventListener("click", async () => {
    if (!await uiConfirm(t("恢复内置默认 catalog？你对该文件的修改将丢失。"), { ok: t("恢复"), danger: true })) return;
    await api("/api/catalog/reset", { method: "POST" }); poll();
  });
  poll();
  schedulePolling();
  startSSE();
  startCtrlHeartbeat();
  loadFlows();   // 任务类型下拉（内置 + 自定义流程）
  refreshSessionAgents();  // 继续会话下拉的工具集合（服务端 60s 缓存，开销小）
  loadOrchestrator();      // 侧栏左下角的编排者供应商指示（进入编排设置页时会再拉一次）
  loadSettings();          // 启动即拉设置：新建表单「默认选中默认路径」依赖它
  suStartupCheck();        // 静默查一次新版本（有新版 toast 提醒，同版本只提一次）
  maybeWelcome();          // 首次启动弹欢迎引导（orch.welcomed 记账，只弹一次）
});

/* ===== 命令面板（Ctrl+K / 侧栏搜索行）：任务直达 + 快捷命令 ===== */
const CmdK = { tab: "all", q: "", sel: 0, flat: [] };

function cmdkOps() {
  return [
    { icon: "i-tasks", label: t("新任务"), kbd: "N", run: () => $("btn-new-task").click() },
    { icon: "i-calendar-days", label: t("自动化"), run: () => switchTab("automation") },
    { icon: "i-blocks", label: t("插件市场"), run: () => switchTab("market") },
    { icon: "i-book", label: t("经验库"), run: () => switchTab("skills") },
    { icon: "i-sigma", label: t("知识库"), run: () => switchTab("knowledge") },
    { icon: "i-cpu", label: t("本机智能体"), run: () => switchTab("agents") },
    { icon: "i-chart", label: t("用量统计"), run: () => switchTab("usage") },
    { icon: "i-gear", label: t("设置"), run: () => enterSettings() },
    { icon: "i-bee", label: t("帮助中心"), run: () => welcomeOpen() },
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

/* ------------------------------------------------ 群摘要页（core/wxdigest.py）
 * 设置 → 群摘要：配置监控文件夹/微信直连 + 各群摘要列表（替代原右下角悬浮
 * 蜜蜂坞——用户拍板去掉悬浮组件，配置与总结统一进页面）。桌面蜜蜂 pet.py
 * 右键「群摘要」直达本页。进页/60s 轮询 /api/wxdigest；进页 POST seen 清未读。 */
const Bee = { view: null, lastUnseen: 0, bubT: 0, selGroups: null, pyDirty: false };

function beeShow(el, on) { if (el) el.classList.toggle("hidden", !on); }

async function beeRefresh() {
  let v;
  try { v = await api("/api/wxdigest"); } catch (e) { return; }   // 静默：不打扰主流程
  Bee.view = v;
  Bee.lastUnseen = v.unseen;
  beeRenderPanel();
}

async function beeMarkSeen() {
  try {
    const r = await api("/api/wxdigest/seen", { method: "POST", body: "{}" });
    Bee.view = r.view;
    Bee.lastUnseen = 0;
    beeRenderPanel();
  } catch (e) { /* ignore */ }
}

function beeRenderPanel() {
  const v = Bee.view;
  if (!v) return;
  const cfg = v.config || {};
  $("bee-enabled").checked = !!cfg.enabled;
  $("bee-reader").checked = !!cfg.reader_enabled;
  beeShow($("bee-reader-box"), !!cfg.reader_enabled);
  // 用户手动改过路径（选候选/输入）后轮询不再回填覆盖——否则 60s 一冲，
  // 选择根本存不进去（「选不中 Python」根因之一）
  if (!Bee.pyDirty && document.activeElement !== $("bee-reader-py"))
    $("bee-reader-py").value = cfg.reader_python || "";
  if (!Bee.selGroups) Bee.selGroups = (cfg.groups || []).slice();
  const n = Bee.selGroups.length;
  $("bee-groups-btn").textContent = t("选择要监听的群（{0}）", n);
  if (document.activeElement !== $("bee-dir")) $("bee-dir").value = cfg.watch_dir || "";
  if (document.activeElement !== $("bee-interval")) $("bee-interval").value = cfg.interval_minutes || 30;
  if (document.activeElement !== $("bee-maxchars")) $("bee-maxchars").value = cfg.max_chars || 12000;
  const bits = [];
  if (v.last_scan) bits.push(t("上次扫描：") + v.last_scan);
  if (v.next_scan && cfg.enabled) bits.push(t("下次：") + v.next_scan);
  if (v.last_error) bits.push("⚠ " + v.last_error);
  if (!v.has_model) bits.push("⚠ " + t("未配置可用模型，请先到「绑定」页设置"));
  $("bee-status").textContent = bits.join("　");
  const list = $("bee-list");
  if (!(v.digests || []).length) {
    list.innerHTML = '<div class="bee-empty">' + esc(t("还没有摘要")) + "</div>";
  } else {
    list.innerHTML = v.digests.map((d) =>
      '<div class="bee-digest">' +
      '<div class="bee-digest-head"><span class="bee-tag">' + esc(d.group) + "</span>" +
      '<span class="bee-meta">' + esc(d.created_at || "") + " · " + Number(d.count || 0) + t(" 条") + "</span>" +
      '<span class="spacer"></span>' +
      '<button class="ghost small bee-to-kb" data-did="' + esc(d.id || "") + '">' + t("转知识库") + "</button></div>" +
      '<div class="bee-text">' + esc(d.text || "") + "</div></div>"
    ).join("");
  }
  if (!$("bee-list").dataset.kbBound) {
    $("bee-list").dataset.kbBound = "1";
    $("bee-list").addEventListener("click", async (e) => {
      const btn = e.target.closest(".bee-to-kb");
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      try {
        await api("/api/wxdigest/to-knowledge", { method: "POST",
          body: JSON.stringify({ id: btn.dataset.did }) });
        btn.textContent = t("已转入");
        toast(t("已转入知识库（草稿态，转正后才会注入任务提示词）"));
      } catch (err) {
        btn.disabled = false;
        toast(t("转入失败：") + (err.message || err), true);
      }
    });
  }
}

/* ---- 微信直连：群选择器（347 个群必须可搜索） ---- */
function beeRenderGroups() {
  const box = $("bee-groups-list");
  if (!box) return;
  const q = ($("bee-groups-search").value || "").trim().toLowerCase();
  const cache = (Bee.view && Bee.view.reader_groups_cache) || [];
  const sel = new Set(Bee.selGroups.map((g) => g.username));
  const rows = cache.filter((g) =>
    !q || (g.name || "").toLowerCase().includes(q) || (g.username || "").toLowerCase().includes(q));
  if (!cache.length) {
    box.innerHTML = '<div class="bee-empty">' + esc(t("群列表为空，点「刷新群列表」")) + "</div>";
    return;
  }
  box.innerHTML = rows.map((g) => {
    const on = sel.has(g.username);
    return '<label class="bee-g' + (on ? " on" : "") + '">' +
      '<input type="checkbox" data-gu="' + esc(g.username) + '" data-gn="' + esc(g.name) + '"' + (on ? " checked" : "") + ">" +
      "<span>" + esc(g.name || g.username) + "</span>" +
      (g.member_count ? '<span class="bee-meta">' + esc(g.member_count) + "</span>" : "") +
      "</label>";
  }).join("") || '<div class="bee-empty">' + esc(t("没有匹配的群")) + "</div>";
}

function beeBindGroups() {
  const list = $("bee-groups-list");
  if (!list || list.dataset.beeBound) return;
  list.dataset.beeBound = "1";
  list.addEventListener("change", (e) => {
    const cb = e.target.closest("input[data-gu]");
    if (!cb) return;
    const gu = cb.dataset.gu, gn = cb.dataset.gn || gu;
    const i = Bee.selGroups.findIndex((g) => g.username === gu);
    if (cb.checked && i < 0) Bee.selGroups.push({ username: gu, name: gn });
    if (!cb.checked && i >= 0) Bee.selGroups.splice(i, 1);
    $("bee-groups-btn").textContent = t("选择要监听的群（{0}）", Bee.selGroups.length);
    cb.closest(".bee-g").classList.toggle("on", cb.checked);
  });
}

async function beeRefreshGroups() {
  const btn = $("bee-groups-refresh");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = t("刷新中…");
  try {
    // 后端 refresh_groups 读的是已保存配置的 reader_python；用户选完候选
    // 直接点刷新（不点保存）时服务端还是旧值——先静默落盘再刷新
    // （「填了路径仍报请先填」根因）
    if (!(await beeSaveCore())) { return; }
    const r = await api("/api/wxdigest/groups", { method: "POST", body: "{}", timeout: 180000 });
    if (Bee.view) Bee.view.reader_groups_cache = r.groups || [];
    toast(t("群列表已更新：") + (r.groups || []).length);
  } catch (e) { toast(t("刷新失败：") + (e.message || e), true); }
  finally {
    btn.disabled = false;
    btn.textContent = old;
    beeRenderGroups();
  }
}

async function beeSaveCore() {
  /* 把表单当前值落到服务端；成功返回 true，失败已 toast。不碰勾选态
     （beeSave 的 selGroups 置空依赖随后的 beeRefresh 重建，这里没有）。 */
  try {
    const r = await api("/api/wxdigest/config", { method: "POST", body: JSON.stringify({
      enabled: $("bee-enabled").checked,
      watch_dir: $("bee-dir").value.trim(),
      interval_minutes: Number($("bee-interval").value || 30),
      max_chars: Number($("bee-maxchars").value || 12000),
      reader_enabled: $("bee-reader").checked,
      reader_python: $("bee-reader-py").value.trim(),
      groups: Bee.selGroups || [],
    }) });
    if (Bee.view) Bee.view.config = r.config;
    Bee.pyDirty = false;   // 保存成功：服务端值与所见一致，恢复轮询回填
    return true;
  } catch (e) { toast(t("保存失败：") + (e.message || e), true); return false; }
}

async function beeSave() {
  if (!(await beeSaveCore())) return;
  Bee.selGroups = null;   // 下次渲染用保存后的值重建勾选态
  toast(t("群摘要设置已保存"));
  beeRefresh();
}

async function beeScan() {
  const btn = $("bee-scan-btn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  try {
    // 微信直连拉取依赖 reader_python，先静默落盘（同 beeRefreshGroups）
    if (!(await beeSaveCore())) { return; }
    const r = await api("/api/wxdigest/scan", { method: "POST", body: "{}", timeout: 300000 });
    if (r.ok && r.made) toast(t("扫描完成，生成") + " " + r.made + t(" 条新摘要"));
    else if (r.ok) toast(t("扫描完成，暂无新内容"));
    else toast(t("扫描有问题：") + (r.error || ""), true);
    beeRefresh();
  } catch (e) { toast(t("扫描失败：") + (e.message || e), true); }
  finally { btn.disabled = false; }
}

(function () {
  const boot = () => {
    if (!$("bee-save")) return;
    $("bee-save").addEventListener("click", beeSave);
    $("bee-scan-btn").addEventListener("click", beeScan);
    $("bee-dir-pick").addEventListener("click", () => window.pickFolder("bee-dir", true));
    $("bee-reader").addEventListener("change", () => beeShow($("bee-reader-box"), $("bee-reader").checked));
    $("bee-groups-btn").addEventListener("click", () => {
      const box = $("bee-groups-box");
      const open = box.classList.contains("hidden");
      beeShow(box, open);
      if (!open) return;
      beeBindGroups();
      beeRenderGroups();
      if (!((Bee.view || {}).reader_groups_cache || []).length) beeRefreshGroups();
    });
    $("bee-groups-search").addEventListener("input", beeRenderGroups);
    $("bee-groups-refresh").addEventListener("click", beeRefreshGroups);
    // 64 位 Python 自动扫描 + 一键安装 wechatauto-replica（借鉴用户诉求：
    // 别手填解释器路径、别自己开 conda 装依赖）
    $("bee-py-scan").addEventListener("click", beePyScan);
    $("bee-py-install").addEventListener("click", beePyInstall);
    // 用户手动改过路径（选候选/输入）后置脏：轮询不再回填覆盖
    $("bee-reader-py").addEventListener("input", () => { Bee.pyDirty = true; });
    beeRefresh();
    setInterval(beeRefresh, 60000);
    // 桌宠右键「群摘要」直达：URL 带 #goto=__wxdigest 时切到群摘要子页
    if (/goto=__wxdigest/.test(location.hash)) {
      setTimeout(() => switchTab("__wxdigest"), 800);
      history.replaceState(null, "", location.pathname + location.search);
    }
  };
  // 错开首屏：令牌门/状态首帧先走，群摘要轮询晚 2s 再起
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(boot, 2000));
  } else {
    setTimeout(boot, 2000);
  }
})();

/* ---- 64 位 Python 扫描/一键安装（蜜蜂坞微信直连配置）---- */
let beePyTimer = null;
async function beePyScan() {
  const btn = $("bee-py-scan"), box = $("bee-py-cands");
  if (!btn) return;
  btn.disabled = true;
  try { await api("/api/wxdigest/pythons/scan", { method: "POST", body: "{}" }); }
  catch (e) { toast(t("扫描失败：") + (e.message || e), true); btn.disabled = false; return; }
  box.classList.remove("hidden");
  box.innerHTML = '<span class="hint">' + esc(t("正在扫描本机 Python…")) + "</span>";
  beePyTimer = setInterval(async () => {
    try {
      const st = await api("/api/wxdigest/pythons");
      if (st.scanning) return;
      clearInterval(beePyTimer); beePyTimer = null;
      btn.disabled = false;
      beePyRender(st.results || []);
    } catch (e) { /* 轮询失败下一拍再试 */ }
  }, 1500);
}
function beePyRender(list) {
  const box = $("bee-py-cands"), st = $("bee-py-status"), ib = $("bee-py-install");
  if (!box) return;
  if (!list.length) { box.innerHTML = '<span class="hint">' + esc(t("没有找到可用的 Python，请手动填写路径")) + "</span>"; return; }
  const cur = ($("bee-reader-py").value || "").trim();
  box.innerHTML = list.map((p, idx) => {
    const tag = p.has_replica ? t("已装 wechatauto-replica") : t("未装依赖");
    const cls = "bee-py-item" + (p.path === cur ? " on" : "");
    return '<button type="button" class="' + cls + '" data-i="' + idx + '">' +
      '<span class="bee-py-path">' + esc(p.path) + "</span>" +
      '<span class="bee-py-meta">' + esc("Py" + p.version + " · " + p.bits + "bit · " + tag) + "</span></button>";
  }).join("");
  box.querySelectorAll(".bee-py-item").forEach((b) => {
    b.addEventListener("click", () => {
      const p = list[Number(b.dataset.i)];
      $("bee-reader-py").value = p.path;
      Bee.pyDirty = true;
      box.querySelectorAll(".bee-py-item").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      beePyInstallVisibility(p);
    });
  });
  // 默认选中：当前输入框命中的；否则第一个已装依赖的 64 位
  const hit = list.find((p) => p.path === cur) ||
    list.find((p) => p.bits >= 64 && p.has_replica) ||
    list.find((p) => p.bits >= 64);
  if (hit) {
    $("bee-reader-py").value = hit.path;
    if (hit.path !== cur) Bee.pyDirty = true;
    beePyInstallVisibility(hit);
    const btn = box.querySelector('.bee-py-item[data-i="' + list.indexOf(hit) + '"]');
    if (btn) btn.classList.add("on");
  }
}
function beePyInstallVisibility(p) {
  const ib = $("bee-py-install"), st = $("bee-py-status");
  if (!ib) return;
  const need = p && p.bits >= 64 && !p.has_replica;
  ib.classList.toggle("hidden", !need);
  if (st && !need) st.textContent = "";
}
async function beePyInstall() {
  const py = ($("bee-reader-py").value || "").trim();
  if (!py) { toast(t("请先选择或填写 Python 路径"), true); return; }
  const ib = $("bee-py-install"), st = $("bee-py-status");
  ib.disabled = true;
  if (st) st.textContent = t("正在安装 wechatauto-replica…（约 1-3 分钟）");
  try { await api("/api/wxdigest/replica/install", { method: "POST", body: JSON.stringify({ python: py }) }); }
  catch (e) {
    toast(t("安装失败：") + (e.message || e), true);
    ib.disabled = false;
    return;
  }
  const timer = setInterval(async () => {
    try {
      const s = await api("/api/wxdigest/replica/status");
      if (!s.running) {
        clearInterval(timer); timer = null;
        ib.disabled = false;
        if (s.ok) {
          if (st) st.textContent = t("安装完成，依赖已就绪");
          toast(t("wechatauto-replica 安装完成"));
        } else {
          if (st) st.textContent = (s.log || []).slice(-3).join(" ").slice(0, 200);
          toast(t("安装失败，详见状态行"), true);
        }
        beePyScan();   // 重扫刷新 has_replica 标记
      }
    } catch (e) { /* 轮询失败下一拍再试 */ }
  }, 3000);
}
