/* 新建任务页 Composer 底部被裁复现探针：矮窗口 + 几何全量 dump + 截图。 */
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18897;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9373;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WIN_H = parseInt(process.argv[2] || "367", 10);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-cmpclip-"));
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    if (!up) throw new Error("service not up");
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      `--window-size=1043,${WIN_H}`, "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    if (!target) throw new Error("edge not up");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: 1043, height: WIN_H, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await evalJs(`if (typeof welcomeClose === "function") welcomeClose(); else { const w = document.getElementById("welcome"); if (w) w.classList.add("hidden"); } true`);
    const rect = async (sel) => JSON.parse(await evalJs(
      `JSON.stringify((() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return null; const r = el.getBoundingClientRect(); return {top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height)}; })())`));
    const out = {
      winH: WIN_H,
      innerH: await evalJs("innerHeight"),
      main: await rect("main"),
      page: await rect("#page-settings"),
      sub: await rect("#sub-tasks"),
      composer: await rect("#sub-tasks .composer"),
      form: await rect("#sub-tasks .form"),
      box: await rect("#sub-tasks .cmp-box"),
      hero: await rect("#sub-tasks .hero-row"),
      pills: await rect("#sub-tasks .cmp-pills"),
    };
    console.log(JSON.stringify(out));
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const data = shot && shot.result && shot.result.data;
    if (!data) throw new Error("no screenshot: " + JSON.stringify(shot).slice(0, 200));
    writeFileSync("E:/GoOut/composer_shot.png", Buffer.from(data, "base64"));
    console.log("SHOT E:/GoOut/composer_shot.png");
  } finally {
    try { ws?.close(); } catch {}
    try { edge?.kill(); } catch {}
    try { svc?.kill(); } catch {}
  }
}
main().then(() => process.exit(0)).catch((e) => { console.error("PROBE_FAIL", e.message); process.exit(1); });
