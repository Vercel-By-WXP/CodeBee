/* 失败态验证：把 /api/usage 拦成 404（模拟旧进程/接口缺失），
 * 确认用量页四个区都有可见反馈，而不是标题下全空看着像页面坏了。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18792;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9342;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-fail-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"],
    { cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT } });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("服务启动", up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge CDP 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Fetch.requestPaused") {
        // 模拟旧进程：/api/usage 一律 404 unknown api
        send("Fetch.fulfillRequest", {
          requestId: m.params.requestId, responseCode: 404,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }],
          body: Buffer.from(JSON.stringify({ error: "unknown api" })).toString("base64"),
        });
      }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/usage*" }] });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await js(`switchTab("usage"); "ok"`);
    await sleep(2000);

    const zones = await js(`(() => {
      const ids = ["usage-kpis","usage-trend","usage-dims","usage-recent"];
      return ids.map(id => id + "=" + (document.getElementById(id).textContent.trim().length > 0 ? "有内容" : "空")).join(" | ");
    })()`);
    console.log("  区块状态:", zones);
    check("失败时四个区都有可见反馈（无空白区）", !/空/.test(zones), zones);
    const trendMsg = await js(`(document.getElementById("usage-trend").textContent||"")`);
    check("趋势区显示加载失败提示", /加载失败/.test(trendMsg), trendMsg.slice(0, 80));
    const recentMsg = await js(`(document.getElementById("usage-recent").textContent||"")`);
    check("最近调用区显示加载失败提示", /加载失败/.test(recentMsg), recentMsg.slice(0, 80));
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) if (p?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 失败态验证：%d 通过 / %d 失败 =====", results.length - bad.length, bad.length);
  if (bad.length) process.exit(1);
}
main().catch((e) => { console.error(e); process.exit(1); });
