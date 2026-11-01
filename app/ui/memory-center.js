/* ============================================================================
 * 记忆中心 v3 覆盖层（memory-center.js，2026-10-30）
 * ============================================================================
 * 为什么是独立文件：并行会话在持续重写 app/ui/app.js（同一检出多会话），
 * 直接在 app.js 里叠加的记忆中心 v3 代码三次被覆盖回旧版。本文件在
 * app.js 之后加载，用同名函数/变量覆盖旧实现：
 *   - window.loadMemoryCenter：先渲染确定性四段总结，再异步拉
 *     /api/memory/summary（LLM 生成）覆盖；拉不到保持确定性总结。
 *   - renderMemoryCenter：默认态 = 四段散文（工作背景/个人
 *     背景/当前关注/近期动态，h4 粗体小标题 + p 段落 + ul 圆点列表）；
 *     编辑态 = 原始治理列表（批准/拒绝/归档/恢复）。
 *   - memorySetSwitch / memoryToggleEdit / memoryUiState 等开关与编辑态。
 * 依赖（由 app.js 提供，本文件不重复定义）：$, esc, t, api, authHeaders,
 * toast, jsq, S, MEMORY_CENTER, memoryCenterSignature, memoryStatusLabel,
 * memoryCenterAction, getLang。
 * 排版样式在 settings-v3.css「记忆中心 v3」段；i18n 词条在 i18n.js。 */
