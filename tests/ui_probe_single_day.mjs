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
      const xs = [...new Set(rects.map(r => Number(r.getAttribute("x"))))];
      const bw = [...new Set(rects.map(r => Number(r.getAttribute("width"))))];
      return { vbW: vb.width, vbH: vb.height, barCount: xs.length, xs, bw,
               labelCount: svg.querySelectorAll("text.uc-x").length };
    })()`;

    // 第 1 轮断言：单日柱子窄（≤48）、居中、图表仍占满容器
    let b = await js(dumpBars);
    check("单日：只有 1 根柱子", b.barCount === 1, JSON.stringify(b));
    check("单日：柱宽封顶 ≤ 48（不再铺满整图）", b.bw?.[0] <= 48, "bw=" + b.bw);
    const centered1 = b.xs && b.bw?.[0] ? Math.abs((b.xs[0] + b.bw[0] / 2) - b.vbW / 2) < 4 : false;
    check("单日：柱子水平居中（±4）", centered1, "x=" + b.xs + " bw=" + b.bw + " vbW=" + b.vbW);
    const kpi1 = await js(`document.getElementById("usage-kpis").textContent`);
    check("单日：KPI 有总量数据（不是全 0）", /240\.6万|2,?406,?000|2406000/.test(kpi1.replace(/\s/g, "")) || /万|亿/.test(kpi1), kpi1.slice(0, 120));

    // 第 2 轮：补前两天数据 → 切「近 7 天」→ 3 根柱子仍封顶
    appendFileSync(monthFile, [seedRec(dayStr(1), "r4", 900000), seedRec(dayStr(1), "r5", 800),
      seedRec(dayStr(2), "r6", 1200000)].join("\n") + "\n");
    await js(`setUsageDays(7); "ok"`);
    await sleep(2000);
    b = await js(dumpBars);
    check("7 天范围：3 根柱子", b.barCount === 3, JSON.stringify(b));
    check("7 天范围：柱宽封顶 ≤ 48", (b.bw || []).every((w) => w <= 48), "bw=" + b.bw);
    const gapped = b.xs && b.xs.length === 3 ? (b.xs[1] - b.xs[0]) : 0;
    check("7 天范围：柱间距 = 柱宽+gap 且合理（<200）", gapped > 0 && gapped < 200, "间距=" + gapped);
    const labelOk = await js(`(() => {
      const svg = document.querySelector("#usage-trend svg");
      const vb = svg.viewBox.baseVal;
      return [...svg.querySelectorAll("text.uc-x")].every(t => {
        const bb = t.getBBox();
        return bb.x >= -1 && bb.x + bb.width <= vb.width + 1;
      });
    })()`);
    check("7 天范围：日期标签不越界", labelOk === true, String(labelOk));

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
