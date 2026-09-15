/* 故事圣经面板显隐闸门：Edge headless + CDP（零依赖，模板同 ui_check.mjs）。
 * 临时数据目录起服务（不碰真实 data/），种一个非连载任务 + 一个连载任务，
 * 断言：代码类任务的任务级/run 级详情都不渲染故事圣经面板；连载任务两条路径都渲染。
 * 服务用独立端口 18796——18798/18799 是其他测试的固定端口，Windows 下双绑不报错，别撞。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18796;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9336;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function seed(dataDir, workDir) {
  const now = "2026-09-15 14:00:00";
  const mkTask = (id, title, serial) => ({
    id, type: serial ? "novel" : "code", engine: serial ? "novel" : "code",
    title, goal: title, context: "", workdir: workDir, mode: "auto", difficulty: "auto",
    implementer: "", attachments: [], created_at: now, status: "failed",
    git_rev: "", verify_command: "",
    ...(serial ? { serial: { chapters: 4, words_per_chapter: 1000 } } : {}),
  });
  const mkRun = (id, task) => ({
    id, kind: "orchestration", title: task.title, task_id: task.id, entry_id: null, op: null,
    status: "failed", steps: [], messages: [], created_at: now, started_at: now, ended_at: now,
    cost_usd: 0, tokens: 0, error: "预置失败现场", verdict: null, summary: "",
  });
  const tasks = [
    mkTask("t-gate-code", "排查代码问题（非连载）", false),
    mkTask("t-gate-novel", "连载小说（连载）", true),
  ];
  for (const t of tasks) {
    mkdirSync(join(dataDir, "tasks"), { recursive: true });
    writeFileSync(join(dataDir, "tasks", t.id + ".json"), JSON.stringify(t, null, 2));
    const run = mkRun("r-" + t.id, t);
    mkdirSync(join(dataDir, "runs", run.id), { recursive: true });
    writeFileSync(join(dataDir, "runs", run.id, "run.json"), JSON.stringify(run, null, 2));
  }
}

async function main() {
  // 端口预检：已有监听者就中止（可能是别人的服务，Windows 双绑不报错）
  try { await fetch(SERVICE + "/api/state", { signal: AbortSignal.timeout(800) }); 
    console.log("端口 " + PORT + " 已被占用，中止（先查 netstat 归属）"); process.exit(2);
  } catch (e) { if (e?.name === "AbortError") { console.log("端口探测超时，中止"); process.exit(2); } }

  const tmp = mkdtempSync(join(tmpdir(), "tutti-bible-"));
  const dataDir = join(tmp, "data");
  const workDir = join(tmp, "work");
  mkdirSync(workDir, { recursive: true });
  seed(dataDir, workDir);

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
    cwd: ROOT, stdio: "ignore",
  });

  const edge = EDGE_CANDIDATES.find(() => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-bible-edge-"));
  const proc = spawn(edge, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* 未就绪 */ }
    }
    check("服务启动（临时数据目录 " + PORT + "）", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);

    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
      return r.result?.result?.value;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    // 等前端状态里出现种下的两个任务（SSE/轮询就绪的标志）
    let ready = false;
    for (let i = 0; i < 20 && !ready; i++) {
      await sleep(500);
      ready = await evalJs(`((S.state||{}).tasks||[]).some(t=>t.id==="t-gate-novel")`);
    }
    check("前端加载到预置任务", !!ready);

    const bibleHidden = `document.getElementById("rd-bible").classList.contains("hidden")`;

    // 1) 非连载任务：run 级详情不渲染故事圣经
    await evalJs(`sideOpenRun("r-t-gate-code"); "ok"`);
    await sleep(1200);
    check("代码任务 run 详情：故事圣经面板隐藏", (await evalJs(bibleHidden)) === true);

    // 2) 非连载任务：任务级详情同样隐藏
    await evalJs(`sideOpenTask("t-gate-code"); "ok"`);
    await sleep(1200);
    check("代码任务任务详情：故事圣经面板隐藏", (await evalJs(bibleHidden)) === true);

    // 3) 连载任务：run 级详情渲染（尚未创建提示是创建入口）
    await evalJs(`sideOpenRun("r-t-gate-novel"); "ok"`);
    await sleep(1200);
    const nv1 = await evalJs(`JSON.stringify({hidden:${bibleHidden}, txt:document.getElementById("rd-bible").innerText.slice(0,80)})`);
    const o1 = JSON.parse(nv1);
    check("连载任务 run 详情：面板渲染", o1.hidden === false, nv1);
    check("连载任务面板带创建入口提示", /尚未创建/.test(o1.txt || ""), nv1);

    // 4) 连载任务：任务级详情渲染
    await evalJs(`sideOpenTask("t-gate-novel"); "ok"`);
    await sleep(1200);
    const nv2 = await evalJs(`JSON.stringify({hidden:${bibleHidden}, txt:document.getElementById("rd-bible").innerText.slice(0,80)})`);
    const o2 = JSON.parse(nv2);
    check("连载任务任务详情：面板渲染", o2.hidden === false, nv2);

    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "bible-gate.png"), Buffer.from(r.result.data, "base64"));
    });
    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 故事圣经闸门：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
