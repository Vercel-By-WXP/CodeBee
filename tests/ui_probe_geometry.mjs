/* 布局几何验证：用真实坐标判断元素是否重叠/贴边/裁切，而不是靠 textContent 拼接猜。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.argv[2] || 8765);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9344;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-geo-"));
  let edge = null, ws = null;
  try {
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

    // 1) 占比条：名称与成功率文字是否重叠（真实坐标）
    const overlap = await js(`(() => {
      const bad = [];
      document.querySelectorAll("#usage-dims .bar-outer").forEach(box => {
        const s = box.querySelector("span"), i = box.querySelector("i");
        if (!s || !i) return;
        const a = s.getBoundingClientRect(), b = i.getBoundingClientRect();
        if (a.right > b.left + 1) bad.push(s.textContent + " ⇄ " + i.textContent +
          " (name右" + Math.round(a.right) + " > pct左" + Math.round(b.left) + ")");
      });
      return bad.length ? bad.slice(0,3).join(" ｜ ") : "";
    })()`);
    console.log("占比条名称/成功率重叠:", overlap === "" ? "无" : overlap);

    // 2) 名称是否被裁（scrollWidth > clientWidth）
    const clipped = await js(`(() => {
      const bad = [...document.querySelectorAll("#usage-dims .bar-outer span")]
        .filter(s => s.scrollWidth > s.clientWidth + 1)
        .map(s => s.textContent + "(" + s.scrollWidth + ">" + s.clientWidth + ")");
      return bad.length ? bad.slice(0,3).join(" ｜ ") : "";
    })()`);
    console.log("名称被裁:", clipped === "" ? "无" : clipped);

    // 3) 趋势图最右日期标签是否在 viewBox 内
    const labelOut = await js(`(() => {
      const svg = document.querySelector("#usage-trend svg");
      if (!svg) return "无 svg";
      const vb = svg.viewBox.baseVal;
      const bad = [];
      svg.querySelectorAll("text.uc-x").forEach(t => {
        const bb = t.getBBox();
        if (bb.x < vb.x - 1 || bb.x + bb.width > vb.x + vb.width + 1)
          bad.push(t.textContent + "(x=" + Math.round(bb.x) + ",右=" + Math.round(bb.x+bb.width) + ",viewBox宽=" + vb.width + ")");
      });
      return bad.length ? bad.join(" ｜ ") : "";
    })()`);
    console.log("日期标签越界:", labelOut === "" ? "无" : labelOut);

    // 4) 相邻日期标签是否互相重叠
    const labelOverlap = await js(`(() => {
      const svg = document.querySelector("#usage-trend svg");
      if (!svg) return "无 svg";
      const ts = [...svg.querySelectorAll("text.uc-x")].filter(t => /^\\d{2}-\\d{2}$/.test(t.textContent));
      const boxes = ts.map(t => ({ t: t.textContent, b: t.getBBox() }));
      const bad = [];
      for (let i = 1; i < boxes.length; i++) {
        if (boxes[i-1].b.x + boxes[i-1].b.width > boxes[i].b.x + 1)
          bad.push(boxes[i-1].t + " ⇄ " + boxes[i].t);
      }
      return bad.length ? bad.join(" ｜ ") : "";
    })()`);
    console.log("相邻日期标签重叠:", labelOverlap === "" ? "无" : labelOverlap);

    // 5) KPI 卡片是否溢出容器
    const kpiOver = await js(`(() => {
      const box = document.getElementById("usage-kpis");
      const bad = [...box.children].filter(c => c.scrollWidth > c.clientWidth + 2)
        .map(c => c.textContent.slice(0,26));
      return bad.length ? bad.join(" ｜ ") : "";
    })()`);
    console.log("KPI 文字溢出:", kpiOver === "" ? "无" : kpiOver);

    // 6) 用量页整体是否横向溢出（桌面 + 手机）
    const deskOver = await js(`(() => { const d = document.documentElement;
      return d.scrollWidth > d.clientWidth + 2 ? (d.scrollWidth + ">" + d.clientWidth) : ""; })()`);
    console.log("桌面横向溢出:", deskOver === "" ? "无" : deskOver);
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await sleep(900);
    const mobOver = await js(`(() => { const d = document.documentElement;
      return d.scrollWidth > d.clientWidth + 2 ? (d.scrollWidth + ">" + d.clientWidth) : ""; })()`);
    console.log("手机横向溢出:", mobOver === "" ? "无" : mobOver);
    const mobKpi = await js(`(() => {
      const box = document.getElementById("usage-kpis");
      const bad = [...box.children].filter(c => c.scrollWidth > c.clientWidth + 2)
        .map(c => c.textContent.slice(0,26));
      return bad.length ? bad.join(" ｜ ") : "";
    })()`);
    console.log("手机 KPI 溢出:", mobKpi === "" ? "无" : mobKpi);
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
