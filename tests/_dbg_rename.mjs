/* 诊断：rename 后侧栏为何没重绘 —— 看 S.state/SSE/sig 在页面里的真实状态 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const PORT = 18798;
const CDP_PORT = 9343;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TASK = "task-ctx";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口被占用"); process.exit(2);
  } catch (e) {}
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-dbg-"));
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_ctx_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore",
  });
  for (let i = 0; i < 40; i++) { await sleep(500); try { if ((await fetch(SERVICE + "/api/state")).ok) break; } catch (e) {} }

  const profile = mkdtempSync(join(tmpdir(), "tutti-dbg-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--disable-sync", "--disable-extensions", `--user-data-dir=${profile}`,
    `--remote-debugging-port=${CDP_PORT}`, "about:blank"], { stdio: "ignore" });
  let target = null;
  for (let i = 0; i < 30 && !target; i++) {
    await sleep(500);
    try {
      const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
      target = list.find((t) => t.type === "page" && t.url === "about:blank");
    } catch (e) {}
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0; const pending = new Map();
  const send = (method, params = {}) => new Promise((res, rej) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
    pending.set(id, (m) => { clearTimeout(timer); res(m); });
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    else if (m.method === "Page.javascriptDialogOpening")
      send("Page.handleJavaScriptDialog", { accept: true });
  };
  const evalJs = async (x) => {
    const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
    const ex = r.result?.exceptionDetails;
    if (ex) throw new Error("页面抛错：" + (ex.exception?.description || ex.text || "").slice(0, 300));
    return r.result?.result?.value;
  };
  await send("Page.enable");
  await send("Page.navigate", { url: SERVICE + "/" });
  await sleep(3500);

  const dump = async (tag) => {
    const d = await evalJs(`JSON.stringify({
      sseLive: !!S.sseLive,
      esState: S.es ? S.es.readyState : "no-es",
      runTitles: (S.state.runs || []).map(r => [r.id.slice(-4), r.title]),
      taskTitles: (S.state.tasks || []).map(t => [t.id, t.title]),
      sideSigLen: (S.sideSig || "").length,
      domTitles: [...document.querySelectorAll("#side-tasks .stask")].map(d => d.querySelector(".t").textContent),
      conn: (document.getElementById("conn") || {}).textContent,
    })`);
    console.log(tag, d);
  };
  await dump("初始:");

  // 直接在页面里调 renameTask（绕开菜单，聚焦刷新链路）
  await evalJs(`(async () => { window.prompt = () => "改名诊断XYZ"; await renameTask("${TASK}"); return 1; })()`);
  await sleep(1000);
  await dump("rename+1s:");
  await sleep(4000);
  await dump("rename+5s:");
  const manual = await evalJs(`(async () => {
    if (typeof refreshState === "function") await refreshState();
    render();
    return [...document.querySelectorAll("#side-tasks .stask")].map(d => d.querySelector(".t").textContent);
  })()`);
  console.log("手动 refreshState+render:", manual);

  try { proc.kill(); } catch (e) {}
  try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) {}
  try { svc.kill(); } catch (e) {}
  try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) {}
  try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  process.exit(0);
}
main().catch((e) => { console.error("诊断异常：", e); process.exit(1); });
