/* 探针：谁在详情打开后又把检查器拉出来？
 * 种子一条 done 任务+run → openInspector → sideOpenRun → 包一层记录
 * syncInspectorVis / openInspector 的调用时序与现场（body 类、detail 显隐）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18901;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9345;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-probe-"));
  mkdirSync(join(tmp, "work"), { recursive: true });
  let svc = null, edge = null, ws = null;
  let taskId = "", runId = "";
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) { await sleep(500); try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {} }
    console.log("service:", up);

    // 与 ui_inspector_ctx 同款：mock 任务真跑
    const state0 = await (await fetch(SERVICE + "/api/state")).json();
    for (const a of state0.agents.filter((a) => a.mode === "real")) {
      await fetch(SERVICE + "/api/orchestration", { method: "POST",
        body: JSON.stringify({ agent_id: a.id, enabled: false }) });
    }
    const sub = await (await fetch(SERVICE + "/api/tasks", { method: "POST",
      body: JSON.stringify({ type: "doc", goal: "让位探针 mock", workdir: join(tmp, "work"), mode: "auto" })
    })).json();
    taskId = sub.task_id || taskId; runId = sub.run_id;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      const r = (await (await fetch(SERVICE + "/api/runs/" + sub.run_id)).json()).run;
      if (["done", "failed", "cancelled"].includes(r.status)) break;
    }
    console.log("mock done, task:", taskId, "run:", runId);

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try { const l = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json(); target = l.find((t) => t.type === "page"); } catch (e) {}
    }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (m2, p = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method: m2, params: p })); });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await js(`window.alert=()=>{};window.confirm=()=>true; "ok"`);

    // 等任务行出现
    for (let i = 0; i < 20; i++) {
      if (await js(`!!document.querySelector("#side-tasks .stask[data-task]")`)) break;
      await sleep(500);
    }

    // 包一层记录调用
    await js(`(() => {
      window.__log = [];
      const snap = (tag) => window.__log.push({ tag,
        body: document.body.className.split(" ").filter(c => c.includes("inspector") || c.includes("settings")).join(","),
        detailHidden: document.getElementById("run-detail").classList.contains("hidden"),
        inspHidden: document.getElementById("inspector").classList.contains("hidden") });
      const _sv = window.syncInspectorVis || syncInspectorVis;
      syncInspectorVis = function () { snap("syncInspectorVis→" + (_sv ? "" : "?")); const r = _sv.apply(this, arguments); snap("syncInspectorVis←"); return r; };
      const _oi = window.openInspector;
      openInspector = function (k) { snap("openInspector:" + k); const r = _oi.apply(this, arguments); snap("openInspector←"); return r; };
      return "ok";
    })()`);

    // 完全复刻 ui_inspector_ctx 流程：行点击 → 用量页 → sideOpenRun
    await js(`document.querySelector("#side-tasks .stask[data-task]").click(); "ok"`);
    await sleep(800);
    await js(`switchTab("usage"); "ok"`);
    await sleep(800);
    await js(`(() => { const k = localStorage.getItem("orch.inspector");
      const lr = (S.state.task_latest || {})[k]; sideOpenRun(lr.id); return "ok"; })()`);
    await sleep(1500);

    const log = await js(`JSON.stringify(window.__log || [])`);
    const state = await js(`JSON.stringify({
      body: document.body.className.split(" ").filter(c => c.includes("inspector")).join(","),
      detailHidden: document.getElementById("run-detail").classList.contains("hidden"),
      inspHidden: document.getElementById("inspector").classList.contains("hidden"),
      detailRunId: S.detailRunId, inspKey: S.inspKey })`);
    console.log("LOG:", log);
    console.log("STATE:", state);
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error("FATAL", e); process.exit(2); });
