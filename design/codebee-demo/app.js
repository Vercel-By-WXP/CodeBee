(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) {
    return String(s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; });
  };
  var ico = function (n, cls) { return '<svg class="ico' + (cls ? " " + cls : "") + '"><use href="#i-' + n + '"/></svg>'; };
  /* 品牌标识沿用产品内既有资源 icons/bee.svg，不另画 */
  var bee = function (cls) { return '<img class="' + (cls || "bee-img") + '" src="bee.svg" alt="">'; };

  /* ---------------- 轻提示 ---------------- */
  var toastEl = $("#toast"), toastTimer = 0;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 2000);
  }

  /* ---------------- 首页漂浮装饰 ---------------- */
  var kiteSeed = [
    [5, 9, 26, -18], [10, 23, 13, 12], [4, 37, 30, -8], [13, 52, 17, 18], [6, 67, 21, -24],
    [22, 6, 15, 8], [34, 3, 21, -14], [47, 4, 12, 20], [61, 5, 19, -6], [75, 3, 27, 14], [88, 8, 14, -20],
    [93, 19, 22, 10], [85, 30, 13, -16], [96, 39, 29, 6], [79, 45, 16, 22], [89, 54, 23, -10], [72, 60, 11, 16],
    [86, 68, 18, -14], [65, 73, 13, 8], [51, 79, 22, -22], [35, 84, 12, 14], [19, 88, 19, -6], [7, 80, 14, 20],
    [44, 62, 10, -12], [58, 89, 16, 10]
  ];
  var field = $("#hexfield");
  if (field) {
    kiteSeed.forEach(function (h, i) {
      var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      s.setAttribute("class", "kite");
      s.setAttribute("viewBox", "0 0 24 24");
      s.setAttribute("aria-hidden", "true");
      s.style.left = h[0] + "%";
      s.style.top = h[1] + "%";
      s.style.width = Math.round(h[2] * 1.6) + "px";
      s.style.height = Math.round(h[2] * 1.6) + "px";
      s.style.setProperty("--rot", h[3] + "deg");
      s.style.opacity = (0.6 + (i % 4) * 0.12).toFixed(2);
      s.style.animationDelay = (-i * 0.62).toFixed(2) + "s";
      s.style.animationDuration = (9 + (i % 5) * 1.6).toFixed(1) + "s";
      s.innerHTML = '<use href="#i-kite"/>';
      field.appendChild(s);
    });
  }

  /* ---------------- 视图切换 ---------------- */
  function go(view) {
    $$(".view").forEach(function (v) { v.classList.toggle("active", v.dataset.view === view); });
  }

  function markNav(key) {
    $$(".nav-row[data-page], .side-sub .task-row[data-page]").forEach(function (b) {
      var on = b.dataset.page === key;
      b.classList.toggle("active", on);
      b.classList.toggle("on", on && !!b.closest(".side-sub"));
    });
    var inMore = ["overview", "kanban", "browser", "availability", "board", "atlas", "index"].indexOf(key) >= 0;
    var more = $("#btn-more");
    if (inMore) {
      $("#side-more").classList.remove("hidden");
      more.setAttribute("aria-expanded", "true");
      more.classList.add("active");
    } else {
      more.classList.remove("active");
    }
  }

  function goPage(key) {
    if (!$("#settings").classList.contains("hidden")) closeSettings();
    if (key === "home") { go("home"); markNav("home"); return; }
    var p = PAGES[key];
    if (!p) { toast("这一页还在画"); return; }
    $("#page-root").innerHTML = p.render();
    go("page");
    markNav(key);
    var v = $(".view-page");
    if (v) v.scrollTop = 0;
    closePop();
  }

  /* ---------------- 片段助手 ---------------- */
  function head(title, desc, actions) {
    return '<div class="page-head"><div><h2>' + title + "</h2><p>" + desc + "</p></div>" +
      (actions ? '<div class="page-actions">' + actions + "</div>" : "") + "</div>";
  }
  function stats(items) {
    return '<div class="stat-grid">' + items.map(function (s) {
      return '<div class="stat"><div class="k">' + s.k + '</div><div class="v">' + s.v +
        (s.unit ? "<small>" + s.unit + "</small>" : "") + "</div>" +
        (s.d ? '<div class="d">' + s.d + "</div>" : "") + "</div>";
    }).join("") + "</div>";
  }
  function panel(title, body, extra, cls) {
    return '<section class="panel' + (cls ? " " + cls : "") + '"><div class="panel-h"><h3>' + title + "</h3>" +
      (extra || "") + '<span class="sp"></span></div>' + body + "</section>";
  }
  function lrow(o) {
    return '<div class="lrow">' +
      (o.i ? '<span class="lrow-ico">' + ico(o.i) + "</span>" : "") +
      '<div class="lrow-txt"><h4>' + o.t + (o.tag || "") + "</h4>" + (o.d ? "<p>" + o.d + "</p>" : "") + "</div>" +
      (o.meta ? '<span class="lrow-meta">' + o.meta + "</span>" : "") +
      (o.act || "") + "</div>";
  }
  function bar(label, right, pct) {
    return '<div class="usage-line"><div class="top"><span>' + label + '</span><span class="r">' + right + "</span></div>" +
      '<div class="bar"><i style="width:' + pct + '%"></i></div></div>';
  }
  function sw(on, label) { return '<button class="switch" role="switch" aria-checked="' + (on ? "true" : "false") + '" type="button" aria-label="' + (label || "开关") + '"></button>'; }
  function kv(k, v) { return '<div class="kv"><span>' + k + "</span><b>" + v + "</b></div>"; }
  function note(txt, i) { return '<div class="note-line">' + ico(i || "info") + "<span>" + txt + "</span></div>"; }

  /* 运行记录卡片行 */
  function runCard(o) {
    var dotColor = o.tone === "run" ? "#d99438" : o.tone === "fail" ? "#b9776f" : "#7fae9a";
    return '<div class="run-card" data-task="' + esc(o.title) + '" data-meta="' + esc(o.meta) + '">' +
      '<input class="cb" type="checkbox" aria-label="选择这一行">' +
      '<span class="dot" style="background:' + dotColor + '"></span>' +
      '<div class="run-main"><div class="run-title"><span class="nm">' + esc(o.title) + "</span>" +
      '<span class="tag ' + (o.tone === "run" ? "warn" : o.tone === "fail" ? "bad" : "ok") + '">' + o.state + "</span></div>" +
      '<div class="run-sub">' + o.sub + "</div></div>" +
      '<div class="run-right"><span class="run-time">' + o.time + "</span>" +
      '<button class="btn-sm" data-toast="' + (o.tone === "fail" ? "重试已排队（演示）" : "打开运行（演示）") + '">' +
      (o.tone === "fail" ? "重试" : "看记录") + "</button>" +
      '<button class="icon-ghost danger" type="button" data-toast="删除这一行（演示）" aria-label="删除">' + ico("trash") + "</button></div></div>";
  }

  /* ---------------- 主页面 ---------------- */
  var PAGES = {};

  /* 运行记录 —— 真实文案：每一次运行都在这条线上。 */
  PAGES.runs = {
    title: "运行记录",
    render: function () {
      var rows = [
        { title: "《头七结账人》21-24章修订复评（第二轮:整章重写至规格）续7", tone: "run", state: "运行中", sub: "第 21/21 步 · 正在压 22 章字数 2,612 → 2,500 · 连载家", meta: "运行中 · 第 21/21 步 · 47 分钟", time: "47 分钟" },
        { title: "《头七结账人》第 24 章 收尾", tone: "fail", state: "失败", sub: "第 6/11 步 · 服务重启中断，可重试 · 已保留现场", meta: "失败 · 第 6/11 步 · 6 小时前", time: "6 小时前" },
        { title: "《头七结账人》21-24章修订复评（第二轮:整章重写至规格）续7", tone: "fail", state: "失败", sub: "第 11/11 步 · 服务重启中断 · 与上面同一条的上一轮", meta: "失败 · 第 11/11 步 · 昨天", time: "昨天 15:34" },
        { title: "每日仓库巡检", tone: "ok", state: "已完成", sub: "扫了 18 个仓库：3 个有新提交，1 个有未推送 · 1 个产物", meta: "已完成 · 今天 09:00", time: "今天 09:00" },
        { title: "知识库整理", tone: "ok", state: "已完成", sub: "新增 6 篇、合并 2 组重复条目 · 索引已更新", meta: "已完成 · 今天 03:00", time: "今天 03:00" },
        { title: "经验库周整理", tone: "ok", state: "已完成", sub: "首轮失败后 18:04 自动重跑成功 · 沉淀 3 条", meta: "已完成 · 昨天 18:04", time: "昨天" },
        { title: "《头七结账人》第 23 章 重写", tone: "ok", state: "已完成", sub: "2,510 字 · 评审 4 项通过 · 3 个产物", meta: "已完成 · 10-26", time: "10-26" },
        { title: "《头七结账人》第 22 章 重写", tone: "ok", state: "已完成", sub: "2,494 字 · 评审 4 项通过 · 3 个产物", meta: "已完成 · 10-24", time: "10-24" }
      ];
      return head("运行记录", "每一次运行都在这条线上。点一行看它怎么做的、卡在哪、产出了什么。",
        '<button class="btn-sm" data-toast="已导出 runs.csv（演示）">' + ico("download") + "导出</button>" +
        '<button class="btn-sm dark" data-page="home">' + ico("plus") + "新建任务</button>") +
        '<div class="chips"><button class="chip-f on" type="button">全部 128</button>' +
        '<button class="chip-f" type="button">运行中 1</button><button class="chip-f" type="button">失败 2</button>' +
        '<button class="chip-f" type="button">待你确认 3</button><button class="chip-f" type="button">已完成 122</button></div>' +
        panel("全部运行",
          '<div class="run-tools"><label><input class="cb" type="checkbox" aria-label="全选"> 全选</label>' +
          "<span>勾选可批量删除</span><span class=\"sp\" style=\"flex:1\"></span>" +
          '<button class="link" type="button" data-toast="已清空（演示）">全部清除</button></div>' +
          '<div class="run-list">' + rows.map(runCard).join("") + "</div>");
    }
  };

  /* 自动化 —— 真实文案：到点就自己跑的任务。 */
  PAGES.automation = {
    title: "自动化",
    render: function () {
      var rules = [
        ["每日仓库巡检", "每天 09:00", "下次 明天 09:00 · 近 7 次全成功 · 最近 38s", true, "ok", "启用"],
        ["经验库周整理", "每周日 18:00", "上次失败（模型超时）· 已自动重跑成功", true, "bad", "上次失败"],
        ["经验库周整理", "每周日 00:30", "与 18:00 那条并存：一条整理、一条归档", true, "ok", "启用"],
        ["知识库整理", "每天 03:00", "下次 明天 03:00 · 近 30 天 30 次全成功", true, "ok", "启用"],
        ["CodeBee 自我迭代", "每 12 小时", "下次 21:00 · 只在评测集退步时提醒你", true, "ok", "启用"]
      ];
      return head("自动化", "到点就自己跑的任务。定好时间，之后不用再管。",
        '<button class="btn-sm" data-toast="近 30 天触发 47 次，失败 2 次">' + ico("history") + "运行历史</button>" +
        '<button class="btn-sm dark" data-toast="演示版：新建自动化表单先留空">' + ico("plus") + "新建自动化</button>") +
        panel("规则 · 5 条",
          rules.map(function (r) {
            return lrow({
              i: "clock",
              t: r[0],
              tag: '<span class="tag ' + r[4] + '">' + r[5] + "</span>",
              d: r[1] + " · " + r[2],
              meta: "上次 " + (r[0] === "每日仓库巡检" ? "今天 09:00" : "按计划"),
              act: sw(true, r[0]) + '<button class="icon-ghost" type="button" data-toast="编辑规则（演示）">' + ico("pencil") + "</button>"
            });
          }).join("")) +
        panel("定时任务模板",
          '<div class="xgrid">' +
          [["clock", "每天跑一次", "巡检、日报、备份这类，时间固定"], ["calendar", "每周跑一次", "整理、汇总、周报"], ["refresh", "每 N 小时", "盯变化，有变化才提醒"]].map(function (t) {
            return '<div class="xcard"><div class="x-top"><span class="xava">' + ico(t[0]) + "</span><div><h4>" + t[1] + "</h4></div></div>" +
              "<p>" + t[2] + '</p><div class="x-foot"><span class="tag">模板</span><span style="flex:1"></span>' +
              '<button class="btn-sm" data-toast="已套用模板：' + t[1] + '">用这个</button></div></div>';
          }).join("") + "</div>");
    }
  };

  /* 专家 —— 真实：专家 / 技能 / 连接器 三个页签 */
  PAGES.experts = {
    title: "专家",
    render: function () {
      var xs = [
        ["doc", "连载家", "写作台 · 12 个任务", "先立设定再逐章推进；每章过评审才往下写，章末必须有钩子。"],
        ["cpu", "架构师", "编码台 · 31 个任务", "改前先复现，改动最小化，必须带守测和一次实拍。"],
        ["check", "审稿人", "评审 · 只读不改", "4 个维度打分：节奏、人物动机、对话占比、章末钩子。"],
        ["folder", "交付匠", "文档台 · 8 个任务", "模板对齐、数字统一 tabular-nums，产物直接能发客户。"]
      ];
      return head("专家", "每个任务先选一个「人设」：它决定拆步骤的方式、验收标准和默认模型。技能和连接器可以叠加。",
        '<div class="seg"><button class="on" type="button">专家 4</button><button type="button" data-toast="技能：4 个已挂载">技能 4</button>' +
        '<button type="button" data-toast="连接器：MCP 3 个 · 本机 CLI 3 个">连接器 6</button></div>') +
        '<div class="xgrid">' + xs.map(function (x) {
          return '<div class="xcard"><div class="x-top"><span class="xava">' + ico(x[0]) + "</span>" +
            "<div><h4>" + x[1] + '</h4><div class="x-role">' + x[2] + "</div></div></div>" +
            "<p>" + x[3] + '</p><div class="x-foot"><span class="tag info">默认专家</span>' +
            '<span class="tag">' + (x[1] === "连载家" ? "写作规范包" : x[1] === "架构师" ? "工程守则" : "只读") + "</span>" +
            '<span style="flex:1"></span>' +
            '<button class="btn-sm" data-toast="已切换到「' + x[1] + '」">选他</button></div></div>';
        }).join("") + "</div>" +
        panel("可以叠加的技能",
          lrow({ i: "spark", t: "写作规范包", d: "章节节奏、对话占比、禁用词表；连载家默认挂载", meta: "v1.4", act: '<button class="btn-sm" data-toast="已启用写作规范包">启用</button>' }) +
          lrow({ i: "shield", t: "工程守则", d: "改前复现、改动最小、必须带守测；架构师默认挂载", meta: "v2.0", act: '<button class="btn-sm" data-toast="已启用工程守则">启用</button>' }) +
          lrow({ i: "folder", t: "交付模板", d: "Word / PDF 版式与命名规则，交付匠按客户切换", meta: "6 套模板", act: '<button class="btn-sm" data-toast="已启用交付模板">启用</button>' }) +
          lrow({ i: "monitor", t: "视觉验收", d: "对截图做对比度、间距、溢出三类检查", meta: "v0.9", act: '<button class="btn-sm" data-toast="已启用视觉验收">启用</button>' }));
    }
  };

  /* 概览 */
  PAGES.overview = {
    title: "概览",
    render: function () {
      var bars = [["10-27", 34], ["10-28", 52], ["10-29", 41], ["10-30", 66], ["10-31", 58], ["11-01", 74], ["今天", 48]];
      return head("概览", "这本作品现在写到哪、还欠多少、最近 7 天跑了多少。",
        '<button class="btn-sm" data-toast="已复制项目分享链接（演示）">' + ico("share") + "分享项目</button>" +
        '<button class="btn-sm" data-page="board">' + ico("monitor") + "打开大屏</button>") +
        stats([
          { k: "已完成章节", v: "22", unit: "/ 40", d: "本轮修订到第 21 章" },
          { k: "运行中", v: "1", d: "已跑 47 分钟" },
          { k: "待你确认", v: "3", d: "22 章重写方案在等" },
          { k: "本月 tokens", v: "2,488", unit: "万", d: "≈ ¥ 6.20" }
        ]) +
        '<div class="grid-2">' +
        panel("项目进展 · 《头七结账人》",
          bar("正文进度", "22 / 40 章", 55) +
          bar("本章字数", "2,486 / 2,500", 99) +
          bar("评审通过率", "21 / 24 章", 88) +
          note("数字来自本地台账；拿不到时显示「—」，不用 0 顶替。", "chart")) +
        panel("近 7 天运行量", '<div class="bars">' + bars.map(function (b, i) {
          return '<div class="b' + (i === 6 ? " on" : "") + '"><i style="height:' + b[1] + '%"></i><span>' + b[0] + "</span></div>";
        }).join("") + "</div>", '<span class="hint">单位：次</span>') +
        "</div>" +
        '<div class="grid-2">' +
        panel("长目标",
          '<div class="card" style="margin:0 0 10px"><div style="font-size:12.4px;color:#2b414a;font-weight:600;margin-bottom:6px">写完 40 章并完成两轮修订</div>' +
          '<p style="margin:0;font-size:11.4px;color:#8b9da6;line-height:1.75">第二轮规格：章末必须有钩子、字数 2500±3%、不动已有伏笔线。当前 21 章已过，22-24 在改。</p></div>' +
          lrow({ i: "flag", t: "第一轮修订", tag: '<span class="tag ok">已完成</span>', d: "1-20 章，10-19 收尾", meta: "100%" }) +
          lrow({ i: "flag", t: "第二轮修订", tag: '<span class="tag warn">进行中</span>', d: "21-24 章，剩 3 章", meta: "25%" })) +
        panel("快捷入口",
          '<div class="pgrid" style="grid-template-columns:repeat(2,minmax(0,1fr));padding-bottom:12px">' +
          [["runs", "运行记录", "128 条"], ["automation", "自动化", "5 条规则"], ["board", "大屏看板", "副屏那一版"], ["atlas", "记忆星图", "12 个节点"]].map(function (p) {
            return '<button class="pcard" type="button" data-page="' + p[0] + '"><b>' + p[1] + "</b><span>" + p[2] + "</span></button>";
          }).join("") + "</div>") +
        "</div>";
    }
  };

  /* 看板 */
  PAGES.kanban = {
    title: "看板",
    render: function () {
      var cols = [
        ["待办", 2, [["《头七结账人》第 25 章 起草", "等 21-24 修订收尾", "待办"], ["第 9 章伏笔呼应复查", "依赖 24 章定稿", "待办"]]],
        ["进行中", 1, [["21-24章修订复评 · 续7", "第 21/21 步 · 47 分钟", "连载家"]]],
        ["待你确认", 3, [["22 章整章重写方案", "会动 2 处伏笔呼应", "连载家"], ["电缆报告批量改名", "12 份产物等复核", "交付匠"], ["24 章 收尾失败现场", "要不要重跑", "连载家"]]],
        ["已完成", 4, [["第 23 章 重写", "2,510 字 · 4 项通过", "10-26"], ["第 22 章 重写", "2,494 字 · 4 项通过", "10-24"], ["每日仓库巡检", "18 个仓库 · 3 处变化", "今天 09:00"]]],
        ["已完结", 2, [["第 21 章 重写", "2,486 字 · 已归档", "10-20"], ["知识库整理", "合并 2 组重复条目", "今天 03:00"]]]
      ];
      return head("看板", "按状态看任务，一格一格往前推。",
        '<button class="btn-sm" data-toast="已按专家分组（演示）">' + ico("layers") + "按专家分组</button>" +
        '<button class="btn-sm dark" data-page="home">' + ico("plus") + "新建任务</button>") +
        '<div class="kanban">' + cols.map(function (c) {
          return '<div class="kcol"><div class="kcol-h">' + c[0] + '<span class="n">' + c[1] + "</span></div>" +
            c[2].map(function (card) {
              return '<div class="kcard"><h4>' + card[0] + "</h4><p>" + card[1] + "</p>" +
                '<div class="kf"><span class="tag">' + card[2] + '</span><span style="flex:1"></span>' +
                '<button class="btn-sm" data-toast="演示版：打开卡片">打开</button></div></div>';
            }).join("") + "</div>";
        }).join("") + "</div>";
    }
  };

  /* 浏览器 */
  PAGES.browser = {
    title: "浏览器",
    render: function () {
      return head("浏览器", "任务要上网时在这里跑：预览本地服务、验证改完的页面、把截图存进产物。",
        '<button class="btn-sm" data-toast="已刷新（演示）">' + ico("refresh") + "刷新</button>" +
        '<button class="btn-sm" data-toast="演示版：新标签先留空">' + ico("plus") + "新标签</button>") +
        '<div class="panel" style="padding-bottom:14px">' +
        '<div class="frame-bar"><span class="tag ok">本地</span><span class="url" style="flex:1;display:inline-flex;align-items:center;gap:5px">' +
        ico("lock") + "http://127.0.0.1:8765" + '</span><button class="icon-ghost" type="button" data-toast="演示版：仅只读预览">' + ico("share") + "</button></div>" +
        '<div class="frame-body"><div>' + ico("monitor") + "<br>内嵌预览区（演示）<br>" +
        '<span style="font-size:10.6px">正式版这里渲染真实页面，可滚动、可截图进产物</span></div></div>' +
        "</div>" +
        '<div class="grid-3">' +
        panel("任务上下文",
          lrow({ i: "folder", t: "工作目录", d: "C:\\Users\\HP\\AppData\\Roaming\\workspace", meta: "master" }) +
          lrow({ i: "doc", t: "这次要看的页", d: "board.html · 大屏看板", meta: "只读" })) +
        panel("验证",
          lrow({ i: "check", t: "对比度检查", d: "20 个格子当前全部 ≥ 3:1", meta: "通过" }) +
          lrow({ i: "warn", t: "溢出检查", d: "1440 宽下无横向滚动", meta: "通过" })) +
        panel("小说发布",
          lrow({ i: "upload", t: "番茄作家后台", d: "上次 10-31 同步 6 章", meta: "已登录" }) +
          lrow({ i: "upload", t: "七猫", d: "格式校验 18 项，2 项待改", meta: "待处理" })) +
        "</div>" +
        note("浏览器只允许访问 localhost 与你在设置里加的白名单，其余域名要先确认。", "shield");
    }
  };

  /* 通道体检 —— 真实数字：可用 8 / 观察中 0 / 已熔断 1 / 已停用 8 */
  PAGES.availability = {
    title: "通道体检",
    render: function () {
      return head("通道体检", "有 1 条通道已熔断。谁还能用、多快、为什么挂了，一眼看完。",
        '<button class="btn-sm" data-toast="已重新探测（演示）">' + ico("refresh") + "重新探测</button>" +
        '<button class="btn-sm" data-open-settings="models">' + ico("hex") + "模型接入</button>") +
        '<div class="banner warn">' + ico("warn") +
        '<span class="sp">OpenRouter 已熔断：连续 3 次超时后自动停用，任务已回退到 DeepSeek 官方。</span>' +
        '<button class="btn-sm" data-toast="已重置熔断计数（演示）">重置</button></div>' +
        stats([
          { k: "可用", v: "8", d: "覆盖当前所有任务类型" },
          { k: "观察中", v: "0", d: "暂时没有变慢的" },
          { k: "已熔断", v: "1", d: "OpenRouter · 超时" },
          { k: "已停用", v: "8", d: "没填密钥或你手动关的" }
        ]) +
        panel("通道明细",
          '<table class="tbl"><colgroup><col style="width:190px"><col><col style="width:110px"><col style="width:90px"><col style="width:120px"></colgroup>' +
          "<thead><tr><th>通道</th><th>模型</th><th>状态</th><th>延迟</th><th>最近一次</th></tr></thead><tbody>" +
          '<tr><td class="nm">DeepSeek 官方</td><td>deepseek-v4.1-flash</td><td><span class="tag ok">可用</span></td><td>318ms</td><td>今天 16:41</td></tr>' +
          '<tr><td class="nm">智谱 GLM</td><td>GLM-5.3 · Coding Plan</td><td><span class="tag ok">可用</span></td><td>642ms</td><td>今天 16:20</td></tr>' +
          '<tr><td class="nm">硅基流动</td><td>Qwen3-Max</td><td><span class="tag ok">可用</span></td><td>712ms</td><td>今天 15:58</td></tr>' +
          '<tr><td class="nm">Moonshot</td><td>kimi-k2</td><td><span class="tag ok">可用</span></td><td>886ms</td><td>今天 15:12</td></tr>' +
          '<tr><td class="nm">Our Free Model</td><td>内置 · 无需密钥</td><td><span class="tag ok">可用</span></td><td>1.1s</td><td>今天 16:41</td></tr>' +
          '<tr><td class="nm">OpenRouter</td><td>auto · 多厂商路由</td><td><span class="tag bad">已熔断</span></td><td>—</td><td>今天 12:06</td></tr>' +
          '<tr><td class="nm">本机 CLI · opencode</td><td>v1.4.2</td><td><span class="tag ok">可用</span></td><td>1.2s</td><td>今天 15:58</td></tr>' +
          '<tr><td class="nm">本机 CLI · claude-code</td><td>v2.0.1</td><td><span class="tag ok">可用</span></td><td>860ms</td><td>今天 14:30</td></tr>' +
          '<tr><td class="nm">Sensetime</td><td>sensenova-6.8-flash-lite</td><td><span class="tag">已停用</span></td><td>—</td><td>—</td></tr>' +
          '<tr><td class="nm">Gemini</td><td>gemini-cli</td><td><span class="tag">已停用</span></td><td>—</td><td>—</td></tr>' +
          "</tbody></table>") +
        '<div class="grid-2">' +
        panel("OpenRouter · 熔断详情",
          kv("触发原因", "超时（连续 3 次）") +
          kv("最后一次尝试", "今天 12:06 · 任务：经验库周整理") +
          kv("下一步", "12:06 之后自动重试 3 次，均失败") +
          '<pre style="margin:10px 0 12px;padding:10px 12px;background:#f8fbfd;border:1px solid #eef4f8;border-radius:8px;font-size:10.8px;color:#7d909a;white-space:pre-wrap;line-height:1.7">当前失败详情：超时\nstderr/stdout: Reconnecting... waiting for network</pre>' +
          '<button class="btn-sm" data-toast="已恢复 OpenRouter（演示）">恢复使用</button>') +
        panel("本机 CLI",
          lrow({ i: "cpu", t: "opencode", tag: '<span class="tag ok">可用</span>', d: "v1.4.2 · 默认关闭自动探测（探测要起子进程，慢）", meta: "1.2s" }) +
          lrow({ i: "cpu", t: "claude-code", tag: '<span class="tag ok">可用</span>', d: "v2.0.1 · 已登录", meta: "860ms" }) +
          lrow({ i: "cpu", t: "gemini-cli", tag: '<span class="tag">已停用</span>', d: "v0.9.0 · 需要先在本机登录", meta: "—" })) +
        "</div>";
    }
  };

  /* 大屏看板 */
  PAGES.board = {
    title: "大屏看板",
    render: function () {
      return head("大屏看板", "挂在副屏上的那一版：谁在跑、跑得怎么样、今天花了多少。",
        '<button class="btn-sm" data-toast="已切换主题（演示）">' + ico("palette") + "换主题</button>" +
        '<button class="btn-sm" data-toast="已复制大屏地址（演示）">' + ico("share") + "分享地址</button>") +
        '<div class="wall">' +
        wallCell("运行中", "1", "47 分钟") +
        wallCell("待你确认", "3", "最早等了 2 小时") +
        wallCell("今日完成", "9", "失败 2") +
        wallCell("成功率", "90%", "近 7 天 93.5%") +
        wallCell("今日 tokens", "2,488 万", "≈ ¥ 6.20") +
        "</div>" +
        '<div class="grid-2">' +
        panel("实时流",
          lrow({ i: "doc", t: "21-24章修订复评 · 续7", tag: '<span class="tag warn">运行中</span>', d: "正在压 22 章字数 2,612 → 2,500", meta: "47 分" }) +
          lrow({ i: "check", t: "每日仓库巡检", tag: '<span class="tag ok">完成</span>', d: "18 个仓库 · 3 处变化", meta: "38s" }) +
          lrow({ i: "check", t: "知识库整理", tag: '<span class="tag ok">完成</span>', d: "新增 6 篇 · 合并 2 组重复", meta: "1m 12s" }) +
          lrow({ i: "warn", t: "《头七结账人》第 24 章 收尾", tag: '<span class="tag bad">失败</span>', d: "服务重启中断，现场已保留", meta: "6 小时前" })) +
        panel("作品进度 · 《头七结账人》",
          bar("正文进度", "22 / 40", 55) +
          bar("本章字数", "2,486 / 2,500", 99) +
          bar("第二轮修订", "21 / 24 章", 88) +
          note("断线时这里显示「—」并标「正在重试」，不会用 0 顶替真实数据。", "warn")) +
        "</div>";
    }
  };
  function wallCell(k, v, d) {
    return '<div class="w"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="d">' + d + "</div></div>";
  }

  /* 记忆星图 */
  PAGES.atlas = {
    title: "记忆星图",
    render: function () {
      var nodes = [
        [300, 208, 11, "头七结账人", "core"],
        [300, 116, 8, "写作偏好", ""], [304, 300, 7, "评审规则", ""],
        [374, 166, 7, "第二轮规格", ""], [230, 166, 6, "界面口味", ""], [376, 262, 6, "大屏口径", ""],
        [226, 262, 6, "交付模板", ""], [436, 206, 5, "发布平台", ""], [172, 208, 5, "命名规范", ""],
        [336, 352, 5, "PDF 改名", ""], [246, 350, 4, "客户偏好", ""], [448, 300, 4, "伏笔清单", ""],
        [186, 112, 4, "节拍表", ""]
      ];
      var links = [[0, 1], [0, 2], [0, 3], [0, 4], [0, 5], [0, 6], [0, 7], [0, 8], [2, 9], [2, 10], [5, 11], [1, 12], [3, 12]];
      var svg = '<svg viewBox="70 50 460 330" preserveAspectRatio="xMidYMid meet">' +
        '<circle cx="300" cy="208" r="78" fill="none" stroke="#d9ebf4" stroke-dasharray="3 5"/>' +
        '<circle cx="300" cy="208" r="132" fill="none" stroke="#e2f0f7" stroke-dasharray="3 5"/>' +
        '<circle cx="300" cy="208" r="186" fill="none" stroke="#eaf4f9" stroke-dasharray="3 5"/>' +
        links.map(function (l) {
          var a = nodes[l[0]], b = nodes[l[1]];
          return '<line x1="' + a[0] + '" y1="' + a[1] + '" x2="' + b[0] + '" y2="' + b[1] + '" stroke="#cfe5f0" stroke-width="1"/>';
        }).join("") +
        nodes.map(function (n) {
          var big = !!n[4];
          return '<circle cx="' + n[0] + '" cy="' + n[1] + '" r="' + (n[2] + 7) + '" fill="none" stroke="#cfe5f0" opacity=".55"/>' +
            '<circle cx="' + n[0] + '" cy="' + n[1] + '" r="' + n[2] + '" fill="' + (big ? "#4f8ea6" : "#9ccade") + '" opacity="' + (big ? ".92" : ".66") + '"/>' +
            '<text x="' + (n[0] + n[2] + 9) + '" y="' + (n[1] + 4) + '" font-size="11.5" fill="#4c6a76">' + n[3] + "</text>";
        }).join("") + "</svg>";
      return head("记忆星图", "它记住的东西不是一列清单，而是一张网：越常被用到的记忆，离中心越近。",
        '<button class="btn-sm" data-open-settings="memory">' + ico("sliders") + "管理记忆</button>" +
        '<button class="btn-sm" data-toast="演示版：重新布局">' + ico("refresh") + "重排</button>") +
        '<div class="atlas-wrap">' +
        '<div class="panel" style="padding:12px"><div class="atlas-canvas">' + svg + "</div>" +
        '<p style="font-size:10.6px;color:#9dafb7;text-align:center;margin:8px 0 2px">13 个记忆节点 · 今天被命中 7 次 · 数据只在本机</p></div>' +
        '<div class="panel"><div class="panel-h" style="padding-top:2px"><h3>图例</h3><span class="sp"></span></div>' +
        '<div class="atlas-legend">' +
        '<div class="legend-item"><span class="legend-dot" style="background:#4f8ea6"></span><span>项目主干：所有任务都会读，改动要谨慎</span></div>' +
        '<div class="legend-item"><span class="legend-dot" style="background:#9ccade"></span><span>偏好与经验：命中后会自动加权</span></div>' +
        '<div class="legend-item"><span class="legend-dot" style="background:#dcecf4"></span><span>虚线环：近期没被用到的记忆，会慢慢淡出</span></div>' +
        "</div>" +
        '<div style="height:1px;background:#f2f6f9;margin:14px 0"></div>' +
        lrow({ i: "spark", t: "最常命中", d: "写作偏好 · 本周 14 次", meta: "连载家" }) +
        lrow({ i: "bulb", t: "刚学会", d: "「数字不撒谎」：拿不到就显示 —", meta: "2 小时前" }) +
        "</div></div>";
    }
  };

  /* 页面清单 */
  PAGES.index = {
    title: "页面清单",
    render: function () {
      var groups = [
        ["主界面", [["home", "工作台", "一句话起任务 + 需要你处理"], ["task", "任务详情", "对话 / 步骤 / 成果"], ["runs", "运行记录", "台账、勾选、批量删"], ["automation", "自动化", "到点自己跑"]]],
        ["工作区", [["experts", "专家", "专家 / 技能 / 连接器"], ["kanban", "看板", "五个状态列"], ["browser", "浏览器", "预览、验证、发布"], ["overview", "概览", "项目进展与长目标"]]],
        ["洞察", [["availability", "通道体检", "可用 8 · 熔断 1"], ["board", "大屏看板", "副屏那一版"], ["atlas", "记忆星图", "记忆之间的网"]]]
      ];
      var settingsPages = [["general", "通用", "6 张入口卡"], ["appearance", "外观", "10 套主题"], ["tasks", "任务", "默认值"], ["usage", "用量统计", "花了多少"],
        ["models", "模型接入", "内置 + 自定义"], ["agents", "本机智能体", "CLI 探测"], ["review", "人工评审", "3 条待裁决"], ["market", "插件市场", "装能力"],
        ["memory", "记忆中心", "改 / 删 / 重置"], ["knowledge", "知识库", "42 篇"], ["skills", "经验库", "12 条"], ["advanced", "高级设置", "编排与钩子"],
        ["data", "数据与备份", "整包带走"], ["about", "关于与更新", "v0.1.121"], ["improve", "帮助改进", "诊断包"], ["guide", "帮助中心", "上手与快捷键"]];
      return head("页面清单", "这次重构覆盖的全部界面。点任意一张进入对应页面或设置页。",
        '<button class="btn-sm" data-toast="共 28 个界面（含 16 个设置内页）">共 28 个界面</button>') +
        groups.map(function (g) {
          return '<div class="panel"><div class="panel-h"><h3>' + g[0] + "</h3><span class=\"sp\"></span>" +
            '<span class="hint">' + g[1].length + " 页</span></div>" +
            '<div class="pgrid" style="padding-bottom:14px">' + g[1].map(function (p) {
              return '<button class="pcard" type="button" data-page="' + p[0] + '"><b>' + p[1] + "</b><span>" + p[2] + "</span></button>";
            }).join("") + "</div></div>";
        }).join("") +
        '<div class="panel"><div class="panel-h"><h3>设置内页</h3><span class="sp"></span><span class="hint">点击直接打开</span></div>' +
        '<div class="pgrid" style="padding-bottom:14px">' + settingsPages.map(function (p) {
          return '<button class="pcard" type="button" data-open-settings="' + p[0] + '"><b>' + p[1] + "</b><span>" + p[2] + "</span></button>";
        }).join("") + "</div></div>";
    }
  };

  /* ---------------- 设置内页 ---------------- */
  function srow(title, desc, ctl) {
    return '<div class="row"><div class="txt"><h4>' + title + "</h4><p>" + desc + "</p></div>" +
      '<div class="side-ctl">' + ctl + "</div></div>";
  }
  function sel(opts) {
    return '<select class="select">' + opts.map(function (o) { return "<option>" + o + "</option>"; }).join("") + "</select>";
  }

  var PANES = {
    general: {
      title: "通用", desc: "每天都会碰到的六个入口，点进去改。",
      body: '<div class="card">' +
        srow("语言与地区", "界面文案和日期格式都按这里走。", sel(["简体中文（中国大陆）", "English"])) +
        srow("启动时先看", "打开应用后先落在哪里。", sel(["工作台", "上次的任务", "运行记录"])) +
        srow("关闭后最小化到托盘", "关窗口不退出，定时任务照常跑。", sw(true, "最小化到托盘")) +
        "</div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>常用设置</h3><span class="sp"></span><span style="font-size:11px;color:#93a5ae">点卡片进入</span></div>' +
        '<div class="pgrid" style="grid-template-columns:repeat(3,minmax(0,1fr));padding-bottom:10px">' +
        entryCard("models", "hex", "用哪个模型", "内置免费模型开箱能用，也能接自己的") +
        entryCard("market", "apps", "装点本事", "专家、技能、连接器，按需装") +
        entryCard("appearance", "palette", "长什么样", "10 套主题，换肤不换结构") +
        entryCard("data", "phone", "在手机上看", "局域网连手机，关掉即断开") +
        entryCard("data", "db", "备份与搬家", "整包导出，换电脑不丢") +
        entryCard("usage", "wallet", "花了多少", "每一笔 token 都能对着台账") +
        "</div></div>"
    },
    appearance: {
      title: "外观", desc: "换主题不换结构：一套 token 派生整套界面。",
      body: '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>主题</h3><span class="sp"></span><span style="font-size:11px;color:#93a5ae">点击即时预览</span></div>' +
        '<div class="theme-grid">' +
        themeCard("honey", "蜜巢", "linear-gradient(140deg,#fdfaf5,#f1e4cd)", true) +
        themeCard("aqua", "浅色", "linear-gradient(140deg,#f7fbfd,#d9edf7)", false) +
        themeCard("", "深色", "linear-gradient(140deg,#2a3138,#171b1f)", false) +
        themeCard("", "有风", "linear-gradient(140deg,#f3f8f5,#dcece2)", false) +
        themeCard("", "经典", "linear-gradient(140deg,#f8f5f0,#eae2d5)", false) +
        themeCard("", "QQ", "linear-gradient(140deg,#f2f7ff,#dbe8fb)", false) +
        themeCard("", "紫金秋", "linear-gradient(140deg,#f7f1fb,#e6d8f2)", false) +
        themeCard("", "同行", "linear-gradient(140deg,#f4f6f7,#dfe4e7)", false) +
        themeCard("", "涟漪", "linear-gradient(140deg,#f2f9fa,#d5ebef)", false) +
        themeCard("", "伙伴", "linear-gradient(140deg,#fdf6f2,#f2dfd5)", false) +
        "</div>" +
        '<p style="margin:10px 0 0;font-size:10.8px;color:#a3b2ba">样张里做了两套可切换（蜜巢 / 浅色），其余主题沿用同一结构，只换 token。</p>' +
        '<div style="height:1px;background:#f2f6f9;margin:14px 0 2px"></div>' +
        srow("字号", "整体缩放界面，不改系统设置。", sel(["标准", "偏大", "偏小"])) +
        srow("界面密度", "紧凑只影响列表行高，不影响点击目标。", sel(["标准", "紧凑"])) +
        srow("侧栏默认收起", "想要更大画布时打开。", sw(false, "侧栏收起")) +
        "</div>"
    },
    tasks: {
      title: "任务", desc: "新建任务时的默认值，改一次就少填几栏。",
      body: '<div class="card">' +
        srow("默认专家", "不确定就选「自动判断」，它会按目标挑人设。", sel(["自动判断", "连载家", "架构师", "交付匠"])) +
        srow("默认工作目录", "留空用数据目录下的 workspaces。", '<input class="select" style="width:230px" value="C:\\Users\\HP\\AppData\\Roaming\\workspace">') +
        srow("开跑前先给步骤", "让它在动手前给你一版步骤，你点头再跑。", sw(true, "跑前给步骤")) +
        srow("危险动作先问你", "删除、重启、发布、强推这类动作会停下来等确认。", sw(true, "危险动作确认")) +
        srow("同时运行上限", "超出的任务排队，不会抢同一个工作区。", sel(["2 个任务", "1 个任务", "3 个任务"])) +
        "</div>"
    },
    usage: {
      title: "用量统计", desc: "花了多少能对上本地台账，没有隐藏配额。",
      body: '<div class="card pad">' +
        '<div class="quota-hero"><span class="quota-num">24.8M</span><span>本月总 tokens</span>' +
        '<button class="link" style="margin-left:auto" data-toast="已导出 usage.csv（演示）">导出明细 ' + ico("arrow-r") + "</button></div>" +
        '<div class="metrics"><div class="metric"><div class="k">输入</div><div class="v">18.2M</div></div>' +
        '<div class="metric"><div class="k">输出</div><div class="v">6.6M</div></div>' +
        '<div class="metric"><div class="k">折合费用</div><div class="v">¥ 68.40</div></div></div>' +
        '<div style="font-size:11px;color:#93a5ae;margin-bottom:6px">Token 活动 · 近 26 周</div>' +
        '<div class="heat">' + heatCells() + "</div>" +
        '<div class="heat-legend"><span>少</span><i style="background:#eef5f9"></i><i style="background:#e2f0f7"></i><i style="background:#c8e3ef"></i><i style="background:#9ccadd"></i><i style="background:#6aa4ba"></i><span>多</span></div>' +
        "</div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>每日趋势</h3><span class="sp"></span><span style="font-size:11px;color:#93a5ae">近 7 天</span></div>' +
        '<div class="bars" style="height:96px">' +
        [["10-27", 42], ["10-28", 61], ["10-29", 35], ["10-30", 78], ["10-31", 55], ["11-01", 88], ["今天", 46]].map(function (b, i) {
          return '<div class="b' + (i === 6 ? " on" : "") + '"><i style="height:' + b[1] + '%"></i><span>' + b[0] + "</span></div>";
        }).join("") + "</div></div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>按模型分账</h3><span class="sp"></span><span style="font-size:11px;color:#93a5ae">近 30 天</span></div>' +
        bar("Our Free Model · 内置", "62%", 62) + bar("DeepSeek 官方", "24%", 24) + bar("智谱 GLM", "14%", 14) + "</div>" +
        note("内置免费模型不计费；这里显示的都是能核对的真值。", "chart")
    },
    models: {
      title: "模型接入", desc: "内置的先用着，不够再接自己的。密钥只存本机。",
      body: '<div class="card pad"><div style="display:flex;align-items:center;gap:12px">' +
        '<span class="model-ico" style="width:34px;height:34px">' + ico("hex") + "</span>" +
        '<div style="flex:1"><h4 style="margin:0 0 3px;font-size:12.8px">Our Free Model</h4>' +
        '<p style="margin:0;font-size:11.6px;color:#8b9da6">内置免费模型，无需密钥，装完就能跑。所有任务默认用它。</p></div>' +
        '<span class="tag ok">使用中</span></div></div>' +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>接自己的</h3><span class="sp"></span>' +
        '<button class="btn" data-toast="演示版：添加模型表单先留空">添加模型</button></div>' +
        '<div class="grid-3" style="padding-bottom:6px">' +
        vendor("DS", "DeepSeek 官方", "deepseek-v4.1-flash · 已连接", "可用", true) +
        vendor("智", "智谱 GLM", "GLM-5.3 · Coding Plan · 已连接", "可用", true) +
        vendor("OR", "OpenRouter", "auto 多厂商路由 · 已熔断，等网络恢复", "已熔断", false) +
        vendor("硅", "硅基流动", "Qwen3-Max · 未填密钥", "未连接", false) +
        vendor("月", "Moonshot", "kimi-k2 · 未填密钥", "未连接", false) +
        vendor("本", "本机 Ollama", "localhost:11434 · 未探测到", "未连接", false) +
        "</div></div>" +
        note("模型添加后写入本机配置文件，不上传；换电脑走「数据与备份」整包迁移。", "info")
    },
    agents: {
      title: "本机智能体", desc: "电脑上装了哪些命令行 AI，能不能直接用。",
      body: '<div class="card">' +
        srow("自动探测本机 CLI", "启动时扫一遍 PATH。探测要起子进程，慢的话可以关掉。", sw(false, "自动探测")) +
        srow("默认工作目录", "CLI 在自己的仓库里跑，不碰 CodeBee 的目录。", '<input class="select" style="width:230px" value="E:\\GoOut">') +
        "</div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>已检测到</h3><span class="sp"></span>' +
        '<button class="btn" data-toast="已重新扫描（演示）">重新扫描</button></div>' +
        agentRow("opencode", "v1.4.2", "可用", "ok", "1.2s") +
        agentRow("claude-code", "v2.0.1", "可用", "ok", "860ms") +
        agentRow("gemini-cli", "v0.9.0", "未登录", "warn", "—") +
        "</div>" +
        note("CLI 会继承系统环境；密钥用它们自己的登录方式，CodeBee 不代存。", "info")
    },
    review: {
      title: "人工评审", desc: "跑完的成果先过你这一关，批准才算交付。",
      body: '<div class="banner">' + ico("check") + '<span class="sp">3 条待你确认 · 最早一条等了 2 小时</span>' +
        '<button class="btn-sm" data-toast="已全部通过（演示）">全部通过</button></div>' +
        '<div class="card">' +
        reviewRow("22 章整章重写方案", "会动 2 处伏笔呼应，预计多花 6 分钟", "连载家 · 47 分钟") +
        reviewRow("电缆报告批量改名", "12 份 .docx 已生成，型号统一为 WDZBN，二维码已删除", "交付匠 · 2m 05s") +
        reviewRow("《头七结账人》第 24 章 收尾", "服务重启中断，现场已保留，可重跑", "连载家 · 6 小时前") +
        "</div>" +
        '<div class="card">' +
        srow("验收门槛", "低于门槛的运行直接标红，不进交付。", sel(["评审 4 项全过", "3 项及以上", "不设门槛"])) +
        srow("裁决后自动归档", "避免产物越堆越多。", sw(true, "自动归档")) +
        "</div>"
    },
    market: {
      title: "插件市场", desc: "装别人做好的能力包：写作规范、工程守则、MCP 工具。",
      body: '<div class="grid-3">' +
        plugCard("写作规范包", "章节节奏、对话占比、禁用词表", "12.4k 安装", true) +
        plugCard("工程守则", "改前复现、最小改动、必须带守测", "9.8k 安装", true) +
        plugCard("视觉验收", "对截图做对比度、间距、溢出检查", "4.3k 安装", true) +
        plugCard("MCP · 数据库", "只读查询 MySQL / PG，结果进产物", "6.1k 安装", false) +
        plugCard("发布助手", "番茄 / 七猫格式校验与同步", "3.6k 安装", false) +
        plugCard("大屏皮肤", "4 套大屏配色，跟随主界面 token", "2.2k 安装", false) +
        "</div>" +
        note("插件只声明它能用的工具；装完还要在任务里显式勾选才会生效。", "info")
    },
    memory: {
      title: "记忆中心", desc: "它记住的偏好和项目背景，随时可看、可改、可清空。",
      body: '<div class="card">' +
        srow("对话记忆", "记住对话里的重要信息，让后续更贴合你。", sw(true, "对话记忆")) +
        srow("本地记忆与技能", "自动记录工作过程并优化技能，数据只在本机。", sw(true, "本地记忆")) +
        "</div>" +
        '<div class="card pad">' +
        '<div style="display:flex;align-items:center;gap:10px;padding-bottom:14px;border-bottom:1px solid #f2f6f9">' +
        '<div style="flex:1"><h4 style="margin:0 0 3px;font-size:12.8px">管理记忆</h4>' +
        '<p style="margin:0;font-size:11.6px;color:#8b9da6">每晚自动整理更新，可随时查看和编辑。</p></div>' +
        '<div class="btn-row"><button class="btn danger" data-toast="演示版：清空需要二次确认">重置</button>' +
        '<button class="btn" data-toast="演示版：编辑器先留空">编辑</button>' +
        '<button class="btn" data-toast="演示版：导入入口先留空">导入</button></div></div>' +
        '<div class="memo">' +
        "<h4>工作背景</h4><p>同时跑三类活：小说连载《头七结账人》、客户交付文档、内部数据看板。写作任务偏「先立设定再逐章推进」，代码任务偏「先复现再改」。</p>" +
        "<h4>个人背景</h4><p>中文沟通，指令简短。对排版间隔敏感，接受不了贴死的版式；对「数字不撒谎」尤其在意，拿不到的数据宁可显示「—」。</p>" +
        "<h4>当前关注</h4><p>把界面整体换成淡青纸张 + 悬浮侧栏的语言，先看样张再谈落地；同时盯着任务详情页长对话下输入框被压扁的老问题。</p>" +
        "<h4>近期动态</h4><p>- 21 章修订过评审，22-24 章在改；第二轮规格要求章末必须有钩子。<br>- 通道体检里 OpenRouter 熔断，任务自动回退到 DeepSeek。<br>- 经验库沉淀 12 条，本月命中 31 次。</p>" +
        "</div>" +
        '<div style="display:flex;align-items:center;gap:10px;justify-content:center;padding-top:4px">' +
        '<button class="link" data-page="atlas">查看记忆星图 ' + ico("arrow-r") + "</button></div>" +
        '<div style="text-align:center;font-size:10.6px;color:#a3b2ba;padding-top:6px">记忆内容仅你本人可见，数据不出本机</div>' +
        "</div>"
    },
    knowledge: {
      title: "知识库", desc: "放进去的文档，做任务时会被翻出来用；索引全在本机。",
      body: '<div class="card">' +
        srow("自动索引", "新文件进来就切块建索引，不再全量重跑。", sw(true, "自动索引")) +
        srow("检索范围", "默认只索引勾选的目录。", sel(["资料库 + 工作区", "仅资料库", "仅工作区"])) +
        "</div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>文档 · 42 篇</h3><span class="sp"></span>' +
        '<button class="btn" data-toast="演示版：上传入口先留空">上传文档</button></div>' +
        docRow("《头七结账人》设定集.md", "人物 / 世界观 / 伏笔清单", "今天") +
        docRow("第二轮修订规格.md", "钩子、字数、不许动的伏笔线", "10-30") +
        docRow("WDZB1N-YJY23 电缆检测报告.pdf", "12 页 · 已建立索引", "10-29") +
        docRow("大屏口径说明.md", "KPI 定义与分母规则", "10-28") +
        docRow("客户交付检查清单.docx", "18 项格式与命名检查", "10-27") +
        "</div>"
    },
    skills: {
      title: "经验库", desc: "踩过的坑写成规矩，下次自动遵守。",
      body: '<div class="card">' +
        srow("自动沉淀", "任务失败并修正后，问你要不要写成经验。", sw(true, "自动沉淀")) +
        srow("生效范围", "经验默认只在同类专家下生效。", sel(["同类专家生效", "全部任务生效", "仅本次任务"])) +
        "</div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>已沉淀 · 12 条</h3><span class="sp"></span><span style="font-size:11px;color:#93a5ae">本月命中 31 次</span></div>' +
        skillRow("长对话下输入条必须 flex: 0 0 auto", "否则高度亏空全压给输入框，实测只剩 22px", "架构师 · 命中 6 次") +
        skillRow("CSS 断言先确认片段唯一", "盲替换会改到别的规则，得出「测试是空的」假结论", "架构师 · 命中 4 次") +
        skillRow("章末钩子要提前到上一章结尾", "21 章结尾改成「第二张清单」，22 章才有引子", "连载家 · 命中 5 次") +
        skillRow("拿不到的数据写「—」不写 0", "0 会被读成真实值，比空着更糟", "全体 · 命中 9 次") +
        skillRow("空槽 margin 不折叠，要 :empty{display:none}", "否则会出现 24px 的莫名空隙", "架构师 · 命中 7 次") +
        "</div>"
    },
    data: {
      title: "数据与备份", desc: "所有产物和记忆都在本机，随时能整包带走。",
      body: '<div class="card">' +
        srow("导出全部数据", "任务、产物、记忆、配置打成一个文件。", '<button class="btn" data-toast="已开始导出（演示）">导出</button>') +
        srow("从文件恢复", "导入会先备份当前数据，再按时间戳合并。", '<button class="btn" data-toast="演示版：请选择备份文件">选择文件</button>') +
        srow("在手机上看", "同一局域网扫码连上，关掉即断开，不走公网。", '<button class="btn" data-toast="演示版：二维码先留空">显示二维码</button>') +
        srow("清理缓存", "只删缩略图和临时文件，不动任务与产物。", '<button class="btn danger" data-toast="演示版：清理需要二次确认">清理</button>') +
        "</div>" +
        '<div class="card pad"><div class="card-h" style="padding-top:2px"><h3>存储位置</h3></div>' +
        kv("数据目录", "%APPDATA%\\CodeBee · 86 MB") +
        kv("工作区目录", "C:\\Users\\HP\\AppData\\Roaming\\workspace · 1.2 GB") +
        kv("产物目录", "workspace\\artifacts · 218 个文件") +
        "</div>" +
        note("不上传云端；手机连接只走局域网，关掉即断开。", "db")
    },
    advanced: {
      title: "高级设置", desc: "编排、调度、评测这些技术项都收在这里，不确定就别改。",
      body: '<div class="card">' +
        advRow("编排设置", "默认流程、步骤上限、失败重试次数", "3 项") +
        advRow("钩子", "任务前后执行本机命令，失败可阻断", "2 个启用") +
        advRow("模型调度", "按专家绑定模型与回退顺序", "5 条规则") +
        advRow("评测集", "回归用的固定任务集，防越改越差", "18 个用例") +
        advRow("禅道", "Bug 同步地址与专用账号", "未配置") +
        advRow("微信摘要", "群聊摘要的抓取范围与频率", "已连接") +
        "</div>" +
        note("这些改动会影响所有任务，改完建议先跑一遍评测集。", "info")
    },
    about: {
      title: "关于与更新", desc: "版本、更新日志和体检报告。",
      body: '<div class="card pad"><div style="display:flex;align-items:center;gap:14px">' +
        '<span class="model-ico" style="width:42px;height:42px;background:#fff;border:1px solid #e4eff5;border-radius:50%"><img class="logo-42" src="logo.png" alt="CodeBee"></span>' +
        '<div><div style="font-size:14px;font-weight:650">CodeBee v0.1.121</div>' +
        '<div style="font-size:11.4px;color:#8ea0a9;margin-top:3px">Windows · npm 全局安装 · 上次检查 2 小时前</div></div>' +
        '<button class="btn" style="margin-left:auto" data-toast="已是最新版本 v0.1.121">检查更新</button></div></div>' +
        '<div class="card"><div class="panel-h" style="padding:13px 0 6px"><h3>更新日志</h3><span class="sp"></span></div>' +
        lrow({ i: "spark", t: "v0.1.121", d: "首页输入口支持专家预填；修复长对话输入框被压扁", meta: "今天" }) +
        lrow({ i: "layers", t: "v0.1.120", d: "新增记忆星图；大屏改为快照回退，断线不再显示 0", meta: "10-30" }) +
        lrow({ i: "doc", t: "v0.1.115", d: "作品信息页签间距重排；hero 改挂页签", meta: "10-28" }) +
        "</div>" +
        '<div class="card">' +
        srow("体检报告", "一键检查端口占用、模型连通与磁盘空间。", '<button class="btn" data-toast="体检完成：3 项通过，1 项提示">运行体检</button>') +
        "</div>"
    },
    improve: {
      title: "帮助改进", desc: "匿名错误报告帮我们定位问题，内容你可以先看再发。",
      body: '<div class="card">' +
        srow("匿名错误报告", "只包含堆栈与版本号，不含稿件和文件内容。", sw(true, "匿名错误报告")) +
        srow("诊断包", "最近 200 行日志、配置摘要与环境信息。", '<button class="btn" data-toast="诊断包已生成（演示）">生成</button>') +
        srow("一键反馈", "带着诊断包打开反馈窗口。", '<button class="btn" data-toast="演示版：反馈窗口先留空">反馈</button>') +
        "</div>" +
        note("发送前会列出将上传的每一项，取消即不发。", "shield")
    },
    guide: {
      title: "帮助中心", desc: "不会用的时候先看这里，比问它快。",
      body: '<div class="grid-3">' +
        guideCard("rocket", "5 分钟上手", "新建任务、选专家、看产物") +
        guideCard("layers", "任务与产物", "步骤、确认、产物都长什么样") +
        guideCard("clock", "自动化", "定时任务怎么写、怎么停") +
        guideCard("db", "资料库", "知识库 / 经验库 / 记忆怎么配合") +
        guideCard("cpu", "本机智能体", "接 CLI、配模型、看通道") +
        guideCard("shield", "权限与安全", "完全访问意味着什么") +
        "</div>" +
        '<div class="card pad"><div class="panel-h" style="padding-top:2px"><h3>常用快捷键</h3><span class="sp"></span></div>' +
        '<div class="grid-2">' +
        shortcut("新聊天", "⌘ N") + shortcut("打开设置", "⌘ ,") +
        shortcut("搜索任务与产物", "⌘ K") + shortcut("发送消息", "Enter") +
        shortcut("换行", "Shift Enter") + shortcut("停止当前运行", "Esc") +
        "</div></div>"
    }
  };

  /* ---------------- 设置内页的零件 ---------------- */
  function entryCard(pane, i, title, desc) {
    return '<button class="pcard" type="button" data-open-settings="' + pane + '">' +
      '<span class="grp">' + ico(i) + "</span><b>" + title + "</b><span>" + desc + "</span></button>";
  }
  function themeCard(set, name, pv, on) {
    var soon = !set;
    return '<button class="theme-card' + (on ? " on" : "") + (soon ? " soon" : "") + '" type="button"' +
      (set ? ' data-set="' + set + '"' : ' data-toast="样张只做了两套可切换主题，其余沿用同一结构"') + ">" +
      '<span class="pv" style="display:block;background:' + pv + '"></span><span class="nm">' + name + "</span></button>";
  }
  function heatCells() {
    var out = "";
    for (var i = 0; i < 182; i++) {
      var v = (i * 37 + (i % 7) * 11 + (i % 13) * 5) % 17;
      var c = v > 13 ? "l4" : v > 10 ? "l3" : v > 7 ? "l2" : v > 4 ? "l1" : "";
      out += '<i class="' + c + '"></i>';
    }
    return out;
  }
  function vendor(badge, name, desc, state, ok) {
    return '<div class="vendor"><div class="vendor-h"><span class="model-ico" style="width:26px;height:26px;font-size:10px">' + badge + "</span>" +
      "<b>" + name + '</b><span style="flex:1"></span><span class="tag ' + (ok ? "ok" : "") + '">' + state + "</span></div>" +
      "<p>" + desc + "</p>" +
      '<div class="row-in"><input type="password" placeholder="粘贴 API Key" aria-label="' + name + ' API Key">' +
      '<button class="btn-sm" data-toast="已保存 ' + name + ' 密钥（演示）">保存</button></div></div>';
  }
  function modelRow(badge, name, tag, connected) {
    return '<div class="model-row"><span class="model-ico">' + badge + "</span>" +
      '<span class="nm">' + name + '<span class="tag">' + tag + '</span></span><span class="sp"></span>' +
      (connected ? '<span class="tag ok">可用</span>' : "") +
      '<button class="icon-ghost" type="button" data-toast="编辑模型（演示）">' + ico("pencil") + "</button>" +
      '<button class="icon-ghost" type="button" data-toast="测试连接（演示）">' + ico("link") + "</button>" +
      '<button class="icon-ghost danger" type="button" data-toast="删除模型（演示）">' + ico("trash") + "</button></div>";
  }
  function agentRow(name, ver, state, tone, cost) {
    return '<div class="model-row"><span class="model-ico">' + name.slice(0, 2).toUpperCase() + "</span>" +
      '<span class="nm">' + name + '<span class="tag">' + ver + '</span></span><span class="sp"></span>' +
      '<span class="tag ' + tone + '">' + state + '</span><span style="font-size:11px;color:#93a5ae;width:48px;text-align:right">' + cost + "</span>" +
      '<button class="btn-sm" data-toast="演示版：切换为该 CLI">使用</button></div>';
  }
  function reviewRow(title, desc, meta) {
    return '<div class="row"><div class="txt"><h4>' + title + "</h4><p>" + desc + " · " + meta + "</p></div>" +
      '<div class="side-ctl"><button class="btn" data-toast="已打回：' + title + '">打回</button>' +
      '<button class="btn dark" data-toast="已通过：' + title + '">通过</button></div></div>';
  }
  function plugCard(name, desc, meta, installed) {
    return '<div class="xcard"><div class="x-top"><span class="xava">' + ico("apps") + "</span>" +
      "<div><h4>" + name + '</h4><div class="x-role">' + meta + "</div></div></div><p>" + desc + "</p>" +
      '<div class="x-foot"><span class="tag">插件</span><span style="flex:1"></span>' +
      '<button class="btn-sm' + (installed ? "" : " dark") + '" data-toast="' + (installed ? "已启用：" : "已安装：") + name + '">' +
      (installed ? "已安装" : "安装") + "</button></div></div>";
  }
  function docRow(name, desc, when) {
    return '<div class="model-row"><span class="model-ico">' + ico("doc") + "</span>" +
      '<span class="nm">' + name + '<span class="tag">' + desc + '</span></span><span class="sp"></span>' +
      '<span class="lrow-meta">' + when + "</span>" +
      '<button class="icon-ghost" type="button" data-toast="演示版：预览先留空">' + ico("link") + "</button></div>";
  }
  function skillRow(title, desc, meta) {
    return '<div class="model-row"><span class="model-ico">' + ico("layers") + "</span>" +
      '<span class="nm" style="max-width:520px">' + title + '<span class="tag">' + meta + '</span></span><span class="sp"></span>' +
      '<button class="icon-ghost" type="button" data-toast="编辑经验（演示）">' + ico("pencil") + "</button>" +
      '<button class="icon-ghost danger" type="button" data-toast="停用经验（演示）">' + ico("trash") + "</button></div>";
  }
  function advRow(title, desc, meta) {
    return '<div class="lrow"><span class="lrow-ico">' + ico("sliders") + "</span>" +
      '<div class="lrow-txt"><h4>' + title + '</h4><p>' + desc + "</p></div>" +
      '<span class="lrow-meta">' + meta + '</span><button class="icon-ghost" type="button" data-toast="' + title + '（演示）">' + ico("chev-r") + "</button></div>";
  }
  function guideCard(i, title, desc) {
    return '<div class="xcard"><div class="x-top"><span class="xava">' + ico(i) + "</span>" +
      "<div><h4>" + title + '</h4></div></div><p>' + desc + "</p>" +
      '<div class="x-foot"><span style="flex:1"></span><button class="btn-sm" data-toast="演示版：文档先留空">打开</button></div></div>';
  }
  function shortcut(label, key) {
    return '<div class="row"><div class="txt"><h4>' + label + '</h4></div><div class="side-ctl"><span class="kbd">' + key + "</span></div></div>";
  }
  function fallback(key) {
    return { title: key, desc: "这一页还在画，结构会与其余设置页保持一致。", body: '<div class="empty-state">' + ico("layers") + "<br>一张汇总卡 + 若干行设置 + 危险动作单独收口。</div>" };
  }

  /* ---------------- 设置渲染 ---------------- */
  function renderPane(key) {
    var p = PANES[key] || fallback(key);
    $("#set-pane").innerHTML =
      '<div class="pane-head"><div><h2>' + p.title + "</h2><p>" + p.desc + "</p></div>" +
      '<span class="sp"></span><button class="pane-close" type="button" id="pane-close" aria-label="关闭设置">' + ico("x") + "</button></div>" +
      p.body +
      '<p style="text-align:center;font-size:10.5px;color:#a9b8bf;margin:18px 0 0">CodeBee v0.1.121 · 全站界面样张，不改动真实数据</p>';
    $("#set-pane").scrollTop = 0;
    $$(".set-item").forEach(function (b) { b.classList.toggle("on", b.dataset.pane === key); });
    var c = $("#pane-close");
    if (c) c.addEventListener("click", closeSettings);
  }
  function openSettings(key) {
    renderPane(key || "general");
    $("#settings").classList.remove("hidden");
    closePop();
  }
  function closeSettings() { $("#settings").classList.add("hidden"); }

  /* ---------------- 用户浮层 ---------------- */
  function openPop() { $("#user-pop").classList.remove("hidden"); }
  function closePop() { $("#user-pop").classList.add("hidden"); }

  /* ---------------- 右键菜单（对齐真实产品：侧栏任务树 / 项目文件夹） ---------------- */
  var ctxEl = null;
  function closeCtx() {
    if (!ctxEl) return;
    ctxEl.remove();
    ctxEl = null;
    document.removeEventListener("mousedown", onCtxDocDown, true);
  }
  function onCtxDocDown(e) { if (ctxEl && !ctxEl.contains(e.target)) closeCtx(); }
  function openCtx(x, y, items) {
    closeCtx();
    var el = document.createElement("div");
    el.className = "ctx";
    el.innerHTML = items.map(function (it) {
      if (it === "-") return '<div class="ctx-sep"></div>';
      return '<button class="ctx-item' + (it.danger ? " danger" : "") + '" type="button">' + it.label + "</button>";
    }).join("");
    document.body.appendChild(el);
    var r = el.getBoundingClientRect();
    el.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + "px";
    el.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + "px";
    var btns = $$(".ctx-item", el);
    el.addEventListener("click", function (e) {
      var b = e.target.closest(".ctx-item");
      if (!b) return;
      var it = items.filter(function (i) { return i !== "-"; })[btns.indexOf(b)];
      closeCtx();
      if (it && it.fn) it.fn();
    });
    ctxEl = el;
    setTimeout(function () { document.addEventListener("mousedown", onCtxDocDown, true); }, 0);
  }

  document.addEventListener("contextmenu", function (e) {
    var row = e.target.closest(".side .proj-row, .side [data-task]");
    if (!row) return;
    e.preventDefault();
    var items;
    if (row.classList.contains("proj-row")) {
      /* 文件夹右键：与真实产品 sdir 菜单同构 */
      items = [
        { label: "查看文件", fn: function () { toast("打开项目文件列表（演示）"); } },
        { label: "新建任务到该目录", fn: function () { toast("演示版：新建任务表单先留空"); } },
        "-",
        { label: "打开工作目录", fn: function () { toast("已在资源管理器中打开工作目录（演示）"); } },
        { label: "复制工作目录路径", fn: function () { toast("已复制：C:\\Users\\HP\\AppData\\Roaming\\workspace"); } },
        "-",
        { label: "置顶该文件夹", fn: function () { toast("已置顶《头七结账人》（演示）"); } },
        "-",
        { label: "移除该文件夹", danger: true, fn: function () { toast("演示版：移除只从侧栏摘掉，不删文件"); } },
        { label: "删除该文件夹", danger: true, fn: function () { toast("演示版：删除需要二次确认"); } }
      ];
    } else {
      /* 任务右键：与真实产品 taskContextItems 同构 */
      var failed = /失败/.test(row.dataset.meta || "");
      items = [
        { label: "打开详情", fn: function () { openTask(row.dataset.task, row.dataset.meta); } },
        "-",
        { label: "打开工作目录", fn: function () { toast("已在资源管理器中打开（演示）"); } },
        { label: "复制工作目录路径", fn: function () { toast("已复制工作目录路径（演示）"); } },
        { label: "复制日志目录路径", fn: function () { toast("已复制日志目录路径（演示）"); } }
      ];
      if (failed) items.push({ label: "↻ 继续任务", fn: function () { toast("重试已排队（演示）"); } });
      items = items.concat([
        { label: "基于此任务新建", fn: function () { toast("演示版：带上下文新建任务"); } },
        { label: "继续连载（新任务）", fn: function () { toast("演示版：从第 25 章接着写"); } },
        { label: "提前完结", fn: function () { toast("演示版：提前完结需要确认"); } },
        "-",
        { label: "重命名任务", fn: function () { toast("演示版：重命名输入框先留空"); } },
        { label: "归档", fn: function () { toast("已归档（演示）"); } },
        { label: "删除任务", danger: true, fn: function () { toast("演示版：删除需要二次确认"); } }
      ]);
    }
    openCtx(e.clientX, e.clientY, items);
  });

  /* ---------------- 任务 ---------------- */
  function openTask(title, meta) {
    $("#task-title").textContent = title;
    $("#task-meta").textContent = meta || "运行中";
    $("#task-live").style.background = /运行/.test(meta || "") ? "#3f9d74" : "#b4c4cb";
    $$(".task-row").forEach(function (r) {
      r.classList.toggle("on", r.dataset.task === title && !r.dataset.page);
    });
    markNav("");
    go("task");
    closePop();
  }

  function scrollBottom() { var b = $("#chat-scroll"); b.scrollTop = b.scrollHeight; }

  function appendReply(goal) {
    var wrap = document.createElement("div");
    wrap.className = "msg-ai";
    wrap.innerHTML = '<div class="ai-avatar">' + bee() + "</div>" +
      '<div class="ai-body"><div class="ai-name">CodeBee <span class="tag">正在拆步骤…</span></div>' +
      '<div class="ai-text"><span class="typing"><i></i><i></i><i></i></span></div></div>';
    $("#chat-stream").appendChild(wrap);
    scrollBottom();
    setTimeout(function () {
      wrap.querySelector(".ai-text").innerHTML =
        "<p>收到，先按「能验收」的方式拆：</p>" +
        "<ol><li>确认范围 —— 这次要动的文件和产物，我先列出来，不顺手改别处。</li>" +
        "<li>小步跑 —— 一次只推进一步，每步都能回看原始输出，失败就说人话。</li>" +
        "<li>给验收 —— 结束时给你一条能直接看的产物，或者能自己跑的验证方式。</li></ol>" +
        "<p>这次的目标是：<strong>" + esc(goal) + "</strong></p>" +
        "<p>要不要我先跑第一步？涉及删除、重启、发布这类动作，我会停下来先问你。</p>";
      wrap.querySelector(".ai-name").innerHTML = 'CodeBee <span class="tag">已读 · 刚刚</span>';
      var tools = document.createElement("div");
      tools.className = "tools-row";
      tools.innerHTML = '<button class="tool" type="button" data-toast="演示版：产物列表先留空">' + ico("doc") + "查看产物</button>" +
        '<span class="tool-sep"></span><span class="tool">' + ico("hex") + "Our Free Model</span>";
      wrap.querySelector(".ai-body").appendChild(tools);
      scrollBottom();
    }, 900);
  }

  function sendGoal(text) {
    if (!text) { $("#home-input").focus(); return; }
    $("#task-title").textContent = text.length > 16 ? text.slice(0, 16) + "…" : text;
    $("#task-meta").textContent = "运行中 · 第 1 步 · 刚刚";
    $("#task-live").style.background = "#3f9d74";
    $("#chat-stream").innerHTML = '<div class="msg-user"><div class="bubble">' + esc(text) + "</div></div>";
    $("#task-input").value = "";
    $("#home-input").value = "";
    go("task");
    markNav("");
    $$(".task-row").forEach(function (r) { r.classList.remove("on"); });
    appendReply(text);
  }

  /* ---------------- 事件 ---------------- */
  document.addEventListener("click", function (e) {
    var t = e.target;

    var swEl = t.closest(".switch");
    if (swEl) { swEl.setAttribute("aria-checked", swEl.getAttribute("aria-checked") === "true" ? "false" : "true"); return; }

    var noop = t.closest("[data-noop]");
    if (noop) { toast("演示版：这个入口先不接功能"); return; }

    if (t.closest("#btn-new-chat")) {
      $("#home-input").value = "";
      goPage("home");
      setTimeout(function () { $("#home-input").focus(); }, 30);
      return;
    }

    var pageBtn = t.closest("[data-page]");
    if (pageBtn) { goPage(pageBtn.dataset.page); return; }

    var openSet = t.closest("[data-open-settings]");
    if (openSet) { openSettings(openSet.dataset.openSettings); return; }

    var toastBtn = t.closest("[data-toast]");
    if (toastBtn) { toast(toastBtn.dataset.toast); return; }

    var taskRow = t.closest("[data-task]");
    if (taskRow) { openTask(taskRow.dataset.task, taskRow.dataset.meta); return; }

    var more = t.closest("#btn-more");
    if (more) {
      var box = $("#side-more");
      box.classList.toggle("hidden");
      more.setAttribute("aria-expanded", box.classList.contains("hidden") ? "false" : "true");
      return;
    }

    var setItem = t.closest(".set-item");
    if (setItem) { renderPane(setItem.dataset.pane); return; }

    var segBtn = t.closest(".seg button");
    if (segBtn) {
      $$(".seg button").forEach(function (b) { b.classList.toggle("on", b === segBtn); });
      toast(segBtn.textContent.trim());
      return;
    }

    var swatch = t.closest("[data-set]");
    if (swatch) {
      document.documentElement.dataset.palette = swatch.dataset.set;
      $$("[data-set]").forEach(function (s) { s.classList.toggle("on", s === swatch); });
      toast("主题已切换到「" + swatch.textContent.trim() + "」");
      return;
    }

    if (t.closest("#approve-yes")) {
      $("#approve").innerHTML = '<div class="approved-note">' + ico("check") + "已允许 · 22 章开始整章重写，完成后会通知你</div>";
      toast("已允许：22 章开始整章重写");
      return;
    }
    if (t.closest("#approve-no")) {
      $("#approve").innerHTML = '<div class="approved-note" style="color:#7d8f98">' + ico("check") + "已跳过，只压字数到 2,500</div>";
      return;
    }

    if (t.closest("#user-btn")) {
      if ($("#user-pop").classList.contains("hidden")) openPop(); else closePop();
      return;
    }
    if (t.closest("#open-settings-btn")) { openSettings("general"); return; }
    if (t.closest("#home-send")) { sendGoal($("#home-input").value.trim()); return; }
    if (t.closest("#task-send")) {
      var v = $("#task-input").value.trim();
      if (!v) { $("#task-input").focus(); return; }
      $("#chat-stream").insertAdjacentHTML("beforeend", '<div class="msg-user"><div class="bubble">' + esc(v) + "</div></div>");
      $("#task-input").value = "";
      appendReply(v);
      return;
    }

    if (!t.closest("#user-pop")) closePop();
  });

  $("#home-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendGoal(this.value.trim()); }
  });
  $("#task-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      var v = this.value.trim();
      if (!v) return;
      $("#chat-stream").insertAdjacentHTML("beforeend", '<div class="msg-user"><div class="bubble">' + esc(v) + "</div></div>");
      this.value = "";
      appendReply(v);
    }
  });

  $("#set-search").addEventListener("input", function () {
    var q = this.value.trim().toLowerCase();
    $$(".set-item").forEach(function (b) {
      b.style.display = !q || b.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none";
    });
    $$(".set-group").forEach(function (g) { g.style.display = q ? "none" : ""; });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (ctxEl) { closeCtx(); return; }
      if (!$("#settings").classList.contains("hidden")) { closeSettings(); return; }
      if (!$("#user-pop").classList.contains("hidden")) { closePop(); return; }
      goPage("home");
    }
    if ((e.metaKey || e.ctrlKey) && e.key === ",") { e.preventDefault(); openSettings("general"); }
  });

  markNav("home");
})();
