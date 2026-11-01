/* ============================================================================
 * chat-intent.js —— 对话化②：意图解析 + 计划确认卡（2026-11-01，决策 15 / C0 阶段②）
 * ============================================================================
 * 用户照常在 composer 说话；提交先过**确定性意图层**（零模型、零额度）：
 *   - 命中「动作意图」（直发/存草稿/重试/暂停/恢复）→ 渲染**计划确认卡**
 *     （审批卡同形态：要做什么一行 + 后果一句 + 取消/确认执行）——**确认前
 *     零请求零 run 创建**，点「确认执行」才真正调接口；拒绝什么都不会发生。
 *   - 命中「查询意图」（进度/写到哪/多少字）→ 直接回一张答案卡（读本地
 *     state + /api/writing/counts，零模型）。
 *   - 没命中 → 原样走 createTask()，行为与今天完全一致（**漏判无害**）。
 * 纪律（坑位预警见迭代计划 C0 阶段②）：
 *   - **宁漏勿误**：带创作动词（写/修订/起草/生成…）的句子一律不拦，照旧
 *     建任务；「继续修订《X》第 11–20 章」这类快速开始模板绝不能被误触发。
 *   - 确认卡是硬闸不是装饰：执行器只认用户点过的「确认执行」。
 *   - 定时/排期识别出来但**不执行**，如实给「去自动化页」的引导卡（第一刀
 *     不假装能定时）。
 * 为什么是独立 overlay：并行会话在持续重写 app.js（memory-center/borrow-ui/
 * chat-stream 同款纪律）。本文件 wrap 全局 window.createTask（composer 的
 * Enter/按钮都汇到它），app.js 一行不改；手机壳激活时不拦（卡片容器在桌面
 * 主页，拦了用户会看不到卡）。
 * 样式在 chat-intent.css（wb token 家族）；文案全走 t()。
 * ============================================================================ */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const _t = (s) => (typeof window.t === "function" ? window.t(s) : s);
  const _esc = (s) =>
    typeof window.esc === "function" ? window.esc(s) : String(s == null ? "" : s);
  /* 占位符替换（rest 参数——箭头函数没有自己的 arguments，曾致 {0} 原样上屏） */
  const _fmt = (s, ...args) => {
    let out = String(s == null ? "" : s);
    args.forEach((v, i) => {
      out = out.split("{" + i + "}").join(String(v));
    });
    return out;
  };

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

  /* == PARSER-BEGIN ==
     纯函数区（零 DOM/零 window 依赖，测试切片用；改解析先改这里）。 */

  const CI_CREATION_GUARD =
    /(写|起草|生成|续写|修订|润色|改稿|大纲|设定|选题|拆解|评审|复盘|分析|总结|翻译|周报|简历|标书|策划|汇报)/;
  const _KINDS = ["publish", "pause", "resume", "retry", "query", "unschedule"];

  function ciNormNum(s) {
    s = String(s || "").replace(/[０-９]/g, (c) =>
      String.fromCharCode(c.charCodeAt(0) - 65248)).trim();
    if (/^\d{1,3}$/.test(s)) return parseInt(s, 10);
    const D = { "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9 };
    const m = s.match(/^([一二三四五六七八九]?)十([一二三四五六七八九]?)$/);
    if (m) {
      const tens = m[1] ? D[m[1]] : 1;
      const ones = m[2] ? D[m[2]] : 0;
      return tens * 10 + ones;
    }
    if (s === "十") return 10;
    if (D[s]) return D[s];
    return NaN;
  }

  function ciExtractBook(text) {
    const m = String(text || "").match(/《([^》]{1,40})》/);
    return m ? m[1].trim() : "";
  }

  function ciPlatform(text) {
    if (/番茄|fanqie/i.test(text)) return "fanqie";
    if (/七猫|qimao/i.test(text)) return "qimao";
    return "";
  }

  /* 时间解析（纯函数）：「明早10点」「晚上8点半」「10:10」→ {hh,mm,def}。
   * def=true = 没说时间，用 10:10（番茄直发安全窗）兜底，卡上如实注明。 */
  function ciParseTime(raw) {
    const colM = raw.match(/([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)/);
    if (colM) {
      return { hh: parseInt(colM[1], 10), mm: parseInt(colM[2], 10), def: false };
    }
    const m = raw.match(
      /(凌晨|清晨|明早|早上|上午|中午|下午|傍晚|晚上|今晚|夜里)?\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*[点时]\s*(半|\d{1,2}\s*分?)?/);
    if (!m) return { hh: 10, mm: 10, def: true };
    let h = /^[0-9]+$/.test(m[2]) ? parseInt(m[2], 10) : ciNormNum(m[2]);
    let min = 0;
    if (m[3]) {
      if (/半/.test(m[3])) min = 30;
      else {
        const mm = m[3].match(/(\d{1,2})/);
        if (mm) min = parseInt(mm[1], 10);
      }
    }
    if (isNaN(h) || h > 23 || min > 59) return { hh: 10, mm: 10, def: true };
    const peri = m[1] || "";
    if ((/下午|傍晚/.test(peri) || /晚上|今晚|夜里/.test(peri)) && h < 12) {
      h = (/晚上|今晚|夜里/.test(peri) && h === 12) ? 0 : h + 12;  // 晚上12点 → 0点
    }
    return { hh: h, mm: min, def: false };
  }

  /* 解析主入口。返回 null = 不是可识别意图（照旧建任务）。
   * ctx.workdir：composer 当前工作目录（无书名的发章命令用它定位任务）。 */
  function ciParseIntent(text, ctx) {
    const raw = String(text || "").trim();
    if (!raw || raw.length > 200) return null;
    const book = ciExtractBook(raw);
    const plat = ciPlatform(raw);
    const ctxObj = ctx || {};
    const workdir = String(ctxObj.workdir || "").trim();

    // 0) 扫榜（选材官的活；带创作动词放行——「写一篇榜单分析」是 article）
    if (!CI_CREATION_GUARD.test(raw) &&
        (/(扫|扒)(一?下|一遍)?[^。]{0,16}榜/.test(raw) ||
         /榜[^。]{0,6}(扫描|选题|分析|调研)/.test(raw))) {
      return { kind: "scan",
               platform: plat,
               category: (raw.match(/女频|男频|都市|仙侠|玄幻|悬疑|灵异|历史|科幻|古言|现言/) || [""])[0] };
    }

    // 1) 取消定时 / 暂停 / 恢复 / 重试（控制动词优先；带创作动词一律放行建任务）
    if (!CI_CREATION_GUARD.test(raw)) {
      if ((/取消|关掉|停掉|撤销|解除/.test(raw) && /定时|自动发|排程|排期/.test(raw) ||
           /(不要|别|不再).{0,4}自动发/.test(raw)) && (book || workdir)) {
        return { kind: "unschedule", book: book, workdir: workdir };
      }
      if (/暂停|停一下|先停|停下/.test(raw) && (book || /任务|运行/.test(raw))) {
        return { kind: "pause", book: book };
      }
      if (/恢复|(继续|接着)(跑|执行|发)/.test(raw) && book) {
        return { kind: "resume", book: book };
      }
      if (/重试|重跑|重发|再来一次/.test(raw) && (book || /失败/.test(raw))) {
        return { kind: "retry", book: book };
      }
    }

    // 2) 发布类：书名或当前目录 + 章 + 发送动词；带创作动词不拦（照旧建任务）
    //    （守卫只门发布分支——查询的「写到哪了/多少字」虽含「写」但语义无歧义）
    const creationish = CI_CREATION_GUARD.test(raw);
    const countM = raw.match(/(?<!第)\s*([0-9０-９一二三四五六七八九十]{1,3})\s*章/);
    const chNoM = raw.match(/第\s*([0-9０-９一二三四五六七八九十百]{1,4})\s*章/);
    const sendVerb = /发布|直发|送审|上传|发到|发上|排发|发出去|发一下|发了|待发章|存草稿|草稿箱/.test(raw) ||
      (/发/.test(raw) && countM);
    // 定时/排期：识别后走真建单（task.auto_publish，每天到点直发待发章）
    // （「排一下」这类泛词不进触发面——「安排一下」会误中，交给 triage 兜底）
    const scheduleish = /定时|预约|排发|排到|明早|明晚|明天|今晚|早上.{0,4}点|晚上.{0,4}点/.test(raw);
    if ((book || workdir) && sendVerb && !creationish &&
        (countM || chNoM || /待发章|全部章|所有章|全部待发/.test(raw) || plat ||
         scheduleish)) {
      // 「第 N 章」= 指定章号（可多章就先支持单章）；「N 章」= 最新 N 章
      let chapters = null, count = 0;
      if (chNoM) {
        const n = ciNormNum(chNoM[1].replace(/十/g, "十"));
        chapters = isNaN(n) ? null : [n];
      } else if (countM) {
        count = ciNormNum(countM[1]);
        if (isNaN(count) || count <= 0 || count > 99) count = 0;
      }
      const mode = scheduleish ? "schedule"
        : (/存草稿|草稿箱|as.?draft/i.test(raw) ? "draft" : "direct");
      return { kind: "publish", mode: mode, book: book, workdir: workdir,
               platform: plat, count: count, chapters: chapters,
               time: scheduleish ? ciParseTime(raw) : null };
    }

    // 3) 查询：书名 + 进度/字数/写到哪
    if (book && /进度|多少字|写到哪|写到哪里|字数|状态|怎么样|查一下|查查|看看/.test(raw)) {
      return { kind: "query", book: book };
    }
    return null;
  }
  /* == PARSER-END == */

  /* ------------------------------------------------ 数据/任务匹配 */
  function tasksList() {
    try {
      if (typeof S === "undefined" || !S || !S.state) return [];
      return ((S.state || {}).tasks || []);
    } catch (e) { return []; }
  }
  function runsList() {
    try {
      if (typeof S === "undefined" || !S || !S.state) return [];
      return ((S.state || {}).runs || []);
    } catch (e) { return []; }
  }
  function findTask(intent) {
    const tasks = tasksList();
    if (intent.book) {
      const hits = tasks.filter((tk) =>
        String(tk.title || "").indexOf(intent.book) >= 0);
      hits.sort((a, b) => String(b.updated_at || b.created_at || "")
        .localeCompare(String(a.updated_at || a.created_at || "")));
      return hits[0] || null;
    }
    if (intent.workdir) {
      const hits = tasks.filter((tk) =>
        String(tk.workdir || "") === intent.workdir &&
        /serial|novel/i.test(String(tk.type || "")));
      if (hits.length === 1) return hits[0];
      return null;
    }
    return null;
  }
  function latestRunOf(taskId) {
    const hits = runsList().filter((r) => r && r.task_id === taskId);
    hits.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    return hits[0] || null;
  }
  function runLabel(r) {
    if (!r) return "";
    const map = { running: "运行中", queued: "排队中", failed: "失败",
                  timeout: "超时", cancelled: "已取消" };
    return _t(map[r.status] || "已完成");
  }

  /* ------------------------------------------------ 卡片容器 */
  function ensureTurns() {
    let box = $("rd-chat-turns");
    if (box) return box;
    const feed = $("rd-chat-feed");
    const anchor = feed || document.querySelector("#page-home .rd-quick-start");
    if (!anchor || !anchor.parentElement) return null;
    box = document.createElement("div");
    box.id = "rd-chat-turns";
    box.className = "ci-turns";
    anchor.parentElement.insertBefore(box, anchor);   // composer → 对话回合 → 动态流
    return box;
  }

  /* 卡片骨架（只建壳，内容由各渲染器一次性 innerHTML 赋值） */
  function cardShell(cls) {
    const card = document.createElement("div");
    card.className = "ci-card " + (cls || "");
    return card;
  }

  function quoteLine(intent) {
    const goal = (($("f-goal") || {}).value || "").trim();
    return _esc(goal.slice(0, 80));
  }

  /* 计划确认卡（确认前零请求零动作；新的意图顶掉旧的待确认卡） */
  function renderConfirm(intent, lines) {
    const box = ensureTurns();
    if (!box) return null;
    box.querySelectorAll(".ci-card.ci-pending").forEach((c) => c.remove());
    const card = cardShell("ci-pending ci-confirm");
    card.innerHTML =
      '<div class="ci-head">' + _esc(_t("计划确认")) + "</div>" +
      '<div class="ci-quote">' + quoteLine(intent) + "</div>" +
      '<div class="ci-line">' + lines.join("<br>") + "</div>" +
      '<div class="ci-sub">' + _t("点「确认执行」之前，我不会动任何东西。") + "</div>" +
      '<div class="ci-actions">' +
      '<button type="button" class="ci-btn ci-cancel">' + _esc(_t("取消")) + "</button>" +
      '<button type="button" class="ci-btn ci-allow">' + _esc(_t("确认执行")) + "</button></div>";
    box.prepend(card);
    card.querySelector(".ci-cancel").addEventListener("click", () => {
      card.classList.remove("ci-pending", "ci-confirm");
      card.classList.add("ci-idle");
      card.querySelector(".ci-head").textContent = _t("已取消，什么都没执行。");
      card.querySelector(".ci-actions").remove();
      card.querySelector(".ci-sub").remove();
    });
    card.querySelector(".ci-allow").addEventListener("click", () => {
      runIntent(intent, card);
    });
    return card;
  }

  /* 结果卡（ok=绿点 / err=红点；可带下钻按钮） */
  function renderResult(ok, text, actions) {
    const box = ensureTurns();
    if (!box) return null;
    const card = cardShell(ok ? "ci-ok" : "ci-err");
    card.innerHTML =
      '<div class="ci-quote">' + quoteLine({}) + "</div>" +
      '<div class="ci-line">' + text + "</div>";
    const acts = (actions || []).filter((a) => a && a.label && a.act);
    if (acts.length) {
      const row = document.createElement("div");
      row.className = "ci-actions";
      acts.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ci-btn ci-ghost";
        b.textContent = a.label;
        b.addEventListener("click", () => {
          try { a.act(); } catch (e) { /* 下钻失败不打扰 */ }
        });
        row.appendChild(b);
      });
      card.appendChild(row);
    }
    box.prepend(card);
    return card;
  }

  /* 答案卡（查询意图；零模型，读本地 state + 字数接口） */
  function renderAnswer(html, actions) {
    const box = ensureTurns();
    if (!box) return null;
    const card = cardShell("ci-answer");
    card.innerHTML =
      '<div class="ci-head">' + _esc(_t("查到了")) + "</div>" +
      '<div class="ci-quote">' + quoteLine({}) + "</div>" +
      '<div class="ci-line">' + html + "</div>";
    const acts = (actions || []).filter((a) => a && a.label && a.act);
    if (acts.length) {
      const row = document.createElement("div");
      row.className = "ci-actions";
      acts.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ci-btn ci-ghost";
        b.textContent = a.label;
        b.addEventListener("click", () => {
          try { a.act(); } catch (e) { /* 静默 */ }
        });
        row.appendChild(b);
      });
      card.appendChild(row);
    }
    box.prepend(card);
    return card;
  }

  function openRunAction(taskId) {
    const r = latestRunOf(taskId);
    if (r && r.id && typeof window.homeOpenRun === "function") {
      return { label: _t("打开详情"), act: () => window.homeOpenRun(r.id) };
    }
    if (typeof window.switchTab === "function") {
      return { label: _t("看运行记录"), act: () => window.switchTab("runs") };
    }
    return null;
  }

  /* ------------------------------------------------ 执行器（只认「确认执行」） */
  function setBusy(card, busy) {
    if (!card) return;
    card.querySelectorAll(".ci-btn").forEach((b) => { b.disabled = !!busy; });
    const allow = card.querySelector(".ci-allow");
    if (allow && busy) allow.textContent = _t("正在执行…");
  }

  async function runIntent(intent, card) {
    setBusy(card, true);
    try {
      const task = findTask(intent);
      if (intent.kind === "publish") {
        if (!task) throw new Error(intent.book
          ? _fmt(_t("没找到《{0}》对应的任务。"), intent.book)
          : _t("这个目录下没有找到连载任务，说一下书名我再办。"));
        if (intent.mode === "schedule") {
          await execSchedule(intent, task, card);
        } else {
          await execPublish(intent, task, card);
        }
      } else if (intent.kind === "scan") {
        await execScan(intent, card);
      } else if (intent.kind === "unschedule") {
        if (!task) throw new Error(intent.book
          ? _fmt(_t("没找到《{0}》对应的任务。"), intent.book)
          : _t("没找到要操作的任务。"));
        const ap = apOf(task);
        if (!ap || !ap.enabled) {
          throw new Error(_t("这本书没挂定时，没什么可取消的。"));
        }
        await postSchedule(task.id, ap.platform, ap.time || "10:10", false);
        cardToResult(card, true,
          _esc(_t("已取消定时：到点不再自动发。")) +
          '<br><span class="ci-dim">' +
          _esc(_t("想挂回来说一声「定时发」就行。")) + "</span>",
          [openRunAction(task.id)]);
        clearGoal();
      } else if (intent.kind === "retry") {
        if (!task) throw new Error(intent.book
          ? _fmt(_t("没找到《{0}》对应的任务。"), intent.book)
          : _t("没找到要重试的任务，说一下书名我再办。"));
        if (String(task.status || "") !== "failed") {
          throw new Error(_t("这个任务不是失败态，没什么可重试的。"));
        }
        await fetchJSON("/api/tasks/" + encodeURIComponent(task.id) + "/retry",
          { method: "POST" });
        cardToResult(card, true, _esc(_t("已重新入队")) + "。", [openRunAction(task.id)]);
        clearGoal();
      } else if (intent.kind === "pause" || intent.kind === "resume") {
        const wantPaused = intent.kind === "pause";
        const r = latestRunOf(task ? task.id : "");
        const okRun = r && (r.status === "running" || r.status === "queued") &&
          (wantPaused ? !r.paused : !!r.paused);
        if (!task) throw new Error(intent.book
          ? _fmt(_t("没找到《{0}》对应的任务。"), intent.book)
          : _t("没找到要操作的任务。"));
        if (!okRun) {
          throw new Error(wantPaused
            ? _t("没有正在跑的轮次，没什么可暂停。")
            : _t("最近一轮不在暂停状态，不需要恢复。"));
        }
        await fetchJSON("/api/runs/" + encodeURIComponent(r.id) + "/pause",
          { method: "POST", body: { paused: wantPaused } });
        cardToResult(card, true,
          _esc(wantPaused ? _t("已暂停。") : _t("已恢复运行")) + "。",
          [openRunAction(task.id)]);
      }
    } catch (e) {
      cardToResult(card, false, _esc(_t("没办成：")) + _esc(e.message || e), []);
    }
  }

  function cardToResult(card, ok, html, actions) {
    if (!card) return;
    card.classList.remove("ci-pending", "ci-confirm");
    card.classList.add(ok ? "ci-ok" : "ci-err");
    card.querySelector(".ci-head").textContent = "";
    const sub = card.querySelector(".ci-sub");
    if (sub) sub.remove();
    const acts = card.querySelector(".ci-actions");
    if (acts) acts.remove();
    const line = card.querySelector(".ci-line");
    if (line) line.innerHTML = html;
    const as = (actions || []).filter(Boolean);
    if (as.length) {
      const row = document.createElement("div");
      row.className = "ci-actions";
      as.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ci-btn ci-ghost";
        b.textContent = a.label;
        b.addEventListener("click", () => {
          try { a.act(); } catch (e) { /* 静默 */ }
        });
        row.appendChild(b);
      });
      card.appendChild(row);
    }
  }

  /* ------------------------------------------------ 定时建单（task.auto_publish） */
  function scheduleSuccessHtml(intent, task, platform) {
    const platName = platform === "fanqie" ? _t("番茄") : _t("七猫");
    const what = intent.book ? "《" + intent.book + "》" : (task.title || task.id);
    return _esc(_fmt(_t("已挂上：每天 {0} 自动直发 {1}（{2}）。"),
      intent._hhmm, _esc(what), _esc(platName))) +
      '<br><span class="ci-dim">' +
      _esc(_t("今天这个点之后有待发章就会发；要关去任务详情页。")) + "</span>";
  }

  async function postSchedule(taskId, platform, hhmm, enabled) {
    await fetchJSON("/api/publish/task/" + encodeURIComponent(taskId) + "/auto-publish",
      { method: "POST",
        body: { platform: platform, time: hhmm, enabled: enabled !== false } });
  }

  /* ------------------------------------------------ 扫榜（选材官，走真实建任务） */
  function scanComposedGoal(intent) {
    const plat = intent.platform === "qimao" ? _t("七猫") : _t("番茄");
    const cat = intent.category || _t("热门总榜");
    const goal0 = (($("f-goal") || {}).value || "").trim();
    return _fmt(_t("扫描{0}{1}榜单热门题材：输出题材热度清单、竞争度评估与 5 个可写选题（每个带一句话卖点）。原始要求：{2}"),
      _esc(plat), _esc(cat), _esc(goal0));
  }

  async function execScan(intent, card) {
    const sel = $("f-type");
    const opt = sel && sel.querySelector
      ? sel.querySelector('option[value="rank_scan"]') : null;
    if (!opt) throw new Error(_t("当前形态没启用选材官，去专家面板看看。"));
    const composed = scanComposedGoal(intent);
    if (sel) sel.value = "rank_scan";
    try {
      if (typeof window.pickType === "function") window.pickType("rank_scan");
    } catch (e) { /* 联动失败不影响建任务 */ }
    const g = $("f-goal");
    if (g) g.value = composed;
    const run = (window.createTask && window.createTask.__ciOrig)
      ? window.createTask.__ciOrig
      : (typeof window.createTask === "function" ? window.createTask : null);
    if (!run) throw new Error(_t("建任务入口不可用。"));
    cardToResult(card, true,
      _esc(_t("已把结构化任务交给选材官起跑；进度会出现在对话流。")), []);
    run();   // 真实建任务路径（读 f-goal 同步取值后走全量编排）
  }

  async function execSchedule(intent, task, card) {
    let platform = intent.platform;
    if (!platform) {
      const d = await fetchJSON("/api/publish/task/" +
        encodeURIComponent(task.id) + "/pending");
      const plats = ((d.books || []))
        .filter((b) => b.bound || (b.pending || 0) > 0)
        .map((b) => b.platform);
      if (plats.length === 1) {
        platform = plats[0];
      } else if (plats.length > 1) {
        askSchedulePlatform(card, intent, task, plats);
        return;
      } else {
        throw new Error(_t("这本书还没在平台登记作品，先在详情页发一次或登记书。"));
      }
    }
    await postSchedule(task.id, platform, intent._hhmm);
    cardToResult(card, true, scheduleSuccessHtml(intent, task, platform),
      [openRunAction(task.id)]);
    clearGoal();
  }

  function askSchedulePlatform(card, intent, task, plats) {
    card.classList.remove("ci-confirm");
    const line = card.querySelector(".ci-line");
    const sub = card.querySelector(".ci-sub");
    if (sub) sub.remove();
    const acts = card.querySelector(".ci-actions");
    if (acts) acts.remove();
    const names = plats.map((p) => (p === "fanqie" ? _t("番茄") : _t("七猫")));
    if (line) {
      line.innerHTML = _esc(_fmt(_t("这本书在 {0} 都有登记——定时发哪家？"),
        names.join("、")));
    }
    const row = document.createElement("div");
    row.className = "ci-actions";
    plats.forEach((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ci-btn ci-ghost";
      b.textContent = p === "fanqie" ? _t("番茄") : _t("七猫");
      b.addEventListener("click", async () => {
        row.querySelectorAll(".ci-btn").forEach((x) => { x.disabled = true; });
        try {
          await postSchedule(task.id, p, intent._hhmm);
          cardToResult(card, true, scheduleSuccessHtml(intent, task, p), []);
          clearGoal();
        } catch (e) {
          cardToResult(card, false, _esc(_t("没办成：")) + _esc(e.message || e), []);
        }
      });
      row.appendChild(b);
    });
    card.appendChild(row);
  }

  async function execPublish(intent, task, card) {
    const d = await fetchJSON("/api/publish/task/" +
      encodeURIComponent(task.id) + "/pending");
    const books = (d.books || []).filter((b) => (b.pending || 0) > 0);
    let book = null;
    if (intent.platform) {
      book = books.find((b) => b.platform === intent.platform);
      if (!book) throw new Error(_fmt(_t("{0}上没有待发章。"),
        intent.platform === "fanqie" ? _t("番茄") : _t("七猫")));
    } else if (books.length === 1) {
      book = books[0];
    } else if (books.length > 1) {
      throw new Error(_fmt(_t("这本书在 {0} 都有待发章，说一声发哪个。"),
        books.map((b) => b.platform === "fanqie" ? _t("番茄") : _t("七猫")).join("、")));
    } else {
      throw new Error(_t("没有待发章，先跑完并过审才有得发。"));
    }
    const items = (book.items || []).slice()
      .sort((a, b) => (a.chapter_no || 0) - (b.chapter_no || 0));
    let chapters;
    if (intent.chapters && intent.chapters.length) {
      chapters = intent.chapters.filter((n) =>
        items.some((it) => (it.chapter_no || 0) === n));
      if (!chapters.length) throw new Error(
        _fmt(_t("第 {0} 章不在待发清单里。"), intent.chapters[0]));
    } else if (intent.count > 0) {
      chapters = items.slice(-intent.count).map((it) => it.chapter_no || 0);
    } else {
      chapters = items.map((it) => it.chapter_no || 0);
    }
    if (!chapters.length) throw new Error(_t("没有可发的章。"));
    chapters = chapters.slice().sort((a, b) => a - b);
    const platName = book.platform === "fanqie" ? _t("番茄") : _t("七猫");
    const body = intent.mode === "draft"
      ? { platform: book.platform, chapters, as_draft: true }
      : { platform: book.platform, chapters, auto_submit: true };
    await fetchJSON("/api/publish/task/" + encodeURIComponent(task.id) + "/publish-all",
      { method: "POST", body: body });
    if (intent.mode === "draft") {
      cardToResult(card, true, _esc(_t("存草稿已启动：逐章自动填稿并保存草稿，进度见发布台")) +
        "（" + _esc(platName) + " · " + _esc(_t("第 ")) + chapters[0] + "–" +
        chapters[chapters.length - 1] + _esc(_t(" 章")) + "）", []);
    } else {
      cardToResult(card, true, _esc(_t("直发已启动，进度见发布台")) +
        "（" + _esc(platName) + " · " + _esc(_t("第 ")) + chapters[0] + "–" +
        chapters[chapters.length - 1] + _esc(_t(" 章")) + "）",
        [openRunAction(task.id)]);
    }
    clearGoal();
  }

  /* ------------------------------------------------ 查询答案（零模型） */
  async function answerQuery(intent, task) {
    if (!task) {
      renderAnswer(_esc(_fmt(_t("没找到《{0}》对应的任务。"), intent.book)) +
        '<br><span class="ci-dim">' + _esc(_t("确定写过的话，去运行记录翻翻。")) + "</span>",
        [typeof window.switchTab === "function"
          ? { label: _t("看运行记录"), act: () => window.switchTab("runs") } : null]);
      return;
    }
    const r = latestRunOf(task.id);
    let wordsHtml = '<span class="ci-dim">…</span>';
    const rows = [
      "<b>" + _esc(task.title || task.id) + "</b>",
      _esc(_t("状态")) + "：" + _esc(runLabel(r) || String(task.status || "—")),
      r ? _esc(_t("最近一轮")) + "：" + _esc(runLabel(r)) +
        (r.step_count ? " · " + _esc(_t("第 ")) + ((r.current_step && r.current_step.n) || 0) +
          "/" + r.step_count + _esc(_t(" 步")) : "") : "",
    ].filter(Boolean);
    const ap = apOf(task);
    if (ap && ap.enabled) {
      rows.push(_esc(_t("定时")) + "：" + _fmt(_t("每天 {0} 自动直发（{1}）"), _esc(ap.time || ""),
        _esc(ap.platform === "fanqie" ? _t("番茄") : _t("七猫"))));
    }
    try {
      const d = await fetchJSON("/api/writing/counts?ids=" +
        encodeURIComponent(task.id));
      const c = ((d.counts || [])[0]) || (d.counts || {})[task.id] || null;
      const known = c && (c.known !== false) &&
        (c.words_human || c.words != null);
      wordsHtml = known
        ? _esc(c.words_human || String(c.words))
        : '<span class="ci-dim">—</span>';
    } catch (e) { wordsHtml = '<span class="ci-dim">—</span>'; }
    renderAnswer(rows.join("<br>") + "<br>" +
      _esc(_t("已写字数")) + "：" + wordsHtml, [openRunAction(task.id)]);
  }

  /* ------------------------------------------------ 意图分派（wrap 入口用） */
  function apOf(task) {
    return (task && typeof task.auto_publish === "object" && task.auto_publish) || null;
  }

  function handleIntent(intent) {
    const task = findTask(intent);
    if (intent.kind === "query") { answerQuery(intent, task); return true; }
    if (intent.kind === "unschedule") {
      const ap = apOf(task);
      if (!ap || !ap.enabled) {
        // 没挂定时：诚实直接说，不浪费一次确认（零请求）
        renderResult(true, _esc(_t("这本书没挂定时，没什么可取消的。")), []);
        return true;
      }
      const platName = ap.platform === "fanqie" ? _t("番茄") : _t("七猫");
      const target = intent.book ? "《" + _esc(intent.book) + "》"
        : '<span class="ci-dim">' + _esc(_t("当前目录的任务")) + "</span>";
      renderConfirm(intent, [
        _fmt(_t("取消 {0} 的定时发布（每天 {1} · {2}）。到点不再自动发。"),
          target, _esc(ap.time || ""), _esc(platName)),
      ]);
      return true;
    }
    if (intent.kind === "scan") {
      const platName = intent.platform
        ? (intent.platform === "fanqie" ? _t("番茄") : _t("七猫"))
        : _t("番茄");
      const cat = intent.category || _t("热门总榜");
      renderConfirm(intent, [
        _fmt(_t("用选材官扫 {0}{1}——任务会真跑（榜单调研 + 选题清单）。"),
          _esc(platName), _esc(cat)) +
        '<br><span class="ci-dim">' +
        _esc(_t("确认后我把结构化指令交给选材官起跑；进度见对话流。")) + "</span>",
      ]);
      return true;
    }
    if (intent.kind === "publish") {
      if (intent.mode === "schedule") {
        // 定时真建单：task.auto_publish（每天到点直发待发章，后端 fire_due 驱动）
        const t8 = intent.time || { hh: 10, mm: 10, def: true };
        const pad = (n) => (n < 10 ? "0" + n : "" + n);
        intent._hhmm = pad(t8.hh) + ":" + pad(t8.mm);
        const target = intent.book ? "《" + _esc(intent.book) + "》"
          : '<span class="ci-dim">' + _esc(_t("当前目录的任务")) + "</span>";
        const plat = intent.platform
          ? (intent.platform === "fanqie" ? _t("番茄") : _t("七猫"))
          : _t("已登记的平台");
        const lines = [_fmt(_t("每天 {0} 自动直发 {1} → {2}。"),
          _esc(intent._hhmm), target, _esc(plat))];
        const notes = [];
        if (intent.count > 0 && !(intent.chapters && intent.chapters.length)) {
          notes.push(_fmt(_t("定时到点会发全部待发章，不是只发 {0} 章；要只发几章就说「直发 N 章」。"),
            intent.count));
        }
        if (t8.def) {
          notes.push(_fmt(_t("时间没说，我按 {0}（直发安全窗）排的，要改就再说一句。"), intent._hhmm));
        }
        notes.push(_t("到点自动执行，无需再确认；要关就去任务详情页。"));
        lines.push('<span class="ci-dim">' + notes.map(_esc).join("<br>") + "</span>");
        renderConfirm(intent, lines);
        return true;
      }
      const lines = [];
      const what = intent.mode === "draft" ? _t("存草稿") : _t("直发");
      const target = intent.book ? "《" + _esc(intent.book) + "》"
        : '<span class="ci-dim">' + _esc(_t("当前目录的任务")) + "</span>";
      const want = intent.chapters && intent.chapters.length
        ? _fmt(_t("第 {0} 章"), intent.chapters[0])
        : (intent.count > 0 ? _fmt(_t("最新 {0} 章"), intent.count)
                            : _t("全部待发章"));
      const plat = intent.platform
        ? (intent.platform === "fanqie" ? _t("番茄") : _t("七猫"))
        : _t("已登记的平台");
      lines.push(_esc(what) + " " + target + " " + _esc(want) + " → " + _esc(plat) + "。");
      renderConfirm(intent, lines);
      return true;
    }
    // pause / resume / retry：控制类也要卡（可逆性不同但都是对别人产出的操作）
    const whatMap = { pause: _t("暂停"), resume: _t("恢复运行"),
                      retry: _t("重试") };
    const target2 = intent.book ? "《" + _esc(intent.book) + "》"
      : _t("最近失败的任务");
    renderConfirm(intent, [_esc(whatMap[intent.kind] || intent.kind) + " " + target2 + "。"]);
    return true;
  }

  function clearGoal() {
    const g = $("f-goal");
    if (g) g.value = "";
  }

  /* ------------------------------------------------ 模型 triage 兜底（C0 阶段③）
   * 确定性解析没接住、但「像命令」的短句，让编排者模型起草意图草稿。
   * 草稿绝不直接执行——照常走确认卡（闸门不因分类器弱而弱化）。
   * 门控纪律：创作动词/超长句零 triage（建任务路径零延迟零调用）。 */
  function triageGate(raw) {
    if (!raw || raw.length > 60) return false;
    if (CI_CREATION_GUARD.test(raw)) return false;
    return /《[^》]{1,40}》/.test(raw) ||
      /发|停|查|进度|字数|定时|重试|暂停|恢复|继续跑|自动发|排/.test(raw);
  }

  function renderThinking() {
    const box = ensureTurns();
    if (!box) return null;
    const card = cardShell("ci-thinking");
    card.innerHTML = '<div class="ci-line"><span class="ci-dim">' +
      _esc(_t("我看一下这句话想干什么…")) + "</span></div>";
    box.prepend(card);
    return card;
  }

  function bookCandidates() {
    const names = [];
    tasksList().forEach((tk) => {
      const m = String(tk.title || "").match(/《([^》]{1,40})》/);
      if (m && names.indexOf(m[1]) < 0) names.push(m[1]);
    });
    return names.slice(0, 30);
  }

  async function triageRemote(raw) {
    const headers = Object.assign(
      {}, (typeof window.authHeaders === "function" ? window.authHeaders() : null) || {},
      { "Content-Type": "application/json" });
    const ctrl = (typeof AbortController === "function") ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => { try { ctrl.abort(); } catch (e) { /* */ } }, 10000) : null;
    try {
      const res = await fetch("/api/intent/triage", {
        method: "POST", headers,
        signal: ctrl ? ctrl.signal : undefined,
        body: JSON.stringify({ text: raw, books: bookCandidates() }),
      });
      const d = await res.json().catch(() => ({}));
      const it = (!res.ok || !d) ? null : d.intent;
      if (!it || _KINDS.indexOf(String(it.kind || "")) < 0) return null;
      const out = { kind: String(it.kind), book: String(it.book || ""),
                    workdir: (($("f-workdir") || {}).value || "").trim(),
                    platform: String(it.platform || ""),
                    count: Number(it.count) || 0, chapters: null };
      if (out.kind === "publish") {
        out.mode = ["direct", "draft", "schedule"].indexOf(String(it.mode || "")) >= 0
          ? String(it.mode) : "direct";
        if (out.mode === "schedule") {
          const m = String(it.time || "").match(/^([01]?\d|2[0-3]):([0-5]\d)$/);
          out.time = m ? { hh: +m[1], mm: +m[2], def: false }
                       : { hh: 10, mm: 10, def: true };
        }
      }
      return out;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /* ------------------------------------------------ wrap createTask */
  function wrapCreateTask() {
    const orig = window.createTask;
    if (typeof orig !== "function" || orig.__ciWrapped) return;
    const wrapped = function () {
      let intent = null;
      let raw = "";
      try {
        if (typeof document === "undefined" ||
            !document.body || !document.body.classList ||
            document.body.classList.contains("mob-shell-on")) {
          return orig.apply(this, arguments);   // 手机壳不拦（卡片容器不在可视层）
        }
        raw = (($("f-goal") || {}).value || "").trim();
        const ctx = { workdir: (($("f-workdir") || {}).value || "").trim() };
        intent = ciParseIntent(raw, ctx);
      } catch (e) { intent = null; }
      if (intent) {
        try {
          if (handleIntent(intent)) return;              // 已接手（出卡，零动作）
        } catch (e) { /* 卡片渲染失败退回原路径，绝不吞提交 */ }
        return orig.apply(this, arguments);
      }
      if (!triageGate(raw)) return orig.apply(this, arguments);   // 建任务句零延迟直过
      // 模型 triage：起草意图草稿；确认卡仍是唯一闸门；失败 fail-open 照旧建任务
      const self = this, args = arguments;
      const think = renderThinking();
      return (async function () {
        let it = null;
        try { it = await triageRemote(raw); } catch (e) { it = null; }
        if (think && think.parentElement) think.remove();
        if (it) {
          try { if (handleIntent(it)) return; } catch (e) { /* fall */ }
        }
        return orig.apply(self, args);
      })();
    };
    wrapped.__ciWrapped = true;
    wrapped.__ciOrig = orig;   // scan 等执行器绕过本 wrap 直走真建任务
    window.createTask = wrapped;
  }

  function boot() {
    try { ensureTurns(); } catch (e) { /* 静默 */ }
    try { wrapCreateTask(); } catch (e) { /* 静默 */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
