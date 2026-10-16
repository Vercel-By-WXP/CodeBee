/* 任务完成右下角通知回归（2026-10-16 用户反馈「任务完成了不知道」案）：
 * 1) 刷新/首帧看到的就是终态 → 不弹（只记账，不轰炸）；
 * 2) 本会话见过 running 翻 done → 右下角 .notice-card 弹「任务已完成+任务名」；
 * 3) 点卡片直开该任务对话；failed 弹红卡；done·未达标弹黄卡；
 * 4) 连环完成卡片封顶 4 张；7s 自动消失。
 * 造数：盘上 run.json 直接终态（首帧不弹的基线），running→done 的活跃转换
 * 由页面层 applyState 包装注入，其余全走真实临时服务。
 * 用法：node tests/ui_run_settle_notice.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18895;
const CDP_PORT = parseInt(process.env.TUTTI_TEST_CDP || "9395", 10);
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const T1 = "t-notice-20261016-0001";
const R_DISK = "r-20261016-090000-00r0"; // 盘上终态轮（首帧基线）
const R1 = "r-20261016-091000-00r1";     // 注入轮（running→done 等转换）
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-notice-"));
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", R_DISK), { recursive: true });

  writeFileSync(join(dataDir, "tasks", T1 + ".json"), JSON.stringify({
    id: T1, title: "通知之书", type: "novel", goal: "造数：右下角完成通知",
    workdir, status: "done", archived: false,
    created_at: "2026-10-16 09:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(dataDir, "runs", R_DISK, "run.json"), JSON.stringify({
    id: R_DISK, kind: "orchestration", title: "通知之书", task_id: T1,
    status: "done", messages: [], steps: [],
    created_at: "2026-10-16 09:00:00", started_at: "2026-10-16 09:00:01",
    ended_at: "2026-10-16 09:00:30", cost_usd: 0, tokens: 0, error: "",
  }, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  // Windows 下 child.kill() 杀不死这套服务：清理一律 taskkill /F /T 按 PID 杀树
  const killTree = (p) => {
    try { if (p && p.pid) spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)],
      { stdio: "ignore" }); } catch (e) { /* 尽力而为 */ }
  };
  // 起跑前端口必须干净：宁可失败也不跟僵尸服务对暗号
  let portDirty = false;
  try {
    const listeners = await new Promise((resolve) => {
      const nls = spawn("netstat", ["-ano"], { stdio: ["ignore", "pipe", "ignore"] });
      let buf = "";
      nls.stdout.on("data", (d) => { buf += d; });
      nls.stdout.on("end", () => resolve(buf));
      nls.on("error", () => resolve(""));
    });
    portDirty = listeners.split("\n").some((l) =>
      l.includes(":" + SERVICE_PORT) && l.includes("LISTENING"));
  } catch (e) { /* 查不到就放行，后面的服务端自证会兜底 */ }
  if (portDirty) {
    console.error("端口 " + SERVICE_PORT + " 已被占用（疑似僵尸服务），先清理再跑本测试");
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    process.exit(2);
  }
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 240 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); await r.text(); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18895)", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP(9395)", !!target);
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

    // 页面层注入：SSE 关掉防覆盖；applyState 包装把 T1 的 task_latest 钉成
    // window.__forceStatus 指定状态的注入轮（R1）。__forceStatus 为空=放行原载荷。
    const inject = await evalJson(`(async () => {
      if (typeof S === "undefined" || typeof applyState !== "function") return { ok: false };
      if (S.es) { try { S.es.close(); } catch (e) {} S.es = null; S.sseLive = false; }
      window.__forceStatus = "";
      const mkRun = (status, extra) => Object.assign({
        id: "${R1}", kind: "orchestration", title: "通知之书", task_id: "${T1}",
        status, messages: [], steps: [],
        created_at: "2026-10-16 09:10:00", started_at: "2026-10-16 09:10:01",
        ended_at: "", cost_usd: 0, tokens: 0, error: "",
      }, extra || {});
      window.__mkRun = mkRun;
      const origApply = applyState;
      applyState = function (d) {
        if (d && d.task_latest && window.__forceStatus)
          d.task_latest["${T1}"] = mkRun(window.__forceStatus);
        return origApply(d);
      };
      try {
        const r = await fetch("/api/state");
        window.__forceStatus = "";      // 首帧放行：盘上终态 run 只记账不弹
        applyState(await r.json());
      } catch (e) { return { ok: false, err: String(e) }; }
      return { ok: true, latest: ((S.state.task_latest || {})["${T1}"] || {}).status };
    })()`);
    check("页面层注入生效", inject && inject.ok && inject.latest === "done",
      JSON.stringify(inject));

    const cardInfo = `(() => {
      const box = document.getElementById("notice-stack");
      const cards = box ? Array.from(box.children) : [];
      return { n: cards.length,
        texts: cards.map((c) => c.textContent),
        cls: cards.map((c) => c.className) };
    })()`;
    // 残卡会顶够张数：等待一律按谓词匹配目标卡，不按数量
    const waitCard = async (predExpr) => {
      for (let i = 0; i < 20; i++) {
        const hit = await evalJson(`(() => {
          const box = document.getElementById("notice-stack");
          const cards = box ? Array.from(box.children) : [];
          return cards.some((c) => (${predExpr})(c));
        })()`);
        if (hit) return await evalJson(cardInfo);
        await sleep(400);
      }
      return await evalJson(cardInfo);
    };

    // 1) 刷新/首帧看到的就是终态：不弹
    await sleep(1500);
    const base = await evalJson(cardInfo);
    check("首帧终态不弹卡", base && base.n === 0, JSON.stringify(base));

    // 2) running → done：右下角弹「任务已完成+任务名」
    const done = await evalJson(`(async () => {
      window.__forceStatus = "running";
      applyState(JSON.parse(JSON.stringify(S.state)));
      await new Promise((r) => setTimeout(r, 300));
      window.__forceStatus = "done";
      applyState(JSON.parse(JSON.stringify(S.state)));
      return true;
    })()`);
    const doneCards = done ? await waitCard((c) => c.className.includes("good")) : null;
    check("running→done 弹卡", !!doneCards && doneCards.n >= 1, JSON.stringify(doneCards));
    check("卡面文案=任务已完成+任务名", !!doneCards && doneCards.texts.some((x) =>
      x.includes("任务已完成") && x.includes("通知之书")), JSON.stringify(doneCards));
    check("卡样式=good", !!doneCards && doneCards.cls.some((c) => c.includes("good")),
      JSON.stringify(doneCards));

    // 3) 点卡片直开该任务对话
    const opened = await evalJson(`(async () => {
      const card = document.querySelector("#notice-stack .notice-card");
      if (!card) return { __err: "card-null" };
      card.click();
      await new Promise((r) => setTimeout(r, 1500));
      return { title: document.getElementById("rd-title").textContent };
    })()`);
    check("点卡直开任务对话", !opened.__err && String(opened.title).includes("通知之书"),
      JSON.stringify(opened));

    // 4) failed 弹红卡；done·未达标弹黄卡（verdict.pass=false 走真实载荷字段）
    await evalJson(`(async () => {
      window.__forceStatus = "running";
      applyState(JSON.parse(JSON.stringify(S.state)));
      await new Promise((r) => setTimeout(r, 300));
      window.__forceStatus = "failed";
      applyState(JSON.parse(JSON.stringify(S.state)));
      return true;
    })()`);
    const failedCards = await waitCard((c) => c.className.includes("bad"));
    check("running→failed 弹红卡", !!failedCards && failedCards.cls.some((c) => c.includes("bad"))
      && failedCards.texts.some((x) => x.includes("任务失败")), JSON.stringify(failedCards));

    await evalJson(`(async () => {
      window.__forceStatus = "running";
      applyState(JSON.parse(JSON.stringify(S.state)));
      await new Promise((r) => setTimeout(r, 300));
      const d = JSON.parse(JSON.stringify(S.state));
      d.task_latest["${T1}"] = window.__mkRun("done", { verdict: { pass: false } });
      window.__forceStatus = "";     // 放行本载荷：带 verdict 的 done
      applyState(d);
      return true;
    })()`);
    const warnRes = await waitCard((c) => c.className.includes("warn"));
    check("done·未达标 弹黄卡", !!warnRes && warnRes.cls.some((c) => c.includes("warn"))
      && warnRes.texts.some((x) => x.includes("未达标")), JSON.stringify(warnRes));

    // 5) 连环完成封顶 4 张
    const burst = await evalJson(`(async () => {
      for (let i = 0; i < 4; i++) {
        window.__forceStatus = "running";
        applyState(JSON.parse(JSON.stringify(S.state)));
        await new Promise((r) => setTimeout(r, 120));
        window.__forceStatus = "done";
        applyState(JSON.parse(JSON.stringify(S.state)));
        await new Promise((r) => setTimeout(r, 120));
      }
      return true;
    })()`);
    await sleep(600);
    const capped = burst ? await evalJson(cardInfo) : null;
    check("连环完成封顶 4 张", !!capped && capped.n <= 4 && capped.n >= 1, JSON.stringify(capped && capped.n));

    // 6) 7s 自动消失
    await sleep(8200);
    const gone = await evalJson(cardInfo);
    check("7s 自动消失", gone && gone.n === 0, JSON.stringify(gone));

    check("无新增控制台错误", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));
  } finally {
    killTree(edge);
    killTree(srv);
    await sleep(800);   // 给 taskkill 一点收尾时间再删目录
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    let still = "";
    try {
      still = await new Promise((resolve) => {
        const nls = spawn("netstat", ["-ano"], { stdio: ["ignore", "pipe", "ignore"] });
        let buf = "";
        nls.stdout.on("data", (d) => { buf += d; });
        nls.stdout.on("end", () => resolve(buf));
        nls.on("error", () => resolve(""));
      });
    } catch (e) { /* 尽力而为 */ }
    if (still.split("\n").some((l) => l.includes(":" + SERVICE_PORT) && l.includes("LISTENING")))
      console.error("警告：端口 " + SERVICE_PORT + " 未释放，请按 PID 清理");
  }

  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项失败` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
