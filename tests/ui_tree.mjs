/* 侧栏任务树核验（文件夹 → 任务 两级，ZCode 桌面端式样）：
 *   一级文件夹行 = 折叠箭头 + 目录图标 + 末段名（hover 全路径）+ 任务计数徽标；
 *   二级任务行 = 单行按钮：状态字形(运行旋转✻/完成绿点/失败红点/取消橙点/排队空圈/未运行暗点)
 *              + 单行省略标题 + 右对齐紧凑相对时间（刚刚/N 分/N 小时/昨天/N 天/MM-DD），
 *              无内嵌步骤层——点击整行右缘滑出「任务详情」（检查器）。
 * 覆盖：文件夹分组/排序/默认展开、结构（无步骤层）、时间右对齐、单行省略、五态字形与动画、
 *       点任务行主栏直开任务详情、选中高亮、文件夹展开状态跨重绘保留、无横向溢出。
 * 前置：TUTTI_DATA 先用 tests/_seed_tree_fixtures.py 造数（含 proj-alpha/beta/gamma 三目录），
 *       再起 18798 临时服务。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = process.env.SERVICE || "http://127.0.0.1:18798";   // 18798 常被并行 agent 双绑，可用 SERVICE 覆盖
const CDP_PORT = Number(process.env.CDP_PORT) || 9339;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const TIME_RE = /^(刚刚|\d+ 分|\d+ 小时|昨天|\d+ 天|\d{2}-\d{2})$/;

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-tree-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    // 本机 headless 默认 prefers-reduced-motion=reduce，会命中全局「减少动效」
    // 媒体查询把动画全关掉；这里的动画断言需要真实 animationName，显式关掉该偏好
    "--blink-settings=prefersReducedMotion=false",
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    // headless Edge 默认报告 prefers-reduced-motion: reduce，会（正确地）关掉自旋/脉冲动画，
    // 所以先模拟 no-preference 再断言动画生效（同 ui_check_models_icons 的处理）
    await send("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-reduced-motion", value: "no-preference" }],
    });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    // 负载高时首轮轮询 3.5s 内完不成，行还没渲染：等行出现再断言（不改断言语义）
    for (let i = 0; i < 20; i++) {
      const n = await evalJs(`document.querySelectorAll("#side-tasks .stask").length`);
      if (n > 0) break;
      await sleep(500);
    }

    /* ---- A0) 文件夹分组：三个工作目录 → 三个一级文件夹，按最近活动倒序、默认展开首个 ---- */
    const dirs0 = JSON.parse(await evalJs(`(() => {
      const ds = [...document.querySelectorAll("#side-tasks details.sdir")];
      return JSON.stringify({
        n: ds.length,
        labels: ds.map((d) => (d.querySelector("summary .t") || {}).textContent?.trim()),
        counts: ds.map((d) => (d.querySelector("summary .cnt") || {}).textContent?.trim()),
        open: ds.map((d) => d.hasAttribute("open")),
        hasFolderIcon: ds.every((d) => !!d.querySelector("summary svg use[href='#i-folder']")),
        hasChev: ds.every((d) => !!d.querySelector("summary svg.chev use[href='#i-chevron-r']")),
        titlesAttr: ds.map((d) => (d.querySelector("summary .t") || {}).getAttribute("title") || ""),
      });
    })()`));
    check("三个工作目录聚成三个文件夹", dirs0.n === 3, JSON.stringify(dirs0));
    check("文件夹按最近活动倒序（alpha 含 5 分前任务居首）",
      dirs0.labels[0] === "proj-alpha", JSON.stringify(dirs0.labels));
    check("末段名作显示、title 挂全路径",
      dirs0.labels.every((l) => !/[\\/]/.test(l)) &&
      dirs0.titlesAttr.every((p) => /proj-/.test(p)), JSON.stringify(dirs0.titlesAttr));
    check("计数徽标：alpha=2 / beta=1 / gamma=1",
      dirs0.counts.join(",") === "2,1,1", JSON.stringify(dirs0.counts));
    check("文件夹行有折叠箭头与目录图标", dirs0.hasChev && dirs0.hasFolderIcon, JSON.stringify(dirs0));
    check("无 running 时默认展开最近活动文件夹、其余折叠",
      dirs0.open[0] === true && dirs0.open.slice(1).every((o) => !o), JSON.stringify(dirs0.open));

    /* ---- A) 真实造数渲染出的结构。可见性按 DOM 契约判定（Chromium 对折叠 <details>
     * 内容仍保留布局盒，getClientRects/offsetParent 不可靠）：行「可见」= 父文件夹带 open。 ---- */
    const og = await evalJs(`(() => {
      const all = [...document.querySelectorAll("#side-tasks .stask")];
      const isOpen = (d) => !!d.closest("details.sdir")?.hasAttribute("open");
      const rows = all.filter(isOpen);
      const hs = rows.map((d) => +d.getBoundingClientRect().height.toFixed(1));
      const tmRights = rows.map((d) => { const t = d.querySelector(".tm");
        return t ? +t.getBoundingClientRect().right.toFixed(1) : null; });
      return {
        allRows: all.length,
        rows: rows.length,
        openFlags: all.map(isOpen),
        titles: rows.map((d) => (d.querySelector(".t") || {}).textContent?.trim()),
        times: rows.map((d) => (d.querySelector(".tm") || {}).textContent?.trim()),
        noChev: rows.every((d) => !d.querySelector("svg.chev")),
        glyph: rows.map((d) => !!d.querySelector(".sglyph")),
        notDetails: all.every((d) => d.tagName === "DIV"),
        heightsAllEqual: new Set(hs).size === 1,
        tmRightAligned: new Set(tmRights).size === 1,
        treeOverflow: (() => { const t = document.getElementById("side-tasks");
          return t.scrollWidth - t.clientWidth; })(),
      };
    })()`);
    const organic = JSON.stringify(og);
    check("DOM 里共 4 个任务行（分属三个文件夹）", og.allRows === 4, organic);
    check("展开的 alpha 文件夹露出 2 个任务行", og.rows === 2, organic);
    check("折叠文件夹里的行确实不可见（父级 open 与可见性一致）",
      og.openFlags.filter(Boolean).length === og.rows && og.openFlags.includes(false),
      JSON.stringify(og.openFlags));
    check("任务行是单行 div（无 details 折叠语义）", og.notDetails, organic);
    check("任务行无折叠箭头、有状态字形", og.noChev && og.glyph.every(Boolean), organic);
    check("右列是紧凑相对时间（不是原始时间戳）", og.times.every((t) => TIME_RE.test(t)), JSON.stringify(og.times));
    check("可见任务覆盖多档：分/小时",
      /分/.test(og.times.join()) && /小时/.test(og.times.join()), JSON.stringify(og.times));
    check("任务行等高（紧凑单行）", og.heightsAllEqual, organic);
    check("时间列右对齐（ZCode 特征）", og.tmRightAligned, organic);
    check("任务树无横向溢出", og.treeOverflow <= 0, "overflow=" + og.treeOverflow);

    /* ---- A2) 内嵌步骤层已移除：侧栏不再有步骤行/「查看全部」辅助行 ---- */
    const gone = await evalJs(`JSON.stringify({
      stepx: document.querySelectorAll("#side-tasks .stepx").length,
      smore: document.querySelectorAll("#side-tasks .smore").length,
      stepsBox: document.querySelectorAll("#side-tasks .stask .steps").length,
    })`);
    const gonev = JSON.parse(gone);
    check("侧栏无内嵌步骤层（.stepx/.smore/.steps 全为 0）",
      gonev.stepx === 0 && gonev.smore === 0 && gonev.stepsBox === 0, gone);

    /* ---- B) 点任务行 → 主栏（中间区域）直开任务详情；检查器让位 ---- */
    await evalJs(`document.querySelector("#side-tasks .stask[data-task]").click(); "ok"`);
    await sleep(1500);
    const opened = JSON.parse(await evalJs(`JSON.stringify({
      settingsMode: document.body.classList.contains("settings-mode"),
      detailShown: !document.getElementById("run-detail").classList.contains("hidden"),
      listHidden: document.querySelector("#sub-runs .panel:first-child").classList.contains("hidden"),
      pageTitle: document.getElementById("page-title").textContent,
      rdTitle: (document.getElementById("rd-title") || {}).textContent || "",
      inspectorHidden: document.getElementById("inspector").classList.contains("hidden"),
      detailTaskKey: S.detailTaskKey,
    })`));
    check("点任务行主栏直开任务详情", opened.settingsMode === false && opened.detailShown &&
      opened.listHidden && opened.pageTitle === "运行详情", JSON.stringify(opened));
    check("点开的是任务级详情（标题带任务名，检查器让位）",
      opened.rdTitle.includes("五分钟前") && opened.inspectorHidden && !!opened.detailTaskKey,
      JSON.stringify(opened));

    /* ---- B2) 行高亮：点击已置 detailTaskKey，重绘后仍在 ---- */
    await evalJs(`(() => { S.sideSig = null; renderSideTasks(); return "ok"; })()`);
    await sleep(400);
    const active = JSON.parse(await evalJs(`JSON.stringify({
      has: !!document.querySelector("#side-tasks .stask.active"),
      title: (document.querySelector("#side-tasks .stask.active .t") || {}).textContent || ""
    })`));
    check("当前查看的任务在树上高亮", active.has && /五分钟前/.test(active.title), JSON.stringify(active));

    /* ---- C) 文件夹展开状态跨重绘保留（2s 轮询不吞用户操作） ---- */
    await evalJs(`(() => {
      const dirs = [...document.querySelectorAll("#side-tasks details.sdir")];
      dirs[1].open = true;    // 人为展开第二个文件夹
      S.sideSig = null;       // 强制下一轮重绘
      renderSideTasks();
      return "ok";
    })()`);
    await sleep(400);
    const kept = JSON.parse(await evalJs(`(() => {
      const dirs = [...document.querySelectorAll("#side-tasks details.sdir")];
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      return JSON.stringify({ second: dirs[1].hasAttribute("open"),
        rows: rows.length, active: !!document.querySelector("#side-tasks .stask.active") });
    })()`));
    check("重绘后文件夹展开状态保留", kept.second && kept.rows === 4 && kept.active, JSON.stringify(kept));

    /* ---- D) 合成状态：五种字形与动画、超长标题省略 ---- */
    await evalJs(`(() => {
      const iso = (off) => { const d = new Date(Date.now() - off * 1000); const p = (x) => String(x).padStart(2, "0");
        return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
          p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds()); };
      const st = (id, title, status, ago, steps) => ({ id, task_id: "t-" + id, title, status,
        created_at: iso(ago), steps: steps || [] });
      const sp = (status) => ({ n: 1, agent: "claude", agent_label: "Claude Code", note: "断点续跑",
        status, started_at: "12:00:00", summary: "合成步骤：" + status });
      S.state = { runs: [
        st("x1", "合成-进行中（这是一个非常非常长的标题用来验证单行省略号是否生效不溢出侧栏）", "running", 30,
          [sp("done"), sp("running")]),
        st("x2", "合成-排队", "queued", 300),
        st("x3", "合成-已取消", "cancelled", 30 * 3600, [sp("cancelled")]),
        st("x4", "合成-失败", "failed", 72 * 3600, [sp("failed")]),
        st("x5", "合成-完成", "done", 5 * 86400, [sp("done")]),
      ]};
      S.detailTaskKey = null;
      S.detailRunId = null;
      S.sideSig = null;
      renderSideTasks();
      // 合成运行全是无主 → 归进「其他」文件夹；首轮展开启发式已跑过（DOM 里有 sdir），
      // 「其他」默认折叠会让隐藏元素算不出动画/省略，这里强制展开再断言
      document.querySelectorAll("#side-tasks details.sdir").forEach((d) => { d.open = true; });
      return "ok";
    })()`);
    await sleep(400);
    const syn = JSON.parse(await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      const by = (id) => rows.find((d) => d.dataset.key === "t-" + id);
      const glyphOf = (row) => { const g = row.querySelector(".sglyph");
        return g ? [...g.classList].filter((c) => c !== "sglyph").join(" ") : ""; };
      const anim = (el) => getComputedStyle(el).animationName;
      const t0 = by("x1").querySelector(".t");
      return JSON.stringify({
        n: rows.length,
        run: glyphOf(by("x1")), queue: glyphOf(by("x2")), cancel: glyphOf(by("x3")),
        fail: glyphOf(by("x4")), done: glyphOf(by("x5")),
        spinAnim: anim(by("x1").querySelector(".sglyph")),
        titleEllipsized: t0.scrollWidth > t0.clientWidth,
        runTimeText: by("x1").querySelector(".tm").textContent.trim(),
        queueTimeText: by("x2").querySelector(".tm").textContent.trim(),
        overflow: (() => { const t = document.getElementById("side-tasks");
          return t.scrollWidth - t.clientWidth; })(),
      });
    })()`));
    check("五种状态各用对字形（✻转圈/空圈/橙点/红点/绿点）",
      syn.run === "ast" && syn.queue === "ring" && syn.cancel === "warn" &&
      syn.fail === "bad" && syn.done === "ok", syn);
    check("进行中✻用的是旋转动画", syn.spinAnim === "ico-spin", syn.spinAnim);
    check("超长标题单行省略", syn.titleEllipsized, syn);
    check("紧凑时间分档正确（刚刚 / 5 分）",
      syn.runTimeText === "刚刚" && syn.queueTimeText === "5 分",
      JSON.stringify({ runTimeText: syn.runTimeText, queueTimeText: syn.queueTimeText }));
    check("合成超长标题下任务树仍无横向溢出", syn.overflow <= 0, "overflow=" + syn.overflow);

    const shot = await send("Page.captureScreenshot", { format: "png" });
    if (shot?.result?.data) {
      writeFileSync(join(ROOT, ".ui-shots", "r3-side-tree.png"), Buffer.from(shot.result.data, "base64"));
    }
    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((x) => !x).length;
  console.log("\n===== 侧栏任务树核验：%d 通过 / %d 失败 =====", results.length - bad, bad);
  if (bad) process.exit(1);
}

main().catch((e) => {
  const bad = results.filter((x) => !x).length;
  console.error("FATAL", e);
  console.log("\n===== 侧栏任务树核验（中断）：%d 通过 / %d 失败 =====", results.length - bad, bad);
  process.exit(1);
});
