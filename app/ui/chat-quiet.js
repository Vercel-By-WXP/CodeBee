/* ============================================================================
 * 对话静音层（chat-quiet.js，2026-11-04）
 * ============================================================================
 * 用户原话：「对话现在看起来眼睛痛，很多不需要的就隐藏或者去掉，还有很多内容
 * 能不能整理成一条」。
 *
 * 病根：时间线上每完成一章/每跑一步就是一条独立 .chat-row——一行时间、一行
 * 「第 1 章完成（约 2500 字）」、一行「（检查点范围提示：…）」、一行「CLI 原始
 * 输出共 1412 字符」。八条过程把一屏撑满，真正要看的东西（评审、失败）被埋了。
 *
 * 做法（只减不加）：把「连续的短状态行」收进一个默认收起的 details，摘要写
 * 「过程 N 条 · 17:19–17:45」。判据是 DOM 形状，不是任务类型——所以对直连对话、
 * CLI 步骤、历史数据同样成立；正文够长的回复、运行中的那条、评审卡/失败卡/
 * 结果卡/文件变更卡一律不合并（它们是结果，不是过程）。
 *
 * 为什么是独立覆盖层：设计宪法 §9——新交互落独立文件，不碰共享检出的 app.js。
 * app.js 每次重绘 #rd-chat-flow 都是整块 innerHTML，所以本层天然幂等：重建后
 * 原来的组消失，下一次观察重新合并；展开状态按组指纹记在 OPEN 里接着恢复。
 * 样式在 chat-quiet.css；词条在 i18n.js（"过程记录"）。
 * 血账对齐：只在 childList 变化后合并、绝不重写被按下的节点内容；
 *          合并期间用 busy 闸门挡住自触发，避免 observer 死循环。
 * -------------------------------------------------------------------------- */
