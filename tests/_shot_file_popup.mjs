/* 文件内容弹窗截图（judge 视觉验收用）：复用 ui_file_popup 的种子，
 * 产出 tests/_shots/fp_files.png / fp_diff.png / fp_md.png 后退出。 */
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18819;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9349;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "tests", "_shots");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

async function main() {
  mkdirSync(OUT, { recursive: true });
  const tmp = mkdtempSync(join(tmpdir(), "tutti-fpshot-"));
  const dataDir = join(tmp, "data");
  const work = join(tmp, "work");
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(work, { recursive: true });
  const G = (a) => execFileSync("git", a, { cwd: work, encoding: "utf-8" });
  G(["init"]); G(["config", "user.name", "Tester"]); G(["config", "user.email", "t@l"]);
  writeFileSync(join(work, "README.md"), "baseline\n", "utf-8");
  G(["add", "-A"]); G(["commit", "-m", "baseline"]);
  const taskId = "t" + Date.now().toString(36) + "shot";
  const runId = "r" + Date.now().toString(36) + "shot";
  G(["checkout", "-q", "-b", "tutti/" + taskId]);
  writeFileSync(join(work, "chapter-01.md"), "第一章 试炼开始\n清晨的雾还没散。\n少年推开了门。\n", "utf-8");
  G(["add", "-A"]); G(["commit", "-m", "seed"]);
  G(["checkout", "-q", "master"]);
  writeFileSync(join(work, "chapter-01.md"), "第一章 试炼开始\n清晨的雾还没散。\n少年推开了门。\n", "utf-8");
  writeFileSync(join(work, "cover.json"), '{"title":"七猫甜宠","chapters":3}', "utf-8");
  writeFileSync(join(work, "hero.png"), PNG_1PX);
  writeFileSync(join(work, "outline.md"), "# 大纲\n\n第一章：出门\n第二章：入山\n", "utf-8");

  const DIFF = [
    "diff --git a/README.md b/README.md",
    "index 1111111..2222222 100644",
    "--- a/README.md",
    "+++ b/README.md",
    "@@ -1,1 +1,2 @@",
    " baseline",
    "+新增一行说明，交代世界观。",
    "diff --git a/chapter-01.md b/chapter-01.md",
    "new file mode 100644",
    "index 0000000..3333333",
    "--- /dev/null",
    "+++ b/chapter-01.md",
    "@@ -0,0 +1,3 @@",
    "+第一章 试炼开始",
    "+清晨的雾还没散。",
    "+少年推开了门。",
  ].join("\n");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", runId), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "code", engine: "code", title: "七猫甜宠线2（auto 多智能体）", goal: "写第三章",
    workdir: work, git_rev: "HEAD", git_state: "isolated", status: "done",
    created_at: "2026-09-14 10:00:00", attachments: [], mode: "auto", difficulty: "auto",
    implementer: "", verify_command: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
    id: runId, kind: "orchestration", title: "七猫甜宠线2", task_id: taskId,
    status: "done",
    steps: [{ n: 1, status: "done", role: "起草", agent_label: "writer",
              summary: "写一章", duration_s: 20, log: "steps/1.log" }],
    messages: [], created_at: "2026-09-14 10:00:01",
    started_at: "2026-09-14 10:00:01", ended_at: "2026-09-14 10:05:00",
    cost_usd: 15.238, tokens: 13198403, error: "", verdict: null, summary: "",
    git: { rev: "HEAD", branch: "tutti/" + taskId, commit: "abc1234", from_branch: "master",
           base_commit: "def0123", restored: true, restore_error: "" },
    changes: { files: [
        { status: "M", path: "README.md", add: 1, del: 0 },
        { status: "A", path: "chapter-01.md", add: 3, del: 0 }],
      add_total: 4, del_total: 0, diff: DIFF },
  }), "utf-8");

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    for (let i = 0; i < 40; i++) { await sleep(500); try { if ((await fetch(SERVICE + "/api/state")).ok) break; } catch (e) {} }
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
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
    const sendErr = async (method, params = {}) => {
      const r = await send(method, params);
      if (r.error) throw new Error(method + ": " + JSON.stringify(r.error).slice(0, 200));
      return r;
    };
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    const shot = async (name) => {
      await sendErr("Page.bringToFront");
      const r = await sendErr("Page.captureScreenshot", { format: "png" });
      const data = (r.result || {}).data;
      if (!data) throw new Error("captureScreenshot 无 data: " + JSON.stringify(r).slice(0, 200));
      writeFileSync(join(OUT, name), Buffer.from(data, "base64"));
    };
    await send("Page.enable");
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await evalJs(`(async () => {
      openRun(${JSON.stringify(runId)});
      await new Promise(r => setTimeout(r, 1200));
      openInspector(${JSON.stringify(taskId)});
      await new Promise(r => setTimeout(r, 1500));
      const b = [...document.querySelectorAll("#insp-tabs .insp-tab")].find(x => x.dataset.tab === "files");
      if (b) b.click();
      await new Promise(r => setTimeout(r, 1800));
      return 1; })()`);
    await shot("fp_files.png");
    await evalJs(`(() => { const b = [...document.querySelectorAll("#insp-git-main .insp-cf")]
      .find(x => x.dataset.p === "chapter-01.md"); if (b) b.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      const t = [...document.querySelectorAll("#insp-tabs .insp-tab")].find(x => x.dataset.tab === "git"); if (t) t.click();
      return 1; })()`);
    await sleep(1000);
    await shot("fp_diff.png");
    await evalJs(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); "ok"`);
    await sleep(300);
    await evalJs(`(() => { const b = [...document.querySelectorAll("#insp-tabs .insp-tab")].find(x => x.dataset.tab === "files");
      if (b) b.click();
      const c = [...document.querySelectorAll("#insp-pane-files .file-chip:not(.prev)")]
        .find(x => x.querySelector(".p") && x.querySelector(".p").textContent === "outline.md");
      if (c) c.click(); return 1; })()`);
    await sleep(1000);
    await shot("fp_md.png");
    console.log("shots saved:", OUT);
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { if (edge) edge.kill(); } catch (e) {}
    try { if (svc) svc.kill(); } catch (e) {}
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error("FATAL", e); process.exit(1); });
