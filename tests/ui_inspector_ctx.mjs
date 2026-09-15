/* 检查器（右缘停靠列）上下文显隐验收（检查器＝任务树浏览态的快捷预览列）：
 * 1) 任务树点任务行 → 检查器滑出（body.inspector-open）
 * 2) 切到用量统计（设置子页）→ 检查器自动收起、body 无 inspector-open
 * 3) localStorage 里 orch.inspector 仍保留选中（不丢内容）
 * 4) 从用量页点子任务进运行详情 → 检查器让位（详情页已铺开全部信息，
 *    同屏不再出现第二份标题/成品/Git），但 S.inspKey 保留
 * 5) 详情页自给任务级数据：meta 条出现「任务累计」pill（side 端点驱动）
 * 6) 点返回 → 检查器保持收起（回新建表单不是检查器上下文）
 * 自含临时服务（端口 18795），mock 任务零配额。
 * 用法：node tests/ui_inspector_ctx.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18795;
const SERVICE = "http://127.0.0.1:" + PORT;
// 可用 TUTTI_TEST_PORT / TUTTI_TEST_CDP 覆盖——9353 与 ui_inspector.mjs 相同，
// 并行跑两测试会互抢 CDP 口、互相驱动对方页面（2026-09-15 踩过）
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9354;
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

    // 1) 顶栏「任务详情」按钮 → 检查器滑出（浏览态快捷预览；点任务行现在主栏直开详情，见 ui_tree）
    await js(`document.getElementById("btn-insp").click(); "ok"`);
    await sleep(900);
    const open1 = await js(`document.body.classList.contains("inspector-open")
      && !document.getElementById("inspector").classList.contains("hidden")`);
    check("顶栏按钮后检查器滑出", open1 === true);
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

    // 3) 进运行详情 → 检查器让位，但选中保留（内嵌步骤层已移除，走 sideOpenRun 直开最近 run）
    await js(`(() => { const k = localStorage.getItem("orch.inspector");
      const lr = (S.state.task_latest || {})[k]; sideOpenRun(lr.id); return "ok"; })()`);
    await sleep(900);
    const yielded = await js(`!document.body.classList.contains("settings-mode")
      && !document.body.classList.contains("inspector-open")
      && document.getElementById("inspector").classList.contains("hidden")
      && !document.getElementById("run-detail").classList.contains("hidden")`);
    check("进运行详情后检查器让位（详情页全功能，不同屏重复）", yielded === true,
      JSON.stringify(await js(`({open:document.body.classList.contains("inspector-open"),
        detailHidden:document.getElementById("run-detail").classList.contains("hidden")})`)));
    const keyKept = await js(`S.inspKey === ${JSON.stringify(String(kept))}`);
    check("让位但 S.inspKey 保留（返回列表可滑回）", keyKept === true, "S.inspKey=" + String(await js("S.inspKey")));

    // 4) 详情页自给任务级数据：meta 条「任务累计」pill（side 端点驱动）
    let tasksum = null;
    for (let i = 0; i < 10 && !tasksum; i++) {
      await sleep(600);
      tasksum = await js(`(() => { const el = document.getElementById("rd-meta-task");
        return (el && !el.classList.contains("hidden")) ? el.textContent : ""; })()`);
      if (tasksum && tasksum.includes("任务累计")) break;
      tasksum = null;
    }
    check("meta 条出现任务累计 pill（side 驱动）", !!tasksum, String(tasksum));
    // 检查器让位后详情页不再有第二份标题：主栏详情标题在、检查器隐藏
    const oneTitle = await js(`!!document.getElementById("rd-title").textContent
      && document.getElementById("inspector").classList.contains("hidden")`);
    check("详情上下文只有主栏一份任务标题", oneTitle === true);

    // 5) 点返回 → 回新建表单，检查器保持收起
    await js(`document.getElementById("btn-back").click(); "ok"`);
    await sleep(900);
    const afterBack = await js(`document.getElementById("run-detail").classList.contains("hidden")
      && !document.body.classList.contains("inspector-open")`);
    check("返回后检查器保持收起（新建表单非检查器上下文）", afterBack === true);

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
    await sleep(400);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = FAIL.length;
  console.log("\n===== 检查器上下文显隐：%d 通过 / %d 失败 =====", PASS.length, bad);
  if (bad) { console.log("失败项：", FAIL); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
