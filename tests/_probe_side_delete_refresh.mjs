/* 探针：任务删除后左侧列表是否刷新（用户真机反馈「任务删了，左侧列表不刷新」）。
 * 流程：临时数据种盘 → 临时服务 → Edge headless → 页面抢控制权 →
 *   不开详情直接走 window.deleteTask 真实入口 → 每 500ms 采样 8s：
 *   S.state.tasks 是否已少该任务 / 侧栏 DOM 行是否消失 / SSE 活性与推送新鲜度。
 * 判定断点在哪一环：
 *   state 已删 + DOM 未刷 → render/签名环节问题
 *   state 未删 + DOM 在   → SSE 推送链问题（后端 bump 未达）
 *   全都及时变            → 主路径健康（用户撞的可能是多实例/旧进程等其他因素）
 * 端口 18873 / CDP 9361（避开常用口）；跑完按 PID 杀进程并验端口归还。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18873, CDP_PORT = 9361;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TASK = "task-ctx";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const say = (m) => console.log(m);

function killByPids(pids) {
  for (const pid of pids) {
    try { execSync(`taskkill /F /T /PID ${pid}`, { stdio: "ignore" }); } catch (e) { /* already gone */ }
  }
}
function pidsOn(port) {
  try {
    return execSync(`netstat -ano | findstr ":${port} " | findstr "LISTENING"`, { stdio: "pipe" })
      .toString().split("\n").map((l) => l.trim().split(/\s+/).pop()).filter(Boolean);
  } catch (e) { return []; }
}

async function main() {
  for (const p of [PORT, CDP_PORT]) {
    const pids = pidsOn(p);
    if (pids.length) { say("✗ 端口 " + p + " 被占 PID=" + pids.join(",")); process.exit(2); }
  }
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-sdr-"));
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
  say((up ? "✓" : "✗") + " 临时服务启动 " + PORT);
  if (!up) process.exit(2);

  const profile = mkdtempSync(join(tmpdir(), "tutti-sdr-edge-"));
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

    // 基线：任务行在侧栏 + SSE 活着
    const base = await evalJs(`JSON.stringify({
      row: !!document.querySelector('#side-tasks .stask[data-task="${TASK}"]'),
      nTasks: (S.state.tasks || []).length,
      sse: !!S.sseLive,
      lastSse: S._lastSseAt ? (Date.now() - S._lastSseAt) : -1,
    })`);
    say("基线: " + base);
    if (!JSON.parse(base).row) throw new Error("基线就失败：侧栏没画出任务行");

    // 走真实删除入口（不开详情）：deleteTask 内部 uiConfirm → 点确认
    await evalJs(`window.deleteTask("${TASK}"); true`);
    await sleep(400);
    const dlg = await evalJs(`(() => {
      const d = document.getElementById("ask");
      const open = d && !d.classList.contains("hidden");
      if (open) document.getElementById("ask-yes").click();
      return open;
    })()`);
    say((dlg ? "✓" : "✗") + " 删除确认框弹出并确认");

    // 采样 8s：哪一环先到位
    let stateGoneAt = -1, rowGoneAt = -1;
    for (let i = 0; i < 16; i++) {
      await sleep(500);
      const s = JSON.parse(await evalJs(`JSON.stringify({
        inState: (S.state.tasks || []).some(t => t.id === "${TASK}"),
        row: !!document.querySelector('#side-tasks .stask[data-task="${TASK}"]'),
        sse: !!S.sseLive,
        lastSse: S._lastSseAt ? (Date.now() - S._lastSseAt) : -1,
        sideSigLen: (S.sideSig || "").length,
      })`));
      if (stateGoneAt < 0 && !s.inState) stateGoneAt = (i + 1) * 500;
      if (rowGoneAt < 0 && !s.row) rowGoneAt = (i + 1) * 500;
      say(`  t+${(i + 1) * 500}ms inState=${s.inState} row=${s.row} sse=${s.sse} lastSse前=${s.lastSse}ms`);
    }
    say("结论: state 刷新于 " + stateGoneAt + "ms, 侧栏 DOM 刷新于 " + rowGoneAt + "ms");
    if (stateGoneAt > 0 && rowGoneAt < 0) { say("✗ 断点=前端 render/签名环节"); exitCode = 1; }
    else if (stateGoneAt < 0 && rowGoneAt < 0) { say("✗ 断点=后端→SSE 推送链"); exitCode = 1; }
    else if (stateGoneAt > 0 && rowGoneAt > 0) { say("✓ 主路径健康（本场景无法复现）"); }
    else { say("? state 未刷但 DOM 刷了（不可能态）"); exitCode = 1; }
  } catch (e) {
    say("✗ " + e.message);
    exitCode = 1;
  } finally {
    // 按 PID 清进程：Edge → 服务
    try { proc.kill(); } catch (e) { /* ignore */ }
    const edgePids = pidsOn(CDP_PORT);
    killByPids(edgePids);
    const svcPids = pidsOn(PORT);
    killByPids(svcPids);
    await sleep(800);
    const left1 = pidsOn(PORT), left2 = pidsOn(CDP_PORT);
    say((left1.length === 0 && left2.length === 0 ? "✓" : "✗") +
      " 端口归还自证 (svc:" + left1.join(",") + " cdp:" + left2.join(",") + ")");
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  process.exit(exitCode);
}
main();
