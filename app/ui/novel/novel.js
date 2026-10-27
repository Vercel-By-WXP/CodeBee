/* CodeBee 小说版 · 作者端
   独立前端，不复用主工作台的 app.js。只消费 /api/profile、/api/flows、
   /api/state、/api/tasks、/api/runs/*，因此内核升级不需要改这里。
   原则：作者看到的是「书、章节、评审、能不能发布」，不是 CLI、模型和步骤。 */
(function () {
  "use strict";

  var TOKEN_KEY = "orch.token";

  var S = {
    profile: null,
    flows: [],
    tasks: [],
    latest: {},
    words: {},
    wordsAt: 0,
    shelfSig: "",
    view: "shelf",
    bookId: "",
    form: "short_story",
    run: null,
    files: [],
    workdir: "",
    pub: null,
    pubHist: null,
    pubStats: null,
    pubAt: 0,
    pubBusy: false,
    pubFail: null,
    out: null,
    outPlat: "",
    outStage: "",
    outBusy: false,
    kit: null,
    timer: null,
    busy: false
  };

  var PLAT_LABEL = { fanqie: "番茄", qimao: "七猫" };

  /* 平台状态 → 圆点样式 + 人话。后端的状态名是给机器看的，
     作者只想知道「现在能不能发」。 */
  var PLAT_STATE = {
    none: ["", "还没连"],
    connected: ["ok", "已就绪"],
    waiting_login: ["wait", "等你扫码登录"],
    busy: ["busy", "正在发布"],
    error: ["bad", "上次出错了"]
  };

  var FLOW_LABEL = {
    novel: "长篇",
    short_story: "短篇",
    serial_novel: "连载",
    rank_scan: "选材"
  };

  var STEP_TEXT = {
    plan: "在搭故事骨架",
    planner: "在搭故事骨架",
    outline: "在列分章大纲",
    write: "在写正文",
    writer: "在写正文",
    draft: "在写正文",
    review: "在评审",
    reviewer: "在评审",
    critics: "在评审",
    revise: "在按意见改稿",
    fix: "在按意见改稿",
    fixer: "在按意见改稿",
    verify: "在验收",
    publish: "在发布"
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  async function api(path, opt) {
    opt = opt || {};
    var headers = { "X-CodeBee-Token": localStorage.getItem(TOKEN_KEY) || "" };
    if (opt.body) headers["Content-Type"] = "application/json";
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, opt.timeout || 20000);
    try {
      var res = await fetch(path, {
        method: opt.method || "GET",
        headers: headers,
        body: opt.body,
        signal: ctrl.signal
      });
      var data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok) {
        var msg = (data && data.error) || ("请求失败（" + res.status + "）");
        throw new Error(msg);
      }
      return data || {};
    } finally {
      clearTimeout(timer);
    }
  }

  function toast(text) {
    var el = $("toast");
    el.textContent = text;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.add("hidden"); }, 2600);
  }

  function relTime(text) {
    if (!text) return "";
    var t = new Date(String(text).replace(" ", "T"));
    if (isNaN(t.getTime())) return "";
    var diff = (Date.now() - t.getTime()) / 1000;
    if (diff < 0) return "刚刚";
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    if (diff < 86400 * 30) return Math.floor(diff / 86400) + " 天前";
    return String(text).slice(0, 10);
  }

  function flowById(id) {
    for (var i = 0; i < S.flows.length; i++) {
      if (S.flows[i].id === id) return S.flows[i];
    }
    return null;
  }

  function flowName(id) {
    var f = flowById(id);
    return (f && f.name) || FLOW_LABEL[id] || id || "作品";
  }

  function targetWords(task) {
    if (task.story && task.story.words) return Number(task.story.words) || 0;
    if (task.serial) {
      var ch = Number(task.serial.chapters) || 0;
      var per = Number(task.serial.words_per_chapter) || 2500;
      return ch * per;
    }
    return 0;
  }

  /* 字数的两个口径要分开说：target 是承诺，words 是事实。界面永远不拿
     目标字数冒充进度——那是作者最容易一眼看穿的假数据。 */
  function fmtWords(n) {
    n = Number(n) || 0;
    if (n <= 0) return "";
    if (n >= 10000) return (n / 10000).toFixed(1) + " 万字";
    return n + " 字";
  }

  function wordsOf(id) {
    var w = S.words[id];
    return w && typeof w === "object" ? w : null;
  }

  function isSerial(task) {
    return task.type === "serial_novel" || !!(task.serial && task.serial.chapters);
  }

  /* 字数批量拉取：3 秒轮询书架时不能每次都读全稿，节流 15 秒；
     force（建完书/切回来）立即重取。 */
  async function ensureWords(force) {
    var now = Date.now();
    if (!force && now - (S.wordsAt || 0) < 15000) return;
    S.wordsAt = now;
    var ids = S.tasks.map(function (t) { return t.id; });
    if (!ids.length) { S.words = {}; return; }
    try {
      var d = await api("/api/writing/counts?ids=" + encodeURIComponent(ids.join(",")));
      S.words = d.counts || {};
    } catch (e) {
      /* 字数拿不到只是少一行信息，不该让整个书架挂掉 */
    }
  }

  function statusOf(task, run) {    var st = (run && run.status) || task.status || "";
    if (st === "running") return { cls: "running", text: "正在写" };
    if (st === "queued") return { cls: "running", text: "排队中" };
    if (st === "done") return { cls: "done", text: "已完成" };
    if (st === "failed") return { cls: "failed", text: "卡住了" };
    if (st === "paused") return { cls: "waiting", text: "已暂停" };
    if (st === "cancelled") return { cls: "", text: "已停止" };
    return { cls: "", text: st || "草稿" };
  }

  function nowText(task, run) {
    var st = (run && run.status) || task.status || "";
    if (st === "queued") return "排队中，很快就轮到这本。";
    if (st === "done") return "写完了，可以读了。";
    if (st === "failed") return "这一步没走通。下面的说明里有原因。";
    if (st === "paused") return "已暂停，随时可以继续。";
    if (st === "cancelled") return "已停止。";
    var step = run && run.current_step;
    if (step) {
      var role = String(step.role || "").toLowerCase();
      var head = STEP_TEXT[role] || "在推进";
      var summary = String(step.summary || "").replace(/\s+/g, " ").trim();
      return summary ? head + "：" + summary : head + "…";
    }
    return "刚刚开始，正在准备。";
  }

  function descOf(task) {
    var goal = String(task.goal || "").replace(/\s+/g, " ").trim();
    if (goal) return goal;
    // 扫榜选材允许方向留空（承诺「默认分析总榜热门题材」），别对作者说“没写目标”。
    if (task.type === "rank_scan") return "（没填方向，默认分析总榜热门题材）";
    return "（这本还没写目标）";
  }

  /* ---------- 数据 ---------- */

  async function refresh(force) {
    var data = await api("/api/state");
    S.state = data;
    S.latest = data.task_latest || {};
    var visible = {};
    (S.profile && S.profile.flows ? S.profile.flows : []).forEach(function (id) { visible[id] = true; });
    var all = (data.tasks || []).slice();
    S.tasks = all.filter(function (t) { return visible[t.type] !== false; });
    S.tasks.sort(function (a, b) {
      var ra = (S.latest[a.id] || {}).ended_at || a.created_at || "";
      var rb = (S.latest[b.id] || {}).ended_at || b.created_at || "";
      return String(rb).localeCompare(String(ra));
    });
    await ensureWords(force);
    $("conn-dot").className = "conn-dot live";
    renderView(force);
  }

  function renderView(force) {
    if (S.view === "book") {
      renderBook();
      loadPublish(force);
      return;
    }
    return renderShelf(force);
  }

  /* ---------- 书架 ---------- */

  function renderShelf(force) {
    var grid = $("shelf-grid");
    var empty = $("shelf-empty");
    var sub = $("shelf-sub");

    if (!S.tasks.length) {
      grid.innerHTML = "";
      grid.classList.add("hidden");
      empty.classList.remove("hidden");
      sub.textContent = "";
      return;
    }

    empty.classList.add("hidden");
    grid.classList.remove("hidden");

    var live = S.tasks.filter(function (t) {
      var st = (S.latest[t.id] || {}).status || t.status;
      return st === "running" || st === "queued";
    }).length;
    sub.textContent = S.tasks.length + " 本" + (live ? "，" + live + " 本正在写" : "");

    var sig = S.tasks.map(function (t) {
      var r = S.latest[t.id] || {};
      var w = wordsOf(t.id) || {};
      return t.id + "|" + (t.status || "") + "|" + (r.status || "") + "|" + (r.progress || 0) +
        "|" + (r.ended_at || "") + "|" + (w.words || 0) + "|" + (w.known ? 1 : 0);
    }).join("~");
    if (!force && sig === S.shelfSig) return;
    S.shelfSig = sig;

    grid.innerHTML = S.tasks.map(function (task) {
      var run = S.latest[task.id] || {};
      var st = statusOf(task, run);
      var real = wordsOf(task.id);
      var progress = Number(run.progress) || 0;
      if (st.cls === "done" && !progress) progress = 100;
      var when = relTime(run.ended_at || run.started_at || task.created_at);
      // 选材/分析类任务没有「已写多少字」这件事，别硬套一个「还没动笔」。
      var foot = "";
      if (task.type !== "rank_scan") {
        foot = real && real.known ? "已写 " + fmtWords(real.words) : "还没动笔";
      }
      if (foot && when) foot += " · ";
      if (when) foot += esc(when);
      return '' +
        '<button class="book-card" type="button" data-id="' + esc(task.id) + '">' +
          '<div class="card-top">' +
            '<span class="kind">' + esc(flowName(task.type)) + '</span>' +
            '<span class="state ' + st.cls + '">' + esc(st.text) + '</span>' +
          '</div>' +
          '<p class="book-name">' + esc(task.title || "未命名") + '</p>' +
          '<p class="book-desc">' + esc(descOf(task)) + '</p>' +
          '<div class="card-foot">' +
            '<span class="mini-bar"><i style="width:' + Math.max(2, progress) + '%"></i></span>' +
            '<span>' + foot + '</span>' +
          '</div>' +
        '</button>';
    }).join("");
  }

  /* ---------- 详情 ---------- */

  async function renderBook() {
    var task = currentTask();
    if (!task) { showShelf(); return; }

    var run = S.latest[task.id] || {};
    var st = statusOf(task, run);

    $("book-kind").textContent = flowName(task.type);
    $("book-title").textContent = task.title || "未命名";
    $("book-goal").textContent = String(task.goal || "").replace(/\s+/g, " ").slice(0, 260);
    var pill = $("book-status");
    pill.className = "pill " + st.cls;
    pill.textContent = st.text;

    $("book-now").textContent = nowText(task, run);
    var progress = Number(run.progress) || 0;
    if (st.cls === "done" && !progress) progress = 100;
    $("book-bar").style.width = Math.max(2, progress) + "%";
    $("book-progress-text").textContent = progress + "%" +
      (run.step_count ? " · 已完成 " + run.step_count + " 个环节" : "");

    var words = targetWords(task);
    var real = wordsOf(task.id) || {};
    var rows = [
      ["类型", flowName(task.type)],
      ["已写", real.known
        ? fmtWords(real.words) + (real.chapters > 1 ? " · " + real.chapters + " 章" : "")
        : "—"],
      ["目标", words ? fmtWords(words) : "未设定"],
      ["开始于", relTime(task.created_at) || "—"],
      ["最近更新", relTime(run.ended_at || run.started_at) || "—"]
    ];
    if (task.serial && task.serial.chapters) {
      rows.splice(4, 0, ["章数", task.serial.chapters + " 章"]);
    }
    $("book-kv").innerHTML = rows.map(function (row) {
      return "<dt>" + esc(row[0]) + "</dt><dd>" + esc(row[1]) + "</dd>";
    }).join("");

    if (run.id) {
      api("/api/runs/" + encodeURIComponent(run.id)).then(function (r) {
        if (S.currentBook() !== task.id) return;
        S.run = r.run || null;
        renderReview();
      }).catch(function () { /* 详情拿不到不影响主视图 */ });
      api("/api/runs/" + encodeURIComponent(run.id) + "/files").then(function (d) {
        if (S.currentBook() !== task.id) return;
        S.files = d.files || [];
        S.workdir = d.workdir || "";
        renderFiles();
      }).catch(function () { /* 同上 */ });
    } else {
      S.run = null; S.files = []; S.workdir = "";
      renderReview(); renderFiles();
    }

    loadPublish();
  }

  S.currentBook = function () { return S.bookId; };

  function renderReview() {
    var box = $("book-review");
    var run = S.run || {};
    var verdict = run.verdict;
    var lines = [];

    if (verdict && typeof verdict === "object") {
      var pass = verdict.publishable != null ? verdict.publishable : verdict.pass;
      if (pass === true) lines.push("结论：通过，可以发布。");
      else if (pass === false) lines.push("结论：还没达标，需要再改一轮。");
      var list = verdict.scores || verdict.dimensions || verdict.items;
      if (Array.isArray(list)) {
        list.forEach(function (item) {
          if (!item || typeof item !== "object") return;
          var name = item.name || item.dimension || item.dim || item.label;
          var score = item.score != null ? item.score : item.value;
          if (name) lines.push(String(name) + "：" + (score != null ? score : "—"));
        });
      }
      if (verdict.summary) lines.push("", String(verdict.summary));
    }

    if (!lines.length && run.summary) lines.push(String(run.summary));
    if (!lines.length && run.error) lines.push("出问题的环节：" + String(run.error));

    if (!lines.length) {
      box.className = "review-body empty-review";
      box.textContent = "还没有评审结果。写完后会自动进入评审。";
      return;
    }
    box.className = "review-body";
    box.textContent = lines.join("\n");
  }

  function fileName(f) {
    if (typeof f === "string") return f;
    if (!f || typeof f !== "object") return "";
    return String(f.name || f.path || f.rel || "");
  }

  function renderFiles() {
    var box = $("book-files");
    if (!S.files.length) {
      box.textContent = "还没有产出文件。";
      return;
    }
    box.innerHTML = S.files.slice(0, 60).map(function (f) {
      var name = fileName(f);
      if (!name) return "";
      return '<span class="file-chip done">' + esc(name) + '</span>';
    }).join("") || "还没有产出文件。";
  }

  /* ---------- 发布 ----------
     作者眼里发布只有一件事：稿子能不能上台面。所以这里不做「流程步骤表」，
     只回答四个问题——平台连没连、作品登记了没、点了之后成没成、失败了怎么办。
     底层仍走主工作台那套已经磨了 43 次事故的发布链，一行都不重写。 */

  var ACT_LABEL = {
    create_book: "建书",
    upload_chapter: "发一章",
    upload_chapter_scheduled: "定时发章",
    upload_chapter_draft: "存草稿",
    upload_short_story_draft: "存草稿",
    publish_short_story: "整篇发布",
    connect: "连接账号",
    probe: "检查表单",
    auto_fire: "定时触发"
  };
  var NOISE_ACTIONS = { connect: true, probe: true, auto_fire: true };

  function currentTask() {
    for (var i = 0; i < S.tasks.length; i++) {
      if (S.tasks[i].id === S.bookId) return S.tasks[i];
    }
    return null;
  }

  function pubPath(id, name) {
    return "/api/publish/task/" + encodeURIComponent(id) + "/" + name;
  }

  async function loadPublish(force) {
    if (S.view !== "book" || !S.bookId) return;
    var now = Date.now();
    // 发布是异步链，等待期间缩短轮询间隔，让作者能看见状态在动。
    var ttl = now < (S.pubUntil || 0) ? 2500 : 12000;
    if (!force && now - (S.pubAt || 0) < ttl) return;
    S.pubAt = now;
    var id = S.bookId;
    try {
      var got = await Promise.all([
        api("/api/publish"),
        api("/api/publish/task/" + encodeURIComponent(id) + "/history"),
        api("/api/publish/stats?days=7"),
        // 结果台账读不到不该拖垮发布面板（它是旁路），失败就给 null
        api("/api/publish/task/" + encodeURIComponent(id) + "/outcome")
          .catch(function () { return null; })
      ]);
      if (S.bookId !== id) return;
      S.pub = got[0] || {};
      S.pubHist = got[1] || {};
      S.pubStats = got[2] || {};
      S.out = got[3] || null;
      renderPublish();
    } catch (e) {
      var box = $("pub-plats");
      if (box) {
        box.innerHTML = '<p class="hint bad">读不到发布状态：' + esc(e.message || "接口没响应") + "</p>";
      }
    }
  }

  function renderPublish() {
    var task = currentTask();
    if (!task) return;
    var view = S.pub || {};
    var plats = view.platforms || {};
    var books = (S.pubHist && S.pubHist.books) || {};
    var published = (S.pubHist && S.pubHist.published) || {};
    var serial = isSerial(task);
    var noBrowser = view.browser_found === false;

    var html = ["fanqie", "qimao"].map(function (pid) {
      var p = plats[pid] || {};
      var state = String(p.status || "none");
      var meta = PLAT_STATE[state] || ["", state];
      var book = books[pid] || {};
      var live = state === "connected" || state === "busy";

      var info = "还没登记作品";
      if (book.book_id || book.title) {
        info = '<span class="book-ref">《' + esc(book.title || "已登记") + "》</span>";
        var n = Number(published[pid]) || 0;
        if (n) info += " · 已在平台发 " + n + " 章";
      } else if (serial) {
        info = "还没建书，先建书才能发章";
      } else {
        info = "第一次发布会自动建一本短故事";
      }

      var actions = "";
      if (!live) {
        actions += '<button class="btn' + (state === "connected" ? "" : " btn-primary") +
          '" type="button" data-act="connect" data-plat="' + pid + '"' +
          (noBrowser ? " disabled" : "") + ">" +
          (state === "none" ? "登录" : "重新登录") + "</button>";
      }
      if (serial) {
        if (!book.book_id) {
          actions += '<button class="btn btn-primary" type="button" data-act="create-book"' +
            ' data-plat="' + pid + '"' + (live ? "" : " disabled") + ">建书</button>";
        } else {
          actions += '<button class="btn btn-primary" type="button" data-act="publish-all"' +
            ' data-plat="' + pid + '"' + (live ? "" : " disabled") + ">发布待发章节</button>";
        }
      } else {
        actions += '<button class="btn btn-primary" type="button" data-act="short-publish"' +
          ' data-plat="' + pid + '"' + (live ? "" : " disabled") + ">发布整篇</button>";
      }

      var errLine = "";
      if (state === "error" && p.error) {
        errLine = ' <span class="em">' + esc(String(p.error).slice(0, 70)) + "</span>";
      }
      return '<div class="pub-row">' +
        '<div class="pub-plat"><span class="pub-dot ' + meta[0] + '"></span>' +
          "<strong>" + esc(PLAT_LABEL[pid] || pid) + "</strong></div>" +
        '<div class="pub-info">' + esc(meta[1]) + " · " + info + errLine + "</div>" +
        actions +
        "</div>";
    }).join("");

    $("pub-plats").innerHTML = html;
    renderPubRate();
    renderPubHint(serial, noBrowser);
    renderPubLog();
    renderOutcome();
  }

  /* ---------- 结果回流 ----------
     平台对这本书的判决（审核/签约/阅读），以及「下次同题材开书时评委会
     看到的那段话」。两件事必须分开显示：
     · 进度（已发/在审/未通过章数）是校准自动读回来的客观事实；
     · 结论（通过没通过、签没签）只认作者登记——平台没把「整本被拒」摆到
       章节列表上，猜出来的结论会让人拿一句没发生过的拒签去改稿。 */
  function renderOutcome() {
    var box = $("out-plats");
    if (!box) return;
    var view = S.out;
    if (!view) {
      box.innerHTML = '<p class="hint">结果台账读不到（不影响发布）。</p>';
      $("out-rate").textContent = "";
      $("out-preview-box").hidden = true;
      return;
    }
    var plats = view.platforms || {};
    var books = (S.pubHist && S.pubHist.books) || {};

    box.innerHTML = ["fanqie", "qimao"].map(function (pid) {
      var p = plats[pid] || {};
      var book = books[pid] || {};
      var kind = String(p.kind || "none");
      var pill = '<span class="out-pill ' + kind + '">' + esc(p.label || "未登记") + "</span>";

      var facts = [];
      if (book.title) facts.push("《" + esc(book.title) + "》");
      if (p.category) facts.push(esc(p.category));
      if (p.words) facts.push(p.words + " 字");
      if (p.reads != null) facts.push("阅读 " + p.reads);
      var progress = [];
      if (p.live) progress.push("已发 " + p.live + " 章");
      if (p.pending) progress.push(p.pending + " 章在审");
      if (p.rejected) progress.push(p.rejected + " 章未通过");
      if (!facts.length && !progress.length) {
        facts.push(book.book_id ? "还没登记结果" : "还没在平台建书");
      }

      var line = facts.join(" · ");
      if (progress.length) {
        line += (line ? " · " : "") + '<span class="out-prog">' + progress.join(" / ") + "</span>";
      }
      if (p.quote) {
        line += '<span class="out-quote">平台原话：' + esc(p.quote.slice(0, 160)) + "</span>";
      } else if (kind === "bad") {
        line += '<span class="out-quote bad">还没记平台原话——下次评审只知道被拒过，不知道该改哪。</span>';
      }
      if (p.at) line += '<span class="out-when">登记于 ' + esc(p.at) + "</span>";

      return '<div class="pub-row">' +
        '<div class="pub-plat"><strong>' + esc(PLAT_LABEL[pid] || pid) + "</strong></div>" +
        '<div class="pub-info">' + pill + " " + line + "</div>" +
        '<button class="btn" type="button" data-out="' + pid + '">' +
          (p.stage ? "更新结果" : "登记结果") + "</button>" +
        "</div>";
    }).join("");

    // 预览里每条记录一行「结果：…」，据此数条数（比后端再开一个字段省事，
    // 也不会出现「界面说 2 条、实际注入 1 条」的不一致）
    var n = ((view.preview || "").match(/结果：/g) || []).length;
    $("out-rate").textContent = n
      ? "下次开同题材的书会带上 " + n + " 条"
      : "还没有可用于下次的记录";
    $("out-rate").className = "pub-rate" + (view.preview ? " good" : "");

    var preview = $("out-preview-box");
    if (view.preview) {
      $("out-preview").textContent = view.preview;
      preview.hidden = false;
    } else {
      preview.hidden = true;
      $("out-preview").textContent = "";
    }
  }

  function outView() {
    return (S.out && S.out.stages) || [];
  }

  function openOutcome(plat) {
    var view = S.out || {};
    S.outPlat = plat || "fanqie";
    S.outStage = ((view.platforms || {})[S.outPlat] || {}).stage || "";
    $("out-msg").textContent = "";
    $("btn-save-out").disabled = false;
    renderOutChips();
    $("f-quote").value = "";
    $("f-reads").value = "";
    $("f-note").value = "";
    var cur = (view.platforms || {})[S.outPlat] || {};
    // 已经登记过的，把上次的原文带出来方便改（清空要自己删，防手滑丢证据）
    if (cur.quote) $("f-quote").value = cur.quote;
    if (cur.reads != null) $("f-reads").value = cur.reads;
    if (cur.note) $("f-note").value = cur.note;
    $("out-mask").classList.remove("hidden");
    $("f-quote").focus();
  }

  function closeOutcome() {
    $("out-mask").classList.add("hidden");
  }

  function renderOutChips() {
    var plats = ["fanqie", "qimao"];
    $("out-plat-chips").innerHTML = plats.map(function (pid) {
      return '<button type="button" class="chip' + (pid === S.outPlat ? " is-on" : "") +
        '" data-out-plat="' + pid + '">' + esc(PLAT_LABEL[pid]) + "</button>";
    }).join("");

    var stages = outView();
    $("out-stage-chips").innerHTML = stages.map(function (s) {
      return '<button type="button" class="chip' + (s.key === S.outStage ? " is-on" : "") +
        '" data-out-stage="' + esc(s.key) + '">' + esc(s.label) + "</button>";
    }).join("");

    var cur = null;
    for (var i = 0; i < stages.length; i++) if (stages[i].key === S.outStage) cur = stages[i];
    $("out-stage-hint").textContent = cur && cur.needs_quote
      ? "这一项建议一定贴上平台原话——被拒的具体理由，下一本书只能从这里学到。"
      : "";
  }

  async function saveOutcome() {
    var task = currentTask();
    if (!task || S.outBusy) return;
    if (!S.outStage) {
      $("out-msg").textContent = "先选一个结果。";
      return;
    }
    S.outBusy = true;
    $("btn-save-out").disabled = true;
    $("out-msg").textContent = "正在存…";
    try {
      var got = await api(pubPath(task.id, "outcome"), {
        method: "POST",
        timeout: 30000,
        body: JSON.stringify({
          platform: S.outPlat,
          stage: S.outStage,
          quote: $("f-quote").value,
          reads: $("f-reads").value,
          note: $("f-note").value
        })
      });
      S.out = got || null;
      renderOutcome();
      closeOutcome();
      toast($("f-quote").value.trim()
        ? "记下了。下次开同题材的书，评审会带着这条经验看稿。"
        : "记下了。补上平台原话，下一本才学得到具体该改什么。");
    } catch (e) {
      $("out-msg").textContent = e.message || "没存上，再试一次。";
      $("btn-save-out").disabled = false;
    } finally {
      S.outBusy = false;
    }
  }

  /* 成功率是产品级指标（目标 95%），所以它常驻显示而不是藏在设置里。
     live 口径只含「上台面」的动作，草稿不计入——否则草稿失败会掩盖真相。 */
  function renderPubRate() {
    var el = $("pub-rate");
    var live = (S.pubStats && S.pubStats.live) || {};
    var goal = Number((S.pubStats && S.pubStats.goal) || 0.95);
    if (!live.attempts) {
      el.className = "pub-rate";
      el.textContent = "近 7 天还没有发布记录";
      return;
    }
    var rate = live.rate == null ? 0 : live.rate;
    var ok = rate >= goal;
    el.className = "pub-rate " + (ok ? "good" : "low");
    el.innerHTML = "近 7 天 <b>" + Math.round(rate * 100) + "%</b> 成功（" +
      live.ok + "/" + live.attempts + "，目标 " + Math.round(goal * 100) + "%）";
  }

  function renderPubHint(serial, noBrowser) {
    var hint = $("pub-hint");
    var retry = $("pub-retry");
    var manual = $("pub-manual");
    var task = currentTask();

    /* 「手动发」的出场条件：这次点失败了、这台机器没浏览器、或者最近一次
       上线动作就是失败。其余时候不铺按钮——它是保命出口，不是主路径。 */
    var failPlat = "";
    if (S.pubFail) failPlat = S.pubFail.plat;
    else if (noBrowser) failPlat = "fanqie";
    else {
      var lastBad = ((S.pubHist && S.pubHist.history) || []).filter(function (r) {
        return r && r.ok === false && !NOISE_ACTIONS[r.action];
      })[0];
      if (lastBad) failPlat = lastBad.platform || "fanqie";
    }
    manual.hidden = !failPlat || !task;
    if (failPlat) manual.setAttribute("data-plat", failPlat);

    if (S.pubFail) {
      hint.className = "hint bad";
      hint.textContent = S.pubFail.error;
      retry.hidden = false;
      retry.textContent = "重试「" + S.pubFail.label + "」";
      return;
    }
    retry.hidden = true;

    if (noBrowser) {
      hint.className = "hint bad";
      hint.textContent = "这台机器上没找到可用的浏览器（需要 Chrome 或 Edge），" +
        "发布走不了——点「手动发」，把稿子和建书资料拿出来自己发一次。";
      return;
    }

    var st = (((S.pub || {}).platforms || {}).fanqie || {}).status || "";
    if (st === "waiting_login") {
      hint.className = "hint";
      hint.textContent = "浏览器已经打开了，扫码登录后回到这一页，状态会自己变。";
      return;
    }
    if (S.pubBusy) {
      hint.className = "hint";
      hint.textContent = "正在把稿子交上去，几分钟内页面会自己刷新。";
      return;
    }
    hint.className = "hint";
    if (!task) { hint.textContent = ""; return; }
    var plats = (S.pub || {}).platforms || {};
    var anyLive = Object.keys(plats).some(function (k) {
      var s = String((plats[k] || {}).status || "");
      return s === "connected" || s === "busy";
    });
    if (!anyLive) {
      hint.textContent = "还没连平台。点「登录」会打开番茄后台，扫码后回到这一页就行。";
      return;
    }
    var registered = Object.keys((S.pubHist && S.pubHist.books) || {}).filter(function (k) {
      var b = S.pubHist.books[k] || {};
      return b.book_id || b.title;
    });
    if (registered.length) {
      hint.textContent = serial
        ? "已经发过的章会自动跳过，重试只补没发出去的那些。"
        : "整篇发布会建书、上传正文、设封面并提交审核，中途失败可以原样重试。";
    } else {
      hint.textContent = serial
        ? "第一次发布要先建书：填书名、简介、分类，之后每章都能续发。"
        : "第一次发布会先在番茄建一本短故事，再把整篇稿子交上去。";
    }
  }

  function renderPubLog() {
    var box = $("pub-log");
    var rows = ((S.pubHist && S.pubHist.history) || []).filter(function (r) {
      if (!r || typeof r !== "object") return false;
      if (NOISE_ACTIONS[r.action] && r.ok) return false;
      return true;
    }).slice(0, 6);

    if (!rows.length) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = '<p class="pub-log-title">最近发布</p>' + rows.map(function (r) {
      var label = ACT_LABEL[r.action] || String(r.action || "发布");
      var head = label + " · " + (PLAT_LABEL[r.platform] || r.platform || "");
      if (r.chapter_no) head += " · 第 " + r.chapter_no + " 章";
      var tail = r.ok ? "成功" : '<span class="why">' + esc(String(r.error || "失败").slice(0, 80)) + "</span>";
      return '<div class="pub-item' + (r.ok ? "" : " bad") + '">' +
        '<span class="when">' + esc(relTime(r.ts) || "") + "</span>" +
        "<span>" + esc(head) + " · " + tail + "</span></div>";
    }).join("");
  }

  async function runPub(act, plat) {
    var task = currentTask();
    if (!task || S.pubBusy) return;
    var id = task.id;
    var serial = isSerial(task);
    var path, label, body = { platform: plat };
    if (act === "connect") {
      path = "/api/publish/" + plat + "/connect";
      label = "登录";
      body = {};
    } else if (act === "create-book") {
      path = pubPath(id, "create-book");
      label = "建书";
    } else if (act === "publish-all") {
      path = pubPath(id, "publish-all");
      label = "发布待发章节";
    } else {
      path = pubPath(id, "short-story-publish");
      label = "发布整篇";
    }

    S.pubBusy = true;
    S.pubFail = null;
    renderPubHint(serial, false);
    $("pub-retry").hidden = true;
    $("pub-hint").className = "hint";
    $("pub-hint").textContent = "正在" + label + "…";
    try {
      await api(path, { method: "POST", body: JSON.stringify(body), timeout: 90000 });
      S.pubUntil = Date.now() + 180000;       // 两分钟内每 2.5 秒看一次
      S.pubAt = 0;
      toast(act === "connect"
        ? "浏览器正在打开，请在弹出的窗口里扫码登录。"
        : label + "已开始，页面会自动更新。");
    } catch (e) {
      S.pubFail = { act: act, plat: plat, label: label, error: e.message || (label + "没成功") };
    } finally {
      S.pubBusy = false;
      await loadPublish(true);
    }
  }

  /* ---------- 手动发布包（人工兜底）
     发布链是模拟点平台页面，平台一改版就整条打不通，而这时作者已经写完
     三万字。这里不做任何自动化：把建书字段和按平台规格切好的正文摊开给他，
     他自己去平台粘一遍。切法在后端复用发布链同一套逻辑，前端只管贴出来。 */

  async function openKit(plat) {
    var task = currentTask();
    if (!task) return;
    $("kit-mask").classList.remove("hidden");
    $("kit-title").textContent = "手动发布包 · " + (PLAT_LABEL[plat] || plat);
    $("kit-sub").textContent = "正在整理稿子和建书资料…";
    $("kit-body").innerHTML = '<p class="hint">读稿中，三万字大约要一秒。</p>';
    $("kit-download").hidden = true;
    $("kit-open").hidden = true;
    $("kit-msg").textContent = "";
    $("kit-msg").className = "msg";
    try {
      var data = await api("/api/publish/task/" + encodeURIComponent(task.id) +
        "/manual-kit?platform=" + encodeURIComponent(plat), { timeout: 30000 });
      S.kit = data;
      renderKit(data);
    } catch (e) {
      $("kit-msg").className = "msg";
      $("kit-msg").textContent = "整理失败：" + (e.message || "接口没响应");
      $("kit-body").innerHTML = "";
    }
  }

  function closeKit() {
    $("kit-mask").classList.add("hidden");
  }

  function kitWholeText(d) {
    // 短故事的编辑器只有一个正文框，分节标记要单独占一行——和生产链一致。
    return (d.chapters || []).map(function (c) {
      return (c.label ? c.label + "\n" : "") + c.text;
    }).join("\n\n");
  }

  function kitBookText(d) {
    return ((d.book || {}).fields || []).filter(function (r) {
      return r.value;
    }).map(function (r) {
      return r.label + "：" + r.value;
    }).join("\n");
  }

  function renderKit(d) {
    var stat = d.stats || {};
    $("kit-title").textContent = "手动发布包 · 《" + (d.title || "未命名") + "》";
    $("kit-sub").textContent = (d.platform_label || "") + " · " +
      (d.kind_label || "") + " · " + stat.chapters + " 章 / " + stat.words + " 字";

    var html = [];
    if (d.missing && d.missing.length) {
      html.push('<div class="kit-note bad"><b>先把这几处补上，再去平台粘：</b><ul>' +
        d.missing.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") +
        "</ul></div>");
    } else {
      html.push('<div class="kit-note good">材料齐备：下面的字段和正文可以直接粘到平台表单。</div>');
    }

    // 建书资料（「还没生成」只在顶部的待补齐里说一次，不在这里重复一遍）
    html.push('<section class="kit-sec"><div class="kit-sec-head">' +
      "<h3>建书资料</h3>" +
      '<button class="btn kit-copy" type="button" data-copy="book">复制全部</button></div>');
    html.push('<dl class="kit-fields">');
    ((d.book || {}).fields || []).forEach(function (r) {
      if (!r.value && !r.required) return;
      html.push('<div class="kit-field' + (r.ok ? "" : " bad") + '">' +
        "<dt>" + esc(r.label) + "</dt>" +
        '<dd><span class="kit-val">' + (r.value ? esc(r.value) : "（必填，还没有）") +
        "</span>" + (r.problem ? '<span class="kit-why">' + esc(r.problem) + "</span>" : "") +
        "</dd>" +
        (r.value ? '<button class="btn kit-copy" type="button" data-copy="field" data-key="' +
          esc(r.key) + '">复制</button>' : "") +
        "</div>");
    });
    html.push("</dl>");
    if (d.cover) {
      html.push('<p class="hint">封面文件：<code>' + esc(d.cover) + "</code>（上传时要手动选它）</p>");
    } else if (d.kind === "short") {
      html.push('<p class="hint bad">还没有封面。番茄的短故事必须传封面才能发布，' +
        "先在 CodeBee 里生成一张，或自己准备一张方图。</p>");
    }
    html.push("</section>");

    // 正文（连载是一章一贴，没有「整篇」这个概念，就不放那颗按钮）
    html.push('<section class="kit-sec"><div class="kit-sec-head">' +
      "<h3>正文</h3>" +
      (d.kind === "short"
        ? '<button class="btn kit-copy" type="button" data-copy="whole">' +
          "复制整篇（含分节标记）</button>"
        : "") +
      "</div>");
    var spec = d.spec || {};
    if (d.kind === "short" && spec.hard) {
      html.push('<p class="hint">平台要求每章 ' + spec.min + "~" + spec.max +
        " 字、至少 2 章，下面已按这个规格切好。</p>");
    } else if (d.kind === "short") {
      html.push('<p class="hint">平台要求整篇 ' + (spec.body_min || "1000") +
        " 字起；分节只是阅读分隔，每节约 " + (spec.target || 1500) + " 字。</p>");
    } else {
      html.push('<p class="hint">平台要求单章 ≥1000 字。逐章粘贴，标题用下面的章名。</p>');
    }
    (d.chapters || []).forEach(function (c, i) {
      html.push('<details class="kit-ch"' + (i === 0 ? " open" : "") + ">" +
        "<summary><span>" + esc(c.title || ("第 " + c.no + " 章")) +
        (c.label ? '<i class="kit-mark">标记「' + esc(c.label) + "」</i>" : "") + "</span>" +
        '<span class="kit-meta">' + c.words + " 字</span>" +
        '<button class="btn kit-copy" type="button" data-copy="ch" data-idx="' + i +
        '">复制</button></summary>' +
        (c.problem ? '<p class="hint bad">' + esc(c.problem) + "</p>" : "") +
        "<pre>" + esc(c.text) + "</pre></details>");
    });
    html.push("</section>");

    // 步骤
    html.push('<section class="kit-sec"><div class="kit-sec-head"><h3>去平台手动发</h3></div>' +
      '<ol class="kit-steps">' + (d.steps || []).map(function (s) {
        return "<li>" + esc(s) + "</li>";
      }).join("") + "</ol></section>");

    $("kit-body").innerHTML = html.join("");

    var base = "/api/publish/task/" + encodeURIComponent(d.task_id) +
      "/manual-kit?platform=" + encodeURIComponent(d.platform) + "&format=txt";
    var tok = localStorage.getItem(TOKEN_KEY) || "";
    $("kit-download").href = base + (tok ? "&token=" + encodeURIComponent(tok) : "");
    $("kit-download").hidden = false;
    var manage = (d.urls || {}).manage || (d.urls || {}).home || "";
    if (manage) { $("kit-open").href = manage; $("kit-open").hidden = false; }
    $("kit-msg").className = "msg ok";
    $("kit-msg").textContent = "粘完记得回这一页看一眼平台状态。";
  }

  async function copyText(text, done) {
    try {
      await navigator.clipboard.writeText(text);
      toast(done || "已复制");
      return;
    } catch (e) { /* 非 https / 无权限时走下面的兼容路径 */ }
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    toast(ok ? (done || "已复制") : "复制失败，请手动选中文字");
  }

  function kitCopy(ev) {
    var btn = ev.target.closest("[data-copy]");
    if (!btn || !S.kit) return;
    ev.preventDefault();
    var kind = btn.getAttribute("data-copy");
    var d = S.kit;
    if (kind === "book") return copyText(kitBookText(d), "建书资料已复制");
    if (kind === "whole") return copyText(kitWholeText(d), "整篇正文已复制");
    if (kind === "field") {
      var key = btn.getAttribute("data-key");
      var row = ((d.book || {}).fields || []).filter(function (r) { return r.key === key; })[0];
      return copyText(row ? row.value : "", "已复制");
    }
    if (kind === "ch") {
      var ch = (d.chapters || [])[parseInt(btn.getAttribute("data-idx"), 10)];
      return copyText(ch ? ch.text : "", "这一章已复制");
    }
  }

  /* ---------- 视图切换 ---------- */

  function showShelf() {
    S.view = "shelf";
    S.bookId = "";
    closeKit();
    S.kit = null;
    $("view-shelf").classList.remove("hidden");
    $("view-book").classList.add("hidden");
    if (location.hash !== "#/") history.replaceState(null, "", "#/");
    renderShelf(true);
  }

  function showBook(id) {
    S.view = "book";
    S.bookId = id;
    closeKit();
    S.kit = null;
    S.run = null; S.files = []; S.workdir = "";
    S.pub = null; S.pubHist = null; S.pubStats = null; S.pubAt = 0;
    S.pubFail = null;
    S.out = null;
    $("pub-plats").innerHTML = '<p class="hint">正在读平台状态…</p>';
    $("out-plats").innerHTML = '<p class="hint">正在读结果…</p>';
    $("out-rate").textContent = "";
    $("out-preview-box").hidden = true;
    $("pub-hint").textContent = "";
    $("pub-retry").hidden = true;
    $("pub-manual").hidden = true;
    $("pub-log").innerHTML = "";
    $("view-shelf").classList.add("hidden");
    $("view-book").classList.remove("hidden");
    if (location.hash !== "#/book/" + id) history.replaceState(null, "", "#/book/" + id);
    renderBook();
    window.scrollTo(0, 0);
  }

  function route() {
    var m = /^#\/book\/(.+)$/.exec(location.hash || "");
    if (m) showBook(decodeURIComponent(m[1]));
    else showShelf();
  }

  /* ---------- 开新书 ---------- */

  function openComposer() {
    $("composer-mask").classList.remove("hidden");
    $("f-goal").value = "";
    $("composer-msg").textContent = "";
    setForm(S.profile && S.profile.default_flow === "serial_novel" ? "serial_novel" : "short_story");
    setTimeout(function () { $("f-goal").focus(); }, 40);
  }

  function closeComposer() {
    $("composer-mask").classList.add("hidden");
  }

  function setForm(id) {
    S.form = id;
    Array.prototype.forEach.call($("f-form").querySelectorAll(".chip"), function (chip) {
      chip.classList.toggle("is-on", chip.getAttribute("data-form") === id);
    });
    $("row-words").classList.toggle("hidden", id !== "short_story");
    $("row-chapters").classList.toggle("hidden", id !== "serial_novel");
    var flow = flowById(id);
    if (id === "short_story" && flow && flow.story && flow.story.words) {
      $("f-words").value = flow.story.words;
    }
    if (id === "serial_novel" && flow && flow.serial && flow.serial.chapters) {
      $("f-chapters").value = flow.serial.chapters;
    }
  }

  async function startBook() {
    if (S.busy) return;
    var goal = ($("f-goal").value || "").trim();
    if (!goal) {
      $("composer-msg").textContent = "先写一句你想写的故事。";
      $("f-goal").focus();
      return;
    }
    var payload = { type: S.form, goal: goal };
    if (S.form === "short_story") {
      payload.story = { words: Math.max(2000, Math.min(50000, parseInt($("f-words").value, 10) || 12000)) };
      payload.story_words = payload.story.words;
    } else if (S.form === "serial_novel") {
      payload.serial = {
        chapters: Math.max(2, Math.min(60, parseInt($("f-chapters").value, 10) || 8)),
        words_per_chapter: 2500,
        variants: 1,
        branches: 1
      };
    }

    S.busy = true;
    var btn = $("btn-start");
    btn.disabled = true;
    $("composer-msg").textContent = "正在建书…";
    try {
      var r = await api("/api/tasks", {
        method: "POST",
        timeout: 60000,
        body: JSON.stringify(payload)
      });
      closeComposer();
      toast("书建好了，蜂群开始动笔。");
      await refresh(true);
      if (r.task_id) showBook(r.task_id);
    } catch (e) {
      $("composer-msg").textContent = e.message || "创建失败，请再试一次。";
    } finally {
      S.busy = false;
      btn.disabled = false;
    }
  }

  /* ---------- 启动 ---------- */

  async function init() {
    var qs = new URLSearchParams(location.search);
    var urlToken = qs.get("token");
    if (urlToken) localStorage.setItem(TOKEN_KEY, urlToken);

    try {
      var pair = await Promise.all([api("/api/profile"), api("/api/flows")]);
      S.profile = pair[0] || {};
      S.flows = (pair[1] && pair[1].flows) || [];
    } catch (e) {
      $("conn-dot").className = "conn-dot dead";
      toast("连不上 CodeBee 服务，请确认它还在运行。");
      return;
    }

    if (S.profile.name) $("brand-name").textContent = S.profile.name;
    document.title = "书架 · " + (S.profile.name || "CodeBee 小说版");

    try { await refresh(true); } catch (e) { $("conn-dot").className = "conn-dot dead"; }
    route();

    $("btn-new").addEventListener("click", openComposer);
    $("btn-empty-new").addEventListener("click", openComposer);
    $("btn-close-composer").addEventListener("click", closeComposer);
    $("btn-start").addEventListener("click", startBook);
    $("btn-back").addEventListener("click", function () { location.hash = "#/"; });
    $("brand-link").addEventListener("click", function (ev) { ev.preventDefault(); location.hash = "#/"; });

    $("composer-mask").addEventListener("click", function (ev) {
      if (ev.target === $("composer-mask")) closeComposer();
    });

    $("f-form").addEventListener("click", function (ev) {
      var chip = ev.target.closest(".chip");
      if (chip) setForm(chip.getAttribute("data-form"));
    });

    $("row-words").addEventListener("click", function (ev) {
      var b = ev.target.closest(".step");
      if (!b) return;
      var input = $("f-words");
      input.value = Math.max(2000, Math.min(50000, (parseInt(input.value, 10) || 12000) + parseInt(b.getAttribute("data-step"), 10)));
    });

    $("row-chapters").addEventListener("click", function (ev) {
      var b = ev.target.closest(".step");
      if (!b) return;
      var input = $("f-chapters");
      input.value = Math.max(2, Math.min(60, (parseInt(input.value, 10) || 8) + parseInt(b.getAttribute("data-chapter-step"), 10)));
    });

    $("shelf-grid").addEventListener("click", function (ev) {
      var card = ev.target.closest(".book-card");
      if (card) showBook(card.getAttribute("data-id"));
    });

    $("pub-plats").addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-act]");
      if (!btn || btn.disabled) return;
      runPub(btn.getAttribute("data-act"), btn.getAttribute("data-plat"));
    });

    $("pub-retry").addEventListener("click", function () {
      if (!S.pubFail) return;
      runPub(S.pubFail.act, S.pubFail.plat);
    });

    $("pub-manual").addEventListener("click", function () {
      openKit($("pub-manual").getAttribute("data-plat") || "fanqie");
    });

    $("btn-close-kit").addEventListener("click", closeKit);
    $("kit-body").addEventListener("click", kitCopy);
    $("kit-mask").addEventListener("click", function (ev) {
      if (ev.target === $("kit-mask")) closeKit();
    });

    $("out-plats").addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-out]");
      if (btn) openOutcome(btn.getAttribute("data-out"));
    });
    $("out-plat-chips").addEventListener("click", function (ev) {
      var chip = ev.target.closest("[data-out-plat]");
      if (!chip) return;
      S.outPlat = chip.getAttribute("data-out-plat");
      S.outStage = ((S.out && S.out.platforms || {})[S.outPlat] || {}).stage || "";
      renderOutChips();
      var cur = ((S.out && S.out.platforms) || {})[S.outPlat] || {};
      $("f-quote").value = cur.quote || "";
      $("f-reads").value = cur.reads == null ? "" : cur.reads;
      $("f-note").value = cur.note || "";
    });
    $("out-stage-chips").addEventListener("click", function (ev) {
      var chip = ev.target.closest("[data-out-stage]");
      if (!chip) return;
      S.outStage = chip.getAttribute("data-out-stage");
      renderOutChips();
    });
    $("btn-close-out").addEventListener("click", closeOutcome);
    $("out-mask").addEventListener("click", function (ev) {
      if (ev.target === $("out-mask")) closeOutcome();
    });
    $("btn-save-out").addEventListener("click", saveOutcome);

    window.addEventListener("hashchange", route);
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      if (!$("kit-mask").classList.contains("hidden")) closeKit();
      else if (!$("out-mask").classList.contains("hidden")) closeOutcome();
      else if (!$("composer-mask").classList.contains("hidden")) closeComposer();
    });

    S.timer = setInterval(function () {
      if (document.hidden || S.busy) return;
      refresh(false).catch(function () { $("conn-dot").className = "conn-dot dead"; });
    }, 3000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
