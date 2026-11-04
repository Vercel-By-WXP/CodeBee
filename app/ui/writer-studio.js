/* ============================================================================
 * 写手工坊覆盖层（writer-studio.js，2026-11-05）
 * ============================================================================
 * 为什么是独立文件：DESIGN.md §9 落点优先级——新 overlay 文件 > 独立 CSS >
 * 直接改 app.js（共享检出多会话，app.js 是高风险区）。本文件在 app.js 之后
 * 加载，包一层 window.loadStrategyLab：打开「机制与策略」子页时顺带拉写手
 * 槽位数据，不改 app.js 一行。
 * 依赖（由 app.js / i18n.js 提供，本文件不重复定义）：$, esc, t, api, toast。
 * 后端：GET/POST /api/writer-slot、GET/POST /api/writer-dataset（main.py）。
 * ============================================================================ */
(function () {
  "use strict";

  const g = (id) => document.getElementById(id);
  let WS = null;          // { slot, shadow_report }
  let DS = null;          // { manifests, latest }

  function setMsg(text) { if (g("ws-msg")) g("ws-msg").textContent = text || ""; }
  /* t() 占位符是 {0}/{1}（i18n.js 实现），不是 %s —— 2026-11-05 探针实锤 */
  function setDsMsg(text) { if (g("ws-ds-msg")) g("ws-ds-msg").textContent = text || ""; }
  function setState(text) { if (g("ws-state")) g("ws-state").textContent = text || ""; }

  function shortTs(ts) { return String(ts || "").replace("T", " "); }

  function qualityLabel(st, degraded, publishable) {
    if (degraded) return t("评审未完成");
    if (st === "signing_ready") return t("可签约");
    if (st === "publish_ready") return t("可发布");
    if (publishable) return t("达标");
    return t("未达标");
  }

  /* —— 主加载：进「机制与策略」子页时被包装后的 loadStrategyLab 带起 —— */

  async function wsLoad() {
    try {
      WS = await api("/api/writer-slot");
    } catch (e) {
      setState(t("加载失败：{0}", String(e && e.message || e)));
      return;
    }
    const slot = WS.slot || {};
    const active = slot.active_version || "";
    setState(active ? t("启用中：{0}", active) : t("未启用（不注入）"));
    wsRenderVersions(active);
    wsRenderLedger(slot.ledger || []);
    wsRenderShadow(slot.shadow || {});
    wsRenderShadowReport(WS.shadow_report || {});
    try {
      DS = await api("/api/writer-dataset");
      wsRenderDatasetList();
    } catch (e) { /* 数据集页签失败不挡写手页 */ }
  }

  /* —— 版本管理 —— */

  function wsRenderVersions(active) {
    const sel = g("ws-version");
    if (!sel) return;
    const versions = (WS.slot && WS.slot.versions) || {};
    const keys = Object.keys(versions).sort((a, b) =>
      (parseInt(b.slice(1), 10) || 0) - (parseInt(a.slice(1), 10) || 0));
    const prev = sel.value;
    sel.innerHTML = keys.map((k) =>
      '<option value="' + esc(k) + '">' + esc(k) +
      (k === active ? " · " + t("启用中") : "") + "</option>").join("");
    sel.value = keys.includes(prev) ? prev : (keys[0] || "");
    wsRenderVersion();
  }

  function wsRenderVersion() {
    const slot = (WS && WS.slot) || {};
    const key = (g("ws-version") || {}).value || "";
    const ver = (slot.versions || {})[key] || {};
    if (g("ws-prompt-view")) g("ws-prompt-view").value = ver.prompt || "";
    const hint = ver.binding_hint || {};
    if (g("ws-hint-agent")) g("ws-hint-agent").value = hint.agent_id || "";
    if (g("ws-hint-provider")) g("ws-hint-provider").value = hint.provider_id || "";
    if (g("ws-hint-model")) g("ws-hint-model").value = hint.model || "";
  }

  async function wsActivate() {
    const key = (g("ws-version") || {}).value || "";
    if (!key) return;
    try {
      WS = await api("/api/writer-slot", { method: "POST",
        body: JSON.stringify({ action: "set_active", version: key }) });
      setMsg(t("已启用 {0}（新起草的章节开始生效）", key));
      await wsLoad();
    } catch (e) { setMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  async function wsDeactivate() {
    try {
      WS = await api("/api/writer-slot", { method: "POST",
        body: JSON.stringify({ action: "set_active", version: "" }) });
      setMsg(t("已停用（连载链不再注入写手纪律）"));
      await wsLoad();
    } catch (e) { setMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  async function wsAddVersion() {
    const prompt = (g("ws-new-prompt") || {}).value || "";
    const notes = (g("ws-new-notes") || {}).value || "";
    if (!prompt.trim()) { setMsg(t("先粘贴新的写手纪律全文")); return; }
    try {
      const res = await api("/api/writer-slot", { method: "POST",
        body: JSON.stringify({ action: "add_version", prompt, notes }) });
      setMsg(t("已保存为 {0}", res.version || ""));
      if (g("ws-new-prompt")) g("ws-new-prompt").value = "";
      if (g("ws-new-notes")) g("ws-new-notes").value = "";
      await wsLoad();
    } catch (e) { setMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  async function wsSaveHint() {
    const key = (g("ws-version") || {}).value || "";
    try {
      await api("/api/writer-slot", { method: "POST",
        body: JSON.stringify({ action: "set_binding_hint", version: key,
          binding_hint: { agent_id: (g("ws-hint-agent") || {}).value || "",
            provider_id: (g("ws-hint-provider") || {}).value || "",
            model: (g("ws-hint-model") || {}).value || "" } }) });
      setMsg(t("绑定提示已存到 {0}", key));
      await wsLoad();
    } catch (e) { setMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  async function wsApplyBinding() {
    const key = (g("ws-version") || {}).value || "";
    try {
      const res = await api("/api/writer-slot", { method: "POST",
        body: JSON.stringify({ action: "apply_binding", version: key }) });
      if (res && res.ok) {
        setMsg(t("已应用到模型绑定：{0}", key));
        await wsLoad();
      } else {
        setMsg(t("加载失败：{0}", String(res && res.error || "")));
      }
    } catch (e) { setMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  /* —— 迭代台账 —— */

  function wsRenderLedger(ledger) {
    const tb = g("ws-ledger-body");
    if (!tb) return;
    if (!ledger.length) {
      tb.innerHTML = '<tr><td colspan="7" class="hint">' +
        esc(t("还没有台账：跑一次连载任务，收尾自动记一行")) + "</td></tr>";
      return;
    }
    tb.innerHTML = ledger.slice(0, 10).map((r) => {
      const overall = (r.overall === null || r.overall === undefined)
        ? "—" : String(r.overall);
      return "<tr>" +
        "<td>" + esc(shortTs(r.ts)) + "</td>" +
        "<td>" + esc(r.version || "—") + "</td>" +
        '<td class="ws-ellipsis" title="' + esc(r.title || "") + '">' +
          esc(r.title || "—") + "</td>" +
        "<td>" + esc(String(r.chapters ?? "—")) + "</td>" +
        "<td>" + esc(String(r.passed_chapters ?? "—")) + "</td>" +
        "<td>" + esc(overall) + "</td>" +
        "<td>" + esc(qualityLabel(r.quality_status, r.review_degraded,
                        r.publishable)) + "</td>" +
        "</tr>";
    }).join("");
  }

  /* —— 挑战者影子 —— */

  function wsRenderShadow(sh) {
    if (g("ws-sh-enabled")) g("ws-sh-enabled").checked = !!sh.enabled;
    if (g("ws-sh-provider")) g("ws-sh-provider").value = sh.provider_id || "";
    if (g("ws-sh-model")) g("ws-sh-model").value = sh.model || "";
    if (g("ws-sh-rate")) g("ws-sh-rate").value = (sh.sample_rate ?? 0);
    if (g("ws-sh-judge-provider")) g("ws-sh-judge-provider").value = sh.judge_provider_id || "";
    if (g("ws-sh-judge-model")) g("ws-sh-judge-model").value = sh.judge_model || "";
  }

  async function wsSaveShadow() {
    try {
      await api("/api/writer-slot", { method: "POST",
        body: JSON.stringify({ action: "set_shadow",
          enabled: !!(g("ws-sh-enabled") || {}).checked,
          provider_id: (g("ws-sh-provider") || {}).value || "",
          model: (g("ws-sh-model") || {}).value || "",
          sample_rate: parseFloat((g("ws-sh-rate") || {}).value || "0") || 0,
          judge_provider_id: (g("ws-sh-judge-provider") || {}).value || "",
          judge_model: (g("ws-sh-judge-model") || {}).value || "" }) });
      setMsg(t("影子配置已保存"));
      await wsLoad();
    } catch (e) { setMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  function wsRenderShadowReport(rep) {
    const el = g("ws-shadow-report");
    if (!el) return;
    if (!rep || !rep.total) {
      el.textContent = t("暂无影子记录：启用影子并跑过连载后，这里给挑战者与生产稿的对账");
      return;
    }
    const meanJ = (rep.mean_judge === null || rep.mean_judge === undefined)
      ? "—" : String(rep.mean_judge);
    const meanC = (rep.mean_champion === null || rep.mean_champion === undefined)
      ? "—" : String(rep.mean_champion);
    el.textContent = t(
      "近30天：试写 {0} 次（成功 {1} / 失败 {2}），判了 {3} 次；挑战者均分 {4} vs 生产均分 {5}，赢 {6} / 平 {7} / 输 {8}；逐字重复 {9} 次。赢了才值得换将。",
      String(rep.total), String(rep.ok), String(rep.failed), String(rep.judged),
      meanJ, meanC, String(rep.wins), String(rep.ties), String(rep.losses),
      String(rep.dup_count));
  }

  async function wsRefreshShadow() {
    await wsLoad();
  }

  /* —— 训练数据集 —— */

  async function wsCollect() {
    setDsMsg(t("归集中，章节多的书要读一会儿…"));
    try {
      const res = await api("/api/writer-dataset", { method: "POST",
        body: JSON.stringify({ action: "collect",
          include_rejected: !!(g("ws-ds-rejected") || {}).checked }) });
      setDsMsg(t("归集完成：{0} 对（{1} 本书 / {2} 个工作目录）→ {3}",
        String(res.pairs ?? 0), String(res.tasks ?? 0),
        String(res.workdirs ?? 0), String(res.pairs_file || "")));
      DS = await api("/api/writer-dataset");
      wsRenderDatasetList();
    } catch (e) { setDsMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  async function wsExportSft() {
    setDsMsg(t("导出中…"));
    try {
      const res = await api("/api/writer-dataset", { method: "POST",
        body: JSON.stringify({ action: "export_sft" }) });
      if (res && res.ok) {
        setDsMsg(t("导出完成：{0} 条 → {1}（训练配方见 docs/writer-lora-recipe.md）",
          String(res.exported ?? 0), String(res.sft_file || "")));
      } else {
        setDsMsg(t("加载失败：{0}", String(res && res.error || "")));
      }
    } catch (e) { setDsMsg(t("加载失败：{0}", String(e && e.message || e))); }
  }

  function wsRenderDatasetList() {
    const el = g("ws-ds-list");
    if (!el) return;
    const items = (DS && DS.manifests) || [];
    if (!items.length) {
      el.textContent = t("还没有数据集：先「归集训练对」，再「导出 SFT」");
      return;
    }
    el.innerHTML = items.slice(0, 5).map((m) => {
      const name = m.sft_file || m.pairs_file || "";
      const n = (m.exported !== undefined) ? m.exported : m.pairs;
      const kind = m.sft_file ? "SFT" : t("训练对");
      return "<div>" + esc(shortTs(m.created_at)) + " · " + esc(kind) +
        " " + esc(name) + " · " + esc(String(n ?? "—")) + "</div>";
    }).join("");
  }

  /* —— 对外暴露 + 挂到「机制与策略」子页的加载链 —— */

  window.wsLoad = wsLoad;
  window.wsRenderVersion = wsRenderVersion;
  window.wsActivate = wsActivate;
  window.wsDeactivate = wsDeactivate;
  window.wsAddVersion = wsAddVersion;
  window.wsSaveHint = wsSaveHint;
  window.wsApplyBinding = wsApplyBinding;
  window.wsSaveShadow = wsSaveShadow;
  window.wsRefreshShadow = wsRefreshShadow;
  window.wsCollect = wsCollect;
  window.wsExportSft = wsExportSft;

  const _origStrategyLab = window.loadStrategyLab;
  window.loadStrategyLab = async function () {
    if (_origStrategyLab) {
      try { await _origStrategyLab.apply(this, arguments); } catch (e) { /* 保持原行为 */ }
    }
    try { await wsLoad(); } catch (e) { /* 写手工坊坏了不挡策略页 */ }
  };
})();
