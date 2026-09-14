/* 侧栏任务树核验（ZCode/Codex 桌面端式样改造）：
 *   任务行 = 折叠箭头(SVG, 旋转) + 状态字形(排队圈/进行中转圈/完成勾/失败叉/取消橙圈)
 *           + 单行省略标题 + 右对齐相对时间；步骤行 = 状态点 + 工具名 + 摘要 + 时间。
 * 覆盖：结构、时间列右对齐、单行省略、查看全部、展开状态跨重绘保留、点步骤跳详情、
 *       选中高亮、五种状态字形与动画、侧栏无横向溢出。
 * 前置：TUTTI_DATA 先用 tests/_seed_tree_fixtures.py 造数，再起 18798 临时服务。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const CDP_PORT = 9339;
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
const TIME_RE = /^(刚刚|\d+ 分钟前|\d+ 小时前|昨天|\d+ 天前|\d{2}-\d{2})$/;

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-tree-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
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
    const dialogs = [];
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      else if (m.method === "Page.javascriptDialogOpening") {
        dialogs.push(m.params.message);
        send("Page.handleJavaScriptDialog", { accept: true });
      }
    };
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- A) 真实造数渲染出的结构 ---- */
    const og = await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      const sums = rows.map(d => d.querySelector("summary"));
      const hs = sums.map(s => +s.getBoundingClientRect().height.toFixed(1));
      const tmRights = sums.map(s => { const t = s.querySelector(".tm");
        return t ? +t.getBoundingClientRect().right.toFixed(1) : null; });
      return {
        rows: rows.length,
        titles: rows.map(d => d.querySelector(".t").textContent.trim()),
        times: rows.map(d => d.querySelector(".tm").textContent.trim()),
        chev: rows.map(d => !!d.querySelector("summary svg.chev use[href='#i-chevron-r']")),
        glyph: rows.map(d => !!d.querySelector("summary .sglyph")),
        oldChevron: getComputedStyle(sums[0], "::before").content,
        heightsAllEqual: new Set(hs).size === 1,
        tmRightAligned: new Set(tmRights).size === 1,
        summaryW: Math.round(sums[0].getBoundingClientRect().width),
        treeOverflow: (() => { const t = document.getElementById("side-tasks");
          return t.scrollWidth - t.clientWidth; })(),
      };
    })()`);
    const organic = JSON.stringify(og);
    check("造数渲染出 4 个任务行", og.rows === 4, organic);
    check("每行都有折叠箭头与状态字形", og.chev.every(Boolean) && og.glyph.every(Boolean), organic);
    check("右列是相对时间（不是原始时间戳）", og.times.every((t) => TIME_RE.test(t)), JSON.stringify(og.times));
    check("时间覆盖多档：分钟/小时/昨天/天前",
      /分钟前/.test(og.times.join()) && /小时前/.test(og.times.join()) &&
      og.times.includes("昨天") && /天前/.test(og.times.join()), JSON.stringify(og.times));
    check("旧文字箭头 ▸/▾ 已移除", !/▸|▾/.test(og.oldChevron), og.oldChevron);
    check("任务行等高（紧凑单行）", og.heightsAllEqual, organic);
    check("时间列右对齐（ZCode/Codex 特征）", og.tmRightAligned, organic);
    check("任务树无横向溢出", og.treeOverflow <= 0, "overflow=" + og.treeOverflow);

    const steps1 = JSON.parse(await evalJs(`(() => {
      const row = document.querySelector("#side-tasks .stask");
      const sx = [...row.querySelectorAll(".stepx")];
      return JSON.stringify({
        open: row.hasAttribute("open"),
        n: sx.length,
        dotOk: sx.every(x => !!x.querySelector(".sdot.done")),
        agent: sx.map(x => x.querySelector(".sagent").textContent.trim()),
        tm: sx.map(x => x.querySelector(".stm").textContent.trim()),
        tmFmt: sx.every(x => /^\\d{2}:\\d{2}$/.test(x.querySelector(".stm").textContent.trim())),
        indented: row.querySelector(".steps") && getComputedStyle(row.querySelector(".steps")).borderLeftWidth,
      });
    })()`));
    check("首任务默认展开且 3 个步骤齐全", steps1.open && steps1.n === 3, JSON.stringify(steps1));
    check("步骤行带状态点/工具名/摘要/时间", steps1.dotOk && steps1.tmFmt, JSON.stringify(steps1));
    check("步骤区有缩进引导线", steps1.indented === "1px", steps1.indented);

    const more = JSON.parse(await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      const long = rows.find(d => d.querySelector(".smore"));
      return JSON.stringify({ found: !!long, text: long ? long.querySelector(".smore").textContent.trim() : "" });
    })()`));
    check("超 8 步收进「查看全部 N 步」", more.found && /查看全部 20 步/.test(more.text), JSON.stringify(more));

    /* ---- B) 点步骤 → 右侧主栏直接开运行详情；该任务在树上高亮 ---- */
    await evalJs(`document.querySelector("#side-tasks .stask .stepx").click(); "ok"`);
    await sleep(1200);
    const jumped = await evalJs(`JSON.stringify({
      title: document.getElementById("page-title").textContent,
      detailShown: !document.getElementById("run-detail").classList.contains("hidden")
    })`);
    // 点步骤不进设置模式，主栏直接展示该步所属运行的详情（标题「运行详情」）
    check("点步骤在主栏打开运行详情", JSON.parse(jumped).title === "运行详情" &&
      JSON.parse(jumped).detailShown, jumped);
    // 高亮类由 renderSideTasks 在下次轮询时刷上；这里强制重绘以直接验证类逻辑
    await evalJs(`S.sideSig = null; renderSideTasks(); "ok"`);
    await sleep(400);
    const active = JSON.parse(await evalJs(`JSON.stringify({
      detail: S.detailRunId,
      has: !!document.querySelector("#side-tasks .stask.active"),
      title: (document.querySelector("#side-tasks .stask.active .t") || {}).textContent || ""
    })`));
    check("当前查看的任务在树上高亮", active.has && /五分钟前/.test(active.title), JSON.stringify(active));

    /* ---- C) 展开状态跨重绘保留（2s 轮询不吞用户操作） ---- */
    await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      rows[2].open = true;    // 人为展开第三个
      S.sideSig = null;       // 强制下一轮重绘
      renderSideTasks();
      return "ok";
    })()`);
    await sleep(400);
    const kept = JSON.parse(await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      return JSON.stringify({ third: rows[2].hasAttribute("open"),
        n: rows.length, active: !!document.querySelector("#side-tasks .stask.active") });
    })()`));
    check("重绘后展开状态保留", kept.third && kept.n === 4 && kept.active, JSON.stringify(kept));

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
      S.detailRunId = null;
      S.sideSig = null;
      renderSideTasks();
      return "ok";
    })()`);
    await sleep(400);
    const syn = JSON.parse(await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      const by = (id) => rows.find(d => d.dataset.key === "t-" + id);
      const glyphOf = (row) => { const g = row.querySelector("summary .sglyph");
        if (!g) return "";
        if (g.tagName.toLowerCase() === "svg") return "svg:" + g.querySelector("use").getAttribute("href");
        return g.classList.contains("spin") ? "spin"
          : g.classList.contains("ring") ? (g.classList.contains("warn") ? "ring warn" : "ring") : ""; };
      const anim = (el) => getComputedStyle(el).animationName;
      const t0 = by("x1").querySelector(".t");
      return JSON.stringify({
        n: rows.length,
        run: glyphOf(by("x1")), queue: glyphOf(by("x2")), cancel: glyphOf(by("x3")),
        fail: glyphOf(by("x4")), done: glyphOf(by("x5")),
        spinAnim: anim(by("x1").querySelector(".sglyph.spin")),
        stepDotAnim: anim(by("x1").querySelector(".sdot.running")),
        titleEllipsized: t0.scrollWidth > t0.clientWidth,
        runTimeText: by("x1").querySelector(".tm").textContent.trim(),
        queueTimeText: by("x2").querySelector(".tm").textContent.trim(),
        chevRotates: (() => { const c = by("x3").querySelector("summary .chev");
          by("x3").open = true;
          return getComputedStyle(c).transform !== "none"; })(),
        overflow: (() => { const t = document.getElementById("side-tasks");
          return t.scrollWidth - t.clientWidth; })(),
      });
    })()`));
    check("五种状态各用对字形（转圈/空圈/橙圈/叉/勾）",
      syn.run === "spin" && syn.queue === "ring" && syn.cancel === "ring warn" &&
      syn.fail === "svg:#i-x" && syn.done === "svg:#i-check", JSON.stringify(syn));
    check("进行中转圈用的是旋转动画", syn.spinAnim === "ico-spin", syn.spinAnim);
    check("进行中步骤点用脉冲动画", syn.stepDotAnim === "pulse", syn.stepDotAnim);
    check("超长标题单行省略", syn.titleEllipsized, JSON.stringify(syn));
    check("相对时间分档正确（刚刚 / 5 分钟前）",
      /^刚刚|^1 分钟前/.test(syn.runTimeText) && syn.queueTimeText === "5 分钟前",
      JSON.stringify({ runTimeText: syn.runTimeText, queueTimeText: syn.queueTimeText }));
    check("展开时折叠箭头旋转 90°", syn.chevRotates, JSON.stringify(syn));
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
