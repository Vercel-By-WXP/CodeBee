/* 检查器 Git 卡「检出失败」态核验（Edge headless + CDP，临时端口 18833）：
 * 任务指定了代码版本（git_rev）但检出失败（run 无 git 信息、带 error）→
 * 卡片必须显示 检出失败 chip + 错误原因，不能误报成「未启用代码版本隔离」；
 * 未指定 git_rev 的任务仍显示「未启用」说明。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18833;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9355;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-cofail-"));
  const dataDir = join(tmp, "data");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  const mk = (taskId, runId, title, gitRev, runGit, runError) => {
    writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
      id: taskId, type: "code", engine: "code", title, goal: "g",
      workdir: join(tmp, "wd-" + taskId), git_rev: gitRev, git_state: "",
      status: "failed", created_at: "2026-09-15 10:00:00", attachments: [],
      mode: "auto", difficulty: "auto", implementer: "", verify_command: "",
    }), "utf-8");
    mkdirSync(join(dataDir, "runs", runId), { recursive: true });
    writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
      id: runId, kind: "orchestration", title, task_id: taskId,
      status: "failed", steps: [], messages: [], created_at: "2026-09-15 10:00:01",
      started_at: "2026-09-15 10:00:01", ended_at: "2026-09-15 10:00:30",
      cost_usd: 0, tokens: 0, error: runError, verdict: null, summary: "",
      git: runGit || null,
    }), "utf-8");
  };
  const ERR = "代码版本检出失败：仓库中不存在版本「no-such-branch」";
  mk("tfail1", "rfail1", "检出失败任务", "HEAD", null, ERR);
  mk("tfail2", "rfail2", "未指定版本任务", "", null, "");

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge headless 就绪", !!target);
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

    const card = `JSON.stringify((() => {
      const chip = document.getElementById("insp-git-chip");
      const main = document.getElementById("insp-git-main");
      return { chip: chip ? chip.textContent : "", hidden: chip ? chip.classList.contains("hidden") : true,
        main: main ? main.innerText.slice(0, 200) : "" }; })())`;

    await evalJs(`(async () => { openInspector("tfail1");
      await new Promise(r => setTimeout(r, 1200)); return 1; })()`);
    let c = JSON.parse(await evalJs(card));
    check("检出失败任务：chip 显示「检出失败」", c.chip.includes("检出失败") && !c.hidden, JSON.stringify(c));
    check("卡片带检出错误原因", c.main.includes("仓库中不存在版本"), c.main);
    check("不再误报「未启用代码版本隔离」", !c.main.includes("未启用代码版本隔离"), c.main);
    check("提示可继续重新检出", c.main.includes("继续任务"), c.main);

    await evalJs(`(async () => { openInspector("tfail2");
      await new Promise(r => setTimeout(r, 1200)); return 1; })()`);
    c = JSON.parse(await evalJs(card));
    check("未指定版本任务：仍显示「未启用」说明", c.hidden && c.main.includes("未启用代码版本隔离"),
      JSON.stringify(c));
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { if (svc) svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
