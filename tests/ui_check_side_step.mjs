/* 侧栏点子任务 → 主栏直接展示运行详情（不跳设置页）验收：
 * 1) 点 .stepx 后 body 仍无 settings-mode（左栏还是任务树）
 * 2) #sub-runs 可见且 #run-detail 展开、标题有内容
 * 3) 点「返回」回任务页（不露出运行列表）
 * 自含临时服务（端口 18797），mock 任务零配额。
 * 用法：node tests/ui_check_side_step.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18797;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9352;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-side-"));
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

    // 禁用全部真实 CLI（默认目录里部分 CLI 默认参与编排），保证 mock 零配额
    const state = await (await fetch(SERVICE + "/api/state")).json();
    for (const a of state.agents.filter((a) => a.mode === "real")) {
      await fetch(SERVICE + "/api/orchestration", { method: "POST",
        body: JSON.stringify({ agent_id: a.id, enabled: false }) });
    }
    // 提交 mock 任务
    const sub = await (await fetch(SERVICE + "/api/tasks", { method: "POST",
      body: JSON.stringify({ type: "doc", goal: "侧栏子任务详情验收", workdir: join(tmp, "work"), mode: "auto" })
    })).json();
    const runId = sub.run_id;
    check("mock 任务受理", Boolean(runId), JSON.stringify(sub).slice(0, 120));
    let run = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      run = (await (await fetch(SERVICE + "/api/runs/" + runId)).json()).run;
      if (["done", "failed", "cancelled"].includes(run.status)) break;
    }
    check("mock 任务完成（有步骤可点）", run && run.status === "done" && (run.steps || []).length > 0,
      run ? run.status + " steps=" + (run.steps || []).length : "无");

    // ===== 默认保存路径：不传 workdir 的任务落到设置里的默认目录 =====
    const wsDir = join(tmp, "默认保存");
    const dw = await (await fetch(SERVICE + "/api/settings/default-workdir", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: wsDir }) })).json();
    const normP = (s) => String(s || "").replace(/\//g, "\\").toLowerCase();
    check("设置默认保存路径", dw.ok === true && normP(dw.settings.default_workdir_effective) === normP(wsDir),
      JSON.stringify({ got: dw.settings?.default_workdir_effective, want: wsDir }).slice(0, 200));
    const sub2 = await (await fetch(SERVICE + "/api/tasks", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "doc", goal: "缺省目录任务", mode: "auto" }) })).json();
    check("不传 workdir 的任务受理", Boolean(sub2.run_id), JSON.stringify(sub2).slice(0, 120));
    let run2f = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      run2f = (await (await fetch(SERVICE + "/api/runs/" + sub2.run_id)).json()).run;
      if (["done", "failed", "cancelled"].includes(run2f.status)) break;
    }
    const fl = await (await fetch(SERVICE + "/api/runs/" + sub2.run_id + "/files")).json();
    check("缺省目录任务的成品落在默认路径", fl.workdir === wsDir && (fl.files || []).length > 0,
      JSON.stringify({ workdir: fl.workdir, n: (fl.files || []).length }).slice(0, 150));
    const fa = await (await fetch(SERVICE + "/api/runs/" + sub2.run_id + "/file?name=" +
      encodeURIComponent(fl.files?.[0]?.name || "无"))).text();
    check("成品文件可读取（非空内容）", fl.files?.length && fa.length > 0, "len=" + fa.length);

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
    let seq = 0; const pending = new Map(); const pageErrors = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.exceptionThrown")
        pageErrors.push(String(m.params.exceptionDetails.exception?.description
          || m.params.exceptionDetails.text).slice(0, 200));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4500);
    console.log("served app.js 版本:", await js(
      `fetch("app.js").then(r => r.text()).then(t => (t.includes("sideOpenTask") ? "新" : "旧") + "-" + (t.includes("applyStepFocus") ? "有焦点" : "无焦点"))`));

    // 侧栏出现任务组与子步骤（SSE/轮询刷新）
    let stepCount = 0;
    for (let i = 0; i < 20; i++) {
      stepCount = await js(`document.querySelectorAll("#side-tasks .stepx").length`) || 0;
      if (stepCount > 0) break;
      await sleep(500);
    }
    check("侧栏出现子步骤条目", stepCount > 0, "stepx=" + stepCount);

    // 点第一个子任务
    await js(`document.querySelector("#side-tasks .stepx").click(); "ok"`);
    await sleep(1500);
    const after = await js(`(() => ({
      settingsMode: document.body.classList.contains("settings-mode"),
      subRunsVisible: !document.getElementById("sub-runs").classList.contains("hidden"),
      detailVisible: !document.getElementById("run-detail").classList.contains("hidden"),
      listHidden: document.querySelector("#sub-runs .panel:first-child").classList.contains("hidden"),
      title: (document.getElementById("rd-title") || {}).textContent || "",
      sideMainVisible: getComputedStyle(document.querySelector("#sidebar .side-main")).display !== "none",
      pageTitle: (document.getElementById("page-title") || {}).textContent || "",
    }))()`);
    check("点击后：左栏仍是任务树（未进设置模式）", after.settingsMode === false && after.sideMainVisible === true,
      JSON.stringify(after));
    check("点击后：主栏直接展示运行详情", after.subRunsVisible && after.detailVisible && after.listHidden,
      JSON.stringify(after));
    check("点击后：详情标题有内容且页面标题为运行详情",
      after.title.trim().length > 0 && after.pageTitle === "运行详情",
      "rd=" + after.title + " page=" + after.pageTitle);
    // 成品文件已移至右侧检查器「成品文件」分区：主栏详情页不再有 rd-files 区块
    await sleep(1500);
    const filesUi = await js(`(() => {
      const main = document.getElementById("rd-files");
      const box = document.getElementById("insp-artifacts");
      return { mainGone: !main, inspectorBox: !!box,
        reportVisible: !!document.getElementById("rd-report") };
    })()`);
    check("点击后：主栏无成品区块，成品容器在检查器",
      filesUi.mainGone === true && filesUi.inspectorBox === true && filesUi.reportVisible === true,
      JSON.stringify(filesUi));

    // 点不同子任务 → 定位到不同步骤（滚动 + 高亮 + 展开日志），内容不再千篇一律
    const pick = await js(`(() => {
      const xs = [...document.querySelectorAll("#side-tasks .stepx")];
      return xs.slice(0, 2).map(x => Number(x.dataset.n) || 0);
    })()`);
    check("侧栏至少两个子任务可对比", pick.length === 2 && pick[0] > 0 && pick[1] > 0 && pick[0] !== pick[1],
      JSON.stringify(pick));
    const focusOf = async (n) => {
      await js(`sideOpenRun(${JSON.stringify(runId)}, ${n}); "ok"`);
      await sleep(1600);
      return js(`(() => {
        const f = document.querySelector("#rd-steps .step.focus");   // 持久高亮态
        const logBox = document.getElementById("rd-log");
        return { focusN: f ? Number(f.dataset.n) : 0,
                 logOpen: !logBox.classList.contains("hidden"),
                 scrolled: (() => { const r = f ? f.getBoundingClientRect() : null;
                   return r ? (r.top >= 0 && r.bottom <= innerHeight + 40) : false; })() };
      })()`);
    };
    const f1 = await focusOf(pick[0]);
    check("点子任务 A：高亮定位到步骤 A", f1.focusN === pick[0], JSON.stringify(f1));
    const f2 = await focusOf(pick[1]);
    check("点子任务 B：高亮切换到步骤 B（内容不同）", f2.focusN === pick[1] && f2.focusN !== f1.focusN,
      JSON.stringify(f2));
    check("定位的步骤在视口内（滚动生效）", f2.scrolled === true, JSON.stringify(f2));

    // 点「返回」：回任务页，不露出运行列表
    await js(`document.getElementById("btn-back").click(); "ok"`);
    await sleep(800);
    const back = await js(`(() => ({
      settingsMode: document.body.classList.contains("settings-mode"),
      subTasksVisible: !document.getElementById("sub-tasks").classList.contains("hidden"),
      detailHidden: document.getElementById("run-detail").classList.contains("hidden"),
      pageTitle: (document.getElementById("page-title") || {}).textContent || "",
    }))()`);
    check("返回后：回到任务页（详情收起、不露运行列表）",
      back.settingsMode === false && back.subTasksVisible && back.detailHidden && back.pageTitle === "任务",
      JSON.stringify(back));

    // 回归：设置页里的 openRunInRuns 路径仍走设置模式
    await js(`openRunInRuns(${JSON.stringify(runId)}); "ok"`);
    await sleep(1000);
    const inSettings = await js(`(() => ({
      settingsMode: document.body.classList.contains("settings-mode"),
      detailVisible: !document.getElementById("run-detail").classList.contains("hidden"),
    }))()`);
    check("回归：管理面板「完整运行」仍进设置模式详情",
      inSettings.settingsMode === true && inSettings.detailVisible === true, JSON.stringify(inSettings));

    // ===== 任务级详情（「查看全部 N 步」）：重试一次造第 2 条 run → 聚合两跑全部步骤 =====
    const runInfo = (await (await fetch(SERVICE + "/api/runs/" + runId)).json()).run;
    const ret = await (await fetch(SERVICE + "/api/tasks/" + encodeURIComponent(runInfo.task_id) + "/retry",
      { method: "POST" })).json();
    const run2 = ret.run_id;
    check("重试受理（产生第 2 条 run）", Boolean(run2), JSON.stringify(ret).slice(0, 120));
    let run2st = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      run2st = (await (await fetch(SERVICE + "/api/runs/" + run2)).json()).run;
      if (["done", "failed", "cancelled"].includes(run2st.status)) break;
    }
    check("第 2 条 run 完成", run2st && run2st.status === "done", run2st ? run2st.status : "无");
    const expectSteps = (runInfo.steps || []).length + (run2st.steps || []).length;

    // 等 SSE/轮询把侧栏刷出 smore（12 步 > 8 才显示）；先整页刷新确保拿到最新 state
    await js(`location.reload(); "ok"`);
    await sleep(4000);
    let smore = null;
    for (let i = 0; i < 20; i++) {
      smore = await js(`(() => {
        const s = [...document.querySelectorAll("#side-tasks .smore")].find(x => x.textContent.includes("查看全部"));
        return s ? s.textContent.trim() : "";
      })()`);
      if (smore) break;
      await sleep(500);
    }
    check("侧栏出现「查看全部 2 次运行 · N 步」",
      smore.includes("查看全部") && smore.includes("2 次运行") && smore.includes(expectSteps + " 步"),
      "smore=" + smore);

    await js(`(() => {
      const s = [...document.querySelectorAll("#side-tasks .smore")].find(x => x.textContent.includes("查看全部"));
      s.click(); return 1;
    })()`);
    await sleep(1800);
    const taskView = await js(`(() => ({
      settingsMode: document.body.classList.contains("settings-mode"),
      detailVisible: !document.getElementById("run-detail").classList.contains("hidden"),
      steps: document.querySelectorAll("#rd-steps .step").length,
      seps: document.querySelectorAll("#rd-steps .run-sep").length,
      meta: (document.getElementById("rd-meta") || {}).textContent || "",
      title: (document.getElementById("rd-title") || {}).textContent || "",
    }))()`);
    check("查看全部：主栏直开任务级详情（未进设置模式）",
      taskView.settingsMode === false && taskView.detailVisible === true, JSON.stringify(taskView).slice(0, 180));
    check("查看全部：步骤数 = 各次运行之和（" + expectSteps + "）", taskView.steps === expectSteps,
      "实际=" + taskView.steps);
    check("查看全部：运行分隔条 = 运行次数（2）", taskView.seps === 2, "seps=" + taskView.seps);
    check("查看全部：汇总行含运行次数与总步数",
      /运行\s*2\s*次/.test(taskView.meta.replace(/\s+/g, " ")) && taskView.meta.includes(String(expectSteps)),
      taskView.meta.slice(0, 120));

    // 返回仍回任务页
    await js(`document.getElementById("btn-back").click(); "ok"`);
    await sleep(600);
    const back2 = await js(`(() => ({
      subTasksVisible: !document.getElementById("sub-tasks").classList.contains("hidden"),
      detailHidden: document.getElementById("run-detail").classList.contains("hidden"),
    }))()`);
    check("查看全部：返回回任务页", back2.subTasksVisible && back2.detailHidden, JSON.stringify(back2));
    console.log("页面异常:", pageErrors.length ? pageErrors.join(" || ") : "无");

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    if (svc?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  console.log("\n===== 侧栏子任务详情验收：%d 通过 / %d 失败 =====", PASS.length, FAIL.length);
  if (FAIL.length) { console.log("失败项：", FAIL); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
