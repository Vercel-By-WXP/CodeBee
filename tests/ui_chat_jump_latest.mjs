/* 对话时间线「跳到最新」核验（2026-10-08 用户诉求：点「继续任务」后对话应跳到
 * 最新，别停在原地或历史中间）。长时间线种子（24 步 + 追问，见
 * _seed_chat_long_fixture.py）走真实渲染链，量四件事：
 *   A) 首开任务详情 → 切进对话页签：时间线落最新（底部）；
 *   B) chatJumpLatest()：无论滚到哪，立即落底；
 *   C) 页签不可见时置标记，切回对话页签那一刻带到底（继续任务时默认落蜂巢的场景）；
 *   D) 回看历史不被打扰：上滑后内容重绘（无标记、非贴底态）不拽人。
 * 前置：TUTTI_DATA 临时目录起 18922 服务（本脚本自起）；结束按 PID 清场。
 */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18922;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9371;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => { try { execSync(`cmd /c if exist "${p}" echo ok`, { stdio: "pipe" }); return true; } catch (e) { return false; } });
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口 " + PORT + " 已被占用，先清理残留服务再跑");
    process.exit(2);
  } catch (e) { /* 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-jump-"));
  const seed = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_chat_long_fixture.py")],
    { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let seedOut = "";
  seed.stdout.on("data", (d) => { seedOut += String(d); });
  await new Promise((res) => seed.on("exit", res));
  const runId = (seedOut.match(/RUN_ID=(\S+)/) || [])[1] || "";
  const taskId = (seedOut.match(/TASK_ID=(\S+)/) || [])[1] || "";
  check("种子长对话 run 就绪", !!runId && !!taskId, seedOut.slice(0, 200));

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  check("临时服务启动", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-jump-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--disable-sync", "--disable-extensions", `--user-data-dir=${profile}`,
    `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless + CDP 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 25000);
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
      if (ex) throw new Error("页面抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    // 滚动位置读数：距底距离（<=40px 视为贴底）+ 气泡数
    const flowPos = async () => JSON.parse(await evalJs(`JSON.stringify((() => {
      const f = document.getElementById("rd-chat-flow");
      if (!f) return { missing: true };
      return { h: f.scrollHeight, top: f.scrollTop, ch: f.clientHeight,
        dist: f.scrollHeight - f.scrollTop - f.clientHeight,
        rows: f.querySelectorAll(".chat-row").length };
    })())`));
    const atBottom = (p) => !p.missing && p.rows > 0 && p.dist <= 40;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`try { localStorage.setItem("orch.welcomed", "1"); } catch (e) {} "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    /* ---- A) 首开任务详情：切进对话页签应落在最新（底部）---- */
    await evalJs(`sideOpenTask(${JSON.stringify(taskId)}); "ok"`);
    await sleep(2500);
    await evalJs(`rdChatNavGo("chat"); "ok"`);
    await sleep(600);
    const a = await flowPos();
    check("A 首开详情时间线足够长（可滚动）", !a.missing && a.h > a.ch * 2, JSON.stringify(a));
    check("A 首开切进对话即落最新（底部）", atBottom(a), JSON.stringify(a));

    /* ---- B) chatJumpLatest：滚到顶后调用立即落底 ---- */
    await evalJs(`(function(){ var f=document.getElementById("rd-chat-flow"); f.scrollTop=0; return f.scrollTop; })()`);
    await evalJs(`chatJumpLatest(); "ok"`);
    const b = await flowPos();
    check("B chatJumpLatest 立即落底", atBottom(b), JSON.stringify(b));

    /* ---- C) 页签不可见置标记 → 切回对话那一刻带到底 ---- */
    await evalJs(`(function(){ var f=document.getElementById("rd-chat-flow"); f.scrollTop=0; return f.scrollTop; })()`);
    await evalJs(`rdChatNavGo("steps"); "ok"`);
    await sleep(300);
    await evalJs(`chatJumpLatest(); "ok"`);
    await evalJs(`rdChatNavGo("chat"); "ok"`);
    await sleep(400);
    const c = await flowPos();
    check("C 隐藏时置标记、切回对话带到底", atBottom(c), JSON.stringify(c));

    /* ---- D) 回看历史不被打扰：上滑后重绘（无标记）不拽人 ---- */
    // scrollTop=0 也要压掉 smooth（否则同步读回还在底部，被误判成贴底跟随）
    await evalJs(`(function(){ chatForceBottom=false; var f=document.getElementById("rd-chat-flow"); f.style.scrollBehavior="auto"; f.scrollTop=0; f.style.scrollBehavior=""; chatLiveSig=""; drawChatFlow(S.lastRun, { items: (chatTimelineItems||[]) }, false); return "ok"; })()`);
    await sleep(300);
    const d = await flowPos();
    check("D 上滑回看历史时重绘不拽到最新", !atBottom(d), JSON.stringify(d));

    /* ---- E) 贴底态重绘跟随（标记消费后近底仍跟随）---- */
    await evalJs(`chatJumpLatest(); "ok"`);
    await evalJs(`(function(){ chatLiveSig=""; drawChatFlow(S.lastRun, { items: (chatTimelineItems||[]) }, false); return "ok"; })()`);
    await sleep(300);
    const e2 = await flowPos();
    check("E 贴底态重绘继续跟随最新", atBottom(e2), JSON.stringify(e2));
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${proc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${svc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r).length;
  console.log("\n===== 对话跳最新（UI/CDP）：" + (results.length - bad) + " 通过 / " + bad + " 失败 =====");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
