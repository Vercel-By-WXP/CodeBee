/* 弹窗几何探针：量 .fp-panel 高度与 .fp-acts 子元素盒子，判断高度自适应与按钮重叠。 */
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18821;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9351;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-fpprobe-"));
  const dataDir = join(tmp, "data");
  const work = join(tmp, "work");
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(work, { recursive: true });
  writeFileSync(join(work, "outline.md"), "# 大纲\n\n- 第一章\n- 第二章\n", "utf-8");
  const taskId = "t" + Date.now().toString(36) + "probe";
  const runId = "r" + Date.now().toString(36) + "probe";
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", runId), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "code", engine: "code", title: "探针", goal: "g", workdir: work,
    status: "done", created_at: "2026-09-14 10:00:00", attachments: [],
  }), "utf-8");
  writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
    id: runId, kind: "orchestration", title: "探针", task_id: taskId, status: "done",
    steps: [], messages: [], created_at: "2026-09-14 10:00:01", started_at: "2026-09-14 10:00:01",
    ended_at: "2026-09-14 10:05:00", cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }), "utf-8");

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    for (let i = 0; i < 40; i++) { await sleep(500); try { if ((await fetch(SERVICE + "/api/state")).ok) break; } catch (e) {} }
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) { await sleep(500);
      try { const l = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = l.find((t) => t.type === "page"); } catch (e) {} }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await evalJs(`(async () => {
      openRun(${JSON.stringify(runId)});
      await new Promise(r => setTimeout(r, 1200));
      openInspector(${JSON.stringify(taskId)});
      await new Promise(r => setTimeout(r, 1500));
      const b = [...document.querySelectorAll("#insp-tabs .insp-tab")].find(x => x.dataset.tab === "files");
      if (b) b.click();
      await new Promise(r => setTimeout(r, 1500));
      return 1; })()`);
    await evalJs(`(() => { const c = [...document.querySelectorAll("#insp-pane-files .file-chip:not(.prev)")]
      .find(x => x.querySelector(".p") && x.querySelector(".p").textContent === "outline.md");
      if (c) c.click(); return 1; })()`);
    await sleep(1000);
    const probe = JSON.parse(await evalJs(`JSON.stringify((() => {
      const panel = document.querySelector("#file-pop .fp-panel");
      const acts = document.querySelector("#file-pop .fp-acts");
      const kids = [...(acts ? acts.children : [])].map((el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return { tag: el.tagName, text: (el.textContent || "").trim().slice(0, 8),
          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
          pos: cs.position, disp: cs.display };
      });
      const pr = panel.getBoundingClientRect();
      const body = document.querySelector("#file-pop .fp-body").getBoundingClientRect();
      return { panel: { h: Math.round(pr.height), w: Math.round(pr.width),
          cssH: getComputedStyle(panel).height, minH: getComputedStyle(panel).minHeight },
        body: { h: Math.round(body.height) },
        acts: kids };
    })())`));
    console.log(JSON.stringify(probe, null, 1));
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { if (edge) edge.kill(); } catch (e) {}
    try { if (svc) svc.kill(); } catch (e) {}
    await sleep(800);
    try { execFileSync("taskkill", ["/F", "/PID", String(svc.pid), "/T"], { stdio: "ignore" }); } catch (e) {}
  }
}
main().catch((e) => { console.error("FATAL", e); process.exit(1); });
