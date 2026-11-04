/* 改稿单查看器（P1-E，2026-11-04）
 *
 * 闸门拦下发布时，发布卡上只有一句「质量闸未过：第 1 章没有兑现任何「获得」」——
 * 作者仍要自己翻回原稿找改哪一段。这个覆盖层把 `GET /api/tasks/<id>/revision`
 * 的结论摆出来：哪一章、第几段第几句、差什么、往哪个方向补。
 *
 * 独立文件、不碰 app.js 主逻辑：并行会话常整文件重写 app.js，新功能挂在外面
 * 才不会被覆盖掉（与 chat-outline.js / chat-quickprompts.js 同一做法）。
 * 依赖 app.js 的全局 api / openModal / closeModal / toast / t，defer 排在其后即可。
 */
(function () {
  if (window.__rbMounted) return;
  window.__rbMounted = true;

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const tr = (s) => (typeof t === "function" ? t(s) : s);

  let lastText = "";

  /* 改稿单是**受限的子集**（## / ### / - / 段落），不是通用 markdown：
     用几个固定类名渲染，比引一个解析器安全，也不会被用户正文注入标签。 */
  function renderBody(data) {
    const rev = (data && data.revision) || {};
    const text = String(rev.text || "");
    if (!text) return "";
    const rows = text.split("\n").map((line) => {
      const s = line.trim();
      if (!s) return "";
      if (s.indexOf("### ") === 0) return '<div class="rb-h2">' + esc(s.slice(4)) + "</div>";
      if (s.indexOf("## ") === 0) return '<div class="rb-h1">' + esc(s.slice(3)) + "</div>";
      if (s.indexOf("- ") === 0) return '<div class="rb-li">' + esc(s.slice(2)) + "</div>";
      return '<p class="rb-p">' + esc(s) + "</p>";
    }).join("");
    const lead = rev.headline ? '<div class="rb-lead">' + esc(rev.headline) + "</div>" : "";
    return lead + rows;
  }

  async function load(taskId) {
    const path = "/api/tasks/" + encodeURIComponent(taskId) + "/revision";
    if (typeof api === "function") return await api(path);
    const resp = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return await resp.json();
  }

  window.openRevisionBrief = async function (taskId) {
    const id = String(taskId || "").trim();
    if (!id) return;
    let data = null;
    try {
      data = await load(id);
    } catch (e) {
      if (typeof toast === "function") {
        toast(tr("改稿单加载失败：") + ((e && e.message) || e), true);
      }
      return;
    }
    const body = renderBody(data);
    if (!body) {
      if (typeof toast === "function") toast(tr("这一稿现在没有被拦的章节"));
      return;
    }
    lastText = String(((data && data.revision) || {}).text || "");
    const foot = '<button class="ghost" onclick="rbCopyBrief()">' + esc(tr("复制改稿单")) +
      '</button><button class="primary" onclick="closeModal()">' + esc(tr("知道了")) + "</button>";
    if (typeof openModal === "function") {
      openModal(tr("改稿单"), body, foot, "rb-modal");
    }
  };

  window.rbCopyBrief = function () {
    const text = lastText || "";
    if (!text) return;
    const done = () => { if (typeof toast === "function") toast(tr("已复制改稿单")); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => {});
      return;
    }
    const box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "readonly");
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* 复制失败不打扰 */ }
    document.body.removeChild(box);
  };
})();
