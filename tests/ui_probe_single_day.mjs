/* 单日柱状图形状验证：只种今天的台账 → 默认「今天」范围下柱子不得铺满整图。
 * 同时验证切到多日范围后柱宽封顶、柱组居中。纯读操作，不抢设备控制权。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18795;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9347;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}

function dayStr(offsetDays) {
  const d = new Date(Date.now() - offsetDays * 86400000);
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

function seedRec(day, runId, total) {
  const rec = {
    ts: day + " 10:0" + (Number(runId.slice(1)) % 10) + ":00", day, run_id: runId, step: 1,
    task_id: "t-" + runId, task_type: "code", role: "implement",
    agent: "codex", agent_label: "Codex", tool: "codex", model: "gpt-x", provider: "p1",
    ok: true, duration_s: 12.5,
    input: Math.floor(total * 0.7), output: Math.floor(total * 0.2),
    cached: 0, reasoning: total - Math.floor(total * 0.7) - Math.floor(total * 0.2),
    total, cost_usd: 0.0123, source: "seed",
  };
  return JSON.stringify(rec);
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-1day-"));
  const dataDir = join(tmp, "data");
  const usageDir = join(dataDir, "usage");
  mkdirSync(usageDir, { recursive: true });
  const monthFile = join(usageDir, "usage-" + dayStr(0).slice(0, 7).replace("-", "") + ".jsonl");

  // 第 1 轮：仅今天 3 条记录
  writeFileSync(monthFile, [seedRec(dayStr(0), "r1", 1600), seedRec(dayStr(0), "r2", 2400000),
    seedRec(dayStr(0), "r3", 3000)].join("\n") + "\n");

  let edge = null, ws = null, svc = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动（端口 " + PORT + "）", up);

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await js(`switchTab("usage"); "ok"`);
    await sleep(2500);

    // 默认范围 = 今天
    const active = await js(`document.querySelector(".seg-btn.active[data-days]")?.dataset.days`);
    check("默认选中「今天」(data-days=1)", active === "1", "active=" + active);

    const dumpBars = `(() => {
      const svg = document.querySelector("#usage-trend svg");
      if (!svg) return { err: "无 svg" };
      const vb = svg.viewBox.baseVal;
      const rects = [...svg.querySelectorAll("rect")].filter(r => !r.closest(".uc-legend"));
      const main = rects.filter(r => r.getAttribute("fill") === "var(--accent)");   // 每天 1 根输入主柱
      const stubs = rects.filter(r => r.getAttribute("fill") === "var(--border-strong)");
      const xs = [...new Set(main.map(r => Math.round(Number(r.getAttribute("x")))))].sort((a, b) => a - b);
      const bw = [...new Set(main.map(r => Number(r.getAttribute("width"))))];
      return { vbW: vb.width, barDays: xs.length, xs, bw, stubCount: stubs.length,
               hasBase: !!svg.querySelector(".uc-base"),
               hasGrid: !!svg.querySelector(".uc-grid"),
               peak: (svg.querySelector(".uc-max") || {}).textContent || "",
               labels: [...svg.querySelectorAll("text.uc-x")].map(t => Math.round(Number(t.getAttribute("x")))),
               labelCount: svg.querySelectorAll("text.uc-x").length };
    })()`;

    // 第 1 轮断言：单日改画构成条卡片（不是 SVG 孤柱）
    const single = await js(`(() => {
      const card = document.querySelector("#usage-trend .usage-single");
      if (!card) return { err: "无 .usage-single 卡片", html: document.getElementById("usage-trend").innerHTML.slice(0, 120) };
      const bar = card.querySelector(".us-bar:not(.us-empty)");
      if (!bar) return { err: "无构成条", html: card.innerHTML.slice(0, 200) };
      const segs = [...bar.querySelectorAll(".us-seg")].map(s => ({
        cls: s.className.replace("us-seg ", ""),
        w: Number(s.style.width.replace("%", "")),
      }));
      const box = bar.getBoundingClientRect();
      const head = card.querySelector(".us-head")?.textContent || "";
      const foot = card.querySelector(".us-foot")?.textContent || "";
      return { segs, barW: Math.round(box.width), barH: Math.round(box.height),
               head: head.replace(/\s+/g, " ").trim(), foot: foot.replace(/\s+/g, " ").trim() };
    })()`);
    check("单日：渲染构成条卡片（无 svg 孤柱）", !single.err, JSON.stringify(single).slice(0, 200));
    const segW = (cls) => (single.segs || []).find(s => s.cls === "us-" + cls)?.w;
    check("单日：输入段 ≈70%", Math.abs((segW("in") || 0) - 70) < 1.5, "in=" + segW("in"));
    check("单日：缓存段 ≈10%", Math.abs((segW("ca") || 0) - 10) < 1.5, "ca=" + segW("ca"));
    check("单日：输出段 ≈20%", Math.abs((segW("out") || 0) - 20) < 1.5, "out=" + segW("out"));
    check("单日：三段宽度合计铺满全宽（≈100%）",
      Math.abs((single.segs || []).reduce((a, s) => a + s.w, 0) - 100) < 0.5,
      JSON.stringify(single.segs));
    check("单日：构成条有实际高度（≥30px）", (single.barH || 0) >= 30, "barH=" + single.barH);
    check("单日：信息头含日期与总量", /09-14/.test(single.head || "") && /调用 3 次/.test(single.head || ""), single.head);
    check("单日：明细行含三段数值", /输入/.test(single.foot || "") && /输出/.test(single.foot || ""), single.foot);
    const kpi1 = await js(`document.getElementById("usage-kpis").textContent`);
    check("单日：KPI 有总量数据（不是全 0）", /240\.6万|2,?406,?000|2406000/.test(kpi1.replace(/\s/g, "")) || /万|亿/.test(kpi1), kpi1.slice(0, 120));

    // 手机窄屏：构成条卡片不得撑出横向滚动
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await sleep(900);
    const mobOver = await js(`(() => { const d = document.documentElement;
      return d.scrollWidth > d.clientWidth + 2 ? (d.scrollWidth + ">" + d.clientWidth) : ""; })()`);
    check("单日：手机窄屏无横向溢出", mobOver === "", mobOver);
    await send("Emulation.clearDeviceMetricsOverride");
    await sleep(600);

    // 第 2 轮：补前两天数据 → 切「近 7 天」：3 天有数据的柱子 + 4 个零日占位
    appendFileSync(monthFile, [seedRec(dayStr(1), "r4", 900000), seedRec(dayStr(1), "r5", 800),
      seedRec(dayStr(2), "r6", 1200000)].join("\n") + "\n");
    await js(`setUsageDays(7); "ok"`);
    await sleep(2000);
    const b = await js(dumpBars);
    check("7 天范围：3 天有柱子", b.barDays === 3, JSON.stringify(b));
    check("7 天范围：4 个零日基线占位", b.stubCount === 4, "stubs=" + b.stubCount);
    check("7 天范围：柱宽封顶 ≤ 48", (b.bw || []).every((w) => w <= 48), "bw=" + b.bw);
    check("7 天范围：有基线和参考虚线", b.hasBase === true && b.hasGrid === true,
      JSON.stringify({ base: b.hasBase, grid: b.hasGrid }));
    check("7 天范围：有峰值刻度", /^峰值 /.test(b.peak || ""), b.peak);
    // 槽位分布：柱子与占位沿全宽均匀分布
    const spread = b.xs && b.xs.length === 3 ? b.xs[b.xs.length - 1] - b.xs[0] : 0;
    check("7 天范围：柱子沿全宽均匀分布（跨度 > 150）", spread > 150, "跨度=" + spread + " xs=" + b.xs);
    // 标签与柱子同槽位居中：labels 前 7 个是日期标签，末 3 个是图例（同样挂 uc-x 类）
    const dateLabels = (b.labels || []).slice(0, 7);
    const slotOk = dateLabels.length === 7 && dateLabels.every((lx, i) =>
      Math.abs(lx - (6 + i * (708 / 7) + (708 / 7) / 2)) < 4);
    check("7 天范围：标签与柱子槽位对齐（7 个日期标签）", slotOk, "labels=" + b.labels);
    const labelOk = await js(`(() => {
      const svg = document.querySelector("#usage-trend svg");
      const vb = svg.viewBox.baseVal;
      return [...svg.querySelectorAll("text.uc-x")].every(t => {
        const bb = t.getBBox();
        return bb.x >= -1 && bb.x + bb.width <= vb.width + 1;
      });
    })()`);
    check("7 天范围：日期标签不越界", labelOk === true, String(labelOk));

    // 表格对齐：数字列表头必须与该列内容同为右对齐，且右边缘重合
    const misalign = await js(`(() => {
      const out = [];
      document.querySelectorAll("table.usage-table").forEach(t => {
        const firstRow = t.querySelector("tbody tr");
        if (!firstRow) return;
        [...t.querySelectorAll("thead th")].forEach((th, ci) => {
          const td = firstRow.children[ci];
          if (!td) return;
          const a = getComputedStyle(th).textAlign, c = getComputedStyle(td).textAlign;
          if (c === "right" && a !== "right") out.push(th.textContent + "(th=" + a + ",td=" + c + ")");
          else if (c === "right" && Math.abs(th.getBoundingClientRect().right - td.getBoundingClientRect().right) > 3)
            out.push(th.textContent + "(右缘偏差)");
        });
      });
      return out;
    })()`);
    check("表格：数字列表头与内容右对齐且边缘重合", Array.isArray(misalign) && misalign.length === 0,
      JSON.stringify(misalign));

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    if (svc?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  console.log("\n===== 单日柱状图验证：%d 通过 / %d 失败 =====", PASS.length, FAIL.length);
  if (FAIL.length) { console.log("失败项：", FAIL); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
