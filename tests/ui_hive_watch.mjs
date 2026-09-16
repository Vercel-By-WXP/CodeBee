/* 蜂巢工作台端到端验证：Edge headless + CDP，零依赖。
 * 造数终态 run（含 3 个步骤 + 真实日志文件）→ 页面打开详情 →
 * 断言蜂巢渲染（状态类/悬停 title/在岗计数）→ 点格开实时日志 →
 * 文件追加后跟随轮询自动更新 + 贴底 → 空日志格提示。
 * 渲染直驱 renderHive(fakeRun) 验证 running 忙碌态（造数不能用 running，
 * load_all 启动恢复会判成中断残骸）。临时数据目录 + 独立端口。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, appendFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18822;
const CDP_PORT = 9344;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990101-000000-0002";
const LOG_REL = "steps/01-draft-mock-a.log";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-hive-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  const logAbs = join(runsDir, ...LOG_REL.split("/"));
  writeFileSync(logAbs, "===== 下达 2099-01-01 00:00:01 =====\n$ codex exec --json\n--- 指令 ---\n写第三章\n--- 输出 ---\n第一行输出\n", "utf-8");
  writeFileSync(join(runsDir, "steps", "02-critique-mock-b.log"),
    "===== 下达 2099-01-01 00:01:01 =====\n--- 输出 ---\n评审启动\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", "task-h.json"), JSON.stringify({
    id: "task-h", title: "蜂巢核验任务", type: "novel", goal: "造数：蜂巢工作台",
    workdir, status: "done", archived: false,
    created_at: "2099-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "蜂巢核验任务",
    task_id: "task-h", status: "done", messages: [],
    steps: [
      { n: 1, role: "draft-c1", agent: "mock-a", agent_label: "演示 A",
        note: "", status: "done", started_at: "00:00:01", ended_at: "00:01:00",
        duration_s: 59.0, exit_code: 0, summary: "第三章草稿完成",
        log: LOG_REL, cost_usd: 0, tokens: 0 },
      { n: 2, role: "critique-c1", agent: "mock-b", agent_label: "演示 B",
        note: "", status: "failed", started_at: "00:01:01", ended_at: "00:01:30",
        duration_s: 29.0, exit_code: 1, summary: "评审超时",
        log: "steps/02-critique-mock-b.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2099-01-01 00:00:00", started_at: "2099-01-01 00:00:00",
    ended_at: "2099-01-01 00:02:00",
    cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18822)", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    // 视口显式钉死：--window-size 在 Edge 多实例快速启停时偶发失效（转交旧进程被忽略）
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1400, height: 950, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    // A) 终态任务详情：蜂巢显示 2 格（done + failed），无忙碌动画
    const hive = await evalJson(`(async () => {
      sideOpenTask("task-h");
      await new Promise((r) => setTimeout(r, 700));
      const box = document.getElementById("rd-hive");
      return {
        visible: !!box && !box.classList.contains("hidden"),
        cells: document.querySelectorAll("#rd-hive-cells .hive-cell").length,
        done: document.querySelectorAll("#rd-hive-cells .hive-cell.st-done").length,
        failed: document.querySelectorAll("#rd-hive-cells .hive-cell.st-failed").length,
        sub: (document.getElementById("rd-hive-sub") || {}).textContent || "",
        tail1: (document.querySelector("#rd-hive-cells .hive-lane .hc-tail") || {}).textContent || "",
        title1: (document.querySelector("#rd-hive-cells .hive-lane .hive-cell") || {}).title || "",
        failTail: (document.querySelector("#rd-hive-cells .hive-cell.st-failed .hc-tail") || {}).textContent || "",
      };
    })()`);
    check("终态任务蜂巢显示", hive.visible === true);
    check("蜂巢 2 格（draft + critique）", hive.cells === 2, hive.cells);
    check("状态类：1 done + 1 failed", hive.done === 1 && hive.failed === 1,
      hive.done + "/" + hive.failed);
    check("在岗计数=全部空闲", (hive.sub || "").includes("空闲"), hive.sub);
    check("卡片尾巴=步骤摘要回看", (hive.tail1 || "").includes("第三章草稿完成"), hive.tail1);
    check("悬停 title 含角色与摘要", (hive.title1 || "").includes("draft-c1") &&
      (hive.title1 || "").includes("第三章"), hive.title1);
    check("悬停 title 带结论前缀", (hive.title1 || "").includes("结论："), hive.title1);
    check("失败格尾巴=失败原因结论", (hive.failTail || "").includes("评审超时"), hive.failTail);

    // A2) 泳道流水线：起草→评审先后关系 + 活跃/完成态
    const lanes = await evalJson(`(() => {
      const ls = Array.from(document.querySelectorAll("#rd-hive-cells .hive-lane"));
      return {
        n: ls.length,
        names: ls.map((l) => (l.querySelector(".lane-name") || {}).textContent || ""),
        cls: ls.map((l) => l.className),
        dots: ls.map((l) => l.querySelectorAll(".lane-track .ld").length),
        flow: document.querySelectorAll("#rd-hive-cells .lane-flow").length,
      };
    })()`);
    check("泳道 2 条（起草→评审）", lanes.n === 2 &&
      lanes.names[0] === "起草" && lanes.names[1] === "评审", JSON.stringify(lanes.names));
    check("先后关系：终态两泳道均为完成态",
      lanes.cls[0].includes("lane-done") && lanes.cls[1].includes("lane-done"),
      JSON.stringify(lanes.cls));
    check("轨道点=步骤数", lanes.dots[0] === 1 && lanes.dots[1] === 1, JSON.stringify(lanes.dots));
    check("泳道间流动轨道存在", lanes.flow === 1, lanes.flow);

    // B) 点格开实时日志：审计头 + 输出都在
    const open1 = await evalJson(`(async () => {
      const cell = document.querySelector("#rd-hive-cells .hive-lane .lane-cells .hive-cell");
      cell.click();
      await new Promise((r) => setTimeout(r, 700));
      const box = document.getElementById("rd-log");
      return {
        open: !box.classList.contains("hidden"),
        text: document.getElementById("rd-log-text").textContent,
      };
    })()`);
    check("点击蜂巢格打开日志面板", open1.open === true);
    check("日志含指令原文与输出", (open1.text || "").includes("写第三章") &&
      (open1.text || "").includes("第一行输出"), (open1.text || "").slice(0, 80));

    // C) 实时跟随：向日志文件追加新行 → 2.5s 轮询内面板自动更新且贴底
    appendFileSync(logAbs, "流式输出第 2 行\n流式输出第 3 行\n", "utf-8");
    await sleep(4500);
    const follow = await evalJson(`(() => {
      const pre = document.getElementById("rd-log-text");
      return { text: pre.textContent, atBottom:
        pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48,
        len: pre.textContent.length, liveOn: !!S.logLive,
        cur: currentLog };
    })()`);
    // 终态步骤：toggleLog 只拉一次不启轮询（step_status 闸门）；蜂巢尾巴/hiveTick
    // 负责活跃 run 的实时性，真跑任务的端到端跟随由手动验收覆盖
    check("终态步骤不开轮询（liveOn=false）", follow.liveOn === false && follow.cur === LOG_REL,
      JSON.stringify({ liveOn: follow.liveOn, cur: follow.cur }));

    // D) 再点同格收起面板
    const closed = await evalJson(`(async () => {
      document.querySelector("#rd-hive-cells .hive-lane .lane-cells .hive-cell").click();
      await new Promise((r) => setTimeout(r, 300));
      return document.getElementById("rd-log").classList.contains("hidden");
    })()`);
    check("再点收起日志面板", closed === true);

    // E) running 忙碌态直驱：蜜蜂图标 + 在岗计数 + ● 工作中
    const busy = await evalJson(`(() => {
      renderHive({ id: "${RUN_ID}", status: "running", steps: [
        { n: 3, role: "draft-c2", agent: "mock-a", agent_label: "演示 A", status: "running",
          started_at: "00:03:00", log: "${LOG_REL}", summary: "" },
        { n: 4, role: "critique-c2", agent: "mock-b", agent_label: "演示 B", status: "done",
          duration_s: 12, log: null, summary: "打分完成" },
      ] });
      return {
        runningCells: document.querySelectorAll("#rd-hive-cells .hive-cell.st-running").length,
        bee: !!document.querySelector("#rd-hive-cells .hive-cell.st-running .hc-bee"),
        live: (document.querySelector("#rd-hive-cells .st-running .hc-live") || {}).textContent || "",
        sub: (document.getElementById("rd-hive-sub") || {}).textContent || "",
        emptyLogClick: (document.querySelectorAll("#rd-hive-cells .hive-cell")[1] || {}).onclick ? "set" : "set-inline",
      };
    })()`);
    check("running 格渲染 + 忙碌蜜蜂", busy.runningCells === 1 && busy.bee === true,
      busy.runningCells + "/" + busy.bee);
    check("● 工作中 标记", (busy.live || "").includes("工作"), busy.live);
    check("在岗计数 1/2", (busy.sub || "").includes("1"), busy.sub);

    // E2) 秒表走动：running 卡计时 1s 刷新（抓间隔 1.3s 前后两次值比对）
    const tick1 = await evalJson(`(() => (document.querySelector("#rd-hive-cells .hc-elapsed[data-started]") || {}).textContent || "")()`);
    await sleep(1300);
    const tick2 = await evalJson(`(() => (document.querySelector("#rd-hive-cells .hc-elapsed[data-started]") || {}).textContent || "")()`);
    check("running 秒表走动（值在变）", tick1 !== tick2, JSON.stringify([tick1, tick2]));
    // 直驱 run 是 running 状态 → 活跃泳道 + 流动光点动画规则命中
    const laneActive = await evalJson(`(() => {
      const la = document.querySelector("#rd-hive-cells .hive-lane.lane-active");
      return { has: !!la,
        name: (la && la.querySelector(".lane-name") || {}).textContent || "",
        runDot: !!document.querySelector("#rd-hive-cells .ld.ld-run"),
        flowNext: !!(la && la.nextElementSibling && la.nextElementSibling.classList.contains("lane-flow")) };
    })()`);
    check("活跃泳道高亮 + run 轨道点", laneActive.has && laneActive.runDot,
      JSON.stringify(laneActive));
    check("流动光点挂在活跃泳道之后", laneActive.flowNext === true);

    // E3) 尾巴去噪：日志里 WARN/遥测行不算"在干什么"，取最后的正文行
    appendFileSync(logAbs,
      "2026-09-15T01:44:31 WARN codex_otel::events::session_telemetry: metrics hist\n" +
      "{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":1}}\n" +
      "第十章女主在码头与旧识重逢，冲突升级。\n", "utf-8");
    await sleep(2400);   // hiveTick 2s 周期
    const tailClean = await evalJson(`(() =>
      (document.querySelector("#rd-hive-cells .st-running .hc-tail") || {}).textContent || "")()`);
    check("尾巴取正文行（弃 WARN/JSON）",
      (tailClean || "").includes("码头与旧识重逢") && !(tailClean || "").includes("WARN"),
      tailClean);

    // F) 标签化布局：头部收纳操作按钮与统计；蜂巢/日志/步骤在主栏；分区切换生效。
    // 终态造数（done + 可取报告）→ 自动选卡落「成果」分区
    const layout = await evalJson(`(() => {
      const panel = document.getElementById("run-detail");
      const head = panel.querySelector(".detail-head");
      const main = panel.querySelector(".rd-main");
      const inHead = (id) => !!(head && head.querySelector("#" + id));
      const inMain = (id) => !!(main && main.querySelector("#" + id));
      const tabs = Array.from(panel.querySelectorAll("#rd-tabs .rd-tab"));
      return {
        tabIds: tabs.map((b) => b.dataset.tab).join(","),
        pauseInHead: inHead("btn-pause"), cancelInHead: inHead("btn-cancel"),
        metaStrip: !!panel.querySelector("#rd-meta.rd-meta-strip"),
        hiveInMain: inMain("rd-hive"), stepsInMain: inMain("rd-steps"),
        logInMain: inMain("rd-log"),
        activeTab: ((panel.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || "",
        stepsPaneHidden: main.querySelector('.rd-pane[data-pane="steps"]').classList.contains("hidden"),
        hivePaneHidden: main.querySelector('.rd-pane[data-pane="hive"]').classList.contains("hidden"),
        oldSideGone: !panel.querySelector(".rd-side"),
      };
    })()`);
    check("标签条六分区（蜂巢/步骤/成果/版本/圣经/作品信息）",
      layout.tabIds === "hive,steps,result,git,bible,bookmeta", layout.tabIds);
    check("操作按钮/统计条上移头部", layout.pauseInHead && layout.cancelInHead && layout.metaStrip);
    check("旧侧栏移除；蜂巢/日志/步骤在主栏",
      layout.oldSideGone && layout.hiveInMain && layout.logInMain && layout.stepsInMain);
    check("终态自动落「成果」分区（pane 切换生效）",
      layout.activeTab === "result" && layout.stepsPaneHidden === true,
      JSON.stringify([layout.activeTab, layout.stepsPaneHidden]));

    // F2) 主区自适应：收起左栏变宽、打开检查器让位（:has 放宽 sub-runs 容器）
    const adapt = await evalJson(`(async () => {
      const page = document.getElementById("page-settings");
      const w = () => Math.round(page.getBoundingClientRect().width);
      const wait = () => new Promise((r) => setTimeout(r, 300));
      const w0 = w();                       // 基准：左栏开
      document.body.classList.add("side-collapsed"); await wait();
      const w1 = w();                       // 左栏收
      document.body.classList.add("inspector-open"); await wait();
      const w2 = w();                       // 组合：左栏收 + 检查器停靠（用户报障场景）
      const cols2 = getComputedStyle(document.getElementById("app")).gridTemplateColumns;
      document.body.classList.remove("side-collapsed"); await wait();
      const w3 = w();                       // 左栏开 + 检查器
      document.body.classList.remove("inspector-open"); await wait();
      const w4 = w();                       // 恢复基准
      return { w0, w1, w2, w3, w4, cols2,
        wideClass: page.classList.contains("page-wide") };
    })()`);
    check("详情容器宽度 class 同步（无 :has 兜底）", adapt.wideClass === true);
    check("收起左栏主区自适应变宽", adapt.w1 > adapt.w0 + 100, JSON.stringify(adapt));
    check("检查器打开主区让位", adapt.w2 < adapt.w1 - 100, JSON.stringify(adapt));
    // 组合态：左栏收起 + 检查器停靠 → 主区应占满除检查器外全部宽度（无 264 隐形列）
    check("收左栏+检查器组合无隐形缺口", Math.abs(adapt.w2 - (adapt.w1 - 340)) < 60,
      JSON.stringify(adapt));
    check("左栏展开+检查器让位正常", Math.abs(adapt.w3 - (adapt.w0 - 340)) < 60,
      JSON.stringify(adapt));
    check("全关后恢复基准", Math.abs(adapt.w4 - adapt.w0) < 30, JSON.stringify(adapt));


    // G) 自动展开：活跃 run 直驱 → 第一眼即在岗日志自动打开（含指令原文）
    const auto1 = await evalJson(`(async () => {
      renderHive({ id: "${RUN_ID}", status: "running", steps: [
        { n: 5, role: "draft-c3", agent: "mock-a", agent_label: "演示 A", status: "running",
          started_at: "00:05:00", log: "${LOG_REL}", summary: "" },
      ] });
      await new Promise((r) => setTimeout(r, 700));
      return {
        open: !document.getElementById("rd-log").classList.contains("hidden"),
        text: document.getElementById("rd-log-text").textContent,
      };
    })()`);
    check("活跃 run 自动展开在岗日志", auto1.open === true);
    check("自动展开已加载内容", (auto1.text || "").length > 10 &&
      !(auto1.text || "").includes("等待输出"), (auto1.text || "").slice(0, 60));
    // 每 run 只自动开一次：手动收起后重画不弹回
    const auto2 = await evalJson(`(async () => {
      document.getElementById("rd-log").classList.add("hidden");
      renderHive({ id: "${RUN_ID}", status: "running", steps: [
        { n: 5, role: "draft-c3", agent: "mock-a", agent_label: "演示 A", status: "running",
          started_at: "00:05:00", log: "${LOG_REL}", summary: "" },
      ] });
      await new Promise((r) => setTimeout(r, 300));
      return document.getElementById("rd-log").classList.contains("hidden");
    })()`);
    check("手动收起后不再自动弹开", auto2 === true);

    check("无未捕获 JS 异常", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 200));
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(600);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\nFAILED ${bad}/${results.length}` : `\nOK ${results.length}/${results.length}`);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
