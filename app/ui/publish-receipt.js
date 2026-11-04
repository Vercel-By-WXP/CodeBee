/* ============================================================================
 * 手工发布回执覆盖层（publish-receipt.js，2026-11-05）
 * ============================================================================
 * 为什么是独立文件：DESIGN.md §9 落点优先级——新 overlay 文件 > 独立 CSS >
 * 直接改 app.js（共享检出多会话，app.js 是高风险区）。本文件在 app.js 之后
 * 加载，MutationObserver 往发布台每平台的「校准」按钮旁注一个「回执」按钮，
 * 弆应用内表单补登作者在平台网页手动发出去的章。
 * 后端：POST /api/publish/task/<id>/manual-receipt（main.py）→
 *       ledger.record_manual_receipt（upload_chapter + source=manual，
 *       stats 已发数/成功率由此不再少算；幂等跳过台账/校准已有的章）。
 * 依赖（由 app.js / i18n.js 提供，本文件不重复定义）：$, esc, t, api, toast,
 * openModal；关闭弹层优先用 app.js 的 closeModal，缺了就就地兜底。
 * ============================================================================ */
(function () {
  "use strict";

  function closeMyModal() {
    if (typeof window.closeModal === "function") { window.closeModal(); return; }
    const m = document.getElementById("modal");
    if (m) m.classList.add("hidden");
  }

  /* "12" / "12-15" / "12,14,16"（中英逗号、顿号、空格混排都认）；非法丢弃，
   * 去重升序，单次封顶 200 章（后端同闸，双保险）。 */
  function parseChapters(raw) {
    const out = [];
    String(raw || "").split(/[,，、\s]+/).forEach((tok) => {
      if (!tok) return;
      const m = tok.match(/^(\d{1,5})-(\d{1,5})$/);
      if (m) {
        let a = parseInt(m[1], 10);
        let b = parseInt(m[2], 10);
        if (a > b) { const c = a; a = b; b = c; }
        for (let n = a; n <= b && out.length < 200; n++) out.push(n);
        return;
      }
      const n = parseInt(tok, 10);
      if (n > 0 && n <= 99999) out.push(n);
    });
    return Array.from(new Set(out)).sort((x, y) => x - y).slice(0, 200);
  }

  window.pbManualReceipt = async function (taskId, platform) {
    const body = '<p class="hint">' + esc(t(
      "把你在平台网页上手动发出去的章补进台账——「已发章数」与成功率因此不再少算；" +
      "已在台账或校准记录里的章会自动跳过，不会重发。")) + "</p>" +
      '<div class="field"><label>' + esc(t("平台")) + "</label>" +
      '<div class="hint">' + esc(platform) + "</div></div>" +
      '<div class="field"><label>' + esc(t("章号")) + "</label>" +
      '<input id="pb-receipt-chapters" type="text" style="width:100%" placeholder="' +
      esc(t("12 或 12-15 或 12,14,16（最多 200 章）")) + '">' +
      '<p class="hint">' + esc(t("你在平台上实际发出去的章号；范围用短横线，多个用逗号或空格。")) +
      "</p></div>" +
      '<div class="field"><label>' + esc(t("备注（可选）")) + "</label>" +
      '<input id="pb-receipt-note" type="text" style="width:100%" placeholder="' +
      esc(t("如：手动发布包第 2 批")) + '"></div>';
    const foot = '<button class="ghost" onclick="closeMyModalPbReceipt()">' + esc(t("取消")) + "</button>" +
      '<button class="primary" onclick="pbReceiptSubmit(\'' + jsq(taskId) + "','" +
      jsq(platform) + '\')">' + esc(t("登记")) + "</button>";
    openModal(t("登记手工回执"), body, foot);
    const inp = document.getElementById("pb-receipt-chapters");
    if (inp) inp.focus();
  };
  window.closeMyModalPbReceipt = closeMyModal;

  window.pbReceiptSubmit = async function (taskId, platform) {
    const chapters = parseChapters((document.getElementById("pb-receipt-chapters") || {}).value);
    if (!chapters.length) {
      toast(t("先填章号"), true);
      return;
    }
    const note = String((document.getElementById("pb-receipt-note") || {}).value || "").trim();
    try {
      const r = await api("/api/publish/task/" + encodeURIComponent(taskId) +
        "/manual-receipt", { method: "POST",
          body: JSON.stringify({ platform: platform, chapters: chapters, note: note }) });
      closeMyModal();
      if (r && r.ok) {
        toast((r.added || []).length
          ? t("已登记 {0} 章（跳过 {1} 章已在账）", String((r.added || []).length),
              String((r.skipped || []).length))
          : t("这些章都已在账（{0} 章跳过）", String((r.skipped || []).length)));
      } else {
        toast(t("登记失败：{0}", String(r && r.error || "")), true);
      }
    } catch (e) {
      toast(t("登记失败：{0}", String(e && e.message || e)), true);
    }
  };

  /* —— 注入：盯发布台重绘，给每平台的「校准」按钮配一个「回执」 —— */

  function inject() {
    document.querySelectorAll('button[onclick*="pbSyncPublished"]').forEach((btn) => {
      if (btn.dataset.receiptDone) return;
      btn.dataset.receiptDone = "1";
      const m = (btn.getAttribute("onclick") || "")
        .match(/pbSyncPublished\('([^']*)',\s*'([^']*)'\)/);
      if (!m) return;
      const b = document.createElement("button");
      b.className = "ghost pb-tool";
      b.type = "button";
      b.textContent = t("回执");
      b.title = t("手工发出去的章在这里补登进台账");
      b.addEventListener("click", () => window.pbManualReceipt(m[1], m[2]));
      btn.parentNode.insertBefore(b, btn.nextSibling);
    });
  }

  const _mo = new MutationObserver(() => inject());
  function start() {
    if (document.body) {
      _mo.observe(document.body, { childList: true, subtree: true });
      inject();
    } else {
      setTimeout(start, 300);
    }
  }
  start();
})();
