/* Logo 重设计 + 去除内置演示区块的渲染核验：Edge headless + CDP。
 * 1) #i-baton symbol 存在且几何非空；品牌磁贴与扫码门 use 指向 #i-baton；
 *    磁贴截图区域有墨迹（白图形 vs 渐变底方差 > 0）。
 * 2) 智能体管理页不再有「内置演示」标题与 ag-mocks 容器。
 * 用法：node tests/ui_logo_verify.mjs （脚本自己起临时服务，端口 18811） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18811;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9341;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-logo-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
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
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- 1) 精灵表与引用 ---- */
    const sprite = await evalJs(`(() => {
      const s = document.querySelector('#icon-sprite symbol#i-baton');
      if (!s) return JSON.stringify({ exists: false });
      const bb = s.getBBox();
      return JSON.stringify({ exists: true, w: +bb.width.toFixed(1), h: +bb.height.toFixed(1), kids: s.children.length });
    })()`);
    const sp = JSON.parse(sprite);
    check("#i-baton symbol 存在且几何非空", sp.exists && sp.w > 10 && sp.h > 10 && sp.kids >= 4, sprite);
    const oldNote = await evalJs(`!!document.querySelector('#icon-sprite symbol#i-note')`);
    check("旧 #i-note 已移除", !oldNote);
    const uses = await evalJs(`(() => {
      const brand = document.querySelector('.brand .logo-tile use');
      const gate = document.querySelector('.gate-brand .logo-tile use');
      return JSON.stringify({ brand: brand && brand.getAttribute('href'), gate: gate && gate.getAttribute('href') });
    })()`);
    const u = JSON.parse(uses);
    check("品牌磁贴 use → #i-baton", u.brand === "#i-baton", uses);
    check("扫码门 use → #i-baton", u.gate === "#i-baton", uses);

    /* ---- 2) 磁贴像素：渐变底上确有白色图形墨迹 ---- */
    const ink = await evalJs(`(() => {
      const r = document.querySelector('.brand .logo-tile').getBoundingClientRect();
      return JSON.stringify({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) });
    })()`);
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const png = Buffer.from(shot.result.data, "base64");
    const inkStat = await evalJs(`(async () => {
      const b = ${ink};
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = "data:image/png;base64,${png.toString("base64")}"; });
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const g = c.getContext('2d'); g.drawImage(img, 0, 0);
      const d = g.getImageData(b.x, b.y, b.w, b.h).data;
      let white = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) { n++; if (d[i] > 235 && d[i+1] > 235 && d[i+2] > 235) white++; }
      return JSON.stringify({ white, n, ratio: +(white / n).toFixed(3) });
    })()`);
    const st = JSON.parse(inkStat);
    // 指挥棒图形约占磁贴 15-35% 亮像素；低于 5% 说明空白，高于 60% 说明整块白底
    check("磁贴内有白色指挥棒图形（亮像素占比合理）", st.ratio > 0.05 && st.ratio < 0.6, inkStat);

    /* ---- 3) 智能体管理页：内置演示区块已去除 ---- */
    const agents = await evalJs(`(async () => {
      switchTab('agents');
      await new Promise(r => setTimeout(r, 1200));
      const page = document.querySelector('#sub-agents') || document.body;
      const titles = [...page.querySelectorAll('.sec-title')].map(h => h.textContent.trim());
      return JSON.stringify({ titles, mocks: !!document.getElementById('ag-mocks') });
    })()`);
    const ag = JSON.parse(agents);
    check("「内置演示」标题不再出现", !ag.titles.includes("内置演示"), agents);
    check("ag-mocks 容器已移除", !ag.mocks, agents);
    check("已安装/可安装分组仍在", ag.titles.includes("已安装") && ag.titles.includes("可安装"), agents);

    const fails = results.filter((r) => !r.ok);
    console.log(fails.length ? "\n✗ " + fails.length + " 项未过" : "\n全部通过");
    process.exitCode = fails.length ? 1 : 0;
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(2); });
