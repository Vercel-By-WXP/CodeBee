/* 运行详情页标签化重构 · 布局探针（一次性诊断脚本，不入回归）：
 * 宽屏：头部一行收纳（操作右贴）/统计条/标签条几何/成果分区可见/无横向溢出；
 * 日志抽屉：sticky 贴底、开在视口内；窄屏 390：标题换行、标签条横滚不撑页面；
 * 英文模式：分区标签与区头翻译。Edge headless + CDP，临时数据目录 + 独立端口。
 * 用法：node tests/_probe_rd_layout.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18824;
const CDP_PORT = 9364;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-2000-01-03-000004";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const out = [];
function log(line) { out.push(line); console.log(line); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-rdl-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(runsDir, "steps", "02-draft-mock-a.log"),
    "===== 下达 2000-01-03 00:00:01 =====\n--- 指令 ---\n写第一章\n--- 输出 ---\n第一章正文输出行一\n第二章正文输出行二\n", "utf-8");
  writeFileSync(join(workdir, "第一章.md"), "# 第一章\n\n正文开头。\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", "task-rdl.json"), JSON.stringify({
    id: "task-rdl", title: "布局探针任务", type: "novel", serial: true,
    goal: "造数", workdir, status: "done", archived: false,
    created_at: "2000-01-03 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "布局探针任务",
    task_id: "task-rdl", status: "done", messages: [],
    steps: [
      { n: 1, role: "plan", agent: "mock-p", agent_label: "规划员", status: "done",
        started_at: "00:00:01", ended_at: "00:00:20", duration_s: 19, exit_code: 0,
        summary: "大纲完成", log: "steps/01-plan.log", cost_usd: 0, tokens: 0 },
      { n: 2, role: "draft-c1", agent: "mock-a", agent_label: "写手 A", status: "done",
        started_at: "00:00:21", ended_at: "00:01:00", duration_s: 39, exit_code: 0,
        summary: "第一章完成", log: "steps/02-draft-mock-a.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2000-01-03 00:00:00", started_at: "2000-01-03 00:00:00",
    ended_at: "2000-01-03 00:02:00", cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(EDGE_CANDIDATES.find((p) => true), [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(`${SERVICE}/api/state`)).ok; } catch (e) { /* retry */ }
    }
    log(`service: ${up ? "up" : "DOWN"}`);
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try { target = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()).find((t) => t.type === "page"); }
      catch (e) { /* wait */ }
    }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable"); await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 950, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);
    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };
    const R = (el) => `(() => { const e = ${el}; const r = e.getBoundingClientRect();
      return { t: +r.top.toFixed(0), b: +r.bottom.toFixed(0), l: +r.left.toFixed(0), rt: +r.right.toFixed(0), w: +r.width.toFixed(0), h: +r.height.toFixed(0) }; })()`;

    /* —— 宽屏 1400 —— */
    await evalJson(`(async () => { sideOpenTask("task-rdl"); await new Promise((r) => setTimeout(r, 900)); return 1; })()`);
    const wide = await evalJson(`(() => {
      const head = document.querySelector("#run-detail .detail-head");
      const meta = document.getElementById("rd-meta");
      const tabs = document.getElementById("rd-tabs");
      const acts = head.querySelector(".rd-actions");
      const active = tabs.querySelector(".rd-tab.active");
      const arts = document.getElementById("rd-arts");
      const panel = document.getElementById("run-detail");
      return {
        headActs: ${R("acts")}, panelRight: Math.round(panel.getBoundingClientRect().right),
        metaTop: Math.round(meta.getBoundingClientRect().top),
        headBottom: Math.round(head.getBoundingClientRect().bottom),
        tabsTop: Math.round(tabs.getBoundingClientRect().top),
        activeTab: active ? active.dataset.tab : "",
        activeWeight: active ? getComputedStyle(active).fontWeight : "",
        activeColor: active ? getComputedStyle(active).color : "",
        idleColor: getComputedStyle(tabs.querySelector('.rd-tab:not(.active)')).color,
        visibleTabs: [...tabs.querySelectorAll(".rd-tab:not(.hidden)")].map((b) => b.dataset.tab).join(","),
        artsH: arts.classList.contains("hidden") ? 0 : Math.round(arts.getBoundingClientRect().height),
        docOverflow: document.documentElement.scrollWidth - innerWidth,
        vw: innerWidth,
      };
    })()`);
    log("wide: " + JSON.stringify(wide));
    log(`check 头部操作右贴边: ${Math.abs(wide.headActs.rt - wide.panelRight) < 40 ? "OK" : "FAIL"}`);
    log(`check 统计条在头部下方: ${wide.metaTop >= wide.headBottom ? "OK" : "FAIL"}`);
    log(`check 标签条在统计条下方: ${wide.tabsTop >= wide.metaTop ? "OK" : "FAIL"}`);
    log(`check 默认落成果分区+加粗+强调色: ${
      wide.activeTab === "result" && wide.activeWeight >= 600 && wide.activeColor !== wide.idleColor ? "OK" : "FAIL"}`);
    log(`check 可见分区=蜂巢/步骤/成果/圣经: ${wide.visibleTabs === "hive,steps,result,bible" ? "OK" : "FAIL"}`);
    log(`check 成品区有高度: ${wide.artsH > 30 ? "OK" : "FAIL"}`);
    log(`check 宽屏无横向溢出: ${wide.docOverflow <= 0 ? "OK" : "FAIL"}`);

    /* —— 日志抽屉：切步骤分区，点第 2 步开抽屉 —— */
    const drawer = await evalJson(`(async () => {
      document.querySelector('#rd-tabs .rd-tab[data-tab="steps"]').click();
      await new Promise((r) => setTimeout(r, 200));
      // 任务级详情的步骤行无 data-n（那是 run 级详情的标记）：点第二个 .step（起草步，日志真实存在）
      const steps = [...document.querySelectorAll("#rd-steps .step")];
      const step = steps[1] || steps[0];
      if (step) step.click();
      await new Promise((r) => setTimeout(r, 800));
      const log = document.getElementById("rd-log");
      return {
        open: !log.classList.contains("hidden"),
        pos: getComputedStyle(log).position,
        rect: ${R("log")},
        vh: innerHeight,
        inPaneScope: !!document.querySelector(".rd-main #rd-log"),
      };
    })()`);
    log("drawer: " + JSON.stringify(drawer));
    log(`check 抽屉 sticky 且开在视口内: ${
      drawer.pos === "sticky" && drawer.rect.h > 100 && drawer.rect.b <= drawer.vh + 2 ? "OK" : "FAIL"}`);
    log(`check 抽屉归属主栏容器: ${drawer.inPaneScope ? "OK" : "FAIL"}`);

    /* —— 窄屏 390 —— */
    await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await sleep(400);
    const narrow = await evalJson(`(() => {
      const h2 = document.querySelector("#run-detail .detail-head h2");
      const acts = document.querySelector("#run-detail .rd-actions");
      const tabs = document.getElementById("rd-tabs");
      return {
        h2OwnLine: Math.round(h2.getBoundingClientRect().width) <= 350,
        actsBelowTitle: acts.getBoundingClientRect().top >= h2.getBoundingClientRect().bottom - 2,
        tabsScrollable: tabs.scrollWidth - tabs.clientWidth,
        docOverflow: document.documentElement.scrollWidth - innerWidth,
        vw: innerWidth,
      };
    })()`);
    log("narrow: " + JSON.stringify(narrow));
    log(`check 窄屏标题独占一行、操作折到下方: ${narrow.h2OwnLine && narrow.actsBelowTitle ? "OK" : "FAIL"}`);
    log(`check 窄屏标签条横滚不撑破页面: ${narrow.docOverflow <= 0 ? "OK" : "FAIL"}`);

    /* —— 英文模式 —— */
    await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 950, deviceScaleFactor: 1, mobile: false });
    const en = await evalJson(`(async () => {
      localStorage.setItem("orch.lang", "en");
      applyI18n();
      await new Promise((r) => setTimeout(r, 300));
      const tabs = [...document.querySelectorAll("#rd-tabs .rd-tab:not(.hidden)")].map((b) => b.textContent.trim());
      const secTitle = (document.querySelector("#rd-hive .sec-title span") || {}).textContent || "";
      const back = (document.getElementById("btn-back") || {}).textContent.trim() || "";
      localStorage.setItem("orch.lang", "zh"); applyI18n();
      return { tabs: tabs.join(","), secTitle, back };
    })()`);
    log("en: " + JSON.stringify(en));
    // 标签文本含徽章计数（Steps2 = Steps + 徽章 2），按前缀匹配
    log(`check 分区标签英文 Hive/Steps/Results/Bible: ${
      /^Hive$/.test(en.tabs.split(",")[0]) && /^Steps\d*$/.test(en.tabs.split(",")[1]) &&
      /^Results\d*$/.test(en.tabs.split(",")[2]) && /^Bible$/.test(en.tabs.split(",")[3]) ? "OK" : "FAIL"}`);
    log(`check 区头/返回英文: ${en.secTitle === "Hive workbench" && en.back.startsWith("Back") ? "OK" : "FAIL"}`);
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(600);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const fails = out.filter((l) => l.includes("FAIL")).length;
  console.log(fails ? `\n${fails} 项需处理` : "\n全部 OK");
  process.exit(fails ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
