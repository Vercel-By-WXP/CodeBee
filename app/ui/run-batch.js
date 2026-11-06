/* ============================================================================
 * 运行记录：一键重试 / 重试所选（run-batch.js，2026-11-05）
 * ============================================================================
 * 用户实拍（11-05 16:05）：一次同步重启把五条运行记录留成「已中断·可续跑」，
 * 想一起续上只能一条条点开详情再点「继续任务」——五条就是五次开详情、五次返回。
 * 运行记录本来就有勾选框（为批量删除而建），这条链路只补「批量重试」这一半。
 *
 * 为什么是独立覆盖层（DESIGN.md §9 落点优先级）：新 overlay 文件 > 独立 CSS >
 * 直接改 app.js。`#run-list` 的整块 innerHTML 由 app.js 的 renderRunList 重写，
 * 本文件用 MutationObserver 在同一个微任务里补按钮（paint 之前完成，不闪），
 * 不碰 app.js 一行。删掉 index.html 的那一行 <script> 即回滚。
 *
 * 重试是**任务级**动作：POST /api/tasks/<task_id>/retry（store.retry_task，
 * 断点续跑，已落盘的章节不重写）。所以：
 *   · 同一任务的多条账本行只发起一次；
 *   · 只认「这一条是该任务最新的运行」——点旧行实际会让最新状态重跑一次，
 *     指向的却不是屏幕上那行，容易变成「点了没反应、却多出一条运行」；
 *   · 任务当前 running/queued 的不给按钮（后端会拒，且本来就在跑）。
 * 依赖 app.js / i18n.js 提供的全局：t, toast, api, uiConfirm, poll, S。
 * ============================================================================ */
