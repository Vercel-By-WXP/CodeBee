/* 探针 B：SSE 假活（推送被吞）下删除任务，侧栏必须靠乐观清场立即刷新。
 * 用户真机故障态：SSE 连接看着活着但删除的 bump 推送没到达（半死/中间层），
 * 旧代码要等 60s 看门狗拉全量才自愈——期间「任务删了，左侧列表不刷新」。
 * 做法：页面加载后把 S.es.onmessage 换成空函数（吞掉一切推送）、onerror 也吞
 * （不让它降级轮询拉全量），S.sseLive 钉住 true，然后走真实 deleteTask。
 * 期望：侧栏行 ≤1.5s 消失（乐观清场+强制重画），不依赖任何网络推送。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18874, CDP_PORT = 9362;
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-sdb-"));
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

  const profile = mkdtempSync(join(tmpdir(), "tutti-sdb-edge-"));
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

    // 模拟 SSE 假活：吞推送+吞错误（不降级轮询），sseLive 钉 true
    await evalJs(`(() => {
      if (S.es) { S.es.onmessage = () => {}; S.es.onerror = () => {}; }
      S.sseLive = true;
      S._lastSseAt = Date.now();   // 假活：推送时钟永远是新鲜的，看门狗不触发
      return true;
    })()`);
    console.log("✓ 已模拟 SSE 假活（推送全吞、看门狗失效）");

    await evalJs(`window.deleteTask("${TASK}"); true`);
    await sleep(400);
    await evalJs(`(() => {
      const d = document.getElementById("ask");
      if (d && !d.classList.contains("hidden")) document.getElementById("ask-yes").click();
      return true;
    })()`);

    let rowGoneAt = -1, stateGoneAt = -1, postSig = "";
    for (let i = 0; i < 6; i++) {
      await sleep(250);
      const s = JSON.parse(await evalJs(`JSON.stringify({
        row: !!document.querySelector('#side-tasks .stask[data-task="${TASK}"]'),
        inState: (S.state.tasks || []).some(t => t.id === "${TASK}"),
        sigEmpty: S.sideSig === "",
      })`));
      if (stateGoneAt < 0 && !s.inState) stateGoneAt = (i + 1) * 250;
      if (rowGoneAt < 0 && !s.row) rowGoneAt = (i + 1) * 250;
      postSig = s.sigEmpty ? "(已重画)" : postSig;
    }
    console.log("结果: state 本地清场于 " + stateGoneAt + "ms, 侧栏 DOM 刷新于 " + rowGoneAt + "ms " + postSig);
    if (rowGoneAt > 0 && rowGoneAt <= 1500) console.log("✓ SSE 假活下删除即时刷新（乐观清场生效）");
    else { console.log("✗ SSE 假活下侧栏仍不刷新"); exitCode = 1; }
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
