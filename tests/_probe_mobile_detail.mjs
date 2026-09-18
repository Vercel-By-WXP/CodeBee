/* 手机端「任务详情打不开」复现：440×956（用户 DevTools 模拟口径）触控视口下
 * sideOpenTask 真实路径，dump 详情页各分区几何 + 控制台错误。一次性诊断脚本。
 * 跑法：node tests/_probe_mobile_detail.mjs   （服务 18867 / CDP 9378，防并行撞车） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18867;
const CDP_PORT = 9378;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990101-000000-0003";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-mbd-"));
  const runFail = process.env.TUTTI_TEST_RUNFAIL === "1";   // 失败态运行：报告区应给可见提示而非空白
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(dataDir, "tasks", "task-a.json"), JSON.stringify({
    id: "task-a", title: "手机详情复现任务", type: "novel", goal: "造数",
    workdir, status: runFail ? "failed" : "done", archived: false,
    created_at: "2099-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "手机详情复现任务",
    task_id: "task-a", status: runFail ? "failed" : "done",
    report: runFail ? null : "report.md",
    steps: [{ n: 1, role: "draft-c1", agent: "mock-a", agent_label: "A",
              status: runFail ? "failed" : "done",
              started_at: "00:00:01", log: "steps/01.log",
              summary: runFail ? "评审没过" : "完成起草", cost_usd: 0, tokens: 0 }],
    messages: [], created_at: "2099-01-01 00:00:00", started_at: "00:00:01",
    ended_at: "00:00:20", cost_usd: 0, tokens: 0, error: runFail ? "评审未通过" : "",
    verdict: null, summary: runFail ? "未完成" : "写完一章",
  }, null, 2));
  writeFileSync(join(runsDir, "steps", "01.log"), "输出行\n", "utf-8");
  if (!runFail)
    writeFileSync(join(runsDir, "report.md"), "# 报告标题\n\n正文第一段。\n\n## 小节\n\n内容内容。\n", "utf-8");

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edge = spawn(EDGE_CANDIDATES[0], [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${mkdtempSync(join(tmpdir(), "tutti-cdp-"))}`,
    `--remote-debugging-port=${CDP_PORT}`,
    "--blink-settings=prefersReducedMotion=false",
    "--window-size=460,980", "about:blank",
  ], { stdio: "ignore" });
  const profDir = edge.spawnargs.find((a) => a.startsWith("--user-data-dir=")).slice(17);
  const errors = [];
  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(`${SERVICE}/api/state`)).ok; } catch (e) {}
    }
    if (!up) throw new Error("service not up");
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
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.consoleAPICalled" &&
          ["error", "warning"].includes(m.params.type)) {
        errors.push(m.params.type + ": " +
          m.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300));
      }
      if (m.method === "Runtime.exceptionThrown")
        errors.push("exc: " + JSON.stringify(m.params.exceptionDetails).slice(0, 300));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 440, height: 956, deviceScaleFactor: 3, mobile: true });
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    await send("Emulation.setEmulatedMedia", { features: [
      { name: "hover", value: "none" }, { name: "pointer", value: "coarse" },
    ]});
    await send("Page.navigate", { url: SERVICE });
    await sleep(3000);
    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails)
        return { __err: JSON.stringify(r.result.exceptionDetails).slice(0, 300) };
      return r.result ? r.result.result.value : undefined;
    };
    await evalJson(`(async () => {
      try { await api("/api/control", { method: "POST", body: JSON.stringify({ action: "acquire" }) }); } catch (e) {}
      try { if (typeof welcomeClose === "function") welcomeClose(); } catch (e) {}
      return 1;
    })()`);
    // 用户真实路径：点侧栏抽屉里的任务行（.stask 本体，别点外层 details 文件夹）
    const tapped = await evalJson(`(() => {
      const row = document.querySelector('#side-tasks .stask[data-task="task-a"]');
      if (!row) return { found: false };
      row.click();
      return { found: true, tag: row.tagName, cls: row.className.slice(0, 60) };
    })()`);
    console.log("tap task row:", JSON.stringify(tapped));
    await sleep(2500);

    const geo = await evalJson(`(() => {
      const vis = (el) => {
        if (!el) return null;
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return { disp: cs.display, x: Math.round(r.x), y: Math.round(r.y),
                 w: Math.round(r.width), h: Math.round(r.height) };
      };
      const g = (id) => vis(document.getElementById(id));
      const q = (sel) => vis(document.querySelector(sel));
      const tabs = Array.from(document.querySelectorAll("#rd-tabs .rd-tab")).map((b) => ({
        t: b.dataset.tab, hidden: b.classList.contains("hidden"), ...vis(b) }));
      return {
        bodyCls: document.body.className,
        vw: document.documentElement.clientWidth,
        vh: document.documentElement.clientHeight,
        scrollW: document.documentElement.scrollWidth,
        runDetail: g("run-detail"), rail: g("rd-rail"), railcol: g("rd-railcol"),
        rdMain: q(".rd-main"), tabs: g("rd-tabs"),
        paneHive: g("rd-pane-hive"), paneSteps: g("rd-pane-steps"), paneResult: g("rd-pane-result"),
        steps: g("rd-steps"), report: g("rd-report"),
        rdTab: (window.S || {}).rdTab,
        hiveHidden: document.getElementById("rd-hive").classList.contains("hidden"),
        hiveHtml: (document.getElementById("rd-hive") || {}).innerHTML?.length || 0,
        tabList: tabs,
        gridCols: getComputedStyle(document.getElementById("run-detail")).gridTemplateColumns,
        reportHtml: (document.getElementById("rd-report") || {}).innerHTML?.slice(0, 120) || null,
        stepsHtml: (document.getElementById("rd-steps") || {}).innerHTML?.slice(0, 120) || null,
      };
    })()`);
    console.log(JSON.stringify(geo, null, 1));

    // 点成果页签：报告内容是否可达；点步骤页签：步骤列表非空；任意时刻恰好一个分区可见
    const clickTab = async (tb) => {
      await evalJson(`(() => {
        const b = document.querySelector('#rd-tabs .rd-tab[data-tab="${tb}"]');
        if (b && !b.classList.contains("hidden")) b.click(); return 1;
      })()`);
      await sleep(600);
    };
    const paneState = () => evalJson(`(() => {
      const panes = Array.from(document.querySelectorAll("#run-detail .rd-pane"));
      const shown = panes.filter((p) => !p.classList.contains("hidden") &&
        getComputedStyle(p).display !== "none");
      const r = document.getElementById("rd-report");
      const rp = r.getBoundingClientRect();
      return { shown: shown.map((p) => p.dataset.pane),
               reportText: (r.textContent || "").trim().slice(0, 50),
               reportH: Math.round(rp.height),
               inViewport: rp.top < window.innerHeight && rp.height > 0 };
    })()`);
    let fails = 0;
    const chk = (ok, name, extra) => {
      if (!ok) fails++;
      console.log(`[${ok ? "ok  " : "FAIL"}] ${name}` + (extra ? " " + extra : ""));
    };

    await clickTab("steps");
    const afterSteps = await evalJson(`(() => {
      const box = document.getElementById("rd-steps");
      return { n: box.querySelectorAll(".step").length,
               text: (box.textContent || "").trim().slice(0, 40) };
    })()`);
    chk(afterSteps.n > 0, "步骤页签可达", "rows=" + afterSteps.n + " «" + afterSteps.text + "»");

    await clickTab("result");
    const after = await paneState();
    const wantText = runFail ? "没有生成报告" : "报告标题";
    chk(after.reportText.includes(wantText) && after.reportH > 10 && after.inViewport,
        runFail ? "失败态报告区给可见提示（非空白）" : "完成态报告区有真内容",
        "«" + after.reportText + "» h=" + after.reportH);
    chk(after.shown.length === 1 && after.shown[0] === "result",
        "成果分区独占可见", JSON.stringify(after.shown));
    console.log("console:", errors.length ? errors.slice(0, 6) : "clean");
    console.log(fails === 0 ? "MOBILE-DETAIL: PASS" : `MOBILE-DETAIL: ${fails} FAIL`);
    if (fails) process.exitCode = 1;
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(500);
    try { rmSync(profDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