(function () {
  "use strict";

  if (window.__runBatchInstalled) return;
  window.__runBatchInstalled = true;

  /* S 是 app.js 顶楼的 const（全局词法环境，后续经典脚本可直接读）；
   * 取不到就按空状态处理——此时列表也没东西可装饰。 */
  function stateS() {
    try { return (typeof S === "object" && S) ? S : (window.S || {}); }
    catch (e) { return window.S || {}; }
  }

  function runsById() {
    const out = {};
    const runs = ((stateS().state || {}).runs) || [];
    for (const r of runs) if (r && r.id) out[r.id] = r;
    return out;
  }

  /* 行上的 run id：行是 app.js 拼字符串渲染的，onclick 里带着 openRun('id')。
   * 从 DOM 读而不是重算可见性（superseded 折叠 + 切片 30 都在 app.js 里），
   * 屏幕上有什么行就按什么行算，永远和用户看到的一致。 */
  function rowId(el) {
    const m = /openRun\('([^']+)'\)/.exec(el.getAttribute("onclick") || "");
    return m ? m[1] : "";
  }

  function latestOf(taskId) {
    const map = (stateS().state || {}).task_latest || {};
    return map[taskId] || null;
  }

  /* 可重试 = 终态异常（失败/取消/超时，含「服务重启打断」）+ 是该任务最新
   * 的一条 + 任务此刻没在跑。done 但未达标不在这里——那是「重写未达标章」，
   * 语义不同，别混进「重试」。 */
  function retryable(r) {
    if (!r || !r.task_id || r.superseded_by) return false;
    const latest = latestOf(r.task_id);
    if (!latest || latest.id !== r.id) return false;
    if (latest.status === "running" || latest.status === "queued") return false;
    return ["failed", "cancelled", "timeout"].indexOf(String(r.status || "")) >= 0;
  }

  let busy = false;

  async function retryRuns(ids) {
    if (busy) return;
    const byId = runsById();
    const picked = [];
    const seenTask = {};
    for (const id of ids || []) {
      const r = byId[id];
      if (!r || !retryable(r)) continue;
      if (seenTask[r.task_id]) continue;      // 同一任务只发起一次（任务级动作）
      seenTask[r.task_id] = true;
      picked.push(r);
    }
    if (!picked.length) {
      toast(t("所选记录都不在可重试状态：可能任务已在运行，或这一条之后又有新运行"));
      return;
    }
    if (picked.length > 1) {
      const ok = await uiConfirm(
        t("重试这 {0} 条记录？从已保存的进度接着跑，已完成的章节不重写。", picked.length),
        { ok: t("重试") });
      if (!ok) return;
    }
    busy = true;
    let done = 0;
    let failed = 0;
    let firstErr = "";
    for (const r of picked) {
      try {
        await api("/api/tasks/" + encodeURIComponent(r.task_id) + "/retry", { method: "POST" });
        done++;
      } catch (e) {
        failed++;
        if (!firstErr) firstErr = (e && e.message) || "";
      }
    }
    busy = false;
    const s = stateS();
    if (s && s.selRuns) s.selRuns = {};
    if (typeof poll === "function") poll();
    if (!done) {
      toast(t("重试失败：") + (firstErr || t("服务没有响应")), true);
      return;
    }
    if (failed) {
      toast(t("已重新发起 {0} 条；{1} 条没发起：{2}", done, failed,
        firstErr || t("任务刚被别人动了")), true);
      return;
    }
    toast(t("已重新发起 {0} 条运行", done));
  }

  function mkBtn(cls, label, title, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls + " small";
    b.textContent = label;
    if (title) b.title = title;
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      onClick();
    });
    return b;
  }

  function decorateToolbar(box, rows) {
    const tools = box.querySelector(".run-tools");
    if (!tools) return;
    tools.querySelectorAll(".rb-tool").forEach((b) => b.remove());
    const sel = stateS().selRuns || {};
    const selRows = rows.filter((r) => sel[r.id]);
    if (selRows.length) {
      const b = mkBtn("primary", "↻ " + t("重试所选") + " (" + selRows.length + ")",
        t("重试所选里可续跑的记录：接着已保存的进度跑，已完成的章节不重写"),
        () => retryRuns(selRows.map((r) => r.id)));
      b.classList.add("rb-tool", "rb-sel");
      const del = tools.querySelector("button.danger");
      tools.insertBefore(b, del || null);
    }
    if (rows.length) {
      const b = mkBtn("ghost", "↻ " + t("全部重试") + " (" + rows.length + ")",
        t("重试列表里所有可续跑的记录；运行中的记录不动"),
        () => retryRuns(rows.map((r) => r.id)));
      b.classList.add("rb-tool", "rb-all");
      tools.appendChild(b);
    }
  }

  function decorate() {
    const box = document.getElementById("run-list");
    if (!box) return;
    const byId = runsById();
    const rows = [];
    box.querySelectorAll(".item").forEach((el) => {
      const host = el.querySelector(".t");
      if (!host) return;
      const id = rowId(el);
      const run = id ? byId[id] : null;
      const old = host.querySelector(".rb-retry");
      if (!retryable(run)) {
        if (old) old.remove();     // 已被续跑接上 / 任务开跑了：收掉死钮
        return;
      }
      rows.push(run);
      if (old) return;
      const b = mkBtn("ghost", "↻ " + t("重试"),
        t("从已保存的进度接着跑，已完成的章节不重写"),
        () => retryRuns([id]));
      b.classList.add("rb-retry");
      const del = host.querySelector("button.danger");
      host.insertBefore(b, del || null);
    });
    decorateToolbar(box, rows);
  }

  function start() {
    const box = document.getElementById("run-list");
    if (!box) return;
    // innerHTML 整块重写的同一微任务里补按钮：回调跑在 paint 之前，不闪。
    // 只监听直接子节点——装饰只动 .t / .run-tools 内部，不会自激。
    new MutationObserver(() => { try { decorate(); } catch (e) { /* 装饰失败不拖垮列表 */ } })
      .observe(box, { childList: true });
    try { decorate(); } catch (e) { /* 同上 */ }
  }

  // 供控制台/探针手动触发（不参与渲染链路）
  window.runBatchDecorate = decorate;
  window.runBatchRetry = retryRuns;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