(function () {
  "use strict";

  const g = (id) => document.getElementById(id);

  /* —— 内容解析辅助 —— */

  function facts(content) {
    const map = {};
    String(content || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean).forEach((line) => {
      const m = line.match(/^[-*•]\s*(.+)$/);
      const body = m ? m[1] : line;
      const kv = body.match(/^([^：:]{1,24})[：:]\s*(.+)$/);
      if (kv && !(kv[1] in map)) map[kv[1]] = kv[2].trim();
    });
    return map;
  }

  function factRows(content) {
    return String(content || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean).map((line) => {
      const m = line.match(/^[-*•]\s*(.+)$/);
      const body = m ? m[1] : line;
      const kv = body.match(/^([^：:]{1,24})[：:]\s*(.+)$/);
      return kv
        ? '<div class="mem-fact"><b>' + esc(kv[1]) + '：</b><span>' + esc(kv[2]) + '</span></div>'
        : '<div class="mem-fact">' + esc(body) + '</div>';
    }).join("");
  }

  function entryTime(ts) {
    const t = Number(ts || 0);
    if (!t) return "";
    try {
      const d = new Date(t * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
    } catch (e) { return ""; }
  }

  /* —— 确定性四段总结（离线回落，不编造）—— */

  function summaryHtml(data) {
    const projects = data.projects || [];
    const all = [];
    projects.forEach((p) => (p.entries || []).forEach((e) => all.push(Object.assign({ _project: p.name || "" }, e))));
    const sorted = all.slice().sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    const lang = (typeof getLang === "function" && getLang() === "en") ? "en" : "zh";

    let work;
    if (!projects.length) {
      work = "<p>" + esc(t("暂无工作背景——完成一次产出型任务后会自动沉淀。")) + "</p>";
    } else {
      work = projects.map((p) => {
        const entries = p.entries || [];
        const approved = entries.filter((e) => e.status === "approved").length;
        const goals = entries.map((e) => e.title || "").filter(Boolean);
        const kinds = {};
        entries.forEach((e) => (kinds[(e.kind || t("项目记忆"))] = true));
        const kindText = Object.keys(kinds).slice(0, 3).join("、");
        const goalText = goals.slice(0, 2).join("；");
        let line = esc(t("在「{0}」开展任务，沉淀 {1} 条记忆", p.name || t("项目记忆"), approved || entries.length));
        if (kindText) line += esc(t("，涉及 {0}", kindText));
        if (goalText) line += esc(t("；近期的目标：{0}", goalText));
        return "<p>" + line + "。</p>";
      }).join("");
    }

    let level = t("简单"), theme = t("浅色");
    try {
      level = localStorage.getItem("orch.ui-level") === "advanced" ? t("高级") : t("简单");
      theme = document.documentElement.getAttribute("data-theme") === "dark" ? t("深色") : t("浅色");
    } catch (e) { /* private browsing */ }
    const personal =
      "<p>" + esc(t("界面语言 {0}，默认{1}设置档，当前{2}主题。", lang === "en" ? "English" : "中文", level, theme)) +
      esc(t("记忆保存在本机 data/ 目录，仅你本人可见。")) + "</p>";

    const latest = sorted.find((e) => e.status === "approved") || sorted[0] || null;
    let focus;
    if (!latest) {
      focus = "<p>" + esc(t("暂无进行中的关注——创建任务后，这里会显示最近在做的事。")) + "</p>";
    } else {
      const f = facts(latest.content);
      const goal = f["任务目标"] || f["目标"] || "";
      let line = goal
        ? t("正在推进「{0}」：{1}", latest.title || t("项目记忆"), goal)
        : t("正在推进「{0}」", latest.title || t("项目记忆"));
      if (latest._project) line += t("（{0}）", latest._project);
      focus = "<p>" + esc(line) + "</p>";
    }

    const recent = sorted.slice(0, 6);
    const dyn = recent.length
      ? "<ul>" + recent.map((e) => {
          const f = facts(e.content);
          const goal = f["任务目标"] || f["目标"] || "";
          const ts = entryTime(e.created_at);
          const head = goal || e.title || t("项目记忆");
          return "<li>" + esc(head) + (ts ? " <i>" + esc(t("（{0}）", ts)) + "</i>" : "") + "</li>";
        }).join("") + "</ul>"
      : "<p>" + esc(t("暂无近期动态。")) + "</p>";

    return (
      '<section class="mem-sec mem-summary"><h4>' + esc(t("工作背景")) + '</h4>' + work + '</section>' +
      '<section class="mem-sec mem-summary"><h4>' + esc(t("个人背景")) + '</h4>' + personal + '</section>' +
      '<section class="mem-sec mem-summary"><h4>' + esc(t("当前关注")) + '</h4>' + focus + '</section>' +
      '<section class="mem-sec mem-summary"><h4>' + esc(t("近期动态")) + '</h4>' + dyn + '</section>'
    );
  }

  /* —— LLM 四段文本 → 同构 HTML（解析失败返回 null）—— */

  function fromText(text, lang) {
    const headers = lang === "en"
      ? ["Work background", "Personal background", "Current focus", "Recent activity"]
      : ["工作背景", "个人背景", "当前关注", "近期动态"];
    const norm = (ln) => String(ln).replace(/^[#*\s]+/, "").replace(/[#*\s]+$/, "").replace(/[:：]+$/, "").trim();
    const sections = [];
    let cur = null;
    String(text || "").split(/\r?\n/).forEach((ln) => {
      const h = headers.indexOf(norm(ln));
      if (h !== -1) { cur = { title: headers[h], body: [] }; sections.push(cur); }
      else if (cur) cur.body.push(ln);
    });
    if (!sections.length) return null;
    return sections.map((sec) => {
      const paras = [], items = [];
      let buf = [];
      const flush = () => { if (buf.length) { paras.push(buf.join(" ").trim()); buf = []; } };
      sec.body.forEach((b) => {
        const s = b.trim();
        if (!s) { flush(); return; }
        const m = s.match(/^[-*•]\s+(.+)$/);
        if (m) { flush(); items.push(m[1]); } else buf.push(s);
      });
      flush();
      let inner = paras.map((p) => "<p>" + esc(p) + "</p>").join("");
      if (items.length) inner += "<ul>" + items.map((it) => "<li>" + esc(it) + "</li>").join("") + "</ul>";
      if (!inner) inner = "<p>" + esc(t("暂无")) + "</p>";
      return '<section class="mem-sec mem-summary"><h4>' + esc(sec.title) + '</h4>' + inner + '</section>';
    }).join("");
  }

  /* —— 开关（localStorage + 服务端双写）—— */

  const UI_KEY = "memory.ui.switches.v1";
  function uiState() {
    let ui = { conv: true, local: true };
    try { Object.assign(ui, JSON.parse(localStorage.getItem(UI_KEY) || "{}")); } catch (e) {}
    ui.conv = ui.conv !== false;
    ui.local = ui.local !== false;
    return ui;
  }
  function applyUi() {
    const ui = uiState();
    document.body.classList.toggle("mem-conv-off", !ui.conv);
    document.body.classList.toggle("mem-local-off", !ui.local);
  }
  function adoptServer(s) {
    if (!s || typeof s !== "object") return;
    const merged = { conv: s.memory_conversation !== false, local: s.memory_local_skills !== false };
    try { localStorage.setItem(UI_KEY, JSON.stringify(merged)); } catch (e) {}
    applyUi();
  }
  async function syncFromServer() {
    try {
      const data = await api("/api/memory/settings");
      adoptServer((data || {}).settings);
    } catch (e) {}
  }

  window.memorySetSwitch = async function (which, on) {
    const ui = uiState();
    ui[which] = !!on;
    try { localStorage.setItem(UI_KEY, JSON.stringify(ui)); } catch (e) {}
    applyUi();
    render();
    try {
      const data = await api("/api/memory/settings", { method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ conversation: ui.conv, local: ui.local }) });
      adoptServer((data || {}).settings);
      render();
    } catch (e) {}
  };

  window.memoryToggleEdit = function () {
    MEMORY_CENTER.editMode = !MEMORY_CENTER.editMode;
    const btn = g("mem-edit-btn");
    if (btn) btn.textContent = MEMORY_CENTER.editMode ? t("完成") : t("编辑");
    render();
  };

  /* —— 渲染：默认四段总结 / 编辑态治理列表 —— */

  function render() {
    const data = MEMORY_CENTER.data || {};
    const ui = uiState();
    const sc = g("mem-sw-conv"), sl = g("mem-sw-local");
    if (sc) sc.checked = ui.conv;
    if (sl) sl.checked = ui.local;
    const doc = g("mem-doc");
    if (!doc) return;
    const editMode = MEMORY_CENTER.editMode === true;
    doc.classList.toggle("is-edit", editMode);
    if (!editMode) {
      const lang = (typeof getLang === "function" && getLang() === "en") ? "en" : "zh";
      const llm = (MEMORY_CENTER.summary && MEMORY_CENTER.summary.text && String(MEMORY_CENTER.summary.text).trim())
        ? fromText(MEMORY_CENTER.summary.text, lang) : null;
      doc.innerHTML = llm || summaryHtml(data);
      return;
    }
    const global = data.global || {};
    const skills = global.skills || {};
    const knowledge = global.knowledge || {};
    const projects = data.projects || [];
    const parts = [];
    parts.push('<section class="mem-sec"><h4>' + esc(t("全局沉淀")) + '</h4><p>' +
      esc(t("经验库 {0} 条 · 知识库 {1} 条，随同类任务自动注入。",
        Number(skills.total || skills.lessons || 0),
        Number(knowledge.total || (knowledge.entries || []).length || 0))) + '</p></section>');
    if (!ui.conv) {
      parts.push('<section class="mem-sec"><h4>' + esc(t("项目记忆")) + '<span class="chip">' + esc(t("已关闭")) + '</span></h4><p>' +
        esc(t("对话记忆已关闭：任务详情不再展示记忆治理面板，已有记忆原样保留。")) + '</p></section>');
    } else if (!projects.length) {
      parts.push('<section class="mem-sec"><h4>' + esc(t("项目记忆")) + '</h4><p>' +
        esc(t("完成任务后，这里会按项目（工作区）自动沉淀记忆。")) + '</p></section>');
    } else {
      projects.forEach((project) => {
        const taskId = (project.task_ids || [])[0] || "";
        const entries = project.entries || [];
        const pending = entries.filter((e) => e.status === "pending" && !e.expired && e.verified).length;
        const rows = entries.map((entry) => {
          const ver = Number(entry.version || 1);
          const canApprove = entry.status === "pending" && !entry.expired && entry.verified;
          const canRestore = entry.status === "archived" && !entry.expired && entry.verified;
          const act = (name, label) => '<button type="button" class="ghost small" onclick="memoryCenterAction(\'' + jsq(taskId) + '\',\'' + jsq(entry.id) + '\',\'' + name + '\',' + ver + ')">' + esc(t(label)) + '</button>';
          const actions = (canApprove ? act("approve", "批准") + act("reject", "拒绝") : "") +
            (entry.status === "approved" ? act("archive", "归档") : "") +
            (canRestore ? act("restore", "恢复") : "") +
            (entry.status === "approved" ? act("preview", "查看注入预览") : "");
          const ts = entryTime(entry.created_at || entry.updated_at);
          return '<section class="mem-sec mem-entry"><h4>' + esc(entry.title || t("项目记忆")) +
            '<span class="chip">' + esc(memoryStatusLabel(entry.status)) + (entry.expired ? " · " + esc(t("已过期")) : "") + '</span></h4>' +
            (ts ? '<div class="mem-entry-meta">' + esc(t("沉淀于 {0}", ts)) + '</div>' : "") +
            '<div class="mem-facts">' + factRows(entry.content) + '</div>' +
            (actions ? '<div class="rd-memory-actions">' + actions + '</div>' : "") + '</section>';
        }).join("");
        parts.push('<section class="mem-sec"><h4>' + esc(project.name || t("项目记忆")) +
          '<span class="chip">' + esc(t("{0} 条记忆", entries.length)) + '</span></h4>' +
          (rows || '<p>' + esc(t("暂无项目记忆")) + '</p>') +
          (pending ? '<div class="rd-memory-actions"><button type="button" class="ghost small" onclick="memoryCenterAction(\'' + jsq(taskId) + '\',\'\',\'approve_all\',0)">' + esc(t("一键批准全部待批准 ({0})", pending)) + '</button></div>' : "") +
          '</section>');
      });
    }
    doc.innerHTML = parts.join("");
  }

  /* —— 覆盖 loadMemoryCenter：确定性总结先上屏，LLM 总结异步覆盖 —— */

  window.loadMemoryCenter = async function () {
    const requestSeq = MEMORY_CENTER.requestSeq = (MEMORY_CENTER.requestSeq || 0) + 1;
    MEMORY_CENTER.stateSignature = memoryCenterSignature();
    const doc = g("mem-doc");
    if (doc) doc.innerHTML = '<div class="hint">' + esc(t("正在加载记忆…")) + '</div>';
    let data = null;
    try {
      data = await api("/api/memory");
    } catch (e) {
      if (requestSeq !== MEMORY_CENTER.requestSeq) return;
      MEMORY_CENTER.stateSignature = "";
      MEMORY_CENTER.data = null;
      if (doc) doc.innerHTML = '<div class="hint">' + esc(e.message) + '</div>';
      return;
    }
    if (requestSeq !== MEMORY_CENTER.requestSeq) return;
    MEMORY_CENTER.data = data;
    render();
    try {
      const lang = (typeof getLang === "function" && getLang() === "en") ? "en" : "zh";
      const sum = await api("/api/memory/summary?lang=" + encodeURIComponent(lang), { timeout: 130000 });
      if (requestSeq !== MEMORY_CENTER.requestSeq) return;
      MEMORY_CENTER.summary = (sum && sum.text && String(sum.text).trim()) ? sum : null;
      render();
    } catch (e) { /* 离线 / 无模型 / 超时：保持确定性总结 */ }
  };

  // 挂到 window 供 app.js 内部的调用点走新实现（app.js 里 loadMemoryCenter/
  // renderMemoryCenter 的旧定义只在其内部互相引用时生效；on* 属性与外部
  // 调用都会解析到 window.*，即本文件版本）。另外把内部 render 也暴露成
  // 全局 renderMemoryCenter，保证 app.js 内部 switchTab('memory') 的直呼
  // 也拿到新渲染。
  window.renderMemoryCenter = render;
  applyUi();
  syncFromServer();
})();