(function () {
  "use strict";

  const t = (s) => { try { return typeof window.t === "function" ? window.t(s) : s; } catch (e) { return s; } };
  const OPEN = new Set();          // 展开过的组（key = 条数 + 首末行文本指纹）
  const SEC_OPEN = new Set();      // 展开过的「次要段」（key = 段首时间 + 段首文本，不含条数）
  // 老内核没有 queueMicrotask：Promise.then 同样是微任务，一样赶在 paint 之前。
  const microtask = typeof queueMicrotask === "function"
    ? queueMicrotask : (fn) => { Promise.resolve().then(fn); };
  let busy = false, pending = false, attempts = 0;

  const flow = () => document.getElementById("rd-chat-flow");

  /* 结构指纹：4s 对账周期里没有新内容时，这里一个字节都不写。
   * 内容是 app.js 重绘出来的，所以指纹变了才说明该重新合并。 */
  function fingerprint(host) {
    return host.children.length + "|" + String(host.textContent || "").length + "|" +
      Array.from(host.children).map((c) => c.className || c.tagName).join(",");
  }

  /* 「一行状态」判定：短、无结果卡、不在跑。
   * 返回 "raw" / "empty" / "short" 表示可合并，null 表示这是结果、别动。 */
  function lightKind(row) {
    if (row.classList.contains("me")) return null;
    const bubble = row.querySelector(":scope > .chat-bubble.agent");
    if (!bubble) return null;
    if (bubble.querySelector(
      ".chat-think.live,.chat-fail-card,.chat-review-card,.chat-result,.chat-fups," +
      ".chat-tool-group,.chat-change-card,.chat-cli-log,.chat-change-row")) return null;
    // 运行中/失败都有专门形态（实时块、红卡），别动；取消/超时反而是「过程」——
    // 它们往往只是一句「这一步没跑完」，比正常步骤更该收敛
    if (bubble.classList.contains("is-running") || bubble.classList.contains("is-failed")) return null;
    // CLI 原始事件流：本身就是「点了才看」的块，过程行里更不该占位
    if (bubble.querySelector(":scope > .chat-raw-fold")) return "raw";
    const body = bubble.querySelector(":scope > .chat-body");
    const txt = body ? String(body.textContent || "").replace(/\s+/g, " ").trim() : "";
    if (!txt) return "empty";
    // 「一行状态」判据：剥掉括注后的主干很短（"第 1 章完成（约 2500 字）" → 8 字），
    // 整行也不过一行半。括注里是「检查点范围提示：…」这类自动说明，正是噪音本体。
    // 只要有结构（列表/代码/表格/引用）或主干成段，就是真回复，绝不合并。
    if (body && body.querySelector("pre,table,ul,ol,blockquote")) return null;
    if (body && body.querySelectorAll("p").length > 2) return null;
    const core = txt.replace(/（[^（）]*）/g, "").replace(/\([^()]*\)/g, "")
      .replace(/\s+/g, " ").trim();
    if (core.length > 60 || txt.length > 140) return null;
    return "short";
  }

  const clock = (row) => {
    const meta = row.querySelector(":scope > .chat-meta");
    const m = String(meta ? meta.textContent : "").match(/(\d{2}:\d{2})/);
    return m ? m[1] : "";
  };
  const label = (row) => {
    // 只认气泡直属的正文：CLI 原始输出块内部也有 .chat-body（整段 JSON），
    // 用后代选择器会把它当成正文，组头就会挂上 `{"type":"thread.started"…`。
    const body = row.querySelector(".chat-bubble.agent > .chat-body");
    const txt = String(body ? body.textContent : "").replace(/\s+/g, " ").trim();
    if (txt) return txt.slice(0, 26);
    const raw = row.querySelector(".chat-bubble.agent > .chat-raw-fold > summary");
    return raw ? String(raw.textContent || "").replace(/\s+/g, " ").trim().slice(0, 26) : "";
  };

  function groupKey(members) {
    return members.length + "|" + clock(members[0]) + "|" + label(members[0]) + "|" +
      clock(members[members.length - 1]) + "|" + label(members[members.length - 1]);
  }

  function buildGroup(host, members, opts) {
    const o = opts || {};
    const key = groupKey(members);
    const first = clock(members[0]), last = clock(members[members.length - 1]);
    const span = first && last ? (first === last ? first : first + "–" + last) : "";
    const d = document.createElement("details");
    d.className = "chat-quiet-group";
    d.dataset.cqKey = key;
    d.setAttribute("data-chat-disclosure", "quiet:" + key);
    const sum = document.createElement("summary");
    const tail = (o.label || t("过程记录")) + " " + String(members.length) + " " + t("条");
    const tailText = o.tail === false ? ""
      : (o.tail != null ? o.tail : (label(members[members.length - 1])
          // 去掉没写完的括注尾巴（「第 1 章完成（约 2500 字）（检查点范围提示：…」），
          // 组头只要能看出「过程停在哪」，一句就够
          .replace(/（[^（）]*$/, "").replace(/\([^()]*$/, "").slice(0, 16)));
    sum.innerHTML =
      '<span class="cq-count">' + tail + "</span>" +
      (span ? '<span class="cq-span">' + span + "</span>" : "") +
      // 摘要带最近一条，组收起来也知道过程停在哪
      (tailText ? '<span class="cq-tail">' + String(tailText).replace(/[<>&]/g, "") + "</span>" : "");
    d.appendChild(sum);
    host.insertBefore(d, o.anchor || members[0]);   // 先插组，再搬成员（顺序不能反）
    members.forEach((m) => {
      // 组内只留时刻：这一组共用一段连续时间，「用时 X 分 Y 秒」「复制」在这里
      // 是噪音（摘要是给人扫的，不是给人算的）。删掉的只是按钮节点，不影响
      // 时间线的事件委托（复制按钮的监听在 #rd-chat-flow 上按 data-ci 找回）。
      const meta = m.querySelector(":scope > .chat-meta");
      const c = clock(m);
      if (meta && c) meta.textContent = c;
      d.appendChild(m);
    });
    if (OPEN.has(key)) d.open = true;
    d.addEventListener("toggle", () => { if (d.open) OPEN.add(key); else OPEN.delete(key); });
    return d;
  }

  /* 评审卡：结构一模一样，只有分数在变。
   * 这是时间线上最大的重复——一个跑 3 小时的连载任务能连出十几张同版式的卡，
   * 每张 49px 加 18px 行距就是 800px。**只保留最新一张**（那是「现在多少分」），
   * 更早的全部收进「历次评审 N 次」的账本里，展开仍是原卡（分数/维度/意见一条不丢）。
   * 注意：不要求连续——中间夹着过程组也一样收，因为它们是同一件事的历史快照。 */
  function mergeReviews(host) {
    const cards = Array.from(host.children)
      .filter((el) => el.classList && el.classList.contains("chat-review-card"));
    if (cards.length < 2) return;
    const older = cards.slice(0, -1);
    const key = "reviews:" + older.length + ":" +
      older.map((c) => (c.querySelector(".chat-review-avg") || {}).textContent || "").join(",");
    const d = document.createElement("details");
    d.className = "chat-quiet-reviews";
    d.dataset.cqKey = key;
    const sum = document.createElement("summary");
    sum.textContent = t("历次评审") + " " + String(older.length) + " " + t("次");
    d.appendChild(sum);
    host.insertBefore(d, older[0]);
    older.forEach((c) => d.appendChild(c));
    if (OPEN.has(key)) d.open = true;
    d.addEventListener("toggle", () => { if (d.open) OPEN.add(key); else OPEN.delete(key); });
  }

  /* 长正文分两档，判据是「这是不是编排步骤的输出」：
   *   · 有 .chat-step-meta（#N 步骤，改稿/评审执行器吐的）→ **整段收起**，只留
   *     「查看输出 · 664 字」一行。它属于过程，不是在跟你说话。
   *   · 没有 step-meta（智能体直接回复你）→ **夹**到 200px + 「展开全文」，
   *     内容还在眼前，只是不占满一屏。
   * 两档都可点开、展开状态记在 OPEN 里，重绘后保持。CLI 原始输出（本身是 details）不动。 */
  const CLAMP_H = 200;
  function clampBodies(host) {
    host.querySelectorAll(".chat-row .chat-bubble.agent > .chat-body").forEach((b) => {
      if (b.dataset.cqClamped) return;
      const full = b.scrollHeight;                       // 先量原始高度，别在加了 max-height 之后量
      const bubble = b.parentElement;
      const isStep = !!(bubble && bubble.querySelector(":scope > .chat-step-meta"));
      const text = String(b.textContent || "").trim();
      const bare = text.length;
      // 步骤输出的第一行通常就是「critique-c2 · #28 · 调用 CLI aider · 模型 opus」这种摘要，
      // 后面几行才是要藏的东西 → 折成一行、入口贴行尾，不额外占一行。
      // 唯一例外是 JSON 原始输出（`{"type":"thread.started"…`），第一行本身就是噪音，
      // 没有可留的摘要，整段收起只留「查看输出 · N 字」。
      const jsony = /^[[{]/.test(text) || /"type"\s*:/.test(text.slice(0, 120));
      let mode = "";
      if (isStep) {
        if (full <= 26) return;                          // 本来就一行，别加按钮
        mode = jsony ? "hidden" : "clamp1";
      } else if (full > CLAMP_H + 60) {
        // 同一条前提的另一处：isStep 恒为真，所以这份「真回复折三行」走不到。
        // 与 isPrimary 的最后一行同源，改渲染规则时两处要一起看。
        mode = "clamp";
      } else {
        return;                                          // 智能体回复不短，别动
      }
      const key = "body:" + full + ":" + bare + ":" + text.slice(0, 12);
      b.dataset.cqClamped = "1";
      b.dataset.cqBodyKey = key;
      b.classList.add("chat-quiet-" + mode);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-quiet-more" + (mode === "clamp1" ? " is-inline" : "");
      btn.dataset.cqBody = key;
      const label = (open) => open ? "▾ " + t("收起")
        : "▸ " + (mode === "clamp" ? t("展开全文") : t("查看输出")) + " · " + String(bare) + " " + t("字");
      btn.textContent = label(false);
      b.insertAdjacentElement("afterend", btn);
      // 贴行尾的入口会盖住正文：按入口实测宽度给正文右侧留白，窄窗下也不会钻字。
      if (mode === "clamp1") {
        const w = btn.getBoundingClientRect().width;
        if (w > 0) b.style.setProperty("--cq-fold-w", Math.ceil(w) + 10 + "px");
      }
      if (OPEN.has(key)) {
        b.classList.add("is-open");
        btn.classList.add("is-open");
        btn.textContent = label(true);
      }
    });
  }

  /* 同因重试：一个模型网关抽风会连续吐「超时；stderr/stdout: Reconnecting 1/5…」
   * 「…2/5…」「…3/5…」，每条都是一段两三百字的英文报错。app.js 只在 status=failed
   * 时合并同文失败，这些算「跑完了但结果是报错」的行就漏成了报错墙。
   * 判据：正文带报错特征词 + 剥掉数字/括注后前 32 字相同，连续 ≥2 条才合。
   * 不判「内容相似」——正常回复永远不进这里，宁可漏。 */
  const ERR_SHAPE = /(stderr\/stdout|Reconnecting|超时|退出码|rate limit|timed out|Service Unavailable|请求失败|ECONN|socket hang up)/i;
  function dupKey(row) {
    if (row.classList.contains("me")) return "";
    const b = row.querySelector(".chat-bubble.agent > .chat-body");
    if (!b) return "";
    const txt = String(b.textContent || "").replace(/\s+/g, " ").trim();
    if (txt.length < 24 || !ERR_SHAPE.test(txt)) return "";
    return txt.replace(/（[^（）]*）/g, "").replace(/\([^()]*\)/g, "")
      .replace(/\d+/g, "#").replace(/\s+/g, " ").trim().slice(0, 32);
  }

  function mergeDupes(host) {
    const kids = Array.from(host.children);
    let i = 0;
    while (i < kids.length) {
      const el = kids[i];
      // kids 是合并前的快照：已被收进过程组的行不再在 host 里，不能再被抢出来
      if (el.parentElement !== host) { i++; continue; }
      const k = el.classList && el.classList.contains("chat-row") ? dupKey(el) : "";
      if (!k) { i++; continue; }
      let j = i;
      const members = [];
      while (j < kids.length && kids[j].parentElement === host &&
             kids[j].classList.contains("chat-row") && dupKey(kids[j]) === k) {
        members.push(kids[j]);
        j++;
      }
      if (members.length >= 2) buildGroup(host, members, { label: t("重试记录"), tail: false });
      i = j;
    }
  }

  /* 连续的过程段再并一层：一轮一章会产出「过程组 → 单条 → 过程组 → 单条」，
   * 中间只隔着别的过程，那就还是一个过程。把它们并成一整段，
   * 一屏只剩「过程记录 N 条 · 17:11–20:33」这一行，点开才是账本。 */
  const isProcPart = (el) => !!(el && el.classList &&
    (el.classList.contains("chat-quiet-group") || el.classList.contains("chat-quiet-solo")));

  function mergeRuns(host) {
    const kids = Array.from(host.children);
    let i = 0;
    while (i < kids.length) {
      if (kids[i].parentElement !== host || !isProcPart(kids[i])) { i++; continue; }
      let j = i;
      const parts = [];
      while (j < kids.length && kids[j].parentElement === host && isProcPart(kids[j])) {
        parts.push(kids[j]);
        j++;
      }
      if (parts.length >= 2) {
        const rows = [];
        parts.forEach((p) => {
          if (p.classList.contains("chat-quiet-group")) {
            p.querySelectorAll(":scope > .chat-row").forEach((r) => rows.push(r));
          } else {
            rows.push(p);
          }
        });
        if (rows.length >= 2) {
          buildGroup(host, rows, { anchor: parts[0] });
          parts.forEach((p) => { if (p.parentElement === host) p.remove(); });
        }
      }
      i = j;
    }
  }

  /* ---------- 语义分层：重点 / 次要（2026-11-04 用户「分一下重点和次要」） ----------
   * 前面几步是「把过程压薄」，本质还是在展示它们。用户真正要的是分层：
   *   **重点**：你说的话、智能体在跟你说的话、执行失败、最新一次评审、
   *             正在跑的那条、结果/文件变更卡。
   *   **次要**：系统记录、过程记录、历次评审、重试账本、步骤输出、CLI 原始输出。
   * 次要内容默认**不展示**：整段收进一个默认收起的 details，摘要只有一行极淡的
   * 「⋯ 过程 12 条」——主线上一眼只剩重点，又不至于让人以为中间被跳过了。
   * 一个都不要漏的判据思路：**认不出来的一律当重点**（宁可多留一行，不误吞结果）。
   *
   * 2026-11-05 实测修正：「智能体在跟你说话 = 没有 step-meta 的成段正文」这条
   * 最初写下来的形态**在产品里并不存在**——app.js 的 stepMeta 表达式在 done 状态下
   * 补一句 t("完成")，所以每个 agent 气泡都带 `.chat-step-meta`。在 4 个真实任务
   * （连载 1103 行 / 短篇 228 行 / 研究 359 行 / 已完结 8 行）、共 1695 条 agent 行
   * 上数过：带 step-meta 的 100%，「成段回复」0 条（`scratch/cq-primary-audit.mjs`）。
   * 对话时间线本来就是步骤流水，没有独立的「回复」形态——这不是缺陷，是产品形态，
   * 步骤全折是用户要的效果。但下面那句 `return true` 因此走不到，别把它当活判据。
   * 前提是跨文件的：哪天 app.js 不再写 step-meta，本层会把所有智能体输出当重点，
   * 而且**不会报错**。`tests/test_ui_chat_quiet_primary.py` 锁住了这条前提。 */

  const PRIMARY_BAD = ".chat-result,.chat-change-row,.chat-fups,.chat-fail-card,.chat-review-card";
  function isPrimary(el) {
    const c = el.classList;
    if (!c) return true;
    if (c.contains("cq-sec")) return true;              // 已经收好了，别再套一层（幂等）
    if (c.contains("chat-fail-card") || c.contains("chat-review-card")
      || c.contains("chat-result") || c.contains("chat-change-row")) return true;
    if (c.contains("chat-quiet-group") || c.contains("chat-quiet-reviews")
      || c.contains("chat-sysnote-group")) return false;
    if (!c.contains("chat-row")) return true;           // 不认识的块：留着，别吞
    // 「系统记录 ×4」这类调度说明：不管它在气泡里还是行里，整条都是次要
    if (el.querySelector(".chat-sysnote-group")) return false;
    if (c.contains("me")) return true;
    const b = el.querySelector(":scope > .chat-bubble.agent");
    if (!b) return true;
    if (b.classList.contains("is-running")) return true;          // 正在跑
    if (b.querySelector(PRIMARY_BAD)) return true;                // 失败/评审/结果
    // 「系统记录 ×8」这类在气泡内部、但整条就是调度说明的行：次要
    if (b.querySelector(".chat-sysnote-group,.chat-quiet-group,.chat-quiet-reviews")) return false;
    if (b.querySelector(":scope > .chat-step-meta")) return false; // 步骤输出 = 次要
    // 兜底：当前渲染规则下走不到（每个 agent 气泡都带 step-meta，见上方实测说明）。
    // 留着是为了「哪天渲染规则变了」之后不至于把真回复当步骤吞掉。
    return true;
  }

  // 组内所有行（组是 details，成员在里面）
  const rowsOf = (el) => (el.classList.contains("chat-row") ? [el]
    : Array.from(el.querySelectorAll(".chat-row")));

  /* 单条被收起的行叫得出名字吗：步骤名 > 正文首句。
   * 步骤名（.chat-step-meta）被 CSS 藏了，但它是这一行最准确的摘要。 */
  function secLabel(row) {
    const sm = row.querySelector(".chat-step-meta");
    const txt = sm ? String(sm.textContent || "").replace(/\s+/g, " ").trim() : "";
    return txt || label(row);
  }

  function foldSecondary(host) {
    const kids = Array.from(host.children);
    const primary = kids.filter((el) => el.parentElement === host && isPrimary(el)).length;
    // 全是次要（没有一条重点）时不做：那样整条时间线会缩成一行，等于什么都没了
    if (!primary) return;
    let i = 0;
    while (i < kids.length) {
      if (kids[i].parentElement !== host || isPrimary(kids[i])) { i++; continue; }
      let j = i;
      const members = [];
      while (j < kids.length && kids[j].parentElement === host && !isPrimary(kids[j])) {
        members.push(kids[j]);
        j++;
      }
      i = j;
      const rows = members.flatMap(rowsOf);
      if (!rows.length) continue;
      // 段里只有「已经收好的账本」（过程组/历次评审）→ 它本身就是一行摘要，不再套一层
      if (rows.length === 1 && members.length === 1
        && !members[0].classList.contains("chat-row")) continue;
      const key = "sec:" + clock(rows[0]) + "|" +
        secLabel(rows[0]).replace(/\s+/g, "").slice(0, 20);
      // key 里绝不能带条数：运行中每 1.2s 重绘都会往这段里追加几条，条数一变
      // key 就变，已展开的分段会被当「没展开过」重建 → 用户正盯着的输出被收起，
      // 观感上又是一次闪。段的起点是不动的历史行，用它当锚才稳。
      const d = document.createElement("details");
      d.className = "cq-sec";
      d.dataset.cqKey = key;
      const sum = document.createElement("summary");
      const first = clock(rows[0]);
      const last = clock(rows[rows.length - 1]);
      const span = first && last ? (first === last ? first : first + "–" + last) : "";
      // 只有一条时「过程 1 条」没信息量：直接说清被收起的是哪一步（步骤名优先）
      const one = rows.length === 1 ? secLabel(rows[0]) : "";
      sum.innerHTML = '<span class="cq-count">⋯ '
        + (one ? one.slice(0, 34).replace(/[<>&]/g, "")
          : t("过程") + " " + rows.length + " " + t("条")) + "</span>"
        + (span && !one ? '<span class="cq-span">' + span + "</span>" : "");
      d.appendChild(sum);
      host.insertBefore(d, members[0]);
      members.forEach((m) => d.appendChild(m));
      if (SEC_OPEN.has(key)) d.open = true;
      d.addEventListener("toggle", () => {
        if (d.open) SEC_OPEN.add(key); else SEC_OPEN.delete(key);
      });
    }
  }

  function merge() {
    const host = flow();
    if (!host || busy) return;
    // 页签不可见时（display:none）不做：几何全是 0，容易把不该合的合了
    if (host.offsetParent === null) return;
    if (fingerprint(host) === host.dataset.cqSig) return;   // 没变就不写 DOM
    // 合并会改变总高（一屏十几行收成一行），原来贴着底看的用户不能被甩到中间
    const pinBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 60;
    busy = true;
    try {
      const kids = Array.from(host.children);
      let i = 0;
      while (i < kids.length) {
        const el = kids[i];
        if (!el.classList.contains("chat-row") || !lightKind(el)) { i++; continue; }
        let j = i;
        const members = [];
        while (j < kids.length && kids[j].classList.contains("chat-row") && lightKind(kids[j])) {
          members.push(kids[j]);
          j++;
        }
        if (members.length >= 2) buildGroup(host, members);
        else {
          members[0].classList.add("chat-quiet-solo");
          // 压成一行会被 CSS 省略号裁掉：把全文挂 title，鼠标停一下能看全
          const b = members[0].querySelector(".chat-bubble.agent > .chat-body");
          if (b) b.title = String(b.textContent || "").trim().slice(0, 400);
        }
        i = j;
      }
      mergeReviews(host);
      mergeDupes(host);
      mergeRuns(host);
      clampBodies(host);
      foldSecondary(host);   // 最后一步：重点留主线，次要整段不展示
      // 运行中那行步骤名被 CSS 按 42% 截断：全文挂 title，鼠标停一下能看全
      host.querySelectorAll(".chat-bubble.agent.is-running > .chat-step-meta").forEach((el) => {
        const full = String(el.textContent || "").trim();
        if (full && el.title !== full) el.title = full;
      });
      // 思考块贴底时不遮最后一行（CSS 的渐隐靠 is-bottom 收掉）
      host.querySelectorAll(".chat-think .ct-body").forEach((b) => {
        const pin = b.scrollHeight - b.scrollTop - b.clientHeight < 12;
        b.classList.toggle("is-bottom", pin);
        if (!b._cqBound) {
          b._cqBound = true;
          b.addEventListener("scroll", () => {
            b.classList.toggle("is-bottom",
              b.scrollHeight - b.scrollTop - b.clientHeight < 12);
          }, { passive: true });
        }
        if (b.classList.contains("is-bottom")) b.scrollTop = b.scrollHeight;
      });
      if (pinBottom) host.scrollTop = host.scrollHeight;
      host.dataset.cqSig = fingerprint(host);   // 写回合并后的指纹，4s 对账不再重做
    } finally {
      busy = false;
    }
  }

  function schedule() {
    if (pending) return;
    // 必须走微任务，不能 setTimeout：app.js 的运行中快轮询每 1.2s 干一次
    // `flow.innerHTML = html`（整块换新节点），合并若延到 40ms 后，
    // 中间那 2~3 帧画出来的就是「没分层的原始长列表」→ 视觉上一闪一闪。
    // MutationObserver 回调本身就是微任务，这里同步排进同一个 checkpoint，
    // 浏览器还没 paint，重绘和合并落在同一帧里，用户看不到中间态。
    pending = true;
    microtask(() => { pending = false; merge(); });
  }

  function observe(f) {
    attempts = 0;
    new MutationObserver(schedule).observe(f, { childList: true });
    // 「展开全文 / 收起」：委托在常驻的 flow 上——正文块与按钮每次重绘都是新节点，
    // 逐个绑会在 1.2s 重绘里漏；委托只认 data 属性。
    f.addEventListener("click", (e) => {
      const btn = e.target.closest(".chat-quiet-more");
      if (!btn) return;
      const body = btn.previousElementSibling;
      if (!body) return;
      const isHide = body.classList.contains("chat-quiet-hidden");
      if (!isHide && !body.classList.contains("chat-quiet-clamp")
        && !body.classList.contains("chat-quiet-clamp1")) return;
      const key = btn.dataset.cqBody || "";
      const open = !body.classList.contains("is-open");
      body.classList.toggle("is-open", open);
      btn.classList.toggle("is-open", open);   // clamp1：展开后入口从「贴行尾」回到正文下方
      if (open) OPEN.add(key); else OPEN.delete(key);
      btn.textContent = open ? "▾ " + t("收起")
        : "▸ " + ((isHide ? t("查看输出") : body.classList.contains("chat-quiet-clamp1")
          ? t("查看输出") : t("展开全文")) + " · " +
           String(body.textContent || "").trim().length + " " + t("字"));
    });
    schedule();
    // 页签切回来时 flow 里没有 childList 变化（app.js 只是把容器 display 回来），
    // 上面那次 return 会让合并永远不补上——低频对账一次兜住。
    setInterval(() => { if (f.offsetParent !== null) schedule(); }, 4000);
  }

  function boot() {
    const f = flow();
    if (!f) {
      // 详情页骨架可能还没渲染出来：低频重试，别死等
      if (attempts++ < 60) setTimeout(boot, 500);
      return;
    }
    observe(f);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
