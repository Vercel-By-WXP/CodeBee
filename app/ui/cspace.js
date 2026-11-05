/* 空间管家 · 设置 → 数据页的独立覆盖层（2026-11-05）
 *
 * 加载顺序：必须在 i18n.js / app.js / borrow-ui.js 之后（要用 t / authHeaders /
 * uiApproveCard）。删掉 index.html 里这一行 <script> 即整层回滚，后端包不受影响。
 *
 * 交互红线：默认只读。清理 / 搬家 / 建链接 / 去重替换四件事，一律
 * 「先出计划 → 审批卡二次确认 → 显式 confirm 才提交」，界面上没有任何
 * 一点就删的入口。原生 alert/confirm/prompt 一律不用。
 */
(function () {
  "use strict";

  const BASE = "/api/cspace/";
  const g = (id) => document.getElementById(id);
  const _t = (s) => (typeof window.t === "function" ? window.t(s) : s);
  const _esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  /* 英文词条注册进 i18n.js 暴露的同一份字典（window.I18N_EN）。
   * 不直接改 i18n.js：并行会话会在同一检出里持续改那个共享文件，往里插
   * 95 行容易互相冲掉；注册到同一个 EN 对象后 t() 照样命中，效果等价。 */
  const EN_DICT = {
    "上层": "Up", "个文件": "files", "份": "copies",
    "优先移入系统回收站；失败则归档到数据目录，绝不硬删。": "Files go to the system Recycle Bin first; if that fails they are archived under the data directory. Nothing is hard-deleted.",
    "会解除目录联接并把数据搬回原路径，目标副本移入回收站。": "Removes the junction, moves data back to the original path, and sends the target copy to the Recycle Bin.",
    "保留": "Keep", "允许执行": "Allow actions",
    "先复制并校验，再把原路径换成目录联接；任一步失败自动回滚，原目录留备份。": "Copies and verifies first, then replaces the original path with a directory junction; any failure rolls back automatically and the original folder is kept as a backup.",
    "其余副本改成指向它的硬链接，路径不变、内容不变，立刻释放重复占用的空间。": "Other copies become hard links to it. Paths and content stay the same, and the duplicated space is freed immediately.",
    "刷新账本": "Refresh ledger", "只读模式": "Read-only",
    "只读模式下不执行，请先解锁": "Read-only mode: unlock before running actions",
    "可执行": "Ready", "可清理候选": "Cleanup candidates", "可用": "free",
    "合计": "Total",
    "填好目录后点「扫描大文件」或「查找重复」，全程只读。": "Enter a directory, then click \"Scan large files\" or \"Find duplicates\". Everything here is read-only.",
    "大文件": "Large files", "安全": "Safe", "完成": "Done",
    "已完成，明细如下": "Done. Details:", "已恢复只读": "Back to read-only",
    "已提权": "Elevated", "已解锁": "Unlocked",
    "已解锁，可执行": "Unlocked; actions enabled", "已链接": "Linked",
    "开始搬家": "Start migration", "恢复只读": "Back to read-only",
    "执行清理": "Run cleanup", "扫描中…": "Scanning…",
    "扫描大文件": "Scan large files", "扫描这一层": "Scan this level",
    "拒绝": "Refused", "搬家": "Migrate", "搬家候选": "Migration candidates",
    "操作未完成，请查看明细。": "Operation did not finish. See details.",
    "文件会移入系统回收站（失败则归档），可在回收站找回；不会直接删除。": "Files go to the Recycle Bin (or an archive if that fails) and can be restored. Nothing is deleted outright.",
    "断链": "Broken link", "普通权限": "Standard privileges",
    "替换为硬链接": "Replace with hard link", "有阻碍": "Blocked",
    "条": "entries", "条规则": "rules", "查找重复": "Find duplicates",
    "正在执行…": "Working…", "没有候选项。": "No candidates.",
    "没有发现重复文件": "No duplicate files found",
    "没有可清理项。": "Nothing to clean.",
    "没有找到大文件": "No large files found", "没有记录": "No records",
    "清理": "Cleanup",
    "清理只走系统回收站或归档，绝不硬删；搬家留备份且失败自动回滚。规则数据来自上游 MIT 项目，见仓库根 THIRD_PARTY_NOTICES.md。": "Cleanup only uses the Recycle Bin or an archive, never hard deletion; migration keeps a backup and rolls back on failure. Rule data comes from an upstream MIT project; see THIRD_PARTY_NOTICES.md at the repository root.",
    "清理审计": "Cleanup audit", "清理计划": "Cleanup plan",
    "源目录（点下面一行填入）": "Source directory (click a row below)",
    "生成清理计划": "Build cleanup plan", "生成计划中…": "Building plan…",
    "盘点中…": "Inventorying…",
    "目标位置（另一个盘的绝对路径）": "Target path (absolute path on another drive)",
    "确定解锁？": "Unlock actions?", "确认开始搬家？": "Start migration?",
    "确认执行": "Confirm", "确认执行清理？": "Run this cleanup?",
    "确认替换为硬链接？": "Replace with a hard link?", "确认迁回？": "Move back?",
    "禁止": "Blocked", "空间管家": "Space Manager",
    "要扫描的目录，多个用逗号隔开": "Directories to scan, comma-separated",
    "要透视的目录（绝对路径）": "Directory to inspect (absolute path)",
    "规则库": "Rule library",
    "解锁后清理与搬家才会真正改动文件；默认仍是每次动作二次确认，且绝不硬删用户文件。": "Only after unlocking can cleanup and migration actually change files. Every action still needs a second confirmation, and user files are never hard-deleted.",
    "请先勾选要清理的目录": "Select at least one directory first",
    "请先填写目录": "Enter a directory first", "请先生成计划": "Build a plan first",
    "请先预检并通过": "Run a passing precheck first",
    "请选择源目录并填写目标位置": "Choose a source and enter a target path",
    "读取账本…": "Reading ledger…", "账本": "Ledger",
    "软链接账本": "Link ledger", "迁回": "Move back",
    "迁回完成": "Moved back", "迁移记录": "Migration history",
    "这一层共": "Items at this level:",
    "这一层没有内容，或目录不可读。": "Nothing here, or the directory is unreadable.",
    "透视": "Inspect", "重复文件": "Duplicate files", "重新盘点": "Re-scan",
    "链接": "Link", "需确认": "Caution", "项": "items",
    "预检": "Precheck", "预检中…": "Prechecking…",
    "预检结果": "Precheck result", "高级": "Advanced",
    "默认只读：透视、盘点、账本都能用；要清理或搬家，先解锁「允许执行」，每次动作还要再确认一次。": "Read-only by default: inspection, inventory and ledger all work. Unlock \"Allow actions\" to clean or migrate; every action still needs a second confirmation.",
  };
  if (window.I18N_EN && typeof window.I18N_EN === "object") {
    Object.assign(window.I18N_EN, EN_DICT);
  }

  const S = {
    tab: "scan", status: null, scanPath: "", scanStack: [], scanItems: [],
    cleanItems: [], cleanPlan: null, migItems: [], migSrc: "", migCheck: null,
    poll: null, op: "", busy: false, drives: [],
  };

  async function api(tail, opts) {
    const o = opts || {};
    const headers = Object.assign({},
      (typeof window.authHeaders === "function" ? window.authHeaders() : null) || {},
      o.body ? { "Content-Type": "application/json" } : {});
    const qs = o.params ? "?" + Object.keys(o.params)
      .map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(o.params[k]))
      .join("&") : "";
    const res = await fetch(BASE + tail + qs, {
      method: o.method || "GET", headers,
      body: o.body ? JSON.stringify(o.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }
  const get = (tail, params) => api(tail, { params });
  const post = (tail, body) => api(tail, { method: "POST", body });

  async function approve(title, detail, allowText) {
    if (typeof window.uiApproveCard === "function") {
      return await window.uiApproveCard({ title, detail, allowText });
    }
    if (typeof window.uiConfirm === "function") {
      try { return !!(await window.uiConfirm(title + "\n" + detail)); }
      catch (e) { return false; }
    }
    return false;
  }

  function toast(msg) {
    if (typeof window.toast === "function") window.toast(msg);
  }

  function out(html, cls) {
    const el = g("cs-out");
    if (el) el.innerHTML = html;
    const plan = g("cs-plan");
    if (plan && cls === "plan") { plan.hidden = false; plan.innerHTML = html; }
  }
  function wait(msg) { out('<span class="cs-wait">' + _esc(msg) + "</span>"); }
  function fail(e) {
    out('<span class="cs-err">' + _esc(e && e.message ? e.message : e) + "</span>");
  }
  function clearPlan() {
    const p = g("cs-plan");
    if (p) { p.hidden = true; p.innerHTML = ""; }
  }

  function badge(level) {
    const map = {
      safe: ["ok", _t("安全")],
      caution: ["mid", _t("需确认")],
      danger: ["no", _t("禁止")],
    };
    const v = map[level] || ["", _esc(level || "—")];
    return '<span class="cs-badge ' + v[0] + '">' + v[1] + "</span>";
  }

  // ---------------------------------------------------------------- 骨架
  function shell() {
    const tabs = [
      ["scan", _t("透视")], ["clean", _t("清理")],
      ["migrate", _t("搬家")], ["ledger", _t("账本")],
      ["advanced", _t("高级")],
    ];
    const lock = S.status && S.status.readonly === false;
    return '<summary>' + _t("空间管家") +
      '<span class="cs-lock' + (lock ? " cs-open" : "") + '" id="cs-lock">' +
      _esc(lock ? _t("已解锁") : _t("只读模式")) + "</span></summary>" +
      '<div class="cs-sub">' + _t("默认只读：透视、盘点、账本都能用；要清理或搬家，先解锁「允许执行」，每次动作还要再确认一次。") + "</div>" +
      '<div class="cs-bar"><button class="cs-btn' + (lock ? " danger" : "") +
      '" data-act="' + (lock ? "lock" : "unlock") + '">' +
      _esc(lock ? _t("恢复只读") : _t("允许执行")) + "</button>" +
      '<span class="hint" id="cs-stat"></span></div>' +
      '<div class="cs-tabs">' + tabs.map((x) =>
        '<button class="cs-tab' + (S.tab === x[0] ? " on" : "") +
        '" data-act="tab" data-tab="' + x[0] + '">' + x[1] + "</button>").join("") +
      "</div>" +
      '<div class="cs-pane" id="cs-pane"></div>' +
      '<div class="cs-out" id="cs-out"></div>' +
      '<div class="cs-plan" id="cs-plan" hidden></div>' +
      '<div class="cs-foot">' + _t("清理只走系统回收站或归档，绝不硬删；搬家留备份且失败自动回滚。规则数据来自上游 MIT 项目，见仓库根 THIRD_PARTY_NOTICES.md。") + "</div>";
  }

  function paneBar(inner) { return '<div class="cs-bar">' + inner + "</div>"; }
  function listHost() { return '<div class="cs-list" id="cs-list"></div>'; }
  function empty(msg) { return '<div class="cs-empty">' + _esc(msg) + "</div>"; }

  function render() {
    const box = g("cspace-box");
    if (!box) return;
    box.innerHTML = shell();
    const pane = g("cs-pane");
    if (!pane) return;
    const p = S.scanPath || (S.drives[0] ? (S.drives[0].letter || "") : "");
    let html = "";
    if (S.tab === "scan") {
      html = '<div class="cs-drives" id="cs-drives"></div>' +
        paneBar('<input class="grow" type="text" id="cs-path" value="' + _esc(p) +
          '" placeholder="' + _esc(_t("要透视的目录（绝对路径）")) + '">' +
          '<button class="cs-btn primary" data-act="scan">' + _t("扫描这一层") + "</button>" +
          '<button class="cs-btn" data-act="up"' + (S.scanStack.length ? "" : " disabled") +
          '>' + _t("上层") + "</button>") +
        '<div class="cs-crumb" id="cs-crumb"></div>' + listHost();
    } else if (S.tab === "clean") {
      html = paneBar('<span class="hint">' + _t("优先移入系统回收站；失败则归档到数据目录，绝不硬删。") +
        '</span><button class="cs-btn" data-act="clean-load">' + _t("重新盘点") + "</button>" +
        '<button class="cs-btn" data-act="clean-plan">' + _t("生成清理计划") + "</button>" +
        '<button class="cs-btn primary" data-act="clean-run" disabled>' + _t("执行清理") + "</button>") +
        listHost();
    } else if (S.tab === "migrate") {
      html = paneBar('<input class="grow" type="text" id="cs-src" value="' + _esc(S.migSrc) +
        '" placeholder="' + _esc(_t("源目录（点下面一行填入）")) + '">' +
        '<input class="grow" type="text" id="cs-dst" placeholder="' +
        _esc(_t("目标位置（另一个盘的绝对路径）")) + '">' +
        '<button class="cs-btn" data-act="mig-check">' + _t("预检") + "</button>" +
        '<button class="cs-btn primary" data-act="mig-run" disabled>' + _t("开始搬家") + "</button>") +
        listHost();
    } else if (S.tab === "ledger") {
      html = paneBar('<button class="cs-btn" data-act="ledger-load">' + _t("刷新账本") +
        "</button>") + listHost();
    } else {
      html = paneBar('<input class="grow" type="text" id="cs-roots" value="' + _esc(p) +
        '" placeholder="' + _esc(_t("要扫描的目录，多个用逗号隔开")) + '">' +
        '<input type="number" id="cs-min" value="100" min="1" max="4096" style="width:88px">' +
        '<span class="hint">MB</span>' +
        '<button class="cs-btn" data-act="big">' + _t("扫描大文件") + "</button>" +
        '<button class="cs-btn" data-act="dup">' + _t("查找重复") + "</button>") +
        listHost();
    }
    pane.innerHTML = html;
    fillStat();
    loadTab();
  }

  function fillStat() {
    const el = g("cs-stat");
    if (!el || !S.status) return;
    el.textContent = _t("规则库") + " " + (S.status.rules || 0) + " " + _t("条规则") +
      " · " + (S.status.elevated ? _t("已提权") : _t("普通权限"));
  }

  // ---------------------------------------------------------------- 各页数据
  function loadTab() {
    if (S.tab === "scan") return renderDrives();
    if (S.tab === "clean") return loadClean();
    if (S.tab === "migrate") return loadMigrate();
    if (S.tab === "ledger") return loadLedger();
    const list = g("cs-list");
    if (list) list.innerHTML = empty(_t("填好目录后点「扫描大文件」或「查找重复」，全程只读。"));
  }

  function renderDrives() {
    const host = g("cs-drives");
    if (!host) return;
    if (!S.drives.length) { host.innerHTML = ""; return; }
    host.innerHTML = S.drives.map((d) => {
      const pct = d.total ? Math.round((d.used || 0) * 100 / d.total) : 0;
      return '<div class="cs-drive"><b>' + _esc(d.letter || "") + " " +
        _esc(d.label || "") + '</b><span class="cs-meta">' +
        _esc(d.free_human || (d.free != null ? Math.round(d.free / 1073741824) + " GB" : "—")) +
        " " + _t("可用") + " / " +
        _esc(d.total_human || (d.total != null ? Math.round(d.total / 1073741824) + " GB" : "—")) +
        '</span><div class="cs-meter"><i style="width:' + pct + '%"></i></div></div>';
    }).join("");
  }

  function scanRows() {
    const host = g("cs-list");
    if (!host) return;
    const crumb = g("cs-crumb");
    if (crumb) crumb.innerHTML = _esc(S.scanPath || "—");
    if (!S.scanItems.length) { host.innerHTML = empty(_t("这一层没有内容，或目录不可读。")); return; }
    host.innerHTML = S.scanItems.map((it, i) =>
      '<div class="cs-row' + (it.is_dir && !it.reparse ? " click" : "") +
      '" data-act="scan-row" data-i="' + i + '">' +
      '<span class="cs-name">' + (it.is_dir ? "▸ " : "· ") + _esc(it.name) + "</span>" +
      '<span class="cs-path">' + _esc(it.path) + "</span>" +
      (it.reparse ? '<span class="cs-badge mid">' + _t("链接") + "</span>" : "") +
      '<span class="cs-meta">' + _esc(it.size_human || "—") +
      (it.files ? " · " + it.files + " " + _t("个文件") : "") + "</span></div>").join("");
  }

  async function doScan(path) {
    wait(_t("扫描中…"));
    try {
      const d = await get("scan", { path });
      S.scanPath = d.path || path;
      S.scanItems = d.items || [];
      const inp = g("cs-path");
      if (inp) inp.value = S.scanPath;
      scanRows();
      out('<span class="cs-ok">' + _esc(_t("这一层共")) + " " + (d.total || 0) + " " +
        _esc(_t("项")) + "</span>");
    } catch (e) { fail(e); }
  }

  function loadClean(refresh) {
    wait(_t("盘点中…"));
    get("candidates", { kind: "clean", refresh: refresh ? 1 : 0 }).then((d) => {
      S.cleanItems = d.items || [];
      const host = g("cs-list");
      if (!host) return;
      if (!S.cleanItems.length) {
        host.innerHTML = empty(_t("没有可清理项。"));
        out("");
        return;
      }
      host.innerHTML = S.cleanItems.map((it, i) =>
        '<label class="cs-row"><input type="checkbox" data-i="' + i + '" data-role="clean-sel"' +
        (it.risk === "danger" || !it.cleanable ? " disabled" : "") + '>' +
        '<span class="cs-name">' + _esc(it.name || it.rule_id || "—") + "</span>" +
        badge(it.risk) +
        '<span class="cs-path">' + _esc(it.path) + "</span>" +
        '<span class="cs-meta">' + _esc(it.size_human || "—") + "</span></label>").join("");
      out(_t("可清理候选") + " " + S.cleanItems.length + " · " + _esc(d.total_human || "—"));
    }).catch(fail);
  }

  function selectedClean() {
    return Array.from(document.querySelectorAll('[data-role="clean-sel"]:checked'))
      .map((c) => S.cleanItems[Number(c.dataset.i)])
      .filter((x) => x && x.path);
  }

  async function cleanPlan() {
    const rows = selectedClean();
    if (!rows.length) { out('<span class="cs-err">' + _esc(_t("请先勾选要清理的目录")) + "</span>"); return; }
    wait(_t("生成计划中…"));
    try {
      const d = await post("clean/plan", { paths: rows.map((x) => x.path), recycle: true });
      S.cleanPlan = d;
      const btn = document.querySelector('[data-act="clean-run"]');
      if (btn) btn.disabled = false;
      const items = (d.items || []).map((x) =>
        "<li>" + _esc(x.path) + " · " + _esc(x.size_human || "—") + "</li>").join("");
      const refused = (d.refused || []).map((x) =>
        "<li>" + _esc(x.path) + " · " + _esc(_t("拒绝")) + "：" + _esc(x.reason) + "</li>").join("");
      const html = "<strong>" + _esc(_t("清理计划")) + "</strong> " +
        _esc(d.plan_id || "") + "<br>" + _esc(_t("合计")) + " " +
        _esc(d.total_human || "—") + " · " + (d.items || []).length + " " +
        _esc(_t("项")) + "<ul class='cs-steps'>" + items + refused + "</ul>";
      out(html, "plan");
    } catch (e) { fail(e); }
  }

  async function cleanRun() {
    if (!S.cleanPlan || !S.cleanPlan.plan_id) {
      out('<span class="cs-err">' + _esc(_t("请先生成计划")) + "</span>"); return;
    }
    if (S.status && S.status.readonly !== false) {
      out('<span class="cs-err">' + _esc(_t("只读模式下不执行，请先解锁")) + "</span>"); return;
    }
    const ok = await approve(_t("确认执行清理？"),
      (S.cleanPlan.items || []).map((x) => x.path).join("、") + "\n" +
      _t("文件会移入系统回收站（失败则归档），可在回收站找回；不会直接删除。"),
      _t("确认执行"));
    if (!ok) return;
    const btn = document.querySelector('[data-act="clean-run"]');
    if (btn) btn.disabled = true;
    wait(_t("正在执行…"));
    try {
      const d = await post("clean/run", { plan_id: S.cleanPlan.plan_id, confirm: true });
      if (d.op_id) { S.op = d.op_id; poll(d.op_id); }
      else { fail(new Error(d.error || _t("操作未完成，请查看明细。"))); }
    } catch (e) { fail(e); }
  }

  function loadMigrate() {
    wait(_t("盘点中…"));
    get("candidates", { kind: "migrate" }).then((d) => {
      S.migItems = d.items || [];
      const host = g("cs-list");
      if (!host) return;
      if (!S.migItems.length) { host.innerHTML = empty(_t("没有候选项。")); out(""); return; }
      host.innerHTML = S.migItems.map((it, i) =>
        '<div class="cs-row click" data-act="mig-row" data-i="' + i + '">' +
        '<span class="cs-name">' + _esc(it.name || it.rule_id || "—") + "</span>" +
        (it.is_linked ? '<span class="cs-badge ok">' + _t("已链接") + "</span>" : "") +
        badge(it.risk) +
        '<span class="cs-path">' + _esc(it.path) + "</span>" +
        '<span class="cs-meta">' + _esc(it.size_human || "—") + "</span></div>").join("");
      out(_t("搬家候选") + " " + S.migItems.length);
    }).catch(fail);
  }

  async function migCheck() {
    const src = (g("cs-src") || {}).value || "";
    const dst = (g("cs-dst") || {}).value || "";
    if (!src.trim() || !dst.trim()) {
      out('<span class="cs-err">' + _esc(_t("请选择源目录并填写目标位置")) + "</span>"); return;
    }
    wait(_t("预检中…"));
    try {
      const d = await post("migrate/precheck", { src: src.trim(), dst: dst.trim() });
      S.migCheck = d;
      const checks = (d.checks || []).map((c) =>
        "<li>" + (c.ok ? "✓ " : "✗ ") + _esc(c.name) + " · " + _esc(c.detail || "") + "</li>").join("");
      const blockers = (d.blockers || []).map((b) =>
        "<li>" + _esc(b) + "</li>").join("");
      const html = "<strong>" + _esc(_t("预检结果")) + "</strong> · " +
        (d.ok ? _t("可执行") : _t("有阻碍")) +
        "<ul class='cs-steps'>" + checks + blockers + "</ul>";
      out(html, "plan");
      const btn = document.querySelector('[data-act="mig-run"]');
      if (btn) btn.disabled = !d.ok;
    } catch (e) { fail(e); }
  }

  async function migRun() {
    if (!S.migCheck || !S.migCheck.ok) {
      out('<span class="cs-err">' + _esc(_t("请先预检并通过")) + "</span>"); return;
    }
    if (S.status && S.status.readonly !== false) {
      out('<span class="cs-err">' + _esc(_t("只读模式下不执行，请先解锁")) + "</span>"); return;
    }
    const src = (g("cs-src") || {}).value.trim();
    const dst = (g("cs-dst") || {}).value.trim();
    const ok = await approve(_t("确认开始搬家？"),
      src + " → " + dst + "\n" +
      _t("先复制并校验，再把原路径换成目录联接；任一步失败自动回滚，原目录留备份。"),
      _t("确认执行"));
    if (!ok) return;
    const btn = document.querySelector('[data-act="mig-run"]');
    if (btn) btn.disabled = true;
    wait(_t("正在执行…"));
    try {
      const d = await post("migrate/run", { src, dst, confirm: true });
      if (d.op_id) { S.op = d.op_id; poll(d.op_id); }
      else { fail(new Error(d.error || _t("操作未完成，请查看明细。"))); }
    } catch (e) { fail(e); }
  }

  function loadLedger() {
    wait(_t("读取账本…"));
    Promise.all([get("links"), get("migrations"), get("audit", { limit: 20 })])
      .then(([a, b, c]) => {
        const host = g("cs-list");
        if (!host) return;
        let html = '<div class="cs-row"><span class="cs-name"><b>' +
          _t("软链接账本") + "</b></span><span class='cs-meta'>" +
          (a.total || 0) + " " + _t("条") + "</span></div>";
        (a.items || []).forEach((x) => {
          html += '<div class="cs-row"><span class="cs-name">' +
            _esc(x.source || "—") + "</span>" +
            (x.broken ? '<span class="cs-badge mid">' + _t("断链") + "</span>" : "") +
            '<span class="cs-path">' + _esc(x.target || "—") + "</span></div>";
        });
        html += '<div class="cs-row"><span class="cs-name"><b>' +
          _t("迁移记录") + "</b></span><span class='cs-meta'>" +
          (b.total || 0) + " " + _t("条") + "</span></div>";
        (b.items || []).forEach((x, i) => {
          html += '<div class="cs-row"><span class="cs-name">' +
            _esc(x.source || "—") + "</span>" +
            '<span class="cs-path">' + _esc(x.target || "—") + "</span>" +
            '<button class="cs-btn danger" data-act="restore" data-src="' +
            _esc(x.source || "") + '">' + _t("迁回") + "</button></div>";
        });
        html += '<div class="cs-row"><span class="cs-name"><b>' +
          _t("清理审计") + "</b></span><span class='cs-meta'>" +
          (c.total || 0) + " " + _t("条") + "</span></div>";
        (c.items || []).slice(-8).forEach((x) => {
          html += '<div class="cs-row"><span class="cs-name">' +
            _esc(x.path || x.action || "—") + "</span>" +
            '<span class="cs-meta">' + _esc(x.ts || x.at || "") + "</span></div>";
        });
        host.innerHTML = html || empty(_t("没有记录"));
        out("");
      }).catch(fail);
  }

  async function restoreOne(src) {
    if (!src) return;
    if (S.status && S.status.readonly !== false) {
      out('<span class="cs-err">' + _esc(_t("只读模式下不执行，请先解锁")) + "</span>"); return;
    }
    const ok = await approve(_t("确认迁回？"),
      src + "\n" + _t("会解除目录联接并把数据搬回原路径，目标副本移入回收站。"),
      _t("确认执行"));
    if (!ok) return;
    wait(_t("正在执行…"));
    try {
      const d = await post("migrate/restore", { src, confirm: true });
      out('<span class="' + (d.ok === false ? "cs-err" : "cs-ok") + '">' +
        _esc(_t("迁回完成")) + " · " + _esc(JSON.stringify(d).slice(0, 240)) + "</span>");
      loadLedger();
    } catch (e) { fail(e); }
  }

  async function advanced(kind) {
    const roots = ((g("cs-roots") || {}).value || "").trim();
    const mb = Number((g("cs-min") || {}).value || 100);
    if (!roots) { out('<span class="cs-err">' + _esc(_t("请先填写目录")) + "</span>"); return; }
    wait(_t("扫描中…"));
    try {
      const d = await get(kind === "big" ? "bigfiles" : "duplicates",
        { paths: roots, min_mb: mb });
      const host = g("cs-list");
      if (!host) return;
      if (kind === "big") {
        const rows = d.items || [];
        host.innerHTML = rows.length ? rows.map((x) =>
          '<div class="cs-row"><span class="cs-name">' + _esc(x.category || "—") +
          "</span>" + '<span class="cs-path">' + _esc(x.path) + "</span>" +
          '<span class="cs-meta">' + _esc(x.size_human || "—") + "</span></div>").join("") :
          empty(_t("没有找到大文件"));
        out(_t("大文件") + " " + (d.total || 0));
      } else {
        const groups = d.groups || [];
        host.innerHTML = groups.length ? groups.map((gp, gi) =>
          '<div class="cs-row"><span class="cs-name">' +
          _esc(_t("重复文件")) + " " + (gp.files || []).length + " " + _esc(_t("份")) +
          "</span>" + badge("caution") +
          '<span class="cs-path">' + _esc((gp.files || [])[0] || "") + "</span>" +
          '<span class="cs-meta">' + _esc(gp.wasted_human || "—") + "</span>" +
          '<button class="cs-btn" data-act="dedup" data-g="' + gi + '">' +
          _t("替换为硬链接") + "</button></div>").join("") :
          empty(_t("没有发现重复文件"));
        S.dupGroups = groups;
        out(_t("重复文件") + " " + (d.total || 0) + " · " + _esc(d.wasted_human || "—"));
      }
    } catch (e) { fail(e); }
  }

  async function dedup(gi) {
    const gp = (S.dupGroups || [])[gi];
    if (!gp || !(gp.files || []).length || gp.files.length < 2) return;
    if (S.status && S.status.readonly !== false) {
      out('<span class="cs-err">' + _esc(_t("只读模式下不执行，请先解锁")) + "</span>"); return;
    }
    const ok = await approve(_t("确认替换为硬链接？"),
      _t("保留") + " " + gp.files[0] + "\n" +
      _t("其余副本改成指向它的硬链接，路径不变、内容不变，立刻释放重复占用的空间。"),
      _t("确认执行"));
    if (!ok) return;
    try {
      const d = await post("dedup/replace",
        { keep: gp.files[0], duplicate: gp.files[1], confirm: true });
      out('<span class="' + (d.ok === false ? "cs-err" : "cs-ok") + '">' +
        _esc(_t("完成")) + " · " + _esc(JSON.stringify(d).slice(0, 240)) + "</span>");
    } catch (e) { fail(e); }
  }

  // ---------------------------------------------------------------- 进度
  function poll(op) {
    clearTimeout(S.poll);
    get("progress", { op }).then((d) => {
      const ev = (d.events || []).slice(-6).map((x) =>
        "<li>" + _esc(x.current || x.phase || "…") + "</li>").join("");
      let html = "<strong>" + _esc(_t("正在执行…")) + "</strong> " +
        Math.round(d.elapsed || 0) + "s<ul class='cs-steps'>" + ev + "</ul>";
      if (d.status === "done" || d.status === "failed") {
        html = "<strong>" + _esc(d.status === "done" ? _t("已完成，明细如下") :
          _t("操作未完成，请查看明细。")) + "</strong>" +
          (d.error ? " · " + _esc(d.error) : "") +
          "<ul class='cs-steps'><li>" +
          _esc(JSON.stringify(d.result || {}).slice(0, 600)) + "</li></ul>";
        S.busy = false;
        S.cleanPlan = null;
        S.migCheck = null;
        clearPlan();
        if (S.status) loadStatus().then(() => { if (S.tab === "ledger") loadLedger(); });
      } else {
        S.poll = setTimeout(() => poll(op), 700);
      }
      out(html);
    }).catch((e) => { S.busy = false; fail(e); });
  }

  function loadStatus() {
    return get("status").then((d) => {
      S.status = d;
      S.drives = d.drives || [];
      const lock = g("cs-lock");
      if (lock) {
        lock.className = "cs-lock" + (d.readonly === false ? " cs-open" : "");
        lock.textContent = d.readonly === false ? _t("已解锁") : _t("只读模式");
      }
      const btn = document.querySelector('[data-act="unlock"],[data-act="lock"]');
      if (btn) {
        btn.dataset.act = d.readonly === false ? "lock" : "unlock";
        btn.textContent = d.readonly === false ? _t("恢复只读") : _t("允许执行");
        btn.className = "cs-btn" + (d.readonly === false ? " danger" : "");
      }
      fillStat();
    });
  }

  // ---------------------------------------------------------------- 事件
  async function onAct(act, el, ev) {
    if (act === "tab") {
      S.tab = el.dataset.tab; S.busy = false; clearTimeout(S.poll);
      render(); return;
    }
    if (act === "unlock") {
      const ok = await approve(_t("确定解锁？"),
        _t("解锁后清理与搬家才会真正改动文件；默认仍是每次动作二次确认，且绝不硬删用户文件。"),
        _t("允许执行"));
      if (!ok) return;
      try {
        await post("settings", { readonly: false });
        await loadStatus();
        toast(_t("已解锁，可执行"));
      } catch (e) { fail(e); }
      return;
    }
    if (act === "lock") {
      try {
        await post("settings", { readonly: true });
        await loadStatus();
        toast(_t("已恢复只读"));
      } catch (e) { fail(e); }
      return;
    }
    if (act === "scan") {
      const p = ((g("cs-path") || {}).value || "").trim();
      if (!p) { out('<span class="cs-err">' + _esc(_t("请先填写目录")) + "</span>"); return; }
      if (S.scanPath && S.scanPath !== p) S.scanStack.push(S.scanPath);
      await doScan(p);
      return;
    }
    if (act === "up") {
      const prev = S.scanStack.pop();
      if (prev) await doScan(prev);
      return;
    }
    if (act === "scan-row") {
      const it = S.scanItems[Number(el.dataset.i)];
      if (!it || !it.is_dir || it.reparse) return;
      S.scanStack.push(S.scanPath);
      await doScan(it.path);
      return;
    }
    if (act === "clean-load") { S.cleanPlan = null; clearPlan(); return loadClean(true); }
    if (act === "clean-plan") return cleanPlan();
    if (act === "clean-run") return cleanRun();
    if (act === "mig-row") {
      const it = S.migItems[Number(el.dataset.i)];
      if (!it) return;
      S.migSrc = it.path;
      const inp = g("cs-src");
      if (inp) inp.value = it.path;
      return;
    }
    if (act === "mig-check") return migCheck();
    if (act === "mig-run") return migRun();
    if (act === "ledger-load") return loadLedger();
    if (act === "restore") return restoreOne(el.dataset.src);
    if (act === "big" || act === "dup") return advanced(act);
    if (act === "dedup") return dedup(Number(el.dataset.g));
  }

  function boot() {
    const host = g("sub-data");
    if (!host || g("cspace-box")) return;
    const box = document.createElement("details");
    box.id = "cspace-box";
    box.className = "cspace-box";
    host.insertBefore(box, host.firstChild);
    box.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-act]");
      if (!el || !box.contains(el)) return;
      ev.preventDefault();
      Promise.resolve(onAct(el.dataset.act, el, ev)).catch(fail);
    });
    loadStatus().then(render).catch((e) => { box.innerHTML = shell(); fail(e); });
    // 语言切换后重绘（动态渲染的内容没有 data-i18n 属性，applyI18n 扫不到）
    try {
      const orig = window.setLang;
      if (typeof orig === "function" && !orig.__cspaceWrapped) {
        const wrapped = function () {
          try { return orig.apply(this, arguments); }
          finally { try { render(); } catch (e) { /* 面板未挂载时静默 */ } }
        };
        wrapped.__cspaceWrapped = true;
        window.setLang = wrapped;
      }
    } catch (e) { /* 不影响主功能 */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
