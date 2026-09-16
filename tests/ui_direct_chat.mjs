/* 直连任务（direct 引擎）UI 核验：新建页默认类型 + 详情页「对话」分区 + 追话续跑。
 *   A) 新建任务：类型默认落在「直接执行」，快捷 chips 含它，直连时不显示验证/评审字段；
 *   B) 详情页：direct run 默认选「对话」分区（不是蜂巢/步骤），时间线渲染用户与智能体气泡；
 *   C) 已结束的 direct run：贴底框发送 → 调 /chat → 起新一轮并跳转。
 * 前置：TUTTI_DATA 临时目录起 18798 服务（本脚本自起）。
 * 种子：_seed_direct_fixture.py 造一个 done 的 direct run（含消息与步骤输出）。
 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const PORT = 18798;
const CDP_PORT = 9336;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
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

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-direct-"));
  const seed = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_direct_fixture.py")],
    { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let seedOut = "";
  seed.stdout.on("data", (d) => { seedOut += String(d); });
  await new Promise((res) => seed.on("exit", res));
  const runId = (seedOut.match(/RUN_ID=(\S+)/) || [])[1] || "";
  const taskId = (seedOut.match(/TASK_ID=(\S+)/) || [])[1] || "";

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
  check("种子直连 run 就绪", !!runId && !!taskId, seedOut.slice(0, 200));

  const profile = mkdtempSync(join(tmpdir(), "tutti-direct-edge-"));
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
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    /* ---- A) 新建任务：默认类型 = 直接执行；直连不显示验证/评审字段 ---- */
    const a = JSON.parse(await evalJs(`JSON.stringify({
      type: (document.getElementById("f-type")||{}).value,
      typeName: (document.getElementById("f-type-name")||{}).textContent,
      hasChip: [...document.querySelectorAll("#cmp-quick .cmp-chip")]
        .some(c => c.textContent.includes("直接执行")),
      codeHidden: document.getElementById("f-code-only").classList.contains("hidden"),
      reviewHidden: document.getElementById("f-review-only").classList.contains("hidden"),
    })`));
    check("新建任务默认类型=直接执行", a.type === "direct", JSON.stringify(a));
    check("类型胶囊显示「直接执行」", a.typeName === "直接执行", a.typeName);
    check("快捷 chips 含「直接执行」", a.hasChip, JSON.stringify(a));
    check("直连时隐藏验证命令字段", a.codeHidden === true, JSON.stringify(a));
    check("直连时隐藏评审参数字段", a.reviewHidden === true, JSON.stringify(a));

    /* ---- B) 详情页：direct run 默认落「对话」分区 ---- */
    await evalJs(`openRun(${JSON.stringify(runId)}); "ok"`);
    await sleep(2500);
    const b = JSON.parse(await evalJs(`JSON.stringify({
      chatVisible: !document.getElementById("rd-chat").classList.contains("hidden"),
      activeTab: (document.querySelector("#rd-tabs .rd-tab.active")||{}).dataset?.tab || "",
      chatTabVisible: !document.querySelector('#rd-tabs .rd-tab[data-tab="chat"]').classList.contains("hidden"),
      bubbles: document.querySelectorAll("#rd-chat-flow .chat-bubble").length,
      mine: document.querySelectorAll("#rd-chat-flow .chat-row.me").length,
      agent: document.querySelectorAll("#rd-chat-flow .chat-bubble.agent").length,
      body: (document.querySelector("#rd-chat-flow .chat-body")||{}).textContent || "",
    })`));
    check("直连 run 显示对话分区", b.chatVisible === true, JSON.stringify(b));
    check("「对话」页签可见且默认选中", b.chatTabVisible && b.activeTab === "chat", JSON.stringify(b));
    check("时间线渲染出气泡", b.bubbles >= 2, JSON.stringify(b));
    check("含用户气泡与智能体气泡", b.mine >= 1 && b.agent >= 1, JSON.stringify(b));
    check("智能体气泡带 CLI 输出正文", /错别字|DIRECT_DONE|README/.test(b.body), b.body.slice(0, 160));

    /* ---- C) 已结束：贴底发送 → /chat 起新一轮 ---- */
    const c1 = await evalJs(`(async () => {
      const ta = document.getElementById("rd-chat-input");
      ta.value = "再补充一节说明";
      const before = (await (await fetch("/api/runs")).json()).runs.length;
      await window.chatSend();
      return JSON.stringify({ before });
    })()`);
    await sleep(3000);
    const c2 = JSON.parse(await evalJs(`JSON.stringify({
      runs: (window.S && S.state && S.state.runs || []).length,
      detailOpen: !document.getElementById("run-detail").classList.contains("hidden"),
      title: (document.getElementById("rd-title")||{}).textContent || "",
    })`));
    check("追话后仍在详情页（已跳到新一轮）", c2.detailOpen === true, JSON.stringify(c2));

    // 后端直查：新 run 与旧 run 同任务、带继承消息
    const runs = await (await fetch(SERVICE + "/api/runs")).json();
    const sameTask = (runs.runs || []).filter((r) => r.task_id === taskId);
    check("同任务出现第二轮 run", sameTask.length >= 2, "同任务 run 数=" + sameTask.length);

    await send("Page.captureScreenshot", { format: "png" }).catch(() => {});
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${proc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${svc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r).length;
  console.log("\n===== 直连任务（UI/CDP）：" + (results.length - bad) + " 通过 / " + bad + " 失败 =====");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
