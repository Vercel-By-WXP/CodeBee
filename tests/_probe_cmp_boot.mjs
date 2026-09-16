/* 一次性探针：起临时服务 → Edge headless 打开首页 → 抓 console/异常 + 查 composer 元素 */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";


const SERVICE = "http://127.0.0.1:18798";
const CDP_PORT = 9345
const EDGE = ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-probe-"));
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_ctx_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", "18798",
    "--no-browser", "--host", "127.0.0.1"],
    { env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) { await sleep(500); try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {} }
  console.log("service up:", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-probe-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
  let wsUrl = null;
  for (let i = 0; i < 30 && !wsUrl; i++) {
    await sleep(400);
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json`)).json();
      wsUrl = list.find((t) => t.type === "page")?.webSocketDebuggerUrl;
    } catch (e) {}
  }
  const ws = new WebSocket(wsUrl); await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let seq = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Runtime.exceptionThrown") console.log("PAGE-EXC:", (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text || "").slice(0, 400));
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") console.log("CONSOLE-ERR:", m.params.args.map(a => a.value || a.description || "").join(" ").slice(0, 300));
  };
  const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return "EXC:" + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text).slice(0, 300);
    return r.result?.result?.value;
  };
  await send("Runtime.enable"); await send("Page.enable");
  await send("Page.navigate", { url: SERVICE + "/" });
  await sleep(4000);
  console.log("f-type:", await evalJs(`!!document.getElementById("f-type")`));
  console.log("f-type options:", await evalJs(`(document.getElementById("f-type")||{options:[]}).options.length`));
  console.log("cmp-greet:", await evalJs(`(document.getElementById("cmp-greet")||{}).textContent`));
  console.log("cmp-quick:", await evalJs(`(document.getElementById("cmp-quick")||{}).childElementCount`));
  console.log("cmp-wd:", await evalJs(`!!document.querySelector(".cmp-wd")`));
  console.log("btn-create:", await evalJs(`!!document.getElementById("btn-create")`));
  console.log("f-goal:", await evalJs(`!!document.getElementById("f-goal")`));
  console.log("f-advanced:", await evalJs(`!!document.getElementById("f-advanced")`));
  console.log("flows len:", await evalJs(`(typeof S !== "undefined" && S.flows || []).length`));
  ws.close(); proc.kill(); try { execSync(`taskkill /F /PID ${svc.pid} /T`, { stdio: "pipe" }); } catch (e) {}
  process.exit(0);
}
main().catch((e) => { console.error("FAIL:", e); process.exit(1); });
