/* 「立即重试」按钮回归（2026-09-30 用户反馈：退避窗口干等没法手动提前）：
 * 1) 任务详情：退避窗口内（将于 HH:MM 自动续跑）→ #btn-resume-now 可见；
 *    点击恰发一次 POST /api/runs/<id>/resume_now，toast 确认；
 * 2) 退避时刻已过 → 按钮隐藏（chip 翻回「排队中」）；
 * 3) 普通排队（无 resume_enqueue_at，等并发位）→ 按钮不出现；
 * 4) 运行级详情页（renderRunDetail）同款可见性。
 * 造数同 ui_resume_backoff：排队 run 由页面层 fetch 桩注入（启动收尸会把
 * 盘上 queued 收成 failed），其余全走真实临时服务。
 * 用法：node tests/ui_resume_now.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18896;
const CDP_PORT = parseInt(process.env.TUTTI_TEST_CDP || "9393", 10);
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const T_RES = "t-resnow-20260930-0001";
const R_DONE = "r-20260930-160000-00r1";
const R_QUE = "r-20260930-162829-00r2";
const RESUME_AT_FUTURE = "2099-01-01 08:30:00";
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-resnow-"));
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", R_DONE), { recursive: true });

  const mkTask = (id, title) => ({
    id, title, type: "novel", serial: { chapters: 8, words_per_chapter: 2000 },
    goal: "造数：" + title, workdir, status: "failed", archived: false,
    created_at: "2026-09-30 15:00:00", mode: "manual",
  });
  writeFileSync(join(dataDir, "tasks", T_RES + ".json"), JSON.stringify(mkTask(T_RES, "立即重试之书"), null, 2));
  const failedRun = {
    id: R_DONE, kind: "orchestration", title: "立即重试之书", task_id: T_RES,
    status: "failed", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "mock-a", agent_label: "写手", note: "", status: "done",
        started_at: "16:00:01", ended_at: "16:00:10", duration_s: 9, exit_code: 0,
        summary: "已完成 8 章大纲", log: "steps/01.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2026-09-30 16:00:00", started_at: "2026-09-30 16:00:00",
    ended_at: "2026-09-30 16:00:20", cost_usd: 0, tokens: 0,
    error: "网关限流", auto_resumes: 1,
  };
  writeFileSync(join(dataDir, "runs", R_DONE, "run.json"), JSON.stringify(failedRun, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const killTree = (p) => {
    try { if (p && p.pid) spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)],
      { stdio: "ignore" }); } catch (e) { /* 尽力而为 */ }
  };
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
  } catch (e) { /* 查不到就放行，服务端自证兜底 */ }
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
    check("临时服务就绪(18896)", up);

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

    // 页面层注入：SSE 关掉防覆盖；排队轮带未来退避时刻；resume_now 接口打桩计数
    const inject = await evalJson(`(async () => {
      if (typeof S === "undefined" || typeof applyState !== "function") return { ok: false };
      if (S.es) { try { S.es.close(); } catch (e) {} S.es = null; S.sseLive = false; }
      const queuedRun = {
        id: "${R_QUE}", kind: "orchestration", title: "立即重试之书", task_id: "${T_RES}",
        status: "queued", messages: [], steps: [],
        created_at: "2026-09-30 16:28:29", started_at: "", ended_at: "",
        cost_usd: 0, tokens: 0, error: "", auto_resumes: 2,
        resume_enqueue_at: "${RESUME_AT_FUTURE}",
      };
      window.__queuedRun = queuedRun;
      window.__resumeNowCalls = 0;
      const failedRun = ${JSON.stringify(failedRun)};
      const origFetch = window.fetch.bind(window);
      window.fetch = async (u, o) => {
        const url = String(u);
        if (url.includes("/api/runs/${R_QUE}/resume_now")) {
          window.__resumeNowCalls += 1;
          return new Response(JSON.stringify({ ok: true }),
            { status: 200, headers: { "Content-Type": "application/json" } });
        }
        if (url.includes("/api/tasks/${T_RES}/runs")) {
          return new Response(JSON.stringify({ runs: [queuedRun, failedRun] }),
            { status: 200, headers: { "Content-Type": "application/json" } });
        }
        if (url.includes("/api/runs/${R_QUE}")) {
          return new Response(JSON.stringify({ run: queuedRun }),
            { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return origFetch(u, o);
      };
      const origApply = applyState;
      applyState = function (d) {
        if (d && d.task_latest) d.task_latest["${T_RES}"] = queuedRun;
        return origApply(d);
      };
      try {
        const r = await origFetch("/api/state");
        applyState(await r.json());
      } catch (e) { return { ok: false, err: String(e) }; }
      return { ok: true, latest: (S.state.task_latest || {})["${T_RES}"].status };
    })()`);
    check("页面层注入生效(排队轮入 task_latest)", inject && inject.ok && inject.latest === "queued",
      JSON.stringify(inject));

    // 等侧栏行渲染
    let rows = null;
    for (let i = 0; i < 30 && !rows; i++) {
      await sleep(500);
      rows = await evalJson(`document.querySelectorAll('.stask').length >= 1 ? true : null`);
    }
    check("侧栏任务行就绪", !!rows);

    // 1) 退避窗口内打开任务详情：chip 自动续跑文案 + 立即重试按钮可见
    const open1 = await evalJson(`(async () => {
      const row = document.querySelector('.stask[data-task="${T_RES}"]');
      if (!row) return { __err: "row-null" };
      row.click();
      await new Promise((r) => setTimeout(r, 1200));
      const btn = document.getElementById("btn-resume-now");
      return { chip: document.getElementById("rd-status").textContent,
               hidden: btn ? btn.classList.contains("hidden") : null,
               text: btn ? btn.textContent : "(no el)" };
    })()`);
    check("chip 显示「将于 08:30 自动续跑（第 2 次）」",
      String(open1.chip || "").includes("自动续跑") && String(open1.chip || "").includes("08:30"),
      open1.chip);
    check("退避窗口内 → 立即重试按钮可见",
      open1.hidden === false && String(open1.text).includes("立即重试"), JSON.stringify(open1));

    // 2) 点击：恰发一次 resume_now POST + toast 确认
    const click1 = await evalJson(`(async () => {
      window.__resumeNowCalls = 0;
      document.getElementById("btn-resume-now").click();
      await new Promise((r) => setTimeout(r, 800));
      const t0 = document.getElementById("toast");
      return { calls: window.__resumeNowCalls,
               toastShown: t0 ? t0.className.includes("show") : false,
               toastText: t0 ? t0.textContent : "" };
    })()`);
    check("点击恰发一次 resume_now 请求", click1.calls === 1, JSON.stringify(click1));
    check("toast 提示已跳过等待",
      click1.toastShown && String(click1.toastText).includes("跳过等待"), JSON.stringify(click1));

    // 3) 退避时刻已过：按钮隐藏，chip 翻回「排队中」
    const after = await evalJson(`(async () => {
      await new Promise((r) => setTimeout(r, 2600));   // 等上一条 toast 消失防串台
      window.__queuedRun.resume_enqueue_at = "2020-01-01 08:30:00";
      S.taskSig = "";
      sideOpenTask("${T_RES}");
      await new Promise((r) => setTimeout(r, 1200));
      const btn = document.getElementById("btn-resume-now");
      return { chip: document.getElementById("rd-status").textContent,
               hidden: btn ? btn.classList.contains("hidden") : null };
    })()`);
    check("退避已过 → 按钮隐藏", after.hidden === true, JSON.stringify(after));
    check("退避已过 → chip 翻回「排队中」",
      String(after.chip || "").includes("排队中") && !String(after.chip || "").includes("自动续跑"),
      after.chip);

    // 4) 普通排队（无 resume_enqueue_at）：按钮不出现
    const plain = await evalJson(`(async () => {
      window.__queuedRun.resume_enqueue_at = "";
      S.taskSig = "";
      sideOpenTask("${T_RES}");
      await new Promise((r) => setTimeout(r, 1200));
      const btn = document.getElementById("btn-resume-now");
      return { chip: document.getElementById("rd-status").textContent,
               hidden: btn ? btn.classList.contains("hidden") : null };
    })()`);
    check("普通并发排队 → 按钮不出现", plain.hidden === true, JSON.stringify(plain));

    // 5) 运行级详情页（renderRunDetail）同款可见性
    const runLevel = await evalJson(`(async () => {
      window.__queuedRun.resume_enqueue_at = "${RESUME_AT_FUTURE}";
      S.runDetailSig = "";
      openRun("${R_QUE}");
      await new Promise((r) => setTimeout(r, 1200));
      const btn = document.getElementById("btn-resume-now");
      return { chip: document.getElementById("rd-status").textContent,
               hidden: btn ? btn.classList.contains("hidden") : null };
    })()`);
    check("运行级详情：退避内按钮可见",
      runLevel.hidden === false && String(runLevel.chip || "").includes("自动续跑"),
      JSON.stringify(runLevel));

    check("无新增控制台错误", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));
  } finally {
    killTree(edge);
    killTree(srv);
    await sleep(800);   // 给 taskkill 一点收尾时间再删目录
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }

  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项失败` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
