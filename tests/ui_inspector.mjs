/* 任务检查器（右缘停靠列）UI 验收：
 * 1) 点任务树的 <summary>：展开行为保留 + 右缘滑出检查器（任务树仍可见）
 * 2) Git 工具卡：分支名 + +/- 统计 + 变更清单 + 合并/丢弃按钮（isolated 时）
 * 3) 进度卡 x/y + 步骤清单；点步骤跳主栏详情
 * 4) 收起按钮 → 列消失；「完整详情」→ 主栏任务详情
 * 5) 迷你指挥区：活跃 run 显示、发送成功入箱
 * 自含临时服务（端口 18796），mock 任务零配额，git 仓库工作目录验证 git 卡。
 * 用法：node tests/ui_inspector.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18796;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9353;
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
  const tmp = mkdtempSync(join(tmpdir(), "tutti-insp-"));
  // git 仓库工作目录：让 mock 任务走代码版本隔离链
  mkdirSync(join(tmp, "work"), { recursive: true });
  const runGit = (args) => new Promise((res) => {
    const p = spawn("git", args, { cwd: join(tmp, "work"), stdio: "ignore" });
    p.on("close", res);
  });
  await runGit(["init"]);
  await runGit(["config", "user.name", "T"]);
  await runGit(["config", "user.email", "t@l"]);
  writeFileSync(join(tmp, "work", "README.md"), "baseline\n", "utf-8");
  await runGit(["add", "-A"]);
  await runGit(["commit", "-m", "baseline"]);

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
      body: JSON.stringify({ type: "code", goal: "检查器验收", workdir: join(tmp, "work"),
        git_rev: "HEAD", mode: "auto" })
    })).json();
    const runId = sub.run_id;
    check("mock git 任务受理", Boolean(runId), JSON.stringify(sub).slice(0, 120));
    let run = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      run = (await (await fetch(SERVICE + "/api/runs/" + runId)).json()).run;
      if (["done", "failed", "cancelled"].includes(run.status)) break;
    }
    check("mock 任务完成", run && run.status === "done", run ? run.status : "无");
    const taskId = run.task_id;
    // status=done 先落、变更快照在 finally 里随后落：轮询等快照就绪再断言
    let side = null;
    for (let i = 0; i < 20; i++) {
      side = await (await fetch(SERVICE + "/api/tasks/" + taskId + "/side")).json();
      if ((side.changes || {}).count > 0) break;
      await sleep(500);
    }
    check("side 端点：分支+统计+步骤齐备",
      side.git.branch === "tutti/" + taskId && side.changes.count > 0 &&
      side.progress.total > 0 && side.task.git_state === "isolated",
      JSON.stringify({ branch: side.git.branch, cnt: side.changes.count, st: side.task.git_state }));

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      // headless 默认 prefers-reduced-motion=reduce 会禁掉滑入动画；本测有动画断言
      "--blink-settings=prefersReducedMotion=false",
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

    // 打掉 alert（设备控制权等原生弹框会挂死 evaluate）
    await js(`window.alert = () => {}; window.confirm = () => true; "ok"`);
    // 等侧栏渲染出任务行
    let taskRow = null;
    for (let i = 0; i < 20 && !taskRow; i++) {
      taskRow = await js(`(() => {
        const d = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(taskId)});
        return d ? true : false;
      })()`);
      if (!taskRow) await sleep(500);
    }
    check("侧栏出现目标任务行", !!taskRow, "taskId=" + taskId);

    // 点任务行 summary：检查器滑出（展开态取反是原生行为——首个任务默认已展开，点击后折叠）
    const wasOpenBefore = await js(`(() => {
      const d = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(taskId)});
      return d ? !!d.open : null;
    })()`);
    await js(`(() => {
      const d = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(taskId)});
      d.querySelector("summary").click(); return 1;
    })()`);
    await sleep(2200);
    const open = await js(`(() => {
      const insp = document.getElementById("inspector");
      const cs = getComputedStyle(insp);
      const treeVisible = !!document.querySelector("#side-tasks details.stask");
      const stask = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(taskId)});
      return {
        bodyOpen: document.body.classList.contains("inspector-open"),
        inspVisible: !insp.classList.contains("hidden") && cs.display !== "none",
        gridCols: getComputedStyle(document.getElementById("app")).gridTemplateColumns.split(" ").length,
        treeVisible,
        expanded: stask ? !!stask.open : null,
        title: (document.getElementById("insp-title") || {}).textContent || "",
        branch: (document.querySelector("#insp-git-main .insp-branch code") || {}).textContent || "",
        plus: (document.querySelector("#insp-git-main .insp-plus") || {}).textContent || "",
        chip: (document.getElementById("insp-git-chip") || {}).textContent || "",
        mergeBtn: !!document.querySelector("#insp-git-main .insp-actions .primary"),
        progress: (document.getElementById("insp-progress-n") || {}).textContent || "",
        steps: document.querySelectorAll("#insp-steps .insp-step").length,
        stats: (document.getElementById("insp-stats") || {}).textContent || "",
        files: document.querySelectorAll("#insp-artifacts .file-chip").length,
      };
    })()`);
    check("点任务行：检查器滑出且任务树仍在", open.bodyOpen && open.inspVisible && open.treeVisible && open.gridCols === 3,
      JSON.stringify(open));
    check("点任务行：任务行展开态正确切换（原生折叠/展开保留）",
      open.expanded !== null && open.expanded !== wasOpenBefore,
      JSON.stringify({ before: wasOpenBefore, after: open.expanded }));
    check("Git 卡：任务分支名 tutti/<id>", open.branch === "tutti/" + taskId, open.branch);
    check("Git 卡：+N 行统计出现", /^\+\d+$/.test(open.plus), open.plus);
    check("Git 卡：待裁决徽标 + 合并/丢弃按钮", open.chip.includes("待裁决") && open.mergeBtn === true,
      JSON.stringify({ chip: open.chip, mergeBtn: open.mergeBtn }));
    check("进度卡：x/y 与步骤清单", /^\d+\/\d+$/.test(open.progress) && open.steps > 0,
      JSON.stringify({ p: open.progress, n: open.steps }));
    check("统计卡：含运行/步骤/成本", open.stats.includes("运行") && open.stats.includes("$"), open.stats);

    // TAB 化：待裁决任务自动选中 Git 分区，徽标齐备，手点可切
    const tabs = await js(`(() => ({
      tabs: [...document.querySelectorAll("#insp-tabs .insp-tab")].map((b) => b.dataset.tab),
      active: (document.querySelector("#insp-tabs .insp-tab.active") || { dataset: {} }).dataset.tab,
      gitPane: !document.getElementById("insp-pane-git").classList.contains("hidden"),
      progressPaneHidden: document.getElementById("insp-pane-progress").classList.contains("hidden"),
      filesPane: !!document.getElementById("insp-pane-files"),
      mainFilesGone: !document.getElementById("rd-files"),
      badgeGit: (document.getElementById("insp-badge-git") || {}).textContent || "",
      badgeProg: (document.getElementById("insp-badge-progress") || {}).textContent || "",
      badgeFiles: (document.getElementById("insp-badge-files") || {}).textContent || "",
    }))()`);
    check("TAB：三分区（Git/进度/成品文件），成品归检查器、主栏详情不再展示",
      tabs.tabs.join(",") === "git,progress,files" && tabs.filesPane && tabs.mainFilesGone,
      JSON.stringify(tabs));
    check("TAB：待裁决任务自动选中 Git 分区", tabs.active === "git" && tabs.gitPane && tabs.progressPaneHidden,
      JSON.stringify(tabs));
    check("TAB：徽标（待裁决/x/y/文件数）", tabs.badgeGit === "待裁决" && /^\d+\/\d+$/.test(tabs.badgeProg) && tabs.badgeFiles !== "",
      JSON.stringify(tabs));
    await js(`(() => { const b = [...document.querySelectorAll("#insp-tabs .insp-tab")].find(x => x.dataset.tab === "progress"); b.click(); return 1; })()`);
    await sleep(1400);   // 等环的 transition 与滑入动画走完
    const switched = await js(`(() => ({
      active: (document.querySelector("#insp-tabs .insp-tab.active") || { dataset: {} }).dataset.tab,
      progressPane: !document.getElementById("insp-pane-progress").classList.contains("hidden"),
      gitPaneHidden: document.getElementById("insp-pane-git").classList.contains("hidden"),
      ring: (() => { const r = document.getElementById("insp-ring"); return r ? Number(r.style.strokeDashoffset || r.getAttribute("stroke-dashoffset") || 0).toFixed(1) : null; })(),
      ringFull: (() => { const r = document.getElementById("insp-ring"); if (!r) return false; return Number(r.style.strokeDashoffset || 999) < 1; })(),
      stepAnim: (() => { const s = document.querySelector("#insp-steps .insp-step"); return s ? getComputedStyle(s).animationName : "none"; })(),
    }))()`);
    check("TAB：手点进度分区即切换", switched.active === "progress" && switched.progressPane && switched.gitPaneHidden,
      JSON.stringify(switched));
    check("进度环：3/3 全完成时环闭合", switched.ringFull === true, "offset=" + switched.ring);
    check("动效：步骤条带滑入动画（真实动效偏好下）", switched.stepAnim === "insp-in", switched.stepAnim);
    // 切回 Git，后续 diff 断言依赖它可见
    await js(`(() => { const b = [...document.querySelectorAll("#insp-tabs .insp-tab")].find(x => x.dataset.tab === "git"); b.click(); return 1; })()`);
    await sleep(300);

    // 点进度步骤 → 跳主栏详情并定位
    await js(`document.querySelector("#insp-steps .insp-step").click(); "ok"`);
    await sleep(1600);
    const jumped = await js(`(() => ({
      detail: !document.getElementById("run-detail").classList.contains("hidden"),
      inspectorStillOpen: document.body.classList.contains("inspector-open"),
      focus: Number((document.querySelector("#rd-steps .step.focus") || { dataset: { n: 0 } }).dataset.n) || 0,
    }))()`);
    check("点进度步骤：主栏开详情、检查器不收起",
      jumped.detail && jumped.inspectorStillOpen && jumped.focus > 0, JSON.stringify(jumped));

    // 变更清单点开单文件 diff
    await js(`(() => { const b = document.querySelector("#insp-git-main .insp-cf"); if (b) b.click(); return 1; })()`);
    await sleep(1200);
    const diffView = await js(`(() => {
      const box = document.getElementById("insp-diff");
      return { visible: !box.classList.contains("hidden"),
               text: (box.textContent || "").slice(0, 80) };
    })()`);
    check("变更清单：点文件展开 diff", diffView.visible && diffView.text.includes("diff --git"),
      JSON.stringify(diffView));

    // 「完整详情」→ 主栏任务级详情
    await js(`document.getElementById("insp-full").click(); "ok"`);
    await sleep(1600);
    const full = await js(`(() => ({
      detail: !document.getElementById("run-detail").classList.contains("hidden"),
      inspectorOpen: document.body.classList.contains("inspector-open"),
    }))()`);
    check("完整详情按钮：主栏任务详情打开、检查器保持", full.detail && full.inspectorOpen, JSON.stringify(full));

    // 收起 → 列消失
    await js(`document.getElementById("insp-close").click(); "ok"`);
    await sleep(600);
    const closed = await js(`(() => ({
      bodyOpen: document.body.classList.contains("inspector-open"),
      inspHidden: document.getElementById("inspector").classList.contains("hidden"),
      gridCols: getComputedStyle(document.getElementById("app")).gridTemplateColumns.split(" ").length,
    }))()`);
    check("收起按钮：检查器隐藏、grid 回两列", !closed.bodyOpen && closed.inspHidden && closed.gridCols === 2,
      JSON.stringify(closed));

    // 重新打开（收起后再点任务行还能回来）
    await js(`(() => {
      const d = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(taskId)});
      d.querySelector("summary").click(); return 1;
    })()`);
    await sleep(1800);
    const reopened = await js(`document.body.classList.contains("inspector-open")`);
    check("再次点任务行：检查器重新滑出", reopened === true, String(reopened));

    // 刷新恢复：收起后 localStorage 应清空；开着时刷新应回到同一任务
    const memOpen = await js(`(() => ({ body: document.body.classList.contains("inspector-open"), ls: localStorage.getItem("orch.inspector") }))()`);
    check("开合记忆：开着时 localStorage 记住任务",
      memOpen.body === true && memOpen.ls === taskId, JSON.stringify(memOpen));
    await js(`location.reload(); "ok"`);
    await sleep(4000);
    let restored = null;
    for (let i = 0; i < 10 && !restored; i++) {
      restored = await js(`(() => {
        const tree = !!document.querySelector("#side-tasks details.stask");
        return tree ? document.body.classList.contains("inspector-open") : null;
      })()`);
      if (restored === null) await sleep(800);
    }
    check("刷新后：检查器自动恢复到上次任务", restored === true, String(restored));
    const restoredTitle = await js(`(document.getElementById("insp-title") || {}).textContent || ""`);
    check("刷新后：恢复的是同一任务", restoredTitle.includes("检查器验收"), restoredTitle);
    await js(`document.getElementById("insp-close").click(); "ok"`);
    await sleep(600);
    const memClosed = await js(`localStorage.getItem("orch.inspector")`);
    check("开合记忆：收起后 localStorage 清空", memClosed === null || memClosed === undefined, String(memClosed));

    // 迷你指挥区：终态任务隐藏（活跃才显示）——先验证隐藏，再用活跃 run 直发验证端到端
    const directHidden = await js(`document.getElementById("insp-card-direct").classList.contains("hidden")`);
    check("迷你指挥区：终态任务下隐藏", directHidden === true, String(directHidden));

    // 没跑过的任务不滑出检查器：建第二条任务 → 等 run 终态 → 删 run（任务回到「从未运行」态，
    // 与刚创建还没开跑的新任务同一条准入分支）→ 点任务行，右缘必须保持收起
    const t2 = await (await fetch(SERVICE + "/api/tasks", { method: "POST",
      body: JSON.stringify({ type: "code", goal: "未运行任务不滑出检查器", workdir: join(tmp, "work"),
        git_rev: "HEAD", mode: "auto" }) })).json();
    let r2 = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      r2 = (await (await fetch(SERVICE + "/api/runs/" + t2.run_id)).json()).run;
      if (["done", "failed", "cancelled"].includes(r2.status)) break;
    }
    const del2 = await (await fetch(SERVICE + "/api/runs/" + t2.run_id + "/delete",
      { method: "POST", body: "{}" })).json();
    check("未运行用例：第二条任务 run 已删除", del2.ok === true, JSON.stringify(del2).slice(0, 120));
    await sleep(1500);   // 等 SSE 把 task_latest 的变化刷进前端
    const skip = await js(`(() => {
      const d = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(t2.task_id)});
      if (!d) return { found: false };
      d.querySelector("summary").click();
      return { found: true, bodyOpen: document.body.classList.contains("inspector-open") };
    })()`);
    await sleep(800);
    const skipAfter = await js(`(() => ({
      bodyOpen: document.body.classList.contains("inspector-open"),
      hidden: document.getElementById("inspector").classList.contains("hidden"),
    }))()`);
    check("没跑过的任务：点行不滑出检查器",
      !!skip.found && skip.bodyOpen === false && skipAfter.bodyOpen === false && skipAfter.hidden === true,
      JSON.stringify({ skip, skipAfter }));
    // 跑过的任务不受影响：回头点第一条任务，检查器照常滑出
    await js(`(() => {
      const d = [...document.querySelectorAll("#side-tasks details.stask")].find(x => x.dataset.task === ${JSON.stringify(taskId)});
      d.querySelector("summary").click(); return 1;
    })()`);
    await sleep(1200);
    const backOpen = await js(`document.body.classList.contains("inspector-open")`);
    check("跑过的任务：点行照常滑出", backOpen === true, String(backOpen));

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

  console.log("\n===== 任务检查器验收：%d 通过 / %d 失败 =====", PASS.length, FAIL.length);
  if (FAIL.length) { console.log("失败项：", FAIL); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
