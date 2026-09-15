/* 检查器（右缘停靠列）上下文显隐验收：
 * 1) 任务树点任务行 → 检查器滑出（body.inspector-open）
 * 2) 切到用量统计（设置子页）→ 检查器自动收起、body 无 inspector-open
 * 3) localStorage 里 orch.inspector 仍保留选中（不丢内容）
 * 4) 退出设置回任务树 → 检查器自动滑回，任务标题一致
 * 5) 从设置子页直接点侧栏子任务进运行详情 → 检查器恢复展示
 * 自含临时服务（端口 18795），mock 任务零配额。
 * 用法：node tests/ui_inspector_ctx.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18795;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9353;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-insp-"));
  mkdirSync(join(tmp, "work"), { recursive: true });
  let edge = null, ws = null, svc = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动（端口 " + PORT + "）", up);

    const state = await (await fetch(SERVICE + "/api/state")).json();
    for (const a of state.agents.filter((a) => a.mode === "real")) {
      await fetch(SERVICE + "/api/orchestration", { method: "POST",
        body: JSON.stringify({ agent_id: a.id, enabled: false }) });
    }
    const sub = await (await fetch(SERVICE + "/api/tasks", { method: "POST",
      body: JSON.stringify({ type: "doc", goal: "检查器上下文显隐验收", workdir: join(tmp, "work"), mode: "auto" })
    })).json();
    check("mock 任务受理", Boolean(sub.run_id), JSON.stringify(sub).slice(0, 120));
    let run = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      run = (await (await fetch(SERVICE + "/api/runs/" + sub.run_id)).json()).run;
      if (["done", "failed", "cancelled"].includes(run.status)) break;
    }
    check("mock 任务完成", run && run.status === "done", run ? run.status : "无");

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
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4500);

    // 等侧栏任务行出现
    let hasTask = false;
    for (let i = 0; i < 20 && !hasTask; i++) {
      hasTask = await js(`!!document.querySelector("#side-tasks .stask[data-task]")`);
      if (!hasTask) await sleep(500);
    }
    check("侧栏出现任务行", hasTask);

    // 1) 点任务行 → 检查器滑出
    await js(`document.querySelector("#side-tasks .stask[data-task] summary").click(); "ok"`);
    await sleep(900);
    const open1 = await js(`document.body.classList.contains("inspector-open")
      && !document.getElementById("inspector").classList.contains("hidden")`);
    check("点任务行后检查器滑出", open1 === true);
    const inspTitle = await js(`(S.inspData||{}).task ? S.inspData.task.title || S.inspData.task.id : ""`);
    check("检查器绑定到该任务", Boolean(inspTitle), String(inspTitle));

    // 2) 切用量统计 → 自动收起
    await js(`switchTab("usage"); "ok"`);
    await sleep(900);
    const closed = await js(`!document.body.classList.contains("inspector-open")
      && document.getElementById("inspector").classList.contains("hidden")`);
    check("切用量统计后检查器自动收起", closed === true);
    const kept = await js(`localStorage.getItem("orch.inspector") || ""`);
    check("选中仍保留在 localStorage", Boolean(kept), String(kept));

    // 3) 退出设置回任务树 → 自动滑回
    await js(`exitSettings(); "ok"`);
    await sleep(900);
    const back = await js(`document.body.classList.contains("inspector-open")
      && !document.getElementById("inspector").classList.contains("hidden")`);
    check("回任务树后检查器自动滑回", back === true);
    const sameTask = await js(`S.inspKey === ${JSON.stringify(String(kept))}`);
    check("滑回后仍指向原任务", sameTask === true, "S.inspKey=" + String(await js("S.inspKey")));

    // 4) 再进设置子页 → 收起；从用量页点子任务进运行详情 → 恢复
    await js(`switchTab("usage"); "ok"`);
    await sleep(700);
    const closed2 = await js(`!document.body.classList.contains("inspector-open")`);
    check("再次进设置子页收起", closed2 === true);
    await js(`document.querySelector("#side-tasks .stepx").click(); "ok"`);
    await sleep(900);
    const inDetail = await js(`!document.body.classList.contains("settings-mode")
      && document.body.classList.contains("inspector-open")`);
    check("从设置子页点子任务进详情后检查器恢复", inDetail === true);

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) {
      if (p && p.pid) {
        try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
      }
    }
    if (results_err(tmp)) { /* keep */ } else {
      try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    }
  }

  const bad = FAIL.length;
  console.log("\n===== 检查器上下文显隐：%d 通过 / %d 失败 =====", PASS.length, bad);
  if (bad) { console.log("失败项：", FAIL); process.exit(1); }

  function results_err(t) { return FAIL.length > 0 && false; } // 失败也不留现场（临时数据无价值）
}
main().catch((e) => { console.error(e); process.exit(1); });
