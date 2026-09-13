/* 快速探针：继续会话下拉选项 + 用量页失败态表现 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18793;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9341;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-quick-"));
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
    console.log("服务启动:", up);
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
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map(); const errs = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.exceptionThrown") {
        errs.push(m.params?.exceptionDetails?.exception?.description || m.params?.exceptionDetails?.text);
      }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      return r.result?.result?.value;
    };
    await send("Runtime.enable"); await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    console.log("\n-- 继续会话下拉 --");
    console.log("选项:", await js(`[...document.getElementById("f-resume-agent").options]
      .map(o => o.value + "=" + o.textContent).join(" | ")`));
    console.log("S.sessionAgents:", await js(`[...(S.sessionAgents||[])].join(",")`));

    console.log("\n-- 用量页（空台账）--");
    await js(`switchTab("usage"); "ok"`);
    await sleep(1500);
    console.log("KPI:", await js(`document.getElementById("usage-kpis").textContent`));
    console.log("趋势区:", JSON.stringify(await js(`document.getElementById("usage-trend").innerHTML`)));
    console.log("维度区:", JSON.stringify((await js(`document.getElementById("usage-dims").textContent`)||"").slice(0,90)));
    console.log("最近区:", JSON.stringify(await js(`document.getElementById("usage-recent").innerHTML`)));

    console.log("\nJS 异常:", errs.length ? errs.join(" | ") : "无");
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) if (p?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
