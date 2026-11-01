/* ============================================================================
 * borrow-ui.js —— 八开源项目借研落地 UI 覆盖层（2026-11-01）
 * ============================================================================
 * 为什么是独立 overlay：并行会话在持续重写 app.js（同一检出多会话），直接
 * 叠加会被冲掉（memory-center.js 同款教训）。本文件在 app.js 之后加载，
 * 只依赖 app.js 的全局：$, esc, t, api, toast, authHeaders, switchTab, uiConfirm。
 *
 * 承载四件事：
 *   1. 注意力条（dsh-superagent「注意力守恒」）——首页顶部「需要你处理」，
 *      只在有 items 时出现；点击行打开对应运行/任务详情。
 *   2. 审批卡（OpenMausBot Allow/Deny 卡）——高危动作统一内联卡，
 *      window.uiApproveCard({title, detail, allowText, onAllow})；首个消费方
 *      = 磁盘清理。
 *   3. 磁盘清理（Purrge 三段式：扫描→AI 判定→人勾选→先归档）+ 记忆 vault
 *      （Obsidian 本地优先）——设置页注入两个 section。
 *   4. Design Mode（Orca）——浮动按钮开「指点模式」，点任何元素即把
 *      「元素 + 你的要求」填进输入框，用户只管指着说。
 * 样式在 borrow-ui.css（wb token 家族，浅深色自适应）；文案走 t()。 */
