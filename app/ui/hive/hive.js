/* CodeBee 蜂群：一个内核，多个专职 Agent。
 *
 * 只消费内核已有接口：/api/profile、/api/flows（每行带 agent）、/api/agents、
 * /api/state、/api/runs/*、/api/tasks。
 * 不认识任何具体流程的业务细节——专家怎么分工由后端 agents.py 声明。
 */
(function () {
  "use strict";

  var S = {
    profile: null,
    agents: [],
    archetypes: {},
    flows: [],
    state: null,
    latest: {},
    tasks: [],
    flow: "",
    run: null,
    files: [],
    report: "",
  };

  var ARCH_ORDER = ["studio", "craft", "dossier", "radar"];

  /* 每个原型要哪几块面板。这是「页面也不一样」的落点：
   * 同一个渲染器，不同的面板组合与顺序，就是不同的工作台。 */
  var LAYOUT = {
    studio: ["queue", "live_steps", "artifacts", "checklist"],
    craft: ["queue", "live_steps", "review", "artifacts"],
    dossier: ["queue", "checklist", "live_steps", "artifacts"],
    radar: ["queue", "findings", "live_steps"]
  };

  var PANEL_TITLE = {
    queue: "我的任务",
    live_steps: "它正在做的事",
    review: "评审意见",
    artifacts: "产物",
    checklist: "门禁",
    findings: "结论"
  };

  var MSG_PLACEHOLDER = {
    studio: "例：把 app/core/store.py 里的任务排序改成按创建时间倒序，并补一个回归用例。",
    craft: "例：一个殡仪馆化妆师发现，每个死者生前都收到过他经手的一封信。悬疑，冷叙述，结尾留一个没解释的细节。",
    dossier: "例：给甲方写一份机房搬迁实施方案，含现状、步骤、回退、风险，两天内交付。",
    radar: "例：扫一遍悬疑惊悚榜，看现在什么题材起量，给我三个可做的选题。"
  };

  /* ---------- 基础 ---------- */

  function $(id) { return document.getElementById(id); }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function api(path, opt) {
    opt = opt || {};
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, opt.timeout || 20000);
    try {
      var res = await fetch(path, {
        method: opt.method || "GET",
        headers: opt.body ? { "Content-Type": "application/json" } : {},
        body: opt.body,
        signal: ctrl.signal,
        credentials: "same-origin"
      });
      var text = await res.text();
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
      if (!res.ok) throw new Error((data && data.error) || ("请求失败（" + res.status + "）"));
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  function toast(msg) {
    var el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.add("hidden"); }, 2600);
  }

  function statusPill(status) {
    if (status === "done") return '<span class="pill done">已完成</span>';
    if (status === "running" || status === "queued") return '<span class="pill running">进行中</span>';
    if (status === "failed") return '<span class="pill failed">卡住了</span>';
    if (status === "paused") return '<span class="pill running">已暂停</span>';
    return '<span class="pill">' + esc(status || "草稿") + "</span>";
  }

  function agentOf(flowId) {
    for (var i = 0; i < S.agents.length; i++) if (S.agents[i].flow === flowId) return S.agents[i];
    return null;
  }

  function kindName(key) {
    return (S.archetypes[key] && S.archetypes[key].name) || key;
  }

  /* ---------- 侧栏 ---------- */

  function renderRail() {
    var groups = {};
    S.agents.forEach(function (a) {
      (groups[a.archetype] = groups[a.archetype] || []).push(a);
    });
    var html = "";
    ARCH_ORDER.forEach(function (key) {
      var list = groups[key] || [];
      if (!list.length) return;
      html += '<p class="rail-group">' + esc(kindName(key)) + "</p>";
      list.forEach(function (a) {
        var n = S.tasks.filter(function (t) { return t.type === a.flow; }).length;
        html += '<a class="rail-item' + (S.flow === a.flow ? " is-on" : "") +
          '" href="#/agent/' + esc(a.flow) + '">' +
          '<span class="n">' + esc(a.name) + "</span>" +
          (n ? '<span class="c">' + n + "</span>" : "") +
          "</a>";
      });
    });
    $("rail-list").innerHTML = html || '<p class="note" style="padding:0 10px">没有可用的专家。</p>';
    $("rail-sub").textContent = S.agents.length + " 位 · 按职责分组";
  }

  /* ---------- 总览 ---------- */

  function renderHub() {
    var groups = {};
    S.agents.forEach(function (a) {
      (groups[a.archetype] = groups[a.archetype] || []).push(a);
    });
    var html = "";
    ARCH_ORDER.forEach(function (key) {
      var list = groups[key] || [];
      if (!list.length) return;
      var arch = S.archetypes[key] || {};
      html += '<section class="group">' +
        '<div class="group-head"><p class="group-name">' + esc(arch.name || key) +
        '</p><p class="group-q">' + esc(arch.question || "") + "</p></div>" +
        '<div class="cards">';
      list.forEach(function (a) {
        html += '<a class="card" href="#/agent/' + esc(a.flow) + '">' +
          '<div class="card-top"><span class="card-dot">' + esc(a.name.slice(0, 1)) + "</span>" +
          '<span class="card-name">' + esc(a.name) + "</span></div>" +
          '<p class="card-focus">' + esc(a.focus) + "</p>" +
          '<div class="card-meta"><span>' + esc(a.metric || "") + "</span>" +
          '<span class="card-act">' + esc(a.primary_action) + " →</span></div>" +
          "</a>";
      });
      html += "</div></section>";
    });
    $("hub-groups").innerHTML = html;
    $("hub-count").textContent = "共 " + S.agents.length + " 位，四种工作台。";
  }

  /* ---------- 面板 ---------- */

  function panel(title, bodyHtml, opt) {
    opt = opt || {};
    return '<div class="panel' + (opt.span ? " span-2" : "") + '">' +
      '<div class="panel-head"><p class="panel-title">' + esc(title) + "</p>" +
      (opt.note ? '<p class="panel-note">' + esc(opt.note) + "</p>" : "") +
      "</div>" + bodyHtml + "</div>";
  }

  function myTasks() {
    return S.tasks.filter(function (t) { return t.type === S.flow; });
  }

  function renderQueue(a) {
    var list = myTasks();
    if (!list.length) {
      return panel(PANEL_TITLE.queue, '<p class="note">还没有任务。</p>');
    }
    var rows = list.slice(0, 8).map(function (t) {
      var run = S.latest[t.id] || {};
      return '<div class="row"><span class="t">' + esc(t.title || "未命名") + "</span>" +
        statusPill(run.status || t.status) + "</div>";
    }).join("");
    return panel(PANEL_TITLE.queue, '<div class="rows">' + rows + "</div>",
      { note: list.length + " 个" });
  }

  function renderSteps() {
    var run = S.run;
    if (!run || !(run.steps || []).length) {
      return panel(PANEL_TITLE.live_steps,
        '<p class="note">' + (run ? "还没有步骤记录。" : "选一个任务后这里会显示它的执行步骤。") + "</p>");
    }
    var steps = run.steps.slice(-6).map(function (s) {
      return '<div class="step"><span class="r">' + esc(s.role || "") + "</span>" +
        '<span class="m">' + esc(s.summary || s.note || s.status || "") + "</span></div>";
    }).join("");
    return panel(PANEL_TITLE.live_steps, '<div class="steps">' + steps + "</div>",
      { note: (run.steps.length) + " 步" });
  }

  function renderReview() {
    var run = S.run;
    var msgs = (run && run.messages) || [];
    if (!msgs.length) {
      return panel(PANEL_TITLE.review,
        '<p class="note">' + (run ? "这一轮没有留下评审意见。" : "选一个任务后这里会显示评审意见。") + "</p>");
    }
    var rows = msgs.slice(-6).map(function (m) {
      var text = m.text || m.content || m.summary || m.note || "";
      return '<div class="step"><span class="r">' + esc(m.role || m.kind || "") + "</span>" +
        '<span class="m">' + esc(String(text).slice(0, 400)) + "</span></div>";
    }).join("");
    return panel(PANEL_TITLE.review, '<div class="steps">' + rows + "</div>",
      { note: msgs.length + " 条" });
  }

  function renderArtifacts() {
    if (!S.files.length) {
      return panel(PANEL_TITLE.artifacts, '<p class="note">还没有产出文件。</p>');
    }
    var rows = S.files.slice(0, 8).map(function (f) {
      var name = f.path || f.rel || f.name || "";
      return '<div class="row"><span class="t mono">' + esc(name) + "</span>" +
        (f.size ? '<span class="s">' + Math.round(f.size / 1024) + " KB</span>" : "") + "</div>";
    }).join("");
    return panel(PANEL_TITLE.artifacts, '<div class="rows">' + rows + "</div>",
      { note: S.files.length + " 个文件" });
  }

  function renderChecklist(a) {
    var gates = a.gates || [];
    var html = "";
    if (gates.length) {
      html += '<div class="rows">' + gates.map(function (g) {
        return '<div class="row"><span class="t">' + esc(g) + "</span>" +
          '<span class="pill running">硬门禁</span></div>';
      }).join("") + "</div>";
    } else {
      html += '<p class="note">这个专家没有硬门禁，靠评审席位打分收敛。</p>';
    }
    if (a.seats) html += '<p class="note" style="margin-top:10px">评审席位：' + a.seats + " 席</p>";
    return panel(PANEL_TITLE.checklist, html, { note: a.metric || "" });
  }

  function renderFindings() {
    var text = (S.run && S.run.summary) || S.report || "";
    if (!text) return panel(PANEL_TITLE.findings, '<p class="note">还没有结论。</p>');
    return panel(PANEL_TITLE.findings,
      '<p class="note" style="white-space:pre-wrap">' + esc(String(text).slice(0, 1200)) + "</p>");
  }

  var RENDER = {
    queue: renderQueue,
    live_steps: renderSteps,
    review: renderReview,
    artifacts: renderArtifacts,
    checklist: renderChecklist,
    findings: renderFindings
  };

  /* ---------- 专家工作台 ---------- */

  async function loadRun() {
    var list = myTasks();
    S.run = null; S.files = []; S.report = "";
    if (!list.length) return;
    var latest = S.latest[list[0].id];
    if (!latest || !latest.id) return;
    try {
      var got = await api("/api/runs/" + latest.id);
      S.run = (got && got.run) || null;
    } catch (e) { S.run = null; }
    try {
      var f = await api("/api/runs/" + latest.id + "/files");
      S.files = (f && f.files) || [];
    } catch (e) { S.files = []; }
    if (S.flow === "rank_scan" || S.flow === "research") {
      try {
        var res = await fetch("/api/runs/" + latest.id + "/report", { credentials: "same-origin" });
        if (res.ok) S.report = await res.text();
      } catch (e) { S.report = ""; }
    }
  }

  async function renderAgent(flow) {
    var a = agentOf(flow);
    if (!a) { location.hash = "#/"; return; }
    S.flow = flow;
    $("view-hub").classList.add("hidden");
    $("view-agent").classList.remove("hidden");

    $("agent-kind").textContent = a.archetype_name || kindName(a.archetype);
    $("agent-name").textContent = a.name;
    $("agent-focus").textContent = a.focus;
    $("btn-start").textContent = a.primary_action || "开一个任务";

    var facts = [
      ["拿什么算干得好", a.metric || "—"],
      ["交出来的东西", a.deliverable || "—"],
      ["工作台", a.archetype_name || kindName(a.archetype)]
    ];
    if (a.seats) facts.push(["评审席位", a.seats + " 席"]);
    $("agent-facts").innerHTML = facts.map(function (kv) {
      return "<div><dt>" + esc(kv[0]) + "</dt><dd>" + esc(kv[1]) + "</dd></div>";
    }).join("");

    var panels = LAYOUT[a.archetype] || LAYOUT.studio;
    var hasAnything = myTasks().length > 0;
    // 没干过活就只留一句引导，不铺四块空面板——空面板比没有更让人困惑。
    $("agent-panels").innerHTML = hasAnything ? panels.map(function (key) {
      var fn = RENDER[key];
      return fn ? fn(a) : "";
    }).join("") : "";
    $("agent-empty").classList.toggle("hidden", hasAnything);
    if (!hasAnything) {
      $("agent-empty-sub").textContent =
        "点右上角「" + (a.primary_action || "开一个任务") + "」，它会按「" + (a.metric || "自己的标准") + "」来要求自己。";
    }

    document.title = a.name + " · CodeBee";
    renderRail();
    await loadRun();
    // 拿到 run 之后再刷一次面板（步骤/意见依赖 run）
    if (hasAnything) {
      $("agent-panels").innerHTML = panels.map(function (key) {
        var fn = RENDER[key];
        return fn ? fn(a) : "";
      }).join("");
    }
  }

  /* ---------- 弹窗 ---------- */

  var currentAgent = null;

  function openComposer(a) {
    currentAgent = a;
    $("dlg-title").textContent = a.primary_action || "开一个任务";
    $("dlg-goal-label").textContent = "交给「" + a.name + "」";
    $("dlg-goal").value = "";
    $("dlg-goal").placeholder = MSG_PLACEHOLDER[a.archetype] || "写清你要什么，越具体越好。";
    $("dlg-hint").textContent = "它只负责一件事：" + a.focus + "。目标里写清这件事的约束，比写得多更重要。";
    $("dlg-msg").textContent = "";
    $("dlg-submit").disabled = false;
    $("mask").classList.remove("hidden");
    setTimeout(function () { $("dlg-goal").focus(); }, 30);
  }

  function closeComposer() { $("mask").classList.add("hidden"); }

  async function submitComposer() {
    if (!currentAgent) return;
    var goal = $("dlg-goal").value.trim();
    if (!goal) { $("dlg-msg").textContent = "先写一句要它做什么。"; return; }
    $("dlg-submit").disabled = true;
    $("dlg-msg").textContent = "";
    try {
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ type: currentAgent.flow, goal: goal })
      });
      closeComposer();
      toast("已交给「" + currentAgent.name + "」");
      await refresh(true);
      if (S.flow === currentAgent.flow) await renderAgent(S.flow);
    } catch (e) {
      $("dlg-msg").textContent = e.message || "创建失败。";
      $("dlg-submit").disabled = false;
    }
  }

  /* ---------- 数据与路由 ---------- */

  async function refresh(force) {
    var dot = $("conn-dot");
    try {
      var pack = await Promise.all([
        api("/api/profile"), api("/api/agents"), api("/api/flows"), api("/api/state")
      ]);
      S.profile = pack[0] || {};
      S.agents = (pack[1] && pack[1].agents) || [];
      S.archetypes = (pack[1] && pack[1].archetypes) || {};
      S.flows = (pack[2] && pack[2].flows) || [];
      S.state = pack[3] || {};
      S.latest = S.state.task_latest || {};
      S.tasks = (S.state.tasks || []).slice().sort(function (a, b) {
        return String(b.created_at || "").localeCompare(String(a.created_at || ""));
      });
      dot.className = "conn-dot on";
      dot.title = "已连接";
      if (S.profile.name) $("brand-name").textContent = S.profile.name;
    } catch (e) {
      dot.className = "conn-dot off";
      dot.title = "连不上内核";
      throw e;
    }
    renderRail();
    if (!S.flow) renderHub();
  }

  async function route() {
    var m = /^#\/agent\/(.+)$/.exec(location.hash || "");
    if (m) {
      await renderAgent(decodeURIComponent(m[1]));
    } else {
      S.flow = "";
      $("view-agent").classList.add("hidden");
      $("view-hub").classList.remove("hidden");
      document.title = "蜂群 · CodeBee";
      renderHub();
      renderRail();
    }
  }

  async function boot() {
    try {
      await refresh();
      await route();
    } catch (e) {
      toast(e.message || "连不上内核");
    }
    setInterval(function () {
      refresh().then(function () {
        if (S.flow) renderAgent(S.flow).catch(function () {});
      }).catch(function () {});
    }, 5000);

    $("btn-refresh").addEventListener("click", function () {
      refresh().then(route).then(function () { toast("已刷新"); }).catch(function () {});
    });
    $("btn-back").addEventListener("click", function (ev) {
      ev.preventDefault();
      location.hash = "#/";
    });
    $("brand-link").addEventListener("click", function (ev) {
      ev.preventDefault();
      location.hash = "#/";
    });
    $("btn-start").addEventListener("click", function () {
      var a = agentOf(S.flow);
      if (a) openComposer(a);
    });
    $("btn-close").addEventListener("click", closeComposer);
    $("mask").addEventListener("click", function (ev) {
      if (ev.target === $("mask")) closeComposer();
    });
    $("dlg-submit").addEventListener("click", submitComposer);
    window.addEventListener("hashchange", function () { route(); });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !$("mask").classList.contains("hidden")) closeComposer();
    });
  }

  boot();
})();
