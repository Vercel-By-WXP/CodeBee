/* 定位"多点几次不自适应"：详情页状态下交替开合左栏/检查器多轮，
 * 每步抓 body class、#app 列、.page 与 #run-detail 实宽。一次性诊断脚本。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18824;
const CDP_PORT = 9346;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990101-000000-0003";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-adapt-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(dataDir, "tasks", "task-a.json"), JSON.stringify({
    id: "task-a", title: "自适应压测任务", type: "novel", goal: "造数",
    workdir, status: "running", archived: false,
    created_at: "2099-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "自适应压测任务",
    task_id: "task-a", status: "running",
    steps: [{ n: 1, role: "draft-c1", agent: "mock-a", agent_label: "A", status: "running",
              started_at: "00:00:01", log: "steps/01.log", summary: "", cost_usd: 0, tokens: 0 }],
    messages: [], created_at: "2099-01-01 00:00:00", started_at: "00:00:01",
    ended_at: null, cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }, null, 2));
  writeFileSync(join(runsDir, "steps", "01.log"), "输出行\n", "utf-8");

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1600,950", "about:blank",
  ], { stdio: "ignore" });
  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(`${SERVICE}/api/state`)).ok; } catch (e) {}
    }
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
      { width: 1600, height: 950, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);
    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };
    await evalJson(`(async () => {
      await api("/api/control", { method: "POST", body: JSON.stringify({ action: "acquire" }) });
      /* sub-tasks（任务页）上下文，不进详情 */
      return 1;
    })()`);

    const snap = `(() => {
      const page = document.getElementById("page-settings");
      const det = document.querySelector("#sub-tasks .panel");
      const app = document.getElementById("app");
      return {
        collapsed: document.body.classList.contains("side-collapsed") ? 1 : 0,
        inspOpen: document.body.classList.contains("inspector-open") ? 1 : 0,
        cols: getComputedStyle(app).gridTemplateColumns,
        page: Math.round(page.getBoundingClientRect().width),
        mw: getComputedStyle(page).maxWidth,
        detail: Math.round(det.getBoundingClientRect().width),
        subRunsHidden: document.getElementById("sub-runs").classList.contains("hidden"),
      };
    })()`;

    const seqSteps = [
      ["初始", () => {}],
      ["收左栏", () => `document.body.classList.add("side-collapsed")`],
      ["开检查器", () => `document.body.classList.add("inspector-open")`],
      ["关检查器", () => `document.body.classList.remove("inspector-open")`],
      ["展左栏", () => `document.body.classList.remove("side-collapsed")`],
    ];
    for (let round = 1; round <= 3; round++) {
      for (const [name, act] of seqSteps.slice(round === 1 ? 0 : 1)) {
        if (act()) await evalJson(`(() => { ${act()} ; return 1; })()`);
        await sleep(350);
        const s = await evalJson(snap);
        console.log(`R${round} ${name}: cols=[${s.cols}] page=${s.page}(${s.mw}) detail=${s.detail} coll=${s.collapsed} insp=${s.inspOpen} subHidden=${s.subRunsHidden}`);
      }
    }
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