(function () {
  "use strict";

  const g = (id) => document.getElementById(id);
  const _t = (s) => (typeof window.t === "function" ? window.t(s) : s);
  const _esc = (s) => (typeof window.esc === "function" ? window.esc(s) : String(s == null ? "" : s));

  async function fetchJSON(path, opts) {
    const o = opts || {};
    const headers = Object.assign(
      {},
      (typeof window.authHeaders === "function" ? window.authHeaders() : null) || {},
      o.body ? { "Content-Type": "application/json" } : {});
    const res = await fetch(path, {
      method: o.method || "GET", headers,
      body: o.body ? JSON.stringify(o.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }

  /* ===================================================== 1. 注意力条 */
  let attTimer = null;
  let attSig = "";

  function attKindLabel(kind) {
    return { failed_run: _t("运行失败"), paused_task: _t("已暂停"),
             stuck_queued: _t("排队滞留"), serial_gap: _t("连载断更"),
             automation_late: _t("定时失联"),
             dead_override: _t("覆盖表未生效") }[kind] || kind;
  }

  function renderAttention(items) {
    const host = document.querySelector("#page-home .rd-home") || g("page-home");
    if (!host) return;
    let bar = g("borrow-attention");
    if (!items || !items.length) {
      if (bar) bar.remove();          // 没事不打扰：空列表连面板都不出现
      return;
    }
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "borrow-attention";
      host.insertBefore(bar, host.firstChild);
    }
    const actBtn = (it) => {
      if (it.kind === "failed_run" && it.task_id) {
        return '<button class="ba-act" data-act="retry" data-task="' + _esc(it.task_id) + '">' + _t("重试") + "</button>";
      }
      if (it.kind === "paused_task" && (it.run_id || it.id)) {
        return '<button class="ba-act" data-act="resume" data-run="' + _esc(it.run_id || it.id) + '">' + _t("恢复") + "</button>";
      }
      if (it.kind === "serial_gap" && it.task_id) {
        return '<button class="ba-act" data-act="retry" data-task="' + _esc(it.task_id) + '">' + _t("补跑") + "</button>";
      }
      if (it.kind === "automation_late" && it.automation_id) {
        return '<button class="ba-act" data-act="run_now" data-auto="' + _esc(it.automation_id) + '">' + _t("立即执行") + "</button>";
      }
      return "";
    };
    const rows = items.slice(0, 5).map((it) => {
      const reason = it.reason ? '<span class="ba-reason">' + _esc(it.reason) + "</span>" : "";
      return '<button class="ba-row" data-task="' + _esc(it.task_id || "") + '" data-run="' +
        _esc(it.run_id || it.id || "") + '" data-kind="' + _esc(it.kind || "") + '">' +
        '<span class="ba-tag ba-' + _esc(it.kind || "other") + '">' + _esc(attKindLabel(it.kind)) + "</span>" +
        '<span class="ba-title">' + _esc(it.title || "") + "</span>" + reason +
        '<span class="ba-time">' + _esc(it.time || "") + "</span>" + actBtn(it) + "</button>";
    }).join("");
    bar.innerHTML = '<div class="ba-card"><div class="ba-head">' +
      _t("需要你处理") + ' · ' + items.length +
      '<button class="ba-close" aria-label="close">×</button></div>' + rows + "</div>";
    const close = bar.querySelector(".ba-close");
    if (close) close.addEventListener("click", (e) => {
      e.stopPropagation();
      bar.remove();
      attSig = "dismissed";   // 本轮轮询不再弹（下次有新 items 会再出现）
    });
    // 真动作：失败→重试 / 暂停→恢复；行点击其余区域=打开运行详情
    bar.querySelectorAll(".ba-act").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        b.disabled = true;
        try {
          if (b.dataset.act === "retry") {
            const ok = await (window.uiConfirm
              ? window.uiConfirm(_t("重新入队这个任务？"), { ok: _t("重试") })
              : Promise.resolve(true));
            if (!ok) { b.disabled = false; return; }
            await fetchJSON("/api/tasks/" + encodeURIComponent(b.dataset.task) + "/retry",
              { method: "POST" });
            if (window.toast) window.toast(_t("已重新入队"));
          } else if (b.dataset.act === "resume") {
            await fetchJSON("/api/runs/" + encodeURIComponent(b.dataset.run) + "/pause",
              { method: "POST", body: { paused: false } });
            if (window.toast) window.toast(_t("已恢复运行"));
          } else if (b.dataset.act === "run_now") {
            const okNow = await (window.uiConfirm
              ? window.uiConfirm(_t("立即执行这个定时任务？（会真实消耗额度）"),
                                 { ok: _t("立即执行") })
              : Promise.resolve(true));
            if (!okNow) { b.disabled = false; return; }
            await fetchJSON("/api/automation/" + encodeURIComponent(b.dataset.auto) + "/run",
              { method: "POST" });
            if (window.toast) window.toast(_t("已加入执行"));
          }
          attSig = "";
          setTimeout(pollAttention, 1200);
        } catch (err) {
          if (window.toast) window.toast(err.message, true);
          b.disabled = false;
        }
      });
    });
    bar.querySelectorAll(".ba-row").forEach((row) => {
      row.addEventListener("click", () => {
        const rid = row.dataset.run;
        if (rid && typeof window.openRun === "function") {
          try { window.openRun(rid); } catch (e) { /* 详情打开失败不打扰 */ }
        }
      });
    });
  }

  async function pollAttention() {
    try {
      const d = await fetchJSON("/api/attention?days=7&limit=20");
      if (!d || !Array.isArray(d.items)) return;
      const sig = d.items.map((x) => x.kind + ":" + (x.id || "")).join("|");
      if (sig === attSig) return;               // 没变化不重画
      if (sig === "dismissed" || attSig === "dismissed") return;
      attSig = sig;
      renderAttention(d.items);
    } catch (e) { /* 静默：注意力面板故障不连累主页 */ }
  }

  function startAttention() {
    if (!g("page-home")) return;
    setTimeout(pollAttention, 2500);            // 首屏稳定后再问一次
    attTimer = setInterval(pollAttention, 90000);
  }

  /* ===================================================== 2. 审批卡 */
  function uiApproveCard(opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      const wrap = document.createElement("div");
      wrap.className = "borrow-approve-mask";
      wrap.innerHTML = '<div class="borrow-approve" role="alertdialog" aria-modal="true">' +
        '<div class="bp-title">' + _esc(opts.title || _t("高危操作确认")) + "</div>" +
        (opts.detail ? '<div class="bp-detail">' + _esc(opts.detail) + "</div>" : "") +
        '<div class="bp-actions">' +
        '<button class="bp-deny">' + _esc(opts.denyText || _t("取消")) + "</button>" +
        '<button class="bp-allow">' + _esc(opts.allowText || _t("允许")) + "</button></div></div>";
      const done = (ok) => {
        wrap.remove();
        document.removeEventListener("keydown", onKey, true);
        resolve(ok);
      };
      const onKey = (e) => { if (e.key === "Escape") done(false); };
      document.addEventListener("keydown", onKey, true);
      wrap.querySelector(".bp-allow").addEventListener("click", () => done(true));
      wrap.querySelector(".bp-deny").addEventListener("click", () => done(false));
      wrap.addEventListener("click", (e) => { if (e.target === wrap) done(false); });
      document.body.appendChild(wrap);
      wrap.querySelector(".bp-allow").focus();
    });
  }
  window.uiApproveCard = uiApproveCard;

  /* ===================================================== 3a. 磁盘清理 */
  function diskSectionHTML() {
    return '<details class="borrow-disk" id="borrow-disk-box"><summary>' +
      _t("AI 磁盘清理（实验）") + "</summary>" +
      '<p class="hint">' + _t("扫描 → AI 逐项判定能清什么 → 你勾选确认后才清理。清理不是删除：先整目录移进 data/trash，可一键还原。") + "</p>" +
      '<div class="bd-actions">' +
      '<button class="ghost small" id="bd-scan">' + _t("扫描") + "</button>" +
      '<button class="ghost small" id="bd-judge" disabled>' + _t("AI 判定") + "</button>" +
      '<button class="ghost small" id="bd-clean" disabled>' + _t("清理勾选项") + "</button>" +
      '<button class="ghost small" id="bd-trash">' + _t("回收站") + "</button></div>" +
      '<div id="bd-out" class="bd-out"></div></details>';
  }

  function diskRow(it) {
    const badge = {
      cleanable: '<span class="bd-badge bd-ok">' + _t("可清") + "</span>",
      review: '<span class="bd-badge bd-mid">' + _t("看一眼") + "</span>",
      never: '<span class="bd-badge bd-no">' + _t("不动") + "</span>",
      skip: "",
    }[it.heuristic] || "";
    const ai = it.ai_verdict && it.ai_verdict !== it.heuristic
      ? ' <span class="bd-ai">' + _t("AI:") + _esc(it.ai_verdict) +
        (it.ai_reason ? " · " + _esc(it.ai_reason) : "") + "</span>" : "";
    const checkable = it.kind === "logs" || it.kind === "review";
    return '<label class="bd-row"><input type="checkbox" data-path="' + _esc(it.path) + '"' +
      (checkable ? "" : " disabled") + "> " +
      "<b>" + _esc(it.path) + "</b> " + badge +
      '<span class="bd-size">' + _esc(it.size_human) + " · " + it.files + " " + _t("个文件") +
      (it.last_write ? " · " + _esc(it.last_write) : "") + "</span>" + ai + "</label>";
  }

  function bindDisk() {
    const box = g("borrow-disk-box");
    if (!box) return;
    const out = box.querySelector("#bd-out");
    const btnScan = box.querySelector("#bd-scan");
    const btnJudge = box.querySelector("#bd-judge");
    const btnClean = box.querySelector("#bd-clean");
    const btnTrash = box.querySelector("#bd-trash");
    let lastScan = null;

    const render = (data) => {
      lastScan = data;
      out.innerHTML = (data.total_human ? '<div class="bd-total">' +
        _t("数据目录合计") + " " + _esc(data.total_human) + "</div>" : "") +
        (data.items || []).map(diskRow).join("") +
        '<div class="bd-note">' + _t("tasks / publish / models 等活数据永不进入清单。") + "</div>";
      btnJudge.disabled = !(data.items || []).length;
      btnClean.disabled = true;
      out.querySelectorAll('input[type="checkbox"]').forEach((c) => {
        c.addEventListener("change", () => {
          btnClean.disabled = !out.querySelector('input:checked');
        });
      });
    };

    btnScan.addEventListener("click", async () => {
      out.innerHTML = '<span class="bd-wait">' + _t("扫描中…") + "</span>";
      try { render(await fetchJSON("/api/maintenance/disk")); }
      catch (e) { out.innerHTML = '<span class="bd-err">' + _esc(e.message) + "</span>"; }
    });
    btnJudge.addEventListener("click", async () => {
      out.innerHTML = '<span class="bd-wait">' + _t("AI 判定中…（无模型时回落启发式）") + "</span>";
      try { render(await fetchJSON("/api/maintenance/disk/judge", { method: "POST" })); }
      catch (e) { out.innerHTML = '<span class="bd-err">' + _esc(e.message) + "</span>"; }
    });
    btnClean.addEventListener("click", async () => {
      const paths = Array.from(out.querySelectorAll("input:checked"))
        .map((c) => c.dataset.path);
      if (!paths.length) return;
      const ok = await uiApproveCard({
        title: _t("确认清理这些目录？"),
        detail: paths.join("、") + " → " + _t("移入 data/trash（可还原）"),
        allowText: _t("清理"),
      });
      if (!ok) return;
      try {
        const res = await fetchJSON("/api/maintenance/disk/clean",
          { method: "POST", body: { paths } });
        const msg = (res.moved || []).length
          ? _t("已移入回收站：") + res.moved.join("、")
          : _t("没有可移动项");
        const bad = (res.refused || []).map((r) => r.path + "（" + r.reason + "）").join("；");
        if (typeof window.toast === "function") window.toast(msg + (bad ? " / " + bad : ""), !!bad);
        render(await fetchJSON("/api/maintenance/disk"));
      } catch (e) {
        if (typeof window.toast === "function") window.toast(e.message, true);
      }
    });
    btnTrash.addEventListener("click", async () => {
      try {
        const d = await fetchJSON("/api/maintenance/disk/trash");
        const rows = (d.entries || []).map((e) =>
          '<div class="bd-row bd-trash-row"><b>' + _esc(e.name) + "</b> " +
          '<span class="bd-size">' + _esc(e.size_human) + " · " + e.items + " " + _t("项") +
          '</span><button class="ghost small" data-batch="' + _esc(e.name) + '">' +
          _t("还原") + "</button></div>").join("");
        out.innerHTML = rows || '<span class="bd-note">' + _t("回收站是空的") + "</span>";
        out.querySelectorAll("[data-batch]").forEach((b) => {
          b.addEventListener("click", async () => {
            try {
              await fetchJSON("/api/maintenance/disk/restore",
                { method: "POST", body: { batch: b.dataset.batch } });
              if (typeof window.toast === "function") window.toast(_t("已还原"));
              btnScan.click();
            } catch (e2) {
              if (typeof window.toast === "function") window.toast(e2.message, true);
            }
          });
        });
      } catch (e) {
        out.innerHTML = '<span class="bd-err">' + _esc(e.message) + "</span>";
      }
    });
  }

  /* ===================================================== 3b. 记忆 vault */
  function vaultSectionHTML() {
    return '<details class="borrow-disk" id="borrow-vault-box"><summary>' +
      _t("记忆 MD 库（实验）") + "</summary>" +
      '<p class="hint">' + _t("把项目记忆导出成一份 Obsidian 式 MD 库（data/memory-vault/），可用任何编辑器手改；「回流」把手改通过版本追加写回记忆日志，版本冲突会如实拦下。") + "</p>" +
      '<div class="bd-actions">' +
      '<button class="ghost small" id="mv-export">' + _t("导出/刷新") + "</button>" +
      '<button class="ghost small" id="mv-status">' + _t("状态") + "</button>" +
      '<button class="ghost small" id="mv-sync">' + _t("回流手改") + "</button></div>" +
      '<div id="mv-out" class="bd-out"></div></details>';
  }

  function bindVault() {
    const box = g("borrow-vault-box");
    if (!box) return;
    const out = box.querySelector("#mv-out");
    box.querySelector("#mv-export").addEventListener("click", async () => {
      out.innerHTML = '<span class="bd-wait">' + _t("导出中…") + "</span>";
      try {
        const d = await fetchJSON("/api/memory/vault/export", { method: "POST" });
        out.innerHTML = _t("已导出") + " " + d.entries + " " + _t("条 · ") +
          '<span class="bd-size">' + _esc(d.dir) + "</span>";
      } catch (e) { out.innerHTML = '<span class="bd-err">' + _esc(e.message) + "</span>"; }
    });
    box.querySelector("#mv-status").addEventListener("click", async () => {
      try {
        const d = await fetchJSON("/api/memory/vault");
        out.innerHTML = d.projects && d.projects.length
          ? d.projects.map((p) => "<b>" + _esc(p.project) + "</b> · " + p.entries + " " + _t("条")).join("<br>")
          : '<span class="bd-note">' + _t("尚未导出") + "</span>";
      } catch (e) { out.innerHTML = '<span class="bd-err">' + _esc(e.message) + "</span>"; }
    });
    box.querySelector("#mv-sync").addEventListener("click", async () => {
      try {
        const d = await fetchJSON("/api/memory/vault/sync",
          { method: "POST", body: { dry_run: false } });
        const parts = [];
        if (d.updated && d.updated.length) parts.push(_t("已回流") + " " + d.updated.length + " " + _t("条"));
        if (d.unchanged) parts.push(_t("无变化") + " " + d.unchanged);
        if (d.conflicts && d.conflicts.length) parts.push(_t("冲突") + " " + d.conflicts.length + _t("（先重新导出再改）"));
        if (d.skipped) parts.push(_t("跳过") + " " + d.skipped);
        if (typeof window.toast === "function") {
          window.toast(parts.join(" · ") || _t("vault 是空的"), !!(d.conflicts || []).length);
        }
      } catch (e) {
        if (typeof window.toast === "function") window.toast(e.message, true);
      }
    });
  }

  /* ===================================================== 3c. 专家 MD 导入/导出 */
  function expertModalHTML() {
    return '<div class="borrow-approve-mask" id="borrow-expert-mask">' +
      '<div class="borrow-approve borrow-expert" role="dialog" aria-modal="true">' +
      '<div class="bp-title">' + _t("专家 · Markdown 导入/导出") + "</div>" +
      '<div class="bx-row"><label>' + _t("导出") + "</label>" +
      '<select id="bx-flow"></select>' +
      '<button class="ghost small" id="bx-export-btn">' + _t("下载 .md") + "</button></div>" +
      '<div class="bx-row"><label>' + _t("导入") + "</label>" +
      '<textarea id="bx-md" rows="8" placeholder="粘贴专家 Markdown（frontmatter: name/flow/focus/...）"></textarea></div>' +
      '<div class="bx-row bx-foot"><label class="bx-ov"><input type="checkbox" id="bx-overwrite"> ' + _t("允许覆盖同名") + "</label>" +
      '<button class="ghost small" id="bx-import-btn">' + _t("导入") + "</button>" +
      '<button class="ghost small" id="bx-close">' + _t("关闭") + "</button></div>" +
      '<div id="bx-out" class="bd-out"></div></div></div>';
  }

  async function openExpertModal() {
    if (g("borrow-expert-mask")) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = expertModalHTML();
    document.body.appendChild(wrap.firstElementChild);
    const mask = g("borrow-expert-mask");
    const out = mask.querySelector("#bx-out");
    try {
      const d = await fetchJSON("/api/agents?all=1");
      mask.querySelector("#bx-flow").innerHTML = (d.agents || [])
        .map((a) => '<option value="' + _esc(a.flow) + '">' + _esc(a.name || a.flow) + "</option>")
        .join("");
    } catch (e) { /* 下拉留空，导入仍可用 */ }
    mask.querySelector("#bx-close").addEventListener("click", () => mask.remove());
    mask.addEventListener("click", (e) => { if (e.target === mask) mask.remove(); });
    mask.querySelector("#bx-export-btn").addEventListener("click", async () => {
      const fid = mask.querySelector("#bx-flow").value;
      if (!fid) return;
      try {
        const d = await fetchJSON("/api/experts/" + encodeURIComponent(fid) + "/export");
        out.innerHTML = '<textarea class="bx-md-view" rows="8" readonly></textarea>';
        out.querySelector("textarea").value = d.markdown || "";
      } catch (e2) { out.innerHTML = '<span class="bd-err">' + _esc(e2.message) + "</span>"; }
    });
    mask.querySelector("#bx-import-btn").addEventListener("click", async () => {
      const md = mask.querySelector("#bx-md").value.trim();
      if (!md) { out.innerHTML = '<span class="bd-note">' + _t("先粘贴 Markdown") + "</span>"; return; }
      try {
        const d = await fetchJSON("/api/experts/import", {
          method: "POST",
          body: { markdown: md, overwrite: mask.querySelector("#bx-overwrite").checked },
        });
        out.innerHTML = _t("已导入：") + _esc(d.flow) +
          (d.flow_exists ? "" : _t("（flows 无同名流程，先建流程才能真正起任务）"));
        if (typeof window.toast === "function") window.toast(_t("专家已导入"));
      } catch (e2) { out.innerHTML = '<span class="bd-err">' + _esc(e2.message) + "</span>"; }
    });
  }

  function bindExperts() {
    const host = g("sub-agents");
    if (!host || g("btn-experts-md")) return;
    const head = host.querySelector(".panel-head .actions") || host.querySelector(".panel-head");
    if (!head) return;
    const btn = document.createElement("button");
    btn.id = "btn-experts-md";
    btn.className = "ghost";
    btn.textContent = _t("专家 MD 导入/导出");
    btn.addEventListener("click", openExpertModal);
    head.appendChild(btn);
  }

  /* ===================================================== 4. Design Mode */
  let dmOn = false;
  let dmPill = null;

  function dmPath(el) {
    const parts = [];
    let node = el;
    while (node && node !== document.body && parts.length < 4) {
      let sel = node.tagName ? node.tagName.toLowerCase() : "";
      if (node.id) sel += "#" + node.id;
      else if (node.className && typeof node.className === "string") {
        const c = node.className.trim().split(/\s+/)[0];
        if (c) sel += "." + c;
      }
      parts.unshift(sel);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function dmToggle(force) {
    dmOn = force !== undefined ? force : !dmOn;
    if (dmPill) dmPill.classList.toggle("dm-on", dmOn);
    document.body.classList.toggle("borrow-designmode", dmOn);
    if (dmOn && typeof window.toast === "function") {
      window.toast(_t("指点模式：点界面上的任何元素，把「这里 + 你的要求」填进输入框；Esc 退出"));
    }
  }

  function bindDesignMode() {
    dmPill = document.createElement("button");
    dmPill.id = "borrow-dm";
    dmPill.type = "button";
    dmPill.title = _t("指点模式（Design Mode）");
    dmPill.textContent = "🎯 " + _t("指点");
    dmPill.addEventListener("click", () => dmToggle());
    document.body.appendChild(dmPill);

    document.addEventListener("mouseover", (e) => {
      if (!dmOn || !e.target || e.target === document.body) return;
      e.target.classList.add("dm-hover");
    }, true);
    document.addEventListener("mouseout", (e) => {
      if (e.target) e.target.classList.remove("dm-hover");
    }, true);
    document.addEventListener("click", (e) => {
      if (!dmOn) return;
      const el = e.target.closest ? e.target : null;
      if (!el || el.closest("#borrow-dm")) return;
      e.preventDefault();
      e.stopPropagation();
      const text = (el.textContent || "").trim().slice(0, 40);
      const path = dmPath(el);
      dmToggle(false);
      const inp = g("f-goal");
      const prefix = _t("请把这里的「") + (text || path) + _t("」改进：");
      const suffix = _t("（元素：") + path + "）";
      if (inp) {
        inp.value = inp.value ? inp.value : prefix + " " + suffix;
        inp.focus();
      }
      if (typeof window.toast === "function") {
        window.toast(_t("已把元素填进输入框，补上你的要求再发送"));
      }
    }, true);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && dmOn) dmToggle(false);
    });
  }

  /* ===================================================== 5. 额度上限编辑（通道体检页注入） */
  function quotaSectionHTML() {
    return '<details class="borrow-disk" id="borrow-quota-box"><summary>' +
      _t("额度节流（实验）") + "</summary>" +
      '<p class="hint">' + _t("按供应商配置 RPM/RPD/TPM/TPD 上限；派单前节流，已用尽的通道自动让位（0=不限）。消耗来自用量台账。") + "</p>" +
      '<div class="bd-actions"><button class="ghost small" id="bq-load">' + _t("加载") + "</button></div>" +
      '<div id="bq-out" class="bd-out"></div></details>';
  }

  function quotaRow(r) {
    const dims = ["rpm", "rpd", "tpm", "tpd"];
    const inputs = dims.map((d) => {
      const lim = (r.limits || {})[d] || 0;
      return '<label class="bq-dim">' + d.toUpperCase() +
        ' <span class="bd-size">' + ((r.used || {})[d] || 0) + "/</span>" +
        '<input type="number" min="0" step="1" value="' + (lim || "") + '" placeholder="0" data-dim="' + d + '"></label>';
    }).join("");
    const th = r.throttled ? ' <span class="bd-badge bd-mid">' + _t("已节流") + "</span>" : "";
    return '<div class="bd-row bq-row" data-pid="' + _esc(r.provider || "") + '">' +
      '<b>' + _esc(r.name || r.provider || "") + "</b>" + th +
      '<span class="bq-dims">' + inputs + "</span>" +
      '<button class="ghost small" data-save="1">' + _t("保存") + "</button></div>";
  }

  function bindQuota(box) {
    const out = box.querySelector("#bq-out");
    box.querySelector("#bq-load").addEventListener("click", async () => {
      out.innerHTML = '<span class="bd-wait">' + _t("加载中…") + "</span>";
      try {
        const d = await fetchJSON("/api/quota");
        const rows = (d.providers || []);
        out.innerHTML = rows.length ? rows.map(quotaRow).join("")
          : '<span class="bd-note">' + _t("暂无供应商用量——先跑一次任务再来") + "</span>";
        out.querySelectorAll("[data-save]").forEach((b) => {
          b.addEventListener("click", async () => {
            const row = b.closest(".bq-row");
            const limits = {};
            row.querySelectorAll("input[data-dim]").forEach((inp) => {
              const v = parseFloat(inp.value);
              if (v > 0) limits[inp.dataset.dim] = v;
            });
            b.disabled = true;
            try {
              await fetchJSON("/api/quota", { method: "POST",
                body: { provider_id: row.dataset.pid, limits } });
              if (window.toast) window.toast(_t("已保存") + "：" + row.dataset.pid);
            } catch (e) {
              if (window.toast) window.toast(e.message, true);
            }
            b.disabled = false;
          });
        });
      } catch (e) {
        out.innerHTML = '<span class="bd-err">' + _esc(e.message) + "</span>";
      }
    });
  }

  function injectQuotaSection() {
    const host = g("sub-availability");
    if (!host || g("borrow-quota-box")) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = quotaSectionHTML();
    const box = wrap.firstElementChild;
    host.appendChild(box);
    bindQuota(box);
  }

  function wrapAvailability() {
    const orig = window.renderAvailability;
    if (typeof orig !== "function" || orig.__borrowWrapped) return;
    const wrapped = function (data) {
      try { return orig(data); }
      finally { try { injectQuotaSection(); } catch (e) { /* 静默 */ } }
    };
    wrapped.__borrowWrapped = true;
    window.renderAvailability = wrapped;
  }

  /* ===================================================== boot */
  function boot() {
    try { startAttention(); } catch (e) { /* 静默 */ }
    try { bindDisk(); } catch (e) { /* 静默 */ }
    try { bindVault(); } catch (e) { /* 静默 */ }
    try { bindExperts(); } catch (e) { /* 静默 */ }
    try { bindDesignMode(); } catch (e) { /* 静默 */ }
    try { wrapAvailability(); } catch (e) { /* 静默 */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
