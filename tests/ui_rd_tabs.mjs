/* 运行详情页标签化重构验收：Edge headless + CDP，零依赖。
 * 造数：终态任务（3 步骤 + 日志 + 成品 md + 连载圣经）→
 * 断言：标签条分区可用性 / 终态自动落「成果」/ 徽章 / 手点钉住不被自动选卡抢 /
 * 日志抽屉跨分区 / sideOpenRun 钉步骤分区并聚焦。临时数据目录 + 独立端口。
 * 用法：node tests/ui_rd_tabs.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18823;
const CDP_PORT = 9356;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990102-000000-0003";
const LOG_REL = "steps/02-draft-mock-a.log";
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-rdt-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(runsDir, ...LOG_REL.split("/")),
    "===== 下达 2099-01-02 00:00:01 =====\n--- 指令 ---\n写第一章\n--- 输出 ---\n第一章正文输出行\n", "utf-8");
  writeFileSync(join(runsDir, "steps", "01-plan-mock-p.log"),
    "===== 下达 2099-01-02 00:00:01 =====\n--- 输出 ---\n大纲：三步走完成\n", "utf-8");
  writeFileSync(join(workdir, "第一章.md"), "# 第一章\n\n正文开头。\n", "utf-8");
  writeFileSync(join(workdir, "story-bible.md"), "# 故事圣经\n\n女主：阿禾。\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", "task-rdt.json"), JSON.stringify({
    id: "task-rdt", title: "标签分区验收任务", type: "novel", serial: true,
    goal: "造数：详情页标签分区", workdir, status: "done", archived: false,
    // 造数时间取过去：成品扫描按「晚于任务首跑」过滤 mtime，未来时间会把刚写的文件全排除
    created_at: "2000-01-02 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "标签分区验收任务",
    task_id: "task-rdt", status: "done", messages: [],
    steps: [
      { n: 1, role: "plan", agent: "mock-p", agent_label: "规划员", note: "", status: "done",
        started_at: "00:00:01", ended_at: "00:00:20", duration_s: 19, exit_code: 0,
        summary: "大纲三步走", log: "steps/01-plan-mock-p.log", cost_usd: 0, tokens: 0 },
      { n: 2, role: "draft-c1", agent: "mock-a", agent_label: "写手 A", note: "", status: "done",
        started_at: "00:00:21", ended_at: "00:01:00", duration_s: 39, exit_code: 0,
        summary: "第一章草稿完成", log: LOG_REL, cost_usd: 0, tokens: 0 },
      { n: 3, role: "critique-c1", agent: "mock-b", agent_label: "评审 B", note: "", status: "done",
        started_at: "00:01:01", ended_at: "00:01:30", duration_s: 29, exit_code: 0,
        summary: "评审通过", log: "steps/03-critique-mock-b.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2000-01-02 00:00:00", started_at: "2000-01-02 00:00:00",
    ended_at: "2000-01-02 00:02:00",
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
    check("临时服务就绪(18823)", up);

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
    const activeTab = `((document.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || ""`;
    const paneHidden = (name) =>
      `document.querySelector('.rd-pane[data-pane="${name}"]').classList.contains("hidden")`;

    // A) 打开任务级详情：分区可用性（无 git → 版本隐藏；连载 → 圣经在场）
    const strip = await evalJson(`(async () => {
      sideOpenTask("task-rdt");
      await new Promise((r) => setTimeout(r, 900));
      const tabs = Array.from(document.querySelectorAll("#rd-tabs .rd-tab"));
      return {
        ids: tabs.map((b) => b.dataset.tab).join(","),
        hiddenIds: tabs.filter((b) => b.classList.contains("hidden")).map((b) => b.dataset.tab).join(","),
        active: ${activeTab},
        stepsBadge: (document.querySelector('#rd-tabs .rd-tab[data-tab="steps"] .rd-badge') || {}).textContent || "",
        resultBadge: (document.querySelector('#rd-tabs .rd-tab[data-tab="result"] .rd-badge') || {}).textContent || "",
        artsChips: document.querySelectorAll("#rd-arts .file-chip:not(.prev)").length,
        artsInResult: !!document.querySelector('.rd-pane[data-pane="result"] #rd-arts'),
      };
    })()`);
    check("分区条：蜂巢/步骤/成果/圣经在场，版本（无 git）隐藏",
      ["hive", "steps", "result", "git", "bible"].every((k) => strip.ids.includes(k)) &&
      strip.hiddenIds === "git",
      JSON.stringify([strip.ids, strip.hiddenIds]));
    check("终态（done）自动落「成果」分区", strip.active === "result", strip.active);
    check("成果分区含成品文件（章节 + 圣经）且在 result pane 内",
      strip.artsChips === 2 && strip.artsInResult === true, JSON.stringify(strip));
    check("徽章：步骤=3 · 成果=文件数",
      strip.stepsBadge === "3" && strip.resultBadge === "2",
      JSON.stringify([strip.stepsBadge, strip.resultBadge]));

    // B) 手点钉住：切到蜂巢后，同任务状态重算（签名未变/已钉）不被自动选卡抢走
    const pin = await evalJson(`(async () => {
      document.querySelector('#rd-tabs .rd-tab[data-tab="hive"]').click();
      await new Promise((r) => setTimeout(r, 200));
      rdTabsSync({ running: false, status: "done", gitState: "", steps: 3,
        runningCount: 0, hasResult: true });   // 轮询重画会带 ctx 再进来
      await new Promise((r) => setTimeout(r, 100));
      return { active: ${activeTab}, bibleVisible:
        !document.querySelector('.rd-pane[data-pane="bible"]').classList.contains("hidden") };
    })()`);
    check("手点蜂巢后重算不抢（钉住生效）", pin.active === "hive", pin.active);

    // C) 日志抽屉：蜂巢格点开日志；切到步骤分区抽屉仍在（跨分区）
    const drawer = await evalJson(`(async () => {
      const cell = document.querySelector("#rd-hive-cells .hive-cell");
      if (cell) cell.click();
      await new Promise((r) => setTimeout(r, 700));
      const openOnHive = !document.getElementById("rd-log").classList.contains("hidden");
      document.querySelector('#rd-tabs .rd-tab[data-tab="steps"]').click();
      await new Promise((r) => setTimeout(r, 200));
      return { openOnHive, stillOpenOnSteps:
        !document.getElementById("rd-log").classList.contains("hidden"),
        text: document.getElementById("rd-log-text").textContent.slice(0, 400),
        stepsPaneNow: !(${paneHidden("steps")}) };
    })()`);
    check("蜂巢格点开日志抽屉", drawer.openOnHive === true);
    check("切分区后抽屉仍在（跨分区）", drawer.stillOpenOnSteps === true && drawer.stepsPaneNow === true,
      JSON.stringify(drawer));
    check("抽屉内容含步骤输出（首格=规划步骤日志）", (drawer.text || "").includes("大纲：三步走完成"), drawer.text);

    // D) sideOpenRun：钉「步骤」分区 + 聚焦步骤 2
    const focus = await evalJson(`(async () => {
      sideOpenRun(${JSON.stringify(RUN_ID)}, 2);
      await new Promise((r) => setTimeout(r, 900));
      const f = document.querySelector("#rd-steps .step.focus");
      return { active: ${activeTab}, focusN: f ? Number(f.dataset.n) : 0,
        logOpen: !document.getElementById("rd-log").classList.contains("hidden") };
    })()`);
    check("sideOpenRun 落「步骤」分区并聚焦第 2 步",
      focus.active === "steps" && focus.focusN === 2, JSON.stringify(focus));

    // D2) 日志抽屉：自动展开的日志带步骤标题（角色·执行者），rdLogClose 收起并清标题
    const drawerTitle = await evalJson(`(() => ({
      step: (document.getElementById("rd-log-step") || {}).textContent || "",
      open: !document.getElementById("rd-log").classList.contains("hidden"),
    }))()`);
    check("抽屉标题=当前步骤（draft-c1 · 写手 A）",
      drawerTitle.open && drawerTitle.step.includes("draft-c1") && drawerTitle.step.includes("写手 A"),
      JSON.stringify(drawerTitle));
    const drawerClosed = await evalJson(`(async () => {
      rdLogClose();
      await new Promise((r) => setTimeout(r, 150));
      return { open: document.getElementById("rd-log").classList.contains("hidden"),
        step: (document.getElementById("rd-log-step") || {}).textContent || "",
        btn: !!document.querySelector("#rd-log .rd-log-x") };
    })()`);
    check("rdLogClose/×按钮：收起并清空标题",
      drawerClosed.open === true && drawerClosed.step === "" && drawerClosed.btn === true,
      JSON.stringify(drawerClosed));

    // F) 运行中指挥（信箱回归蜂巢分区）：活跃 run 显示输入区，终态隐藏
    const steer = await evalJson(`(async () => {
      renderDirector({ id: ${JSON.stringify(RUN_ID)}, status: "running", messages: [
        { text: "第三章节奏太慢", sender: "作者", created_at: "00:03:00", consumed: false } ] }, true);
      await new Promise((r) => setTimeout(r, 150));
      const box = document.getElementById("rd-direct");
      const inHivePane = !!document.querySelector('.rd-pane[data-pane="hive"] #rd-direct');
      const shown = box && !box.classList.contains("hidden");
      const parts = shown ? {
        ta: !!document.getElementById("rd-msg-input"),
        send: !!document.getElementById("rd-send-btn"),
        attach: !!document.getElementById("rd-attach-btn"),
        msg: (document.getElementById("rd-msgs") || {}).textContent || "",
      } : null;
      renderDirector(null, false);
      await new Promise((r) => setTimeout(r, 150));
      return { shown, inHivePane, parts,
        hiddenAfter: document.getElementById("rd-direct").classList.contains("hidden") };
    })()`);
    check("运行中指挥：活跃 run 在蜂巢分区露出（输入/附件/发送齐备）",
      steer.shown === true && steer.inHivePane === true && steer.parts && steer.parts.ta &&
      steer.parts.send && steer.parts.attach, JSON.stringify(steer));
    check("运行中指挥：已入箱消息可见；终态收起",
      (steer.parts && steer.parts.msg || "").includes("第三章节奏太慢") && steer.hiddenAfter === true,
      JSON.stringify(steer));

    // E) 运行中徽章：running 上下文重算 → 蜂巢徽章 ● 在岗数（自动落蜂巢分区）
    const live = await evalJson(`(async () => {
      S.rdTabPin = false; S.rdTabSig = "";   // 解钉后模拟新状态签名（done→running）
      rdTabsSync({ running: true, status: "running", gitState: "", steps: 3,
        runningCount: 2, hasResult: false });
      await new Promise((r) => setTimeout(r, 100));
      return { active: ${activeTab},
        badge: (document.querySelector('#rd-tabs .rd-tab[data-tab="hive"] .rd-badge') || {}).textContent || "" };
    })()`);
    check("运行中自动落「蜂巢」分区，徽章=● 在岗数",
      live.active === "hive" && live.badge.includes("2"), JSON.stringify(live));

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
