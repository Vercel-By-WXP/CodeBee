/* 滚动条审计探针：复刻「到处都是滚动条」用户截图场景（多步骤蜂巢 + 长日志抽屉），
 * 在详情页三种状态下枚举所有「真实可见」的滚动条（overflow auto/scroll 且内容溢出），
 * 供人工判断哪些是合理的（main 长内容 / 日志 pre），哪些是元凶。
 * 用法：node tests/_probe_scrollbars.mjs
 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18891;
const CDP_PORT = 9366;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990102-000000-0009";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function seed(dataDir) {
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  const longLog = ["===== 下达 2099-01-02 00:00:01 =====", "--- 指令 ---", "写章节", "--- 输出 ---"]
    .concat(Array.from({ length: 260 }, (_, i) => `评审输出第 ${i + 1} 行：{"note":"假若这是很长的一行日志内容，用来撑出滚动条","summary":"章节人物弧光与暗光推进上做到音行有据，成长可判"}`))
    .join("\n");
  writeFileSync(join(runsDir, "steps", "03-critique-long.log"), longLog, "utf-8");
  writeFileSync(join(workdir, "第一章.md"), "# 第一章\n\n正文开头。\n", "utf-8");
  writeFileSync(join(workdir, "story-bible.md"), "# 故事圣经\n\n女主：阿禾。\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", "task-sb.json"), JSON.stringify({
    id: "task-sb", title: "滚动条审计任务", type: "novel", serial: true,
    goal: "造数：滚动条审计", workdir, status: "done", archived: false,
    created_at: "2000-01-02 00:00:00", mode: "manual",
  }, null, 2));
  const steps = [
    { n: 1, role: "plan", agent: "mock-p", agent_label: "规划员", status: "done", summary: "继承上一卷大纲（共 8 章），已完成 3…", log: "steps/01-plan.log", duration_s: 0.1 },
  ];
  for (let i = 1; i <= 12; i++) {
    steps.push({ n: 1 + i, role: `draft-c${i}`, agent: i % 2 ? "mock-a" : "mock-b", agent_label: i % 2 ? "写手 A" : "写手 B", status: "done", summary: `第 ${i} 章草稿完成`, log: `steps/${10 + i}-draft.log`, duration_s: 3 });
    writeFileSync(join(runsDir, "steps", `${10 + i}-draft.log`), `--- 输出 ---\n第 ${i} 章正文\n`, "utf-8");
  }
  for (let i = 1; i <= 4; i++) {
    const long = i === 1;
    steps.push({ n: 14 + i, role: `critique-c${i}`, agent: "mock-b", agent_label: "评审 B", status: "done", summary: long ? "评审输出（长日志）" : "评审通过", log: long ? "steps/03-critique-long.log" : `steps/${20 + i}-critique.log`, duration_s: 24 });
    if (!long) writeFileSync(join(runsDir, "steps", `${20 + i}-critique.log`), `--- 输出 ---\n评审 ${i} 通过\n`, "utf-8");
  }
  steps.forEach((s) => Object.assign(s, { note: "", exit_code: 0, cost_usd: 0, tokens: 0 }));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "滚动条审计任务",
    task_id: "task-sb", status: "done", messages: [], steps,
    created_at: "2000-01-02 00:00:00", started_at: "2000-01-02 00:00:00",
    ended_at: "2000-01-02 00:02:00", cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }, null, 2));
}

const AUDIT = `(() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    const canY = cs.overflowY === "auto" || cs.overflowY === "scroll";
    const canX = cs.overflowX === "auto" || cs.overflowX === "scroll";
    const hiddenY = cs.scrollbarWidth === "none";
    const oY = el.scrollHeight - el.clientHeight;
    const oX = el.scrollWidth - el.clientWidth;
    const tag = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + "." +
      String(el.className instanceof SVGAnimatedString ? el.className.baseVal : (el.className || "")).trim().split(/\\s+/).slice(0, 3).join(".");
    if ((canY && oY > 1 && !hiddenY) || (canX && oX > 1)) {
      if (seen.has(tag)) continue;
      seen.add(tag);
      out.push({ el: tag, oY: canY && oY > 1 ? oY : 0, oX: canX && oX > 1 ? oX : 0,
        ch: el.clientHeight, sh: el.scrollHeight });
    }
  }
  const main = document.querySelector("main");
  return { scrollbars: out,
    pageScroll: document.documentElement.scrollHeight - window.innerHeight,
    mainScroll: main ? main.scrollHeight - main.clientHeight : -1 };
})()`;

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-sb-"));
  seed(dataDir);
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
    if (!up) throw new Error("service not up");
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    if (!target) throw new Error("no CDP target");
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
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

    // 状态 A：详情页默认（终态 → 蜂巢分区），抽屉关着
    await evalJson(`(async () => { sideOpenTask("task-sb"); await new Promise((r) => setTimeout(r, 1200)); return 1; })()`);
    const a = await evalJson(AUDIT);
    console.log("A 详情页·蜂巢（抽屉关）:", JSON.stringify(a, null, 1));

    // 状态 B：直接调 toggleLog 打开长日志抽屉
    const b = await evalJson(`(async () => {
      await toggleLog(${JSON.stringify(RUN_ID)}, "steps/03-critique-long.log");
      await new Promise((r) => setTimeout(r, 600));
      const box = document.getElementById("rd-log");
      const pre = document.getElementById("rd-log-text");
      const cs = getComputedStyle(pre);
      return { open: !box.classList.contains("hidden"),
        text: pre.textContent.slice(0, 60),
        boxH: box.clientHeight + "/" + box.scrollHeight,
        preH: pre.clientHeight + "/" + pre.scrollHeight,
        preOv: cs.overflowY + "/" + cs.scrollbarWidth };
    })()`);
    console.log("B 抽屉状态:", JSON.stringify(b));
    const bAudit = await evalJson(AUDIT);
    console.log("B 长日志抽屉打开:", JSON.stringify(b), JSON.stringify(bAudit, null, 1));

    // 状态 C：切步骤分区（抽屉跨分区仍在）
    await evalJson(`(async () => { document.querySelector('#rd-tabs .rd-tab[data-tab="steps"]').click(); await new Promise((r) => setTimeout(r, 400)); return 1; })()`);
    const c = await evalJson(AUDIT);
    console.log("C 步骤分区（抽屉在）:", JSON.stringify(c, null, 1));
  } finally {
    try { edge.kill(); } catch (e) { /* ignore */ }
    try { srv.kill(); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
}

main().then(() => process.exit(0)).catch((e) => { console.error("PROBE FAIL:", e); process.exit(1); });
