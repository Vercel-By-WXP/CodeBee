/* 探针 C：SSE 假活（推送被吞）下归档/取消归档，侧栏必须靠乐观挪位立即刷新。
 * 与删除探针同款故障态：S.es.onmessage/onerror 全吞 + sseLive 钉 true + 推送
 * 时钟保鲜 → 看门狗永不触发。归档=行从侧栏消失；取消归档=行回来。
 * 期望两步都 ≤1.5s 完成（乐观挪位+强制重画），S.state 两数组挪位正确。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18876, CDP_PORT = 9364;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TASK = "task-ctx";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pidsOn(port) {
  try {
    return execSync(`netstat -ano | findstr ":${port} " | findstr "LISTENING"`, { stdio: "pipe" })
      .toString().split("\n").map((l) => l.trim().split(/\s+/).pop()).filter(Boolean);
  } catch (e) { return []; }
}
function killByPids(pids) {
  for (const pid of pids) {
    try { execSync(`taskkill /F /T /PID ${pid}`, { stdio: "ignore" }); } catch (e) { /* gone */ }
  }
}

async function main() {
  for (const p of [PORT, CDP_PORT]) {
    const pids = pidsOn(p);
    if (pids.length) { console.log("✗ 端口 " + p + " 被占 PID=" + pids.join(",")); process.exit(2); }
  }
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-sca-"));
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_ctx_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  console.log((up ? "✓" : "✗") + " 临时服务启动 " + PORT);
  if (!up) process.exit(2);

  const profile = mkdtempSync(join(tmpdir(), "tutti-sca-edge-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync", "--disable-extensions",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });
  let exitCode = 0;
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    if (!target) throw new Error("Edge CDP 未就绪");
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
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
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    const base = JSON.parse(await evalJs(`JSON.stringify({
      row: !!document.querySelector('#side-tasks .stask[data-task="${TASK}"]'),
      sse: !!S.sseLive,
    })`));
    if (!base.row || !base.sse) throw new Error("基线失败 row=" + base.row + " sse=" + base.sse);
    console.log("✓ 基线：任务行在侧栏、SSE 活着");
    await evalJs(`(() => {
      if (S.es) { S.es.onmessage = () => {}; S.es.onerror = () => {}; }
      S.sseLive = true; S._lastSseAt = Date.now();
      return true;
    })()`);
    console.log("✓ 已模拟 SSE 假活");

    // 归档：行应立即消失（默认不显示已归档）
    await evalJs(`window.archiveTask("${TASK}", true); true`);
    let archGoneAt = -1, archMoved = false;
    for (let i = 0; i < 6 && archGoneAt < 0; i++) {
      await sleep(250);
      const s = JSON.parse(await evalJs(`JSON.stringify({
        row: !!document.querySelector('#side-tasks .stask[data-task="${TASK}"]'),
        inTasks: (S.state.tasks || []).some(t => t.id === "${TASK}"),
        inArch: (S.state.archived_tasks || []).some(t => t.id === "${TASK}"),
      })`));
      if (!s.row && !s.inTasks && s.inArch) { archGoneAt = (i + 1) * 250; archMoved = true; }
    }
    console.log((archGoneAt > 0 && archGoneAt <= 1500 ? "✓" : "✗") +
      " SSE 假活下归档即时生效：行消失于 " + archGoneAt + "ms，两数组挪位=" + archMoved);

    // 取消归档：行应立即回来
    await evalJs(`window.archiveTask("${TASK}", false); true`);
    let backAt = -1, backMoved = false;
    for (let i = 0; i < 6 && backAt < 0; i++) {
      await sleep(250);
      const s = JSON.parse(await evalJs(`JSON.stringify({
        row: !!document.querySelector('#side-tasks .stask[data-task="${TASK}"]'),
        inTasks: (S.state.tasks || []).some(t => t.id === "${TASK}"),
        inArch: (S.state.archived_tasks || []).some(t => t.id === "${TASK}"),
      })`));
      if (s.row && s.inTasks && !s.inArch) { backAt = (i + 1) * 250; backMoved = true; }
    }
    console.log((backAt > 0 && backAt <= 1500 ? "✓" : "✗") +
      " SSE 假活下取消归档即时生效：行回来于 " + backAt + "ms，挪回=" + backMoved);
    if (!(archGoneAt > 0 && archGoneAt <= 1500 && archMoved &&
          backAt > 0 && backAt <= 1500 && backMoved)) exitCode = 1;
  } catch (e) {
    console.log("✗ " + e.message);
    exitCode = 1;
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    killByPids(pidsOn(CDP_PORT));
    killByPids(pidsOn(PORT));
    await sleep(800);
    const l1 = pidsOn(PORT), l2 = pidsOn(CDP_PORT);
    console.log((l1.length === 0 && l2.length === 0 ? "✓" : "✗") +
      " 端口归还自证 (svc:" + l1.join(",") + " cdp:" + l2.join(",") + ")");
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  process.exit(exitCode);
}
main();
