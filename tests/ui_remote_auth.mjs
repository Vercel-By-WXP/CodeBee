/* 远程令牌鉴权回归：局域网 IP 访问下，详情数据链（runs/报告/文件）必须带令牌。
 * 背景：远程（非本机）访问时所有 /api/* 都要令牌（header 或 query），本机豁免——
 * 裸 fetch 在开发期从不暴露，一上手机连接就 401 静默空白（2026-09-18 用户报）。
 * 覆盖：① 无令牌 curl → 401，带头 → 200（服务端闸门）；② 页面 ?token= 进
 * → 点任务行 → 步骤区渲染（原 401 路径）→ 成果页签报告可见；③ urlAuth
 * 助手存在且文件弹窗 URL 已补令牌。
 * 跑法：node tests/ui_remote_auth.mjs   （服务 18877 / CDP 9381） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir, networkInterfaces } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18877;
const CDP_PORT = 9381;
const TOKEN = "e2etoken177";
const RUN_ID = "r-20990101-000000-0003";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function lanIP() {
  for (const list of Object.values(networkInterfaces()))
    for (const ni of list || [])
      if (ni.family === "IPv4" && !ni.internal) return ni.address;
  return null;
}

async function main() {
  const lan = lanIP();
  if (!lan) { console.log("SKIP: 无局域网 IP（单机环境跑不了远程鉴权场景）"); return; }
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-rauth-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(dataDir, "remote.json"), JSON.stringify({ token: TOKEN }));
  writeFileSync(join(dataDir, "tasks", "task-a.json"), JSON.stringify({
    id: "task-a", title: "远程鉴权回归任务", type: "novel", goal: "造数",
    workdir, status: "done", archived: false,
    created_at: "2099-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "远程鉴权回归任务",
    task_id: "task-a", status: "done", report: "report.md",
    steps: [{ n: 1, role: "draft-c1", agent: "mock-a", agent_label: "A", status: "done",
              started_at: "00:00:01", log: "steps/01.log", summary: "完成起草", cost_usd: 0, tokens: 0 }],
    messages: [], created_at: "2099-01-01 00:00:00", started_at: "00:00:01",
    ended_at: "00:00:20", cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "写完一章",
  }, null, 2));
  writeFileSync(join(runsDir, "steps", "01.log"), "输出行\n", "utf-8");
  writeFileSync(join(runsDir, "report.md"), "# 远程报告\n\n令牌链路正文。\n", "utf-8");

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const BASE = `http://${lan}:${SERVICE_PORT}`;
  let fails = 0;
  const chk = (ok, name, extra) => {
    if (!ok) fails++;
    console.log(`[${ok ? "ok  " : "FAIL"}] ${name}` + (extra ? " " + extra : ""));
  };
  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(`${BASE}/api/state`, { headers: { "X-CodeBee-Token": TOKEN } })).ok; } catch (e) {}
    }
    if (!up) throw new Error("service not up on " + BASE);

    // ① 服务端闸门：局域网来源无令牌 401、带头 200、query 200
    const r401 = await fetch(`${BASE}/api/tasks/task-a/runs`);
    chk(r401.status === 401, "无令牌 runs 接口 401", "got " + r401.status);
    const rHdr = await fetch(`${BASE}/api/tasks/task-a/runs`,
      { headers: { "X-CodeBee-Token": TOKEN } });
    chk(rHdr.ok, "带头 runs 接口 200", "got " + rHdr.status);
    const rQ = await fetch(`${BASE}/api/runs/${RUN_ID}/report?token=${TOKEN}`);
    chk(rQ.ok, "query 令牌报告接口 200", "got " + rQ.status);

    // ② 静态扫：app.js 不得再有裸 fetch("/api（必须 authHeaders 或经 urlAuth）
    const appjs = await fetch(`${BASE}/app.js`).then((r) => r.text());
    const bareApi = appjs.split("\n").filter((ln) =>
      /fetch\((?:["'`]\/api|["'`]`\$\{)/.test(ln) && !/authHeaders|urlAuth/.test(ln) &&
      !/encodeURIComponent\(latest\.id\)|encodeURIComponent\(id\)/.test(ln));
    const stmts = appjs.match(/fetch\((?:[^;]|\n)*?;|(?:const r = await fetch\((?:.|\n)*?\);)/g) || [];
    const bad = stmts.filter((s) => s.includes('"/api') && !s.includes("authHeaders") && !s.includes("urlAuth"));
    chk(bad.length === 0, "app.js 无裸 /api fetch", bad.length ? JSON.stringify(bad[0]).slice(0, 120) : "");
    chk(/function urlAuth\(/.test(appjs) && /url = urlAuth\(url\)/.test(appjs),
        "urlAuth 助手 + 文件弹窗入口就位");

    // ③ 浏览器：?token= 进 → 点任务行 → 步骤渲染 → 成果报告可见
    const edge = spawn("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", [
      "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
      `--user-data-dir=${mkdtempSync(join(tmpdir(), "tutti-rauth-cdp-"))}`,
      `--remote-debugging-port=${CDP_PORT}`,
      "--blink-settings=prefersReducedMotion=false", "--window-size=460,980", "about:blank",
    ], { stdio: "ignore" });
    try {
      let target = null;
      for (let i = 0; i < 30 && !target; i++) {
        await sleep(500);
        try {
          target = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json())
            .find((t) => t.type === "page");
        } catch (e) {}
      }
      const ws = new WebSocket(target.webSocketDebuggerUrl);
      await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
      let seq = 0; const pending = new Map();
      ws.onmessage = (ev) => { const m = JSON.parse(ev.data);
        if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
      const send = (method, params = {}) => new Promise((res) => {
        const id = ++seq; pending.set(id, res);
        ws.send(JSON.stringify({ id, method, params }));
      });
      await send("Runtime.enable");
      await send("Page.enable");
      await send("Emulation.setDeviceMetricsOverride",
        { width: 440, height: 956, deviceScaleFactor: 3, mobile: true });
      await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}` });
      await sleep(4500);
      const ev = async (expr) => {
        const r = await send("Runtime.evaluate",
          { expression: expr, awaitPromise: true, returnByValue: true });
        if (r.result && r.result.exceptionDetails)
          return { __err: JSON.stringify(r.result.exceptionDetails).slice(0, 200) };
        return r.result ? r.result.result.value : undefined;
      };
      const tok = await ev(`localStorage.getItem("orch.token")`);
      chk(tok === TOKEN, "页面启动把 ?token= 存入 localStorage", String(tok));
      await ev(`(() => {
        const r = document.querySelector('#side-tasks .stask[data-task="task-a"]');
        if (r) r.click(); return 1;
      })()`);
      await sleep(2500);
      const detail = await ev(`(() => {
        const open = document.getElementById("run-detail");
        const steps = document.getElementById("rd-steps");
        return { open: open ? !open.classList.contains("hidden") : false,
                 stepsLen: steps ? (steps.textContent || "").trim().length : -1 };
      })()`);
      chk(detail.open && detail.stepsLen > 0, "远程会话开详情：步骤区渲染（原 401 空白路径）",
          JSON.stringify(detail));
      await ev(`(() => {
        const b = document.querySelector('#rd-tabs .rd-tab[data-tab="result"]');
        if (b && !b.classList.contains("hidden")) b.click(); return 1;
      })()`);
      await sleep(1200);
      const rep = await ev(`(() => {
        const r = document.getElementById("rd-report");
        return (r.textContent || "").trim().slice(0, 40);
      })()`);
      chk(String(rep).includes("远程报告"), "成果页签报告可见", "«" + rep + "»");
    } finally {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    }
    console.log(fails === 0 ? "REMOTE-AUTH: PASS" : `REMOTE-AUTH: ${fails} FAIL`);
    if (fails) process.exitCode = 1;
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(500);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
